"""
探索试仓回放器。

用途：读取历史执行审计里的 grade_gate 样本，用当前探索试仓规则重新评估，
估算新通道会释放多少模拟开仓，以及是否存在单日过密风险。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from signal_enums import TradeGrade
from sim_signal_bridge import (
    audit_rule_sim_signal_decision,
    build_rule_sim_signal_decision,
    _is_price_near_entry_zone,
    _resolve_entry_zone_position,
    _resolve_signal_side,
)


_GRADE_GATE_SECONDARY_LABELS = {
    "event_gate": "事件窗口",
    "source_gate": "非结构型信号",
    "grade_not_observe": "结构等级偏低",
    "rr_not_ready": "盈亏比未准备好",
    "rr_too_low": "RR不足",
    "risk_reward_state_bad": "盈亏比状态不佳",
    "multi_timeframe_misaligned": "多周期未同向",
    "direction_unclear": "方向不清晰",
    "target_incomplete": "止损目标不完整",
    "entry_zone_miss": "未回到执行区",
    "chasing_upper": "上沿追价",
    "chasing_lower": "下沿追空",
    "unknown": "待继续细分",
}

_RR_NOT_READY_TERTIARY_LABELS = {
    "no_price": "现价缺失",
    "no_direction": "方向基础不足",
    "atr_missing_no_key_levels": "ATR缺失且关键位不足",
    "key_range_invalid": "关键位区间无效",
    "price_span_too_small": "止损目标跨度过小",
    "entry_zone_missing": "入场区间未生成",
    "unknown": "待继续细分",
}

_NO_DIRECTION_COMPONENT_LABELS = {
    "signal_side_missing": "信号方向缺失",
    "intraday_sideways": "日内方向震荡",
    "multi_not_aligned": "多周期未同向",
    "breakout_direction_neutral": "突破方向中性",
    "breakout_state_none": "突破未确认",
    "retest_state_none": "回踩未确认",
}


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _load_feature_payload(raw_json: str) -> dict:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _row_value(row, key: str, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        return default


def _parse_datetime_text(value: object) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt, length in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            return datetime.strptime(text[:length], fmt)
        except ValueError:
            continue
    return None


def _normalize_block_reason_text(value: object) -> str:
    text = _normalize_text(value)
    if "：" in text:
        text = text.split("：", 1)[1].strip()
    return text or "未返回可解释原因"


def _diagnose_grade_gate_secondary(item: dict) -> tuple[str, str]:
    trade_grade = _normalize_text(item.get("trade_grade", ""))
    trade_grade_source = _normalize_text(item.get("trade_grade_source", "")).lower()
    event_note = _normalize_text(item.get("event_note", ""))
    if "事件" in trade_grade or "事件" in event_note:
        return "event_gate", _GRADE_GATE_SECONDARY_LABELS["event_gate"]
    if trade_grade_source not in {"structure", "setup"}:
        return "source_gate", _GRADE_GATE_SECONDARY_LABELS["source_gate"]
    if trade_grade != TradeGrade.OBSERVE_ONLY:
        return "grade_not_observe", _GRADE_GATE_SECONDARY_LABELS["grade_not_observe"]
    if not bool(item.get("risk_reward_ready", False)):
        return "rr_not_ready", _GRADE_GATE_SECONDARY_LABELS["rr_not_ready"]

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    if rr < 1.8:
        return "rr_too_low", _GRADE_GATE_SECONDARY_LABELS["rr_too_low"]

    risk_reward_state = _normalize_text(item.get("risk_reward_state", "")).lower()
    if risk_reward_state and risk_reward_state not in {"acceptable", "favorable", "good"}:
        return "risk_reward_state_bad", _GRADE_GATE_SECONDARY_LABELS["risk_reward_state_bad"]

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        return "direction_unclear", _GRADE_GATE_SECONDARY_LABELS["direction_unclear"]

    multi_alignment = _normalize_text(item.get("multi_timeframe_alignment", "")).lower()
    multi_bias = _normalize_text(item.get("multi_timeframe_bias", "")).lower()
    if multi_alignment and multi_alignment not in {"aligned", "partial"}:
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]
    if multi_bias in {"bullish", "long"} and action != "long":
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]
    if multi_bias in {"bearish", "short"} and action != "short":
        return "multi_timeframe_misaligned", _GRADE_GATE_SECONDARY_LABELS["multi_timeframe_misaligned"]

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return "target_incomplete", _GRADE_GATE_SECONDARY_LABELS["target_incomplete"]

    if not _is_price_near_entry_zone(item, action):
        return "entry_zone_miss", _GRADE_GATE_SECONDARY_LABELS["entry_zone_miss"]

    zone_side, _zone_side_text = _resolve_entry_zone_position(item, action)
    if action == "long" and zone_side == "upper":
        return "chasing_upper", _GRADE_GATE_SECONDARY_LABELS["chasing_upper"]
    if action == "short" and zone_side == "lower":
        return "chasing_lower", _GRADE_GATE_SECONDARY_LABELS["chasing_lower"]
    return "unknown", _GRADE_GATE_SECONDARY_LABELS["unknown"]


def _resolve_rr_direction_hint(item: dict) -> str:
    for key in ("signal_side", "risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = _normalize_text(item.get(key, "")).lower()
        if value in {"long", "bullish"}:
            return "bullish"
        if value in {"short", "bearish"}:
            return "bearish"
    return "unknown"


def _diagnose_rr_not_ready_tertiary(item: dict) -> tuple[str, str]:
    current_price = float(item.get("latest_price", 0.0) or 0.0)
    key_high = float(item.get("key_level_high", 0.0) or 0.0)
    key_low = float(item.get("key_level_low", 0.0) or 0.0)
    atr14 = max(float(item.get("atr14", 0.0) or 0.0), 0.0)
    direction = _resolve_rr_direction_hint(item)

    if current_price <= 0:
        return "no_price", _RR_NOT_READY_TERTIARY_LABELS["no_price"]
    if direction not in {"bullish", "bearish"}:
        return "no_direction", _RR_NOT_READY_TERTIARY_LABELS["no_direction"]
    if min(key_high, key_low) <= 0 or key_high <= key_low:
        if atr14 <= 0:
            return "atr_missing_no_key_levels", _RR_NOT_READY_TERTIARY_LABELS["atr_missing_no_key_levels"]
        return "unknown", _RR_NOT_READY_TERTIARY_LABELS["unknown"]
    if (key_high - key_low) <= 0:
        return "key_range_invalid", _RR_NOT_READY_TERTIARY_LABELS["key_range_invalid"]

    stop_price = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    target_price = float(item.get("risk_reward_target_price", 0.0) or 0.0)
    if min(stop_price, target_price) > 0:
        risk = abs(current_price - stop_price)
        reward = abs(target_price - current_price)
        if risk < 1e-5 or reward < 1e-5:
            return "price_span_too_small", _RR_NOT_READY_TERTIARY_LABELS["price_span_too_small"]

    zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if zone_low <= 0 or zone_high <= 0:
        return "entry_zone_missing", _RR_NOT_READY_TERTIARY_LABELS["entry_zone_missing"]
    return "unknown", _RR_NOT_READY_TERTIARY_LABELS["unknown"]


def _diagnose_no_direction_components(item: dict) -> list[tuple[str, str]]:
    components: list[tuple[str, str]] = []
    signal_side = _normalize_text(item.get("signal_side", "")).lower()
    intraday_bias = _normalize_text(item.get("intraday_bias", "")).lower()
    multi_alignment = _normalize_text(item.get("multi_timeframe_alignment", "")).lower()
    multi_bias = _normalize_text(item.get("multi_timeframe_bias", "")).lower()
    breakout_direction = _normalize_text(item.get("breakout_direction", "")).lower()
    breakout_state = _normalize_text(item.get("breakout_state", "")).lower()
    retest_state = _normalize_text(item.get("retest_state", "")).lower()

    if signal_side not in {"long", "short"}:
        components.append(("signal_side_missing", _NO_DIRECTION_COMPONENT_LABELS["signal_side_missing"]))
    if intraday_bias not in {"bullish", "bearish"}:
        components.append(("intraday_sideways", _NO_DIRECTION_COMPONENT_LABELS["intraday_sideways"]))
    if multi_alignment not in {"aligned", "partial"} or multi_bias not in {"bullish", "bearish"}:
        components.append(("multi_not_aligned", _NO_DIRECTION_COMPONENT_LABELS["multi_not_aligned"]))
    if breakout_direction not in {"bullish", "bearish"}:
        components.append(("breakout_direction_neutral", _NO_DIRECTION_COMPONENT_LABELS["breakout_direction_neutral"]))
    if breakout_state in {"", "none", "unknown"}:
        components.append(("breakout_state_none", _NO_DIRECTION_COMPONENT_LABELS["breakout_state_none"]))
    if retest_state in {"", "none", "unknown"}:
        components.append(("retest_state_none", _NO_DIRECTION_COMPONENT_LABELS["retest_state_none"]))
    return components


def _build_snapshot_from_market_row(row) -> dict:
    features = _load_feature_payload(str(_row_value(row, "feature_json", "{}") or "{}"))
    item = {
        **features,
        "symbol": _normalize_text(_row_value(row, "symbol", "")).upper(),
        "latest_price": float(_row_value(row, "latest_price", 0.0) or 0.0),
        "spread_points": float(_row_value(row, "spread_points", 0.0) or 0.0),
        "has_live_quote": bool(_row_value(row, "has_live_quote", 0)),
        "tone": _normalize_text(_row_value(row, "tone", "")),
        "trade_grade": _normalize_text(_row_value(row, "trade_grade", "")),
        "trade_grade_source": _normalize_text(_row_value(row, "trade_grade_source", "")),
        "alert_state_text": _normalize_text(_row_value(row, "alert_state_text", "")),
        "event_risk_mode_text": _normalize_text(_row_value(row, "event_risk_mode_text", "")),
        "event_active_name": _normalize_text(_row_value(row, "event_active_name", "")),
        "event_importance_text": _normalize_text(_row_value(row, "event_importance_text", "")),
        "event_note": _normalize_text(_row_value(row, "event_note", "")),
        "signal_side": _normalize_text(_row_value(row, "signal_side", "")).lower(),
        "regime_tag": _normalize_text(_row_value(row, "regime_tag", "")),
        "regime_text": _normalize_text(_row_value(row, "regime_text", "")),
    }
    return {
        "last_refresh_text": _normalize_text(_row_value(row, "snapshot_time", "")),
        "items": [item],
    }


def _fetch_market_snapshot(conn, audit_row) -> object:
    snapshot_id = int(_row_value(audit_row, "snapshot_id", 0) or 0)
    symbol = _normalize_text(_row_value(audit_row, "symbol", "")).upper()
    snapshot_time = _normalize_text(_row_value(audit_row, "snapshot_time", ""))
    if snapshot_id > 0:
        row = conn.execute(
            """
            SELECT *
            FROM market_snapshots
            WHERE id = ?
            LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()
        if row:
            return row
    if not symbol or not snapshot_time:
        return None
    return conn.execute(
        """
        SELECT *
        FROM market_snapshots
        WHERE snapshot_time = ? AND symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (snapshot_time, symbol),
    ).fetchone()


def replay_exploratory_grade_gate(
    db_path: Path | str | None = None,
    hours: int = 48,
    now: datetime | None = None,
    daily_limit: int = 3,
    cooldown_min: int = 10,
    limit: int = 1000,
) -> dict:
    current = now or datetime.now()
    cutoff = (current - timedelta(hours=max(1, int(hours)))).strftime("%Y-%m-%d %H:%M:%S")
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    released_rows: list[dict] = []
    missing_snapshot_count = 0
    scanned_count = 0
    still_blocked_count = 0
    still_blocked_reason_counts: Counter[str] = Counter()
    still_blocked_reason_key_counts: Counter[str] = Counter()
    still_blocked_reason_label_counts: Counter[str] = Counter()
    grade_gate_secondary_key_counts: Counter[str] = Counter()
    grade_gate_secondary_label_counts: Counter[str] = Counter()
    rr_not_ready_tertiary_key_counts: Counter[str] = Counter()
    rr_not_ready_tertiary_label_counts: Counter[str] = Counter()
    no_direction_component_key_counts: Counter[str] = Counter()
    no_direction_component_label_counts: Counter[str] = Counter()
    no_direction_examples: list[dict] = []
    symbol_counts: Counter[str] = Counter()
    day_counts: Counter[str] = Counter()

    with open_knowledge_connection(target, ensure_schema=True) as conn:
        audit_rows = conn.execute(
            """
            SELECT id, occurred_at, snapshot_time, snapshot_id, symbol, action, reason_text
            FROM execution_audits
            WHERE occurred_at >= ?
              AND reason_key = 'grade_gate'
            ORDER BY occurred_at ASC, id ASC
            LIMIT ?
            """,
            (cutoff, max(1, int(limit))),
        ).fetchall()
        for audit_row in audit_rows:
            scanned_count += 1
            market_row = _fetch_market_snapshot(conn, audit_row)
            if not market_row:
                missing_snapshot_count += 1
                continue
            snapshot = _build_snapshot_from_market_row(market_row)
            signal, reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)
            if not signal or _normalize_text(signal.get("execution_profile", "")).lower() != "exploratory":
                still_blocked_count += 1
                blocked_key = ""
                blocked_label = ""
                blocked_reason = _normalize_block_reason_text(reason)
                item = dict((snapshot.get("items") or [{}])[0] or {})
                audit_payload = audit_rule_sim_signal_decision(snapshot, allow_exploratory=True)
                audit_rows = list(audit_payload.get("rows", []) or [])
                audit_row = next(
                    (
                        row
                        for row in audit_rows
                        if _normalize_text(row.get("symbol", "")).upper()
                        == _normalize_text(_row_value(audit_row, "symbol", "")).upper()
                    ),
                    None,
                )
                if audit_row:
                    blocked_key = _normalize_text(audit_row.get("reason_key", "")).lower()
                    blocked_label = _normalize_text(audit_row.get("reason_label", ""))
                    if blocked_reason == "未返回可解释原因":
                        blocked_reason = _normalize_block_reason_text(audit_row.get("reason", "")) or blocked_reason
                elif blocked_reason == "未返回可解释原因":
                    blocked_summary = list(audit_payload.get("blocked_summary", []) or [])
                    if blocked_summary:
                        blocked_label = _normalize_text(blocked_summary[0].get("reason_label", ""))
                        blocked_reason = blocked_label or blocked_reason
                if blocked_key:
                    still_blocked_reason_key_counts[blocked_key] += 1
                if blocked_label:
                    still_blocked_reason_label_counts[blocked_label] += 1
                if blocked_key == "grade_gate":
                    secondary_key, secondary_label = _diagnose_grade_gate_secondary(item)
                    grade_gate_secondary_key_counts[secondary_key] += 1
                    grade_gate_secondary_label_counts[secondary_label] += 1
                    if secondary_key == "rr_not_ready":
                        tertiary_key, tertiary_label = _diagnose_rr_not_ready_tertiary(item)
                        rr_not_ready_tertiary_key_counts[tertiary_key] += 1
                        rr_not_ready_tertiary_label_counts[tertiary_label] += 1
                        if tertiary_key == "no_direction":
                            for component_key, component_label in _diagnose_no_direction_components(item):
                                no_direction_component_key_counts[component_key] += 1
                                no_direction_component_label_counts[component_label] += 1
                            if len(no_direction_examples) < 12:
                                no_direction_examples.append(
                                    {
                                        "snapshot_time": _normalize_text(_row_value(market_row, "snapshot_time", "")),
                                        "symbol": _normalize_text(_row_value(market_row, "symbol", "")).upper(),
                                        "signal_side": _normalize_text(item.get("signal_side", "")).lower(),
                                        "intraday_bias": _normalize_text(item.get("intraday_bias", "")).lower(),
                                        "multi_timeframe_alignment": _normalize_text(item.get("multi_timeframe_alignment", "")).lower(),
                                        "multi_timeframe_bias": _normalize_text(item.get("multi_timeframe_bias", "")).lower(),
                                        "breakout_direction": _normalize_text(item.get("breakout_direction", "")).lower(),
                                        "breakout_state": _normalize_text(item.get("breakout_state", "")).lower(),
                                        "retest_state": _normalize_text(item.get("retest_state", "")).lower(),
                                    }
                                )
                still_blocked_reason_counts[blocked_reason] += 1
                continue

            symbol = _normalize_text(signal.get("symbol", "")).upper()
            occurred_at = _normalize_text(_row_value(audit_row, "occurred_at", ""))
            day_key = occurred_at[:10] if len(occurred_at) >= 10 else "unknown"
            symbol_counts[symbol] += 1
            day_counts[day_key] += 1
            item = dict((snapshot.get("items") or [{}])[0] or {})
            released_rows.append(
                {
                    "audit_id": int(_row_value(audit_row, "id", 0) or 0),
                    "occurred_at": occurred_at,
                    "snapshot_time": _normalize_text(_row_value(market_row, "snapshot_time", "")),
                    "symbol": symbol,
                    "action": _normalize_text(signal.get("action", "")).lower(),
                    "price": float(signal.get("price", 0.0) or 0.0),
                    "sl": float(signal.get("sl", 0.0) or 0.0),
                    "tp": float(signal.get("tp", 0.0) or 0.0),
                    "risk_reward_ratio": float(item.get("risk_reward_ratio", 0.0) or 0.0),
                    "multi_timeframe_alignment": _normalize_text(item.get("multi_timeframe_alignment", "")),
                    "multi_timeframe_bias": _normalize_text(item.get("multi_timeframe_bias", "")),
                    "execution_profile": "exploratory",
                }
            )

    released_count = len(released_rows)
    release_rate = released_count / scanned_count if scanned_count > 0 else 0.0
    accepted_day_counts: Counter[str] = Counter()
    cooldown_blocked_day_counts: Counter[str] = Counter()
    daily_limit_blocked_day_counts: Counter[str] = Counter()
    last_accepted_by_key: dict[tuple[str, str], datetime] = {}
    policy_accepted_count = 0
    cooldown_blocked_count = 0
    daily_limit_blocked_count = 0
    clean_daily_limit = max(0, int(daily_limit or 0))
    clean_cooldown_min = max(0, int(cooldown_min or 0))
    cooldown_delta = timedelta(minutes=clean_cooldown_min)
    for row in released_rows:
        day_key = str(row.get("occurred_at", "") or "")[:10] or "unknown"
        symbol = _normalize_text(row.get("symbol", "")).upper()
        action = _normalize_text(row.get("action", "")).lower()
        occurred_dt = _parse_datetime_text(row.get("occurred_at", ""))
        policy_reason = ""
        key = (symbol, action)
        last_dt = last_accepted_by_key.get(key)
        if (
            clean_cooldown_min > 0
            and occurred_dt is not None
            and last_dt is not None
            and occurred_dt - last_dt < cooldown_delta
        ):
            policy_reason = "cooldown"
            cooldown_blocked_count += 1
            cooldown_blocked_day_counts[day_key] += 1
        elif clean_daily_limit > 0 and accepted_day_counts[day_key] >= clean_daily_limit:
            policy_reason = "daily_limit"
            daily_limit_blocked_count += 1
            daily_limit_blocked_day_counts[day_key] += 1
        else:
            policy_accepted_count += 1
            accepted_day_counts[day_key] += 1
            if occurred_dt is not None and symbol and action:
                last_accepted_by_key[key] = occurred_dt
        row["policy_status"] = "blocked" if policy_reason else "accepted"
        row["policy_block_reason"] = policy_reason

    policy_blocked_count = cooldown_blocked_count + daily_limit_blocked_count
    policy_accept_rate = policy_accepted_count / released_count if released_count > 0 else 0.0
    by_symbol = [
        {"symbol": symbol, "count": count}
        for symbol, count in sorted(symbol_counts.items(), key=lambda item: (-item[1], item[0]))
    ]
    by_day = [
        {
            "day": day,
            "count": count,
            "accepted_count": int(accepted_day_counts.get(day, 0)),
            "cooldown_blocked_count": int(cooldown_blocked_day_counts.get(day, 0)),
            "daily_limit_blocked_count": int(daily_limit_blocked_day_counts.get(day, 0)),
            "over_limit": bool(clean_daily_limit > 0 and count > clean_daily_limit),
        }
        for day, count in sorted(day_counts.items())
    ]
    over_limit_days = [row for row in by_day if bool(row.get("over_limit", False))]
    top_still_blocked_labels = [
        {"reason_label": label, "count": count}
        for label, count in still_blocked_reason_label_counts.most_common(5)
    ]
    top_grade_gate_secondary_labels = [
        {"reason_label": label, "count": count}
        for label, count in grade_gate_secondary_label_counts.most_common(5)
    ]
    top_rr_not_ready_tertiary_labels = [
        {"reason_label": label, "count": count}
        for label, count in rr_not_ready_tertiary_label_counts.most_common(5)
    ]
    top_no_direction_components = [
        {"reason_label": label, "count": count}
        for label, count in no_direction_component_label_counts.most_common(6)
    ]
    top_still_blocked_reasons = [
        {"reason": reason, "count": count}
        for reason, count in still_blocked_reason_counts.most_common(5)
    ]
    if scanned_count <= 0:
        summary_text = f"探索回放：最近 {max(1, int(hours))} 小时没有 grade_gate 样本。"
    else:
        density_text = "未超过每日探索上限"
        if over_limit_days:
            density_text = f"{len(over_limit_days)} 天超过每日探索上限 {clean_daily_limit} 次"
        blocked_reason_text = ""
        if top_still_blocked_labels:
            top_reason = dict(top_still_blocked_labels[0] or {})
            blocked_reason_text = f"；仍阻塞主因：{top_reason.get('reason_label', '--')} {int(top_reason.get('count', 0) or 0)} 条"
        elif top_still_blocked_reasons:
            top_reason = dict(top_still_blocked_reasons[0] or {})
            blocked_reason_text = f"；仍阻塞主因：{top_reason.get('reason', '--')} {int(top_reason.get('count', 0) or 0)} 条"
        grade_gate_secondary_text = ""
        if top_still_blocked_labels and str(top_still_blocked_labels[0].get("reason_label", "") or "").strip() == "未到试仓级别":
            top_secondary = dict(top_grade_gate_secondary_labels[0] or {}) if top_grade_gate_secondary_labels else {}
            if top_secondary:
                grade_gate_secondary_text = (
                    f"；其中次阻因：{top_secondary.get('reason_label', '--')} "
                    f"{int(top_secondary.get('count', 0) or 0)} 条"
                )
                if str(top_secondary.get("reason_label", "") or "").strip() == "盈亏比未准备好":
                    top_tertiary = dict(top_rr_not_ready_tertiary_labels[0] or {}) if top_rr_not_ready_tertiary_labels else {}
                    if top_tertiary:
                        grade_gate_secondary_text += (
                            f"；RR细分：{top_tertiary.get('reason_label', '--')} "
                            f"{int(top_tertiary.get('count', 0) or 0)} 条"
                        )
                        if str(top_tertiary.get("reason_label", "") or "").strip() == "方向基础不足":
                            component_parts = [
                                f"{str(row.get('reason_label', '') or '').strip()} {int(row.get('count', 0) or 0)}"
                                for row in top_no_direction_components[:2]
                                if str(row.get("reason_label", "") or "").strip() and int(row.get("count", 0) or 0) > 0
                            ]
                            if component_parts:
                                grade_gate_secondary_text += f"；方向构成：{' / '.join(component_parts)}"
        summary_text = (
            f"探索回放：最近 {max(1, int(hours))} 小时扫描 grade_gate {scanned_count} 条，"
            f"按当前探索规则可释放 {released_count} 条（{release_rate * 100:.0f}%），"
            f"{density_text}；按冷却/上限后预计执行 {policy_accepted_count} 条，"
            f"冷却拦截 {cooldown_blocked_count} 条，日上限拦截 {daily_limit_blocked_count} 条"
            f"{blocked_reason_text}{grade_gate_secondary_text}。"
        )
    return {
        "scanned_count": scanned_count,
        "released_count": released_count,
        "release_rate": release_rate,
        "policy_accepted_count": policy_accepted_count,
        "policy_blocked_count": policy_blocked_count,
        "policy_accept_rate": policy_accept_rate,
        "cooldown_blocked_count": cooldown_blocked_count,
        "daily_limit_blocked_count": daily_limit_blocked_count,
        "daily_limit": clean_daily_limit,
        "cooldown_min": clean_cooldown_min,
        "still_blocked_count": still_blocked_count,
        "still_blocked_reason_counts": dict(still_blocked_reason_counts),
        "still_blocked_reason_key_counts": dict(still_blocked_reason_key_counts),
        "still_blocked_reason_label_counts": dict(still_blocked_reason_label_counts),
        "grade_gate_secondary_key_counts": dict(grade_gate_secondary_key_counts),
        "grade_gate_secondary_label_counts": dict(grade_gate_secondary_label_counts),
        "rr_not_ready_tertiary_key_counts": dict(rr_not_ready_tertiary_key_counts),
        "rr_not_ready_tertiary_label_counts": dict(rr_not_ready_tertiary_label_counts),
        "no_direction_component_key_counts": dict(no_direction_component_key_counts),
        "no_direction_component_label_counts": dict(no_direction_component_label_counts),
        "top_still_blocked_labels": top_still_blocked_labels,
        "top_grade_gate_secondary_labels": top_grade_gate_secondary_labels,
        "top_rr_not_ready_tertiary_labels": top_rr_not_ready_tertiary_labels,
        "top_no_direction_components": top_no_direction_components,
        "no_direction_examples": no_direction_examples,
        "top_still_blocked_reasons": top_still_blocked_reasons,
        "missing_snapshot_count": missing_snapshot_count,
        "by_symbol": by_symbol,
        "by_day": by_day,
        "over_limit_days": over_limit_days,
        "top_released": released_rows[:20],
        "summary_text": summary_text,
    }


def write_exploratory_replay_report(report: dict, output_path: Path | str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="回放 grade_gate 样本，估算探索试仓释放量")
    parser.add_argument("--db", default=str(KNOWLEDGE_DB_FILE))
    parser.add_argument("--hours", type=int, default=48)
    parser.add_argument("--daily-limit", type=int, default=3)
    parser.add_argument("--cooldown-min", type=int, default=10)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = replay_exploratory_grade_gate(
        db_path=args.db,
        hours=args.hours,
        daily_limit=args.daily_limit,
        cooldown_min=args.cooldown_min,
        limit=args.limit,
    )
    print(report["summary_text"])
    if args.output:
        path = write_exploratory_replay_report(report, args.output)
        print(f"报告已写入：{path}")


if __name__ == "__main__":
    main()
