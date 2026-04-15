from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen, Request

from app_config import PROJECT_DIR
from external_feed_models import MacroNewsItem

MACRO_NEWS_CACHE_FILE = PROJECT_DIR / ".runtime" / "macro_news_feed_cache.json"


def _normalize_macro_news_item(item: dict | MacroNewsItem | None) -> dict:
    """统一外部资讯条目字段契约。"""
    return MacroNewsItem.from_payload(item).to_dict()

GLOBAL_SYMBOLS = {"XAUUSD", "XAGUSD", "EURUSD", "USDJPY"}
HIGH_IMPORTANCE_KEYWORDS = {
    "fomc",
    "federal reserve",
    "powell",
    "rate decision",
    "policy decision",
    "interest rate",
    "cpi",
    "pce",
    "inflation",
    "payroll",
    "nonfarm",
    "nfp",
    "gdp",
    "ecb",
    "lagarde",
    "boj",
    "bank of japan",
    "ueda",
}
MEDIUM_IMPORTANCE_KEYWORDS = {
    "treasury",
    "yield",
    "bond",
    "employment",
    "jobless",
    "pmi",
    "ism",
    "euro area",
    "japan",
    "yen",
    "dollar",
    "gold",
    "silver",
}
SYMBOL_KEYWORDS = {
    "XAUUSD": {"gold", "bullion", "fomc", "federal reserve", "powell", "cpi", "pce", "inflation", "yield", "treasury"},
    "XAGUSD": {"silver", "bullion", "fomc", "federal reserve", "powell", "cpi", "pce", "inflation", "yield"},
    "EURUSD": {"ecb", "lagarde", "euro area", "eurozone", "euro", "federal reserve", "powell", "dollar", "cpi", "pce"},
    "USDJPY": {"boj", "bank of japan", "ueda", "yen", "japan", "treasury", "yield", "federal reserve", "powell"},
}
USD_HAWKISH_KEYWORDS = {
    "hawkish",
    "rate hike",
    "higher for longer",
    "sticky inflation",
    "hot inflation",
    "strong payroll",
    "strong labor",
    "higher yield",
    "yields rise",
    "stronger dollar",
}
USD_DOVISH_KEYWORDS = {
    "dovish",
    "rate cut",
    "easing",
    "cooling inflation",
    "soft inflation",
    "weak payroll",
    "weak labor",
    "lower yield",
    "yields fall",
    "weaker dollar",
}
ECB_HAWKISH_KEYWORDS = {
    "ecb hawkish",
    "lagarde hawkish",
    "rate hike",
    "policy tightening",
    "higher rates",
    "inflation remains in focus",
    "inflation still in focus",
    "inflation remains the focus",
}
ECB_DOVISH_KEYWORDS = {"ecb dovish", "lagarde dovish", "rate cut", "policy easing", "lower rates"}
BOJ_HAWKISH_KEYWORDS = {"boj hawkish", "ueda hawkish", "tightening", "yield hike", "higher rates", "yen stronger"}
BOJ_DOVISH_KEYWORDS = {"boj dovish", "ueda dovish", "policy easing", "more stimulus", "yen weaker"}


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _parse_sources_text(value: str) -> list[str]:
    text = str(value or "").replace("；", ";").replace("\n", ";")
    return [item.strip() for item in text.split(";") if item.strip()]


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


def _parse_cache_time(value: object) -> datetime | None:
    text = _normalize_text(value)
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


def _cache_is_fresh(cache_payload: dict, source_text: str, refresh_min: int, current: datetime) -> bool:
    if _normalize_text(cache_payload.get("source_text", "")) != _normalize_text(source_text):
        return False
    fetched_at = _parse_cache_time(cache_payload.get("fetched_at"))
    if fetched_at is None:
        return False
    age_minutes = (current - fetched_at).total_seconds() / 60.0
    return age_minutes >= 0 and age_minutes <= float(refresh_min)


def _load_source_text(source: str) -> str:
    source_text = str(source or "").strip()
    if source_text.lower().startswith(("http://", "https://")):
        # 使用浏览器 UA 避免部分资讯站点（Seeking Alpha、ZeroHedge 等）返回 403
        req = Request(
            source_text,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"},
        )
        try:
            with urlopen(req, timeout=8) as response:
                payload = response.read()
        except HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"网络错误：{exc.reason}") from exc
        return payload.decode("utf-8", errors="ignore")
    path = Path(source_text).expanduser()
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    return path.read_text(encoding="utf-8")


def _strip_tag(tag: str) -> str:
    return str(tag or "").split("}", 1)[-1].lower()


def _find_child_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(element):
        if _strip_tag(child.tag) in names:
            return _normalize_text(child.text)
    return ""


