"""
消息推送：支持钉钉 Webhook 与 PushPlus。
"""
from __future__ import annotations

from datetime import datetime

from ai_signal_audit import resolve_ai_signal_execution_audit
from app_config import MetalMonitorConfig
from quote_models import SnapshotItem
from signal_enums import AlertTone
from signal_protocol import normalize_signal_meta


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一通知推送链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _build_user_facing_title(entry: dict) -> str:
    """将内部标题统一映射成更适合用户读取的状态更新标题。"""
    category = _normalize_text(entry.get("category", "")).lower()
    original_title = _normalize_text(entry.get("title", "")) or "提醒"
    symbol = _normalize_text(entry.get("symbol", "")).upper()
    if not symbol:
        maybe_symbol = original_title.split(" ", 1)[0].strip().upper()
        if maybe_symbol and maybe_symbol.isascii() and any(ch.isalpha() for ch in maybe_symbol):
            symbol = maybe_symbol
    event_name = _normalize_text(entry.get("event_name", ""))
    signal_side = _normalize_text(entry.get("signal_side", "")).lower()
    structure_stage = _normalize_text(entry.get("structure_entry_stage", "")).lower()

    if category == "structure":
        if symbol:
            if structure_stage == "inside_zone":
                if signal_side == "long":
                    return f"{symbol} 机会更新：多单到位"
                if signal_side == "short":
                    return f"{symbol} 机会更新：空单到位"
                return f"{symbol} 机会更新：位置到位"
            if structure_stage == "near_zone":
                return f"{symbol} 机会更新：接近位置"
            return f"{symbol} 机会更新：继续等待"
    if category == "structure_cancel":
        if symbol:
            return f"{symbol} 机会更新：已失效"
        return "机会更新：已失效"
    if category == "spread":
        if symbol:
            return f"{symbol} 风控更新：点差过宽"
        return "风控更新：点差过宽"
    if category == "recovery":
        if symbol:
            return f"{symbol} 风控更新：点差恢复"
        return "风控更新：点差恢复"
    if category == "macro":
        if event_name:
            return f"{event_name} 状态更新：继续观望"
        if symbol:
            return f"{symbol} 状态更新：继续观望"
        return "状态更新：继续观望"
    if category == "source":
        return "系统状态更新：外部数据降级"
    if category == "session":
        if symbol:
            return f"{symbol} 状态更新：暂不交易"
        return "状态更新：暂不交易"
    if category == "mt5":
        return "系统状态更新：MT5 链路异常"
    if category == "ai":
        ai_rule_eligible = entry.get("ai_rule_eligible")
        if symbol:
            if signal_side == "long":
                if ai_rule_eligible is False:
                    return f"{symbol} 动作更新：偏多观察"
                return f"{symbol} 动作更新：可准备做多"
            if signal_side == "short":
                if ai_rule_eligible is False:
                    return f"{symbol} 动作更新：偏空观察"
                return f"{symbol} 动作更新：可准备做空"
            return f"{symbol} 动作更新：先别动手"
        return "动作更新：先别动手"
    return original_title


def _format_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):,.{decimals}f}"


def _clip_text(value: str, limit: int = 68) -> str:
    text = _normalize_text(value)
    if len(text) <= max(12, int(limit)):
        return text
    return text[: max(12, int(limit)) - 1].rstrip() + "…"


def _append_block(lines: list[str], title: str, items: list[str]) -> None:
    payload = [str(item or "").strip() for item in items if str(item or "").strip()]
    if not payload:
        return
    lines.extend(["", f"**{title}**"])
    lines.extend(payload)


def _format_learning_rule_lines(rules: list[str], limit: int = 3) -> list[str]:
    """统一学习摘要里的规则列表缩进，避免 Markdown 粘连。"""
    formatted: list[str] = []
    for rule in rules[: max(0, int(limit))]:
        text = _normalize_text(rule)
        if not text:
            continue
        text = text.lstrip("-•> ").strip()
        formatted.append(f"  > {text}")
    return formatted


def _format_spread_points(value: float) -> str:
    spread = float(value or 0.0)
    if abs(spread - round(spread)) < 1e-6:
        return f"{int(round(spread))}点"
    return f"{spread:.1f}点"


def _to_float(value) -> float:
    try:
        if value in ("", None):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _pick_first_text(entry: dict, *keys: str) -> str:
    for key in keys:
        text = _normalize_text(entry.get(key, ""))
        if text:
            return text
    return ""


