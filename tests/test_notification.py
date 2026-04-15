import json
import shutil
import sys
from datetime import datetime
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


def test_pick_notify_entries_honors_cooldown_and_session_keeps_priority_after_macro_silence():
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
    # spread-1 在冷却期内被拦截；泛宏观广播现在默认静默，只保留真正有行动意义的 session 提醒
    assert len(picked) == 1
    assert picked[0]["title"] == "休市 / 暂停提醒"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_non_actionable_macro_entry():
    state_dir = ROOT / ".runtime_test_notify_macro_skip"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "宏观提醒",
            "detail": "先关注美元方向。",
            "tone": "accent",
            "signature": "macro-skip-1",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_high_impact_macro_entry():
    state_dir = ROOT / ".runtime_test_notify_macro"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "宏观提醒",
            "detail": "联储利率决议窗口内，先别抢第一脚。",
            "tone": "warning",
            "signature": "macro-high-1",
            "event_importance_text": "高影响",
            "event_name": "联储利率决议",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "宏观提醒"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_scope_less_high_impact_macro_without_result():
    state_dir = ROOT / ".runtime_test_notify_macro_scope_less"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "宏观提醒",
            "detail": "联储利率决议窗口内，先别抢第一脚。",
            "tone": "warning",
            "signature": "macro-high-scope-less-1",
            "event_importance_text": "高影响",
            "event_name": "联储利率决议",
            "macro_actionable": True,
            "macro_scope_bound": False,
            "macro_has_result": False,
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_scope_bound_high_impact_macro_without_result():
    state_dir = ROOT / ".runtime_test_notify_macro_scope_bound"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "美国 CPI 宏观提醒",
            "detail": "高影响窗口内，黄金先别抢第一脚。",
            "tone": "warning",
            "signature": "macro-high-scope-bound-1",
            "event_importance_text": "高影响",
            "event_name": "美国 CPI",
            "macro_actionable": True,
            "macro_scope_bound": True,
            "macro_has_result": False,
            "symbol": "XAUUSD",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "美国 CPI 宏观提醒"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_low_impact_accent_spread():
    state_dir = ROOT / ".runtime_test_notify_low"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "EURUSD 点差偏宽",
            "detail": "当前点差偏宽。",
            "tone": "accent",
            "signature": "spread-low-1",
            "event_importance_text": "低影响",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_sorts_high_impact_first():
    state_dir = ROOT / ".runtime_test_notify_priority"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "EURUSD 点差偏宽",
            "detail": "当前点差偏宽。",
            "tone": "accent",
            "signature": "spread-medium-1",
            "event_importance_text": "中影响",
        },
        {
            "occurred_at": "2026-04-12 10:21:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-high-1",
            "event_importance_text": "高影响",
        },
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert [item["title"] for item in picked] == ["XAUUSD 点差高警戒", "EURUSD 点差偏宽"]
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_recovery_entry():
    state_dir = ROOT / ".runtime_test_notify_recovery"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "recovery",
            "title": "XAUUSD 点差已恢复",
            "detail": "当前点差已回落。",
            "tone": "success",
            "signature": "recovery-allow-1",
            "symbol": "XAUUSD",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 点差已恢复"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_structure_entry_when_risk_reward_is_good():
    state_dir = ROOT / ".runtime_test_notify_structure"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 结构候选",
            "detail": "结构和报价相对干净，可继续观察。",
            "tone": "success",
            "signature": "structure-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 2.1,
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 结构候选"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_source_alert_entry():
    state_dir = ROOT / ".runtime_test_notify_source"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "source",
            "title": "宏观数据状态提醒",
            "detail": "结构化宏观数据拉取失败：timeout",
            "tone": "warning",
            "signature": "source-1",
            "source_name": "macro_data",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert len(picked) == 1
    assert picked[0]["title"] == "宏观数据状态提醒"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_respects_dnd_for_market_alerts():
    state_dir = ROOT / ".runtime_test_notify_dnd"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    config = _build_config()
    config.notify_dnd_enabled = True
    config.notify_dnd_start_hour = 0
    config.notify_dnd_end_hour = 7

    picked = notification.pick_notify_entries(
        [
            {
                "occurred_at": "2026-04-12 01:20:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-dnd-1",
            },
            {
                "occurred_at": "2026-04-12 01:21:00",
                "category": "source",
                "title": "宏观数据状态提醒",
                "detail": "结构化宏观数据拉取失败：timeout",
                "tone": "warning",
                "signature": "source-dnd-1",
                "source_name": "macro_data",
            },
        ],
        config,
        state_file=state_file,
    )

    assert len(picked) == 1
    assert picked[0]["title"] == "宏观数据状态提醒"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_rollover_spread_between_five_and_seven():
    state_dir = ROOT / ".runtime_test_notify_rollover"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    config = _build_config()
    config.notify_dnd_enabled = False
    config.overnight_spread_guard_enabled = True
    config.overnight_spread_guard_start_hour = 5
    config.overnight_spread_guard_end_hour = 7

    picked = notification.pick_notify_entries(
        [
            {
                "occurred_at": "2026-04-12 05:30:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-rollover-1",
            },
            {
                "occurred_at": "2026-04-12 05:35:00",
                "category": "macro",
                "title": "宏观提醒",
                "detail": "联储利率决议窗口内，先别抢第一脚。",
                "tone": "warning",
                "signature": "macro-rollover-1",
                "event_importance_text": "高影响",
                "event_name": "联储利率决议",
            },
        ],
        config,
        state_file=state_file,
    )

    assert len(picked) == 1
    assert picked[0]["title"] == "宏观提醒"
    shutil.rmtree(state_dir)


