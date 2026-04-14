from __future__ import annotations

from signal_protocol import normalize_signal_meta, validate_signal_meta


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


def _is_actionable_item(item: dict) -> bool:
    if not bool(item.get("has_live_quote", False)):
        return False
    if _normalize_text(item.get("trade_grade", "")) != "可轻仓试仓":
        return False
    if _normalize_text(item.get("trade_grade_source", "")) not in {"structure", "setup"}:
        return False
    if not bool(item.get("risk_reward_ready", False)):
        return False
    if float(item.get("risk_reward_ratio", 0.0) or 0.0) < 1.6:
        return False
    if _normalize_text(item.get("signal_side", "")).lower() not in {"long", "short"}:
        return False
    if min(
        float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        float(item.get("risk_reward_target_price", 0.0) or 0.0),
    ) <= 0:
        return False
    entry_zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
    entry_zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
    if entry_zone_low > 0 and entry_zone_high > 0:
        price = _pick_entry_price(item, _normalize_text(item.get("signal_side", "")).lower())
        low, high = sorted((entry_zone_low, entry_zone_high))
        if price < low or price > high:
            return False
    return True


def build_rule_sim_signal(snapshot: dict) -> dict | None:
    candidates = [
        dict(item or {})
        for item in list((snapshot or {}).get("items", []) or [])
        if _is_actionable_item(item)
    ]
    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            float(item.get("risk_reward_ratio", 0.0) or 0.0),
            float(item.get("latest_price", 0.0) or 0.0),
        ),
        reverse=True,
    )
    best = candidates[0]
    payload = normalize_signal_meta(
        {
            "symbol": _normalize_text(best.get("symbol", "")).upper(),
            "action": _normalize_text(best.get("signal_side", "")).lower(),
            "price": _pick_entry_price(best, _normalize_text(best.get("signal_side", "")).lower()),
            "sl": float(best.get("risk_reward_stop_price", 0.0) or 0.0),
            "tp": float(best.get("risk_reward_target_price", 0.0) or 0.0),
        }
    )
    valid, _reason = validate_signal_meta(payload)
    if not valid:
        return None
    return payload
