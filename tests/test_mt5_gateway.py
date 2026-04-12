import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mt5_gateway


def test_is_live_tick_rejects_stale_tick():
    tick = SimpleNamespace(time=1_000, bid=1.1, ask=1.2, last=0.0)
    assert mt5_gateway._is_live_tick(tick, now_ts=1_050, max_age_sec=180) is True
    assert mt5_gateway._is_live_tick(tick, now_ts=1_400, max_age_sec=180) is False


def test_is_connection_alive_uses_terminal_info(monkeypatch):
    class FakeMt5:
        @staticmethod
        def terminal_info():
            return {"name": "MetaTrader 5"}

    monkeypatch.setattr(mt5_gateway, "HAS_MT5", True)
    monkeypatch.setattr(mt5_gateway, "mt5", FakeMt5)
    monkeypatch.setattr(mt5_gateway, "_mt5_initialized", True)
    assert mt5_gateway._is_connection_alive() is True


def test_is_connection_alive_handles_missing_terminal(monkeypatch):
    class FakeMt5:
        @staticmethod
        def terminal_info():
            return None

    monkeypatch.setattr(mt5_gateway, "HAS_MT5", True)
    monkeypatch.setattr(mt5_gateway, "mt5", FakeMt5)
    monkeypatch.setattr(mt5_gateway, "_mt5_initialized", True)
    assert mt5_gateway._is_connection_alive() is False
