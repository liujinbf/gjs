import sys
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot, summarize_outcome_stats
from quote_models import QuoteRow


def _build_snapshot(snapshot_time: str, price: float, trade_grade: str = "可轻仓试仓") -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "event_active_name": "",
        "regime_tag": "trend_expansion",
        "regime_text": "趋势扩张",
        "summary_text": "测试快照",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 18,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": trade_grade,
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构干净，等待延续。",
                "trade_next_review": "15 分钟后复核。",
                "alert_state_text": "结构候选",
                "event_importance_text": "",
                "event_note": "",
                "intraday_bias": "bullish",
                "intraday_bias_text": "偏多",
                "multi_timeframe_bias": "bullish",
                "multi_timeframe_bias_text": "偏多",
                "breakout_direction": "bullish",
                "breakout_state": "confirmed_above",
                "breakout_state_text": "上破已确认",
                "retest_state": "confirmed_support",
                "retest_state_text": "回踩已确认",
                "key_level_state": "breakout_above",
                "key_level_state_text": "上破高位",
                "risk_reward_state": "good",
                "risk_reward_state_text": "盈亏比优秀",
                "status_text": "实时报价",
                "quote_text": "Bid 100.00 / Ask 100.18",
                "execution_note": "测试执行建议",
                "regime_tag": "trend_expansion",
                "regime_text": "趋势扩张",
                "regime_reason": "多周期同向偏多。",
                "intraday_context_text": "近1小时偏多",
                "multi_timeframe_context_text": "多周期同向偏多",
            }
        ],
    }


def test_record_snapshot_and_backfill_outcomes(tmp_path):
    db_path = tmp_path / "knowledge.db"

    first = record_snapshot(_build_snapshot("2026-04-13 10:00:00", 100.00), db_path=db_path)
    second = record_snapshot(_build_snapshot("2026-04-13 10:10:00", 100.25), db_path=db_path)
    third = record_snapshot(_build_snapshot("2026-04-13 10:20:00", 100.35), db_path=db_path)

    assert first["inserted_count"] == 1
    assert second["inserted_count"] == 1
    assert third["inserted_count"] == 1

    result = backfill_snapshot_outcomes(
        db_path=db_path,
        now=datetime(2026, 4, 13, 10, 50, 0),
        horizons_min=(15, 30),
    )
    stats_30m = summarize_outcome_stats(db_path=db_path, horizon_min=30)

    assert result["labeled_count"] >= 2
    assert stats_30m["total_count"] >= 2
    assert stats_30m["success_count"] >= 1

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT regime_tag, regime_text FROM market_snapshots LIMIT 1").fetchone()
    conn.close()
    assert row == ("trend_expansion", "趋势扩张")


def test_backfill_marks_non_signal_snapshot_as_observe(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(_build_snapshot("2026-04-13 11:00:00", 100.00, trade_grade="只适合观察"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 11:10:00", 100.40, trade_grade="只适合观察"), db_path=db_path)

    backfill_snapshot_outcomes(
        db_path=db_path,
        now=datetime(2026, 4, 13, 12, 0, 0),
        horizons_min=(15,),
    )
    stats_15m = summarize_outcome_stats(db_path=db_path, horizon_min=15)

    assert stats_15m["observe_count"] >= 1


def test_record_snapshot_accepts_quote_row_items(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = {
        "last_refresh_text": "2026-04-13 12:00:00",
        "event_risk_mode_text": "正常观察",
        "event_active_name": "",
        "regime_tag": "trend_expansion",
        "regime_text": "趋势扩张",
        "summary_text": "测试快照",
        "items": [
            QuoteRow(
                symbol="XAUUSD",
                latest_price=100.0,
                bid=99.9,
                ask=100.1,
                spread_points=20.0,
                point=0.01,
                tick_time=1000,
                status="实时报价",
                quote_status_code="live",
                has_live_quote=True,
                extra={
                    "tone": "success",
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "trade_grade_detail": "结构干净。",
                    "trade_next_review": "15 分钟后复核。",
                    "alert_state_text": "结构候选",
                    "event_importance_text": "",
                    "event_note": "",
                    "intraday_bias": "bullish",
                    "intraday_bias_text": "偏多",
                    "multi_timeframe_bias": "bullish",
                    "multi_timeframe_bias_text": "偏多",
                    "breakout_direction": "bullish",
                    "breakout_state": "confirmed_above",
                    "breakout_state_text": "上破已确认",
                    "retest_state": "confirmed_support",
                    "retest_state_text": "回踩已确认",
                    "key_level_state": "breakout_above",
                    "key_level_state_text": "上破高位",
                    "risk_reward_state": "good",
                    "risk_reward_state_text": "盈亏比优秀",
                    "status_text": "实时报价",
                    "quote_text": "Bid 99.90 / Ask 100.10",
                    "execution_note": "测试执行建议",
                    "regime_tag": "trend_expansion",
                    "regime_text": "趋势扩张",
                    "regime_reason": "多周期同向偏多。",
                    "intraday_context_text": "近1小时偏多",
                    "multi_timeframe_context_text": "多周期同向偏多",
                },
            )
        ],
    }

    result = record_snapshot(snapshot, db_path=db_path)

    assert result["inserted_count"] == 1
    assert result["snapshot_bindings"]["XAUUSD"] > 0


def test_record_snapshot_persists_numeric_risk_features(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = _build_snapshot("2026-04-13 13:00:00", 100.00)
    snapshot["items"][0].update(
        {
            "risk_reward_ready": True,
            "risk_reward_ratio": 2.4,
            "risk_reward_direction": "bullish",
            "risk_reward_stop_price": 98.0,
            "risk_reward_target_price": 104.8,
            "risk_reward_entry_zone_low": 99.5,
            "risk_reward_entry_zone_high": 100.2,
            "model_ready": True,
            "model_win_probability": 0.71,
            "execution_model_ready": True,
            "execution_open_probability": 0.63,
        }
    )

    result = record_snapshot(snapshot, db_path=db_path)

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute(
        "SELECT feature_json FROM market_snapshots WHERE id = ?",
        (result["inserted_snapshot_ids"][0],),
    ).fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)
    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_ratio"] == 2.4
    assert payload["risk_reward_stop_price"] == 98.0
    assert payload["model_win_probability"] == 0.71
    assert payload["execution_open_probability"] == 0.63


def test_record_snapshot_persists_direction_for_observe_grade(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = _build_snapshot("2026-04-13 14:00:00", 100.00, trade_grade="只适合观察")
    snapshot["items"][0].update(
        {
            "signal_side": "",
            "signal_side_text": "",
            "risk_reward_ready": False,
            "risk_reward_ratio": 0.0,
        }
    )

    result = record_snapshot(snapshot, db_path=db_path)

    import sqlite3

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT signal_side, feature_json FROM market_snapshots WHERE id = ?",
        (result["inserted_snapshot_ids"][0],),
    ).fetchone()
    conn.close()

    payload = json.loads(row[1])
    assert row[0] == "long"
    assert payload["signal_side"] == "long"
    assert payload["signal_side_text"] == "【↑ 多头参考】"
    assert payload["signal_side_basis"] == "结构投票"
    assert payload["signal_side_long_votes"] >= 3
