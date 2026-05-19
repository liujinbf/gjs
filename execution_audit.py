from __future__ import annotations

import json
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from quote_models import SnapshotItem
from signal_protocol import normalize_signal_meta


_BLOCK_DIAGNOSTIC_META_KEYS = {
    "block_reason_key",
    "block_reason_label",
    "block_reason_text",
    "block_secondary_reason_key",
    "block_secondary_reason_label",
    "block_tertiary_reason_key",
    "block_tertiary_reason_label",
    "block_direction_components",
    "block_direction_components_json",
}

_GRADE_GATE_SECONDARY_LABELS = {
    "event_gate": "事件窗口",
    "source_gate": "非结构型信号",
    "grade_not_observe": "结构等级偏低",
    "rr_not_ready": "盈亏比未准备好",
    "rr_too_low": "RR不足",
    "risk_reward_state_bad": "盈亏比状态不佳",
    "multi_timeframe_misaligned": "多周期未同向",
    "direction_unclear": "方向不清晰",
    "target_incomplete": "止损目标不完整",
    "entry_zone_miss": "未回到执行区",
    "chasing_upper": "上沿追价",
    "chasing_lower": "下沿追空",
    "unknown": "待继续细分",
}

_RR_NOT_READY_TERTIARY_LABELS = {
    "no_price": "现价缺失",
    "no_direction": "方向基础不足",
    "atr_missing_no_key_levels": "ATR缺失且关键位不足",
    "key_range_invalid": "关键位区间无效",
    "price_span_too_small": "止损目标跨度过小",
    "entry_zone_missing": "入场区间未生成",
    "unknown": "待继续细分",
}

_NO_DIRECTION_COMPONENT_LABELS = {
    "signal_side_missing": "信号方向缺失",
    "intraday_sideways": "日内方向震荡",
    "multi_not_aligned": "多周期未同向",
    "breakout_direction_neutral": "突破方向中性",
    "breakout_state_none": "突破未确认",
    "retest_state_none": "回踩未确认",
}

_GRADE_GATE_SECONDARY_KEY_BY_LABEL = {label: key for key, label in _GRADE_GATE_SECONDARY_LABELS.items()}


def _now_text() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    return SnapshotItem.from_payload(item).to_dict()


def _merge_block_diagnostic_meta(normalized_meta: dict, raw_meta: dict | None) -> dict:
    merged = dict(normalized_meta or {})
    source = dict(raw_meta or {})
    for key in _BLOCK_DIAGNOSTIC_META_KEYS:
        if key in source and key not in merged:
            merged[key] = source[key]
    return merged


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
    raw_meta = dict(meta or {})
    normalized_meta = _merge_block_diagnostic_meta(normalize_signal_meta(raw_meta), raw_meta)
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


def _load_meta_json(raw_json: object) -> dict:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _increment_key_count(
    key_counts: dict[str, int],
    key_labels: dict[str, str],
    key: str,
    label: str,
    fallback_labels: dict[str, str],
) -> None:
    clean_key = _normalize_text(key).lower()
    if not clean_key:
        return
    clean_label = _normalize_text(label) or fallback_labels.get(clean_key, clean_key)
    key_counts[clean_key] = int(key_counts.get(clean_key, 0) or 0) + 1
    key_labels[clean_key] = clean_label


def _top_key_rows(key_counts: dict[str, int], key_labels: dict[str, str], limit: int) -> list[dict]:
    return [
        {
            "reason_key": key,
            "reason_label": key_labels.get(key, key),
            "count": int(count),
        }
        for key, count in sorted(key_counts.items(), key=lambda item: (-int(item[1]), str(key_labels.get(item[0], item[0]))))[
            : max(1, int(limit or 3))
        ]
    ]


def _extract_grade_gate_secondary_from_text(reason_text: str) -> tuple[str, str]:
    text = _normalize_text(reason_text)
    if "细分：" not in text:
        return "", ""
    detail = text.split("细分：", 1)[1].strip()
    for marker in ("）", ")", "。", "；", ";", "，", ","):
        if marker in detail:
            detail = detail.split(marker, 1)[0].strip()
    key = _GRADE_GATE_SECONDARY_KEY_BY_LABEL.get(detail, "")
    return key, detail


def _iter_direction_components(meta: dict) -> list[dict]:
    components = meta.get("block_direction_components", [])
    if not isinstance(components, list):
        components = []
    if not components:
        raw_json = _normalize_text(meta.get("block_direction_components_json", ""))
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except (TypeError, ValueError, json.JSONDecodeError):
                parsed = []
            if isinstance(parsed, list):
                components = parsed
    normalized = []
    for item in components:
        if isinstance(item, dict):
            normalized.append(item)
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            normalized.append({"reason_key": item[0], "reason_label": item[1]})
    return normalized


