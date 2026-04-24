import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_runtime import record_snapshot
from missed_opportunity_auditor import audit_missed_opportunities, build_optimization_recommendations


def _snapshot(
    time_text: str,
    price: float,
    trade_grade: str = "只适合观察",
    source: str = "structure",
    event_mode: str = "正常观察",
    event_note: str = "",
) -> dict:
    return {
        "last_refresh_text": time_text,
        "event_risk_mode_text": event_mode,
        "event_active_name": "",
        "regime_tag": "trend_expansion",
        "regime_text": "趋势扩张",
        "summary_text": "测试快照",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 17,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": trade_grade,
                "trade_grade_source": source,
                "trade_grade_detail": "测试分级",
                "trade_next_review": "稍后复核",
                "alert_state_text": "报价正常观察" if source != "event" else "高影响事件后观察",
                "event_importance_text": "高影响" if source == "event" else "",
                "event_note": event_note,
                "intraday_bias": "bullish",
                "intraday_bias_text": "偏多",
                "multi_timeframe_bias": "bullish",
                "multi_timeframe_bias_text": "偏多",
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_alignment_text": "多周期同向",
                "breakout_direction": "bullish",
                "breakout_state": "confirmed_above",
                "breakout_state_text": "上破已确认",
                "retest_state": "confirmed_support",
                "retest_state_text": "回踩已确认",
                "key_level_state": "breakout_above",
                "key_level_state_text": "上破关键位",
                "risk_reward_state": "favorable",
                "risk_reward_state_text": "盈亏比优秀",
                "status_text": "实时报价",
                "quote_text": "Bid / Ask",
                "execution_note": "测试",
                "intraday_context_text": "近1小时偏多",
                "multi_timeframe_context_text": "多周期同向偏多",
            }
        ],
    }


def test_audit_missed_opportunities_detects_observe_before_big_move(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(_snapshot("2026-04-20 10:00:00", 100.0, trade_grade="只适合观察"), db_path=db_path)
    record_snapshot(_snapshot("2026-04-20 10:10:00", 100.4, trade_grade="只适合观察"), db_path=db_path)
    record_snapshot(_snapshot("2026-04-20 10:30:00", 100.8, trade_grade="只适合观察"), db_path=db_path)

    report = audit_missed_opportunities(db_path=db_path, horizon_min=30, min_move_pct=0.3, dedupe_minutes=0)

    assert report["missed_count"] >= 1
    assert report["top_missed"][0]["best_side"] == "long"
    assert report["top_missed"][0]["system_grade"] == "只适合观察"


def test_audit_missed_opportunities_counts_captured_signal(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(_snapshot("2026-04-20 11:00:00", 100.0, trade_grade="可轻仓试仓"), db_path=db_path)
    record_snapshot(_snapshot("2026-04-20 11:20:00", 100.5, trade_grade="只适合观察"), db_path=db_path)

    report = audit_missed_opportunities(db_path=db_path, horizon_min=30, min_move_pct=0.3, dedupe_minutes=0)

    assert report["captured_count"] >= 1
    assert report["missed_count"] == 0


def test_audit_missed_opportunities_attributes_event_gate(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(
        _snapshot(
            "2026-04-20 12:00:00",
            100.0,
            trade_grade="当前不宜出手",
            source="event",
            event_mode="事件落地观察",
            event_note="高影响事件刚落地",
        ),
        db_path=db_path,
    )
    record_snapshot(_snapshot("2026-04-20 12:30:00", 100.7, trade_grade="只适合观察"), db_path=db_path)

    report = audit_missed_opportunities(db_path=db_path, horizon_min=30, min_move_pct=0.3, dedupe_minutes=0)

    assert report["missed_count"] >= 1
    assert report["reason_summary"][0]["reason_key"] == "event_gate"
    assert report["optimization_recommendations"][0]["reason_key"] == "event_gate"
    assert "事件后" in report["optimization_recommendations"][0]["title"]


def test_build_optimization_recommendations_maps_top_reasons():
    result = build_optimization_recommendations(
        [
            {"reason_key": "rr_unknown", "count": 6},
            {"reason_key": "mtf_mixed", "count": 3},
        ]
    )

    assert [row["reason_key"] for row in result] == ["rr_unknown", "mtf_mixed"]
    assert "盈亏比" in result[0]["title"]


def test_audit_missed_opportunities_splits_inactive_stale_tick_reason(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = _snapshot("2026-04-20 14:00:00", 100.0, trade_grade="当前不宜出手")
    snapshot["items"][0]["has_live_quote"] = False
    snapshot["items"][0]["quote_live_reason"] = "stale_tick"
    snapshot["items"][0]["quote_live_diagnostic_text"] = "最新 tick 约延迟 420 秒，超过活跃阈值 180 秒。"
    record_snapshot(snapshot, db_path=db_path)
    record_snapshot(_snapshot("2026-04-20 14:30:00", 100.8, trade_grade="只适合观察"), db_path=db_path)

    report = audit_missed_opportunities(db_path=db_path, horizon_min=30, min_move_pct=0.3, dedupe_minutes=0)

    assert report["reason_summary"][0]["reason_key"] == "inactive_stale_tick"
    assert report["optimization_recommendations"][0]["reason_key"] == "inactive_stale_tick"