def test_send_notifications_aggregates_same_group_entries(monkeypatch):
    state_dir = ROOT / ".runtime_test_notify_aggregate"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    sent_entries = []

    def fake_ding(entry, webhook):
        sent_entries.append(entry)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    result = notification.send_notifications(
        [
            {
                "occurred_at": "2026-04-13 09:20:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-group-1",
                "symbol": "XAUUSD",
                "event_importance_text": "高影响",
            },
            {
                "occurred_at": "2026-04-13 09:21:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "点差继续抬升。",
                "tone": "warning",
                "signature": "spread-group-2",
                "symbol": "XAUUSD",
                "event_importance_text": "高影响",
            },
        ],
        _build_config(),
        state_file=state_file,
    )

    assert result["sent_count"] == 1
    assert sent_entries
    assert sent_entries[0]["aggregate_count"] == 2
    assert "持续" in sent_entries[0]["title"]
    shutil.rmtree(state_dir)


def test_send_notifications_escalates_within_group_cooldown(monkeypatch):
    state_dir = ROOT / ".runtime_test_notify_escalate"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "group::dingtalk::spread::XAUUSD::last_time": "2026-04-13 09:10:00",
                "group::dingtalk::spread::XAUUSD::last_priority": 2,
                "group::dingtalk::spread::XAUUSD::pending_count": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sent_entries = []

    def fake_ding(entry, webhook):
        sent_entries.append(entry)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    result = notification.send_notifications(
        [
            {
                "occurred_at": "2026-04-13 09:20:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-escalate-1",
                "symbol": "XAUUSD",
                "event_importance_text": "高影响",
            }
        ],
        _build_config(),
        state_file=state_file,
    )

    assert result["sent_count"] == 1
    assert sent_entries
    assert "升级提醒" in sent_entries[0]["title"]
    assert sent_entries[0]["aggregate_count"] == 2
    shutil.rmtree(state_dir)


