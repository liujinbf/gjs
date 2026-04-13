"""
AI 研判留痕：记录最近一次模型结论与推送结果，便于后续复盘。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from app_config import PROJECT_DIR
from runtime_utils import parse_time as _parse_time_impl

RUNTIME_DIR = PROJECT_DIR / ".runtime"
AI_HISTORY_FILE = RUNTIME_DIR / "ai_brief_history.jsonl"
MAX_AI_HISTORY_LINES = 200


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _pick_summary_line(content: str) -> str:
    preferred_prefixes = ("当前结论：", "方向判断：", "风险点：", "行动建议：")
    fallback_lines = []
    for line in str(content or "").splitlines():
        text = _normalize_text(line)
        if not text:
            continue
        fallback_lines.append(text)
        for prefix in preferred_prefixes:
            if text.startswith(prefix):
                return text
    return fallback_lines[0] if fallback_lines else ""


def build_ai_history_entry(result: dict, snapshot: dict, push_result: dict | None = None) -> dict:
    content = str((result or {}).get("content", "") or "").strip()
    model = str((result or {}).get("model", "") or "").strip() or "unknown"
    symbols = [
        str(item.get("symbol", "") or "").strip().upper()
        for item in list((snapshot or {}).get("items", []) or [])
        if str(item.get("symbol", "") or "").strip()
    ]
    push_result = push_result or {}
    push_messages = [_normalize_text(item) for item in list(push_result.get("messages", []) or []) if _normalize_text(item)]
    push_errors = [_normalize_text(item) for item in list(push_result.get("errors", []) or []) if _normalize_text(item)]
    return {
        "occurred_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": model,
        "symbols": symbols,
        "summary_line": _pick_summary_line(content),
        "content": content,
        "rulebook_summary_text": _normalize_text((result or {}).get("rulebook_summary_text", "")),
        "snapshot_time": str((snapshot or {}).get("last_refresh_text", "") or "").strip(),
        "status_hint": _normalize_text((snapshot or {}).get("status_hint", "")),
        "alert_text": _normalize_text((snapshot or {}).get("alert_text", "")),
        "push_sent": bool(push_messages),
        "push_messages": push_messages,
        "push_errors": push_errors,
        "signature": f"{model}|{_pick_summary_line(content)}|{str((snapshot or {}).get('last_refresh_text', '') or '').strip()}",
    }


def append_ai_history_entry(entry: dict, history_file: Path | None = None) -> int:
    target = Path(history_file) if history_file else AI_HISTORY_FILE
    target.parent.mkdir(parents=True, exist_ok=True)

    signature = str((entry or {}).get("signature", "") or "").strip()
    if not signature:
        return 0

    recent_signatures = set()
    if target.exists():
        try:
            recent_lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()][-20:]
            for line in recent_lines:
                try:
                    recent_signatures.add(str(json.loads(line).get("signature", "") or "").strip())
                except json.JSONDecodeError:
                    continue
        except OSError:
            recent_signatures = set()

    if signature in recent_signatures:
        return 0

    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    try:
        lines = [line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) > MAX_AI_HISTORY_LINES:
            target.write_text("\n".join(lines[-MAX_AI_HISTORY_LINES:]) + "\n", encoding="utf-8")
    except OSError:
        pass

    return 1


def read_recent_ai_history(limit: int = 5, history_file: Path | None = None) -> list[dict]:
    target = Path(history_file) if history_file else AI_HISTORY_FILE
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


# P-004 修复：局部 _parse_time 委托给公共 runtime_utils.parse_time，消除重复定义
def _parse_time(value: str) -> datetime | None:
    return _parse_time_impl(value)


def summarize_recent_ai_history(days: int = 7, history_file: Path | None = None, now: datetime | None = None) -> dict:
    rows = read_recent_ai_history(limit=MAX_AI_HISTORY_LINES, history_file=history_file)
    if not rows:
        return {
            "total_count": 0,
            "push_count": 0,
            "latest_model": "--",
            "latest_time": "--",
            "latest_summary": "最近还没有 AI 研判记录。",
            "summary_text": f"最近 {max(1, int(days))} 天还没有 AI 研判留痕。",
        }

    current = now or datetime.now()
    cutoff = current - timedelta(days=max(1, int(days)))
    filtered = []
    for row in rows:
        occurred_at = _parse_time(row.get("occurred_at", ""))
        if occurred_at and occurred_at >= cutoff:
            filtered.append((occurred_at, row))

    if not filtered:
        return {
            "total_count": 0,
            "push_count": 0,
            "latest_model": "--",
            "latest_time": "--",
            "latest_summary": "最近还没有 AI 研判记录。",
            "summary_text": f"最近 {max(1, int(days))} 天还没有 AI 研判留痕。",
        }

    filtered.sort(key=lambda item: item[0])
    latest_dt, latest_row = filtered[-1]
    total_count = len(filtered)
    push_count = sum(1 for _dt, row in filtered if bool(row.get("push_sent")))
    latest_model = str(latest_row.get("model", "--") or "--").strip()
    latest_summary = str(latest_row.get("summary_line", "最近一次 AI 研判未返回摘要。") or "最近一次 AI 研判未返回摘要。").strip()
    return {
        "total_count": total_count,
        "push_count": push_count,
        "latest_model": latest_model,
        "latest_time": latest_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "latest_summary": latest_summary,
        "summary_text": f"最近 {max(1, int(days))} 天共记录 {total_count} 次 AI 研判，其中 {push_count} 次已发送到外部提醒渠道。",
    }
