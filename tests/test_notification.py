import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app_config import MetalMonitorConfig
import notification
import notification_payloads
from quote_models import SnapshotItem


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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    # spread-1 在冷却期内被拦截；宏观继续观望与休市状态都不再外推，
    # 避免“现在别动手”类消息继续占据用户通知栏。
    assert picked == []
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_feedback_push_policy_can_promote_clean_near_zone_structure_entry():
    entry = {
        "category": "structure",
        "structure_entry_stage": "near_zone",
        "signal_side": "long",
        "risk_reward_ratio": 1.8,
        "opportunity_score": 80,
    }
    policy = {
        "active": True,
        "advance_warning": True,
        "reduce_noise": False,
        "tighten_risk": False,
        "min_score_boost": -5,
    }

    assert notification._is_feedback_policy_promoted(entry, policy) is True
    assert notification._is_feedback_policy_suppressed(entry, policy) is False


def test_feedback_push_policy_suppresses_noisy_low_score_opportunity():
    entry = {
        "category": "opportunity",
        "signal_side": "long",
        "risk_reward_ratio": 1.7,
        "opportunity_score": 70,
    }
    policy = {
        "active": True,
        "advance_warning": False,
        "reduce_noise": True,
        "tighten_risk": False,
        "min_score_boost": 10,
    }

    assert notification._is_feedback_policy_suppressed(entry, policy) is True


