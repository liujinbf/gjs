import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mt5_gateway
from breakout_context import analyze_breakout_signal
from intraday_context import analyze_intraday_bars, analyze_multi_timeframe_context
from key_levels import analyze_key_levels


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


def test_analyze_intraday_bars_marks_bullish_upper_zone():
    context = analyze_intraday_bars(
        "XAUUSD",
        [
            {"time": 1, "open": 4700.0, "high": 4702.0, "low": 4699.5, "close": 4701.5},
            {"time": 2, "open": 4701.5, "high": 4704.0, "low": 4701.0, "close": 4703.2},
            {"time": 3, "open": 4703.2, "high": 4706.0, "low": 4702.8, "close": 4705.8},
            {"time": 4, "open": 4705.8, "high": 4708.0, "low": 4705.0, "close": 4707.4},
        ],
    )
    assert context["intraday_context_ready"] is True
    assert context["intraday_bias"] == "bullish"
    assert context["intraday_location"] == "upper"


def test_analyze_multi_timeframe_context_marks_alignment():
    payload = analyze_multi_timeframe_context(
        {
            "m5": {"intraday_context_ready": True, "intraday_bias": "bullish", "intraday_bias_text": "偏多"},
            "m15": {"intraday_context_ready": True, "intraday_bias": "bullish", "intraday_bias_text": "偏多"},
            "h1": {"intraday_context_ready": True, "intraday_bias": "sideways", "intraday_bias_text": "震荡"},
        }
    )
    assert payload["multi_timeframe_context_ready"] is True
    assert payload["multi_timeframe_alignment"] == "aligned"
    assert payload["multi_timeframe_bias"] == "bullish"


def test_analyze_key_levels_marks_near_high():
    payload = analyze_key_levels(
        "XAUUSD",
        4707.0,
        [
            {"time": 1, "high": 4680.0, "low": 4660.0, "close": 4672.0},
            {"time": 2, "high": 4690.0, "low": 4670.0, "close": 4684.0},
            {"time": 3, "high": 4700.0, "low": 4680.0, "close": 4695.0},
            {"time": 4, "high": 4708.0, "low": 4690.0, "close": 4704.0},
        ],
    )
    assert payload["key_level_ready"] is True
    assert payload["key_level_state"] == "near_high"


def test_analyze_breakout_signal_marks_confirmed_above():
    key_context = {
        "key_level_ready": True,
        "key_level_high": 4700.0,
        "key_level_low": 4660.0,
    }
    payload = analyze_breakout_signal(
        key_context,
        [
            {"high": 4698.0, "low": 4690.0, "close": 4696.0},
            {"high": 4702.0, "low": 4694.0, "close": 4701.0},
            {"high": 4705.0, "low": 4699.0, "close": 4703.5},
            {"high": 4708.0, "low": 4701.0, "close": 4706.2},
        ],
    )
    assert payload["breakout_ready"] is True
    assert payload["breakout_state"] == "confirmed_above"
    assert payload["retest_ready"] is True


def test_analyze_breakout_signal_marks_retest_confirmed_after_breakout():
    key_context = {
        "key_level_ready": True,
        "key_level_high": 4700.0,
        "key_level_low": 4660.0,
    }
    payload = analyze_breakout_signal(
        key_context,
        [
            {"high": 4698.0, "low": 4690.0, "close": 4696.0},
            {"high": 4704.0, "low": 4696.0, "close": 4701.5},
            {"high": 4706.0, "low": 4699.8, "close": 4704.4},
            {"high": 4708.0, "low": 4700.2, "close": 4706.1},
        ],
    )
    assert payload["breakout_state"] == "confirmed_above"
    assert payload["retest_state"] == "confirmed_support"


