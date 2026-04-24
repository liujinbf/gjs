"""
知识库运行时学习：记录市场快照，并回标后续结果，形成可验证的策略样本。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from runtime_utils import parse_time as _parse_time_impl

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from quote_models import QuoteRow
from signal_side_utils import derive_signal_side_meta
from signal_enums import AlertTone, SignalSide, TradeGrade


def _connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    return open_knowledge_connection(target, ensure_schema=True)


def _now_text(now: datetime | None = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _normalize_snapshot_item(item: dict | QuoteRow | None) -> dict:
    """将快照项中的报价核心字段先归一化，再保留其余分析字段。"""
    return QuoteRow.from_payload(item).to_dict()


# P-004 修复（DEFECT-004）：委托给公共 runtime_utils.parse_time，消除第4个重复定义。
def _parse_time(value: str) -> datetime | None:
    return _parse_time_impl(value)


def _get_direction_threshold_pct(symbol: str) -> float:
    symbol_key = _normalize_text(symbol).upper()
    if symbol_key.startswith("XAU"):
        return 0.18
    if symbol_key.startswith("XAG"):
        return 0.30
    return 0.08


def _infer_signal_side(item: dict) -> str:
    meta = derive_signal_side_meta(item)
    return _normalize_text(meta.get("signal_side", SignalSide.NEUTRAL.value)).lower() or SignalSide.NEUTRAL.value


def _build_feature_payload(snapshot: dict, item: dict) -> dict:
    signal_side_meta = derive_signal_side_meta(item)
    return {
        "atr14": float(item.get("atr14", 0.0) or 0.0),
        "atr14_h4": float(item.get("atr14_h4", 0.0) or 0.0),
        "regime_tag": _normalize_text(item.get("regime_tag", "")),
        "regime_text": _normalize_text(item.get("regime_text", "")),
        "regime_reason": _normalize_text(item.get("regime_reason", "")),
        "setup_kind": _normalize_text(item.get("setup_kind", "")),
        "trade_grade_detail": _normalize_text(item.get("trade_grade_detail", "")),
        "trade_next_review": _normalize_text(item.get("trade_next_review", "")),
        "signal_side": _normalize_text(item.get("signal_side", "")) or _normalize_text(signal_side_meta.get("signal_side", "")),
        "signal_side_text": _normalize_text(item.get("signal_side_text", "")) or _normalize_text(signal_side_meta.get("signal_side_text", "")),
        "signal_side_basis": _normalize_text(item.get("signal_side_basis", "")) or _normalize_text(signal_side_meta.get("signal_side_basis", "")),
        "signal_side_reason": _normalize_text(item.get("signal_side_reason", "")) or _normalize_text(signal_side_meta.get("signal_side_reason", "")),
        "signal_side_long_votes": int(item.get("signal_side_long_votes", signal_side_meta.get("signal_side_long_votes", 0)) or 0),
        "signal_side_short_votes": int(item.get("signal_side_short_votes", signal_side_meta.get("signal_side_short_votes", 0)) or 0),
        "status_text": _normalize_text(item.get("status_text", "")),
        "quote_text": _normalize_text(item.get("quote_text", "")),
        "execution_note": _normalize_text(item.get("execution_note", "")),
        "event_scope_text": _normalize_text(item.get("event_scope_text", "")),
        "event_mode_text": _normalize_text(item.get("event_mode_text", "")),
        "intraday_bias_text": _normalize_text(item.get("intraday_bias_text", "")),
        "intraday_volatility_text": _normalize_text(item.get("intraday_volatility_text", "")),
        "intraday_location_text": _normalize_text(item.get("intraday_location_text", "")),
        "multi_timeframe_alignment_text": _normalize_text(item.get("multi_timeframe_alignment_text", "")),
        "multi_timeframe_bias_text": _normalize_text(item.get("multi_timeframe_bias_text", "")),
        "key_level_state_text": _normalize_text(item.get("key_level_state_text", "")),
        "breakout_state_text": _normalize_text(item.get("breakout_state_text", "")),
        "retest_state_text": _normalize_text(item.get("retest_state_text", "")),
        "intraday_bias": _normalize_text(item.get("intraday_bias", "")),
        "intraday_volatility": _normalize_text(item.get("intraday_volatility", "")),
        "intraday_location": _normalize_text(item.get("intraday_location", "")),
        "multi_timeframe_alignment": _normalize_text(item.get("multi_timeframe_alignment", "")),
        "multi_timeframe_bias": _normalize_text(item.get("multi_timeframe_bias", "")),
        "key_level_state": _normalize_text(item.get("key_level_state", "")),
        "breakout_state": _normalize_text(item.get("breakout_state", "")),
        "breakout_direction": _normalize_text(item.get("breakout_direction", "")),
        "retest_state": _normalize_text(item.get("retest_state", "")),
        "risk_reward_ready": bool(item.get("risk_reward_ready", False)),
        "risk_reward_state": _normalize_text(item.get("risk_reward_state", "")),
        "risk_reward_state_text": _normalize_text(item.get("risk_reward_state_text", "")),
        "risk_reward_ratio": float(item.get("risk_reward_ratio", 0.0) or 0.0),
        "risk_reward_direction": _normalize_text(item.get("risk_reward_direction", "")),
        "risk_reward_stop_price": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        "risk_reward_target_price": float(item.get("risk_reward_target_price", 0.0) or 0.0),
        "risk_reward_target_price_2": float(item.get("risk_reward_target_price_2", 0.0) or 0.0),
        "risk_reward_entry_zone_low": float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0),
        "risk_reward_entry_zone_high": float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0),
        "risk_reward_atr": float(item.get("risk_reward_atr", 0.0) or 0.0),
        "quote_live_reason": _normalize_text(item.get("quote_live_reason", "")),
        "quote_live_reason_text": _normalize_text(item.get("quote_live_reason_text", "")),
        "quote_live_diagnostic_text": _normalize_text(item.get("quote_live_diagnostic_text", "")),
        "quote_live_delta_sec": float(item.get("quote_live_delta_sec", 0.0) or 0.0),
        "quote_live_max_age_sec": float(item.get("quote_live_max_age_sec", 0.0) or 0.0),
        "quote_broker_offset_sec": float(item.get("quote_broker_offset_sec", 0.0) or 0.0),
        "quote_offset_recalibrated": bool(item.get("quote_offset_recalibrated", False)),
        "quote_price_available": bool(item.get("quote_price_available", False)),
        "quote_tick_utc_text": _normalize_text(item.get("quote_tick_utc_text", "")),
        "quote_now_utc_text": _normalize_text(item.get("quote_now_utc_text", "")),
        "model_ready": bool(item.get("model_ready", False)),
        "model_win_probability": float(item.get("model_win_probability", 0.0) or 0.0),
        "model_base_win_probability": float(item.get("model_base_win_probability", 0.0) or 0.0),
        "execution_model_ready": bool(item.get("execution_model_ready", False)),
        "execution_open_probability": float(item.get("execution_open_probability", 0.0) or 0.0),
        "execution_model_base_probability": float(item.get("execution_model_base_probability", 0.0) or 0.0),
        "execution_model_confidence_text": _normalize_text(item.get("execution_model_confidence_text", "")),
        "execution_model_note": _normalize_text(item.get("execution_model_note", "")),
        "summary_text": _normalize_text(snapshot.get("summary_text", "")),
        "event_risk_reason": _normalize_text(snapshot.get("event_risk_reason", "")),
    }


def _cleanup_old_snapshots(conn: sqlite3.Connection, retain_days: int = 30) -> int:
    """M-007 修复：清理超过 retain_days 天且已完成回标的快照，防止数据库无限增长。"""
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(days=max(7, retain_days))).strftime("%Y-%m-%d %H:%M:%S")
    # 找到已完成所有回标时间窗口（15/30/60分钟均已回标）且超过保留期的快照
    old_ids = [
        int(row[0])
        for row in conn.execute(
            """
            SELECT ms.id
            FROM market_snapshots ms
            WHERE ms.snapshot_time < ?
              AND (
                SELECT COUNT(*) FROM snapshot_outcomes so WHERE so.snapshot_id = ms.id
              ) >= 3
            ORDER BY ms.snapshot_time ASC
            LIMIT 5000
            """,
            (cutoff,),
        ).fetchall()
    ]
    if not old_ids:
        return 0
    placeholders = ",".join("?" * len(old_ids))
    # DEFECT-003 修复：三个 DELETE 用显式事务包裹，防止中途崩溃产生孤児记录。
    with conn:
        conn.execute(f"DELETE FROM rule_snapshot_matches WHERE snapshot_id IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM snapshot_outcomes WHERE snapshot_id IN ({placeholders})", old_ids)
        conn.execute(f"DELETE FROM market_snapshots WHERE id IN ({placeholders})", old_ids)
    return len(old_ids)


def record_snapshot(snapshot: dict, db_path: Path | str | None = None) -> dict:
    snapshot_time = _normalize_text((snapshot or {}).get("last_refresh_text", ""))
    if not snapshot_time:
        snapshot_time = _now_text()

    items = [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]
    inserted_count = 0
    inserted_snapshot_ids = []
    snapshot_bindings = {}
    with _connect(db_path) as conn:
        # M-007 修复：每次写入前检查并清理超出保留期的旧快照
        _cleanup_old_snapshots(conn, retain_days=30)

        for item in items:
            symbol = _normalize_text(item.get("symbol", "")).upper()
            if not symbol:
                continue
            feature_json = json.dumps(_build_feature_payload(snapshot, item), ensure_ascii=False)
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO market_snapshots (
                    snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
                    trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
                    event_active_name, event_importance_text, event_note, signal_side,
                    regime_tag, regime_text, feature_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_time,
                    symbol,
                    float(item.get("latest_price", 0.0) or 0.0),
                    float(item.get("spread_points", 0.0) or 0.0),
                    1 if bool(item.get("has_live_quote", False)) else 0,
                    _normalize_text(item.get("tone", "")) or AlertTone.NEUTRAL.value,
                    _normalize_text(item.get("trade_grade", "")),
                    _normalize_text(item.get("trade_grade_source", "")),
                    _normalize_text(item.get("alert_state_text", "")),
                    _normalize_text(snapshot.get("event_risk_mode_text", "")),
                    _normalize_text(item.get("event_active_name", "")) or _normalize_text(snapshot.get("event_active_name", "")),
                    _normalize_text(item.get("event_importance_text", "")),
                    _normalize_text(item.get("event_note", "")),
                    _infer_signal_side(item),
                    _normalize_text(item.get("regime_tag", "")) or _normalize_text(snapshot.get("regime_tag", "")),
                    _normalize_text(item.get("regime_text", "")) or _normalize_text(snapshot.get("regime_text", "")),
                    feature_json,
                    snapshot_time,
                ),
            )
            if cursor.rowcount > 0:
                inserted_count += 1
                inserted_snapshot_ids.append(int(cursor.lastrowid))
                snapshot_bindings[symbol] = int(cursor.lastrowid)
                continue
            existing = conn.execute(
                """
                SELECT id
                FROM market_snapshots
                WHERE snapshot_time = ? AND symbol = ?
                LIMIT 1
                """,
                (snapshot_time, symbol),
            ).fetchone()
            if existing is not None:
                snapshot_bindings[symbol] = int(existing["id"])

    return {
        "snapshot_time": snapshot_time,
        "inserted_count": inserted_count,
        "item_count": len(items),
        "inserted_snapshot_ids": inserted_snapshot_ids,
        "snapshot_bindings": snapshot_bindings,
    }


def _compute_directional_metrics(base_price: float, future_price: float, max_price: float, min_price: float, side: str) -> dict:
    if base_price <= 0:
        return {
            "price_change_pct": 0.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
        }

    if side == SignalSide.SHORT.value:
        directional_change = (base_price - future_price) / base_price * 100.0
        mfe_pct = (base_price - min_price) / base_price * 100.0
        mae_pct = (max_price - base_price) / base_price * 100.0
    else:
        directional_change = (future_price - base_price) / base_price * 100.0
        mfe_pct = (max_price - base_price) / base_price * 100.0
        mae_pct = (base_price - min_price) / base_price * 100.0

    return {
        "price_change_pct": directional_change,
        "mfe_pct": max(mfe_pct, 0.0),
        "mae_pct": max(mae_pct, 0.0),
    }


def _label_outcome(symbol: str, side: str, trade_grade: str, directional_change: float, mfe_pct: float, mae_pct: float) -> tuple[str, str]:
    if trade_grade != TradeGrade.LIGHT_POSITION or side not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return "observe", "neutral"

    threshold = _get_direction_threshold_pct(symbol)
    if mfe_pct >= threshold and mae_pct <= threshold * 0.8:
        return "success", "high"
    if directional_change <= -(threshold * 0.6) or mae_pct >= threshold * 1.2:
        return "fail", "low"
    return "mixed", "medium"


def backfill_snapshot_outcomes(
    db_path: Path | str | None = None,
    now: datetime | None = None,
    horizons_min: tuple[int, ...] = (15, 30, 60),
) -> dict:
    current = now or datetime.now()
    labeled_count = 0
    labeled_snapshot_ids = set()
    with _connect(db_path) as conn:
        snapshots = conn.execute(
            """
            SELECT id, snapshot_time, symbol, latest_price, spread_points, trade_grade, signal_side
            FROM market_snapshots
            ORDER BY snapshot_time ASC, id ASC
            """
        ).fetchall()

        for row in snapshots:
            snapshot_time = _parse_time(str(row["snapshot_time"]))
            if snapshot_time is None:
                continue

            for horizon in tuple(int(item) for item in horizons_min if int(item) > 0):
                already = conn.execute(
                    "SELECT 1 FROM snapshot_outcomes WHERE snapshot_id = ? AND horizon_min = ?",
                    (int(row["id"]), horizon),
                ).fetchone()
                if already:
                    continue

                cutoff_time = snapshot_time + timedelta(minutes=horizon)
                if cutoff_time > current:
                    continue

                future_rows = conn.execute(
                    """
                    SELECT snapshot_time, latest_price, spread_points
                    FROM market_snapshots
                    WHERE symbol = ? AND snapshot_time > ? AND snapshot_time <= ?
                    ORDER BY snapshot_time ASC
                    """,
                    (
                        str(row["symbol"]),
                        snapshot_time.strftime("%Y-%m-%d %H:%M:%S"),
                        cutoff_time.strftime("%Y-%m-%d %H:%M:%S"),
                    ),
                ).fetchall()
                if not future_rows:
                    continue

                base_price = float(row["latest_price"] or 0.0)
                prices = [float(item["latest_price"] or 0.0) for item in future_rows if float(item["latest_price"] or 0.0) > 0]
                if base_price <= 0 or not prices:
                    continue

                future_row = future_rows[-1]
                future_price = float(future_row["latest_price"] or 0.0)
                future_spread_points = float(future_row["spread_points"] or 0.0)
                max_price = max(prices)
                min_price = min(prices)
                metrics = _compute_directional_metrics(
                    base_price=base_price,
                    future_price=future_price,
                    max_price=max_price,
                    min_price=min_price,
                    side=str(row["signal_side"] or SignalSide.NEUTRAL.value),
                )
                outcome_label, signal_quality = _label_outcome(
                    symbol=str(row["symbol"]),
                    side=str(row["signal_side"] or SignalSide.NEUTRAL.value),
                    trade_grade=str(row["trade_grade"] or ""),
                    directional_change=float(metrics["price_change_pct"]),
                    mfe_pct=float(metrics["mfe_pct"]),
                    mae_pct=float(metrics["mae_pct"]),
                )

                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO snapshot_outcomes (
                        snapshot_id, symbol, snapshot_time, horizon_min, future_snapshot_time,
                        future_price, future_spread_points, price_change_pct, max_price, min_price,
                        mfe_pct, mae_pct, outcome_label, signal_quality, labeled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(row["id"]),
                        str(row["symbol"]),
                        snapshot_time.strftime("%Y-%m-%d %H:%M:%S"),
                        horizon,
                        _normalize_text(future_row["snapshot_time"]),
                        future_price,
                        future_spread_points,
                        float(metrics["price_change_pct"]),
                        max_price,
                        min_price,
                        float(metrics["mfe_pct"]),
                        float(metrics["mae_pct"]),
                        outcome_label,
                        signal_quality,
                        _now_text(current),
                    ),
                )
                if cursor.rowcount > 0:
                    labeled_count += 1
                    labeled_snapshot_ids.add(int(row["id"]))

    return {
        "labeled_count": labeled_count,
        "horizons": list(horizons_min),
        "labeled_snapshot_ids": sorted(labeled_snapshot_ids),
    }


def summarize_outcome_stats(
    db_path: Path | str | None = None,
    horizon_min: int = 30,
) -> dict:
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT outcome_label, COUNT(*) AS count
            FROM snapshot_outcomes
            WHERE horizon_min = ?
            GROUP BY outcome_label
            """,
            (int(horizon_min),),
        ).fetchall()
    counts = {str(row["outcome_label"]): int(row["count"]) for row in rows}
    total = sum(counts.values())
    return {
        "total_count": total,
        "success_count": counts.get("success", 0),
        "mixed_count": counts.get("mixed", 0),
        "fail_count": counts.get("fail", 0),
        "observe_count": counts.get("observe", 0),
        "summary_text": (
            f"{horizon_min} 分钟结果样本 {total} 条；"
            f"成功 {counts.get('success', 0)} 条，混合 {counts.get('mixed', 0)} 条，"
            f"失败 {counts.get('fail', 0)} 条，观察 {counts.get('observe', 0)} 条。"
        ),
    }
