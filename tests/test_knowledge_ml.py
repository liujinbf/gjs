import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from execution_audit import record_execution_audit
from knowledge_ml import (
    annotate_snapshot_with_model,
    apply_model_probability_context,
    train_execution_model,
    train_probability_model,
)
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot
from quote_models import SnapshotItem


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
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.1,
                "risk_reward_direction": "bullish",
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

    import sqlite3

    with sqlite3.connect(str(db_path)) as conn:
        rr_bucket_count = conn.execute(
            "SELECT COUNT(*) FROM ml_feature_stats WHERE feature_name = 'rr_ratio_bucket'"
        ).fetchone()[0]
    assert rr_bucket_count > 0

    annotated = annotate_snapshot_with_model(
        _build_snapshot("2026-04-13 17:00:00", 101.80),
        db_path=db_path,
        horizon_min=30,
    )
    item = annotated["items"][0]
    assert item["model_ready"] is True
    assert 0.05 <= item["model_win_probability"] <= 0.95
    assert "本地模型参考胜率" in item["model_note"]


def test_train_execution_model_and_annotate_snapshot(tmp_path):
    db_path = tmp_path / "knowledge.db"

    for idx, price in enumerate([100.00, 100.15, 100.30, 100.45, 100.60, 100.72, 100.88, 101.02, 101.18, 101.30, 101.42, 101.60]):
        snapshot = _build_snapshot(f"2026-04-13 {10 + idx // 6:02d}:{(idx % 6) * 10:02d}:00", price)
        recorded = record_snapshot(snapshot, db_path=db_path)
        symbol = "XAUUSD"
        binding = int(recorded["snapshot_bindings"][symbol])
        status = "opened" if idx < 8 else "blocked"
        result_message = "成功开仓 0.10 手 XAUUSD" if status == "opened" else "价格尚未回到可执行观察区间附近"
        record_execution_audit(
            source_kind="rule_engine",
            decision_status=status,
            snapshot=snapshot,
            snapshot_id=binding,
            meta={"symbol": symbol, "action": "long", "price": price, "sl": price - 2.0, "tp": price + 4.0},
            result_message=result_message,
            db_path=db_path,
        )

    result = train_execution_model(db_path=db_path, horizon_min=888, min_train_samples=4)
    assert result["status"] == "trained"
    assert result["sample_count"] >= 8

    annotated = annotate_snapshot_with_model(
        _build_snapshot("2026-04-13 17:00:00", 101.80),
        db_path=db_path,
        horizon_min=30,
    )
    item = annotated["items"][0]
    assert item["execution_model_ready"] is True
    assert 0.05 <= item["execution_open_probability"] <= 0.95
    assert "本地执行模型参考就绪度" in item["execution_model_note"]
    assert "平均就绪度" in annotated["execution_probability_summary_text"]


def test_apply_model_probability_context_downgrades_low_probability_setup():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "trade_grade_source": "structure",
                "alert_state_text": "结构候选",
                "alert_state_rank": 2,
                "model_ready": True,
                "model_win_probability": 0.42,
                "execution_note": "可轻仓试仓：结构相对干净，可作为候选机会。",
            }
        ],
    }
    result = apply_model_probability_context(snapshot)
    item = result["items"][0]
    assert item["trade_grade"] == "只适合观察"
    assert item["trade_grade_source"] == "model"
    assert "本地模型参考胜率仅约 42%" in item["trade_grade_detail"]
    assert item["execution_note"].startswith("只适合观察：")
    assert "可轻仓试仓：" not in item["execution_note"]
    assert result["trade_grade"] == "只适合观察"
    assert "模型参考：" in result["summary_text"]


def test_apply_model_probability_context_keeps_high_probability_setup_as_candidate():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "trade_grade_source": "structure",
                "model_ready": True,
                "model_win_probability": 0.74,
            }
        ],
    }
    result = apply_model_probability_context(snapshot)
    item = result["items"][0]
    assert item["trade_grade"] == "可轻仓试仓"
    assert "本地模型参考胜率约 74%" in item["trade_grade_detail"]
    assert result["trade_grade"] == "可轻仓试仓"


def test_apply_model_probability_context_appends_execution_readiness_note():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "trade_grade_source": "structure",
                "model_ready": True,
                "model_win_probability": 0.74,
                "execution_model_ready": True,
                "execution_open_probability": 0.61,
            }
        ],
    }
    result = apply_model_probability_context(snapshot)
    item = result["items"][0]
    assert "执行模型参考就绪度约 61%" in item["trade_grade_detail"]
    assert "执行就绪度约 61%" in result["summary_text"]