def _pick_first_number(entry: dict, *keys: str) -> float:
    for key in keys:
        value = _to_float(entry.get(key))
        if value > 0:
            return value
    return 0.0


def _format_rr_as_r(value: float) -> str:
    rr = float(value or 0.0)
    if rr <= 0:
        return "--"
    text = f"{rr:.2f}".rstrip("0").rstrip(".")
    return f"{text}R"


def _resolve_card_side(entry: dict) -> str:
    side = _normalize_text(
        entry.get("opportunity_action", "")
        or entry.get("signal_side", "")
        or entry.get("risk_reward_direction", "")
    ).lower()
    if side in {"long", "bullish", "buy"}:
        return "long"
    if side in {"short", "bearish", "sell"}:
        return "short"
    return "watch"


def _build_short_term_card_text(entry: dict, side: str) -> str:
    text = _normalize_text(entry.get("opportunity_action_text", ""))
    if text:
        if side == "long" and "做多" in text:
            return "偏多，可轻仓试多" if "可提醒" in text else "偏多，重点观察"
        if side == "short" and "做空" in text:
            return "偏空，可轻仓试空" if "可提醒" in text else "偏空，重点观察"
        return text
    trade_grade = _normalize_text(entry.get("trade_grade", ""))
    ai_rule_eligible = entry.get("ai_rule_eligible")
    if side == "long":
        if ai_rule_eligible is False:
            return "偏多，先观察"
        if trade_grade == "可轻仓试仓":
            return "偏多，可轻仓试多"
        return "偏多，等回踩确认"
    if side == "short":
        if ai_rule_eligible is False:
            return "偏空，先观察"
        if trade_grade == "可轻仓试仓":
            return "偏空，可轻仓试空"
        return "偏空，等反抽确认"
    return trade_grade or "方向不明，继续观察"


def _build_long_term_card_text(entry: dict, side: str) -> str:
    text = _normalize_text(entry.get("opportunity_long_term_text", ""))
    if text:
        return text
    h4_text = _pick_first_text(entry, "h4_context_text", "tech_summary_h4")
    if h4_text:
        return _clip_text(h4_text, 46)
    timeframe = _normalize_text(entry.get("opportunity_timeframe", "")).lower()
    if side == "long":
        if timeframe == "short_term":
            return "多头趋势未破，但当前位置不适合追"
        return "多头背景仍在，等待回踩确认"
    if side == "short":
        if timeframe == "short_term":
            return "空头压力未破，但当前位置不适合追"
        return "空头背景仍在，等待反抽确认"
    regime_text = _pick_first_text(entry, "regime_text", "regime_reason")
    if regime_text:
        return _clip_text(regime_text, 46)
    return "长线方向未确认，先不做隔夜判断"


def _build_card_risk_text(entry: dict) -> str:
    event_name = _normalize_text(entry.get("event_name", "") or entry.get("event_active_name", ""))
    event_time = _normalize_text(entry.get("event_time_text", "") or entry.get("event_active_time_text", ""))
    event_importance = _normalize_text(entry.get("event_importance_text", ""))
    external_note = _normalize_text(entry.get("external_bias_note", ""))
    event_note = _normalize_text(entry.get("event_note", ""))
    model_note = _normalize_text(entry.get("model_note", ""))
    ai_rule_reason = _normalize_text(entry.get("ai_rule_reason", ""))
    parts: list[str] = []
    if entry.get("ai_rule_eligible") is False and ai_rule_reason:
        parts.append(_clip_text(f"规则未放行：{ai_rule_reason}", 42))
    if external_note:
        parts.append(_clip_text(external_note, 34))
    if event_name or event_time:
        event_bits = " ".join(bit for bit in (event_importance, event_time, event_name) if bit)
        parts.append(_clip_text(event_bits, 34))
    elif event_note:
        parts.append(_clip_text(event_note, 42))
    if model_note:
        parts.append(_clip_text(model_note, 34))
    return "；".join(parts[:2]) or "暂无额外高风险提示，仍需严格止损"


