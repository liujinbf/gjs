"""
AI 结构化信号入库：把每次 AI 研判的机器信号沉淀到知识库。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from ai_signal_audit import resolve_ai_signal_execution_audit
from ai_history import _pick_summary_line
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from quote_models import SnapshotItem
from signal_protocol import build_empty_signal_meta, normalize_signal_meta, validate_signal_meta


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一 AI 信号入库链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _build_snapshot_symbols(snapshot: dict) -> list[str]:
    result = []
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        symbol = str(item.get("symbol", "") or "").strip().upper()
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def _build_snapshot_from_market_row(row) -> dict:
    feature_payload = {}
    try:
        feature_payload = json.loads(str(row["feature_json"] or "{}"))
    except Exception:
        feature_payload = {}
    item = {
        **feature_payload,
        "symbol": _normalize_text(row["symbol"]).upper(),
        "latest_price": float(row["latest_price"] or 0.0),
        "spread_points": float(row["spread_points"] or 0.0),
        "has_live_quote": bool(row["has_live_quote"]),
        "tone": _normalize_text(row["tone"]),
        "trade_grade": _normalize_text(row["trade_grade"]),
        "trade_grade_source": _normalize_text(row["trade_grade_source"]),
        "alert_state_text": _normalize_text(row["alert_state_text"]),
        "event_risk_mode_text": _normalize_text(row["event_risk_mode_text"]),
        "event_active_name": _normalize_text(row["event_active_name"]),
        "event_importance_text": _normalize_text(row["event_importance_text"]),
        "event_note": _normalize_text(row["event_note"]),
        "signal_side": _normalize_text(row["signal_side"]).lower(),
        "regime_tag": _normalize_text(row["regime_tag"]),
        "regime_text": _normalize_text(row["regime_text"]),
    }
    return {
        "last_refresh_text": _normalize_text(row["snapshot_time"]),
        "items": [item],
    }


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
    execution_audit = resolve_ai_signal_execution_audit(snapshot, symbol=primary_symbol)
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
        "used_structured_payload": 1 if bool((result or {}).get("used_structured_payload", False)) else 0,
        "ai_parse_mode": _normalize_text((result or {}).get("ai_parse_mode", "")),
        "ai_raw_response_logged": 1 if bool((result or {}).get("ai_raw_response_logged", False)) else 0,
        "ai_raw_response_length": int((result or {}).get("ai_raw_response_length", 0) or 0),
        "ai_raw_response_excerpt": str((result or {}).get("ai_raw_response_excerpt", "") or "")[:500],
        "model": model,
        "api_base": str((result or {}).get("api_base", "") or "").strip(),
        "is_fallback": 1 if bool((result or {}).get("is_fallback", False)) else 0,
        "push_sent": 1 if bool(list(push_result.get("messages", []) or [])) else 0,
        "summary_line": summary_line,
        "content": content,
        "signal_json": json.dumps(signal_meta, ensure_ascii=False),
        "snapshot_trade_grade": execution_audit["trade_grade"],
        "snapshot_trade_grade_source": execution_audit["trade_grade_source"],
        "snapshot_signal_side": execution_audit["snapshot_signal_side"] or "neutral",
        "snapshot_has_live_quote": 1 if execution_audit["has_live_quote"] else 0,
        "sim_eligible": 1 if execution_audit["sim_eligible"] else 0,
        "sim_block_reason": execution_audit["sim_block_reason"],
        "sim_block_reason_key": execution_audit["sim_block_reason_key"],
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
                signal_meta_reason, used_structured_payload, ai_parse_mode, ai_raw_response_logged,
                ai_raw_response_length, ai_raw_response_excerpt, model, api_base, is_fallback,
                push_sent, summary_line, content, signal_json, snapshot_trade_grade, snapshot_trade_grade_source,
                snapshot_signal_side, snapshot_has_live_quote, sim_eligible, sim_block_reason,
                sim_block_reason_key, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                entry["used_structured_payload"],
                entry["ai_parse_mode"],
                entry["ai_raw_response_logged"],
                entry["ai_raw_response_length"],
                entry["ai_raw_response_excerpt"],
                entry["model"],
                entry["api_base"],
                entry["is_fallback"],
                entry["push_sent"],
                entry["summary_line"],
                entry["content"],
                entry["signal_json"],
                entry["snapshot_trade_grade"],
                entry["snapshot_trade_grade_source"],
                entry["snapshot_signal_side"],
                entry["snapshot_has_live_quote"],
                entry["sim_eligible"],
                entry["sim_block_reason"],
                entry["sim_block_reason_key"],
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
        sim_eligible_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ? AND sim_eligible = 1",
                (cutoff,),
            ).fetchone()[0]
        )
        fallback_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ? AND is_fallback = 1",
                (cutoff,),
            ).fetchone()[0]
        )
        structured_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM ai_signal_events WHERE occurred_at >= ? AND used_structured_payload = 1",
                (cutoff,),
            ).fetchone()[0]
        )
    return {
        "total_count": total_count,
        "valid_count": valid_count,
        "executable_count": executable_count,
        "sim_eligible_count": sim_eligible_count,
        "fallback_count": fallback_count,
        "structured_count": structured_count,
        "summary_text": (
            f"最近 {max(1, int(days))} 天共沉淀 {total_count} 条 AI 结构化信号，"
            f"其中协议校验通过 {valid_count} 条，可执行方向信号 {executable_count} 条，"
            f"规则链允许试仓 {sim_eligible_count} 条；"
            f"结构化解析 {structured_count} 条，降级 {fallback_count} 条。"
        ),
    }


def backfill_ai_signal_execution_audit(
    db_path: Path | str | None = None,
    limit: int | None = None,
) -> dict:
    updated_count = 0
    scanned_count = 0
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        sql = """
            SELECT ase.id, ase.symbol, ase.snapshot_time
            FROM ai_signal_events ase
            WHERE ase.action IN ('long', 'short')
              AND (
                    ase.snapshot_trade_grade = ''
                 OR ase.snapshot_trade_grade_source = ''
                 OR ase.sim_block_reason_key = ''
              )
            ORDER BY ase.id ASC
        """
        params: tuple[object, ...] = ()
        if limit is not None and int(limit) > 0:
            sql += " LIMIT ?"
            params = (int(limit),)
        rows = conn.execute(sql, params).fetchall()
        for row in rows:
            scanned_count += 1
            market_row = conn.execute(
                """
                SELECT snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
                       trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
                       event_active_name, event_importance_text, event_note, signal_side,
                       regime_tag, regime_text, feature_json
                FROM market_snapshots
                WHERE snapshot_time = ? AND symbol = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (_normalize_text(row["snapshot_time"]), _normalize_text(row["symbol"]).upper()),
            ).fetchone()
            if market_row is None:
                continue
            audit = resolve_ai_signal_execution_audit(
                _build_snapshot_from_market_row(market_row),
                symbol=_normalize_text(row["symbol"]).upper(),
            )
            conn.execute(
                """
                UPDATE ai_signal_events
                SET snapshot_trade_grade = ?,
                    snapshot_trade_grade_source = ?,
                    snapshot_signal_side = ?,
                    snapshot_has_live_quote = ?,
                    sim_eligible = ?,
                    sim_block_reason = ?,
                    sim_block_reason_key = ?
                WHERE id = ?
                """,
                (
                    audit["trade_grade"],
                    audit["trade_grade_source"],
                    audit["snapshot_signal_side"] or "neutral",
                    1 if audit["has_live_quote"] else 0,
                    1 if audit["sim_eligible"] else 0,
                    audit["sim_block_reason"],
                    audit["sim_block_reason_key"],
                    int(row["id"]),
                ),
            )
            updated_count += 1
    return {
        "scanned_count": scanned_count,
        "updated_count": updated_count,
    }
