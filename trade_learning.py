from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_tag_list(tags: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in list(tags or []):
        text = _normalize_text(item)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _classify_close_reason_key(reason: str, profit: float) -> str:
    text = _normalize_text(reason)
    if "爆仓" in text:
        return "margin_call"
    if "保本" in text:
        return "break_even_exit"
    if "止盈" in text or "目标" in text:
        return "take_profit"
    if "止损" in text:
        return "stop_loss"
    if profit > 0:
        return "win_exit"
    if profit < 0:
        return "loss_exit"
    return "flat_exit"


def _build_loss_reason_tags(*, reason: str, profit: float, entry_payload: dict) -> list[str]:
    tags: list[str] = []
    close_reason_key = _classify_close_reason_key(reason, profit)
    execution_profile = _normalize_text(entry_payload.get("execution_profile", "")) or "standard"
    strategy_family = _normalize_text(entry_payload.get("strategy_family", ""))
    setup_kind = _normalize_text(entry_payload.get("setup_kind", ""))
    trade_grade_source = _normalize_text(entry_payload.get("trade_grade_source", ""))
    multi_alignment = _normalize_text(entry_payload.get("multi_timeframe_alignment", "")).lower()
    key_level_state = _normalize_text(entry_payload.get("key_level_state", "")).lower()
    breakout_state = _normalize_text(entry_payload.get("breakout_state", "")).lower()
    retest_state = _normalize_text(entry_payload.get("retest_state", "")).lower()
    entry_zone_side = _normalize_text(entry_payload.get("entry_zone_side_text", "") or entry_payload.get("entry_zone_side", ""))
    risk_reward_state = _normalize_text(entry_payload.get("risk_reward_state", "")).lower()
    risk_reward_ratio = float(entry_payload.get("risk_reward_ratio", 0.0) or 0.0)

    tags.append("探索试仓" if execution_profile == "exploratory" else "标准试仓")
    if strategy_family:
        tags.append(f"策略:{strategy_family}")
    if setup_kind:
        tags.append(setup_kind)
    if trade_grade_source:
        tags.append(f"来源:{trade_grade_source}")
    if close_reason_key == "take_profit":
        tags.append("目标兑现")
    elif close_reason_key == "break_even_exit":
        tags.append("保本离场")
    elif close_reason_key == "margin_call":
        tags.extend(["爆仓", "风险过大"])
    elif close_reason_key in {"stop_loss", "loss_exit"}:
        tags.append("止损亏损")
        if multi_alignment == "mixed":
            tags.append("多周期分歧")
        if key_level_state == "mid_range":
            tags.append("中段起动")
        if breakout_state in {"none", "unknown", ""} and retest_state in {"none", "unknown", ""}:
            tags.append("缺少确认")
        if entry_zone_side in {"上沿", "upper"}:
            tags.append("偏追价")
        if entry_zone_side in {"下沿", "lower"}:
            tags.append("偏追空")
        if risk_reward_state == "acceptable" or (0 < risk_reward_ratio < 1.8):
            tags.append("盈亏比一般")
    return _normalize_tag_list(tags)


def _build_entry_payload(meta: dict) -> dict:
    keys = (
        "snapshot_id",
        "snapshot_time",
        "symbol",
        "action",
        "execution_profile",
        "trade_grade",
        "trade_grade_source",
        "trade_grade_detail",
        "signal_side",
        "signal_side_text",
        "signal_side_reason",
        "setup_kind",
        "risk_reward_ratio",
        "risk_reward_state",
        "risk_reward_direction",
        "entry_zone_side",
        "entry_zone_side_text",
        "model_win_probability",
        "execution_open_probability",
        "multi_timeframe_alignment",
        "multi_timeframe_bias",
        "intraday_bias",
        "intraday_volatility",
        "key_level_state",
        "breakout_state",
        "retest_state",
        "regime_tag",
        "regime_text",
        "event_risk_mode_text",
        "execution_note",
        "strategy_param_summary",
    )
    payload = {key: meta.get(key) for key in keys if meta.get(key) not in (None, "")}
    strategy_param_snapshot = meta.get("strategy_param_snapshot")
    if isinstance(strategy_param_snapshot, dict) and strategy_param_snapshot:
        payload["strategy_param_snapshot"] = dict(strategy_param_snapshot)
    setup_kind = _normalize_text(payload.get("setup_kind", ""))
    if setup_kind:
        payload["strategy_family"] = setup_kind
    elif _normalize_text(payload.get("trade_grade_source", "")):
        payload["strategy_family"] = _normalize_text(payload.get("trade_grade_source", ""))
    return payload


def summarize_trade_learning_by_strategy(
    *,
    days: int = 7,
    db_path: Path | str | None = None,
    limit: int = 5,
) -> dict:
    from datetime import timedelta

    horizon_days = max(1, int(days or 7))
    clean_limit = max(1, int(limit or 5))
    cutoff = (datetime.now() - timedelta(days=horizon_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows: list[dict] = []
    total_count = 0
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        raw_rows = conn.execute(
            """
            SELECT
                COALESCE(NULLIF(json_extract(entry_payload_json, '$.strategy_family'), ''), setup_kind, trade_grade_source, 'unknown') AS strategy_family,
                COUNT(*) AS total_count,
                SUM(CASE WHEN outcome_label = 'success' THEN 1 ELSE 0 END) AS win_count,
                SUM(CASE WHEN outcome_label = 'fail' THEN 1 ELSE 0 END) AS loss_count,
                SUM(CASE WHEN outcome_label NOT IN ('success', 'fail') THEN 1 ELSE 0 END) AS open_or_mixed_count,
                SUM(profit) AS net_profit,
                AVG(CASE WHEN risk_reward_ratio > 0 THEN risk_reward_ratio ELSE NULL END) AS avg_rr
            FROM trade_learning_journal
            WHERE opened_at >= ?
            GROUP BY COALESCE(NULLIF(json_extract(entry_payload_json, '$.strategy_family'), ''), setup_kind, trade_grade_source, 'unknown')
            ORDER BY total_count DESC, net_profit DESC
            LIMIT ?
            """,
            (cutoff, clean_limit),
        ).fetchall()
    for row in raw_rows:
        family = _normalize_text(row["strategy_family"]) or "unknown"
        count = int(row["total_count"] or 0)
        win_count = int(row["win_count"] or 0)
        loss_count = int(row["loss_count"] or 0)
        open_or_mixed_count = int(row["open_or_mixed_count"] or 0)
        decided_count = win_count + loss_count
        win_rate = (win_count / decided_count * 100.0) if decided_count > 0 else 0.0
        net_profit = float(row["net_profit"] or 0.0)
        avg_rr = float(row["avg_rr"] or 0.0)
        total_count += count
        rows.append(
            {
                "strategy_family": family,
                "total_count": count,
                "win_count": win_count,
                "loss_count": loss_count,
                "open_or_mixed_count": open_or_mixed_count,
                "win_rate": win_rate,
                "net_profit": net_profit,
                "avg_rr": avg_rr,
            }
        )
    return {
        "days": horizon_days,
        "total_count": total_count,
        "rows": rows,
    }


def record_trade_learning_open(
    *,
    sim_position_id: int,
    user_id: str,
    meta: dict,
    quantity: float,
    required_margin: float,
    sizing_balance: float,
    risk_budget_pct: float,
    db_path: Path | str | None = None,
) -> None:
    if int(sim_position_id or 0) <= 0:
        return
    payload = dict(meta or {})
    snapshot_id = int(payload.get("snapshot_id", 0) or 0)
    snapshot_time = _normalize_text(payload.get("snapshot_time", ""))
    if snapshot_id > 0 and not snapshot_time:
        with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
            row = conn.execute(
                "SELECT snapshot_time FROM market_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
            snapshot_time = _normalize_text(row["snapshot_time"]) if row else ""

    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        now = _now_text()
        conn.execute(
            """
            INSERT INTO trade_learning_journal (
                sim_position_id, user_id, snapshot_id, snapshot_time, symbol, action,
                execution_profile, trade_grade, trade_grade_source, signal_side,
                signal_side_reason, setup_kind, risk_reward_ratio, risk_reward_state,
                model_win_probability, execution_open_probability, entry_zone_side,
                regime_tag, event_risk_mode_text, sizing_reference_balance,
                risk_budget_pct, entry_price, stop_loss, take_profit, take_profit_2,
                quantity, required_margin, execution_note, entry_payload_json,
                opened_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(sim_position_id),
                _normalize_text(user_id) or "system",
                snapshot_id,
                snapshot_time,
                _normalize_text(payload.get("symbol", "")).upper(),
                _normalize_text(payload.get("action", "")).lower(),
                _normalize_text(payload.get("execution_profile", "")) or "standard",
                _normalize_text(payload.get("trade_grade", "")),
                _normalize_text(payload.get("trade_grade_source", "")),
                _normalize_text(payload.get("signal_side", "")),
                _normalize_text(payload.get("signal_side_reason", "")),
                _normalize_text(payload.get("setup_kind", "")),
                float(payload.get("risk_reward_ratio", 0.0) or 0.0),
                _normalize_text(payload.get("risk_reward_state", "")),
                float(payload.get("model_win_probability", 0.0) or 0.0),
                float(payload.get("execution_open_probability", 0.0) or 0.0),
                _normalize_text(payload.get("entry_zone_side_text", "") or payload.get("entry_zone_side", "")),
                _normalize_text(payload.get("regime_tag", "")),
                _normalize_text(payload.get("event_risk_mode_text", "")),
                float(sizing_balance or 0.0),
                float(risk_budget_pct or 0.0),
                float(payload.get("price", 0.0) or 0.0),
                float(payload.get("sl", 0.0) or 0.0),
                float(payload.get("tp", 0.0) or 0.0),
                float(payload.get("tp2", 0.0) or 0.0),
                float(quantity or 0.0),
                float(required_margin or 0.0),
                _normalize_text(payload.get("execution_note", "")),
                json.dumps(_build_entry_payload(payload), ensure_ascii=False),
                now,
                now,
            ),
        )


def record_trade_learning_close(
    *,
    sim_position_id: int,
    exit_price: float,
    profit: float,
    reason: str,
    db_path: Path | str | None = None,
) -> None:
    if int(sim_position_id or 0) <= 0:
        return
    with open_knowledge_connection(db_path=db_path or KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        row = conn.execute(
            "SELECT entry_payload_json FROM trade_learning_journal WHERE sim_position_id = ?",
            (int(sim_position_id),),
        ).fetchone()
        if not row:
            return
        try:
            entry_payload = json.loads(str(row["entry_payload_json"] or "{}"))
        except json.JSONDecodeError:
            entry_payload = {}
        close_reason_key = _classify_close_reason_key(reason, profit)
        if profit > 0:
            outcome_label = "success"
        elif profit < 0:
            outcome_label = "fail"
        else:
            outcome_label = "mixed"
        loss_reason_tags = _build_loss_reason_tags(reason=reason, profit=profit, entry_payload=entry_payload)
        now = _now_text()
        conn.execute(
            """
            UPDATE trade_learning_journal
            SET closed_at = ?,
                exit_price = ?,
                profit = ?,
                outcome_label = ?,
                close_reason = ?,
                close_reason_key = ?,
                loss_reason_tags_json = ?,
                updated_at = ?
            WHERE sim_position_id = ?
            """,
            (
                now,
                float(exit_price or 0.0),
                float(profit or 0.0),
                outcome_label,
                _normalize_text(reason),
                close_reason_key,
                json.dumps(loss_reason_tags, ensure_ascii=False),
                now,
                int(sim_position_id),
            ),
        )