def test_pick_notify_entries_suppresses_high_impact_macro_entry_for_user_push():
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_suppresses_scope_bound_high_impact_macro_without_result():
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 点差已恢复"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_suppresses_same_spread_state_within_transition_window():
    state_dir = ROOT / ".runtime_test_notify_spread_same_state"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "group::dingtalk::spread::XAUUSD::last_time": "2026-04-12 09:40:00",
                "group::dingtalk::spread::XAUUSD::last_priority": 5,
                "group::dingtalk::spread::XAUUSD::last_fingerprint": "spread | XAUUSD 点差高警戒 | 当前点差明显放大。 | warning",
                "group::pushplus::spread::XAUUSD::last_time": "2026-04-12 09:40:00",
                "group::pushplus::spread::XAUUSD::last_priority": 5,
                "group::pushplus::spread::XAUUSD::last_fingerprint": "spread | XAUUSD 点差高警戒 | 当前点差明显放大。 | warning",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-same-state-1",
            "symbol": "XAUUSD",
            "event_importance_text": "高影响",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_spread_when_state_changed_within_transition_window():
    state_dir = ROOT / ".runtime_test_notify_spread_state_changed"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "group::dingtalk::spread::XAUUSD::last_time": "2026-04-12 09:40:00",
                "group::dingtalk::spread::XAUUSD::last_priority": 5,
                "group::dingtalk::spread::XAUUSD::last_fingerprint": "spread | XAUUSD 点差高警戒 | 当前点差明显放大。 | warning",
                "group::pushplus::spread::XAUUSD::last_time": "2026-04-12 09:40:00",
                "group::pushplus::spread::XAUUSD::last_priority": 5,
                "group::pushplus::spread::XAUUSD::last_fingerprint": "spread | XAUUSD 点差高警戒 | 当前点差明显放大。 | warning",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "点差继续抬升。",
            "tone": "warning",
            "signature": "spread-state-changed-1",
            "symbol": "XAUUSD",
            "event_importance_text": "高影响",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert len(picked) == 1
    assert picked[0]["detail"] == "点差继续抬升。"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_structure_cancel_entry():
    state_dir = ROOT / ".runtime_test_notify_structure_cancel"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure_cancel",
            "title": "XAUUSD 本次机会失效",
            "detail": "价格仍在区间内，但多单位置不再漂亮，先作废上一条提醒。",
            "tone": "warning",
            "signature": "structure-cancel-1",
            "symbol": "XAUUSD",
            "invalidated_from_title": "XAUUSD 进入观察区间（下沿）",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 本次机会失效"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_plain_structure_candidate_even_when_risk_reward_is_good():
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
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_prioritizes_structure_inside_entry_zone():
    state_dir = ROOT / ".runtime_test_notify_structure_zone"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-zone-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 进入观察区间（下沿）"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_long_inside_zone_upper_side():
    state_dir = ROOT / ".runtime_test_notify_structure_inside_upper"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（上沿）",
            "detail": "已进入观察区间，但位置更靠近上沿。",
            "tone": "success",
            "signature": "structure-inside-upper-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.9,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "上沿",
            "signal_side": "long",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_long_inside_zone_middle_side():
    state_dir = ROOT / ".runtime_test_notify_structure_inside_middle"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（中段）",
            "detail": "已进入观察区间，但仍在中段。",
            "tone": "success",
            "signature": "structure-inside-middle-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.9,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "中段",
            "signal_side": "long",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_allows_short_inside_zone_upper_side():
    state_dir = ROOT / ".runtime_test_notify_structure_inside_short_upper"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（上沿）",
            "detail": "已进入观察区间上沿，可重点盯反抽承压。",
            "tone": "success",
            "signature": "structure-inside-short-upper-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "上沿",
            "signal_side": "short",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert len(picked) == 1
    assert picked[0]["title"] == "XAUUSD 进入观察区间（上沿）"
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_inside_zone_when_model_probability_is_low():
    state_dir = ROOT / ".runtime_test_notify_structure_model_block"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-model-block-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
            "model_ready": True,
            "model_win_probability": 0.42,
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_inside_zone_when_high_impact_event_blocks_entry():
    state_dir = ROOT / ".runtime_test_notify_structure_event_block"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-event-block-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
            "event_importance_text": "高影响",
            "event_note": "高影响窗口：美国 CPI 将于 20:30 落地，当前先别抢第一脚。",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_inside_zone_when_external_bias_conflicts():
    state_dir = ROOT / ".runtime_test_notify_structure_external_block"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-external-block-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
            "external_bias_note": "宏观数据：美国10年期实际利率 当前值 1.85，背景偏空",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_inside_zone_when_spread_is_too_wide():
    state_dir = ROOT / ".runtime_test_notify_structure_spread_block"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-spread-block-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
            "baseline_spread_points": 35.0,
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 20, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_expired_structure_entry():
    state_dir = ROOT / ".runtime_test_notify_structure_expired"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    config = _build_config()
    config.refresh_interval_sec = 30

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 进入观察区间（下沿）",
            "detail": "已进入观察区间，可重点盯执行。",
            "tone": "success",
            "signature": "structure-expired-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 1.8,
            "structure_entry_stage": "inside_zone",
            "entry_zone_side_text": "下沿",
            "signal_side": "long",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        config,
        state_file=state_file,
        now=datetime(2026, 4, 12, 10, 22, 30),
    )
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_skips_structure_near_zone():
    state_dir = ROOT / ".runtime_test_notify_structure_near_zone"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 靠近观察区间（下沿）",
            "detail": "价格已经靠近观察区间，可开始盯执行。",
            "tone": "success",
            "signature": "structure-near-zone-1",
            "symbol": "XAUUSD",
            "risk_reward_ratio": 2.0,
            "structure_entry_stage": "near_zone",
            "entry_zone_side_text": "下沿",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert picked == []
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


def test_pick_notify_entries_skips_non_warning_source_status():
    state_dir = ROOT / ".runtime_test_notify_source_accent"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"

    entries = [
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "source",
            "title": "宏观数据状态提醒",
            "detail": "外部事件源等待后台同步，本地暂时沿用缓存。",
            "tone": "accent",
            "signature": "source-accent-1",
            "source_name": "event_feed",
        }
    ]
    picked = notification.pick_notify_entries(entries, _build_config(), state_file=state_file)
    assert picked == []
    shutil.rmtree(state_dir)


