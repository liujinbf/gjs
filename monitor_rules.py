from __future__ import annotations

from app_config import get_quote_risk_thresholds
from signal_enums import AlertTone, QuoteStatus, TradeGrade
from app_config import get_sim_strategy_min_rr


def format_quote_price(value: float, point: float = 0.0) -> str:
    decimals = 2
    point_value = max(float(point or 0.0), 0.0)
    if point_value > 0:
        point_text = f"{point_value:.10f}".rstrip("0").rstrip(".")
        if "." in point_text:
            decimals = max(2, min(6, len(point_text.split(".")[1])))
    return f"{float(value or 0.0):.{decimals}f}"


def _symbol_family(symbol: str) -> str:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith(("XAU", "XAG")):
        return "metal"
    return "fx"


def _intraday_context_text(row: dict) -> str:
    return str(row.get("intraday_context_text", "") or "").strip()


def _multi_timeframe_context_text(row: dict) -> str:
    return str(row.get("multi_timeframe_context_text", "") or "").strip()


def _key_level_context_text(row: dict) -> str:
    return str(row.get("key_level_context_text", "") or "").strip()


def _breakout_context_text(row: dict) -> str:
    return str(row.get("breakout_context_text", "") or "").strip()


def _retest_context_text(row: dict) -> str:
    return str(row.get("retest_context_text", "") or "").strip()


def _risk_reward_context_text(row: dict) -> str:
    return str(row.get("risk_reward_context_text", "") or "").strip()


def _normalize_risk_reward_state(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"good", "excellent"}:
        return "favorable"
    if text in {"favorable", "acceptable", "poor", "unknown"}:
        return text
    return "unknown" if not text else text


def _resolve_risk_reward_state(row: dict) -> str:
    state = _normalize_risk_reward_state(row.get("risk_reward_state", "unknown"))
    if state != "unknown":
        return state
    state_text = str(row.get("risk_reward_state_text", "") or "").strip()
    if "优秀" in state_text:
        return "favorable"
    if "可接受" in state_text:
        return "acceptable"
    if "偏差" in state_text:
        return "poor"
    if "未知" in state_text:
        return "unknown"
    return state


def _normalize_event_importance(value: str) -> str:
    text = str(value or "").strip().lower()
    if text == "high":
        return "high"
    if text == "low":
        return "low"
    return "medium"


def _event_targets_symbol(event_context: dict | None, symbol: str) -> bool:
    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    if not active_name:
        return True
    targets = {
        str(item or "").strip().upper()
        for item in list(context.get("active_event_symbols", []) or [])
        if str(item or "").strip()
    }
    if not targets:
        return True
    return str(symbol or "").strip().upper() in targets


def _is_directional_confirmation_ready(direction: str, breakout_state: str, retest_state: str) -> bool:
    if direction == "bullish":
        return breakout_state == "confirmed_above" or retest_state == "confirmed_support"
    if direction == "bearish":
        return breakout_state == "confirmed_below" or retest_state == "confirmed_resistance"
    return False


def _build_early_momentum_candidate(symbol_key: str, family: str, row: dict) -> dict[str, str] | None:
    if family != "metal":
        return None

    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_ready = bool(row.get("key_level_ready", False))
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)
    risk_reward_ratio = float(row.get("risk_reward_ratio", 0.0) or 0.0)
    risk_reward_direction = str(row.get("risk_reward_direction", "unknown") or "unknown").strip()
    risk_reward_basis = str(row.get("risk_reward_basis", "unknown") or "unknown").strip()

    if not intraday_ready or intraday_bias not in {"bullish", "bearish"}:
        return None
    if intraday_volatility in {"low", "unknown"}:
        return None
    if not multi_ready or multi_alignment not in {"aligned", "partial"} or multi_bias != intraday_bias:
        return None
    if not risk_reward_ready or risk_reward_state not in {"acceptable", "favorable"}:
        return None
    if risk_reward_ratio < 1.3:
        return None
    if risk_reward_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None
    if breakout_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None
    if breakout_state in {"none", "unknown"} and retest_state in {"none", "unknown"}:
        return None
    if _is_directional_confirmation_ready(intraday_bias, breakout_state, retest_state):
        return None
    if retest_ready and retest_state in {"failed_support", "failed_resistance"}:
        return None
    if breakout_ready and breakout_state in {"failed_above", "failed_below"}:
        return None

    pending_breakout = (
        (intraday_bias == "bullish" and breakout_state == "pending_above")
        or (intraday_bias == "bearish" and breakout_state == "pending_below")
    )
    near_trigger_zone = (
        key_level_ready
        and (
            (intraday_bias == "bullish" and key_level_state in {"near_high", "breakout_above"})
            or (intraday_bias == "bearish" and key_level_state in {"near_low", "breakout_below"})
        )
    )
    if not pending_breakout and not near_trigger_zone:
        return None

    intraday_text = _intraday_context_text(row)
    multi_text = _multi_timeframe_context_text(row)
    breakout_text = _breakout_context_text(row)
    key_level_text = _key_level_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    direction_text = "偏多" if intraday_bias == "bullish" else "偏空"
    alignment_text = "多周期同向" if multi_alignment == "aligned" else "M5/M15 已先同向，较大周期仍在跟随"

    detail_parts = [
        f"{symbol_key} 当前先进入早期动能候选：短线{direction_text}、{alignment_text}，执行面允许先轻仓跟踪。"
    ]
    if pending_breakout:
        detail_parts.append(breakout_text or "价格正在试探关键位，属于未完全确认前的第一段动能。")
    elif near_trigger_zone:
        detail_parts.append(key_level_text or "价格已经逼近关键触发位，但还没走出完整突破/回踩确认。")
    if intraday_text:
        detail_parts.append(intraday_text)
    if multi_text:
        detail_parts.append(multi_text)
    if risk_reward_text:
        detail_parts.append(risk_reward_text)
    if risk_reward_basis == "atr_fallback":
        detail_parts.append("当前盈亏比仍带临时估算成分，只能按低置信轻仓候选看待。")

    return {
        "grade": TradeGrade.LIGHT_POSITION.value,
        "detail": " ".join(part for part in detail_parts if part),
        "next_review": "把它当早期动能候选处理：优先盯下一到两根 M5 是否确认突破/回踩；一旦失守关键位就立即取消。",
        "tone": AlertTone.SUCCESS.value,
        "source": "setup",
        "setup_kind": "early_momentum",
    }


