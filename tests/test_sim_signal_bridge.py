import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from quote_models import SnapshotItem
from sim_signal_bridge import build_rule_sim_signal, build_rule_sim_signal_decision


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
                    "risk_reward_target_price_2": 4810.0,
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
    assert signal["tp2"] == 4810.0
    assert signal["atr14"] == 18.0
    assert signal["risk_reward_atr"] == 18.0
    assert signal["entry_zone_side"] == "middle"


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


def test_build_rule_sim_signal_accepts_mid_quality_setup_when_model_is_strong():
    signal = build_rule_sim_signal(
        {
            "items": [
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "",
                    "risk_reward_direction": "bullish",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.4,
                    "latest_price": 1.1720,
                    "bid": 1.1719,
                    "ask": 1.1721,
                    "risk_reward_stop_price": 1.1708,
                    "risk_reward_target_price": 1.1766,
                    "risk_reward_entry_zone_low": 1.1719,
                    "risk_reward_entry_zone_high": 1.1726,
                    "point": 0.0001,
                    "model_ready": True,
                    "model_win_probability": 0.72,
                }
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "EURUSD"
    assert signal["action"] == "long"
    assert signal["price"] == 1.1721
    assert signal["entry_zone_side"] == "lower"


def test_build_rule_sim_signal_decision_returns_block_reason():
    signal, reason = build_rule_sim_signal_decision(
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
    assert "继续等回踩" in reason


def test_build_rule_sim_signal_blocks_long_when_price_is_near_upper_side():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "long",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.2,
                    "latest_price": 4764.8,
                    "bid": 4764.7,
                    "ask": 4764.9,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_entry_zone_low": 4750.0,
                    "risk_reward_entry_zone_high": 4765.0,
                    "atr14": 18.0,
                }
            ]
        }
    )

    assert signal is None
    assert "上沿追价" in reason


def test_build_rule_sim_signal_blocks_short_when_price_is_near_lower_side():
    signal, reason = build_rule_sim_signal_decision(
        {
            "items": [
                {
                    "symbol": "EURUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "signal_side": "short",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.0,
                    "latest_price": 1.1711,
                    "bid": 1.1710,
                    "ask": 1.1712,
                    "risk_reward_stop_price": 1.1730,
                    "risk_reward_target_price": 1.1675,
                    "risk_reward_entry_zone_low": 1.1710,
                    "risk_reward_entry_zone_high": 1.1725,
                    "point": 0.0001,
                    "atr14": 0.0012,
                }
            ]
        }
    )

    assert signal is None
    assert "下沿追空" in reason


def test_build_rule_sim_signal_accepts_snapshot_item_objects():
    signal = build_rule_sim_signal(
        {
            "items": [
                SnapshotItem(
                    symbol="XAUUSD",
                    latest_price=4759.82,
                    bid=4759.74,
                    ask=4759.91,
                    spread_points=17.0,
                    has_live_quote=True,
                    trade_grade="可轻仓试仓",
                    trade_grade_source="structure",
                    quote_status_code="live",
                    signal_side="long",
                    extra={
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.4,
                        "risk_reward_stop_price": 4748.0,
                        "risk_reward_target_price": 4788.0,
                        "risk_reward_target_price_2": 4810.0,
                        "risk_reward_entry_zone_low": 4750.0,
                        "risk_reward_entry_zone_high": 4765.0,
                        "atr14": 18.0,
                        "risk_reward_atr": 18.0,
                    },
                )
            ]
        }
    )

    assert signal is not None
    assert signal["symbol"] == "XAUUSD"
    assert signal["action"] == "long"