def _parse_time(value: str) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    try:
        parsed_rfc = parsedate_to_datetime(text)
        return parsed_rfc.astimezone().replace(tzinfo=None) if parsed_rfc.tzinfo else parsed_rfc
    except (TypeError, ValueError, IndexError):
        return None


def _infer_symbols(title: str, summary: str, watch_symbols: list[str] | None = None) -> list[str]:
    text = f"{_normalize_text(title)} {_normalize_text(summary)}".lower()
    matched = []
    target_symbols = [str(item or "").strip().upper() for item in list(watch_symbols or []) if str(item or "").strip()]
    candidates = target_symbols or sorted(GLOBAL_SYMBOLS)
    for symbol in candidates:
        keywords = SYMBOL_KEYWORDS.get(symbol, set())
        if any(keyword in text for keyword in keywords):
            matched.append(symbol)
    return matched


def _infer_importance(title: str, summary: str) -> str:
    text = f"{_normalize_text(title)} {_normalize_text(summary)}".lower()
    if any(keyword in text for keyword in HIGH_IMPORTANCE_KEYWORDS):
        return "high"
    if any(keyword in text for keyword in MEDIUM_IMPORTANCE_KEYWORDS):
        return "medium"
    return "low"


def _text_contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _infer_symbol_news_bias(symbol: str, title: str, summary: str, source: str) -> str:
    symbol_key = str(symbol or "").strip().upper()
    text = f"{_normalize_text(title)} {_normalize_text(summary)} {_normalize_text(source)}".lower()

    if symbol_key in {"XAUUSD", "XAGUSD"}:
        if _text_contains_any(text, USD_HAWKISH_KEYWORDS):
            return "bearish"
        if _text_contains_any(text, USD_DOVISH_KEYWORDS):
            return "bullish"
    elif symbol_key == "EURUSD":
        if _text_contains_any(text, ECB_HAWKISH_KEYWORDS):
            return "bullish"
        if _text_contains_any(text, ECB_DOVISH_KEYWORDS):
            return "bearish"
        if _text_contains_any(text, USD_HAWKISH_KEYWORDS):
            return "bearish"
        if _text_contains_any(text, USD_DOVISH_KEYWORDS):
            return "bullish"
    elif symbol_key == "USDJPY":
        if _text_contains_any(text, BOJ_HAWKISH_KEYWORDS):
            return "bearish"
        if _text_contains_any(text, BOJ_DOVISH_KEYWORDS):
            return "bullish"
        if _text_contains_any(text, USD_HAWKISH_KEYWORDS):
            return "bullish"
        if _text_contains_any(text, USD_DOVISH_KEYWORDS):
            return "bearish"
    return "neutral"


def _build_bias_summary_text(symbols: list[str], title: str, summary: str, source: str) -> tuple[dict[str, str], str]:
    bias_by_symbol = {}
    summaries = []
    for symbol in list(symbols or []):
        bias = _infer_symbol_news_bias(symbol, title, summary, source)
        if bias not in {"bullish", "bearish"}:
            continue
        bias_by_symbol[str(symbol).strip().upper()] = bias
        bias_text = "偏多" if bias == "bullish" else "偏空"
        summaries.append(f"{str(symbol).strip().upper()} {bias_text}")
    return bias_by_symbol, "；".join(summaries)


def _source_name_from_url(url: str) -> str:
    host = (urlparse(str(url or "").strip()).netloc or "").lower()
    if "ecb.europa.eu" in host:
        return "ECB"
    if "federalreserve.gov" in host:
        return "Federal Reserve"
    if "bls.gov" in host:
        return "BLS"
    if "bea.gov" in host:
        return "BEA"
    if "treasury.gov" in host or "fiscaldata.treasury.gov" in host:
        return "U.S. Treasury"
    return host or "外部资讯源"


def _entry_link(entry: ET.Element) -> str:
    for child in list(entry):
        if _strip_tag(child.tag) != "link":
            continue
        href = _normalize_text(child.attrib.get("href", ""))
        if href:
            return href
        text = _normalize_text(child.text)
        if text:
            return text
    return ""


