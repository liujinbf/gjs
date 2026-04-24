import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot
from learning_closure import (
    backfill_alert_effect_outcomes,
    backfill_missed_opportunity_samples,
    summarize_alert_effect_outcomes,
    summarize_missed_opportunity_samples,
)


def _snapshot(snapshot_time: str, price: float) -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 12,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "回踩确认后偏多。",
                "trade_next_review": "30 分钟后复核。",
                "alert_state_text": "结构候选",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.1,
                "risk_reward_stop_price": price - 0.6,
                "risk_reward_target_price": price + 1.2,
            }
        ],
    }


def test_backfill_alert_effect_outcomes_links_push_to_future_outcome(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    for time_text, price in [
        ("2026-04-24 10:00:00", 100.00),
        ("2026-04-24 10:10:00", 100.30),
        ("2026-04-24 10:30:00", 100.85),
    ]:
        record_snapshot(_snapshot(time_text, price), db_path=db_path)

    backfill_snapshot_outcomes(
        db_path=db_path,
        now=datetime(2026, 4, 24, 10, 40, 0),
        horizons_min=(30,),
    )
    history_file.write_text(
        json.dumps(
            {
                "occurred_at": "2026-04-24 10:00:00",
                "category": "structure",
                "title": "XAUUSD 机会更新：多单到位",
                "detail": "回踩确认后偏多。",
                "symbol": "XAUUSD",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.1,
                "baseline_latest_price": 100.00,
                "stop_loss_price": 99.60,
                "signature": "alert-xau-long-1",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = backfill_alert_effect_outcomes(history_file=history_file, db_path=db_path, horizon_min=30)
    summary = summarize_alert_effect_outcomes(db_path=db_path, horizon_min=30)

    assert result["checked_count"] == 1
    assert result["inserted_count"] == 1
    assert summary["total_count"] == 1
    assert summary["success_count"] == 1
    assert summary["reached_1r_count"] == 1
    assert summary["avg_favorable_r"] >= 1.0
    assert "提醒后 30 分钟效果样本 1 条" in summary["summary_text"]


def test_backfill_alert_effect_outcomes_ignores_non_trade_alerts(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"
    history_file.write_text(
        json.dumps(
            {
                "occurred_at": "2026-04-24 10:00:00",
                "category": "spread",
                "title": "XAUUSD 风控更新：点差过宽",
                "detail": "先别下单。",
                "symbol": "XAUUSD",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    result = backfill_alert_effect_outcomes(history_file=history_file, db_path=db_path, horizon_min=30)

    assert result["checked_count"] == 0
    assert result["inserted_count"] == 0


def test_backfill_missed_opportunity_samples_persists_actionable_gaps(tmp_path):
    db_path = tmp_path / "knowledge.db"

    def observe_snapshot(snapshot_time: str, price: float) -> dict:
        return {
            "last_refresh_text": snapshot_time,
            "event_risk_mode_text": "正常观察",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": price,
                    "spread_points": 12,
                    "has_live_quote": True,
                    "tone": "neutral",
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "trade_grade_detail": "位置还没给到确认。",
                    "trade_next_review": "继续观察。",
                    "alert_state_text": "结构观察",
                    "signal_side": "neutral",
                    "risk_reward_ready": False,
                    "risk_reward_state_text": "盈亏比未知",
                }
            ],
        }

    for time_text, price in [
        ("2026-04-24 10:00:00", 100.00),
        ("2026-04-24 10:10:00", 100.08),
        ("2026-04-24 10:30:00", 100.42),
    ]:
        record_snapshot(observe_snapshot(time_text, price), db_path=db_path)

    result = backfill_missed_opportunity_samples(db_path=db_path, symbols=("XAUUSD",), horizon_min=30)
    summary = summarize_missed_opportunity_samples(db_path=db_path, horizon_min=30)

    assert result["missed_count"] >= 1
    assert result["inserted_count"] >= 1
    assert summary["total_count"] >= 1
    assert "漏机会样本" in summary["summary_text"]
