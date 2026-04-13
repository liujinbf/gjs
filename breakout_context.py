from __future__ import annotations


def _bar_value(bar, key: str) -> float:
    if isinstance(bar, dict):
        return float(bar.get(key, 0.0) or 0.0)
    try:
        return float(bar[key] or 0.0)
    except Exception:  # noqa: BLE001
        return float(getattr(bar, key, 0.0) or 0.0)


def build_empty_breakout_context() -> dict:
    return {
        "breakout_ready": False,
        "breakout_state": "unknown",
        "breakout_state_text": "突破未知",
        "breakout_context_text": "",
        "breakout_direction": "unknown",
        "retest_ready": False,
        "retest_state": "unknown",
        "retest_state_text": "回踩未知",
        "retest_context_text": "",
    }


def _build_retest_payload(state: str, state_text: str, context_text: str) -> dict:
    return {
        "retest_ready": True,
        "retest_state": state,
        "retest_state_text": state_text,
        "retest_context_text": context_text,
    }


def _analyze_retest_payload(breakout_state: str, key_high: float, key_low: float, recent_bars: list[dict], close_buffer: float, wick_buffer: float) -> dict:
    last_bar = recent_bars[-1]
    lookback_bars = recent_bars[-3:]

    if breakout_state == "confirmed_above" and key_high > 0:
        touched_support = any(float(bar["low"] or 0.0) <= key_high + wick_buffer for bar in lookback_bars)
        if not touched_support:
            return _build_retest_payload("waiting_support", "回踩待确认", "上破后还没出现有效回踩，先别急着在延伸段追多")
        if float(last_bar["close"] or 0.0) >= key_high + close_buffer * 0.25:
            return _build_retest_payload("confirmed_support", "回踩已确认", "上破后的回踩已经守住突破位，可以继续观察是否走二次上攻")
        if float(last_bar["close"] or 0.0) < key_high - close_buffer * 0.35:
            return _build_retest_payload("failed_support", "回踩失守", "上破后回踩已经跌回突破位下方，强度不足，疑似假动作")
        return _build_retest_payload("waiting_support", "回踩待确认", "上破后已经回踩到突破位附近，但还需要再看收线是否守住")

    if breakout_state == "confirmed_below" and key_low > 0:
        touched_resistance = any(float(bar["high"] or 0.0) >= key_low - wick_buffer for bar in lookback_bars)
        if not touched_resistance:
            return _build_retest_payload("waiting_resistance", "反抽待确认", "下破后还没出现有效反抽，先别急着在延伸段追空")
        if float(last_bar["close"] or 0.0) <= key_low - close_buffer * 0.25:
            return _build_retest_payload("confirmed_resistance", "反抽已确认", "下破后的反抽没有站回突破位，可以继续观察是否走二次下压")
        if float(last_bar["close"] or 0.0) > key_low + close_buffer * 0.35:
            return _build_retest_payload("failed_resistance", "反抽失守", "下破后价格重新站回突破位上方，强度不足，疑似假跌破")
        return _build_retest_payload("waiting_resistance", "反抽待确认", "下破后已经反抽到突破位附近，但还需要再看收线是否重新失守")

    return {
        "retest_ready": False,
        "retest_state": "none",
        "retest_state_text": "暂无回踩",
        "retest_context_text": "",
    }


