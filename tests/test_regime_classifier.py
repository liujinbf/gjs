import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from regime_classifier import build_snapshot_regime_summary, classify_market_regime


def test_classify_event_driven_regime():
    regime = classify_market_regime(
        "XAUUSD",
        {
            "has_live_quote": True,
            "latest_price": 4800.0,
            "spread_points": 20,
            "atr14": 12.0,
            "intraday_volatility": "normal",
        },
        "success",
        {
            "event_applies": True,
            "event_active_name": "美国 CPI",
            "event_importance_text": "高影响",
        },
    )
    assert regime["regime_tag"] == "event_driven"
    assert "事件" in regime["regime_text"]


def test_classify_trend_expansion_regime():
    regime = classify_market_regime(
        "XAUUSD",
        {
            "has_live_quote": True,
            "latest_price": 4800.0,
            "spread_points": 18,
            "atr14": 14.0,
            "intraday_volatility": "normal",
            "intraday_bias": "bullish",
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_bias": "bullish",
            "breakout_state": "confirmed_above",
            "retest_state": "confirmed_support",
        },
        "success",
        {},
    )
    assert regime["regime_tag"] == "trend_expansion"


def test_build_snapshot_regime_summary_prioritizes_event_risk():
    summary = build_snapshot_regime_summary(
        [
            {"symbol": "XAUUSD", "regime_tag": "low_volatility_range", "regime_text": "低波震荡", "regime_reason": "偏静。"},
            {"symbol": "EURUSD", "regime_tag": "event_driven", "regime_text": "事件驱动", "regime_reason": "数据窗口。"},
        ]
    )
    assert summary["regime_tag"] == "event_driven"
    assert "事件驱动" in summary["regime_summary_text"]
