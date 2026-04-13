"""
用户反馈学习层：记录用户对提醒质量的主观反馈，并聚合到规则层。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

FEEDBACK_SCORE_MAP = {
    "helpful": 1.0,
    "unhelpful": -1.0,
    "too_late": -0.8,
    "noise": -1.0,
    "risky": -1.2,
}

FEEDBACK_LABEL_ALIASES = {
    "helpful": "helpful",
    "有帮助": "helpful",
    "有用": "helpful",
    "命中": "helpful",
    "unhelpful": "unhelpful",
    "没帮助": "unhelpful",
    "无帮助": "unhelpful",
    "没用": "unhelpful",
    "too_late": "too_late",
    "太晚": "too_late",
    "太晚了": "too_late",
    "延迟": "too_late",
    "noise": "noise",
    "噪音": "noise",
    "打扰": "noise",
    "risky": "risky",
    "太激进": "risky",
    "风险太高": "risky",
    "不该做": "risky",
}


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _parse_time(value: str) -> datetime | None:
    text = _normalize_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def normalize_feedback_label(label: str) -> str:
    return FEEDBACK_LABEL_ALIASES.get(_normalize_text(label).lower(), "unhelpful")


def _feedback_score_for_label(label: str) -> float:
    return float(FEEDBACK_SCORE_MAP.get(normalize_feedback_label(label), -1.0))


def _resolve_snapshot(
    conn: sqlite3.Connection,
    symbol: str = "",
    snapshot_time: str = "",
    snapshot_id: int | None = None,
    tolerance_min: int = 180,
) -> sqlite3.Row | None:
    if snapshot_id:
        return conn.execute(
            """
            SELECT id, symbol, snapshot_time
            FROM market_snapshots
            WHERE id = ?
            """,
            (int(snapshot_id),),
        ).fetchone()

    symbol_text = _normalize_text(symbol).upper()
    if not symbol_text:
        return None

    if not _normalize_text(snapshot_time):
        return conn.execute(
            """
            SELECT id, symbol, snapshot_time
            FROM market_snapshots
            WHERE symbol = ?
            ORDER BY snapshot_time DESC, id DESC
            LIMIT 1
            """,
            (symbol_text,),
        ).fetchone()

    target_time = _parse_time(snapshot_time)
    if target_time is None:
        return None

    rows = conn.execute(
        """
        SELECT id, symbol, snapshot_time
        FROM market_snapshots
        WHERE symbol = ?
        ORDER BY snapshot_time DESC, id DESC
        LIMIT 200
        """,
        (symbol_text,),
    ).fetchall()
    best_row = None
    best_delta = None
    tolerance = timedelta(minutes=max(1, int(tolerance_min)))
    for row in rows:
        row_time = _parse_time(row["snapshot_time"])
        if row_time is None:
            continue
        delta = abs(row_time - target_time)
        if delta > tolerance:
            continue
        if best_delta is None or delta < best_delta:
            best_row = row
            best_delta = delta
    return best_row


def record_user_feedback(
    symbol: str,
    feedback_label: str,
    snapshot_time: str = "",
    snapshot_id: int | None = None,
    feedback_text: str = "",
    source: str = "manual",
    db_path: Path | str | None = None,
    now: datetime | None = None,
) -> dict:
    created_at = _now_text(now=now)
    clean_label = normalize_feedback_label(feedback_label)
    clean_symbol = _normalize_text(symbol).upper()
    clean_feedback_text = _normalize_text(feedback_text)
    clean_source = _normalize_text(source) or "manual"
    with _connect(db_path) as conn:
        snapshot = _resolve_snapshot(
            conn,
            symbol=clean_symbol,
            snapshot_time=snapshot_time,
            snapshot_id=snapshot_id,
        )
        if not snapshot:
            return {
                "inserted_count": 0,
                "feedback_id": None,
                "snapshot_id": None,
                "error": "未找到可关联的市场快照，当前反馈未入库。",
            }

        resolved_snapshot_id = int(snapshot["id"])
        resolved_symbol = _normalize_text(snapshot["symbol"]).upper()
        resolved_snapshot_time = _normalize_text(snapshot["snapshot_time"])
        signature_base = "|".join(
            [
                str(resolved_snapshot_id),
                clean_label,
                clean_source,
                clean_feedback_text,
            ]
        )
        signature = hashlib.sha1(signature_base.encode("utf-8")).hexdigest()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO user_feedback (
                snapshot_id, symbol, snapshot_time, feedback_label, feedback_score, feedback_text, source, signature, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_snapshot_id,
                resolved_symbol,
                resolved_snapshot_time,
                clean_label,
                _feedback_score_for_label(clean_label),
                clean_feedback_text,
                clean_source,
                signature,
                created_at,
            ),
        )
        row = conn.execute(
            "SELECT id FROM user_feedback WHERE signature = ?",
            (signature,),
        ).fetchone()

    return {
        "inserted_count": 1 if cursor.rowcount > 0 else 0,
        "feedback_id": int(row["id"]) if row else None,
        "snapshot_id": resolved_snapshot_id,
        "symbol": resolved_symbol,
        "snapshot_time": resolved_snapshot_time,
        "feedback_label": clean_label,
    }


def refresh_rule_feedback_scores(
    db_path: Path | str | None = None,
) -> dict:
    updated_count = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                rm.rule_id AS rule_id,
                SUM(CASE WHEN uf.feedback_label = 'helpful' THEN 1 ELSE 0 END) AS helpful_count,
                SUM(CASE WHEN uf.feedback_label = 'unhelpful' THEN 1 ELSE 0 END) AS unhelpful_count,
                SUM(CASE WHEN uf.feedback_label = 'too_late' THEN 1 ELSE 0 END) AS too_late_count,
                SUM(CASE WHEN uf.feedback_label = 'noise' THEN 1 ELSE 0 END) AS noise_count,
                SUM(CASE WHEN uf.feedback_label = 'risky' THEN 1 ELSE 0 END) AS risky_count,
                COUNT(*) AS total_count,
                COALESCE(SUM(uf.feedback_score), 0) AS total_score
            FROM user_feedback uf
            JOIN rule_snapshot_matches rm ON rm.snapshot_id = uf.snapshot_id
            GROUP BY rm.rule_id
            ORDER BY rm.rule_id ASC
            """
        ).fetchall()
        updated_rule_ids = set()
        now_text = _now_text()
        for row in rows:
            helpful_count = int(row["helpful_count"] or 0)
            unhelpful_count = int(row["unhelpful_count"] or 0)
            too_late_count = int(row["too_late_count"] or 0)
            noise_count = int(row["noise_count"] or 0)
            risky_count = int(row["risky_count"] or 0)
            total_count = int(row["total_count"] or 0)
            positive_rate = helpful_count / total_count if total_count > 0 else 0.0
            negative_count = unhelpful_count + too_late_count + noise_count + risky_count
            negative_rate = negative_count / total_count if total_count > 0 else 0.0
            score = (float(row["total_score"] or 0.0) / total_count * 100.0) if total_count > 0 else 0.0
            conn.execute(
                """
                INSERT INTO rule_feedback_scores (
                    rule_id, helpful_count, unhelpful_count, too_late_count, noise_count, risky_count,
                    total_count, positive_rate, negative_rate, score, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    helpful_count=excluded.helpful_count,
                    unhelpful_count=excluded.unhelpful_count,
                    too_late_count=excluded.too_late_count,
                    noise_count=excluded.noise_count,
                    risky_count=excluded.risky_count,
                    total_count=excluded.total_count,
                    positive_rate=excluded.positive_rate,
                    negative_rate=excluded.negative_rate,
                    score=excluded.score,
                    updated_at=excluded.updated_at
                """,
                (
                    int(row["rule_id"]),
                    helpful_count,
                    unhelpful_count,
                    too_late_count,
                    noise_count,
                    risky_count,
                    total_count,
                    float(positive_rate),
                    float(negative_rate),
                    float(score),
                    now_text,
                ),
            )
            updated_count += 1
            updated_rule_ids.add(int(row["rule_id"]))

        if updated_rule_ids:
            placeholders = ",".join("?" for _ in updated_rule_ids)
            conn.execute(
                f"DELETE FROM rule_feedback_scores WHERE rule_id NOT IN ({placeholders})",
                tuple(sorted(updated_rule_ids)),
            )
        else:
            conn.execute("DELETE FROM rule_feedback_scores")

    return {
        "updated_count": updated_count,
    }


