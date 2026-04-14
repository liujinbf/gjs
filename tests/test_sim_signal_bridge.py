import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sim_signal_bridge import build_rule_sim_signal


def test_build_rule_sim_signal_picks_actionable_structure_candidate():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4759.82,
                    "bid": 4759.74,
                    "ask": 4759.91,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                    "risk_reward_atr": 18.0,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "long"
    assert signal["price"] == 4759.91
    assert signal["sl"] == 4748.0
    assert signal["tp"] == 4788.0
    assert signal["atr14"] == 18.0
    assert signal["risk_reward_atr"] == 18.0


def test_build_rule_sim_signal_skips_candidate_outside_entry_zone():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.4,
                    "latest_price": 4780.0,
                    "bid": 4779.9,
                    "ask": 4780.1,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4810.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None
