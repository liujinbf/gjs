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


def _infer_quote_status_code(source: dict[str, Any]) -> str:
    explicit_code = _safe_text(source.get("quote_status_code", ""))
    if explicit_code:
        return explicit_code.lower()

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
    if "休市" in status_text or "暂无" in status_text:
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
