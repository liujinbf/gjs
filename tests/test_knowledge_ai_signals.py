import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_ai_signals import backfill_ai_signal_execution_audit, record_ai_signal, summarize_recent_ai_signals
from knowledge_base import open_knowledge_connection
from knowledge_runtime import record_snapshot
from quote_models import SnapshotItem


def _build_snapshot() -> dict:
    return {
        "last_refresh_text": "2026-04-14 16:20:00",
        "items": [
            {"symbol": "XAUUSD"},
            {"symbol": "EURUSD"},
        ],
    }


def test_record_ai_signal_persists_structured_event(tmp_path):
    db_path = tmp_path / "knowledge.db"
    result = {
        "content": "当前结论：轻仓试多。",
        "signal_meta": {"symbol": "XAUUSD", "action": "long", "price": 2350.0, "sl": 2342.0, "tp": 2366.0},
        "signal_schema_version": "signal-meta-v1",
        "signal_meta_valid": True,
        "signal_meta_reason": "做多信号结构有效",
        "used_structured_payload": True,
        "ai_parse_mode": "json_mode",
        "ai_raw_response_logged": True,
        "ai_raw_response_length": 148,
        "ai_raw_response_excerpt": '{"summary_text":"当前结论：轻仓试多。"}',
        "model": "demo-model",
        "api_base": "https://example.com/v1",
    }

    first = record_ai_signal(result, _build_snapshot(), push_result={"messages": ["ok"]}, db_path=db_path)
    second = record_ai_signal(result, _build_snapshot(), push_result={"messages": ["ok"]}, db_path=db_path)

    assert first["inserted_count"] == 1
    assert second["inserted_count"] == 0

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            """
            SELECT symbol, action, signal_meta_valid, used_structured_payload, ai_parse_mode,
                   ai_raw_response_logged, ai_raw_response_length, push_sent, sim_eligible, sim_block_reason
            FROM ai_signal_events
            """
        ).fetchone()
    assert row["symbol"] == "XAUUSD"
    assert row["action"] == "long"
    assert row["signal_meta_valid"] == 1
    assert row["used_structured_payload"] == 1
    assert row["ai_parse_mode"] == "json_mode"
    assert row["ai_raw_response_logged"] == 1
    assert row["ai_raw_response_length"] == 148
    assert row["push_sent"] == 1
    assert row["sim_eligible"] == 0
    assert row["sim_block_reason"] == ""


def test_summarize_recent_ai_signals_counts_valid_and_executable(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = _build_snapshot()
    record_ai_signal(
        {
            "content": "当前结论：轻仓试多。",
            "signal_meta": {"symbol": "XAUUSD", "action": "long", "price": 2350.0, "sl": 2342.0, "tp": 2366.0},
            "signal_schema_version": "signal-meta-v1",
            "signal_meta_valid": True,
            "signal_meta_reason": "做多信号结构有效",
            "used_structured_payload": True,
            "model": "demo-model",
            "api_base": "https://example.com/v1",
        },
        snapshot,
        db_path=db_path,
    )
    record_ai_signal(
        {
            "content": "当前结论：观望。",
            "signal_meta": {"symbol": "XAUUSD", "action": "neutral", "price": 0, "sl": 0, "tp": 0},
            "signal_schema_version": "signal-meta-v1",
            "signal_meta_valid": True,
            "signal_meta_reason": "观望信号",
            "model": "demo-model",
            "api_base": "https://example.com/v1",
        },
        {"last_refresh_text": "2026-04-14 16:25:00", "items": [{"symbol": "XAUUSD"}]},
        db_path=db_path,
    )

    summary = summarize_recent_ai_signals(days=30, db_path=db_path, now=datetime(2026, 4, 14, 18, 0, 0))
    assert summary["total_count"] == 2
    assert summary["valid_count"] == 2
    assert summary["executable_count"] == 1
    assert summary["sim_eligible_count"] == 0
    assert summary["structured_count"] == 1
    assert summary["fallback_count"] == 0
    assert "结构化解析 1 条，降级 0 条" in summary["summary_text"]


def test_record_ai_signal_accepts_snapshot_item_objects(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = {
        "last_refresh_text": "2026-04-14 16:30:00",
        "items": [
            SnapshotItem(symbol="XAUUSD"),
            SnapshotItem(symbol="EURUSD"),
        ],
    }

    result = record_ai_signal(
        {
            "content": "当前结论：轻仓试多。",
            "signal_meta": {"symbol": "XAUUSD", "action": "long", "price": 2350.0, "sl": 2342.0, "tp": 2366.0},
            "signal_schema_version": "signal-meta-v1",
            "signal_meta_valid": True,
            "signal_meta_reason": "做多信号结构有效",
            "model": "demo-model",
            "api_base": "https://example.com/v1",
        },
        snapshot,
        db_path=db_path,
    )

    assert result["inserted_count"] == 1
    assert '"XAUUSD"' in result["entry"]["snapshot_symbols_json"]
    assert result["entry"]["sim_eligible"] == 0


def test_backfill_ai_signal_execution_audit_updates_historical_rows(tmp_path):
    db_path = tmp_path / "knowledge.db"
    snapshot = {
        "last_refresh_text": "2026-04-14 16:40:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 2350.0,
                "has_live_quote": True,
                "trade_grade": "只适合观察",
                "trade_grade_source": "structure",
                "signal_side": "neutral",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.0,
                "risk_reward_stop_price": 2342.0,
                "risk_reward_target_price": 2366.0,
            }
        ],
    }
    record_snapshot(snapshot, db_path=db_path)
    result = record_ai_signal(
        {
            "content": "当前结论：轻仓试多。",
            "signal_meta": {"symbol": "XAUUSD", "action": "long", "price": 2350.0, "sl": 2342.0, "tp": 2366.0},
            "signal_schema_version": "signal-meta-v1",
            "signal_meta_valid": True,
            "signal_meta_reason": "做多信号结构有效",
            "model": "demo-model",
            "api_base": "https://example.com/v1",
        },
        snapshot,
        db_path=db_path,
    )

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        conn.execute(
            """
            UPDATE ai_signal_events
            SET snapshot_trade_grade = '',
                snapshot_trade_grade_source = '',
                snapshot_signal_side = 'neutral',
                snapshot_has_live_quote = 0,
                sim_eligible = 0,
                sim_block_reason = '',
                sim_block_reason_key = ''
            WHERE id = ?
            """,
            (int(result["event_id"]),),
        )

    backfill = backfill_ai_signal_execution_audit(db_path=db_path)
    assert backfill["updated_count"] == 1

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            """
            SELECT snapshot_trade_grade, snapshot_trade_grade_source, sim_eligible, sim_block_reason_key
            FROM ai_signal_events
            WHERE id = ?
            """,
            (int(result["event_id"]),),
        ).fetchone()
    assert row["snapshot_trade_grade"] == "只适合观察"
    assert row["snapshot_trade_grade_source"] == "structure"
    assert row["sim_eligible"] == 0
    assert row["sim_block_reason_key"] == "grade_gate"
