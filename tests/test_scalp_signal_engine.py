import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scalp_signal_engine import detect_scalp_signal


def test_ema_crossover_requires_previous_opposite_side():
    item = {
        "latest_price": 100.0,
        "ema9_m5": 100.05,
        "ema21_m5": 100.0,
        "prev_ema9_m5": 100.04,
        "prev_ema21_m5": 100.0,
        "rsi6_m5": 60.0,
        "atr5_m1": 0.1,
        "atr5_m5": 0.2,
        "intraday_bias": "bullish",
        "multi_timeframe_bias": "bullish",
    }

    result = detect_scalp_signal(item)

    assert result["scalp_ready"] is False
    assert result["scalp_signal_type"] == ""


def test_ema_crossover_releases_when_previous_side_crossed():
    item = {
        "latest_price": 100.0,
        "ema9_m5": 100.05,
        "ema21_m5": 100.0,
        "prev_ema9_m5": 99.95,
        "prev_ema21_m5": 100.0,
        "rsi6_m5": 60.0,
        "atr5_m1": 0.1,
        "atr5_m5": 0.2,
        "intraday_bias": "bullish",
        "multi_timeframe_bias": "bullish",
    }

    result = detect_scalp_signal(item)

    assert result["scalp_ready"] is True
    assert result["scalp_signal_type"] == "ema_crossover"
    assert result["scalp_direction"] == "long"


def test_liquidity_grab_requires_real_m5_spike_evidence():
    item = {
        "latest_price": 99.8,
        "key_level_high": 100.0,
        "key_level_low": 95.0,
        "rsi6_m5": 70.0,
        "atr5_m1": 0.2,
        "atr5_m5": 1.0,
        "intraday_bias": "bearish",
        "m5_last_high": 100.2,
        "m5_last_low": 99.5,
        "m5_prev_high": 99.9,
        "m5_prev_low": 99.4,
    }

    result = detect_scalp_signal(item)

    assert result["scalp_ready"] is False
    assert result["scalp_signal_type"] == ""


def test_liquidity_grab_releases_after_spike_and_pullback():
    item = {
        "latest_price": 99.8,
        "key_level_high": 100.0,
        "key_level_low": 95.0,
        "rsi6_m5": 70.0,
        "atr5_m1": 0.2,
        "atr5_m5": 1.0,
        "intraday_bias": "bearish",
        "m5_last_high": 100.7,
        "m5_last_low": 99.5,
        "m5_prev_high": 99.9,
        "m5_prev_low": 99.4,
    }

    result = detect_scalp_signal(item)

    assert result["scalp_ready"] is True
    assert result["scalp_signal_type"] == "liquidity_grab"
    assert result["scalp_direction"] == "short"
