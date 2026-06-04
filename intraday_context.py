from __future__ import annotations


def _symbol_thresholds(symbol: str) -> dict[str, float]:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return {"trend_pct": 0.12, "range_high_pct": 0.45, "range_low_pct": 0.10}
    if symbol_key.startswith("XAG"):
        return {"trend_pct": 0.22, "range_high_pct": 0.90, "range_low_pct": 0.20}
    return {"trend_pct": 0.05, "range_high_pct": 0.14, "range_low_pct": 0.03}


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


def build_empty_intraday_context() -> dict:
    return {
        "intraday_context_ready": False,
        "intraday_context_text": "",
        "intraday_bias": "unknown",
        "intraday_bias_text": "节奏不足",
        "intraday_volatility": "unknown",
        "intraday_volatility_text": "波动未知",
        "intraday_location": "unknown",
        "intraday_location_text": "位置未知",
        "price_structure_ready": False,
        "price_structure_direction": "unknown",
        "price_structure_strength": 0,
        "price_structure_state": "unknown",
        "price_structure_text": "",
    }


def _is_non_decreasing(values: list[float], tolerance: float) -> bool:
    if len(values) < 2:
        return False
    return all(values[index] >= values[index - 1] - tolerance for index in range(1, len(values)))


def _is_non_increasing(values: list[float], tolerance: float) -> bool:
    if len(values) < 2:
        return False
    return all(values[index] <= values[index - 1] + tolerance for index in range(1, len(values)))


def _segment_extremes(normalized: list[dict], parts: int = 4) -> tuple[list[float], list[float]]:
    if len(normalized) < parts:
        return [], []
    highs: list[float] = []
    lows: list[float] = []
    size = len(normalized) / parts
    for index in range(parts):
        start = int(round(index * size))
        end = int(round((index + 1) * size))
        segment = normalized[start:end] or normalized[max(0, start - 1): start + 1]
        if not segment:
            continue
        highs.append(max(float(item["high"] or 0.0) for item in segment))
        lows.append(min(float(item["low"] or 0.0) for item in segment))
    return highs, lows


