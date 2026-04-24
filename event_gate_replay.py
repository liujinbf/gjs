"""
事件窗口规则回放器。

用途：
1. 把历史上因事件窗口被拦截的快照，重新喂给当前规则回放。
2. 统计新规则会额外放行多少“事件后延续候选”。
3. 对未放行样本继续归因，回答“为什么还是放不出来”。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from monitor_rules import _can_release_post_event_continuation, _symbol_family, build_trade_grade
from runtime_utils import parse_time
from signal_enums import SignalSide, TradeGrade


def _normalize_text(value: object) -> str:
    return " ".join(str(value or "").replace("\n", " ").split()).strip()


def _load_feature_payload(raw_json: str) -> dict:
    try:
        payload = json.loads(str(raw_json or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _direction_threshold_pct(symbol: str) -> float:
    symbol_key = _normalize_text(symbol).upper()
    if symbol_key.startswith("XAU"):
        return 0.18
    if symbol_key.startswith("XAG"):
        return 0.30
    return 0.08


_BLOCK_REASON_LABELS = {
    "released": "已放行到结构层",
    "not_post_event": "不是事件后窗口",
    "not_target_symbol": "事件不指向当前品种",
    "not_metal": "非贵金属",
    "importance_not_high": "非高影响事件",
    "tone_not_clean": "点差/报价状态不干净",
    "inactive_quote": "无活跃报价",
    "mtf_not_aligned": "多周期未同向",
    "intraday_not_synced": "短线方向或波动未同步",
    "rr_not_ready": "盈亏比未准备好",
    "rr_ratio_too_low": "盈亏比仍偏低",
    "direction_conflict": "方向字段冲突",
    "no_confirmation": "突破/回踩确认不足",
    "structure_not_light": "结构层复评后仍未到试仓级别",
    "unknown": "原因未知",
}


def _build_event_context(row: dict) -> dict:
    symbol = _normalize_text(row.get("symbol", "")).upper()
    return {
        "mode": "post_event",
        "mode_text": _normalize_text(row.get("event_risk_mode_text", "")) or "事件落地观察",
        "active_event_name": _normalize_text(row.get("event_active_name", "")),
        "active_event_importance": "high" if _normalize_text(row.get("event_importance_text", "")) == "高影响" else "medium",
        "active_event_importance_text": _normalize_text(row.get("event_importance_text", "")),
        "active_event_symbols": [symbol] if symbol else [],
    }


def _build_replay_row(row: dict, features: dict) -> dict:
    payload = dict(features)
    intraday_ready = bool(
        _normalize_text(features.get("intraday_bias", ""))
        or _normalize_text(features.get("intraday_bias_text", ""))
    )
    multi_ready = bool(
        _normalize_text(features.get("multi_timeframe_alignment", ""))
        or _normalize_text(features.get("multi_timeframe_alignment_text", ""))
        or _normalize_text(features.get("multi_timeframe_bias", ""))
    )
    payload.update(
        {
            "symbol": _normalize_text(row.get("symbol", "")).upper(),
            "latest_price": float(row.get("latest_price", 0.0) or 0.0),
            "spread_points": float(row.get("spread_points", 0.0) or 0.0),
            "has_live_quote": bool(row.get("has_live_quote", False)),
            "quote_status_code": "live" if bool(row.get("has_live_quote", False)) else "inactive",
            "intraday_context_ready": intraday_ready,
            "multi_timeframe_context_ready": multi_ready,
            "key_level_ready": bool(_normalize_text(features.get("key_level_state", ""))),
            "breakout_ready": bool(_normalize_text(features.get("breakout_state", ""))),
            "retest_ready": bool(_normalize_text(features.get("retest_state", ""))),
        }
    )
    return payload


def _classify_block_reason(row: dict, replay_row: dict, event_context: dict, tone: str) -> str:
    symbol = _normalize_text(row.get("symbol", "")).upper()
    family = _symbol_family(symbol)
    if family != "metal":
        return "not_metal"
    if _normalize_text(row.get("event_risk_mode_text", "")) != "事件落地观察":
        return "not_post_event"
    if not event_context.get("active_event_symbols"):
        return "not_target_symbol"
    if _normalize_text(row.get("event_importance_text", "")) != "高影响":
        return "importance_not_high"
    if tone != "success":
        return "tone_not_clean"
    if not bool(replay_row.get("has_live_quote", False)):
        return "inactive_quote"

    multi_alignment = _normalize_text(replay_row.get("multi_timeframe_alignment", ""))
    multi_bias = _normalize_text(replay_row.get("multi_timeframe_bias", ""))
    if multi_alignment != "aligned" or multi_bias not in {"bullish", "bearish"}:
        return "mtf_not_aligned"

    intraday_bias = _normalize_text(replay_row.get("intraday_bias", ""))
    intraday_volatility = _normalize_text(replay_row.get("intraday_volatility", ""))
    if intraday_bias != multi_bias or intraday_volatility in {"", "low", "unknown"}:
        return "intraday_not_synced"

    if not bool(replay_row.get("risk_reward_ready", False)):
        return "rr_not_ready"
    if _normalize_text(replay_row.get("risk_reward_state", "")) not in {"acceptable", "good", "excellent", "favorable"}:
        return "rr_not_ready"
    if float(replay_row.get("risk_reward_ratio", 0.0) or 0.0) < 1.6:
        return "rr_ratio_too_low"

    breakout_direction = _normalize_text(replay_row.get("breakout_direction", ""))
    risk_reward_direction = _normalize_text(replay_row.get("risk_reward_direction", ""))
    if breakout_direction not in {"", "unknown", multi_bias}:
        return "direction_conflict"
    if risk_reward_direction not in {"", "unknown", multi_bias}:
        return "direction_conflict"

    breakout_state = _normalize_text(replay_row.get("breakout_state", ""))
    retest_state = _normalize_text(replay_row.get("retest_state", ""))
    if multi_bias == "bullish" and breakout_state != "confirmed_above" and retest_state != "confirmed_support":
        return "no_confirmation"
    if multi_bias == "bearish" and breakout_state != "confirmed_below" and retest_state != "confirmed_resistance":
        return "no_confirmation"
    return "structure_not_light"


def _resolve_replay_side(replay_row: dict) -> str:
    explicit = _normalize_text(replay_row.get("signal_side", "")).lower()
    if explicit in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return explicit

    for key in ("risk_reward_direction", "breakout_direction", "multi_timeframe_bias", "intraday_bias"):
        value = _normalize_text(replay_row.get(key, "")).lower()
        if value == "bullish":
            return SignalSide.LONG.value
        if value == "bearish":
            return SignalSide.SHORT.value
    return SignalSide.NEUTRAL.value


def _label_replay_outcome(symbol: str, replay_row: dict, row: dict) -> str:
    side = _resolve_replay_side(replay_row)
    if side not in {SignalSide.LONG.value, SignalSide.SHORT.value}:
        return "unknown"

    threshold = _direction_threshold_pct(symbol)
    mfe_pct = abs(float(row.get("mfe_pct", 0.0) or 0.0))
    mae_pct = abs(float(row.get("mae_pct", 0.0) or 0.0))
    directional_change = float(row.get("price_change_pct", 0.0) or 0.0)
    if side == SignalSide.SHORT.value:
        directional_change = -directional_change

    if mfe_pct >= threshold and mae_pct <= threshold * 0.8:
        return "success"
    if directional_change <= -(threshold * 0.6) or mae_pct >= threshold * 1.2:
        return "fail"
    return "mixed"


def replay_event_gate_rows(
    db_path: Path | str | None = None,
    symbol: str = "XAUUSD",
    horizon_min: int = 30,
    start_time: str = "",
    end_time: str = "",
    limit: int = 30,
    dedupe_minutes: int = 10,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    symbol_key = _normalize_text(symbol).upper() or "XAUUSD"
    clauses = [
        "ms.symbol = ?",
        "ms.event_risk_mode_text = '事件落地观察'",
        "ms.trade_grade_source = 'event'",
        "so.horizon_min = ?",
        "COALESCE(ms.regime_tag, '') NOT LIKE 'external_%'",
    ]
    params: list[object] = [symbol_key, int(horizon_min)]
    if start_time:
        clauses.append("ms.snapshot_time >= ?")
        params.append(start_time)
    if end_time:
        clauses.append("ms.snapshot_time <= ?")
        params.append(end_time)
    where_sql = " AND ".join(clauses)

    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        rows = conn.execute(
            f"""
            SELECT
                ms.id, ms.snapshot_time, ms.symbol, ms.latest_price, ms.spread_points, ms.has_live_quote,
                ms.tone, ms.trade_grade, ms.trade_grade_source, ms.alert_state_text,
                ms.event_risk_mode_text, ms.event_active_name, ms.event_importance_text, ms.event_note,
                ms.signal_side, ms.feature_json,
                so.outcome_label, so.signal_quality, so.price_change_pct, so.mfe_pct, so.mae_pct,
                so.max_price, so.min_price
            FROM market_snapshots ms
            JOIN snapshot_outcomes so ON so.snapshot_id = ms.id
            WHERE {where_sql}
            ORDER BY ms.snapshot_time ASC, ms.id ASC
            """,
            params,
        ).fetchall()

    total_rows = 0
    released_rows = 0
    released_counter: Counter[str] = Counter()
    blocked_counter: Counter[str] = Counter()
    last_cluster_at: datetime | None = None
    released_clusters = 0
    top_released: list[dict] = []

    for raw in rows:
        row = dict(raw)
        total_rows += 1
        snapshot_dt = parse_time(_normalize_text(row.get("snapshot_time", "")))
        features = _load_feature_payload(row.get("feature_json", "{}"))
        replay_row = _build_replay_row(row, features)
        tone = _normalize_text(row.get("tone", "")).lower() or "neutral"
        event_context = _build_event_context(row)

        continuation = _can_release_post_event_continuation(
            symbol_key,
            _symbol_family(symbol_key),
            replay_row,
            tone,
            "post_event",
            event_context=event_context,
        )
        replay_grade = build_trade_grade(
            symbol_key,
            replay_row,
            tone,
            connected=True,
            event_risk_mode="post_event",
            event_context=event_context,
        )
        released = (
            continuation is not None
            and _normalize_text(replay_grade.get("grade", "")) == TradeGrade.LIGHT_POSITION.value
            and _normalize_text(replay_grade.get("source", "")) == "structure"
        )
        if released:
            released_rows += 1
            outcome = _label_replay_outcome(symbol_key, replay_row, row)
            released_counter[outcome] += 1
            if snapshot_dt is not None:
                if last_cluster_at is None or snapshot_dt - last_cluster_at >= timedelta(minutes=max(0, int(dedupe_minutes))):
                    released_clusters += 1
                    last_cluster_at = snapshot_dt
            if len(top_released) < max(1, int(limit)):
                potential_move_pct = max(
                    abs(float(row.get("mfe_pct", 0.0) or 0.0)),
                    abs(float(row.get("price_change_pct", 0.0) or 0.0)),
                )
                top_released.append(
                    {
                        "snapshot_id": int(row.get("id", 0) or 0),
                        "snapshot_time": _normalize_text(row.get("snapshot_time", "")),
                        "event_name": _normalize_text(row.get("event_active_name", "")),
                        "replay_grade": _normalize_text(replay_grade.get("grade", "")),
                        "replay_source": _normalize_text(replay_grade.get("source", "")),
                        "replay_detail": _normalize_text(replay_grade.get("detail", "")),
                        "replay_outcome_label": outcome,
                        "risk_reward_ratio": float(replay_row.get("risk_reward_ratio", 0.0) or 0.0),
                        "multi_timeframe_alignment": _normalize_text(replay_row.get("multi_timeframe_alignment", "")),
                        "multi_timeframe_bias": _normalize_text(replay_row.get("multi_timeframe_bias", "")),
                        "intraday_bias": _normalize_text(replay_row.get("intraday_bias", "")),
                        "breakout_state": _normalize_text(replay_row.get("breakout_state", "")),
                        "retest_state": _normalize_text(replay_row.get("retest_state", "")),
                        "potential_move_pct": round(potential_move_pct, 4),
                    }
                )
            continue

        reason_key = _classify_block_reason(row, replay_row, event_context, tone)
        blocked_counter[reason_key] += 1

    return {
        "ok": True,
        "db_path": str(target_db),
        "symbol": symbol_key,
        "horizon_min": int(horizon_min),
        "start_time": start_time,
        "end_time": end_time,
        "total_event_rows": total_rows,
        "released_rows": released_rows,
        "released_clusters": released_clusters,
        "release_rate": (released_rows / max(total_rows, 1)),
        "released_outcomes": [
            {"outcome_label": key, "count": count}
            for key, count in released_counter.most_common()
        ],
        "blocked_summary": [
            {"reason_key": key, "reason_label": _BLOCK_REASON_LABELS.get(key, key), "count": count}
            for key, count in blocked_counter.most_common()
        ],
        "top_released": top_released,
    }


def write_event_gate_replay_report(report: dict, output_path: Path | str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回放事件窗口被拦截样本，看新规则能放行多少。")
    parser.add_argument("--db-path", default=str(KNOWLEDGE_DB_FILE), help="知识库 DB 路径")
    parser.add_argument("--symbol", default="XAUUSD", help="品种代码，默认 XAUUSD")
    parser.add_argument("--horizon-min", type=int, default=30, help="结果窗口，默认 30 分钟")
    parser.add_argument("--start-time", default="", help="开始时间，例如 2026-04-20 00:00:00")
    parser.add_argument("--end-time", default="", help="结束时间，例如 2026-04-22 23:59:59")
    parser.add_argument("--limit", type=int, default=20, help="输出最多展示多少条放行样本")
    parser.add_argument("--dedupe-minutes", type=int, default=10, help="放行簇去重分钟数")
    parser.add_argument("--output", default="", help="可选 JSON 输出路径")
    args = parser.parse_args(argv)

    report = replay_event_gate_rows(
        db_path=args.db_path,
        symbol=args.symbol,
        horizon_min=args.horizon_min,
        start_time=args.start_time,
        end_time=args.end_time,
        limit=args.limit,
        dedupe_minutes=args.dedupe_minutes,
    )
    if args.output:
        path = write_event_gate_replay_report(report, args.output)
        print(f"已写入回放报告：{path}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
