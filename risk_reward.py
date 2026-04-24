from __future__ import annotations

RISK_REWARD_EPSILON = 1e-5


def _format_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):.{decimals}f}"


def build_empty_risk_reward_context() -> dict:
    return {
        "risk_reward_ready": False,
        "risk_reward_state": "unknown",
        "risk_reward_state_text": "盈亏比未知",
        "risk_reward_context_text": "",
        "risk_reward_ratio": 0.0,
        "risk_reward_direction": "unknown",
        "risk_reward_basis": "unknown",
        "risk_reward_atr": 0.0,
        "risk_reward_stop_price": 0.0,
        "risk_reward_target_price": 0.0,
        "risk_reward_target_price_2": 0.0,
        "risk_reward_position_text": "",
        "risk_reward_invalidation_text": "",
        "risk_reward_entry_zone_low": 0.0,
        "risk_reward_entry_zone_high": 0.0,
        "risk_reward_entry_zone_text": "",
    }


def _build_atr_fallback_context(current_price: float, point: float, atr14: float, direction: str) -> dict:
    if current_price <= 0 or atr14 <= 0 or direction not in {"bullish", "bearish"}:
        return build_empty_risk_reward_context()

    stop_distance = atr14 * 1.2
    target_distance = atr14 * 2.4
    target_distance_2 = atr14 * 3.6
    entry_band = atr14 * 0.45
    if min(stop_distance, target_distance, entry_band) <= RISK_REWARD_EPSILON:
        return build_empty_risk_reward_context()

    if direction == "bullish":
        stop_price = current_price - stop_distance
        target_price = current_price + target_distance
        target_price_2 = current_price + target_distance_2
        entry_zone_low = current_price - entry_band
        entry_zone_high = current_price + atr14 * 0.15
        direction_text = "多头"
        invalidation_text = f"若价格重新跌回 {_format_price(stop_price, point)} 下方，当前{direction_text}临时结构视为失效。"
    else:
        stop_price = current_price + stop_distance
        target_price = current_price - target_distance
        target_price_2 = current_price - target_distance_2
        entry_zone_low = current_price - atr14 * 0.15
        entry_zone_high = current_price + entry_band
        direction_text = "空头"
        invalidation_text = f"若价格重新站回 {_format_price(stop_price, point)} 上方，当前{direction_text}临时结构视为失效。"

    risk = abs(current_price - stop_price)
    reward = abs(target_price - current_price)
    if risk < RISK_REWARD_EPSILON or reward < RISK_REWARD_EPSILON:
        return build_empty_risk_reward_context()

    ratio = reward / risk
    state = "acceptable" if ratio >= 1.3 else "poor"
    state_text = "盈亏比可接受" if ratio >= 1.3 else "盈亏比偏差"
    position_text = "关键位还没完全补齐，只能把它当成低置信轻仓候选，不能按完整结构等价看待。"
    context_text = (
        f"{direction_text}在关键位不足时按 ATR(14)≈{_format_price(atr14, point)} 临时估算："
        f"止损 {_format_price(stop_price, point)}，目标1 {_format_price(target_price, point)}"
        f" / 目标2 {_format_price(target_price_2, point)}，当前盈亏比约 {ratio:.2f}:1。"
    )
    entry_zone_text = (
        f"临时观察区间 {_format_price(min(entry_zone_low, entry_zone_high), point)} - "
        f"{_format_price(max(entry_zone_low, entry_zone_high), point)}，更适合等回踩或下一根确认。"
    )
    return {
        "risk_reward_ready": True,
        "risk_reward_state": state,
        "risk_reward_state_text": state_text,
        "risk_reward_context_text": context_text,
        "risk_reward_ratio": ratio,
        "risk_reward_direction": direction,
        "risk_reward_basis": "atr_fallback",
        "risk_reward_atr": atr14,
        "risk_reward_stop_price": stop_price,
        "risk_reward_target_price": target_price,
        "risk_reward_target_price_2": target_price_2,
        "risk_reward_position_text": position_text,
        "risk_reward_invalidation_text": invalidation_text,
        "risk_reward_entry_zone_low": min(entry_zone_low, entry_zone_high),
        "risk_reward_entry_zone_high": max(entry_zone_low, entry_zone_high),
        "risk_reward_entry_zone_text": entry_zone_text,
    }


