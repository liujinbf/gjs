from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


DEFAULT_TICK_SHOCK_THRESHOLDS = {
    "XAU": 0.50,
    "XAG": 0.08,
    "FX": 0.0008,
}


@dataclass
class _SymbolTickState:
    ticks: deque[tuple[float, float]] = field(default_factory=deque)
    shock_until_ts: float = 0.0
    shock_direction: str = ""
    shock_move: float = 0.0
    shock_threshold: float = 0.0


_TICK_STATE: dict[str, _SymbolTickState] = {}


def reset_tick_shock_state() -> None:
    _TICK_STATE.clear()


def normalize_tick_shock_thresholds(value: object | None = None) -> dict[str, float]:
    result = dict(DEFAULT_TICK_SHOCK_THRESHOLDS)
    if value is None:
        return result
    payload = value
    if isinstance(payload, str):
        import json

        text = payload.strip()
        if not text:
            return result
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return result
    if not isinstance(payload, dict):
        return result
    for key, raw_value in payload.items():
        clean_key = str(key or "").strip().upper()
        if not clean_key:
            continue
        try:
            result[clean_key] = max(0.0, float(raw_value))
        except (TypeError, ValueError):
            continue
    return result


def _symbol_family(symbol: str) -> str:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return "XAU"
    if symbol_key.startswith("XAG"):
        return "XAG"
    return "FX"


def _pick_price(bid: float = 0.0, ask: float = 0.0, last: float = 0.0) -> float:
    bid = float(bid or 0.0)
    ask = float(ask or 0.0)
    last = float(last or 0.0)
    if bid > 0 and ask > 0 and ask >= bid:
        return (bid + ask) / 2.0
    if last > 0:
        return last
    if ask > 0:
        return ask
    if bid > 0:
        return bid
    return 0.0


def build_empty_tick_shock_state() -> dict:
    return {
        "tick_shock_ready": False,
        "tick_shock_active": False,
        "tick_shock_direction": "none",
        "tick_shock_move": 0.0,
        "tick_shock_threshold": 0.0,
        "tick_shock_window_sec": 0.0,
        "tick_shock_cooldown_until_ts": 0.0,
        "tick_shock_text": "",
    }


def update_tick_shock_state(
    symbol: str,
    *,
    bid: float = 0.0,
    ask: float = 0.0,
    last: float = 0.0,
    now_ts: float | None = None,
    enabled: bool = True,
    window_sec: float = 5.0,
    cooldown_sec: float = 60.0,
    thresholds: object | None = None,
) -> dict:
    symbol_key = str(symbol or "").strip().upper()
    current_ts = float(now_ts if now_ts is not None else time.time())
    if not symbol_key or not bool(enabled):
        return build_empty_tick_shock_state()

    price = _pick_price(bid=bid, ask=ask, last=last)
    if price <= 0:
        return build_empty_tick_shock_state()

    window = max(0.5, float(window_sec or 5.0))
    cooldown = max(0.0, float(cooldown_sec or 0.0))
    threshold_map = normalize_tick_shock_thresholds(thresholds)
    family = _symbol_family(symbol_key)
    threshold = float(threshold_map.get(symbol_key, threshold_map.get(family, threshold_map.get("FX", 0.0))) or 0.0)
    state = _TICK_STATE.setdefault(symbol_key, _SymbolTickState())
    state.ticks.append((current_ts, price))
    cutoff = current_ts - window
    while state.ticks and state.ticks[0][0] < cutoff:
        state.ticks.popleft()

    reference_price = state.ticks[0][1] if state.ticks else price
    move = price - reference_price
    triggered = bool(threshold > 0 and abs(move) >= threshold and len(state.ticks) >= 2)
    if triggered:
        state.shock_until_ts = current_ts + cooldown
        state.shock_direction = "bullish" if move > 0 else "bearish"
        state.shock_move = move
        state.shock_threshold = threshold

    active = bool(state.shock_until_ts > current_ts)
    if not active:
        state.shock_direction = ""
        state.shock_move = 0.0
        state.shock_threshold = threshold

    direction = state.shock_direction or "none"
    direction_text = "急拉" if direction == "bullish" else ("急砸" if direction == "bearish" else "平稳")
    active_text = (
        f"{symbol_key} Tick 异动冷却中：{window:.1f}秒窗口内{direction_text} "
        f"{state.shock_move:+.4f}，阈值 {threshold:.4f}，等待短周期收线确认。"
        if active
        else ""
    )
    return {
        "tick_shock_ready": True,
        "tick_shock_active": active,
        "tick_shock_direction": direction,
        "tick_shock_move": round(float(state.shock_move if active else move), 6),
        "tick_shock_threshold": threshold,
        "tick_shock_window_sec": window,
        "tick_shock_cooldown_until_ts": float(state.shock_until_ts if active else 0.0),
        "tick_shock_text": active_text,
    }
