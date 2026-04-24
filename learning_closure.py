from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from alert_history_store import read_full_history
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from missed_opportunity_auditor import audit_missed_opportunities
from runtime_utils import parse_time
from signal_enums import SignalSide


ACTIONABLE_CATEGORIES = {"structure", "opportunity", "ai"}


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _parse_time(value: object) -> datetime | None:
    return parse_time(_normalize_text(value))


def _resolve_action(entry: dict) -> str:
    action = _normalize_text(
        entry.get("opportunity_action", "")
        or entry.get("signal_side", "")
        or entry.get("risk_reward_direction", "")
    ).lower()
    if action in {SignalSide.LONG.value, "buy", "bullish"}:
        return SignalSide.LONG.value
    if action in {SignalSide.SHORT.value, "sell", "bearish"}:
        return SignalSide.SHORT.value
    title = _normalize_text(entry.get("title", ""))
    detail = _normalize_text(entry.get("detail", ""))
    text = f"{title} {detail}"
    if any(token in text for token in ("做多", "多单", "偏多")):
        return SignalSide.LONG.value
    if any(token in text for token in ("做空", "空单", "偏空")):
        return SignalSide.SHORT.value
    return SignalSide.NEUTRAL.value


def _is_actionable_alert(entry: dict) -> bool:
    category = _normalize_text(entry.get("category", "")).lower()
    if category not in ACTIONABLE_CATEGORIES:
        return False
    if _resolve_action(entry) not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return False
    if bool(entry.get("opportunity_is_actionable", False)):
        return True
    if _normalize_text(entry.get("opportunity_push_level", "")).lower() == "push":
        return True
    if bool(entry.get("risk_reward_ready", False)):
        return True
    if float(entry.get("risk_reward_ratio", 0.0) or 0.0) > 0:
        return True
    return category == "ai"


def _alert_signature(entry: dict, symbol: str, occurred_at: str, action: str) -> str:
    raw = _normalize_text(entry.get("signature", ""))
    if raw:
        return raw
    basis = "|".join(
        [
            _normalize_text(entry.get("category", "")),
            _normalize_text(entry.get("title", "")),
            symbol,
            occurred_at,
            action,
        ]
    )
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()


def _find_nearest_snapshot(conn, symbol: str, occurred_at: str, tolerance_min: int) -> dict:
    alert_time = _parse_time(occurred_at)
    if alert_time is None:
        return {}
    rows = conn.execute(
        """
        SELECT id, symbol, snapshot_time, latest_price
        FROM market_snapshots
        WHERE symbol = ?
        ORDER BY snapshot_time DESC, id DESC
        LIMIT 800
        """,
        (symbol,),
    ).fetchall()
    best = None
    best_delta = None
    for row in rows:
        snapshot_time = _parse_time(row["snapshot_time"])
        if snapshot_time is None:
            continue
        delta = abs((snapshot_time - alert_time).total_seconds())
        if delta > max(1, int(tolerance_min)) * 60:
            continue
        if best_delta is None or delta < best_delta:
            best = row
            best_delta = delta
    if not best:
        return {}
    return {
        "snapshot_id": int(best["id"]),
        "snapshot_time": _normalize_text(best["snapshot_time"]),
        "latest_price": float(best["latest_price"] or 0.0),
        "delta_sec": float(best_delta or 0.0),
    }


def _load_outcome(conn, snapshot_id: int, horizon_min: int) -> dict:
    row = conn.execute(
        """
        SELECT horizon_min, future_snapshot_time, price_change_pct, mfe_pct, mae_pct, outcome_label, signal_quality
        FROM snapshot_outcomes
        WHERE snapshot_id = ? AND horizon_min = ?
        LIMIT 1
        """,
        (int(snapshot_id), int(horizon_min)),
    ).fetchone()
    if not row:
        return {}
    return {
        "horizon_min": int(row["horizon_min"] or horizon_min),
        "future_snapshot_time": _normalize_text(row["future_snapshot_time"]),
        "price_change_pct": float(row["price_change_pct"] or 0.0),
        "mfe_pct": float(row["mfe_pct"] or 0.0),
        "mae_pct": float(row["mae_pct"] or 0.0),
        "outcome_label": _normalize_text(row["outcome_label"]).lower() or "unknown",
        "signal_quality": _normalize_text(row["signal_quality"]).lower() or "neutral",
    }


