"""
规则治理层：将规则评分转成启用、观察、冻结和人工复核状态，并生成学习摘要。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from app_config import (
    PROJECT_DIR,
    get_runtime_config,
    get_sim_strategy_cooldown_min,
    get_sim_strategy_daily_limit,
    get_sim_strategy_min_rr,
    normalize_sim_strategy_cooldown_min,
    normalize_sim_strategy_daily_limit,
    normalize_sim_strategy_min_rr,
    save_runtime_config,
)
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection, upsert_source
from knowledge_feedback import summarize_feedback_stats
from learning_closure import summarize_alert_effect_outcomes, summarize_missed_opportunity_samples
from trade_learning import summarize_trade_learning_by_strategy


SIM_DB_PATH = PROJECT_DIR / ".runtime" / "mt5_sim_trading.sqlite"

_STRATEGY_FAMILY_LABEL_MAP = {
    "pullback_sniper_probe": "回调狙击",
    "directional_probe": "方向试仓",
    "direct_momentum": "直线动能",
    "early_momentum": "早期动能",
    "structure": "结构候选",
    "setup": "Setup",
    "unknown": "未分类",
}


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
    source_type = _normalize_text(row["source_type"]).lower()
    feedback_total_count = int(row["feedback_total_count"] or 0)
    feedback_negative_rate = float(row["feedback_negative_rate"] or 0.0)

    if feedback_total_count >= 3 and feedback_negative_rate >= 0.75:
        return "frozen", "用户侧负反馈已明显聚集，先冻结该规则，避免继续向外放大噪音。"
    if validation_status == "validated" and feedback_total_count >= 3 and feedback_negative_rate >= 0.60:
        return "watch", "历史样本达标，但用户反馈显示落点偏晚或噪音偏高，先降到观察名单。"

    if validation_status == "reference":
        return "reference", "该规则来自基础知识导入，保留为参考背景，不纳入自动进化赛道。"
    if validation_status in {"archived", "manual_review"}:
        return "archived", "该内容不适合作为自动交易执行规则，已自动归档为知识背景。"
    if validation_status == "validated":
        if source_type in {"auto_miner", "llm_cluster_loss", "llm_golden_setup", "sim_feedback", "sim_reflection"} and sample_count < 8:
            return "watch", "自动学习规则仍处试运行期，先只影响观察和模拟盘，不急着进入正式启用。"
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
                ks.source_type,
                COALESCE(rfs.total_count, 0) AS feedback_total_count,
                COALESCE(rfs.negative_rate, 0) AS feedback_negative_rate
            FROM rule_scores rs
            JOIN knowledge_rules kr ON kr.id = rs.rule_id
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            LEFT JOIN rule_feedback_scores rfs ON rfs.rule_id = rs.rule_id
            WHERE rs.horizon_min = ?
            ORDER BY rs.rule_id ASC
            """,
            (int(horizon_min),),
        ).fetchall()

        now_text = _now_text()
        for row in rows:
            if _normalize_text(row["source_type"]).lower() == "strategy_learning":
                continue
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
        "archived_count": counts.get("archived", 0),
        "reference_count": counts.get("reference", 0),
        "summary_text": (
            f"规则治理：启用 {counts.get('active', 0)} 条，观察 {counts.get('watch', 0)} 条，"
            f"冻结 {counts.get('frozen', 0)} 条，待积累 {counts.get('pending', 0)} 条，"
            f"自动归档 {counts.get('archived', 0)} 条，基础参考 {counts.get('reference', 0)} 条，"
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


def _format_money(value: float) -> str:
    numeric = float(value or 0.0)
    sign = "+" if numeric > 0 else ("-" if numeric < 0 else "")
    return f"{sign}${abs(numeric):,.2f}"


def _format_strategy_family_label(value: object) -> str:
    key = _normalize_text(value).lower()
    return _STRATEGY_FAMILY_LABEL_MAP.get(key, key.replace("_", " ") if key else "未分类")


def _build_strategy_review_candidate(row: dict, *, days: int) -> dict | None:
    family = _normalize_text(row.get("strategy_family", "")).lower() or "unknown"
    label = _format_strategy_family_label(family)
    total_count = int(row.get("total_count", 0) or 0)
    win_count = int(row.get("win_count", 0) or 0)
    loss_count = int(row.get("loss_count", 0) or 0)
    decided_count = win_count + loss_count
    win_rate = float(row.get("win_rate", 0.0) or 0.0)
    net_profit = float(row.get("net_profit", 0.0) or 0.0)
    avg_rr = float(row.get("avg_rr", 0.0) or 0.0)

    if decided_count < 3:
        return None

    action_kind = ""
    rule_text = ""
    if loss_count >= 2 and net_profit < 0:
        action_kind = "tighten"
        rule_text = f"策略学习建议：收紧{label}探索入场阈值"
    elif win_count >= 2 and win_rate >= 60.0 and net_profit > 0:
        action_kind = "keep_collecting"
        rule_text = f"策略学习建议：保留{label}当前阈值并继续收集样本"
    else:
        return None

    rationale = (
        f"近{max(1, int(days))}天{label}共 {total_count} 笔，已决 {decided_count} 笔，"
        f"{win_count}胜{loss_count}负，胜率 {win_rate:.0f}%，净盈亏 {_format_money(net_profit)}，"
        f"平均RR {avg_rr:.2f}。该建议只进入人工复核，不自动改动交易配置。"
    )
    return {
        "strategy_family": family,
        "strategy_label": label,
        "action_kind": action_kind,
        "rule_text": rule_text,
        "rationale": rationale,
        "logic": {
            "source": "strategy_learning",
            "strategy_family": family,
            "strategy_label": label,
            "action_kind": action_kind,
            "days": max(1, int(days)),
            "total_count": total_count,
            "decided_count": decided_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "win_rate": win_rate,
            "net_profit": net_profit,
            "avg_rr": avg_rr,
        },
    }


def sync_strategy_learning_reviews(
    db_path: Path | str | None = None,
    *,
    days: int = 7,
    limit: int = 5,
    now: datetime | None = None,
) -> dict:
    """
    将模拟盘策略族表现转成 HITL 待审建议。

    这里故意只生成 manual_review，不自动修改阈值或交易配置：策略调参会改变出手频率，
    需要先让用户在规则审批台确认。
    """
    clean_days = max(1, int(days or 7))
    summary = summarize_trade_learning_by_strategy(days=clean_days, db_path=db_path, limit=limit)
    candidates = [
        item
        for item in (
            _build_strategy_review_candidate(dict(row or {}), days=clean_days)
            for row in list(summary.get("rows", []) or [])
        )
        if item
    ]
    if not candidates:
        return {
            "created_count": 0,
            "updated_count": 0,
            "review_count": 0,
            "skipped_count": int(len(list(summary.get("rows", []) or []))),
        }

    created_count = 0
    updated_count = 0
    review_count = 0
    now_text = _now_text(now)
    target_db = db_path or KNOWLEDGE_DB_FILE

    for item in candidates:
        family = _normalize_text(item.get("strategy_family", "")).lower() or "unknown"
        action_kind = _normalize_text(item.get("action_kind", "")).lower() or "review"
        source_id = upsert_source(
            title="模拟盘策略学习建议",
            source_type="strategy_learning",
            location=f"strategy_learning::{family}::{action_kind}",
            trust_level="working",
            tags=["strategy_learning", family, action_kind],
            notes="由模拟盘真实开平仓样本生成的策略调参待审建议。",
            db_path=target_db,
        )
        logic_json = json.dumps(dict(item.get("logic", {}) or {}), ensure_ascii=False)
        tags_json = json.dumps(["strategy_learning", family, action_kind], ensure_ascii=False)
        with _connect(target_db) as conn:
            row = conn.execute(
                """
                SELECT id
                FROM knowledge_rules
                WHERE source_id = ?
                  AND section_title = '模拟盘策略学习'
                LIMIT 1
                """,
                (int(source_id),),
            ).fetchone()
            if row:
                rule_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE knowledge_rules
                    SET rule_text = ?,
                        category = 'risk',
                        asset_scope = 'XAUUSD',
                        confidence = 'pending',
                        evidence_type = '模拟盘策略学习',
                        tags_json = ?,
                        logic_json = ?
                    WHERE id = ?
                    """,
                    (
                        _normalize_text(item.get("rule_text", "")),
                        tags_json,
                        logic_json,
                        rule_id,
                    ),
                )
                updated_count += 1
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO knowledge_rules (
                        source_id, document_id, section_title, category, asset_scope,
                        rule_text, confidence, evidence_type, tags_json, logic_json, created_at
                    ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(source_id),
                        "模拟盘策略学习",
                        "risk",
                        "XAUUSD",
                        _normalize_text(item.get("rule_text", "")),
                        "pending",
                        "模拟盘策略学习",
                        tags_json,
                        logic_json,
                        now_text,
                    ),
                )
                rule_id = int(cursor.lastrowid)
                created_count += 1

            status_row = conn.execute(
                """
                SELECT governance_status
                FROM rule_governance
                WHERE rule_id = ? AND horizon_min = 30
                """,
                (rule_id,),
            ).fetchone()
            current_status = _normalize_text(status_row["governance_status"] if status_row else "").lower()
            next_status = "manual_review" if current_status not in {"active", "frozen"} else current_status
            logic = dict(item.get("logic", {}) or {})
            decided_count = int(logic.get("decided_count", 0) or 0)
            win_count = int(logic.get("win_count", 0) or 0)
            loss_count = int(logic.get("loss_count", 0) or 0)
            success_rate = (win_count / decided_count) if decided_count > 0 else 0.0
            score = abs(float(logic.get("net_profit", 0.0) or 0.0)) + decided_count
            validation_status = "candidate"
            if next_status == "active":
                validation_status = "validated"
            elif next_status == "frozen":
                validation_status = "rejected"
            conn.execute(
                """
                INSERT INTO rule_scores (
                    rule_id, horizon_min, sample_count, success_count, mixed_count,
                    fail_count, observe_count, success_rate, score, validation_status,
                    last_processed_outcome_id, updated_at
                ) VALUES (?, 30, ?, ?, 0, ?, 0, ?, ?, ?, 0, ?)
                ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                    sample_count = excluded.sample_count,
                    success_count = excluded.success_count,
                    fail_count = excluded.fail_count,
                    success_rate = excluded.success_rate,
                    score = excluded.score,
                    validation_status = excluded.validation_status,
                    updated_at = excluded.updated_at
                """,
                (
                    rule_id,
                    decided_count,
                    win_count,
                    loss_count,
                    success_rate,
                    score,
                    validation_status,
                    now_text,
                ),
            )
            conn.execute(
                """
                INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
                VALUES (?, 30, ?, ?, ?)
                ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                    governance_status = excluded.governance_status,
                    rationale = excluded.rationale,
                    updated_at = excluded.updated_at
                """,
                (
                    rule_id,
                    next_status,
                    _normalize_text(item.get("rationale", "")),
                    now_text,
                ),
            )
            if next_status == "manual_review":
                review_count += 1

    return {
        "created_count": created_count,
        "updated_count": updated_count,
        "review_count": review_count,
        "skipped_count": max(0, int(len(list(summary.get("rows", []) or []))) - len(candidates)),
    }


def apply_strategy_learning_review(
    rule_id: int,
    *,
    approved: bool,
    db_path: Path | str | None = None,
) -> dict:
    if int(rule_id or 0) <= 0 or not bool(approved):
        return {"applied": False, "reason": "未批准策略建议，不修改配置。"}

    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT kr.logic_json, kr.rule_text, ks.source_type
            FROM knowledge_rules kr
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            WHERE kr.id = ?
            LIMIT 1
            """,
            (int(rule_id),),
        ).fetchone()
    if not row or _normalize_text(row["source_type"]).lower() != "strategy_learning":
        return {"applied": False, "reason": "不是策略学习建议。"}

    try:
        logic = json.loads(str(row["logic_json"] or "{}"))
    except json.JSONDecodeError:
        logic = {}
    if not isinstance(logic, dict):
        logic = {}
    family = _normalize_text(logic.get("strategy_family", "")).lower()
    action_kind = _normalize_text(logic.get("action_kind", "")).lower()
    if not family:
        return {"applied": False, "reason": "策略族为空，无法应用。"}

    config = get_runtime_config()
    rr_map = normalize_sim_strategy_min_rr(getattr(config, "sim_strategy_min_rr", {}))
    daily_limit_map = normalize_sim_strategy_daily_limit(getattr(config, "sim_strategy_daily_limit", {}))
    cooldown_map = normalize_sim_strategy_cooldown_min(getattr(config, "sim_strategy_cooldown_min", {}))
    current_rr = float(rr_map.get(family, get_sim_strategy_min_rr(family, config=config)) or 1.6)
    current_daily_limit = int(daily_limit_map.get(family, get_sim_strategy_daily_limit(family, config=config)) or 3)
    current_cooldown_min = int(cooldown_map.get(family, get_sim_strategy_cooldown_min(family, config=config)) or 10)
    if action_kind == "tighten":
        loss_count = int(logic.get("loss_count", 0) or 0)
        step = 0.20 if loss_count >= 3 else 0.15
        new_rr = min(10.0, round(current_rr + step, 2))
        new_daily_limit = max(1, current_daily_limit - 1)
        cooldown_step = 10 if loss_count >= 3 else 5
        new_cooldown_min = min(240, current_cooldown_min + cooldown_step)
        if new_rr <= current_rr and new_daily_limit >= current_daily_limit and new_cooldown_min <= current_cooldown_min:
            return {
                "applied": False,
                "reason": f"{_format_strategy_family_label(family)} 当前阈值已到上限。",
            }
        rr_map[family] = new_rr
        daily_limit_map[family] = new_daily_limit
        cooldown_map[family] = new_cooldown_min
        config.sim_strategy_min_rr = rr_map
        config.sim_strategy_daily_limit = daily_limit_map
        config.sim_strategy_cooldown_min = cooldown_map
        save_runtime_config(config)
        message_parts = []
        if new_rr != current_rr:
            message_parts.append(f"最小 RR 已由 {current_rr:.2f} 调整为 {new_rr:.2f}")
        if new_daily_limit != current_daily_limit:
            message_parts.append(f"日上限已由 {current_daily_limit} 调整为 {new_daily_limit}")
        if new_cooldown_min != current_cooldown_min:
            message_parts.append(f"冷却已由 {current_cooldown_min} 分钟调整为 {new_cooldown_min} 分钟")
        return {
            "applied": True,
            "strategy_family": family,
            "action_kind": action_kind,
            "old_rr": current_rr,
            "new_rr": new_rr,
            "old_daily_limit": current_daily_limit,
            "new_daily_limit": new_daily_limit,
            "old_cooldown_min": current_cooldown_min,
            "new_cooldown_min": new_cooldown_min,
            "message": f"{_format_strategy_family_label(family)} " + "；".join(message_parts),
        }

    if action_kind == "keep_collecting":
        rr_map[family] = current_rr
        daily_limit_map[family] = current_daily_limit
        cooldown_map[family] = current_cooldown_min
        config.sim_strategy_min_rr = rr_map
        config.sim_strategy_daily_limit = daily_limit_map
        config.sim_strategy_cooldown_min = cooldown_map
        save_runtime_config(config)
        return {
            "applied": True,
            "strategy_family": family,
            "action_kind": action_kind,
            "old_rr": current_rr,
            "new_rr": current_rr,
            "old_daily_limit": current_daily_limit,
            "new_daily_limit": current_daily_limit,
            "old_cooldown_min": current_cooldown_min,
            "new_cooldown_min": current_cooldown_min,
            "message": (
                f"{_format_strategy_family_label(family)} 继续沿用当前参数："
                f"RR {current_rr:.2f} / 日上限 {current_daily_limit} / 冷却 {current_cooldown_min} 分钟"
            ),
        }

    return {"applied": False, "reason": f"暂不支持的策略建议动作：{action_kind or '--'}。"}


