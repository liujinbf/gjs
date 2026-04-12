import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from macro_focus import build_global_market_focus, build_symbol_macro_focus
from monitor_engine import (
    build_portfolio_trade_grade,
    build_quote_risk_note,
    build_quote_structure_text,
    build_snapshot_from_rows,
    build_trade_grade,
)


def test_build_symbol_macro_focus_for_gold():
    assert "非农" in build_symbol_macro_focus("XAUUSD")


def test_build_global_market_focus_contains_precious_alert():
    payload = build_global_market_focus(["XAUUSD", "EURUSD"])
    assert "贵金属提醒" in payload["alert_text"]
    assert "外汇提醒" in payload["alert_text"]


def test_build_quote_structure_text_renders_bid_ask_and_spread():
    text = build_quote_structure_text(
        {
            "latest_price": 4759.82,
            "bid": 4759.74,
            "ask": 4759.91,
            "spread_points": 17,
            "point": 0.01,
        }
    )
    assert "Bid" in text
    assert "Ask" in text
    assert "点差 17点" in text


def test_build_quote_risk_note_marks_wide_spread_warning():
    tone, note = build_quote_risk_note(
        "XAUUSD",
        {
            "latest_price": 4759.82,
            "bid": 4758.00,
            "ask": 4760.50,
            "spread_points": 250,
            "point": 0.01,
        },
    )
    assert tone == "warning"
    assert "点差明显放大" in note


def test_build_trade_grade_marks_gold_as_light_probe_when_quote_is_clean():
    grade = build_trade_grade(
        "XAUUSD",
        {
            "latest_price": 4759.82,
            "bid": 4759.74,
            "ask": 4759.91,
            "spread_points": 17,
            "point": 0.01,
            "status": "实时报价",
            "has_live_quote": True,
        },
        "success",
        True,
    )
    assert grade["grade"] == "可轻仓试仓"
    assert "轻仓" in grade["next_review"] or "轻仓" in grade["detail"]


def test_build_trade_grade_marks_fx_as_wait_event_when_spread_is_wide():
    grade = build_trade_grade(
        "EURUSD",
        {
            "latest_price": 1.17270,
            "bid": 1.17259,
            "ask": 1.17295,
            "spread_points": 36,
            "point": 0.00001,
            "status": "实时报价",
            "has_live_quote": True,
        },
        "accent",
        True,
    )
    assert grade["grade"] == "等待事件落地"
    assert "消息" in grade["detail"] or "波动" in grade["detail"]


def test_build_trade_grade_respects_pre_event_mode():
    grade = build_trade_grade(
        "XAUUSD",
        {
            "latest_price": 4759.82,
            "bid": 4759.74,
            "ask": 4759.91,
            "spread_points": 17,
            "point": 0.01,
            "status": "实时报价",
            "has_live_quote": True,
        },
        "success",
        True,
        event_risk_mode="pre_event",
    )
    assert grade["grade"] == "等待事件落地"
    assert "高敏" in grade["detail"] or "事件" in grade["detail"]


def test_build_portfolio_trade_grade_prefers_no_trade_when_risky_symbol_exists():
    grade = build_portfolio_trade_grade(
        [
            {"symbol": "XAUUSD", "trade_grade": "当前不宜出手"},
            {"symbol": "XAGUSD", "trade_grade": "可轻仓试仓"},
        ],
        connected=True,
    )
    assert grade["grade"] == "当前不宜出手"
    assert "XAUUSD" in grade["detail"]


def test_build_portfolio_trade_grade_respects_illiquid_mode():
    grade = build_portfolio_trade_grade(
        [{"symbol": "XAUUSD", "trade_grade": "可轻仓试仓"}],
        connected=True,
        event_risk_mode="illiquid",
    )
    assert grade["grade"] == "当前不宜出手"
    assert "流动性偏弱" in grade["detail"]


