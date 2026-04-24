import os
import sys
import json
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from quote_models import SnapshotItem
from ui_logic_editor import RuleLogicEditorDialog
from ui_panels import LeftTabPanel, PendingRulesPanel, SimTradingPanel, WatchListTable


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


def test_left_tab_panel_shows_ai_signal_health(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        "ui_panels.summarize_recent_ai_history",
        lambda days=7: {"summary_text": "最近7天共记录 2 次 AI 研判。"},
    )
    monkeypatch.setattr(
        "ui_panels.read_recent_ai_history",
        lambda limit=1: [
            {
                "summary_line": "当前结论：轻仓试多。",
                "occurred_at": "2026-04-22 21:00:00",
                "push_sent": True,
            }
        ],
    )
    monkeypatch.setattr(
        "ui_panels.summarize_recent_ai_signals",
        lambda days=30: {
            "total_count": 20,
            "valid_count": 19,
            "executable_count": 6,
            "sim_eligible_count": 2,
            "structured_count": 18,
            "fallback_count": 1,
        },
    )
    monkeypatch.setattr(
        "ui_panels.summarize_recent_history",
        lambda days=7: {"total_count": 0, "spread_count": 0, "summary_text": "近7天暂无提醒。"},
    )
    monkeypatch.setattr(
        "ui_panels.summarize_effectiveness",
        lambda snapshot: {"effective_count": 0, "summary_text": "暂无可评估提醒。"},
    )
    monkeypatch.setattr("ui_panels.read_recent_history", lambda limit=8: [])

    panel = LeftTabPanel()
    try:
        text = panel.lbl_ai_history_summary.text()
        assert "最近一次：当前结论：轻仓试多。" in text
        assert "AI链路健康：结构化 18/20（90%）" in text
        assert "协议有效 19/20" in text
        assert "方向信号 6/20（30%）" in text
        assert "规则允许试仓 2/20" in text
        assert "降级 1" in text
    finally:
        panel.close()
        app.processEvents()


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
        monkeypatch.setattr(widget, "_start_feedback_worker", lambda worker: worker())
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
        monkeypatch.setattr(widget, "_start_feedback_worker", lambda worker: worker())
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
        monkeypatch.setattr(widget, "_start_feedback_worker", lambda worker: worker())
        widget.update_from_snapshot(snapshot)
        widget._on_row_clicked(widget.table.item(0, 0))
        widget._submit_feedback("helpful")

        assert captured["symbol"] == "XAUUSD"
        assert captured["snapshot_id"] == 78
        assert "已记录" in widget._lbl_feedback_hint.text()
    finally:
        widget.close()
        app.processEvents()


def test_watch_list_feedback_runs_write_in_background_path(monkeypatch):
    app = QApplication.instance() or QApplication([])
    started = {"called": False}

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(_build_snapshot("2026-04-13 10:00:00"))
        widget._on_row_clicked(widget.table.item(0, 0))

        def fake_start(worker):
            started["called"] = True

        monkeypatch.setattr(widget, "_start_feedback_worker", fake_start)
        widget._submit_feedback("helpful")

        assert started["called"] is True
        assert "正在记录" in widget._lbl_feedback_hint.text()
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


def test_watch_list_displays_compact_quote_text_and_tooltips():
    app = QApplication.instance() or QApplication([])

    long_note = "只适合观察：报价相对平稳，但结构还没有进入理想出手位置。"
    snapshot = {
        "last_refresh_text": "2026-04-18 04:28:14",
        "items": [
            {
                "symbol": "XAUUSD",
                "snapshot_id": 77,
                "latest_text": "4854.52",
                "latest_price": 4854.52,
                "bid": 4854.44,
                "ask": 4854.63,
                "spread_points": 17,
                "point": 0.01,
                "status_text": "实时报价",
                "quote_status_code": "live",
                "has_live_quote": True,
                "macro_focus": "关注美国 CPI 和美元方向。",
                "alert_state_text": "报价正常观察",
                "execution_note": long_note,
                "tone": "neutral",
            }
        ],
    }

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(snapshot)

        assert widget.table.item(0, 2).text() == "4854.44 / 4854.63 · 17点"
        assert widget.table.item(0, 6).toolTip() == long_note
    finally:
        widget.close()
        app.processEvents()


def test_watch_list_uses_current_trade_grade_when_execution_note_stale():
    app = QApplication.instance() or QApplication([])

    snapshot = {
        "last_refresh_text": "2026-04-23 03:16:24",
        "items": [
            {
                "symbol": "XAUUSD",
                "snapshot_id": 88,
                "latest_text": "4739.48",
                "latest_price": 4739.48,
                "bid": 4739.39,
                "ask": 4739.56,
                "spread_points": 17,
                "point": 0.01,
                "status_text": "实时报价",
                "quote_status_code": "live",
                "has_live_quote": True,
                "macro_focus": "关注美国 PMI 和美元方向。",
                "alert_state_text": "宏观结果冲突",
                "signal_side_text": "【↑ 多头参考】",
                "trade_grade": "只适合观察",
                "trade_grade_detail": "报价相对平稳，但还没有形成足够干净的执行环境。",
                "execution_note": "可轻仓试仓：报价相对平稳，近24小时偏多。",
                "tone": "neutral",
            }
        ],
    }

    widget = WatchListTable()
    try:
        widget.update_from_snapshot(snapshot)

        text = widget.table.item(0, 6).text()
        tooltip = widget.table.item(0, 6).toolTip()
        assert "只适合观察：" in text
        assert "可轻仓试仓：" not in text
        assert "只适合观察：" in tooltip
        assert "可轻仓试仓：" not in tooltip
    finally:
        widget.close()
        app.processEvents()


def test_pending_rules_panel_load_uses_background_worker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    started = {"called": False}

    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: started.update({"called": True}),
    )

    panel = PendingRulesPanel()
    try:
        assert started["called"] is True
        assert "正在读取待审规则" in panel.lbl_pending_status.text()
    finally:
        panel.close()
        app.processEvents()


