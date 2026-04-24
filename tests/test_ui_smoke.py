import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

import ui


def _build_test_config():
    return SimpleNamespace(
        symbols=["XAUUSD", "XAGUSD"],
        refresh_interval_sec=30,
        event_risk_mode="normal",
        event_auto_mode_enabled=False,
        event_schedule_text="",
        event_pre_window_min=30,
        event_post_window_min=15,
        event_feed_enabled=False,
        event_feed_url="",
        event_feed_refresh_min=60,
        dingtalk_webhook="",
        pushplus_token="",
        notify_cooldown_min=30,
        ai_api_key="",
        ai_model="test-model",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )


def test_main_window_can_bootstrap(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui, "get_runtime_config", lambda: _build_test_config())
    monkeypatch.setattr(ui.MetalMonitorWindow, "refresh_snapshot", lambda self: None)

    window = ui.MetalMonitorWindow()
    try:
        assert window.left_panel is not None
        assert window.right_table is not None
        assert window.lbl_status_badge.text() == "准备中"
        assert "执行漏斗" in window.lbl_execution_funnel.text()
        assert "AI" in window.left_panel.txt_ai_brief.toPlainText()

    finally:
        window.close()
        app.processEvents()
