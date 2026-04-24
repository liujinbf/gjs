"""
错过行情复盘器。

用途：从知识库历史快照中找出“后续出现足够波动，但当时系统没有给可执行方向”的样本，
并归因到事件、点差、多周期、盈亏比、分级门槛等阻断原因。
"""
from __future__ import annotations

import argparse
import bisect
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
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


_REASON_LABELS = {
    "captured": "已给出同向机会",
    "inactive_quote": "非活跃报价",
    "inactive_stale_tick": "报价延迟/旧 tick",
    "inactive_no_price": "MT5 空报价/时间异常",
    "event_gate": "事件窗口拦截",
    "spread_gate": "点差/执行成本拦截",
    "rr_unknown": "盈亏比未知",
    "rr_poor": "盈亏比偏差",
    "mtf_mixed": "多周期分歧",
    "mtf_pending": "多周期待确认",
    "no_breakout_retest": "无突破/无回踩确认",
    "sideways": "短线震荡",
    "mid_range": "区间中段",
    "grade_gate": "未到试仓级别",
    "opposite_signal": "给了反向信号",
    "unknown": "原因未知",
}

_RECOMMENDATION_MAP = {
    "event_gate": {
        "title": "增加事件后延续候选",
        "action": "为高影响事件落地后 5-20 分钟增加小仓候选模式，只在点差恢复、M1/M5 动能延续且止损距离可控时放行。",
        "benefit": "减少 CPI、就业、利率等事件后第二段行情完全错过的问题。",
        "risk": "事件后波动路径更乱，必须限制风险倍率和每日触发次数。",
    },
    "rr_unknown": {
        "title": "补齐盈亏比降级估算",
        "action": "当关键位不足导致盈亏比未知时，使用 ATR + 最近摆动高低点生成临时止损/目标，并标记为低置信候选。",
        "benefit": "避免只因关键位缺失就完全失明，尤其适合强动能启动初期。",
        "risk": "临时目标不如结构关键位可靠，必须单独统计胜率。",
    },
    "inactive_quote": {
        "title": "修复报价活跃性误杀",
        "action": "复核 tick_time 与本地时间的判定，增加 MT5 重连、服务器时间偏移和静态报价恢复后的立即复评。",
        "benefit": "减少行情实际在动但系统判成非活跃的错过。",
        "risk": "放宽过度会把休市或假报价误判为可交易。",
    },
    "inactive_stale_tick": {
        "title": "校准旧 tick 诊断与时差漂移",
        "action": "记录 tick 延迟秒数、UTC 推算时间和 offset 重校准结果，优先修复旧 tick 被误判为离线的问题。",
        "benefit": "能区分真正停盘和时间偏移误杀，减少把活跃行情误归为无报价。",
        "risk": "若重校准条件过宽，可能把异常时钟也误当成活跃行情。",
    },
    "inactive_no_price": {
        "title": "补齐 MT5 空报价兜底",
        "action": "针对空 bid/ask、无 tick_time、品种未选中等场景分开记录并触发自愈，而不是统一落成非活跃。",
        "benefit": "更容易定位是 MT5 终端、品种配置还是交易时段本身的问题。",
        "risk": "诊断分支变多后，日志和告警也会更复杂，需要做好聚合。",
    },
    "rr_poor": {
        "title": "改进动态目标与止损",
        "action": "对趋势延续行情引入移动目标或分批止盈，不只用当前静态第一目标判定盈亏比。",
        "benefit": "减少强趋势中因第一目标过近、止损过宽而被判为盈亏比偏差。",
        "risk": "目标拉远后胜率会下降，需要用外部回放样本校准。",
    },
    "mtf_mixed": {
        "title": "增加早期动能候选",
        "action": "允许 M1/M5 与 M15 同向时生成观察候选，不强制等待 H1/H4 完全共振，但只进入候选层，不直接实盘。",
        "benefit": "更早发现启动行情，改善系统总是等确认太晚的问题。",
        "risk": "假启动数量会上升，需要更严格的止损和冷却。",
    },
    "no_breakout_retest": {
        "title": "识别无回踩直线动能",
        "action": "新增“无回踩动能延续”模式：连续实体、ATR 放大、点差稳定时允许小仓试探。",
        "benefit": "覆盖黄金常见的直线拉升/杀跌，不再完全依赖回踩确认。",
        "risk": "追单属性更强，必须限制在高动能且低点差环境。",
    },
    "spread_gate": {
        "title": "点差恢复后立即复评",
        "action": "点差从偏宽恢复后触发一次即时复评，而不是等下一轮普通刷新。",
        "benefit": "减少点差刚恢复时行情已经启动但系统还在等待的问题。",
        "risk": "恢复瞬间可能仍有跳价，需要保留最大点差保护。",
    },
}