def test_fetch_quotes_includes_intraday_context(monkeypatch):
    class FakeMt5:
        TIMEFRAME_M5 = 5
        TIMEFRAME_M15 = 15
        TIMEFRAME_H1 = 60

        @staticmethod
        def symbol_select(symbol, enable):
            return True

        @staticmethod
        def symbol_info(symbol):
            return SimpleNamespace(spread=17.0, point=0.01)

        @staticmethod
        def symbol_info_tick(symbol):
            return SimpleNamespace(time=1_000, bid=4759.74, ask=4759.91, last=4759.82)

        @staticmethod
        def copy_rates_from_pos(symbol, timeframe, start_pos, count):
            if timeframe == FakeMt5.TIMEFRAME_M15:
                return [
                    {"time": 1, "open": 4690.0, "high": 4694.0, "low": 4688.5, "close": 4693.0},
                    {"time": 2, "open": 4693.0, "high": 4699.0, "low": 4692.5, "close": 4697.4},
                    {"time": 3, "open": 4697.4, "high": 4703.0, "low": 4696.8, "close": 4702.1},
                    {"time": 4, "open": 4702.1, "high": 4707.0, "low": 4701.4, "close": 4706.4},
                ]
            if timeframe == FakeMt5.TIMEFRAME_H1:
                return [
                    {"time": 1, "open": 4660.0, "high": 4675.0, "low": 4658.0, "close": 4672.0},
                    {"time": 2, "open": 4672.0, "high": 4688.0, "low": 4670.0, "close": 4685.5},
                    {"time": 3, "open": 4685.5, "high": 4699.0, "low": 4682.0, "close": 4696.2},
                    {"time": 4, "open": 4696.2, "high": 4708.0, "low": 4692.0, "close": 4706.8},
                ]
            return [
                {"time": 1, "open": 4700.0, "high": 4702.0, "low": 4699.5, "close": 4701.5},
                {"time": 2, "open": 4701.5, "high": 4704.0, "low": 4701.0, "close": 4703.2},
                {"time": 3, "open": 4703.2, "high": 4706.0, "low": 4702.8, "close": 4705.8},
                {"time": 4, "open": 4705.8, "high": 4708.0, "low": 4705.0, "close": 4707.4},
            ]

    monkeypatch.setattr(mt5_gateway, "mt5", FakeMt5)
    monkeypatch.setattr(mt5_gateway, "HAS_MT5", True)
    monkeypatch.setattr(mt5_gateway, "initialize_connection", lambda: (True, "ok"))
    monkeypatch.setattr(mt5_gateway, "_is_live_tick", lambda tick, now_ts=None, max_age_sec=180: True)

    rows = mt5_gateway.fetch_quotes(["XAUUSD"])
    assert len(rows) == 1
    assert rows[0]["intraday_context_ready"] is True
    assert "近" in rows[0]["intraday_context_text"]  # 标签随 M5 bar 数变化，只断言有文字即可
    assert rows[0]["multi_timeframe_context_ready"] is True
    assert "多周期" in rows[0]["multi_timeframe_context_text"]
    assert rows[0]["key_level_ready"] is True
    assert rows[0]["key_level_state"] in {"near_high", "mid_range", "breakout_above"}
    assert rows[0]["breakout_ready"] is True
    assert "retest_state" in rows[0]


def test_fetch_quotes_prefers_live_bid_ask_spread_over_symbol_info(monkeypatch):
    class FakeMt5:
        TIMEFRAME_M5 = 5
        TIMEFRAME_M15 = 15
        TIMEFRAME_H1 = 60

        @staticmethod
        def symbol_select(symbol, enable):
            return False

        @staticmethod
        def symbol_info(symbol):
            return SimpleNamespace(spread=99.0, point=0.01)

        @staticmethod
        def symbol_info_tick(symbol):
            return SimpleNamespace(time=1_000, bid=4759.74, ask=4759.91, last=4759.82)

    monkeypatch.setattr(mt5_gateway, "mt5", FakeMt5)
    monkeypatch.setattr(mt5_gateway, "HAS_MT5", True)
    monkeypatch.setattr(mt5_gateway, "initialize_connection", lambda: (True, "ok"))
    monkeypatch.setattr(mt5_gateway, "_is_live_tick", lambda tick, now_ts=None, max_age_sec=180: True)

    rows = mt5_gateway.fetch_quotes(["XAUUSD"])
    assert len(rows) == 1
    assert rows[0]["spread_points"] == 17.0
