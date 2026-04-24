from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from alert_history_store import build_latest_symbol_event_map
from alert_status_state import apply_alert_state_transitions, read_recent_transitions
from app_config import EVENT_RISK_MODES
from macro_focus import build_global_market_focus, build_symbol_macro_focus
from monitor_cards import build_alert_status_cards, build_event_window_cards, build_macro_data_status_card, build_runtime_status_cards, build_spread_focus_cards
from regime_classifier import build_snapshot_regime_summary, classify_market_regime
from quote_models import QuoteRow, SnapshotItem
from risk_reward import analyze_risk_reward
from monitor_rules import (
    build_portfolio_trade_grade,
    build_quote_risk_note,
    build_quote_structure_text,
    build_trade_grade,
    format_quote_price,
)
from mt5_gateway import fetch_quotes, initialize_connection
from runtime_utils import parse_time as _parse_time
from signal_side_utils import derive_signal_side_meta
from signal_enums import AlertTone, EventModeText, QuoteStatus, SignalSide, TradeGrade
from trade_opportunity import score_trade_opportunity


_SIGNAL_SIDE_TEXT = {
    SignalSide.LONG.value: "【↑ 多头参考】",
    SignalSide.SHORT.value: "【↓ 空头参考】",
    SignalSide.NEUTRAL.value: "",
}


def _safe_field(d: dict, key: str) -> str:
    """Safe string extraction — prevents numpy array bool ambiguity errors."""
    val = d.get(key, "")
    try:
        return str(val).lower().strip() if val is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _retry_risk_reward_with_signal_side(row: dict, enriched_row: dict, signal_side: str) -> dict:
    side = str(signal_side or "").strip().lower()
    if side not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return dict(enriched_row or {})
    if bool(enriched_row.get("risk_reward_ready", False)):
        return dict(enriched_row or {})

    retry_input = {**dict(row or {}), **dict(enriched_row or {}), "signal_side": side}
    retried = analyze_risk_reward(retry_input)
    if not retried:
        return dict(enriched_row or {})

    merged = dict(enriched_row or {})
    merged.update(retried)
    return merged


def _normalize_quote_row(row: dict | QuoteRow | None) -> dict:
    """将原始报价行归一化为稳定字典结构。

    说明：当前先在 monitor_engine 内部消费稳定字段契约，暂不一次性替换整个快照模型。
    """
    return QuoteRow.from_payload(row).to_dict()


def _event_targets_symbol(context: dict, symbol: str) -> bool:
    targets = {
        str(item or "").strip().upper()
        for item in list(context.get("active_event_symbols", []) or [])
        if str(item or "").strip()
    }
    if not targets:
        return True
    return str(symbol or "").strip().upper() in targets


def _build_symbol_event_meta(symbol: str, event_context: dict | None = None) -> dict:
    context = dict(event_context or {})
    mode = str(context.get("mode", "normal") or "normal").strip().lower()
    active_name = str(context.get("active_event_name", "") or "").strip()
    active_time_text = str(context.get("active_event_time_text", "") or "").strip()
    importance_text = str(context.get("active_event_importance_text", "") or "").strip()
    scope_text = str(context.get("active_event_scope_text", "") or "").strip()
    applies = bool(active_name) and mode in {"pre_event", "post_event"} and _event_targets_symbol(context, symbol)
    note = ""
    if applies:
        if mode == "pre_event":
            note = (
                f"{importance_text or '事件'}窗口：{active_name} 将于 {active_time_text or '稍后'} 落地，"
                "当前品种先别抢第一脚。"
            )
        else:
            note = (
                f"{importance_text or '事件'}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                "当前品种先等重新定价完成。"
            )
    return {
        "event_mode_text": str(context.get("mode_text", "") or "").strip(),
        "event_active_name": active_name,
        "event_active_time_text": active_time_text,
        "event_importance_text": importance_text,
        "event_scope_text": scope_text,
        "event_note": note,
        "event_applies": applies,
    }


