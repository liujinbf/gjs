import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import backtest_engine
from backtest_engine import extract_signal_meta
from datetime import datetime


def test_extract_signal_meta_supports_new_json_payload():
    content = (
        '{"summary_text":"当前结论：轻仓试多。",'
        '"signal_meta":{"symbol":"XAUUSD","action":"long","price":2350.5,"sl":2342.0,"tp":2366.0}}'
    )
    meta = extract_signal_meta(content)
    assert meta == {
        "symbol": "XAUUSD",
        "action": "long",
        "price": 2350.5,
        "sl": 2342.0,
        "tp": 2366.0,
    }


def test_extract_signal_meta_keeps_backward_compatibility():
    content = (
        "当前结论：继续观察。\n"
        "<!-- TRACKER_META: {\"symbol\": \"EURUSD\", \"action\": \"short\", "
        "\"price\": 1.1, \"sl\": 1.11, \"tp\": 1.08} -->"
    )
    meta = extract_signal_meta(content)
    assert meta == {
        "symbol": "EURUSD",
        "action": "short",
        "price": 1.1,
        "sl": 1.11,
        "tp": 1.08,
    }


def test_evaluate_signal_uses_conservative_loss_when_same_bar_hits_tp_and_sl(monkeypatch):
    class FakeMt5:
        TIMEFRAME_M5 = 5

        @staticmethod
        def copy_rates_range(symbol, timeframe, start_time, now_time):
            return [
                {"high": 2362.0, "low": 2340.0},
            ]

    monkeypatch.setattr(backtest_engine, "HAS_MT5", True)
    monkeypatch.setattr(backtest_engine, "mt5", FakeMt5)

    status = backtest_engine.evaluate_signal(
        {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 2350.0,
            "sl": 2342.0,
            "tp": 2360.0,
        },
        datetime(2026, 4, 16, 10, 0, 0),
        datetime(2026, 4, 16, 10, 10, 0),
    )

    assert status == "loss"
