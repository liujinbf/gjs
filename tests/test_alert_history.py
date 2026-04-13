import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alert_history import (
    append_history_entries,
    build_snapshot_history_entries,
    read_recent_history,
    summarize_effectiveness,
    summarize_recent_history,
)


def test_build_snapshot_history_entries_collects_spread_macro_and_session_items():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "等待事件落地",
        "trade_grade_detail": "当前宏观窗口敏感，先别抢第一脚。",
        "trade_next_review": "等 15 分钟后再复核。",
        "runtime_status_cards": [
            {"title": "MT5 终端已连通", "detail": "终端正常。", "tone": "success"},
            {"title": "休市 / 暂停提醒", "detail": "USDJPY 当前休市。", "tone": "accent"},
        ],
        "spread_focus_cards": [
            {"title": "XAUUSD 点差高警戒", "detail": "当前点差明显放大。", "tone": "warning"},
            {"title": "点差状态稳定", "detail": "当前稳定。", "tone": "success"},
        ],
        "event_risk_mode_text": "事件前高敏",
        "event_active_name": "美国 CPI",
        "event_active_time_text": "2026-04-12 20:30:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 250.0,
                "trade_grade": "当前不宜出手",
                "trade_grade_detail": "点差明显放大，先不要追单。",
                "trade_next_review": "等点差恢复正常后再看。",
                "event_importance_text": "高影响",
                "event_scope_text": "XAUUSD",
                "event_note": "高影响窗口：美国 CPI 将于 2026-04-12 20:30:00 落地，当前品种先别抢第一脚。",
            }
        ],
        "alert_text": "贵金属提醒：非农前后先盯点差。",
    }
    entries = build_snapshot_history_entries(snapshot)
    titles = [item["title"] for item in entries]
    assert "休市 / 暂停提醒" in titles
    assert "XAUUSD 点差高警戒" in titles
    assert "宏观提醒" in titles
    assert "点差状态稳定" not in titles
    spread_entry = next(item for item in entries if item["title"] == "XAUUSD 点差高警戒")
    assert spread_entry["trade_grade"] == "当前不宜出手"
    assert spread_entry["event_importance_text"] == "高影响"
    assert "高影响窗口" in spread_entry["event_note"]
    macro_entry = next(item for item in entries if item["title"] == "宏观提醒")
    assert macro_entry["trade_grade"] == "等待事件落地"


def test_append_history_entries_dedupes_recent_signatures():
    history_dir = ROOT / ".runtime_test"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    entries = [
        {
            "occurred_at": "2026-04-12 12:00:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "XAUUSD 点差高警戒|当前点差明显放大。|warning",
        }
    ]
    assert append_history_entries(entries, history_file=history_file) == 1
    assert append_history_entries(entries, history_file=history_file) == 0
    loaded = read_recent_history(limit=5, history_file=history_file)
    assert len(loaded) == 1
    assert loaded[0]["title"] == "XAUUSD 点差高警戒"
    shutil.rmtree(history_dir)


def test_summarize_recent_history_aggregates_categories():
    history_dir = ROOT / ".runtime_test_stats"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    entries = [
        {
            "occurred_at": "2026-04-10 09:00:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-1",
        },
        {
            "occurred_at": "2026-04-11 11:30:00",
            "category": "macro",
            "title": "宏观提醒",
            "detail": "非农前先看点差。",
            "tone": "warning",
            "signature": "macro-1",
        },
        {
            "occurred_at": "2026-04-11 14:30:00",
            "category": "recovery",
            "title": "XAUUSD 点差已恢复",
            "detail": "当前点差已回落。",
            "tone": "success",
            "signature": "recovery-1",
        },
        {
            "occurred_at": "2026-04-12 07:45:00",
            "category": "session",
            "title": "休市 / 暂停提醒",
            "detail": "USDJPY 当前休市。",
            "tone": "accent",
            "signature": "session-1",
        },
    ]
    assert append_history_entries(entries, history_file=history_file) == 4
    stats = summarize_recent_history(days=7, history_file=history_file, now=datetime(2026, 4, 12, 12, 0, 0))
    assert stats["total_count"] == 4
    assert stats["spread_count"] == 1
    assert stats["recovery_count"] == 1
    assert stats["macro_count"] == 1
    assert stats["session_count"] == 1
    assert stats["latest_title"] == "休市 / 暂停提醒"
    assert "最近 7 天共记录 4 条关键提醒" in stats["summary_text"]
    shutil.rmtree(history_dir)


