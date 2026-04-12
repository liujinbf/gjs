"""
提醒留痕：把关键监控提醒落到本地，便于后续复盘。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app_config import PROJECT_DIR

RUNTIME_DIR = PROJECT_DIR / ".runtime"
HISTORY_FILE = RUNTIME_DIR / "alert_history.jsonl"
MAX_HISTORY_LINES = 500


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _build_entry(category: str, title: str, detail: str, tone: str, occurred_at: str, extra: dict | None = None) -> dict:
    clean_title = _normalize_text(title)
    clean_detail = _normalize_text(detail)
    clean_tone = str(tone or "neutral").strip() or "neutral"
    payload = {
        "occurred_at": str(occurred_at or "").strip(),
        "category": str(category or "general").strip() or "general",
        "title": clean_title,
        "detail": clean_detail,
        "tone": clean_tone,
        "signature": f"{clean_title}|{clean_detail}|{clean_tone}",
    }
    if isinstance(extra, dict):
        payload.update(extra)
    return payload


def _snapshot_trade_meta(snapshot: dict) -> dict:
    return {
        "trade_grade": str(snapshot.get("trade_grade", "") or "").strip(),
        "trade_grade_detail": _normalize_text(snapshot.get("trade_grade_detail", "")),
        "trade_next_review": _normalize_text(snapshot.get("trade_next_review", "")),
    }


def build_snapshot_history_entries(snapshot: dict) -> list[dict]:
    if not isinstance(snapshot, dict):
        return []

    occurred_at = str(snapshot.get("last_refresh_text", "") or "").strip()
    entries = []
    items_by_symbol = {
        str(item.get("symbol", "") or "").strip().upper(): item
        for item in list(snapshot.get("items", []) or [])
        if str(item.get("symbol", "") or "").strip()
    }

    runtime_cards = list(snapshot.get("runtime_status_cards", []) or [])
    trade_meta = _snapshot_trade_meta(snapshot)
    if runtime_cards:
        primary = runtime_cards[0]
        tone = str(primary.get("tone", "neutral") or "neutral")
        if tone in {"negative", "warning"}:
            entries.append(
                _build_entry(
                    "mt5",
                    primary.get("title", "MT5 状态提醒"),
                    primary.get("detail", ""),
                    tone,
                    occurred_at,
                    extra=trade_meta,
                )
            )
    if len(runtime_cards) > 1:
        secondary = runtime_cards[1]
        title = str(secondary.get("title", "") or "").strip()
        if title and title not in {"市场活跃度正常"}:
            entries.append(
                _build_entry(
                    "session",
                    title,
                    secondary.get("detail", ""),
                    secondary.get("tone", "neutral"),
                    occurred_at,
                    extra=trade_meta,
                )
            )

    for card in list(snapshot.get("spread_focus_cards", []) or []):
        title = str(card.get("title", "") or "").strip()
        if not title or title == "点差状态稳定":
            continue
        symbol = title.split(" ", 1)[0].strip().upper()
        item = items_by_symbol.get(symbol, {})
        entries.append(
            _build_entry(
                "spread",
                title,
                card.get("detail", ""),
                card.get("tone", "neutral"),
                occurred_at,
                extra={
                    "symbol": symbol,
                    "baseline_latest_price": float(item.get("latest_price", 0.0) or 0.0),
                    "baseline_spread_points": float(item.get("spread_points", 0.0) or 0.0),
                    "trade_grade": str(item.get("trade_grade", "") or "").strip() or trade_meta.get("trade_grade", ""),
                    "trade_grade_detail": _normalize_text(item.get("trade_grade_detail", "")) or trade_meta.get("trade_grade_detail", ""),
                    "trade_next_review": _normalize_text(item.get("trade_next_review", "")) or trade_meta.get("trade_next_review", ""),
                },
            )
        )

    alert_text = _normalize_text(snapshot.get("alert_text", ""))
    if alert_text:
        entries.append(_build_entry("macro", "宏观提醒", alert_text, "warning", occurred_at, extra=trade_meta))

    unique_entries = []
    seen = set()
    for entry in entries:
        signature = str(entry.get("signature", "") or "").strip()
        if not signature or signature in seen:
            continue
        seen.add(signature)
        unique_entries.append(entry)
    return unique_entries


def append_history_entries(entries: list[dict], history_file: Path | None = None) -> int:
    target = Path(history_file) if history_file else HISTORY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    recent_signatures = set()
    if target.exists():
        try:
            recent_lines = [
                line.strip()
                for line in target.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ][-20:]
            for line in recent_lines:
                try:
                    recent_signatures.add(str(json.loads(line).get("signature", "") or "").strip())
                except json.JSONDecodeError:
                    continue
        except OSError:
            recent_signatures = set()

    append_lines = []
    for entry in entries or []:
        signature = str(entry.get("signature", "") or "").strip()
        if not signature or signature in recent_signatures:
            continue
        recent_signatures.add(signature)
        append_lines.append(json.dumps(entry, ensure_ascii=False))

    if not append_lines:
        return 0

    with target.open("a", encoding="utf-8") as handle:
        for line in append_lines:
            handle.write(line + "\n")

    try:
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) > MAX_HISTORY_LINES:
            trimmed = lines[-MAX_HISTORY_LINES:]
            target.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
    except OSError:
        pass

    return len(append_lines)


def read_recent_history(limit: int = 8, history_file: Path | None = None) -> list[dict]:
    target = Path(history_file) if history_file else HISTORY_FILE
    if not target.exists():
        return []

    try:
        lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []

    result = []
    for line in lines[-max(1, int(limit)):]:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return list(reversed(result))


def read_full_history(history_file: Path | None = None) -> list[dict]:
    target = Path(history_file) if history_file else HISTORY_FILE
    if not target.exists():
        return []

    try:
        lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []

    result = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return result


def _parse_occurred_at(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def summarize_recent_history(days: int = 7, history_file: Path | None = None, now: datetime | None = None) -> dict:
    history = read_full_history(history_file=history_file)
    if not history:
        return {
            "total_count": 0,
            "spread_count": 0,
            "macro_count": 0,
            "session_count": 0,
            "mt5_count": 0,
            "latest_title": "暂无异常",
            "latest_time": "--",
            "summary_text": f"最近 {max(1, int(days))} 天还没有记录到关键提醒。",
        }

    current = now or datetime.now()
    cutoff = current - timedelta(days=max(1, int(days)))
    filtered = []
    for entry in history:
        occurred_at = _parse_occurred_at(entry.get("occurred_at", ""))
        if occurred_at and occurred_at >= cutoff:
            filtered.append((occurred_at, entry))

    if not filtered:
        return {
            "total_count": 0,
            "spread_count": 0,
            "macro_count": 0,
            "session_count": 0,
            "mt5_count": 0,
            "latest_title": "暂无异常",
            "latest_time": "--",
            "summary_text": f"最近 {max(1, int(days))} 天还没有记录到关键提醒。",
        }

    filtered.sort(key=lambda item: item[0])
    latest_dt, latest_entry = filtered[-1]
    counts = {"spread": 0, "macro": 0, "session": 0, "mt5": 0}
    for _occurred_at, entry in filtered:
        category = str(entry.get("category", "general") or "general").strip()
        if category in counts:
            counts[category] += 1

    total_count = len(filtered)
    latest_title = str(latest_entry.get("title", "最近提醒") or "最近提醒").strip()
    latest_time = latest_dt.strftime("%Y-%m-%d %H:%M:%S")
    summary_text = (
        f"最近 {max(1, int(days))} 天共记录 {total_count} 条关键提醒；"
        f"点差异常 {counts['spread']} 条，宏观提醒 {counts['macro']} 条，"
        f"休市/时段提醒 {counts['session']} 条，MT5 状态提醒 {counts['mt5']} 条。"
    )
    return {
        "total_count": total_count,
        "spread_count": counts["spread"],
        "macro_count": counts["macro"],
        "session_count": counts["session"],
        "mt5_count": counts["mt5"],
        "latest_title": latest_title or "最近提醒",
        "latest_time": latest_time,
        "summary_text": summary_text,
    }


def _get_price_move_threshold_pct(symbol: str) -> float:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return 0.35
    if symbol_key.startswith("XAG"):
        return 0.60
    return 0.18


def _get_spread_warn_points(symbol: str) -> float:
    symbol_key = str(symbol or "").strip().upper()
    if symbol_key.startswith("XAU"):
        return 45.0
    if symbol_key.startswith("XAG"):
        return 80.0
    return 25.0


def summarize_effectiveness(
    snapshot: dict,
    history_file: Path | None = None,
    now: datetime | None = None,
    min_age_minutes: int = 30,
    max_age_minutes: int = 120,
) -> dict:
    items_by_symbol = {
        str(item.get("symbol", "") or "").strip().upper(): item
        for item in list((snapshot or {}).get("items", []) or [])
        if str(item.get("symbol", "") or "").strip()
    }
    history = read_full_history(history_file=history_file)
    if not history:
        return {
            "evaluated_count": 0,
            "effective_count": 0,
            "ineffective_count": 0,
            "waiting_count": 0,
            "stale_count": 0,
            "latest_title": "暂无可评估提醒",
            "latest_time": "--",
            "summary_text": "最近还没有进入评估窗口的点差异常提醒。",
        }

    snapshot_time = _parse_occurred_at((snapshot or {}).get("last_refresh_text", ""))
    current = now or snapshot_time or datetime.now()
    evaluations = []
    for entry in history:
        if str(entry.get("category", "") or "").strip() != "spread":
            continue
        occurred_at = _parse_occurred_at(entry.get("occurred_at", ""))
        symbol = str(entry.get("symbol", "") or "").strip().upper()
        if occurred_at is None or not symbol:
            continue
        age_minutes = (current - occurred_at).total_seconds() / 60.0
        if age_minutes < float(min_age_minutes):
            evaluations.append({"status": "waiting", "entry": entry, "occurred_at": occurred_at, "symbol": symbol})
            continue
        if age_minutes > float(max_age_minutes):
            evaluations.append({"status": "stale", "entry": entry, "occurred_at": occurred_at, "symbol": symbol})
            continue

        item = items_by_symbol.get(symbol, {})
        current_price = float(item.get("latest_price", 0.0) or 0.0)
        current_spread_points = float(item.get("spread_points", 0.0) or 0.0)
        baseline_price = float(entry.get("baseline_latest_price", 0.0) or 0.0)
        baseline_spread_points = float(entry.get("baseline_spread_points", 0.0) or 0.0)

        if current_price <= 0 or baseline_price <= 0:
            evaluations.append({"status": "waiting", "entry": entry, "occurred_at": occurred_at, "symbol": symbol})
            continue

        price_move_pct = abs(current_price - baseline_price) / baseline_price * 100.0
        spread_trigger = max(_get_spread_warn_points(symbol), baseline_spread_points * 0.8)
        effective = current_spread_points >= spread_trigger or price_move_pct >= _get_price_move_threshold_pct(symbol)
        evaluations.append(
            {
                "status": "effective" if effective else "ineffective",
                "entry": entry,
                "occurred_at": occurred_at,
                "symbol": symbol,
                "price_move_pct": price_move_pct,
                "current_spread_points": current_spread_points,
            }
        )

    effective_items = [item for item in evaluations if item["status"] == "effective"]
    ineffective_items = [item for item in evaluations if item["status"] == "ineffective"]
    waiting_items = [item for item in evaluations if item["status"] == "waiting"]
    stale_items = [item for item in evaluations if item["status"] == "stale"]
    evaluated_items = effective_items + ineffective_items

    if evaluated_items:
        latest_item = max(evaluated_items, key=lambda item: item["occurred_at"])
        latest_title = str(latest_item["entry"].get("title", "最近评估") or "最近评估").strip()
        latest_time = latest_item["occurred_at"].strftime("%Y-%m-%d %H:%M:%S")
    else:
        latest_title = "暂无可评估提醒"
        latest_time = "--"

    summary_text = (
        f"已进入评估窗口 {len(evaluated_items)} 条；"
        f"其中有效 {len(effective_items)} 条，无效 {len(ineffective_items)} 条，"
        f"待观察 {len(waiting_items)} 条，超窗未评估 {len(stale_items)} 条。"
    )
    if effective_items:
        top_item = max(effective_items, key=lambda item: item["occurred_at"])
        summary_text += (
            f" 最近一次有效提醒：{top_item['symbol']}，"
            f"价格偏移 {top_item.get('price_move_pct', 0.0):.3f}% ，"
            f"当前点差 {top_item.get('current_spread_points', 0.0):.0f} 点。"
        )

    return {
        "evaluated_count": len(evaluated_items),
        "effective_count": len(effective_items),
        "ineffective_count": len(ineffective_items),
        "waiting_count": len(waiting_items),
        "stale_count": len(stale_items),
        "latest_title": latest_title,
        "latest_time": latest_time,
        "summary_text": summary_text,
    }
