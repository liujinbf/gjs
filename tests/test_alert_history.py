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
from quote_models import SnapshotItem


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
        "event_active_importance_text": "高影响",
        "event_result_summary_text": "事件结果：美国 CPI：实际 3.4%，预期 3.2%，结果解读偏空。",
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 250.0,
                "point": 0.01,
                "has_live_quote": True,
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
    assert "美国 CPI 宏观提醒" in titles
    assert "点差状态稳定" not in titles
    spread_entry = next(item for item in entries if item["title"] == "XAUUSD 点差高警戒")
    assert spread_entry["trade_grade"] == "当前不宜出手"
    assert spread_entry["event_importance_text"] == "高影响"
    assert "高影响窗口" in spread_entry["event_note"]
    macro_entry = next(item for item in entries if item["title"] == "美国 CPI 宏观提醒")
    assert macro_entry["trade_grade"] == "当前不宜出手"
    assert macro_entry["symbol"] == "XAUUSD"
    assert macro_entry["baseline_latest_price"] == 4759.82


def test_build_snapshot_history_entries_skips_generic_macro_broadcast_without_actionable_event():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "只适合观察",
        "trade_grade_detail": "先观察。",
        "trade_next_review": "稍后再看。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 17.0,
                "point": 0.01,
                "has_live_quote": True,
                "trade_grade": "只适合观察",
            }
        ],
        "alert_text": "贵金属提醒：先看美元方向和点差。",
    }
    entries = build_snapshot_history_entries(snapshot)
    assert all(item["category"] != "macro" for item in entries)


def test_build_snapshot_history_entries_skips_medium_macro_broadcast_without_result():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "只适合观察",
        "trade_grade_detail": "先观察。",
        "trade_next_review": "稍后再看。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "event_risk_mode_text": "事件观察",
        "event_active_name": "欧元区工业产出",
        "event_active_time_text": "2026-04-12 15:00:00",
        "event_active_importance_text": "中影响",
        "items": [
            {
                "symbol": "EURUSD",
                "latest_price": 1.1727,
                "spread_points": 18.0,
                "point": 0.0001,
                "has_live_quote": True,
                "trade_grade": "只适合观察",
                "trade_grade_detail": "先看结构。",
                "event_applies": True,
                "event_note": "中影响窗口：欧元区工业产出将于 2026-04-12 15:00:00 落地。",
            }
        ],
        "alert_text": "宏观提醒：先看数据是否显著偏离预期。",
    }

    entries = build_snapshot_history_entries(snapshot)
    assert all(item["category"] != "macro" for item in entries)


def test_build_snapshot_history_entries_adds_structure_entry_with_action_meta():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可继续观察。",
        "trade_next_review": "10 分钟后复核。",
        "runtime_status_cards": [],
        "spread_focus_cards": [{"title": "点差状态稳定", "detail": "当前稳定。", "tone": "success"}],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4759.82,
                "spread_points": 17.0,
                "point": 0.01,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构相对干净，可继续观察。",
                "trade_next_review": "10 分钟后复核。",
                "signal_side_text": "【↑ 多头参考】",
                "risk_reward_ready": True,
                "risk_reward_state": "favorable",
                "risk_reward_ratio": 2.4,
                "risk_reward_context_text": "多头预估止损 4748.00，目标 4788.00，当前盈亏比约 2.40:1",
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
                "risk_reward_position_text": "可轻仓试仓，优先分两段止盈，第一目标落袋后再看延续。",
                "risk_reward_invalidation_text": "若价格重新跌回 4748.00 下方，当前多头结构可视为失效。",
            }
        ],
        "alert_text": "",
    }

    entries = build_snapshot_history_entries(snapshot)
    structure_entry = next(item for item in entries if item["category"] == "structure")
    assert structure_entry["title"] == "XAUUSD 结构候选"
    assert structure_entry["risk_reward_ratio"] == 2.4
    assert structure_entry["stop_loss_price"] == 4748.0
    assert structure_entry["take_profit_1"] == 4788.0
    assert structure_entry["take_profit_2"] == 4810.0
    assert structure_entry["baseline_bid"] == 0.0
    assert structure_entry["baseline_ask"] == 0.0
    assert "两段止盈" in structure_entry["position_plan_text"]
    assert "观察进场区间" in structure_entry["detail"]


