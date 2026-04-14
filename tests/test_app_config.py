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
