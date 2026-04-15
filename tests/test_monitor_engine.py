import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from alert_history import append_history_entries
from macro_focus import build_global_market_focus, build_symbol_macro_focus
from monitor_engine import (
    build_portfolio_trade_grade,
    build_quote_risk_note,
    build_quote_structure_text,
    build_snapshot_from_rows,
    build_trade_grade,
)
from quote_models import QuoteRow, SnapshotItem


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
        event_context={
            "active_event_name": "联储利率决议",
            "active_event_time_text": "2026-04-16 02:00",
            "active_event_importance": "high",
            "active_event_importance_text": "高影响",
            "active_event_scope_text": "XAUUSD",
            "active_event_symbols": ["XAUUSD"],
        },
    )
    assert grade["grade"] == "当前不宜出手"
    assert "高影响" in grade["detail"]


def test_build_trade_grade_skips_unrelated_event_window_for_other_symbol():
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
        event_context={
            "active_event_name": "欧元区通胀",
            "active_event_time_text": "2026-04-15 20:30",
            "active_event_importance": "high",
            "active_event_importance_text": "高影响",
            "active_event_scope_text": "EURUSD",
            "active_event_symbols": ["EURUSD"],
        },
    )
    assert grade["grade"] == "可轻仓试仓"


def test_build_trade_grade_uses_intraday_context_to_downgrade_quiet_market():
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
            "intraday_context_ready": True,
            "intraday_context_text": "近1小时震荡，处于区间中段，波动偏静",
            "intraday_bias": "sideways",
            "intraday_volatility": "low",
            "intraday_location": "middle",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "近1小时震荡" in grade["detail"]


def test_build_trade_grade_downgrades_when_multi_timeframe_conflicts():
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
            "intraday_context_ready": True,
            "intraday_context_text": "近1小时偏多，贴近区间高位，波动正常",
            "intraday_bias": "bullish",
            "intraday_volatility": "normal",
            "intraday_location": "upper",
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "mixed",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏空 / H1 震荡，多周期方向分歧",
            "multi_timeframe_bias": "mixed",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "多周期方向分歧" in grade["detail"]


def test_build_trade_grade_avoids_chasing_near_high_even_when_aligned():
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
            "intraday_context_ready": True,
            "intraday_context_text": "近1小时偏多，贴近区间高位，波动正常",
            "intraday_bias": "bullish",
            "intraday_volatility": "normal",
            "intraday_location": "upper",
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "key_level_ready": True,
            "key_level_state": "near_high",
            "key_level_context_text": "当前贴近近12小时高位，位置偏贵，先别直接追多",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "先别直接追多" in grade["detail"]


def test_build_trade_grade_waits_when_breakout_is_pending():
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
            "intraday_context_ready": True,
            "intraday_context_text": "近1小时偏多，贴近区间高位，波动正常",
            "intraday_bias": "bullish",
            "intraday_volatility": "normal",
            "intraday_location": "upper",
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "breakout_ready": True,
            "breakout_state": "pending_above",
            "breakout_direction": "bullish",
            "breakout_context_text": "价格正在尝试上破高位，但还需要再看一到两根 M5 收线确认",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "还需要再看一到两根 M5 收线确认" in grade["detail"]


def test_build_trade_grade_supports_confirmed_breakout_for_metal():
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
            "intraday_context_ready": True,
            "intraday_context_text": "近1小时偏多，贴近区间高位，波动正常",
            "intraday_bias": "bullish",
            "intraday_volatility": "normal",
            "intraday_location": "upper",
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "key_level_ready": True,
            "key_level_state": "breakout_above",
            "key_level_context_text": "近12小时刚上破高点，先等回踩确认，别在第一脚追多",
            "breakout_ready": True,
            "breakout_state": "confirmed_above",
            "breakout_direction": "bullish",
            "breakout_context_text": "M5 连续收在关键位上方，属于已确认上破",
        },
        "success",
        True,
    )
    assert grade["grade"] == "可轻仓试仓"
    assert "已确认上破" in grade["detail"]


def test_build_trade_grade_prefers_retest_confirmed_setup():
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
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "breakout_ready": True,
            "breakout_state": "confirmed_above",
            "breakout_direction": "bullish",
            "breakout_context_text": "M5 连续收在关键位上方，属于已确认上破",
            "retest_ready": True,
            "retest_state": "confirmed_support",
            "retest_context_text": "上破后的回踩已经守住突破位，可以继续观察是否走二次上攻",
        },
        "success",
        True,
    )
    assert grade["grade"] == "可轻仓试仓"
    assert "回踩已经守住突破位" in grade["detail"]


