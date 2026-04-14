import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_ai_signals import record_ai_signal, summarize_recent_ai_signals
from knowledge_base import open_knowledge_connection


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
        "model": "demo-model",
        "api_base": "https://example.com/v1",
    }

    first = record_ai_signal(result, _build_snapshot(), push_result={"messages": ["ok"]}, db_path=db_path)
    second = record_ai_signal(result, _build_snapshot(), push_result={"messages": ["ok"]}, db_path=db_path)

    assert first["inserted_count"] == 1
    assert second["inserted_count"] == 0

    with open_knowledge_connection(db_path=db_path, ensure_schema=True) as conn:
        row = conn.execute(
            "SELECT symbol, action, signal_meta_valid, push_sent FROM ai_signal_events"
        ).fetchone()
    assert row["symbol"] == "XAUUSD"
    assert row["action"] == "long"
    assert row["signal_meta_valid"] == 1
    assert row["push_sent"] == 1


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
