import os
import json
import queue
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

import ui


def test_detect_opportunity_prefers_lightweight_opportunity_score():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "opportunity_action": "long",
                "opportunity_push_level": "push",
                "opportunity_score": 82,
                "risk_reward_ready": False,
                "risk_reward_ratio": 0.0,
            }
        ]
    }

    assert ui._detect_opportunity(snapshot) is True


def test_detect_opportunity_keeps_legacy_rr_fallback():
    snapshot = {
        "items": [
            {
                "symbol": "XAUUSD",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.1,
            }
        ]
    }

    assert ui._detect_opportunity(snapshot) is True


def test_build_execution_funnel_payload_reports_ready_path():
    payload = ui._build_execution_funnel_payload(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "risk_reward_ready": True,
                    "signal_side": "long",
                    "risk_reward_ratio": 2.1,
                    "risk_reward_stop_price": 3300.0,
                    "risk_reward_target_price": 3340.0,
                    "risk_reward_entry_zone_low": 3310.0,
                    "risk_reward_entry_zone_high": 3315.0,
                    "latest_price": 3312.0,
                    "bid": 3311.9,
                    "ask": 3312.1,
                }
            ]
        },
        {"status_text": "已完成", "action_text": "做多", "push_text": "已推送"},
    )

    assert payload["live_count"] == 1
    assert payload["structure_count"] == 1
    assert payload["rr_ready_count"] == 1
    assert payload["direction_ready_count"] == 1
    assert payload["sim_ready_count"] == 1
    assert payload["tone"] == "success"
    assert "自动试仓就绪 1" in payload["text"]
    assert "AI链路：已完成 | 最近方向：做多 | 推送：已推送" in payload["text"]


def test_build_execution_funnel_payload_reports_structure_gate():
    payload = ui._build_execution_funnel_payload(
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "risk",
                    "risk_reward_ready": False,
                    "signal_side": "neutral",
                }
            ]
        },
        {"status_text": "待命", "action_text": "观望", "push_text": "未发生"},
    )

    assert payload["live_count"] == 1
    assert payload["structure_count"] == 0
    assert payload["sim_ready_count"] == 0
    assert payload["tone"] == "accent"
    assert "当前没有结构放行的候选" in payload["text"]


def test_build_trade_grade_display_text_marks_portfolio_grade_and_execution_block():
    text = ui._build_trade_grade_display_text(
        {
            "trade_grade": "可轻仓试仓",
            "trade_grade_detail": "XAGUSD 当前执行面相对干净，可作为候选机会。",
            "trade_next_review": "10 分钟后复核。",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "risk_reward_ready": False,
                    "signal_side": "neutral",
                },
                {
                    "symbol": "XAGUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "risk_reward_ready": False,
                    "signal_side": "neutral",
                },
            ],
        },
        trade_mode="simulation",
    )

    assert "组合分级：可轻仓试仓" in text
    assert "自动试仓：未就绪" in text
    assert "当前拦截" in text


def test_build_trade_grade_display_text_marks_execution_ready():
    text = ui._build_trade_grade_display_text(
        {
            "trade_grade": "可轻仓试仓",
            "trade_grade_detail": "XAUUSD 当前执行面相对干净，可作为候选机会。",
            "trade_next_review": "10 分钟后复核。",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "has_live_quote": True,
                    "trade_grade": "可轻仓试仓",
                    "trade_grade_source": "structure",
                    "risk_reward_ready": True,
                    "signal_side": "long",
                    "risk_reward_ratio": 2.1,
                    "risk_reward_stop_price": 3300.0,
                    "risk_reward_target_price": 3340.0,
                    "risk_reward_entry_zone_low": 3310.0,
                    "risk_reward_entry_zone_high": 3315.0,
                    "latest_price": 3312.0,
                    "bid": 3311.9,
                    "ask": 3312.1,
                }
            ],
        },
        trade_mode="simulation",
    )

    assert "组合分级：可轻仓试仓" in text
    assert "自动试仓：已就绪" in text
    assert "XAUUSD 做多" in text


