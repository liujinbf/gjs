from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from event_schedule import normalize_event_importance, normalize_event_schedule_text, parse_event_schedules, parse_event_symbols
from external_feed_models import EventFeedItem

PROJECT_DIR = Path(__file__).resolve().parent
EVENT_FEED_CACHE_FILE = PROJECT_DIR / ".runtime" / "event_feed_cache.json"


def _normalize_event_item_payload(item: dict | EventFeedItem | None) -> dict:
    """统一事件源条目字段契约。"""
    return EventFeedItem.from_payload(item).to_dict()


def merge_event_schedule_texts(*parts: str) -> str:
    chunks = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not chunks:
        return ""
    return normalize_event_schedule_text("\n".join(chunks))


def load_event_feed(
    enabled: bool,
    source: str,
    refresh_min: int,
    now: datetime | None = None,
    cache_file: Path | None = None,
    cache_only: bool = False,
) -> dict:
    current = now or datetime.now()
    cache_path = Path(cache_file) if cache_file else EVENT_FEED_CACHE_FILE
    source_text = str(source or "").strip()
    safe_refresh_min = max(5, int(refresh_min or 60))

    if not bool(enabled):
        return {
            "enabled": False,
            "status": "disabled",
            "status_text": "外部事件源未开启，当前仅使用手填事件计划。",
            "schedule_text": "",
            "item_count": 0,
            "items": [],
            "result_item_count": 0,
            "result_summary_text": "",
        }

    if not source_text:
        return {
            "enabled": True,
            "status": "missing",
            "status_text": "外部事件源已开启，但尚未配置 JSON 地址或本地文件路径。",
            "schedule_text": "",
            "item_count": 0,
            "items": [],
            "result_item_count": 0,
            "result_summary_text": "",
        }

    cached = _read_cache(cache_path)
    if bool(cache_only):
        if _cache_matches_source(cached, source_text) and _parse_cache_time(cached.get("fetched_at")) is not None:
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            age_text = _format_age_text(current, fetched_at)
            item_count = int(cached.get("item_count", 0) or 0)
            return {
                "enabled": True,
                "status": "cache_only",
                "status_text": f"外部事件源本地缓存载入：{item_count} 条，{age_text}同步。",
                "schedule_text": str(cached.get("schedule_text", "") or "").strip(),
                "item_count": item_count,
                "fetched_at_text": cached.get("fetched_at_text", ""),
                "items": list(cached.get("items", []) or []),
                "result_item_count": int(cached.get("result_item_count", 0) or 0),
                "result_summary_text": str(cached.get("result_summary_text", "") or "").strip(),
            }
        return {
            "enabled": True,
            "status": "cache_missing",
            "status_text": "外部事件源等待后台同步，本地尚无可用缓存。",
            "schedule_text": "",
            "item_count": 0,
            "items": [],
            "result_item_count": 0,
            "result_summary_text": "",
        }
    if _cache_is_fresh(cached, source_text, safe_refresh_min, current):
        fetched_at = _parse_cache_time(cached.get("fetched_at"))
        age_text = _format_age_text(current, fetched_at)
        item_count = int(cached.get("item_count", 0) or 0)
        return {
            "enabled": True,
            "status": "cache",
            "status_text": f"外部事件源缓存生效：{item_count} 条，{age_text}更新。",
            "schedule_text": str(cached.get("schedule_text", "") or "").strip(),
            "item_count": item_count,
            "fetched_at_text": cached.get("fetched_at_text", ""),
            "items": list(cached.get("items", []) or []),
            "result_item_count": int(cached.get("result_item_count", 0) or 0),
            "result_summary_text": str(cached.get("result_summary_text", "") or "").strip(),
        }

    try:
        payload = _load_event_feed_payload(source_text)
        items = build_structured_event_items(payload)
        schedule_text = build_schedule_text_from_payload(payload, items=items)
        item_count = len(parse_event_schedules(schedule_text))
        result_summary_text = build_event_result_summary(items)
        cache_payload = {
            "source": source_text,
            "fetched_at": current.isoformat(timespec="seconds"),
            "fetched_at_text": current.strftime("%Y-%m-%d %H:%M:%S"),
            "schedule_text": schedule_text,
            "item_count": item_count,
            "items": items,
            "result_item_count": len([item for item in items if bool(item.get("has_result"))]),
            "result_summary_text": result_summary_text,
        }
        _write_cache(cache_path, cache_payload)
        return {
            "enabled": True,
            "status": "fresh",
            "status_text": f"外部事件源已同步：{item_count} 条。",
            "schedule_text": schedule_text,
            "item_count": item_count,
            "fetched_at_text": cache_payload["fetched_at_text"],
            "items": items,
            "result_item_count": cache_payload["result_item_count"],
            "result_summary_text": result_summary_text,
        }
    except Exception as exc:  # noqa: BLE001
        if _cache_matches_source(cached, source_text) and str(cached.get("schedule_text", "") or "").strip():
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            age_text = _format_age_text(current, fetched_at)
            item_count = int(cached.get("item_count", 0) or 0)
            return {
                "enabled": True,
                "status": "stale_cache",
                "status_text": f"外部事件源拉取失败，继续使用{age_text}缓存：{item_count} 条。",
                "schedule_text": str(cached.get("schedule_text", "") or "").strip(),
                "item_count": item_count,
                "fetched_at_text": cached.get("fetched_at_text", ""),
                "error_text": str(exc),
                "items": list(cached.get("items", []) or []),
                "result_item_count": int(cached.get("result_item_count", 0) or 0),
                "result_summary_text": str(cached.get("result_summary_text", "") or "").strip(),
            }
        return {
            "enabled": True,
            "status": "error",
            "status_text": f"外部事件源拉取失败：{str(exc).strip() or '未知错误'}",
            "schedule_text": "",
            "item_count": 0,
            "error_text": str(exc),
            "items": [],
            "result_item_count": 0,
            "result_summary_text": "",
        }


