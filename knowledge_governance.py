"""
规则治理层：将规则评分转成启用、观察、冻结和人工复核状态，并生成学习摘要。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from knowledge_feedback import summarize_feedback_stats


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _load_previous_learning_report(
    conn: sqlite3.Connection,
    report_type: str = "rule_digest",
) -> dict:
    row = conn.execute(
        """
        SELECT summary_text, payload_json, created_at
        FROM learning_reports
        WHERE report_type = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (_normalize_text(report_type) or "rule_digest",),
    ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["summary_text"] = _normalize_text(row["summary_text"])
    payload["created_at"] = _normalize_text(row["created_at"])
    return payload


def _decide_governance(row: sqlite3.Row) -> tuple[str, str]:
    validation_status = _normalize_text(row["validation_status"]).lower()
    sample_count = int(row["sample_count"] or 0)
    success_rate = float(row["success_rate"] or 0.0)
    score = float(row["score"] or 0.0)
    category = _normalize_text(row["category"]).lower()
    feedback_total_count = int(row["feedback_total_count"] or 0)
    feedback_negative_rate = float(row["feedback_negative_rate"] or 0.0)

    if feedback_total_count >= 3 and feedback_negative_rate >= 0.75:
        return "frozen", "用户侧负反馈已明显聚集，先冻结该规则，避免继续向外放大噪音。"
    if validation_status == "validated" and feedback_total_count >= 3 and feedback_negative_rate >= 0.60:
        return "watch", "历史样本达标，但用户反馈显示落点偏晚或噪音偏高，先降到观察名单。"

    if validation_status == "manual_review":
        return "manual_review", "该规则更偏心态、资金管理或案例经验，暂不做自动启停。"
    if validation_status == "validated":
        return "active", "样本量和评分均达标，进入当前有效规则集。"
    if validation_status == "candidate":
        if sample_count >= 3 and score >= 10.0 and success_rate >= 0.50:
            return "watch", "表现初步可用，但样本和稳定性仍需继续观察。"
        return "pending", "已有一定样本，但暂未达到观察名单的启用门槛。"
    if validation_status == "rejected":
        return "frozen", "历史表现偏弱，先从自动规则集中冻结，避免继续放大噪音。"
    if validation_status == "insufficient":
        if category in {"entry", "trend", "directional"} and sample_count > 0:
            return "watch", "已有少量样本，但仍不足以下结论，继续观察。"
        return "pending", "当前样本不足，暂不纳入自动规则集。"
    return "pending", "暂未形成明确治理动作。"


def refresh_rule_governance(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    updated_count = 0
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                rs.rule_id,
                rs.sample_count,
                rs.success_rate,
                rs.score,
                rs.validation_status,
                kr.category,
                COALESCE(rfs.total_count, 0) AS feedback_total_count,
                COALESCE(rfs.negative_rate, 0) AS feedback_negative_rate
            FROM rule_scores rs
            JOIN knowledge_rules kr ON kr.id = rs.rule_id
            LEFT JOIN rule_feedback_scores rfs ON rfs.rule_id = rs.rule_id
            WHERE rs.horizon_min = ?
            ORDER BY rs.rule_id ASC
            """,
            (int(horizon_min),),
        ).fetchall()

        now_text = _now_text()
        for row in rows:
            governance_status, rationale = _decide_governance(row)
            cursor = conn.execute(
                """
                INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                    governance_status=excluded.governance_status,
                    rationale=excluded.rationale,
                    updated_at=excluded.updated_at
                """,
                (
                    int(row["rule_id"]),
                    int(horizon_min),
                    governance_status,
                    rationale,
                    now_text,
                ),
            )
            if cursor.rowcount >= 0:
                updated_count += 1

    return {
        "updated_count": updated_count,
        "horizon_min": int(horizon_min),
    }


def summarize_rule_governance(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT governance_status, COUNT(*) AS count
            FROM rule_governance
            WHERE horizon_min = ?
            GROUP BY governance_status
            """,
            (int(horizon_min),),
        ).fetchall()
    counts = {str(row["governance_status"]): int(row["count"]) for row in rows}
    return {
        "active_count": counts.get("active", 0),
        "watch_count": counts.get("watch", 0),
        "frozen_count": counts.get("frozen", 0),
        "pending_count": counts.get("pending", 0),
        "manual_review_count": counts.get("manual_review", 0),
        "summary_text": (
            f"规则治理：启用 {counts.get('active', 0)} 条，观察 {counts.get('watch', 0)} 条，"
            f"冻结 {counts.get('frozen', 0)} 条，待积累 {counts.get('pending', 0)} 条，"
            f"人工复核 {counts.get('manual_review', 0)} 条。"
        ),
    }


