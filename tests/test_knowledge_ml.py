import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_ml import annotate_snapshot_with_model, train_probability_model
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot


def _build_snapshot(snapshot_time: str, price: float, trade_grade: str = "可轻仓试仓") -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "event_risk_mode_text": "正常观察",
        "regime_tag": "trend_expansion",
        "regime_text": "趋势扩张",
        "summary_text": "测试快照",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": price,
                "spread_points": 18,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": trade_grade,
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构干净，等待延续。",
                "trade_next_review": "15 分钟后复核。",
                "alert_state_text": "结构候选",
                "event_importance_text": "",
                "event_note": "",
                "intraday_bias": "bullish",
                "intraday_bias_text": "偏多",
                "multi_timeframe_bias": "bullish",
                "multi_timeframe_bias_text": "偏多",
                "breakout_direction": "bullish",
                "breakout_state_text": "上破已确认",
                "retest_state_text": "回踩已确认",
                "risk_reward_state_text": "盈亏比优秀",
                "status_text": "实时报价",
                "quote_text": "Bid 100.00 / Ask 100.18",
                "execution_note": "测试执行建议",
                "regime_tag": "trend_expansion",
                "regime_text": "趋势扩张",
                "regime_reason": "多周期同向偏多。",
                "atr14": 8.0,
                "atr14_h4": 20.0,
                "signal_side": "long",
            }
        ],
    }


def test_train_probability_model_and_annotate_snapshot(tmp_path):
    db_path = tmp_path / "knowledge.db"

    for idx, price in enumerate([100.00, 100.15, 100.30, 100.45, 100.60, 100.72, 100.88, 101.02, 101.18, 101.30, 101.42, 101.60]):
        record_snapshot(_build_snapshot(f"2026-04-13 {10 + idx // 6:02d}:{(idx % 6) * 10:02d}:00", price), db_path=db_path)
    for idx, price in enumerate([100.00, 99.92, 99.85, 99.70, 99.60, 99.52, 99.45, 99.38, 99.30, 99.22]):
        snap = _build_snapshot(f"2026-04-13 {14 + idx // 6:02d}:{(idx % 6) * 10:02d}:00", price)
        snap["regime_tag"] = "low_volatility_range"
        snap["regime_text"] = "低波震荡"
        snap["items"][0]["regime_tag"] = "low_volatility_range"
        snap["items"][0]["regime_text"] = "低波震荡"
        snap["items"][0]["trade_grade"] = "只适合观察"
        record_snapshot(snap, db_path=db_path)

    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 16, 30, 0), horizons_min=(30,))
    result = train_probability_model(db_path=db_path, horizon_min=30, min_train_samples=4)
    assert result["status"] == "trained"
    assert result["sample_count"] >= 8

    annotated = annotate_snapshot_with_model(
        _build_snapshot("2026-04-13 17:00:00", 101.80),
        db_path=db_path,
        horizon_min=30,
    )
    item = annotated["items"][0]
    assert item["model_ready"] is True
    assert 0.05 <= item["model_win_probability"] <= 0.95
    assert "本地模型参考胜率" in item["model_note"]
