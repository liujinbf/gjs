import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from knowledge_base import open_knowledge_connection
from snapshot_feature_backfill import backfill_snapshot_features


def _insert_snapshot(db_path: Path, feature_payload: dict, snapshot_time: str = "2026-04-22 14:00:29") -> None:
    with open_knowledge_connection(db_path, ensure_schema=True) as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                snapshot_time, symbol, latest_price, spread_points, has_live_quote, tone,
                trade_grade, trade_grade_source, alert_state_text, event_risk_mode_text,
                event_active_name, event_importance_text, event_note, signal_side,
                regime_tag, regime_text, feature_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_time,
                "XAUUSD",
                3310.0,
                18.0,
                1,
                "neutral",
                "只适合观察",
                "event",
                "事件窗口观察",
                "事件落地观察",
                "CPI y/y",
                "高影响",
                "",
                "neutral",
                "event_driven",
                "事件驱动",
                json.dumps(feature_payload, ensure_ascii=False),
                snapshot_time,
            ),
        )


def test_backfill_snapshot_features_repairs_text_and_history_fields(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "intraday_bias_text": "偏多",
            "intraday_volatility_text": "波动放大",
            "intraday_location_text": "贴近区间高位",
            "multi_timeframe_alignment_text": "多周期同向",
            "multi_timeframe_bias_text": "偏多",
            "key_level_state_text": "上破关键位",
            "breakout_state_text": "上破已确认",
            "retest_state_text": "回踩已确认",
            "trade_grade_detail": "事件刚落地，先观察。",
        },
    )

    history_file.write_text(
        json.dumps(
            {
                "occurred_at": "2026-04-22 14:00:29",
                "symbol": "XAUUSD",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.0,
                "model_ready": True,
                "model_win_probability": 0.62,
                "signal_side": "long",
                "signal_side_text": "【↑ 多头参考】",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["updated_rows"] == 1
    assert report["text_field_updates"] >= 8
    assert report["history_field_updates"] >= 4

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute("SELECT feature_json FROM market_snapshots LIMIT 1").fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)

    assert payload["intraday_bias"] == "bullish"
    assert payload["intraday_volatility"] == "high"
    assert payload["intraday_location"] == "upper"
    assert payload["multi_timeframe_alignment"] == "aligned"
    assert payload["multi_timeframe_bias"] == "bullish"
    assert payload["key_level_state"] == "breakout_above"
    assert payload["breakout_state"] == "confirmed_above"
    assert payload["retest_state"] == "confirmed_support"
    assert payload["breakout_direction"] == "bullish"
    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_ratio"] == 2.0
    assert payload["model_ready"] is True
    assert payload["model_win_probability"] == 0.62
    assert payload["signal_side"] == "long"
    assert payload["risk_reward_direction"] == "bullish"


def test_backfill_snapshot_features_does_not_override_existing_values(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "intraday_bias": "bearish",
            "intraday_bias_text": "偏多",
            "risk_reward_ratio": 1.4,
            "model_win_probability": 0.31,
        },
        snapshot_time="2026-04-22 15:00:29",
    )

    history_file.write_text(
        json.dumps(
            {
                "occurred_at": "2026-04-22 15:00:29",
                "symbol": "XAUUSD",
                "risk_reward_ratio": 2.6,
                "model_win_probability": 0.71,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["updated_rows"] == 1

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute("SELECT feature_json FROM market_snapshots LIMIT 1").fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)

    assert payload["intraday_bias"] == "bearish"
    assert payload["risk_reward_ratio"] == 1.4
    assert payload["model_win_probability"] == 0.31
    assert payload["risk_reward_direction"] == "bearish"


def test_backfill_snapshot_features_recomputes_risk_reward_when_missing(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "atr14": 10.0,
            "intraday_bias_text": "震荡",
            "multi_timeframe_alignment_text": "多周期待确认",
            "multi_timeframe_bias_text": "偏多",
            "breakout_state_text": "暂无突破",
            "retest_state_text": "暂无回踩",
        },
        snapshot_time="2026-04-22 16:00:29",
    )
    history_file.write_text("", encoding="utf-8")

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["updated_rows"] == 1
    assert report["recomputed_risk_fields"] >= 6

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute("SELECT feature_json FROM market_snapshots LIMIT 1").fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)

    assert payload["risk_reward_ready"] is True
    assert payload["risk_reward_basis"] == "atr_fallback"
    assert payload["risk_reward_direction"] == "bullish"
    assert payload["risk_reward_ratio"] == 2.0
    assert payload["risk_reward_state_text"] == "盈亏比可接受"


def test_backfill_snapshot_features_infers_inactive_quote_reason(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "status_text": "非活跃或暂无实时报价",
            "quote_text": "Bid 3310.00 / Ask 3310.18",
        },
        snapshot_time="2026-04-22 16:30:29",
    )
    with open_knowledge_connection(db_path, ensure_schema=True) as conn:
        conn.execute("UPDATE market_snapshots SET has_live_quote = 0")
    history_file.write_text("", encoding="utf-8")

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["quote_activity_updates"] == 1

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute("SELECT feature_json FROM market_snapshots LIMIT 1").fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)

    assert payload["quote_live_reason"] == "stale_tick"
    assert "旧 tick" in payload["quote_live_reason_text"]


def test_backfill_snapshot_features_repairs_signal_side_for_observe_snapshot(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "intraday_bias": "bullish",
            "multi_timeframe_bias": "bullish",
            "breakout_direction": "bullish",
            "signal_side": "neutral",
            "signal_side_text": "",
        },
        snapshot_time="2026-04-22 17:00:29",
    )
    history_file.write_text("", encoding="utf-8")

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["updated_rows"] == 1
    assert report["signal_side_repairs"] == 1

    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT signal_side, feature_json FROM market_snapshots LIMIT 1").fetchone()
    conn.close()
    payload = json.loads(row[1])

    assert row[0] == "long"
    assert payload["signal_side"] == "long"
    assert payload["signal_side_basis"] == "结构投票"
    assert payload["signal_side_text"] == "【↑ 多头参考】"


def test_backfill_snapshot_features_repairs_stale_execution_note_prefix(tmp_path):
    db_path = tmp_path / "knowledge.db"
    history_file = tmp_path / "alert_history.jsonl"

    _insert_snapshot(
        db_path,
        {
            "trade_grade_detail": "外部宏观结果与当前结构方向相反，先别逆着最新数据硬做。",
            "execution_note": "可轻仓试仓：报价相对平稳，适合继续观察关键位。",
        },
        snapshot_time="2026-04-23 03:16:24",
    )
    history_file.write_text("", encoding="utf-8")

    report = backfill_snapshot_features(
        db_path=db_path,
        history_file=history_file,
        symbol="XAUUSD",
    )

    assert report["updated_rows"] == 1
    assert report["execution_note_repairs"] == 1

    conn = sqlite3.connect(str(db_path))
    raw_json = conn.execute("SELECT feature_json FROM market_snapshots LIMIT 1").fetchone()[0]
    conn.close()
    payload = json.loads(raw_json)

    assert payload["execution_note"].startswith("只适合观察：")
    assert "可轻仓试仓：" not in payload["execution_note"]
