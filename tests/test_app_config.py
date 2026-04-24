import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app_config


def test_normalize_symbols_filters_to_precious_and_fx():
    # 现在不再只限制特定品种，任何合法字符串都会被保留，并去重过滤空字符串
    result = app_config.normalize_symbols("BTCUSDT,XAUUSD,USDT,EURUSD,ETHUSDT,USDJPY")
    assert "XAUUSD" in result
    assert "EURUSD" in result
    assert "USDJPY" in result
    assert len(result) == 6  # 所有 6 个品种都应被保留


def test_normalize_symbols_returns_defaults_when_empty():
    assert app_config.normalize_symbols("") == app_config.DEFAULT_SYMBOLS


def test_extract_supported_symbols_can_return_empty():
    # 现在 BTCUSDT/ETHUSDT 也是合法品种，不会被清空
    # 空输入仍然返回空列表
    assert app_config.extract_supported_symbols("") == []
    assert app_config.extract_supported_symbols("   ") == []


def test_runtime_config_has_notify_defaults():
    config = app_config.get_runtime_config()
    assert hasattr(config, "dingtalk_webhook")
    assert hasattr(config, "pushplus_token")
    assert hasattr(config, "notify_cooldown_min")
    assert hasattr(config, "event_risk_mode")
    assert int(config.notify_cooldown_min) >= 5
    assert hasattr(config, "ai_api_key")
    assert hasattr(config, "ai_api_base")
    assert hasattr(config, "ai_model")
    assert hasattr(config, "ai_push_enabled")
    assert hasattr(config, "ai_push_summary_only")
    assert hasattr(config, "event_auto_mode_enabled")
    assert hasattr(config, "event_schedule_text")
    assert hasattr(config, "event_pre_window_min")
    assert hasattr(config, "event_post_window_min")
    assert hasattr(config, "event_feed_enabled")
    assert hasattr(config, "event_feed_url")
    assert hasattr(config, "event_feed_refresh_min")
    assert hasattr(config, "notify_dnd_enabled")
    assert hasattr(config, "notify_dnd_start_hour")
    assert hasattr(config, "notify_dnd_end_hour")
    assert hasattr(config, "overnight_spread_guard_enabled")
    assert hasattr(config, "overnight_spread_guard_start_hour")
    assert hasattr(config, "overnight_spread_guard_end_hour")
    assert hasattr(config, "sim_min_rr")
    assert hasattr(config, "sim_relaxed_rr")
    assert hasattr(config, "sim_model_min_probability")
    assert hasattr(config, "sim_exploratory_daily_limit")
    assert hasattr(config, "sim_exploratory_cooldown_min")
    assert hasattr(config, "live_order_precheck_only")
    assert hasattr(config, "live_max_open_positions")
    assert hasattr(config, "live_max_orders_per_day")
    assert isinstance(config.live_order_precheck_only, bool)
    assert int(config.live_max_open_positions) >= 1
    assert int(config.live_max_orders_per_day) >= 1


def test_get_quote_risk_thresholds_supports_env_override(monkeypatch):
    monkeypatch.setattr(app_config, "load_project_env", lambda: Path("."))
    monkeypatch.setenv(
        "QUOTE_RISK_THRESHOLDS_JSON",
        '{"FX":{"warn_points":31,"alert_points":46,"warn_pct":0.021,"alert_pct":0.041}}',
    )

    thresholds = app_config.get_quote_risk_thresholds("EURUSD")

    assert thresholds["warn_points"] == 31.0
    assert thresholds["alert_points"] == 46.0
    assert thresholds["warn_pct"] == 0.021
    assert thresholds["alert_pct"] == 0.041