def summarize_feedback_stats(
    db_path: Path | str | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> dict:
    current = now or datetime.now()
    cutoff = current - timedelta(days=max(1, int(days)))
    cutoff_text = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT feedback_label, COUNT(*) AS count
            FROM user_feedback
            WHERE created_at >= ?
            GROUP BY feedback_label
            """,
            (cutoff_text,),
        ).fetchall()
        top_positive_rows = conn.execute(
            """
            SELECT kr.rule_text, kr.category, rfs.total_count, rfs.positive_rate, rfs.negative_rate, rfs.score
            FROM rule_feedback_scores rfs
            JOIN knowledge_rules kr ON kr.id = rfs.rule_id
            WHERE rfs.total_count > 0
            ORDER BY rfs.score DESC, rfs.total_count DESC, kr.id ASC
            LIMIT 3
            """
        ).fetchall()
        top_negative_rows = conn.execute(
            """
            SELECT kr.rule_text, kr.category, rfs.total_count, rfs.positive_rate, rfs.negative_rate, rfs.score
            FROM rule_feedback_scores rfs
            JOIN knowledge_rules kr ON kr.id = rfs.rule_id
            WHERE rfs.total_count > 0
            ORDER BY rfs.score ASC, rfs.total_count DESC, kr.id ASC
            LIMIT 3
            """
        ).fetchall()

    counts = {str(row["feedback_label"]): int(row["count"]) for row in rows}
    total_count = sum(counts.values())

    def _format_rule_rows(items: list[sqlite3.Row]) -> list[str]:
        return [
            f"- [{_normalize_text(row['category']).lower() or 'general'}] {_normalize_text(row['rule_text'])}"
            f"（反馈 {int(row['total_count'] or 0)} 次，正向 {float(row['positive_rate'] or 0.0) * 100:.0f}%，"
            f"负向 {float(row['negative_rate'] or 0.0) * 100:.0f}%，反馈分 {float(row['score'] or 0.0):.1f}）"
            for row in items
        ]

    return {
        "total_count": total_count,
        "helpful_count": counts.get("helpful", 0),
        "unhelpful_count": counts.get("unhelpful", 0),
        "too_late_count": counts.get("too_late", 0),
        "noise_count": counts.get("noise", 0),
        "risky_count": counts.get("risky", 0),
        "top_positive_rules": _format_rule_rows(list(top_positive_rows)),
        "top_negative_rules": _format_rule_rows(list(top_negative_rows)),
        "summary_text": (
            f"最近 {max(1, int(days))} 天共收到 {total_count} 条用户反馈，"
            f"其中有帮助 {counts.get('helpful', 0)} 条，没帮助 {counts.get('unhelpful', 0)} 条，"
            f"太晚 {counts.get('too_late', 0)} 条，噪音 {counts.get('noise', 0)} 条，"
            f"风险过高 {counts.get('risky', 0)} 条。"
        ),
    }