def test_process_snapshot_side_effects_runs_io_chain(monkeypatch):
    captured = {
        "quotes": None,
        "notified": None,
        "sim_exec": None,
        "audit": None,
    }

    def fake_record_snapshot(snapshot):
        assert snapshot["last_refresh_text"] == "2026-04-13 10:00:00"
        return {
            "inserted_count": 1,
            "inserted_snapshot_ids": [101],
            "snapshot_bindings": {"XAUUSD": 101},
        }

    def fake_build_snapshot_history_entries(snapshot):
        return [{"symbol": "XAUUSD", "title": "结构候选"}]

    def fake_append_history_entries(entries):
        assert len(entries) == 1
        return 1

    def fake_send_notifications(entries, config):
        captured["notified"] = {
            "entries": list(entries),
            "cooldown": getattr(config, "notify_cooldown_min", None),
        }
        return {
            "messages": ["已发送 1 条提醒"],
            "errors": [],
        }

    def fake_update_prices(quotes):
        captured["quotes"] = quotes

    def fake_get_open_positions():
        return []

    def fake_execute_signal(meta):
        captured["sim_exec"] = dict(meta)
        return True, "ok"

    def fake_record_execution_audit(**kwargs):
        captured["audit"] = dict(kwargs)
        return {"audit_id": 1}

    monkeypatch.setattr(ui, "record_snapshot", fake_record_snapshot)
    monkeypatch.setattr(ui, "build_snapshot_history_entries", fake_build_snapshot_history_entries)
    monkeypatch.setattr(ui, "append_history_entries", fake_append_history_entries)
    monkeypatch.setattr(ui, "send_notifications", fake_send_notifications)
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", fake_update_prices)
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", fake_get_open_positions)
    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", fake_execute_signal)
    monkeypatch.setattr(ui, "record_execution_audit", fake_record_execution_audit)

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 3310.2,
                "bid": 3310.1,
                "ask": 3310.3,
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "signal_side": "long",
                "risk_reward_ready": True,
                "risk_reward_ratio": 2.2,
                "risk_reward_stop_price": 3299.0,
                "risk_reward_target_price": 3335.0,
                "risk_reward_entry_zone_low": 3308.0,
                "risk_reward_entry_zone_high": 3312.0,
            }
        ],
    }
    config = SimpleNamespace(notify_cooldown_min=30)

    result = ui.process_snapshot_side_effects(snapshot, config, run_backtest=False)

    assert result["snapshot_inserted_count"] == 1
    assert result["snapshot_ids"] == [101]
    assert result["snapshot_bindings"] == {"XAUUSD": 101}
    assert result["refresh_histories"] is True
    assert result["notify_status_changed"] is True
    assert result["sim_data_changed"] is True
    assert captured["quotes"] == {
        "XAUUSD": {
            "latest": 3310.2,
            "bid": 3310.1,
            "ask": 3310.3,
        }
    }
    assert captured["sim_exec"]["symbol"] == "XAUUSD"
    assert captured["sim_exec"]["action"] == "long"
    assert captured["audit"]["source_kind"] == "rule_engine"
    assert captured["audit"]["decision_status"] == "opened"
    assert captured["notified"]["cooldown"] == 30
    assert any("知识库" in line for line in result["log_lines"])
    assert any("消息推送" in line for line in result["log_lines"])
    assert any("模拟盘规则跟单" in line for line in result["log_lines"])


def test_attempt_sim_execution_fills_missing_ai_levels_from_snapshot(monkeypatch):
    captured = {}

    def fake_execute_signal(meta, user_id="system"):
        captured["meta"] = dict(meta)
        captured["user_id"] = user_id
        return True, "ok"

    def fake_record_execution_audit(**kwargs):
        captured["audit"] = dict(kwargs)
        return {"audit_id": 8}

    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", fake_execute_signal)
    monkeypatch.setattr(ui, "record_execution_audit", fake_record_execution_audit)
    monkeypatch.setattr(ui, "resolve_snapshot_binding", lambda **_kwargs: 88)

    ok, message = ui._attempt_sim_execution(
        source_kind="ai_auto",
        snapshot={
            "last_refresh_text": "2026-04-22 22:57:53",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4801.85,
                    "bid": 4801.74,
                    "ask": 4801.96,
                    "risk_reward_ready": True,
                    "risk_reward_direction": "bullish",
                    "risk_reward_stop_price": 4776.48,
                    "risk_reward_target_price": 4852.58,
                    "risk_reward_target_price_2": 4877.94,
                    "risk_reward_atr": 21.14,
                }
            ],
        },
        meta={"symbol": "XAUUSD", "action": "long"},
        signal_signature="demo-signal",
        user_id="system",
    )

    assert ok is True
    assert message == "ok"
    assert captured["meta"]["price"] == 4801.96
    assert captured["meta"]["sl"] == 4776.48
    assert captured["meta"]["tp"] == 4852.58
    assert captured["meta"]["tp2"] == 4877.94
    assert captured["meta"]["snapshot_id"] == 88
    assert captured["audit"]["source_kind"] == "ai_auto"
    assert captured["audit"]["decision_status"] == "opened"
    assert captured["audit"]["signal_signature"] == "demo-signal"