def _build_card_suggestion_text(entry: dict, side: str) -> str:
    timeframe = _normalize_text(entry.get("opportunity_timeframe", "")).lower()
    event_risk = bool(entry.get("event_applies", False)) or bool(
        _normalize_text(entry.get("event_name", "") or entry.get("event_active_name", "") or entry.get("event_note", ""))
    )
    if entry.get("ai_rule_eligible") is False:
        return "规则未放行，只观察，不下单。"
    if side in {"long", "short"} and (timeframe in {"short_term", "mixed"} or event_risk):
        return "只做短线，不隔夜。"
    explicit = _normalize_text(entry.get("opportunity_suggestion", "") or entry.get("position_plan_text", ""))
    if explicit:
        return _clip_text(explicit, 42)
    if side in {"long", "short"}:
        return "等确认后轻仓执行，价格离开入场区就放弃。"
    return "继续观察，等下一轮更干净的机会。"


def _build_trade_plan_card(entry: dict, price_point: float) -> list[str]:
    symbol = _normalize_text(entry.get("symbol", "")) or "当前品种"
    side = _resolve_card_side(entry)
    entry_zone = _pick_first_text(entry, "opportunity_entry_zone_text", "entry_zone_text", "risk_reward_entry_zone_text")
    stop_loss = _pick_first_number(entry, "opportunity_stop_price", "stop_loss_price", "risk_reward_stop_price")
    target_1 = _pick_first_number(entry, "opportunity_target_price", "take_profit_1", "risk_reward_target_price")
    target_2 = _pick_first_number(entry, "opportunity_target_price_2", "take_profit_2", "risk_reward_target_price_2")
    rr = _pick_first_number(entry, "opportunity_risk_reward_ratio", "risk_reward_ratio")
    score = int(_pick_first_number(entry, "opportunity_score", "signal_score"))

    lines = [
        f"### {symbol}",
        f"- 短线：{_build_short_term_card_text(entry, side)}",
        f"- 长线：{_build_long_term_card_text(entry, side)}",
        "",
    ]
    if entry_zone:
        lines.append(f"- 入场区：{_clip_text(entry_zone, 54)}")
    if stop_loss > 0:
        lines.append(f"- 止损：{_format_price(stop_loss, price_point)}")
    if target_1 > 0:
        lines.append(f"- 止盈1：{_format_price(target_1, price_point)}")
    if target_2 > 0:
        lines.append(f"- 止盈2：{_format_price(target_2, price_point)}")
    if rr > 0:
        lines.append(f"- 盈亏比：{_format_rr_as_r(rr)}")
    if score > 0:
        lines.append(f"- 信号评分：{min(100, score)}/100")
    lines.append(f"- 风险：{_build_card_risk_text(entry)}")
    lines.append(f"- 建议：{_build_card_suggestion_text(entry, side)}")
    return lines


def _build_concise_status_card(entry: dict, category_key: str, price_point: float) -> list[str]:
    symbol = _normalize_text(entry.get("symbol", ""))
    trade_grade = _normalize_text(entry.get("trade_grade", ""))
    detail = _normalize_text(entry.get("trade_grade_detail", "") or entry.get("detail", ""))
    review = _normalize_text(entry.get("trade_next_review", ""))
    latest_price = _pick_first_number(entry, "baseline_latest_price", "latest_price")
    spread = _pick_first_number(entry, "baseline_spread_points", "spread_points")
    event_note = _normalize_text(entry.get("event_note", ""))
    event_name = _normalize_text(entry.get("event_name", ""))
    event_time = _normalize_text(entry.get("event_time_text", ""))

    reason = event_note or detail or _build_notify_execution_state_text(entry, category_key)
    action_map = {
        "spread": "先别下单，等点差恢复。",
        "macro": "先观望，等事件落地后再决定。",
        "source": "外部数据降级，自动判断只作参考。",
        "session": "当前不适合下单，等待活跃报价。",
        "mt5": "MT5 链路未稳定，暂停依赖实时信号。",
        "recovery": "点差已恢复，重新等待理想位置。",
        "structure_cancel": "上一条机会作废，重新等下一次提醒。",
    }
    action = action_map.get(category_key, "先按当前状态处理。")
    if category_key == "macro" and (event_name or event_time):
        reason = " | ".join(part for part in (event_name, event_time) if part)
    if category_key == "structure_cancel":
        reason = _normalize_text(entry.get("invalidated_from_title", "")) or reason

    lines = []
    if symbol:
        lines.append(f"- 品种：{symbol}")
    if trade_grade:
        lines.append(f"- 状态：{trade_grade}")
    elif category_key:
        lines.append(f"- 状态：{_build_user_facing_title(entry)}")
    if latest_price > 0:
        price_text = _format_price(latest_price, price_point)
        if spread > 0:
            price_text += f" | 点差 {_format_spread_points(spread)}"
        lines.append(f"- 当前价：{price_text}")
    lines.append(f"- 原因：{_clip_text(reason, 58)}")
    lines.append(f"- 动作：{_clip_text(action, 42)}")
    if review:
        lines.append(f"- 复核：{_clip_text(review, 48)}")
    return lines[:6]