def test_send_notifications_accumulates_pending_when_low_priority_repeat_skipped(monkeypatch):
    state_dir = ROOT / ".runtime_test_notify_pending"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "group::dingtalk::spread::EURUSD::last_time": "2026-04-13 09:10:00",
                "group::dingtalk::spread::EURUSD::last_priority": 4,
                "group::dingtalk::spread::EURUSD::pending_count": 1,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    result = notification.send_notifications(
        [
            {
                "occurred_at": "2026-04-13 09:20:00",
                "category": "spread",
                "title": "EURUSD 点差偏宽",
                "detail": "当前点差偏宽。",
                "tone": "accent",
                "signature": "spread-pending-1",
                "symbol": "EURUSD",
                "event_importance_text": "中影响",
            }
        ],
        _build_config(),
        state_file=state_file,
    )

    assert result["sent_count"] == 0
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert state["group::dingtalk::spread::EURUSD::pending_count"] == 2


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
            "symbol": "XAUUSD",
            "baseline_latest_price": 4759.82,
            "baseline_bid": 4759.74,
            "baseline_ask": 4759.91,
            "baseline_spread_points": 17.0,
            "price_point": 0.01,
            "trade_grade": "当前不宜出手",
            "trade_grade_detail": "执行成本过高，强行追单容易被来回扫掉。",
            "trade_next_review": "等点差恢复正常后再复核。",
        }
    )
    assert "品种：XAUUSD" in markdown
    assert "价格：4,759.82" in markdown
    assert "盘口：Bid 4,759.74 / Ask 4,759.91 · 点差 17点" in markdown
    assert "结论：**当前不宜出手**" in markdown
    assert "**先看这个**" in markdown
    assert "复核：等点差恢复正常后再复核。" in markdown


def test_build_markdown_includes_event_window_details():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "EURUSD 点差偏宽",
            "detail": "当前点差偏宽。",
            "trade_grade": "当前不宜出手",
            "trade_grade_detail": "高影响事件前，先别抢第一脚。",
            "event_mode_text": "事件前高敏",
            "event_name": "欧元区通胀",
            "event_time_text": "2026-04-15 20:30",
            "event_importance_text": "高影响",
            "event_scope_text": "EURUSD",
            "event_note": "高影响窗口：欧元区通胀将于 2026-04-15 20:30 落地，当前品种先别抢第一脚。",
        }
    )
    assert "**背景**" in markdown
    assert "事件：欧元区通胀 | 2026-04-15 20:30 | 高影响 | EURUSD" in markdown
    assert "提醒：高影响窗口：欧元区通胀将于 2026-04-15 20:30 落地，当前品种先别抢第一脚。" in markdown


def test_build_markdown_includes_risk_reward_action_levels():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 结构候选",
            "detail": "结构和报价相对干净。",
            "symbol": "XAUUSD",
            "baseline_latest_price": 4759.82,
            "baseline_bid": 4759.74,
            "baseline_ask": 4759.91,
            "baseline_spread_points": 17.0,
            "price_point": 0.01,
            "trade_grade": "可轻仓试仓",
            "trade_grade_detail": "可作为候选机会观察。",
            "trade_grade_source": "structure",
            "regime_text": "趋势扩张",
            "regime_reason": "H1 与 H4 同向偏多，ATR 走阔，当前更像趋势延续而不是低波震荡。",
            "external_bias_note": "宏观数据：美国10年期实际利率 当前值 1.85，较前值 -0.06，背景偏多",
            "risk_reward_ratio": 2.4,
            "model_ready": True,
            "model_win_probability": 0.74,
            "model_confidence_text": "中等信心",
            "stop_loss_price": 4748.0,
            "take_profit_1": 4788.0,
            "take_profit_2": 4810.0,
            "entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
            "position_plan_text": "可轻仓试仓，优先分两段止盈，第一目标落袋后再看延续。",
            "entry_invalidation_text": "若价格重新跌回 4748.00 下方，当前多头结构可视为失效。",
        }
    )
    assert "价格：4,759.82" in markdown
    assert "盘口：Bid 4,759.74 / Ask 4,759.91 · 点差 17点" in markdown
    assert "**决策速览**" in markdown
    assert "环境：趋势扩张" in markdown
    assert "模型：参考胜率 74%（中等信心），与当前结构基本一致" in markdown
    assert "外部：宏观数据：美国10年期实际利率 当前值 1.85，较前值 -0.06，背景偏多" in markdown
    assert "**执行卡片**" in markdown
    assert "观察 观察进场区间 4760.00 - 4770.00" in markdown
    assert "止损 4,748.00" in markdown
    assert "目标 4,788.00 / 4,810.00" in markdown
    assert "盈亏比 1:2.40" in markdown
    assert "**执行参数**" in markdown
    assert "止损：4,748.00" in markdown
    assert "目标1：4,788.00" in markdown
    assert "目标2：4,810.00" in markdown
    assert "观察区间：观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。" in markdown
    assert "仓位：可轻仓试仓，优先分两段止盈，第一目标落袋后再看延续。" in markdown
    assert "失效：若价格重新跌回 4748.00 下方，当前多头结构可视为失效。" in markdown
    assert "**背景**" in markdown
    assert "外部背景：宏观数据：美国10年期实际利率 当前值 1.85，较前值 -0.06，背景偏多" in markdown
    assert "技术面" not in markdown


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
    state_dir = ROOT / ".runtime_test_ai_brief_summary"
    if state_dir.exists():
        import shutil
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

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
        state_file=state_file,
    )

    assert result["sent_count"] == 1
    assert payloads
    assert payloads[0]["title"].startswith("AI 研判")
    assert "方向判断：黄金偏强。" == payloads[0]["detail"]

    import shutil
    shutil.rmtree(state_dir)