def test_process_snapshot_side_effects_runs_exploratory_sim_candidate(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        ui,
        "record_snapshot",
        lambda snapshot: {
            "inserted_count": 1,
            "inserted_snapshot_ids": [21110],
            "snapshot_bindings": {"XAUUSD": 21110},
        },
    )
    monkeypatch.setattr(ui, "build_snapshot_history_entries", lambda snapshot: [])
    monkeypatch.setattr(ui, "append_history_entries", lambda entries: 0)
    monkeypatch.setattr(ui, "send_notifications", lambda entries, config: {"messages": [], "errors": []})
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", lambda quotes: captured.update({"quotes": quotes}))
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", lambda: [])

    def fake_execute_signal(meta, user_id="system"):
        captured["meta"] = dict(meta)
        return True, "ok"

    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", fake_execute_signal)
    monkeypatch.setattr(ui, "record_execution_audit", lambda **kwargs: captured.update({"audit": dict(kwargs)}) or {"audit_id": 9})
    monkeypatch.setattr(ui, "_exploratory_cooldown_active", lambda symbol="", action="", meta=None: False)
    monkeypatch.setattr(ui, "_exploratory_daily_limit_reached", lambda symbol="", meta=None: False)

    result = ui.process_snapshot_side_effects(
        {
            "last_refresh_text": "2026-04-20 22:57:53",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4801.85,
                    "bid": 4801.74,
                    "ask": 4801.96,
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "neutral",
                    "risk_reward_ready": True,
                    "risk_reward_state": "acceptable",
                    "risk_reward_ratio": 2.0,
                    "risk_reward_direction": "bullish",
                    "multi_timeframe_alignment": "aligned",
                    "multi_timeframe_bias": "bullish",
                    "risk_reward_stop_price": 4776.48,
                    "risk_reward_target_price": 4852.58,
                    "risk_reward_target_price_2": 4877.94,
                    "risk_reward_entry_zone_low": 4792.33,
                    "risk_reward_entry_zone_high": 4805.02,
                    "atr14": 21.14,
                    "risk_reward_atr": 21.14,
                }
            ],
        },
        SimpleNamespace(trade_mode="simulation", notify_cooldown_min=30),
        run_backtest=False,
    )

    assert result["sim_data_changed"] is True
    assert captured["meta"]["action"] == "long"
    assert captured["meta"]["execution_profile"] == "exploratory"
    assert captured["meta"]["strategy_family"] == "structure"
    assert captured["meta"]["snapshot_id"] == 21110
    assert captured["audit"]["source_kind"] == "rule_engine"
    assert captured["audit"]["decision_status"] == "opened"
    assert any("模拟盘规则跟单" in line for line in result["log_lines"])


