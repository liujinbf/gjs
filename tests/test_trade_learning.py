import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trade_learning import record_trade_learning_close, record_trade_learning_open, summarize_trade_learning_by_strategy
from knowledge_base import open_knowledge_connection


def test_trade_learning_journal_records_open_and_close(tmp_path):
    db_path = tmp_path / "knowledge.db"

    with open_knowledge_connection(db_path=db_path, ensure_schema=True):
        pass

    record_trade_learning_open(
        sim_position_id=101,
        user_id="system",
        meta={
            "snapshot_id": 88,
            "snapshot_time": "2026-04-23 03:16:24",
            "symbol": "XAUUSD",
            "action": "long",
            "execution_profile": "exploratory",
            "trade_grade": "可轻仓试仓",
            "trade_grade_source": "setup",
            "signal_side": "long",
            "signal_side_reason": "短线偏多，波动放大。",
            "setup_kind": "directional_probe",
            "risk_reward_ratio": 1.7,
            "risk_reward_state": "acceptable",
            "model_win_probability": 0.63,
            "execution_open_probability": 0.59,
            "entry_zone_side": "upper",
            "entry_zone_side_text": "上沿",
            "multi_timeframe_alignment": "mixed",
            "key_level_state": "mid_range",
            "breakout_state": "none",
            "retest_state": "none",
            "event_risk_mode_text": "正常观察",
            "execution_note": "探索试仓：方向已明确，等待真实样本。",
            "strategy_param_summary": "方向试仓 / 探索 / RR 1.80R / 日上限 3 次 / 冷却 10 分钟",
            "strategy_param_snapshot": {
                "strategy_family": "directional_probe",
                "execution_profile": "exploratory",
                "min_rr": 1.80,
                "daily_limit": 3,
                "cooldown_min": 10,
            },
            "price": 4739.48,
            "sl": 4721.85,
            "tp": 4792.72,
            "tp2": 4819.34,
        },
        quantity=0.12,
        required_margin=568.74,
        sizing_balance=250.0,
        risk_budget_pct=0.005,
        db_path=db_path,
    )

    record_trade_learning_close(
        sim_position_id=101,
        exit_price=4721.85,
        profit=-21.16,
        reason="止损离场：突破未延续，回撤触发保护止损",
        db_path=db_path,
    )

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            "SELECT * FROM trade_learning_journal WHERE sim_position_id = ?",
            (101,),
        ).fetchone()

    assert row is not None
    assert row["execution_profile"] == "exploratory"
    assert row["symbol"] == "XAUUSD"
    assert abs(float(row["sizing_reference_balance"]) - 250.0) < 1e-9
    assert abs(float(row["risk_budget_pct"]) - 0.005) < 1e-9
    assert row["outcome_label"] == "fail"
    assert row["close_reason_key"] == "stop_loss"

    payload = json.loads(str(row["entry_payload_json"] or "{}"))
    tags = json.loads(str(row["loss_reason_tags_json"] or "[]"))

    assert payload["setup_kind"] == "directional_probe"
    assert payload["strategy_family"] == "directional_probe"
    assert payload["execution_profile"] == "exploratory"
    assert payload["strategy_param_summary"] == "方向试仓 / 探索 / RR 1.80R / 日上限 3 次 / 冷却 10 分钟"
    assert payload["strategy_param_snapshot"]["min_rr"] == 1.80
    assert payload["strategy_param_snapshot"]["daily_limit"] == 3
    assert "探索试仓" in tags
    assert "策略:directional_probe" in tags
    assert "止损亏损" in tags
    assert "多周期分歧" in tags
    assert "中段起动" in tags
    assert "缺少确认" in tags
    assert "偏追价" in tags
    assert "盈亏比一般" in tags


def test_summarize_trade_learning_by_strategy_groups_entry_payload_family(tmp_path):
    db_path = tmp_path / "knowledge.db"

    with open_knowledge_connection(db_path=db_path, ensure_schema=True):
        pass

    for idx, (family, profit, reason) in enumerate(
        [
            ("pullback_sniper_probe", 18.5, "止盈离场"),
            ("pullback_sniper_probe", -9.5, "止损离场"),
            ("directional_probe", 0.0, ""),
        ],
        start=1,
    ):
        record_trade_learning_open(
            sim_position_id=200 + idx,
            user_id="system",
            meta={
                "snapshot_time": "2026-04-23 10:00:00",
                "symbol": "XAUUSD",
                "action": "long",
                "execution_profile": "exploratory",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "setup",
                "setup_kind": family,
                "risk_reward_ratio": 1.6,
                "risk_reward_state": "acceptable",
                "price": 4778.0,
                "sl": 4766.0,
                "tp": 4796.0,
            },
            quantity=0.01,
            required_margin=50.0,
            sizing_balance=1000.0,
            risk_budget_pct=0.005,
            db_path=db_path,
        )
        if reason:
            record_trade_learning_close(
                sim_position_id=200 + idx,
                exit_price=4796.0 if profit > 0 else 4766.0,
                profit=profit,
                reason=reason,
                db_path=db_path,
            )

    summary = summarize_trade_learning_by_strategy(days=7, db_path=db_path, limit=5)
    rows = {row["strategy_family"]: row for row in summary["rows"]}

    assert summary["total_count"] == 3
    assert rows["pullback_sniper_probe"]["total_count"] == 2
    assert rows["pullback_sniper_probe"]["win_count"] == 1
    assert rows["pullback_sniper_probe"]["loss_count"] == 1
    assert rows["pullback_sniper_probe"]["win_rate"] == 50.0
    assert abs(rows["pullback_sniper_probe"]["net_profit"] - 9.0) < 1e-9
    assert rows["directional_probe"]["open_or_mixed_count"] == 1
