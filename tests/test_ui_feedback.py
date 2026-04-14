import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from ui_panels import WatchListTable


def _build_snapshot(snapshot_time: str = "2026-04-13 10:00:00") -> dict:
    return {
        "last_refresh_text": snapshot_time,
        "items": [
            {
                "symbol": "XAUUSD",
                "snapshot_id": 34,
                "latest_text": "3310.20",
                "quote_text": "Bid 3310.10 / Ask 3310.20 / 点差 10点",
                "status_text": "实时报价",
                "macro_focus": "关注美国 CPI。",
                "alert_state_text": "结构候选",
                "execution_note": "等待回踩确认。",
                "tone": "neutral",
            }
        ],
    }


def test_watch_list_feedback_uses_selected_snapshot_time(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = {}

    def fake_record_user_feedback(**kwargs):
        captured.update(kwargs)
        return {
            "inserted_count": 1,
            "feedback_id": 12,
            "snapshot_id": 34,
        }

    monkeypatch.setattr("knowledge_feedback.record_user_feedback", fake_record_user_feedback)

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(_build_snapshot("2026-04-13 10:00:00"))
        widget._on_row_clicked(widget.table.item(0, 0))
        widget._submit_feedback("helpful")

        assert captured["symbol"] == "XAUUSD"
        assert captured["snapshot_id"] == 34
        assert captured["snapshot_time"] == "2026-04-13 10:00:00"
        assert captured["source"] == "ui_quick"
        assert "已记录" in widget._lbl_feedback_hint.text()
    finally:
        widget.close()
        app.processEvents()


def test_watch_list_feedback_waits_until_snapshot_binding_ready(monkeypatch):
    app = QApplication.instance() or QApplication([])

    def fake_record_user_feedback(**kwargs):
        raise AssertionError("未绑定 snapshot_id 前不应提交反馈")

    monkeypatch.setattr("knowledge_feedback.record_user_feedback", fake_record_user_feedback)

    snapshot = _build_snapshot("2026-04-13 10:00:00")
    snapshot["items"][0]["snapshot_id"] = 0

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(snapshot)
        widget._on_row_clicked(widget.table.item(0, 0))

        assert "样本仍在入库" in widget._lbl_feedback_hint.text()

        widget.bind_feedback_snapshot_ids("2026-04-13 10:00:00", {"XAUUSD": 56})
        widget._on_row_clicked(widget.table.item(0, 0))

        assert "这次提醒对你有帮助吗" in widget._lbl_feedback_hint.text()
    finally:
        widget.close()
        app.processEvents()


def test_watch_list_feedback_shows_failure_when_snapshot_missing(monkeypatch):
    app = QApplication.instance() or QApplication([])

    def fake_record_user_feedback(**kwargs):
        return {
            "inserted_count": 0,
            "feedback_id": None,
            "error": "未找到可关联的市场快照，当前反馈未入库。",
        }

    monkeypatch.setattr("knowledge_feedback.record_user_feedback", fake_record_user_feedback)

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(_build_snapshot("2026-04-13 10:00:00"))
        widget._on_row_clicked(widget.table.item(0, 0))
        widget._submit_feedback("noise")

        assert "反馈未写入" in widget._lbl_feedback_hint.text()
        assert "未找到可关联的市场快照" in widget._lbl_feedback_hint.text()
    finally:
        widget.close()
        app.processEvents()
