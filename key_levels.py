from __future__ import annotations


def _bar_value(bar, key: str) -> float:
    if isinstance(bar, dict):
        return float(bar.get(key, 0.0) or 0.0)
    try:
        return float(bar[key] or 0.0)
    except Exception:  # noqa: BLE001
        return float(getattr(bar, key, 0.0) or 0.0)


def _bar_time(bar) -> int:
    if isinstance(bar, dict):
        return int(bar.get("time", 0) or 0)
    try:
        return int(bar["time"] or 0)
    except Exception:  # noqa: BLE001
        return int(getattr(bar, "time", 0) or 0)


def build_empty_key_level_context() -> dict:
    return {
        "key_level_ready": False,
        "key_level_context_text": "",
        "key_level_state": "unknown",
        "key_level_state_text": "关键位未知",
        "key_level_high": 0.0,
        "key_level_low": 0.0,
        "key_level_range_pct": 0.0,
        "key_level_location_ratio": 0.5,
    }


def analyze_key_levels(symbol: str, latest_price: float, bars, lookback_label: str = "近12小时") -> dict:
    # BUG-009 修复：MT5 copy_rates_from_pos() 返回 numpy structured array。
    # 复合条件判断中 numpy array 的隐式布尔运算会触发
    # "ValueError: The truth value of an array with more than one element is ambiguous"。
    # 改用 try/except 安全转换，与 technical_indicators._extract_closes 保持相同模式。
    try:
        bars_list = list(bars) if bars is not None else []
    except (TypeError, ValueError):
        bars_list = []

    normalized = []
    for bar in bars_list:
        high_price = _bar_value(bar, "high")
        low_price = _bar_value(bar, "low")
        close_price = _bar_value(bar, "close")
        if min(high_price, low_price, close_price) <= 0 or high_price < low_price:
            continue
        normalized.append(
            {
                "time": _bar_time(bar),
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )

    current_price = float(latest_price or 0.0)
    if len(normalized) < 4 or current_price <= 0:
        return build_empty_key_level_context()

    normalized.sort(key=lambda item: (item["time"], item["close"]))
    highest = max(float(item["high"] or 0.0) for item in normalized)
    lowest = min(float(item["low"] or 0.0) for item in normalized)
    if min(highest, lowest) <= 0 or highest <= lowest:
        return build_empty_key_level_context()

    range_price = max(highest - lowest, 0.0)
    range_pct = (range_price / current_price * 100.0) if current_price > 0 else 0.0
    location_ratio = ((current_price - lowest) / range_price) if range_price > 0 else 0.5
    breakout_buffer = max(range_price * 0.03, current_price * 0.0002)

    if current_price > highest + breakout_buffer:
        state = "breakout_above"
        state_text = "上破关键位"
        context_text = f"{lookback_label}刚上破高点，先等回踩确认，别在第一脚追多"
    elif current_price < lowest - breakout_buffer:
        state = "breakout_below"
        state_text = "下破关键位"
        context_text = f"{lookback_label}刚下破低点，先等反抽确认，别在第一脚追空"
    elif location_ratio >= 0.88:
        state = "near_high"
        state_text = "贴近高位"
        context_text = f"当前贴近{lookback_label}高位，位置偏贵，先别直接追多"
    elif location_ratio <= 0.12:
        state = "near_low"
        state_text = "贴近低位"
        context_text = f"当前贴近{lookback_label}低位，位置偏深，先别直接追空"
    else:
        state = "mid_range"
        state_text = "位于区间中段"
        context_text = f"当前位于{lookback_label}区间中段，位置相对中性"

    return {
        "key_level_ready": True,
        "key_level_context_text": context_text,
        "key_level_state": state,
        "key_level_state_text": state_text,
        "key_level_high": highest,
        "key_level_low": lowest,
        "key_level_range_pct": range_pct,
        "key_level_location_ratio": location_ratio,
    }