def _parse_feed_items(xml_text: str, source_url: str, watch_symbols: list[str] | None = None) -> list[dict]:
    try:
        root = ET.fromstring(str(xml_text or "").strip())
    except ET.ParseError as exc:
        raise RuntimeError("资讯源内容不是合法 RSS/Atom XML") from exc

    items = []
    root_tag = _strip_tag(root.tag)
    source_name = _source_name_from_url(source_url)
    if root_tag == "rss":
        channel = next((child for child in list(root) if _strip_tag(child.tag) == "channel"), root)
        source_name = _find_child_text(channel, ("title",)) or source_name
        raw_items = [child for child in list(channel) if _strip_tag(child.tag) == "item"]
        for item in raw_items:
            title = _find_child_text(item, ("title",))
            summary = _find_child_text(item, ("description", "summary"))
            published_at = _parse_time(_find_child_text(item, ("pubdate", "published", "updated", "dc:date")))
            items.append(
                {
                    "title": title or "未命名资讯",
                    "summary": summary,
                    "published_at": published_at.strftime("%Y-%m-%d %H:%M:%S") if published_at else "",
                    "link": _find_child_text(item, ("link",)),
                    "source": source_name,
                }
            )
    elif root_tag == "feed":
        source_name = _find_child_text(root, ("title",)) or source_name
        raw_items = [child for child in list(root) if _strip_tag(child.tag) == "entry"]
        for item in raw_items:
            title = _find_child_text(item, ("title",))
            summary = _find_child_text(item, ("summary", "content"))
            published_at = _parse_time(_find_child_text(item, ("published", "updated")))
            items.append(
                {
                    "title": title or "未命名资讯",
                    "summary": summary,
                    "published_at": published_at.strftime("%Y-%m-%d %H:%M:%S") if published_at else "",
                    "link": _entry_link(item),
                    "source": source_name,
                }
            )
    else:
        raise RuntimeError("当前仅支持 RSS 或 Atom 资讯源")

    normalized = []
    seen = set()
    for item in items:
        title = _normalize_text(item.get("title", ""))
        summary = _normalize_text(item.get("summary", ""))
        if not title:
            continue
        published_at = _normalize_text(item.get("published_at", ""))
        signature = f"{title}|{published_at}|{_normalize_text(item.get('source', ''))}"
        if signature in seen:
            continue
        seen.add(signature)
        symbols = _infer_symbols(title, summary, watch_symbols=watch_symbols)
        importance = _infer_importance(title, summary)
        bias_by_symbol, bias_summary_text = _build_bias_summary_text(symbols, title, summary, _normalize_text(item.get("source", "")) or source_name)
        normalized.append(
            MacroNewsItem(
                title=title,
                summary=summary,
                published_at=published_at,
                link=_normalize_text(item.get("link", "")),
                source=_normalize_text(item.get("source", "")) or source_name,
                importance=importance,
                symbols=symbols,
                bias_by_symbol=bias_by_symbol,
                bias_summary_text=bias_summary_text,
            ).to_dict()
        )
    return normalized


def _relevance_score(item: dict, watch_symbols: list[str] | None = None) -> int:
    item = _normalize_macro_news_item(item)
    score = 0
    importance = _normalize_text(item.get("importance", "")).lower()
    if importance == "high":
        score += 3
    elif importance == "medium":
        score += 2
    else:
        score += 1
    symbols = {str(symbol or "").strip().upper() for symbol in list(item.get("symbols", []) or []) if str(symbol or "").strip()}
    target_symbols = {str(symbol or "").strip().upper() for symbol in list(watch_symbols or []) if str(symbol or "").strip()}
    if target_symbols and symbols.intersection(target_symbols):
        score += 3
    elif not symbols:
        score += 1
    return score


def _format_digest(items: list[dict]) -> str:
    if not items:
        return "外部资讯流暂无高相关更新。"
    highlights = []
    for item in [_normalize_macro_news_item(item) for item in items[:3]]:
        title = _normalize_text(item.get("title", ""))
        source = _normalize_text(item.get("source", ""))
        bias_summary_text = _normalize_text(item.get("bias_summary_text", ""))
        if source:
            text = f"{source}：{title}"
        else:
            text = title
        if bias_summary_text:
            text += f"（{bias_summary_text}）"
        highlights.append(text)
    return f"外部资讯流：近一轮抓到 {len(items)} 条高相关更新，最新包括 {'；'.join(highlights)}。"