def test_process_snapshot_side_effects_blocks_exploratory_when_daily_limit_reached(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        ui,
        "record_snapshot",
        lambda snapshot: {
            "inserted_count": 1,
            "inserted_snapshot_ids": [21110],
            "snapshot_bindings": {"XAUUSD": 21110},
        },
    )
    monkeypatch.setattr(ui, "build_snapshot_history_entries", lambda snapshot: [])
    monkeypatch.setattr(ui, "append_history_entries", lambda entries: 0)
    monkeypatch.setattr(ui, "send_notifications", lambda entries, config: {"messages": [], "errors": []})
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", lambda quotes: None)
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", lambda: [])
    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("达到上限后不应继续开仓")))
    monkeypatch.setattr(ui, "_exploratory_cooldown_active", lambda symbol="", action="", meta=None: False)
    monkeypatch.setattr(ui, "_exploratory_daily_limit_reached", lambda symbol="", meta=None: True)
    monkeypatch.setattr(ui, "record_execution_audit", lambda **kwargs: captured.update({"audit": dict(kwargs)}) or {"audit_id": 10})

    result = ui.process_snapshot_side_effects(
        {
            "last_refresh_text": "2026-04-20 22:57:53",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4801.85,
                    "bid": 4801.74,
                    "ask": 4801.96,
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "neutral",
                    "risk_reward_ready": True,
                    "risk_reward_state": "acceptable",
                    "risk_reward_ratio": 2.0,
                    "risk_reward_direction": "bullish",
                    "multi_timeframe_alignment": "aligned",
                    "multi_timeframe_bias": "bullish",
                    "risk_reward_stop_price": 4776.48,
                    "risk_reward_target_price": 4852.58,
                    "risk_reward_target_price_2": 4877.94,
                    "risk_reward_entry_zone_low": 4792.33,
                    "risk_reward_entry_zone_high": 4805.02,
                    "atr14": 21.14,
                    "risk_reward_atr": 21.14,
                }
            ],
        },
        SimpleNamespace(trade_mode="simulation", notify_cooldown_min=30),
        run_backtest=False,
    )

    assert captured["audit"]["decision_status"] == "blocked"
    assert captured["audit"]["reason_key"] == "exploratory_daily_limit"
    assert captured["audit"]["meta"]["execution_profile"] == "exploratory"
    assert captured["audit"]["meta"]["strategy_family"] == "structure"
    assert any("探索试仓暂停" in line for line in result["log_lines"])


def test_process_snapshot_side_effects_blocks_exploratory_when_cooldown_active(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        ui,
        "record_snapshot",
        lambda snapshot: {
            "inserted_count": 1,
            "inserted_snapshot_ids": [21110],
            "snapshot_bindings": {"XAUUSD": 21110},
        },
    )
    monkeypatch.setattr(ui, "build_snapshot_history_entries", lambda snapshot: [])
    monkeypatch.setattr(ui, "append_history_entries", lambda entries: 0)
    monkeypatch.setattr(ui, "send_notifications", lambda entries, config: {"messages": [], "errors": []})
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", lambda quotes: None)
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", lambda: [])
    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("冷却期内不应继续开仓")))
    monkeypatch.setattr(ui, "_exploratory_cooldown_active", lambda symbol="", action="", meta=None: True)
    monkeypatch.setattr(ui, "_resolve_exploratory_cooldown_min", lambda meta=None: 10)
    monkeypatch.setattr(ui, "_exploratory_daily_limit_reached", lambda symbol="", meta=None: False)
    monkeypatch.setattr(ui, "record_execution_audit", lambda **kwargs: captured.update({"audit": dict(kwargs)}) or {"audit_id": 11})

    result = ui.process_snapshot_side_effects(
        {
            "last_refresh_text": "2026-04-20 22:57:53",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4801.85,
                    "bid": 4801.74,
                    "ask": 4801.96,
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "neutral",
                    "risk_reward_ready": True,
                    "risk_reward_state": "acceptable",
                    "risk_reward_ratio": 2.0,
                    "risk_reward_direction": "bullish",
                    "multi_timeframe_alignment": "aligned",
                    "multi_timeframe_bias": "bullish",
                    "risk_reward_stop_price": 4776.48,
                    "risk_reward_target_price": 4852.58,
                    "risk_reward_target_price_2": 4877.94,
                    "risk_reward_entry_zone_low": 4792.33,
                    "risk_reward_entry_zone_high": 4805.02,
                    "atr14": 21.14,
                    "risk_reward_atr": 21.14,
                }
            ],
        },
        SimpleNamespace(trade_mode="simulation", notify_cooldown_min=30),
        run_backtest=False,
    )

    assert captured["audit"]["decision_status"] == "blocked"
    assert captured["audit"]["reason_key"] == "exploratory_cooldown"
    assert captured["audit"]["meta"]["execution_profile"] == "exploratory"
    assert captured["audit"]["meta"]["strategy_family"] == "structure"
    assert any("探索试仓冷却" in line for line in result["log_lines"])