def _build_direct_momentum_candidate(symbol_key: str, family: str, row: dict) -> dict[str, str] | None:
    if family != "metal":
        return None

    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_location = str(row.get("intraday_location", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_ready = bool(row.get("key_level_ready", False))
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)
    risk_reward_ratio = float(row.get("risk_reward_ratio", 0.0) or 0.0)
    risk_reward_direction = str(row.get("risk_reward_direction", "unknown") or "unknown").strip()
    risk_reward_basis = str(row.get("risk_reward_basis", "unknown") or "unknown").strip()

    if not intraday_ready or intraday_bias not in {"bullish", "bearish"}:
        return None
    if intraday_volatility not in {"elevated", "high"}:
        return None
    if not multi_ready or multi_alignment not in {"aligned", "partial"} or multi_bias != intraday_bias:
        return None
    if not risk_reward_ready or risk_reward_state not in {"acceptable", "favorable"}:
        return None
    if risk_reward_ratio < (1.6 if multi_alignment == "partial" else 1.4):
        return None
    if breakout_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None
    if risk_reward_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None
    if breakout_ready and breakout_state not in {"none", "unknown"}:
        return None
    if retest_ready and retest_state not in {"none", "unknown"}:
        return None

    directional_edge = (
        (intraday_bias == "bullish" and intraday_location == "upper")
        or (intraday_bias == "bearish" and intraday_location == "lower")
    )
    near_trigger_zone = (
        key_level_ready
        and (
            (intraday_bias == "bullish" and key_level_state in {"near_high", "breakout_above"})
            or (intraday_bias == "bearish" and key_level_state in {"near_low", "breakout_below"})
        )
    )
    mid_range_launch = (
        key_level_ready
        and key_level_state == "mid_range"
        and multi_alignment == "aligned"
        and risk_reward_ratio >= 1.8
    )
    if not directional_edge and not near_trigger_zone and not mid_range_launch:
        return None

    intraday_text = _intraday_context_text(row)
    multi_text = _multi_timeframe_context_text(row)
    key_level_text = _key_level_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    direction_text = "偏多" if intraday_bias == "bullish" else "偏空"
    alignment_text = "多周期同向" if multi_alignment == "aligned" else "M5/M15 已先同向，较大周期仍在跟随"

    detail_parts = [
        f"{symbol_key} 当前进入直线动能候选：短线{direction_text}且波动正在扩张，{alignment_text}，暂时不必死等回踩才跟踪。"
    ]
    if intraday_text:
        detail_parts.append(intraday_text)
    if key_level_text:
        detail_parts.append(key_level_text)
    elif mid_range_launch:
        detail_parts.append("价格虽然还在区间中段，但当前更像是中段起动后的直线扩张，不适合再死等完整回踩。")
    if multi_text:
        detail_parts.append(multi_text)
    if risk_reward_text:
        detail_parts.append(risk_reward_text)
    detail_parts.append("当前属于无回踩动能延续场景，只能轻仓跟，不能把它当成熟回踩结构。")
    if risk_reward_basis == "atr_fallback":
        detail_parts.append("盈亏比仍带临时估算成分，失速时要更快退出。")

    return {
        "grade": TradeGrade.LIGHT_POSITION.value,
        "detail": " ".join(part for part in detail_parts if part),
        "next_review": "按直线动能候选处理：盯下一根到两根 M5 是否继续加速；一旦转入横盘或跌回关键位，立即取消。",
        "tone": AlertTone.SUCCESS.value,
        "source": "setup",
        "setup_kind": "direct_momentum",
    }


def _build_directional_probe_candidate(symbol_key: str, family: str, row: dict) -> dict[str, str] | None:
    if family != "metal":
        return None

    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_ready = bool(row.get("key_level_ready", False))
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)
    risk_reward_ratio = float(row.get("risk_reward_ratio", 0.0) or 0.0)
    risk_reward_direction = str(row.get("risk_reward_direction", "unknown") or "unknown").strip()
    risk_reward_basis = str(row.get("risk_reward_basis", "unknown") or "unknown").strip()

    if not intraday_ready or intraday_bias not in {"bullish", "bearish"}:
        return None
    if intraday_volatility not in {"elevated", "high"}:
        return None
    if not multi_ready or multi_alignment != "mixed" or multi_bias not in {"mixed", "unknown", ""}:
        return None
    if not key_level_ready or key_level_state != "mid_range":
        return None
    if not risk_reward_ready or risk_reward_state not in {"acceptable", "favorable"}:
        return None
    if risk_reward_ratio < 1.8:
        return None
    if breakout_ready and breakout_state not in {"none", "unknown"}:
        return None
    if retest_ready and retest_state not in {"none", "unknown"}:
        return None
    if risk_reward_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None

    intraday_text = _intraday_context_text(row)
    multi_text = _multi_timeframe_context_text(row)
    key_level_text = _key_level_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    direction_text = "偏多" if intraday_bias == "bullish" else "偏空"

    detail_parts = [
        f"{symbol_key} 当前进入方向试仓候选：短线{direction_text}、波动放大、盈亏比达标，虽然多周期还没完全共振，但已经具备轻仓试探价值。"
    ]
    if intraday_text:
        detail_parts.append(intraday_text)
    if multi_text:
        detail_parts.append(f"{multi_text}，说明更大级别还在跟随，仓位必须更轻。")
    else:
        detail_parts.append("当前大级别仍未完全跟上，所以这里只能按探索式轻仓处理。")
    if key_level_text:
        detail_parts.append(key_level_text)
    else:
        detail_parts.append("价格仍处于区间中段，更像是中段起动后的第一段方向性试探。")
    if risk_reward_text:
        detail_parts.append(risk_reward_text)
    if risk_reward_basis == "atr_fallback":
        detail_parts.append("止损目标仍带临时估算成分，失速时要更快撤退。")

    return {
        "grade": TradeGrade.LIGHT_POSITION.value,
        "detail": " ".join(part for part in detail_parts if part),
        "next_review": "按方向试仓候选处理：轻仓试探，盯下一到两根 M5 是否继续顺着方向扩张；一旦重新转回横盘就立即取消。",
        "tone": AlertTone.SUCCESS.value,
        "source": "setup",
        "setup_kind": "directional_probe",
    }