def test_send_ai_brief_notification_cooldown_blocks_second_push(monkeypatch):
    """S-004 修复验证：AI 研判推送在冷却期内第二次调用被拦截。"""
    state_dir = ROOT / ".runtime_test_ai_brief_cooldown"
    if state_dir.exists():
        import shutil
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    config = _build_config()
    config.ai_push_enabled = True
    config.ai_push_summary_only = False
    config.ai_auto_interval_min = 60  # 冷却 = max(20, 60//2) = 30分钟

    called = []

    def fake_ding(entry, webhook):
        called.append("sent")
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    brief_payload = {
        "model": "deepseek-chat",
        "content": "黄金偏多，先等回踩。",
    }
    snap = {"summary_text": "快照", "items": [{"symbol": "XAUUSD"}]}

    # 第一次：无状态，应该正常发送
    r1 = notification.send_ai_brief_notification(brief_payload, snap, config, state_file=state_file)
    assert r1["sent_count"] == 1, "第一次应发送成功"

    # 第二次：刚发过，应该被冷却拦截
    r2 = notification.send_ai_brief_notification(brief_payload, snap, config, state_file=state_file)
    assert r2["sent_count"] == 0, "冷却期内应被拦截"
    assert "_cooldown" in r2.get("skipped_reason", ""), f"期望冷却被拒，实际：{r2}"

    assert len(called) == 1, "只应该实际发送1次"

    import shutil
    shutil.rmtree(state_dir)


def test_write_state_purges_expired_notify_records():
    """M-006 修复验证：写入 state 时自动清理超过7天的 notified:: 记录。"""
    from notification_state import _write_state, _read_state
    import tempfile
    from datetime import timedelta

    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "notify_state.json"
        old_time = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        fresh_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state = {
            "notified::dingtalk::old-sig": old_time,      # 应被清理
            "notified::pushplus::old-sig": old_time,       # 应被清理
            "notified::dingtalk::fresh-sig": fresh_time,  # 应保留
            "last_result_text": "最后推送",               # 非冷却记录，应保留
        }
        _write_state(state, state_file=state_file)
        result = _read_state(state_file=state_file)
        assert "notified::dingtalk::old-sig" not in result, "10天前的记录应被清理"
        assert "notified::pushplus::old-sig" not in result, "10天前的记录应被清理"
        assert "notified::dingtalk::fresh-sig" in result, "新鲜记录应保留"
        assert "last_result_text" in result, "非冷却字段应保留"


