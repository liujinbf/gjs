import os
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

    monkeypatch.setattr(ui, "record_snapshot", fake_record_snapshot)
    monkeypatch.setattr(ui, "build_snapshot_history_entries", fake_build_snapshot_history_entries)
    monkeypatch.setattr(ui, "append_history_entries", fake_append_history_entries)
    monkeypatch.setattr(ui, "send_notifications", fake_send_notifications)
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", fake_update_prices)

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 3310.2,
                "bid": 3310.1,
                "ask": 3310.3,
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
    assert captured["notified"]["cooldown"] == 30
    assert any("知识库" in line for line in result["log_lines"])
    assert any("消息推送" in line for line in result["log_lines"])