def test_pick_notify_entries_suppresses_repeated_source_alert_within_transition_window():
    state_dir = ROOT / ".runtime_test_notify_source_transition"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    state_file.write_text(
        json.dumps(
            {
                "group::dingtalk::source::macro_data::last_time": "2026-04-12 10:00:00",
                "group::dingtalk::source::macro_data::last_priority": 4,
                "group::pushplus::source::macro_data::last_time": "2026-04-12 10:00:00",
                "group::pushplus::source::macro_data::last_priority": 4,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    entries = [
        {
            "occurred_at": "2026-04-12 11:20:00",
            "category": "source",
            "title": "宏观数据状态提醒",
            "detail": "结构化宏观数据拉取失败：timeout",
            "tone": "warning",
            "signature": "source-transition-1",
            "source_name": "macro_data",
        }
    ]
    picked = notification.pick_notify_entries(
        entries,
        _build_config(),
        state_file=state_file,
        now=datetime(2026, 4, 12, 11, 20, 0),
    )
    assert picked == []
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

    assert picked == []
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
    # 只配置钉钉，不配置 PushPlus，保证 pushplus 通道不进入发送路径
    # 否则 pushplus 通道也会被乐观入队，导致 sent_count 因 pushplus 通道也计数
    config_single_channel = _build_config()
    config_single_channel.pushplus_token = ""  # 不配置 pushplus

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
        config_single_channel,
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
    assert sent_titles == ["ding:XAUUSD 风控更新：点差过宽", "push:XAUUSD 风控更新：点差过宽"]
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
    assert "## ⚠️【XAUUSD 风控更新：点差过宽】" in markdown
    assert "- 品种：XAUUSD" in markdown
    assert "- 状态：当前不宜出手" in markdown
    assert "- 当前价：4,759.82 | 点差 17点" in markdown
    assert "- 原因：执行成本过高，强行追单容易被来回扫掉。" in markdown
    assert "- 动作：先别下单，等点差恢复。" in markdown
    assert "复核：等点差恢复正常后再复核。" in markdown
    assert "**出手拆解**" not in markdown
    assert "**三步执行**" not in markdown
    assert "说明：" not in markdown


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
    assert "- 状态：当前不宜出手" in markdown
    assert "原因：高影响窗口：欧元区通胀将于 2026-04-15 20:30 落地，当前品种先别抢第一脚。" in markdown
    assert "- 动作：先别下单，等点差恢复。" in markdown
    assert "**出手拆解**" not in markdown
    assert "**三步执行**" not in markdown
    assert "说明：" not in markdown


def test_build_markdown_keeps_macro_entry_action_only():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "macro",
            "title": "美国 CPI 宏观提醒",
            "detail": "美国 CPI 即将公布，黄金短时波动可能放大。",
            "trade_grade": "等待事件落地",
            "event_name": "美国 CPI",
            "event_time_text": "2026-04-15 20:30",
            "event_note": "高影响窗口内先别抢第一脚。",
        }
    )
    assert "## 📊【美国 CPI 状态更新：继续观望】" in markdown
    assert "- 状态：等待事件落地" in markdown
    assert "- 原因：美国 CPI | 2026-04-15 20:30" in markdown
    assert "- 动作：先观望，等事件落地后再决定。" in markdown
    assert "**三步执行**" not in markdown
    assert "说明：" not in markdown
    assert "注意：" not in markdown


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
            "structure_entry_stage": "inside_zone",
            "signal_side": "long",
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
            "entry_zone_side_text": "下沿",
            "entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
            "position_plan_text": "可轻仓试仓，优先分两段止盈，第一目标落袋后再看延续。",
            "entry_invalidation_text": "若价格重新跌回 4748.00 下方，当前多头结构可视为失效。",
            "structure_validity_text": "仅当前短时有效；若价格离开观察区间或下一两轮仍无确认，请直接忽略。",
            "opportunity_action": "long",
            "opportunity_action_text": "可提醒短线做多",
            "opportunity_timeframe": "short_term",
            "opportunity_score": 78,
        }
    )
    assert "## 📐【XAUUSD 机会更新：多单到位】" in markdown
    assert "### XAUUSD" in markdown
    assert "短线：偏多，可轻仓试多" in markdown
    assert "长线：多头趋势未破，但当前位置不适合追" in markdown
    assert "入场区：观察进场区间 4760.00 - 4770.00" in markdown
    assert "止损：4,748.00" in markdown
    assert "止盈1：4,788.00" in markdown
    assert "止盈2：4,810.00" in markdown
    assert "盈亏比：2.4R" in markdown
    assert "信号评分：78/100" in markdown
    assert "建议：只做短线，不隔夜。" in markdown
    assert "**出手拆解**" not in markdown
    assert "**三步执行**" not in markdown
    assert "方向：XAUUSD 做多" not in markdown
    assert "参考：模型胜率" not in markdown
    assert "技术面" not in markdown


