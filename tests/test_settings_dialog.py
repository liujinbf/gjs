import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app_config import MetalMonitorConfig
from settings_dialog import MetalSettingsDialog
from settings_dialog import _build_ai_test_request


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


def test_build_ai_test_request_uses_bearer_for_openai_compatible():
    url, headers = _build_ai_test_request("https://api.siliconflow.cn/v1", "demo-key")

    assert url == "https://api.siliconflow.cn/v1/models"
    assert headers["Authorization"] == "Bearer demo-key"
    assert "x-api-key" not in headers


def test_build_ai_test_request_uses_anthropic_headers():
    url, headers = _build_ai_test_request("https://api.anthropic.com/v1", "anthropic-key")

    assert url == "https://api.anthropic.com/v1/models"
    assert headers["x-api-key"] == "anthropic-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "Authorization" not in headers


def test_build_runtime_config_includes_sim_short_term_controls(qapp):
    dialog = MetalSettingsDialog(
        MetalMonitorConfig(
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
            ai_api_base="https://api.siliconflow.cn/v1",
            ai_model="deepseek-ai/DeepSeek-R1",
            ai_push_enabled=False,
            ai_push_summary_only=True,
            sim_initial_balance=1000.0,
            sim_exploratory_base_balance=1000.0,
            sim_no_tp2_lock_r=0.5,
            sim_no_tp2_partial_close_ratio=0.5,
            sim_min_rr=1.6,
            sim_relaxed_rr=1.3,
            sim_model_min_probability=0.68,
            sim_exploratory_daily_limit=3,
            sim_exploratory_cooldown_min=10,
            sim_strategy_min_rr={
                "early_momentum": 1.30,
                "direct_momentum": 1.40,
                "pullback_sniper_probe": 1.45,
                "directional_probe": 1.80,
            },
            sim_strategy_daily_limit={
                "early_momentum": 3,
                "direct_momentum": 3,
                "pullback_sniper_probe": 3,
                "directional_probe": 3,
            },
            sim_strategy_cooldown_min={
                "early_momentum": 10,
                "direct_momentum": 10,
                "pullback_sniper_probe": 10,
                "directional_probe": 10,
            },
        )
    )

    dialog.spin_sim_initial_balance.setValue(100.0)
    dialog.spin_sim_exploratory_base_balance.setValue(200.0)
    dialog.spin_sim_no_tp2_lock_r.setValue(0.4)
    dialog.spin_sim_no_tp2_partial_close_ratio.setValue(0.35)
    dialog.spin_sim_min_rr.setValue(1.45)
    dialog.spin_sim_rr_early_momentum.setValue(1.35)
    dialog.spin_sim_rr_direct_momentum.setValue(1.50)
    dialog.spin_sim_rr_pullback_sniper.setValue(1.70)
    dialog.spin_sim_rr_directional_probe.setValue(2.00)
    dialog.spin_sim_relaxed_rr.setValue(1.20)
    dialog.spin_sim_model_min_probability.setValue(0.61)
    dialog.spin_sim_exploratory_daily_limit.setValue(4)
    dialog.spin_sim_limit_early_momentum.setValue(5)
    dialog.spin_sim_limit_direct_momentum.setValue(4)
    dialog.spin_sim_limit_pullback_sniper.setValue(2)
    dialog.spin_sim_limit_directional_probe.setValue(1)
    dialog.spin_sim_exploratory_cooldown_min.setValue(12)
    dialog.spin_sim_cooldown_early_momentum.setValue(8)
    dialog.spin_sim_cooldown_direct_momentum.setValue(6)
    dialog.spin_sim_cooldown_pullback_sniper.setValue(15)
    dialog.spin_sim_cooldown_directional_probe.setValue(20)

    config = dialog._build_runtime_config()

    assert abs(config.sim_initial_balance - 100.0) < 1e-9
    assert abs(config.sim_exploratory_base_balance - 200.0) < 1e-9
    assert abs(config.sim_no_tp2_lock_r - 0.4) < 1e-9
    assert abs(config.sim_no_tp2_partial_close_ratio - 0.35) < 1e-9
    assert abs(config.sim_min_rr - 1.45) < 1e-9
    assert abs(config.sim_strategy_min_rr["early_momentum"] - 1.35) < 1e-9
    assert abs(config.sim_strategy_min_rr["direct_momentum"] - 1.50) < 1e-9
    assert abs(config.sim_strategy_min_rr["pullback_sniper_probe"] - 1.70) < 1e-9
    assert abs(config.sim_strategy_min_rr["directional_probe"] - 2.00) < 1e-9
    assert abs(config.sim_relaxed_rr - 1.2) < 1e-9
    assert abs(config.sim_model_min_probability - 0.61) < 1e-9
    assert config.sim_exploratory_daily_limit == 4
    assert config.sim_strategy_daily_limit["early_momentum"] == 5
    assert config.sim_strategy_daily_limit["direct_momentum"] == 4
    assert config.sim_strategy_daily_limit["pullback_sniper_probe"] == 2
    assert config.sim_strategy_daily_limit["directional_probe"] == 1
    assert config.sim_exploratory_cooldown_min == 12
    assert config.sim_strategy_cooldown_min["early_momentum"] == 8
    assert config.sim_strategy_cooldown_min["direct_momentum"] == 6
    assert config.sim_strategy_cooldown_min["pullback_sniper_probe"] == 15
    assert config.sim_strategy_cooldown_min["directional_probe"] == 20