def _resolve_direction(row: dict) -> str:
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    signal_side = str(row.get("signal_side", "neutral") or "neutral").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()

    if retest_state == "confirmed_support":
        return "bullish"
    if retest_state == "confirmed_resistance":
        return "bearish"
    if breakout_state == "confirmed_above":
        return "bullish"
    if breakout_state == "confirmed_below":
        return "bearish"
    if breakout_direction in {"bullish", "bearish"}:
        return breakout_direction
    if signal_side == "long":
        return "bullish"
    if signal_side == "short":
        return "bearish"
    if multi_alignment in {"aligned", "partial"} and multi_bias in {"bullish", "bearish"}:
        return multi_bias
    if intraday_bias in {"bullish", "bearish"}:
        return intraday_bias
    return "unknown"


def analyze_risk_reward(row: dict) -> dict:
    current_price = float(row.get("latest_price", 0.0) or 0.0)
    key_high = float(row.get("key_level_high", 0.0) or 0.0)
    key_low = float(row.get("key_level_low", 0.0) or 0.0)
    key_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    point = float(row.get("point", 0.0) or 0.0)
    atr14 = max(float(row.get("atr14", 0.0) or 0.0), 0.0)

    direction = _resolve_direction(row)
    if direction not in {"bullish", "bearish"}:
        return build_empty_risk_reward_context()

    if min(current_price, key_high, key_low) <= 0 or key_high <= key_low:
        return _build_atr_fallback_context(current_price, point, atr14, direction)

    range_price = max(key_high - key_low, 0.0)
    if range_price <= 0:
        return build_empty_risk_reward_context()

    use_atr = atr14 > 0
    stop_distance = atr14 * 1.5 if use_atr else 0.0
    target_distance = atr14 * 3.0 if use_atr else 0.0
    target_distance_2 = atr14 * 4.5 if use_atr else 0.0
    entry_band = atr14 * 0.6 if use_atr else 0.0
    basis = "atr" if use_atr else "range"

    stop_price = 0.0
    target_price = 0.0
    target_price_2 = 0.0
    entry_zone_low = 0.0
    entry_zone_high = 0.0
    if direction == "bullish":
        if retest_state == "confirmed_support":
            if use_atr:
                stop_price = key_high - stop_distance
                target_price = current_price + target_distance
                target_price_2 = current_price + target_distance_2
                entry_zone_low = key_high - entry_band
                entry_zone_high = key_high + entry_band
            else:
                stop_price = key_high - range_price * 0.05
                target_price = current_price + range_price * 0.60
                target_price_2 = current_price + range_price * 1.00
                entry_zone_low = key_high - range_price * 0.02
                entry_zone_high = key_high + range_price * 0.08
        elif breakout_state == "confirmed_above":
            if use_atr:
                stop_price = key_high - stop_distance
                target_price = current_price + target_distance
                target_price_2 = current_price + target_distance_2
                entry_zone_low = key_high
                entry_zone_high = key_high + max(entry_band, atr14 * 0.8)
            else:
                stop_price = key_high - range_price * 0.08
                target_price = current_price + range_price * 0.55
                target_price_2 = current_price + range_price * 0.90
                entry_zone_low = key_high
                entry_zone_high = key_high + range_price * 0.10
        else:
            if use_atr:
                stop_price = current_price - stop_distance
                target_price = min(key_high, current_price + target_distance)
                target_price_2 = current_price + target_distance_2
                entry_zone_low = max(key_low, current_price - max(entry_band, atr14 * 0.8))
                entry_zone_high = min(current_price, entry_zone_low + max(entry_band, atr14 * 0.5))
                if key_state == "near_high":
                    target_price = min(key_high, current_price + atr14 * 0.8)
                    target_price_2 = current_price + target_distance
                    entry_zone_low = current_price - entry_band
                    entry_zone_high = current_price + atr14 * 0.15
            else:
                stop_price = key_low - range_price * 0.05
                target_price = key_high
                target_price_2 = current_price + range_price * 0.45
                entry_zone_low = max(key_low + range_price * 0.10, current_price - range_price * 0.08)
                entry_zone_high = min(key_low + range_price * 0.25, current_price)
                if key_state == "near_high":
                    target_price = current_price + range_price * 0.10
                    target_price_2 = current_price + range_price * 0.30
                    entry_zone_low = current_price - range_price * 0.04
                    entry_zone_high = current_price + range_price * 0.01
    else:
        if retest_state == "confirmed_resistance":
            if use_atr:
                stop_price = key_low + stop_distance
                target_price = current_price - target_distance
                target_price_2 = current_price - target_distance_2
                entry_zone_low = key_low - entry_band
                entry_zone_high = key_low + entry_band
            else:
                stop_price = key_low + range_price * 0.05
                target_price = current_price - range_price * 0.60
                target_price_2 = current_price - range_price * 1.00
                entry_zone_low = key_low - range_price * 0.08
                entry_zone_high = key_low + range_price * 0.02
        elif breakout_state == "confirmed_below":
            if use_atr:
                stop_price = key_low + stop_distance
                target_price = current_price - target_distance
                target_price_2 = current_price - target_distance_2
                entry_zone_low = key_low - max(entry_band, atr14 * 0.8)
                entry_zone_high = key_low
            else:
                stop_price = key_low + range_price * 0.08
                target_price = current_price - range_price * 0.55
                target_price_2 = current_price - range_price * 0.90
                entry_zone_low = key_low - range_price * 0.10
                entry_zone_high = key_low
        else:
            if use_atr:
                stop_price = current_price + stop_distance
                target_price = max(key_low, current_price - target_distance)
                target_price_2 = current_price - target_distance_2
                entry_zone_high = min(key_high, current_price + max(entry_band, atr14 * 0.8))
                entry_zone_low = max(current_price, entry_zone_high - max(entry_band, atr14 * 0.5))
                if key_state == "near_low":
                    target_price = max(key_low, current_price - atr14 * 0.8)
                    target_price_2 = current_price - target_distance
                    entry_zone_low = current_price - atr14 * 0.15
                    entry_zone_high = current_price + entry_band
            else:
                stop_price = key_high + range_price * 0.05
                target_price = key_low
                target_price_2 = current_price - range_price * 0.45
                entry_zone_low = max(key_high - range_price * 0.25, current_price)
                entry_zone_high = min(key_high - range_price * 0.10, current_price + range_price * 0.08)
                if key_state == "near_low":
                    target_price = current_price - range_price * 0.10
                    target_price_2 = current_price - range_price * 0.30
                    entry_zone_low = current_price - range_price * 0.01
                    entry_zone_high = current_price + range_price * 0.04

    risk = abs(current_price - stop_price)
    reward = abs(target_price - current_price)
    if risk < RISK_REWARD_EPSILON or reward < RISK_REWARD_EPSILON:
        return build_empty_risk_reward_context()
    if entry_zone_low <= 0 or entry_zone_high <= 0:
        return build_empty_risk_reward_context()

    entry_zone_low, entry_zone_high = sorted((entry_zone_low, entry_zone_high))

    ratio = reward / risk
    if ratio >= 2.0:
        state = "favorable"
        state_text = "盈亏比优秀"
        position_text = "可轻仓试仓，优先分两段止盈，第一目标落袋后再看延续。"
    elif ratio >= 1.3:
        state = "acceptable"
        state_text = "盈亏比可接受"
        position_text = "仅适合轻仓观察单，先盯第一目标，不宜急着加仓。"
    else:
        state = "poor"
        state_text = "盈亏比偏差"
        position_text = "盈亏比偏低，尽量别主动追，除非后续结构继续改善。"

    direction_text = "多头" if direction == "bullish" else "空头"
    basis_text = (
        f"按 ATR(14)≈{_format_price(atr14, point)} 动态估算"
        if use_atr
        else "按近12小时关键区间估算"
    )
    context_text = (
        f"{direction_text}预估止损 {_format_price(stop_price, point)}，目标1 {_format_price(target_price, point)}"
        f" / 目标2 {_format_price(target_price_2, point)}，当前盈亏比约 {ratio:.2f}:1（{basis_text}）"
    )
    invalidation_text = (
        f"若价格重新跌回 {_format_price(stop_price, point)} 下方，当前{direction_text}结构可视为失效。"
        if direction == "bullish"
        else f"若价格重新站回 {_format_price(stop_price, point)} 上方，当前{direction_text}结构可视为失效。"
    )
    entry_zone_text = (
        f"观察进场区间 {_format_price(entry_zone_low, point)} - {_format_price(entry_zone_high, point)}，"
        "若价格直接远离该区间，就不建议追。"
    )
    return {
        "risk_reward_ready": True,
        "risk_reward_state": state,
        "risk_reward_state_text": state_text,
        "risk_reward_context_text": context_text,
        "risk_reward_ratio": ratio,
        "risk_reward_direction": direction,
        "risk_reward_basis": basis,
        "risk_reward_atr": atr14,
        "risk_reward_stop_price": stop_price,
        "risk_reward_target_price": target_price,
        "risk_reward_target_price_2": target_price_2,
        "risk_reward_position_text": position_text,
        "risk_reward_invalidation_text": invalidation_text,
        "risk_reward_entry_zone_low": entry_zone_low,
        "risk_reward_entry_zone_high": entry_zone_high,
        "risk_reward_entry_zone_text": entry_zone_text,
    }