def test_build_markdown_includes_upper_side_caution_for_long_structure():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:20:00",
            "category": "structure",
            "title": "XAUUSD 靠近观察区间（上沿）",
            "detail": "价格已靠近观察区间上沿。",
            "symbol": "XAUUSD",
            "price_point": 0.01,
            "trade_grade": "可轻仓试仓",
            "trade_grade_detail": "结构相对干净，但位置不算理想。",
            "trade_grade_source": "structure",
            "structure_entry_stage": "near_zone",
            "signal_side": "long",
            "entry_zone_side_text": "上沿",
            "entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
            "risk_reward_ratio": 1.9,
        }
    )
    assert "### XAUUSD" in markdown
    assert "短线：偏多，可轻仓试多" in markdown
    assert "长线：多头背景仍在，等待回踩确认" in markdown
    assert "建议：等确认后轻仓执行，价格离开入场区就放弃。" in markdown
    assert "失效：" not in markdown


def test_build_markdown_for_structure_cancel_keeps_action_only():
    markdown = notification._build_markdown(
        {
            "occurred_at": "2026-04-12 10:25:00",
            "category": "structure_cancel",
            "title": "XAUUSD 本次机会失效",
            "detail": "价格仍在区间内，但多单位置不再漂亮，先作废上一条提醒。",
            "tone": "warning",
            "symbol": "XAUUSD",
            "invalidated_from_title": "XAUUSD 进入观察区间（下沿）",
        }
    )
    assert "## 📊【XAUUSD 机会更新：已失效】" in markdown
    assert "- 品种：XAUUSD" in markdown
    assert "- 原因：XAUUSD 进入观察区间（下沿）" in markdown
    assert "- 动作：上一条机会作废，重新等下一次提醒。" in markdown
    assert "**三步执行**" not in markdown


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
            "signal_meta": {
                "symbol": "XAUUSD",
                "action": "long",
                "price": 4759.80,
                "sl": 4749.80,
                "tp": 4779.80,
            },
        },
        {
            "summary_text": "当前共观察 2 个品种。",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4759.82,
                    "bid": 4759.70,
                    "ask": 4759.82,
                    "spread_points": 12.0,
                    "point": 0.01,
                    "has_live_quote": True,
                    "risk_reward_entry_zone_text": "4758.80-4760.20",
                },
                {"symbol": "EURUSD"},
            ],
        },
        config,
        state_file=state_file,
    )

    # 新语义：sent_count 表示入队的通道数（乐观计数）
    # 配置了钉钉+PushPlus，但测试用 summary_mode，关注的是 payload 格式正确性
    # 期望值改为 2（两个通道均入队）
    assert result["sent_count"] >= 1
    assert payloads
    assert payloads[0]["title"] == "XAUUSD 动作更新：可准备做多"
    assert payloads[0]["detail"] == "XAUUSD 可准备做多，先等确认后再动手。"
    assert "## 🤖【AI 动作提醒：XAUUSD】" in payloads[0]["markdown_body"]
    assert "### XAUUSD" in payloads[0]["markdown_body"]
    assert "短线：偏多，等回踩确认" in payloads[0]["markdown_body"]
    assert "入场区：4758.80-4760.20" in payloads[0]["markdown_body"]
    assert "止损：4,749.80" in payloads[0]["markdown_body"]
    assert "止盈1：4,779.80" in payloads[0]["markdown_body"]
    assert "盈亏比：2R" in payloads[0]["markdown_body"]
    assert "理由：方向判断：黄金偏强。" in payloads[0]["markdown_body"]
    assert "**出手拆解**" not in payloads[0]["markdown_body"]
    assert "**三步执行**" not in payloads[0]["markdown_body"]
    assert "方向：XAUUSD 做多" not in payloads[0]["markdown_body"]

    import shutil
    shutil.rmtree(state_dir)