def test_pending_rules_panel_reads_governance_pending_rows(monkeypatch):
    app = QApplication.instance() or QApplication([])
    sync_called = {"called": False}

    class _FakeRows:
        @staticmethod
        def fetchall():
            return [
                {
                    "id": 12,
                    "created_at": "2026-04-18 10:30:00",
                    "category": "entry",
                    "asset_scope": "XAUUSD",
                    "rule_text": "等待回踩下沿后再试多",
                    "source_type": "llm_golden_setup",
                    "rationale": "人工复核原因",
                    "governance_status": "manual_review",
                    "validation_status": "manual_review",
                },
            ]

    class _FakeConn:
        row_factory = None
        _execute_count = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, sql, params=()):
            self._execute_count += 1
            if "manual_review_count" in sql:
                assert params == (30, 30)
                class _CountRows:
                    @staticmethod
                    def fetchall():
                        return [
                            {
                                "manual_review_count": 1,
                                "pending_count": 1,
                                "archived_count": 8,
                                "active_count": 2,
                                "watch_count": 3,
                                "frozen_count": 4,
                                "reference_count": 12,
                            },
                        ]
                return _CountRows()
            if "total_new_24h" in sql:
                class _RecentRows:
                    @staticmethod
                    def fetchall():
                        return [
                            {
                                "total_new_24h": 5,
                                "auto_learn_new_24h": 4,
                            "fallback_30m_new_24h": 3,
                            "sim_reflection_new_24h": 1,
                            "strategy_learning_new_24h": 1,
                            "frequent_pattern_new_24h": 0,
                        },
                        ]
                return _RecentRows()
            if "usable_888_count" in sql:
                class _HealthRows:
                    @staticmethod
                    def fetchall():
                        return [
                            {
                                "usable_888_count": 7,
                                "usable_30m_exec_count": 12,
                            },
                        ]
                return _HealthRows()
            if "FROM learning_reports" in sql and "deep_mining_status" in sql:
                class _DeepMiningRows:
                    @staticmethod
                    def fetchall():
                        return [
                            {
                                "summary_text": "最近一次深度挖掘于 2026-04-18 11:10:00 完成，本地新增 1 条，深度反思新增 0 条，共 1 条。",
                                "payload_json": json.dumps(
                                    {
                                        "ok": True,
                                        "total_inserted_rules": 1,
                                        "local_inserted_rules": 1,
                                        "llm_inserted_rules": 0,
                                        "llm_raw_candidate_count": 8,
                                        "llm_prepared_candidate_count": 5,
                                        "llm_quality_filtered_count": 2,
                                        "llm_duplicate_skipped_count": 3,
                                        "llm_duplicate_in_batch_count": 1,
                                        "llm_duplicate_existing_count": 2,
                                        "reflection_horizon": 30,
                                        "error": "",
                                    },
                                    ensure_ascii=False,
                                ),
                                "created_at": "2026-04-18 11:10:00",
                            },
                        ]
                return _DeepMiningRows()
            if "ORDER BY kr.id DESC" in sql:
                if "LIMIT 5" in sql:
                    class _RecentRuleListRows:
                        @staticmethod
                        def fetchall():
                            return [
                                {
                                    "id": 14,
                                    "created_at": "2026-04-18 11:05:00",
                                    "source_type": "llm_cluster_loss",
                                    "location": "auto_miner_v2_llm_fallback_30m",
                                    "category": "risk",
                                    "asset_scope": "XAUUSD",
                                    "rule_text": "事件前后若点差突然放大，暂停首脚建仓",
                                },
                                {
                                    "id": 13,
                                    "created_at": "2026-04-18 10:55:00",
                                    "source_type": "llm_golden_setup",
                                    "location": "auto_miner_v2_llm_fallback_30m",
                                    "category": "entry",
                                    "asset_scope": "XAUUSD",
                                    "rule_text": "等待回踩下沿后再试多",
                                },
                            ]
                    return _RecentRuleListRows()
                class _LatestRows:
                    @staticmethod
                    def fetchall():
                        return [
                            {
                                "id": 12,
                                "created_at": "2026-04-18 10:35:00",
                                "source_type": "llm_golden_setup",
                                "location": "auto_miner_v2_llm_fallback_30m",
                                "category": "entry",
                                "asset_scope": "XAUUSD",
                                "rule_text": "等待回踩下沿后再试多",
                            }
                        ]
                return _LatestRows()
            assert params == (30,)
            assert "FROM rule_governance" in sql
            assert "rg.governance_status = 'manual_review'" in sql
            return _FakeRows()

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeConn(),
    )
    monkeypatch.setattr(
        "ui_panels.sync_strategy_learning_reviews",
        lambda **_kwargs: sync_called.update({"called": True}) or {"review_count": 0},
    )
    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )

    panel = PendingRulesPanel()
    try:
        panel._run_load_pending_rules()
        app.processEvents()

        assert panel.table.rowCount() == 1
        assert sync_called["called"] is True
        assert panel.lbl_pending_review_count.text() == "人工复核 1"
        assert panel.lbl_pending_accumulate_count.text() == "待积累 1"
        assert panel.lbl_pending_archived_count.text() == "自动归档 8"
        assert panel.lbl_pending_active_count.text() == "启用 2"
        assert panel.lbl_pending_frozen_count.text() == "冻结 4"
        assert panel.lbl_pending_reference_count.text() == "基础参考 12"
        assert panel.lbl_pending_recent_count.text() == "24h新增 5"
        assert "自动赛道 启用 2 条 / 观察 3 条 / 冻结 4 条 / 待积累 1 条；基础参考 12 条。" in panel.lbl_learning_digest.text()
        assert "30m 轻量反思 3 条" in panel.lbl_learning_digest.text()
        assert "888 模拟盘反思 1 条" in panel.lbl_learning_digest.text()
        assert "策略参数：" in panel.lbl_strategy_param_state.text()
        assert "回调狙击 1.45R" in panel.lbl_strategy_param_state.text()
        assert "方向试仓 1.80R" in panel.lbl_strategy_param_state.text()
        assert "回调狙击：1.45R / 日上限 3 次 / 冷却 10 分钟" in panel.lbl_strategy_param_state.toolTip()
        assert "888 待反思样本 7 条" in panel.lbl_learning_health.text()
        assert "30m 可执行样本 12 条" in panel.lbl_learning_health.text()
        assert "最近24小时深度反思新增 4 条规则" in panel.lbl_learning_health.text()
        assert "最近一次深挖 04-18 11:10" in panel.lbl_learning_health.text()
        assert "本地新增 1 条，深度反思新增 0 条，共 1 条 / LLM反思 h30" in panel.lbl_learning_health.text()
        assert "上次学习漏斗：原始候选 8 条 -> 质量过滤后 6 条 -> 去重后 2 条 -> 最终入库 0 条" in panel.lbl_learning_health.text()
        assert "质量闸门拦下 2 条" in panel.lbl_learning_health.text()
        assert "去重拦下 3 条（批内重复 1，库内已存在 2）" in panel.lbl_learning_health.text()
        assert "当前学习链仍在正常工作" in panel.lbl_learning_health.text()
        assert "#166534" in panel.lbl_learning_health.styleSheet()
        assert "#bbf7d0" in panel.lbl_learning_health.styleSheet()
        assert "最近24小时新增规则：" in panel.lbl_recent_learning_rules.text()
        assert "llm_cluster_loss/risk" in panel.lbl_recent_learning_rules.text()
        assert "事件前后若点差突然放大，暂停首脚建仓" in panel.lbl_recent_learning_rules.text()
        assert panel.table.item(0, 4).text() == "人工复核"
        assert panel.table.item(0, 5).toolTip() == "人工复核原因"
    finally:
        panel.close()
        app.processEvents()


def test_pending_rules_panel_updates_governance_status_instead_of_confidence(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = []

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, sql, params=()):
            captured.append((" ".join(str(sql).split()), params))
            class _Result:
                rowcount = 1
            return _Result()

        def commit(self):
            captured.append(("COMMIT", ()))

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeConn(),
    )
    monkeypatch.setattr(
        "ui_panels.apply_strategy_learning_review",
        lambda *_args, **_kwargs: {"applied": True, "message": "回调狙击 最小 RR 已由 1.45 调整为 1.60"},
    )
    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )

    panel = PendingRulesPanel()
    try:
        monkeypatch.setattr(panel, "load_pending_rules", lambda: None)
        panel._run_update_rule_status(88, "active")
        app.processEvents()

        assert any("INSERT INTO rule_governance" in sql for sql, _ in captured)
        assert any("UPDATE rule_scores SET validation_status" in sql for sql, _ in captured)
        assert any("最小 RR 已由 1.45 调整为 1.60" in str(params) for _, params in captured)
        assert all("UPDATE knowledge_rules SET confidence" not in sql for sql, _ in captured)
        assert "最近应用：回调狙击 最小 RR 已由 1.45 调整为 1.60" in panel.lbl_strategy_param_state.text()
    finally:
        panel.close()
        app.processEvents()


