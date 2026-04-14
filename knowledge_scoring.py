"""
知识规则评分：将候选规则映射到运行时样本，并按结果回标给出验证分层。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, kv_get, kv_set, open_knowledge_connection

SUPPORTED_RUNTIME_CATEGORIES = {"entry", "trend", "directional"}
SKIP_RULE_MARKERS = {
    "来源标注",
    "来源列表",
    "快速参考卡",
    "最后提醒",
    "仅供参考",
    "风险提示",
    "说明",
    "适用场景",
}
DOMAIN_KEYWORDS = [
    "支撑位",
    "压力位",
    "回调",
    "企稳",
    "回踩",
    "突破",
    "确认",
    "假突破",
    "上破",
    "下破",
    "均线",
    "多头",
    "空头",
    "偏多",
    "偏空",
    "多周期",
    "MACD",
    "RSI",
    "KDJ",
    "盈亏比",
    "顺势",
    "逆势",
    "轻仓",
    "黄金",
    "白银",
    "金银比",
    "做多",
    "做空",
    "事件前",
    "高影响",
]
MATCH_STATE_KV_KEY = "rule_match_state"


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _load_feature_text(feature_json: str) -> str:
    try:
        payload = json.loads(str(feature_json or "{}"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return " ".join(_normalize_text(item) for item in payload.values() if _normalize_text(item))


def _asset_scope_matches(rule_scope: str, symbol: str) -> bool:
    scope = _normalize_text(rule_scope).upper()
    symbol_key = _normalize_text(symbol).upper()
    if not scope or scope == "ALL":
        return True
    targets = {item.strip().upper() for item in scope.split(",") if item.strip()}
    return not targets or symbol_key in targets


def _should_skip_rule(rule_text: str, category: str) -> bool:
    text = _normalize_text(rule_text)
    if not text or len(text) < 8:
        return True
    if any(marker in text for marker in SKIP_RULE_MARKERS):
        return True
    if text.endswith("：") or text.endswith(":"):
        return True
    if category not in SUPPORTED_RUNTIME_CATEGORIES:
        return True
    return False


def _extract_keywords(rule_text: str) -> list[str]:
    text = _normalize_text(rule_text)
    keywords = [item for item in DOMAIN_KEYWORDS if item in text]
    if not keywords:
        for token in [part.strip() for part in text.replace("，", " ").replace("。", " ").replace("；", " ").split()]:
            token_text = _normalize_text(token)
            if 2 <= len(token_text) <= 8:
                keywords.append(token_text)
    seen = set()
    result = []
    for item in keywords:
        token = _normalize_text(item)
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result[:6]


def _snapshot_text(snapshot: sqlite3.Row) -> str:
    text_parts = [
        _normalize_text(snapshot["trade_grade"]),
        _normalize_text(snapshot["trade_grade_source"]),
        _normalize_text(snapshot["alert_state_text"]),
        _normalize_text(snapshot["event_risk_mode_text"]),
        _normalize_text(snapshot["event_active_name"]),
        _normalize_text(snapshot["event_importance_text"]),
        _normalize_text(snapshot["event_note"]),
        _normalize_text(snapshot["regime_tag"]),
        _normalize_text(snapshot["regime_text"]),
        _normalize_text(snapshot["signal_side"]),
        _load_feature_text(str(snapshot["feature_json"] or "{}")),
    ]
    return " ".join(item for item in text_parts if item)


def _rule_direction_hint(rule_text: str) -> str:
    text = _normalize_text(rule_text)
    if any(keyword in text for keyword in ("做多", "多头", "偏多", "上破", "回踩确认", "顺势做多")):
        return "long"
    if any(keyword in text for keyword in ("做空", "空头", "偏空", "下破", "反抽确认", "顺势做空")):
        return "short"
    return "neutral"


def _rule_matches_snapshot(rule: sqlite3.Row, snapshot: sqlite3.Row) -> tuple[bool, str]:
    rule_text = _normalize_text(rule["rule_text"])
    category = _normalize_text(rule["category"]).lower()
    if _should_skip_rule(rule_text, category):
        return False, ""
    if not _asset_scope_matches(str(rule["asset_scope"] or ""), str(snapshot["symbol"] or "")):
        return False, ""

    snapshot_trade_grade = _normalize_text(snapshot["trade_grade"])
    snapshot_side = _normalize_text(snapshot["signal_side"]).lower()
    snapshot_text = _snapshot_text(snapshot)
    direction_hint = _rule_direction_hint(rule_text)

    if category in {"entry", "trend", "directional"} and snapshot_trade_grade != "可轻仓试仓":
        return False, ""
    if category == "directional" and snapshot_side == "neutral":
        return False, ""
    if direction_hint in {"long", "short"} and snapshot_side not in {direction_hint, "neutral"}:
        return False, ""

    keywords = _extract_keywords(rule_text)
    matched_keywords = [item for item in keywords if item and item in snapshot_text]
    if matched_keywords:
        required = 1 if len(keywords) <= 2 else 2
        if len(matched_keywords) >= required or category == "directional":
            return True, f"keyword:{','.join(matched_keywords[:3])}"

    if category == "trend":
        if any(keyword in snapshot_text for keyword in ("突破", "回踩", "多周期", "支撑位", "压力位")):
            return True, "category:trend"
    if category == "entry":
        if any(keyword in snapshot_text for keyword in ("轻仓", "盈亏比", "企稳", "回调", "回踩")):
            return True, "category:entry"
    if category == "directional":
        if snapshot_side in {"long", "short"}:
            return True, f"direction:{snapshot_side}"

    return False, ""


def _normalize_id_list(values: list[int] | tuple[int, ...] | None) -> list[int]:
    result = []
    seen = set()
    for item in list(values or []):
        try:
            value = int(item or 0)
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _fetch_rules(conn: sqlite3.Connection, rule_ids: list[int] | None = None) -> list[sqlite3.Row]:
    if rule_ids is not None and not rule_ids:
        return []
    if rule_ids:
        placeholders = ",".join("?" for _ in rule_ids)
        return conn.execute(
            f"""
            SELECT id, category, asset_scope, rule_text
            FROM knowledge_rules
            WHERE id IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(rule_ids),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, category, asset_scope, rule_text
        FROM knowledge_rules
        ORDER BY id ASC
        """
    ).fetchall()


def _fetch_snapshots(conn: sqlite3.Connection, snapshot_ids: list[int] | None = None) -> list[sqlite3.Row]:
    if snapshot_ids is not None and not snapshot_ids:
        return []
    if snapshot_ids:
        placeholders = ",".join("?" for _ in snapshot_ids)
        return conn.execute(
            f"""
            SELECT id, symbol, trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
                   event_active_name, event_importance_text, event_note, regime_tag, regime_text, signal_side, feature_json
            FROM market_snapshots
            WHERE id IN ({placeholders})
            ORDER BY id ASC
            """,
            tuple(snapshot_ids),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, symbol, trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
               event_active_name, event_importance_text, event_note, regime_tag, regime_text, signal_side, feature_json
        FROM market_snapshots
        ORDER BY id ASC
        """
    ).fetchall()