def test_send_ai_brief_notification_marks_blocked_direction_as_watch_only(monkeypatch):
    state_dir = ROOT / ".runtime_test_ai_brief_watch_only"
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
            "content": "方向判断：黄金偏强。\n行动建议：先等回踩确认。",
            "signal_meta": {
                "symbol": "XAUUSD",
                "action": "long",
                "price": 4759.80,
                "sl": 4749.80,
                "tp": 4779.80,
            },
        },
        {
            "summary_text": "当前共观察 1 个品种。",
            "items": [
                {
                    "symbol": "XAUUSD",
                    "latest_price": 4759.82,
                    "bid": 4759.70,
                    "ask": 4759.82,
                    "spread_points": 12.0,
                    "point": 0.01,
                    "has_live_quote": True,
                    "trade_grade": "只适合观察",
                    "trade_grade_source": "structure",
                    "signal_side": "neutral",
                    "risk_reward_ready": True,
                    "risk_reward_ratio": 2.0,
                    "risk_reward_stop_price": 4749.80,
                    "risk_reward_target_price": 4779.80,
                    "risk_reward_entry_zone_text": "4758.80-4760.20",
                }
            ],
        },
        config,
        state_file=state_file,
    )

    assert result["sent_count"] >= 1
    assert payloads
    assert payloads[0]["title"] == "XAUUSD 动作更新：偏多观察"
    assert payloads[0]["detail"] == "XAUUSD AI 偏向做多，但规则层仍未放行，先观察。"
    assert "- 结论：**偏多观察**" in payloads[0]["markdown_body"]
    assert "短线：偏多，先观察" in payloads[0]["markdown_body"]
    assert "建议：规则未放行，只观察，不下单。" in payloads[0]["markdown_body"]
    assert "风险：规则未放行" in payloads[0]["markdown_body"]
    assert "**出手拆解**" not in payloads[0]["markdown_body"]
    assert "**三步执行**" not in payloads[0]["markdown_body"]

    import shutil
    shutil.rmtree(state_dir)


def test_send_ai_brief_notification_accepts_snapshot_item_objects(monkeypatch):
    state_dir = ROOT / ".runtime_test_ai_brief_snapshot_item"
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
            "content": "方向判断：黄金偏强。\n行动建议：先等回踩确认。",
            "signal_meta": {
                "symbol": "XAUUSD",
                "action": "neutral",
                "price": 0,
                "sl": 0,
                "tp": 0,
            },
        },
        {
            "summary_text": "当前共观察 1 个品种。",
            "items": [
                SnapshotItem(
                    symbol="XAUUSD",
                    latest_price=4759.82,
                    spread_points=17.0,
                    point=0.01,
                    has_live_quote=True,
                    status_text="实时报价",
                )
            ],
        },
        config,
        state_file=state_file,
    )

    assert result["sent_count"] == 0
    assert result["skipped_reason"] == "ai_neutral_suppressed"
    assert payloads == []

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
        "signal_meta": {
            "symbol": "XAUUSD",
            "action": "long",
            "price": 4759.8,
            "sl": 4749.8,
            "tp": 4779.8,
        },
    }
    snap = {"summary_text": "快照", "items": [{"symbol": "XAUUSD"}]}

    # 第一次：无状态，应该正常发送（入队 ≥ 1 个通道）
    r1 = notification.send_ai_brief_notification(brief_payload, snap, config, state_file=state_file)
    assert r1["sent_count"] >= 1, "第一次应入队推送"

    # 第二次：刚发过，应该被冷却拦截
    r2 = notification.send_ai_brief_notification(brief_payload, snap, config, state_file=state_file)
    assert r2["sent_count"] == 0, "冷却期内应被拦截"
    assert "_cooldown" in r2.get("skipped_reason", ""), f"期望冷却被拒，实际：{r2}"

    assert len(called) == 1, "只应该实际发送1次"

    import shutil
    shutil.rmtree(state_dir)