def _build_pullback_sniper_candidate(symbol_key: str, family: str, row: dict) -> dict[str, str] | None:
    if family != "metal":
        return None

    latest_price = float(row.get("latest_price", 0.0) or 0.0)
    ma20 = float(row.get("ma20", 0.0) or 0.0)
    ma50 = float(row.get("ma50", 0.0) or 0.0)
    ma20_h4 = float(row.get("ma20_h4", 0.0) or 0.0)
    ma50_h4 = float(row.get("ma50_h4", 0.0) or 0.0)
    rsi14 = float(row.get("rsi14", 0.0) or 0.0)
    atr14 = float(row.get("atr14", 0.0) or 0.0)
    macd_histogram = float(row.get("macd_histogram", 0.0) or 0.0)
    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_location = str(row.get("intraday_location", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)
    risk_reward_ratio = float(row.get("risk_reward_ratio", 0.0) or 0.0)
    risk_reward_direction = str(row.get("risk_reward_direction", "unknown") or "unknown").strip()
    risk_reward_basis = str(row.get("risk_reward_basis", "unknown") or "unknown").strip()

    if min(latest_price, ma20, ma50, atr14) <= 0:
        return None
    if not intraday_ready or intraday_bias not in {"bullish", "bearish"}:
        return None
    if intraday_volatility in {"low", "unknown"}:
        return None
    if not multi_ready or multi_alignment not in {"aligned", "partial"} or multi_bias != intraday_bias:
        return None
    if not risk_reward_ready or risk_reward_state not in {"acceptable", "favorable"}:
        return None
    min_rr = get_sim_strategy_min_rr("pullback_sniper_probe", default=1.45)
    if risk_reward_ratio < min_rr:
        return None
    if risk_reward_direction not in {"unknown", "", "neutral", intraday_bias}:
        return None
    if breakout_ready and breakout_state in {"failed_above", "failed_below"}:
        return None
    if retest_ready and retest_state in {"failed_support", "failed_resistance"}:
        return None

    near_value_ma = abs(latest_price - ma20) <= max(atr14 * 0.6, latest_price * 0.0008)
    if not near_value_ma:
        return None

    if intraday_bias == "bullish":
        h1_trend_ok = latest_price > ma50 and ma20 > ma50
        h4_trend_ok = ma20_h4 <= 0 or ma50_h4 <= 0 or ma20_h4 >= ma50_h4
        momentum_ok = macd_histogram >= 0
        not_chasing = intraday_location not in {"upper"} and key_level_state not in {"near_high", "breakout_above"}
        value_zone_text = "回踩到 H1 MA20 价值区后仍守在 MA50 上方"
    else:
        h1_trend_ok = latest_price < ma50 and ma20 < ma50
        h4_trend_ok = ma20_h4 <= 0 or ma50_h4 <= 0 or ma20_h4 <= ma50_h4
        momentum_ok = macd_histogram <= 0
        not_chasing = intraday_location not in {"lower"} and key_level_state not in {"near_low", "breakout_below"}
        value_zone_text = "反抽到 H1 MA20 价值区后仍压在 MA50 下方"
    if not h1_trend_ok or not h4_trend_ok or not momentum_ok or not not_chasing:
        return None
    if not 35 <= rsi14 <= 65:
        return None

    intraday_text = _intraday_context_text(row)
    multi_text = _multi_timeframe_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    direction_text = "偏多" if intraday_bias == "bullish" else "偏空"
    rr_text = f"盈亏比约 {risk_reward_ratio:.2f}:1"
    detail_parts = [
        (
            f"{symbol_key} 当前进入回调狙击候选：短线{direction_text}，{value_zone_text}，"
            f"RSI={rsi14:.1f} 未过热，{rr_text}，适合按探索试仓采样。"
        )
    ]
    if multi_text:
        detail_parts.append(multi_text)
    if intraday_text:
        detail_parts.append(intraday_text)
    if risk_reward_text:
        detail_parts.append(risk_reward_text)
    if risk_reward_basis == "atr_fallback":
        detail_parts.append("止损目标仍含 ATR 临时估算，必须小仓位、快复核。")

    return {
        "grade": TradeGrade.LIGHT_POSITION.value,
        "detail": " ".join(part for part in detail_parts if part),
        "next_review": "按回调狙击候选处理：只做固定本金探索试仓；若价格重新远离 MA20 价值区或 RSI 快速过热，下一轮取消。",
        "tone": AlertTone.SUCCESS.value,
        "source": "setup",
        "setup_kind": "pullback_sniper_probe",
    }


def _can_release_post_event_continuation(
    symbol: str,
    family: str,
    row: dict,
    tone: str,
    event_risk_mode: str,
    event_context: dict | None = None,
) -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    context = dict(event_context or {})
    if mode != "post_event" or family != "metal":
        return None
    if not _event_targets_symbol(context, symbol):
        return None

    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    if importance != "high":
        return None
    if tone != AlertTone.SUCCESS.value:
        return None
    if not bool(row.get("has_live_quote", False)):
        return None

    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)
    risk_reward_direction = str(row.get("risk_reward_direction", "unknown") or "unknown").strip()
    risk_reward_ratio = float(row.get("risk_reward_ratio", 0.0) or 0.0)

    if not multi_ready or multi_alignment != "aligned" or multi_bias not in {"bullish", "bearish"}:
        return None
    if not intraday_ready or intraday_bias != multi_bias or intraday_volatility in {"low", "unknown"}:
        return None
    if not risk_reward_ready or risk_reward_state not in {"acceptable", "favorable"}:
        return None
    if risk_reward_ratio < 1.6:
        return None
    if breakout_direction not in {"unknown", "", "neutral", multi_bias}:
        return None
    if risk_reward_direction not in {"unknown", "", "neutral", multi_bias}:
        return None
    if not _is_directional_confirmation_ready(multi_bias, breakout_state, retest_state):
        return None

    active_name = str(context.get("active_event_name", "") or "").strip()
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or "高影响"
    return {
        "event_override_kind": "post_event_continuation",
        "event_override_note": (
            f"{importance_text}事件 {active_name or '当前数据'} 已落地，且报价、结构、盈亏比已重新同步，"
            "当前按事件后延续候选处理，但仍需快进快出、短周期复核。"
        ),
        "detail_prefix": (
            f"{importance_text}事件后已出现二次确认，当前不再按第一脚重定价处理。"
        ),
        "next_review_prefix": "建议 3-5 分钟内复核一次关键位、点差和回踩是否继续成立。",
    }