def _build_notify_execution_state_text(entry: dict, category_key: str) -> str:
    stage = _normalize_text(entry.get("structure_entry_stage", "")).lower()
    signal_side = _normalize_text(entry.get("signal_side", "")).lower()
    if category_key == "ai":
        ai_rule_eligible = entry.get("ai_rule_eligible")
        ai_rule_reason = _normalize_text(entry.get("ai_rule_reason", ""))
        if signal_side == "long":
            if ai_rule_eligible is False:
                return _clip_text(f"AI 偏向做多，但规则层仍未放行；{ai_rule_reason or '继续观察。'}", 68)
            return "AI 偏向做多，但仍要等回踩承接确认。"
        if signal_side == "short":
            if ai_rule_eligible is False:
                return _clip_text(f"AI 偏向做空，但规则层仍未放行；{ai_rule_reason or '继续观察。'}", 68)
            return "AI 偏向做空，但仍要等反抽承压确认。"
        return "AI 当前不给执行信号，继续观察。"
    if category_key == "structure":
        if stage == "inside_zone":
            if signal_side == "long":
                return "已进候选做多区，只等回踩承接确认。"
            if signal_side == "short":
                return "已进候选做空区，只等反抽承压确认。"
            return "已进候选区，但方向还不够干净。"
        if stage == "near_zone":
            return "接近执行位，还差最后确认，不追单。"
        return "当前仍是观察状态，别提前动手。"
    if category_key == "spread":
        return "点差未恢复前，默认不出手。"
    if category_key == "macro":
        return "事件落地前，默认只观察不执行。"
    if category_key == "recovery":
        return "点差刚恢复，先等位置和节奏重新配合。"
    if category_key in {"session", "mt5"}:
        return "链路未恢复前，所有动作都暂停。"
    trade_grade = _normalize_text(entry.get("trade_grade", ""))
    if trade_grade:
        return trade_grade
    return "等下一轮刷新后再决定。"


def _build_markdown(entry: dict) -> str:
    title         = _build_user_facing_title(entry)
    markdown_body = str(entry.get("markdown_body", "") or "").strip()
    occurred_at   = str(entry.get("occurred_at", "--") or "--").strip()
    category      = str(entry.get("category", "general") or "general").strip()
    aggregate_count       = int(entry.get("aggregate_count", 0) or 0)
    notify_mode_text      = _normalize_text(entry.get("notify_mode_text", ""))
    price_point           = float(entry.get("price_point", 0.0) or 0.0)

    if markdown_body:
        return markdown_body

    # ── 分类 emoji 映射 ──
    CATEGORY_EMOJI = {
        "spread": "⚠️", "recovery": "✅", "structure": "📐",
        "event": "📅", "source": "🛰️", "ai": "🤖", "general": "📊",
    }
    emoji = CATEGORY_EMOJI.get(category, "📊")

    category_key = category.lower()
    lines = [f"## {emoji}【{title}】", ""]
    if category_key in {"structure", "ai", "opportunity"}:
        lines.extend(_build_trade_plan_card(entry, price_point))
    else:
        lines.extend(_build_concise_status_card(entry, category_key, price_point))

    # ── 合并提醒说明 ──
    if aggregate_count > 1:
        lines.extend(["", f"> 同类提醒近一轮累计 **{aggregate_count}** 条，已合并发送"])

    if notify_mode_text:
        lines.extend(["", f"- 推送策略：{notify_mode_text}"])

    lines.append("")
    lines.append(f"> 仅供参考，严格止损。{occurred_at}")
    return "\n".join(lines)


def _pick_ai_focus_item(signal_meta: dict, items: list[dict]) -> dict:
    target_symbol = str(signal_meta.get("symbol", "") or "").strip().upper()
    if target_symbol:
        for item in items:
            if str(item.get("symbol", "") or "").strip().upper() == target_symbol:
                return dict(item)
    for item in items:
        if str(item.get("symbol", "") or "").strip():
            return dict(item)
    return {}