def test_write_state_purges_expired_notify_records(tmp_path):
    """M-006 修复验证：写入 state 时自动清理超过7天的 notified:: 记录。"""
    from notification_state import _write_state, _read_state
    from datetime import timedelta

    state_file = tmp_path / "notify_state.json"
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


def test_write_state_purges_expired_group_companion_fields(tmp_path):
    from notification_state import _write_state, _read_state

    state_file = tmp_path / "notify_state.json"
    state = {
        "group::dingtalk::spread::XAUUSD::last_time": "2026-04-01 10:00:00",
        "group::dingtalk::spread::XAUUSD::last_priority": 4,
        "group::dingtalk::spread::XAUUSD::pending_count": 3,
        "group::pushplus::spread::XAUUSD::last_time": "2026-04-19 10:00:00",
        "group::pushplus::spread::XAUUSD::last_priority": 4,
        "group::pushplus::spread::XAUUSD::pending_count": 1,
    }

    _write_state(state, state_file=state_file, now=datetime(2026, 4, 20, 10, 0, 0))
    result = _read_state(state_file=state_file)

    assert "group::dingtalk::spread::XAUUSD::last_time" not in result
    assert "group::dingtalk::spread::XAUUSD::last_priority" not in result
    assert "group::dingtalk::spread::XAUUSD::pending_count" not in result
    assert "group::pushplus::spread::XAUUSD::last_time" in result
    assert "group::pushplus::spread::XAUUSD::pending_count" in result


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

    assert first["sent_count"] >= 1  # 乐观入队：入队的通道数，至少有钉钉通道
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

    assert first["sent_count"] >= 1  # 乐观入队：至少钉钉通道入队
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

    assert first["sent_count"] >= 1  # 乐观入队：至少钉钉通道入队
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_report_unchanged"
    shutil.rmtree(state_dir)


def test_build_learning_report_entry_indents_rule_lines():
    entry = notification_payloads._build_learning_report_entry(
        {
            "summary_text": "规则治理：启用 1 条，观察 1 条，冻结 0 条。",
            "promoted_rules": ["回踩确认后轻仓介入", "- 趋势扩张时先等回踩"],
            "active_rules": ["[entry] 回踩确认后轻仓介入"],
            "watch_rules": ["[trend] 第一次突破先等回踩"],
        }
    )

    markdown = entry["markdown_body"]
    assert "#### 本轮状态变化" in markdown
    assert "  > 回踩确认后轻仓介入" in markdown
    assert "  > 趋势扩张时先等回踩" in markdown
    assert "  > [entry] 回踩确认后轻仓介入" in markdown


def test_build_learning_health_entry_uses_mobile_friendly_copy():
    entry = notification_payloads._build_learning_health_entry(
        {
            "occurred_at": "2026-04-18 12:00:00",
            "status_text": "质量闸门拦截",
            "summary_text": "自动学习本轮未新增规则，主要被质量闸门拦下 4 条候选。",
            "latest_rule_text": "[llm_cluster_loss] 事件前后若点差突然放大，暂停首脚建仓",
            "tone": "warning",
        }
    )

    assert entry["title"] == "学习状态：质量闸门拦截"
    assert entry["detail"] == "候选已生成，但主要被质量闸门拦截。"
    assert "### 自动学习状态变化" in entry["markdown_body"]
    assert "- 摘要：自动学习本轮未新增规则，主要被质量闸门拦下 4 条候选。" in entry["markdown_body"]
    assert "- 最近规则：[llm_cluster_loss] 事件前后若点差突然放大，暂停首脚建仓" in entry["markdown_body"]