def _pick_float(entry: dict, *keys: str) -> float:
    for key in keys:
        try:
            value = float(entry.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _resolve_r_metrics(entry: dict, snapshot: dict, outcome: dict, action: str) -> dict:
    entry_price = _pick_float(entry, "entry_price", "price", "baseline_latest_price", "latest_price")
    if entry_price <= 0:
        entry_price = float(snapshot.get("latest_price", 0.0) or 0.0)
    stop_loss = _pick_float(entry, "stop_loss_price", "sl", "opportunity_stop_price", "risk_reward_stop_price")
    if entry_price <= 0 or stop_loss <= 0:
        return {"max_favorable_r": 0.0, "max_adverse_r": 0.0, "reached_1r": False}
    risk_distance = abs(entry_price - stop_loss)
    if risk_distance <= 0:
        return {"max_favorable_r": 0.0, "max_adverse_r": 0.0, "reached_1r": False}

    # snapshot_outcomes 已按信号方向计算 MFE/MAE 百分比，可直接折算为价格距离。
    favorable_move = entry_price * float(outcome.get("mfe_pct", 0.0) or 0.0) / 100.0
    adverse_move = entry_price * float(outcome.get("mae_pct", 0.0) or 0.0) / 100.0
    max_favorable_r = max(0.0, favorable_move / risk_distance)
    max_adverse_r = max(0.0, adverse_move / risk_distance)
    return {
        "max_favorable_r": max_favorable_r,
        "max_adverse_r": max_adverse_r,
        "reached_1r": max_favorable_r >= 1.0,
    }


def backfill_alert_effect_outcomes(
    *,
    history_file: Path | str | None = None,
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    tolerance_min: int = 180,
    now: datetime | None = None,
) -> dict:
    """把已推送的交易提醒和后续行情结果对齐，形成提醒质量样本。"""
    entries = read_full_history(history_file=Path(history_file) if history_file else None)
    checked_count = 0
    inserted_count = 0
    skipped_count = 0
    missing_snapshot_count = 0
    missing_outcome_count = 0
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    created_at = _now_text(now)

    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        for entry in entries:
            if not isinstance(entry, dict) or not _is_actionable_alert(entry):
                skipped_count += 1
                continue
            checked_count += 1
            symbol = _normalize_text(entry.get("symbol", "")).upper()
            if not symbol:
                title_head = _normalize_text(entry.get("title", "")).split(" ", 1)[0].upper()
                symbol = title_head if title_head.isascii() else ""
            occurred_at = _normalize_text(entry.get("occurred_at", ""))
            action = _resolve_action(entry)
            if not symbol or not occurred_at:
                skipped_count += 1
                continue

            snapshot = _find_nearest_snapshot(conn, symbol, occurred_at, tolerance_min=tolerance_min)
            if not snapshot:
                missing_snapshot_count += 1
                continue
            outcome = _load_outcome(conn, int(snapshot["snapshot_id"]), int(horizon_min))
            if not outcome:
                missing_outcome_count += 1
                continue

            signature = _alert_signature(entry, symbol, occurred_at, action)
            r_metrics = _resolve_r_metrics(entry, snapshot, outcome, action)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO alert_effect_outcomes (
                    alert_signature, category, title, symbol, action, occurred_at,
                    snapshot_id, snapshot_time, horizon_min, outcome_label, signal_quality,
                    price_change_pct, mfe_pct, mae_pct, max_favorable_r, max_adverse_r,
                    reached_1r, meta_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signature,
                    _normalize_text(entry.get("category", "")),
                    _normalize_text(entry.get("title", "")),
                    symbol,
                    action,
                    occurred_at,
                    int(snapshot["snapshot_id"]),
                    _normalize_text(snapshot["snapshot_time"]),
                    int(horizon_min),
                    _normalize_text(outcome["outcome_label"]),
                    _normalize_text(outcome["signal_quality"]),
                    float(outcome["price_change_pct"]),
                    float(outcome["mfe_pct"]),
                    float(outcome["mae_pct"]),
                    float(r_metrics["max_favorable_r"]),
                    float(r_metrics["max_adverse_r"]),
                    1 if bool(r_metrics["reached_1r"]) else 0,
                    json.dumps(
                        {
                            "detail": _normalize_text(entry.get("detail", "")),
                            "risk_reward_ratio": float(entry.get("risk_reward_ratio", 0.0) or 0.0),
                            "opportunity_score": float(entry.get("opportunity_score", 0.0) or 0.0),
                            "stop_loss_price": stop_loss if (stop_loss := _pick_float(entry, "stop_loss_price", "sl", "opportunity_stop_price", "risk_reward_stop_price")) > 0 else 0.0,
                            "snapshot_delta_sec": float(snapshot.get("delta_sec", 0.0) or 0.0),
                        },
                        ensure_ascii=False,
                    ),
                    created_at,
                ),
            )
            if cursor.rowcount > 0:
                inserted_count += 1

    return {
        "checked_count": checked_count,
        "inserted_count": inserted_count,
        "skipped_count": skipped_count,
        "missing_snapshot_count": missing_snapshot_count,
        "missing_outcome_count": missing_outcome_count,
        "horizon_min": int(horizon_min),
    }