def _build_event_mode_adjustment(
    event_risk_mode: str,
    event_context: dict | None = None,
    symbol: str = "",
    allow_post_event_continuation: bool = False,
) -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    active_time_text = str(context.get("active_event_time_text", "") or "").strip()
    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or {
        "high": "高影响",
        "medium": "中影响",
        "low": "低影响",
    }.get(importance, "中影响")
    scope_text = str(context.get("active_event_scope_text", "") or "").strip()

    if mode in {"pre_event", "post_event"} and not _event_targets_symbol(context, symbol):
        return None

    if mode == "pre_event":
        if active_name:
            if importance == "high":
                return {
                    "grade": TradeGrade.NO_TRADE.value,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                        f"{scope_text or '会直接影响当前品种'}，数据前第一脚和点差都更容易失真。"
                    ),
                    "next_review": "至少等事件公布后 15-20 分钟，并确认点差明显收敛后再复核。",
                    "tone": AlertTone.WARNING.value,
                    "source": "event",
                }
            if importance == "low":
                return {
                    "grade": TradeGrade.OBSERVE_ONLY.value,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                        "但短线节奏仍可能被打乱，先观察别抢。"
                    ),
                    "next_review": "等事件落地后 5-10 分钟，再复核短线节奏和点差。",
                    "tone": AlertTone.ACCENT.value,
                    "source": "event",
                }
            return {
                "grade": TradeGrade.WAIT_EVENT.value,
                "detail": (
                    f"{importance_text}窗口：{active_name} 将在 {active_time_text or '稍后'} 落地，"
                    "当前先别抢第一脚波动。"
                ),
                "next_review": "等事件公布后 10-15 分钟，并确认点差开始收敛后再复核。",
                "tone": AlertTone.WARNING.value,
                "source": "event",
            }
        return {
            "grade": TradeGrade.WAIT_EVENT.value,
            "detail": "当前处于事件前高敏阶段，第一脚波动和点差都更容易失真，先别抢。",
            "next_review": "等事件公布后 15 分钟，并确认点差明显收敛后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "event",
        }
    if mode == "post_event":
        if active_name:
            if importance == "high":
                if allow_post_event_continuation:
                    return None
                return {
                    "grade": TradeGrade.NO_TRADE.value,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                        "市场往往还在重新定价阶段，别急着追第二脚。"
                    ),
                    "next_review": "至少等 15-20 分钟，并确认关键位与点差一起稳定后再复核。",
                    "tone": AlertTone.WARNING.value,
                    "source": "event",
                }
            if importance == "low":
                return {
                    "grade": TradeGrade.OBSERVE_ONLY.value,
                    "detail": (
                        f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                        "但短线还可能有一次回摆，先别急着追。"
                    ),
                    "next_review": "建议 5-10 分钟后再复核方向、点差和关键位。",
                    "tone": AlertTone.ACCENT.value,
                    "source": "event",
                }
            return {
                "grade": TradeGrade.OBSERVE_ONLY.value,
                "detail": (
                    f"{importance_text}窗口：{active_name} 已在 {active_time_text or '刚才'} 落地，"
                    "方向还在重新定价阶段，先观察再决定更稳。"
                ),
                "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
                "tone": AlertTone.ACCENT.value,
                "source": "event",
            }
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": "事件刚落地，方向还在重新定价阶段，先等波动和报价稳定下来。",
            "next_review": "建议 10-15 分钟后再复核方向、点差和关键位。",
            "tone": AlertTone.ACCENT.value,
            "source": "event",
        }
    if mode == "illiquid":
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "当前人为标记为流动性偏弱阶段，点差和执行成本都不适合普通用户硬做。",
            "next_review": "等进入正常观察模式后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "event",
        }
    return None