def _build_symbol_alert_state(
    symbol: str,
    row: dict,
    tone: str,
    trade_grade: dict,
    item_event_meta: dict,
    latest_symbol_event: dict | None = None,
) -> dict:
    symbol_key = str(symbol or "").strip().upper()
    status_text = str(row.get("status", "") or "").strip()
    status_code = str(row.get("quote_status_code", "") or "").strip().lower()
    has_live_quote = bool(row.get("has_live_quote", False))
    current_spread_points = float(row.get("spread_points", 0.0) or 0.0)
    event_note = str(item_event_meta.get("event_note", "") or "").strip()
    event_name = str(item_event_meta.get("event_active_name", "") or "").strip()
    event_importance_text = str(item_event_meta.get("event_importance_text", "") or "").strip()
    event_time_text = str(item_event_meta.get("event_active_time_text", "") or "").strip()
    event_mode_text = str(item_event_meta.get("event_mode_text", "") or "").strip()
    trade_source = str(trade_grade.get("source", "") or "").strip()

    if not has_live_quote or status_code in {
        QuoteStatus.INACTIVE.value,
        QuoteStatus.UNKNOWN_SYMBOL.value,
        QuoteStatus.NOT_SELECTED.value,
        QuoteStatus.ERROR.value,
    }:
        return {
            "alert_state_text": "非活跃 / 静态报价",
            "alert_state_detail": (
                f"{symbol_key} 当前没有新 tick，看到的 Bid/Ask 可能是静态缓存，先不做临场判断。"
                f"{(' ' + str(row.get('quote_live_diagnostic_text', '') or '').strip()) if str(row.get('quote_live_diagnostic_text', '') or '').strip() else ''}"
            ),
            "alert_state_tone": AlertTone.NEUTRAL.value,
            "alert_state_rank": 3,
        }

    if tone == AlertTone.WARNING.value:
        detail = f"{symbol_key} 当前点差仍明显放大，异常仍在进行中。"
        if event_note:
            detail += f" {event_note}"
        return {
            "alert_state_text": "点差异常进行中",
            "alert_state_detail": detail,
            "alert_state_tone": AlertTone.WARNING.value,
            "alert_state_rank": 6,
        }

    if tone == AlertTone.ACCENT.value:
        detail = f"{symbol_key} 当前点差仍偏宽，先继续观察别急着追。"
        if event_note:
            detail += f" {event_note}"
        return {
            "alert_state_text": "点差偏宽观察",
            "alert_state_detail": detail,
            "alert_state_tone": AlertTone.ACCENT.value,
            "alert_state_rank": 5,
        }

    if (
        str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(trade_grade.get("event_override_kind", "") or "").strip() == "post_event_continuation"
    ):
        return {
            "alert_state_text": "事件后延续候选",
            "alert_state_detail": f"{symbol_key} 当前已通过事件后延续纪律校验，可继续按轻仓候选跟踪，但必须短周期复核。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    if (
        str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(trade_grade.get("setup_kind", "") or "").strip() == "early_momentum"
    ):
        return {
            "alert_state_text": "早期动能候选",
            "alert_state_detail": f"{symbol_key} 当前已进入早期动能候选，允许轻仓跟踪，但必须盯紧下一到两根 M5 的确认与失效。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    if (
        str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(trade_grade.get("setup_kind", "") or "").strip() == "direct_momentum"
    ):
        return {
            "alert_state_text": "直线动能候选",
            "alert_state_detail": f"{symbol_key} 当前属于无回踩动能延续，允许轻仓跟踪，但必须盯紧 M5 是否继续扩张或立刻衰减。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    if (
        str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(trade_grade.get("setup_kind", "") or "").strip() == "directional_probe"
    ):
        return {
            "alert_state_text": "方向试仓候选",
            "alert_state_detail": f"{symbol_key} 当前方向明确、波动扩张、盈亏比达标，虽然结构还不完美，但已允许轻仓试探。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    if (
        str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(trade_grade.get("setup_kind", "") or "").strip() == "pullback_sniper_probe"
    ):
        return {
            "alert_state_text": "回调狙击候选",
            "alert_state_detail": f"{symbol_key} 当前趋势方向明确，价格回到均线价值区且 RSI 未过热，允许按探索试仓采样。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    # 事件窗口优先级最高（高影响事件前 / 后）
    if bool(item_event_meta.get("event_applies")) and event_name:
        if event_mode_text == EventModeText.PRE_EVENT:
            return {
                "alert_state_text": f"{event_importance_text or '事件'}事件前",
                "alert_state_detail": f"{event_name} 将于 {event_time_text or '稍后'} 落地，当前品种正处于事件前观察窗口。",
                "alert_state_tone": AlertTone.WARNING.value if "高影响" in event_importance_text else AlertTone.ACCENT.value,
                "alert_state_rank": 4 if "高影响" in event_importance_text else 3,
            }
        if event_mode_text == EventModeText.POST_EVENT:
            return {
                "alert_state_text": f"{event_importance_text or '事件'}事件后观察",
                "alert_state_detail": f"{event_name} 刚落地，当前品种先等重新定价完成再动手。",
                "alert_state_tone": AlertTone.ACCENT.value,
                "alert_state_rank": 3,
            }

    latest_event = dict(latest_symbol_event or {})
    latest_category = str(latest_event.get("category", "") or "").strip().lower()
    if latest_category == "spread":
        # 只有 12 小时内的点差历史才触发"已恢复"提示，避免陈旧记录干扰
        occurred_at_text = str(latest_event.get("occurred_at", "") or "").strip()
        _expired = True
        if occurred_at_text:
            _event_time = _parse_time(occurred_at_text)
            if _event_time is not None:
                _expired = (datetime.now() - _event_time) > timedelta(hours=12)
        if not _expired:
            latest_title = str(latest_event.get("title", "上一轮点差异常") or "上一轮点差异常").strip()
            return {
                "alert_state_text": "点差已恢复",
                "alert_state_detail": f"{symbol_key} 当前点差约 {current_spread_points:.0f} 点，相比 {latest_title} 已明显收敛。",
                "alert_state_tone": AlertTone.SUCCESS.value,
                "alert_state_rank": 4,
            }

    if str(trade_grade.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION:
        return {
            "alert_state_text": "结构候选",
            "alert_state_detail": f"{symbol_key} 当前执行面相对干净，可以继续作为候选机会观察。",
            "alert_state_tone": AlertTone.SUCCESS.value,
            "alert_state_rank": 2,
        }

    if trade_source == "event":
        return {
            "alert_state_text": "事件窗口观察",
            "alert_state_detail": event_note or f"{symbol_key} 当前更受事件窗口约束，先观察。",
            "alert_state_tone": AlertTone.ACCENT.value,
            "alert_state_rank": 3,
        }

    return {
        "alert_state_text": "报价正常观察",
        "alert_state_detail": f"{symbol_key} 当前报价和点差相对稳定，继续等待更清晰结构。",
        "alert_state_tone": AlertTone.NEUTRAL.value,
        "alert_state_rank": 1,
    }


def build_snapshot_from_rows(
    symbols: list[str],
    rows: list[dict],
    connected: bool,
    connection_message: str,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
    history_file: Path | None = None,
    status_state_file: Path | None = None,
) -> dict:
    snapshot_time = datetime.now()
    normalized_rows = [_normalize_quote_row(item) for item in rows or []]
    rows_by_symbol = {str(item.get("symbol", "")).strip().upper(): item for item in normalized_rows}
    items = []
    live_count = 0
    inactive_count = 0
    context = event_context or {}
    latest_symbol_events = build_latest_symbol_event_map(history_file=history_file)

    for symbol in symbols:
        row = rows_by_symbol.get(str(symbol).strip().upper(), {})
        has_live_quote = bool(row.get("has_live_quote", False))
        if has_live_quote:
            live_count += 1
        else:
            inactive_count += 1

        latest_price = float(row.get("latest_price", 0.0) or 0.0)
        point = float(row.get("point", 0.0) or 0.0)
        bid_price = float(row.get("bid", 0.0) or 0.0)
        ask_price = float(row.get("ask", 0.0) or 0.0)
        display_price = latest_price
        if display_price <= 0 and bid_price > 0 and ask_price > 0:
            display_price = (bid_price + ask_price) / 2.0
        elif display_price <= 0:
            display_price = max(bid_price, ask_price, 0.0)
        tone, execution_note = build_quote_risk_note(symbol, row)
        enriched_row = dict(row or {})
        enriched_row.update(analyze_risk_reward(enriched_row))
        item_event_meta = _build_symbol_event_meta(symbol, context)
        trade_grade = build_trade_grade(
            symbol,
            enriched_row,
            tone,
            connected,
            event_risk_mode=event_risk_mode,
            event_context=context,
        )
        display_event_meta = dict(item_event_meta)
        override_event_note = str(trade_grade.get("event_override_note", "") or "").strip()
        if override_event_note:
            display_event_meta["event_note"] = override_event_note
        alert_state = _build_symbol_alert_state(
            symbol,
            row,
            tone,
            trade_grade,
            display_event_meta,
            latest_symbol_event=latest_symbol_events.get(str(symbol).strip().upper()),
        )
        regime_meta = classify_market_regime(symbol, enriched_row, tone, item_event_meta)
        execution_segments = [
            f"{trade_grade['grade']}：{trade_grade['detail'] if str(trade_grade.get('source', '') or '').strip() == 'event' else execution_note}"
        ]
        signal_side_meta = derive_signal_side_meta({**row, **enriched_row})
        inferred_signal_side = str(signal_side_meta.get("signal_side", SignalSide.NEUTRAL.value) or SignalSide.NEUTRAL.value).strip().lower()
        enriched_row = _retry_risk_reward_with_signal_side(row, enriched_row, inferred_signal_side)
        signal_side_meta = derive_signal_side_meta({**row, **enriched_row})
        signal_side = str(signal_side_meta.get("signal_side", SignalSide.NEUTRAL.value) or SignalSide.NEUTRAL.value).strip().lower()
        signal_side_text = str(signal_side_meta.get("signal_side_text", "") or "").strip()

        if display_event_meta["event_note"] and str(trade_grade.get("source", "") or "").strip() != "event":
            execution_segments.append(display_event_meta["event_note"])
        for extra_text in (
            str(row.get("intraday_context_text", "") or "").strip(),
            str(row.get("multi_timeframe_context_text", "") or "").strip(),
            str(row.get("key_level_context_text", "") or "").strip(),
            str(row.get("breakout_context_text", "") or "").strip(),
            str(row.get("retest_context_text", "") or "").strip(),
            str(enriched_row.get("risk_reward_context_text", "") or "").strip(),
        ):
            if extra_text:
                execution_segments.append(extra_text)
        item_payload = SnapshotItem(
            symbol=str(symbol).strip().upper(),
            latest_price=latest_price,
            spread_points=float(row.get("spread_points", 0.0) or 0.0),
            point=point,
            has_live_quote=has_live_quote,
            bid=bid_price,
            ask=ask_price,
            tick_time=int(row.get("tick_time", 0) or 0),
            latest_text=format_quote_price(display_price, point) if display_price > 0 else "--",
            quote_text=build_quote_structure_text(row),
            status_text=str(row.get("status", "暂无快照") or "暂无快照"),
            quote_status_code=str(row.get("quote_status_code", "") or "").strip().lower(),
            execution_note=" ".join(segment for segment in execution_segments if segment),
            trade_grade=trade_grade["grade"],
            trade_grade_detail=trade_grade["detail"],
            trade_next_review=trade_grade["next_review"],
            trade_grade_source=str(trade_grade.get("source", "") or "").strip(),
            event_importance_text=display_event_meta["event_importance_text"],
            event_note=display_event_meta["event_note"],
            macro_focus=build_symbol_macro_focus(symbol),
            alert_state_text=alert_state["alert_state_text"],
            alert_state_detail=alert_state["alert_state_detail"],
            alert_state_tone=alert_state["alert_state_tone"],
            alert_state_rank=int(alert_state["alert_state_rank"] or 0),
            regime_tag=str(regime_meta.get("regime_tag", "") or "").strip(),
            regime_text=str(regime_meta.get("regime_text", "") or "").strip(),
            regime_reason=str(regime_meta.get("regime_reason", "") or "").strip(),
            regime_rank=int(regime_meta.get("regime_rank", 0) or 0),
            tone=tone,
            signal_side=signal_side,
            signal_side_text=signal_side_text,
            intraday_bias=str(row.get("intraday_bias", "") or "").strip(),
            intraday_bias_text=str(row.get("intraday_bias_text", "") or "").strip(),
            multi_timeframe_alignment=str(row.get("multi_timeframe_alignment", "") or "").strip(),
            multi_timeframe_alignment_text=str(row.get("multi_timeframe_alignment_text", "") or "").strip(),
            multi_timeframe_bias=str(row.get("multi_timeframe_bias", "") or "").strip(),
            multi_timeframe_bias_text=str(row.get("multi_timeframe_bias_text", "") or "").strip(),
            intraday_context_text=str(row.get("intraday_context_text", "") or "").strip(),
            multi_timeframe_context_text=str(row.get("multi_timeframe_context_text", "") or "").strip(),
            key_level_context_text=str(row.get("key_level_context_text", "") or "").strip(),
            key_level_state=str(row.get("key_level_state", "") or "").strip(),
            key_level_state_text=str(row.get("key_level_state_text", "") or "").strip(),
            breakout_direction=str(row.get("breakout_direction", "") or "").strip(),
            breakout_context_text=str(row.get("breakout_context_text", "") or "").strip(),
            breakout_state=str(row.get("breakout_state", "") or "").strip(),
            breakout_state_text=str(row.get("breakout_state_text", "") or "").strip(),
            retest_context_text=str(row.get("retest_context_text", "") or "").strip(),
            retest_state=str(row.get("retest_state", "") or "").strip(),
            retest_state_text=str(row.get("retest_state_text", "") or "").strip(),
            risk_reward_ready=bool(enriched_row.get("risk_reward_ready", False)),
            risk_reward_context_text=str(enriched_row.get("risk_reward_context_text", "") or "").strip(),
            risk_reward_state=str(enriched_row.get("risk_reward_state", "") or "").strip(),
            risk_reward_state_text=str(enriched_row.get("risk_reward_state_text", "") or "").strip(),
            risk_reward_ratio=float(enriched_row.get("risk_reward_ratio", 0.0) or 0.0),
            risk_reward_direction=str(enriched_row.get("risk_reward_direction", "") or "").strip(),
            risk_reward_stop_price=float(enriched_row.get("risk_reward_stop_price", 0.0) or 0.0),
            risk_reward_target_price=float(enriched_row.get("risk_reward_target_price", 0.0) or 0.0),
            risk_reward_target_price_2=float(enriched_row.get("risk_reward_target_price_2", 0.0) or 0.0),
            risk_reward_entry_zone_low=float(enriched_row.get("risk_reward_entry_zone_low", 0.0) or 0.0),
            risk_reward_entry_zone_high=float(enriched_row.get("risk_reward_entry_zone_high", 0.0) or 0.0),
            risk_reward_entry_zone_text=str(enriched_row.get("risk_reward_entry_zone_text", "") or "").strip(),
            risk_reward_position_text=str(enriched_row.get("risk_reward_position_text", "") or "").strip(),
            risk_reward_invalidation_text=str(enriched_row.get("risk_reward_invalidation_text", "") or "").strip(),
            risk_reward_atr=float(enriched_row.get("risk_reward_atr", 0.0) or 0.0),
            atr14=float(row.get("atr14", 0.0) or 0.0),
            atr14_h4=float(row.get("atr14_h4", 0.0) or 0.0),
            event_mode_text=display_event_meta["event_mode_text"],
            event_active_name=display_event_meta["event_active_name"],
            event_active_time_text=display_event_meta["event_active_time_text"],
            event_scope_text=display_event_meta["event_scope_text"],
            event_applies=display_event_meta["event_applies"],
            tech_summary=str(row.get("tech_summary", "") or "").strip(),
            tech_summary_h4=str(row.get("tech_summary_h4", "") or "").strip(),
            h4_context_text=str(row.get("h4_context_text", "") or "").strip(),
            extra={
                "setup_kind": str(trade_grade.get("setup_kind", "") or "").strip(),
                "signal_side_basis": str(signal_side_meta.get("signal_side_basis", "") or "").strip(),
                "signal_side_reason": str(signal_side_meta.get("signal_side_reason", "") or "").strip(),
                "signal_side_long_votes": int(signal_side_meta.get("signal_side_long_votes", 0) or 0),
                "signal_side_short_votes": int(signal_side_meta.get("signal_side_short_votes", 0) or 0),
                "risk_reward_basis": str(enriched_row.get("risk_reward_basis", "") or "").strip(),
                "intraday_volatility": str(row.get("intraday_volatility", "") or "").strip(),
                "intraday_volatility_text": str(row.get("intraday_volatility_text", "") or "").strip(),
                "intraday_location": str(row.get("intraday_location", "") or "").strip(),
                "intraday_location_text": str(row.get("intraday_location_text", "") or "").strip(),
                "quote_live_reason": str(row.get("quote_live_reason", "") or "").strip(),
                "quote_live_reason_text": str(row.get("quote_live_reason_text", "") or "").strip(),
                "quote_live_diagnostic_text": str(row.get("quote_live_diagnostic_text", "") or "").strip(),
                "quote_live_delta_sec": float(row.get("quote_live_delta_sec", 0.0) or 0.0),
                "quote_live_max_age_sec": float(row.get("quote_live_max_age_sec", 0.0) or 0.0),
                "quote_tick_utc_text": str(row.get("quote_tick_utc_text", "") or "").strip(),
                "quote_now_utc_text": str(row.get("quote_now_utc_text", "") or "").strip(),
                "quote_broker_offset_sec": float(row.get("quote_broker_offset_sec", 0.0) or 0.0),
                "quote_offset_recalibrated": bool(row.get("quote_offset_recalibrated", False)),
                "quote_price_available": bool(row.get("quote_price_available", False)),
                "rsi14": row.get("rsi14"),
                "ma20": row.get("ma20"),
                "ma50": row.get("ma50"),
                "change_pct_24h": row.get("change_pct_24h"),
                "bollinger_upper": row.get("bollinger_upper"),
                "bollinger_mid": row.get("bollinger_mid"),
                "bollinger_lower": row.get("bollinger_lower"),
                "rsi14_h4": row.get("rsi14_h4"),
                "ma20_h4": row.get("ma20_h4"),
                "ma50_h4": row.get("ma50_h4"),
                "bollinger_upper_h4": row.get("bollinger_upper_h4"),
                "bollinger_mid_h4": row.get("bollinger_mid_h4"),
                "bollinger_lower_h4": row.get("bollinger_lower_h4"),
                "macd": row.get("macd"),
                "macd_signal": row.get("macd_signal"),
                "macd_histogram": row.get("macd_histogram"),
            },
        ).to_dict()
        item_payload.update(score_trade_opportunity(item_payload))
        items.append(item_payload)

    items = apply_alert_state_transitions(items, state_file=status_state_file, now=snapshot_time)
    recent_transitions = read_recent_transitions(state_file=status_state_file, now=snapshot_time)
    market_focus = build_global_market_focus(symbols, event_context=context)
    regime_summary = build_snapshot_regime_summary(items)
    portfolio_grade = build_portfolio_trade_grade(
        items,
        connected,
        event_risk_mode=event_risk_mode,
        event_context=context,
    )

    summary_lines = [
        f"当前共观察 {len(symbols)} 个品种，实时报价 {live_count} 个，非活跃或暂无报价 {inactive_count} 个。",
        f"事件纪律：{EVENT_RISK_MODES.get(str(event_risk_mode or 'normal').strip().lower(), '正常观察')}。",
        f"出手分级：{portfolio_grade['grade']}。{portfolio_grade['detail']}",
        regime_summary.get("regime_summary_text", "") or "当前环境样本不足，先服从点差与事件窗口纪律。",
        market_focus.get("hint_text", "") or "先看点差、美元方向和宏观事件窗口。",
    ]
    intraday_digest = [
        f"{item['symbol']} {item['intraday_context_text']}"
        for item in items
        if str(item.get("intraday_context_text", "") or "").strip()
    ]
    if intraday_digest:
        summary_lines.append(f"短线节奏：{'；'.join(intraday_digest[:3])}。")
    multi_timeframe_digest = [
        f"{item['symbol']} {item['multi_timeframe_context_text']}"
        for item in items
        if str(item.get("multi_timeframe_context_text", "") or "").strip()
    ]
    if multi_timeframe_digest:
        summary_lines.append(f"多周期一致性：{'；'.join(multi_timeframe_digest[:3])}。")
    key_level_digest = [
        f"{item['symbol']} {item['key_level_context_text']}"
        for item in items
        if str(item.get("key_level_context_text", "") or "").strip()
    ]
    if key_level_digest:
        summary_lines.append(f"关键位：{'；'.join(key_level_digest[:3])}。")
    breakout_digest = [
        f"{item['symbol']} {item['breakout_context_text']}"
        for item in items
        if str(item.get("breakout_context_text", "") or "").strip()
    ]
    if breakout_digest:
        summary_lines.append(f"突破确认：{'；'.join(breakout_digest[:3])}。")
    retest_digest = [
        f"{item['symbol']} {item['retest_context_text']}"
        for item in items
        if str(item.get("retest_context_text", "") or "").strip()
    ]
    if retest_digest:
        summary_lines.append(f"回踩确认：{'；'.join(retest_digest[:3])}。")
    risk_reward_digest = [
        f"{item['symbol']} {item['risk_reward_context_text']}"
        for item in items
        if str(item.get("risk_reward_context_text", "") or "").strip()
    ]
    if risk_reward_digest:
        summary_lines.append(f"风险回报：{'；'.join(risk_reward_digest[:3])}。")
    alert_status_digest = [
        (
            f"{item['symbol']} {item['alert_state_transition_text']}"
            if str(item.get("alert_state_transition_text", "") or "").strip()
            else f"{item['symbol']} {item['alert_state_text']}"
        )
        for item in items
        if int(item.get("alert_state_rank", 0) or 0) > 1
    ]
    if alert_status_digest:
        summary_lines.append(f"提醒状态：{'；'.join(alert_status_digest[:3])}。")
    transition_digest = [
        f"{str(item.get('symbol', '--') or '--').strip()}：{str(item.get('from_state', '') or '').strip()} -> {str(item.get('to_state', '') or '').strip()}"
        for item in recent_transitions
        if str(item.get("symbol", "") or "").strip() and str(item.get("to_state", "") or "").strip()
    ]
    if transition_digest:
        summary_lines.append(f"近30分钟迁移：{'；'.join(transition_digest[:3])}。")
    if (bool(context.get("auto_enabled")) or str(event_risk_mode or "normal").strip().lower() != "normal") and str(
        context.get("reason", "") or ""
    ).strip():
        summary_lines.append(f"纪律说明：{str(context.get('reason', '') or '').strip()}")
    if str(context.get("feed_status_text", "") or "").strip():
        summary_lines.append(f"事件源：{str(context.get('feed_status_text', '') or '').strip()}")
    if connection_message:
        summary_lines.append(connection_message)

    live_digest = [
        f"{item['symbol']} {item['latest_text']}"
        for item in items
        if bool(item.get("has_live_quote", False)) and item["latest_text"] != "--"
    ]
    event_mode = str(event_risk_mode or "normal").strip().lower()
    transition_summary_text = "；".join(
        [
            (
                f"{str(item.get('changed_at', '') or '').strip()} "
                f"{str(item.get('symbol', '--') or '--').strip()}："
                f"{str(item.get('from_state', '') or '').strip()} -> {str(item.get('to_state', '') or '').strip()}"
            ).strip()
            for item in recent_transitions
            if str(item.get("symbol", "") or "").strip() and str(item.get("to_state", "") or "").strip()
        ]
    )

    return {
        "status_badge": "MT5 已连接" if connected else "MT5 未连接",
        "status_tone": AlertTone.SUCCESS.value if connected else AlertTone.NEGATIVE.value,
        "status_hint": connection_message or "可继续观察点差、关键位和宏观窗口。",
        "summary_text": "\n".join(line for line in summary_lines if str(line).strip()),
        "regime_tag": str(regime_summary.get("regime_tag", "") or "").strip(),
        "regime_text": str(regime_summary.get("regime_text", "") or "").strip(),
        "regime_summary_text": str(regime_summary.get("regime_summary_text", "") or "").strip(),
        "alert_text": market_focus.get("alert_text", ""),
        "market_text": market_focus.get("market_text", ""),
        "trade_grade": portfolio_grade["grade"],
        "trade_grade_detail": portfolio_grade["detail"],
        "trade_next_review": portfolio_grade["next_review"],
        "trade_grade_tone": portfolio_grade["tone"],
        "event_risk_mode": event_mode,
        "event_risk_mode_text": EVENT_RISK_MODES.get(event_mode, "正常观察"),
        "event_risk_mode_source": str(context.get("source", "manual") or "manual").strip(),
        "event_risk_mode_source_text": str(context.get("source_text", "手动模式") or "手动模式").strip(),
        "event_risk_reason": str(context.get("reason", "") or "").strip(),
        "event_feed_status_text": str(context.get("feed_status_text", "") or "").strip(),
        "event_active_name": str(context.get("active_event_name", "") or "").strip(),
        "event_active_time_text": str(context.get("active_event_time_text", "") or "").strip(),
        "event_active_importance_text": str(context.get("active_event_importance_text", "") or "").strip(),
        "event_active_scope_text": str(context.get("active_event_scope_text", "") or "").strip(),
        "event_next_name": str(context.get("next_event_name", "") or "").strip(),
        "event_next_time_text": str(context.get("next_event_time_text", "") or "").strip(),
        "items": items,
        "alert_transition_summary_text": transition_summary_text,
        "runtime_status_cards": build_runtime_status_cards(
            connected=connected,
            connection_message=connection_message,
            items=items,
            watch_count=len(symbols),
            live_count=live_count,
            inactive_count=inactive_count,
        ),
        "spread_focus_cards": build_spread_focus_cards(items),
        "event_window_cards": build_event_window_cards(symbols, event_context=context),
        "alert_status_cards": build_alert_status_cards(items, transitions=recent_transitions),
        "macro_data_status_cards": [],  # 宏观数据状态卡片在 MonitorWorker 应用宏观数据后设置
        "watch_count": len(symbols),
        "live_count": live_count,
        "inactive_count": inactive_count,
        "live_digest": " | ".join(live_digest[:4]) if live_digest else "暂无有效实时报价",
        "last_refresh_text": snapshot_time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def run_monitor_cycle(
    symbols: list[str],
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
    history_file: Path | None = None,
    status_state_file: Path | None = None,
) -> dict:
    connected, connection_message = initialize_connection()
    rows = fetch_quotes(symbols, include_inactive=True) if connected else []
    return build_snapshot_from_rows(
        symbols,
        rows,
        connected,
        connection_message,
        event_risk_mode=event_risk_mode,
        event_context=event_context,
        history_file=history_file,
        status_state_file=status_state_file,
    )
