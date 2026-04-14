from __future__ import annotations

import json
import re
import csv
import time
from datetime import datetime
from pathlib import Path
from urllib import error, parse, request

from app_config import PROJECT_DIR

MACRO_DATA_CACHE_FILE = PROJECT_DIR / ".runtime" / "macro_data_feed_cache.json"


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _parse_time(value: object) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        return None


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


def _cache_is_fresh(cache_payload: dict, spec_text: str, refresh_min: int, current: datetime) -> bool:
    if _normalize_text(cache_payload.get("spec_text", "")) != _normalize_text(spec_text):
        return False
    fetched_at = _parse_cache_time(cache_payload.get("fetched_at"))
    if fetched_at is None:
        return False
    age_minutes = (current - fetched_at).total_seconds() / 60.0
    return age_minutes >= 0 and age_minutes <= float(refresh_min)


def _load_text(source: str) -> str:
    source_text = str(source or "").strip()
    if source_text.lower().startswith(("http://", "https://")):
        req = request.Request(source_text, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with request.urlopen(req, timeout=8) as response:
                return response.read().decode("utf-8", errors="ignore")
        except error.HTTPError as exc:
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except error.URLError as exc:
            raise RuntimeError(f"网络错误：{exc.reason}") from exc
    path = Path(source_text).expanduser()
    if not path.is_absolute():
        path = (PROJECT_DIR / path).resolve()
    return path.read_text(encoding="utf-8")


def _fetch_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 10) -> dict:
    data = None
    request_headers = {"User-Agent": "Mozilla/5.0"}
    if isinstance(headers, dict):
        request_headers.update(headers)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json; charset=utf-8")
    req = request.Request(url=str(url).strip(), data=data, headers=request_headers, method="POST" if data else "GET")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="ignore")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"网络错误：{exc.reason}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError("宏观数据源返回的不是合法 JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("宏观数据源返回结构异常")
    return payload