def _build_clean_quote_grade_with_context(symbol_key: str, family: str, row: dict) -> dict[str, str]:
    context_text = _intraday_context_text(row)
    multi_context_text = _multi_timeframe_context_text(row)
    key_level_text = _key_level_context_text(row)
    breakout_text = _breakout_context_text(row)
    retest_text = _retest_context_text(row)
    risk_reward_text = _risk_reward_context_text(row)
    intraday_ready = bool(row.get("intraday_context_ready", False))
    intraday_bias = str(row.get("intraday_bias", "unknown") or "unknown").strip()
    intraday_location = str(row.get("intraday_location", "unknown") or "unknown").strip()
    intraday_volatility = str(row.get("intraday_volatility", "unknown") or "unknown").strip()
    multi_ready = bool(row.get("multi_timeframe_context_ready", False))
    multi_alignment = str(row.get("multi_timeframe_alignment", "unknown") or "unknown").strip()
    multi_bias = str(row.get("multi_timeframe_bias", "unknown") or "unknown").strip()
    key_level_ready = bool(row.get("key_level_ready", False))
    key_level_state = str(row.get("key_level_state", "unknown") or "unknown").strip()
    breakout_ready = bool(row.get("breakout_ready", False))
    breakout_state = str(row.get("breakout_state", "unknown") or "unknown").strip()
    breakout_direction = str(row.get("breakout_direction", "unknown") or "unknown").strip()
    retest_ready = bool(row.get("retest_ready", False))
    retest_state = str(row.get("retest_state", "unknown") or "unknown").strip()
    risk_reward_ready = bool(row.get("risk_reward_ready", False))
    risk_reward_state = _resolve_risk_reward_state(row)

    bullish_pressure = multi_bias == "bullish" or intraday_bias == "bullish"
    bearish_pressure = multi_bias == "bearish" or intraday_bias == "bearish"

    if retest_ready and retest_state in {"failed_support", "failed_resistance"}:
        detail = retest_text or "突破后的回踩/反抽已经失败，当前更像是假动作。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "至少再等一到两轮 M5 重新站稳关键位后再复核，不要在失败回踩后硬追。",
            "tone": AlertTone.ACCENT.value,
        }

    if breakout_ready and breakout_state in {"failed_above", "failed_below"}:
        detail = breakout_text or "刚尝试突破关键位又收回，疑似假突破，先不要追。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "优先等下一到两根 M5 收线确认，别在假突破后第一时间反手硬追。",
            "tone": AlertTone.ACCENT.value,
        }

    pullback_sniper_candidate = _build_pullback_sniper_candidate(symbol_key, family, row)
    if pullback_sniper_candidate is not None:
        return pullback_sniper_candidate

    early_momentum_candidate = _build_early_momentum_candidate(symbol_key, family, row)
    if early_momentum_candidate is not None:
        return early_momentum_candidate

    direct_momentum_candidate = _build_direct_momentum_candidate(symbol_key, family, row)
    if direct_momentum_candidate is not None:
        return direct_momentum_candidate

    directional_probe_candidate = _build_directional_probe_candidate(symbol_key, family, row)
    if directional_probe_candidate is not None:
        return directional_probe_candidate

    if breakout_ready and breakout_state in {"pending_above", "pending_below"}:
        detail = breakout_text or "价格正在尝试突破关键位，但当前还没确认。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "至少再看一到两根 M5 收线，确认站稳或失守后再决定。",
            "tone": AlertTone.ACCENT.value,
        }

    if key_level_ready and key_level_state in {"near_high", "breakout_above"} and bullish_pressure and breakout_state != "confirmed_above":
        detail = "点差和节奏都不差，但价格已经顶到关键位上沿，直接追多的性价比不高。"
        if key_level_text:
            detail = f"点差和节奏都不差，但{key_level_text}。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "优先等回踩确认或突破后二次站稳，再复核是否还有空间。",
            "tone": AlertTone.ACCENT.value,
        }

    if key_level_ready and key_level_state in {"near_low", "breakout_below"} and bearish_pressure and breakout_state != "confirmed_below":
        detail = "点差和节奏都不差，但价格已经压到关键位下沿，直接追空的性价比不高。"
        if key_level_text:
            detail = f"点差和节奏都不差，但{key_level_text}。"
        if multi_context_text:
            detail += f" 当前{multi_context_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "优先等反抽确认或跌破后二次失守，再复核是否还有空间。",
            "tone": AlertTone.ACCENT.value,
        }

    if risk_reward_ready and risk_reward_state == "poor":
        detail = risk_reward_text or "当前结构虽然不算差，但这笔盈亏比不划算。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "优先等回踩更深一点，或等目标空间重新拉开后再复核。",
            "tone": AlertTone.NEUTRAL.value,
        }

    if multi_ready and multi_alignment == "mixed":
        detail = "点差虽然稳定，但多周期方向正在打架，这种环境很容易出现假突破。"
        if multi_context_text:
            detail = f"点差虽然稳定，但{multi_context_text}，这种环境很容易出现假突破。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "建议等 M5 / M15 / H1 至少两档重新同向后再复核。",
            "tone": AlertTone.NEUTRAL.value,
        }

    if intraday_ready and (intraday_volatility == "low" or intraday_bias == "sideways"):
        detail = "点差虽然稳定，但短线节奏还不够干净，先别为了有报价就硬找机会。"
        if context_text:
            detail = f"点差虽然稳定，但{context_text}，短线边际还不够明显。"
        if multi_context_text and multi_alignment in {"range", "partial"}:
            detail += f" 同时{multi_context_text}。"
        if key_level_text and key_level_state == "mid_range":
            detail += f" {key_level_text}。"
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": detail,
            "next_review": "建议 5-10 分钟后再看一次短线节奏和关键位变化。",
            "tone": AlertTone.NEUTRAL.value,
        }

    if family == "metal":
        detail = "执行层面当前较干净，点差稳定、报价活跃，可以把它视作候选机会，但仍要配合 MT5 图表确认关键位。"
        next_review = "如果准备出手，建议先以轻仓试探，并在 10-15 分钟内复核节奏。"
        if retest_ready and retest_state in {"confirmed_support", "confirmed_resistance"} and multi_ready and multi_alignment == "aligned":
            detail = f"执行层面当前较干净，且{retest_text or '突破后的回踩已经守住'}，同时{multi_context_text or '多周期也在配合'}，可以把它视作更完整的候选机会。"
            next_review = "优先盯突破位/回踩位是否继续守住，5 分钟内复核一次 M5 收线和点差。"
        elif breakout_ready and breakout_state in {"confirmed_above", "confirmed_below"} and multi_ready and multi_alignment == "aligned":
            detail = f"执行层面当前较干净，且{breakout_text or '突破已经确认'}，同时{multi_context_text or '多周期也在配合'}，可以把它视作候选机会，但仍建议等回踩确认。"
            next_review = "优先盯突破位回踩是否守住，5 分钟内复核一次 M5 收线和点差。"
        elif multi_ready and multi_alignment == "aligned" and multi_bias in {"bullish", "bearish"}:
            detail = f"执行层面当前较干净，且{multi_context_text or '多周期已经同向'}，可以把它视作候选机会，但仍要等回踩或二次确认。"
            next_review = "优先等 M5 回踩或二次确认，5-10 分钟内复核一次多周期是否继续同向。"
        elif intraday_ready and intraday_bias in {"bullish", "bearish"}:
            detail = f"执行层面当前较干净，且{context_text or '短线已有方向性'}，可以把它视作候选机会，但仍要等回踩或二次确认。"
            if intraday_location in {"upper", "lower"}:
                next_review = "优先等回踩或二次确认，5-10 分钟内复核一次短线节奏后再决定。"
        if risk_reward_text:
            detail += f" {risk_reward_text}。"
        return {
            "grade": TradeGrade.LIGHT_POSITION.value,
            "detail": detail,
            "next_review": next_review,
            "tone": AlertTone.SUCCESS.value,
        }

    detail = "外汇报价虽然稳定，但更容易受央行和美元方向扰动，普通用户先观察会更稳。"
    if retest_ready and retest_state in {"confirmed_support", "confirmed_resistance"} and multi_ready and multi_alignment == "aligned":
        detail = f"外汇报价当前不差，而且{retest_text or '回踩确认已经出现'}，但普通用户仍建议先等美元方向和二次确认。"
    elif breakout_ready and breakout_state in {"confirmed_above", "confirmed_below"} and multi_ready and multi_alignment == "aligned":
        detail = f"外汇报价当前不差，而且{breakout_text or '突破已经确认'}，但普通用户仍建议先等美元方向和二次确认。"
    elif multi_ready and multi_alignment == "aligned" and multi_bias in {"bullish", "bearish"}:
        detail = f"外汇报价当前不差，而且{multi_context_text or '多周期刚形成同向'}，但普通用户仍建议先等美元方向和二次确认。"
    elif intraday_ready and intraday_bias in {"bullish", "bearish"}:
        detail = f"外汇报价当前不差，但{context_text or '短线方向刚形成'}，仍建议先等美元方向和二次确认。"
    if key_level_text and key_level_state == "mid_range":
        detail += f" {key_level_text}。"
    if risk_reward_text and risk_reward_state in {"acceptable", "favorable"}:
        detail += f" {risk_reward_text}。"
    return {
        "grade": TradeGrade.OBSERVE_ONLY.value,
        "detail": detail,
        "next_review": "建议等美元方向更清楚或下一轮复核后再决定。",
        "tone": AlertTone.NEUTRAL.value,
    }