def test_build_trade_grade_downgrades_when_retest_fails():
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
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "retest_ready": True,
            "retest_state": "failed_support",
            "retest_context_text": "上破后回踩已经跌回突破位下方，强度不足，疑似假动作",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "疑似假动作" in grade["detail"]


def test_build_trade_grade_downgrades_when_risk_reward_is_poor():
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
            "multi_timeframe_context_ready": True,
            "multi_timeframe_alignment": "aligned",
            "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
            "multi_timeframe_bias": "bullish",
            "risk_reward_ready": True,
            "risk_reward_state": "poor",
            "risk_reward_context_text": "多头预估止损 4752.00，目标 4763.00，当前盈亏比约 0.52:1",
        },
        "success",
        True,
    )
    assert grade["grade"] == "只适合观察"
    assert "盈亏比约 0.52:1" in grade["detail"]


def test_build_portfolio_trade_grade_prefers_no_trade_when_risky_symbol_exists():
    grade = build_portfolio_trade_grade(
        [
            {"symbol": "XAUUSD", "trade_grade": "当前不宜出手", "trade_grade_source": "spread"},
            {"symbol": "XAGUSD", "trade_grade": "可轻仓试仓"},
        ],
        connected=True,
    )
    assert grade["grade"] == "当前不宜出手"
    assert "XAUUSD" in grade["detail"]


def test_build_portfolio_trade_grade_keeps_candidate_when_other_symbol_only_blocked_by_event():
    grade = build_portfolio_trade_grade(
        [
            {"symbol": "XAUUSD", "trade_grade": "可轻仓试仓", "trade_grade_source": "structure"},
            {"symbol": "EURUSD", "trade_grade": "当前不宜出手", "trade_grade_source": "event"},
        ],
        connected=True,
        event_risk_mode="pre_event",
        event_context={
            "active_event_name": "欧元区通胀",
            "active_event_importance": "high",
            "active_event_importance_text": "高影响",
            "active_event_symbols": ["EURUSD"],
        },
    )
    assert grade["grade"] == "可轻仓试仓"
    assert "EURUSD" in grade["detail"]


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
                "quote_status_code": "live",
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
    assert snapshot["items"][0]["quote_status_code"] == "live"
    assert snapshot["spread_focus_cards"]
    assert snapshot["event_window_cards"]
    assert snapshot["alert_status_cards"]
    assert snapshot["trade_grade"] in {"当前不宜出手", "只适合观察", "可轻仓试仓", "等待事件落地"}
    assert "trade_grade" in snapshot["items"][0]
    assert "trade_next_review" in snapshot["items"][0]
    assert snapshot["items"][0]["latest_price"] == 4759.82
    assert snapshot["items"][0]["spread_points"] == 17.0
    assert "alert_state_text" in snapshot["items"][0]


