from __future__ import annotations

from typing import Any

from signal_enums import AlertTone, SignalSide, TradeGrade


_BULLISH_VALUES = {"bullish", "long", "buy"}
_BEARISH_VALUES = {"bearish", "short", "sell"}
_SUPPORTIVE_BREAKOUT = {
    "confirmed_above": SignalSide.LONG.value,
    "confirmed_below": SignalSide.SHORT.value,
}
_SUPPORTIVE_RETEST = {
    "confirmed_support": SignalSide.LONG.value,
    "confirmed_resistance": SignalSide.SHORT.value,
}


def _safe_text(value: Any) -> str:
    try:
        if value is None:
            return ""
        return str(value).strip()
    except Exception:  # noqa: BLE001
        return ""


def _safe_float(value: Any) -> float:
    try:
        if value in ("", None):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _direction_from_value(value: Any) -> str:
    text = _safe_text(value).lower()
    if text in _BULLISH_VALUES:
        return SignalSide.LONG.value
    if text in _BEARISH_VALUES:
        return SignalSide.SHORT.value
    return SignalSide.NEUTRAL.value


def _resolve_direction(item: dict[str, Any]) -> str:
    for key in ("signal_side", "risk_reward_direction", "multi_timeframe_bias", "intraday_bias"):
        side = _direction_from_value(item.get(key))
        if side in {SignalSide.LONG.value, SignalSide.SHORT.value}:
            return side
    return SignalSide.NEUTRAL.value


def _bias_matches(value: Any, direction: str) -> bool:
    return _direction_from_value(value) == direction


def _append_reason(reasons: list[str], text: str, limit: int = 3) -> None:
    payload = _safe_text(text)
    if payload and payload not in reasons and len(reasons) < limit:
        reasons.append(payload)


def _prioritize_reason(reasons: list[str], text: str, limit: int = 3) -> None:
    payload = _safe_text(text)
    if not payload:
        return
    if payload in reasons:
        reasons.remove(payload)
    reasons.insert(0, payload)
    del reasons[limit:]


def _has_high_event_risk(item: dict[str, Any]) -> bool:
    importance = _safe_text(item.get("event_importance_text"))
    mode_text = _safe_text(item.get("event_mode_text"))
    trade_source = _safe_text(item.get("trade_grade_source")).lower()
    return bool(item.get("event_applies", False)) and (
        "高影响" in importance or "事件前" in mode_text or trade_source == "event"
    )


def _score_risk_reward(item: dict[str, Any], reasons: list[str]) -> int:
    if not bool(item.get("risk_reward_ready", False)):
        _append_reason(reasons, "缺少完整进场、止损和目标，先只观察。")
        return 0

    rr = _safe_float(item.get("risk_reward_ratio"))
    if rr >= 2.0:
        _append_reason(reasons, f"盈亏比约 1:{rr:.2f}，具备更好的容错。")
        return 25
    if rr >= 1.5:
        _append_reason(reasons, f"盈亏比约 1:{rr:.2f}，达到轻仓观察线。")
        return 20
    if rr >= 1.2:
        _append_reason(reasons, f"盈亏比约 1:{rr:.2f}，只能按低置信候选处理。")
        return 10
    _append_reason(reasons, "盈亏比不足，当前位置不适合主动追。")
    return -10


def _score_short_term(item: dict[str, Any], direction: str, reasons: list[str]) -> int:
    if direction not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        _append_reason(reasons, "方向还没有形成一致结论。")
        return 0

    score = 25
    _append_reason(reasons, "方向已形成偏多参考。" if direction == SignalSide.LONG.value else "方向已形成偏空参考。")
    score += _score_risk_reward(item, reasons)

    if _bias_matches(item.get("intraday_bias"), direction):
        score += 15
        _append_reason(reasons, "近 1 小时方向与交易方向一致。")

    alignment = _safe_text(item.get("multi_timeframe_alignment")).lower()
    if alignment == "aligned" and _bias_matches(item.get("multi_timeframe_bias"), direction):
        score += 15
        _append_reason(reasons, "多周期同向，短线信号更干净。")
    elif alignment == "partial" and _bias_matches(item.get("multi_timeframe_bias"), direction):
        score += 8

    breakout_state = _safe_text(item.get("breakout_state")).lower()
    retest_state = _safe_text(item.get("retest_state")).lower()
    if _SUPPORTIVE_BREAKOUT.get(breakout_state) == direction:
        score += 8
        _append_reason(reasons, "突破已经得到确认。")
    if _SUPPORTIVE_RETEST.get(retest_state) == direction:
        score += 10
        _append_reason(reasons, "回踩确认后再延续，入场质量更高。")

    return score


