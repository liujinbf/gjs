import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from quote_models import SnapshotItem
from ui_panels import SimTradingPanel, WatchListTable


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


def test_watch_list_accepts_snapshot_item_objects(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = {}

    def fake_record_user_feedback(**kwargs):
        captured.update(kwargs)
        return {
            "inserted_count": 1,
            "feedback_id": 22,
            "snapshot_id": 78,
        }

    monkeypatch.setattr("knowledge_feedback.record_user_feedback", fake_record_user_feedback)

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
        "items": [
            SnapshotItem(
                symbol="XAUUSD",
                latest_price=3310.20,
                quote_status_code="live",
                extra={
                    "snapshot_id": 78,
                    "latest_text": "3310.20",
                    "quote_text": "Bid 3310.10 / Ask 3310.20 / 点差 10点",
                    "status_text": "实时报价",
                    "macro_focus": "关注美国 CPI。",
                    "alert_state_text": "结构候选",
                    "execution_note": "等待回踩确认。",
                    "tone": "neutral",
                },
            )
        ],
    }

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(snapshot)
        widget._on_row_clicked(widget.table.item(0, 0))
        widget._submit_feedback("helpful")

        assert captured["symbol"] == "XAUUSD"
        assert captured["snapshot_id"] == 78
        assert "已记录" in widget._lbl_feedback_hint.text()
    finally:
        widget.close()
        app.processEvents()


def test_watch_list_displays_normalized_quote_status_text():
    app = QApplication.instance() or QApplication([])

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
        "items": [
            {
                "symbol": "EURUSD",
                "snapshot_id": 56,
                "latest_text": "1.17270",
                "quote_text": "Bid 1.17260 / Ask 1.17270 / 点差 10点",
                "status_text": "经纪商自定义实时状态",
                "quote_status_code": "live",
                "macro_focus": "关注美元方向。",
                "alert_state_text": "报价正常观察",
                "execution_note": "等待结构更清楚。",
                "tone": "neutral",
            }
        ],
    }

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(snapshot)
        assert widget.table.item(0, 3).text() == "活跃报价"
    finally:
        widget.close()
        app.processEvents()


def test_sim_trading_panel_displays_risk_reward_columns(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "equity": 99093.73,
                "total_profit": 0.0,
                "used_margin": 2453.67,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return [
                {
                    "symbol": "XAUUSD",
                    "action": "long",
                    "quantity": 0.51,
                    "entry_price": 4811.12,
                    "stop_loss": 4771.91,
                    "take_profit": 4874.19,
                    "take_profit_2": 4905.73,
                    "floating_pnl": -906.27,
                }
            ]

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            contract_size = 100.0
            price_diff = (current_price - entry_price) if is_long else (entry_price - current_price)
            return 0.0, price_diff * lots * contract_size

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []
            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())

    panel = SimTradingPanel()
    try:
        panel.update_data()

        assert panel.tbl_positions.columnCount() == 10
        assert panel.tbl_positions.item(0, 6).text().startswith("$")
        assert "T1" in panel.tbl_positions.item(0, 7).text()
        assert "R" in panel.tbl_positions.item(0, 8).text()
        assert panel.tbl_positions.item(0, 9).text() == "-$906.27"
    finally:
        panel.close()
        app.processEvents()
