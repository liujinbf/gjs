import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exploratory_replay import replay_exploratory_grade_gate
from knowledge_base import open_knowledge_connection
from knowledge_runtime import record_snapshot


def _snapshot(time_text: str, price: float = 4801.85) -> dict:
    return {
        "last_refresh_text": time_text,
        "summary_text": "探索回放测试快照",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "bid": price - 0.11,
                "ask": price + 0.11,
                "spread_points": 18,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "只适合观察",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构观察，但多周期同向且 RR 完整。",
                "alert_state_text": "报价正常观察",
                "event_risk_mode_text": "正常观察",
                "signal_side": "neutral",
                "risk_reward_ready": True,
                "risk_reward_state": "acceptable",
                "risk_reward_ratio": 2.0,
                "risk_reward_direction": "bullish",
                "risk_reward_stop_price": price - 25.0,
                "risk_reward_target_price": price + 50.0,
                "risk_reward_target_price_2": price + 75.0,
                "risk_reward_entry_zone_low": price - 10.0,
                "risk_reward_entry_zone_high": price + 4.0,
                "risk_reward_atr": 21.0,
                "atr14": 21.0,
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_bias": "bullish",
                "intraday_bias": "sideways",
                "breakout_direction": "neutral",
            }
        ],
    }


def _insert_grade_gate_audit(db_path: Path, snapshot_time: str, snapshot_id: int, occurred_at: str) -> None:
    with open_knowledge_connection(db_path, ensure_schema=True) as conn:
        conn.execute(
            """
            INSERT INTO execution_audits (
                occurred_at, snapshot_time, snapshot_id, signal_signature, symbol, action,
                source_kind, trade_mode, decision_status, reason_key, reason_text, user_id,
                entry_price, stop_loss, take_profit, meta_json, created_at
            ) VALUES (?, ?, ?, '', 'XAUUSD', 'neutral', 'rule_engine', 'simulation',
                      'blocked', 'grade_gate', '未到试仓级别', 'system', 0, 0, 0, '{}', ?)
            """,
            (occurred_at, snapshot_time, snapshot_id, occurred_at),
        )


def test_replay_exploratory_grade_gate_estimates_released_candidates(tmp_path):
    db_path = tmp_path / "knowledge.db"
    times = [
        "2026-04-22 10:00:00",
        "2026-04-22 10:01:00",
        "2026-04-22 10:02:00",
        "2026-04-22 10:03:00",
    ]
    for idx, time_text in enumerate(times):
        record_snapshot(_snapshot(time_text, price=4800.0 + idx), db_path=db_path)
        with open_knowledge_connection(db_path, ensure_schema=True) as conn:
            snapshot_id = int(
                conn.execute(
                    "SELECT id FROM market_snapshots WHERE snapshot_time=? AND symbol='XAUUSD'",
                    (time_text,),
                ).fetchone()["id"]
            )
        _insert_grade_gate_audit(db_path, time_text, snapshot_id, time_text)

    report = replay_exploratory_grade_gate(
        db_path=db_path,
        hours=48,
        now=datetime(2026, 4, 22, 18, 0, 0),
        daily_limit=3,
        cooldown_min=10,
    )

    assert report["scanned_count"] == 4
    assert report["released_count"] == 4
    assert report["release_rate"] == 1.0
    assert report["policy_accepted_count"] == 1
    assert report["policy_blocked_count"] == 3
    assert report["cooldown_blocked_count"] == 3
    assert report["daily_limit_blocked_count"] == 0
    assert report["still_blocked_reason_counts"] == {}
    assert report["still_blocked_reason_key_counts"] == {}
    assert report["still_blocked_reason_label_counts"] == {}
    assert report["grade_gate_secondary_key_counts"] == {}
    assert report["grade_gate_secondary_label_counts"] == {}
    assert report["rr_not_ready_tertiary_key_counts"] == {}
    assert report["rr_not_ready_tertiary_label_counts"] == {}
    assert report["no_direction_component_key_counts"] == {}
    assert report["no_direction_component_label_counts"] == {}
    assert report["top_still_blocked_labels"] == []
    assert report["top_grade_gate_secondary_labels"] == []
    assert report["top_rr_not_ready_tertiary_labels"] == []
    assert report["top_no_direction_components"] == []
    assert report["no_direction_examples"] == []
    assert report["top_still_blocked_reasons"] == []
    assert report["by_symbol"] == [{"symbol": "XAUUSD", "count": 4}]
    assert report["by_day"][0]["over_limit"] is True
    assert report["by_day"][0]["accepted_count"] == 1
    assert "可释放 4 条" in report["summary_text"]
    assert "预计执行 1 条" in report["summary_text"]
    assert "超过每日探索上限 3 次" in report["summary_text"]
    assert report["top_released"][0]["execution_profile"] == "exploratory"
    assert report["top_released"][0]["policy_status"] == "accepted"
    assert report["top_released"][1]["policy_block_reason"] == "cooldown"