def test_build_snapshot_from_rows_keeps_all_symbols():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD", "XAGUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "bid": 4759.74,
                "ask": 4759.91,
                "spread_points": 17,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="normal",
    )
    assert snapshot["watch_count"] == 2
    assert len(snapshot["items"]) == 2
    assert snapshot["items"][0]["symbol"] == "XAUUSD"
    assert snapshot["items"][1]["symbol"] == "XAGUSD"
    assert snapshot["spread_focus_cards"]
    assert snapshot["event_window_cards"]
    assert snapshot["trade_grade"] in {"当前不宜出手", "只适合观察", "可轻仓试仓", "等待事件落地"}
    assert "trade_grade" in snapshot["items"][0]
    assert "trade_next_review" in snapshot["items"][0]
    assert snapshot["items"][0]["latest_price"] == 4759.82
    assert snapshot["items"][0]["spread_points"] == 17.0


def test_build_snapshot_from_rows_creates_warning_spread_focus_card():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "bid": 4758.00,
                "ask": 4760.50,
                "spread_points": 250,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="normal",
    )
    assert snapshot["spread_focus_cards"][0]["tone"] == "warning"
    assert "点差高警戒" in snapshot["spread_focus_cards"][0]["title"]


def test_build_snapshot_from_rows_adds_event_window_disclaimer():
    snapshot = build_snapshot_from_rows(
        ["EURUSD"],
        [],
        False,
        "MT5 未连接。",
        event_risk_mode="normal",
    )
    details = " ".join(card["detail"] for card in snapshot["event_window_cards"])
    assert "结构性提醒" in details


def test_build_snapshot_from_rows_adds_connected_runtime_status_cards():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD", "EURUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "bid": 4759.74,
                "ask": 4759.91,
                "spread_points": 17,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
            },
            {
                "symbol": "EURUSD",
                "latest_price": 1.17270,
                "bid": 1.17259,
                "ask": 1.17280,
                "spread_points": 21,
                "point": 0.00001,
                "status": "休市或暂无实时报价",
                "has_live_quote": False,
            },
        ],
        True,
        "MT5 连接成功：terminal64.exe",
        event_risk_mode="normal",
    )
    assert snapshot["runtime_status_cards"][0]["title"] == "MT5 终端已连通"
    assert "2 个品种" in snapshot["runtime_status_cards"][0]["detail"]
    assert snapshot["runtime_status_cards"][1]["title"] == "休市 / 暂停提醒"
    assert "EURUSD" in snapshot["runtime_status_cards"][1]["detail"]


def test_build_snapshot_from_rows_adds_disconnected_runtime_status_cards():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD", "XAGUSD"],
        [],
        False,
        "MT5 初始化失败。",
        event_risk_mode="normal",
    )
    assert snapshot["runtime_status_cards"][0]["title"] == "MT5 终端未连通"
    assert "MT5 初始化失败" in snapshot["runtime_status_cards"][0]["detail"]
    assert snapshot["runtime_status_cards"][1]["title"] == "等待连接后再判断时段"
    assert snapshot["trade_grade"] == "当前不宜出手"


def test_build_snapshot_from_rows_includes_event_mode_text():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "bid": 4759.74,
                "ask": 4759.91,
                "spread_points": 17,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="pre_event",
    )
    assert snapshot["event_risk_mode"] == "pre_event"
    assert snapshot["event_risk_mode_text"] == "事件前高敏"
    assert snapshot["trade_grade"] == "等待事件落地"


def test_build_snapshot_from_rows_includes_event_context_reason():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "bid": 4759.74,
                "ask": 4759.91,
                "spread_points": 17,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="pre_event",
        event_context={
            "mode": "pre_event",
            "mode_text": "事件前高敏",
            "source": "auto",
            "source_text": "自动模式",
            "reason": "美国 CPI 将在 2026-04-15 20:30 落地，当前自动进入事件前高敏阶段。",
            "next_event_name": "美国 CPI",
            "next_event_time_text": "2026-04-15 20:30",
        },
    )
    assert snapshot["event_risk_mode_source"] == "auto"
    assert snapshot["event_risk_mode_source_text"] == "自动模式"
    assert "纪律说明" in snapshot["summary_text"]
    assert snapshot["event_window_cards"][0]["title"].startswith("纪律模式")
