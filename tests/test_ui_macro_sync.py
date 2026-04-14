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
        event_feed_enabled=True,
        event_feed_url="demo.json",
        event_feed_refresh_min=60,
        macro_news_feed_enabled=True,
        macro_news_feed_urls="demo.xml",
        macro_news_feed_refresh_min=30,
        macro_data_feed_enabled=True,
        macro_data_feed_specs="demo.json",
        macro_data_feed_refresh_min=60,
        dingtalk_webhook="",
        pushplus_token="",
        notify_cooldown_min=30,
        ai_api_key="",
        ai_model="test-model",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )


def test_macro_sync_ready_triggers_refresh_when_status_changes(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui, "get_runtime_config", lambda: _build_test_config())
    monkeypatch.setattr(ui.MetalMonitorWindow, "refresh_snapshot", lambda self: None)

    window = ui.MetalMonitorWindow()
    called = {"count": 0, "logs": []}
    try:
        window._last_snapshot = {
            "event_feed_status_text": "外部事件源等待后台同步，本地尚无可用缓存。",
            "macro_news_status_text": "外部资讯流等待后台同步，本地尚无可用缓存。",
            "macro_data_status_text": "结构化宏观数据等待后台同步，本地尚无可用缓存。",
        }
        window.refresh_snapshot = lambda: called.__setitem__("count", called["count"] + 1)
        window._append_log = lambda message: called["logs"].append(str(message))

        window._on_macro_sync_ready(
            {
                "event_feed": {"status": "fresh", "status_text": "外部事件源已同步：4 条。"},
                "macro_news": {"status": "fresh", "status_text": "外部资讯流已同步：2 条高相关更新。"},
                "macro_data": {"status": "fresh", "status_text": "结构化宏观数据已同步：3 条。"},
            }
        )

        assert called["count"] == 1
        assert any("宏观同步" in line for line in called["logs"])
    finally:
        window.close()
        app.processEvents()


def test_macro_sync_ready_marks_pending_when_snapshot_worker_busy(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(ui, "get_runtime_config", lambda: _build_test_config())
    monkeypatch.setattr(ui.MetalMonitorWindow, "refresh_snapshot", lambda self: None)

    window = ui.MetalMonitorWindow()
    called = {"count": 0}

    class _BusyWorker:
        @staticmethod
        def isRunning():
            return True

        @staticmethod
        def wait(_timeout=0):
            return True

    try:
        window._last_snapshot = {
            "event_feed_status_text": "外部事件源等待后台同步，本地尚无可用缓存。",
        }
        window._worker = _BusyWorker()
        window.refresh_snapshot = lambda: called.__setitem__("count", called["count"] + 1)

        window._on_macro_sync_ready(
            {
                "event_feed": {"status": "fresh", "status_text": "外部事件源已同步：4 条。"},
            }
        )

        assert called["count"] == 0
        assert window._macro_sync_refresh_pending is True
    finally:
        window.close()
        app.processEvents()