def _insert_rule_snapshot_matches(
    conn: sqlite3.Connection,
    rules: list[sqlite3.Row],
    snapshots: list[sqlite3.Row],
) -> int:
    matched_count = 0
    if not rules or not snapshots:
        return matched_count
    now_text = _now_text()
    for snapshot in snapshots:
        for rule in rules:
            is_match, matched_by = _rule_matches_snapshot(rule, snapshot)
            if not is_match:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO rule_snapshot_matches (rule_id, snapshot_id, symbol, matched_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(rule["id"]),
                    int(snapshot["id"]),
                    _normalize_text(snapshot["symbol"]).upper(),
                    matched_by,
                    now_text,
                ),
            )
            if cursor.rowcount > 0:
                matched_count += 1
    return matched_count


def match_rules_to_snapshots(
    db_path: Path | str | None = None,
    snapshot_ids: list[int] | tuple[int, ...] | None = None,
) -> dict:
    matched_count = 0
    new_snapshot_match_count = 0
    new_rule_backfill_count = 0
    explicit_snapshot_ids = _normalize_id_list(snapshot_ids)
    match_state = dict(kv_get(MATCH_STATE_KV_KEY, default={}, db_path=db_path) or {})
    last_rule_id = int(match_state.get("last_rule_id", 0) or 0)
    last_snapshot_id = int(match_state.get("last_snapshot_id", 0) or 0)
    with _connect(db_path) as conn:
        max_rule_row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM knowledge_rules").fetchone()
        max_snapshot_row = conn.execute("SELECT COALESCE(MAX(id), 0) FROM market_snapshots").fetchone()
        current_max_rule_id = int(max_rule_row[0] or 0)
        current_max_snapshot_id = int(max_snapshot_row[0] or 0)

        incremental_snapshot_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM market_snapshots WHERE id > ? ORDER BY id ASC",
                (last_snapshot_id,),
            ).fetchall()
        ]
        new_rule_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT id FROM knowledge_rules WHERE id > ? ORDER BY id ASC",
                (last_rule_id,),
            ).fetchall()
        ]

        # 阶段1：所有已有规则匹配新快照（含显式传入的快照）
        snapshot_phase_ids = sorted(set(explicit_snapshot_ids) | set(incremental_snapshot_ids))
        if snapshot_phase_ids:
            snapshot_phase_rule_ids = [
                int(row[0])
                for row in conn.execute(
                    "SELECT id FROM knowledge_rules WHERE id NOT IN ({}) ORDER BY id ASC".format(
                        ",".join("?" for _ in new_rule_ids)
                    ),
                    tuple(new_rule_ids),
                ).fetchall()
            ] if new_rule_ids else None
            new_snapshot_match_count = _insert_rule_snapshot_matches(
                conn,
                _fetch_rules(conn, snapshot_phase_rule_ids),
                _fetch_snapshots(conn, snapshot_phase_ids),
            )
            matched_count += new_snapshot_match_count

        # 阶段2：新规则回吃全部历史快照，补齐旧样本
        if new_rule_ids:
            new_rule_backfill_count = _insert_rule_snapshot_matches(
                conn,
                _fetch_rules(conn, new_rule_ids),
                _fetch_snapshots(conn, None),
            )
            matched_count += new_rule_backfill_count

    if current_max_rule_id != last_rule_id or current_max_snapshot_id != last_snapshot_id:
        kv_set(
            MATCH_STATE_KV_KEY,
            {
                "last_rule_id": current_max_rule_id,
                "last_snapshot_id": current_max_snapshot_id,
            },
            db_path=db_path,
        )

    return {
        "matched_count": matched_count,
        "new_snapshot_match_count": new_snapshot_match_count,
        "new_rule_backfill_count": new_rule_backfill_count,
    }


