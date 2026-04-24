import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app_config import MetalMonitorConfig
from startup_check import CHECK_FAIL, CHECK_OK, CHECK_SKIP, CHECK_WARN, run_startup_check


def _config(**overrides):
    payload = {
        "symbols": ["XAUUSD", "EURUSD"],
        "refresh_interval_sec": 30,
        "event_risk_mode": "normal",
        "mt5_path": "",
        "mt5_login": "",
        "mt5_password": "",
        "mt5_server": "",
        "dingtalk_webhook": "",
        "pushplus_token": "",
        "notify_cooldown_min": 30,
        "ai_api_key": "",
        "ai_api_base": "https://api.siliconflow.cn/v1",
        "ai_model": "deepseek-ai/DeepSeek-R1",
        "ai_push_enabled": False,
        "ai_push_summary_only": True,
        "trade_mode": "simulation",
        "live_order_precheck_only": True,
        "live_max_open_positions": 1,
        "live_max_orders_per_day": 3,
        "live_max_drawdown_pct": 0.05,
    }
    payload.update(overrides)
    return MetalMonitorConfig(**payload)


def _summary(rule_count=3):
    return {
        "rule_count": rule_count,
        "summary_text": f"知识库可访问，候选规则 {rule_count} 条。",
    }


def _item(report, key):
    for item in report["items"]:
        if item["key"] == key:
            return item
    raise AssertionError(f"missing startup check item: {key}")


def test_startup_check_passes_core_personal_workbench(tmp_path):
    report = run_startup_check(
        config=_config(dingtalk_webhook="https://example.com/hook", ai_api_key="key"),
        mt5_probe=lambda symbols: (True, f"MT5 OK: {','.join(symbols)}"),
        knowledge_summary_loader=lambda: _summary(rule_count=5),
        runtime_dir=tmp_path,
    )

    assert report["overall_status"] == CHECK_OK
    assert report["counts"][CHECK_FAIL] == 0
    assert _item(report, "mt5.connection")["status"] == CHECK_OK
    assert _item(report, "ai.config")["status"] == CHECK_OK


def test_startup_check_warns_when_ai_missing_and_no_notification(tmp_path):
    report = run_startup_check(
        config=_config(),
        mt5_probe=lambda _symbols: (True, "connected"),
        knowledge_summary_loader=lambda: _summary(rule_count=3),
        runtime_dir=tmp_path,
    )

    assert report["overall_status"] == CHECK_WARN
    assert _item(report, "notification.channels")["status"] == CHECK_WARN
    assert _item(report, "ai.config")["status"] == CHECK_SKIP


def test_startup_check_blocks_when_mt5_fails(tmp_path):
    report = run_startup_check(
        config=_config(),
        mt5_probe=lambda _symbols: (False, "terminal not found"),
        knowledge_summary_loader=lambda: _summary(rule_count=3),
        runtime_dir=tmp_path,
    )

    assert report["overall_status"] == CHECK_FAIL
    assert _item(report, "mt5.connection")["status"] == CHECK_FAIL
    assert "terminal not found" in _item(report, "mt5.connection")["detail"]


def test_startup_check_flags_live_without_precheck(tmp_path):
    report = run_startup_check(
        config=_config(trade_mode="live", live_order_precheck_only=False),
        mt5_probe=lambda _symbols: (True, "connected"),
        knowledge_summary_loader=lambda: _summary(rule_count=3),
        runtime_dir=tmp_path,
    )

    assert report["overall_status"] == CHECK_FAIL
    live_item = _item(report, "trade.live_safety")
    assert live_item["status"] == CHECK_FAIL
    assert "真实订单" in live_item["detail"]


def test_startup_check_warns_when_knowledge_has_no_rules(tmp_path):
    report = run_startup_check(
        config=_config(),
        mt5_probe=lambda _symbols: (True, "connected"),
        knowledge_summary_loader=lambda: _summary(rule_count=0),
        runtime_dir=tmp_path,
    )

    assert report["overall_status"] == CHECK_WARN
    assert _item(report, "knowledge.db")["status"] == CHECK_WARN


def test_startup_check_reports_broker_symbol_mapping(tmp_path, monkeypatch):
    monkeypatch.setenv("BROKER_SYMBOL_MAP_JSON", '{"XAUUSD":"GOLD"}')

    report = run_startup_check(
        config=_config(dingtalk_webhook="https://example.com/hook", ai_api_key="key"),
        mt5_probe=lambda _symbols: (True, "connected"),
        knowledge_summary_loader=lambda: _summary(rule_count=3),
        runtime_dir=tmp_path,
    )

    item = _item(report, "broker.symbol_map")
    assert item["status"] == CHECK_OK
    assert "XAUUSD->GOLD" in item["detail"]