def _load_specs(spec_source: str) -> list[dict]:
    text = _normalize_text(spec_source)
    if not text:
        return []
    if text.startswith("["):
        payload = json.loads(text)
    else:
        payload = json.loads(_load_text(text))
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _safe_float(value: object) -> float | None:
    text = _normalize_text(value)
    if not text or text in {".", "nan", "NaN", "None"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _format_number(value: float | None) -> str:
    if value is None:
        return "--"
    if abs(value) >= 100:
        return f"{value:.0f}"
    if abs(value) >= 10:
        return f"{value:.2f}"
    return f"{value:.3f}".rstrip("0").rstrip(".")


def _vix_annotation(vix: float) -> str:
    """将 VIX 数值转换为分级结构化标注，供 AI 直接理解市场恐慌程度。

    分级标准（参考 CBOE 历史统计）：
      < 15  : 极度乐观，市场无避险需求，对黄金利空
      15-25 : 正常波动，避险情绪平稳，对黄金中性
      25-35 : 市场紧张，避险情绪升温，对黄金偏多
      > 35  : 恐慌模式，强烈风险规避，对黄金强多
    """
    if vix < 15:
        level = "极度乐观区"
        sentiment = "市场无避险需求"
        gold_bias = "对黄金利空"
    elif vix < 25:
        level = "正常波动区"
        sentiment = "避险情绪平稳"
        gold_bias = "对黄金中性"
    elif vix < 35:
        level = "市场紧张区"
        sentiment = "避险情绪升温"
        gold_bias = "对黄金偏多"
    else:
        level = "恐慌模式"
        sentiment = "强烈风险规避"
        gold_bias = "对黄金强多⚡"
    return f"{vix:.2f}（{level}，{sentiment}，{gold_bias}）"


def _normalize_symbols(value: object) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        items = value
    else:
        items = str(value or "").replace("；", ",").replace("，", ",").split(",")
    seen = set()
    result = []
    for item in items:
        symbol = _normalize_text(item).upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _bias_text(bias_mode: str, delta: float | None, symbols: list[str]) -> str:
    if delta is None or abs(delta) < 1e-9:
        return "较前值基本持平，先结合事件和价格结构再判断。"
    clean_mode = _normalize_text(bias_mode).lower()
    scope = "/".join(symbols[:3]) if symbols else "当前观察品种"
    if clean_mode == "higher_bearish":
        return f"{scope} 在该指标上通常呈现“数值上行偏空、数值回落偏多”。"
    if clean_mode == "higher_bullish":
        return f"{scope} 在该指标上通常呈现“数值上行偏多、数值回落偏空”。"
    if clean_mode == "lower_bullish":
        return f"{scope} 在该指标上通常呈现“数值回落偏多、数值上行偏空”。"
    if clean_mode == "lower_bearish":
        return f"{scope} 在该指标上通常呈现“数值回落偏空、数值上行偏多”。"
    return "该指标更适合作为背景信息，先结合价格结构再判断。"


def _resolve_direction(bias_mode: str, delta: float | None) -> str:
    if delta is None or abs(delta) < 1e-9:
        return "neutral"
    higher = delta > 0
    clean_mode = _normalize_text(bias_mode).lower()
    if clean_mode == "higher_bearish":
        return "bearish" if higher else "bullish"
    if clean_mode == "higher_bullish":
        return "bullish" if higher else "bearish"
    if clean_mode == "lower_bullish":
        return "bearish" if higher else "bullish"
    if clean_mode == "lower_bearish":
        return "bullish" if higher else "bearish"
    return "neutral"


def _build_item(
    spec: dict,
    source: str,
    published_at: str,
    latest_value: float | None,
    previous_value: float | None,
    value_text: str = "",
) -> dict:
    symbols = _normalize_symbols(spec.get("symbols", []))
    delta = None if latest_value is None or previous_value is None else latest_value - previous_value
    direction = _resolve_direction(str(spec.get("bias_mode", "neutral") or "neutral"), delta)
    latest_value_text = value_text or _format_number(latest_value)
    if delta is None:
        delta_text = "前值不足，先看当前水平。"
    else:
        sign = "+" if delta > 0 else ""
        delta_text = f"较前值 {sign}{_format_number(delta)}"
    return {
        "name": _normalize_text(spec.get("name", "")) or "未命名宏观数据",
        "source": _normalize_text(source) or "外部宏观数据源",
        "published_at": _normalize_text(published_at),
        "latest_value": latest_value,
        "previous_value": previous_value,
        "value_text": latest_value_text,
        "delta_text": delta_text,
        "importance": _normalize_text(spec.get("importance", "medium")).lower() or "medium",
        "symbols": symbols,
        "bias_mode": _normalize_text(spec.get("bias_mode", "neutral")).lower() or "neutral",
        "direction": direction,
        "bias_text": _bias_text(str(spec.get("bias_mode", "neutral")), delta, symbols),
    }


def _load_fred_item(spec: dict, env: dict | None = None) -> dict:
    env_map = dict(env or {})
    api_key = _normalize_text(spec.get("api_key", "")) or _normalize_text(env_map.get(str(spec.get("api_key_env", "FRED_API_KEY")), ""))
    if not api_key:
        raise RuntimeError("FRED 数据源缺少 API Key")
    series_id = _normalize_text(spec.get("series_id", ""))
    if not series_id:
        raise RuntimeError("FRED 数据源缺少 series_id")
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": str(int(spec.get("limit", 2) or 2)),
    }
    url = f"https://api.stlouisfed.org/fred/series/observations?{parse.urlencode(params)}"
    payload = _fetch_json(url)
    observations = list(payload.get("observations", []) or [])
    if not observations:
        raise RuntimeError("FRED 未返回观测值")
    latest = observations[0]
    previous = observations[1] if len(observations) > 1 else {}
    return _build_item(
        spec,
        source="FRED",
        published_at=_normalize_text(latest.get("date", "")),
        latest_value=_safe_float(latest.get("value")),
        previous_value=_safe_float(previous.get("value")),
    )


