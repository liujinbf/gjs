from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from signal_enums import QuoteStatus


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in ("", None):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value).strip()
    except Exception:  # noqa: BLE001
        return default


def _normalize_explicit_quote_status_code(value: Any) -> str:
    text = _safe_text(value).lower()
    if not text:
        return ""
    valid_values = {status.value for status in QuoteStatus}
    return text if text in valid_values else ""


def _infer_quote_status_code(source: dict[str, Any]) -> str:
    explicit_code = _normalize_explicit_quote_status_code(source.get("quote_status_code", ""))
    if explicit_code:
        return explicit_code

    has_live_quote = bool(source.get("has_live_quote", False))
    if has_live_quote:
        return QuoteStatus.LIVE.value

    status_text = _safe_text(source.get("status", "")).lower()
    if not status_text:
        return QuoteStatus.ERROR.value
    if "未识别" in status_text:
        return QuoteStatus.UNKNOWN_SYMBOL.value
    if "未加入" in status_text:
        return QuoteStatus.NOT_SELECTED.value
    if "异常" in status_text:
        return QuoteStatus.ERROR.value
    if "休市" in status_text or "非活跃" in status_text or "暂无" in status_text:
        return QuoteStatus.INACTIVE.value
    return QuoteStatus.INACTIVE.value if not has_live_quote else QuoteStatus.LIVE.value