def test_build_snapshot_history_entries_adds_spread_recovery_when_latest_event_was_spread():
    history_dir = ROOT / ".runtime_test_recovery"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    append_history_entries(
        [
            {
                "occurred_at": "2026-04-12 10:00:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-recovery-source",
                "symbol": "XAUUSD",
            }
        ],
        history_file=history_file,
    )

    snapshot = {
        "last_refresh_text": "2026-04-12 10:20:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "报价重新干净。",
        "trade_next_review": "继续观察。",
        "runtime_status_cards": [],
        "spread_focus_cards": [{"title": "点差状态稳定", "detail": "当前稳定。", "tone": "success"}],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 18.0,
                "tone": "success",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "报价重新干净。",
                "trade_next_review": "继续观察。",
            }
        ],
        "alert_text": "",
    }
    entries = build_snapshot_history_entries(snapshot, history_file=history_file)
    recovery_entry = next(item for item in entries if item["category"] == "recovery")
    assert recovery_entry["title"] == "XAUUSD 点差已恢复"
    assert "已明显收敛" in recovery_entry["detail"]
    shutil.rmtree(history_dir)


def test_build_snapshot_history_entries_skips_recovery_when_latest_event_already_recovered():
    history_dir = ROOT / ".runtime_test_recovery_skip"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    append_history_entries(
        [
            {
                "occurred_at": "2026-04-12 10:00:00",
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-recovery-old",
                "symbol": "XAUUSD",
            },
            {
                "occurred_at": "2026-04-12 10:10:00",
                "category": "recovery",
                "title": "XAUUSD 点差已恢复",
                "detail": "当前点差已回落。",
                "tone": "success",
                "signature": "spread-recovery-new",
                "symbol": "XAUUSD",
            },
        ],
        history_file=history_file,
    )

    snapshot = {
        "last_refresh_text": "2026-04-12 10:20:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "报价重新干净。",
        "trade_next_review": "继续观察。",
        "runtime_status_cards": [],
        "spread_focus_cards": [{"title": "点差状态稳定", "detail": "当前稳定。", "tone": "success"}],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 18.0,
                "tone": "success",
                "has_live_quote": True,
                "trade_grade": "可轻仓试仓",
                "trade_grade_detail": "报价重新干净。",
                "trade_next_review": "继续观察。",
            }
        ],
        "alert_text": "",
    }
    entries = build_snapshot_history_entries(snapshot, history_file=history_file)
    assert not any(item["category"] == "recovery" for item in entries)
    shutil.rmtree(history_dir)


def test_summarize_effectiveness_marks_spread_alert_effective():
    history_dir = ROOT / ".runtime_test_effective"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    entries = [
        {
            "occurred_at": "2026-04-12 10:00:00",
            "category": "spread",
            "title": "XAUUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-effective",
            "symbol": "XAUUSD",
            "baseline_latest_price": 4700.0,
            "baseline_spread_points": 80.0,
        }
    ]
    append_history_entries(entries, history_file=history_file)
    snapshot = {
        "last_refresh_text": "2026-04-12 10:45:00",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4720.0,
                "spread_points": 70.0,
            }
        ],
    }
    stats = summarize_effectiveness(snapshot, history_file=history_file, now=datetime(2026, 4, 12, 10, 45, 0))
    assert stats["evaluated_count"] == 1
    assert stats["effective_count"] == 1
    assert stats["ineffective_count"] == 0
    shutil.rmtree(history_dir)


def test_summarize_effectiveness_marks_spread_alert_waiting_and_stale():
    history_dir = ROOT / ".runtime_test_effect_states"
    if history_dir.exists():
        shutil.rmtree(history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    history_file = history_dir / "alert_history.jsonl"
    entries = [
        {
            "occurred_at": "2026-04-12 11:50:00",
            "category": "spread",
            "title": "EURUSD 点差偏宽",
            "detail": "当前点差偏宽。",
            "tone": "accent",
            "signature": "spread-waiting",
            "symbol": "EURUSD",
            "baseline_latest_price": 1.1700,
            "baseline_spread_points": 30.0,
        },
        {
            "occurred_at": "2026-04-12 08:00:00",
            "category": "spread",
            "title": "XAGUSD 点差高警戒",
            "detail": "当前点差明显放大。",
            "tone": "warning",
            "signature": "spread-stale",
            "symbol": "XAGUSD",
            "baseline_latest_price": 76.0,
            "baseline_spread_points": 120.0,
        },
    ]
    append_history_entries(entries, history_file=history_file)
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "items": [
            {
                "symbol": "EURUSD",
                "latest_price": 1.1701,
                "spread_points": 12.0,
            }
        ],
    }
    stats = summarize_effectiveness(snapshot, history_file=history_file, now=datetime(2026, 4, 12, 12, 0, 0))
    assert stats["waiting_count"] == 1
    assert stats["stale_count"] == 1
    shutil.rmtree(history_dir)
