import os
import queue
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

import ui


def test_process_snapshot_side_effects_runs_io_chain(monkeypatch):
    captured = {
        "quotes": None,
        "notified": None,
        "sim_exec": None,
    }

    def fake_record_snapshot(snapshot):
        assert snapshot["last_refresh_text"] == "2026-04-13 10:00:00"
        return {
            "inserted_count": 1,
            "inserted_snapshot_ids": [101],
            "snapshot_bindings": {"XAUUSD": 101},
        }

    def fake_build_snapshot_history_entries(snapshot):
        return [{"symbol": "XAUUSD", "title": "结构候选"}]

    def fake_append_history_entries(entries):
        assert len(entries) == 1
        return 1

    def fake_send_notifications(entries, config):
        captured["notified"] = {
            "entries": list(entries),
            "cooldown": getattr(config, "notify_cooldown_min", None),
        }
        return {
            "messages": ["已发送 1 条提醒"],
            "errors": [],
        }

    def fake_update_prices(quotes):
        captured["quotes"] = quotes

    def fake_get_open_positions():
        return []

    def fake_execute_signal(meta):
        captured["sim_exec"] = dict(meta)
        return True, "ok"

    monkeypatch.setattr(ui, "record_snapshot", fake_record_snapshot)
    monkeypatch.setattr(ui, "build_snapshot_history_entries", fake_build_snapshot_history_entries)
    monkeypatch.setattr(ui, "append_history_entries", fake_append_history_entries)
    monkeypatch.setattr(ui, "send_notifications", fake_send_notifications)
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", fake_update_prices)
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", fake_get_open_positions)
    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", fake_execute_signal)

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 3310.2,
                "bid": 3310.1,
                "ask": 3310.3,
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.2,
                "risk_reward_stop_price": 3299.0,
                "risk_reward_target_price": 3335.0,
                "risk_reward_entry_zone_low": 3308.0,
                "risk_reward_entry_zone_high": 3312.0,
            }
        ],
    }
    config = SimpleNamespace(notify_cooldown_min=30)

    result = ui.process_snapshot_side_effects(snapshot, config, run_backtest=False)

    assert result["snapshot_inserted_count"] == 1
    assert result["snapshot_ids"] == [101]
    assert result["snapshot_bindings"] == {"XAUUSD": 101}
    assert result["refresh_histories"] is True
    assert result["notify_status_changed"] is True
    assert result["sim_data_changed"] is True
    assert captured["quotes"] == {
        "XAUUSD": {
            "latest": 3310.2,
            "bid": 3310.1,
            "ask": 3310.3,
        }
    }
    assert captured["sim_exec"]["symbol"] == "XAUUSD"
    assert captured["sim_exec"]["action"] == "long"
    assert captured["notified"]["cooldown"] == 30
    assert any("知识库" in line for line in result["log_lines"])
    assert any("消息推送" in line for line in result["log_lines"])
    assert any("模拟盘规则跟单" in line for line in result["log_lines"])


def test_queue_latest_task_drops_oldest_snapshot_when_full(monkeypatch):
    task_queue = queue.Queue(maxsize=1)
    task_queue.put({"kind": "snapshot_side_effects", "snapshot": {"last_refresh_text": "old"}})
    monkeypatch.setattr(ui, "SNAPSHOT_TASK_QUEUE", task_queue)

    dropped_count = ui._queue_latest_task(
        {"kind": "snapshot_side_effects", "snapshot": {"last_refresh_text": "new"}}
    )

    assert dropped_count == 1
    queued = task_queue.get_nowait()
    assert queued["snapshot"]["last_refresh_text"] == "new"
