import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pair_arbitrage import build_au_ag_pair_signals


def test_build_au_ag_pair_signals_builds_two_legs_from_zscore_context():
    signals, reason = build_au_ag_pair_signals(
        {
            "correlation_context": {
                "au_ag_ready": True,
                "au_ag_zscore_ready": True,
                "au_ag_signal": "long_xag_short_xau",
                "au_ag_ratio": 86.08,
                "au_ag_zscore": 2.4,
                "au_ag_ratio_momentum": 0.0,
                "au_ag_exit_zscore": 0.35,
                "au_ag_pair_legs": [
                    {"symbol": "XAUUSD", "action": "short", "role": "rich_leg"},
                    {"symbol": "XAGUSD", "action": "long", "role": "cheap_leg"},
                ],
            },
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "latest_price": 4476.0,
                    "bid": 4475.9,
                    "ask": 4476.1,
                    "atr14": 8.0,
                    "spread_points": 20.0,
                    "point": 0.01,
                    "volume_step": 0.01,
                    "volume_min": 0.01,
                },
                {
                    "symbol": "XAGUSD",
                    "has_live_quote": True,
                    "latest_price": 52.0,
                    "bid": 51.99,
                    "ask": 52.01,
                    "atr14": 0.35,
                    "spread_points": 40.0,
                    "point": 0.001,
                    "volume_step": 0.01,
                    "volume_min": 0.01,
                },
            ],
        }
    )

    assert reason == ""
    assert [item["symbol"] for item in signals] == ["XAUUSD", "XAGUSD"]
    assert [item["action"] for item in signals] == ["short", "long"]
    assert signals[0]["sl"] > signals[0]["price"] > signals[0]["tp"]
    assert signals[1]["sl"] < signals[1]["price"] < signals[1]["tp"]
    assert signals[0]["pair_group_id"] == signals[1]["pair_group_id"]
    assert signals[0]["strategy_family"] == "au_ag_pair"
    assert signals[0]["fixed_lots"] > 0
    assert signals[1]["fixed_lots"] > 0
    assert signals[0]["fixed_lots"] <= 0.01
    assert signals[1]["fixed_lots"] <= 0.02
    notional_gap = abs(signals[0]["pair_leg_notional"] - signals[1]["pair_leg_notional"])
    assert notional_gap / max(signals[0]["pair_target_notional"], 1.0) < 0.20


def test_build_au_ag_pair_signals_waits_for_zscore_history():
    signals, reason = build_au_ag_pair_signals(
        {
            "correlation_context": {
                "au_ag_ready": True,
                "au_ag_zscore_ready": False,
                "au_ag_pair_legs": [
                    {"symbol": "XAUUSD", "action": "short"},
                    {"symbol": "XAGUSD", "action": "long"},
                ],
            },
            "items": [],
        }
    )

    assert signals == []
    assert "历史样本不足" in reason


def test_build_au_ag_pair_signals_blocks_neutral_zscore_even_when_ratio_extreme():
    signals, reason = build_au_ag_pair_signals(
        {
            "correlation_context": {
                "au_ag_ready": True,
                "au_ag_zscore_ready": True,
                "au_ag_signal": "long_xau_short_xag",
                "au_ag_ratio": 59.33,
                "au_ag_zscore": -0.06,
                "au_ag_pair_legs": [
                    {"symbol": "XAUUSD", "action": "long"},
                    {"symbol": "XAGUSD", "action": "short"},
                ],
            },
            "items": [
                {"symbol": "XAUUSD", "has_live_quote": True, "latest_price": 4504.0, "bid": 4503.9, "ask": 4504.1},
                {"symbol": "XAGUSD", "has_live_quote": True, "latest_price": 75.9, "bid": 75.89, "ask": 75.91},
            ],
        }
    )

    assert signals == []
    assert "未达到入场阈值" in reason


def test_build_au_ag_pair_signals_waits_when_high_ratio_still_expanding():
    signals, reason = build_au_ag_pair_signals(
        {
            "correlation_context": {
                "au_ag_ready": True,
                "au_ag_zscore_ready": True,
                "au_ag_signal": "long_xag_short_xau",
                "au_ag_ratio": 86.08,
                "au_ag_zscore": 2.4,
                "au_ag_ratio_momentum": 0.04,
                "au_ag_pair_legs": [
                    {"symbol": "XAUUSD", "action": "short"},
                    {"symbol": "XAGUSD", "action": "long"},
                ],
            },
            "items": [
                {"symbol": "XAUUSD", "has_live_quote": True, "latest_price": 4476.0, "bid": 4475.9, "ask": 4476.1},
                {"symbol": "XAGUSD", "has_live_quote": True, "latest_price": 52.0, "bid": 51.99, "ask": 52.01},
            ],
        }
    )

    assert signals == []
    assert "仍在扩大" in reason


def test_build_au_ag_pair_signals_blocks_wide_xag_spread():
    signals, reason = build_au_ag_pair_signals(
        {
            "correlation_context": {
                "au_ag_ready": True,
                "au_ag_zscore_ready": True,
                "au_ag_signal": "long_xag_short_xau",
                "au_ag_ratio": 86.08,
                "au_ag_zscore": 2.4,
                "au_ag_pair_legs": [
                    {"symbol": "XAUUSD", "action": "short"},
                    {"symbol": "XAGUSD", "action": "long"},
                ],
            },
            "items": [
                {"symbol": "XAUUSD", "has_live_quote": True, "latest_price": 4476.0, "bid": 4475.9, "ask": 4476.1},
                {
                    "symbol": "XAGUSD",
                    "has_live_quote": True,
                    "latest_price": 52.0,
                    "bid": 51.99,
                    "ask": 52.01,
                    "spread_points": 180.0,
                    "point": 0.001,
                },
            ],
        }
    )

    assert signals == []
    assert "点差" in reason