def _format_rule_line(row: sqlite3.Row, prefix: str = "- ") -> str:
    return (
        f"{prefix}[{_normalize_text(row['category']).lower() or 'general'}] {_normalize_text(row['rule_text'])}"
        f"（样本 {int(row['sample_count'] or 0)}，成功率 {float(row['success_rate'] or 0.0) * 100:.0f}%，评分 {float(row['score'] or 0.0):.1f}）"
    )


def _build_governance_map(rows: list[sqlite3.Row]) -> dict[str, dict]:
    result = {}
    for row in rows:
        rule_id = str(int(row["rule_id"]))
        result[rule_id] = {
            "rule_id": int(row["rule_id"]),
            "rule_text": _normalize_text(row["rule_text"]),
            "category": _normalize_text(row["category"]).lower() or "general",
            "governance_status": _normalize_text(row["governance_status"]).lower(),
            "sample_count": int(row["sample_count"] or 0),
            "success_rate": float(row["success_rate"] or 0.0),
            "score": float(row["score"] or 0.0),
        }
    return result


def _select_status_changes(
    current_map: dict[str, dict],
    previous_map: dict[str, dict],
    target_statuses: set[str],
    previous_statuses: set[str] | None = None,
    exclude_if_previous_same: bool = True,
) -> list[dict]:
    rows = []
    for rule_id, current in current_map.items():
        current_status = _normalize_text(current.get("governance_status", "")).lower()
        previous_status = _normalize_text((previous_map.get(rule_id) or {}).get("governance_status", "")).lower()
        if current_status not in target_statuses:
            continue
        if exclude_if_previous_same and previous_status == current_status:
            continue
        if previous_statuses is not None and previous_status not in previous_statuses:
            continue
        rows.append(
            {
                **current,
                "previous_status": previous_status or "new",
            }
        )
    rows.sort(key=lambda item: (-float(item.get("score", 0.0)), -int(item.get("sample_count", 0)), int(item.get("rule_id", 0))))
    return rows