def build_optimization_recommendations(reason_summary: list[dict], top_n: int = 5) -> list[dict]:
    recommendations: list[dict] = []
    for row in list(reason_summary or []):
        reason_key = _normalize_text(row.get("reason_key", ""))
        count = int(row.get("count", 0) or 0)
        if count <= 0 or reason_key not in _RECOMMENDATION_MAP:
            continue
        payload = dict(_RECOMMENDATION_MAP[reason_key])
        payload.update(
            {
                "reason_key": reason_key,
                "reason_label": _REASON_LABELS.get(reason_key, reason_key),
                "missed_count": count,
            }
        )
        recommendations.append(payload)
        if len(recommendations) >= max(1, int(top_n)):
            break
    return recommendations


def _is_same_direction_capture(row: dict, best_side: str) -> bool:
    grade = _normalize_text(row.get("trade_grade", ""))
    side = _normalize_text(row.get("signal_side", "")).lower()
    return grade == TradeGrade.LIGHT_POSITION.value and side == best_side


def _classify_block_reason(row: dict, features: dict, best_side: str) -> str:
    if _is_same_direction_capture(row, best_side):
        return "captured"

    side = _normalize_text(row.get("signal_side", "")).lower()
    if side in {SignalSide.LONG.value, SignalSide.SHORT.value} and side != best_side:
        return "opposite_signal"

    if not bool(row.get("has_live_quote", False)):
        inactive_reason = _normalize_text(features.get("quote_live_reason", "")).lower()
        if inactive_reason == "stale_tick":
            return "inactive_stale_tick"
        if inactive_reason in {"no_price", "no_tick", "no_tick_time"}:
            return "inactive_no_price"
        status_text = _normalize_text(features.get("status_text", ""))
        quote_text = _normalize_text(features.get("quote_text", ""))
        if "报价延迟" in status_text or quote_text or float(row.get("latest_price", 0.0) or 0.0) > 0:
            return "inactive_stale_tick"
        return "inactive_quote"

    source = _normalize_text(row.get("trade_grade_source", "")).lower()
    event_text = _normalize_text(row.get("event_risk_mode_text", ""))
    alert_text = _normalize_text(row.get("alert_state_text", ""))
    tone = _normalize_text(row.get("tone", "")).lower()
    if source == "event" or "事件" in event_text or "事件" in alert_text:
        return "event_gate"
    if source == "spread" or "点差" in alert_text or tone in {"warning", "accent"}:
        return "spread_gate"

    rr_state = _normalize_text(features.get("risk_reward_state_text", ""))
    if "未知" in rr_state:
        return "rr_unknown"
    if "偏差" in rr_state:
        return "rr_poor"

    mtf_text = _normalize_text(features.get("multi_timeframe_alignment_text", ""))
    if "分歧" in mtf_text:
        return "mtf_mixed"
    if "待确认" in mtf_text:
        return "mtf_pending"

    breakout_text = _normalize_text(features.get("breakout_state_text", ""))
    retest_text = _normalize_text(features.get("retest_state_text", ""))
    if "暂无" in breakout_text and "暂无" in retest_text:
        return "no_breakout_retest"

    bias_text = _normalize_text(features.get("intraday_bias_text", ""))
    if "震荡" in bias_text:
        return "sideways"

    key_text = _normalize_text(features.get("key_level_state_text", ""))
    if "中段" in key_text:
        return "mid_range"

    if _normalize_text(row.get("trade_grade", "")) != TradeGrade.LIGHT_POSITION.value:
        return "grade_gate"
    return "unknown"


