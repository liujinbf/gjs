import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from external_feed_models import MacroDataItem
from rule_engine_brief import generate_rule_engine_brief
from quote_models import SnapshotItem


def test_generate_rule_engine_brief_includes_model_probability(monkeypatch):
    monkeypatch.setattr("rule_engine_brief._get_rulebook_text", lambda snapshot=None: "优先等回踩确认。")
    snapshot = {
        "model_probability_summary_text": "本地模型平均参考胜率约 69%。",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "multi_timeframe_alignment": "bullish",
                "multi_timeframe_bias_text": "M15 与 H1 同向偏多",
                "m15_context_text": "偏多",
                "h1_context_text": "偏多",
                "tech_summary_h4": "H4 维持偏多节奏。",
                "risk_reward_ready": True,
                "risk_reward_ratio": 1.8,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "bollinger_upper": 4788.0,
                "bollinger_lower": 4748.0,
                "bollinger_mid": 4768.0,
                "rsi14": 57.0,
                "ma20": 4762.0,
                "ma50": 4751.0,
                "macd": 1.2,
                "macd_signal": 0.9,
                "macd_histogram": 0.3,
                "model_ready": True,
                "model_win_probability": 0.74,
                "model_confidence_text": "中等信心",
                "model_note": "本地模型参考胜率约 74%。主要依据：regime_tag=trend_expansion（样本 88，胜率 69%）。",
            }
        ],
    }

    result = generate_rule_engine_brief(snapshot)

    assert "• 概率：" in result["content"]
    assert "本地模型平均参考胜率约 69%" in result["content"]
    assert "当前结构参考胜率约 74%" in result["content"]
    assert "中等信心" in result["content"]


def test_generate_rule_engine_brief_accepts_snapshot_item_objects(monkeypatch):
    monkeypatch.setattr("rule_engine_brief._get_rulebook_text", lambda snapshot=None: "优先等回踩确认。")
    snapshot = {
        "model_probability_summary_text": "本地模型平均参考胜率约 69%。",
        "items": [
            SnapshotItem(
                symbol="XAUUSD",
                latest_price=4759.82,
                trade_grade="可轻仓试仓",
                trade_grade_detail="结构干净，等待延续。",
                quote_status_code="live",
                extra={
                    "multi_timeframe_alignment": "bullish",
                    "multi_timeframe_bias_text": "M15 与 H1 同向偏多",
                    "m15_context_text": "偏多",
                    "h1_context_text": "偏多",
                    "tech_summary_h4": "H4 维持偏多节奏。",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 1.8,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "bollinger_upper": 4788.0,
                    "bollinger_lower": 4748.0,
                    "bollinger_mid": 4768.0,
                    "rsi14": 57.0,
                    "ma20": 4762.0,
                    "ma50": 4751.0,
                    "macd": 1.2,
                    "macd_signal": 0.9,
                    "macd_histogram": 0.3,
                    "model_ready": True,
                    "model_win_probability": 0.74,
                    "model_confidence_text": "中等信心",
                    "model_note": "本地模型参考胜率约 74%。主要依据：regime_tag=trend_expansion（样本 88，胜率 69%）。",
                },
            )
        ],
    }

    result = generate_rule_engine_brief(snapshot)

    assert "XAUUSD" in result["content"]
    assert "当前结构参考胜率约 74%" in result["content"]


def test_generate_rule_engine_brief_accepts_macro_data_item_objects(monkeypatch):
    monkeypatch.setattr("rule_engine_brief._get_rulebook_text", lambda snapshot=None: "优先等回踩确认。")
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "multi_timeframe_alignment": "bullish",
                "multi_timeframe_bias_text": "M15 与 H1 同向偏多",
                "m15_context_text": "偏多",
                "h1_context_text": "偏多",
                "risk_reward_ready": True,
                "risk_reward_ratio": 1.8,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "bollinger_upper": 4788.0,
                "bollinger_lower": 4748.0,
                "bollinger_mid": 4768.0,
                "rsi14": 57.0,
                "ma20": 4762.0,
                "ma50": 4751.0,
                "macd": 1.2,
                "macd_signal": 0.9,
                "macd_histogram": 0.3,
            }
        ],
        "macro_data_items": [
            MacroDataItem(
                name="VIX 波动率指数",
                source="CBOE",
                value_text="18.4",
                direction="bearish",
            )
        ],
    }

    result = generate_rule_engine_brief(snapshot)

    assert "VIX 18.4" in result["content"]