def test_enrich_signal_does_not_fill_levels_against_snapshot_direction():
    meta = ui._enrich_signal_with_snapshot_context(
        {"symbol": "XAUUSD", "action": "short"},
        {
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4801.85,
                    "bid": 4801.74,
                    "ask": 4801.96,
                    "risk_reward_ready": True,
                    "risk_reward_direction": "bullish",
                    "risk_reward_stop_price": 4776.48,
                    "risk_reward_target_price": 4852.58,
                }
            ],
        },
    )

    assert float(meta.get("price", 0.0) or 0.0) == 0.0
    assert float(meta.get("sl", 0.0) or 0.0) == 0.0
    assert float(meta.get("tp", 0.0) or 0.0) == 0.0


def test_queue_latest_task_drops_oldest_snapshot_when_full(monkeypatch):
    task_queue = queue.Queue(maxsize=1)
    task_queue.put({"kind": "snapshot_side_effects", "snapshot": {"last_refresh_text": "old"}})
    monkeypatch.setattr(ui, "SNAPSHOT_TASK_QUEUE", task_queue)

    dropped_count = ui._queue_latest_task(
        {"kind": "snapshot_side_effects", "snapshot": {"last_refresh_text": "new"}}
    )

    assert dropped_count == 1
    queued = task_queue.get_nowait()
    assert queued["snapshot"]["last_refresh_text"] == "new"


def test_background_outbox_persists_and_marks_snapshot_task_done(tmp_path, monkeypatch):
    db_path = tmp_path / "background_outbox.sqlite"
    captured = {}

    def fake_process_snapshot_side_effects(snapshot, config, run_backtest=False):
        captured["snapshot"] = dict(snapshot)
        captured["cooldown"] = getattr(config, "notify_cooldown_min", None)
        captured["run_backtest"] = bool(run_backtest)
        return {"log_lines": ["ok"], "snapshot_ids": [7]}

    monkeypatch.setattr(ui, "process_snapshot_side_effects", fake_process_snapshot_side_effects)

    outbox_id = ui._persist_snapshot_side_effect_task(
        {"last_refresh_text": "2026-04-13 10:00:00"},
        SimpleNamespace(notify_cooldown_min=18),
        run_backtest=True,
        db_path=db_path,
    )
    task = ui._claim_background_outbox_task(db_path=db_path)
    result = ui._process_background_task(task, db_path=db_path)

    assert task["outbox_id"] == outbox_id
    assert captured == {
        "snapshot": {"last_refresh_text": "2026-04-13 10:00:00"},
        "cooldown": 18,
        "run_backtest": True,
    }
    assert result["snapshot_ids"] == [7]
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT status, attempts, last_error FROM background_outbox WHERE id=?", (outbox_id,)).fetchone()
    assert row == ("done", 1, "")


def test_background_outbox_recovers_interrupted_running_task(tmp_path):
    db_path = tmp_path / "background_outbox.sqlite"
    outbox_id = ui._persist_snapshot_side_effect_task(
        {"last_refresh_text": "2026-04-13 10:00:00"},
        SimpleNamespace(notify_cooldown_min=18),
        run_backtest=False,
        db_path=db_path,
    )
    claimed = ui._claim_background_outbox_task(db_path=db_path)
    assert claimed["outbox_id"] == outbox_id

    recovered = ui._recover_interrupted_background_outbox_tasks(db_path=db_path)
    claimed_again = ui._claim_background_outbox_task(db_path=db_path)

    assert recovered == 1
    assert claimed_again["outbox_id"] == outbox_id
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute("SELECT status, attempts FROM background_outbox WHERE id=?", (outbox_id,)).fetchone()
    assert row == ("running", 2)


def test_background_outbox_retries_failed_task_before_final_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "background_outbox.sqlite"
    outbox_id = ui._persist_snapshot_side_effect_task(
        {"last_refresh_text": "2026-04-13 10:00:00"},
        SimpleNamespace(notify_cooldown_min=18),
        run_backtest=False,
        db_path=db_path,
    )

    def fail_process(*_args, **_kwargs):
        raise RuntimeError("temporary failure")

    monkeypatch.setattr(ui, "process_snapshot_side_effects", fail_process)
    monkeypatch.setattr(ui, "BACKGROUND_OUTBOX_MAX_ATTEMPTS", 3)

    for expected_attempt, expected_status in ((1, "pending"), (2, "pending"), (3, "failed")):
        task = ui._claim_background_outbox_task(db_path=db_path)
        assert task["outbox_id"] == outbox_id
        assert task["attempts"] == expected_attempt
        with pytest.raises(RuntimeError):
            ui._process_background_task(task, db_path=db_path)
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT status, attempts, last_error FROM background_outbox WHERE id=?",
                (outbox_id,),
            ).fetchone()
        assert row[0] == expected_status
        assert row[1] == expected_attempt
        assert "temporary failure" in row[2]