def test_ai_key_test_uses_background_worker(qapp):
    dialog = MetalSettingsDialog(
        MetalMonitorConfig(
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
            ai_api_key="demo-key",
            ai_api_base="https://api.siliconflow.cn/v1",
            ai_model="deepseek-ai/DeepSeek-R1",
            ai_push_enabled=False,
            ai_push_summary_only=True,
        )
    )
    started = {"called": False}

    try:
        dialog.entry_ai_key.setText("demo-key")
        dialog._start_ai_key_test_worker = lambda worker: started.update({"called": True})

        dialog._test_ai_key()

        assert started["called"] is True
        assert dialog.btn_test_ai_key.isEnabled() is False
        assert "测试中" in dialog.btn_test_ai_key.text()
    finally:
        dialog.close()
        qapp.processEvents()


def test_ai_key_test_result_restores_button_and_reports_success(monkeypatch, qapp):
    dialog = MetalSettingsDialog(
        MetalMonitorConfig(
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
            ai_api_key="demo-key",
            ai_api_base="https://api.siliconflow.cn/v1",
            ai_model="deepseek-ai/DeepSeek-R1",
            ai_push_enabled=False,
            ai_push_summary_only=True,
        )
    )
    captured = {}

    try:
        dialog.btn_test_ai_key.setEnabled(False)
        dialog.btn_test_ai_key.setText("测试中...")
        monkeypatch.setattr(
            "settings_dialog.QMessageBox.information",
            lambda _parent, title, message: captured.update({"title": title, "message": message}),
        )

        dialog._on_ai_test_result({"ok": True, "title": "测试成功", "message": "通过"})

        assert dialog.btn_test_ai_key.isEnabled() is True
        assert dialog.btn_test_ai_key.text() == "测试密钥"
        assert captured["title"] == "测试成功"
    finally:
        dialog.close()
        qapp.processEvents()


def test_notification_test_uses_background_worker(qapp):
    dialog = MetalSettingsDialog(
        MetalMonitorConfig(
            symbols=["XAUUSD"],
            refresh_interval_sec=30,
            event_risk_mode="normal",
            mt5_path="",
            mt5_login="",
            mt5_password="",
            mt5_server="",
            dingtalk_webhook="https://example.invalid/hook",
            pushplus_token="",
            notify_cooldown_min=30,
            ai_api_key="",
            ai_api_base="https://api.siliconflow.cn/v1",
            ai_model="deepseek-ai/DeepSeek-R1",
            ai_push_enabled=False,
            ai_push_summary_only=True,
        )
    )
    started = {"called": False}

    try:
        dialog._start_notification_test_worker = lambda worker: started.update({"called": True})

        dialog._test_notification()

        assert started["called"] is True
        assert dialog.btn_test_notification.isEnabled() is False
        assert "测试中" in dialog.btn_test_notification.text()
    finally:
        dialog.close()
        qapp.processEvents()