def summarize_sim_trade_profiles(
    sim_db_path: Path | str | None = None,
    days: int = 30,
    now: datetime | None = None,
) -> dict:
    target = Path(sim_db_path) if sim_db_path else SIM_DB_PATH
    empty_result = {
        "total_count": 0,
        "profiles": {},
        "profile_rows": [],
        "summary_text": f"模拟交易样本：最近 {max(1, int(days))} 天暂无已平仓样本。",
    }
    if not target.exists():
        return empty_result

    cutoff = ((now or datetime.now()) - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with sqlite3.connect(str(target)) as conn:
            conn.row_factory = sqlite3.Row
            columns = {
                str(row["name"]).strip().lower()
                for row in conn.execute("PRAGMA table_info(sim_trades)").fetchall()
            }
            profile_expr = (
                "COALESCE(NULLIF(execution_profile, ''), 'standard')"
                if "execution_profile" in columns
                else "'standard'"
            )
            rows = conn.execute(
                f"""
                SELECT
                    {profile_expr} AS execution_profile,
                    COUNT(*) AS total_count,
                    SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS win_count,
                    SUM(CASE WHEN profit < 0 THEN 1 ELSE 0 END) AS loss_count,
                    SUM(CASE WHEN profit = 0 THEN 1 ELSE 0 END) AS flat_count,
                    COALESCE(SUM(profit), 0.0) AS net_profit,
                    COALESCE(AVG(profit), 0.0) AS avg_profit
                FROM sim_trades
                WHERE COALESCE(closed_at, '') >= ?
                GROUP BY {profile_expr}
                """,
                (cutoff,),
            ).fetchall()
    except sqlite3.Error:
        return empty_result

    profile_rows = []
    profiles = {}
    for row in rows:
        profile = _normalize_text(row["execution_profile"]).lower() or "standard"
        if profile not in {"standard", "exploratory"}:
            profile = "standard"
        total_count = int(row["total_count"] or 0)
        win_count = int(row["win_count"] or 0)
        loss_count = int(row["loss_count"] or 0)
        flat_count = int(row["flat_count"] or 0)
        net_profit = float(row["net_profit"] or 0.0)
        avg_profit = float(row["avg_profit"] or 0.0)
        win_rate = (win_count / total_count) if total_count > 0 else 0.0
        item = {
            "execution_profile": profile,
            "label": "探索试仓" if profile == "exploratory" else "标准试仓",
            "total_count": total_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "flat_count": flat_count,
            "win_rate": win_rate,
            "net_profit": net_profit,
            "avg_profit": avg_profit,
        }
        profiles[profile] = item
        profile_rows.append(item)

    profile_rows.sort(key=lambda item: (0 if item["execution_profile"] == "standard" else 1, item["execution_profile"]))
    total_count = sum(int(item["total_count"]) for item in profile_rows)
    if total_count <= 0:
        return empty_result

    summary_parts = []
    for item in profile_rows:
        summary_parts.append(
            f"{item['label']} {item['total_count']} 笔，"
            f"胜率 {item['win_rate'] * 100:.0f}%，"
            f"净盈亏 {_format_money(item['net_profit'])}"
        )
    return {
        "total_count": total_count,
        "profiles": profiles,
        "profile_rows": profile_rows,
        "summary_text": f"模拟交易样本：最近 {max(1, int(days))} 天，" + "；".join(summary_parts) + "。",
    }


def build_learning_report(
    db_path: Path | str | None = None,
    sim_db_path: Path | str | None = None,
    now: datetime | None = None,
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
    alert_effect_summary = summarize_alert_effect_outcomes(db_path=db_path, horizon_min=horizon_min)
    missed_opportunity_summary = summarize_missed_opportunity_samples(db_path=db_path, horizon_min=horizon_min)
    sim_trade_profile_summary = summarize_sim_trade_profiles(sim_db_path=sim_db_path, days=30, now=now)
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
    if int(alert_effect_summary.get("total_count", 0) or 0) > 0:
        summary_text = f"{summary_text} {alert_effect_summary.get('summary_text', '')}".strip()
    if int(missed_opportunity_summary.get("total_count", 0) or 0) > 0:
        summary_text = f"{summary_text} {missed_opportunity_summary.get('summary_text', '')}".strip()
    summary_text = f"{summary_text} {sim_trade_profile_summary.get('summary_text', '')}".strip()
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
        "alert_effect_summary": alert_effect_summary,
        "missed_opportunity_summary": missed_opportunity_summary,
        "sim_trade_profile_summary": sim_trade_profile_summary,
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
                    _now_text(now),
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
            "alert_effect_summary": summarize_alert_effect_outcomes(db_path=db_path, horizon_min=30),
            "missed_opportunity_summary": summarize_missed_opportunity_samples(db_path=db_path, horizon_min=30),
            "sim_trade_profile_summary": summarize_sim_trade_profiles(days=30),
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