def test_replay_exploratory_grade_gate_collects_remaining_block_reasons(tmp_path):
    db_path = tmp_path / "knowledge.db"
    time_text = "2026-04-22 11:00:00"
    snapshot = _snapshot(time_text, price=4805.0)
    item = dict(snapshot["items"][0])
    item["has_live_quote"] = False
    snapshot["items"] = [item]
    record_snapshot(snapshot, db_path=db_path)
    with open_knowledge_connection(db_path, ensure_schema=True) as conn:
        snapshot_id = int(
            conn.execute(
                "SELECT id FROM market_snapshots WHERE snapshot_time=? AND symbol='XAUUSD'",
                (time_text,),
            ).fetchone()["id"]
        )
    _insert_grade_gate_audit(db_path, time_text, snapshot_id, time_text)

    report = replay_exploratory_grade_gate(
        db_path=db_path,
        hours=48,
        now=datetime(2026, 4, 22, 18, 0, 0),
        daily_limit=3,
        cooldown_min=10,
    )

    assert report["scanned_count"] == 1
    assert report["released_count"] == 0
    assert report["still_blocked_count"] == 1
    assert report["still_blocked_reason_key_counts"]["inactive_quote"] == 1
    assert report["still_blocked_reason_label_counts"]["非实时报价"] == 1
    assert report["grade_gate_secondary_key_counts"] == {}
    assert report["grade_gate_secondary_label_counts"] == {}
    assert report["rr_not_ready_tertiary_key_counts"] == {}
    assert report["rr_not_ready_tertiary_label_counts"] == {}
    assert report["no_direction_component_key_counts"] == {}
    assert report["no_direction_component_label_counts"] == {}
    assert report["top_still_blocked_labels"][0]["reason_label"] == "非实时报价"
    assert report["top_still_blocked_reasons"][0]["count"] == 1
    assert "当前不是实时报价" in report["top_still_blocked_reasons"][0]["reason"]


def test_replay_exploratory_grade_gate_splits_grade_gate_secondary_reason(tmp_path):
    db_path = tmp_path / "knowledge.db"
    time_text = "2026-04-22 12:00:00"
    snapshot = _snapshot(time_text, price=4810.0)
    item = dict(snapshot["items"][0])
    item["risk_reward_ready"] = False
    item["risk_reward_direction"] = ""
    item["multi_timeframe_bias"] = ""
    item["breakout_direction"] = ""
    item["intraday_bias"] = ""
    snapshot["items"] = [item]
    record_snapshot(snapshot, db_path=db_path)
    with open_knowledge_connection(db_path, ensure_schema=True) as conn:
        snapshot_id = int(
            conn.execute(
                "SELECT id FROM market_snapshots WHERE snapshot_time=? AND symbol='XAUUSD'",
                (time_text,),
            ).fetchone()["id"]
        )
    _insert_grade_gate_audit(db_path, time_text, snapshot_id, time_text)

    report = replay_exploratory_grade_gate(
        db_path=db_path,
        hours=48,
        now=datetime(2026, 4, 22, 18, 0, 0),
        daily_limit=3,
        cooldown_min=10,
    )

    assert report["still_blocked_reason_key_counts"]["grade_gate"] == 1
    assert report["grade_gate_secondary_key_counts"]["rr_not_ready"] == 1
    assert report["grade_gate_secondary_label_counts"]["盈亏比未准备好"] == 1
    assert report["top_grade_gate_secondary_labels"][0]["reason_label"] == "盈亏比未准备好"
    assert report["rr_not_ready_tertiary_key_counts"]["no_direction"] == 1
    assert report["rr_not_ready_tertiary_label_counts"]["方向基础不足"] == 1
    assert report["top_rr_not_ready_tertiary_labels"][0]["reason_label"] == "方向基础不足"
    assert report["no_direction_component_key_counts"]["signal_side_missing"] == 1
    assert report["no_direction_component_key_counts"]["intraday_sideways"] == 1
    assert report["no_direction_component_key_counts"]["multi_not_aligned"] == 1
    assert report["no_direction_component_key_counts"]["breakout_direction_neutral"] == 1
    assert report["no_direction_component_key_counts"]["breakout_state_none"] == 1
    assert report["no_direction_component_key_counts"]["retest_state_none"] == 1
    assert report["top_no_direction_components"][0]["count"] == 1
    assert report["no_direction_examples"][0]["symbol"] == "XAUUSD"