def build_learning_report(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    top_limit: int = 3,
    persist: bool = True,
) -> dict:
    with _connect(db_path) as conn:
        previous_report = _load_previous_learning_report(conn)
        rows = conn.execute(
            """
            SELECT rg.rule_id, rg.governance_status, kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score
            FROM rule_governance rg
            JOIN knowledge_rules kr ON kr.id = rg.rule_id
            JOIN rule_scores rs ON rs.rule_id = rg.rule_id AND rs.horizon_min = rg.horizon_min
            WHERE rg.horizon_min = ?
            ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
            """,
            (int(horizon_min),),
        ).fetchall()

        active_rows = [row for row in rows if _normalize_text(row["governance_status"]).lower() == "active"][: max(1, int(top_limit))]
        watch_rows = [row for row in rows if _normalize_text(row["governance_status"]).lower() == "watch"][: max(1, int(top_limit))]
        frozen_rows = [row for row in rows if _normalize_text(row["governance_status"]).lower() == "frozen"][: max(1, int(top_limit))]
        current_map = _build_governance_map(list(rows))
        previous_map = dict((previous_report.get("governance_map", {}) or {}))

    def _format(rows_: list[sqlite3.Row | dict]) -> list[str]:
        result = []
        for row in rows_:
            result.append(_format_rule_line(row))
        return result

    governance_summary = summarize_rule_governance(db_path=db_path, horizon_min=horizon_min)
    feedback_summary = summarize_feedback_stats(db_path=db_path, days=30)
    active_lines = _format(active_rows)
    watch_lines = _format(watch_rows)
    frozen_lines = _format(frozen_rows)
    promoted_rows = _select_status_changes(current_map, previous_map, {"active"})
    new_watch_rows = _select_status_changes(current_map, previous_map, {"watch"}, previous_statuses={"pending", "new"})
    new_frozen_rows = _select_status_changes(current_map, previous_map, {"frozen"})
    recovered_rows = _select_status_changes(current_map, previous_map, {"active", "watch"}, previous_statuses={"frozen"})
    promoted_lines = _format(promoted_rows[: max(1, int(top_limit))])
    new_watch_lines = _format(new_watch_rows[: max(1, int(top_limit))])
    new_frozen_lines = _format(new_frozen_rows[: max(1, int(top_limit))])
    recovered_lines = _format(recovered_rows[: max(1, int(top_limit))])

    change_parts = []
    if promoted_lines:
        change_parts.append(f"本轮新增启用 {len(promoted_rows)} 条")
    if new_watch_lines:
        change_parts.append(f"新增观察 {len(new_watch_rows)} 条")
    if new_frozen_lines:
        change_parts.append(f"新冻结 {len(new_frozen_rows)} 条")
    if recovered_lines:
        change_parts.append(f"从冻结中恢复 {len(recovered_rows)} 条")
    summary_text = (
        f"{governance_summary.get('summary_text', '')} "
        f"当前最值得优先执行的是 {len(active_lines)} 条启用规则；"
        f"需继续观察 {len(watch_lines)} 条；"
        f"已冻结 {len(frozen_lines)} 条高噪音规则。"
    ).strip()
    if change_parts:
        summary_text = f"{summary_text} 状态变化：{'，'.join(change_parts)}。".strip()
    if int(feedback_summary.get("total_count", 0) or 0) > 0:
        summary_text = f"{summary_text} {feedback_summary.get('summary_text', '')}".strip()
    payload = {
        "active_rules": active_lines,
        "watch_rules": watch_lines,
        "frozen_rules": frozen_lines,
        "promoted_rules": promoted_lines,
        "new_watch_rules": new_watch_lines,
        "new_frozen_rules": new_frozen_lines,
        "recovered_rules": recovered_lines,
        "governance_map": current_map,
        "governance_summary": governance_summary,
        "feedback_summary": feedback_summary,
        "summary_text": summary_text,
    }

    if persist:
        with _connect(db_path) as conn:
            conn.execute(
                """
                INSERT INTO learning_reports (report_type, horizon_min, summary_text, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "rule_digest",
                    int(horizon_min),
                    summary_text,
                    json.dumps(payload, ensure_ascii=False),
                    _now_text(),
                ),
            )

    return payload


def read_latest_learning_report(
    db_path: Path | str | None = None,
    report_type: str = "rule_digest",
) -> dict:
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT summary_text, payload_json, created_at
            FROM learning_reports
            WHERE report_type = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (_normalize_text(report_type) or "rule_digest",),
        ).fetchone()
    if not row:
        return {
            "summary_text": "当前还没有学习摘要。",
            "created_at": "--",
            "active_rules": [],
            "watch_rules": [],
            "frozen_rules": [],
            "promoted_rules": [],
            "new_watch_rules": [],
            "new_frozen_rules": [],
            "recovered_rules": [],
            "governance_map": {},
            "feedback_summary": summarize_feedback_stats(db_path=db_path, days=30),
        }

    try:
        payload = json.loads(str(row["payload_json"] or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload["summary_text"] = _normalize_text(row["summary_text"])
    payload["created_at"] = _normalize_text(row["created_at"])
    return payload