def build_quote_structure_text(row: dict) -> str:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    point = float(row.get("point", 0.0) or 0.0)
    if bid <= 0 or ask <= 0 or ask < bid:
        return "暂无有效 Bid / Ask 报价"

    reference_price = float(row.get("latest_price", 0.0) or 0.0) or ((bid + ask) / 2.0)
    spread_price = max(ask - bid, 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    if spread_points <= 0 and point > 0:
        spread_points = spread_price / point
    spread_pct = (spread_price / reference_price * 100.0) if reference_price > 0 else 0.0
    return (
        f"Bid {format_quote_price(bid, point)} | "
        f"Ask {format_quote_price(ask, point)} | "
        f"点差 {spread_points:.0f}点 / {format_quote_price(spread_price, point)} ({spread_pct:.3f}%)"
    )


def build_quote_risk_note(symbol: str, row: dict) -> tuple[str, str]:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    point = float(row.get("point", 0.0) or 0.0)
    latest = float(row.get("latest_price", 0.0) or 0.0)
    status_code = str(row.get("quote_status_code", "") or "").strip().lower()
    if status_code in {
        QuoteStatus.INACTIVE.value,
        QuoteStatus.UNKNOWN_SYMBOL.value,
        QuoteStatus.NOT_SELECTED.value,
        QuoteStatus.ERROR.value,
    }:
        return AlertTone.NEUTRAL.value, "当前暂无完整报价，先确认 MT5 终端和品种报价状态。"
    if bid <= 0 or ask <= 0 or ask < bid:
        return AlertTone.NEUTRAL.value, "当前暂无完整报价，先确认 MT5 终端和品种报价状态。"

    spread_price = max(ask - bid, 0.0)
    spread_points = float(row.get("spread_points", 0.0) or 0.0)
    if spread_points <= 0 and point > 0:
        spread_points = spread_price / point
    spread_pct = (spread_price / latest * 100.0) if latest > 0 else 0.0
    thresholds = get_quote_risk_thresholds(symbol)
    spread_text = format_quote_price(spread_price, point)

    if spread_points >= thresholds["alert_points"] or spread_pct >= thresholds["alert_pct"]:
        return AlertTone.WARNING.value, f"点差明显放大（{spread_points:.0f}点 / {spread_text}），先等报价收敛再考虑追单。"
    if spread_points >= thresholds["warn_points"] or spread_pct >= thresholds["warn_pct"]:
        return AlertTone.ACCENT.value, f"点差偏宽（{spread_points:.0f}点 / {spread_text}），顺势单也先等点差回落再跟。"
    return AlertTone.SUCCESS.value, f"报价相对平稳（点差 {spread_points:.0f}点 / {spread_text}），适合继续观察关键位。"

def build_trade_grade(
    symbol: str,
    row: dict,
    tone: str,
    connected: bool,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
) -> dict[str, str]:
    symbol_key = str(symbol or "").strip().upper()
    family = _symbol_family(symbol_key)
    status_code = str(row.get("quote_status_code", "") or "").strip().lower()
    has_live_quote = bool(row.get("has_live_quote", False))

    if not connected:
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "MT5 终端当前未连通，先恢复报价链路，再讨论任何入场时机。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": AlertTone.WARNING.value,
            "source": "connection",
        }
    if not has_live_quote or status_code in {
        QuoteStatus.INACTIVE.value,
        QuoteStatus.UNKNOWN_SYMBOL.value,
        QuoteStatus.NOT_SELECTED.value,
        QuoteStatus.ERROR.value,
    }:
        diagnostic_text = str(row.get("quote_live_diagnostic_text", "") or "").strip()
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": (
                f"{symbol_key} 当前没有活跃报价，静态价格不适合做临场判断。"
                f"{(' ' + diagnostic_text) if diagnostic_text else ''}"
            ),
            "next_review": "等待下一个活跃时段或 MT5 报价恢复后再看。",
            "tone": AlertTone.WARNING.value,
            "source": "inactive",
        }

    post_event_continuation = _can_release_post_event_continuation(
        symbol_key,
        family,
        row,
        tone,
        event_risk_mode=event_risk_mode,
        event_context=event_context,
    )
    event_adjustment = _build_event_mode_adjustment(
        event_risk_mode,
        event_context=event_context,
        symbol=symbol_key,
        allow_post_event_continuation=post_event_continuation is not None,
    )
    if event_adjustment is not None:
        return event_adjustment

    if tone == AlertTone.WARNING.value:
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "点差已经明显放大，执行成本偏高，强行追单很容易被反向扫掉。",
            "next_review": "至少等点差回到正常区间后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "spread",
        }
    if tone == AlertTone.ACCENT.value:
        if family == "metal":
            detail = "报价还在，但点差已经偏宽，黄金/白银这时候容易出现假动作，先别急着伸手。"
            context_text = _intraday_context_text(row)
            multi_context_text = _multi_timeframe_context_text(row)
            if multi_context_text:
                detail = f"报价还在，但点差已经偏宽，而且{multi_context_text}，先别急着伸手。"
            elif context_text:
                detail = f"报价还在，但点差已经偏宽，而且{context_text}，先别急着伸手。"
            return {
                "grade": TradeGrade.OBSERVE_ONLY.value,
                "detail": detail,
                "next_review": "建议 10-15 分钟后复核一次点差和报价节奏。",
                "tone": AlertTone.ACCENT.value,
                "source": "spread",
            }
        detail = "外汇品种本来就更吃消息和美元方向，点差又在变宽，先等波动收敛再判断更稳。"
        context_text = _intraday_context_text(row)
        multi_context_text = _multi_timeframe_context_text(row)
        if multi_context_text:
            detail = f"外汇品种本来就更吃消息和美元方向，点差又在变宽，而且{multi_context_text}，先等波动收敛再判断更稳。"
        elif context_text:
            detail = f"外汇品种本来就更吃消息和美元方向，点差又在变宽，而且{context_text}，先等波动收敛再判断更稳。"
        return {
            "grade": TradeGrade.WAIT_EVENT.value,
            "detail": detail,
            "next_review": "先等 15 分钟后或消息波动落地后再复核。",
            "tone": AlertTone.ACCENT.value,
            "source": "spread",
        }
    result = _build_clean_quote_grade_with_context(symbol_key, family, row)
    if post_event_continuation is not None and str(result.get("grade", "") or "").strip() == TradeGrade.LIGHT_POSITION.value:
        detail_prefix = str(post_event_continuation.get("detail_prefix", "") or "").strip()
        if detail_prefix:
            result["detail"] = f"{detail_prefix} {result['detail']}".strip()
        next_review_prefix = str(post_event_continuation.get("next_review_prefix", "") or "").strip()
        if next_review_prefix:
            result["next_review"] = next_review_prefix
        result["event_override_kind"] = str(post_event_continuation.get("event_override_kind", "") or "").strip()
        result["event_override_note"] = str(post_event_continuation.get("event_override_note", "") or "").strip()
    result.setdefault("source", "structure")
    return result


