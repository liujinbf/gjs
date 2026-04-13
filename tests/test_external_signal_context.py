import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from external_signal_context import apply_external_signal_context


def test_apply_external_signal_context_downgrades_conflicting_event_result():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "execution_note": "可轻仓试仓：结构相对干净。",
                "signal_side": "long",
                "alert_state_text": "结构候选",
                "alert_state_detail": "当前执行面相对干净。",
                "alert_state_tone": "success",
                "alert_state_rank": 2,
            }
        ],
        "event_feed_items": [
            {
                "name": "美国 CPI",
                "importance": "high",
                "symbols": ["XAUUSD"],
                "has_result": True,
                "result_bias": "bearish",
                "result_summary_text": "美国 CPI：实际 3.4%，预期 3.2%，前值 3.1%，结果解读 偏空",
            }
        ],
        "macro_data_items": [],
    }

    result = apply_external_signal_context(snapshot)
    item = result["items"][0]
    assert item["trade_grade"] == "只适合观察"
    assert item["trade_grade_source"] == "macro"
    assert "美国 CPI" in item["trade_grade_detail"]
    assert "事件结果" in item["execution_note"]
    assert item["alert_state_text"] == "宏观结果冲突"
    assert result["trade_grade"] == "只适合观察"


def test_apply_external_signal_context_keeps_candidate_when_macro_aligns():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "execution_note": "可轻仓试仓：结构相对干净。",
                "signal_side": "long",
            }
        ],
        "event_feed_items": [],
        "macro_data_items": [
            {
                "name": "美国10年期实际利率（黄金/白银）",
                "importance": "high",
                "symbols": ["XAUUSD"],
                "direction": "bullish",
                "value_text": "1.85",
                "delta_text": "较前值 -0.06",
            }
        ],
    }

    result = apply_external_signal_context(snapshot)
    item = result["items"][0]
    assert item["trade_grade"] == "可轻仓试仓"
    assert "宏观数据" in item["execution_note"]
    assert "外部背景与当前结构同向" in result["summary_text"]
