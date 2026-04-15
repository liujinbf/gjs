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
