import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app_config import MetalMonitorConfig
import notification


def _build_config() -> MetalMonitorConfig:
    return MetalMonitorConfig(
        symbols=["XAUUSD", "EURUSD"],
        refresh_interval_sec=30,
        event_risk_mode="normal",
        mt5_path="",
        mt5_login="",
        mt5_password="",
        mt5_server="",
        dingtalk_webhook="https://example.com/dingtalk",
        pushplus_token="pushplus-token",
        notify_cooldown_min=30,
        ai_api_key="demo-key",
        ai_api_base="https://api.deepseek.com",
        ai_model="deepseek-chat",
        ai_push_enabled=False,
        ai_push_summary_only=True,
    )


def test_pick_notify_entries_skips_macro_and_honors_cooldown():
    state_dir = ROOT / ".runtime_test_notify"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps({"notified::spread-1": "2026-04-12 10:10:00"}, ensure_ascii=False),
        encoding="utf-8",
    )
    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-1",
        },
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "宏观提醒",
            "detail": "关注非农。",
            "tone": "warning",
            "signature": "macro-1",
        },
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "session",
            "title": "休市 / 暂停提醒",
            "detail": "EURUSD 当前休市。",
            "tone": "accent",
            "signature": "session-1",
        },
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "休市 / 暂停提醒"
    shutil.rmtree(state_dir)


def test_send_notifications_updates_state_and_returns_messages(monkeypatch):
    state_dir = ROOT / ".runtime_test_notify_send"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    sent_titles = []

    def fake_ding(entry, webhook):
        sent_titles.append(f"ding:{entry['title']}")
        return True, "ok"

    def fake_pushplus(entry, token):
        sent_titles.append(f"push:{entry['title']}")
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", fake_pushplus)

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-send-1",
        }
    ]
    result = notification.send_notifications(entries, _build_config(), state_file=state_file)
    assert result["sent_count"] == 1
    assert result["sent_channel_count"] == 2
    assert sent_titles == ["ding:XAUUSD 点差高警戒", "push:XAUUSD 点差高警戒"]
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "notified::dingtalk::spread-send-1" in state
    assert "notified::pushplus::spread-send-1" in state
    shutil.rmtree(state_dir)


def test_build_markdown_includes_trade_grade_and_next_review():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "trade_grade": "当前不宜出手",
            "trade_grade_detail": "执行成本过高，强行追单容易被来回扫掉。",
            "trade_next_review": "等点差恢复正常后再复核。",
        }
    )
    assert "当前结论：当前不宜出手" in markdown
    assert "下一次复核：等点差恢复正常后再复核。" in markdown


def test_send_test_notification_returns_channel_messages(monkeypatch):
    def fake_ding(entry, webhook):
        return True, "ok"

    def fake_pushplus(entry, token):
        return False, "token invalid"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", fake_pushplus)

    result = notification.send_test_notification(_build_config())
    assert "钉钉测试推送成功" in result["messages"]
    assert any("PushPlus 测试推送失败" in item for item in result["errors"])


def test_get_notification_status_reads_last_result(tmp_path=None):
    state_dir = ROOT / ".runtime_test_notify_status"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "last_result_text": "钉钉测试推送成功",
                "last_result_time": "2026-04-12 19:00:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    status = notification.get_notification_status(_build_config(), state_file=state_file)
    assert "钉钉已配置" in status["channels_text"]
    assert "PushPlus已配置" in status["channels_text"]
    assert status["last_result_text"] == "钉钉测试推送成功"
    assert status["last_result_time"] == "2026-04-12 19:00:00"
    shutil.rmtree(state_dir)


def test_send_ai_brief_notification_honors_summary_mode(monkeypatch):
    config = _build_config()
    config.ai_push_enabled = True
    config.ai_push_summary_only = True
    payloads = []

    def fake_ding(entry, webhook):
        payloads.append(entry)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    result = notification.send_ai_brief_notification(
        {
            "model": "deepseek-chat",
            "content": "方向判断：黄金偏强。\n风险点：非农前点差可能放大。\n行动建议：先等回踩确认。",
        },
        {
            "summary_text": "当前共观察 2 个品种。",
            "items": [{"symbol": "XAUUSD"}, {"symbol": "EURUSD"}],
        },
        config,
    )

    assert result["sent_count"] == 1
    assert payloads
    assert payloads[0]["title"].startswith("AI 研判")
    assert "方向判断：黄金偏强。" == payloads[0]["detail"]


def test_send_notifications_retries_only_failed_channel(monkeypatch):
    state_dir = ROOT / ".runtime_test_notify_retry"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "notified::dingtalk::spread-retry-1": "2026-04-12 10:10:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    called = []

    def fake_ding(entry, webhook):
        called.append("ding")
        return True, "ok"

    def fake_pushplus(entry, token):
        called.append("push")
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", fake_pushplus)

    result = notification.send_notifications(
        [
            {
                "occurred_at": "2026-04-12 10:20:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-retry-1",
            }
        ],
        _build_config(),
        state_file=state_file,
    )

    assert result["sent_count"] == 1
    assert result["sent_channel_count"] == 1
    assert called == ["push"]
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert "notified::dingtalk::spread-retry-1" in state
    assert "notified::pushplus::spread-retry-1" in state
    shutil.rmtree(state_dir)
