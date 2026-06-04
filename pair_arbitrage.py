from __future__ import annotations

from datetime import datetime

_AU_AG_PAIR_ENTRY_ZSCORE = 2.0
_AU_AG_PAIR_TARGET_NOTIONAL = 4500.0


def _text(value: object) -> str:
    return str(value or "").strip()


def _float(value: object) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _pick_item(snapshot: dict, symbol: str) -> dict:
    target = _text(symbol).upper()
    for item in list((snapshot or {}).get("items", []) or []):
        payload = dict(item or {})
        if _text(payload.get("symbol", "")).upper() == target:
            return payload
    return {}


def _entry_price(item: dict, action: str) -> float:
    bid = _float(item.get("bid"))
    ask = _float(item.get("ask"))
    latest = _float(item.get("latest_price"))
    if action == "long":
        return ask if ask > 0 else latest
    if action == "short":
        return bid if bid > 0 else latest
    return latest


def _emergency_distance(symbol: str, item: dict, price: float) -> float:
    atr = max(_float(item.get("atr14")), _float(item.get("risk_reward_atr")), _float(item.get("atr14_h4")))
    if atr > 0:
        return atr * 2.0
    symbol_key = _text(symbol).upper()
    if symbol_key.startswith("XAU"):
        return max(price * 0.0045, 1.0)
    if symbol_key.startswith("XAG"):
        return max(price * 0.0120, 0.05)
    return max(price * 0.0030, 0.0005)


def _contract_size(symbol: str) -> float:
    symbol_key = _text(symbol).upper()
    if symbol_key.startswith("XAU"):
        return 100.0
    if symbol_key.startswith("XAG"):
        return 5000.0
    return 100000.0


def _item_contract_size(symbol: str, item: dict) -> float:
    configured = _float(item.get("contract_size")) or _float(item.get("trade_contract_size"))
    return configured if configured > 0 else _contract_size(symbol)


def _volume_meta(item: dict) -> tuple[float, float, int]:
    step = _float(item.get("volume_step")) or 0.01
    minimum = _float(item.get("volume_min")) or step
    step = max(step, 0.01)
    minimum = max(minimum, step)
    decimals = 0
    text = f"{step:.10f}".rstrip("0")
    if "." in text:
        decimals = len(text.split(".", 1)[1])
    return step, minimum, decimals


def _align_lots(raw_lots: float, item: dict) -> float:
    import math

    step, minimum, decimals = _volume_meta(item)
    lots = math.floor(max(raw_lots, 0.0) / step) * step
    lots = round(lots, decimals)
    return max(minimum, lots)


def _nearest_lots_for_notional(symbol: str, price: float, target_notional: float, item: dict) -> float:
    import math

    step, minimum, decimals = _volume_meta(item)
    raw_lots = target_notional / max(price * _item_contract_size(symbol, item), 1e-8)
    floor_lots = math.floor(max(raw_lots, 0.0) / step) * step
    ceil_lots = math.ceil(max(raw_lots, 0.0) / step) * step
    candidates = {minimum, round(max(minimum, floor_lots), decimals), round(max(minimum, ceil_lots), decimals)}
    return min(
        candidates,
        key=lambda lots: abs(_notional_value(symbol, price, lots, item) - target_notional),
    )


def _notional_value(symbol: str, price: float, lots: float, item: dict | None = None) -> float:
    return max(0.0, float(price or 0.0) * _item_contract_size(symbol, item or {}) * max(0.0, float(lots or 0.0)))


def _spread_cost_pct(item: dict, price: float) -> float:
    spread_points = _float(item.get("spread_points"))
    point = _float(item.get("point"))
    if spread_points <= 0 or point <= 0 or price <= 0:
        return 0.0
    return (spread_points * point) / price


def _spread_guard_reason(symbol: str, item: dict, price: float) -> str:
    symbol_key = _text(symbol).upper()
    spread_points = _float(item.get("spread_points"))
    spread_pct = _spread_cost_pct(item, price)
    if symbol_key.startswith("XAG"):
        if spread_points > 120:
            return f"XAGUSD 点差 {spread_points:.0f} 点过宽，白银点差陷阱风险高，暂不执行配对套利。"
        if spread_pct > 0.0012:
            return f"XAGUSD 点差成本约 {spread_pct:.3%}，超过配对套利容忍阈值，暂不执行。"
    if symbol_key.startswith("XAU") and spread_points > 80:
        return f"XAUUSD 点差 {spread_points:.0f} 点过宽，暂不执行配对套利。"
    return ""


def _build_beta_matched_lots(
    leg_specs: list[tuple[str, str, dict, float]],
    target_notional: float = _AU_AG_PAIR_TARGET_NOTIONAL,
) -> dict[str, dict]:
    prelim: dict[str, dict] = {}
    notionals: list[float] = []
    for symbol, _action, item, price in leg_specs:
        lots = _nearest_lots_for_notional(symbol, price, target_notional, item)
        notional = _notional_value(symbol, price, lots, item)
        prelim[symbol] = {"lots": lots, "notional": notional}
        notionals.append(notional)
    if not notionals:
        return prelim
    matched_notional = min(value for value in notionals if value > 0) if any(value > 0 for value in notionals) else 0.0
    if matched_notional <= 0:
        return prelim
    result: dict[str, dict] = {}
    for symbol, _action, item, price in leg_specs:
        lots = _nearest_lots_for_notional(symbol, price, matched_notional, item)
        result[symbol] = {
            "lots": lots,
            "notional": round(_notional_value(symbol, price, lots, item), 2),
            "target_notional": round(matched_notional, 2),
        }
    return result


