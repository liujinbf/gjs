from __future__ import annotations

import json
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from quote_models import SnapshotItem
from signal_protocol import normalize_signal_meta


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    return SnapshotItem.from_payload(item).to_dict()


def _classify_reason_key(result_message: str, decision_status: str) -> str:
    text = _normalize_text(result_message)
    status = _normalize_text(decision_status).lower()
    if not text and status == "opened":
        return "opened"
    if status == "closed":
        if "保本" in text:
            return "break_even_exit"
        if "止盈" in text or "目标" in text:
            return "take_profit"
        if "爆仓" in text:
            return "margin_call"
        if "止损" in text:
            return "stop_loss"
        return "closed"
    if "已有活跃持仓" in text:
        return "existing_position"
    if "保证金不足" in text:
        return "margin_insufficient"
    if "缺失点位数据" in text:
        return "meta_incomplete"
    if "非明确执行信号" in text:
        return "direction_unclear"
    if "未输出机器信号" in text:
        return "no_machine_signal"
    if "中性" in text or "neutral" in text:
        return "neutral_signal"
    if "默认不自动发射实盘单" in text:
        return "live_auto_disabled"
    if status == "blocked":
        return "blocked"
    if status == "skipped":
        return "skipped"
    if status == "rejected":
        return "engine_rejected"
    return ""


def _resolve_symbol(meta: dict, snapshot: dict | None = None) -> str:
    symbol = _normalize_text(meta.get("symbol", "")).upper()
    if symbol:
        return symbol
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        symbol = _normalize_text(item.get("symbol", "")).upper()
        if symbol:
            return symbol
    return ""


def _resolve_snapshot_id(conn, snapshot_time: str, symbol: str) -> int:
    if not snapshot_time or not symbol:
        return 0
    row = conn.execute(
        """
        SELECT id
        FROM market_snapshots
        WHERE snapshot_time = ? AND symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (snapshot_time, symbol),
    ).fetchone()
    return int(row["id"]) if row else 0


def resolve_snapshot_binding(
    *,
    snapshot: dict | None = None,
    symbol: str = "",
    db_path: Path | str | None = None,
) -> int:
    snapshot_time = _normalize_text((snapshot or {}).get("last_refresh_text", ""))
    clean_symbol = _normalize_text(symbol).upper()
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        return _resolve_snapshot_id(conn, snapshot_time, clean_symbol)


def record_execution_audit(
    *,
    source_kind: str,
    decision_status: str,
    snapshot: dict | None = None,
    snapshot_id: int = 0,
    meta: dict | None = None,
    signal_signature: str = "",
    result_message: str = "",
    reason_key: str = "",
    trade_mode: str = "simulation",
    user_id: str = "system",
    db_path: Path | str | None = None,
) -> dict:
    normalized_meta = normalize_signal_meta(dict(meta or {}))
    snapshot_time = _normalize_text((snapshot or {}).get("last_refresh_text", ""))
    symbol = _resolve_symbol(normalized_meta, snapshot=snapshot)
    action = _normalize_text(normalized_meta.get("action", "neutral")).lower() or "neutral"
    clean_reason_text = _normalize_text(result_message)
    clean_reason_key = _normalize_text(reason_key).lower() or _classify_reason_key(clean_reason_text, decision_status)
    occurred_at = _now_text()

    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        bound_snapshot_id = int(snapshot_id or 0)
        if bound_snapshot_id <= 0:
            bound_snapshot_id = _resolve_snapshot_id(conn, snapshot_time, symbol)
        cursor = conn.execute(
            """
            INSERT INTO execution_audits (
                occurred_at, snapshot_time, snapshot_id, signal_signature, symbol, action,
                source_kind, trade_mode, decision_status, reason_key, reason_text, user_id,
                entry_price, stop_loss, take_profit, meta_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                occurred_at,
                snapshot_time,
                bound_snapshot_id,
                _normalize_text(signal_signature),
                symbol,
                action,
                _normalize_text(source_kind),
                _normalize_text(trade_mode) or "simulation",
                _normalize_text(decision_status),
                clean_reason_key,
                clean_reason_text,
                _normalize_text(user_id) or "system",
                float(normalized_meta.get("price", 0.0) or 0.0),
                float(normalized_meta.get("sl", 0.0) or 0.0),
                float(normalized_meta.get("tp", 0.0) or 0.0),
                json.dumps(normalized_meta, ensure_ascii=False),
                occurred_at,
            ),
        )
    return {
        "audit_id": int(cursor.lastrowid or 0),
        "snapshot_id": bound_snapshot_id,
        "symbol": symbol,
        "action": action,
        "decision_status": _normalize_text(decision_status),
        "reason_key": clean_reason_key,
        "reason_text": clean_reason_text,
    }