def test_send_learning_report_notification_honors_enable_flag(monkeypatch):
    config = _build_config()
    payloads = []

    def fake_ding(entry, webhook):
        payloads.append(entry)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    result = notification.send_learning_report_notification(
        {
            "summary_text": "规则治理：启用 1 条，观察 1 条，冻结 0 条。",
            "active_rules": ["- [entry] 回踩确认后轻仓介入"],
            "watch_rules": ["- [trend] 第一次突破先等回踩"],
            "frozen_rules": [],
        },
        config,
    )

    assert result["sent_count"] == 0
    assert result["skipped_reason"] == "learning_push_disabled"
    assert payloads == []


def test_send_learning_report_notification_dedupes_same_digest(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_notify"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    config.learning_push_min_interval_hour = 12
    payloads = []

    def fake_ding(entry, webhook):
        payloads.append(entry)
        return True, "ok"

    monkeypatch.setattr(notification, "send_dingtalk", fake_ding)
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    report = {
        "summary_text": "规则治理：启用 1 条，观察 1 条，冻结 1 条。",
        "active_rules": ["- [entry] 回踩确认后轻仓介入（样本 8，成功率 63%，评分 32.0）"],
        "watch_rules": ["- [trend] 第一次突破先等回踩（样本 4，成功率 50%，评分 12.0）"],
        "frozen_rules": ["- [directional] 连续冲高直接追多（样本 6，成功率 17%，评分 -40.0）"],
    }

    first = notification.send_learning_report_notification(
        report,
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 9, 0, 0),
    )
    second = notification.send_learning_report_notification(
        report,
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 18, 0, 0),
    )

    assert first["sent_count"] == 1
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_report_unchanged"
    assert len(payloads) == 1
    assert payloads[0]["title"] == "知识库学习摘要"
    shutil.rmtree(state_dir)


def test_send_learning_report_notification_rate_limits_changed_digest(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_notify_rate"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    config.learning_push_min_interval_hour = 12

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    first = notification.send_learning_report_notification(
        {
            "summary_text": "规则治理：启用 1 条，观察 0 条，冻结 0 条。",
            "active_rules": ["- [entry] 回踩确认后轻仓介入"],
            "watch_rules": [],
            "frozen_rules": [],
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 9, 0, 0),
    )
    second = notification.send_learning_report_notification(
        {
            "summary_text": "规则治理：启用 2 条，观察 0 条，冻结 0 条。",
            "active_rules": [
                "- [entry] 回踩确认后轻仓介入",
                "- [trend] 多周期同向时优先顺势",
            ],
            "watch_rules": [],
            "frozen_rules": [],
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 15, 0, 0),
    )

    assert first["sent_count"] == 1
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_report_rate_limited"
    shutil.rmtree(state_dir)


def test_send_learning_report_notification_ignores_delta_text_only_changes(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_notify_same_core"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    config.learning_push_min_interval_hour = 1

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    first = notification.send_learning_report_notification(
        {
            "summary_text": "规则治理：启用 1 条，观察 0 条，冻结 0 条。 状态变化：本轮新增启用 1 条。",
            "governance_summary": {"summary_text": "规则治理：启用 1 条，观察 0 条，冻结 0 条，待积累 0 条，人工复核 0 条。"},
            "active_rules": ["- [entry] 回踩确认后轻仓介入"],
            "watch_rules": [],
            "frozen_rules": [],
            "promoted_rules": ["- [entry] 回踩确认后轻仓介入"],
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 9, 0, 0),
    )
    second = notification.send_learning_report_notification(
        {
            "summary_text": "规则治理：启用 1 条，观察 0 条，冻结 0 条。",
            "governance_summary": {"summary_text": "规则治理：启用 1 条，观察 0 条，冻结 0 条，待积累 0 条，人工复核 0 条。"},
            "active_rules": ["- [entry] 回踩确认后轻仓介入"],
            "watch_rules": [],
            "frozen_rules": [],
            "promoted_rules": [],
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 13, 11, 0, 0),
    )

    assert first["sent_count"] == 1
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_report_unchanged"
    shutil.rmtree(state_dir)


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