def _extract_ai_summary_line(content: str) -> str:
    for line in str(content or "").splitlines():
        text = _normalize_text(line)
        if text:
            return text
    return ""


def _build_ai_action_summary(signal_meta: dict, item: dict, summary_line: str) -> str:
    action = str(signal_meta.get("action", "neutral") or "neutral").strip().lower()
    symbol = str(signal_meta.get("symbol", "") or item.get("symbol", "") or "").strip().upper() or "当前品种"
    ai_rule_eligible = item.get("ai_rule_eligible")
    if action == "long":
        if ai_rule_eligible is False:
            return f"{symbol} AI 偏向做多，但规则层仍未放行，先观察。"
        return f"{symbol} 可准备做多，先等确认后再动手。"
    if action == "short":
        if ai_rule_eligible is False:
            return f"{symbol} AI 偏向做空，但规则层仍未放行，先观察。"
        return f"{symbol} 可准备做空，先等确认后再动手。"
    if summary_line:
        return _clip_text(summary_line, 40)
    return f"{symbol} 当前先不出手。"


def _build_ai_markdown_body(result: dict, item: dict, signal_meta: dict, occurred_at: str) -> str:
    model_name = str((result or {}).get("model", "--") or "--").strip()
    action = str(signal_meta.get("action", "neutral") or "neutral").strip().lower()
    symbol = str(signal_meta.get("symbol", "") or item.get("symbol", "") or "").strip().upper() or "--"
    content = str((result or {}).get("content", "") or "").strip()
    summary_line = _extract_ai_summary_line(content)
    price_point = float(item.get("point", 0.0) or 0.0)
    latest_price = float(item.get("latest_price", 0.0) or 0.0)
    bid_price = float(item.get("bid", 0.0) or 0.0)
    ask_price = float(item.get("ask", 0.0) or 0.0)
    spread_points = float(item.get("spread_points", 0.0) or 0.0)
    entry_zone_text = _normalize_text(item.get("risk_reward_entry_zone_text", ""))
    event_note = _normalize_text(item.get("event_note", ""))
    event_name = _normalize_text(item.get("event_active_name", ""))
    event_time_text = _normalize_text(item.get("event_active_time_text", ""))
    external_bias_note = _normalize_text(item.get("external_bias_note", ""))
    ai_rule_eligible = item.get("ai_rule_eligible")
    ai_rule_reason = _normalize_text(item.get("ai_rule_reason", ""))

    lines = [f"## 🤖【AI 动作提醒：{symbol}】", ""]
    headline = [f"- 时间：{occurred_at}"]
    if action == "long":
        headline.append("- 结论：**偏多观察**" if ai_rule_eligible is False else "- 结论：**可准备做多**")
    elif action == "short":
        headline.append("- 结论：**偏空观察**" if ai_rule_eligible is False else "- 结论：**可准备做空**")
    else:
        headline.append("- 结论：**先别动手**")
    headline.append(f"- 品种：{symbol}")
    if latest_price > 0:
        headline.append(f"- 价格：{_format_price(latest_price, price_point)}")
    quote_parts = []
    if bid_price > 0:
        quote_parts.append(f"Bid {_format_price(bid_price, price_point)}")
    if ask_price > 0:
        quote_parts.append(f"Ask {_format_price(ask_price, price_point)}")
    if spread_points > 0:
        quote_parts.append(f"点差 {_format_spread_points(spread_points)}")
    if quote_parts:
        headline.append(f"- 盘口：{' / '.join(quote_parts[:2])}" + (f" · {quote_parts[2]}" if len(quote_parts) > 2 else ""))
    lines.extend(headline)

    action_lines: list[str] = []
    price = float(signal_meta.get("price", 0.0) or 0.0)
    sl = float(signal_meta.get("sl", 0.0) or 0.0)
    tp = float(signal_meta.get("tp", 0.0) or 0.0)
    ai_rr = 0.0
    if action == "long":
        if ai_rule_eligible is False:
            action_lines.append("- 动作：AI 偏多，但这轮还没到可执行级别，继续等回踩确认。")
        else:
            action_lines.append("- 动作：等回踩承接确认后再做多，不追已经离开位置的拉升。")
    elif action == "short":
        if ai_rule_eligible is False:
            action_lines.append("- 动作：AI 偏空，但这轮还没到可执行级别，继续等反抽确认。")
        else:
            action_lines.append("- 动作：等反抽承压确认后再做空，不追已经离开位置的杀跌。")
    else:
        action_lines.append("- 动作：这次先不做，继续观察下一次更干净的位置。")
    if price > 0:
        action_lines.append(f"- 进场：{_format_price(price, price_point)}")
    if sl > 0:
        action_lines.append(f"- 止损：{_format_price(sl, price_point)}")
    if tp > 0:
        action_lines.append(f"- 目标：{_format_price(tp, price_point)}")
    if min(price, sl, tp) > 0:
        if action == "long" and price > sl:
            ai_rr = abs(tp - price) / max(abs(price - sl), 1e-6)
            action_lines.append(f"- 盈亏比：1:{ai_rr:.2f}")
        elif action == "short" and sl > price:
            ai_rr = abs(price - tp) / max(abs(sl - price), 1e-6)
            action_lines.append(f"- 盈亏比：1:{ai_rr:.2f}")
    if entry_zone_text:
        action_lines.append(f"- 盯盘：{_clip_text(entry_zone_text, 60)}")
    elif summary_line:
        action_lines.append(f"- 说明：{_clip_text(summary_line, 60)}")
    decision_entry = {
        "category": "ai",
        "symbol": symbol,
        "signal_side": action,
        "detail": summary_line or _build_ai_action_summary(signal_meta, item, summary_line),
        "trade_grade": _normalize_text(item.get("trade_grade", "")),
        "entry_zone_text": entry_zone_text,
        "baseline_spread_points": spread_points,
        "alert_state_text": _normalize_text(item.get("alert_state_text", "")),
        "alert_state_detail": _normalize_text(item.get("alert_state_detail", "")),
        "event_note": event_note,
        "event_name": event_name,
        "event_time_text": event_time_text,
        "event_importance_text": _normalize_text(item.get("event_importance_text", "")),
        "event_applies": bool(item.get("event_applies", False)),
        "external_bias_note": external_bias_note,
        "h4_context_text": _normalize_text(item.get("h4_context_text", "")),
        "tech_summary_h4": _normalize_text(item.get("tech_summary_h4", "")),
        "regime_text": _normalize_text(item.get("regime_text", "")),
        "regime_reason": _normalize_text(item.get("regime_reason", "")),
        "opportunity_action": _normalize_text(item.get("opportunity_action", "")),
        "opportunity_action_text": _normalize_text(item.get("opportunity_action_text", "")),
        "opportunity_timeframe": _normalize_text(item.get("opportunity_timeframe", "")),
        "opportunity_score": item.get("opportunity_score", 0),
        "opportunity_entry_zone_text": _normalize_text(item.get("opportunity_entry_zone_text", "")),
        "opportunity_stop_price": item.get("opportunity_stop_price", 0.0),
        "opportunity_target_price": item.get("opportunity_target_price", 0.0),
        "opportunity_target_price_2": item.get("opportunity_target_price_2", 0.0),
        "opportunity_risk_reward_ratio": item.get("opportunity_risk_reward_ratio", 0.0),
        "trade_next_review": "仅当前短时有效，若价格离开位置或下一两轮无确认，请直接忽略。",
        "stop_loss_price": sl,
        "take_profit_1": tp,
        "risk_reward_ratio": ai_rr,
        "ai_rule_eligible": ai_rule_eligible,
        "ai_rule_reason": ai_rule_reason,
    }
    lines.extend(_build_trade_plan_card(decision_entry, price_point))
    if summary_line:
        lines.append(f"- 理由：{_clip_text(summary_line, 48)}")
    lines.append("")
    lines.append(f"> 仅供参考，严格止损。{occurred_at} | {model_name}")
    return "\n".join(lines)


