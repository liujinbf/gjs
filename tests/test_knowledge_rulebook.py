import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import import_markdown_source
from knowledge_rulebook import build_rulebook
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot
from knowledge_scoring import match_rules_to_snapshots, refresh_rule_scores


def _build_snapshot(snapshot_time: str, price: float, execution_note: str) -> dict:
    regime_tag = "trend_expansion" if "企稳" in execution_note else "low_volatility_range"
    regime_text = "趋势扩张" if regime_tag == "trend_expansion" else "低波震荡"
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "regime_tag": regime_tag,
        "regime_text": regime_text,
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
                "regime_tag": regime_tag,
                "regime_text": regime_text,
                "regime_reason": "测试环境标签",
                "intraday_context_text": "回调至关键支撑位后企稳，近1小时偏多",
                "multi_timeframe_context_text": "多周期同向偏多，等待回踩确认",
            }
        ],
    }


def test_build_rulebook_outputs_active_and_rejected_sections(tmp_path):
    db_path = tmp_path / "knowledge.db"
    file_path = tmp_path / "rules.md"
    file_path.write_text(
        """
# 入场逻辑
- 回调至关键支撑位企稳后介入
- 连续冲高时直接追多
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)

    for offset, price in enumerate([100.00, 100.12, 100.28, 100.40, 100.52, 100.66, 100.78]):
        record_snapshot(
            _build_snapshot(f"2026-04-13 {10 + offset // 6:02d}:{(offset % 6) * 10:02d}:00", price, "回调至关键支撑位后企稳，等待回踩确认"),
            db_path=db_path,
        )
    for offset, price in enumerate([100.00, 99.85, 99.70, 99.62, 100.00, 99.88, 99.74, 99.60]):
        record_snapshot(
            _build_snapshot(f"2026-04-13 {12 + offset // 6:02d}:{(offset % 6) * 10:02d}:00", price, "连续冲高时直接追多"),
            db_path=db_path,
        )

    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 14, 30, 0), horizons_min=(30,))
    match_rules_to_snapshots(db_path=db_path)
    refresh_rule_scores(db_path=db_path, horizon_min=30)

    rulebook = build_rulebook(db_path=db_path, horizon_min=30)
    assert "当前优先遵守" in rulebook["summary_text"] or "当前暂无已验证规则" in rulebook["summary_text"]
    assert "暂无" not in rulebook["active_rules_text"] or rulebook["candidate_rules"]
    assert "淘汰" in rulebook["summary_text"] or rulebook["rejected_rules_text"]


def test_build_rulebook_can_surface_regime_specific_rules(tmp_path):
    db_path = tmp_path / "knowledge.db"
    file_path = tmp_path / "rules.md"
    file_path.write_text(
        """
# 入场逻辑
- 回调至关键支撑位企稳后介入
- 低波震荡时不要追第一次突破
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)

    for offset, price in enumerate([100.00, 100.12, 100.30, 100.45, 100.58]):
        record_snapshot(
            _build_snapshot(f"2026-04-13 10:{offset * 10:02d}:00", price, "回调至关键支撑位后企稳，等待回踩确认"),
            db_path=db_path,
        )
    for offset, price in enumerate([100.00, 99.98, 100.02, 99.97, 100.01]):
        record_snapshot(
            _build_snapshot(f"2026-04-13 12:{offset * 10:02d}:00", price, "低波震荡时不要追第一次突破"),
            db_path=db_path,
        )

    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 13, 30, 0), horizons_min=(30,))
    match_rules_to_snapshots(db_path=db_path)
    refresh_rule_scores(db_path=db_path, horizon_min=30)

    rulebook = build_rulebook(db_path=db_path, horizon_min=30, current_regime_tag="low_volatility_range")
    assert rulebook["current_regime_tag"] == "low_volatility_range"
    assert "当前环境" in rulebook["regime_summary_text"]
    assert rulebook["regime_rules_text"]
    assert "当前环境暂无明确观察规则。" in rulebook["regime_watch_rules_text"] or rulebook["regime_watch_rules"]
    assert "当前环境暂无明确回避规则。" in rulebook["regime_avoid_rules_text"] or rulebook["regime_avoid_rules"]