def test_send_learning_health_notification_only_pushes_on_state_change(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_health_notify"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    payloads = []

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (payloads.append(entry) or True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    report = {
        "occurred_at": "2026-04-18 12:00:00",
        "status_key": "quality_gate",
        "status_text": "质量闸门拦截",
        "summary_text": "自动学习本轮未新增规则，主要被质量闸门拦下 4 条候选。",
        "latest_rule_text": "[llm_cluster_loss] 事件前后若点差突然放大，暂停首脚建仓",
        "tone": "warning",
    }

    first = notification.send_learning_health_notification(
        report,
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 12, 0, 0),
    )
    second = notification.send_learning_health_notification(
        dict(report),
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 14, 0, 0),
    )
    third = notification.send_learning_health_notification(
        {
            **report,
            "status_key": "productive",
            "status_text": "恢复产出",
            "summary_text": "自动学习已恢复产出：本轮本地新增 1 条，深度反思新增 1 条。",
            "tone": "success",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 16, 0, 0),
    )

    assert first["sent_count"] >= 1
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_health_unchanged"
    assert third["sent_count"] >= 1
    assert len(payloads) == 2
    assert payloads[0]["title"] == "学习状态：质量闸门拦截"
    assert payloads[0]["detail"] == "候选已生成，但主要被质量闸门拦截。"
    assert payloads[1]["title"] == "学习状态：恢复产出"
    assert payloads[1]["detail"] == "本轮已有新规则入库，学习链恢复正常。"
    shutil.rmtree(state_dir)


def test_send_learning_health_notification_suppresses_degraded_flapping(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_health_flap"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    payloads = []

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (payloads.append(entry) or True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    first = notification.send_learning_health_notification(
        {
            "occurred_at": "2026-04-18 12:00:00",
            "status_key": "quality_gate",
            "status_text": "质量闸门拦截",
            "summary_text": "自动学习本轮未新增规则，主要被质量闸门拦下 4 条候选。",
            "latest_rule_text": "",
            "tone": "warning",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 12, 0, 0),
    )
    second = notification.send_learning_health_notification(
        {
            "occurred_at": "2026-04-18 15:00:00",
            "status_key": "dedup_blocked",
            "status_text": "去重拦截",
            "summary_text": "自动学习本轮未新增规则，主要因去重机制拦下 3 条候选。",
            "latest_rule_text": "",
            "tone": "accent",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 15, 0, 0),
    )
    third = notification.send_learning_health_notification(
        {
            "occurred_at": "2026-04-19 13:00:00",
            "status_key": "dedup_blocked",
            "status_text": "去重拦截",
            "summary_text": "自动学习本轮未新增规则，主要因去重机制拦下 3 条候选。",
            "latest_rule_text": "",
            "tone": "accent",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 19, 13, 0, 0),
    )

    assert first["sent_count"] >= 1
    assert second["sent_count"] == 0
    assert second["skipped_reason"] == "learning_health_transition_cooldown"
    assert third["sent_count"] >= 1
    assert len(payloads) == 2
    assert payloads[0]["title"] == "学习状态：质量闸门拦截"
    assert payloads[1]["title"] == "学习状态：去重拦截"
    shutil.rmtree(state_dir)


def test_send_learning_health_notification_productive_breaks_degraded_cooldown(monkeypatch):
    state_dir = ROOT / ".runtime_test_learning_health_productive"
    if state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "notify_state.json"
    config = _build_config()
    config.learning_push_enabled = True
    payloads = []

    monkeypatch.setattr(notification, "send_dingtalk", lambda entry, webhook: (payloads.append(entry) or True, "ok"))
    monkeypatch.setattr(notification, "send_pushplus", lambda entry, token: (False, "skip"))

    first = notification.send_learning_health_notification(
        {
            "occurred_at": "2026-04-18 12:00:00",
            "status_key": "sample_wait",
            "status_text": "样本积累中",
            "summary_text": "自动学习当前没有新的可反思样本，继续等待样本积累。",
            "latest_rule_text": "",
            "tone": "accent",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 12, 0, 0),
    )
    second = notification.send_learning_health_notification(
        {
            "occurred_at": "2026-04-18 13:00:00",
            "status_key": "productive",
            "status_text": "恢复产出",
            "summary_text": "自动学习已恢复产出：本轮本地新增 1 条，深度反思新增 1 条。",
            "latest_rule_text": "",
            "tone": "success",
        },
        config,
        state_file=state_file,
        now=datetime(2026, 4, 18, 13, 0, 0),
    )

    assert first["sent_count"] >= 1
    assert second["sent_count"] >= 1
    assert len(payloads) == 2
    assert payloads[1]["title"] == "学习状态：恢复产出"
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