def _build_ai_brief_entry(result: dict, snapshot: dict, config: MetalMonitorConfig) -> dict:
    items = [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]
    normalized_signal_meta = normalize_signal_meta((result or {}).get("signal_meta"))
    focus_item = _pick_ai_focus_item(normalized_signal_meta, items)
    symbols = [str(item.get("symbol", "") or "").strip().upper() for item in items if str(item.get("symbol", "") or "").strip()]
    focus_symbol = str(
        normalized_signal_meta.get("symbol", "") or focus_item.get("symbol", "") or (symbols[0] if symbols else "")
    ).strip().upper()
    content = str((result or {}).get("content", "") or "").strip()
    summary_line = _extract_ai_summary_line(content)
    if bool(config.ai_push_summary_only):
        content = summary_line

    ai_audit = resolve_ai_signal_execution_audit(snapshot, symbol=focus_symbol)
    focus_item = dict(focus_item)
    focus_item["ai_rule_eligible"] = ai_audit["sim_eligible"] if ai_audit["audit_available"] else None
    focus_item["ai_rule_reason"] = ai_audit["sim_block_reason"]

    occurred_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    markdown_body = _build_ai_markdown_body(result, focus_item, normalized_signal_meta, occurred_at)
    detail_text = _build_ai_action_summary(normalized_signal_meta, focus_item, summary_line) or content or "模型未返回有效结论。"

    payload = {
        "occurred_at": occurred_at,
        "category": "ai",
        "title": f"AI 研判：{focus_symbol}" if focus_symbol else ("AI 研判：" + " / ".join(symbols[:3]) if symbols else "AI 研判已生成"),
        "detail": detail_text,
        "tone": AlertTone.ACCENT.value,
        "symbol": focus_symbol,
        "signal_side": str(normalized_signal_meta.get("action", "neutral") or "neutral").strip().lower(),
        "ai_rule_eligible": focus_item.get("ai_rule_eligible"),
        "ai_rule_reason": focus_item.get("ai_rule_reason", ""),
        "markdown_body": markdown_body,
    }
    payload["raw_title"] = payload["title"]
    payload["title"] = _build_user_facing_title(payload)
    payload["signature"] = f"ai::{payload['raw_title']}::{occurred_at}"
    return payload


