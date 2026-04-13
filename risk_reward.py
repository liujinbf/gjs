from __future__ import annotations


def build_empty_risk_reward_context() -> dict:
    return {
        "risk_reward_ready": False,
        "risk_reward_state": "unknown",
        "risk_reward_state_text": "盈亏比未知",
        "risk_reward_context_text": "",
        "risk_reward_ratio": 0.0,
        "risk_reward_direction": "unknown",
        "risk_reward_stop_price": 0.0,
        "risk_reward_target_price": 0.0,
        "risk_reward_target_price_2": 0.0,
        "risk_reward_position_text": "",
        "risk_reward_invalidation_text": "",
    }


def _resolve_direction(row: dict) -> str:
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
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
    if multi_alignment == "aligned" and multi_bias in {"bullish", "bearish"}:
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
    if min(current_price, key_high, key_low) <= 0 or key_high <= key_low:
        return build_empty_risk_reward_context()

    direction = _resolve_direction(row)
    if direction not in {"bullish", "bearish"}:
        return build_empty_risk_reward_context()

    range_price = max(key_high - key_low, 0.0)
    if range_price <= 0:
        return build_empty_risk_reward_context()

    stop_price = 0.0
    target_price = 0.0
    target_price_2 = 0.0
    if direction == "bullish":
        if retest_state == "confirmed_support":
            stop_price = key_high - range_price * 0.05
            target_price = current_price + range_price * 0.60
            target_price_2 = current_price + range_price * 1.00
        elif breakout_state == "confirmed_above":
            stop_price = key_high - range_price * 0.08
            target_price = current_price + range_price * 0.55
            target_price_2 = current_price + range_price * 0.90
        else:
            stop_price = key_low - range_price * 0.05
            target_price = key_high
            target_price_2 = current_price + range_price * 0.45
            if key_state == "near_high":
                target_price = current_price + range_price * 0.10
                target_price_2 = current_price + range_price * 0.30
    else:
        if retest_state == "confirmed_resistance":
            stop_price = key_low + range_price * 0.05
            target_price = current_price - range_price * 0.60
            target_price_2 = current_price - range_price * 1.00
        elif breakout_state == "confirmed_below":
            stop_price = key_low + range_price * 0.08
            target_price = current_price - range_price * 0.55
            target_price_2 = current_price - range_price * 0.90
        else:
            stop_price = key_high + range_price * 0.05
            target_price = key_low
            target_price_2 = current_price - range_price * 0.45
            if key_state == "near_low":
                target_price = current_price - range_price * 0.10
                target_price_2 = current_price - range_price * 0.30

    risk = abs(current_price - stop_price)
    reward = abs(target_price - current_price)
    if min(risk, reward) <= 0:
        return build_empty_risk_reward_context()

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
    context_text = (
        f"{direction_text}预估止损 {stop_price:.2f}，目标1 {target_price:.2f}"
        f" / 目标2 {target_price_2:.2f}，当前盈亏比约 {ratio:.2f}:1"
    )
    invalidation_text = (
        f"若价格重新跌回 {stop_price:.2f} 下方，当前{direction_text}结构可视为失效。"
        if direction == "bullish"
        else f"若价格重新站回 {stop_price:.2f} 上方，当前{direction_text}结构可视为失效。"
    )
    return {
        "risk_reward_ready": True,
        "risk_reward_state": state,
        "risk_reward_state_text": state_text,
        "risk_reward_context_text": context_text,
        "risk_reward_ratio": ratio,
        "risk_reward_direction": direction,
        "risk_reward_stop_price": stop_price,
        "risk_reward_target_price": target_price,
        "risk_reward_target_price_2": target_price_2,
        "risk_reward_position_text": position_text,
        "risk_reward_invalidation_text": invalidation_text,
    }