def test_background_outbox_cleanup_removes_old_done_and_failed_rows(tmp_path):
    db_path = tmp_path / "background_outbox.sqlite"
    old_done_id = ui._persist_snapshot_side_effect_task({}, SimpleNamespace(), db_path=db_path)
    old_failed_id = ui._persist_snapshot_side_effect_task({}, SimpleNamespace(), db_path=db_path)
    fresh_done_id = ui._persist_snapshot_side_effect_task({}, SimpleNamespace(), db_path=db_path)

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "UPDATE background_outbox SET status='done', updated_at='2000-01-01 00:00:00' WHERE id=?",
            (old_done_id,),
        )
        conn.execute(
            "UPDATE background_outbox SET status='failed', updated_at='2000-01-01 00:00:00' WHERE id=?",
            (old_failed_id,),
        )
        conn.execute(
            "UPDATE background_outbox SET status='done', updated_at=datetime('now') WHERE id=?",
            (fresh_done_id,),
        )
        conn.commit()

    deleted = ui._cleanup_background_outbox(done_retention_days=7, failed_retention_days=30, db_path=db_path)

    assert deleted == 2
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, status FROM background_outbox ORDER BY id").fetchall()
    assert rows == [(fresh_done_id, "done")]


def test_enqueue_snapshot_side_effects_keeps_task_in_outbox_when_wakeup_queue_full(tmp_path, monkeypatch):
    db_path = tmp_path / "background_outbox.sqlite"
    task_queue = queue.Queue(maxsize=1)
    task_queue.put({"kind": "outbox_snapshot_side_effects", "outbox_id": 1})
    logs = []
    fake_self = SimpleNamespace(
        _config=SimpleNamespace(notify_cooldown_min=30),
        _last_backtest_eval_time=None,
        _append_log=lambda message: logs.append(message),
    )
    monkeypatch.setattr(ui, "BACKGROUND_OUTBOX_DB", db_path)
    monkeypatch.setattr(ui, "SNAPSHOT_TASK_QUEUE", task_queue)

    ui.MetalMonitorWindow._enqueue_snapshot_side_effects(
        fake_self,
        {"last_refresh_text": "2026-04-13 10:01:00"},
    )

    queued = task_queue.get_nowait()
    assert queued["kind"] == "outbox_snapshot_side_effects"
    assert any("outbox" in line for line in logs)
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT id, status, payload_json FROM background_outbox ORDER BY id").fetchall()
    assert len(rows) == 1
    assert queued["outbox_id"] == rows[0][0]
    assert rows[0][1] == "pending"
    payload = json.loads(rows[0][2])
    assert payload["snapshot"]["last_refresh_text"] == "2026-04-13 10:01:00"
    assert payload["config"]["notify_cooldown_min"] == 30


def test_process_snapshot_side_effects_logs_reason_when_rule_signal_not_executed(monkeypatch):
    monkeypatch.setattr(ui, "record_snapshot", lambda snapshot: {"inserted_count": 0, "inserted_snapshot_ids": [], "snapshot_bindings": {}})
    monkeypatch.setattr(ui, "build_snapshot_history_entries", lambda snapshot: [])
    monkeypatch.setattr(ui, "append_history_entries", lambda entries: 0)
    monkeypatch.setattr(ui, "send_notifications", lambda entries, config: {"messages": [], "errors": []})
    monkeypatch.setattr(ui.SIM_ENGINE, "update_prices", lambda quotes: None)
    monkeypatch.setattr(ui.SIM_ENGINE, "get_open_positions", lambda: [])
    monkeypatch.setattr(ui, "record_execution_audit", lambda **kwargs: {"audit_id": 1})

    snapshot = {
        "last_refresh_text": "2026-04-13 10:00:00",
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
            }
        ],
    }
    config = SimpleNamespace(notify_cooldown_min=30)

    result = ui.process_snapshot_side_effects(snapshot, config, run_backtest=False)

    assert any("模拟盘规则候选未执行" in line for line in result["log_lines"])