def _fetch_snapshot_rows(conn, symbol: str, start_time: str = "", end_time: str = "", exclude_external: bool = True) -> list[dict]:
    clauses = ["symbol = ?"]
    params: list[object] = [str(symbol).upper()]
    if start_time:
        clauses.append("snapshot_time >= ?")
        params.append(start_time)
    if end_time:
        clauses.append("snapshot_time <= ?")
        params.append(end_time)
    if exclude_external:
        clauses.append("COALESCE(regime_tag, '') NOT LIKE 'external_%'")
    where_sql = " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT
            id, snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
            trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
            event_active_name, event_importance_text, event_note, signal_side,
            regime_tag, regime_text, feature_json
        FROM market_snapshots
        WHERE {where_sql}
        ORDER BY snapshot_time ASC, id ASC
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def audit_missed_opportunities(
    db_path: Path | str | None = None,
    symbol: str = "XAUUSD",
    horizon_min: int = 30,
    min_move_pct: float | None = None,
    start_time: str = "",
    end_time: str = "",
    limit: int = 50,
    dedupe_minutes: int = 10,
    exclude_external: bool = True,
) -> dict:
    target_db = Path(db_path) if db_path else KNOWLEDGE_DB_FILE
    symbol_key = str(symbol or "XAUUSD").strip().upper()
    threshold = float(min_move_pct if min_move_pct is not None else _direction_threshold_pct(symbol_key))
    horizon = max(1, int(horizon_min))
    dedupe_delta = timedelta(minutes=max(0, int(dedupe_minutes)))

    with open_knowledge_connection(target_db, ensure_schema=True) as conn:
        rows = _fetch_snapshot_rows(conn, symbol_key, start_time=start_time, end_time=end_time, exclude_external=exclude_external)

    normalized_rows: list[dict] = []
    for row in rows:
        snapshot_dt = parse_time(str(row.get("snapshot_time", "") or ""))
        price = float(row.get("latest_price", 0.0) or 0.0)
        if snapshot_dt is None or price <= 0:
            continue
        payload = dict(row)
        payload["_snapshot_dt"] = snapshot_dt
        payload["_price"] = price
        normalized_rows.append(payload)

    times = [row["_snapshot_dt"] for row in normalized_rows]
    opportunities: list[dict] = []
    captured_count = 0
    missed_count = 0
    reason_counter: Counter[str] = Counter()
    side_counter: Counter[str] = Counter()
    last_cluster_at: datetime | None = None

    for index, row in enumerate(normalized_rows):
        snapshot_dt = row["_snapshot_dt"]
        cutoff = snapshot_dt + timedelta(minutes=horizon)
        end_index = bisect.bisect_right(times, cutoff)
        future_rows = normalized_rows[index + 1 : end_index]
        if not future_rows:
            continue

        base_price = float(row["_price"])
        max_price = max(float(item["_price"]) for item in future_rows)
        min_price = min(float(item["_price"]) for item in future_rows)
        upside_pct = (max_price - base_price) / base_price * 100.0
        downside_pct = (base_price - min_price) / base_price * 100.0
        if max(upside_pct, downside_pct) < threshold:
            continue

        best_side = SignalSide.LONG.value if upside_pct >= downside_pct else SignalSide.SHORT.value
        potential_move_pct = max(upside_pct, downside_pct)
        features = _load_feature_payload(str(row.get("feature_json", "{}") or "{}"))
        reason_key = _classify_block_reason(row, features, best_side)
        if reason_key == "captured":
            captured_count += 1
            continue

        if last_cluster_at is not None and snapshot_dt - last_cluster_at < dedupe_delta:
            continue
        last_cluster_at = snapshot_dt

        missed_count += 1
        reason_counter[reason_key] += 1
        side_counter[best_side] += 1
        opportunities.append(
            {
                "snapshot_id": int(row.get("id", 0) or 0),
                "snapshot_time": snapshot_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "symbol": symbol_key,
                "base_price": base_price,
                "future_max_price": max_price,
                "future_min_price": min_price,
                "horizon_min": horizon,
                "best_side": best_side,
                "potential_move_pct": round(potential_move_pct, 4),
                "upside_pct": round(upside_pct, 4),
                "downside_pct": round(downside_pct, 4),
                "system_grade": _normalize_text(row.get("trade_grade", "")),
                "system_side": _normalize_text(row.get("signal_side", "")).lower() or SignalSide.NEUTRAL.value,
                "source": _normalize_text(row.get("trade_grade_source", "")),
                "reason_key": reason_key,
                "reason_label": _REASON_LABELS.get(reason_key, reason_key),
                "alert_state_text": _normalize_text(row.get("alert_state_text", "")),
                "event_risk_mode_text": _normalize_text(row.get("event_risk_mode_text", "")),
                "risk_reward_state_text": _normalize_text(features.get("risk_reward_state_text", "")),
                "multi_timeframe_alignment_text": _normalize_text(features.get("multi_timeframe_alignment_text", "")),
                "intraday_bias_text": _normalize_text(features.get("intraday_bias_text", "")),
                "breakout_state_text": _normalize_text(features.get("breakout_state_text", "")),
                "retest_state_text": _normalize_text(features.get("retest_state_text", "")),
            }
        )

    opportunities.sort(key=lambda item: float(item["potential_move_pct"]), reverse=True)
    limited_rows = opportunities[: max(1, int(limit))]
    reason_summary = [
        {"reason_key": key, "reason_label": _REASON_LABELS.get(key, key), "count": count}
        for key, count in reason_counter.most_common()
    ]
    return {
        "ok": True,
        "db_path": str(target_db),
        "symbol": symbol_key,
        "horizon_min": horizon,
        "min_move_pct": threshold,
        "start_time": start_time,
        "end_time": end_time,
        "exclude_external": bool(exclude_external),
        "analyzed_snapshots": len(normalized_rows),
        "missed_count": missed_count,
        "captured_count": captured_count,
        "missed_rate": (missed_count / max(missed_count + captured_count, 1)),
        "reason_summary": reason_summary,
        "side_summary": [{"side": key, "count": count} for key, count in side_counter.most_common()],
        "optimization_recommendations": build_optimization_recommendations(reason_summary),
        "top_missed": limited_rows,
    }