def _analyze_price_structure(symbol: str, normalized: list[dict], thresholds: dict[str, float]) -> dict:
    if len(normalized) < 16:
        return {
            "price_structure_ready": False,
            "price_structure_direction": "unknown",
            "price_structure_strength": 0,
            "price_structure_state": "insufficient",
            "price_structure_text": "",
        }

    last_close = float(normalized[-1]["close"] or 0.0)
    first_open = float(normalized[0]["open"] or 0.0)
    highest = max(float(item["high"] or 0.0) for item in normalized)
    lowest = min(float(item["low"] or 0.0) for item in normalized)
    range_price = max(highest - lowest, 0.0)
    if min(last_close, first_open) <= 0 or range_price <= 0:
        return {
            "price_structure_ready": False,
            "price_structure_direction": "unknown",
            "price_structure_strength": 0,
            "price_structure_state": "invalid",
            "price_structure_text": "",
        }

    segment_highs, segment_lows = _segment_extremes(normalized, parts=4)
    tolerance = max(range_price * 0.06, last_close * 0.00025)
    net_change_pct = ((last_close - first_open) / first_open * 100.0) if first_open > 0 else 0.0
    location_ratio = (last_close - lowest) / range_price
    closes = [float(item["close"] or 0.0) for item in normalized]
    recent_window = max(6, min(20, len(closes) // 3))
    recent_avg = sum(closes[-recent_window:]) / recent_window
    previous_slice = closes[-recent_window * 2 : -recent_window]
    previous_avg = sum(previous_slice) / len(previous_slice) if previous_slice else closes[0]
    previous_high = max(float(item["high"] or 0.0) for item in normalized[:-recent_window]) if len(normalized) > recent_window else highest
    previous_low = min(float(item["low"] or 0.0) for item in normalized[:-recent_window]) if len(normalized) > recent_window else lowest

    highs_rising = _is_non_decreasing(segment_highs[-3:], tolerance)
    lows_rising = _is_non_decreasing(segment_lows[-3:], tolerance)
    highs_falling = _is_non_increasing(segment_highs[-3:], tolerance)
    lows_falling = _is_non_increasing(segment_lows[-3:], tolerance)
    trend_pct = float(thresholds.get("trend_pct", 0.05) or 0.05)

    bullish_score = 0
    bearish_score = 0
    if net_change_pct >= trend_pct:
        bullish_score += 1
    if net_change_pct <= -trend_pct:
        bearish_score += 1
    if highs_rising:
        bullish_score += 1
    if lows_rising:
        bullish_score += 1
    if highs_falling:
        bearish_score += 1
    if lows_falling:
        bearish_score += 1
    if recent_avg > previous_avg + tolerance * 0.25:
        bullish_score += 1
    if recent_avg < previous_avg - tolerance * 0.25:
        bearish_score += 1
    if last_close >= previous_high - tolerance and location_ratio >= 0.62:
        bullish_score += 1
    if last_close <= previous_low + tolerance and location_ratio <= 0.38:
        bearish_score += 1
    if location_ratio >= 0.72:
        bullish_score += 1
    if location_ratio <= 0.28:
        bearish_score += 1

    if bullish_score >= 4 and bullish_score >= bearish_score + 2:
        state = "breakout_hold" if last_close >= previous_high - tolerance else "higher_high_higher_low"
        text = "结构偏多：高低点逐级抬升，价格处在区间上半部，逆势短空应先禁止。"
        return {
            "price_structure_ready": True,
            "price_structure_direction": "bullish",
            "price_structure_strength": bullish_score,
            "price_structure_state": state,
            "price_structure_text": text,
        }
    if bearish_score >= 4 and bearish_score >= bullish_score + 2:
        state = "breakdown_hold" if last_close <= previous_low + tolerance else "lower_high_lower_low"
        text = "结构偏空：高低点逐级压低，价格处在区间下半部，逆势追多应先禁止。"
        return {
            "price_structure_ready": True,
            "price_structure_direction": "bearish",
            "price_structure_strength": bearish_score,
            "price_structure_state": state,
            "price_structure_text": text,
        }

    return {
        "price_structure_ready": True,
        "price_structure_direction": "range",
        "price_structure_strength": max(bullish_score, bearish_score),
        "price_structure_state": "mixed",
        "price_structure_text": "结构暂未单边：高低点或位置仍有分歧，先按区间纪律处理。",
    }


def analyze_intraday_bars(symbol: str, bars, lookback_label: str = "近1小时") -> dict:
    try:
        bars_list = list(bars) if bars is not None else []
    except (TypeError, ValueError):
        bars_list = []

    normalized = []
    for bar in bars_list:
        open_price = _bar_value(bar, "open")
        high_price = _bar_value(bar, "high")
        low_price = _bar_value(bar, "low")
        close_price = _bar_value(bar, "close")
        if min(open_price, high_price, low_price, close_price) <= 0:
            continue
        normalized.append(
            {
                "time": _bar_time(bar),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
            }
        )

    if len(normalized) < 4:
        return build_empty_intraday_context()

    normalized.sort(key=lambda item: (item["time"], item["close"]))
    first_open = float(normalized[0]["open"] or 0.0)
    last_close = float(normalized[-1]["close"] or 0.0)
    highest = max(float(item["high"] or 0.0) for item in normalized)
    lowest = min(float(item["low"] or 0.0) for item in normalized)
    if min(first_open, last_close, highest, lowest) <= 0 or highest < lowest:
        return build_empty_intraday_context()

    thresholds = _symbol_thresholds(symbol)
    range_price = max(highest - lowest, 0.0)
    range_pct = (range_price / last_close * 100.0) if last_close > 0 else 0.0
    net_change_pct = ((last_close - first_open) / first_open * 100.0) if first_open > 0 else 0.0
    location_ratio = ((last_close - lowest) / range_price) if range_price > 0 else 0.5

    if location_ratio >= 0.80:
        location = "upper"
        location_text = "贴近区间高位"
    elif location_ratio <= 0.20:
        location = "lower"
        location_text = "贴近区间低位"
    else:
        location = "middle"
        location_text = "处于区间中段"

    if range_pct >= thresholds["range_high_pct"]:
        volatility = "high"
        volatility_text = "波动放大"
    elif range_pct <= thresholds["range_low_pct"]:
        volatility = "low"
        volatility_text = "波动偏静"
    else:
        volatility = "normal"
        volatility_text = "波动正常"

    if net_change_pct >= thresholds["trend_pct"] and location_ratio >= 0.58:
        bias = "bullish"
        bias_text = "偏多"
    elif net_change_pct <= -thresholds["trend_pct"] and location_ratio <= 0.42:
        bias = "bearish"
        bias_text = "偏空"
    else:
        bias = "sideways"
        bias_text = "震荡"

    structure = _analyze_price_structure(symbol, normalized, thresholds)
    context_text = f"{lookback_label}{bias_text}，{location_text}，{volatility_text}"
    if structure.get("price_structure_ready") and structure.get("price_structure_direction") in {"bullish", "bearish"}:
        direction_text = "结构多头" if structure.get("price_structure_direction") == "bullish" else "结构空头"
        context_text = f"{context_text}，{direction_text}"

    return {
        "intraday_context_ready": True,
        "intraday_context_text": context_text,
        "intraday_bias": bias,
        "intraday_bias_text": bias_text,
        "intraday_volatility": volatility,
        "intraday_volatility_text": volatility_text,
        "intraday_location": location,
        "intraday_location_text": location_text,
        "intraday_range_pct": range_pct,
        "intraday_change_pct": net_change_pct,
        "intraday_location_ratio": location_ratio,
        "intraday_bar_count": len(normalized),
        **structure,
    }


def analyze_multi_timeframe_context(frame_contexts: dict[str, dict] | None) -> dict:
    contexts = {str(key or "").strip().lower(): dict(value or {}) for key, value in dict(frame_contexts or {}).items() if str(key or "").strip()}
    ready_contexts = {
        key: value
        for key, value in contexts.items()
        if bool(value.get("intraday_context_ready", False))
    }
    if not ready_contexts:
        return {
            "multi_timeframe_context_ready": False,
            "multi_timeframe_alignment": "unknown",
            "multi_timeframe_alignment_text": "多周期不足",
            "multi_timeframe_bias": "unknown",
            "multi_timeframe_bias_text": "待确认",
            "multi_timeframe_context_text": "",
            "multi_timeframe_detail": "",
        }

    ordered_keys = [key for key in ("m5", "m15", "h1", "h4") if key in contexts]
    if not ordered_keys:
        ordered_keys = list(contexts.keys())
    bias_map = {
        key: str(ready_contexts.get(key, {}).get("intraday_bias", "unknown") or "unknown").strip()
        for key in ordered_keys
        if key in ready_contexts
    }
    directional = {key: value for key, value in bias_map.items() if value in {"bullish", "bearish"}}
    bullish_keys = [key.upper() for key, value in directional.items() if value == "bullish"]
    bearish_keys = [key.upper() for key, value in directional.items() if value == "bearish"]
    frame_brief = []
    for key in ordered_keys:
        context = ready_contexts.get(key)
        if not context:
            continue
        frame_brief.append(f"{key.upper()} {str(context.get('intraday_bias_text', '待确认') or '待确认').strip()}")

    if bullish_keys and bearish_keys:
        return {
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "mixed",
            "multi_timeframe_alignment_text": "多周期分歧",
            "multi_timeframe_bias": "mixed",
            "multi_timeframe_bias_text": "方向分歧",
            "multi_timeframe_context_text": f"{' / '.join(frame_brief)}，多周期方向分歧",
            "multi_timeframe_detail": f"多周期分歧：{'、'.join(bullish_keys)} 偏多，{'、'.join(bearish_keys)} 偏空。",
        }

    if len(directional) >= 2:
        bias = "bullish" if bullish_keys else "bearish"
        bias_text = "偏多" if bias == "bullish" else "偏空"
        aligned_keys = bullish_keys or bearish_keys
        return {
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_alignment_text": "多周期同向",
            "multi_timeframe_bias": bias,
            "multi_timeframe_bias_text": bias_text,
            "multi_timeframe_context_text": f"{' / '.join(frame_brief)}，多周期同向{bias_text}",
            "multi_timeframe_detail": f"多周期同向：{'、'.join(aligned_keys)} 都偏{bias_text[-1]}。",
        }

    if directional:
        key, bias = next(iter(directional.items()))
        bias_text = "偏多" if bias == "bullish" else "偏空"
        return {
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "partial",
            "multi_timeframe_alignment_text": "多周期待确认",
            "multi_timeframe_bias": bias,
            "multi_timeframe_bias_text": bias_text,
            "multi_timeframe_context_text": f"{' / '.join(frame_brief)}，目前主要由 {key.upper()} {bias_text}",
            "multi_timeframe_detail": f"当前只有 {key.upper()} 给出明确方向，其他周期仍待确认。",
        }

    return {
        "multi_timeframe_context_ready": True,
        "multi_timeframe_alignment": "range",
        "multi_timeframe_alignment_text": "多周期震荡",
        "multi_timeframe_bias": "sideways",
        "multi_timeframe_bias_text": "震荡",
        "multi_timeframe_context_text": f"{' / '.join(frame_brief)}，多周期仍以震荡为主",
        "multi_timeframe_detail": "当前多个周期都还没有形成清晰方向，先观察更稳。",
    }
