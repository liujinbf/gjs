import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import import_markdown_source
from knowledge_feedback import record_user_feedback, refresh_rule_feedback_scores, summarize_feedback_stats
from knowledge_governance import refresh_rule_governance, summarize_rule_governance
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot
from knowledge_scoring import match_rules_to_snapshots, refresh_rule_scores


def _build_snapshot(snapshot_time: str, price: float, execution_note: str) -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 18,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": execution_note,
                "trade_next_review": "30 分钟后复核。",
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
                "execution_note": execution_note,
                "intraday_context_text": execution_note,
                "multi_timeframe_context_text": execution_note,
            }
        ],
    }


def _prepare_feedback_runtime(db_path: Path) -> None:
    file_path = db_path.parent / "rules.md"
    file_path.write_text(
        """
# 入场逻辑
- 回调至关键支撑位企稳后介入
- 连续冲高时直接追多
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)
    for idx, price in enumerate([100.00, 100.18, 100.35, 100.48, 100.62, 100.78, 100.90]):
        note = "回调至关键支撑位后企稳，等待回踩确认" if idx < 4 else "连续冲高时直接追多"
        record_snapshot(
            _build_snapshot(f"2026-04-13 {10 + idx // 6:02d}:{(idx % 6) * 10:02d}:00", price, note),
            db_path=db_path,
        )
    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 12, 30, 0), horizons_min=(30,))
    match_rules_to_snapshots(db_path=db_path)
    refresh_rule_scores(db_path=db_path, horizon_min=30)


def test_record_user_feedback_resolves_snapshot_by_symbol_and_time(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_feedback_runtime(db_path)

    result = record_user_feedback(
        symbol="XAUUSD",
        snapshot_time="2026-04-13 10:00:00",
        feedback_label="有帮助",
        feedback_text="这次提醒比较及时。",
        db_path=db_path,
    )

    assert result["inserted_count"] == 1
    assert result["snapshot_id"] is not None
    assert result["feedback_label"] == "helpful"


def test_refresh_rule_feedback_scores_and_summary(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_feedback_runtime(db_path)

    record_user_feedback("XAUUSD", "helpful", snapshot_time="2026-04-13 10:00:00", feedback_text="提醒有效", db_path=db_path)
    record_user_feedback("XAUUSD", "太晚了", snapshot_time="2026-04-13 10:40:00", feedback_text="已经冲出去才提醒", db_path=db_path)
    record_user_feedback("XAUUSD", "噪音", snapshot_time="2026-04-13 10:50:00", feedback_text="不该追", db_path=db_path)

    refresh_result = refresh_rule_feedback_scores(db_path=db_path)
    summary = summarize_feedback_stats(db_path=db_path, days=30, now=datetime(2026, 4, 13, 13, 0, 0))

    assert refresh_result["updated_count"] >= 1
    assert summary["total_count"] == 3
    assert summary["helpful_count"] == 1
    assert summary["too_late_count"] == 1
    assert summary["noise_count"] == 1
    assert isinstance(summary["top_negative_rules"], list)


def test_negative_feedback_can_downgrade_validated_rule(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_feedback_runtime(db_path)

    record_user_feedback("XAUUSD", "too_late", snapshot_time="2026-04-13 10:00:00", feedback_text="还是慢了", db_path=db_path)
    record_user_feedback("XAUUSD", "noise", snapshot_time="2026-04-13 10:10:00", feedback_text="噪音偏高", db_path=db_path)
    record_user_feedback("XAUUSD", "risky", snapshot_time="2026-04-13 10:20:00", feedback_text="这类追法太激进", db_path=db_path)

    refresh_rule_feedback_scores(db_path=db_path)
    refresh_rule_governance(db_path=db_path, horizon_min=30)
    summary = summarize_rule_governance(db_path=db_path, horizon_min=30)

    assert summary["watch_count"] >= 1 or summary["frozen_count"] >= 1
