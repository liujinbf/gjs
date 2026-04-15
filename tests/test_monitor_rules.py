import os
import sys
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