@dataclass(slots=True)
class QuoteRow:
    """报价主链的轻量模型。

    说明：
    - 先只收口最核心的报价字段，降低 mt5_gateway -> monitor_engine 之间的隐式契约风险。
    - 其他分析字段继续放在 extra 中，保持渐进式迁移，避免一次性重写全链路。
    """

    symbol: str
    latest_price: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread_points: float = 0.0
    point: float = 0.0
    tick_time: int = 0
    status: str = ""
    quote_status_code: str = QuoteStatus.ERROR.value
    has_live_quote: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | "QuoteRow" | None) -> "QuoteRow":
        if isinstance(payload, cls):
            return payload
        source = dict(payload or {})
        known_keys = {
            "symbol",
            "latest_price",
            "bid",
            "ask",
            "spread_points",
            "point",
            "tick_time",
            "status",
            "quote_status_code",
            "has_live_quote",
        }
        return cls(
            symbol=_safe_text(source.get("symbol", "")).upper(),
            latest_price=_safe_float(source.get("latest_price", 0.0)),
            bid=_safe_float(source.get("bid", 0.0)),
            ask=_safe_float(source.get("ask", 0.0)),
            spread_points=_safe_float(source.get("spread_points", 0.0)),
            point=_safe_float(source.get("point", 0.0)),
            tick_time=_safe_int(source.get("tick_time", 0)),
            status=_safe_text(source.get("status", "")),
            quote_status_code=_infer_quote_status_code(source),
            has_live_quote=bool(source.get("has_live_quote", False)),
            extra={key: value for key, value in source.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "latest_price": self.latest_price,
            "bid": self.bid,
            "ask": self.ask,
            "spread_points": self.spread_points,
            "point": self.point,
            "tick_time": self.tick_time,
            "status": self.status,
            "quote_status_code": self.quote_status_code,
            "has_live_quote": self.has_live_quote,
        }
        payload.update(dict(self.extra))
        return payload


@dataclass(slots=True)
class SnapshotItem:
    """监控快照单项的轻量模型。

    说明：
    - 先收口最常跨模块流转的字段，减少 monitor_engine 里巨型 dict 的核心噪音。
    - 技术分析与解释性长尾字段先放在 extra，后续再逐步迁移。
    """

    symbol: str
    latest_price: float = 0.0
    spread_points: float = 0.0
    point: float = 0.0
    has_live_quote: bool = False
    bid: float = 0.0
    ask: float = 0.0
    tick_time: int = 0
    latest_text: str = "--"
    quote_text: str = ""
    status_text: str = ""
    quote_status_code: str = QuoteStatus.ERROR.value
    execution_note: str = ""
    trade_grade: str = ""
    trade_grade_detail: str = ""
    trade_next_review: str = ""
    trade_grade_source: str = ""
    event_importance_text: str = ""
    event_note: str = ""
    macro_focus: str = ""
    alert_state_text: str = ""
    alert_state_detail: str = ""
    alert_state_tone: str = ""
    alert_state_rank: int = 0
    regime_tag: str = ""
    regime_text: str = ""
    regime_reason: str = ""
    regime_rank: int = 0
    tone: str = ""
    signal_side: str = ""
    signal_side_text: str = ""
    intraday_bias: str = ""
    intraday_bias_text: str = ""
    multi_timeframe_alignment: str = ""
    multi_timeframe_alignment_text: str = ""
    multi_timeframe_bias: str = ""
    multi_timeframe_bias_text: str = ""
    intraday_context_text: str = ""
    multi_timeframe_context_text: str = ""
    key_level_context_text: str = ""
    key_level_state: str = ""
    key_level_state_text: str = ""
    breakout_direction: str = ""
    breakout_context_text: str = ""
    breakout_state: str = ""
    breakout_state_text: str = ""
    retest_context_text: str = ""
    retest_state: str = ""
    retest_state_text: str = ""
    risk_reward_ready: bool = False
    risk_reward_context_text: str = ""
    risk_reward_state: str = ""
    risk_reward_state_text: str = ""
    risk_reward_ratio: float = 0.0
    risk_reward_direction: str = ""
    risk_reward_stop_price: float = 0.0
    risk_reward_target_price: float = 0.0
    risk_reward_target_price_2: float = 0.0
    risk_reward_entry_zone_low: float = 0.0
    risk_reward_entry_zone_high: float = 0.0
    risk_reward_entry_zone_text: str = ""
    risk_reward_position_text: str = ""
    risk_reward_invalidation_text: str = ""
    risk_reward_atr: float = 0.0
    atr14: float = 0.0
    atr14_h4: float = 0.0
    event_mode_text: str = ""
    event_active_name: str = ""
    event_active_time_text: str = ""
    event_scope_text: str = ""
    event_applies: bool = False
    tech_summary: str = ""
    tech_summary_h4: str = ""
    h4_context_text: str = ""
    model_ready: bool = False
    model_win_probability: float = 0.0
    model_confidence_text: str = ""
    model_note: str = ""
    snapshot_id: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | "SnapshotItem" | None) -> "SnapshotItem":
        if isinstance(payload, cls):
            return payload
        source = dict(payload or {})
        known_keys = {
            "symbol",
            "latest_price",
            "spread_points",
            "point",
            "has_live_quote",
            "bid",
            "ask",
            "tick_time",
            "latest_text",
            "quote_text",
            "status_text",
            "quote_status_code",
            "execution_note",
            "trade_grade",
            "trade_grade_detail",
            "trade_next_review",
            "trade_grade_source",
            "event_importance_text",
            "event_note",
            "macro_focus",
            "alert_state_text",
            "alert_state_detail",
            "alert_state_tone",
            "alert_state_rank",
            "regime_tag",
            "regime_text",
            "regime_reason",
            "regime_rank",
            "tone",
            "signal_side",
            "signal_side_text",
            "intraday_bias",
            "intraday_bias_text",
            "multi_timeframe_alignment",
            "multi_timeframe_alignment_text",
            "multi_timeframe_bias",
            "multi_timeframe_bias_text",
            "intraday_context_text",
            "multi_timeframe_context_text",
            "key_level_context_text",
            "key_level_state",
            "key_level_state_text",
            "breakout_direction",
            "breakout_context_text",
            "breakout_state",
            "breakout_state_text",
            "retest_context_text",
            "retest_state",
            "retest_state_text",
            "risk_reward_ready",
            "risk_reward_context_text",
            "risk_reward_state",
            "risk_reward_state_text",
            "risk_reward_ratio",
            "risk_reward_direction",
            "risk_reward_stop_price",
            "risk_reward_target_price",
            "risk_reward_target_price_2",
            "risk_reward_entry_zone_low",
            "risk_reward_entry_zone_high",
            "risk_reward_entry_zone_text",
            "risk_reward_position_text",
            "risk_reward_invalidation_text",
            "risk_reward_atr",
            "atr14",
            "atr14_h4",
            "event_mode_text",
            "event_active_name",
            "event_active_time_text",
            "event_scope_text",
            "event_applies",
            "tech_summary",
            "tech_summary_h4",
            "h4_context_text",
            "model_ready",
            "model_win_probability",
            "model_confidence_text",
            "model_note",
            "snapshot_id",
        }
        quote_status_source = {
            "quote_status_code": source.get("quote_status_code", ""),
            "has_live_quote": source.get("has_live_quote", False),
            "status": source.get("status_text", source.get("status", "")),
        }
        return cls(
            symbol=_safe_text(source.get("symbol", "")).upper(),
            latest_price=_safe_float(source.get("latest_price", 0.0)),
            spread_points=_safe_float(source.get("spread_points", 0.0)),
            point=_safe_float(source.get("point", 0.0)),
            has_live_quote=bool(source.get("has_live_quote", False)),
            bid=_safe_float(source.get("bid", 0.0)),
            ask=_safe_float(source.get("ask", 0.0)),
            tick_time=_safe_int(source.get("tick_time", 0)),
            latest_text=_safe_text(source.get("latest_text", "--"), "--"),
            quote_text=_safe_text(source.get("quote_text", "")),
            status_text=_safe_text(source.get("status_text", source.get("status", ""))),
            quote_status_code=_infer_quote_status_code(quote_status_source),
            execution_note=_safe_text(source.get("execution_note", "")),
            trade_grade=_safe_text(source.get("trade_grade", "")),
            trade_grade_detail=_safe_text(source.get("trade_grade_detail", "")),
            trade_next_review=_safe_text(source.get("trade_next_review", "")),
            trade_grade_source=_safe_text(source.get("trade_grade_source", "")),
            event_importance_text=_safe_text(source.get("event_importance_text", "")),
            event_note=_safe_text(source.get("event_note", "")),
            macro_focus=_safe_text(source.get("macro_focus", "")),
            alert_state_text=_safe_text(source.get("alert_state_text", "")),
            alert_state_detail=_safe_text(source.get("alert_state_detail", "")),
            alert_state_tone=_safe_text(source.get("alert_state_tone", "")),
            alert_state_rank=_safe_int(source.get("alert_state_rank", 0)),
            regime_tag=_safe_text(source.get("regime_tag", "")),
            regime_text=_safe_text(source.get("regime_text", "")),
            regime_reason=_safe_text(source.get("regime_reason", "")),
            regime_rank=_safe_int(source.get("regime_rank", 0)),
            tone=_safe_text(source.get("tone", "")),
            signal_side=_safe_text(source.get("signal_side", "")),
            signal_side_text=_safe_text(source.get("signal_side_text", "")),
            intraday_bias=_safe_text(source.get("intraday_bias", "")),
            intraday_bias_text=_safe_text(source.get("intraday_bias_text", "")),
            multi_timeframe_alignment=_safe_text(source.get("multi_timeframe_alignment", "")),
            multi_timeframe_alignment_text=_safe_text(source.get("multi_timeframe_alignment_text", "")),
            multi_timeframe_bias=_safe_text(source.get("multi_timeframe_bias", "")),
            multi_timeframe_bias_text=_safe_text(source.get("multi_timeframe_bias_text", "")),
            intraday_context_text=_safe_text(source.get("intraday_context_text", "")),
            multi_timeframe_context_text=_safe_text(source.get("multi_timeframe_context_text", "")),
            key_level_context_text=_safe_text(source.get("key_level_context_text", "")),
            key_level_state=_safe_text(source.get("key_level_state", "")),
            key_level_state_text=_safe_text(source.get("key_level_state_text", "")),
            breakout_direction=_safe_text(source.get("breakout_direction", "")),
            breakout_context_text=_safe_text(source.get("breakout_context_text", "")),
            breakout_state=_safe_text(source.get("breakout_state", "")),
            breakout_state_text=_safe_text(source.get("breakout_state_text", "")),
            retest_context_text=_safe_text(source.get("retest_context_text", "")),
            retest_state=_safe_text(source.get("retest_state", "")),
            retest_state_text=_safe_text(source.get("retest_state_text", "")),
            risk_reward_ready=bool(source.get("risk_reward_ready", False)),
            risk_reward_context_text=_safe_text(source.get("risk_reward_context_text", "")),
            risk_reward_state=_safe_text(source.get("risk_reward_state", "")),
            risk_reward_state_text=_safe_text(source.get("risk_reward_state_text", "")),
            risk_reward_ratio=_safe_float(source.get("risk_reward_ratio", 0.0)),
            risk_reward_direction=_safe_text(source.get("risk_reward_direction", "")),
            risk_reward_stop_price=_safe_float(source.get("risk_reward_stop_price", 0.0)),
            risk_reward_target_price=_safe_float(source.get("risk_reward_target_price", 0.0)),
            risk_reward_target_price_2=_safe_float(source.get("risk_reward_target_price_2", 0.0)),
            risk_reward_entry_zone_low=_safe_float(source.get("risk_reward_entry_zone_low", 0.0)),
            risk_reward_entry_zone_high=_safe_float(source.get("risk_reward_entry_zone_high", 0.0)),
            risk_reward_entry_zone_text=_safe_text(source.get("risk_reward_entry_zone_text", "")),
            risk_reward_position_text=_safe_text(source.get("risk_reward_position_text", "")),
            risk_reward_invalidation_text=_safe_text(source.get("risk_reward_invalidation_text", "")),
            risk_reward_atr=_safe_float(source.get("risk_reward_atr", 0.0)),
            atr14=_safe_float(source.get("atr14", 0.0)),
            atr14_h4=_safe_float(source.get("atr14_h4", 0.0)),
            event_mode_text=_safe_text(source.get("event_mode_text", "")),
            event_active_name=_safe_text(source.get("event_active_name", "")),
            event_active_time_text=_safe_text(source.get("event_active_time_text", "")),
            event_scope_text=_safe_text(source.get("event_scope_text", "")),
            event_applies=bool(source.get("event_applies", False)),
            tech_summary=_safe_text(source.get("tech_summary", "")),
            tech_summary_h4=_safe_text(source.get("tech_summary_h4", "")),
            h4_context_text=_safe_text(source.get("h4_context_text", "")),
            model_ready=bool(source.get("model_ready", False)),
            model_win_probability=_safe_float(source.get("model_win_probability", 0.0)),
            model_confidence_text=_safe_text(source.get("model_confidence_text", "")),
            model_note=_safe_text(source.get("model_note", "")),
            snapshot_id=_safe_int(source.get("snapshot_id", 0)),
            extra={key: value for key, value in source.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "symbol": self.symbol,
            "latest_price": self.latest_price,
            "spread_points": self.spread_points,
            "point": self.point,
            "has_live_quote": self.has_live_quote,
            "bid": self.bid,
            "ask": self.ask,
            "tick_time": self.tick_time,
            "latest_text": self.latest_text,
            "quote_text": self.quote_text,
            "status_text": self.status_text,
            "quote_status_code": self.quote_status_code,
            "execution_note": self.execution_note,
            "trade_grade": self.trade_grade,
            "trade_grade_detail": self.trade_grade_detail,
            "trade_next_review": self.trade_next_review,
            "trade_grade_source": self.trade_grade_source,
            "event_importance_text": self.event_importance_text,
            "event_note": self.event_note,
            "macro_focus": self.macro_focus,
            "alert_state_text": self.alert_state_text,
            "alert_state_detail": self.alert_state_detail,
            "alert_state_tone": self.alert_state_tone,
            "alert_state_rank": self.alert_state_rank,
            "regime_tag": self.regime_tag,
            "regime_text": self.regime_text,
            "regime_reason": self.regime_reason,
            "regime_rank": self.regime_rank,
            "tone": self.tone,
            "signal_side": self.signal_side,
            "signal_side_text": self.signal_side_text,
            "intraday_bias": self.intraday_bias,
            "intraday_bias_text": self.intraday_bias_text,
            "multi_timeframe_alignment": self.multi_timeframe_alignment,
            "multi_timeframe_alignment_text": self.multi_timeframe_alignment_text,
            "multi_timeframe_bias": self.multi_timeframe_bias,
            "multi_timeframe_bias_text": self.multi_timeframe_bias_text,
            "intraday_context_text": self.intraday_context_text,
            "multi_timeframe_context_text": self.multi_timeframe_context_text,
            "key_level_context_text": self.key_level_context_text,
            "key_level_state": self.key_level_state,
            "key_level_state_text": self.key_level_state_text,
            "breakout_direction": self.breakout_direction,
            "breakout_context_text": self.breakout_context_text,
            "breakout_state": self.breakout_state,
            "breakout_state_text": self.breakout_state_text,
            "retest_context_text": self.retest_context_text,
            "retest_state": self.retest_state,
            "retest_state_text": self.retest_state_text,
            "risk_reward_ready": self.risk_reward_ready,
            "risk_reward_context_text": self.risk_reward_context_text,
            "risk_reward_state": self.risk_reward_state,
            "risk_reward_state_text": self.risk_reward_state_text,
            "risk_reward_ratio": self.risk_reward_ratio,
            "risk_reward_direction": self.risk_reward_direction,
            "risk_reward_stop_price": self.risk_reward_stop_price,
            "risk_reward_target_price": self.risk_reward_target_price,
            "risk_reward_target_price_2": self.risk_reward_target_price_2,
            "risk_reward_entry_zone_low": self.risk_reward_entry_zone_low,
            "risk_reward_entry_zone_high": self.risk_reward_entry_zone_high,
            "risk_reward_entry_zone_text": self.risk_reward_entry_zone_text,
            "risk_reward_position_text": self.risk_reward_position_text,
            "risk_reward_invalidation_text": self.risk_reward_invalidation_text,
            "risk_reward_atr": self.risk_reward_atr,
            "atr14": self.atr14,
            "atr14_h4": self.atr14_h4,
            "event_mode_text": self.event_mode_text,
            "event_active_name": self.event_active_name,
            "event_active_time_text": self.event_active_time_text,
            "event_scope_text": self.event_scope_text,
            "event_applies": self.event_applies,
            "tech_summary": self.tech_summary,
            "tech_summary_h4": self.tech_summary_h4,
            "h4_context_text": self.h4_context_text,
            "model_ready": self.model_ready,
            "model_win_probability": self.model_win_probability,
            "model_confidence_text": self.model_confidence_text,
            "model_note": self.model_note,
            "snapshot_id": self.snapshot_id,
        }
        payload.update(dict(self.extra))
        return payload