def _build_learning_report_entry(report: dict) -> dict:
    summary_text = _normalize_text((report or {}).get("summary_text", "") or "")
    created_at = _normalize_text((report or {}).get("created_at", "") or "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    governance_summary = _normalize_text(((report or {}).get("governance_summary", {}) or {}).get("summary_text", "") or "")
    active_rules = [str(item).strip() for item in list((report or {}).get("active_rules", []) or []) if str(item).strip()]
    watch_rules = [str(item).strip() for item in list((report or {}).get("watch_rules", []) or []) if str(item).strip()]
    frozen_rules = [str(item).strip() for item in list((report or {}).get("frozen_rules", []) or []) if str(item).strip()]
    promoted_rules = [str(item).strip() for item in list((report or {}).get("promoted_rules", []) or []) if str(item).strip()]
    new_watch_rules = [str(item).strip() for item in list((report or {}).get("new_watch_rules", []) or []) if str(item).strip()]
    new_frozen_rules = [str(item).strip() for item in list((report or {}).get("new_frozen_rules", []) or []) if str(item).strip()]
    recovered_rules = [str(item).strip() for item in list((report or {}).get("recovered_rules", []) or []) if str(item).strip()]
    feedback_summary_text = _normalize_text(((report or {}).get("feedback_summary", {}) or {}).get("summary_text", "") or "")
    alert_effect_summary_text = _normalize_text(((report or {}).get("alert_effect_summary", {}) or {}).get("summary_text", "") or "")
    missed_opportunity_summary_text = _normalize_text(((report or {}).get("missed_opportunity_summary", {}) or {}).get("summary_text", "") or "")
    feedback_actions = [
        dict(item)
        for item in list((((report or {}).get("feedback_summary", {}) or {}).get("action_suggestions", [])) or [])
        if isinstance(item, dict)
    ]
    top_positive_rules = [str(item).strip() for item in list((((report or {}).get("feedback_summary", {}) or {}).get("top_positive_rules", [])) or []) if str(item).strip()]
    top_negative_rules = [str(item).strip() for item in list((((report or {}).get("feedback_summary", {}) or {}).get("top_negative_rules", [])) or []) if str(item).strip()]

    markdown_lines = [
        "### 知识库学习摘要",
        "",
        f"- 时间：{created_at}",
        f"- 总结：{summary_text or '当前暂无可推送的学习结论。'}",
    ]
    if governance_summary:
        markdown_lines.append(f"- 治理概况：{governance_summary}")
    if feedback_summary_text:
        markdown_lines.append(f"- 用户反馈：{feedback_summary_text}")
    if alert_effect_summary_text:
        markdown_lines.append(f"- 提醒效果：{alert_effect_summary_text}")
    if missed_opportunity_summary_text:
        markdown_lines.append(f"- 漏机会：{missed_opportunity_summary_text}")
    if feedback_actions:
        markdown_lines.extend(["", "#### 反馈驱动建议", ""])
        for item in feedback_actions[:3]:
            title = _normalize_text(item.get("title", ""))
            suggestion = _normalize_text(item.get("suggestion", ""))
            reason = _normalize_text(item.get("reason", ""))
            if suggestion:
                markdown_lines.append(f"- {title or '调整建议'}：{suggestion}" + (f"（{reason}）" if reason else ""))
    if promoted_rules or new_watch_rules or new_frozen_rules or recovered_rules:
        markdown_lines.extend(["", "#### 本轮状态变化", ""])
        if promoted_rules:
            markdown_lines.append(f"- 新增启用：{len(promoted_rules)} 条")
            markdown_lines.extend(_format_learning_rule_lines(promoted_rules))
        if new_watch_rules:
            markdown_lines.append(f"- 新增观察：{len(new_watch_rules)} 条")
            markdown_lines.extend(_format_learning_rule_lines(new_watch_rules))
        if new_frozen_rules:
            markdown_lines.append(f"- 新冻结：{len(new_frozen_rules)} 条")
            markdown_lines.extend(_format_learning_rule_lines(new_frozen_rules))
        if recovered_rules:
            markdown_lines.append(f"- 冻结恢复：{len(recovered_rules)} 条")
            markdown_lines.extend(_format_learning_rule_lines(recovered_rules))
    if active_rules:
        markdown_lines.extend(["", "#### 当前有效规则", ""])
        markdown_lines.extend(_format_learning_rule_lines(active_rules))
    if watch_rules:
        markdown_lines.extend(["", "#### 候选观察规则", ""])
        markdown_lines.extend(_format_learning_rule_lines(watch_rules))
    if frozen_rules:
        markdown_lines.extend(["", "#### 暂不采用规则", ""])
        markdown_lines.extend(_format_learning_rule_lines(frozen_rules))
    if top_positive_rules:
        markdown_lines.extend(["", "#### 用户认可度较高", ""])
        markdown_lines.extend(_format_learning_rule_lines(top_positive_rules))
    if top_negative_rules:
        markdown_lines.extend(["", "#### 用户负反馈较多", ""])
        markdown_lines.extend(_format_learning_rule_lines(top_negative_rules))

    return {
        "occurred_at": created_at,
        "category": "learning",
        "title": "知识库学习摘要",
        "detail": summary_text or "当前暂无可推送的学习结论。",
        "tone": AlertTone.ACCENT.value,
        "markdown_body": "\n".join(markdown_lines),
    }


def _build_learning_health_entry(report: dict) -> dict:
    occurred_at = _normalize_text((report or {}).get("occurred_at", "") or "") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_text = _normalize_text((report or {}).get("status_text", "") or "") or "学习状态更新"
    summary_text = _normalize_text((report or {}).get("summary_text", "") or "") or "自动学习状态已更新。"
    latest_rule = _normalize_text((report or {}).get("latest_rule_text", "") or "")
    tone = _normalize_text((report or {}).get("tone", AlertTone.ACCENT.value) or AlertTone.ACCENT.value)

    concise_detail = summary_text
    if status_text == "恢复产出":
        concise_detail = "本轮已有新规则入库，学习链恢复正常。"
    elif status_text == "样本积累中":
        concise_detail = "当前没有可反思新样本，继续等待积累。"
    elif status_text == "质量闸门拦截":
        concise_detail = "候选已生成，但主要被质量闸门拦截。"
    elif status_text == "去重拦截":
        concise_detail = "候选已生成，但主要被去重机制拦截。"
    elif status_text == "24h无新增":
        concise_detail = "最近24小时没有新增规则，建议关注样本积累。"
    elif status_text == "深挖异常":
        concise_detail = "深度挖掘异常结束，请尽快检查日志。"

    markdown_lines = [
        "### 自动学习状态变化",
        "",
        f"- 时间：{occurred_at}",
        f"- 状态：{status_text}",
        f"- 摘要：{summary_text}",
    ]
    if latest_rule:
        markdown_lines.append(f"- 最近规则：{latest_rule}")

    return {
        "occurred_at": occurred_at,
        "category": "learning_health",
        "title": f"学习状态：{status_text}",
        "detail": concise_detail,
        "tone": tone or AlertTone.ACCENT.value,
        "markdown_body": "\n".join(markdown_lines),
    }
