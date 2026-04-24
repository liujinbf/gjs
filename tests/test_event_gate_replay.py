import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from event_gate_replay import replay_event_gate_rows
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot


def _snapshot(
    time_text: str,
    price: float,
    *,
    multi_alignment: str = "aligned",
    risk_reward_ready: bool = True,
    risk_reward_ratio: float = 1.9,
) -> dict:
    return {
        "last_refresh_text": time_text,
        "event_risk_mode_text": "事件落地观察",
        "event_active_name": "美国 CPI",
        "regime_tag": "event_driven",
        "regime_text": "事件驱动",
        "summary_text": "测试事件回放",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 17,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "当前不宜出手",
                "trade_grade_source": "event",
                "trade_grade_detail": "事件后原始拦截",
                "trade_next_review": "稍后复核",
                "alert_state_text": "高影响事件后观察",
                "event_importance_text": "高影响",
                "event_note": "高影响事件刚落地",
                "intraday_bias": "bullish",
                "intraday_bias_text": "偏多",
                "intraday_volatility": "normal",
                "intraday_context_text": "近1小时偏多，事件后延续正在抬高低点",
                "multi_timeframe_bias": "bullish",
                "multi_timeframe_bias_text": "偏多",
                "multi_timeframe_alignment": multi_alignment,
                "multi_timeframe_alignment_text": "多周期同向" if multi_alignment == "aligned" else "多周期分歧",
                "multi_timeframe_context_text": "M5 / M15 / H1 多周期同向偏多" if multi_alignment == "aligned" else "M5 偏多 / M15 偏空",
                "breakout_direction": "bullish",
                "breakout_state": "confirmed_above",
                "breakout_state_text": "上破已确认",
                "breakout_context_text": "M5 连续收在关键位上方",
                "retest_state": "confirmed_support",
                "retest_state_text": "回踩已确认",
                "retest_context_text": "回踩突破位后重新企稳",
                "key_level_state": "breakout_above",
                "key_level_state_text": "上破关键位",
                "risk_reward_ready": risk_reward_ready,
                "risk_reward_state": "acceptable" if risk_reward_ready else "unknown",
                "risk_reward_state_text": "盈亏比可接受" if risk_reward_ready else "盈亏比未知",
                "risk_reward_ratio": risk_reward_ratio,
                "risk_reward_direction": "bullish",
                "atr14": 5.0,
                "atr14_h4": 15.0,
                "status_text": "实时报价",
                "quote_text": "Bid / Ask",
                "execution_note": "测试",
            }
        ],
    }


def _follow_snapshot(time_text: str, price: float) -> dict:
    return {
        "last_refresh_text": time_text,
        "event_risk_mode_text": "正常观察",
        "summary_text": "后续价格",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 17,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "只适合观察",
                "trade_grade_source": "structure",
                "alert_state_text": "报价正常观察",
                "status_text": "实时报价",
            }
        ],
    }


def test_replay_event_gate_rows_detects_released_candidate(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(_snapshot("2026-04-20 10:00:00", 100.0), db_path=db_path)
    record_snapshot(_follow_snapshot("2026-04-20 10:10:00", 100.2), db_path=db_path)
    record_snapshot(_follow_snapshot("2026-04-20 10:30:00", 100.5), db_path=db_path)
    backfill_snapshot_outcomes(db_path=db_path, now=None, horizons_min=(30,))

    report = replay_event_gate_rows(db_path=db_path, symbol="XAUUSD", horizon_min=30, dedupe_minutes=0)

    assert report["total_event_rows"] == 1
    assert report["released_rows"] == 1
    assert report["released_clusters"] == 1
    assert report["released_outcomes"][0]["outcome_label"] == "success"
    assert report["top_released"][0]["replay_outcome_label"] == "success"


def test_replay_event_gate_rows_reports_remaining_block_reason(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_snapshot(
        _snapshot("2026-04-20 11:00:00", 100.0, multi_alignment="mixed", risk_reward_ready=False, risk_reward_ratio=0.0),
        db_path=db_path,
    )
    record_snapshot(_follow_snapshot("2026-04-20 11:10:00", 100.3), db_path=db_path)
    record_snapshot(_follow_snapshot("2026-04-20 11:30:00", 100.6), db_path=db_path)
    backfill_snapshot_outcomes(db_path=db_path, now=None, horizons_min=(30,))

    report = replay_event_gate_rows(db_path=db_path, symbol="XAUUSD", horizon_min=30, dedupe_minutes=0)

    assert report["released_rows"] == 0
    assert report["blocked_summary"][0]["reason_key"] == "mtf_not_aligned"