def write_missed_opportunity_report(report: dict, output_path: Path | str) -> Path:
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="复盘系统错过的大波动行情。")
    parser.add_argument("--db-path", default=str(KNOWLEDGE_DB_FILE), help="知识库 DB 路径")
    parser.add_argument("--symbol", default="XAUUSD", help="品种代码，默认 XAUUSD")
    parser.add_argument("--horizon-min", type=int, default=30, help="未来评估窗口，默认 30 分钟")
    parser.add_argument("--min-move-pct", type=float, default=None, help="最小错过波动百分比，默认按品种自动设置")
    parser.add_argument("--start-time", default="", help="开始时间，例如 2026-04-20 00:00:00")
    parser.add_argument("--end-time", default="", help="结束时间，例如 2026-04-22 23:59:59")
    parser.add_argument("--limit", type=int, default=30, help="输出最大错过样本数")
    parser.add_argument("--dedupe-minutes", type=int, default=10, help="相邻错过样本去重分钟数")
    parser.add_argument("--include-external", action="store_true", help="包含外部回放样本")
    parser.add_argument("--output", default="", help="可选 JSON 报告输出路径")
    args = parser.parse_args(argv)

    report = audit_missed_opportunities(
        db_path=args.db_path,
        symbol=args.symbol,
        horizon_min=args.horizon_min,
        min_move_pct=args.min_move_pct,
        start_time=args.start_time,
        end_time=args.end_time,
        limit=args.limit,
        dedupe_minutes=args.dedupe_minutes,
        exclude_external=not bool(args.include_external),
    )
    if args.output:
        write_missed_opportunity_report(report, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
