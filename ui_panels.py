import json
import re
import threading
from contextlib import closing
from datetime import datetime, timedelta

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QTabWidget, QToolButton, QVBoxLayout, QWidget, QHeaderView
)
from quote_models import SnapshotItem
from signal_enums import AlertTone, QuoteStatus, TradeGrade
import style
from alert_history import (
    read_full_history, read_recent_history, summarize_effectiveness, summarize_recent_history
)
from ai_history import (
    read_recent_ai_history, summarize_recent_ai_history
)
from app_config import (
    get_runtime_config,
    normalize_sim_strategy_cooldown_min,
    normalize_sim_strategy_daily_limit,
    normalize_sim_strategy_min_rr,
    save_runtime_config,
)
from execution_audit import (
    fetch_recent_execution_audits,
    summarize_execution_audits,
    summarize_execution_reason_counts,
    summarize_today_execution_audits,
)
from exploratory_replay import replay_exploratory_grade_gate
from knowledge_ai_signals import summarize_recent_ai_signals
from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection
from knowledge_governance import apply_strategy_learning_review, sync_strategy_learning_reviews
from runtime_utils import parse_time as _parse_time
from sim_signal_bridge import audit_rule_sim_signal_decision, build_rule_sim_signal_decision
from trade_learning import summarize_trade_learning_by_strategy


_DIRECTION_TEXT_MAP = {
    "long": "做多",
    "short": "做空",
    "bullish": "做多",
    "bearish": "做空",
}

_EXECUTION_REASON_LABEL_MAP = {
    "existing_position": "已有持仓",
    "margin_insufficient": "保证金不足",
    "meta_incomplete": "点位缺失",
    "direction_unclear": "方向不清",
    "no_machine_signal": "未出信号",
    "neutral_signal": "中性信号",
    "grade_gate": "观察级别",
    "live_auto_disabled": "自动实盘关闭",
    "engine_rejected": "执行被拒",
    "blocked": "规则阻塞",
    "skipped": "已跳过",
    "opened": "成功开仓",
    "take_profit": "止盈离场",
    "stop_loss": "止损离场",
    "break_even_exit": "保本离场",
    "margin_call": "爆仓离场",
    "closed": "已平仓",
    "exploratory_daily_limit": "探索上限",
    "exploratory_cooldown": "探索冷却",
}

_EXECUTION_STATUS_LABEL_MAP = {
    "opened": "已开仓",
    "closed": "已平仓",
    "blocked": "规则阻塞",
    "rejected": "执行拒绝",
    "skipped": "已跳过",
}

_STRATEGY_FAMILY_LABEL_MAP = {
    "pullback_sniper_probe": "回调狙击",
    "directional_probe": "方向试仓",
    "direct_momentum": "直线动能",
    "early_momentum": "早期动能",
    "structure": "结构候选",
    "setup": "Setup",
    "unknown": "未分类",
}


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一前台面板消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _format_quote_status_text(item: dict | SnapshotItem | None) -> str:
    """统一前台表格展示的报价状态语义。"""
    normalized = _normalize_snapshot_item(item)
    status_code = str(normalized.get("quote_status_code", "") or "").strip().lower()
    if status_code == QuoteStatus.LIVE:
        return "活跃报价"
    if status_code == QuoteStatus.INACTIVE:
        return "非活跃报价"
    if status_code == QuoteStatus.UNKNOWN_SYMBOL:
        return "未识别品种"
    if status_code == QuoteStatus.NOT_SELECTED:
        return "未加入市场报价"
    if status_code == QuoteStatus.ERROR:
        return "报价拉取异常"
    return str(normalized.get("status_text", "--") or "--").strip()