def test_build_snapshot_from_rows_exposes_risk_reward_fields_and_hides_inactive_prices():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD", "EURUSD"],
        [
            {
                "symbol": "XAUUSD",
                "latest_price": 4810.0,
                "bid": 4809.9,
                "ask": 4810.1,
                "spread_points": 20,
                "point": 0.01,
                "status": "实时报价",
                "has_live_quote": True,
                "intraday_bias": "bullish",
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_bias": "bullish",
                "breakout_state": "confirmed_above",
                "breakout_direction": "bullish",
                "key_level_high": 4800.0,
                "key_level_low": 4700.0,
                "key_level_state": "breakout_above",
                "atr14": 10.0,
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
        "MT5 连接成功。",
        event_risk_mode="normal",
    )
    xau = next(item for item in snapshot["items"] if item["symbol"] == "XAUUSD")
    eur = next(item for item in snapshot["items"] if item["symbol"] == "EURUSD")
    assert xau["risk_reward_ready"] is True
    assert xau["atr14"] == 10.0
    assert xau["risk_reward_ratio"] > 0
    assert xau["risk_reward_stop_price"] > 0
    assert xau["risk_reward_target_price"] > 0
    assert xau["risk_reward_target_price_2"] > xau["risk_reward_target_price"]
    assert xau["risk_reward_position_text"]
    assert xau["risk_reward_invalidation_text"]
    assert xau["risk_reward_entry_zone_low"] > 0
    assert xau["risk_reward_entry_zone_high"] >= xau["risk_reward_entry_zone_low"]
    assert xau["risk_reward_entry_zone_text"]
    assert "ATR(14)" in xau["risk_reward_context_text"]
    assert eur["latest_text"] == "--"
    assert "EURUSD" not in snapshot["live_digest"]


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
                "quote_status_code": "live",
                "has_live_quote": True,
            },
            {
                "symbol": "EURUSD",
                "latest_price": 1.17270,
                "bid": 1.17259,
                "ask": 1.17280,
                "spread_points": 21,
                "point": 0.00001,
                "status": "经纪商返回静态报价",
                "quote_status_code": "inactive",
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


def test_build_snapshot_from_rows_only_downgrades_related_symbol_in_event_window():
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
                "status": "实时报价",
                "has_live_quote": True,
            },
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="pre_event",
        event_context={
            "mode": "pre_event",
            "mode_text": "事件前高敏",
            "source": "auto",
            "source_text": "自动模式",
            "reason": "欧元区通胀将落地，EURUSD 当前自动进入事件前高敏阶段。",
            "active_event_name": "欧元区通胀",
            "active_event_time_text": "2026-04-15 20:30",
            "active_event_importance": "high",
            "active_event_importance_text": "高影响",
            "active_event_scope_text": "EURUSD",
            "active_event_symbols": ["EURUSD"],
            "next_event_name": "欧元区通胀",
            "next_event_time_text": "2026-04-15 20:30",
        },
    )
    grades = {item["symbol"]: item["trade_grade"] for item in snapshot["items"]}
    assert grades["EURUSD"] == "当前不宜出手"
    assert grades["XAUUSD"] == "可轻仓试仓"
    assert snapshot["trade_grade"] == "可轻仓试仓"
    eur_item = next(item for item in snapshot["items"] if item["symbol"] == "EURUSD")
    assert "高影响窗口" in eur_item["execution_note"]
    assert eur_item["event_note"]
    assert eur_item["alert_state_text"] == "高影响事件前"


def test_build_snapshot_from_rows_includes_intraday_context_digest(tmp_path):
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
                "intraday_context_ready": True,
                "intraday_context_text": "近1小时偏多，贴近区间高位，波动正常",
                "intraday_bias_text": "偏多",
                "intraday_volatility_text": "波动正常",
                "intraday_location_text": "贴近区间高位",
                "intraday_bias": "bullish",
                "intraday_volatility": "normal",
                "intraday_location": "upper",
                "multi_timeframe_context_ready": True,
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_alignment_text": "多周期同向",
                "multi_timeframe_bias": "bullish",
                "multi_timeframe_bias_text": "偏多",
                "multi_timeframe_context_text": "M5 偏多 / M15 偏多 / H1 震荡，多周期同向偏多",
                "key_level_ready": True,
                "key_level_state": "near_high",
                "key_level_state_text": "贴近高位",
                "key_level_context_text": "当前贴近近12小时高位，位置偏贵，先别直接追多",
                "breakout_ready": True,
                "breakout_state": "pending_above",
                "breakout_state_text": "上破待确认",
                "breakout_context_text": "价格正在尝试上破高位，但还需要再看一到两根 M5 收线确认",
                "retest_ready": False,
                "retest_state": "none",
                "retest_state_text": "暂无回踩",
                "retest_context_text": "",
                "key_level_high": 4765.0,
                "key_level_low": 4725.0,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="normal",
        history_file=tmp_path / "alert_history.jsonl",  # 隔离历史文件，防止全局记录干扰
    )
    assert "短线节奏" in snapshot["summary_text"]
    assert "多周期一致性" in snapshot["summary_text"]
    assert "关键位" in snapshot["summary_text"]
    assert "突破确认" in snapshot["summary_text"]
    assert "回踩确认" not in snapshot["summary_text"]
    assert "风险回报" in snapshot["summary_text"]
    assert "近1小时偏多" in snapshot["items"][0]["execution_note"]
    assert "多周期同向偏多" in snapshot["items"][0]["execution_note"]
    assert "先别直接追多" in snapshot["items"][0]["execution_note"]
    assert "上破高位" in snapshot["items"][0]["execution_note"]
    assert "盈亏比" in snapshot["items"][0]["execution_note"]
    assert snapshot["items"][0]["alert_state_text"] == "报价正常观察"


