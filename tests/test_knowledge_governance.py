import sys
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import import_markdown_source, open_knowledge_connection
from knowledge_governance import (
    apply_strategy_learning_review,
    build_learning_report,
    read_latest_learning_report,
    refresh_rule_governance,
    sync_strategy_learning_reviews,
    summarize_sim_trade_profiles,
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
    import_markdown_source(file_path, db_path=db_path, source_type="auto_miner")

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


def test_refresh_rule_governance_sets_active_and_archived(tmp_path):
    db_path = tmp_path / "knowledge.db"
    _prepare_runtime_scores(db_path, include_negative_rule=False, include_negative_samples=False)

    result = refresh_rule_governance(db_path=db_path, horizon_min=30)
    summary = summarize_rule_governance(db_path=db_path, horizon_min=30)

    assert result["updated_count"] >= 1
    assert summary["watch_count"] >= 1
    assert summary["archived_count"] >= 1


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


def test_build_learning_report_groups_standard_and_exploratory_sim_stats(tmp_path):
    db_path = tmp_path / "knowledge.db"
    sim_db_path = tmp_path / "sim.sqlite"
    _prepare_runtime_scores(db_path)
    refresh_rule_governance(db_path=db_path, horizon_min=30)

    with sqlite3.connect(str(sim_db_path)) as conn:
        conn.execute(
            """
            CREATE TABLE sim_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                symbol TEXT,
                action TEXT,
                entry_price REAL,
                exit_price REAL,
                quantity REAL,
                profit REAL,
                closed_at TEXT,
                reason TEXT,
                execution_profile TEXT DEFAULT 'standard'
            )
            """
        )
        rows = [
            ("system", "XAUUSD", "long", 100.0, 102.0, 0.1, 20.0, "2026-04-22 10:00:00", "止盈", "standard"),
            ("system", "XAUUSD", "short", 100.0, 101.0, 0.1, -10.0, "2026-04-22 11:00:00", "止损", "standard"),
            ("system", "XAUUSD", "long", 100.0, 101.0, 0.1, 10.0, "2026-04-22 12:00:00", "止盈", "exploratory"),
            ("system", "XAUUSD", "long", 100.0, 99.0, 0.1, -10.0, "2026-04-22 13:00:00", "止损", "exploratory"),
            ("system", "XAUUSD", "long", 100.0, 98.0, 0.1, -20.0, "2026-04-22 14:00:00", "止损", "exploratory"),
        ]
        conn.executemany(
            """
            INSERT INTO sim_trades (
                user_id, symbol, action, entry_price, exit_price, quantity, profit,
                closed_at, reason, execution_profile
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    now = datetime(2026, 4, 22, 18, 0, 0)
    stats = summarize_sim_trade_profiles(sim_db_path=sim_db_path, days=30, now=now)
    report = build_learning_report(db_path=db_path, sim_db_path=sim_db_path, now=now, horizon_min=30, persist=True)
    latest = read_latest_learning_report(db_path=db_path)

    assert stats["profiles"]["standard"]["total_count"] == 2
    assert stats["profiles"]["standard"]["win_rate"] == 0.5
    assert stats["profiles"]["exploratory"]["total_count"] == 3
    assert round(stats["profiles"]["exploratory"]["win_rate"], 2) == 0.33
    assert stats["profiles"]["exploratory"]["net_profit"] == -20.0
    assert "标准试仓 2 笔，胜率 50%，净盈亏 +$10.00" in report["summary_text"]
    assert "探索试仓 3 笔，胜率 33%，净盈亏 -$20.00" in report["summary_text"]
    assert latest["sim_trade_profile_summary"]["profiles"]["exploratory"]["total_count"] == 3


def test_sync_strategy_learning_reviews_creates_manual_review_rule(tmp_path):
    db_path = tmp_path / "knowledge.db"
    opened_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        for idx, profit in enumerate([-12.0, -8.0, 3.0], start=1):
            conn.execute(
                """
                INSERT INTO trade_learning_journal (
                    sim_position_id, symbol, action, setup_kind, risk_reward_ratio,
                    opened_at, updated_at, outcome_label, profit, entry_payload_json
                ) VALUES (?, 'XAUUSD', 'long', 'pullback_sniper_probe', 1.45, ?, ?, ?, ?, ?)
                """,
                (
                    idx,
                    opened_at,
                    opened_at,
                    "success" if profit > 0 else "fail",
                    profit,
                    json.dumps({"strategy_family": "pullback_sniper_probe"}, ensure_ascii=False),
                ),
            )

    result = sync_strategy_learning_reviews(db_path=db_path, days=7, limit=5)

    assert result["created_count"] == 1
    assert result["review_count"] == 1
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            """
            SELECT
                kr.rule_text,
                kr.category,
                ks.source_type,
                rg.governance_status,
                rg.rationale,
                rs.sample_count,
                rs.success_count,
                rs.fail_count,
                rs.validation_status
            FROM knowledge_rules kr
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            JOIN rule_governance rg ON rg.rule_id = kr.id AND rg.horizon_min = 30
            JOIN rule_scores rs ON rs.rule_id = kr.id AND rs.horizon_min = 30
            WHERE ks.source_type = 'strategy_learning'
            LIMIT 1
            """
        ).fetchone()

    assert row is not None
    assert row["rule_text"] == "策略学习建议：收紧回调狙击探索入场阈值"
    assert row["category"] == "risk"
    assert row["source_type"] == "strategy_learning"
    assert row["governance_status"] == "manual_review"
    assert "1胜2负" in row["rationale"]
    assert "净盈亏 -$17.00" in row["rationale"]
    assert row["sample_count"] == 3
    assert row["success_count"] == 1
    assert row["fail_count"] == 2
    assert row["validation_status"] == "candidate"


def test_refresh_rule_governance_preserves_strategy_learning_review(tmp_path):
    db_path = tmp_path / "knowledge.db"
    opened_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        for idx, profit in enumerate([-10.0, -7.0, 2.0], start=1):
            conn.execute(
                """
                INSERT INTO trade_learning_journal (
                    sim_position_id, symbol, action, setup_kind, risk_reward_ratio,
                    opened_at, updated_at, outcome_label, profit, entry_payload_json
                ) VALUES (?, 'XAUUSD', 'long', 'directional_probe', 1.80, ?, ?, ?, ?, ?)
                """,
                (
                    idx,
                    opened_at,
                    opened_at,
                    "success" if profit > 0 else "fail",
                    profit,
                    json.dumps({"strategy_family": "directional_probe"}, ensure_ascii=False),
                ),
            )

    sync_strategy_learning_reviews(db_path=db_path, days=7, limit=5)
    refresh_rule_governance(db_path=db_path, horizon_min=30)

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        status = conn.execute(
            """
            SELECT rg.governance_status
            FROM rule_governance rg
            JOIN knowledge_rules kr ON kr.id = rg.rule_id
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            WHERE ks.source_type = 'strategy_learning'
            LIMIT 1
            """
        ).fetchone()[0]

    assert status == "manual_review"


def test_apply_strategy_learning_review_tightens_only_that_strategy(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    opened_at = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        for idx, profit in enumerate([-10.0, -7.0, 2.0], start=1):
            conn.execute(
                """
                INSERT INTO trade_learning_journal (
                    sim_position_id, symbol, action, setup_kind, risk_reward_ratio,
                    opened_at, updated_at, outcome_label, profit, entry_payload_json
                ) VALUES (?, 'XAUUSD', 'long', 'pullback_sniper_probe', 1.45, ?, ?, ?, ?, ?)
                """,
                (
                    idx,
                    opened_at,
                    opened_at,
                    "success" if profit > 0 else "fail",
                    profit,
                    json.dumps({"strategy_family": "pullback_sniper_probe"}, ensure_ascii=False),
                ),
            )
    sync_strategy_learning_reviews(db_path=db_path, days=7, limit=5)
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        rule_id = conn.execute(
            """
            SELECT kr.id
            FROM knowledge_rules kr
            JOIN knowledge_sources ks ON ks.id = kr.source_id
            WHERE ks.source_type = 'strategy_learning'
            LIMIT 1
            """
        ).fetchone()[0]

    saved = {}
    config = SimpleNamespace(
        sim_strategy_min_rr={
            "pullback_sniper_probe": 1.45,
            "directional_probe": 1.80,
        },
        sim_strategy_daily_limit={
            "pullback_sniper_probe": 3,
            "directional_probe": 3,
        },
        sim_strategy_cooldown_min={
            "pullback_sniper_probe": 10,
            "directional_probe": 10,
        },
    )
    monkeypatch.setattr("knowledge_governance.get_runtime_config", lambda: config)
    monkeypatch.setattr(
        "knowledge_governance.save_runtime_config",
        lambda cfg: saved.update(
            {
                "rr": dict(cfg.sim_strategy_min_rr),
                "daily_limit": dict(cfg.sim_strategy_daily_limit),
                "cooldown_min": dict(cfg.sim_strategy_cooldown_min),
            }
        ),
    )

    result = apply_strategy_learning_review(rule_id, approved=True, db_path=db_path)

    assert result["applied"] is True
    assert result["old_rr"] == 1.45
    assert result["new_rr"] == 1.60
    assert result["old_daily_limit"] == 3
    assert result["new_daily_limit"] == 2
    assert result["old_cooldown_min"] == 10
    assert result["new_cooldown_min"] == 15
    assert abs(saved["rr"]["pullback_sniper_probe"] - 1.60) < 1e-9
    assert abs(saved["rr"]["directional_probe"] - 1.80) < 1e-9
    assert saved["daily_limit"]["pullback_sniper_probe"] == 2
    assert saved["daily_limit"]["directional_probe"] == 3
    assert saved["cooldown_min"]["pullback_sniper_probe"] == 15
    assert saved["cooldown_min"]["directional_probe"] == 10
    assert "日上限已由 3 调整为 2" in result["message"]
    assert "冷却已由 10 分钟调整为 15 分钟" in result["message"]


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