def summarize_execution_block_diagnostics(
    *,
    days: int = 30,
    hours: int = 0,
    source_kind: str = "",
    symbol: str = "",
    statuses: tuple[str, ...] = ("blocked", "rejected", "skipped"),
    limit: int = 3,
    db_path: Path | str | None = None,
) -> dict:
    from datetime import datetime, timedelta

    clean_statuses = tuple(_normalize_text(status).lower() for status in tuple(statuses or ()) if _normalize_text(status))
    if not clean_statuses:
        return {
            "total_count": 0,
            "grade_gate_count": 0,
            "secondary_counts": {},
            "secondary_label_counts": {},
            "tertiary_counts": {},
            "tertiary_label_counts": {},
            "direction_component_counts": {},
            "direction_component_label_counts": {},
            "top_secondary_labels": [],
            "top_tertiary_labels": [],
            "top_direction_components": [],
        }
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

    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT reason_key, reason_text, meta_json
            FROM execution_audits
            WHERE occurred_at >= ?
              AND {' AND '.join(filters)}
            ORDER BY occurred_at DESC, id DESC
            """,
            tuple(params),
        ).fetchall()

    secondary_counts: dict[str, int] = {}
    secondary_labels: dict[str, str] = {}
    tertiary_counts: dict[str, int] = {}
    tertiary_labels: dict[str, str] = {}
    component_counts: dict[str, int] = {}
    component_labels: dict[str, str] = {}
    grade_gate_count = 0

    for row in rows:
        meta = _load_meta_json(row["meta_json"])
        reason_key = _normalize_text(row["reason_key"]).lower()
        block_reason_key = _normalize_text(meta.get("block_reason_key", "")).lower() or reason_key
        if block_reason_key != "grade_gate":
            continue
        grade_gate_count += 1

        secondary_key = _normalize_text(meta.get("block_secondary_reason_key", "")).lower()
        secondary_label = _normalize_text(meta.get("block_secondary_reason_label", ""))
        if not secondary_key and not secondary_label:
            secondary_key, secondary_label = _extract_grade_gate_secondary_from_text(str(row["reason_text"] or ""))
        if secondary_label and not secondary_key:
            secondary_key = _GRADE_GATE_SECONDARY_KEY_BY_LABEL.get(secondary_label, "")
        _increment_key_count(
            secondary_counts,
            secondary_labels,
            secondary_key,
            secondary_label,
            _GRADE_GATE_SECONDARY_LABELS,
        )

        tertiary_key = _normalize_text(meta.get("block_tertiary_reason_key", "")).lower()
        tertiary_label = _normalize_text(meta.get("block_tertiary_reason_label", ""))
        _increment_key_count(
            tertiary_counts,
            tertiary_labels,
            tertiary_key,
            tertiary_label,
            _RR_NOT_READY_TERTIARY_LABELS,
        )

        for component in _iter_direction_components(meta):
            component_key = _normalize_text(component.get("reason_key", "")).lower()
            component_label = _normalize_text(component.get("reason_label", ""))
            _increment_key_count(
                component_counts,
                component_labels,
                component_key,
                component_label,
                _NO_DIRECTION_COMPONENT_LABELS,
            )

    return {
        "total_count": len(rows),
        "grade_gate_count": grade_gate_count,
        "secondary_counts": dict(secondary_counts),
        "secondary_label_counts": {secondary_labels.get(key, key): count for key, count in secondary_counts.items()},
        "tertiary_counts": dict(tertiary_counts),
        "tertiary_label_counts": {tertiary_labels.get(key, key): count for key, count in tertiary_counts.items()},
        "direction_component_counts": dict(component_counts),
        "direction_component_label_counts": {component_labels.get(key, key): count for key, count in component_counts.items()},
        "top_secondary_labels": _top_key_rows(secondary_counts, secondary_labels, limit),
        "top_tertiary_labels": _top_key_rows(tertiary_counts, tertiary_labels, limit),
        "top_direction_components": _top_key_rows(component_counts, component_labels, limit),
    }


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
            SELECT occurred_at, symbol, action, source_kind, decision_status, reason_key, reason_text, meta_json
            FROM execution_audits
            WHERE occurred_at >= ?{extra_sql}
            ORDER BY occurred_at DESC, id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
    result = []
    for row in rows:
        meta = _load_meta_json(row["meta_json"])
        result.append(
            {
                "occurred_at": str(row["occurred_at"] or "").strip(),
                "symbol": str(row["symbol"] or "").strip().upper(),
                "action": str(row["action"] or "").strip().lower(),
                "source_kind": str(row["source_kind"] or "").strip(),
                "decision_status": str(row["decision_status"] or "").strip().lower(),
                "reason_key": str(row["reason_key"] or "").strip().lower(),
                "reason_text": str(row["reason_text"] or "").strip(),
                "block_secondary_reason_key": _normalize_text(meta.get("block_secondary_reason_key", "")).lower(),
                "block_secondary_reason_label": _normalize_text(meta.get("block_secondary_reason_label", "")),
                "block_tertiary_reason_key": _normalize_text(meta.get("block_tertiary_reason_key", "")).lower(),
                "block_tertiary_reason_label": _normalize_text(meta.get("block_tertiary_reason_label", "")),
            }
        )
    return result