def _build_portfolio_event_mode_adjustment(
    items: list[dict],
    connected: bool,
    event_risk_mode: str,
    event_context: dict | None = None,
) -> dict[str, str] | None:
    mode = str(event_risk_mode or "normal").strip().lower()
    if not connected:
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "MT5 连接尚未稳定，当前只能做状态检查，不适合做任何临场执行判断。",
            "next_review": "先恢复终端连接后立即复核。",
            "tone": AlertTone.WARNING.value,
            "source": "connection",
        }

    item_grades = list(items or [])
    if not item_grades:
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "观察池还没有有效快照，先等第一轮报价回来。",
            "next_review": "等到至少 1 个品种出现活跃报价后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "inactive",
        }

    if mode == "illiquid":
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": "当前被标记为流动性偏弱阶段，执行面整体不干净，先不建议主动出手。",
            "next_review": "等回到正常观察模式后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "event",
        }
    if mode not in {"pre_event", "post_event"}:
        return None

    context = dict(event_context or {})
    active_name = str(context.get("active_event_name", "") or "").strip()
    if active_name:
        targets = {
            str(item or "").strip().upper()
            for item in list(context.get("active_event_symbols", []) or [])
            if str(item or "").strip()
        }
        watched = {
            str(item.get("symbol", "") or "").strip().upper()
            for item in item_grades
            if str(item.get("symbol", "") or "").strip()
        }
        if targets and watched and not watched.issubset(targets):
            return None

    importance = _normalize_event_importance(str(context.get("active_event_importance", "") or "").strip())
    importance_text = str(context.get("active_event_importance_text", "") or "").strip() or {
        "high": "高影响",
        "medium": "中影响",
        "low": "低影响",
    }.get(importance, "中影响")

    if mode == "pre_event":
        if active_name and importance == "high":
            return {
                "grade": TradeGrade.NO_TRADE.value,
                "detail": f"{active_name} 属于{importance_text}，并且会直接影响当前观察池，先别抢数据前第一脚。",
                "next_review": "至少等事件公布后 15-20 分钟，并确认点差回到正常区间后再看。",
                "tone": AlertTone.WARNING.value,
                "source": "event",
            }
        if active_name and importance == "low":
            return {
                "grade": TradeGrade.OBSERVE_ONLY.value,
                "detail": f"{active_name} 虽然只是{importance_text}，但当前仍在事件前窗口，先观察更稳。",
                "next_review": "等事件落地后 5-10 分钟，再复核短线节奏和点差。",
                "tone": AlertTone.ACCENT.value,
                "source": "event",
            }
        return {
            "grade": TradeGrade.WAIT_EVENT.value,
            "detail": "当前被标记为事件前高敏阶段，整个观察池都应先防假突破和点差放大，不抢第一脚。",
            "next_review": "等事件落地后 10-15 分钟，并确认点差回到正常区间后再看。",
            "tone": AlertTone.WARNING.value,
            "source": "event",
        }

    if active_name and importance == "high":
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": f"{active_name} 刚落地且属于{importance_text}，当前观察池更适合先等重新定价完成。",
            "next_review": "至少等 15-20 分钟，并确认关键位与点差一起稳定后再复核。",
            "tone": AlertTone.WARNING.value,
            "source": "event",
        }
    return {
        "grade": TradeGrade.OBSERVE_ONLY.value,
        "detail": "当前被标记为事件落地观察阶段，方向正在重新定价，先观察再决定更稳。",
        "next_review": "建议 10-15 分钟后再复核。",
        "tone": AlertTone.ACCENT.value,
        "source": "event",
    }


