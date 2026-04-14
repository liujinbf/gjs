import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alert_status_state import apply_alert_state_transitions, read_recent_transitions
from knowledge_base import kv_get, kv_set


def test_alert_status_state_persists_to_sqlite_when_state_file_is_none(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    legacy_state_file = tmp_path / "alert_status_state.json"
    monkeypatch.setattr("alert_status_state.kv_get", lambda key: kv_get(key, db_path=db_path))
    monkeypatch.setattr("alert_status_state.kv_set", lambda key, value: kv_set(key, value, db_path=db_path))
    monkeypatch.setattr("alert_status_state.ALERT_STATUS_STATE_FILE", legacy_state_file)

    first = apply_alert_state_transitions(
        [{"symbol": "XAUUSD", "alert_state_text": "点差异常进行中"}],
        state_file=None,
        now=datetime(2026, 4, 14, 10, 0, 0),
    )
    second = apply_alert_state_transitions(
        [{"symbol": "XAUUSD", "alert_state_text": "点差已恢复"}],
        state_file=None,
        now=datetime(2026, 4, 14, 10, 5, 0),
    )
    transitions = read_recent_transitions(
        state_file=None,
        now=datetime(2026, 4, 14, 10, 10, 0),
        window_min=30,
    )

    assert first[0]["alert_state_transition_text"] == ""
    assert "点差异常进行中 -> 点差已恢复" in second[0]["alert_state_transition_text"]
    assert transitions
    assert transitions[0]["symbol"] == "XAUUSD"
