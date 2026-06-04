import os
import sys
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_config
import monitor_rules


def test_build_quote_risk_note_uses_env_threshold_override(monkeypatch):
    monkeypatch.setattr(app_config, "load_project_env", lambda: Path("."))
    monkeypatch.setenv(
        "QUOTE_RISK_THRESHOLDS_JSON",
        '{"XAU":{"warn_points":5,"alert_points":12,"warn_pct":0.001,"alert_pct":0.005}}',
    )

    tone, note = monitor_rules.build_quote_risk_note(
        "XAUUSD",
        {
            "bid": 3300.0,
            "ask": 3300.08,
            "latest_price": 3300.04,
            "point": 0.01,
            "spread_points": 8.0,
            "quote_status_code": "live",
        },
    )

    assert tone == "accent"
    assert "点差偏宽" in note

    monkeypatch.delenv("QUOTE_RISK_THRESHOLDS_JSON", raising=False)


def test_build_trade_grade_uses_status_code_instead_of_text():
    payload = monitor_rules.build_trade_grade(
        "XAUUSD",
        {
            "status": "状态文本变了但仍然不是活跃报价",
            "quote_status_code": "inactive",
            "has_live_quote": False,
        },
        tone="neutral",
        connected=True,
    )

    assert payload["source"] == "inactive"
    assert payload["grade"] == "当前不宜出手"


def test_scalp_candidate_respects_global_disabled_switch(monkeypatch):
    monkeypatch.setattr(
        app_config,
        "get_runtime_config",
        lambda: app_config.MetalMonitorConfig(
            symbols=["XAUUSD"],
            refresh_interval_sec=30,
            event_risk_mode="normal",
            mt5_path="",
            mt5_login="",
            mt5_password="",
            mt5_server="",
            dingtalk_webhook="",
            pushplus_token="",
            notify_cooldown_min=30,
            ai_api_key="",
            ai_api_base="https://api.demo.com",
            ai_model="demo",
            ai_push_enabled=False,
            ai_push_summary_only=True,
            scalp_enabled=False,
        ),
    )

    result = monitor_rules._build_scalp_ready_candidate(
        "XAUUSD",
        {
            "scalp_ready": True,
            "scalp_direction": "long",
            "scalp_rr": 2.0,
            "scalp_confidence": "high",
            "spread_points": 10.0,
            "point": 0.01,
            "atr5_m5": 2.0,
        },
    )

    assert result is None


def test_get_24h_avg_spread_reads_market_snapshots(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.sqlite"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE market_snapshots (
                snapshot_time TEXT NOT NULL,
                symbol TEXT NOT NULL,
                spread_points REAL NOT NULL DEFAULT 0
            )
            """
        )
        conn.executemany(
            "INSERT INTO market_snapshots (snapshot_time, symbol, spread_points) VALUES (?, ?, ?)",
            [
                ("2099-01-01 00:00:00", "XAUUSD", 20.0),
                ("2099-01-01 00:01:00", "XAUUSD", 24.0),
            ],
        )
    monitor_rules._SPREAD_AVG_CACHE.clear()
    monkeypatch.setattr("knowledge_base.KNOWLEDGE_DB_FILE", db_path)

    assert monitor_rules._get_24h_avg_spread("XAUUSD") == 22.0