def build_portfolio_trade_grade(
    items: list[dict],
    connected: bool,
    event_risk_mode: str = "normal",
    event_context: dict | None = None,
) -> dict[str, str]:
    portfolio_event_adjustment = _build_portfolio_event_mode_adjustment(
        items,
        connected,
        event_risk_mode=event_risk_mode,
        event_context=event_context,
    )
    if portfolio_event_adjustment is not None:
        return portfolio_event_adjustment

    item_grades = list(items or [])
    hard_blockers = [
        item
        for item in item_grades
        if str(item.get("trade_grade", "") or "").strip() == TradeGrade.NO_TRADE
        and str(item.get("trade_grade_source", item.get("source", "")) or "").strip() != "event"
    ]
    if hard_blockers:
        risk_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in hard_blockers
        ]
        return {
            "grade": TradeGrade.NO_TRADE.value,
            "detail": f"当前观察池里 {'、'.join(risk_symbols[:3])} 已经触发高风险条件，先把重点放在控制节奏，而不是抢第一脚。",
            "next_review": "等点差回落、报价恢复或休市结束后再看。",
            "tone": AlertTone.WARNING.value,
            "source": "risk",
        }

    if any(str(item.get("trade_grade", "") or "").strip() == TradeGrade.WAIT_EVENT for item in item_grades):
        event_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade", "") or "").strip() == TradeGrade.WAIT_EVENT
        ]
        return {
            "grade": TradeGrade.WAIT_EVENT.value,
            "detail": f"{'、'.join(event_symbols[:3])} 当前更受宏观和美元方向影响，先等波动落地比强行猜方向更划算。",
            "next_review": "优先在 15 分钟后或事件波动明显收敛后复核。",
            "tone": AlertTone.ACCENT.value,
            "source": "event",
        }

    if any(str(item.get("trade_grade", "") or "").strip() == TradeGrade.LIGHT_POSITION for item in item_grades):
        candidate_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        ]
        event_symbols = [
            str(item.get("symbol", "") or "").strip()
            for item in item_grades
            if str(item.get("trade_grade_source", item.get("source", "")) or "").strip() == "event"
        ]
        detail = f"{'、'.join(candidate_symbols[:3])} 当前执行面相对干净，可作为候选机会，但仍建议轻仓、短周期复核。"
        if event_symbols:
            detail += f" 同时 {'、'.join(event_symbols[:2])} 仍在事件窗口内，别被它们的节奏带着走。"
        return {
            "grade": TradeGrade.LIGHT_POSITION.value,
            "detail": detail,
            "next_review": "建议 10-15 分钟内复核关键位、点差和美元方向。",
            "tone": AlertTone.SUCCESS.value,
            "source": "setup",
        }

    event_blockers = [
        str(item.get("symbol", "") or "").strip()
        for item in item_grades
        if str(item.get("trade_grade_source", item.get("source", "")) or "").strip() == "event"
    ]
    if event_blockers:
        return {
            "grade": TradeGrade.OBSERVE_ONLY.value,
            "detail": f"{'、'.join(event_blockers[:3])} 当前主要受事件窗口约束，先观察、等节奏重新稳定更稳。",
            "next_review": "建议事件波动收敛后再结合关键位复核。",
            "tone": AlertTone.ACCENT.value,
            "source": "event",
        }

    observe_symbols = [
        str(item.get("symbol", "") or "").strip()
        for item in item_grades
        if str(item.get("trade_grade", "") or "").strip() == TradeGrade.OBSERVE_ONLY
    ]
    return {
        "grade": TradeGrade.OBSERVE_ONLY.value,
        "detail": f"{'、'.join(observe_symbols[:3]) or '当前观察池'} 还没有形成足够干净的执行环境，先观察更稳。",
        "next_review": "建议下一轮轮询后结合 MT5 图表再评估。",
        "tone": AlertTone.NEUTRAL.value,
        "source": "structure",
    }