def summarize_execution_audits(
    *,
    days: int = 30,
    hours: int = 0,
    source_kind: str = "",
    symbol: str = "",
    db_path: Path | str | None = None,
) -> dict:
    from datetime import datetime, timedelta

    if int(hours or 0) > 0:
        cutoff_dt = datetime.now() - timedelta(hours=max(1, int(hours)))
    else:
        cutoff_dt = datetime.now() - timedelta(days=max(1, int(days)))
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    params: list[object] = [cutoff]
    filters: list[str] = []
    clean_source = _normalize_text(source_kind)
    if clean_source:
        filters.append("source_kind = ?")
        params.append(clean_source)
    clean_symbol = _normalize_text(symbol).upper()
    if clean_symbol:
        filters.append("symbol = ?")
        params.append(clean_symbol)
    extra_sql = ""
    if filters:
        extra_sql = " AND " + " AND ".join(filters)
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT decision_status, COUNT(*) AS count
            FROM execution_audits
            WHERE occurred_at >= ?{extra_sql}
            GROUP BY decision_status
            """,
            tuple(params),
        ).fetchall()
    counts = {str(row["decision_status"]): int(row["count"]) for row in rows}
    return {
        "total_count": sum(counts.values()),
        "counts": counts,
    }


def summarize_today_execution_audits(
    *,
    now=None,
    source_kind: str = "",
    trade_mode: str = "",
    symbol: str = "",
    db_path: Path | str | None = None,
) -> dict:
    from datetime import datetime, timedelta

    current = now or datetime.now()
    day_start_dt = current.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_dt = day_start_dt + timedelta(days=1)
    params: list[object] = [
        day_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        day_end_dt.strftime("%Y-%m-%d %H:%M:%S"),
    ]
    filters: list[str] = []
    clean_source = _normalize_text(source_kind)
    if clean_source:
        filters.append("source_kind = ?")
        params.append(clean_source)
    clean_trade_mode = _normalize_text(trade_mode)
    if clean_trade_mode:
        filters.append("trade_mode = ?")
        params.append(clean_trade_mode)
    clean_symbol = _normalize_text(symbol).upper()
    if clean_symbol:
        filters.append("symbol = ?")
        params.append(clean_symbol)
    extra_sql = ""
    if filters:
        extra_sql = " AND " + " AND ".join(filters)
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        status_rows = conn.execute(
            f"""
            SELECT decision_status, COUNT(*) AS count
            FROM execution_audits
            WHERE occurred_at >= ? AND occurred_at < ?{extra_sql}
            GROUP BY decision_status
            """,
            tuple(params),
        ).fetchall()
        reason_rows = conn.execute(
            f"""
            SELECT COALESCE(NULLIF(reason_key, ''), 'unknown') AS reason_key, COUNT(*) AS count
            FROM execution_audits
            WHERE occurred_at >= ? AND occurred_at < ?{extra_sql}
            GROUP BY COALESCE(NULLIF(reason_key, ''), 'unknown')
            """,
            tuple(params),
        ).fetchall()
    counts = {str(row["decision_status"] or "").strip().lower(): int(row["count"] or 0) for row in status_rows}
    reason_counts = {str(row["reason_key"] or "").strip().lower(): int(row["count"] or 0) for row in reason_rows}
    return {
        "date": day_start_dt.strftime("%Y-%m-%d"),
        "total_count": sum(counts.values()),
        "counts": counts,
        "reason_counts": reason_counts,
    }


def summarize_execution_reason_counts(
    *,
    days: int = 30,
    hours: int = 0,
    source_kind: str = "",
    symbol: str = "",
    statuses: tuple[str, ...] = ("blocked", "rejected", "skipped"),
    limit: int = 3,
    db_path: Path | str | None = None,
) -> list[dict]:
    from datetime import datetime, timedelta

    clean_statuses = tuple(_normalize_text(status).lower() for status in tuple(statuses or ()) if _normalize_text(status))
    if not clean_statuses:
        return []
    if int(hours or 0) > 0:
        cutoff_dt = datetime.now() - timedelta(hours=max(1, int(hours)))
    else:
        cutoff_dt = datetime.now() - timedelta(days=max(1, int(days)))
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    params: list[object] = [cutoff]
    filters = [f"decision_status IN ({','.join(['?'] * len(clean_statuses))})"]
    params.extend(clean_statuses)
    clean_source = _normalize_text(source_kind)
    if clean_source:
        filters.append("source_kind = ?")
        params.append(clean_source)
    clean_symbol = _normalize_text(symbol).upper()
    if clean_symbol:
        filters.append("symbol = ?")
        params.append(clean_symbol)
    params.append(max(1, int(limit)))
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT
                COALESCE(NULLIF(reason_key, ''), 'unknown') AS reason_key,
                COALESCE(NULLIF(reason_text, ''), '未写入原因') AS reason_text,
                COUNT(*) AS count
            FROM execution_audits
            WHERE occurred_at >= ?
              AND {' AND '.join(filters)}
            GROUP BY COALESCE(NULLIF(reason_key, ''), 'unknown'), COALESCE(NULLIF(reason_text, ''), '未写入原因')
            ORDER BY count DESC, reason_key ASC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "reason_key": str(row["reason_key"] or "").strip().lower(),
            "reason_text": str(row["reason_text"] or "").strip(),
            "count": int(row["count"] or 0),
        }
        for row in rows
    ]


def fetch_recent_execution_audits(
    *,
    days: int = 30,
    hours: int = 0,
    source_kind: str = "",
    symbol: str = "",
    limit: int = 5,
    db_path: Path | str | None = None,
) -> list[dict]:
    from datetime import datetime, timedelta

    if int(hours or 0) > 0:
        cutoff_dt = datetime.now() - timedelta(hours=max(1, int(hours)))
    else:
        cutoff_dt = datetime.now() - timedelta(days=max(1, int(days)))
    cutoff = cutoff_dt.strftime("%Y-%m-%d %H:%M:%S")
    params: list[object] = [cutoff]
    filters: list[str] = []
    clean_source = _normalize_text(source_kind)
    if clean_source:
        filters.append("source_kind = ?")
        params.append(clean_source)
    clean_symbol = _normalize_text(symbol).upper()
    if clean_symbol:
        filters.append("symbol = ?")
        params.append(clean_symbol)
    params.append(max(1, int(limit)))
    extra_sql = ""
    if filters:
        extra_sql = " AND " + " AND ".join(filters)
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT occurred_at, symbol, action, source_kind, decision_status, reason_key, reason_text
            FROM execution_audits
            WHERE occurred_at >= ?{extra_sql}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    return [
        {
            "occurred_at": str(row["occurred_at"] or "").strip(),
            "symbol": str(row["symbol"] or "").strip().upper(),
            "action": str(row["action"] or "").strip().lower(),
            "source_kind": str(row["source_kind"] or "").strip(),
            "decision_status": str(row["decision_status"] or "").strip().lower(),
            "reason_key": str(row["reason_key"] or "").strip().lower(),
            "reason_text": str(row["reason_text"] or "").strip(),
        }
        for row in rows
    ]