def _bls_period_to_time_text(year: str, period: str) -> str:
    year_text = _normalize_text(year)
    period_text = _normalize_text(period).upper()
    if period_text.startswith("M") and len(period_text) == 3 and period_text != "M13":
        return f"{year_text}-{period_text[1:]}"
    return year_text


def _load_bls_item(spec: dict, env: dict | None = None) -> dict:
    env_map = dict(env or {})
    series_id = _normalize_text(spec.get("series_id", ""))
    if not series_id:
        raise RuntimeError("BLS 数据源缺少 series_id")
    current_year = datetime.now().year
    payload = {
        "seriesid": [series_id],
        "startyear": str(int(spec.get("start_year", current_year - 1) or current_year - 1)),
        "endyear": str(int(spec.get("end_year", current_year) or current_year)),
    }
    registration_key = _normalize_text(spec.get("registration_key", "")) or _normalize_text(
        env_map.get(str(spec.get("registration_key_env", "BLS_API_KEY")), "")
    )
    if registration_key:
        payload["registrationkey"] = registration_key
    response = _fetch_json("https://api.bls.gov/publicAPI/v2/timeseries/data/", payload=payload)
    series_rows = list((((response.get("Results", {}) or {}).get("series", [])) or []))
    if not series_rows:
        raise RuntimeError("BLS 未返回序列数据")
    data_rows = [row for row in list((series_rows[0].get("data", []) or [])) if _normalize_text(row.get("period", "")).upper() != "M13"]
    if not data_rows:
        raise RuntimeError("BLS 序列没有可用观测值")
    latest = data_rows[0]
    previous = data_rows[1] if len(data_rows) > 1 else {}
    return _build_item(
        spec,
        source="BLS",
        published_at=_bls_period_to_time_text(latest.get("year", ""), latest.get("period", "")),
        latest_value=_safe_float(latest.get("value")),
        previous_value=_safe_float(previous.get("value")),
    )


def _load_treasury_item(spec: dict, env: dict | None = None) -> dict:
    _ = env
    url = _normalize_text(spec.get("url", ""))
    if not url:
        raise RuntimeError("Treasury 数据源缺少 url")
    payload = _fetch_json(url)
    data_rows = list(payload.get("data", []) or [])
    if not data_rows:
        raise RuntimeError("Treasury 未返回数据")
    value_field = _normalize_text(spec.get("value_field", ""))
    time_field = _normalize_text(spec.get("time_field", "record_date")) or "record_date"
    if not value_field:
        raise RuntimeError("Treasury 数据源缺少 value_field")
    latest = data_rows[0]
    previous = data_rows[1] if len(data_rows) > 1 else {}
    return _build_item(
        spec,
        source=_normalize_text(spec.get("source_label", "")) or "U.S. Treasury",
        published_at=_normalize_text(latest.get(time_field, "")),
        latest_value=_safe_float(latest.get(value_field)),
        previous_value=_safe_float(previous.get(value_field)),
    )


def _load_generic_json_item(spec: dict, env: dict | None = None) -> dict:
    _ = env
    url = _normalize_text(spec.get("url", ""))
    if not url:
        raise RuntimeError("generic_json 数据源缺少 url")
    payload = _fetch_json(url)

    # 增强：支持点号路径提取数据列表
    data_key = str(spec.get("data_key", "data")).strip()
    data_rows = payload
    if data_key and data_key != ".":
        for part in data_key.split("."):
            if isinstance(data_rows, dict) and part in data_rows:
                data_rows = data_rows[part]
            elif isinstance(data_rows, list) and part.isdigit():
                idx = int(part)
                if 0 <= idx < len(data_rows):
                    data_rows = data_rows[idx]
                else:
                    data_rows = []
                    break
            else:
                data_rows = []
                break

    if not isinstance(data_rows, list) or not data_rows:
        raise RuntimeError(f"generic_json 在路径 '{data_key}' 未返回有效列表")

    latest = data_rows[0]
    previous = data_rows[1] if len(data_rows) > 1 else {}
    value_field = _normalize_text(spec.get("value_field", "value")) or "value"
    time_field = _normalize_text(spec.get("time_field", "date")) or "date"

    return _build_item(
        spec,
        source=_normalize_text(spec.get("source_label", "")) or "外部 JSON 数据源",
        published_at=_normalize_text(latest.get(time_field, "")),
        latest_value=_safe_float(latest.get(value_field)),
        previous_value=_safe_float(previous.get(value_field)),
        value_text=_normalize_text(latest.get(value_field, "")),
    )