def test_apply_model_probability_context_keeps_candidate_when_probability_is_low_but_near_model_baseline():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可作为候选机会。",
        "items": [
            {
                "symbol": "XAUUSD",
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "结构相对干净，可作为候选机会。",
                "trade_next_review": "10 分钟后复核。",
                "trade_grade_source": "structure",
                "alert_state_text": "结构候选",
                "alert_state_rank": 2,
                "model_ready": True,
                "model_win_probability": 0.27,
                "model_base_win_probability": 0.29,
            }
        ],
    }
    result = apply_model_probability_context(snapshot)
    item = result["items"][0]
    assert item["trade_grade"] == "可轻仓试仓"
    assert item["trade_grade_source"] == "structure"
    assert "不单独否决规则候选" in item["trade_grade_detail"]


def test_annotate_snapshot_with_model_accepts_snapshot_item_objects(tmp_path):
    db_path = tmp_path / "knowledge.db"

    for idx, price in enumerate([100.00, 100.15, 100.30, 100.45, 100.60, 100.72, 100.88, 101.02, 101.18, 101.30, 101.42, 101.60]):
        record_snapshot(_build_snapshot(f"2026-04-13 10:{idx * 5:02d}:00", price), db_path=db_path)
    for idx, price in enumerate([100.00, 99.92, 99.85, 99.70, 99.60, 99.52, 99.45, 99.38, 99.30, 99.22]):
        snap = _build_snapshot(f"2026-04-13 {14 + idx // 6:02d}:{(idx % 6) * 10:02d}:00", price)
        snap["regime_tag"] = "low_volatility_range"
        snap["regime_text"] = "低波震荡"
        snap["items"][0]["regime_tag"] = "low_volatility_range"
        snap["items"][0]["regime_text"] = "低波震荡"
        snap["items"][0]["trade_grade"] = "只适合观察"
        record_snapshot(snap, db_path=db_path)

    backfill_snapshot_outcomes(db_path=db_path, now=datetime(2026, 4, 13, 16, 30, 0), horizons_min=(30,))
    train_probability_model(db_path=db_path, horizon_min=30, min_train_samples=4)

    snapshot = {
        "last_refresh_text": "2026-04-13 12:30:00",
        "event_risk_mode_text": "正常观察",
        "regime_tag": "trend_expansion",
        "regime_text": "趋势扩张",
        "items": [
            SnapshotItem(
                symbol="XAUUSD",
                latest_price=101.40,
                spread_points=18,
                has_live_quote=True,
                trade_grade="可轻仓试仓",
                trade_grade_source="structure",
                quote_status_code="live",
                extra={
                    "tone": "success",
                    "alert_state_text": "结构候选",
                    "event_importance_text": "",
                    "intraday_bias_text": "偏多",
                    "multi_timeframe_bias_text": "偏多",
                    "breakout_state_text": "上破已确认",
                    "retest_state_text": "回踩已确认",
                    "risk_reward_state_text": "盈亏比优秀",
                    "regime_tag": "trend_expansion",
                    "atr14": 8.0,
                    "atr14_h4": 20.0,
                    "signal_side": "long",
                },
            )
        ],
    }

    annotated = annotate_snapshot_with_model(snapshot, db_path=db_path, horizon_min=30)
    item = annotated["items"][0]

    assert item["model_ready"] is True
    assert 0.05 <= item["model_win_probability"] <= 0.95


def test_apply_model_probability_context_accepts_snapshot_item_objects():
    snapshot = {
        "status_tone": "success",
        "event_risk_mode": "normal",
        "summary_text": "出手分级：可轻仓试仓。结构相对干净，可作为候选机会。",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可作为候选机会。",
        "items": [
            SnapshotItem(
                symbol="XAUUSD",
                trade_grade="可轻仓试仓",
                trade_grade_detail="结构相对干净，可作为候选机会。",
                trade_next_review="10 分钟后复核。",
                trade_grade_source="structure",
                extra={
                    "model_ready": True,
                    "model_win_probability": 0.42,
                    "alert_state_text": "结构候选",
                    "alert_state_rank": 2,
                },
            )
        ],
    }

    result = apply_model_probability_context(snapshot)
    item = result["items"][0]

    assert item["trade_grade"] == "只适合观察"
    assert item["trade_grade_source"] == "model"