def _format_watch_quote_text(item: dict | SnapshotItem | None) -> str:
    """把观察表的盘口压成短格式，避免 Bid/Ask/点差被省略号截断。"""
    normalized = _normalize_snapshot_item(item)
    bid = float(normalized.get("bid", 0.0) or 0.0)
    ask = float(normalized.get("ask", 0.0) or 0.0)
    spread = float(normalized.get("spread_points", 0.0) or 0.0)
    point = float(normalized.get("point", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        decimals = 5 if 0 < point < 0.001 else 2
        if spread > 0:
            return f"{bid:.{decimals}f} / {ask:.{decimals}f} · {spread:.0f}点"
        return f"{bid:.{decimals}f} / {ask:.{decimals}f}"
    return str(normalized.get("quote_text", "--") or "--").strip()


def _format_watch_execution_note(item: dict | SnapshotItem | None) -> str:
    """优先使用当前分级语义，避免表格展示过期的执行建议。"""
    normalized = _normalize_snapshot_item(item)
    execution_note = str(normalized.get("execution_note", "--") or "--").strip()
    trade_grade = str(normalized.get("trade_grade", "") or "").strip()
    trade_grade_detail = str(normalized.get("trade_grade_detail", "") or "").strip()
    if not trade_grade or not trade_grade_detail:
        return execution_note

    current_note = f"{trade_grade}：{trade_grade_detail}"
    current_prefix = f"{trade_grade}："
    known_prefixes = tuple(f"{candidate.value}：" for candidate in TradeGrade)
    if not execution_note or execution_note == "--":
        return current_note
    if execution_note.startswith(current_prefix):
        return execution_note if trade_grade_detail in execution_note else current_note
    if execution_note.startswith(known_prefixes):
        return current_note
    return execution_note


def _row_value(row, key: str, default=0):
    """兼容 sqlite3.Row 与 dict 的轻量取值。"""
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:
        return default


def _resolve_display_direction(item: dict) -> tuple[str, str]:
    for key in ("signal_side", "risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = str(item.get(key, "") or "").strip().lower()
        if value in _DIRECTION_TEXT_MAP:
            action = "long" if value in {"long", "bullish"} else "short"
            return action, _DIRECTION_TEXT_MAP[value]
    return "neutral", "方向未明"


def _format_execution_profile_text(value: object) -> str:
    profile = str(value or "standard").strip().lower()
    if profile == "exploratory":
        return "探索"
    return "标准"


def _format_strategy_family_text(value: object) -> str:
    family = str(value or "").strip()
    if not family:
        return "--"
    return _STRATEGY_FAMILY_LABEL_MAP.get(family, family)


def _build_strategy_rr_summary(config=None, *, separator: str = " / ") -> tuple[str, str]:
    runtime_config = config or get_runtime_config()
    rr_map = normalize_sim_strategy_min_rr(getattr(runtime_config, "sim_strategy_min_rr", {}))
    daily_limit_map = normalize_sim_strategy_daily_limit(getattr(runtime_config, "sim_strategy_daily_limit", {}))
    cooldown_map = normalize_sim_strategy_cooldown_min(getattr(runtime_config, "sim_strategy_cooldown_min", {}))
    ordered_keys = ("early_momentum", "direct_momentum", "pullback_sniper_probe", "directional_probe")
    parts = []
    tooltip_lines = ["当前策略 RR 阈值："]
    for key in ordered_keys:
        label = _format_strategy_family_text(key)
        rr_value = float(rr_map.get(key, 0.0) or 0.0)
        daily_limit = int(daily_limit_map.get(key, 0) or 0)
        cooldown_min = int(cooldown_map.get(key, 0) or 0)
        parts.append(f"{label} {rr_value:.2f}R")
        tooltip_lines.append(
            f"{label}：{rr_value:.2f}R / 日上限 {daily_limit} 次 / 冷却 {cooldown_min} 分钟"
        )
    return separator.join(parts), "\n".join(tooltip_lines)


def _parse_strategy_param_snapshot(value: object) -> dict:
    payload = value
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _format_strategy_param_snapshot(value: object) -> str:
    payload = _parse_strategy_param_snapshot(value)
    if not payload:
        return ""
    family_label = _format_strategy_family_text(payload.get("strategy_family", ""))
    execution_profile = _format_execution_profile_text(payload.get("execution_profile", "standard"))
    min_rr = float(payload.get("min_rr", 0.0) or 0.0)
    daily_limit = int(payload.get("daily_limit", 0) or 0)
    cooldown_min = int(payload.get("cooldown_min", 0) or 0)
    return (
        f"命中参数：{family_label} / {execution_profile}"
        f"\n最小 RR：{min_rr:.2f}R"
        f"\n日上限：{daily_limit} 次"
        f"\n冷却：{cooldown_min} 分钟"
    )


def _build_current_strategy_param_snapshot(strategy_family: str, config=None) -> dict:
    family = str(strategy_family or "").strip().lower()
    if not family:
        return {}
    runtime_config = config or get_runtime_config()
    rr_map = normalize_sim_strategy_min_rr(getattr(runtime_config, "sim_strategy_min_rr", {}))
    daily_limit_map = normalize_sim_strategy_daily_limit(getattr(runtime_config, "sim_strategy_daily_limit", {}))
    cooldown_map = normalize_sim_strategy_cooldown_min(getattr(runtime_config, "sim_strategy_cooldown_min", {}))
    return {
        "strategy_family": family,
        "execution_profile": "standard",
        "min_rr": float(rr_map.get(family, 0.0) or 0.0),
        "daily_limit": int(daily_limit_map.get(family, 0) or 0),
        "cooldown_min": int(cooldown_map.get(family, 0) or 0),
    }


def _format_strategy_param_compare(snapshot_value: object, *, config=None, latest_apply: dict | None = None, closed_at: str = "") -> str:
    snapshot = _parse_strategy_param_snapshot(snapshot_value)
    if not snapshot:
        return ""
    current = _build_current_strategy_param_snapshot(snapshot.get("strategy_family", ""), config=config)
    lines = [_format_strategy_param_snapshot(snapshot)]
    if current:
        before_rr = float(snapshot.get("min_rr", 0.0) or 0.0)
        after_rr = float(current.get("min_rr", 0.0) or 0.0)
        before_limit = int(snapshot.get("daily_limit", 0) or 0)
        after_limit = int(current.get("daily_limit", 0) or 0)
        before_cooldown = int(snapshot.get("cooldown_min", 0) or 0)
        after_cooldown = int(current.get("cooldown_min", 0) or 0)
        lines.append("当前参数对比：")
        if abs(after_rr - before_rr) <= 1e-9:
            lines.append(f"RR：当前 {after_rr:.2f}R（与当时一致）")
        else:
            lines.append(f"RR：当前 {after_rr:.2f}R（较当时 {after_rr - before_rr:+.2f}R）")
        if after_limit == before_limit:
            lines.append(f"日上限：当前 {after_limit} 次（与当时一致）")
        else:
            lines.append(f"日上限：当前 {after_limit} 次（较当时 {after_limit - before_limit:+d}）")
        if after_cooldown == before_cooldown:
            lines.append(f"冷却：当前 {after_cooldown} 分钟（与当时一致）")
        else:
            lines.append(f"冷却：当前 {after_cooldown} 分钟（较当时 {after_cooldown - before_cooldown:+d} 分钟）")

    apply_payload = dict(latest_apply or {})
    apply_time = _parse_time(str(apply_payload.get("updated_at", "") or "").strip())
    trade_time = _parse_time(str(closed_at or "").strip())
    if apply_time and trade_time:
        relation = "后" if trade_time >= apply_time else "前"
        lines.append(f"时间关系：按平仓时间看，这笔单发生在最近一次调参{relation}。")
    return "\n".join(line for line in lines if str(line or "").strip())


def _load_latest_strategy_apply_summary() -> dict:
    events = _load_strategy_apply_events(limit=1)
    if not events:
        return {
            "text": "最近调参：暂无人工批准记录。",
            "tooltip": "最近调参：暂无人工批准记录。",
            "tone": "neutral",
            "updated_at": "",
        }
    return _build_strategy_apply_summary_payload(events[0])


def _load_strategy_apply_events(limit: int | None = None) -> list[dict]:
    sql = """
        SELECT rg.rule_id, rg.updated_at, rg.rationale, kr.rule_text, kr.logic_json
        FROM rule_governance rg
        JOIN knowledge_rules kr ON kr.id = rg.rule_id
        JOIN knowledge_sources ks ON ks.id = kr.source_id
        WHERE rg.horizon_min = 30
          AND rg.governance_status = 'active'
          AND ks.source_type = 'strategy_learning'
          AND rg.rationale LIKE '人工在待审面板中批准%'
        ORDER BY rg.updated_at DESC, rg.rule_id DESC
    """
    params: tuple[object, ...] = ()
    if limit is not None:
        sql += "\nLIMIT ?"
        params = (max(1, int(limit or 1)),)
    try:
        with open_knowledge_connection(KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
            rows = conn.execute(sql, params).fetchall()
    except Exception:
        return []

    prefix = "人工在待审面板中批准；"
    events: list[dict] = []
    for row in list(rows or []):
        try:
            logic = json.loads(str(row["logic_json"] or "{}"))
        except json.JSONDecodeError:
            logic = {}
        if not isinstance(logic, dict):
            logic = {}
        rationale = str(row["rationale"] or "").strip()
        summary = rationale[len(prefix):].strip() if rationale.startswith(prefix) else rationale
        events.append(
            {
                "rule_id": int(row["rule_id"] or 0),
                "updated_at": str(row["updated_at"] or "").strip(),
                "rationale": rationale,
                "rule_text": str(row["rule_text"] or "").strip(),
                "summary": summary.rstrip("。"),
                "strategy_family": str(logic.get("strategy_family", "") or "").strip().lower(),
                "action_kind": str(logic.get("action_kind", "") or "").strip().lower(),
            }
        )
    return events


def _build_strategy_apply_summary_payload(event: dict | None) -> dict:
    payload = dict(event or {})
    updated_at = str(payload.get("updated_at", "") or "").strip()
    rationale = str(payload.get("rationale", "") or "").strip()
    rule_text = str(payload.get("rule_text", "") or "").strip()
    summary = str(payload.get("summary", "") or "").strip()
    display_time = updated_at[5:16] if len(updated_at) >= 16 else (updated_at or "--")
    text = f"最近调参：{display_time} {summary or rule_text or '已批准策略学习建议'}"
    tooltip = text
    if rationale and rationale != text:
        tooltip = f"{text}\n规则：{rule_text or '--'}\n原始说明：{rationale}"
    return {
        "text": text,
        "tooltip": tooltip,
        "tone": "info",
        "updated_at": updated_at,
        "strategy_family": str(payload.get("strategy_family", "") or "").strip().lower(),
        "action_kind": str(payload.get("action_kind", "") or "").strip().lower(),
    }


def _compact_strategy_apply_summary(summary: str) -> str:
    text = str(summary or "").strip().rstrip("。")
    if not text:
        return ""
    text = re.sub(r"最小 RR 已由 ([0-9.]+) 调整为 ([0-9.]+)", r"RR \1→\2", text)
    text = re.sub(r"日上限已由 (\d+) 调整为 (\d+)", r"上限 \1→\2", text)
    text = re.sub(r"冷却已由 (\d+) 分钟调整为 (\d+) 分钟", r"冷却 \1→\2m", text)
    text = re.sub(r"继续沿用当前参数：", "", text)
    text = re.sub(r"\s*；\s*", " / ", text)
    return " ".join(text.split()).strip()


def _load_all_sim_trade_rows(db_file: str | None) -> list:
    if not str(db_file or "").strip():
        return []
    try:
        import sqlite3

        with closing(sqlite3.connect(db_file)) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                """
                SELECT closed_at, profit, strategy_family
                FROM sim_trades
                WHERE COALESCE(closed_at, '') <> ''
                ORDER BY id DESC
                """
            ).fetchall()
    except Exception:
        return []


def _summarize_trade_rows(rows: list, *, apply_time: datetime, strategy_family: str = "") -> dict:
    family = str(strategy_family or "").strip().lower()
    before_count = after_count = 0
    before_win = after_win = 0
    before_net = after_net = 0.0
    for row in list(rows or []):
        trade_time = _parse_time(str(_row_value(row, "closed_at", "") or "").strip())
        if not trade_time:
            continue
        trade_family = str(_row_value(row, "strategy_family", "") or "").strip().lower()
        if family and trade_family != family:
            continue
        profit = float(_row_value(row, "profit", 0.0) or 0.0)
        if trade_time >= apply_time:
            after_count += 1
            after_net += profit
            if profit > 0:
                after_win += 1
        else:
            before_count += 1
            before_net += profit
            if profit > 0:
                before_win += 1
    return {
        "before_count": before_count,
        "after_count": after_count,
        "before_win": before_win,
        "after_win": after_win,
        "before_net": before_net,
        "after_net": after_net,
    }


def _load_recent_strategy_apply_board(limit: int = 3) -> dict:
    rows = _load_strategy_apply_events(limit=max(1, int(limit or 3)))

    if not rows:
        return {
            "text": "调参看板：暂无最近三次人工调参记录。",
            "tooltip": "调参看板：暂无最近三次人工调参记录。",
            "tone": "neutral",
        }

    compact_entries = []
    tooltip_lines = ["最近三次人工调参："]
    for row in rows:
        updated_at = str(row.get("updated_at", "") or "").strip()
        rule_text = str(row.get("rule_text", "") or "").strip()
        summary = str(row.get("summary", "") or "").strip()
        display_time = updated_at[5:16] if len(updated_at) >= 16 else (updated_at or "--")
        compact_entries.append(f"{display_time} {_compact_strategy_apply_summary(summary or rule_text)}")
        tooltip_lines.append(f"{display_time} {summary or rule_text}")
    return {
        "text": "调参看板：" + " | ".join(compact_entries),
        "tooltip": "\n".join(tooltip_lines),
        "tone": "info",
    }


def _build_strategy_apply_impact_summary(sim_db_path: str | None, latest_apply: dict | None = None) -> dict:
    payload = dict(latest_apply or {})
    apply_time = _parse_time(str(payload.get("updated_at", "") or "").strip())
    if not apply_time:
        return {
            "text": "调参影响：等待最近一次带时间戳的人工调参记录。",
            "tooltip": "调参影响：等待最近一次带时间戳的人工调参记录。",
            "tone": "neutral",
        }

    summary = _summarize_trade_rows(_load_all_sim_trade_rows(sim_db_path), apply_time=apply_time)
    before_count = int(summary["before_count"])
    after_count = int(summary["after_count"])
    before_win = int(summary["before_win"])
    after_win = int(summary["after_win"])
    before_net = float(summary["before_net"])
    after_net = float(summary["after_net"])

    if after_count <= 0:
        return {
            "text": "调参影响：最近一次调参后还没有新的已平仓样本。",
            "tooltip": "调参影响：最近一次调参后还没有新的已平仓样本，暂时无法对比前后表现。",
            "tone": "neutral",
        }

    after_win_rate = after_win / after_count * 100.0 if after_count > 0 else 0.0
    before_win_rate = before_win / before_count * 100.0 if before_count > 0 else 0.0
    text = (
        "调参影响："
        f"调参后 {after_count} 笔 / 胜率 {after_win_rate:.0f}% / 净盈亏 {after_net:+.2f}；"
        f"调参前 {before_count} 笔 / 胜率 {before_win_rate:.0f}% / 净盈亏 {before_net:+.2f}"
    )
    tone = "info"
    if after_net > before_net:
        tone = "success"
    elif after_net < before_net:
        tone = "warning"
    tooltip = (
        f"{text}\n"
        "说明：当前按全量历史成交的平仓时间，把最近一次调参前后分成两段做轻量比较，"
        "用于快速复盘，不等同于严格统计检验。"
    )
    return {"text": text, "tooltip": tooltip, "tone": tone}


def _build_strategy_family_apply_impact_summary(
    sim_db_path: str | None,
    latest_apply: dict | None = None,
    limit: int = 3,
) -> dict:
    payload = dict(latest_apply or {})
    fallback_apply_time = _parse_time(str(payload.get("updated_at", "") or "").strip())
    if latest_apply is not None and not fallback_apply_time:
        return {
            "text": "策略分组：等待最近一次带时间戳的人工调参记录。",
            "tooltip": "策略分组：等待最近一次带时间戳的人工调参记录。",
            "tone": "neutral",
        }

    trade_rows = _load_all_sim_trade_rows(sim_db_path)
    if not trade_rows:
        return {
            "text": "策略分组：最近还没有可用于调参对比的已平仓样本。",
            "tooltip": "策略分组：最近还没有可用于调参对比的已平仓样本。",
            "tone": "neutral",
        }

    family_events: dict[str, dict] = {}
    for event in _load_strategy_apply_events():
        family = str(event.get("strategy_family", "") or "").strip().lower()
        if family and family not in family_events:
            family_events[family] = event

    grouped: dict[str, dict[str, float | int | str]] = {}
    fallback_used = False
    for row in list(trade_rows or []):
        family = str(_row_value(row, "strategy_family", "") or "").strip().lower() or "unknown"
        event = family_events.get(family)
        apply_time = _parse_time(str((event or {}).get("updated_at", "") or "").strip()) or fallback_apply_time
        if not apply_time:
            continue
        if event is None and fallback_apply_time:
            fallback_used = True
        bucket = grouped.setdefault(
            family,
            {
                "before_count": 0,
                "after_count": 0,
                "before_win": 0,
                "after_win": 0,
                "before_net": 0.0,
                "after_net": 0.0,
                "updated_at": str((event or {}).get("updated_at", "") or "").strip(),
            },
        )
        summary = _summarize_trade_rows([row], apply_time=apply_time)
        bucket["before_count"] = int(bucket["before_count"]) + int(summary["before_count"])
        bucket["after_count"] = int(bucket["after_count"]) + int(summary["after_count"])
        bucket["before_win"] = int(bucket["before_win"]) + int(summary["before_win"])
        bucket["after_win"] = int(bucket["after_win"]) + int(summary["after_win"])
        bucket["before_net"] = float(bucket["before_net"]) + float(summary["before_net"])
        bucket["after_net"] = float(bucket["after_net"]) + float(summary["after_net"])

    if not grouped:
        return {
            "text": "策略分组：最近还没有可用于调参对比的已平仓样本。",
            "tooltip": "策略分组：最近还没有可用于调参对比的已平仓样本。",
            "tone": "neutral",
        }

    ordered = sorted(
        grouped.items(),
        key=lambda item: (
            -int(item[1]["after_count"]),
            -abs(float(item[1]["after_net"])),
            -int(item[1]["before_count"]),
            str(item[0]),
        ),
    )[: max(1, int(limit or 3))]

    parts = []
    tooltip_lines = ["按策略族看各自最近一次调参前后："]
    any_positive = False
    any_negative = False
    for family, stats in ordered:
        label = _format_strategy_family_text(family)
        after_count = int(stats["after_count"])
        before_count = int(stats["before_count"])
        after_net = float(stats["after_net"])
        before_net = float(stats["before_net"])
        after_win_rate = (int(stats["after_win"]) / after_count * 100.0) if after_count > 0 else 0.0
        before_win_rate = (int(stats["before_win"]) / before_count * 100.0) if before_count > 0 else 0.0
        parts.append(
            f"{label} 后{after_count}笔 {after_win_rate:.0f}% {after_net:+.2f} / 前{before_count}笔 {before_win_rate:.0f}% {before_net:+.2f}"
        )
        tooltip_lines.append(
            f"{label}：调参后 {after_count} 笔 / 胜率 {after_win_rate:.0f}% / 净盈亏 {after_net:+.2f}；"
            f"调参前 {before_count} 笔 / 胜率 {before_win_rate:.0f}% / 净盈亏 {before_net:+.2f}"
        )
        updated_at = str(stats.get("updated_at", "") or "").strip()
        if updated_at:
            tooltip_lines.append(f"{label} 使用该策略族最近一次调参时间：{updated_at}")
        if after_net > before_net:
            any_positive = True
        elif after_net < before_net:
            any_negative = True

    tone = "info"
    if any_positive and not any_negative:
        tone = "success"
    elif any_negative and not any_positive:
        tone = "warning"
    if fallback_used:
        tooltip_lines.append("说明：部分策略族尚未找到独立调参记录，本次先回退到最近一次全局调参时间做轻量比较。")
    else:
        tooltip_lines.append("说明：这里按策略族拆分比较，每个策略族各自使用最近一次已生效调参时间。")
    return {
        "text": "策略分组：" + " | ".join(parts),
        "tooltip": "\n".join(tooltip_lines),
        "tone": tone,
    }


def _format_ai_signal_health(stats: dict) -> str:
    total = int(stats.get("total_count", 0) or 0)
    valid = int(stats.get("valid_count", 0) or 0)
    directional = int(stats.get("executable_count", 0) or 0)
    sim_eligible = int(stats.get("sim_eligible_count", 0) or 0)
    structured = int(stats.get("structured_count", 0) or 0)
    fallback = int(stats.get("fallback_count", 0) or 0)
    if total <= 0:
        return "AI链路健康：近30天暂无结构化信号入库，重启程序并完成一次 AI 研判后这里会显示解析与执行漏斗。"
    direction_rate = directional / total * 100
    structured_rate = structured / total * 100
    return (
        "AI链路健康："
        f"结构化 {structured}/{total}（{structured_rate:.0f}%），"
        f"协议有效 {valid}/{total}，"
        f"方向信号 {directional}/{total}（{direction_rate:.0f}%），"
        f"规则允许试仓 {sim_eligible}/{total}，"
        f"降级 {fallback}。"
    )


# ─────────────────────────────────────────────
#  DashboardMetricsPanel  （三个指标卡）
# ─────────────────────────────────────────────
class DashboardMetricsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.lbl_watch_count = self._build_card("观察品种", "--")
        self.lbl_live_count  = self._build_card("实时报价", "--")
        self.lbl_refresh_time = self._build_card("最近刷新", "--")
        layout.addWidget(self.lbl_watch_count)
        layout.addWidget(self.lbl_live_count)
        layout.addWidget(self.lbl_refresh_time)

    def _build_card(self, title: str, value: str) -> QLabel:
        card = QLabel(f"{title}\n{value}")
        card.setAlignment(Qt.AlignCenter)
        card.setFixedHeight(70)
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def update_from_snapshot(self, snapshot: dict):
        self.lbl_watch_count.setText(f"观察品种\n{snapshot.get('watch_count', 0)}")
        self.lbl_live_count.setText(f"实时报价\n{snapshot.get('live_count', 0)}")
        self.lbl_refresh_time.setText(f"最近刷新\n{snapshot.get('last_refresh_text', '--')}")


#    Row1 [stretch=2]  MT5状态 | 时段休市
#    AI简报 [stretch=4]  ← 最大区域，可滚动
#    Row2 [stretch=3]  点差 | 事件 | 提醒 | 宏观（4列）
# ─────────────────────────────────────────────
class InsightPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # ── 第一行：MT5状态 + 时段（无固定高度，随行伸展）──
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self.runtime_status_label = self._panel(row1, "MT5 连接状态")
        self.session_status_label  = self._panel(row1, "时段 / 休市")
        layout.addLayout(row1, 2)  # stretch=2

        # ── AI 简报独占最大空间，使用 QTextEdit 支持滚动 ──
        ai_frame = QFrame()
        ai_frame.setStyleSheet(style.STYLE_CARD_CONTAINER)
        ai_lay = QVBoxLayout(ai_frame)
        ai_lay.setContentsMargins(16, 12, 16, 12)
        ai_lay.setSpacing(6)

        ai_header = QHBoxLayout()
        ai_title = QLabel("🤖  AI 研判简报")
        ai_title.setStyleSheet(
            "font-size:14px; font-weight:800; color:#1d4ed8;"
            " font-family:'Segoe UI','Microsoft YaHei',sans-serif;"
        )
        ai_header.addWidget(ai_title)
        ai_header.addStretch()
        ai_lay.addLayout(ai_header)

        self.txt_ai_brief = QTextEdit()
        self.txt_ai_brief.setReadOnly(True)
        self.txt_ai_brief.setStyleSheet(style.STYLE_TEXT_ACCENT)
        self.txt_ai_brief.setPlainText("点击顶部「🤖 AI研判」生成简报，或等待自动研判（每30分钟）。")
        ai_lay.addWidget(self.txt_ai_brief, 1)
        layout.addWidget(ai_frame, 4)  # stretch=4 ── 最大

        # ── 第二行：4列分析卡（充满剩余空间）──
        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.spread_focus_labels = self._multi_panel(row2, "点差高亮",  2)
        self.event_window_labels = self._multi_panel(row2, "事件窗口",  2)
        self.alert_status_labels = self._multi_panel(row2, "提醒状态",  2)
        self.macro_data_labels   = self._multi_panel(row2, "宏观数据",  2)
        layout.addLayout(row2, 3)  # stretch=3


    # ── 构建辅助 ──────────────────────────────────────────
    def _panel(self, parent_layout, title_text: str) -> QLabel:
        """无固定高度的单信息面板，随 stretch 比例拉伸"""
        frame = QFrame()
        frame.setStyleSheet(style.STYLE_CARD_CONTAINER)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        t = QLabel(title_text)
        t.setStyleSheet(style.STYLE_CARD_TITLE)
        lay.addWidget(t)
        lbl = QLabel("等待首次刷新…")
        lbl.setWordWrap(True)
        lbl.setAlignment(Qt.AlignTop)
        lbl.setStyleSheet(style.STYLE_PANEL_NEUTRAL)
        lay.addWidget(lbl, 1)
        parent_layout.addWidget(frame, 1)
        return lbl

    def _multi_panel(self, parent_layout, title_text: str, count: int) -> list:
        """多条目信息面板，使用 RichText 显示加粗标题 + 详情"""
        frame = QFrame()
        frame.setStyleSheet(style.STYLE_CARD_CONTAINER)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)
        t = QLabel(title_text)
        t.setStyleSheet(style.STYLE_CARD_TITLE)
        lay.addWidget(t)
        labels = []
        for i in range(count):
            lbl = QLabel("等待刷新…")
            lbl.setWordWrap(True)
            lbl.setAlignment(Qt.AlignTop)
            lbl.setStyleSheet(style.STYLE_PANEL_NEUTRAL)
            lay.addWidget(lbl, 1)
            labels.append(lbl)
        parent_layout.addWidget(frame, 1)
        return labels

    def _fill(self, labels: list, cards: list):
        tone_styles = style.PANEL_STYLE_MAP
        safe = list(cards or [])
        while len(safe) < len(labels):
            safe.append({"title": "暂无", "detail": "", "tone": AlertTone.NEUTRAL.value})
        for lbl, card in zip(labels, safe):
            title  = str(card.get("title", "") or "").strip()
            detail = str(card.get("detail", "") or "").strip()
            tone   = str(
                card.get("tone", AlertTone.NEUTRAL.value)
                or AlertTone.NEUTRAL.value
            ).strip()
            if detail:
                lbl.setText(f"<b>{title}</b><br><small style='color:#64748b;'>{detail}</small>")
                lbl.setTextFormat(Qt.RichText)
            else:
                lbl.setTextFormat(Qt.PlainText)
                lbl.setText(title)
            base_style = tone_styles.get(tone, style.STYLE_PANEL_NEUTRAL)
            lbl.setStyleSheet(base_style)

    # ── 公开接口 ────────────────────────────────────────
    def update_from_snapshot(self, snapshot: dict):
        rc = list(snapshot.get("runtime_status_cards", []) or [])
        self._fill([self.runtime_status_label],
                   [rc[0]] if rc else [{"title": "等待刷新", "detail": "", "tone": AlertTone.NEUTRAL.value}])
        self._fill([self.session_status_label],
                   [rc[1]] if len(rc) > 1 else [{"title": "等待刷新", "detail": "", "tone": AlertTone.NEUTRAL.value}])
        self._fill(self.spread_focus_labels, snapshot.get("spread_focus_cards", []))
        self._fill(self.event_window_labels, snapshot.get("event_window_cards", []))
        self._fill(self.alert_status_labels, snapshot.get("alert_status_cards", []))
        self._fill(self.macro_data_labels,   snapshot.get("macro_data_status_cards", []))

    def set_ai_brief(self, text: str):
        self.txt_ai_brief.setPlainText(str(text or "").strip())


class LeftTabPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(style.STYLE_TAB_WIDGET)

        # ── Tab A：大盘与AI简报 ───────────────────────────
        tab_ov = QWidget()
        tab_ov_lay = QVBoxLayout(tab_ov)
        tab_ov_lay.setContentsMargins(8, 8, 8, 8)
        self.txt_summary = QTextEdit()
        self.txt_summary.setReadOnly(True)
        self.txt_summary.setStyleSheet(style.STYLE_TEXT_NEUTRAL)
        tab_ov_lay.addWidget(QLabel("系统与盘面概览"), 0)
        tab_ov_lay.addWidget(self.txt_summary, 2)

        tab_ov_lay.addWidget(QLabel("AI 快速研判"), 0)
        self.lbl_ai_history_summary = QLabel("最近还没有 AI 研判留痕。")
        self.lbl_ai_history_summary.setWordWrap(True)
        self.lbl_ai_history_summary.setStyleSheet(style.STYLE_PANEL_ACCENT)
        tab_ov_lay.addWidget(self.lbl_ai_history_summary)

        self.txt_ai_brief = QTextEdit()
        self.txt_ai_brief.setReadOnly(True)
        self.txt_ai_brief.setStyleSheet(style.STYLE_TEXT_ACCENT)
        self.txt_ai_brief.setPlainText("点击顶部AI研判按钮后在此查看结论。")
        tab_ov_lay.addWidget(self.txt_ai_brief, 3)
        self.tabs.addTab(tab_ov, "大盘与AI简报")

        # ── Tab B：提醒留痕历史 ───────────────────────────
        tab_hist = QWidget()
        tab_hist_lay = QVBoxLayout(tab_hist)
        tab_hist_lay.setContentsMargins(8, 8, 8, 8)
        hist_metrics = QHBoxLayout()
        self.lbl_history_total     = self._metric_card("近7天提醒", "--")
        self.lbl_history_spread    = self._metric_card("点差异常", "--")
        self.lbl_history_effective = self._metric_card("有效提醒", "--")
        hist_metrics.addWidget(self.lbl_history_total)
        hist_metrics.addWidget(self.lbl_history_spread)
        hist_metrics.addWidget(self.lbl_history_effective)
        tab_hist_lay.addLayout(hist_metrics)

        self.lbl_history_summary = QLabel("正在整理提醒统计...")
        self.lbl_history_summary.setWordWrap(True)
        self.lbl_history_summary.setStyleSheet(style.STYLE_PANEL_WARNING_LIGHT)
        tab_hist_lay.addWidget(self.lbl_history_summary)

        self.lbl_effectiveness_summary = QLabel("正在整理提醒评估...")
        self.lbl_effectiveness_summary.setWordWrap(True)
        self.lbl_effectiveness_summary.setStyleSheet(style.STYLE_PANEL_SUCCESS)
        tab_hist_lay.addWidget(self.lbl_effectiveness_summary)

        self.txt_history = QTextEdit()
        self.txt_history.setReadOnly(True)
        self.txt_history.setStyleSheet(style.STYLE_TEXT_WARNING)
        self.txt_history.document().setMaximumBlockCount(500)  # 炸弹一修复：限制最多 500 行，防止 OOM
        tab_hist_lay.addWidget(self.txt_history, 1)
        self.tabs.addTab(tab_hist, "提醒留痕历史")

        # ── Tab C：底层运行日志 ───────────────────────────
        tab_logs = QWidget()
        tab_logs_lay = QVBoxLayout(tab_logs)
        tab_logs_lay.setContentsMargins(8, 8, 8, 8)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(style.STYLE_TEXT_LOG)
        self.txt_log.document().setMaximumBlockCount(1000)  # 炸弹一修复：限制最多 1000 行，防止 7×24 运行 OOM
        tab_logs_lay.addWidget(self.txt_log, 1)
        self.tabs.addTab(tab_logs, "底层运行日志")

        layout.addWidget(self.tabs)
        self.refresh_histories()

    def _metric_card(self, title: str, value: str) -> QLabel:
        card = QLabel(f"{title}\n{value}")
        card.setAlignment(Qt.AlignCenter)
        card.setFixedHeight(60)
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def append_log(self, text: str):
        if text.strip():
            self.txt_log.append(text.strip())

    def set_ai_brief(self, text: str):
        self.txt_ai_brief.setPlainText(str(text or "").strip())

    def update_from_snapshot(self, snapshot: dict):
        self.txt_summary.setPlainText(snapshot.get("summary_text", ""))

    def refresh_histories(self, snapshot: dict = None):
        stats_ai = summarize_recent_ai_history(days=7)
        recent_ai = read_recent_ai_history(limit=1)
        try:
            ai_signal_health_text = _format_ai_signal_health(summarize_recent_ai_signals(days=30))
        except Exception as exc:  # noqa: BLE001
            ai_signal_health_text = f"AI链路健康：读取结构化信号统计失败（{str(exc) or '未知错误'}）。"
        if not recent_ai:
            self.lbl_ai_history_summary.setText(
                "最近还没有 AI 研判留痕。完成一次手动研判后，这里会显示最近一次结论和是否已推送。\n"
                f"{ai_signal_health_text}"
            )
        else:
            latest = recent_ai[0]
            ls = str(latest.get("summary_line", "最近一次 AI 研判未返回摘要。") or "最近一次 AI 研判未返回摘要。").strip()
            lt = str(latest.get("occurred_at", "--") or "--").strip()
            lp = "已推送" if bool(latest.get("push_sent")) else "未推送"
            self.lbl_ai_history_summary.setText(
                f"{stats_ai.get('summary_text', '')}\n"
                f"最近一次：{ls}（{lt}，{lp}）\n"
                f"{ai_signal_health_text}"
            )

        stats = summarize_recent_history(days=7)
        self.lbl_history_total.setText(f"近7天提醒\n{stats.get('total_count', 0)}")
        self.lbl_history_spread.setText(f"点差异常\n{stats.get('spread_count', 0)}")
        effectiveness = summarize_effectiveness(snapshot or {})
        self.lbl_history_effective.setText(f"有效提醒\n{effectiveness.get('effective_count', 0)}")
        latest_title = str(stats.get("latest_title", "暂无异常") or "暂无异常").strip()
        latest_time  = str(stats.get("latest_time", "--") or "--").strip()
        self.lbl_history_summary.setText(
            f"{stats.get('summary_text', '')}\n最近一次：{latest_title}（{latest_time}）"
        )
        self.lbl_effectiveness_summary.setText(
            f"{effectiveness.get('summary_text', '')}\n"
            f"最近一次进入评估窗口：{effectiveness.get('latest_title', '暂无可评估提醒')}（{effectiveness.get('latest_time', '--')}）"
        )
        entries = read_recent_history(limit=8)
        if not entries:
            self.txt_history.setPlainText("近期暂无提醒留痕。")
        else:
            lines = []
            for item in entries:
                oa = str(item.get("occurred_at", "--") or "--").strip()
                tt = str(item.get("title", "提醒") or "提醒").strip()
                dt = str(item.get("detail", "") or "").strip()
                lines.append(f"[{oa}] {tt}\n{dt}")
            self.txt_history.setPlainText("\n\n".join(lines))


# ─────────────────────────────────────────────
#  WatchListTable  （Tab1 品种表格）
# ─────────────────────────────────────────────
class WatchListTable(QFrame):
    feedback_result_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.feedback_result_ready.connect(self._on_feedback_result)
        self.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        title = QLabel("观察品种")
        title.setStyleSheet(style.STYLE_SECTION_TITLE)
        layout.addWidget(title)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["品种", "最新价", "报价结构", "报价状态", "宏观提醒", "提醒状态", "出手建议"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 110)
        self.table.setColumnWidth(5, 160)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.itemClicked.connect(self._on_row_clicked)
        layout.addWidget(self.table, 1)

        # ── 快捷反馈条 ─────────────────────────────────
        self._feedback_bar = QFrame()
        self._feedback_bar.setStyleSheet(
            "QFrame{background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;}"
        )
        fb_lay = QHBoxLayout(self._feedback_bar)
        fb_lay.setContentsMargins(8, 3, 8, 3)
        fb_lay.setSpacing(6)
        self._lbl_feedback_hint = QLabel("请选择反馈")
        self._lbl_feedback_hint.setStyleSheet("color:#0369a1;font-size:12px;font-weight:600;")
        fb_lay.addWidget(self._lbl_feedback_hint)
        fb_lay.addStretch()
        for btn_text, fb_label, btn_style in [
            ("✔ 有用", "helpful",   "background:#dcfce7;color:#166534;border:none;border-radius:7px;padding:3px 9px;font-size:12px;"),
            ("✘ 没用", "unhelpful", "background:#fee2e2;color:#b91c1c;border:none;border-radius:7px;padding:3px 9px;font-size:12px;"),
            ("⏰ 太晚", "too_late",  "background:#fef9c3;color:#854d0e;border:none;border-radius:7px;padding:3px 9px;font-size:12px;"),
            ("🔕 噪音", "noise",    "background:#f1f5f9;color:#475569;border:none;border-radius:7px;padding:3px 9px;font-size:12px;"),
        ]:
            btn = QPushButton(btn_text)
            btn.setStyleSheet(btn_style)
            btn.setFixedHeight(26)
            btn.clicked.connect(lambda checked=False, lb=fb_label: self._submit_feedback(lb))
            fb_lay.addWidget(btn)
        btn_close = QPushButton("×")
        btn_close.setStyleSheet("background:transparent;color:#94a3b8;font-size:14px;border:none;")
        btn_close.setFixedSize(22, 22)
        btn_close.clicked.connect(self._feedback_bar.hide)
        fb_lay.addWidget(btn_close)
        self._feedback_bar.hide()
        layout.addWidget(self._feedback_bar)

        self._row_feedback_targets: list[dict] = []
        self._selected_feedback_target: dict = {}

    def update_from_snapshot(self, snapshot: dict):
        items = [_normalize_snapshot_item(item) for item in list(snapshot.get("items", []) or [])]
        snapshot_time = str(snapshot.get("last_refresh_text", "") or "").strip()
        self.table.setRowCount(len(items))
        tone_bg = style.TABLE_ROW_BG_MAP
        for row_index, item in enumerate(items):
            signal_side_text = str(item.get("signal_side_text", "") or "").strip()
            exec_note = _format_watch_execution_note(item)
            exec_display = f"{signal_side_text} {exec_note}".strip() if signal_side_text else exec_note
            transition = str(item.get("alert_state_transition_text", "") or "").strip()
            alert_cell = (
                f"{item.get('alert_state_text', '--')}\n{transition}"
                if transition else item.get("alert_state_text", "--")
            )
            values = [
                item.get("symbol", "--"),
                item.get("latest_text", "--"),
                _format_watch_quote_text(item),
                _format_quote_status_text(item),
                item.get("macro_focus", "--"),
                alert_cell,
                exec_display,
            ]
            for col_index, value in enumerate(values):
                val_str = str(value or "--")
                cell = self.table.item(row_index, col_index)
                if not cell:
                    cell = QTableWidgetItem(val_str)
                    cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                    self.table.setItem(row_index, col_index, cell)
                else:
                    cell.setText(val_str)
                cell.setToolTip(str(value or "--"))
                bg_str = tone_bg.get(
                    str(item.get("tone", AlertTone.NEUTRAL.value) or AlertTone.NEUTRAL.value),
                    "#f8fafc",
                )
                cell.setBackground(QColor(bg_str))
        self._row_feedback_targets = [
            {
                "symbol": str(item.get("symbol", "") or "").strip().upper(),
                "snapshot_time": snapshot_time,
                "snapshot_id": int(item.get("snapshot_id", 0) or 0),
            }
            for item in items
        ]
        self._selected_feedback_target = {}
        self._feedback_bar.hide()

    def bind_feedback_snapshot_ids(self, snapshot_time: str, snapshot_bindings: dict[str, int] | None = None):
        time_text = str(snapshot_time or "").strip()
        bindings = {
            str(symbol or "").strip().upper(): int(snapshot_id)
            for symbol, snapshot_id in dict(snapshot_bindings or {}).items()
            if str(symbol or "").strip() and int(snapshot_id or 0) > 0
        }
        if not time_text or not bindings:
            return
        for target in self._row_feedback_targets:
            if str(target.get("snapshot_time", "") or "").strip() != time_text:
                continue
            symbol = str(target.get("symbol", "") or "").strip().upper()
            if symbol in bindings:
                target["snapshot_id"] = int(bindings[symbol])

    def _on_row_clicked(self, item: QTableWidgetItem):
        row = item.row()
        target = self._row_feedback_targets[row] if row < len(self._row_feedback_targets) else {}
        symbol = str(target.get("symbol", "") or "").strip().upper()
        if not symbol:
            return
        if int(target.get("snapshot_id", 0) or 0) <= 0:
            self._selected_feedback_target = {}
            self._lbl_feedback_hint.setText(f"⏳ 【{symbol}】样本仍在入库，请 1 秒后再点一次。")
            self._feedback_bar.show()
            return
        self._selected_feedback_target = dict(target)
        self._lbl_feedback_hint.setText(f"【{symbol}】 这次提醒对你有帮助吗？")
        self._feedback_bar.show()

    def _submit_feedback(self, label: str):
        target = dict(self._selected_feedback_target or {})
        symbol = str(target.get("symbol", "") or "").strip().upper()
        if not symbol:
            return
        snapshot_id = int(target.get("snapshot_id", 0) or 0)
        if snapshot_id <= 0:
            self._lbl_feedback_hint.setText(f"⏳ 【{symbol}】样本仍在入库，请稍后再试。")
            self._feedback_bar.show()
            return
        self._lbl_feedback_hint.setText(f"⏳ 正在记录【{symbol}】的【{label}】反馈...")
        self._feedback_bar.show()
        payload = {
            "symbol": symbol,
            "snapshot_id": snapshot_id,
            "snapshot_time": str(target.get("snapshot_time", "") or "").strip(),
            "feedback_label": label,
            "source": "ui_quick",
        }
        self._start_feedback_worker(lambda: self._run_feedback_write(payload))

    def _start_feedback_worker(self, worker) -> None:
        threading.Thread(target=worker, daemon=True, name="ui-feedback-write").start()

    def _run_feedback_write(self, payload: dict) -> None:
        symbol = str(payload.get("symbol", "") or "").strip().upper()
        label = str(payload.get("feedback_label", "") or "").strip()
        try:
            from knowledge_feedback import record_user_feedback

            result = record_user_feedback(
                symbol=symbol,
                snapshot_id=int(payload.get("snapshot_id", 0) or 0),
                snapshot_time=str(payload.get("snapshot_time", "") or "").strip(),
                feedback_label=label,
                source=str(payload.get("source", "ui_quick") or "ui_quick"),
            )
            self.feedback_result_ready.emit(
                {
                    "symbol": symbol,
                    "label": label,
                    "result": dict(result or {}),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.feedback_result_ready.emit(
                {
                    "symbol": symbol,
                    "label": label,
                    "result": {},
                    "error": str(exc) or "未知错误",
                }
            )

    def _on_feedback_result(self, payload: dict) -> None:
        symbol = str(payload.get("symbol", "") or "").strip().upper()
        label = str(payload.get("label", "") or "").strip()
        error_text = str(payload.get("error", "") or "").strip()
        result = dict(payload.get("result", {}) or {})
        if error_text:
            self._lbl_feedback_hint.setText(f"⚠️ 【{symbol}】反馈记录失败：{error_text}")
            QTimer.singleShot(2200, self._feedback_bar.hide)
            return
        if str(result.get("error", "") or "").strip() or not result.get("feedback_id"):
            self._lbl_feedback_hint.setText(
                f"⚠️ 【{symbol}】反馈未写入：{str(result.get('error', '') or '未找到可关联快照')}"
            )
            QTimer.singleShot(2200, self._feedback_bar.hide)
            return
        self._lbl_feedback_hint.setText(f"✅ 已记录【{symbol}】的【{label}】反馈，感谢！")
        QTimer.singleShot(1800, self._feedback_bar.hide)

# ─────────────────────────────────────────────
#  SimTradingPanel  （模拟盘战绩）
# ─────────────────────────────────────────────
class SimTradingPanel(QWidget):
    grade_gate_focus_result_ready = Signal(dict)
    strategy_insight_result_ready = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grade_gate_focus_result_ready.connect(self._on_grade_gate_focus_result)
        self.strategy_insight_result_ready.connect(self._on_strategy_insight_result)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(8)

        header_bar = QHBoxLayout()
        header_bar.setSpacing(8)
        self.lbl_sim_balance_hint = QLabel("模拟本金配置：$1,000")
        self.lbl_sim_balance_hint.setWordWrap(False)
        self.lbl_sim_balance_hint.setMinimumHeight(28)
        self.lbl_sim_balance_hint.setMaximumHeight(34)
        self.lbl_sim_balance_hint.setStyleSheet("color:#475569;font-size:12px;font-weight:600;")
        header_bar.addWidget(self.lbl_sim_balance_hint, 1)
        header_bar.addStretch(1)
        self.btn_reset_sim_100 = QPushButton("重置为 $100")
        self.btn_reset_sim_100.setFixedWidth(108)
        self.btn_reset_sim_100.clicked.connect(lambda: self._reset_sim_account(100.0, persist_config=True))
        header_bar.addWidget(self.btn_reset_sim_100)
        self.btn_reset_sim_1000 = QPushButton("重置为 $1,000")
        self.btn_reset_sim_1000.setFixedWidth(118)
        self.btn_reset_sim_1000.clicked.connect(lambda: self._reset_sim_account(1000.0, persist_config=True))
        header_bar.addWidget(self.btn_reset_sim_1000)
        self.btn_reset_sim_config = QPushButton("按当前配置重置")
        self.btn_reset_sim_config.setFixedWidth(126)
        self.btn_reset_sim_config.clicked.connect(lambda: self._reset_sim_account(None, persist_config=False))
        header_bar.addWidget(self.btn_reset_sim_config)
        layout.addLayout(header_bar)

        self.lbl_today_execution = QLabel("今日实际执行：等待读取。")
        self.lbl_today_execution.setWordWrap(True)
        self.lbl_today_execution.setMaximumHeight(34)
        self.lbl_today_execution.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_today_execution.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        layout.addWidget(self.lbl_today_execution)

        self.lbl_latest_no_open_reason = QLabel("最近未开仓：等待本轮候选。")
        self.lbl_latest_no_open_reason.setWordWrap(True)
        self.lbl_latest_no_open_reason.setMaximumHeight(34)
        self.lbl_latest_no_open_reason.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_latest_no_open_reason.setStyleSheet(
            "background:#fffbeb;color:#92400e;border:1px solid #fde68a;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        layout.addWidget(self.lbl_latest_no_open_reason)

        self.lbl_grade_gate_focus = QLabel("24h观察复盘：等待读取。")
        self.lbl_grade_gate_focus.setWordWrap(True)
        self.lbl_grade_gate_focus.setMaximumHeight(34)
        self.lbl_grade_gate_focus.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_grade_gate_focus.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        layout.addWidget(self.lbl_grade_gate_focus)

        strategy_header = QHBoxLayout()
        strategy_header.setSpacing(8)
        self.btn_strategy_detail_toggle = QToolButton()
        self.btn_strategy_detail_toggle.setText("策略复盘")
        self.btn_strategy_detail_toggle.setCheckable(True)
        self.btn_strategy_detail_toggle.setChecked(False)
        self.btn_strategy_detail_toggle.setArrowType(Qt.RightArrow)
        self.btn_strategy_detail_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.btn_strategy_detail_toggle.setStyleSheet(
            "QToolButton{background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;}"
            "QToolButton:hover{background:#dbeafe;}"
        )
        self.btn_strategy_detail_toggle.toggled.connect(self._toggle_strategy_detail_panel)
        strategy_header.addWidget(self.btn_strategy_detail_toggle)
        self.lbl_strategy_digest = QLabel("策略复盘：等待后台摘要。")
        self.lbl_strategy_digest.setWordWrap(True)
        self.lbl_strategy_digest.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_digest.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        strategy_header.addWidget(self.lbl_strategy_digest, 1)
        layout.addLayout(strategy_header)

        self.strategy_detail_panel = QWidget()
        strategy_detail_layout = QVBoxLayout(self.strategy_detail_panel)
        strategy_detail_layout.setContentsMargins(0, 0, 0, 0)
        strategy_detail_layout.setSpacing(8)

        self.lbl_strategy_learning = QLabel("策略学习：等待探索试仓样本。")
        self.lbl_strategy_learning.setWordWrap(True)
        self.lbl_strategy_learning.setMaximumHeight(34)
        self.lbl_strategy_learning.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_learning.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_learning)
        self.lbl_strategy_params = QLabel("策略参数：等待读取当前 RR 配置。")
        self.lbl_strategy_params.setWordWrap(True)
        self.lbl_strategy_params.setMaximumHeight(34)
        self.lbl_strategy_params.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_params.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_params)
        self.lbl_strategy_apply = QLabel("最近调参：暂无人工批准记录。")
        self.lbl_strategy_apply.setWordWrap(True)
        self.lbl_strategy_apply.setMaximumHeight(34)
        self.lbl_strategy_apply.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_apply.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_apply)
        self.lbl_strategy_apply_board = QLabel("调参看板：暂无最近三次人工调参记录。")
        self.lbl_strategy_apply_board.setWordWrap(True)
        self.lbl_strategy_apply_board.setMaximumHeight(52)
        self.lbl_strategy_apply_board.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_apply_board.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_apply_board)
        self.lbl_strategy_apply_impact = QLabel("调参影响：等待最近一次带时间戳的人工调参记录。")
        self.lbl_strategy_apply_impact.setWordWrap(True)
        self.lbl_strategy_apply_impact.setMaximumHeight(34)
        self.lbl_strategy_apply_impact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_apply_impact.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_apply_impact)
        self.lbl_strategy_apply_family_impact = QLabel("策略分组：等待最近一次带时间戳的人工调参记录。")
        self.lbl_strategy_apply_family_impact.setWordWrap(True)
        self.lbl_strategy_apply_family_impact.setMaximumHeight(52)
        self.lbl_strategy_apply_family_impact.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_strategy_apply_family_impact.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        strategy_detail_layout.addWidget(self.lbl_strategy_apply_family_impact)
        self.strategy_detail_panel.setVisible(False)
        layout.addWidget(self.strategy_detail_panel)
        self._grade_gate_focus_cache_key = ""
        self._grade_gate_focus_cache_time = datetime.min
        self._grade_gate_focus_cache_payload: tuple[str, str, str] = (
            "24h观察复盘：等待读取。",
            "neutral",
            "24h观察复盘：等待读取。",
        )
        self._grade_gate_focus_pending_key = ""
        self._today_execution_cache_time = datetime.min
        self._today_execution_cache_payload: tuple[str, str] = (
            "今日实际执行：等待读取。",
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;",
        )
        self._recent_execution_summary_cache_time = datetime.min
        self._recent_execution_summary_cache_key = ""
        self._recent_execution_summary_cache_payload: tuple[str, str] = ("", "")
        self._recent_execution_trace_cache_time = datetime.min
        self._recent_execution_trace_cache_key = ""
        self._recent_execution_trace_cache_payload: tuple[str, str] = (
            "最近执行明细：等待第一批执行留痕。",
            "neutral",
        )
        self._strategy_learning_cache_time = datetime.min
        self._strategy_learning_cache_payload: tuple[str, str, str] = (
            "策略学习：等待探索试仓样本。",
            "neutral",
            "策略学习：等待探索试仓样本。",
        )
        self._strategy_params_cache_text = "策略参数：等待读取当前 RR 配置。"
        self._strategy_params_cache_tooltip = self._strategy_params_cache_text
        self._strategy_apply_cache_time = datetime.min
        self._strategy_apply_cache_payload: tuple[str, str, str] = (
            "最近调参：暂无人工批准记录。",
            "最近调参：暂无人工批准记录。",
            "neutral",
        )
        self._strategy_apply_board_cache_time = datetime.min
        self._strategy_apply_board_cache_payload: tuple[str, str, str] = (
            "调参看板：暂无最近三次人工调参记录。",
            "调参看板：暂无最近三次人工调参记录。",
            "neutral",
        )
        self._strategy_insight_cache_key = ""
        self._strategy_insight_cache_time = datetime.min
        self._strategy_insight_cache_payload: dict = {}
        self._strategy_insight_pending_key = ""

        self.lbl_entry_status = QLabel("自动试仓状态：等待下一轮行情刷新。")
        self.lbl_entry_status.setWordWrap(True)
        self.lbl_entry_status.setMinimumHeight(92)
        self.lbl_entry_status.setMaximumHeight(124)
        self.lbl_entry_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_entry_status.setStyleSheet(
            "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;"
            "border-radius:8px;padding:7px 9px;font-size:11px;font-weight:600;line-height:1.35;"
        )
        self.lbl_entry_audit = QLabel("试仓阻塞审计：等待本轮快照。")
        self.lbl_entry_audit.setWordWrap(True)
        self.lbl_entry_audit.setMaximumHeight(42)
        self.lbl_entry_audit.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_entry_audit.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 7px;font-size:10px;font-weight:500;"
        )
        self.lbl_entry_trace = QLabel("最近执行明细：等待第一批执行留痕。")
        self.lbl_entry_trace.setWordWrap(True)
        self.lbl_entry_trace.setMaximumHeight(42)
        self.lbl_entry_trace.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_entry_trace.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;"
            "border-radius:8px;padding:4px 7px;font-size:10px;font-weight:500;"
        )
        info_layout = QHBoxLayout()
        info_layout.setSpacing(8)
        right_info_layout = QVBoxLayout()
        right_info_layout.setSpacing(8)
        info_layout.addWidget(self.lbl_entry_status, 3)
        right_info_layout.addWidget(self.lbl_entry_audit)
        right_info_layout.addWidget(self.lbl_entry_trace)
        info_layout.addLayout(right_info_layout, 2)
        layout.addLayout(info_layout)

        account_bar = QHBoxLayout()
        account_bar.setSpacing(8)
        self.lbl_initial_balance = self._build_card("起始本金", "$1,000.00", color="#0f766e")
        self.lbl_current_balance = self._build_card("当前余额", "$1,000.00", color="#1d4ed8")
        self.lbl_drawdown_pct = self._build_card("当前回撤", "0.0%", color="#dc2626")
        account_bar.addWidget(self.lbl_initial_balance)
        account_bar.addWidget(self.lbl_current_balance)
        account_bar.addWidget(self.lbl_drawdown_pct)
        layout.addLayout(account_bar)

        # 1. 顶部四大表盘
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        self.lbl_equity = self._build_card("可用净值", "$100,000.00")
        self.lbl_profit = self._build_card("累计盈亏", "$0.00", color="#475569")
        self.lbl_margin = self._build_card("已用保证金", "$0.00")
        self.lbl_win_rate = self._build_card("历史胜率", "--%")
        self.lbl_total_risk = self._build_card("总风险暴露", "$0.00", color="#dc2626")
        self.lbl_avg_rr = self._build_card("平均盈亏比", "--", color="#7c3aed")
        top_bar.addWidget(self.lbl_equity)
        top_bar.addWidget(self.lbl_profit)
        top_bar.addWidget(self.lbl_margin)
        top_bar.addWidget(self.lbl_win_rate)
        top_bar.addWidget(self.lbl_total_risk)
        top_bar.addWidget(self.lbl_avg_rr)
        layout.addLayout(top_bar)

        # 中部表格区分两列：左侧持仓（略窄），右侧历史（略宽）
        tables_lay = QHBoxLayout()
        tables_lay.setSpacing(12)

        # 左侧：持仓区（占 3/5 宽度）
        left_layout = QVBoxLayout()
        left_layout.setSpacing(4)
        lbl_active = QLabel("🟢 正在持仓 (Open Positions)")
        lbl_active.setStyleSheet("font-weight: 800; font-size: 13px; color: #1e293b;")
        left_layout.addWidget(lbl_active)
        self.tbl_positions = QTableWidget(0, 10)
        self.tbl_positions.setHorizontalHeaderLabels([
            "标的", "方向", "手数", "入场价", "止损价", "止盈价",
            "风险", "目标盈利", "盈亏比", "浮动盈亏"
        ])
        hdr_pos = self.tbl_positions.horizontalHeader()
        hdr_pos.setSectionResizeMode(QHeaderView.Interactive)
        hdr_pos.setMinimumSectionSize(44)
        self._configure_sim_positions_table_columns()
        self.tbl_positions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_positions.setAlternatingRowColors(True)
        self.tbl_positions.verticalHeader().setVisible(False)
        self.tbl_positions.verticalHeader().setDefaultSectionSize(28)
        self.tbl_positions.setWordWrap(False)
        self.tbl_positions.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tbl_positions.setMinimumHeight(190)
        left_layout.addWidget(self.tbl_positions, 1)
        tables_lay.addLayout(left_layout, 5)

        # 右侧：历史区（占更宽比例）
        right_layout = QVBoxLayout()
        right_layout.setSpacing(4)
        lbl_history = QLabel("📑 历史交易 (Trade History)")
        lbl_history.setStyleSheet("font-weight: 800; font-size: 13px; color: #1e293b;")
        right_layout.addWidget(lbl_history)
        self.tbl_history = QTableWidget(0, 8)
        self.tbl_history.setHorizontalHeaderLabels(["标的", "方向", "策略", "平仓价", "盈亏", "退出类型", "时间", "原因"])
        hdr_hist = self.tbl_history.horizontalHeader()
        hdr_hist.setSectionResizeMode(QHeaderView.Interactive)
        hdr_hist.setMinimumSectionSize(44)
        self._configure_sim_history_table_columns()
        self.tbl_history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_history.setAlternatingRowColors(True)
        self.tbl_history.verticalHeader().setVisible(False)
        self.tbl_history.verticalHeader().setDefaultSectionSize(28)
        self.tbl_history.setWordWrap(False)
        self.tbl_history.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tbl_history.setMinimumHeight(190)
        right_layout.addWidget(self.tbl_history, 1)
        tables_lay.addLayout(right_layout, 6)
        layout.addLayout(tables_lay, 1)
        self._refresh_strategy_digest()

    def _configure_sim_positions_table_columns(self) -> None:
        widths = {
            0: 74,
            1: 100,
            2: 56,
            3: 72,
            4: 72,
            5: 72,
            6: 78,
            7: 88,
            8: 68,
            9: 92,
        }
        header = self.tbl_positions.horizontalHeader()
        for col, width in widths.items():
            self.tbl_positions.setColumnWidth(col, width)
        header.setStretchLastSection(False)

    def _configure_sim_history_table_columns(self) -> None:
        widths = {
            0: 82,
            1: 94,
            2: 88,
            3: 82,
            4: 76,
            5: 68,
            6: 86,
        }
        header = self.tbl_history.horizontalHeader()
        for col, width in widths.items():
            self.tbl_history.setColumnWidth(col, width)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        header.setStretchLastSection(True)

    def _build_card(self, title: str, value: str, color: str = "#1d4ed8") -> QLabel:
        card = QLabel()
        card.setAlignment(Qt.AlignCenter)
        card.setMinimumHeight(66)
        card.setMaximumHeight(74)
        card.setText(self._metric_card_html(title, value, color))
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def _metric_card_html(self, title: str, value: str, color: str = "#1d4ed8") -> str:
        return (
            f"<div style='font-size:11px;color:#64748b;line-height:1.25;'>{str(title or '').strip()}</div>"
            f"<div style='font-size:19px;font-weight:800;color:{color};line-height:1.2;'>{str(value or '').strip()}</div>"
        )

    def _compact_text(self, text: str, limit: int = 120) -> str:
        payload = " ".join(str(text or "").replace("\n", " | ").split()).strip()
        if len(payload) <= max(24, int(limit)):
            return payload
        return payload[: max(24, int(limit)) - 1].rstrip(" |") + "…"

    def _compact_status_text(self, text: str, *, line_limit: int = 4, line_char_limit: int = 74) -> str:
        raw_lines = [str(line or "").strip() for line in str(text or "").splitlines() if str(line or "").strip()]
        if not raw_lines:
            return ""
        compact_lines = []
        for line in raw_lines[: max(1, int(line_limit))]:
            compact_lines.append(self._compact_text(line, line_char_limit))
        remaining = len(raw_lines) - len(compact_lines)
        if remaining > 0:
            compact_lines.append(f"更多 {remaining} 条细节见悬浮提示。")
        return "\n".join(compact_lines)

    def _format_money(self, value: float) -> str:
        amount = float(value or 0.0)
        return f"${amount:,.2f}"

    def _format_price(self, value: float) -> str:
        return f"{float(value or 0.0):.2f}"

    def _classify_exit_type(self, reason: str) -> str:
        text = str(reason or "").strip()
        if not text:
            return "--"
        if "回撤至保本止损" in text:
            return "保本"
        if "目标2" in text:
            return "T2止盈"
        if "目标1" in text:
            return "T1止盈"
        if "浮盈达到" in text or "减仓" in text:
            return "锁盈减仓"
        if "爆仓" in text:
            return "爆仓"
        if "止盈" in text:
            return "止盈"
        if "止损" in text:
            return "止损"
        return "平仓"

    def _refresh_sim_balance_hint(self, config=None):
        config = config or get_runtime_config()
        amount = float(getattr(config, "sim_initial_balance", 1000.0) or 1000.0)
        lock_r = float(getattr(config, "sim_no_tp2_lock_r", 0.5) or 0.5)
        partial_ratio = float(getattr(config, "sim_no_tp2_partial_close_ratio", 0.5) or 0.5)
        sim_min_rr = float(getattr(config, "sim_min_rr", 1.6) or 1.6)
        sim_relaxed_rr = float(getattr(config, "sim_relaxed_rr", 1.3) or 1.3)
        sim_model_min_probability = float(getattr(config, "sim_model_min_probability", 0.68) or 0.68)
        exploratory_daily_limit = int(getattr(config, "sim_exploratory_daily_limit", 3) or 0)
        exploratory_cooldown_min = int(getattr(config, "sim_exploratory_cooldown_min", 10) or 0)
        exploratory_base_balance = float(getattr(config, "sim_exploratory_base_balance", amount) or amount)
        strategy_rr_summary, strategy_rr_tooltip = _build_strategy_rr_summary(config, separator=" | ")
        cooldown_text = "无冷却" if exploratory_cooldown_min <= 0 else f"{exploratory_cooldown_min}分钟"
        full_text = (
            "模拟试仓风格："
            f"本金 {self._format_money(amount)}"
            f" | 无 TP2 保本 {lock_r:.2f}R"
            f" | 首次减仓 {partial_ratio:.0%}"
            f" | 标准 RR {sim_min_rr:.2f}"
            f" | 放宽 RR {sim_relaxed_rr:.2f} + 模型 {sim_model_min_probability:.0%}"
            f" | 探索基准 {self._format_money(exploratory_base_balance)}"
            f" | 探索上限 {exploratory_daily_limit}次/日"
            f" | 探索冷却 {cooldown_text}"
            f" | {strategy_rr_summary}"
        )
        compact_text = (
            f"模拟账户 {self._format_money(amount)} | RR {sim_min_rr:.2f}/{sim_relaxed_rr:.2f} "
            f"| 模型 {sim_model_min_probability:.0%} | 探索 {exploratory_daily_limit}次/日 · {cooldown_text}"
        )
        self.lbl_sim_balance_hint.setText(compact_text)
        self.lbl_sim_balance_hint.setToolTip((full_text + "\n\n" + strategy_rr_tooltip).strip())

    def _apply_recent_trade_param_tooltip(self, history_rows: list, *, config=None, latest_apply: dict | None = None) -> None:
        base_tooltip = str(self.lbl_sim_balance_hint.toolTip() or "").strip()
        recent_snapshot_value = ""
        recent_closed_at = ""
        for row in list(history_rows or []):
            recent_snapshot_value = _row_value(row, "strategy_param_json", "")
            if _parse_strategy_param_snapshot(recent_snapshot_value):
                recent_closed_at = str(_row_value(row, "closed_at", "") or "").strip()
                break
        if not _parse_strategy_param_snapshot(recent_snapshot_value):
            self.lbl_sim_balance_hint.setToolTip(base_tooltip)
            return
        recent_text = _format_strategy_param_compare(
            recent_snapshot_value,
            config=config,
            latest_apply=latest_apply,
            closed_at=recent_closed_at,
        )
        combined = base_tooltip
        if recent_text:
            combined = (base_tooltip + "\n\n最近成交参数快照：\n" + recent_text).strip()
        self.lbl_sim_balance_hint.setToolTip(combined)

    def _format_signed_money(self, value: float) -> str:
        amount = float(value or 0.0)
        if amount > 0:
            return f"+${amount:,.2f}"
        if amount < 0:
            return f"-${abs(amount):,.2f}"
        return "$0.00"

    def _build_today_sim_trade_summary(self, history_rows: list) -> dict:
        today = datetime.now().strftime("%Y-%m-%d")
        total_count = 0
        win_count = 0
        loss_count = 0
        flat_count = 0
        net_profit = 0.0
        for row in list(history_rows or []):
            closed_at = str(_row_value(row, "closed_at", "") or "").strip()
            if not closed_at.startswith(today):
                continue
            profit = float(_row_value(row, "profit", 0.0) or 0.0)
            total_count += 1
            net_profit += profit
            if profit > 0:
                win_count += 1
            elif profit < 0:
                loss_count += 1
            else:
                flat_count += 1
        return {
            "total_count": total_count,
            "win_count": win_count,
            "loss_count": loss_count,
            "flat_count": flat_count,
            "net_profit": net_profit,
        }

    def _refresh_today_execution_summary(self, history_rows: list) -> None:
        now_dt = datetime.now()
        if (now_dt - self._today_execution_cache_time).total_seconds() < 8:
            cached_text, cached_style = self._today_execution_cache_payload
            self.lbl_today_execution.setText(cached_text)
            self.lbl_today_execution.setToolTip(cached_text)
            self.lbl_today_execution.setStyleSheet(cached_style)
            return
        try:
            audit_summary = summarize_today_execution_audits(trade_mode="simulation")
        except Exception:
            self.lbl_today_execution.setText("今日实际执行：执行审计读取失败，稍后再试。")
            self.lbl_today_execution.setToolTip(self.lbl_today_execution.text())
            self.lbl_today_execution.setStyleSheet(
                "background:#fffbeb;color:#92400e;border:1px solid #fde68a;"
                "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
            )
            return

        counts = dict(audit_summary.get("counts", {}) or {})
        reason_counts = dict(audit_summary.get("reason_counts", {}) or {})
        opened = int(counts.get("opened", 0) or 0)
        closed = int(counts.get("closed", 0) or 0)
        rejected = int(counts.get("rejected", 0) or 0)
        blocked = int(counts.get("blocked", 0) or 0)
        skipped = int(counts.get("skipped", 0) or 0)
        cooldown = int(reason_counts.get("exploratory_cooldown", 0) or 0)
        daily_limit = int(reason_counts.get("exploratory_daily_limit", 0) or 0)
        grade_gate = int(reason_counts.get("grade_gate", 0) or 0)
        trades = self._build_today_sim_trade_summary(history_rows)
        trade_total = int(trades.get("total_count", 0) or 0)
        win_count = int(trades.get("win_count", 0) or 0)
        loss_count = int(trades.get("loss_count", 0) or 0)
        flat_count = int(trades.get("flat_count", 0) or 0)
        net_profit = float(trades.get("net_profit", 0.0) or 0.0)

        parts = [
            f"开仓 {opened}",
            f"平仓 {closed or trade_total}",
            f"拒绝 {rejected}",
            f"阻塞 {blocked}",
        ]
        if skipped > 0:
            parts.append(f"跳过 {skipped}")
        parts.extend([f"冷却 {cooldown}", f"上限 {daily_limit}"])
        if grade_gate > 0:
            parts.append(f"观察级别 {grade_gate}")
        result_text = f"成交 {trade_total} 笔（盈 {win_count} / 亏 {loss_count} / 平 {flat_count}）"
        parts.append(result_text)
        parts.append(f"净盈亏 {self._format_signed_money(net_profit)}")

        text = "今日实际执行：" + " | ".join(parts)
        self.lbl_today_execution.setText(self._compact_text(text, 150))
        self.lbl_today_execution.setToolTip(text)
        if net_profit < 0:
            style_text = "background:#fef2f2;color:#991b1b;border:1px solid #fecaca;"
        elif opened > 0 or trade_total > 0:
            style_text = "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;"
        elif cooldown > 0 or daily_limit > 0 or blocked > 0 or rejected > 0:
            style_text = "background:#fffbeb;color:#92400e;border:1px solid #fde68a;"
        else:
            style_text = "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
        full_style = style_text + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        self.lbl_today_execution.setStyleSheet(full_style)
        self._today_execution_cache_time = now_dt
        self._today_execution_cache_payload = (self._compact_text(text, 150), full_style)

    def _resolve_primary_symbol(self, snapshot: dict | None) -> str:
        item, _direction_text = self._pick_primary_candidate(snapshot)
        if item:
            symbol = str(item.get("symbol", "") or "").strip().upper()
            if symbol:
                return symbol
        for raw in list((snapshot or {}).get("items", []) or []):
            normalized = _normalize_snapshot_item(raw)
            symbol = str(normalized.get("symbol", "") or "").strip().upper()
            if symbol:
                return symbol
        return ""

    def _set_latest_no_open_reason(self, text: str, tone: str = "warning") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "最近未开仓：等待本轮候选。"
        self.lbl_latest_no_open_reason.setText(self._compact_text(clean_text, 150))
        self.lbl_latest_no_open_reason.setToolTip(clean_text)
        self.lbl_latest_no_open_reason.setStyleSheet(
            palette.get(tone, palette["warning"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )

    def _set_grade_gate_focus(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "24h观察复盘：等待读取。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_grade_gate_focus.setText(self._compact_text(clean_text, 150))
        self.lbl_grade_gate_focus.setToolTip(clean_tooltip)
        self.lbl_grade_gate_focus.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )

    def _set_strategy_learning(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "策略学习：等待探索试仓样本。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_strategy_learning.setText(clean_text)
        self.lbl_strategy_learning.setToolTip(clean_tooltip)
        self.lbl_strategy_learning.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        self._refresh_strategy_digest()

    def _set_strategy_apply_summary(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "最近调参：暂无人工批准记录。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_strategy_apply.setText(clean_text)
        self.lbl_strategy_apply.setToolTip(clean_tooltip)
        self.lbl_strategy_apply.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:700;"
        )
        self._refresh_strategy_digest()

    def _set_strategy_apply_board(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "调参看板：暂无最近三次人工调参记录。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_strategy_apply_board.setText(clean_text)
        self.lbl_strategy_apply_board.setToolTip(clean_tooltip)
        self.lbl_strategy_apply_board.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )

    def _set_strategy_apply_impact(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "调参影响：等待最近一次带时间戳的人工调参记录。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_strategy_apply_impact.setText(clean_text)
        self.lbl_strategy_apply_impact.setToolTip(clean_tooltip)
        self.lbl_strategy_apply_impact.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        self._refresh_strategy_digest()

    def _set_strategy_apply_family_impact(self, text: str, tone: str = "neutral", tooltip: str = "") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "策略分组：等待最近一次带时间戳的人工调参记录。"
        clean_tooltip = str(tooltip or "").strip() or clean_text
        self.lbl_strategy_apply_family_impact.setText(clean_text)
        self.lbl_strategy_apply_family_impact.setToolTip(clean_tooltip)
        self.lbl_strategy_apply_family_impact.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 8px;font-size:11px;font-weight:600;"
        )
        self._refresh_strategy_digest()

    def _toggle_strategy_detail_panel(self, checked: bool) -> None:
        self.strategy_detail_panel.setVisible(bool(checked))
        self.btn_strategy_detail_toggle.setArrowType(Qt.DownArrow if checked else Qt.RightArrow)

    def _refresh_strategy_digest(self) -> None:
        learning_text = str(self.lbl_strategy_learning.text() or "").strip()
        apply_text = str(self.lbl_strategy_apply.text() or "").strip()
        impact_text = str(self.lbl_strategy_apply_impact.text() or "").strip()

        learning_summary = learning_text.replace("策略学习：", "", 1).strip() if learning_text.startswith("策略学习：") else learning_text
        apply_summary = apply_text.replace("最近调参：", "", 1).strip() if apply_text.startswith("最近调参：") else apply_text
        impact_summary = impact_text.replace("调参影响：", "", 1).strip() if impact_text.startswith("调参影响：") else impact_text

        parts = []
        if learning_summary:
            parts.append(learning_summary)
        if apply_summary:
            parts.append(f"调参 {apply_summary}")
        if impact_summary and "等待最近一次带时间戳的人工调参记录" not in impact_summary:
            parts.append(f"影响 {impact_summary}")
        digest_text = "策略复盘：" + " | ".join(parts[:3]) if parts else "策略复盘：等待后台摘要。"
        digest_tooltip = "\n".join(
            [
                "策略学习：",
                learning_text or "--",
                "",
                "最近调参：",
                apply_text or "--",
                "",
                "调参影响：",
                impact_text or "--",
            ]
        ).strip()
        self.lbl_strategy_digest.setText(digest_text)
        self.lbl_strategy_digest.setToolTip(digest_tooltip)

    def _refresh_strategy_param_summary(self, config=None) -> None:
        try:
            summary_text, tooltip = _build_strategy_rr_summary(config=config)
            text = "策略参数：" + summary_text
        except Exception:
            text = "策略参数：当前 RR 配置读取失败，稍后再试。"
            tooltip = text
        self._strategy_params_cache_text = text
        self._strategy_params_cache_tooltip = tooltip
        self.lbl_strategy_params.setText(text)
        self.lbl_strategy_params.setToolTip(tooltip)

    def _refresh_strategy_apply_summary(self) -> None:
        now_dt = datetime.now()
        if (now_dt - self._strategy_apply_cache_time).total_seconds() < 12:
            cached_text, cached_tooltip, cached_tone = self._strategy_apply_cache_payload
            self._set_strategy_apply_summary(cached_text, tone=cached_tone, tooltip=cached_tooltip)
            return
        payload = _load_latest_strategy_apply_summary()
        text = str(payload.get("text", "") or "").strip() or "最近调参：暂无人工批准记录。"
        tooltip = str(payload.get("tooltip", "") or "").strip() or text
        tone = str(payload.get("tone", "") or "neutral").strip() or "neutral"
        self._strategy_apply_cache_time = now_dt
        self._strategy_apply_cache_payload = (text, tooltip, tone)
        self._set_strategy_apply_summary(text, tone=tone, tooltip=tooltip)

    def _refresh_strategy_apply_board(self) -> None:
        now_dt = datetime.now()
        if (now_dt - self._strategy_apply_board_cache_time).total_seconds() < 12:
            cached_text, cached_tooltip, cached_tone = self._strategy_apply_board_cache_payload
            self._set_strategy_apply_board(cached_text, tone=cached_tone, tooltip=cached_tooltip)
            return
        payload = _load_recent_strategy_apply_board(limit=3)
        text = str(payload.get("text", "") or "").strip() or "调参看板：暂无最近三次人工调参记录。"
        tooltip = str(payload.get("tooltip", "") or "").strip() or text
        tone = str(payload.get("tone", "") or "neutral").strip() or "neutral"
        self._strategy_apply_board_cache_time = now_dt
        self._strategy_apply_board_cache_payload = (text, tooltip, tone)
        self._set_strategy_apply_board(text, tone=tone, tooltip=tooltip)

    def _apply_strategy_insight_payload(self, payload: dict) -> None:
        learning = dict(payload.get("learning", {}) or {})
        apply_summary = dict(payload.get("apply_summary", {}) or {})
        apply_board = dict(payload.get("apply_board", {}) or {})
        impact = dict(payload.get("impact", {}) or {})
        family_impact = dict(payload.get("family_impact", {}) or {})

        learning_text = str(learning.get("text", "") or "").strip() or "策略学习：等待探索试仓样本。"
        learning_tone = str(learning.get("tone", "") or "neutral").strip() or "neutral"
        learning_tooltip = str(learning.get("tooltip", "") or "").strip() or learning_text
        self._set_strategy_learning(learning_text, tone=learning_tone, tooltip=learning_tooltip)

        apply_text = str(apply_summary.get("text", "") or "").strip() or "最近调参：暂无人工批准记录。"
        apply_tone = str(apply_summary.get("tone", "") or "neutral").strip() or "neutral"
        apply_tooltip = str(apply_summary.get("tooltip", "") or "").strip() or apply_text
        self._set_strategy_apply_summary(apply_text, tone=apply_tone, tooltip=apply_tooltip)

        board_text = str(apply_board.get("text", "") or "").strip() or "调参看板：暂无最近三次人工调参记录。"
        board_tone = str(apply_board.get("tone", "") or "neutral").strip() or "neutral"
        board_tooltip = str(apply_board.get("tooltip", "") or "").strip() or board_text
        self._set_strategy_apply_board(board_text, tone=board_tone, tooltip=board_tooltip)

        impact_text = str(impact.get("text", "") or "").strip() or "调参影响：等待最近一次带时间戳的人工调参记录。"
        impact_tone = str(impact.get("tone", "") or "neutral").strip() or "neutral"
        impact_tooltip = str(impact.get("tooltip", "") or "").strip() or impact_text
        self._set_strategy_apply_impact(impact_text, tone=impact_tone, tooltip=impact_tooltip)

        family_text = str(family_impact.get("text", "") or "").strip() or "策略分组：等待最近一次带时间戳的人工调参记录。"
        family_tone = str(family_impact.get("tone", "") or "neutral").strip() or "neutral"
        family_tooltip = str(family_impact.get("tooltip", "") or "").strip() or family_text
        self._set_strategy_apply_family_impact(family_text, tone=family_tone, tooltip=family_tooltip)

    def _set_strategy_insight_loading(self) -> None:
        self._set_strategy_learning(
            "策略学习：正在后台整理近7天探索样本...",
            tone="info",
            tooltip="策略学习：正在后台整理近7天探索样本，页面已优先完成其余渲染。",
        )
        self._set_strategy_apply_summary(
            "最近调参：正在后台读取审批记录...",
            tone="info",
            tooltip="最近调参：正在后台读取审批记录，页面已优先完成其余渲染。",
        )
        self._set_strategy_apply_board(
            "调参看板：正在后台整理最近调参记录...",
            tone="info",
            tooltip="调参看板：正在后台整理最近调参记录，页面已优先完成其余渲染。",
        )
        self._set_strategy_apply_impact(
            "调参影响：正在后台统计全量历史样本...",
            tone="info",
            tooltip="调参影响：正在后台统计全量历史样本，页面已优先完成其余渲染。",
        )
        self._set_strategy_apply_family_impact(
            "策略分组：正在后台按策略族复盘调参前后...",
            tone="info",
            tooltip="策略分组：正在后台按策略族复盘调参前后，页面已优先完成其余渲染。",
        )

    def _start_strategy_insight_worker(self, worker) -> None:
        if not self.isVisible():
            worker()
            return
        threading.Thread(target=worker, daemon=True, name="ui-strategy-insight").start()

    def _run_strategy_insight_refresh(self, cache_key: str, sim_db_path: str | None) -> None:
        try:
            learning_payload = self._compute_strategy_learning_payload()
            apply_summary = _load_latest_strategy_apply_summary()
            apply_board = _load_recent_strategy_apply_board(limit=3)
            impact_payload = _build_strategy_apply_impact_summary(sim_db_path, apply_summary)
            family_payload = _build_strategy_family_apply_impact_summary(sim_db_path, apply_summary, limit=3)
            payload = {
                "cache_key": cache_key,
                "ok": True,
                "learning": learning_payload,
                "apply_summary": apply_summary,
                "apply_board": apply_board,
                "impact": impact_payload,
                "family_impact": family_payload,
                "finished_at": datetime.now(),
            }
        except Exception as exc:
            error_text = str(exc) or "未知错误"
            payload = {
                "cache_key": cache_key,
                "ok": False,
                "learning": {"text": "策略学习：后台整理失败，稍后再试。", "tone": "warning", "tooltip": error_text},
                "apply_summary": {"text": "最近调参：后台读取失败。", "tone": "warning", "tooltip": error_text, "updated_at": ""},
                "apply_board": {"text": "调参看板：后台整理失败。", "tone": "warning", "tooltip": error_text},
                "impact": {"text": "调参影响：后台统计失败。", "tone": "warning", "tooltip": error_text},
                "family_impact": {"text": "策略分组：后台统计失败。", "tone": "warning", "tooltip": error_text},
                "finished_at": datetime.now(),
            }
        self.strategy_insight_result_ready.emit(payload)

    def _on_strategy_insight_result(self, payload: dict) -> None:
        cache_key = str(payload.get("cache_key", "") or "").strip()
        if not cache_key or cache_key != self._strategy_insight_pending_key:
            return
        finished_at = payload.get("finished_at")
        self._strategy_insight_cache_time = finished_at if isinstance(finished_at, datetime) else datetime.now()
        self._strategy_insight_cache_key = cache_key
        self._strategy_insight_cache_payload = dict(payload or {})
        self._strategy_insight_pending_key = ""
        self._apply_strategy_insight_payload(payload)

        learning = dict(payload.get("learning", {}) or {})
        apply_summary = dict(payload.get("apply_summary", {}) or {})
        apply_board = dict(payload.get("apply_board", {}) or {})
        self._strategy_learning_cache_time = self._strategy_insight_cache_time
        self._strategy_learning_cache_payload = (
            str(learning.get("text", "") or "").strip() or "策略学习：等待探索试仓样本。",
            str(learning.get("tone", "") or "neutral").strip() or "neutral",
            str(learning.get("tooltip", "") or "").strip() or str(learning.get("text", "") or "").strip() or "策略学习：等待探索试仓样本。",
        )
        self._strategy_apply_cache_time = self._strategy_insight_cache_time
        self._strategy_apply_cache_payload = (
            str(apply_summary.get("text", "") or "").strip() or "最近调参：暂无人工批准记录。",
            str(apply_summary.get("tooltip", "") or "").strip() or str(apply_summary.get("text", "") or "").strip() or "最近调参：暂无人工批准记录。",
            str(apply_summary.get("tone", "") or "neutral").strip() or "neutral",
        )
        self._strategy_apply_board_cache_time = self._strategy_insight_cache_time
        self._strategy_apply_board_cache_payload = (
            str(apply_board.get("text", "") or "").strip() or "调参看板：暂无最近三次人工调参记录。",
            str(apply_board.get("tooltip", "") or "").strip() or str(apply_board.get("text", "") or "").strip() or "调参看板：暂无最近三次人工调参记录。",
            str(apply_board.get("tone", "") or "neutral").strip() or "neutral",
        )

    def _refresh_strategy_insights(self, sim_db_path: str | None) -> None:
        cache_key = str(sim_db_path or "").strip() or "default"
        now_dt = datetime.now()
        if (
            cache_key == self._strategy_insight_cache_key
            and (now_dt - self._strategy_insight_cache_time).total_seconds() < 12
        ):
            self._apply_strategy_insight_payload(self._strategy_insight_cache_payload)
            return
        if cache_key == self._strategy_insight_pending_key:
            self._set_strategy_insight_loading()
            return
        self._strategy_insight_pending_key = cache_key
        self._set_strategy_insight_loading()
        self._start_strategy_insight_worker(
            lambda: self._run_strategy_insight_refresh(cache_key, sim_db_path)
        )

    def _build_strategy_learning_suggestion(self, rows: list[dict]) -> tuple[str, str]:
        for row in rows:
            family = str(row.get("strategy_family", "") or "unknown").strip()
            label = _STRATEGY_FAMILY_LABEL_MAP.get(family, family)
            win_count = int(row.get("win_count", 0) or 0)
            loss_count = int(row.get("loss_count", 0) or 0)
            net_profit = float(row.get("net_profit", 0.0) or 0.0)
            decided_count = win_count + loss_count
            if decided_count >= 3 and loss_count >= 2 and net_profit < 0:
                return f"建议收紧{label}", "warning"
        for row in rows:
            family = str(row.get("strategy_family", "") or "unknown").strip()
            label = _STRATEGY_FAMILY_LABEL_MAP.get(family, family)
            win_count = int(row.get("win_count", 0) or 0)
            loss_count = int(row.get("loss_count", 0) or 0)
            net_profit = float(row.get("net_profit", 0.0) or 0.0)
            decided_count = win_count + loss_count
            win_rate = float(row.get("win_rate", 0.0) or 0.0)
            if decided_count >= 3 and win_count >= 2 and win_rate >= 60.0 and net_profit > 0:
                return f"{label}表现较好", "success"
        return "", ""

    def _refresh_strategy_learning_summary(self) -> None:
        now_dt = datetime.now()
        if (now_dt - self._strategy_learning_cache_time).total_seconds() < 12:
            cached_text, cached_tone, cached_tooltip = self._strategy_learning_cache_payload
            self._set_strategy_learning(cached_text, tone=cached_tone, tooltip=cached_tooltip)
            return
        payload = self._compute_strategy_learning_payload()
        text = str(payload.get("text", "") or "").strip() or "策略学习：等待探索试仓样本。"
        tone = str(payload.get("tone", "") or "neutral").strip() or "neutral"
        tooltip = str(payload.get("tooltip", "") or "").strip() or text
        self._strategy_learning_cache_time = now_dt
        self._strategy_learning_cache_payload = (text, tone, tooltip)
        self._set_strategy_learning(text, tone=tone, tooltip=tooltip)

    def _compute_strategy_learning_payload(self) -> dict:
        try:
            summary = summarize_trade_learning_by_strategy(days=7, limit=4)
        except Exception:
            text = "策略学习：交易学习日志读取失败，稍后再试。"
            return {"text": text, "tone": "warning", "tooltip": text}
        rows = list(summary.get("rows", []) or [])
        total_count = int(summary.get("total_count", 0) or 0)
        if total_count <= 0 or not rows:
            text = "策略学习：近7天暂无探索试仓样本。"
            return {"text": text, "tone": "neutral", "tooltip": text}
        parts = []
        tooltip_lines = [f"近7天策略族样本共 {total_count} 笔："]
        has_decided = False
        has_profit = False
        for row in rows:
            family = str(row.get("strategy_family", "") or "unknown").strip()
            label = _STRATEGY_FAMILY_LABEL_MAP.get(family, family)
            count = int(row.get("total_count", 0) or 0)
            win_count = int(row.get("win_count", 0) or 0)
            loss_count = int(row.get("loss_count", 0) or 0)
            net_profit = float(row.get("net_profit", 0.0) or 0.0)
            avg_rr = float(row.get("avg_rr", 0.0) or 0.0)
            decided_count = win_count + loss_count
            if decided_count > 0:
                has_decided = True
            if abs(net_profit) > 1e-9:
                has_profit = True
            win_rate = float(row.get("win_rate", 0.0) or 0.0)
            if decided_count > 0:
                parts.append(f"{label} {count}笔 {win_rate:.0f}%")
            else:
                parts.append(f"{label} {count}笔 待收盘")
            tooltip_lines.append(
                f"{label}：{count}笔，胜{win_count}/负{loss_count}，"
                f"净盈亏 {self._format_signed_money(net_profit)}，均RR {avg_rr:.2f}"
            )
        text = "策略学习：" + " | ".join(parts[:4])
        suggestion_text, suggestion_tone = self._build_strategy_learning_suggestion(rows)
        if suggestion_text:
            text += f" | {suggestion_text}"
            tooltip_lines.append(f"调参建议：{suggestion_text}。先人工确认，不自动修改配置。")
        tone = "success" if has_decided and has_profit else ("info" if has_decided else "neutral")
        if suggestion_tone == "warning":
            tone = "warning"
        elif suggestion_tone == "success" and tone != "warning":
            tone = "success"
        return {"text": text, "tone": tone, "tooltip": "\n".join(tooltip_lines)}

    def _start_grade_gate_focus_worker(self, worker) -> None:
        if not self.isVisible():
            worker()
            return
        threading.Thread(target=worker, daemon=True, name="ui-grade-gate-focus").start()

    def _build_grade_gate_focus_payload(self, report: dict) -> tuple[str, str, str]:
        scanned = int(report.get("scanned_count", 0) or 0)
        released = int(report.get("released_count", 0) or 0)
        accepted = int(report.get("policy_accepted_count", 0) or 0)
        top_labels = list(report.get("top_still_blocked_labels", []) or [])
        top_entry = dict(top_labels[0] or {}) if top_labels else {}
        top_label = str(top_entry.get("reason_label", "") or "").strip()
        top_count = int(top_entry.get("count", 0) or 0)
        secondary_labels = list(report.get("top_grade_gate_secondary_labels", []) or [])
        secondary_entry = dict(secondary_labels[0] or {}) if secondary_labels else {}
        secondary_label = str(secondary_entry.get("reason_label", "") or "").strip()
        secondary_count = int(secondary_entry.get("count", 0) or 0)
        tertiary_labels = list(report.get("top_rr_not_ready_tertiary_labels", []) or [])
        tertiary_entry = dict(tertiary_labels[0] or {}) if tertiary_labels else {}
        tertiary_label = str(tertiary_entry.get("reason_label", "") or "").strip()
        tertiary_count = int(tertiary_entry.get("count", 0) or 0)
        direction_components = list(report.get("top_no_direction_components", []) or [])
        if scanned <= 0:
            text = "24h观察复盘：暂无观察级别样本。"
            tone = "neutral"
        else:
            parts = [
                f"观察级别 {scanned}",
                f"可释放 {released}",
                f"预计执行 {accepted}",
            ]
            if top_label and top_count > 0:
                parts.append(f"主阻因 {top_label} {top_count}")
            if top_label == "未到试仓级别" and secondary_label and secondary_count > 0:
                parts.append(f"次阻因 {secondary_label} {secondary_count}")
                if secondary_label == "盈亏比未准备好" and tertiary_label and tertiary_count > 0:
                    parts.append(f"RR细分 {tertiary_label} {tertiary_count}")
                    if tertiary_label == "方向基础不足" and direction_components:
                        comp = dict(direction_components[0] or {})
                        comp_label = str(comp.get("reason_label", "") or "").strip()
                        comp_count = int(comp.get("count", 0) or 0)
                        if comp_label and comp_count > 0:
                            parts.append(f"方向细分 {comp_label} {comp_count}")
            text = "24h观察复盘：" + " | ".join(parts)
            tone = "success" if accepted > 0 else ("warning" if scanned > 0 else "neutral")
        tooltip = str(report.get("summary_text", "") or "").strip() or text
        return text, tone, tooltip

    def _run_grade_gate_focus_refresh(self, cache_key: str, daily_limit: int, cooldown_min: int) -> None:
        try:
            report = replay_exploratory_grade_gate(hours=24, daily_limit=daily_limit, cooldown_min=cooldown_min)
            text, tone, tooltip = self._build_grade_gate_focus_payload(dict(report or {}))
        except Exception:
            text = "24h观察复盘：回放读取失败，稍后再试。"
            tone = "warning"
            tooltip = text
        self.grade_gate_focus_result_ready.emit(
            {
                "cache_key": cache_key,
                "text": text,
                "tone": tone,
                "tooltip": tooltip,
                "finished_at": datetime.now(),
            }
        )

    def _on_grade_gate_focus_result(self, payload: dict) -> None:
        cache_key = str(payload.get("cache_key", "") or "").strip()
        if not cache_key or cache_key != self._grade_gate_focus_pending_key:
            return
        text = str(payload.get("text", "") or "").strip() or "24h观察复盘：等待读取。"
        tone = str(payload.get("tone", "") or "neutral").strip() or "neutral"
        tooltip = str(payload.get("tooltip", "") or "").strip() or text
        finished_at = payload.get("finished_at")
        cache_time = finished_at if isinstance(finished_at, datetime) else datetime.now()
        self._grade_gate_focus_cache_key = cache_key
        self._grade_gate_focus_cache_time = cache_time
        self._grade_gate_focus_cache_payload = (text, tone, tooltip)
        self._grade_gate_focus_pending_key = ""
        self._set_grade_gate_focus(text, tone=tone, tooltip=tooltip)

    def _refresh_grade_gate_focus(self, config=None) -> None:
        config = config or get_runtime_config()
        daily_limit = int(getattr(config, "sim_exploratory_daily_limit", 3) or 0)
        cooldown_min = int(getattr(config, "sim_exploratory_cooldown_min", 10) or 0)
        cache_key = f"{daily_limit}:{cooldown_min}"
        now_dt = datetime.now()
        if (
            cache_key == self._grade_gate_focus_cache_key
            and (now_dt - self._grade_gate_focus_cache_time).total_seconds() < 60
        ):
            cached_text, cached_tone, cached_tooltip = self._grade_gate_focus_cache_payload
            self._set_grade_gate_focus(cached_text, tone=cached_tone, tooltip=cached_tooltip)
            return
        if cache_key == self._grade_gate_focus_pending_key:
            self._set_grade_gate_focus(
                "24h观察复盘：正在后台整理观察级别样本...",
                tone="info",
                tooltip="24h观察复盘：正在后台整理观察级别样本，页面已优先完成其余渲染。",
            )
            return
        self._grade_gate_focus_pending_key = cache_key
        self._set_grade_gate_focus(
            "24h观察复盘：正在后台整理观察级别样本...",
            tone="info",
            tooltip="24h观察复盘：正在后台整理观察级别样本，页面已优先完成其余渲染。",
        )
        self._start_grade_gate_focus_worker(
            lambda: self._run_grade_gate_focus_refresh(cache_key, daily_limit, cooldown_min)
        )

    def _build_latest_no_open_reason_text(self, snapshot: dict | None, positions: list[dict]) -> tuple[str, str]:
        if positions:
            return "最近未开仓：当前已有持仓，系统暂停同品种重复开仓。", "success"
        if not snapshot:
            return "最近未开仓：等待下一轮行情刷新。", "neutral"

        primary_symbol = self._resolve_primary_symbol(snapshot)
        try:
            signal, reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)
        except Exception:
            signal, reason = None, ""
        try:
            current_audit = audit_rule_sim_signal_decision(snapshot or {}, allow_exploratory=True)
        except Exception:
            current_audit = {}

        if signal:
            return "最近未开仓：暂无，本轮已满足自动试仓条件。", "success"

        current_text = ""
        blocked_summary = list((current_audit or {}).get("blocked_summary", []) or [])
        if blocked_summary:
            label = str(blocked_summary[0].get("reason_label", "") or "").strip()
            count = int(blocked_summary[0].get("count", 0) or 0)
            if label:
                current_text = f"本轮主要拦截 {label}" + (f" {count}个" if count > 1 else "")
        if not current_text and str(reason or "").strip():
            current_text = str(reason or "").replace("\n", " ").strip().split("。")[0][:28]

        recent_text = ""
        if primary_symbol:
            try:
                rows = fetch_recent_execution_audits(hours=48, symbol=primary_symbol, limit=6)
            except Exception:
                rows = []
            blocked_row = next(
                (
                    row
                    for row in list(rows or [])
                    if str(row.get("decision_status", "") or "").strip().lower() in {"blocked", "rejected", "skipped"}
                ),
                None,
            )
            if blocked_row:
                occurred_at = str(blocked_row.get("occurred_at", "") or "").strip()
                time_text = occurred_at[5:16] if len(occurred_at) >= 16 else occurred_at or "--"
                reason_key = str(blocked_row.get("reason_key", "") or "").strip().lower()
                reason_text = str(blocked_row.get("reason_text", "") or "").strip()
                detail = _EXECUTION_REASON_LABEL_MAP.get(reason_key, "") or (reason_text[:18] if reason_text else "未写入原因")
                recent_text = f"最近留痕 {time_text} {detail}".strip()

        if current_text and recent_text:
            return f"最近未开仓：{current_text} | {recent_text}", "warning"
        if current_text:
            return f"最近未开仓：{current_text}", "warning"
        if recent_text:
            return f"最近未开仓：{recent_text}", "info"
        return "最近未开仓：当前没有新的阻断留痕，继续等下一轮候选。", "neutral"

    def _reset_sim_account(self, initial_balance: float | None, persist_config: bool):
        from mt5_sim_trading import SIM_ENGINE

        current_config = get_runtime_config()
        target_balance = float(initial_balance if initial_balance is not None else getattr(current_config, "sim_initial_balance", 1000.0) or 1000.0)
        target_balance = max(100.0, min(1000000.0, target_balance))
        confirm = QMessageBox.question(
            self,
            "重置模拟盘",
            (
                f"这会把模拟盘账户重置为 {self._format_money(target_balance)}，"
                "并清空当前模拟持仓与历史成交。\n\n"
                "这一步不可撤销，确定继续吗？"
            ),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        if persist_config:
            current_config.sim_initial_balance = target_balance
            save_runtime_config(current_config)
        SIM_ENGINE.reset_account(initial_balance=target_balance, clear_history=True)
        self._refresh_sim_balance_hint()
        self.update_data()
        QMessageBox.information(
            self,
            "模拟盘已重置",
            f"模拟盘账户已重置为 {self._format_money(target_balance)}，可以开始按新本金观察试仓效果。",
        )

    def _build_position_risk_metrics(self, sim_engine, pos: dict) -> dict:
        symbol = str(pos.get("symbol", "") or "").strip().upper()
        action = str(pos.get("action", "") or "").strip().lower()
        is_long = action == "long"
        quantity = float(pos.get("quantity", 0.0) or 0.0)
        entry_price = float(pos.get("entry_price", 0.0) or 0.0)
        stop_loss = float(pos.get("stop_loss", 0.0) or 0.0)
        take_profit = float(pos.get("take_profit", 0.0) or 0.0)
        take_profit_2 = float(pos.get("take_profit_2", 0.0) or 0.0)

        if not symbol or quantity <= 0 or min(entry_price, stop_loss, take_profit) <= 0:
            return {
                "risk_text": "--",
                "reward_text": "--",
                "ratio_text": "--",
            }

        _, risk_pnl = sim_engine._calculate_margin_and_pnl(symbol, quantity, entry_price, stop_loss, is_long)
        _, reward_pnl_1 = sim_engine._calculate_margin_and_pnl(symbol, quantity, entry_price, take_profit, is_long)
        reward_pnl_2 = 0.0
        if take_profit_2 > 0:
            _, reward_pnl_2 = sim_engine._calculate_margin_and_pnl(symbol, quantity, entry_price, take_profit_2, is_long)

        risk_amount = abs(float(risk_pnl or 0.0))
        reward_amount_1 = abs(float(reward_pnl_1 or 0.0))
        reward_amount_2 = abs(float(reward_pnl_2 or 0.0))
        if risk_amount <= 0:
            ratio_text = "--"
        elif reward_amount_2 > 0:
            ratio_text = f"{reward_amount_1 / risk_amount:.2f}R / {reward_amount_2 / risk_amount:.2f}R"
        else:
            ratio_text = f"{reward_amount_1 / risk_amount:.2f}R"

        if reward_amount_2 > 0:
            reward_text = f"T1 {self._format_money(reward_amount_1)} / T2 {self._format_money(reward_amount_2)}"
        else:
            reward_text = self._format_money(reward_amount_1)

        return {
            "risk_text": self._format_money(risk_amount),
            "reward_text": reward_text,
            "ratio_text": ratio_text,
            "risk_amount": risk_amount,
            "reward_amount_1": reward_amount_1,
            "reward_amount_2": reward_amount_2,
        }

    def _build_portfolio_risk_summary(self, sim_engine, positions: list[dict]) -> dict:
        total_risk = 0.0
        ratio_values = []
        for pos in list(positions or []):
            metrics = self._build_position_risk_metrics(sim_engine, pos)
            total_risk += float(metrics.get("risk_amount", 0.0) or 0.0)
            ratio_text = str(metrics.get("ratio_text", "") or "").strip()
            if not ratio_text or ratio_text == "--":
                continue
            first_ratio_text = ratio_text.split("/")[0].strip().replace("R", "").strip()
            try:
                ratio_values.append(float(first_ratio_text))
            except ValueError:
                continue
        if ratio_values:
            avg_rr_text = f"{sum(ratio_values) / len(ratio_values):.2f}R"
        else:
            avg_rr_text = "--"
        return {
            "total_risk_text": self._format_money(total_risk),
            "avg_rr_text": avg_rr_text,
        }

    def _set_entry_status(self, text: str, tone: str = "neutral") -> None:
        palette = {
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "danger": "background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;",
        }
        clean_text = str(text or "").strip() or "自动试仓状态：等待下一轮行情刷新。"
        self.lbl_entry_status.setText(self._compact_status_text(clean_text, line_limit=5, line_char_limit=82))
        self.lbl_entry_status.setToolTip(clean_text)
        self.lbl_entry_status.setStyleSheet(
            palette.get(tone, palette["info"])
            + "border-radius:8px;padding:7px 9px;font-size:11px;font-weight:600;line-height:1.35;"
        )

    def _set_entry_audit(self, text: str, tone: str = "neutral") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "试仓阻塞审计：等待本轮快照。"
        self.lbl_entry_audit.setText(self._compact_text(clean_text, 120))
        self.lbl_entry_audit.setToolTip(clean_text)
        self.lbl_entry_audit.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 7px;font-size:10px;font-weight:500;"
        )

    def _set_entry_trace(self, text: str, tone: str = "neutral") -> None:
        palette = {
            "neutral": "background:#f8fafc;color:#475569;border:1px solid #e2e8f0;",
            "warning": "background:#fffbeb;color:#92400e;border:1px solid #fde68a;",
            "info": "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;",
            "success": "background:#ecfdf5;color:#166534;border:1px solid #bbf7d0;",
        }
        clean_text = str(text or "").strip() or "最近执行明细：等待第一批执行留痕。"
        self.lbl_entry_trace.setText(self._compact_text(clean_text, 120))
        self.lbl_entry_trace.setToolTip(clean_text)
        self.lbl_entry_trace.setStyleSheet(
            palette.get(tone, palette["neutral"])
            + "border-radius:8px;padding:4px 7px;font-size:10px;font-weight:500;"
        )

    def _pick_primary_candidate(self, snapshot: dict | None) -> tuple[dict, str]:
        candidates = []
        for raw in list((snapshot or {}).get("items", []) or []):
            item = _normalize_snapshot_item(raw)
            symbol = str(item.get("symbol", "") or "").strip().upper()
            if not symbol or not bool(item.get("has_live_quote", False)):
                continue
            action, direction_text = _resolve_display_direction(item)
            if action == "neutral":
                continue
            rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
            ready_bonus = 10 if bool(item.get("risk_reward_ready", False)) else 0
            grade_bonus = 20 if str(item.get("trade_grade", "") or "").strip() == "可轻仓试仓" else 0
            candidates.append((grade_bonus + ready_bonus + rr, item, direction_text))
        if not candidates:
            return {}, ""
        _score, item, direction_text = sorted(candidates, key=lambda row: row[0], reverse=True)[0]
        return item, direction_text

    def _build_primary_candidate_details(self, snapshot: dict | None, signal: dict | None = None) -> dict:
        if signal:
            symbol = str(signal.get("symbol", "--") or "--").strip().upper()
            action = "做多" if str(signal.get("action", "") or "").strip().lower() == "long" else "做空"
            price = float(signal.get("price", 0.0) or 0.0)
            details = {
                "direction": f"{symbol} {action}",
                "price_label": "参考价",
                "price_text": self._format_price(price) if price > 0 else "",
                "rr_text": "",
                "zone_text": "",
            }
            snapshot_details = self._build_primary_candidate_details(snapshot, signal=None)
            if snapshot_details:
                details["rr_text"] = snapshot_details.get("rr_text", "")
                details["zone_text"] = snapshot_details.get("zone_text", "")
            return details

        item, direction_text = self._pick_primary_candidate(snapshot)
        if not item:
            return {}
        symbol = str(item.get("symbol", "--") or "--").strip().upper()
        latest = float(item.get("latest_price", 0.0) or 0.0)
        bid = float(item.get("bid", 0.0) or 0.0)
        ask = float(item.get("ask", 0.0) or 0.0)
        price = latest if latest > 0 else max(bid, ask, 0.0)
        rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
        zone_low = float(item.get("risk_reward_entry_zone_low", 0.0) or 0.0)
        zone_high = float(item.get("risk_reward_entry_zone_high", 0.0) or 0.0)
        zone_text = ""
        if zone_low > 0 and zone_high > 0:
            low, high = sorted((zone_low, zone_high))
            zone_text = f"{self._format_price(low)} - {self._format_price(high)}"
        return {
            "direction": f"{symbol} {direction_text}",
            "price_label": "现价",
            "price_text": self._format_price(price) if price > 0 else "",
            "rr_text": f"{rr:.2f}R" if rr > 0 else "",
            "zone_text": zone_text,
            }

    def _compose_entry_status(self, title: str, details: dict | None = None, extra_lines: list[str] | None = None) -> str:
        lines = [str(title or "").strip() or "自动试仓状态：等待下一轮行情刷新。"]
        detail_map = dict(details or {})
        detail_parts = []
        direction = str(detail_map.get("direction", "") or "").strip()
        if direction:
            detail_parts.append(f"方向：{direction}")
        price_label = str(detail_map.get("price_label", "") or "").strip()
        price_text = str(detail_map.get("price_text", "") or "").strip()
        if price_label and price_text:
            detail_parts.append(f"{price_label}：{price_text}")
        zone_text = str(detail_map.get("zone_text", "") or "").strip()
        if zone_text:
            detail_parts.append(f"执行区：{zone_text}")
        rr_text = str(detail_map.get("rr_text", "") or "").strip()
        if rr_text:
            detail_parts.append(f"盈亏比：{rr_text}")
        if detail_parts:
            lines.append(" · ".join(detail_parts))
        compact_lines = [str(raw_line or "").strip() for raw_line in list(extra_lines or []) if str(raw_line or "").strip()]
        for start in range(0, len(compact_lines), 2):
            lines.append(" | ".join(compact_lines[start:start + 2]))
        return "\n".join(lines)

    def _build_candidate_execution_text(self, details: dict, item: dict, blocked_reason: str = "") -> str:
        zone_text = str(details.get("zone_text", "") or "").strip()
        position_text = str(item.get("risk_reward_position_text", "") or "").strip()
        parts = []
        if zone_text:
            parts.append(f"观察区 {zone_text}")
        if position_text:
            parts.append(position_text)
        if not parts:
            state_text = str(item.get("risk_reward_state_text", "") or "").strip()
            if state_text:
                parts.append(state_text)
        if not parts and blocked_reason:
            if "观察区间" in blocked_reason:
                parts.append("价格还没回到观察区附近")
            elif "上沿" in blocked_reason or "下沿" in blocked_reason:
                parts.append("当前追价位置不理想")
        return "；".join(part for part in parts if part) or "等待价格回到更理想的执行位。"

    def _build_candidate_rr_text(self, details: dict, item: dict) -> str:
        rr_text = str(details.get("rr_text", "") or "").strip()
        parts = [rr_text] if rr_text else []
        stop_price = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
        target_price = float(item.get("risk_reward_target_price", 0.0) or 0.0)
        if stop_price > 0:
            parts.append(f"止损 {self._format_price(stop_price)}")
        if target_price > 0:
            parts.append(f"目标 {self._format_price(target_price)}")
        return "；".join(parts) or "盈亏比仍在等待系统确认。"

    def _build_candidate_quote_text(self, item: dict) -> str:
        state_text = str(item.get("alert_state_text", "") or "").strip()
        state_detail = str(item.get("alert_state_detail", "") or "").strip()
        if state_text and state_detail:
            return f"{state_text}；{state_detail}"
        if state_text:
            return state_text
        quote_status = _format_quote_status_text(item)
        spread_points = float(item.get("spread_points", 0.0) or 0.0)
        if spread_points > 0:
            return f"{quote_status}；当前点差约 {spread_points:.0f} 点。"
        return quote_status or "等待报价进一步稳定。"

    def _build_candidate_event_text(self, item: dict) -> str:
        event_note = str(item.get("event_note", "") or "").strip()
        if event_note:
            return event_note
        event_mode_text = str(item.get("event_mode_text", "") or "").strip()
        trade_next_review = str(item.get("trade_next_review", "") or "").strip()
        if event_mode_text and trade_next_review:
            return f"{event_mode_text}；{trade_next_review}"
        if event_mode_text:
            return event_mode_text
        if trade_next_review:
            return trade_next_review
        return "当前没有额外事件硬性阻断，继续按执行纪律复核。"

    def _build_candidate_execution_model_text(self, item: dict) -> str:
        execution_ready = bool(item.get("execution_model_ready", False))
        execution_probability = float(item.get("execution_open_probability", 0.0) or 0.0)
        execution_note = str(item.get("execution_model_note", "") or "").strip()
        if execution_ready and execution_probability > 0:
            parts = [f"就绪度约 {execution_probability * 100:.0f}%"]
            if execution_note:
                cleaned_note = execution_note.replace("本地执行模型参考就绪度约", "").strip(" 。")
                if cleaned_note:
                    parts.append(cleaned_note)
            return "；".join(parts)
        if execution_note:
            return execution_note
        return "历史执行样本仍在积累，先按结构和报价纪律处理。"

    def _build_candidate_status_lines(
        self,
        snapshot: dict | None,
        details: dict | None = None,
        blocked_reason: str = "",
        is_signal_ready: bool = False,
    ) -> list[str]:
        detail_map = dict(details or {})
        item, _direction_text = self._pick_primary_candidate(snapshot)
        if not item and detail_map:
            lines = []
            zone_text = str(detail_map.get("zone_text", "") or "").strip()
            rr_text = str(detail_map.get("rr_text", "") or "").strip()
            if zone_text:
                lines.append(f"执行位：观察区 {zone_text}")
            if rr_text:
                lines.append(f"盈亏比：{rr_text}")
            if is_signal_ready:
                lines.append("执行状态：条件已满足，若未开仓请复核保证金或重复持仓限制。")
            elif blocked_reason:
                lines.append(f"拦截原因：{blocked_reason}")
            return lines

        lines = [
            f"执行位：{self._build_candidate_execution_text(detail_map, item, blocked_reason)}",
            f"盈亏比：{self._build_candidate_rr_text(detail_map, item)}",
            f"点差状态：{self._build_candidate_quote_text(item)}",
            f"事件纪律：{self._build_candidate_event_text(item)}",
            f"执行模型：{self._build_candidate_execution_model_text(item)}",
        ]
        if is_signal_ready:
            lines.append("执行状态：条件已满足，若未开仓请复核保证金或重复持仓限制。")
        elif blocked_reason:
            lines.append(f"拦截原因：{blocked_reason}")
        else:
            lines.append("执行状态：方向和结构存在，但还没满足自动试仓执行纪律。")
        return lines

    def _classify_history_block_reason(self, entry: dict) -> str:
        trade_source = str(entry.get("trade_grade_source", "") or "").strip().lower()
        trade_grade = str(entry.get("trade_grade", "") or "").strip()
        detail = str(entry.get("trade_grade_detail", "") or entry.get("detail", "") or "").strip()
        signal_side = str(entry.get("signal_side", "") or "").strip().lower()
        risk_ready = bool(entry.get("risk_reward_ready", False))
        event_note = str(entry.get("event_note", "") or "").strip()
        model_ready = bool(entry.get("model_ready", False))
        model_probability = float(entry.get("model_win_probability", 0.0) or 0.0)

        if trade_source == "event" or event_note or "等待事件落地" in trade_grade or "事件" in detail:
            return "事件窗口"
        if not risk_ready:
            return "盈亏比未就绪"
        if model_ready and 0.0 < model_probability < 0.35:
            return "模型胜率低"
        if signal_side not in {"long", "short"}:
            return "方向不清晰"
        if "分歧" in detail or "假突破" in detail or "重新同向" in detail:
            return "多周期分歧"
        if "点差" in detail:
            return "点差约束"
        if "观察区间" in detail or "回踩" in detail or "反抽" in detail:
            return "未到执行区"
        return "执行纪律未满足"

    def _build_recent_execution_audit_text(self, snapshot: dict | None, primary_symbol: str) -> tuple[str, str]:
        snapshot_time = str((snapshot or {}).get("last_refresh_text", "") or "").strip()
        if not snapshot_time or not primary_symbol:
            return "", ""
        now_dt = datetime.now()
        cache_key = f"{primary_symbol}:{snapshot_time[:16]}"
        if (
            cache_key == self._recent_execution_summary_cache_key
            and (now_dt - self._recent_execution_summary_cache_time).total_seconds() < 8
        ):
            return self._recent_execution_summary_cache_payload
        try:
            summary = summarize_execution_audits(hours=48, symbol=primary_symbol)
            reason_rows = summarize_execution_reason_counts(hours=48, symbol=primary_symbol, limit=3)
        except Exception:
            return "", ""

        total_count = int(summary.get("total_count", 0) or 0)
        counts = dict(summary.get("counts", {}) or {})
        if total_count <= 0:
            return "", ""

        opened = int(counts.get("opened", 0) or 0)
        rejected = int(counts.get("rejected", 0) or 0)
        blocked = int(counts.get("blocked", 0) or 0)
        skipped = int(counts.get("skipped", 0) or 0)
        parts = [f"已尝试 {total_count} 次", f"开仓 {opened} 次"]
        if rejected > 0:
            parts.append(f"拒绝 {rejected} 次")
        if blocked > 0:
            parts.append(f"阻塞 {blocked} 次")
        if skipped > 0:
            parts.append(f"跳过 {skipped} 次")
        history_text = f"最近48小时执行：{' | '.join(parts)}"

        if reason_rows:
            reason_parts = []
            for row in reason_rows:
                reason_key = str(row.get("reason_key", "") or "").strip().lower()
                reason_text = str(row.get("reason_text", "") or "").strip()
                count = int(row.get("count", 0) or 0)
                label = _EXECUTION_REASON_LABEL_MAP.get(reason_key, "")
                if not label:
                    label = reason_text[:16] if reason_text else "未写入原因"
                if label and count > 0:
                    reason_parts.append(f"{label} {count}次")
            if reason_parts:
                history_text += f"\n主要阻断：{' | '.join(reason_parts)}"

        tone = "success" if opened > 0 else "warning"
        self._recent_execution_summary_cache_key = cache_key
        self._recent_execution_summary_cache_time = now_dt
        self._recent_execution_summary_cache_payload = (history_text, tone)
        return history_text, tone

    def _build_recent_execution_trace_text(self, primary_symbol: str) -> tuple[str, str]:
        if not primary_symbol:
            return "最近执行明细：等待本轮候选品种。", "neutral"
        now_dt = datetime.now()
        cache_key = primary_symbol
        if (
            cache_key == self._recent_execution_trace_cache_key
            and (now_dt - self._recent_execution_trace_cache_time).total_seconds() < 6
        ):
            return self._recent_execution_trace_cache_payload
        try:
            rows = fetch_recent_execution_audits(hours=48, symbol=primary_symbol, limit=4)
        except Exception:
            return "最近执行明细：执行留痕读取失败，稍后再试。", "warning"
        if not rows:
            return "最近执行明细：最近48小时还没有新的真实执行留痕。", "neutral"

        parts = []
        tone = "info"
        for row in rows:
            occurred_at = str(row.get("occurred_at", "") or "").strip()
            time_text = occurred_at[5:16] if len(occurred_at) >= 16 else occurred_at or "--"
            status = _EXECUTION_STATUS_LABEL_MAP.get(str(row.get("decision_status", "") or "").strip().lower(), "已记录")
            action = str(row.get("action", "") or "").strip().lower()
            action_text = "做多" if action == "long" else ("做空" if action == "short" else "观望")
            reason_key = str(row.get("reason_key", "") or "").strip().lower()
            reason_label = _EXECUTION_REASON_LABEL_MAP.get(reason_key, "")
            reason_text = str(row.get("reason_text", "") or "").strip()
            detail = reason_label or (reason_text[:18] if reason_text else "")
            line = f"{time_text} {status} {primary_symbol} {action_text}".strip()
            if detail:
                line += f" · {detail}"
            parts.append(line)
            if str(row.get("decision_status", "") or "").strip().lower() in {"opened", "closed"}:
                tone = "success"
        payload = (f"最近执行明细：{'；'.join(parts)}", tone)
        self._recent_execution_trace_cache_key = cache_key
        self._recent_execution_trace_cache_time = now_dt
        self._recent_execution_trace_cache_payload = payload
        return payload

    def _build_recent_block_audit_text(self, snapshot: dict | None) -> tuple[str, str]:
        current_audit = audit_rule_sim_signal_decision(snapshot or {})
        current_parts = []
        for row in list(current_audit.get("blocked_summary", []) or [])[:3]:
            label = str(row.get("reason_label", "") or "").strip()
            count = int(row.get("count", 0) or 0)
            if label and count > 0:
                current_parts.append(f"{label} {count}个")
        ready_count = int(current_audit.get("ready_count", 0) or 0)
        if ready_count > 0:
            current_text = f"本轮候选：已有 {ready_count} 个满足自动试仓条件。"
            tone = "success"
        elif current_parts:
            current_text = f"本轮阻塞：{' | '.join(current_parts)}"
            tone = "warning"
        else:
            current_text = "本轮阻塞：当前没有形成可审计的候选结构。"
            tone = "neutral"

        primary_symbol = ""
        item, _direction_text = self._pick_primary_candidate(snapshot)
        if item:
            primary_symbol = str(item.get("symbol", "") or "").strip().upper()
        if not primary_symbol:
            for raw in list((snapshot or {}).get("items", []) or []):
                normalized = _normalize_snapshot_item(raw)
                symbol = str(normalized.get("symbol", "") or "").strip().upper()
                if symbol:
                    primary_symbol = symbol
                    break

        execution_text, execution_tone = self._build_recent_execution_audit_text(snapshot, primary_symbol)
        if execution_text:
            if execution_tone == "success":
                tone = "success"
            elif tone != "success" and execution_tone:
                tone = execution_tone
            return f"试仓阻塞审计：{current_text}\n{execution_text}", tone

        history_text = "最近48小时阻塞：暂无足够留痕样本。"
        if primary_symbol:
            cutoff = datetime.now() - timedelta(hours=48)
            counts: dict[str, int] = {}
            for entry in list(read_full_history())[::-1]:
                symbol = str(entry.get("symbol", "") or "").strip().upper()
                if symbol != primary_symbol:
                    continue
                occurred_at = str(entry.get("occurred_at", "") or "").strip()
                try:
                    occurred_dt = datetime.strptime(occurred_at, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                if occurred_dt < cutoff:
                    continue
                trade_grade = str(entry.get("trade_grade", "") or "").strip()
                if trade_grade not in {"只适合观察", "等待事件落地", "当前不宜出手"}:
                    continue
                label = self._classify_history_block_reason(entry)
                counts[label] = int(counts.get(label, 0) or 0) + 1
            if counts:
                top_parts = [f"{label} {count}次" for label, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:3]]
                history_text = f"最近48小时阻塞：{' | '.join(top_parts)}"

        return f"试仓阻塞审计：{current_text}\n{history_text}", tone

    def _update_entry_status(self, snapshot: dict | None, positions: list[dict]) -> None:
        primary_symbol = self._resolve_primary_symbol(snapshot)

        if positions:
            first = dict(positions[0] or {})
            symbol = str(first.get("symbol", "--") or "--").strip().upper()
            action = "做多" if str(first.get("action", "") or "").strip().lower() == "long" else "做空"
            quantity = float(first.get("quantity", 0.0) or 0.0)
            self._set_entry_status(
                self._compose_entry_status(
                    "自动试仓状态：当前已有持仓",
                    details={"direction": f"{symbol} {action} {quantity:.2f} 手"},
                    extra_lines=["当前重点：先看保本保护和止盈退出。"],
                ),
                tone="success",
            )
            self._set_entry_audit(
                "试仓阻塞审计：当前已有持仓，阻塞统计暂停累计。\n最近48小时阻塞：持仓中的样本不纳入未开仓审计。",
                tone="success",
            )
            trace_text, trace_tone = self._build_recent_execution_trace_text(symbol)
            self._set_entry_trace(trace_text, tone=trace_tone)
            return

        if not snapshot:
            self._set_entry_status("自动试仓状态：等待下一轮行情刷新，系统暂时还没有新的试仓判断。", tone="info")
            self._set_entry_audit("试仓阻塞审计：等待本轮快照。", tone="info")
            self._set_entry_trace("最近执行明细：等待第一批执行留痕。", tone="info")
            return

        signal, reason = build_rule_sim_signal_decision(snapshot)
        trace_text, trace_tone = self._build_recent_execution_trace_text(primary_symbol)
        if signal:
            candidate_details = self._build_primary_candidate_details(snapshot, signal=signal)
            self._set_entry_status(
                self._compose_entry_status(
                    "自动试仓状态：已满足自动试仓条件",
                    details=candidate_details,
                    extra_lines=self._build_candidate_status_lines(snapshot, candidate_details, is_signal_ready=True),
                ),
                tone="info",
            )
            audit_text, audit_tone = self._build_recent_block_audit_text(snapshot)
            self._set_entry_audit(audit_text, tone="success" if audit_tone == "success" else "info")
            self._set_entry_trace(trace_text, tone=trace_tone)
            return

        if reason:
            candidate_details = self._build_primary_candidate_details(snapshot)
            self._set_entry_status(
                self._compose_entry_status(
                    "自动试仓状态：当前未开仓",
                    details=candidate_details,
                    extra_lines=self._build_candidate_status_lines(
                        snapshot,
                        candidate_details,
                        blocked_reason=str(reason or "").strip(),
                    ),
                ),
                tone="warning",
            )
            audit_text, audit_tone = self._build_recent_block_audit_text(snapshot)
            self._set_entry_audit(audit_text, tone=audit_tone)
            self._set_entry_trace(trace_text, tone=trace_tone)
            return

        candidate_details = self._build_primary_candidate_details(snapshot)
        if candidate_details:
            self._set_entry_status(
                self._compose_entry_status(
                    "自动试仓状态：当前未开仓",
                    details=candidate_details,
                    extra_lines=self._build_candidate_status_lines(snapshot, candidate_details),
                ),
                tone="warning",
            )
            audit_text, audit_tone = self._build_recent_block_audit_text(snapshot)
            self._set_entry_audit(audit_text, tone=audit_tone)
            self._set_entry_trace(trace_text, tone=trace_tone)
            return

        self._set_entry_status("自动试仓状态：当前还没有满足自动试仓条件的多空结构机会。", tone="danger")
        self._set_entry_audit("试仓阻塞审计：本轮没有形成可评估候选，最近48小时阻塞暂无法归因。", tone="neutral")
        self._set_entry_trace("最近执行明细：最近48小时还没有新的真实执行留痕。", tone="neutral")

    def update_data(self, snapshot: dict | None = None):
        """拉取 SIM_ENGINE 渲染表格"""
        from mt5_sim_trading import SIM_ENGINE
        import sqlite3
        self.setUpdatesEnabled(False)
        self.tbl_positions.setUpdatesEnabled(False)
        self.tbl_history.setUpdatesEnabled(False)
        try:
            config = get_runtime_config()
            self._refresh_sim_balance_hint(config=config)
            self._refresh_grade_gate_focus(config=config)
            self._refresh_strategy_param_summary(config=config)
            # 获取账户
            account = SIM_ENGINE.get_account()
            self._refresh_strategy_insights(SIM_ENGINE.db_file)
            latest_apply_payload = _load_latest_strategy_apply_summary()
            strategy_insight_pending = bool(self._strategy_insight_pending_key)
            initial_balance = float(getattr(config, "sim_initial_balance", 1000.0) or 1000.0)
            balance = float(account.get("balance", initial_balance))
            equity = float(account.get("equity", 100000.0))
            profit = float(account.get("total_profit", 0.0))
            margin = float(account.get("used_margin", 0.0))
            wins = int(account.get("win_count", 0))
            losses = int(account.get("loss_count", 0))
            total_trades = wins + losses
            win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

            profit_color = "#16a34a" if profit > 0 else ("#dc2626" if profit < 0 else "#475569")
            profit_sign = "+" if profit > 0 else ""
            drawdown_pct = 0.0
            if initial_balance > 0:
                drawdown_pct = max(0.0, (initial_balance - equity) / initial_balance * 100.0)
            self.lbl_initial_balance.setText(self._metric_card_html("起始本金", self._format_money(initial_balance), "#0f766e"))
            self.lbl_current_balance.setText(self._metric_card_html("当前余额", self._format_money(balance), "#1d4ed8"))
            self.lbl_drawdown_pct.setText(self._metric_card_html("当前回撤", f"{drawdown_pct:.1f}%", "#dc2626"))

            self.lbl_equity.setText(self._metric_card_html("可用净值", f"${equity:,.2f}", "#1e293b"))
            self.lbl_profit.setText(self._metric_card_html("累计盈亏", f"{profit_sign}${profit:,.2f}", profit_color))
            self.lbl_margin.setText(self._metric_card_html("已用保证金", f"${margin:,.2f}", "#f59e0b"))
            self.lbl_win_rate.setText(self._metric_card_html("历史胜率", f"{win_rate:.1f}% ({wins}W/{losses}L)", "#1d4ed8"))

            # 持仓表
            pos_list = SIM_ENGINE.get_open_positions()
            latest_reason_text, latest_reason_tone = self._build_latest_no_open_reason_text(snapshot, pos_list)
            self._set_latest_no_open_reason(latest_reason_text, tone=latest_reason_tone)
            self._update_entry_status(snapshot, pos_list)
            portfolio_summary = self._build_portfolio_risk_summary(SIM_ENGINE, pos_list)
            self.lbl_total_risk.setText(
                self._metric_card_html("总风险暴露", portfolio_summary["total_risk_text"], "#dc2626")
            )
            self.lbl_avg_rr.setText(
                self._metric_card_html("平均盈亏比", portfolio_summary["avg_rr_text"], "#7c3aed")
            )
            self.tbl_positions.setRowCount(len(pos_list))
            for i, pos in enumerate(pos_list):
                pnl = float(pos["floating_pnl"])
                pnl_str = f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}"
                c_pnl = QColor("#e6ffe6") if pnl > 0 else (QColor("#ffe6e6") if pnl < 0 else QColor("#ffffff"))
                metrics = self._build_position_risk_metrics(SIM_ENGINE, pos)

                items = [
                    pos["symbol"],
                    f"{'做多' if pos['action'] == 'long' else '做空'} / {_format_execution_profile_text(pos.get('execution_profile', 'standard'))}",
                    f"{float(pos['quantity']):.2f}",
                    self._format_price(float(pos["entry_price"])),
                    self._format_price(float(pos["stop_loss"])),
                    self._format_price(float(pos["take_profit"])),
                    metrics["risk_text"],
                    metrics["reward_text"],
                    metrics["ratio_text"],
                    pnl_str
                ]
                for col, val in enumerate(items):
                    val_str = str(val)
                    cell = self.tbl_positions.item(i, col)
                    if not cell:
                        cell = QTableWidgetItem(val_str)
                        cell.setTextAlignment(Qt.AlignCenter)
                        self.tbl_positions.setItem(i, col, cell)
                    else:
                        cell.setText(val_str)
                    if col == 9:  # 给盈亏上色
                        cell.setBackground(c_pnl)
                    else:
                        cell.setBackground(QColor("#ffffff"))
                position_param_tip = _format_strategy_param_compare(
                    pos.get("strategy_param_json", ""),
                    config=config,
                    latest_apply=latest_apply_payload,
                )
                if position_param_tip:
                    self.tbl_positions.item(i, 1).setToolTip(position_param_tip)

            # 历史表 (抓取最后 50 条)
            try:
                with closing(sqlite3.connect(SIM_ENGINE.db_file)) as conn:
                    conn.row_factory = sqlite3.Row
                    history_rows = conn.execute("SELECT * FROM sim_trades ORDER BY id DESC LIMIT 50").fetchall()
            except sqlite3.OperationalError:
                history_rows = []

            self._apply_recent_trade_param_tooltip(history_rows, config=config, latest_apply=latest_apply_payload)
            if not strategy_insight_pending:
                impact_payload = _build_strategy_apply_impact_summary(SIM_ENGINE.db_file, latest_apply_payload)
                self._set_strategy_apply_impact(
                    str(impact_payload.get("text", "") or "").strip(),
                    tone=str(impact_payload.get("tone", "") or "neutral").strip() or "neutral",
                    tooltip=str(impact_payload.get("tooltip", "") or "").strip(),
                )
                family_impact_payload = _build_strategy_family_apply_impact_summary(
                    SIM_ENGINE.db_file,
                    latest_apply_payload,
                    limit=3,
                )
                self._set_strategy_apply_family_impact(
                    str(family_impact_payload.get("text", "") or "").strip(),
                    tone=str(family_impact_payload.get("tone", "") or "neutral").strip() or "neutral",
                    tooltip=str(family_impact_payload.get("tooltip", "") or "").strip(),
                )
            self._refresh_today_execution_summary(history_rows)
            self.tbl_history.setRowCount(len(history_rows))
            for i, row in enumerate(history_rows):
                profit = float(row["profit"])
                pnl_str = f"+${profit:,.2f}" if profit > 0 else f"-${abs(profit):,.2f}"
                c_pnl = QColor("#e6ffe6") if profit > 0 else (QColor("#ffe6e6") if profit < 0 else QColor("#ffffff"))
                time_short = str(row["closed_at"])[5:16] # MM-DD HH:MM

                exit_type = self._classify_exit_type(str(row["reason"] or ""))
                items = [
                    row["symbol"],
                    f"{'做多' if row['action'] == 'long' else '做空'} / {_format_execution_profile_text(_row_value(row, 'execution_profile', 'standard'))}",
                    _format_strategy_family_text(_row_value(row, "strategy_family", "")),
                    f"{float(row['exit_price']):.2f}",
                    pnl_str,
                    exit_type,
                    time_short,
                    row["reason"]
                ]
                for col, val in enumerate(items):
                    val_str = str(val)
                    cell = self.tbl_history.item(i, col)
                    if not cell:
                        cell = QTableWidgetItem(val_str)
                        cell.setTextAlignment(Qt.AlignCenter)
                        self.tbl_history.setItem(i, col, cell)
                    else:
                        cell.setText(val_str)
                    if col == 4:  # 盈亏颜色
                        cell.setBackground(c_pnl)
                    else:
                        cell.setBackground(QColor("#ffffff"))
                history_param_tip = _format_strategy_param_compare(
                    _row_value(row, "strategy_param_json", ""),
                    config=config,
                    latest_apply=latest_apply_payload,
                    closed_at=str(_row_value(row, "closed_at", "") or "").strip(),
                )
                if history_param_tip:
                    self.tbl_history.item(i, 2).setToolTip(history_param_tip)
                    reason_tip = str(_row_value(row, "reason", "") or "").strip()
                    if reason_tip:
                        self.tbl_history.item(i, 7).setToolTip(reason_tip + "\n\n" + history_param_tip)
                    else:
                        self.tbl_history.item(i, 7).setToolTip(history_param_tip)
        finally:
            self.tbl_positions.setUpdatesEnabled(True)
            self.tbl_history.setUpdatesEnabled(True)
            self.setUpdatesEnabled(True)


# ─────────────────────────────────────────────
#  PendingRulesPanel  （待审规则批准台面板）
# ─────────────────────────────────────────────
class PendingRulesPanel(QWidget):
    pending_rules_loaded = Signal(dict)
    rule_status_updated = Signal(dict)
    REVIEW_HORIZON_MIN = 30

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pending_rules_loaded.connect(self._on_pending_rules_loaded)
        self.rule_status_updated.connect(self._on_rule_status_updated)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        self._last_learning_snapshot = {
            "counts": {},
            "recent_stats": {},
            "health_stats": {},
            "latest_rule": {},
            "recent_rules": [],
        }
        self._last_strategy_apply_message = ""

        header_lay = QHBoxLayout()
        title = QLabel("🛡️ 待审规则批准台 (HITL)")
        title.setStyleSheet("font-weight: 800; font-size: 16px; color: #1e293b;")
        header_lay.addWidget(title)

        self.btn_refresh = QPushButton("⟳ 刷新待审列表")
        self.btn_refresh.setFixedWidth(120)
        self.btn_refresh.setStyleSheet("background:#1d4ed8;color:white;border-radius:4px;padding:4px;")
        self.btn_refresh.clicked.connect(self.load_pending_rules)
        header_lay.addWidget(self.btn_refresh)
        self.btn_copy_learning_summary = QPushButton("复制学习摘要")
        self.btn_copy_learning_summary.setFixedWidth(108)
        self.btn_copy_learning_summary.setStyleSheet("background:#0f766e;color:white;border-radius:4px;padding:4px;")
        self.btn_copy_learning_summary.clicked.connect(self._copy_learning_summary)
        header_lay.addWidget(self.btn_copy_learning_summary)
        self.lbl_pending_status = QLabel("")
        self.lbl_pending_status.setStyleSheet("color:#64748b;font-size:12px;")
        header_lay.addWidget(self.lbl_pending_status)
        self.lbl_pending_review_count = QLabel("人工复核 --")
        self.lbl_pending_review_count.setStyleSheet(
            "background:#ede9fe;color:#6d28d9;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_review_count)
        self.lbl_pending_accumulate_count = QLabel("待积累 --")
        self.lbl_pending_accumulate_count.setStyleSheet(
            "background:#fef3c7;color:#92400e;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_accumulate_count)
        self.lbl_pending_archived_count = QLabel("自动归档 --")
        self.lbl_pending_archived_count.setStyleSheet(
            "background:#e2e8f0;color:#475569;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_archived_count)
        self.lbl_pending_active_count = QLabel("启用 --")
        self.lbl_pending_active_count.setStyleSheet(
            "background:#dcfce7;color:#166534;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_active_count)
        self.lbl_pending_frozen_count = QLabel("冻结 --")
        self.lbl_pending_frozen_count.setStyleSheet(
            "background:#fee2e2;color:#b91c1c;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_frozen_count)
        self.lbl_pending_reference_count = QLabel("基础参考 --")
        self.lbl_pending_reference_count.setStyleSheet(
            "background:#eff6ff;color:#1d4ed8;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_reference_count)
        self.lbl_pending_recent_count = QLabel("24h新增 --")
        self.lbl_pending_recent_count.setStyleSheet(
            "background:#f0fdf4;color:#15803d;border-radius:10px;padding:3px 8px;font-size:12px;font-weight:700;"
        )
        header_lay.addWidget(self.lbl_pending_recent_count)
        header_lay.addStretch()

        layout.addLayout(header_lay)
        self.lbl_learning_copy_hint = QLabel("")
        self.lbl_learning_copy_hint.setStyleSheet("color:#0f766e;font-size:12px;font-weight:600;")
        layout.addWidget(self.lbl_learning_copy_hint)
        self.lbl_learning_digest = QLabel("学习总览：等待读取知识库状态。")
        self.lbl_learning_digest.setWordWrap(True)
        self.lbl_learning_digest.setStyleSheet(
            "background:#f8fafc;color:#334155;border:1px solid #e2e8f0;"
            "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:600;line-height:1.55;"
        )
        layout.addWidget(self.lbl_learning_digest)
        self.lbl_learning_health = QLabel("学习链健康：等待读取反思样本状态。")
        self.lbl_learning_health.setWordWrap(True)
        self.lbl_learning_health.setStyleSheet(
            "background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;"
            "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:600;line-height:1.55;"
        )
        layout.addWidget(self.lbl_learning_health)
        self.lbl_recent_learning_rules = QLabel("最近24小时新增规则：等待读取。")
        self.lbl_recent_learning_rules.setWordWrap(True)
        self.lbl_recent_learning_rules.setStyleSheet(
            "background:#ffffff;color:#475569;border:1px dashed #cbd5e1;"
            "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:500;line-height:1.55;"
        )
        layout.addWidget(self.lbl_recent_learning_rules)
        self.lbl_strategy_param_state = QLabel("策略参数：等待读取。")
        self.lbl_strategy_param_state.setWordWrap(True)
        self.lbl_strategy_param_state.setStyleSheet(
            "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;"
            "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:600;line-height:1.55;"
        )
        layout.addWidget(self.lbl_strategy_param_state)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels([
            "ID", "生成时间", "大类", "品种", "待审状态", "规则内容", "操作"
        ])
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)

        layout.addWidget(self.table)
        self.lbl_pending_empty_state = QLabel("")
        self.lbl_pending_empty_state.setWordWrap(True)
        self.lbl_pending_empty_state.setAlignment(Qt.AlignCenter)
        self.lbl_pending_empty_state.setStyleSheet(
            "background:#f8fafc;color:#475569;border:1px dashed #cbd5e1;"
            "border-radius:10px;padding:14px;font-size:13px;font-weight:600;"
        )
        layout.addWidget(self.lbl_pending_empty_state)
        self.load_pending_rules()

    def load_pending_rules(self):
        self.btn_refresh.setEnabled(False)
        self.btn_refresh.setText("⟳ 刷新中...")
        self.lbl_pending_status.setText("正在读取待审规则...")
        self._start_pending_rules_worker(self._run_load_pending_rules)

    def _start_pending_rules_worker(self, worker) -> None:
        threading.Thread(target=worker, daemon=True, name="pending-rules-load").start()

    def _run_load_pending_rules(self) -> None:
        import sqlite3
        from knowledge_base import open_knowledge_connection, KNOWLEDGE_DB_FILE
        try:
            strategy_sync_error = ""
            try:
                sync_strategy_learning_reviews(db_path=KNOWLEDGE_DB_FILE, days=7, limit=5)
            except Exception as sync_exc:  # noqa: BLE001
                strategy_sync_error = str(sync_exc) or "未知错误"
            with open_knowledge_connection(KNOWLEDGE_DB_FILE) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT
                        kr.id,
                        kr.created_at,
                        kr.category,
                        kr.asset_scope,
                        kr.rule_text,
                        ks.source_type,
                        rg.rationale,
                        rg.governance_status,
                        COALESCE(rs.validation_status, '') AS validation_status
                    FROM rule_governance rg
                    JOIN knowledge_rules kr ON kr.id = rg.rule_id
                    JOIN knowledge_sources ks ON ks.id = kr.source_id
                    LEFT JOIN rule_scores rs
                        ON rs.rule_id = rg.rule_id
                       AND rs.horizon_min = rg.horizon_min
                    WHERE rg.horizon_min = ?
                      AND rg.governance_status = 'manual_review'
                      AND (
                            kr.category IN ('entry', 'trend', 'directional')
                            OR ks.source_type = 'strategy_learning'
                          )
                    ORDER BY
                        kr.id DESC
                    """,
                    (int(self.REVIEW_HORIZON_MIN),),
                ).fetchall()
                count_rows = conn.execute(
                    """
                    SELECT
                        SUM(
                            CASE
                                WHEN COALESCE(rg.governance_status, '') = 'manual_review'
                                 AND (
                                      kr.category IN ('entry', 'trend', 'directional')
                                      OR ks.source_type = 'strategy_learning'
                                     )
                                THEN 1 ELSE 0
                            END
                        ) AS manual_review_count,
                        SUM(CASE WHEN COALESCE(rg.governance_status, '') = 'pending' THEN 1 ELSE 0 END) AS pending_count,
                        SUM(CASE WHEN COALESCE(rg.governance_status, '') = 'archived' THEN 1 ELSE 0 END) AS archived_count,
                        SUM(CASE WHEN COALESCE(rg.governance_status, '') = 'active' THEN 1 ELSE 0 END) AS active_count,
                        SUM(CASE WHEN COALESCE(rg.governance_status, '') = 'watch' THEN 1 ELSE 0 END) AS watch_count,
                        SUM(CASE WHEN COALESCE(rg.governance_status, '') = 'frozen' THEN 1 ELSE 0 END) AS frozen_count,
                        SUM(
                            CASE
                                WHEN COALESCE(rg.governance_status, '') = 'reference'
                                  OR COALESCE(rs.validation_status, '') = 'reference'
                                  OR ks.source_type = 'local_markdown'
                                THEN 1 ELSE 0
                            END
                        ) AS reference_count
                    FROM knowledge_rules kr
                    JOIN knowledge_sources ks ON ks.id = kr.source_id
                    LEFT JOIN rule_governance rg
                        ON rg.rule_id = kr.id
                       AND rg.horizon_min = ?
                    LEFT JOIN rule_scores rs
                        ON rs.rule_id = kr.id
                       AND rs.horizon_min = ?
                    """,
                    (int(self.REVIEW_HORIZON_MIN), int(self.REVIEW_HORIZON_MIN)),
                ).fetchall()
                recent_rows = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total_new_24h,
                        SUM(
                            CASE
                                WHEN ks.source_type IN ('auto_miner', 'llm_cluster_loss', 'llm_golden_setup', 'strategy_learning')
                                THEN 1 ELSE 0
                            END
                        ) AS auto_learn_new_24h,
                        SUM(CASE WHEN ks.location = 'auto_miner_v2_llm_fallback_30m' THEN 1 ELSE 0 END) AS fallback_30m_new_24h,
                        SUM(CASE WHEN ks.location = 'auto_miner_v2_llm_sim' THEN 1 ELSE 0 END) AS sim_reflection_new_24h,
                        SUM(CASE WHEN ks.source_type = 'strategy_learning' THEN 1 ELSE 0 END) AS strategy_learning_new_24h,
                        SUM(CASE WHEN ks.source_type = 'auto_miner' THEN 1 ELSE 0 END) AS frequent_pattern_new_24h
                    FROM knowledge_rules kr
                    JOIN knowledge_sources ks ON ks.id = kr.source_id
                    WHERE datetime(kr.created_at) >= datetime('now', '-1 day')
                    """
                ).fetchall()
                latest_rows = conn.execute(
                    """
                    SELECT
                        kr.id,
                        kr.created_at,
                        ks.source_type,
                        ks.location,
                        kr.category,
                        kr.asset_scope,
                        kr.rule_text
                    FROM knowledge_rules kr
                    JOIN knowledge_sources ks ON ks.id = kr.source_id
                    ORDER BY kr.id DESC
                    LIMIT 1
                    """
                ).fetchall()
                recent_rule_rows = conn.execute(
                    """
                    SELECT
                        kr.id,
                        kr.created_at,
                        ks.source_type,
                        ks.location,
                        kr.category,
                        kr.asset_scope,
                        kr.rule_text
                    FROM knowledge_rules kr
                    JOIN knowledge_sources ks ON ks.id = kr.source_id
                    WHERE datetime(kr.created_at) >= datetime('now', '-1 day')
                    ORDER BY kr.id DESC
                    LIMIT 5
                    """
                ).fetchall()
                health_rows = conn.execute(
                    """
                    SELECT
                        (
                            SELECT COUNT(*)
                            FROM snapshot_outcomes so
                            WHERE so.horizon_min = 888
                              AND COALESCE(so.is_clustered, 0) = 0
                        ) AS usable_888_count,
                        (
                            SELECT COUNT(*)
                            FROM snapshot_outcomes so
                            JOIN market_snapshots ms ON ms.id = so.snapshot_id
                            WHERE so.horizon_min = 30
                              AND COALESCE(so.is_clustered, 0) = 0
                              AND so.outcome_label IN ('success', 'fail')
                              AND ms.trade_grade = '可轻仓试仓'
                              AND ms.trade_grade_source IN ('structure', 'setup')
                              AND ms.signal_side IN ('long', 'short')
                        ) AS usable_30m_exec_count
                    """
                ).fetchall()
                deep_mining_rows = conn.execute(
                    """
                    SELECT
                        summary_text,
                        payload_json,
                        created_at
                    FROM learning_reports
                    WHERE report_type = 'deep_mining_status'
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchall()
            count_row = count_rows[0] if count_rows else None
            recent_row = recent_rows[0] if recent_rows else None
            latest_row = latest_rows[0] if latest_rows else None
            health_row = health_rows[0] if health_rows else None
            deep_mining_row = deep_mining_rows[0] if deep_mining_rows else None
            reflection_new_24h = (
                int(_row_value(recent_row, "fallback_30m_new_24h", 0) or 0)
                + int(_row_value(recent_row, "sim_reflection_new_24h", 0) or 0)
            )
            deep_payload = {}
            if deep_mining_row:
                try:
                    deep_payload = json.loads(str(_row_value(deep_mining_row, "payload_json", "{}") or "{}"))
                except json.JSONDecodeError:
                    deep_payload = {}
            if not isinstance(deep_payload, dict):
                deep_payload = {}
            self.pending_rules_loaded.emit(
                {
                    "ok": True,
                    "rows": [dict(row) for row in rows],
                    "counts": {
                        "manual_review": int(_row_value(count_row, "manual_review_count", 0) or 0),
                        "pending": int(_row_value(count_row, "pending_count", 0) or 0),
                        "archived": int(_row_value(count_row, "archived_count", 0) or 0),
                        "active": int(_row_value(count_row, "active_count", 0) or 0),
                        "watch": int(_row_value(count_row, "watch_count", 0) or 0),
                        "frozen": int(_row_value(count_row, "frozen_count", 0) or 0),
                        "reference": int(_row_value(count_row, "reference_count", 0) or 0),
                    },
                    "recent_stats": {
                        "total_new_24h": int(_row_value(recent_row, "total_new_24h", 0) or 0),
                        "auto_learn_new_24h": int(_row_value(recent_row, "auto_learn_new_24h", 0) or 0),
                        "fallback_30m_new_24h": int(_row_value(recent_row, "fallback_30m_new_24h", 0) or 0),
                        "sim_reflection_new_24h": int(_row_value(recent_row, "sim_reflection_new_24h", 0) or 0),
                        "strategy_learning_new_24h": int(_row_value(recent_row, "strategy_learning_new_24h", 0) or 0),
                        "frequent_pattern_new_24h": int(_row_value(recent_row, "frequent_pattern_new_24h", 0) or 0),
                    },
                    "health_stats": {
                        "usable_888_count": int(_row_value(health_row, "usable_888_count", 0) or 0),
                        "usable_30m_exec_count": int(_row_value(health_row, "usable_30m_exec_count", 0) or 0),
                        "reflection_new_24h": int(reflection_new_24h),
                        "last_deep_mining_at": str(_row_value(deep_mining_row, "created_at", "") or "").strip(),
                        "last_deep_mining_summary": str(_row_value(deep_mining_row, "summary_text", "") or "").strip(),
                        "last_deep_mining_ok": bool(deep_payload.get("ok", False)),
                        "last_deep_mining_total_inserted": int(deep_payload.get("total_inserted_rules", 0) or 0),
                        "last_deep_mining_local_inserted": int(deep_payload.get("local_inserted_rules", 0) or 0),
                        "last_deep_mining_llm_inserted": int(deep_payload.get("llm_inserted_rules", 0) or 0),
                        "last_llm_raw_candidate_count": int(deep_payload.get("llm_raw_candidate_count", 0) or 0),
                        "last_llm_prepared_candidate_count": int(deep_payload.get("llm_prepared_candidate_count", 0) or 0),
                        "last_llm_quality_filtered_count": int(deep_payload.get("llm_quality_filtered_count", 0) or 0),
                        "last_llm_duplicate_skipped_count": int(deep_payload.get("llm_duplicate_skipped_count", 0) or 0),
                        "last_llm_duplicate_in_batch_count": int(deep_payload.get("llm_duplicate_in_batch_count", 0) or 0),
                        "last_llm_duplicate_existing_count": int(deep_payload.get("llm_duplicate_existing_count", 0) or 0),
                        "last_reflection_horizon": int(deep_payload.get("reflection_horizon", 0) or 0),
                        "last_deep_mining_error": str(deep_payload.get("error", "") or "").strip(),
                    },
                    "latest_rule": dict(latest_row) if latest_row else {},
                    "recent_rules": [dict(row) for row in recent_rule_rows],
                    "strategy_sync_error": strategy_sync_error,
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.pending_rules_loaded.emit(
                {
                    "ok": False,
                    "rows": [],
                    "counts": {
                        "manual_review": 0,
                        "pending": 0,
                        "archived": 0,
                        "active": 0,
                        "watch": 0,
                        "frozen": 0,
                        "reference": 0,
                    },
                    "recent_stats": {
                        "total_new_24h": 0,
                        "auto_learn_new_24h": 0,
                        "fallback_30m_new_24h": 0,
                        "sim_reflection_new_24h": 0,
                        "strategy_learning_new_24h": 0,
                        "frequent_pattern_new_24h": 0,
                    },
                    "health_stats": {
                        "usable_888_count": 0,
                        "usable_30m_exec_count": 0,
                        "reflection_new_24h": 0,
                        "last_deep_mining_at": "",
                        "last_deep_mining_summary": "",
                        "last_deep_mining_ok": False,
                        "last_deep_mining_total_inserted": 0,
                        "last_deep_mining_local_inserted": 0,
                        "last_deep_mining_llm_inserted": 0,
                        "last_llm_raw_candidate_count": 0,
                        "last_llm_prepared_candidate_count": 0,
                        "last_llm_quality_filtered_count": 0,
                        "last_llm_duplicate_skipped_count": 0,
                        "last_llm_duplicate_in_batch_count": 0,
                        "last_llm_duplicate_existing_count": 0,
                        "last_reflection_horizon": 0,
                        "last_deep_mining_error": "",
                    },
                    "latest_rule": {},
                    "recent_rules": [],
                    "strategy_sync_error": "",
                    "error": str(exc) or "未知错误",
                }
            )

    def _format_learning_digest(self, counts: dict, recent_stats: dict, latest_rule: dict) -> str:
        active_count = int(counts.get("active", 0) or 0)
        watch_count = int(counts.get("watch", 0) or 0)
        frozen_count = int(counts.get("frozen", 0) or 0)
        pending_count = int(counts.get("pending", 0) or 0)
        reference_count = int(counts.get("reference", 0) or 0)
        total_new_24h = int(recent_stats.get("total_new_24h", 0) or 0)
        auto_learn_new_24h = int(recent_stats.get("auto_learn_new_24h", 0) or 0)
        fallback_30m_new_24h = int(recent_stats.get("fallback_30m_new_24h", 0) or 0)
        sim_reflection_new_24h = int(recent_stats.get("sim_reflection_new_24h", 0) or 0)
        strategy_learning_new_24h = int(recent_stats.get("strategy_learning_new_24h", 0) or 0)
        frequent_pattern_new_24h = int(recent_stats.get("frequent_pattern_new_24h", 0) or 0)

        latest_text = "最近没有新的规则写入。"
        if latest_rule:
            source_type = str(latest_rule.get("source_type", "") or "").strip() or "--"
            created_at = str(latest_rule.get("created_at", "") or "").strip()
            created_short = created_at[5:16] if len(created_at) >= 16 else (created_at or "--")
            rule_text = str(latest_rule.get("rule_text", "") or "").strip() or "规则内容为空"
            latest_text = f"最近一条：{created_short} [{source_type}] {rule_text}"

        return (
            "学习总览："
            f"自动赛道 启用 {active_count} 条 / 观察 {watch_count} 条 / 冻结 {frozen_count} 条 / 待积累 {pending_count} 条；"
            f"基础参考 {reference_count} 条。\n"
            f"最近24小时新增 {total_new_24h} 条，其中自动学习 {auto_learn_new_24h} 条，"
            f"本地频繁模式 {frequent_pattern_new_24h} 条，30m 轻量反思 {fallback_30m_new_24h} 条，"
            f"888 模拟盘反思 {sim_reflection_new_24h} 条，策略待审 {strategy_learning_new_24h} 条。\n"
            f"{latest_text}"
        )

    def _format_recent_learning_rules(self, recent_rules: list[dict]) -> str:
        rows = list(recent_rules or [])
        if not rows:
            return "最近24小时新增规则：暂无新增。"
        lines = ["最近24小时新增规则："]
        for row in rows[:5]:
            created_at = str(row.get("created_at", "") or "").strip()
            created_short = created_at[5:16] if len(created_at) >= 16 else (created_at or "--")
            source_type = str(row.get("source_type", "") or "").strip() or "--"
            category = str(row.get("category", "") or "").strip() or "general"
            rule_text = str(row.get("rule_text", "") or "").strip() or "规则内容为空"
            lines.append(f"• {created_short} [{source_type}/{category}] {rule_text}")
        return "\n".join(lines)

    def _build_learning_share_text(
        self,
        counts: dict,
        recent_stats: dict,
        health_stats: dict,
        latest_rule: dict,
        recent_rules: list[dict],
    ) -> str:
        active_count = int(counts.get("active", 0) or 0)
        watch_count = int(counts.get("watch", 0) or 0)
        frozen_count = int(counts.get("frozen", 0) or 0)
        pending_count = int(counts.get("pending", 0) or 0)
        total_new_24h = int(recent_stats.get("total_new_24h", 0) or 0)
        usable_888_count = int(health_stats.get("usable_888_count", 0) or 0)
        usable_30m_exec_count = int(health_stats.get("usable_30m_exec_count", 0) or 0)
        reflection_new_24h = int(health_stats.get("reflection_new_24h", 0) or 0)
        last_raw_candidate_count = int(health_stats.get("last_llm_raw_candidate_count", 0) or 0)
        last_quality_filtered = int(health_stats.get("last_llm_quality_filtered_count", 0) or 0)
        last_duplicate_skipped = int(health_stats.get("last_llm_duplicate_skipped_count", 0) or 0)
        last_llm_inserted = int(health_stats.get("last_deep_mining_llm_inserted", 0) or 0)
        last_local_inserted = int(health_stats.get("last_deep_mining_local_inserted", 0) or 0)
        last_run_at = str(health_stats.get("last_deep_mining_at", "") or "").strip()
        last_run_short = last_run_at[5:16] if len(last_run_at) >= 16 else (last_run_at or "--")

        if usable_888_count <= 0 and usable_30m_exec_count <= 0:
            status_line = "学习状态：样本不足，先继续积累。"
        elif reflection_new_24h > 0 or last_llm_inserted > 0 or last_local_inserted > 0:
            status_line = "学习状态：链路正常，最近已有有效产出。"
        elif last_quality_filtered > last_duplicate_skipped:
            status_line = "学习状态：主要卡在质量闸门。"
        elif last_duplicate_skipped > 0:
            status_line = "学习状态：主要卡在去重拦截。"
        else:
            status_line = "学习状态：链路可运行，但最近产出偏少。"

        latest_text = "最近规则：暂无新增。"
        if latest_rule:
            source_type = str(latest_rule.get("source_type", "") or "").strip() or "--"
            rule_text = str(latest_rule.get("rule_text", "") or "").strip() or "规则内容为空"
            latest_text = f"最近规则：[{source_type}] {rule_text}"

        newest_focus = "最近24h：暂无新增规则。"
        recent_rows = list(recent_rules or [])
        if recent_rows:
            row = dict(recent_rows[0] or {})
            source_type = str(row.get("source_type", "") or "").strip() or "--"
            category = str(row.get("category", "") or "").strip() or "general"
            rule_text = str(row.get("rule_text", "") or "").strip() or "规则内容为空"
            newest_focus = f"最近24h重点：[{source_type}/{category}] {rule_text}"

        return "\n".join(
            [
                "自动学习摘要",
                f"规则池：启用 {active_count} / 观察 {watch_count} / 冻结 {frozen_count} / 待积累 {pending_count}",
                f"样本池：888待反思 {usable_888_count} / 30m可执行 {usable_30m_exec_count}",
                f"近24h：新增 {total_new_24h} 条，深度反思新增 {reflection_new_24h} 条",
                f"最近深挖：{last_run_short} 本地新增 {last_local_inserted} / 深度反思新增 {last_llm_inserted}",
                f"学习漏斗：原始 {last_raw_candidate_count} -> 质量后 {max(last_raw_candidate_count - last_quality_filtered, 0)} -> 去重后 {max(int(health_stats.get('last_llm_prepared_candidate_count', 0) or 0) - last_duplicate_skipped, 0)} -> 入库 {last_llm_inserted}",
                f"拦截明细：质量闸门 {last_quality_filtered} / 去重 {last_duplicate_skipped}",
                status_line,
                latest_text,
                newest_focus,
            ]
        )

    def _build_strategy_param_state_payload(self, extra_message: str = "") -> tuple[str, str]:
        try:
            summary_text, tooltip = _build_strategy_rr_summary(separator=" | ")
            base_text = "策略参数：" + summary_text
        except Exception:
            base_text = "策略参数：当前 RR 配置读取失败。"
            tooltip = base_text
        clean_extra = str(extra_message or "").strip()
        if clean_extra:
            return base_text + f"\n最近应用：{clean_extra}", tooltip + f"\n最近应用：{clean_extra}"
        return base_text, tooltip

    def _format_strategy_param_state(self, extra_message: str = "") -> str:
        return self._build_strategy_param_state_payload(extra_message)[0]

    def _copy_learning_summary(self) -> None:
        payload = dict(self._last_learning_snapshot or {})
        summary_text = self._build_learning_share_text(
            dict(payload.get("counts", {}) or {}),
            dict(payload.get("recent_stats", {}) or {}),
            dict(payload.get("health_stats", {}) or {}),
            dict(payload.get("latest_rule", {}) or {}),
            list(payload.get("recent_rules", []) or []),
        )
        QApplication.clipboard().setText(summary_text)
        self.lbl_learning_copy_hint.setText("学习摘要已复制到剪贴板。")
        QTimer.singleShot(2500, lambda: self.lbl_learning_copy_hint.setText(""))

    def _learning_health_style(self, health_stats: dict) -> str:
        usable_888_count = int(health_stats.get("usable_888_count", 0) or 0)
        usable_30m_exec_count = int(health_stats.get("usable_30m_exec_count", 0) or 0)
        reflection_new_24h = int(health_stats.get("reflection_new_24h", 0) or 0)
        last_run_at = str(health_stats.get("last_deep_mining_at", "") or "").strip()
        last_run_ok = bool(health_stats.get("last_deep_mining_ok", False))
        last_total_inserted = int(health_stats.get("last_deep_mining_total_inserted", 0) or 0)
        last_quality_filtered = int(health_stats.get("last_llm_quality_filtered_count", 0) or 0)
        last_duplicate_skipped = int(health_stats.get("last_llm_duplicate_skipped_count", 0) or 0)

        # 异常结束优先红色，避免被其他状态掩盖。
        if last_run_at and not last_run_ok:
            return (
                "background:#fef2f2;color:#b91c1c;border:1px solid #fecaca;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        # 有真实产出时优先绿色，代表学习链正在正常工作。
        if reflection_new_24h > 0 or last_total_inserted > 0:
            return (
                "background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        # 样本不足或尚未开始，用冷色提示“先积累样本”。
        if not last_run_at or (usable_888_count <= 0 and usable_30m_exec_count <= 0):
            return (
                "background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        # 质量闸门 / 去重为主要拦截原因时，用不同强调色区分。
        if last_quality_filtered > 0 and last_duplicate_skipped <= 0:
            return (
                "background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        if last_duplicate_skipped > 0 and last_quality_filtered <= 0:
            return (
                "background:#faf5ff;color:#7c3aed;border:1px solid #ddd6fe;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        if last_quality_filtered >= last_duplicate_skipped:
            return (
                "background:#fff7ed;color:#9a3412;border:1px solid #fed7aa;"
                "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
            )
        return (
            "background:#faf5ff;color:#7c3aed;border:1px solid #ddd6fe;"
            "border-radius:10px;padding:10px 12px;font-size:12px;font-weight:700;line-height:1.55;"
        )

    def _format_learning_health(self, health_stats: dict) -> str:
        usable_888_count = int(health_stats.get("usable_888_count", 0) or 0)
        usable_30m_exec_count = int(health_stats.get("usable_30m_exec_count", 0) or 0)
        reflection_new_24h = int(health_stats.get("reflection_new_24h", 0) or 0)
        last_run_at = str(health_stats.get("last_deep_mining_at", "") or "").strip()
        last_run_short = last_run_at[5:16] if len(last_run_at) >= 16 else (last_run_at or "--")
        last_run_ok = bool(health_stats.get("last_deep_mining_ok", False))
        last_total_inserted = int(health_stats.get("last_deep_mining_total_inserted", 0) or 0)
        last_local_inserted = int(health_stats.get("last_deep_mining_local_inserted", 0) or 0)
        last_llm_inserted = int(health_stats.get("last_deep_mining_llm_inserted", 0) or 0)
        last_raw_candidate_count = int(health_stats.get("last_llm_raw_candidate_count", 0) or 0)
        last_prepared_candidate_count = int(health_stats.get("last_llm_prepared_candidate_count", 0) or 0)
        last_quality_filtered = int(health_stats.get("last_llm_quality_filtered_count", 0) or 0)
        last_duplicate_skipped = int(health_stats.get("last_llm_duplicate_skipped_count", 0) or 0)
        last_duplicate_in_batch = int(health_stats.get("last_llm_duplicate_in_batch_count", 0) or 0)
        last_duplicate_existing = int(health_stats.get("last_llm_duplicate_existing_count", 0) or 0)
        last_reflection_horizon = int(health_stats.get("last_reflection_horizon", 0) or 0)
        last_error = str(health_stats.get("last_deep_mining_error", "") or "").strip()

        if not last_run_at:
            last_run_text = "最近还没有深度挖掘运行记录。"
        elif not last_run_ok:
            last_run_text = f"最近一次深挖 {last_run_short} 异常结束：{last_error or '未知错误'}。"
        else:
            horizon_text = f" / LLM反思 h{last_reflection_horizon}" if last_reflection_horizon > 0 else ""
            last_run_text = (
                f"最近一次深挖 {last_run_short}：本地新增 {last_local_inserted} 条，"
                f"深度反思新增 {last_llm_inserted} 条，共 {last_total_inserted} 条{horizon_text}。"
            )

        funnel_text = ""
        if last_run_at and (last_raw_candidate_count > 0 or last_prepared_candidate_count > 0 or last_llm_inserted > 0):
            after_quality_count = max(last_raw_candidate_count - last_quality_filtered, 0)
            after_dedup_count = max(last_prepared_candidate_count - last_duplicate_skipped, 0)
            funnel_text = (
                "上次学习漏斗："
                f"原始候选 {last_raw_candidate_count} 条"
                f" -> 质量过滤后 {after_quality_count} 条"
                f" -> 去重后 {after_dedup_count} 条"
                f" -> 最终入库 {last_llm_inserted} 条。"
            )

        block_detail_parts = []
        if last_quality_filtered > 0:
            block_detail_parts.append(f"质量闸门拦下 {last_quality_filtered} 条")
        if last_duplicate_skipped > 0:
            block_detail_parts.append(
                f"去重拦下 {last_duplicate_skipped} 条（批内重复 {last_duplicate_in_batch}，库内已存在 {last_duplicate_existing}）"
            )
        block_detail_text = ""
        if block_detail_parts:
            block_detail_text = "上次未入库明细：" + "，".join(block_detail_parts) + "。"

        if usable_888_count <= 0 and usable_30m_exec_count <= 0:
            diagnosis_text = "当前更像是样本不足，反思线程暂时没有新原料。"
        elif last_run_ok and last_total_inserted <= 0 and last_quality_filtered > 0 and last_duplicate_skipped <= 0:
            diagnosis_text = "当前已有待消化样本，但最近一次深挖产出 0 条，主要是质量闸门在拦截。"
        elif last_run_ok and last_total_inserted <= 0 and last_duplicate_skipped > 0 and last_quality_filtered <= 0:
            diagnosis_text = "当前已有待消化样本，但最近一次深挖产出 0 条，主要是去重机制阻止了重复入库。"
        elif last_run_ok and last_total_inserted <= 0:
            diagnosis_text = "当前已有待消化样本，但最近一次深挖产出 0 条，更像是质量闸门、去重或规则筛选在共同生效。"
        elif reflection_new_24h > 0 or last_total_inserted > 0:
            diagnosis_text = "当前学习链仍在正常工作，最近阶段已有有效产出。"
        else:
            diagnosis_text = "当前学习链可运行，但最近产出偏少，建议继续观察样本积累。"

        return (
            "学习链健康："
            f"888 待反思样本 {usable_888_count} 条，"
            f"30m 可执行样本 {usable_30m_exec_count} 条。"
            f"最近24小时深度反思新增 {reflection_new_24h} 条规则。"
            f"{last_run_text}"
            f"{funnel_text}"
            f"{block_detail_text}"
            f"{diagnosis_text}"
        )

    def _on_pending_rules_loaded(self, payload: dict) -> None:
        rows = list(payload.get("rows", []) or [])
        counts = dict(payload.get("counts", {}) or {})
        recent_stats = dict(payload.get("recent_stats", {}) or {})
        health_stats = dict(payload.get("health_stats", {}) or {})
        latest_rule = dict(payload.get("latest_rule", {}) or {})
        recent_rules = list(payload.get("recent_rules", []) or [])
        self._last_learning_snapshot = {
            "counts": dict(counts),
            "recent_stats": dict(recent_stats),
            "health_stats": dict(health_stats),
            "latest_rule": dict(latest_rule),
            "recent_rules": list(recent_rules),
        }
        self.btn_refresh.setEnabled(True)
        self.btn_refresh.setText("⟳ 刷新待审列表")
        if bool(payload.get("ok", False)):
            self.lbl_pending_status.setText(f"当前待审 {len(rows)} 条")
        else:
            self.lbl_pending_status.setText(f"读取失败：{str(payload.get('error', '') or '未知错误')}")
        self.lbl_pending_review_count.setText(f"人工复核 {int(counts.get('manual_review', 0) or 0)}")
        self.lbl_pending_accumulate_count.setText(f"待积累 {int(counts.get('pending', 0) or 0)}")
        self.lbl_pending_archived_count.setText(f"自动归档 {int(counts.get('archived', 0) or 0)}")
        self.lbl_pending_active_count.setText(f"启用 {int(counts.get('active', 0) or 0)}")
        self.lbl_pending_frozen_count.setText(f"冻结 {int(counts.get('frozen', 0) or 0)}")
        self.lbl_pending_reference_count.setText(f"基础参考 {int(counts.get('reference', 0) or 0)}")
        self.lbl_pending_recent_count.setText(f"24h新增 {int(recent_stats.get('total_new_24h', 0) or 0)}")
        self.lbl_learning_digest.setText(self._format_learning_digest(counts, recent_stats, latest_rule))
        self.lbl_learning_health.setStyleSheet(self._learning_health_style(health_stats))
        self.lbl_learning_health.setText(self._format_learning_health(health_stats))
        self.lbl_recent_learning_rules.setText(self._format_recent_learning_rules(recent_rules))
        strategy_state_text, strategy_state_tooltip = self._build_strategy_param_state_payload(self._last_strategy_apply_message)
        self.lbl_strategy_param_state.setText(strategy_state_text)
        self.lbl_strategy_param_state.setToolTip(strategy_state_tooltip)
        if not rows and bool(payload.get("ok", False)):
            self.lbl_pending_empty_state.setText(
                "当前没有需要人工审核的规则。\n"
                f"系统已自动归档 {int(counts.get('archived', 0) or 0)} 条非执行类内容，"
                f"{int(counts.get('pending', 0) or 0)} 条规则正在等待样本积累。\n"
                "你无需手动处理，系统会继续自动学习。"
            )
            self.lbl_pending_empty_state.show()
        elif not rows:
            self.lbl_pending_empty_state.setText("待审列表暂时不可用，请稍后刷新。")
            self.lbl_pending_empty_state.show()
        else:
            self.lbl_pending_empty_state.hide()
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            r_id = row["id"]

            self.table.setItem(i, 0, QTableWidgetItem(str(r_id)))
            self.table.setItem(i, 1, QTableWidgetItem(str(row["created_at"])[:16]))
            self.table.setItem(i, 2, QTableWidgetItem(str(row["category"])))
            self.table.setItem(i, 3, QTableWidgetItem(str(row["asset_scope"])))

            governance_status = str(row.get("governance_status", "") or "").strip().lower()
            validation_status = str(row.get("validation_status", "") or "").strip().lower()
            if governance_status == "manual_review":
                review_text = "人工复核"
                review_color = "#7c3aed"
            elif governance_status == "pending":
                if validation_status == "insufficient":
                    review_text = "待积累样本"
                else:
                    review_text = "待审核"
                review_color = "#d97706"
            else:
                review_text = governance_status or "--"
                review_color = "#0284c7"
            review_item = QTableWidgetItem(review_text)
            review_item.setForeground(QColor(review_color))
            self.table.setItem(i, 4, review_item)

            rule_text = str(row["rule_text"])
            rationale = str(row.get("rationale", "") or "").strip()
            source_type = str(row.get("source_type", "") or "").strip()
            display_rule_text = rule_text
            if source_type == "strategy_learning" and rationale:
                display_rule_text = f"{rule_text}\n{rationale}"
            rule_item = QTableWidgetItem(display_rule_text)
            if rationale:
                rule_item.setToolTip(rationale)
            self.table.setItem(i, 5, rule_item)

            action_widget = QWidget()
            action_lay = QHBoxLayout(action_widget)
            action_lay.setContentsMargins(4, 2, 4, 2)
            action_lay.setSpacing(4)

            btn_sandbox = QPushButton("📝 沙盘推演")
            btn_sandbox.setStyleSheet("background:#e0f2fe;color:#0369a1;border:none;border-radius:4px;padding:4px 8px;")
            btn_sandbox.clicked.connect(lambda checked, rid=r_id: self.open_sandbox_editor(rid))

            btn_approve = QPushButton("✅ 批准")
            btn_approve.setStyleSheet("background:#dcfce7;color:#166534;border:none;border-radius:4px;padding:4px 8px;")
            btn_approve.clicked.connect(lambda checked, rid=r_id: self.update_rule_status(rid, "active"))

            btn_reject = QPushButton("❌ 驳回")
            btn_reject.setStyleSheet("background:#fee2e2;color:#b91c1c;border:none;border-radius:4px;padding:4px 8px;")
            btn_reject.clicked.connect(lambda checked, rid=r_id: self.update_rule_status(rid, "frozen"))

            action_lay.addWidget(btn_sandbox)
            action_lay.addWidget(btn_approve)
            action_lay.addWidget(btn_reject)

            self.table.setCellWidget(i, 6, action_widget)

    def open_sandbox_editor(self, rule_id: int):
        from ui_logic_editor import RuleLogicEditorDialog
        dialog = RuleLogicEditorDialog(rule_id, self)
        # 覆写或保存后，重新加载待审列表
        dialog.saved.connect(self.load_pending_rules)
        dialog.exec_()

    def update_rule_status(self, rule_id: int, new_status: str):
        self.lbl_pending_status.setText(f"正在更新规则 #{rule_id} ...")
        self._start_pending_rules_worker(lambda: self._run_update_rule_status(rule_id, new_status))

    def _run_update_rule_status(self, rule_id: int, new_status: str) -> None:
        from knowledge_base import open_knowledge_connection, KNOWLEDGE_DB_FILE
        from datetime import datetime

        try:
            governance_status = str(new_status or "").strip().lower()
            apply_result = {"applied": False}
            if governance_status == "active":
                apply_result = apply_strategy_learning_review(rule_id, approved=True, db_path=KNOWLEDGE_DB_FILE)
            with open_knowledge_connection(KNOWLEDGE_DB_FILE) as conn:
                now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                rationale = "人工在待审面板中批准。"
                validation_status = "validated"
                if governance_status == "frozen":
                    rationale = "人工在待审面板中驳回。"
                    validation_status = "rejected"
                elif bool(apply_result.get("applied", False)):
                    rationale = f"人工在待审面板中批准；{str(apply_result.get('message', '') or '').strip()}。"

                conn.execute(
                    """
                    INSERT INTO rule_governance (rule_id, horizon_min, governance_status, rationale, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(rule_id, horizon_min) DO UPDATE SET
                        governance_status = excluded.governance_status,
                        rationale = excluded.rationale,
                        updated_at = excluded.updated_at
                    """,
                    (
                        int(rule_id),
                        int(self.REVIEW_HORIZON_MIN),
                        governance_status,
                        rationale,
                        now_text,
                    ),
                )
                conn.execute(
                    """
                    UPDATE rule_scores
                    SET validation_status = ?, updated_at = ?
                    WHERE rule_id = ? AND horizon_min = ?
                    """,
                    (
                        validation_status,
                        now_text,
                        int(rule_id),
                        int(self.REVIEW_HORIZON_MIN),
                    ),
                )
                conn.commit()
            self.rule_status_updated.emit(
                {
                    "ok": True,
                    "rule_id": rule_id,
                    "status": governance_status,
                    "apply_message": str(apply_result.get("message", "") or "").strip(),
                    "error": "",
                }
            )
        except Exception as exc:  # noqa: BLE001
            self.rule_status_updated.emit({"ok": False, "rule_id": rule_id, "status": new_status, "error": str(exc) or "未知错误"})

    def _on_rule_status_updated(self, payload: dict) -> None:
        if bool(payload.get("ok", False)):
            status_text = str(payload.get("status", "") or "").strip().lower()
            self._last_strategy_apply_message = str(payload.get("apply_message", "") or "").strip()
            if status_text == "active":
                status_text = "active（已批准）"
            elif status_text == "frozen":
                status_text = "frozen（已驳回）"
            base_text = f"规则 #{int(payload.get('rule_id', 0) or 0)} 已更新为 {status_text}"
            if self._last_strategy_apply_message:
                base_text += f"；{self._last_strategy_apply_message}"
            self.lbl_pending_status.setText(base_text)
            strategy_state_text, strategy_state_tooltip = self._build_strategy_param_state_payload(self._last_strategy_apply_message)
            self.lbl_strategy_param_state.setText(strategy_state_text)
            self.lbl_strategy_param_state.setToolTip(strategy_state_tooltip)
            self.load_pending_rules()
            return
        self.lbl_pending_status.setText(f"更新失败：{str(payload.get('error', '') or '未知错误')}")
