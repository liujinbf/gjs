import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import import_markdown_source
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot
from knowledge_scoring import match_rules_to_snapshots, refresh_rule_scores, summarize_rule_scores


def _build_snapshot(snapshot_time: str, price: float, execution_note: str, trade_grade: str = "可轻仓试仓") -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "event_active_name": "",
        "summary_text": "测试快照",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 18,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": trade_grade,
                "trade_grade_source": "structure" if trade_grade == "可轻仓试仓" else "event",
                "trade_grade_detail": execution_note,
                "trade_next_review": "30 分钟后复核。",
                "alert_state_text": "结构候选" if trade_grade == "可轻仓试仓" else "高影响事件前",
                "event_importance_text": "高影响" if trade_grade != "可轻仓试仓" else "",
                "event_note": "高影响窗口：美国 CPI 将于稍后落地，当前品种先别抢第一脚。" if trade_grade != "可轻仓试仓" else "",
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
                "intraday_context_text": "回调至关键支撑位后企稳，近1小时偏多",
                "multi_timeframe_context_text": "多周期同向偏多，回踩确认后再考虑介入",
            }
        ],
    }


def test_rule_scoring_marks_runtime_rule_as_validated(tmp_path):
    db_path = tmp_path / "knowledge.db"
    file_path = tmp_path / "rules.md"
    file_path.write_text(
        """
# 入场逻辑
- 回调至关键支撑位企稳后介入
- 不追第一次突破，优先等回踩确认

# 心态纪律
- 连续止损3次后先暂停
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)

    record_snapshot(_build_snapshot("2026-04-13 10:00:00", 100.00, "回调至关键支撑位后企稳，等待回踩确认后介入"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 10:10:00", 100.18, "结构延续"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 10:20:00", 100.35, "结构延续"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 10:30:00", 100.42, "结构延续"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 10:40:00", 100.55, "结构延续"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 10:50:00", 100.70, "结构延续"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 11:00:00", 100.85, "结构延续"), db_path=db_path)
    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 12, 0, 0), horizons_min=(30,))

    match_result = match_rules_to_snapshots(db_path=db_path)
    score_result = refresh_rule_scores(db_path=db_path, horizon_min=30)
    summary = summarize_rule_scores(db_path=db_path, horizon_min=30)

    assert match_result["matched_count"] >= 2
    assert score_result["updated_count"] >= 1
    assert summary["validated_count"] >= 1
    assert summary["manual_review_count"] >= 1
    assert any("回调至关键支撑位企稳后介入" in item["rule_text"] for item in summary["top_rules"])


def test_rule_scoring_keeps_negative_rule_as_rejected_or_insufficient(tmp_path):
    db_path = tmp_path / "knowledge.db"
    file_path = tmp_path / "rules.md"
    file_path.write_text(
        """
# 入场逻辑
- 回调至关键支撑位企稳后介入
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)

    record_snapshot(_build_snapshot("2026-04-13 11:00:00", 100.00, "回调至关键支撑位后企稳，考虑介入"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 11:10:00", 99.70, "结构失败"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 11:20:00", 99.55, "结构失败"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 11:30:00", 99.50, "结构失败"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 12:00:00", 100.00, "再次尝试"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 12:10:00", 99.68, "结构失败"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 12:20:00", 99.52, "结构失败"), db_path=db_path)
    record_snapshot(_build_snapshot("2026-04-13 12:30:00", 99.48, "结构失败"), db_path=db_path)
    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 13, 0, 0), horizons_min=(30,))

    match_rules_to_snapshots(db_path=db_path)
    refresh_rule_scores(db_path=db_path, horizon_min=30)
    summary = summarize_rule_scores(db_path=db_path, horizon_min=30)

    assert summary["rejected_count"] >= 1 or summary["insufficient_count"] >= 1
