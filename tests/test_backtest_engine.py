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


def test_extract_signal_meta_supports_prefixed_markdown_json_without_json_repair(monkeypatch):
    monkeypatch.setattr(backtest_engine, "_json_repair_loads", None)
    content = (
        "好的，为您分析如下：\n"
        "```json\n"
        '{"summary_text":"当前结论：轻仓试空。",'
        '"signal_meta":{"symbol":"XAUUSD","action":"short","price":2350.5,"sl":2362.0,"tp":2328.0}}'
        "\n```"
    )

    meta = extract_signal_meta(content)

    assert meta == {
        "symbol": "XAUUSD",
        "action": "short",
        "price": 2350.5,
        "sl": 2362.0,
        "tp": 2328.0,
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


def test_save_backtest_results_uses_atomic_replace(monkeypatch, tmp_path):
    result_file = tmp_path / "backtest_results.json"
    replaced = {"called": False}
    original_replace = Path.replace

    def spy_replace(self, target):
        if str(self).endswith(".tmp"):
            replaced["called"] = True
        return original_replace(self, target)

    monkeypatch.setattr(backtest_engine, "BACKTEST_RESULTS_FILE", result_file)
    monkeypatch.setattr(Path, "replace", spy_replace)

    backtest_engine.save_backtest_results(
        {
            "sig-1": {
                "status": "win",
                "occurred_at": "2026-04-16 10:00:00",
            }
        }
    )

    assert replaced["called"] is True
    assert result_file.exists()
    assert backtest_engine.load_backtest_results()["sig-1"]["status"] == "win"
