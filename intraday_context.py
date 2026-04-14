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
    }


def analyze_intraday_bars(symbol: str, bars, lookback_label: str = "近1小时") -> dict:
    normalized = []
    for bar in (list(bars) if bars is not None and hasattr(bars, '__len__') and len(bars) > 0 else []):
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

    return {
        "intraday_context_ready": True,
        "intraday_context_text": f"{lookback_label}{bias_text}，{location_text}，{volatility_text}",
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
