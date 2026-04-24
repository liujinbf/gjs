import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from trade_opportunity import apply_trade_opportunity_scores, score_trade_opportunity


def _strong_long_item(**overrides):
    item = {
        "symbol": "XAUUSD",
        "has_live_quote": True,
        "tone": "success",
        "trade_grade": "可轻仓试仓",
        "trade_grade_source": "structure",
        "signal_side": "long",
        "intraday_bias": "bullish",
        "multi_timeframe_alignment": "aligned",
        "multi_timeframe_bias": "bullish",
        "breakout_state": "confirmed_above",
        "retest_state": "confirmed_support",
        "risk_reward_ready": True,
        "risk_reward_ratio": 2.1,
        "risk_reward_direction": "bullish",
        "risk_reward_entry_zone_text": "观察进场区间 4800.00 - 4810.00。",
        "risk_reward_stop_price": 4788.0,
        "risk_reward_target_price": 4845.0,
        "risk_reward_target_price_2": 4868.0,
        "h4_context_text": "H4 偏多，回踩仍守住支撑。",
    }
    item.update(overrides)
    return item


def test_score_trade_opportunity_marks_strong_short_term_long_as_push():
    result = score_trade_opportunity(_strong_long_item())

    assert result["opportunity_action"] == "long"
    assert result["opportunity_push_level"] == "push"
    assert result["opportunity_is_actionable"] is True
    assert result["opportunity_score"] >= 80
    assert result["opportunity_stop_price"] == 4788.0
    assert result["opportunity_target_price"] == 4845.0
    assert result["opportunity_entry_zone_text"]
    assert len(result["opportunity_reasons"]) <= 3


def test_score_trade_opportunity_downgrades_high_impact_event():
    result = score_trade_opportunity(
        _strong_long_item(
            event_applies=True,
            event_importance_text="高影响",
            event_mode_text="事件前高敏",
            trade_grade="当前不宜出手",
            trade_grade_source="event",
        )
    )

    assert result["opportunity_action"] == "watch"
    assert result["opportunity_push_level"] == "record"
    assert result["opportunity_score"] <= 45
    assert "高影响事件窗口内" in result["opportunity_reasons"][0]


def test_score_trade_opportunity_records_when_quote_is_not_live():
    result = score_trade_opportunity(_strong_long_item(has_live_quote=False))

    assert result["opportunity_action"] == "watch"
    assert result["opportunity_push_level"] == "record"
    assert result["opportunity_score"] <= 20
    assert "暂无实时报价" in result["opportunity_reasons"][0]


def test_score_trade_opportunity_can_pick_long_term_when_h4_and_multi_timeframe_align():
    result = score_trade_opportunity(
        _strong_long_item(
            intraday_bias="sideways",
            breakout_state="none",
            retest_state="none",
            risk_reward_ratio=1.6,
            h4_context_text="H4 仍偏多，上行趋势保持，回踩支撑有效。",
        )
    )

    assert result["opportunity_action"] == "long"
    assert result["opportunity_timeframe"] == "long_term"
    assert result["opportunity_long_term_score"] > result["opportunity_short_term_score"]
    assert result["opportunity_push_level"] in {"display", "push"}


def test_apply_trade_opportunity_scores_keeps_original_fields():
    items = [{"symbol": "XAUUSD", "custom": "keep", **_strong_long_item()}]

    scored = apply_trade_opportunity_scores(items)

    assert scored[0]["custom"] == "keep"
    assert scored[0]["opportunity_action"] == "long"