def build_schedule_text_from_payload(payload: object, items: list[dict] | None = None) -> str:
    source_items = [_normalize_event_item_payload(item) for item in list(items or build_structured_event_items(payload))]
    lines = []
    for normalized in source_items:
        if not normalized:
            continue
        importance = normalized["importance"]
        symbols = normalized["symbols"]
        if importance == "medium" and not symbols:
            lines.append(f"{normalized['time_text']}|{normalized['name']}")
        else:
            symbol_text = ",".join(symbols) if symbols else "全部"
            lines.append(f"{normalized['time_text']}|{normalized['name']}|{importance}|{symbol_text}")
    return normalize_event_schedule_text("\n".join(lines))


def build_structured_event_items(payload: object) -> list[dict]:
    result = []
    for item in _extract_event_items(payload):
        normalized = _normalize_event_item(item)
        if normalized:
            result.append(_normalize_event_item_payload(normalized))
    return result


def _extract_event_items(payload: object) -> list[object]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("events", "data", "items", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    return []


def _normalize_event_item(item: object) -> dict | None:
    if not isinstance(item, dict):
        return None

    # ---- Forex Factory 格式识别 ----
    # FF JSON 字段: title, date, time, impact, currency, forecast, previous, actual
    is_forex_factory = "impact" in item or ("currency" in item and "title" in item)
    if is_forex_factory:
        item = _adapt_forex_factory_item(item)

    event_time = _coerce_event_time(
        item.get("time", item.get("datetime", item.get("scheduled_at", item.get("timestamp", item.get("date")))))
    )
    if event_time is None:
        return None
    name = str(item.get("name", item.get("title", item.get("event", ""))) or "").strip() or "未命名事件"
    importance = normalize_event_importance(str(item.get("importance", item.get("level", "")) or "").strip())
    raw_symbols = item.get("symbols", item.get("symbol", item.get("scope", "")))
    if isinstance(raw_symbols, (list, tuple, set)):
        symbols = parse_event_symbols(",".join(str(part or "").strip() for part in raw_symbols))
    else:
        symbols = parse_event_symbols(str(raw_symbols or "").strip())
    actual = _coerce_metric_value(item.get("actual", item.get("value", item.get("actual_value"))))
    forecast = _coerce_metric_value(item.get("forecast", item.get("consensus", item.get("estimate"))))
    previous = _coerce_metric_value(item.get("previous", item.get("prior", item.get("previous_value"))))
    better_when = _normalize_better_when(item.get("better_when", item.get("bias_mode", item.get("interpretation", ""))))
    result_bias = _resolve_event_result_bias(actual, forecast, previous, better_when)
    return EventFeedItem(
        time_text=event_time.strftime("%Y-%m-%d %H:%M"),
        name=name,
        importance=importance,
        symbols=symbols,
        actual=actual,
        forecast=forecast,
        previous=previous,
        unit=str(item.get("unit", "") or "").strip(),
        country=str(item.get("country", item.get("region", "")) or "").strip(),
        source=str(item.get("source", "") or "").strip(),
        better_when=better_when,
        has_result=any(value is not None for value in (actual, forecast, previous)),
        result_bias=result_bias,
        result_summary_text=_build_event_result_text(name, actual, forecast, previous, str(item.get("unit", "") or "").strip(), result_bias),
    ).to_dict()


# Forex Factory currency -> affected symbols mapping
_FF_CURRENCY_SYMBOLS: dict[str, list[str]] = {
    "USD": ["XAUUSD", "XAGUSD", "EURUSD", "USDJPY"],
    "EUR": ["EURUSD"],
    "JPY": ["USDJPY"],
    "XAU": ["XAUUSD"],
    "XAG": ["XAGUSD"],
    "GBP": [],
    "AUD": [],
    "CAD": [],
    "CHF": [],
    "NZD": [],
}

_FF_IMPACT_MAP = {
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "Holiday": "low",
}


def _adapt_forex_factory_item(item: dict) -> dict:
    """将 Forex Factory JSON 格式转为内部通用格式。"""
    adapted = dict(item)
    # impact -> importance
    ff_impact = str(item.get("impact", "") or "").strip()
    if ff_impact and "importance" not in adapted:
        adapted["importance"] = _FF_IMPACT_MAP.get(ff_impact, "medium")
    # currency -> symbols
    ff_currency = str(item.get("currency", "") or "").strip().upper()
    if ff_currency and "symbols" not in adapted:
        mapped_symbols = _FF_CURRENCY_SYMBOLS.get(ff_currency, [])
        adapted["symbols"] = ",".join(mapped_symbols) if mapped_symbols else ""
    # title -> name
    if "title" in item and "name" not in adapted:
        adapted["name"] = str(item.get("title", "") or "").strip()
    # date + time -> time (combine if both present)
    if "date" in item and "time" in item and "datetime" not in adapted:
        date_str = str(item.get("date", "") or "").strip()
        time_str = str(item.get("time", "") or "").strip()
        if date_str and time_str:
            adapted["time"] = f"{date_str} {time_str}"
        elif date_str:
            adapted["time"] = date_str
    return adapted



def _coerce_metric_value(value: object) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"--", "n/a", "N/A", "null", "None"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _normalize_better_when(value: str) -> str:
    text = str(value or "").strip().lower()
    if text in {"higher_bullish", "higher_positive", "higher_better"}:
        return "higher_bullish"
    if text in {"higher_bearish", "higher_negative", "higher_worse"}:
        return "higher_bearish"
    if text in {"lower_bullish", "lower_positive", "lower_better"}:
        return "lower_bullish"
    if text in {"lower_bearish", "lower_negative", "lower_worse"}:
        return "lower_bearish"
    return "neutral"


def _resolve_event_result_bias(
    actual: float | None,
    forecast: float | None,
    previous: float | None,
    better_when: str,
) -> str:
    baseline = forecast if forecast is not None else previous
    if actual is None or baseline is None:
        return "neutral"
    delta = actual - baseline
    if abs(delta) < 1e-12:
        return "neutral"
    if better_when == "higher_bullish":
        return "bullish" if delta > 0 else "bearish"
    if better_when == "higher_bearish":
        return "bearish" if delta > 0 else "bullish"
    if better_when == "lower_bullish":
        return "bullish" if delta < 0 else "bearish"
    if better_when == "lower_bearish":
        return "bearish" if delta < 0 else "bullish"
    return "neutral"


def _format_metric_value(value: float | None, unit: str = "") -> str:
    if value is None:
        return "--"
    if abs(value) >= 100:
        text = f"{value:.0f}"
    elif abs(value) >= 10:
        text = f"{value:.2f}"
    else:
        text = f"{value:.3f}".rstrip("0").rstrip(".")
    return f"{text}{unit}".strip()


def _build_event_result_text(
    name: str,
    actual: float | None,
    forecast: float | None,
    previous: float | None,
    unit: str,
    result_bias: str,
) -> str:
    if all(value is None for value in (actual, forecast, previous)):
        return ""
    direction_text = "中性"
    if result_bias == "bullish":
        direction_text = "偏多"
    elif result_bias == "bearish":
        direction_text = "偏空"
    parts = [
        f"{name}：实际 {_format_metric_value(actual, unit)}",
        f"预期 {_format_metric_value(forecast, unit)}",
        f"前值 {_format_metric_value(previous, unit)}",
        f"结果解读 {direction_text}",
    ]
    return "，".join(parts)


def build_event_result_summary(items: list[dict]) -> str:
    result_items = [_normalize_event_item_payload(item) for item in list(items or []) if bool(_normalize_event_item_payload(item).get("has_result"))]
    if not result_items:
        return ""
    prioritized = sorted(
        result_items,
        key=lambda item: (
            0 if str(item.get("importance", "medium")).strip().lower() == "high" else 1,
            str(item.get("time_text", "") or "").strip(),
            str(item.get("name", "") or "").strip(),
        ),
    )
    parts = [str(item.get("result_summary_text", "") or "").strip() for item in prioritized[:3] if str(item.get("result_summary_text", "") or "").strip()]
    if not parts:
        return ""
    return f"事件结果：{'；'.join(parts)}。"


def apply_event_feed_to_snapshot(snapshot: dict, feed_result: dict) -> dict:
    payload = dict(snapshot or {})
    result = dict(feed_result or {})
    items = [_normalize_event_item_payload(item) for item in list(result.get("items", []) or [])]
    result_summary_text = str(result.get("result_summary_text", "") or "").strip()
    payload["event_feed_items"] = items
    payload["event_result_item_count"] = int(result.get("result_item_count", 0) or 0)
    payload["event_result_summary_text"] = result_summary_text
    if result_summary_text:
        base_summary = str(payload.get("summary_text", "") or "").strip()
        if result_summary_text not in base_summary:
            payload["summary_text"] = (base_summary + f"\n{result_summary_text}").strip()
        base_market_text = str(payload.get("market_text", "") or "").strip()
        if result_summary_text not in base_market_text:
            payload["market_text"] = (base_market_text + f" {result_summary_text}").strip()
    return payload


def _coerce_event_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone().replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except (OSError, OverflowError, ValueError):
            return None

    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return datetime.fromtimestamp(float(text))
        except (OSError, OverflowError, ValueError):
            return None

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


def _load_event_feed_payload(source: str) -> object:
    text = str(source or "").strip()
    if text.lower().startswith(("http://", "https://")):
        try:
            from urllib.request import Request
            req = Request(text, headers={'User-Agent': 'Mozilla/5.0 (Windows; U; Windows NT 10.0; Win64; x64) AppleWebkit/537.36'})
            with urlopen(req, timeout=5) as response:
                payload = response.read().decode("utf-8")
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"网络错误：{exc.reason}") from exc
    else:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = (PROJECT_DIR / path).resolve()
        payload = path.read_text(encoding="utf-8")
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("事件源内容不是合法 JSON") from exc


def _read_cache(cache_file: Path) -> dict:
    if not cache_file.exists():
        return {}
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_cache(cache_file: Path, payload: dict) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temp_file = cache_file.with_suffix(f"{cache_file.suffix}.tmp")
    temp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_file.replace(cache_file)


def _cache_matches_source(cache_payload: dict, source: str) -> bool:
    return str(cache_payload.get("source", "") or "").strip() == str(source or "").strip()


def _cache_is_fresh(cache_payload: dict, source: str, refresh_min: int, current: datetime) -> bool:
    if not _cache_matches_source(cache_payload, source):
        return False
    fetched_at = _parse_cache_time(cache_payload.get("fetched_at"))
    if fetched_at is None:
        return False
    age_minutes = (current - fetched_at).total_seconds() / 60.0
    return age_minutes >= 0 and age_minutes <= float(refresh_min)


def _parse_cache_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def _format_age_text(current: datetime, fetched_at: datetime | None) -> str:
    if fetched_at is None:
        return "刚刚"
    delta_sec = max(0, int((current - fetched_at).total_seconds()))
    if delta_sec < 60:
        return "刚刚"
    minutes = max(1, delta_sec // 60)
    return f"{minutes} 分钟前"