def _load_alphavantage_item(spec: dict, env: dict | None = None) -> dict:
    env_map = dict(env or {})
    api_key = _normalize_text(spec.get("api_key", "")) or _normalize_text(
        env_map.get(str(spec.get("api_key_env", "ALPHAVANTAGE_API_KEY")), "")
    )
    if not api_key:
        raise RuntimeError("Alpha Vantage 数据源缺少 API Key")

    func = _normalize_text(spec.get("function", ""))
    if not func:
        raise RuntimeError("Alpha Vantage 数据源缺少 function 参数")

    params = {
        "function": func,
        "apikey": api_key,
    }
    # 可选：interval
    interval = _normalize_text(spec.get("interval", ""))
    if interval:
        params["interval"] = interval

    url = f"https://www.alphavantage.co/query?{parse.urlencode(params)}"
    payload = _fetch_json(url)

    # Alpha Vantage 宏观指标通常返回 { "name": "...", "data": [...] }
    data_rows = list(payload.get("data", []) or [])
    if not data_rows:
        # 有些接口可能返回 Note 或 Error Message
        if "Note" in payload:
            raise RuntimeError(f"Alpha Vantage 额度限制: {payload['Note']}")
        if "Error Message" in payload:
            raise RuntimeError(f"Alpha Vantage 错误: {payload['Error Message']}")
        raise RuntimeError("Alpha Vantage 未返回有效数据列表")

    latest = data_rows[0]
    previous = data_rows[1] if len(data_rows) > 1 else {}

    return _build_item(
        spec,
        source="Alpha Vantage",
        published_at=_normalize_text(latest.get("date", "")),
        latest_value=_safe_float(latest.get("value")),
        previous_value=_safe_float(previous.get("value")),
    )


def _load_yfinance_item(spec: dict, env: dict | None = None) -> dict:
    """轻量级 Yahoo Finance 拉取，使用 query1 API 获取 chart 数据。"""
    _ = env
    symbol = _normalize_text(spec.get("symbol", ""))
    if not symbol:
        raise RuntimeError("Yahoo Finance 数据源缺少 symbol")

    # 获取最近 5 天的数据以确保有前值
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{parse.quote(symbol)}?interval=1d&range=5d"
    payload = _fetch_json(url)

    result_set = payload.get("chart", {}).get("result", [])
    if not result_set:
        raise RuntimeError(f"Yahoo Finance 未返回 {symbol} 的数据")

    res = result_set[0]
    timestamps = list(res.get("timestamp", []) or [])
    indicators = res.get("indicators", {}).get("quote", [{}])[0]
    closes = list(indicators.get("close", []) or [])

    # 过滤掉 None 值
    valid_data = []
    for i in range(len(timestamps)):
        if i < len(closes) and closes[i] is not None:
            valid_data.append((timestamps[i], closes[i]))

    if not valid_data:
        raise RuntimeError(f"Yahoo Finance 未返回 {symbol} 的有效收盘价")

    # 时间降序排列
    valid_data.sort(key=lambda x: x[0], reverse=True)
    latest_ts, latest_val = valid_data[0]
    prev_val = valid_data[1][1] if len(valid_data) > 1 else None

    published_at = datetime.fromtimestamp(latest_ts).strftime("%Y-%m-%d")

    return _build_item(
        spec,
        source="Yahoo Finance",
        published_at=published_at,
        latest_value=latest_val,
        previous_value=prev_val,
        value_text=_vix_annotation(latest_val) if symbol == "^VIX" else "",
    )


