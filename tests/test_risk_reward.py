import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from risk_reward import analyze_risk_reward


def test_analyze_risk_reward_prefers_atr_for_confirmed_breakout():
    payload = analyze_risk_reward(
        {
            "latest_price": 4805.0,
            "point": 0.01,
            "atr14": 10.0,
            "key_level_high": 4800.0,
            "key_level_low": 4700.0,
            "key_level_state": "breakout_above",
            "breakout_state": "confirmed_above",
            "breakout_direction": "bullish",
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_bias": "bullish",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "atr"
    assert payload["risk_reward_atr"] == 10.0
    assert payload["risk_reward_stop_price"] == 4785.0
    assert payload["risk_reward_target_price"] == 4835.0
    assert payload["risk_reward_target_price_2"] == 4850.0
    assert payload["risk_reward_ratio"] == 1.5
    assert "ATR(14)" in payload["risk_reward_context_text"]
    assert "4785.00" in payload["risk_reward_invalidation_text"]


def test_analyze_risk_reward_falls_back_to_range_when_atr_missing():
    payload = analyze_risk_reward(
        {
            "latest_price": 4805.0,
            "point": 0.01,
            "key_level_high": 4800.0,
            "key_level_low": 4700.0,
            "key_level_state": "breakout_above",
            "breakout_state": "confirmed_above",
            "breakout_direction": "bullish",
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_bias": "bullish",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "range"
    assert payload["risk_reward_atr"] == 0.0
    assert "ATR(14)" not in payload["risk_reward_context_text"]
    assert "近12小时关键区间" in payload["risk_reward_context_text"]


def test_analyze_risk_reward_rejects_near_zero_risk():
    payload = analyze_risk_reward(
        {
            "latest_price": 4800.0,
            "point": 0.01,
            "atr14": 0.000001,
            "key_level_high": 4800.0,
            "key_level_low": 4700.0,
            "key_level_state": "breakout_above",
            "breakout_state": "confirmed_above",
            "breakout_direction": "bullish",
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_bias": "bullish",
        }
    )

    assert payload["risk_reward_ready"] is False
    assert payload["risk_reward_ratio"] == 0.0


def test_analyze_risk_reward_supports_bearish_breakout_short_plan():
    payload = analyze_risk_reward(
        {
            "latest_price": 4795.0,
            "point": 0.01,
            "atr14": 10.0,
            "key_level_high": 4900.0,
            "key_level_low": 4800.0,
            "key_level_state": "breakout_below",
            "breakout_state": "confirmed_below",
            "breakout_direction": "bearish",
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_bias": "bearish",
            "intraday_bias": "bearish",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_direction"] == "bearish"
    assert payload["risk_reward_stop_price"] == 4815.0
    assert payload["risk_reward_target_price"] == 4765.0
    assert payload["risk_reward_target_price_2"] == 4750.0
    assert payload["risk_reward_target_price"] < 4795.0 < payload["risk_reward_stop_price"]
    assert payload["risk_reward_entry_zone_low"] == 4792.0
    assert payload["risk_reward_entry_zone_high"] == 4800.0
    assert "空头" in payload["risk_reward_context_text"]
    assert "4815.00 上方" in payload["risk_reward_invalidation_text"]


def test_analyze_risk_reward_uses_atr_fallback_when_key_levels_missing():
    payload = analyze_risk_reward(
        {
            "latest_price": 4805.0,
            "point": 0.01,
            "atr14": 10.0,
            "multi_timeframe_alignment": "partial",
            "multi_timeframe_bias": "bullish",
            "intraday_bias": "sideways",
            "breakout_state": "none",
            "retest_state": "none",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "atr_fallback"
    assert payload["risk_reward_direction"] == "bullish"
    assert payload["risk_reward_ratio"] == 2.0
    assert payload["risk_reward_stop_price"] == 4793.0
    assert payload["risk_reward_target_price"] == 4829.0
    assert "关键位不足时按 ATR" in payload["risk_reward_context_text"]


def test_analyze_risk_reward_uses_breakout_direction_when_states_not_confirmed():
    payload = analyze_risk_reward(
        {
            "latest_price": 4805.0,
            "point": 0.01,
            "atr14": 10.0,
            "breakout_direction": "bullish",
            "breakout_state": "pending_above",
            "multi_timeframe_alignment": "unknown",
            "multi_timeframe_bias": "unknown",
            "intraday_bias": "sideways",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "atr_fallback"
    assert payload["risk_reward_direction"] == "bullish"


def test_analyze_risk_reward_uses_signal_side_when_other_direction_fields_missing():
    payload = analyze_risk_reward(
        {
            "latest_price": 4795.0,
            "point": 0.01,
            "atr14": 10.0,
            "signal_side": "short",
            "breakout_state": "none",
            "multi_timeframe_alignment": "unknown",
            "multi_timeframe_bias": "unknown",
            "intraday_bias": "sideways",
        }
    )

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "atr_fallback"
    assert payload["risk_reward_direction"] == "bearish"
