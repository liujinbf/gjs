import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tick_shock_detector import reset_tick_shock_state, update_tick_shock_state


def test_update_tick_shock_state_detects_fast_xau_move():
    reset_tick_shock_state()

    first = update_tick_shock_state(
        "XAUUSD",
        bid=4476.00,
        ask=4476.10,
        now_ts=1000.0,
        window_sec=5.0,
        cooldown_sec=60.0,
        thresholds={"XAU": 0.50},
    )
    second = update_tick_shock_state(
        "XAUUSD",
        bid=4476.70,
        ask=4476.80,
        now_ts=1000.5,
        window_sec=5.0,
        cooldown_sec=60.0,
        thresholds={"XAU": 0.50},
    )

    assert first["tick_shock_active"] is False
    assert second["tick_shock_active"] is True
    assert second["tick_shock_direction"] == "bullish"
    assert second["tick_shock_move"] >= 0.50
    assert "Tick 异动冷却" in second["tick_shock_text"]


def test_update_tick_shock_state_keeps_cooldown_after_initial_trigger():
    reset_tick_shock_state()
    update_tick_shock_state("XAGUSD", bid=76.00, ask=76.02, now_ts=2000.0, thresholds={"XAG": 0.08})
    update_tick_shock_state("XAGUSD", bid=75.88, ask=75.90, now_ts=2001.0, thresholds={"XAG": 0.08})

    cooled = update_tick_shock_state("XAGUSD", bid=75.89, ask=75.91, now_ts=2030.0, thresholds={"XAG": 0.08})

    assert cooled["tick_shock_active"] is True
    assert cooled["tick_shock_direction"] == "bearish"