def refresh_rule_scores(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    """3.3 修复：增量评分模式。
    只处理 id > last_processed_outcome_id 的新 snapshot_outcomes，
    将新结果累加到现有计数上，永远不回头遍历旧数据，确保 O(新增) 复杂度。
    """
    updated_count = 0
    with _connect(db_path) as conn:
        rules = conn.execute(
            """
            SELECT kr.id, kr.category, kr.rule_text,
                   COALESCE(rs.success_count, 0)  AS cur_success,
                   COALESCE(rs.mixed_count, 0)    AS cur_mixed,
                   COALESCE(rs.fail_count, 0)     AS cur_fail,
                   COALESCE(rs.observe_count, 0)  AS cur_observe,
                   COALESCE(rs.last_processed_outcome_id, 0) AS last_id
            FROM knowledge_rules kr
            LEFT JOIN rule_scores rs ON rs.rule_id = kr.id AND rs.horizon_min = ?
            ORDER BY kr.id ASC
            """,
            (int(horizon_min),),
        ).fetchall()

        now_text = _now_text()
        for rule in rules:
            rule_id = int(rule["id"])
            category = _normalize_text(rule["category"]).lower()
            last_outcome_id = int(rule["last_id"] or 0)

            if _should_skip_rule(str(rule["rule_text"] or ""), category):
                # 跳过规则：只更新 validation_status，不动计数
                conn.execute(
                    """
                    INSERT INTO rule_scores (
                        rule_id, horizon_min, sample_count, success_count, mixed_count, fail_count,
                        observe_count, success_rate, score, validation_status,
                        last_processed_outcome_id, updated_at
                    ) VALUES (?, ?, 0, 0, 0, 0, 0, 0, 0, 'manual_review', ?, ?)
                    ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                        validation_status = 'manual_review',
                        updated_at = excluded.updated_at
                    """,
                    (rule_id, int(horizon_min), last_outcome_id, now_text),
                )
                updated_count += 1
                continue

            # 只查询比上次处理更新的结果（增量），并直接在 SQLite 内聚合，避免把标签全拉回 Python 内存。
            aggregate_row = conn.execute(
                """
                SELECT
                    COALESCE(MAX(so.id), 0) AS max_outcome_id,
                    COALESCE(SUM(CASE WHEN so.outcome_label = 'success' THEN 1 ELSE 0 END), 0) AS add_success,
                    COALESCE(SUM(CASE WHEN so.outcome_label = 'mixed' THEN 1 ELSE 0 END), 0)   AS add_mixed,
                    COALESCE(SUM(CASE WHEN so.outcome_label = 'fail' THEN 1 ELSE 0 END), 0)    AS add_fail,
                    COALESCE(SUM(CASE WHEN so.outcome_label = 'observe' THEN 1 ELSE 0 END), 0) AS add_observe,
                    COUNT(*) AS new_count
                FROM rule_snapshot_matches rm
                JOIN snapshot_outcomes so ON so.snapshot_id = rm.snapshot_id
                WHERE rm.rule_id = ? AND so.horizon_min = ? AND so.id > ?
                """,
                (rule_id, int(horizon_min), last_outcome_id),
            ).fetchone()

            if aggregate_row is None or int(aggregate_row["new_count"] or 0) <= 0:
                # 没有新结果：不更新任何内容，保留现有计数
                continue

            max_new_id = int(aggregate_row["max_outcome_id"] or last_outcome_id)
            add_success = int(aggregate_row["add_success"] or 0)
            add_mixed = int(aggregate_row["add_mixed"] or 0)
            add_fail = int(aggregate_row["add_fail"] or 0)
            add_observe = int(aggregate_row["add_observe"] or 0)

            new_success = int(rule["cur_success"]) + add_success
            new_mixed   = int(rule["cur_mixed"]) + add_mixed
            new_fail    = int(rule["cur_fail"]) + add_fail
            new_observe = int(rule["cur_observe"]) + add_observe
            sample_count = new_success + new_mixed + new_fail

            if sample_count <= 0:
                success_rate = 0.0
                score = 0.0
                validation_status = "insufficient"
            else:
                success_rate = (new_success + new_mixed * 0.5) / sample_count
                score = ((new_success + new_mixed * 0.35) - new_fail) / sample_count * 100.0
                if sample_count < 5:
                    validation_status = "insufficient"
                elif score >= 25.0 and success_rate >= 0.58:
                    validation_status = "validated"
                elif score >= 0.0:
                    validation_status = "candidate"
                else:
                    validation_status = "rejected"

            conn.execute(
                """
                INSERT INTO rule_scores (
                    rule_id, horizon_min, sample_count, success_count, mixed_count, fail_count,
                    observe_count, success_rate, score, validation_status,
                    last_processed_outcome_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                    sample_count   = excluded.sample_count,
                    success_count  = excluded.success_count,
                    mixed_count    = excluded.mixed_count,
                    fail_count     = excluded.fail_count,
                    observe_count  = excluded.observe_count,
                    success_rate   = excluded.success_rate,
                    score          = excluded.score,
                    validation_status = excluded.validation_status,
                    last_processed_outcome_id = excluded.last_processed_outcome_id,
                    updated_at     = excluded.updated_at
                """,
                (
                    rule_id, int(horizon_min),
                    sample_count, new_success, new_mixed, new_fail, new_observe,
                    float(success_rate), float(score), validation_status,
                    max_new_id, now_text,
                ),
            )
            updated_count += 1

    return {
        "updated_count": updated_count,
        "horizon_min": int(horizon_min),
    }



def summarize_rule_scores(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    limit: int = 5,
) -> dict:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT rs.validation_status, COUNT(*) AS count
            FROM rule_scores rs
            WHERE rs.horizon_min = ?
            GROUP BY rs.validation_status
            """,
            (int(horizon_min),),
        ).fetchall()
        top_rows = conn.execute(
            """
            SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score, rs.validation_status
            FROM rule_scores rs
            JOIN knowledge_rules kr ON kr.id = rs.rule_id
            WHERE rs.horizon_min = ? AND rs.validation_status IN ('validated', 'candidate')
            ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
            LIMIT ?
            """,
            (int(horizon_min), max(1, int(limit))),
        ).fetchall()

    counts = {str(row["validation_status"]): int(row["count"]) for row in rows}
    top_rules = [
        {
            "rule_text": _normalize_text(row["rule_text"]),
            "category": _normalize_text(row["category"]).lower(),
            "sample_count": int(row["sample_count"]),
            "success_rate": float(row["success_rate"]),
            "score": float(row["score"]),
            "validation_status": _normalize_text(row["validation_status"]),
        }
        for row in top_rows
    ]
    return {
        "validated_count": counts.get("validated", 0),
        "candidate_count": counts.get("candidate", 0),
        "rejected_count": counts.get("rejected", 0),
        "insufficient_count": counts.get("insufficient", 0),
        "manual_review_count": counts.get("manual_review", 0),
        "top_rules": top_rules,
        "summary_text": (
            f"{horizon_min} 分钟规则评分：已验证 {counts.get('validated', 0)} 条，"
            f"候选 {counts.get('candidate', 0)} 条，拒绝 {counts.get('rejected', 0)} 条，"
            f"样本不足 {counts.get('insufficient', 0)} 条，人工评审 {counts.get('manual_review', 0)} 条。"
        ),
    }