def _load_generic_csv_item(spec: dict, env: dict | None = None) -> dict:
    _ = env
    url = _normalize_text(spec.get("url", ""))
    if not url:
        raise RuntimeError("generic_csv 数据源缺少 url")

    # 下载 CSV 并解析
    req = request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=10) as response:
            content = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        raise RuntimeError(f"CSV 下载失败: {exc}")

    lines = content.strip().splitlines()
    if not lines:
        raise RuntimeError("CSV 内容为空")

    reader = csv.DictReader(lines)
    data_rows = list(reader)
    if not data_rows:
        raise RuntimeError("CSV 解析后无数据行")

    # CSV 通常是时间升序，我们需要最新的
    # 如果没指定排序列，假设最后一行最新（常见于价格序列）；如果指定了日期列，则排序。
    date_col = _normalize_text(spec.get("time_field", ""))
    if date_col and date_col in data_rows[0]:
        data_rows.sort(key=lambda x: str(x[date_col]), reverse=True)
    else:
        # 默认假设最后一行是最新数据，反转列表
        data_rows.reverse()

    latest = data_rows[0]
    previous = data_rows[1] if len(data_rows) > 1 else {}
    value_col = _normalize_text(spec.get("value_field", ""))
    if not value_col or value_col not in latest:
        # 如果没指定列名，尝试取第二列（通常第一列是日期/代码）
        keys = list(latest.keys())
        value_col = keys[1] if len(keys) > 1 else keys[0]

    return _build_item(
        spec,
        source=_normalize_text(spec.get("source_label", "")) or "外部 CSV 数据源",
        published_at=_normalize_text(latest.get(date_col, "")) if date_col else "",
        latest_value=_safe_float(latest.get(value_col)),
        previous_value=_safe_float(previous.get(value_col)),
    )