def summarize_alert_effect_outcomes(
    *,
    db_path: Path | str | None = None,
    horizon_min: int = 30,
    limit: int = 5,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        rows = conn.execute(
            """
            SELECT outcome_label, COUNT(*) AS count
            FROM alert_effect_outcomes
            WHERE horizon_min = ?
            GROUP BY outcome_label
            """,
            (int(horizon_min),),
        ).fetchall()
        recent_rows = conn.execute(
            """
            SELECT title, symbol, action, outcome_label, mfe_pct, mae_pct, max_favorable_r, max_adverse_r, reached_1r, occurred_at
            FROM alert_effect_outcomes
            WHERE horizon_min = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(horizon_min), max(1, int(limit))),
        ).fetchall()
        r_row = conn.execute(
            """
            SELECT
                COUNT(*) AS total_count,
                SUM(CASE WHEN reached_1r = 1 THEN 1 ELSE 0 END) AS reached_1r_count,
                AVG(CASE WHEN max_favorable_r > 0 THEN max_favorable_r ELSE NULL END) AS avg_favorable_r
            FROM alert_effect_outcomes
            WHERE horizon_min = ?
            """,
            (int(horizon_min),),
        ).fetchone()

    counts = {str(row["outcome_label"]): int(row["count"]) for row in rows}
    total = sum(counts.values())
    useful = counts.get("success", 0) + counts.get("mixed", 0)
    useful_rate = useful / total if total > 0 else 0.0
    r_payload = dict(r_row) if r_row else {}
    reached_1r_count = int(r_payload.get("reached_1r_count", 0) or 0)
    reached_1r_rate = reached_1r_count / total if total > 0 else 0.0
    avg_favorable_r = float(r_payload.get("avg_favorable_r", 0.0) or 0.0)
    return {
        "total_count": total,
        "success_count": counts.get("success", 0),
        "mixed_count": counts.get("mixed", 0),
        "fail_count": counts.get("fail", 0),
        "observe_count": counts.get("observe", 0),
        "useful_rate": useful_rate,
        "reached_1r_count": reached_1r_count,
        "reached_1r_rate": reached_1r_rate,
        "avg_favorable_r": avg_favorable_r,
        "recent_rows": [dict(row) for row in recent_rows],
        "summary_text": (
            f"提醒后 {int(horizon_min)} 分钟效果样本 {total} 条；"
            f"成功 {counts.get('success', 0)}，混合 {counts.get('mixed', 0)}，"
            f"失败 {counts.get('fail', 0)}，观察 {counts.get('observe', 0)}，"
            f"有效率 {useful_rate * 100:.0f}%，给到 1R 机会 {reached_1r_count} 条"
            f"（{reached_1r_rate * 100:.0f}%）。"
        ),
    }


def backfill_missed_opportunity_samples(
    *,
    db_path: Path | str | None = None,
    symbols: list[str] | tuple[str, ...] | None = None,
    horizon_min: int = 30,
    limit_per_symbol: int = 30,
    now: datetime | None = None,
) -> dict:
    """把漏掉的大波动样本固化到知识库，后续用于阈值和提醒策略复盘。"""
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    created_at = _now_text(now)
    symbol_list = [str(item or "").strip().upper() for item in (symbols or ("XAUUSD", "XAGUSD")) if str(item or "").strip()]
    inserted_count = 0
    analyzed_count = 0
    missed_count = 0
    reason_counts: dict[str, int] = {}

    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        for symbol in symbol_list:
            report = audit_missed_opportunities(
                db_path=target_db,
                symbol=symbol,
                horizon_min=horizon_min,
                limit=limit_per_symbol,
            )
            analyzed_count += int(report.get("analyzed_snapshots", 0) or 0)
            missed_count += int(report.get("missed_count", 0) or 0)
            for row in list(report.get("reason_summary", []) or []):
                key = _normalize_text(row.get("reason_key", ""))
                if key:
                    reason_counts[key] = int(reason_counts.get(key, 0) or 0) + int(row.get("count", 0) or 0)
            for item in list(report.get("top_missed", []) or []):
                snapshot_id = int(item.get("snapshot_id", 0) or 0)
                if snapshot_id <= 0:
                    continue
                best_side = _normalize_text(item.get("best_side", "")).lower() or SignalSide.NEUTRAL.value
                upside_pct = float(item.get("upside_pct", 0.0) or 0.0)
                downside_pct = float(item.get("downside_pct", 0.0) or 0.0)
                if best_side == SignalSide.SHORT.value:
                    mfe_pct = downside_pct
                    mae_pct = upside_pct
                    price_change_pct = -downside_pct
                else:
                    mfe_pct = upside_pct
                    mae_pct = downside_pct
                    price_change_pct = upside_pct
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO missed_opportunity_samples (
                        snapshot_id, symbol, snapshot_time, horizon_min, best_side,
                        reason_key, reason_label, mfe_pct, mae_pct, price_change_pct,
                        meta_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot_id,
                        _normalize_text(item.get("symbol", "")).upper() or symbol,
                        _normalize_text(item.get("snapshot_time", "")),
                        int(horizon_min),
                        best_side,
                        _normalize_text(item.get("reason_key", "")),
                        _normalize_text(item.get("reason_label", "")),
                        float(mfe_pct),
                        float(mae_pct),
                        float(price_change_pct),
                        json.dumps(item, ensure_ascii=False),
                        created_at,
                    ),
                )
                if cursor.rowcount > 0:
                    inserted_count += 1

    return {
        "symbols": symbol_list,
        "horizon_min": int(horizon_min),
        "analyzed_count": analyzed_count,
        "missed_count": missed_count,
        "inserted_count": inserted_count,
        "reason_counts": reason_counts,
    }


def summarize_missed_opportunity_samples(
    *,
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        total_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM missed_opportunity_samples
            WHERE horizon_min = ?
            """,
            (int(horizon_min),),
        ).fetchone()
        rows = conn.execute(
            """
            SELECT reason_key, reason_label, COUNT(*) AS count
            FROM missed_opportunity_samples
            WHERE horizon_min = ?
            GROUP BY reason_key, reason_label
            ORDER BY count DESC, reason_key ASC
            LIMIT 5
            """,
            (int(horizon_min),),
        ).fetchall()
    reason_summary = [dict(row) for row in rows]
    total_count = int(total_row["count"] if total_row else 0)
    top_reason = reason_summary[0] if reason_summary else {}
    top_text = (
        f"，主要原因：{_normalize_text(top_reason.get('reason_label', ''))} {int(top_reason.get('count', 0) or 0)} 条"
        if top_reason
        else ""
    )
    return {
        "total_count": total_count,
        "reason_summary": reason_summary,
        "summary_text": f"漏机会样本 {total_count} 条{top_text}。",
    }
