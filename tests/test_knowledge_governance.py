import sys
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import import_markdown_source
from knowledge_governance import (
    build_learning_report,
    read_latest_learning_report,
    refresh_rule_governance,
    summarize_rule_governance,
)
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


def _prepare_runtime_scores(
    db_path: Path,
    include_negative_rule: bool = True,
    include_negative_samples: bool = True,
) -> None:
    file_path = db_path.parent / "rules.md"
    negative_rule_text = "\n- 连续冲高时直接追多" if include_negative_rule else ""
    file_path.write_text(
        f"""
# 入场逻辑
- 回调至关键支撑位企稳后介入
{negative_rule_text}

# 心态纪律
- 连续止损3次后先暂停
""",
        encoding="utf-8",
    )
    import_markdown_source(file_path, db_path=db_path)

    for idx, price in enumerate([100.00, 100.18, 100.30, 100.42, 100.56, 100.68, 100.82]):
        record_snapshot(
            _build_snapshot(
                f"2026-04-13 {10 + idx // 6:02d}:{(idx % 6) * 10:02d}:00",
                price,
                "回调至关键支撑位后企稳，等待回踩确认",
            ),
            db_path=db_path,
        )

    if include_negative_samples:
        for idx, price in enumerate([100.00, 99.84, 99.70, 99.60, 100.00, 99.86, 99.74, 99.62]):
            record_snapshot(
                _build_snapshot(
                    f"2026-04-13 {12 + idx // 6:02d}:{(idx % 6) * 10:02d}:00",
                    price,
                    "连续冲高时直接追多",
                ),
                db_path=db_path,
            )

    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 14, 30, 0), horizons_min=(30,))
    match_rules_to_snapshots(db_path=db_path)
    refresh_rule_scores(db_path=db_path, horizon_min=30)


def test_refresh_rule_governance_sets_active_and_manual_review(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_runtime_scores(db_path, include_negative_rule=False, include_negative_samples=False)

    result = refresh_rule_governance(db_path=db_path, horizon_min=30)
    summary = summarize_rule_governance(db_path=db_path, horizon_min=30)

    assert result["updated_count"] >= 1
    assert summary["active_count"] >= 1
    assert summary["manual_review_count"] >= 1


def test_build_learning_report_persists_latest_digest(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_runtime_scores(db_path)
    refresh_rule_governance(db_path=db_path, horizon_min=30)

    report = build_learning_report(db_path=db_path, horizon_min=30, persist=True)
    latest = read_latest_learning_report(db_path=db_path)

    assert "规则治理" in report["summary_text"]
    assert latest["summary_text"] == report["summary_text"]
    assert isinstance(latest.get("active_rules", []), list)
    assert isinstance(latest.get("governance_map", {}), dict)


def test_build_learning_report_includes_status_deltas_against_previous_digest(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_runtime_scores(db_path)
    refresh_rule_governance(db_path=db_path, horizon_min=30)
    build_learning_report(db_path=db_path, horizon_min=30, persist=True)

    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            """
            SELECT rg.rule_id, kr.rule_text
            FROM rule_governance rg
            JOIN knowledge_rules kr ON kr.id = rg.rule_id
            WHERE rg.horizon_min = 30
            """
        ).fetchall()
        positive_rule_id = next(rule_id for rule_id, rule_text in rows if "回调至关键支撑位企稳后介入" in rule_text)
        negative_rule_id = next(rule_id for rule_id, rule_text in rows if "连续冲高时直接追多" in rule_text)
        conn.execute(
            "UPDATE rule_governance SET governance_status = 'frozen' WHERE rule_id = ? AND horizon_min = 30",
            (positive_rule_id,),
        )
        conn.execute(
            "UPDATE rule_governance SET governance_status = 'active' WHERE rule_id = ? AND horizon_min = 30",
            (negative_rule_id,),
        )
        latest_id, payload_json = conn.execute(
            "SELECT id, payload_json FROM learning_reports ORDER BY id DESC LIMIT 1"
        ).fetchone()
        payload = json.loads(payload_json)
        payload["governance_map"][str(negative_rule_id)]["governance_status"] = "frozen"
        conn.execute(
            "UPDATE learning_reports SET payload_json = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), latest_id),
        )

    report = build_learning_report(db_path=db_path, horizon_min=30, persist=False)

    assert "状态变化" in report["summary_text"]
    assert any("回调至关键支撑位企稳后介入" in item for item in report["new_frozen_rules"])
    assert any("连续冲高时直接追多" in item for item in report["recovered_rules"])
    assert isinstance(report.get("governance_map", {}), dict)