def _score_long_term(item: dict[str, Any], direction: str, reasons: list[str]) -> int:
    if direction not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return 0

    score = 20
    alignment = _safe_text(item.get("multi_timeframe_alignment")).lower()
    if alignment == "aligned" and _bias_matches(item.get("multi_timeframe_bias"), direction):
        score += 25
        _append_reason(reasons, "多周期方向一致，适合纳入长线观察。")
    elif alignment == "partial" and _bias_matches(item.get("multi_timeframe_bias"), direction):
        score += 12
        _append_reason(reasons, "多周期部分同向，长线仍需继续确认。")
    else:
        score -= 10

    score += min(18, max(0, _score_risk_reward(item, reasons) - 5))

    h4_text = f"{_safe_text(item.get('h4_context_text'))} {_safe_text(item.get('tech_summary_h4'))}".lower()
    if h4_text:
        if direction == SignalSide.LONG.value and any(word in h4_text for word in ("偏多", "上行", "支撑", "bull")):
            score += 12
            _append_reason(reasons, "H4 背景与多头方向不冲突。")
        elif direction == SignalSide.SHORT.value and any(word in h4_text for word in ("偏空", "下行", "压力", "bear")):
            score += 12
            _append_reason(reasons, "H4 背景与空头方向不冲突。")

    regime_tag = _safe_text(item.get("regime_tag")).lower()
    regime_text = _safe_text(item.get("regime_text"))
    if "trend" in regime_tag or "趋势" in regime_text:
        score += 8

    return score


def _cap_for_risk_state(item: dict[str, Any], score: int, reasons: list[str]) -> int:
    capped = int(max(0, min(100, score)))
    trade_grade = _safe_text(item.get("trade_grade"))
    tone = _safe_text(item.get("tone")).lower()

    if not bool(item.get("has_live_quote", False)):
        _prioritize_reason(reasons, "暂无实时报价，只记录不提醒。")
        return min(capped, 20)
    high_event_risk = _has_high_event_risk(item)
    if high_event_risk:
        _prioritize_reason(reasons, "高影响事件窗口内，不推主动买卖提醒。")
        capped = min(capped, 55)
    if trade_grade == TradeGrade.NO_TRADE.value:
        if high_event_risk:
            _append_reason(reasons, "当前出手分级为不宜出手。")
        else:
            _prioritize_reason(reasons, "当前出手分级为不宜出手。")
        capped = min(capped, 45)
    elif trade_grade == TradeGrade.WAIT_EVENT.value:
        _prioritize_reason(reasons, "事件或流动性约束仍在，等待确认。")
        capped = min(capped, 55)
    elif trade_grade == TradeGrade.OBSERVE_ONLY.value:
        capped = min(capped, 70)
    if tone in {AlertTone.WARNING.value, AlertTone.NEGATIVE.value}:
        _append_reason(reasons, "报价或点差状态不够理想。")
        capped = min(capped, 60)
    return capped


def _resolve_push_level(score: int, item: dict[str, Any], direction: str) -> str:
    if direction not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return "record"
    if score >= 80 and bool(item.get("risk_reward_ready", False)) and not _has_high_event_risk(item):
        return "push"
    if score >= 65:
        return "display"
    return "record"


def _action_text(direction: str, push_level: str, timeframe: str) -> str:
    if direction == SignalSide.LONG.value:
        base = "做多"
    elif direction == SignalSide.SHORT.value:
        base = "做空"
    else:
        return "继续观察"

    prefix = "可提醒" if push_level == "push" else "重点观察" if push_level == "display" else "记录观察"
    suffix = "短线" if timeframe == "short_term" else "长线" if timeframe == "long_term" else "短长线"
    return f"{prefix}{suffix}{base}"


def score_trade_opportunity(item: dict[str, Any] | None) -> dict[str, Any]:
    """给单个快照项生成个人交易助手使用的轻量机会评分。"""
    payload = dict(item or {})
    direction = _resolve_direction(payload)
    short_reasons: list[str] = []
    long_reasons: list[str] = []

    short_score = _score_short_term(payload, direction, short_reasons)
    long_score = _score_long_term(payload, direction, long_reasons)
    short_score = _cap_for_risk_state(payload, short_score, short_reasons)
    long_score = _cap_for_risk_state(payload, long_score, long_reasons)

    if short_score >= long_score:
        score = short_score
        timeframe = "short_term"
        reasons = short_reasons
    else:
        score = long_score
        timeframe = "long_term"
        reasons = long_reasons

    if abs(short_score - long_score) <= 5 and score >= 65:
        timeframe = "mixed"

    push_level = _resolve_push_level(score, payload, direction)
    action = direction if score >= 50 and direction in {SignalSide.LONG.value, SignalSide.SHORT.value} else "watch"
    action_text = _action_text(action, push_level, timeframe)
    reasons = reasons[:3]
    summary = "；".join(reasons) if reasons else "当前没有足够明确的交易机会，继续观察。"

    return {
        "opportunity_score": score,
        "opportunity_short_term_score": short_score,
        "opportunity_long_term_score": long_score,
        "opportunity_timeframe": timeframe if action != "watch" else "watch",
        "opportunity_action": action,
        "opportunity_action_text": action_text,
        "opportunity_push_level": push_level,
        "opportunity_is_actionable": push_level == "push",
        "opportunity_reasons": reasons,
        "opportunity_summary": summary,
        "opportunity_entry_zone_text": _safe_text(payload.get("risk_reward_entry_zone_text")),
        "opportunity_stop_price": _safe_float(payload.get("risk_reward_stop_price")),
        "opportunity_target_price": _safe_float(payload.get("risk_reward_target_price")),
        "opportunity_target_price_2": _safe_float(payload.get("risk_reward_target_price_2")),
        "opportunity_risk_reward_ratio": _safe_float(payload.get("risk_reward_ratio")),
    }


def apply_trade_opportunity_scores(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored_items: list[dict[str, Any]] = []
    for item in items or []:
        payload = dict(item or {})
        payload.update(score_trade_opportunity(payload))
        scored_items.append(payload)
    return scored_items
