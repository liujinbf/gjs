from __future__ import annotations

from signal_enums import SignalSide


_SIGNAL_SIDE_TEXT = {
    SignalSide.LONG.value: "【↑ 多头参考】",
    SignalSide.SHORT.value: "【↓ 空头参考】",
    SignalSide.NEUTRAL.value: "",
}


def _normalize_text(value: object) -> str:
    return str(value or "").strip().lower()


def build_signal_side_text(signal_side: str) -> str:
    side = _normalize_text(signal_side)
    return _SIGNAL_SIDE_TEXT.get(side, "")


def derive_signal_side_meta(payload: dict | None) -> dict:
    """根据结构字段推断方向，并保留方向依据供学习系统使用。"""
    source = dict(payload or {})

    explicit_side = _normalize_text(source.get("signal_side", ""))
    if explicit_side in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return {
            "signal_side": explicit_side,
            "signal_side_text": build_signal_side_text(explicit_side),
            "signal_side_basis": "显式信号",
            "signal_side_reason": f"快照已显式给出方向 {explicit_side}。",
            "signal_side_long_votes": int(explicit_side == SignalSide.LONG.value),
            "signal_side_short_votes": int(explicit_side == SignalSide.SHORT.value),
        }

    long_votes: list[str] = []
    short_votes: list[str] = []

    directional_fields = (
        ("intraday_bias", "日内偏向"),
        ("multi_timeframe_bias", "多周期偏向"),
        ("breakout_direction", "突破方向"),
    )
    for field, label in directional_fields:
        value = _normalize_text(source.get(field, ""))
        if value == "bullish":
            long_votes.append(label)
        elif value == "bearish":
            short_votes.append(label)

    confirmation_fields = (
        ("breakout_state", "confirmed_above", "突破确认上破", "confirmed_below", "突破确认下破"),
        ("retest_state", "confirmed_support", "回踩确认支撑", "confirmed_resistance", "反抽确认压制"),
    )
    for field, long_value, long_label, short_value, short_label in confirmation_fields:
        value = _normalize_text(source.get(field, ""))
        if value == long_value:
            long_votes.append(long_label)
        elif value == short_value:
            short_votes.append(short_label)

    if len(long_votes) > len(short_votes):
        reason = "、".join(long_votes[:3])
        return {
            "signal_side": SignalSide.LONG.value,
            "signal_side_text": build_signal_side_text(SignalSide.LONG.value),
            "signal_side_basis": "结构投票",
            "signal_side_reason": f"偏多依据：{reason}。",
            "signal_side_long_votes": len(long_votes),
            "signal_side_short_votes": len(short_votes),
        }
    if len(short_votes) > len(long_votes):
        reason = "、".join(short_votes[:3])
        return {
            "signal_side": SignalSide.SHORT.value,
            "signal_side_text": build_signal_side_text(SignalSide.SHORT.value),
            "signal_side_basis": "结构投票",
            "signal_side_reason": f"偏空依据：{reason}。",
            "signal_side_long_votes": len(long_votes),
            "signal_side_short_votes": len(short_votes),
        }

    risk_reward_direction = _normalize_text(source.get("risk_reward_direction", ""))
    if risk_reward_direction == "bullish":
        return {
            "signal_side": SignalSide.LONG.value,
            "signal_side_text": build_signal_side_text(SignalSide.LONG.value),
            "signal_side_basis": "盈亏比方向兜底",
            "signal_side_reason": "结构投票平手，使用盈亏比方向偏多兜底。",
            "signal_side_long_votes": len(long_votes),
            "signal_side_short_votes": len(short_votes),
        }
    if risk_reward_direction == "bearish":
        return {
            "signal_side": SignalSide.SHORT.value,
            "signal_side_text": build_signal_side_text(SignalSide.SHORT.value),
            "signal_side_basis": "盈亏比方向兜底",
            "signal_side_reason": "结构投票平手，使用盈亏比方向偏空兜底。",
            "signal_side_long_votes": len(long_votes),
            "signal_side_short_votes": len(short_votes),
        }

    return {
        "signal_side": SignalSide.NEUTRAL.value,
        "signal_side_text": build_signal_side_text(SignalSide.NEUTRAL.value),
        "signal_side_basis": "方向不足",
        "signal_side_reason": "当前结构字段仍不足以形成明确方向。",
        "signal_side_long_votes": len(long_votes),
        "signal_side_short_votes": len(short_votes),
    }