def test_build_snapshot_history_entries_marks_structure_inside_entry_zone():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可继续观察。",
        "trade_next_review": "10 分钟后复核。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4764.0,
                "spread_points": 17.0,
                "point": 0.01,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构相对干净，可继续观察。",
                "trade_next_review": "10 分钟后复核。",
                "signal_side_text": "【↑ 多头参考】",
                "risk_reward_ready": True,
                "risk_reward_state": "favorable",
                "risk_reward_ratio": 2.1,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_low": 4760.0,
                "risk_reward_entry_zone_high": 4770.0,
                "risk_reward_entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
            }
        ],
        "alert_text": "",
    }

    entries = build_snapshot_history_entries(snapshot)
    structure_entry = next(item for item in entries if item["category"] == "structure")
    assert structure_entry["title"] == "XAUUSD 进入观察区间（中段）"
    assert structure_entry["structure_entry_stage"] == "inside_zone"
    assert structure_entry["entry_zone_distance"] == 0.0
    assert structure_entry["entry_zone_side"] == "middle"
    assert structure_entry["entry_zone_side_text"] == "中段"
    assert "已进入观察区间" in structure_entry["detail"]


def test_build_snapshot_history_entries_marks_structure_near_zone_upper_side():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可继续观察。",
        "trade_next_review": "10 分钟后复核。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "items": [
            {
                "symbol": "XAUUSD",
                "latest_price": 4772.0,
                "spread_points": 17.0,
                "point": 0.01,
                "has_live_quote": True,
                "tone": "success",
                "trade_grade": "可轻仓试仓",
                "trade_grade_source": "structure",
                "trade_grade_detail": "结构相对干净，可继续观察。",
                "trade_next_review": "10 分钟后复核。",
                "signal_side_text": "【↑ 多头参考】",
                "risk_reward_ready": True,
                "risk_reward_state": "favorable",
                "risk_reward_ratio": 2.1,
                "risk_reward_stop_price": 4748.0,
                "risk_reward_target_price": 4788.0,
                "risk_reward_target_price_2": 4810.0,
                "risk_reward_entry_zone_low": 4760.0,
                "risk_reward_entry_zone_high": 4770.0,
                "risk_reward_entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
            }
        ],
        "alert_text": "",
    }

    entries = build_snapshot_history_entries(snapshot)
    structure_entry = next(item for item in entries if item["category"] == "structure")
    assert structure_entry["title"] == "XAUUSD 靠近观察区间（上沿）"
    assert structure_entry["entry_zone_side"] == "upper"
    assert structure_entry["entry_zone_side_text"] == "上沿"
    assert "距离观察区间上沿约" in structure_entry["detail"]


def test_build_snapshot_history_entries_adds_external_source_alerts():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "只适合观察",
        "trade_grade_detail": "先观察。",
        "trade_next_review": "等待数据恢复。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "items": [],
        "alert_text": "",
        "macro_news_status_text": "外部资讯流拉取失败：network timeout",
        "macro_data_status_text": "结构化宏观数据拉取失败，继续使用20分钟前缓存：2 条。",
    }
    entries = build_snapshot_history_entries(snapshot)
    titles = [item["title"] for item in entries]
    assert "资讯流状态提醒" in titles
    assert "宏观数据状态提醒" in titles
    source_entry = next(item for item in entries if item["title"] == "资讯流状态提醒")
    assert source_entry["category"] == "source"
    assert source_entry["tone"] == "warning"


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


def test_build_snapshot_history_entries_accepts_snapshot_item_objects():
    snapshot = {
        "last_refresh_text": "2026-04-12 12:00:00",
        "trade_grade": "可轻仓试仓",
        "trade_grade_detail": "结构相对干净，可继续观察。",
        "trade_next_review": "10 分钟后复核。",
        "runtime_status_cards": [],
        "spread_focus_cards": [],
        "items": [
            SnapshotItem(
                symbol="XAUUSD",
                latest_price=4764.0,
                spread_points=17.0,
                point=0.01,
                has_live_quote=True,
                status_text="实时报价",
                execution_note="测试执行建议",
                trade_grade="可轻仓试仓",
                trade_grade_source="structure",
                trade_grade_detail="结构相对干净，可继续观察。",
                trade_next_review="10 分钟后复核。",
                alert_state_text="结构候选",
                alert_state_detail="当前执行面相对干净。",
                alert_state_tone="success",
                alert_state_rank=2,
                tone="success",
                signal_side="long",
                signal_side_text="【↑ 多头参考】",
                extra={
                    "risk_reward_ready": True,
                    "risk_reward_state": "favorable",
                    "risk_reward_ratio": 2.1,
                    "risk_reward_stop_price": 4748.0,
                    "risk_reward_target_price": 4788.0,
                    "risk_reward_target_price_2": 4810.0,
                    "risk_reward_entry_zone_low": 4760.0,
                    "risk_reward_entry_zone_high": 4770.0,
                    "risk_reward_entry_zone_text": "观察进场区间 4760.00 - 4770.00，若价格直接远离该区间，就不建议追。",
                },
            )
        ],
        "alert_text": "",
    }

    entries = build_snapshot_history_entries(snapshot)
    structure_entry = next(item for item in entries if item["category"] == "structure")
    assert structure_entry["title"] == "XAUUSD 进入观察区间（中段）"
    assert structure_entry["trade_grade_source"] == "structure"