def test_migrate_legacy_ai_and_notification_settings(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    legacy_env = tmp_path / "legacy.env"
    legacy_runtime = tmp_path / "config.json"
    env_file.write_text("", encoding="utf-8")
    legacy_env.write_text(
        "\n".join(
            [
                "AI_API_KEY='legacy-key'",
                "DINGTALK_WEBHOOK='https://example.com/hook'",
                "PUSHPLUS_TOKEN='legacy-push'",
                "MT5_SERVER='MetaQuotes-Demo'",
            ]
        ),
        encoding="utf-8",
    )
    legacy_runtime.write_text(
        '{"AI_BASE_URL":"https://api.siliconflow.cn/v1","AI_MODEL":"deepseek-ai/DeepSeek-R1","TARGET_SYMBOLS":["XAUUSD","EURUSD"]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.setattr(app_config, "LEGACY_ENV_FILE", legacy_env)
    monkeypatch.setattr(app_config, "LEGACY_RUNTIME_CONFIG", legacy_runtime)
    for key in ("AI_API_KEY", "AI_API_BASE", "AI_MODEL", "DINGTALK_WEBHOOK", "PUSHPLUS_TOKEN", "MT5_SERVER", "TARGET_SYMBOLS"):
        monkeypatch.delenv(key, raising=False)

    migrated = app_config.migrate_legacy_ai_settings_if_needed()
    assert migrated is True
    content = env_file.read_text(encoding="utf-8")
    assert "legacy-key" in content
    assert "https://api.siliconflow.cn/v1" in content
    assert "deepseek-ai/DeepSeek-R1" in content
    assert "https://example.com/hook" in content
    assert "legacy-push" in content
    assert "MetaQuotes-Demo" in content
    assert "XAUUSD,EURUSD" in content


def test_normalize_event_risk_mode_returns_safe_default():
    assert app_config.normalize_event_risk_mode("pre_event") == "pre_event"
    assert app_config.normalize_event_risk_mode("invalid-mode") == "normal"


def test_save_runtime_config_blocks_future_legacy_refill(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    legacy_env = tmp_path / "legacy.env"
    legacy_runtime = tmp_path / "config.json"
    env_file.write_text("", encoding="utf-8")
    legacy_env.write_text(
        "\n".join(
            [
                "AI_API_KEY='legacy-key'",
                "DINGTALK_WEBHOOK='https://example.com/hook'",
                "PUSHPLUS_TOKEN='legacy-push'",
                "MT5_SERVER='MetaQuotes-Demo'",
            ]
        ),
        encoding="utf-8",
    )
    legacy_runtime.write_text(
        '{"AI_BASE_URL":"https://api.siliconflow.cn/v1","AI_MODEL":"deepseek-ai/DeepSeek-R1","TARGET_SYMBOLS":["XAUUSD","EURUSD"]}',
        encoding="utf-8",
    )

    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.setattr(app_config, "LEGACY_ENV_FILE", legacy_env)
    monkeypatch.setattr(app_config, "LEGACY_RUNTIME_CONFIG", legacy_runtime)
    for key in (
        "AI_API_KEY",
        "AI_API_BASE",
        "AI_MODEL",
        "DINGTALK_WEBHOOK",
        "PUSHPLUS_TOKEN",
        "MT5_SERVER",
        "TARGET_SYMBOLS",
        app_config.LEGACY_MIGRATION_DONE_KEY,
    ):
        monkeypatch.delenv(key, raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
        )
    )

    migrated = app_config.migrate_legacy_ai_settings_if_needed()
    values = dotenv_values(str(env_file))
    assert migrated is False
    assert values.get(app_config.LEGACY_MIGRATION_DONE_KEY) == "1"
    assert values.get("DINGTALK_WEBHOOK", "") == ""


def test_save_runtime_config_uses_atomic_replace(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("CUSTOM_KEEP='yes'\n", encoding="utf-8")
    replaced = {"called": False}
    original_replace = Path.replace

    def spy_replace(self, target):
        if str(self).endswith(".tmp"):
            replaced["called"] = True
        return original_replace(self, target)

    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.setattr(Path, "replace", spy_replace)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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

    values = dotenv_values(str(env_file))
    assert replaced["called"] is True
    assert values.get("CUSTOM_KEEP") == "yes"
    assert values.get("AI_API_KEY") == "demo-key"


def test_runtime_config_supports_no_tp2_lock_settings(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    for key in (
        "SIM_NO_TP2_LOCK_R",
        "SIM_NO_TP2_PARTIAL_CLOSE_RATIO",
        app_config.LEGACY_MIGRATION_DONE_KEY,
    ):
        monkeypatch.delenv(key, raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_no_tp2_lock_r=0.75,
            sim_no_tp2_partial_close_ratio=0.25,
        )
    )

    monkeypatch.setenv("SIM_NO_TP2_LOCK_R", "0.75")
    monkeypatch.setenv("SIM_NO_TP2_PARTIAL_CLOSE_RATIO", "0.25")
    config = app_config.get_runtime_config()

    assert abs(config.sim_no_tp2_lock_r - 0.75) < 1e-9
    assert abs(config.sim_no_tp2_partial_close_ratio - 0.25) < 1e-9
    content = env_file.read_text(encoding="utf-8")
    assert "SIM_NO_TP2_LOCK_R='0.75'" in content or 'SIM_NO_TP2_LOCK_R="0.75"' in content
    assert "SIM_NO_TP2_PARTIAL_CLOSE_RATIO='0.25'" in content or 'SIM_NO_TP2_PARTIAL_CLOSE_RATIO="0.25"' in content


def test_runtime_config_supports_sim_signal_thresholds(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    for key in (
        "SIM_MIN_RR",
        "SIM_RELAXED_RR",
        "SIM_MODEL_MIN_PROBABILITY",
        "SIM_EXPLORATORY_DAILY_LIMIT",
        "SIM_EXPLORATORY_COOLDOWN_MIN",
        app_config.LEGACY_MIGRATION_DONE_KEY,
    ):
        monkeypatch.delenv(key, raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_min_rr=1.45,
            sim_relaxed_rr=1.2,
            sim_model_min_probability=0.61,
            sim_exploratory_daily_limit=4,
            sim_exploratory_cooldown_min=12,
        )
    )

    monkeypatch.setenv("SIM_MIN_RR", "1.45")
    monkeypatch.setenv("SIM_RELAXED_RR", "1.2")
    monkeypatch.setenv("SIM_MODEL_MIN_PROBABILITY", "0.61")
    monkeypatch.setenv("SIM_EXPLORATORY_DAILY_LIMIT", "4")
    monkeypatch.setenv("SIM_EXPLORATORY_COOLDOWN_MIN", "12")
    config = app_config.get_runtime_config()

    assert abs(config.sim_min_rr - 1.45) < 1e-9
    assert abs(config.sim_relaxed_rr - 1.2) < 1e-9
    assert abs(config.sim_model_min_probability - 0.61) < 1e-9
    assert config.sim_exploratory_daily_limit == 4
    assert config.sim_exploratory_cooldown_min == 12
    content = env_file.read_text(encoding="utf-8")
    assert "SIM_MIN_RR='1.45'" in content or 'SIM_MIN_RR="1.45"' in content
    assert "SIM_RELAXED_RR='1.2'" in content or 'SIM_RELAXED_RR="1.2"' in content
    assert "SIM_MODEL_MIN_PROBABILITY='0.61'" in content or 'SIM_MODEL_MIN_PROBABILITY="0.61"' in content
    assert "SIM_EXPLORATORY_DAILY_LIMIT='4'" in content or 'SIM_EXPLORATORY_DAILY_LIMIT="4"' in content
    assert "SIM_EXPLORATORY_COOLDOWN_MIN='12'" in content or 'SIM_EXPLORATORY_COOLDOWN_MIN="12"' in content


def test_runtime_config_supports_strategy_min_rr_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.delenv("SIM_STRATEGY_MIN_RR_JSON", raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_strategy_min_rr={"pullback_sniper_probe": 1.75, "directional_probe": 2.05},
        )
    )

    monkeypatch.setenv("SIM_STRATEGY_MIN_RR_JSON", '{"pullback_sniper_probe": 1.75, "directional_probe": 2.05}')
    config = app_config.get_runtime_config()

    assert abs(config.sim_strategy_min_rr["pullback_sniper_probe"] - 1.75) < 1e-9
    assert abs(config.sim_strategy_min_rr["directional_probe"] - 2.05) < 1e-9
    assert abs(config.sim_strategy_min_rr["early_momentum"] - 1.30) < 1e-9
    content = env_file.read_text(encoding="utf-8")
    assert "SIM_STRATEGY_MIN_RR_JSON=" in content


def test_runtime_config_supports_strategy_exploratory_controls_json(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.delenv("SIM_STRATEGY_DAILY_LIMIT_JSON", raising=False)
    monkeypatch.delenv("SIM_STRATEGY_COOLDOWN_JSON", raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_strategy_daily_limit={"pullback_sniper_probe": 5, "directional_probe": 2},
            sim_strategy_cooldown_min={"pullback_sniper_probe": 18, "directional_probe": 6},
        )
    )

    monkeypatch.setenv("SIM_STRATEGY_DAILY_LIMIT_JSON", '{"pullback_sniper_probe": 5, "directional_probe": 2}')
    monkeypatch.setenv("SIM_STRATEGY_COOLDOWN_JSON", '{"pullback_sniper_probe": 18, "directional_probe": 6}')
    config = app_config.get_runtime_config()

    assert config.sim_strategy_daily_limit["pullback_sniper_probe"] == 5
    assert config.sim_strategy_daily_limit["directional_probe"] == 2
    assert config.sim_strategy_daily_limit["early_momentum"] == 3
    assert config.sim_strategy_cooldown_min["pullback_sniper_probe"] == 18
    assert config.sim_strategy_cooldown_min["directional_probe"] == 6
    assert config.sim_strategy_cooldown_min["early_momentum"] == 10
    content = env_file.read_text(encoding="utf-8")
    assert "SIM_STRATEGY_DAILY_LIMIT_JSON=" in content
    assert "SIM_STRATEGY_COOLDOWN_JSON=" in content


def test_runtime_config_supports_exploratory_base_balance(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.delenv("SIM_EXPLORATORY_BASE_BALANCE", raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_exploratory_base_balance=250.0,
        )
    )

    monkeypatch.setenv("SIM_EXPLORATORY_BASE_BALANCE", "250")
    config = app_config.get_runtime_config()

    assert abs(config.sim_exploratory_base_balance - 250.0) < 1e-9
    content = env_file.read_text(encoding="utf-8")
    assert (
        "SIM_EXPLORATORY_BASE_BALANCE='250.0'" in content
        or 'SIM_EXPLORATORY_BASE_BALANCE="250.0"' in content
    )


def test_runtime_config_supports_sim_initial_balance(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(app_config, "ENV_FILE", env_file)
    monkeypatch.delenv("SIM_INITIAL_BALANCE", raising=False)

    app_config.save_runtime_config(
        app_config.MetalMonitorConfig(
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
            sim_initial_balance=100.0,
        )
    )

    monkeypatch.setenv("SIM_INITIAL_BALANCE", "100")
    config = app_config.get_runtime_config()

    assert abs(config.sim_initial_balance - 100.0) < 1e-9
    content = env_file.read_text(encoding="utf-8")
    assert "SIM_INITIAL_BALANCE='100.0'" in content or 'SIM_INITIAL_BALANCE="100.0"' in content


def test_runtime_config_invalid_ai_auto_interval_falls_back(monkeypatch):
    monkeypatch.setattr(app_config, "load_project_env", lambda: Path("."))
    monkeypatch.setenv("AI_AUTO_INTERVAL_MIN", "not-a-number")

    config = app_config.get_runtime_config()

    assert config.ai_auto_interval_min == 0