def _load_worldbank_item(spec: dict, env: dict | None = None) -> dict:
    """从 WorldBank REST API 拉取数据，例如 CPI、失业率。格式: [meta, [records...]]"""
    url = _normalize_text(spec.get("url", ""))
    if not url:
        raise RuntimeError("worldbank 数据源缺少 url")
    req = request.Request(url + "&mrv=2", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with request.urlopen(req, timeout=8) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        raise RuntimeError(f"WorldBank 请求失败: {exc}") from exc
    if not isinstance(raw, list) or len(raw) < 2 or not isinstance(raw[1], list):
        raise RuntimeError("WorldBank 响应格式异常")
    records = [r for r in raw[1] if r.get("value") is not None]
    if not records:
        raise RuntimeError("WorldBank 无有效观测值")
    latest = records[0]
    previous = records[1] if len(records) > 1 else {}
    return _build_item(
        spec,
        source=_normalize_text(spec.get("source_label", "")) or "World Bank",
        published_at=_normalize_text(latest.get("date", "")),
        latest_value=_safe_float(latest.get("value")),
        previous_value=_safe_float(previous.get("value")),
    )


PROVIDER_LOADERS = {
    "fred": _load_fred_item,
    "bls": _load_bls_item,
    "treasury": _load_treasury_item,
    "worldbank": _load_worldbank_item,
    "generic_json": _load_generic_json_item,
    "generic_csv": _load_generic_csv_item,
    "alphavantage": _load_alphavantage_item,
    "yfinance": _load_yfinance_item,
}


def _score_item(item: dict, watch_symbols: list[str] | None = None) -> int:
    score = 0
    importance = _normalize_text(item.get("importance", "")).lower()
    if importance == "high":
        score += 3
    elif importance == "medium":
        score += 2
    else:
        score += 1
    target_symbols = {str(symbol or "").strip().upper() for symbol in list(watch_symbols or []) if str(symbol or "").strip()}
    item_symbols = {str(symbol or "").strip().upper() for symbol in list(item.get("symbols", []) or []) if str(symbol or "").strip()}
    if target_symbols and item_symbols.intersection(target_symbols):
        score += 3
    elif not item_symbols:
        score += 1
    if _normalize_text(item.get("direction", "")).lower() in {"bullish", "bearish"}:
        score += 1
    return score


def _build_digest(items: list[dict]) -> str:
    if not items:
        return "结构化宏观数据层当前暂无高相关更新。"
    parts = []
    for item in items[:3]:
        direction = _normalize_text(item.get("direction", "")).lower()
        direction_text = "偏多" if direction == "bullish" else ("偏空" if direction == "bearish" else "中性")
        parts.append(
            f"{_normalize_text(item.get('name', ''))} {str(item.get('value_text', '--') or '--').strip()}"
            f"（{_normalize_text(item.get('delta_text', ''))}，{direction_text}）"
        )
    return f"结构化宏观数据：近一轮高相关数据包括 {'；'.join(parts)}。"


def load_macro_data_feed(
    enabled: bool,
    spec_source: str,
    refresh_min: int,
    symbols: list[str] | None = None,
    now: datetime | None = None,
    cache_file: Path | None = None,
    env: dict | None = None,
    cache_only: bool = False,
) -> dict:
    current = now or datetime.now()
    cache_path = Path(cache_file) if cache_file else MACRO_DATA_CACHE_FILE
    clean_spec_source = str(spec_source or "").strip()
    safe_refresh_min = max(5, int(refresh_min or 60))
    watch_symbols = [str(item or "").strip().upper() for item in list(symbols or []) if str(item or "").strip()]

    if not bool(enabled):
        return {
            "enabled": False,
            "status": "disabled",
            "status_text": "结构化宏观数据层未开启。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }
    if not clean_spec_source:
        return {
            "enabled": True,
            "status": "missing",
            "status_text": "结构化宏观数据层已开启，但尚未配置数据源规格。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }

    cached = _read_cache(cache_path)
    if bool(cache_only):
        if _normalize_text(cached.get("spec_text", "")) == clean_spec_source and _parse_cache_time(cached.get("fetched_at")) is not None:
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            items = list(cached.get("items", []) or [])
            return {
                "enabled": True,
                "status": "cache_only",
                "status_text": f"结构化宏观数据本地缓存载入：{len(items)} 条，{_format_age_text(current, fetched_at)}同步。",
                "item_count": len(items),
                "summary_text": _normalize_text(cached.get("summary_text", "")),
                "items": items,
                "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
            }
        return {
            "enabled": True,
            "status": "cache_missing",
            "status_text": "结构化宏观数据等待后台同步，本地尚无可用缓存。",
            "item_count": 0,
            "summary_text": "",
            "items": [],
            "fetched_at_text": "",
        }
    if _cache_is_fresh(cached, clean_spec_source, safe_refresh_min, current):
        fetched_at = _parse_cache_time(cached.get("fetched_at"))
        items = list(cached.get("items", []) or [])
        return {
            "enabled": True,
            "status": "cache",
            "status_text": f"结构化宏观数据缓存生效：{len(items)} 条，{_format_age_text(current, fetched_at)}更新。",
            "items": items,
            "summary_text": _normalize_text(cached.get("summary_text", "")),
            "item_count": len(items),
            "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
        }

    specs = _load_specs(clean_spec_source)
    if not specs:
        if _normalize_text(cached.get("spec_text", "")) == clean_spec_source and list(cached.get("items", []) or []):
            fetched_at = _parse_cache_time(cached.get("fetched_at"))
            items = list(cached.get("items", []) or [])
            return {
                "enabled": True,
                "status": "stale_cache",
                "status_text": f"结构化宏观数据规格为空，继续使用{_format_age_text(current, fetched_at)}缓存：{len(items)} 条。",
                "items": items,
                "summary_text": _normalize_text(cached.get("summary_text", "")),
                "item_count": len(items),
                "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
            }
        return {
            "enabled": True,
            "status": "missing",
            "status_text": "结构化宏观数据层未解析到有效数据源规格。",
            "items": [],
            "summary_text": "",
            "item_count": 0,
        }

    items = []
    errors = []
    env_map = dict(env or {})
    for spec in specs:
        provider = _normalize_text(spec.get("provider", "") or spec.get("type", "")).lower()
        loader = PROVIDER_LOADERS.get(provider)
        if not loader:
            errors.append(f"{provider or 'unknown'}: 暂不支持")
            continue
        try:
            item = loader(spec, env=env_map)
            item_symbols = {str(symbol or "").strip().upper() for symbol in list(item.get("symbols", []) or []) if str(symbol or "").strip()}
            if watch_symbols and item_symbols and not item_symbols.intersection(set(watch_symbols)):
                continue
            items.append(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{_normalize_text(spec.get('name', provider or 'unknown'))}: {_normalize_text(exc)}")

    ranked_items = sorted(
        items,
        key=lambda item: (
            -_score_item(item, watch_symbols=watch_symbols),
            _normalize_text(item.get("published_at", "")),
            _normalize_text(item.get("name", "")),
        ),
        reverse=True,
    )[:6]
    summary_text = _build_digest(ranked_items)
    cache_payload = {
        "spec_text": clean_spec_source,
        "fetched_at": current.isoformat(timespec="seconds"),
        "fetched_at_text": current.strftime("%Y-%m-%d %H:%M:%S"),
        "items": ranked_items,
        "summary_text": summary_text,
    }
    if ranked_items:
        _write_cache(cache_path, cache_payload)
        return {
            "enabled": True,
            "status": "fresh",
            "status_text": f"结构化宏观数据已同步：{len(ranked_items)} 条。",
            "items": ranked_items,
            "summary_text": summary_text,
            "item_count": len(ranked_items),
            "fetched_at_text": cache_payload["fetched_at_text"],
            "error_text": "；".join(errors),
        }

    if _normalize_text(cached.get("spec_text", "")) == clean_spec_source and list(cached.get("items", []) or []):
        fetched_at = _parse_cache_time(cached.get("fetched_at"))
        items = list(cached.get("items", []) or [])
        return {
            "enabled": True,
            "status": "stale_cache",
            "status_text": f"结构化宏观数据拉取失败，继续使用{_format_age_text(current, fetched_at)}缓存：{len(items)} 条。",
            "items": items,
            "summary_text": _normalize_text(cached.get("summary_text", "")),
            "item_count": len(items),
            "fetched_at_text": _normalize_text(cached.get("fetched_at_text", "")),
            "error_text": "；".join(errors),
        }

    return {
        "enabled": True,
        "status": "error",
        "status_text": f"结构化宏观数据拉取失败：{_normalize_text('；'.join(errors) or '未知错误')}",
        "items": [],
        "summary_text": "",
        "item_count": 0,
        "error_text": "；".join(errors),
    }


def apply_macro_data_to_snapshot(snapshot: dict, feed_result: dict) -> dict:
    payload = dict(snapshot or {})
    result = dict(feed_result or {})
    items = list(result.get("items", []) or [])
    summary_text = _normalize_text(result.get("summary_text", ""))
    payload["macro_data_status_text"] = _normalize_text(result.get("status_text", ""))
    payload["macro_data_summary_text"] = summary_text
    payload["macro_data_items"] = items
    if summary_text:
        base_summary = _normalize_text(payload.get("summary_text", ""))
        if summary_text not in base_summary:
            payload["summary_text"] = (str(payload.get("summary_text", "") or "").strip() + f"\n宏观数据：{summary_text}").strip()
        base_market_text = _normalize_text(payload.get("market_text", ""))
        if summary_text not in base_market_text:
            payload["market_text"] = (str(payload.get("market_text", "") or "").strip() + f" {summary_text}").strip()
    return payload
