from __future__ import annotations

from signal_protocol import normalize_signal_meta, validate_signal_meta

BASE_RULE_SIM_RR = 1.6
MODEL_RELAXED_RR = 1.3
MODEL_CONFIRM_PROBABILITY = 0.68


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _pick_entry_price(item: dict, action: str) -> float:
    bid = float(item.get("bid", 0.0) or 0.0)
    ask = float(item.get("ask", 0.0) or 0.0)
    latest = float(item.get("latest_price", 0.0) or 0.0)
    if action == "long":
        return ask if ask > 0 else latest
    if action == "short":
        return bid if bid > 0 else latest
    return latest


def _resolve_signal_side(item: dict) -> str:
    explicit = _normalize_text(item.get("signal_side", "")).lower()
    if explicit in {"long", "short"}:
        return explicit

    for key in ("risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = _normalize_text(item.get(key, "")).lower()
        if value == "bullish":
            return "long"
        if value == "bearish":
            return "short"

    price = float(item.get("latest_price", 0.0) or 0.0)
    stop = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
    target = float(item.get("risk_reward_target_price", 0.0) or 0.0)
    if min(price, stop, target) > 0:
        if stop < price < target:
            return "long"
        if target < price < stop:
            return "short"
    return "neutral"


def _is_price_near_entry_zone(item: dict, action: str) -> bool:
    entry_zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    entry_zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if entry_zone_low <= 0 or entry_zone_high <= 0:
        return True

    price = _pick_entry_price(item, action)
    low, high = sorted((entry_zone_low, entry_zone_high))
    span = max(high - low, 0.0)
    atr = max(
        float(item.get("atr14", 0.0) or 0.0),
        float(item.get("risk_reward_atr", 0.0) or 0.0),
    )
    point = max(float(item.get("point", 0.0) or 0.0), 0.0)
    padding = max(span * 0.35, atr * 0.15, point * 20)
    return (low - padding) <= price <= (high + padding)


def _evaluate_item_for_sim(item: dict) -> tuple[bool, str, str]:
    if not bool(item.get("has_live_quote", False)):
        return False, "当前不是实时报价。", "neutral"
    if _normalize_text(item.get("trade_grade", "")) != "可轻仓试仓":
        return False, "当前还没到可轻仓试仓级别。", "neutral"
    if _normalize_text(item.get("trade_grade_source", "")) not in {"structure", "setup"}:
        return False, "当前候选并非结构型入场信号。", "neutral"
    if not bool(item.get("risk_reward_ready", False)):
        return False, "盈亏比尚未准备好。", "neutral"

    rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
    model_ready = bool(item.get("model_ready", False))
    model_probability = float(item.get("model_win_probability", 0.0) or 0.0)
    if rr < BASE_RULE_SIM_RR:
        if not (rr >= MODEL_RELAXED_RR and model_ready and model_probability >= MODEL_CONFIRM_PROBABILITY):
            return False, "盈亏比还不够健康，先继续观察。", "neutral"

    action = _resolve_signal_side(item)
    if action not in {"long", "short"}:
        return False, "方向还不够清晰，暂不自动试仓。", "neutral"

    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return False, "止损或目标价仍不完整。", action
    if not _is_price_near_entry_zone(item, action):
        return False, "价格尚未回到可执行观察区间附近，继续等回踩。", action

    meta = normalize_signal_meta(
        {
            "symbol": _normalize_text(item.get("symbol", "")).upper(),
            "action": action,
            "price": _pick_entry_price(item, action),
            "sl": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
            "tp": float(item.get("risk_reward_target_price", 0.0) or 0.0),
        }
    )
    valid, reason = validate_signal_meta(meta)
    if not valid:
        return False, reason, action
    return True, "", action


def build_rule_sim_signal_decision(snapshot: dict) -> tuple[dict | None, str]:
    actionable_candidates: list[tuple[float, dict]] = []
    blocked_reasons: list[str] = []

    for item in [dict(item or {}) for item in list((snapshot or {}).get("items", []) or [])]:
        eligible, reason, action = _evaluate_item_for_sim(item)
        symbol = _normalize_text(item.get("symbol", "")).upper()
        if not symbol:
            continue
        if not eligible:
            if bool(item.get("has_live_quote", False)) and _normalize_text(item.get("trade_grade", "")):
                blocked_reasons.append(f"{symbol}：{reason}")
            continue

        score = float(item.get("risk_reward_ratio", 0.0) or 0.0)
        if bool(item.get("model_ready", False)):
            score += float(item.get("model_win_probability", 0.0) or 0.0)
        payload = normalize_signal_meta(
            {
                "symbol": symbol,
                "action": action,
                "price": _pick_entry_price(item, action),
                "sl": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
                "tp": float(item.get("risk_reward_target_price", 0.0) or 0.0),
            }
        )
        payload["atr14"] = float(item.get("atr14", 0.0) or 0.0)
        payload["atr14_h4"] = float(item.get("atr14_h4", 0.0) or 0.0)
        payload["risk_reward_atr"] = float(item.get("risk_reward_atr", 0.0) or 0.0)
        payload["tp2"] = float(item.get("risk_reward_target_price_2", 0.0) or 0.0)
        actionable_candidates.append((score, payload))

    if actionable_candidates:
        actionable_candidates.sort(key=lambda item_: item_[0], reverse=True)
        return actionable_candidates[0][1], ""
    return None, (blocked_reasons[0] if blocked_reasons else "")


def build_rule_sim_signal(snapshot: dict) -> dict | None:
    signal, _reason = build_rule_sim_signal_decision(snapshot)
    return signal