def test_rule_logic_editor_save_activates_governance_instead_of_confidence(monkeypatch):
    app = QApplication.instance() or QApplication([])
    captured = []

    class _FetchOne:
        @staticmethod
        def fetchone():
            return {
                "rule_text": "突破后回踩确认再试仓",
                "logic_json": '{"op":"AND","conditions":[{"field":"signal_side","op":"==","value":"long"}]}',
            }

    class _Result:
        rowcount = 1

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            normalized_sql = " ".join(str(sql).split())
            captured.append((normalized_sql, params))
            if "SELECT rule_text, logic_json" in normalized_sql:
                return _FetchOne()
            return _Result()

    monkeypatch.setattr(
        "ui_logic_editor.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeConn(),
    )
    monkeypatch.setattr("ui_logic_editor.QMessageBox.information", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("ui_logic_editor.QMessageBox.critical", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("ui_logic_editor.RuleLogicEditorDialog._start_load_worker", lambda self, worker: None)

    dialog = RuleLogicEditorDialog(rule_id=66)
    try:
        dialog._run_save_and_activate(
            {"op": "AND", "conditions": [{"field": "signal_side", "op": "==", "value": "long"}]}
        )
        app.processEvents()

        assert any("UPDATE knowledge_rules SET logic_json = ?" in sql for sql, _ in captured)
        assert any("INSERT INTO rule_scores" in sql and "validation_status" in sql for sql, _ in captured)
        assert any("INSERT INTO rule_governance" in sql and "governance_status" in sql for sql, _ in captured)
        assert all("confidence" not in sql for sql, _ in captured)
    finally:
        dialog.close()
        app.processEvents()


def test_rule_logic_editor_load_uses_background_worker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    started = {"called": False}

    monkeypatch.setattr(
        "ui_logic_editor.RuleLogicEditorDialog._start_load_worker",
        lambda self, worker: started.update({"called": True}),
    )

    dialog = RuleLogicEditorDialog(7)
    try:
        assert started["called"] is True
        assert dialog.tree.isEnabled() is False
        assert dialog.btn_save.isEnabled() is False
        assert "正在读取规则" in dialog.info_lbl.text()
    finally:
        dialog.close()
        app.processEvents()


def test_rule_logic_editor_applies_loaded_rule(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr("ui_logic_editor.RuleLogicEditorDialog._start_load_worker", lambda self, worker: None)
    dialog = RuleLogicEditorDialog(7)
    try:
        dialog._on_load_result(
            {
                "ok": True,
                "rule_text": "回踩下沿确认后试多",
                "logic_dict": {"op": "AND", "conditions": [{"field": "signal_side", "op": "==", "value": "long"}]},
                "error": "",
            }
        )

        assert dialog.tree.isEnabled() is True
        assert dialog.btn_save.isEnabled() is True
        assert "回踩下沿确认后试多" in dialog.info_lbl.text()
        assert dialog.tree.topLevelItem(0).childCount() == 1
    finally:
        dialog.close()
        app.processEvents()


def test_pending_rules_panel_shows_empty_state_when_no_manual_review(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )

    panel = PendingRulesPanel()
    try:
        panel._on_pending_rules_loaded(
            {
                "ok": True,
                "rows": [],
                "counts": {"manual_review": 0, "pending": 33, "archived": 1541, "active": 0, "watch": 0, "frozen": 609, "reference": 2183},
                "recent_stats": {"total_new_24h": 0, "auto_learn_new_24h": 0, "fallback_30m_new_24h": 0, "sim_reflection_new_24h": 0, "frequent_pattern_new_24h": 0},
                "health_stats": {
                    "usable_888_count": 0,
                    "usable_30m_exec_count": 0,
                    "reflection_new_24h": 0,
                    "last_deep_mining_at": "",
                    "last_deep_mining_ok": False,
                    "last_deep_mining_total_inserted": 0,
                    "last_deep_mining_local_inserted": 0,
                    "last_deep_mining_llm_inserted": 0,
                    "last_llm_raw_candidate_count": 0,
                    "last_llm_prepared_candidate_count": 0,
                    "last_llm_quality_filtered_count": 0,
                    "last_llm_duplicate_skipped_count": 0,
                    "last_llm_duplicate_in_batch_count": 0,
                    "last_llm_duplicate_existing_count": 0,
                    "last_reflection_horizon": 0,
                    "last_deep_mining_error": "",
                },
                "latest_rule": {"created_at": "2026-04-18 04:15:00", "source_type": "local_markdown", "rule_text": "不确定方向时，宁可空仓观望，也不要强行押注"},
                "recent_rules": [],
                "error": "",
            }
        )

        assert panel.table.rowCount() == 0
        assert not panel.lbl_pending_empty_state.isHidden()
        assert "当前没有需要人工审核的规则" in panel.lbl_pending_empty_state.text()
        assert "自动归档 1541 条" in panel.lbl_pending_empty_state.text()
        assert "33 条规则正在等待样本积累" in panel.lbl_pending_empty_state.text()
        assert "基础参考 2183 条" in panel.lbl_learning_digest.text()
        assert "最近24小时新增 0 条" in panel.lbl_learning_digest.text()
        assert "888 待反思样本 0 条" in panel.lbl_learning_health.text()
        assert "30m 可执行样本 0 条" in panel.lbl_learning_health.text()
        assert "最近还没有深度挖掘运行记录" in panel.lbl_learning_health.text()
        assert "当前更像是样本不足" in panel.lbl_learning_health.text()
        assert "#1d4ed8" in panel.lbl_learning_health.styleSheet()
        assert "#bfdbfe" in panel.lbl_learning_health.styleSheet()
        assert panel.lbl_recent_learning_rules.text() == "最近24小时新增规则：暂无新增。"
    finally:
        panel.close()
        app.processEvents()


def test_pending_rules_panel_learning_health_uses_quality_gate_color(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )

    panel = PendingRulesPanel()
    try:
        panel._on_pending_rules_loaded(
            {
                "ok": True,
                "rows": [],
                "counts": {"manual_review": 0, "pending": 1, "archived": 0, "active": 0, "watch": 0, "frozen": 0, "reference": 0},
                "recent_stats": {"total_new_24h": 0, "auto_learn_new_24h": 0, "fallback_30m_new_24h": 0, "sim_reflection_new_24h": 0, "frequent_pattern_new_24h": 0},
                "health_stats": {
                    "usable_888_count": 2,
                    "usable_30m_exec_count": 3,
                    "reflection_new_24h": 0,
                    "last_deep_mining_at": "2026-04-18 12:00:00",
                    "last_deep_mining_ok": True,
                    "last_deep_mining_total_inserted": 0,
                    "last_deep_mining_local_inserted": 0,
                    "last_deep_mining_llm_inserted": 0,
                    "last_llm_raw_candidate_count": 5,
                    "last_llm_prepared_candidate_count": 3,
                    "last_llm_quality_filtered_count": 4,
                    "last_llm_duplicate_skipped_count": 0,
                    "last_llm_duplicate_in_batch_count": 0,
                    "last_llm_duplicate_existing_count": 0,
                    "last_reflection_horizon": 30,
                    "last_deep_mining_error": "",
                },
                "latest_rule": {},
                "recent_rules": [],
                "error": "",
            }
        )

        assert "主要是质量闸门在拦截" in panel.lbl_learning_health.text()
        assert "#9a3412" in panel.lbl_learning_health.styleSheet()
        assert "#fed7aa" in panel.lbl_learning_health.styleSheet()
    finally:
        panel.close()
        app.processEvents()


def test_pending_rules_panel_learning_health_uses_dedup_color(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )

    panel = PendingRulesPanel()
    try:
        panel._on_pending_rules_loaded(
            {
                "ok": True,
                "rows": [],
                "counts": {"manual_review": 0, "pending": 1, "archived": 0, "active": 0, "watch": 0, "frozen": 0, "reference": 0},
                "recent_stats": {"total_new_24h": 0, "auto_learn_new_24h": 0, "fallback_30m_new_24h": 0, "sim_reflection_new_24h": 0, "frequent_pattern_new_24h": 0},
                "health_stats": {
                    "usable_888_count": 2,
                    "usable_30m_exec_count": 3,
                    "reflection_new_24h": 0,
                    "last_deep_mining_at": "2026-04-18 12:00:00",
                    "last_deep_mining_ok": True,
                    "last_deep_mining_total_inserted": 0,
                    "last_deep_mining_local_inserted": 0,
                    "last_deep_mining_llm_inserted": 0,
                    "last_llm_raw_candidate_count": 5,
                    "last_llm_prepared_candidate_count": 3,
                    "last_llm_quality_filtered_count": 0,
                    "last_llm_duplicate_skipped_count": 4,
                    "last_llm_duplicate_in_batch_count": 1,
                    "last_llm_duplicate_existing_count": 3,
                    "last_reflection_horizon": 30,
                    "last_deep_mining_error": "",
                },
                "latest_rule": {},
                "recent_rules": [],
                "error": "",
            }
        )

        assert "主要是去重机制阻止了重复入库" in panel.lbl_learning_health.text()
        assert "#7c3aed" in panel.lbl_learning_health.styleSheet()
        assert "#ddd6fe" in panel.lbl_learning_health.styleSheet()
    finally:
        panel.close()
        app.processEvents()


def test_pending_rules_panel_can_copy_learning_summary(monkeypatch):
    app = QApplication.instance() or QApplication([])

    monkeypatch.setattr(
        "ui_panels.PendingRulesPanel._start_pending_rules_worker",
        lambda self, worker: None,
    )
    monkeypatch.setattr("ui_panels.QTimer.singleShot", lambda *_args, **_kwargs: None)

    panel = PendingRulesPanel()
    try:
        panel._on_pending_rules_loaded(
            {
                "ok": True,
                "rows": [],
                "counts": {"manual_review": 0, "pending": 3, "archived": 10, "active": 2, "watch": 4, "frozen": 1, "reference": 8},
                "recent_stats": {"total_new_24h": 6, "auto_learn_new_24h": 4, "fallback_30m_new_24h": 3, "sim_reflection_new_24h": 1, "frequent_pattern_new_24h": 1},
                "health_stats": {
                    "usable_888_count": 5,
                    "usable_30m_exec_count": 9,
                    "reflection_new_24h": 4,
                    "last_deep_mining_at": "2026-04-18 12:30:00",
                    "last_deep_mining_ok": True,
                    "last_deep_mining_total_inserted": 2,
                    "last_deep_mining_local_inserted": 1,
                    "last_deep_mining_llm_inserted": 1,
                    "last_llm_raw_candidate_count": 6,
                    "last_llm_prepared_candidate_count": 4,
                    "last_llm_quality_filtered_count": 1,
                    "last_llm_duplicate_skipped_count": 2,
                    "last_llm_duplicate_in_batch_count": 1,
                    "last_llm_duplicate_existing_count": 1,
                    "last_reflection_horizon": 30,
                    "last_deep_mining_error": "",
                },
                "latest_rule": {
                    "created_at": "2026-04-18 12:20:00",
                    "source_type": "llm_cluster_loss",
                    "rule_text": "事件前后若点差突然放大，暂停首脚建仓",
                },
                "recent_rules": [
                    {
                        "created_at": "2026-04-18 12:20:00",
                        "source_type": "llm_cluster_loss",
                        "category": "risk",
                        "rule_text": "事件前后若点差突然放大，暂停首脚建仓",
                    }
                ],
                "error": "",
            }
        )

        panel._copy_learning_summary()
        copied = app.clipboard().text()

        assert "自动学习摘要" in copied
        assert "规则池：启用 2 / 观察 4 / 冻结 1 / 待积累 3" in copied
        assert "样本池：888待反思 5 / 30m可执行 9" in copied
        assert "学习漏斗：原始 6 -> 质量后 5 -> 去重后 2 -> 入库 1" in copied
        assert "最近规则：[llm_cluster_loss] 事件前后若点差突然放大，暂停首脚建仓" in copied
        assert "最近24h重点：[llm_cluster_loss/risk] 事件前后若点差突然放大，暂停首脚建仓" in copied
        assert panel.lbl_learning_copy_hint.text() == "学习摘要已复制到剪贴板。"
    finally:
        panel.close()
        app.processEvents()


def test_rule_logic_editor_save_uses_background_worker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    started = {"called": False}

    monkeypatch.setattr(
        "ui_logic_editor.RuleLogicEditorDialog._start_load_worker",
        lambda self, worker: None,
    )

    dialog = RuleLogicEditorDialog(1)
    try:
        monkeypatch.setattr(dialog, "_start_save_worker", lambda worker: started.update({"called": True}))
        dialog._save_and_activate()

        assert started["called"] is True
        assert dialog.btn_save.isEnabled() is False
        assert "正在保存" in dialog.btn_save.text()
    finally:
        dialog.close()
        app.processEvents()


def test_rule_logic_editor_sandbox_uses_background_worker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    started = {"called": False}

    monkeypatch.setattr(
        "ui_logic_editor.RuleLogicEditorDialog._start_load_worker",
        lambda self, worker: None,
    )

    dialog = RuleLogicEditorDialog(1)
    try:
        monkeypatch.setattr(dialog, "_start_sandbox_worker", lambda worker: started.update({"called": True}))
        dialog._run_sandbox()

        assert started["called"] is True
        assert dialog.btn_simulate.isEnabled() is False
        assert "沙盒运转中" in dialog.btn_simulate.text()
    finally:
        dialog.close()
        app.processEvents()


def test_sim_trading_panel_displays_risk_reward_columns(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 960.0,
                "equity": 930.0,
                "total_profit": -40.0,
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
                    "execution_profile": "exploratory",
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

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []
            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels.replay_exploratory_grade_gate",
        lambda **_kwargs: {
            "scanned_count": 12,
            "released_count": 3,
            "policy_accepted_count": 1,
            "top_still_blocked_labels": [{"reason_label": "未到试仓级别", "count": 9}],
            "top_grade_gate_secondary_labels": [{"reason_label": "盈亏比未准备好", "count": 6}],
            "top_rr_not_ready_tertiary_labels": [{"reason_label": "ATR缺失且关键位不足", "count": 4}],
            "top_no_direction_components": [],
            "summary_text": "观察复盘测试摘要",
        },
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4811.12,
                        "bid": 4810.95,
                        "ask": 4811.12,
                        "has_live_quote": True,
                        "trade_grade": "可轻仓试仓",
                        "trade_grade_source": "structure",
                        "signal_side": "long",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 1.8,
                        "risk_reward_stop_price": 4771.91,
                        "risk_reward_target_price": 4874.19,
                        "risk_reward_entry_zone_low": 4806.0,
                        "risk_reward_entry_zone_high": 4812.0,
                    }
                ]
            }
        )

        assert panel.tbl_positions.columnCount() == 10
        assert panel.tbl_history.columnCount() == 8
        assert panel.lbl_latest_no_open_reason.maximumHeight() <= 34
        assert panel.lbl_grade_gate_focus.maximumHeight() <= 34
        assert panel.lbl_entry_status.maximumHeight() <= 124
        assert panel.lbl_entry_audit.maximumHeight() <= 42
        assert panel.lbl_entry_trace.maximumHeight() <= 42
        assert panel.tbl_positions.minimumHeight() >= 170
        assert panel.tbl_history.minimumHeight() >= 170
        assert panel.lbl_initial_balance.maximumHeight() <= 74
        assert panel.strategy_detail_panel.isVisible() is False
        assert "策略复盘：" in panel.lbl_strategy_digest.text()
        assert "模拟账户 $1,000.00" in panel.lbl_sim_balance_hint.text()
        assert "RR 1.60/1.30" in panel.lbl_sim_balance_hint.text()
        assert "模型 68%" in panel.lbl_sim_balance_hint.text()
        assert "探索 3次/日" in panel.lbl_sim_balance_hint.text()
        assert "10分钟" in panel.lbl_sim_balance_hint.text()
        assert "无 TP2 保本 0.40R" in panel.lbl_sim_balance_hint.toolTip()
        assert "首次减仓 35%" in panel.lbl_sim_balance_hint.toolTip()
        assert "自动试仓状态：当前已有持仓" in panel.lbl_entry_status.text()
        assert panel.lbl_entry_status.toolTip() == panel.lbl_entry_status.text()
        assert "最近未开仓：当前已有持仓" in panel.lbl_latest_no_open_reason.text()
        assert panel.lbl_latest_no_open_reason.toolTip() == panel.lbl_latest_no_open_reason.text()
        assert "24h观察复盘：观察级别 12 | 可释放 3 | 预计执行 1 | 主阻因 未到试仓级别 9 | 次阻因 盈亏比未准备好 6 | RR细分 ATR缺失且关键位不足 4" in panel.lbl_grade_gate_focus.text()
        assert panel.lbl_grade_gate_focus.toolTip() == "观察复盘测试摘要"
        assert "方向：XAUUSD 做多 0.51 手" in panel.lbl_entry_status.text()
        assert "当前重点：先看保本保护和止盈退出。" in panel.lbl_entry_status.text()
        assert "$1,000.00" in panel.lbl_initial_balance.text()
        assert "$960.00" in panel.lbl_current_balance.text()
        assert "7.0%" in panel.lbl_drawdown_pct.text()
        assert "$1,999.71" in panel.lbl_total_risk.text()
        assert "1.61R" in panel.lbl_avg_rr.text()
        assert panel.tbl_positions.item(0, 1).text() == "做多 / 探索"
        assert panel.tbl_positions.item(0, 6).text().startswith("$")
        assert "T1" in panel.tbl_positions.item(0, 7).text()
        assert "R" in panel.tbl_positions.item(0, 8).text()
        assert panel.tbl_positions.item(0, 9).text() == "-$906.27"
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_shows_not_open_reason_from_snapshot(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4780.0,
                        "bid": 4779.9,
                        "ask": 4780.1,
                        "has_live_quote": True,
                        "trade_grade": "可轻仓试仓",
                        "trade_grade_source": "structure",
                        "signal_side": "long",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.2,
                        "risk_reward_stop_price": 4748.0,
                        "risk_reward_target_price": 4810.0,
                        "risk_reward_entry_zone_low": 4750.0,
                        "risk_reward_entry_zone_high": 4765.0,
                        "atr14": 18.0,
                        "alert_state_text": "报价正常观察",
                        "alert_state_detail": "当前执行面相对干净，继续等价格回踩。",
                        "trade_next_review": "5 分钟后再复核一次执行位。",
                        "execution_model_ready": True,
                        "execution_open_probability": 0.61,
                        "execution_model_note": "本地执行模型参考就绪度约 61%。主要用于提示这类结构历史上是更容易成交，还是更常被执行链挡住。",
                    }
                ]
            }
        )

        assert "自动试仓状态：当前未开仓" in panel.lbl_entry_status.text()
        assert "方向：XAUUSD 做多" in panel.lbl_entry_status.text()
        assert "现价：4780.00" in panel.lbl_entry_status.text()
        assert "执行区：4750.00 - 4765.00" in panel.lbl_entry_status.text()
        assert "盈亏比：2.20R" in panel.lbl_entry_status.text()
        assert "执行位：观察区 4750.00 - 4765.00" in panel.lbl_entry_status.text()
        assert "执行模型：就绪度约 61%" in panel.lbl_entry_status.text()
        assert "点差状态：报价正常观察；当前执行面相对干净，继续等价格回踩。" in panel.lbl_entry_status.text()
        assert "事件纪律：5 分钟后再复核一次执行位。" in panel.lbl_entry_status.text()
        assert "拦截原因：" in panel.lbl_entry_status.text()
        assert "价格尚未回到可执行观察区间附近" in panel.lbl_entry_status.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_shows_short_candidate_when_blocked(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels.read_full_history",
        lambda: [
            {
                "occurred_at": "2099-04-20 23:05:26",
                "symbol": "XAUUSD",
                "trade_grade": "只适合观察",
                "trade_grade_source": "structure",
                "trade_grade_detail": "点差虽然稳定，但多周期方向分歧，这种环境很容易出现假突破。",
                "signal_side": "",
                "risk_reward_ready": True,
            },
            {
                "occurred_at": "2099-04-20 23:15:21",
                "symbol": "XAUUSD",
                "trade_grade": "等待事件落地",
                "trade_grade_source": "event",
                "trade_grade_detail": "低影响窗口先观察。",
                "event_note": "低影响窗口：BOC Business Outlook Survey 将于 2026-04-20 23:30 落地。",
            },
            {
                "occurred_at": "2099-04-20 22:29:52",
                "symbol": "XAUUSD",
                "trade_grade": "只适合观察",
                "trade_grade_source": "model",
                "trade_grade_detail": "结构虽然好看，但历史延续率还不够，先别急着动手。",
                "model_ready": True,
                "model_win_probability": 0.24,
                "risk_reward_ready": True,
                "signal_side": "long",
            },
        ],
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                        "trade_grade": "可轻仓试仓",
                        "trade_grade_source": "structure",
                        "signal_side": "short",
                        "risk_reward_direction": "bearish",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.0,
                        "risk_reward_stop_price": 4815.0,
                        "risk_reward_target_price": 4765.0,
                        "risk_reward_entry_zone_low": 4792.0,
                        "risk_reward_entry_zone_high": 4800.0,
                        "atr14": 10.0,
                        "alert_state_text": "点差偏宽观察",
                        "alert_state_detail": "当前点差仍偏宽，先继续观察别急着追。",
                        "event_note": "高影响窗口：美国 CPI 将于 20:30 落地，当前品种先别抢第一脚。",
                    }
                ]
            }
        )

        assert "自动试仓状态：当前未开仓" in panel.lbl_entry_status.text()
        assert "方向：XAUUSD 做空" in panel.lbl_entry_status.text()
        assert "现价：4788.00" in panel.lbl_entry_status.text()
        assert "执行区：4792.00 - 4800.00" in panel.lbl_entry_status.text()
        assert "盈亏比：2.00R" in panel.lbl_entry_status.text()
        assert "执行位：观察区 4792.00 - 4800.00" in panel.lbl_entry_status.text()
        assert "点差状态：点差偏宽观察；当前点差仍偏宽，先继续观察别急着追。" in panel.lbl_entry_status.text()
        assert "事件纪律：高影响窗口：美国 CPI 将于 20:30 落地，当前品种先别抢第一脚。" in panel.lbl_entry_status.text()
        assert "拦截原因：" in panel.lbl_entry_status.text()
        assert "价格尚未回到可执行观察区间附近" in panel.lbl_entry_status.text()
        assert "试仓阻塞审计：" in panel.lbl_entry_audit.text()
        assert "本轮阻塞：" in panel.lbl_entry_audit.text()
        assert "最近48小时阻塞：" in panel.lbl_entry_audit.text()
        assert "事件窗口 1次" in panel.lbl_entry_audit.text()
        assert "模型胜率低 1次" in panel.lbl_entry_audit.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_recent_execution_audit_summary(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels.summarize_execution_audits",
        lambda hours=48, symbol="", **_kwargs: {
            "total_count": 6,
            "counts": {
                "opened": 2,
                "blocked": 3,
                "rejected": 1,
            },
        },
    )
    monkeypatch.setattr(
        "ui_panels.summarize_execution_reason_counts",
        lambda hours=48, symbol="", limit=3, **_kwargs: [
            {"reason_key": "existing_position", "reason_text": "已有活跃持仓，跳过自动试仓", "count": 2},
            {"reason_key": "margin_insufficient", "reason_text": "可用保证金不足", "count": 1},
        ],
    )
    monkeypatch.setattr(
        "ui_panels.fetch_recent_execution_audits",
        lambda hours=48, symbol="", limit=4, **_kwargs: [
            {
                "occurred_at": "2026-04-22 10:18:00",
                "symbol": "XAUUSD",
                "action": "short",
                "decision_status": "opened",
                "reason_key": "opened",
                "reason_text": "成功开仓 0.10 手 XAUUSD",
            },
            {
                "occurred_at": "2026-04-22 10:12:00",
                "symbol": "XAUUSD",
                "action": "short",
                "decision_status": "rejected",
                "reason_key": "margin_insufficient",
                "reason_text": "可用保证金不足",
            },
        ],
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "last_refresh_text": "2026-04-22 10:00:00",
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                        "trade_grade": "可轻仓试仓",
                        "trade_grade_source": "structure",
                        "signal_side": "short",
                        "risk_reward_direction": "bearish",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.0,
                        "risk_reward_stop_price": 4815.0,
                        "risk_reward_target_price": 4765.0,
                        "risk_reward_entry_zone_low": 4792.0,
                        "risk_reward_entry_zone_high": 4800.0,
                    }
                ],
            }
        )

        assert "最近48小时执行：已尝试 6 次 | 开仓 2 次 | 拒绝 1 次 | 阻塞 3 次" in panel.lbl_entry_audit.text()
        assert "主要阻断：已有持仓 2次 | 保证金不足 1次" in panel.lbl_entry_audit.text()
        assert "最近执行明细：" in panel.lbl_entry_trace.text()
        assert "04-22 10:18 已开仓 XAUUSD 做空" in panel.lbl_entry_trace.text()
        assert "04-22 10:12 执行拒绝 XAUUSD 做空 · 保证金不足" in panel.lbl_entry_trace.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_latest_no_open_reason_bar(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels.replay_exploratory_grade_gate",
        lambda **_kwargs: {
            "scanned_count": 25,
            "released_count": 4,
            "policy_accepted_count": 1,
            "top_still_blocked_labels": [{"reason_label": "未到试仓级别", "count": 21}],
            "top_grade_gate_secondary_labels": [{"reason_label": "未回到执行区", "count": 11}],
            "top_rr_not_ready_tertiary_labels": [],
            "top_no_direction_components": [],
            "summary_text": "观察复盘说明",
        },
    )
    monkeypatch.setattr(
        "ui_panels.build_rule_sim_signal_decision",
        lambda snapshot, allow_exploratory=False: (None, "价格尚未回到可执行观察区间附近，继续等回踩。"),
    )
    monkeypatch.setattr(
        "ui_panels.audit_rule_sim_signal_decision",
        lambda snapshot, allow_exploratory=False: {
            "ready_count": 0,
            "blocked_summary": [{"reason_label": "未到执行区", "count": 2}],
        },
    )
    monkeypatch.setattr(
        "ui_panels.fetch_recent_execution_audits",
        lambda hours=48, symbol="", limit=6, **_kwargs: [
            {
                "occurred_at": "2026-04-22 10:12:00",
                "symbol": "XAUUSD",
                "action": "long",
                "decision_status": "blocked",
                "reason_key": "exploratory_cooldown",
                "reason_text": "同向冷却内暂停重复试错",
            },
        ],
    )
    monkeypatch.setattr(
        "ui_panels.summarize_today_execution_audits",
        lambda **_kwargs: {"total_count": 0, "counts": {}, "reason_counts": {}},
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "last_refresh_text": "2026-04-22 10:00:00",
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                        "trade_grade": "只适合观察",
                        "trade_grade_source": "structure",
                        "signal_side": "neutral",
                        "risk_reward_direction": "bullish",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.0,
                        "risk_reward_stop_price": 4765.0,
                        "risk_reward_target_price": 4828.0,
                        "risk_reward_entry_zone_low": 4776.0,
                        "risk_reward_entry_zone_high": 4782.0,
                        "multi_timeframe_alignment": "aligned",
                        "multi_timeframe_bias": "bullish",
                    }
                ],
            }
        )

        text = panel.lbl_latest_no_open_reason.text()
        assert "最近未开仓：" in text
        assert "本轮主要拦截 未到执行区" in text
        assert "最近留痕 04-22 10:12 探索冷却" in text
        assert panel.lbl_latest_no_open_reason.toolTip() == text
        assert "24h观察复盘：观察级别 25 | 可释放 4 | 预计执行 1 | 主阻因 未到试仓级别 21 | 次阻因 未回到执行区 11" in panel.lbl_grade_gate_focus.text()
        assert panel.lbl_grade_gate_focus.toolTip() == "观察复盘说明"
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_today_execution_summary(monkeypatch):
    from datetime import datetime

    app = QApplication.instance() or QApplication([])
    today = datetime.now().strftime("%Y-%m-%d")

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1036.5,
                "equity": 1036.5,
                "total_profit": 36.5,
                "used_margin": 0.0,
                "win_count": 1,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return [
                        {
                            "symbol": "XAUUSD",
                            "action": "long",
                            "exit_price": 4818.55,
                            "profit": 36.5,
                            "closed_at": f"{today} 10:20:00",
                            "reason": "目标1止盈",
                            "execution_profile": "exploratory",
                        }
                    ]

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels.summarize_today_execution_audits",
        lambda **_kwargs: {
            "total_count": 5,
            "counts": {"opened": 1, "closed": 1, "rejected": 1, "blocked": 2},
            "reason_counts": {"exploratory_cooldown": 2, "exploratory_daily_limit": 1, "grade_gate": 4},
        },
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data()

        text = panel.lbl_today_execution.text()
        assert "今日实际执行：" in text
        assert "开仓 1" in text
        assert "平仓 1" in text
        assert "拒绝 1" in text
        assert "阻塞 2" in text
        assert "冷却 2" in text
        assert "上限 1" in text
        assert "观察级别 4" in text
        assert "成交 1 笔（盈 1 / 亏 0 / 平 0）" in text
        assert "净盈亏 +$36.50" in text
        assert panel.lbl_today_execution.toolTip() == text
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_strategy_learning_summary(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr("ui_panels.fetch_recent_execution_audits", lambda **_kwargs: [])
    monkeypatch.setattr("ui_panels.summarize_today_execution_audits", lambda **_kwargs: {"counts": {}, "reason_counts": {}})
    monkeypatch.setattr(
        "ui_panels._load_latest_strategy_apply_summary",
        lambda: {
            "text": "最近调参：04-23 10:15 回调狙击 最小 RR 已由 1.45 调整为 1.60；日上限已由 3 调整为 2；冷却已由 10 分钟调整为 15 分钟",
            "tooltip": "最近调参：04-23 10:15 回调狙击 最小 RR 已由 1.45 调整为 1.60；日上限已由 3 调整为 2；冷却已由 10 分钟调整为 15 分钟",
            "tone": "info",
            "updated_at": "2026-04-23 10:15:00",
        },
    )
    monkeypatch.setattr(
        "ui_panels._load_recent_strategy_apply_board",
        lambda limit=3: {
            "text": "调参看板：04-23 10:15 回调狙击 RR 1.45→1.60 / 上限 3→2 / 冷却 10→15m | 04-22 21:40 方向试仓 RR 1.80→1.95 | 04-21 08:10 直线动能 RR 1.40→1.50",
            "tooltip": "最近三次人工调参：\n04-23 10:15 回调狙击 最小 RR 已由 1.45 调整为 1.60；日上限已由 3 调整为 2；冷却已由 10 分钟调整为 15 分钟\n04-22 21:40 方向试仓 最小 RR 已由 1.80 调整为 1.95\n04-21 08:10 直线动能 最小 RR 已由 1.40 调整为 1.50",
            "tone": "info",
        },
    )
    monkeypatch.setattr(
        "ui_panels.summarize_trade_learning_by_strategy",
        lambda **_kwargs: {
            "days": 7,
            "total_count": 3,
            "rows": [
                {
                    "strategy_family": "pullback_sniper_probe",
                    "total_count": 2,
                    "win_count": 1,
                    "loss_count": 1,
                    "open_or_mixed_count": 0,
                    "win_rate": 50.0,
                    "net_profit": 9.0,
                    "avg_rr": 1.6,
                },
                {
                    "strategy_family": "directional_probe",
                    "total_count": 1,
                    "win_count": 0,
                    "loss_count": 0,
                    "open_or_mixed_count": 1,
                    "win_rate": 0.0,
                    "net_profit": 0.0,
                    "avg_rr": 2.0,
                },
            ],
        },
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data()

        text = panel.lbl_strategy_learning.text()
        assert "策略学习：" in text
        assert "回调狙击 2笔 50%" in text
        assert "方向试仓 1笔 待收盘" in text
        assert "净盈亏 +$9.00" in panel.lbl_strategy_learning.toolTip()
        assert "策略参数：" in panel.lbl_strategy_params.text()
        assert "回调狙击 1.45R" in panel.lbl_strategy_params.text()
        assert "方向试仓 1.80R" in panel.lbl_strategy_params.text()
        assert "回调狙击：1.45R / 日上限 3 次 / 冷却 10 分钟" in panel.lbl_strategy_params.toolTip()
        assert "最近调参：04-23 10:15" in panel.lbl_strategy_apply.text()
        assert "日上限已由 3 调整为 2" in panel.lbl_strategy_apply.text()
        assert "调参看板：04-23 10:15 回调狙击 RR 1.45→1.60" in panel.lbl_strategy_apply_board.text()
        assert "04-22 21:40 方向试仓 RR 1.80→1.95" in panel.lbl_strategy_apply_board.text()
        assert "调参影响：最近一次调参后还没有新的已平仓样本。" in panel.lbl_strategy_apply_impact.text()
        assert "策略分组：最近还没有可用于调参对比的已平仓样本。" in panel.lbl_strategy_apply_family_impact.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_strategy_tuning_suggestion(monkeypatch):
    from datetime import datetime

    app = QApplication.instance() or QApplication([])

    panel = SimTradingPanel()
    try:
        panel._strategy_learning_cache_time = datetime.min
        monkeypatch.setattr(
            "ui_panels.summarize_trade_learning_by_strategy",
            lambda **_kwargs: {
                "days": 7,
                "total_count": 4,
                "rows": [
                    {
                        "strategy_family": "pullback_sniper_probe",
                        "total_count": 4,
                        "win_count": 1,
                        "loss_count": 3,
                        "open_or_mixed_count": 0,
                        "win_rate": 25.0,
                        "net_profit": -18.0,
                        "avg_rr": 1.5,
                    }
                ],
            },
        )

        panel._refresh_strategy_learning_summary()

        assert "建议收紧回调狙击" in panel.lbl_strategy_learning.text()
        assert "先人工确认，不自动修改配置" in panel.lbl_strategy_learning.toolTip()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_no_recent_strategy_apply_when_absent(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr("ui_panels.fetch_recent_execution_audits", lambda **_kwargs: [])
    monkeypatch.setattr("ui_panels.summarize_today_execution_audits", lambda **_kwargs: {"counts": {}, "reason_counts": {}})
    monkeypatch.setattr("ui_panels.summarize_trade_learning_by_strategy", lambda **_kwargs: {"days": 7, "total_count": 0, "rows": []})
    monkeypatch.setattr(
        "ui_panels._load_latest_strategy_apply_summary",
        lambda: {"text": "最近调参：暂无人工批准记录。", "tooltip": "最近调参：暂无人工批准记录。", "tone": "neutral", "updated_at": ""},
    )
    monkeypatch.setattr(
        "ui_panels._load_recent_strategy_apply_board",
        lambda limit=3: {"text": "调参看板：暂无最近三次人工调参记录。", "tooltip": "调参看板：暂无最近三次人工调参记录。", "tone": "neutral"},
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data()

        assert panel.lbl_strategy_apply.text() == "最近调参：暂无人工批准记录。"
        assert panel.lbl_strategy_apply.toolTip() == "最近调参：暂无人工批准记录。"
        assert panel.lbl_strategy_apply_board.text() == "调参看板：暂无最近三次人工调参记录。"
        assert panel.lbl_strategy_apply_impact.text() == "调参影响：等待最近一次带时间戳的人工调参记录。"
        assert panel.lbl_strategy_apply_family_impact.text() == "策略分组：等待最近一次带时间戳的人工调参记录。"
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_shows_execution_model_pending_text_when_samples_insufficient(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr("ui_panels.fetch_recent_execution_audits", lambda **_kwargs: [])
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                        "trade_grade": "可轻仓试仓",
                        "trade_grade_source": "structure",
                        "signal_side": "short",
                        "risk_reward_direction": "bearish",
                        "risk_reward_ready": True,
                        "risk_reward_ratio": 2.0,
                        "risk_reward_stop_price": 4815.0,
                        "risk_reward_target_price": 4765.0,
                        "risk_reward_entry_zone_low": 4792.0,
                        "risk_reward_entry_zone_high": 4800.0,
                    }
                ],
            }
        )

        assert "执行模型：历史执行样本仍在积累" in panel.lbl_entry_status.text()
        assert "最近48小时还没有新的真实执行留痕" in panel.lbl_entry_trace.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_displays_history_exit_type(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 980.0,
                "equity": 980.0,
                "total_profit": 12.4,
                "used_margin": 0.0,
                "win_count": 1,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return [
                        {
                            "symbol": "XAUUSD",
                            "action": "long",
                            "exit_price": 4818.55,
                            "profit": 36.5,
                            "closed_at": "2026-04-17 10:20:00",
                            "reason": "回撤至保本止损",
                            "execution_profile": "exploratory",
                            "strategy_family": "pullback_sniper_probe",
                            "strategy_param_json": "{\"strategy_family\":\"pullback_sniper_probe\",\"execution_profile\":\"exploratory\",\"min_rr\":1.45,\"daily_limit\":3,\"cooldown_min\":10}",
                        }
                    ]

            return _Rows()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr(
        "ui_panels._load_latest_strategy_apply_summary",
        lambda: {
            "text": "最近调参：04-16 09:00 回调狙击 最小 RR 已由 1.45 调整为 1.60；日上限已由 3 调整为 2；冷却已由 10 分钟调整为 15 分钟",
            "tooltip": "最近调参：04-16 09:00 回调狙击 最小 RR 已由 1.45 调整为 1.60；日上限已由 3 调整为 2；冷却已由 10 分钟调整为 15 分钟",
            "tone": "info",
            "updated_at": "2026-04-16 09:00:00",
        },
    )
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_strategy_min_rr": {"pullback_sniper_probe": 1.60},
            "sim_strategy_daily_limit": {"pullback_sniper_probe": 2},
            "sim_strategy_cooldown_min": {"pullback_sniper_probe": 15},
        },
    )()
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)

    panel = SimTradingPanel()
    try:
        panel.update_data()

        assert panel.tbl_history.columnCount() == 8
        assert panel.tbl_history.item(0, 0).text() == "XAUUSD"
        assert panel.tbl_history.item(0, 1).text() == "做多 / 探索"
        assert panel.tbl_history.item(0, 2).text() == "回调狙击"
        assert panel.tbl_history.item(0, 4).text() == "+$36.50"
        assert panel.tbl_history.item(0, 5).text() == "保本"
        assert panel.tbl_history.item(0, 6).text() == "04-17 10:20"
        assert panel.tbl_history.item(0, 7).text() == "回撤至保本止损"
        assert "命中参数：回调狙击 / 探索" in panel.tbl_history.item(0, 2).toolTip()
        assert "最小 RR：1.45R" in panel.tbl_history.item(0, 2).toolTip()
        assert "RR：当前 1.60R（较当时 +0.15R）" in panel.tbl_history.item(0, 2).toolTip()
        assert "日上限：当前 2 次（较当时 -1）" in panel.tbl_history.item(0, 2).toolTip()
        assert "冷却：当前 15 分钟（较当时 +5 分钟）" in panel.tbl_history.item(0, 2).toolTip()
        assert "按平仓时间看，这笔单发生在最近一次调参后" in panel.tbl_history.item(0, 2).toolTip()
        assert "最近成交参数快照" in panel.lbl_sim_balance_hint.toolTip()
        assert "调参影响：调参后 1 笔 / 胜率 100% / 净盈亏 +36.50" in panel.lbl_strategy_apply_impact.text()
        assert "策略分组：回调狙击 后1笔 100% +36.50 / 前0笔 0% +0.00" in panel.lbl_strategy_apply_family_impact.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_refreshes_grade_gate_focus_in_background_when_visible(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    started = {"called": False}
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
            "sim_exploratory_daily_limit": 3,
            "sim_exploratory_cooldown_min": 10,
        },
    )()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)
    monkeypatch.setattr("ui_panels.summarize_today_execution_audits", lambda **_kwargs: {"counts": {}, "reason_counts": {}})
    monkeypatch.setattr("ui_panels.fetch_recent_execution_audits", lambda **_kwargs: [])
    monkeypatch.setattr("ui_panels.summarize_execution_audits", lambda **_kwargs: {"total_count": 0, "counts": {}})
    monkeypatch.setattr("ui_panels.summarize_execution_reason_counts", lambda **_kwargs: [])

    panel = SimTradingPanel()
    try:
        panel.show()
        app.processEvents()

        def fake_start(worker):
            started["called"] = True

        monkeypatch.setattr(panel, "_start_grade_gate_focus_worker", fake_start)
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                    }
                ]
            }
        )

        assert started["called"] is True
        assert "正在后台整理观察级别样本" in panel.lbl_grade_gate_focus.text()
    finally:
        panel.close()
        app.processEvents()