def analyze_breakout_signal(key_level_context: dict | None, bars) -> dict:
    key_context = dict(key_level_context or {})
    if not bool(key_context.get("key_level_ready", False)):
        return build_empty_breakout_context()

    normalized = []
    for bar in (list(bars) if bars is not None and hasattr(bars, '__len__') and len(bars) > 0 else []):
        high_price = _bar_value(bar, "high")
        low_price = _bar_value(bar, "low")
        close_price = _bar_value(bar, "close")
        if min(high_price, low_price, close_price) <= 0 or high_price < low_price:
            continue
        normalized.append({"high": high_price, "low": low_price, "close": close_price})

    if len(normalized) < 4:
        return build_empty_breakout_context()

    last_bar = normalized[-1]
    prev_bar = normalized[-2]
    recent_bars = normalized[-3:]
    avg_bar_range = sum(max(item["high"] - item["low"], 0.0) for item in recent_bars) / max(1, len(recent_bars))
    last_close = float(last_bar["close"] or 0.0)
    key_high = float(key_context.get("key_level_high", 0.0) or 0.0)
    key_low = float(key_context.get("key_level_low", 0.0) or 0.0)
    close_buffer = max(avg_bar_range * 0.18, last_close * 0.00012)
    wick_buffer = max(avg_bar_range * 0.10, last_close * 0.00006)

    recent_high = max(item["high"] for item in recent_bars)
    recent_low = min(item["low"] for item in recent_bars)

    if key_high > 0:
        if last_close > key_high + close_buffer and float(prev_bar["close"] or 0.0) > key_high:
            breakout_payload = {
                "breakout_ready": True,
                "breakout_state": "confirmed_above",
                "breakout_state_text": "上破已确认",
                "breakout_context_text": "M5 连续收在关键位上方，属于已确认上破",
                "breakout_direction": "bullish",
            }
            breakout_payload.update(_analyze_retest_payload("confirmed_above", key_high, key_low, normalized, close_buffer, wick_buffer))
            return breakout_payload
        if recent_high > key_high + wick_buffer and last_close < key_high - close_buffer * 0.5:
            return {
                "breakout_ready": True,
                "breakout_state": "failed_above",
                "breakout_state_text": "上破失败",
                "breakout_context_text": "价格刚刺破高位又收回关键位下方，疑似假上破",
                "breakout_direction": "bullish",
                "retest_ready": False,
                "retest_state": "none",
                "retest_state_text": "暂无回踩",
                "retest_context_text": "",
            }
        if last_bar["high"] > key_high + wick_buffer and last_close >= key_high - close_buffer * 0.3:
            return {
                "breakout_ready": True,
                "breakout_state": "pending_above",
                "breakout_state_text": "上破待确认",
                "breakout_context_text": "价格正在尝试上破高位，但还需要再看一到两根 M5 收线确认",
                "breakout_direction": "bullish",
                "retest_ready": False,
                "retest_state": "none",
                "retest_state_text": "暂无回踩",
                "retest_context_text": "",
            }

    if key_low > 0:
        if last_close < key_low - close_buffer and float(prev_bar["close"] or 0.0) < key_low:
            breakout_payload = {
                "breakout_ready": True,
                "breakout_state": "confirmed_below",
                "breakout_state_text": "下破已确认",
                "breakout_context_text": "M5 连续收在关键位下方，属于已确认下破",
                "breakout_direction": "bearish",
            }
            breakout_payload.update(_analyze_retest_payload("confirmed_below", key_high, key_low, normalized, close_buffer, wick_buffer))
            return breakout_payload
        if recent_low < key_low - wick_buffer and last_close > key_low + close_buffer * 0.5:
            return {
                "breakout_ready": True,
                "breakout_state": "failed_below",
                "breakout_state_text": "下破失败",
                "breakout_context_text": "价格刚刺破低位又收回关键位上方，疑似假下破",
                "breakout_direction": "bearish",
                "retest_ready": False,
                "retest_state": "none",
                "retest_state_text": "暂无回踩",
                "retest_context_text": "",
            }
        if last_bar["low"] < key_low - wick_buffer and last_close <= key_low + close_buffer * 0.3:
            return {
                "breakout_ready": True,
                "breakout_state": "pending_below",
                "breakout_state_text": "下破待确认",
                "breakout_context_text": "价格正在尝试下破低位，但还需要再看一到两根 M5 收线确认",
                "breakout_direction": "bearish",
                "retest_ready": False,
                "retest_state": "none",
                "retest_state_text": "暂无回踩",
                "retest_context_text": "",
            }

    return {
        "breakout_ready": True,
        "breakout_state": "none",
        "breakout_state_text": "暂无突破",
        "breakout_context_text": "关键位附近暂时没有形成可确认的突破形态",
        "breakout_direction": "neutral",
        "retest_ready": False,
        "retest_state": "none",
        "retest_state_text": "暂无回踩",
        "retest_context_text": "",
    }
