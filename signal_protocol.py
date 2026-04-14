"""
AI 结构化信号协议：统一版本、归一化与校验规则。
"""
from __future__ import annotations

SIGNAL_SCHEMA_VERSION = "signal-meta-v1"
VALID_ACTIONS = {"long", "short", "neutral"}


def build_empty_signal_meta(symbol: str = "--") -> dict:
    return {
        "symbol": str(symbol or "--").strip().upper() or "--",
        "action": "neutral",
        "price": 0.0,
        "sl": 0.0,
        "tp": 0.0,
    }


def normalize_signal_meta(meta: dict | None) -> dict:
    payload = dict(meta or {})
    symbol = str(payload.get("symbol", "--") or "--").strip().upper() or "--"
    action = str(payload.get("action", "neutral") or "neutral").strip().lower()
    if action not in VALID_ACTIONS:
        action = "neutral"
    price = float(payload.get("price", 0.0) or 0.0)
    sl = float(payload.get("sl", 0.0) or 0.0)
    tp = float(payload.get("tp", 0.0) or 0.0)
    if action == "neutral":
        return build_empty_signal_meta(symbol=symbol)
    return {
        "symbol": symbol,
        "action": action,
        "price": price,
        "sl": sl,
        "tp": tp,
    }


def validate_signal_meta(meta: dict | None) -> tuple[bool, str]:
    payload = normalize_signal_meta(meta)
    action = str(payload.get("action", "neutral") or "neutral").strip().lower()
    symbol = str(payload.get("symbol", "") or "").strip().upper()
    price = float(payload.get("price", 0.0) or 0.0)
    sl = float(payload.get("sl", 0.0) or 0.0)
    tp = float(payload.get("tp", 0.0) or 0.0)

    if action == "neutral":
        return True, "观望信号"
    if not symbol or symbol == "--":
        return False, "缺少有效品种代码"
    if min(price, sl, tp) <= 0:
        return False, "缺少有效的入场价/止损价/目标价"
    if action == "long":
        if not (sl < price < tp):
            return False, "做多信号要求 止损 < 入场 < 目标"
        return True, "做多信号结构有效"
    if action == "short":
        if not (tp < price < sl):
            return False, "做空信号要求 目标 < 入场 < 止损"
        return True, "做空信号结构有效"
    return False, "未知动作类型"
