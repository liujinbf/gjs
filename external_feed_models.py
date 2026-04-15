from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _safe_text(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        return str(value).strip()
    except Exception:  # noqa: BLE001
        return default


def _safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value) if value not in ("", None) else default


def _safe_symbols(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").replace("；", ",").replace("，", ",").split(",")
    result: list[str] = []
    seen = set()
    for item in items:
        symbol = _safe_text(item).upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _safe_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, current in value.items():
        clean_key = _safe_text(key).upper()
        clean_value = _safe_text(current).lower()
        if clean_key and clean_value:
            result[clean_key] = clean_value
    return result


@dataclass(slots=True)
class EventFeedItem:
    """外部事件源条目的轻量模型。"""

    time_text: str = ""
    name: str = ""
    importance: str = "medium"
    symbols: list[str] = field(default_factory=list)
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    unit: str = ""
    country: str = ""
    source: str = ""
    better_when: str = "neutral"
    has_result: bool = False
    result_bias: str = "neutral"
    result_summary_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | "EventFeedItem" | None) -> "EventFeedItem":
        if isinstance(payload, cls):
            return payload
        source = dict(payload or {})
        known_keys = {
            "time_text", "name", "importance", "symbols", "actual", "forecast", "previous",
            "unit", "country", "source", "better_when", "has_result", "result_bias", "result_summary_text",
        }
        return cls(
            time_text=_safe_text(source.get("time_text", "")),
            name=_safe_text(source.get("name", "")),
            importance=_safe_text(source.get("importance", "medium")).lower() or "medium",
            symbols=_safe_symbols(source.get("symbols", [])),
            actual=_safe_float(source.get("actual")),
            forecast=_safe_float(source.get("forecast")),
            previous=_safe_float(source.get("previous")),
            unit=_safe_text(source.get("unit", "")),
            country=_safe_text(source.get("country", "")),
            source=_safe_text(source.get("source", "")),
            better_when=_safe_text(source.get("better_when", "neutral")).lower() or "neutral",
            has_result=_safe_bool(source.get("has_result", False)),
            result_bias=_safe_text(source.get("result_bias", "neutral")).lower() or "neutral",
            result_summary_text=_safe_text(source.get("result_summary_text", "")),
            extra={key: value for key, value in source.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "time_text": self.time_text,
            "name": self.name,
            "importance": self.importance,
            "symbols": list(self.symbols),
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
            "unit": self.unit,
            "country": self.country,
            "source": self.source,
            "better_when": self.better_when,
            "has_result": self.has_result,
            "result_bias": self.result_bias,
            "result_summary_text": self.result_summary_text,
        }
        payload.update(dict(self.extra))
        return payload


@dataclass(slots=True)
class MacroDataItem:
    """结构化宏观数据条目的轻量模型。"""

    name: str = ""
    source: str = ""
    published_at: str = ""
    latest_value: float | None = None
    previous_value: float | None = None
    value_text: str = ""
    delta_text: str = ""
    importance: str = "medium"
    symbols: list[str] = field(default_factory=list)
    bias_mode: str = "neutral"
    direction: str = "neutral"
    bias_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | "MacroDataItem" | None) -> "MacroDataItem":
        if isinstance(payload, cls):
            return payload
        source = dict(payload or {})
        known_keys = {
            "name", "source", "published_at", "latest_value", "previous_value", "value_text",
            "delta_text", "importance", "symbols", "bias_mode", "direction", "bias_text",
        }
        return cls(
            name=_safe_text(source.get("name", "")),
            source=_safe_text(source.get("source", "")),
            published_at=_safe_text(source.get("published_at", "")),
            latest_value=_safe_float(source.get("latest_value")),
            previous_value=_safe_float(source.get("previous_value")),
            value_text=_safe_text(source.get("value_text", "")),
            delta_text=_safe_text(source.get("delta_text", "")),
            importance=_safe_text(source.get("importance", "medium")).lower() or "medium",
            symbols=_safe_symbols(source.get("symbols", [])),
            bias_mode=_safe_text(source.get("bias_mode", "neutral")).lower() or "neutral",
            direction=_safe_text(source.get("direction", "neutral")).lower() or "neutral",
            bias_text=_safe_text(source.get("bias_text", "")),
            extra={key: value for key, value in source.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "source": self.source,
            "published_at": self.published_at,
            "latest_value": self.latest_value,
            "previous_value": self.previous_value,
            "value_text": self.value_text,
            "delta_text": self.delta_text,
            "importance": self.importance,
            "symbols": list(self.symbols),
            "bias_mode": self.bias_mode,
            "direction": self.direction,
            "bias_text": self.bias_text,
        }
        payload.update(dict(self.extra))
        return payload


@dataclass(slots=True)
class MacroNewsItem:
    """外部资讯条目的轻量模型。"""

    title: str = ""
    summary: str = ""
    published_at: str = ""
    link: str = ""
    source: str = ""
    importance: str = "medium"
    symbols: list[str] = field(default_factory=list)
    bias_by_symbol: dict[str, str] = field(default_factory=dict)
    bias_summary_text: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | "MacroNewsItem" | None) -> "MacroNewsItem":
        if isinstance(payload, cls):
            return payload
        source = dict(payload or {})
        known_keys = {
            "title", "summary", "published_at", "link", "source", "importance",
            "symbols", "bias_by_symbol", "bias_summary_text",
        }
        return cls(
            title=_safe_text(source.get("title", "")),
            summary=_safe_text(source.get("summary", "")),
            published_at=_safe_text(source.get("published_at", "")),
            link=_safe_text(source.get("link", "")),
            source=_safe_text(source.get("source", "")),
            importance=_safe_text(source.get("importance", "medium")).lower() or "medium",
            symbols=_safe_symbols(source.get("symbols", [])),
            bias_by_symbol=_safe_str_map(source.get("bias_by_symbol", {})),
            bias_summary_text=_safe_text(source.get("bias_summary_text", "")),
            extra={key: value for key, value in source.items() if key not in known_keys},
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "title": self.title,
            "summary": self.summary,
            "published_at": self.published_at,
            "link": self.link,
            "source": self.source,
            "importance": self.importance,
            "symbols": list(self.symbols),
            "bias_by_symbol": dict(self.bias_by_symbol),
            "bias_summary_text": self.bias_summary_text,
        }
        payload.update(dict(self.extra))
        return payload
