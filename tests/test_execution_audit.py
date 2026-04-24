import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution_audit import (
    fetch_recent_execution_audits,
    record_execution_audit,
    summarize_execution_audits,
    summarize_execution_reason_counts,
    summarize_today_execution_audits,
)
from knowledge_base import open_knowledge_connection
from knowledge_runtime import record_snapshot


def test_record_execution_audit_persists_snapshot_link(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = {
        "last_refresh_text": "2026-04-22 10:00:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 3300.0,
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "signal_side": "long",
            }
        ],
    }
    record_snapshot(snapshot, db_path=db_path)

    result = record_execution_audit(
        source_kind="ai_auto",
        decision_status="opened",
        snapshot=snapshot,
        meta={"symbol": "XAUUSD", "action": "long", "price": 3300.0, "sl": 3290.0, "tp": 3330.0},
        signal_signature="sig-1",
        result_message="成功开仓 0.10 手 XAUUSD",
        db_path=db_path,
    )

    assert result["snapshot_id"] > 0
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            """
            SELECT source_kind, decision_status, symbol, action, snapshot_id, signal_signature
            FROM execution_audits
            """
        ).fetchone()
    assert row["source_kind"] == "ai_auto"
    assert row["decision_status"] == "opened"
    assert row["symbol"] == "XAUUSD"
    assert row["action"] == "long"
    assert row["snapshot_id"] == result["snapshot_id"]
    assert row["signal_signature"] == "sig-1"


def test_summarize_execution_audits_counts_rows(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_execution_audit(
        source_kind="rule_engine",
        decision_status="opened",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        db_path=db_path,
    )
    record_execution_audit(
        source_kind="rule_engine",
        decision_status="rejected",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        result_message="可用保证金不足",
        db_path=db_path,
    )

    summary = summarize_execution_audits(days=30, source_kind="rule_engine", db_path=db_path)
    assert summary["total_count"] == 2
    assert summary["counts"]["opened"] == 1
    assert summary["counts"]["rejected"] == 1


def test_summarize_today_execution_audits_counts_status_and_reason(tmp_path):
    from datetime import datetime

    db_path = tmp_path / "knowledge.db"
    record_execution_audit(
        source_kind="rule_engine",
        trade_mode="simulation",
        decision_status="opened",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        db_path=db_path,
    )
    record_execution_audit(
        source_kind="rule_engine",
        trade_mode="simulation",
        decision_status="blocked",
        reason_key="exploratory_cooldown",
        result_message="同向冷却",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        db_path=db_path,
    )
    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        conn.execute(
            "UPDATE execution_audits SET occurred_at='2026-04-22 11:00:00', created_at='2026-04-22 11:00:00'"
        )

    summary = summarize_today_execution_audits(
        now=datetime(2026, 4, 22, 18, 0, 0),
        trade_mode="simulation",
        symbol="XAUUSD",
        db_path=db_path,
    )

    assert summary["date"] == "2026-04-22"
    assert summary["total_count"] == 2
    assert summary["counts"]["opened"] == 1
    assert summary["counts"]["blocked"] == 1
    assert summary["reason_counts"]["exploratory_cooldown"] == 1


def test_summarize_execution_reason_counts_filters_symbol(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_execution_audit(
        source_kind="ai_auto",
        decision_status="blocked",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        result_message="已有活跃持仓，跳过自动试仓",
        db_path=db_path,
    )
    record_execution_audit(
        source_kind="ai_auto",
        decision_status="blocked",
        snapshot={},
        meta={"symbol": "XAGUSD", "action": "short"},
        result_message="可用保证金不足",
        db_path=db_path,
    )

    summary = summarize_execution_audits(hours=48, symbol="XAUUSD", db_path=db_path)
    reason_rows = summarize_execution_reason_counts(hours=48, symbol="XAUUSD", db_path=db_path)

    assert summary["total_count"] == 1
    assert summary["counts"]["blocked"] == 1
    assert reason_rows[0]["reason_key"] == "existing_position"
    assert reason_rows[0]["count"] == 1


def test_fetch_recent_execution_audits_returns_latest_rows(tmp_path):
    db_path = tmp_path / "knowledge.db"
    record_execution_audit(
        source_kind="rule_engine",
        decision_status="blocked",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        result_message="已有活跃持仓，跳过自动试仓",
        db_path=db_path,
    )
    record_execution_audit(
        source_kind="sim_engine",
        decision_status="closed",
        snapshot={},
        meta={"symbol": "XAUUSD", "action": "long"},
        result_message="目标1止盈；本次盈亏 23.5 美元",
        db_path=db_path,
    )

    rows = fetch_recent_execution_audits(hours=48, symbol="XAUUSD", limit=2, db_path=db_path)

    assert len(rows) == 2
    assert rows[0]["decision_status"] == "closed"
    assert rows[0]["reason_key"] == "take_profit"
    assert rows[1]["decision_status"] == "blocked"
