import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from event_schedule import (
    format_event_schedule_for_editor,
    normalize_event_schedule_text,
    parse_event_schedules,
    resolve_event_risk_context,
)


def test_parse_event_schedules_keeps_valid_entries_sorted():
    entries = parse_event_schedules(
        "2026-04-16 02:00|联储利率决议\n2026-04-15 20:30|美国 CPI\ninvalid"
    )
    assert [item["name"] for item in entries] == ["美国 CPI", "联储利率决议"]
    assert entries[0]["time_text"] == "2026-04-15 20:30"


def test_parse_event_schedules_supports_importance_and_symbols():
    entries = parse_event_schedules("2026-04-16 02:00|联储利率决议|high|XAUUSD,EURUSD")
    assert entries[0]["importance"] == "high"
    assert entries[0]["importance_text"] == "高影响"
    assert entries[0]["symbols"] == ["XAUUSD", "EURUSD"]


def test_normalize_event_schedule_text_merges_lines_to_single_line():
    text = normalize_event_schedule_text("2026-04-15 20:30|美国 CPI\n2026-04-16 02:00|联储利率决议")
    assert text == "2026-04-15 20:30|美国 CPI;2026-04-16 02:00|联储利率决议"
    assert "联储利率决议" in format_event_schedule_for_editor(text)


def test_normalize_event_schedule_text_keeps_structured_fields():
    text = normalize_event_schedule_text("2026-04-16 02:00|联储利率决议|high|XAUUSD,EURUSD")
    assert text == "2026-04-16 02:00|联储利率决议|high|XAUUSD,EURUSD"


def test_resolve_event_risk_context_enters_pre_event_window():
    context = resolve_event_risk_context(
        base_mode="normal",
        auto_enabled=True,
        schedule_text="2026-04-15 20:30|美国 CPI",
        pre_event_lead_min=60,
        post_event_window_min=15,
        now=datetime(2026, 4, 15, 19, 45, 0),
        symbols=["XAUUSD"],
    )
    assert context["mode"] == "pre_event"
    assert context["source"] == "auto"
    assert context["active_event_name"] == "美国 CPI"
    assert context["active_event_symbols"] == []


def test_resolve_event_risk_context_enters_post_event_window():
    context = resolve_event_risk_context(
        base_mode="normal",
        auto_enabled=True,
        schedule_text="2026-04-15 20:30|美国 CPI",
        pre_event_lead_min=60,
        post_event_window_min=20,
        now=datetime(2026, 4, 15, 20, 40, 0),
        symbols=["XAUUSD"],
    )
    assert context["mode"] == "post_event"
    assert context["source"] == "auto"
    assert "落地" in context["reason"]


def test_resolve_event_risk_context_keeps_illiquid_manual_priority():
    context = resolve_event_risk_context(
        base_mode="illiquid",
        auto_enabled=True,
        schedule_text="2026-04-15 20:30|美国 CPI",
        pre_event_lead_min=60,
        post_event_window_min=20,
        now=datetime(2026, 4, 15, 20, 0, 0),
        symbols=["XAUUSD"],
    )
    assert context["mode"] == "illiquid"
    assert context["source"] == "manual"


def test_resolve_event_risk_context_filters_irrelevant_symbols():
    context = resolve_event_risk_context(
        base_mode="normal",
        auto_enabled=True,
        schedule_text="2026-04-15 20:30|欧元区通胀|high|EURUSD",
        pre_event_lead_min=60,
        post_event_window_min=20,
        now=datetime(2026, 4, 15, 20, 0, 0),
        symbols=["XAUUSD"],
    )
    assert context["mode"] == "normal"
    assert "暂无与你的观察品种直接相关的事件" in context["reason"]


def test_resolve_event_risk_context_high_impact_extends_pre_window():
    context = resolve_event_risk_context(
        base_mode="normal",
        auto_enabled=True,
        schedule_text="2026-04-15 20:30|联储利率决议|high|XAUUSD",
        pre_event_lead_min=60,
        post_event_window_min=20,
        now=datetime(2026, 4, 15, 19, 5, 0),
        symbols=["XAUUSD"],
    )
    assert context["mode"] == "pre_event"
    assert context["active_event_importance_text"] == "高影响"
    assert context["active_event_symbols"] == ["XAUUSD"]