def test_on_deep_mining_ready_refreshes_pending_panel_without_event_bus():
    logs = []
    refresh_calls = []
    fake_self = SimpleNamespace(
        _deep_miner_worker=object(),
        _append_log=lambda message: logs.append(message),
        pending_panel=SimpleNamespace(load_pending_rules=lambda: refresh_calls.append("loaded")),
    )

    ui.MetalMonitorWindow._on_deep_mining_ready(
        fake_self,
        {"log_lines": ["已完成模式聚类", "新增 2 条候选规则"]},
    )

    assert fake_self._deep_miner_worker is None
    assert logs == ["已完成模式聚类", "新增 2 条候选规则"]
    assert refresh_calls == ["loaded"]


def test_on_ai_auto_brief_ready_uses_signal_meta_without_callback_crash(monkeypatch):
    logs = []
    funnel_states = []
    status_texts = []
    history_refreshes = []
    executed = []
    audits = []

    monkeypatch.setattr(ui, "send_ai_brief_notification", lambda result, snapshot, config, is_opportunity=False: {"messages": ["已推送"], "errors": []})
    monkeypatch.setattr(ui, "append_ai_history_entry", lambda entry: 1)
    monkeypatch.setattr(ui, "build_ai_history_entry", lambda result, snapshot, push_result=None: {"result": result, "snapshot": snapshot})
    monkeypatch.setattr(ui, "record_ai_signal", lambda result, snapshot, push_result=None: {"inserted_count": 1, "entry": {"signal_signature": "sig-auto-1"}})
    monkeypatch.setattr(ui, "summarize_recent_ai_signals", lambda days=30: {"summary_text": "近30天 AI 信号正常"})
    monkeypatch.setattr(ui, "resolve_snapshot_binding", lambda snapshot=None, symbol="", db_path=None: 101)
    monkeypatch.setattr(ui.SIM_ENGINE, "execute_signal", lambda meta, user_id="system": (executed.append(dict(meta)) or True, "ok"))
    monkeypatch.setattr(ui, "record_execution_audit", lambda **kwargs: audits.append(dict(kwargs)) or {"audit_id": 1})

    fake_self = SimpleNamespace(
        _ai_worker=object(),
        _ai_auto_is_running=True,
        _last_snapshot={
            "last_refresh_text": "2026-04-13 10:00:00",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 3300.0,
                    "risk_reward_stop_price": 3290.0,
                    "risk_reward_target_price": 3330.0,
                }
            ],
        },
        _config=SimpleNamespace(ai_auto_interval_min=30),
        left_panel=SimpleNamespace(
            set_ai_brief=lambda text: logs.append(f"brief:{text}"),
            refresh_histories=lambda snapshot: history_refreshes.append(snapshot),
        ),
        lbl_ai_status=SimpleNamespace(setText=lambda text: status_texts.append(text)),
        _append_log=lambda message: logs.append(message),
        _set_ai_funnel_state=lambda status, action="neutral", push_text="未发生", tone="neutral": funnel_states.append(
            {"status": status, "action": action, "push_text": push_text, "tone": tone}
        ),
        _update_notify_status=lambda snapshot: logs.append("notify-updated"),
        _update_execution_funnel=lambda snapshot: logs.append("funnel-updated"),
    )

    ui.MetalMonitorWindow._on_ai_auto_brief_ready(
        fake_self,
        {
            "content": "自动研判完成",
            "model": "demo-model",
            "signal_meta": {"symbol": "XAUUSD", "action": "long", "price": 3300, "sl": 3290, "tp": 3330},
        },
    )

    assert fake_self._ai_worker is None
    assert fake_self._ai_auto_is_running is False
    assert funnel_states[0]["action"] == "long"
    assert "已推送最新简报" in status_texts[-1]
    assert executed[0]["symbol"] == "XAUUSD"
    assert executed[0]["snapshot_id"] == 101
    assert audits[0]["source_kind"] == "ai_auto"
    assert audits[0]["decision_status"] == "opened"
    assert any("[AI自动留痕]" in line for line in logs)
    assert any("[AI自动信号]" in line for line in logs)
    assert any("[AI自动模拟跟单成功]" in line for line in logs)
    assert history_refreshes == [fake_self._last_snapshot]


