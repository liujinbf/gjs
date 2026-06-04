import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategic_gold_plan import build_strategic_gold_plan_entries


def test_build_strategic_gold_plan_entries_near_first_level():
    entries = build_strategic_gold_plan_entries(
        {
            "last_refresh_text": "2026-06-02 18:30:00",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "latest_price": 4392.0,
                }
            ],
        },
        SimpleNamespace(
            strategic_gold_plan_enabled=True,
            strategic_gold_plan_symbol="XAUUSD",
            strategic_gold_plan_levels=[4400.0, 4250.0, 4100.0],
            strategic_gold_plan_band=15.0,
        ),
    )

    assert len(entries) == 1
    assert entries[0]["category"] == "strategic_plan"
    assert entries[0]["strategic_plan"] is True
    assert entries[0]["strategic_plan_level"] == 4400.0
    assert entries[0]["trade_grade_source"] == "strategic_plan"
    assert "不进入短线自动开仓" in entries[0]["detail"]


def test_build_strategic_gold_plan_entries_ignores_far_price():
    entries = build_strategic_gold_plan_entries(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "latest_price": 4550.0,
                }
            ],
        },
        SimpleNamespace(
            strategic_gold_plan_enabled=True,
            strategic_gold_plan_symbol="XAUUSD",
            strategic_gold_plan_levels=[4400.0, 4250.0],
            strategic_gold_plan_band=15.0,
        ),
    )

    assert entries == []
