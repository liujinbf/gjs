import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from technical_indicators import build_technical_indicators, calc_atr


def _build_rates(count: int, start_close: float = 100.0, step: float = 1.0) -> list[dict]:
    rows = []
    close = start_close
    for index in range(count):
        rows.append(
            {
                "time": index,
                "open": close - 1.0,
                "high": close + 5.0,
                "low": close - 5.0,
                "close": close,
            }
        )
        close += step
    return rows


def test_calc_atr_returns_wilder_average():
    rates = _build_rates(15, start_close=100.0, step=1.0)
    atr = calc_atr(rates, period=14)
    assert atr == 10.0


def test_build_technical_indicators_includes_atr_fields():
    indicators = build_technical_indicators(
        {
            "m5": _build_rates(300, start_close=100.0, step=0.2),
            "h1": _build_rates(60, start_close=100.0, step=1.0),
            "h4": _build_rates(120, start_close=200.0, step=2.0),
        }
    )

    assert indicators["atr14"] == 10.0
    assert indicators["atr14_h4"] == 10.0
    assert "ATR(14)=10.0000" in indicators["tech_summary"]
    assert "H4 ATR(14)=10.0000" in indicators["tech_summary_h4"]