def test_build_snapshot_from_rows_marks_recovered_symbol_status_from_history(tmp_path):
    from datetime import datetime, timedelta
    recent_time = (datetime.now() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    history_file = tmp_path / "alert_history.jsonl"
    append_history_entries(
        [
            {
                "occurred_at": recent_time,
                "category": "spread",
                "title": "XAUUSD 点差高警戒",
                "detail": "当前点差明显放大。",
                "tone": "warning",
                "signature": "spread-recovery-status-1",
                "symbol": "XAUUSD",
            }
        ],
        history_file=history_file,
    )

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
        event_risk_mode="normal",
        history_file=history_file,
    )
    item = snapshot["items"][0]
    assert item["alert_state_text"] == "点差已恢复"
    assert "已明显收敛" in item["alert_state_detail"]
    assert any("点差已恢复" in card["title"] for card in snapshot["alert_status_cards"])


def test_build_snapshot_from_rows_tracks_alert_state_transition(tmp_path):
    status_state_file = tmp_path / "alert_status_state.json"

    first_snapshot = build_snapshot_from_rows(
        ["EURUSD"],
        [
            {
                "symbol": "EURUSD",
                "latest_price": 1.17270,
                "bid": 1.17259,
                "ask": 1.17280,
                "spread_points": 21,
                "point": 0.00001,
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
            "active_event_name": "欧元区通胀",
            "active_event_time_text": "2026-04-15 20:30",
            "active_event_importance_text": "高影响",
            "active_event_symbols": ["EURUSD"],
        },
        status_state_file=status_state_file,
    )
    assert first_snapshot["items"][0]["alert_state_transition_text"] == ""

    second_snapshot = build_snapshot_from_rows(
        ["EURUSD"],
        [
            {
                "symbol": "EURUSD",
                "latest_price": 1.17270,
                "bid": 1.17259,
                "ask": 1.17280,
                "spread_points": 21,
                "point": 0.00001,
                "status": "实时报价",
                "has_live_quote": True,
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="post_event",
        event_context={
            "mode": "post_event",
            "mode_text": "事件落地观察",
            "active_event_name": "欧元区通胀",
            "active_event_time_text": "2026-04-15 20:30",
            "active_event_importance_text": "高影响",
            "active_event_symbols": ["EURUSD"],
        },
        status_state_file=status_state_file,
    )
    item = second_snapshot["items"][0]
    assert "高影响事件前 -> 高影响事件后观察" in item["alert_state_transition_text"]
    assert second_snapshot["alert_status_cards"][0]["title"] == "最近30分钟状态迁移"
    assert any("状态迁移" in card["detail"] for card in second_snapshot["alert_status_cards"])
    assert "近30分钟迁移" in second_snapshot["summary_text"]
    assert "EURUSD：高影响事件前 -> 高影响事件后观察" in second_snapshot["alert_transition_summary_text"]


def test_build_snapshot_from_rows_includes_regime_summary():
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
                "atr14": 16.0,
                "intraday_volatility": "normal",
                "intraday_bias": "bullish",
                "multi_timeframe_alignment": "aligned",
                "multi_timeframe_bias": "bullish",
                "breakout_state": "confirmed_above",
                "retest_state": "confirmed_support",
            }
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="normal",
    )
    assert snapshot["regime_tag"] == "trend_expansion"
    assert "趋势扩张" in snapshot["regime_summary_text"]
    assert snapshot["items"][0]["regime_text"] == "趋势扩张"


def test_build_snapshot_from_rows_accepts_quote_row_objects():
    snapshot = build_snapshot_from_rows(
        ["XAUUSD"],
        [
            QuoteRow(
                symbol="XAUUSD",
                latest_price=4759.82,
                bid=4759.74,
                ask=4759.91,
                spread_points=17.0,
                point=0.01,
                tick_time=1000,
                status="实时报价",
                quote_status_code="live",
                has_live_quote=True,
                extra={"intraday_bias": "bullish"},
            )
        ],
        True,
        "MT5 连接成功。",
        event_risk_mode="normal",
    )
    item = snapshot["items"][0]
    assert item["symbol"] == "XAUUSD"
    assert item["quote_status_code"] == "live"
    assert item["latest_text"] != "--"


def test_snapshot_item_exports_core_fields():
    item = SnapshotItem(
        symbol="XAUUSD",
        latest_price=4759.82,
        spread_points=17.0,
        point=0.01,
        has_live_quote=True,
        bid=4759.74,
        ask=4759.91,
        tick_time=1000,
        latest_text="4,759.82",
        quote_text="Bid 4759.74 / Ask 4759.91 · 点差 17点",
        status_text="实时报价",
        quote_status_code="live",
        execution_note="测试执行建议",
        trade_grade="可轻仓试仓",
        trade_grade_detail="结构干净",
        trade_next_review="15 分钟后复核",
        trade_grade_source="structure",
        event_importance_text="高影响",
        event_note="美国 CPI 即将公布。",
        macro_focus="关注黄金与美元方向。",
        alert_state_text="结构候选",
        alert_state_detail="当前执行面相对干净",
        alert_state_tone="success",
        alert_state_rank=2,
        regime_tag="trend_expansion",
        regime_text="趋势扩张",
        regime_reason="多周期同向偏多",
        regime_rank=5,
        tone="success",
        signal_side="long",
        signal_side_text="【↑ 多头参考】",
        intraday_bias="bullish",
        intraday_bias_text="偏多",
        multi_timeframe_alignment="aligned",
        multi_timeframe_alignment_text="多周期同向",
        multi_timeframe_bias="bullish",
        multi_timeframe_bias_text="偏多",
        breakout_direction="bullish",
        breakout_state="confirmed_above",
        breakout_state_text="上破已确认",
        retest_state="confirmed_support",
        retest_state_text="回踩已确认",
        risk_reward_ready=True,
        risk_reward_state="good",
        risk_reward_state_text="盈亏比优秀",
        risk_reward_ratio=1.8,
        risk_reward_stop_price=4748.0,
        risk_reward_target_price=4788.0,
        risk_reward_target_price_2=4810.0,
        risk_reward_entry_zone_low=4750.0,
        risk_reward_entry_zone_high=4765.0,
        risk_reward_atr=18.0,
        atr14=18.0,
        atr14_h4=42.0,
        model_ready=True,
        model_win_probability=0.74,
        model_confidence_text="中等信心",
        model_note="本地模型参考胜率约 74%。",
        snapshot_id=88,
        extra={"intraday_context_text": "近1小时偏多"},
    ).to_dict()

    assert item["symbol"] == "XAUUSD"
    assert item["trade_grade_source"] == "structure"
    assert item["event_importance_text"] == "高影响"
    assert item["macro_focus"] == "关注黄金与美元方向。"
    assert item["alert_state_text"] == "结构候选"
    assert item["regime_text"] == "趋势扩张"
    assert item["intraday_bias"] == "bullish"
    assert item["multi_timeframe_alignment"] == "aligned"
    assert item["risk_reward_ratio"] == 1.8
    assert item["atr14"] == 18.0
    assert item["model_win_probability"] == 0.74
    assert item["snapshot_id"] == 88


def test_snapshot_item_from_payload_normalizes_high_frequency_fields():
    item = SnapshotItem.from_payload(
        {
            "symbol": "xauusd",
            "event_importance_text": "高影响",
            "macro_focus": "关注黄金与美元方向。",
            "intraday_bias": "bullish",
            "multi_timeframe_alignment": "aligned",
            "risk_reward_ready": 1,
            "risk_reward_ratio": "1.8",
            "risk_reward_stop_price": "4748.0",
            "atr14": "18.0",
            "model_ready": 1,
            "model_win_probability": "0.74",
            "snapshot_id": "88",
        }
    ).to_dict()

    assert item["symbol"] == "XAUUSD"
    assert item["event_importance_text"] == "高影响"
    assert item["macro_focus"] == "关注黄金与美元方向。"
    assert item["intraday_bias"] == "bullish"
    assert item["multi_timeframe_alignment"] == "aligned"
    assert item["risk_reward_ready"] is True
    assert item["risk_reward_ratio"] == 1.8
    assert item["risk_reward_stop_price"] == 4748.0
    assert item["atr14"] == 18.0
    assert item["model_ready"] is True
    assert item["model_win_probability"] == 0.74
    assert item["snapshot_id"] == 88
    assert item["intraday_bias"] == "bullish"