def test_sim_trading_panel_refreshes_strategy_insights_in_background_when_visible(monkeypatch):
    app = QApplication.instance() or QApplication([])

    class _FakeSimEngine:
        db_file = "C:/not-used.sqlite"

        @staticmethod
        def get_account(user_id: str = "system"):
            return {
                "balance": 1000.0,
                "equity": 1000.0,
                "total_profit": 0.0,
                "used_margin": 0.0,
                "win_count": 0,
                "loss_count": 0,
            }

        @staticmethod
        def get_open_positions(user_id: str = "system"):
            return []

        @staticmethod
        def _calculate_margin_and_pnl(symbol, lots, entry_price, current_price, is_long, usdjpy_rate=150.0):
            return 0.0, 0.0

    class _FakeConn:
        row_factory = None

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def close(self):
            return None

        def execute(self, _sql):
            class _Rows:
                @staticmethod
                def fetchall():
                    return []

            return _Rows()

    started = {"strategy": False}
    fake_config = type(
        "Cfg",
        (),
        {
            "sim_initial_balance": 1000.0,
            "sim_no_tp2_lock_r": 0.4,
            "sim_no_tp2_partial_close_ratio": 0.35,
            "sim_min_rr": 1.6,
            "sim_relaxed_rr": 1.3,
            "sim_model_min_probability": 0.68,
            "sim_exploratory_daily_limit": 3,
            "sim_exploratory_cooldown_min": 10,
            "sim_strategy_min_rr": {},
            "sim_strategy_daily_limit": {},
            "sim_strategy_cooldown_min": {},
        },
    )()

    monkeypatch.setattr("mt5_sim_trading.SIM_ENGINE", _FakeSimEngine)
    monkeypatch.setattr("sqlite3.connect", lambda *_args, **_kwargs: _FakeConn())
    monkeypatch.setattr("ui_panels.get_runtime_config", lambda: fake_config)
    monkeypatch.setattr("ui_panels.summarize_today_execution_audits", lambda **_kwargs: {"counts": {}, "reason_counts": {}})
    monkeypatch.setattr("ui_panels.fetch_recent_execution_audits", lambda **_kwargs: [])
    monkeypatch.setattr("ui_panels.summarize_execution_audits", lambda **_kwargs: {"total_count": 0, "counts": {}})
    monkeypatch.setattr("ui_panels.summarize_execution_reason_counts", lambda **_kwargs: [])

    panel = SimTradingPanel()
    try:
        panel.show()
        app.processEvents()

        monkeypatch.setattr(panel, "_start_grade_gate_focus_worker", lambda worker: None)

        def fake_start(worker):
            started["strategy"] = True

        monkeypatch.setattr(panel, "_start_strategy_insight_worker", fake_start)
        panel.update_data(
            snapshot={
                "items": [
                    {
                        "symbol": "XAUUSD",
                        "latest_price": 4788.0,
                        "bid": 4787.9,
                        "ask": 4788.1,
                        "has_live_quote": True,
                    }
                ]
            }
        )

        assert started["strategy"] is True
        assert "正在后台整理近7天探索样本" in panel.lbl_strategy_learning.text()
        assert "正在后台读取审批记录" in panel.lbl_strategy_apply.text()
        assert "正在后台整理最近调参记录" in panel.lbl_strategy_apply_board.text()
        assert "正在后台统计全量历史样本" in panel.lbl_strategy_apply_impact.text()
        assert "正在后台按策略族复盘调参前后" in panel.lbl_strategy_apply_family_impact.text()
    finally:
        panel.close()
        app.processEvents()