def load_macro_news_feed(
    enabled: bool,
    source_text: str,
    refresh_min: int,
    symbols: list[str] | None = None,
    now: datetime | None = None,
    cache_file: Path | None = None,
    cache_only: bool = False,
) -> dict:
    current = now or datetime.now()
    cache_path = Path(cache_file) if cache_file else MACRO_NEWS_CACHE_FILE
    clean_source_text = _normalize_text(source_text)
    safe_refresh_min = max(5, int(refresh_min or 30))
    watch_symbols = [str(item or "").strip().upper() for item in list(symbols or []) if str(item or "").strip()]

    if not bool(enabled):
        return {
            "enabled": False,
            "status": "disabled",
            "status_text": "外部资讯流未开启，当前仍以本地结构判断和事件表为主。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }
    if not clean_source_text:
        return {
            "enabled": True,
            "status": "missing",
            "status_text": "外部资讯流已开启，但尚未配置 RSS/Atom 地址。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }

    cached = _read_cache(cache_path)
    if bool(cache_only):
        if _normalize_text(cached.get("source_text", "")) == clean_source_text and _parse_cache_time(cached.get("fetched_at")) is not None:
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            items = list(cached.get("items", []) or [])
            return {
                "enabled": True,
                "status": "cache_only",
                "status_text": f"外部资讯流本地缓存载入：{len(items)} 条，{_format_age_text(current, fetched_at)}同步。",
                "item_count": len(items),
                "summary_text": _normalize_text(cached.get("summary_text", "")),
                "items": items,
                "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
            }
        return {
            "enabled": True,
            "status": "cache_missing",
            "status_text": "外部资讯流等待后台同步，本地尚无可用缓存。",
            "item_count": 0,
            "summary_text": "",
            "items": [],
            "fetched_at_text": "",
        }
    if _cache_is_fresh(cached, clean_source_text, safe_refresh_min, current):
        fetched_at = _parse_cache_time(cached.get("fetched_at"))
        items = list(cached.get("items", []) or [])
        return {
            "enabled": True,
            "status": "cache",
            "status_text": f"外部资讯流缓存生效：{len(items)} 条，{_format_age_text(current, fetched_at)}更新。",
            "items": items,
            "summary_text": _normalize_text(cached.get("summary_text", "")),
            "item_count": len(items),
            "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
        }

    sources = _parse_sources_text(clean_source_text)
    if not sources:
        return {
            "enabled": True,
            "status": "missing",
            "status_text": "外部资讯流已开启，但未解析到有效 RSS/Atom 地址。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }

    try:
        entries = []
        for source in sources:
            entries.extend(_parse_feed_items(_load_source_text(source), source, watch_symbols=watch_symbols))
        unique_entries = []
        seen = set()
        for item in sorted(
            entries,
            key=lambda current_item: (
                -_relevance_score(current_item, watch_symbols=watch_symbols),
                _normalize_text(current_item.get("published_at", "")),
                _normalize_text(current_item.get("title", "")),
            ),
            reverse=True,
        ):
            signature = f"{_normalize_text(item.get('title', ''))}|{_normalize_text(item.get('published_at', ''))}|{_normalize_text(item.get('source', ''))}"
            if signature in seen:
                continue
            seen.add(signature)
            if watch_symbols:
                related_symbols = {str(symbol or "").strip().upper() for symbol in list(item.get("symbols", []) or []) if str(symbol or "").strip()}
                if related_symbols and not related_symbols.intersection(set(watch_symbols)):
                    continue
            unique_entries.append(item)
        top_items = unique_entries[:6]
        summary_text = _format_digest(top_items)
        cache_payload = {
            "source_text": clean_source_text,
            "fetched_at": current.isoformat(timespec="seconds"),
            "fetched_at_text": current.strftime("%Y-%m-%d %H:%M:%S"),
            "items": top_items,
            "summary_text": summary_text,
        }
        _write_cache(cache_path, cache_payload)
        return {
            "enabled": True,
            "status": "fresh",
            "status_text": f"外部资讯流已同步：{len(top_items)} 条高相关更新。",
            "items": top_items,
            "summary_text": summary_text,
            "item_count": len(top_items),
            "fetched_at_text": cache_payload["fetched_at_text"],
        }
    except Exception as exc:  # noqa: BLE001
        if _normalize_text(cached.get("source_text", "")) == clean_source_text and list(cached.get("items", []) or []):
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            items = list(cached.get("items", []) or [])
            return {
                "enabled": True,
                "status": "stale_cache",
                "status_text": f"外部资讯流拉取失败，继续使用{_format_age_text(current, fetched_at)}缓存：{len(items)} 条。",
                "items": items,
                "summary_text": _normalize_text(cached.get("summary_text", "")),
                "item_count": len(items),
                "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
                "error_text": str(exc),
            }
        return {
            "enabled": True,
            "status": "error",
            "status_text": f"外部资讯流拉取失败：{_normalize_text(exc)}",
            "items": [],
            "summary_text": "",
            "item_count": 0,
            "error_text": str(exc),
        }


def apply_macro_news_to_snapshot(snapshot: dict, feed_result: dict) -> dict:
    payload = dict(snapshot or {})
    result = dict(feed_result or {})
    items = [_normalize_macro_news_item(item) for item in list(result.get("items", []) or [])]
    summary_text = _normalize_text(result.get("summary_text", ""))
    payload["macro_news_status_text"] = _normalize_text(result.get("status_text", ""))
    payload["macro_news_summary_text"] = summary_text
    payload["macro_news_items"] = items
    if summary_text:
        base_summary = _normalize_text(payload.get("summary_text", ""))
        if summary_text not in base_summary:
            payload["summary_text"] = (str(payload.get("summary_text", "") or "").strip() + f"\n资讯流：{summary_text}").strip()
        base_market_text = _normalize_text(payload.get("market_text", ""))
        if summary_text not in base_market_text:
            payload["market_text"] = (str(payload.get("market_text", "") or "").strip() + f" {summary_text}").strip()
    return payload
