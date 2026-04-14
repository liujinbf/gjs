"""
AI 结构化信号入库：把每次 AI 研判的机器信号沉淀到知识库。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ai_history import _pick_summary_line
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from signal_protocol import build_empty_signal_meta, normalize_signal_meta, validate_signal_meta


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _build_snapshot_symbols(snapshot: dict) -> list[str]:
    result = []
    for item in list((snapshot or {}).get("items", []) or []):
        symbol = str(item.get("symbol", "") or "").strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def build_ai_signal_entry(result: dict, snapshot: dict, push_result: dict | None = None) -> dict:
    occurred_at = _now_text()
    content = str((result or {}).get("content", "") or "").strip()
    summary_line = _pick_summary_line(content)
    signal_meta = normalize_signal_meta((result or {}).get("signal_meta", {}) or {})
    signal_valid, signal_reason = validate_signal_meta(signal_meta)
    snapshot_symbols = _build_snapshot_symbols(snapshot)
    primary_symbol = str(signal_meta.get("symbol", "") or "").strip().upper()
    if not primary_symbol or primary_symbol == "--":
        primary_symbol = snapshot_symbols[0] if snapshot_symbols else "--"
        if signal_meta.get("action") == "neutral":
            signal_meta = build_empty_signal_meta(symbol=primary_symbol)
    push_result = push_result or {}
    snapshot_time = str((snapshot or {}).get("last_refresh_text", "") or "").strip()
    model = str((result or {}).get("model", "") or "").strip()
    signature = f"{model}|{summary_line}|{snapshot_time}"
    return {
        "signal_signature": signature,
        "occurred_at": occurred_at,
        "snapshot_time": snapshot_time,
        "snapshot_symbols_json": json.dumps(snapshot_symbols, ensure_ascii=False),
        "symbol": primary_symbol,
        "action": str(signal_meta.get("action", "neutral") or "neutral").strip().lower(),
        "entry_price": float(signal_meta.get("price", 0.0) or 0.0),
        "stop_loss": float(signal_meta.get("sl", 0.0) or 0.0),
        "take_profit": float(signal_meta.get("tp", 0.0) or 0.0),
        "signal_schema_version": str((result or {}).get("signal_schema_version", "") or "").strip(),
        "signal_meta_valid": 1 if bool((result or {}).get("signal_meta_valid", signal_valid)) else 0,
        "signal_meta_reason": _normalize_text((result or {}).get("signal_meta_reason", "") or signal_reason),
        "model": model,
        "api_base": str((result or {}).get("api_base", "") or "").strip(),
        "is_fallback": 1 if bool((result or {}).get("is_fallback", False)) else 0,
        "push_sent": 1 if bool(list(push_result.get("messages", []) or [])) else 0,
        "summary_line": summary_line,
        "content": content,
        "signal_json": json.dumps(signal_meta, ensure_ascii=False),
        "created_at": occurred_at,
    }


def record_ai_signal(
    result: dict,
    snapshot: dict,
    push_result: dict | None = None,
    db_path: Path | str | None = None,
) -> dict:
    entry = build_ai_signal_entry(result, snapshot, push_result=push_result)
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO ai_signal_events (
                signal_signature, occurred_at, snapshot_time, snapshot_symbols_json, symbol, action,
                entry_price, stop_loss, take_profit, signal_schema_version, signal_meta_valid,
                signal_meta_reason, model, api_base, is_fallback, push_sent, summary_line,
                content, signal_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry["signal_signature"],
                entry["occurred_at"],
                entry["snapshot_time"],
                entry["snapshot_symbols_json"],
                entry["symbol"],
                entry["action"],
                entry["entry_price"],
                entry["stop_loss"],
                entry["take_profit"],
                entry["signal_schema_version"],
                entry["signal_meta_valid"],
                entry["signal_meta_reason"],
                entry["model"],
                entry["api_base"],
                entry["is_fallback"],
                entry["push_sent"],
                entry["summary_line"],
                entry["content"],
                entry["signal_json"],
                entry["created_at"],
            ),
        )
        inserted = int(cursor.rowcount or 0)
        row = conn.execute(
            "SELECT id FROM ai_signal_events WHERE signal_signature = ?",
            (entry["signal_signature"],),
        ).fetchone()
    return {
        "inserted_count": inserted,
        "event_id": int(row[0]) if row else 0,
        "entry": entry,
    }


def summarize_recent_ai_signals(
    days: int = 30,
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now()
    cutoff = (current - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d %H:%M:%S")
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        total_count = int(
            conn.execute("SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ?", (cutoff,)).fetchone()[0]
        )
        valid_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ? AND signal_meta_valid = 1",
                (cutoff,),
            ).fetchone()[0]
        )
        executable_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ? AND action IN ('long', 'short')",
                (cutoff,),
            ).fetchone()[0]
        )
    return {
        "total_count": total_count,
        "valid_count": valid_count,
        "executable_count": executable_count,
        "summary_text": (
            f"最近 {max(1, int(days))} 天共沉淀 {total_count} 条 AI 结构化信号，"
            f"其中协议校验通过 {valid_count} 条，可执行方向信号 {executable_count} 条。"
        ),
    }