def _build_leg_meta(
    *,
    symbol: str,
    action: str,
    item: dict,
    pair_group_id: str,
    pair_signal: str,
    ratio: float,
    zscore: float,
    exit_zscore: float,
    fixed_lots: float,
    leg_notional: float,
    target_notional: float,
) -> dict:
    price = _entry_price(item, action)
    distance = _emergency_distance(symbol, item, price)
    if action == "long":
        sl = price - distance
        tp = price + distance * 1.5
    else:
        sl = price + distance
        tp = price - distance * 1.5
    return {
        "symbol": symbol,
        "action": action,
        "price": round(price, 5),
        "sl": round(sl, 5),
        "tp": round(tp, 5),
        "source_kind": "pair_arbitrage",
        "trade_grade_source": "pair_arbitrage",
        "strategy_family": "au_ag_pair",
        "setup_kind": "au_ag_zscore",
        "execution_profile": "exploratory",
        "pair_group_id": pair_group_id,
        "pair_signal": pair_signal,
        "pair_exit_zscore": float(exit_zscore),
        "au_ag_ratio": float(ratio),
        "au_ag_zscore": float(zscore),
        "fixed_lots": float(fixed_lots),
        "pair_leg_notional": float(leg_notional),
        "pair_target_notional": float(target_notional),
        "atr14": _float(item.get("atr14")),
        "risk_reward_atr": _float(item.get("risk_reward_atr")),
        "volume_step": _float(item.get("volume_step")),
        "volume_min": _float(item.get("volume_min")),
    }


def build_au_ag_pair_signals(snapshot: dict) -> tuple[list[dict], str]:
    context = dict((snapshot or {}).get("correlation_context", {}) or {})
    if not bool(context.get("au_ag_ready", False)):
        return [], ""
    if not bool(context.get("au_ag_zscore_ready", False)):
        return [], "Au/Ag 历史样本不足，暂不执行配对套利。"

    zscore = _float(context.get("au_ag_zscore"))
    if abs(zscore) < _AU_AG_PAIR_ENTRY_ZSCORE:
        return [], f"Au/Ag Z-Score={zscore:+.2f} 未达到入场阈值 ±{_AU_AG_PAIR_ENTRY_ZSCORE:.1f}，禁止在均值附近反复交易。"

    pair_legs = list(context.get("au_ag_pair_legs", []) or [])
    if len(pair_legs) != 2:
        return [], "Au/Ag 配对腿信息不完整，暂不执行。"

    ratio = _float(context.get("au_ag_ratio"))
    exit_zscore = abs(_float(context.get("au_ag_exit_zscore")) or 0.35)
    pair_signal = _text(context.get("au_ag_signal"))
    ratio_momentum = _float(context.get("au_ag_ratio_momentum"))
    if pair_signal == "long_xag_short_xau" and ratio_momentum > 0.01:
        return [], f"Au/Ag 金银比仍在扩大（动量 {ratio_momentum:+.4f}），先等偏离停止上冲再做均值回归。"
    if pair_signal == "long_xau_short_xag" and ratio_momentum < -0.01:
        return [], f"Au/Ag 金银比仍在下探（动量 {ratio_momentum:+.4f}），先等偏离停止下探再做均值回归。"
    pair_group_id = f"au_ag_{datetime.now().strftime('%Y%m%d%H%M%S')}_{abs(hash((ratio, zscore, pair_signal))) % 100000:05d}"

    leg_specs: list[tuple[str, str, dict, float]] = []
    for leg in pair_legs:
        symbol = _text((leg or {}).get("symbol", "")).upper()
        action = _text((leg or {}).get("action", "")).lower()
        if symbol not in {"XAUUSD", "XAGUSD"} or action not in {"long", "short"}:
            return [], "Au/Ag 配对腿方向非法，暂不执行。"
        item = _pick_item(snapshot, symbol)
        if not item or not bool(item.get("has_live_quote", False)):
            return [], f"{symbol} 没有实时报价，暂不执行配对套利。"
        price = _entry_price(item, action)
        spread_reason = _spread_guard_reason(symbol, item, price)
        if spread_reason:
            return [], spread_reason
        leg_specs.append((symbol, action, item, price))

    matched_lots = _build_beta_matched_lots(leg_specs)
    signals: list[dict] = []
    for symbol, action, item, _price in leg_specs:
        sizing = dict(matched_lots.get(symbol, {}) or {})
        signal = _build_leg_meta(
            symbol=symbol,
            action=action,
            item=item,
            pair_group_id=pair_group_id,
            pair_signal=pair_signal,
            ratio=ratio,
            zscore=zscore,
            exit_zscore=exit_zscore,
            fixed_lots=float(sizing.get("lots", 0.0) or 0.0),
            leg_notional=float(sizing.get("notional", 0.0) or 0.0),
            target_notional=float(sizing.get("target_notional", 0.0) or 0.0),
        )
        if min(_float(signal.get("price")), _float(signal.get("sl")), _float(signal.get("tp"))) <= 0:
            return [], f"{symbol} 配对腿点位不完整，暂不执行。"
        signals.append(signal)

    return signals, ""
