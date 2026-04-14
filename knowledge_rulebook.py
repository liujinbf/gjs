"""
规则提炼层：从规则评分中整理出当前有效规则集、观察规则和淘汰规则。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from knowledge_governance import summarize_rule_governance
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

# N-012 修复：规则库 5 分钟内存缓存，避免每次 AI 研判都重查 SQLite
import time as _time
_rulebook_cache: dict = {}          # cache_key -> {result, expires_at}
_RULEBOOK_CACHE_TTL_SEC = 300       # 5 分钟



def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _format_rule_line(row: sqlite3.Row, prefix: str = "") -> str:
    category = _normalize_text(row["category"]).lower() or "general"
    sample_count = int(row["sample_count"] or 0)
    success_rate = float(row["success_rate"] or 0.0) * 100.0
    score = float(row["score"] or 0.0)
    rule_text = _normalize_text(row["rule_text"])
    return (
        f"{prefix}[{category}] {rule_text}"
        f"（样本 {sample_count}，成功率 {success_rate:.0f}%，评分 {score:.1f}）"
    ).strip()


def build_rulebook(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    validated_limit: int = 6,
    candidate_limit: int = 4,
    rejected_limit: int = 3,
    current_regime_tag: str = "",
) -> dict:
    # N-012 修复：5 分钟内存缓存，相同参数直接返回缓存结果，避免高频查 SQLite
    global _rulebook_cache
    regime_tag = _normalize_text(current_regime_tag).lower().replace(" ", "_")
    _cache_key = (
        str(db_path),
        int(horizon_min),
        int(validated_limit),
        int(candidate_limit),
        int(rejected_limit),
        regime_tag,
    )
    _cached = _rulebook_cache.get(_cache_key)
    if _cached and _time.time() < _cached["expires_at"]:
        return _cached["result"]

    with _connect(db_path) as conn:
        governance_count = int(
            conn.execute(
                "SELECT COUNT(*) FROM rule_governance WHERE horizon_min = ?",
                (int(horizon_min),),
            ).fetchone()[0]
        )

        if governance_count > 0:
            active_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score
                FROM rule_governance rg
                JOIN knowledge_rules kr ON kr.id = rg.rule_id
                JOIN rule_scores rs ON rs.rule_id = rg.rule_id AND rs.horizon_min = rg.horizon_min
                WHERE rg.horizon_min = ? AND rg.governance_status = 'active'
                ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(validated_limit))),
            ).fetchall()
            watch_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score
                FROM rule_governance rg
                JOIN knowledge_rules kr ON kr.id = rg.rule_id
                JOIN rule_scores rs ON rs.rule_id = rg.rule_id AND rs.horizon_min = rg.horizon_min
                WHERE rg.horizon_min = ? AND rg.governance_status = 'watch'
                ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(candidate_limit))),
            ).fetchall()
            frozen_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score
                FROM rule_governance rg
                JOIN knowledge_rules kr ON kr.id = rg.rule_id
                JOIN rule_scores rs ON rs.rule_id = rg.rule_id AND rs.horizon_min = rg.horizon_min
                WHERE rg.horizon_min = ? AND rg.governance_status = 'frozen'
                ORDER BY rs.score ASC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(rejected_limit))),
            ).fetchall()
        else:
            active_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score, rs.validation_status
                FROM rule_scores rs
                JOIN knowledge_rules kr ON kr.id = rs.rule_id
                WHERE rs.horizon_min = ? AND rs.validation_status = 'validated'
                ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(validated_limit))),
            ).fetchall()
            watch_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score, rs.validation_status
                FROM rule_scores rs
                JOIN knowledge_rules kr ON kr.id = rs.rule_id
                WHERE rs.horizon_min = ? AND rs.validation_status = 'candidate'
                ORDER BY rs.score DESC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(candidate_limit))),
            ).fetchall()
            frozen_rows = conn.execute(
                """
                SELECT kr.rule_text, kr.category, rs.sample_count, rs.success_rate, rs.score, rs.validation_status
                FROM rule_scores rs
                JOIN knowledge_rules kr ON kr.id = rs.rule_id
                WHERE rs.horizon_min = ? AND rs.validation_status = 'rejected'
                ORDER BY rs.score ASC, rs.sample_count DESC, kr.id ASC
                LIMIT ?
                """,
                (int(horizon_min), max(1, int(rejected_limit))),
            ).fetchall()

        regime_rows = []
        if regime_tag:
            if governance_count > 0:
                regime_rows = conn.execute(
                    """
                    SELECT
                        kr.rule_text,
                        kr.category,
                        COUNT(*) AS sample_count,
                        (
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'success' THEN 1 ELSE 0 END), 0) +
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'mixed' THEN 0.5 ELSE 0 END), 0)
                        ) * 1.0 / NULLIF(
                            COALESCE(SUM(CASE WHEN so.outcome_label IN ('success', 'mixed', 'fail') THEN 1 ELSE 0 END), 0),
                            0
                        ) AS success_rate,
                        (
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'success' THEN 1 ELSE 0 END), 0) +
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'mixed' THEN 0.35 ELSE 0 END), 0) -
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'fail' THEN 1 ELSE 0 END), 0)
                        ) * 100.0 / NULLIF(
                            COALESCE(SUM(CASE WHEN so.outcome_label IN ('success', 'mixed', 'fail') THEN 1 ELSE 0 END), 0),
                            0
                        ) AS score
                    FROM rule_governance rg
                    JOIN knowledge_rules kr ON kr.id = rg.rule_id
                    JOIN rule_snapshot_matches rm ON rm.rule_id = rg.rule_id
                    JOIN market_snapshots ms ON ms.id = rm.snapshot_id
                    JOIN snapshot_outcomes so ON so.snapshot_id = rm.snapshot_id AND so.horizon_min = rg.horizon_min
                    WHERE rg.horizon_min = ?
                      AND rg.governance_status IN ('active', 'watch')
                      AND ms.regime_tag = ?
                    GROUP BY rg.rule_id, kr.rule_text, kr.category
                    HAVING COUNT(*) >= 1
                    ORDER BY score DESC, sample_count DESC, rg.rule_id ASC
                    LIMIT ?
                    """,
                    (int(horizon_min), regime_tag, max(1, int(validated_limit))),
                ).fetchall()
            else:
                regime_rows = conn.execute(
                    """
                    SELECT
                        kr.rule_text,
                        kr.category,
                        COUNT(*) AS sample_count,
                        (
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'success' THEN 1 ELSE 0 END), 0) +
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'mixed' THEN 0.5 ELSE 0 END), 0)
                        ) * 1.0 / NULLIF(
                            COALESCE(SUM(CASE WHEN so.outcome_label IN ('success', 'mixed', 'fail') THEN 1 ELSE 0 END), 0),
                            0
                        ) AS success_rate,
                        (
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'success' THEN 1 ELSE 0 END), 0) +
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'mixed' THEN 0.35 ELSE 0 END), 0) -
                            COALESCE(SUM(CASE WHEN so.outcome_label = 'fail' THEN 1 ELSE 0 END), 0)
                        ) * 100.0 / NULLIF(
                            COALESCE(SUM(CASE WHEN so.outcome_label IN ('success', 'mixed', 'fail') THEN 1 ELSE 0 END), 0),
                            0
                        ) AS score
                    FROM rule_scores rs
                    JOIN knowledge_rules kr ON kr.id = rs.rule_id
                    JOIN rule_snapshot_matches rm ON rm.rule_id = rs.rule_id
                    JOIN market_snapshots ms ON ms.id = rm.snapshot_id
                    JOIN snapshot_outcomes so ON so.snapshot_id = rm.snapshot_id AND so.horizon_min = rs.horizon_min
                    WHERE rs.horizon_min = ?
                      AND rs.validation_status IN ('validated', 'candidate')
                      AND ms.regime_tag = ?
                    GROUP BY rs.rule_id, kr.rule_text, kr.category
                    HAVING COUNT(*) >= 1
                    ORDER BY score DESC, sample_count DESC, rs.rule_id ASC
                    LIMIT ?
                    """,
                    (int(horizon_min), regime_tag, max(1, int(validated_limit))),
                ).fetchall()

    validated_rules = [_format_rule_line(row, prefix="- ") for row in active_rows]
    candidate_rules = [_format_rule_line(row, prefix="- ") for row in watch_rows]
    rejected_rules = [_format_rule_line(row, prefix="- ") for row in frozen_rows]
    regime_rules = [_format_rule_line(row, prefix="- ") for row in regime_rows]
    governance_summary = summarize_rule_governance(db_path=db_path, horizon_min=horizon_min)

    if validated_rules:
        active_rules_text = "\n".join(validated_rules)
        active_summary = f"当前优先遵守 {len(validated_rules)} 条已验证规则。"
    elif candidate_rules:
        active_rules_text = "\n".join(candidate_rules)
        active_summary = "当前暂无已验证规则，先参考候选规则并严格服从当前快照风控。"
    else:
        active_rules_text = "暂无已验证规则，优先服从当前快照、点差状态和事件窗口纪律。"
        active_summary = "当前规则库样本仍不足，先以当前快照和风控纪律为主。"

    candidate_rules_text = "\n".join(candidate_rules) if candidate_rules else "暂无候选规则。"
    rejected_rules_text = "\n".join(rejected_rules) if rejected_rules else "暂无明确淘汰规则。"
    regime_rules_text = "\n".join(regime_rules) if regime_rules else "当前环境样本仍不足，先参考全局规则并服从快照风控。"

    result = {
        "horizon_min": int(horizon_min),
        "current_regime_tag": regime_tag,
        "validated_rules": validated_rules,
        "candidate_rules": candidate_rules,
        "rejected_rules": rejected_rules,
        "regime_rules": regime_rules,
        "active_rules_text": active_rules_text,
        "candidate_rules_text": candidate_rules_text,
        "rejected_rules_text": rejected_rules_text,
        "regime_rules_text": regime_rules_text,
        "regime_summary_text": (
            f"当前环境 {regime_tag.replace('_', ' ')} 下，优先参考 {len(regime_rules)} 条历史更稳规则。"
            if regime_tag and regime_rules
            else (
                f"当前环境 {regime_tag.replace('_', ' ')} 的样本仍不足，先参考全局规则。"
                if regime_tag
                else ""
            )
        ),
        "governance_summary_text": governance_summary.get("summary_text", ""),
        "summary_text": (
            f"{active_summary} 已验证 {len(validated_rules)} 条，"
            f"候选 {len(candidate_rules)} 条，淘汰 {len(rejected_rules)} 条。"
            f" {governance_summary.get('summary_text', '')}".strip()
        ),
    }
    # N-012 修复：写入缓存，TTL = 5 分钟
    _rulebook_cache[_cache_key] = {"result": result, "expires_at": _time.time() + _RULEBOOK_CACHE_TTL_SEC}
    return result