def test_run_deep_mining_persists_status_report(monkeypatch):
    captured = []

    monkeypatch.setattr(
        "knowledge_miner.mine_frequent_patterns",
        lambda: {"mined_patterns": 2, "inserted_rules": 1},
    )
    monkeypatch.setattr(
        "knowledge_miner.run_llm_batch_reflection",
        lambda db_path=None, config=None: {
            "mined_patterns": 0,
            "inserted_rules": 0,
            "reflection_horizon": 30,
            "raw_candidate_count": 4,
            "prepared_candidate_count": 2,
            "quality_filtered_count": 1,
            "duplicate_skipped_count": 2,
            "duplicate_in_batch_count": 1,
            "duplicate_existing_count": 1,
        },
    )

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            captured.append((normalized, params))
            if "COUNT(*) AS total_new_24h" in normalized:
                return SimpleNamespace(fetchone=lambda: {"total_new_24h": 1})
            if "SELECT kr.rule_text, ks.source_type" in normalized:
                return SimpleNamespace(fetchone=lambda: {"rule_text": "等待回踩下沿后再试多", "source_type": "llm_golden_setup"})
            if "usable_888_count" in normalized:
                return SimpleNamespace(fetchone=lambda: {"usable_888_count": 0, "usable_30m_exec_count": 0})
            return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeConn(),
    )
    monkeypatch.setattr(ui, "send_learning_health_notification", lambda report, config: {"sent_count": 0, "messages": [], "errors": [], "report": report})

    result = ui.run_deep_mining(SimpleNamespace())

    assert result["local_inserted_rules"] == 1
    assert result["llm_inserted_rules"] == 0
    assert result["total_inserted_rules"] == 1
    insert_sql, insert_params = next((sql, params) for sql, params in captured if "INSERT INTO learning_reports" in sql)
    assert insert_params[0] == "deep_mining_status"
    payload = json.loads(insert_params[3])
    assert payload["local_inserted_rules"] == 1
    assert payload["llm_inserted_rules"] == 0
    assert payload["total_inserted_rules"] == 1
    assert payload["reflection_horizon"] == 30
    assert payload["llm_quality_filtered_count"] == 1
    assert payload["llm_duplicate_skipped_count"] == 2
    assert payload["llm_duplicate_in_batch_count"] == 1
    assert payload["llm_duplicate_existing_count"] == 1


def test_run_deep_mining_emits_learning_health_push_log(monkeypatch):
    monkeypatch.setattr(
        "knowledge_miner.mine_frequent_patterns",
        lambda: {"mined_patterns": 0, "inserted_rules": 0},
    )
    monkeypatch.setattr(
        "knowledge_miner.run_llm_batch_reflection",
        lambda db_path=None, config=None: {
            "mined_patterns": 0,
            "inserted_rules": 0,
            "reflection_horizon": 30,
            "raw_candidate_count": 4,
            "prepared_candidate_count": 2,
            "quality_filtered_count": 2,
            "duplicate_skipped_count": 0,
            "duplicate_in_batch_count": 0,
            "duplicate_existing_count": 0,
        },
    )

    class _FakeRow(dict):
        def __getitem__(self, key):
            return dict.__getitem__(self, key)

    class _FakeConn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            normalized = " ".join(str(sql).split())
            if "INSERT INTO learning_reports" in normalized:
                return SimpleNamespace(rowcount=1)
            if "COUNT(*) AS total_new_24h" in normalized:
                return SimpleNamespace(fetchone=lambda: _FakeRow({"total_new_24h": 3}))
            if "SELECT kr.rule_text, ks.source_type" in normalized:
                return SimpleNamespace(fetchone=lambda: _FakeRow({"rule_text": "等待回踩下沿后再试多", "source_type": "llm_golden_setup"}))
            if "usable_888_count" in normalized:
                return SimpleNamespace(fetchone=lambda: _FakeRow({"usable_888_count": 2, "usable_30m_exec_count": 5}))
            return SimpleNamespace(rowcount=1, fetchone=lambda: None)

    monkeypatch.setattr(
        "knowledge_base.open_knowledge_connection",
        lambda *_args, **_kwargs: _FakeConn(),
    )
    monkeypatch.setattr(
        ui,
        "send_learning_health_notification",
        lambda report, config: {"sent_count": 1, "messages": ["自动学习状态变化已投递到 1 个渠道"], "errors": []},
    )

    result = ui.run_deep_mining(SimpleNamespace())

    assert any("[学习状态推送] 自动学习状态变化已投递到 1 个渠道" in line for line in result["log_lines"])
