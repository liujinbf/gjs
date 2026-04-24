import copy
import json
import os
import queue
import sqlite3
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget


import style
from alert_history import append_history_entries, build_snapshot_history_entries
from ai_briefing import request_ai_brief
from ai_history import append_ai_history_entry, build_ai_history_entry
from app_config import (
    PROJECT_DIR,
    get_runtime_config,
    get_sim_strategy_cooldown_min,
    get_sim_strategy_daily_limit,
)
from mt5_sim_trading import SIM_ENGINE
from backtest_engine import extract_signal_meta
from event_feed import apply_event_feed_to_snapshot, load_event_feed, merge_event_schedule_texts
from event_schedule import resolve_event_risk_context
from execution_audit import record_execution_audit, resolve_snapshot_binding
from external_signal_context import apply_external_signal_context
from knowledge_feedback import refresh_feedback_push_policy, refresh_rule_feedback_scores, summarize_feedback_stats
from knowledge_governance import build_learning_report, refresh_rule_governance
from learning_closure import backfill_alert_effect_outcomes, backfill_missed_opportunity_samples
from knowledge_ai_signals import record_ai_signal, summarize_recent_ai_signals
from knowledge_ml import (
    annotate_snapshot_with_model,
    apply_model_probability_context,
    train_execution_model,
    train_probability_model,
)
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot, summarize_outcome_stats
from knowledge_scoring import match_rules_to_snapshots, refresh_rule_scores, summarize_rule_scores
from macro_data_feed import apply_macro_data_to_snapshot, load_macro_data_feed
from macro_news_feed import apply_macro_news_to_snapshot, load_macro_news_feed
from monitor_engine import run_monitor_cycle
from mt5_gateway import shutdown_connection
from notification import (
    get_notification_status,
    send_ai_brief_notification,
    send_learning_health_notification,
    send_learning_report_notification,
    send_notifications,
)
from quote_models import SnapshotItem
from settings_dialog import MetalSettingsDialog
from signal_enums import AlertTone, TradeGrade
from sim_signal_bridge import audit_rule_sim_signal_decision, build_rule_sim_signal_decision
from ui_panels import DashboardMetricsPanel, InsightPanel, LeftTabPanel, WatchListTable, PendingRulesPanel

SNAPSHOT_TASK_QUEUE: queue.Queue = queue.Queue(maxsize=100)
BACKGROUND_OUTBOX_DB = PROJECT_DIR / ".runtime" / "background_outbox.sqlite"
BACKGROUND_OUTBOX_MAX_ATTEMPTS = 3
BACKGROUND_OUTBOX_DONE_RETENTION_DAYS = 7
BACKGROUND_OUTBOX_FAILED_RETENTION_DAYS = 30
MACRO_SYNC_INTERVAL_MS = 15 * 60 * 1000
MACRO_SYNC_SLOW_THRESHOLD_MS = 3000
MACRO_SYNC_DEGRADED_STATUSES = {"error", "stale_cache", "cache_missing"}
MACRO_SYNC_BACKOFF_BASE_SEC = 15 * 60
MACRO_SYNC_BACKOFF_MAX_SEC = 2 * 60 * 60


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一 UI 主链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _is_degraded_feed_status(status: str, status_text: str) -> bool:
    clean_status = str(status or "").strip().lower()
    clean_text = str(status_text or "").strip()
    if clean_status in MACRO_SYNC_DEGRADED_STATUSES:
        return True
    return any(keyword in clean_text for keyword in ("拉取失败", "继续使用", "等待后台同步", "尚无可用缓存"))


def _queue_latest_task(task_payload: dict) -> int:
    """将最新后台任务入队；若队列已满则丢弃最旧任务，优先保住最新快照。"""
    try:
        SNAPSHOT_TASK_QUEUE.put(task_payload, block=False)
        return 0
    except queue.Full:
        dropped = 0
        try:
            SNAPSHOT_TASK_QUEUE.get_nowait()
            dropped = 1
        except queue.Empty:
            dropped = 0
        SNAPSHOT_TASK_QUEUE.put(task_payload, block=False)
        return dropped


def _queue_stop_task() -> None:
    """关闭窗口时向后台投递停机信号。"""
    try:
        SNAPSHOT_TASK_QUEUE.put({"kind": "stop"}, block=False)
    except queue.Full:
        try:
            SNAPSHOT_TASK_QUEUE.get_nowait()
        except queue.Empty:
            pass
        SNAPSHOT_TASK_QUEUE.put({"kind": "stop"}, block=False)


def _json_safe_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, SimpleNamespace):
        return dict(vars(value))
    if hasattr(value, "__dict__"):
        return {key: item for key, item in vars(value).items() if not str(key).startswith("_")}
    return str(value)


def _config_to_outbox_payload(config) -> dict:
    if config is None:
        return {}
    if is_dataclass(config):
        return asdict(config)
    if isinstance(config, SimpleNamespace):
        return dict(vars(config))
    if isinstance(config, dict):
        return dict(config)
    if hasattr(config, "__dict__"):
        return {key: value for key, value in vars(config).items() if not str(key).startswith("_")}
    return {}


def _connect_background_outbox(db_path: str | os.PathLike | None = None) -> sqlite3.Connection:
    target = Path(db_path) if db_path else BACKGROUND_OUTBOX_DB
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS background_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_background_outbox_status_id
        ON background_outbox(status, id)
        """
    )
    conn.commit()
    return conn


def _persist_snapshot_side_effect_task(
    snapshot: dict,
    config,
    run_backtest: bool = False,
    db_path: str | os.PathLike | None = None,
) -> int:
    payload = {
        "snapshot": dict(snapshot or {}),
        "config": _config_to_outbox_payload(config),
        "run_backtest": bool(run_backtest),
    }
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect_background_outbox(db_path)
    try:
        cursor = conn.execute(
            """
            INSERT INTO background_outbox (kind, payload_json, status, attempts, created_at, updated_at)
            VALUES (?, ?, 'pending', 0, ?, ?)
            """,
            (
                "snapshot_side_effects",
                json.dumps(payload, ensure_ascii=False, default=_json_safe_default),
                now_text,
                now_text,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)
    finally:
        conn.close()


def _row_to_background_task(row: sqlite3.Row | dict | None) -> dict | None:
    if not row:
        return None
    payload = json.loads(str(row["payload_json"] or "{}"))
    config_payload = dict(payload.get("config", {}) or {})
    return {
        "kind": str(row["kind"] or "").strip(),
        "outbox_id": int(row["id"]),
        "snapshot": dict(payload.get("snapshot", {}) or {}),
        "config": SimpleNamespace(**config_payload),
        "run_backtest": bool(payload.get("run_backtest", False)),
    }


def _claim_background_outbox_task(
    outbox_id: int | None = None,
    db_path: str | os.PathLike | None = None,
) -> dict | None:
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect_background_outbox(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if outbox_id is not None:
            row = conn.execute(
                """
                SELECT * FROM background_outbox
                WHERE id = ? AND status = 'pending'
                LIMIT 1
                """,
                (int(outbox_id),),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM background_outbox
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            conn.commit()
            return None
        conn.execute(
            """
            UPDATE background_outbox
            SET status = 'running',
                attempts = attempts + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (now_text, int(row["id"])),
        )
        conn.commit()
        task = _row_to_background_task(row)
        if task is not None:
            task["attempts"] = int(row["attempts"] or 0) + 1
        return task
    finally:
        conn.close()


def _mark_background_outbox_task(
    outbox_id: int,
    status: str,
    error_text: str = "",
    db_path: str | os.PathLike | None = None,
) -> None:
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    clean_status = str(status or "").strip() or "pending"
    conn = _connect_background_outbox(db_path)
    try:
        conn.execute(
            """
            UPDATE background_outbox
            SET status = ?,
                last_error = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (clean_status, str(error_text or "")[:1000], now_text, int(outbox_id)),
        )
        conn.commit()
    finally:
        conn.close()


def _recover_interrupted_background_outbox_tasks(db_path: str | os.PathLike | None = None) -> int:
    """启动后台线程前，把上次异常退出遗留的 running 任务放回 pending。"""
    now_text = time.strftime("%Y-%m-%d %H:%M:%S")
    conn = _connect_background_outbox(db_path)
    try:
        cursor = conn.execute(
            """
            UPDATE background_outbox
            SET status = 'pending',
                last_error = '',
                updated_at = ?
            WHERE status = 'running'
            """,
            (now_text,),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def _cleanup_background_outbox(
    done_retention_days: int = BACKGROUND_OUTBOX_DONE_RETENTION_DAYS,
    failed_retention_days: int = BACKGROUND_OUTBOX_FAILED_RETENTION_DAYS,
    db_path: str | os.PathLike | None = None,
) -> int:
    """清理过旧 outbox 记录，防止长时间运行后 SQLite 无限增长。"""
    done_days = max(1, int(done_retention_days or BACKGROUND_OUTBOX_DONE_RETENTION_DAYS))
    failed_days = max(1, int(failed_retention_days or BACKGROUND_OUTBOX_FAILED_RETENTION_DAYS))
    conn = _connect_background_outbox(db_path)
    try:
        cursor = conn.execute(
            """
            DELETE FROM background_outbox
            WHERE
                (status = 'done' AND datetime(updated_at) < datetime('now', ?))
                OR
                (status = 'failed' AND datetime(updated_at) < datetime('now', ?))
            """,
            (f"-{done_days} days", f"-{failed_days} days"),
        )
        conn.commit()
        return int(cursor.rowcount or 0)
    finally:
        conn.close()


def _process_background_task(task: dict, db_path: str | os.PathLike | None = None) -> dict | None:
    if str((task or {}).get("kind", "") or "").strip() != "snapshot_side_effects":
        return None
    outbox_id = int((task or {}).get("outbox_id", 0) or 0)
    try:
        result = process_snapshot_side_effects(
            dict(task.get("snapshot", {}) or {}),
            task.get("config"),
            run_backtest=bool(task.get("run_backtest", False)),
        )
        if outbox_id > 0:
            _mark_background_outbox_task(outbox_id, "done", db_path=db_path)
        return result
    except Exception as exc:  # noqa: BLE001
        if outbox_id > 0:
            attempts = int((task or {}).get("attempts", 1) or 1)
            next_status = "failed" if attempts >= BACKGROUND_OUTBOX_MAX_ATTEMPTS else "pending"
            _mark_background_outbox_task(outbox_id, next_status, str(exc), db_path=db_path)
        raise


def _build_execution_funnel_payload(snapshot: dict, ai_state: dict | None = None) -> dict:
    items = [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]
    total_count = len(items)
    live_count = sum(1 for item in items if bool(item.get("has_live_quote", False)))
    structure_count = sum(
        1
        for item in items
        if bool(item.get("has_live_quote", False))
        and str(item.get("trade_grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(item.get("trade_grade_source", "") or "").strip() in {"structure", "setup"}
    )
    rr_ready_count = sum(
        1
        for item in items
        if bool(item.get("has_live_quote", False))
        and str(item.get("trade_grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(item.get("trade_grade_source", "") or "").strip() in {"structure", "setup"}
        and bool(item.get("risk_reward_ready", False))
    )
    direction_ready_count = sum(
        1
        for item in items
        if bool(item.get("has_live_quote", False))
        and str(item.get("trade_grade", "") or "").strip() == TradeGrade.LIGHT_POSITION
        and str(item.get("trade_grade_source", "") or "").strip() in {"structure", "setup"}
        and bool(item.get("risk_reward_ready", False))
        and str(item.get("signal_side", "") or "").strip().lower() in {"long", "short"}
    )
    sim_audit = audit_rule_sim_signal_decision(snapshot)
    sim_ready_count = int(sim_audit.get("ready_count", 0) or 0)
    blocked_summary = list(sim_audit.get("blocked_summary", []) or [])
    top_block = blocked_summary[0] if blocked_summary else {}
    top_block_label = str(top_block.get("reason_label", "") or "").strip() or "暂无明显阻断"
    top_block_count = int(top_block.get("count", 0) or 0)

    ai_state = dict(ai_state or {})
    ai_status_text = str(ai_state.get("status_text", "待命") or "待命").strip()
    ai_action_text = str(ai_state.get("action_text", "观望") or "观望").strip()
    ai_push_text = str(ai_state.get("push_text", "未发生") or "未发生").strip()

    if sim_ready_count > 0:
        tone = AlertTone.SUCCESS.value
        diagnosis = f"当前已有 {sim_ready_count} 个品种满足自动试仓纪律。"
    elif live_count <= 0:
        tone = AlertTone.NEUTRAL.value
        diagnosis = "当前没有活跃报价，执行链路停在 MT5 报价层。"
    elif structure_count <= 0:
        tone = AlertTone.ACCENT.value
        diagnosis = "当前没有结构放行的候选，主要卡在出手分级。"
    elif rr_ready_count <= 0:
        tone = AlertTone.WARNING.value
        diagnosis = "当前已有结构候选，但盈亏比还没准备好。"
    else:
        tone = AlertTone.WARNING.value
        diagnosis = f"当前最主要的执行阻断是「{top_block_label}」"
        if top_block_count > 0:
            diagnosis += f"（{top_block_count} 个品种）"
        diagnosis += "。"

    text = (
        f"执行漏斗：活跃报价 {live_count}/{total_count} -> 结构候选 {structure_count} -> 风控就绪 {rr_ready_count} -> 方向明确 {direction_ready_count} -> 自动试仓就绪 {sim_ready_count}\n"
        f"当前卡点：{diagnosis}\n"
        f"AI链路：{ai_status_text} | 最近方向：{ai_action_text} | 推送：{ai_push_text}"
    )
    return {
        "text": text,
        "tone": tone,
        "total_count": total_count,
        "live_count": live_count,
        "structure_count": structure_count,
        "rr_ready_count": rr_ready_count,
        "direction_ready_count": direction_ready_count,
        "sim_ready_count": sim_ready_count,
        "top_block_label": top_block_label,
        "top_block_count": top_block_count,
        "ai_status_text": ai_status_text,
        "ai_action_text": ai_action_text,
        "ai_push_text": ai_push_text,
    }


def _build_trade_grade_display_text(snapshot: dict, trade_mode: str = "simulation") -> str:
    grade = str(
        snapshot.get("trade_grade", TradeGrade.OBSERVE_ONLY.value)
        or TradeGrade.OBSERVE_ONLY.value
    ).strip()
    detail = str(
        snapshot.get("trade_grade_detail", "先完成一轮快照刷新，再评估当前执行环境。")
        or "先完成一轮快照刷新，再评估当前执行环境。"
    ).strip()
    next_review = str(snapshot.get("trade_next_review", "下一轮轮询后再看。") or "下一轮轮询后再看。").strip()

    allow_exploratory = str(trade_mode or "simulation").strip().lower() != "live"
    execution_line = "自动试仓：等待执行审计。"
    try:
        signal, reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=allow_exploratory)
    except Exception:
        signal, reason = None, ""
    try:
        audit = audit_rule_sim_signal_decision(snapshot, allow_exploratory=allow_exploratory)
    except Exception:
        audit = {}

    if signal:
        symbol = str(signal.get("symbol", "") or "").strip().upper()
        action = str(signal.get("action", "") or "").strip().lower()
        action_text = {"long": "做多", "short": "做空"}.get(action, action or "执行")
        profile = str(signal.get("execution_profile", "") or "").strip().lower()
        profile_text = "探索试仓" if profile == "exploratory" else "规则试仓"
        execution_line = f"自动试仓：已就绪（{profile_text}，{symbol} {action_text}）"
    else:
        blocked_summary = list((audit or {}).get("blocked_summary", []) or [])
        if blocked_summary:
            top = dict(blocked_summary[0] or {})
            label = str(top.get("reason_label", "") or "").strip()
            count = int(top.get("count", 0) or 0)
            if label:
                execution_line = f"自动试仓：未就绪（当前拦截：{label}" + (f" {count}个" if count > 1 else "") + "）"
        elif str(reason or "").strip():
            concise = str(reason or "").replace("\n", " ").strip().split("。")[0]
            execution_line = f"自动试仓：未就绪（{concise}）"

    return f"组合分级：{grade}\n原因：{detail}\n{execution_line}\n下一次复核：{next_review}"


def _load_external_feeds(
    runtime_config,
    symbols: list[str],
    cache_only: bool,
    force_cache_keys: set[str] | None = None,
) -> dict:
    started_at = time.perf_counter()
    bundle = {}
    sync_meta = {
        "cache_only": bool(cache_only),
        "feed_metrics": {},
    }
    forced_keys = {str(key or "").strip() for key in set(force_cache_keys or set()) if str(key or "").strip()}
    feed_loaders = {
        "event_feed": lambda feed_cache_only: load_event_feed(
            enabled=bool(getattr(runtime_config, "event_feed_enabled", False)) if runtime_config else False,
            source=str(getattr(runtime_config, "event_feed_url", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "event_feed_refresh_min", 60) or 60) if runtime_config else 60,
            cache_only=feed_cache_only,
        ),
        "macro_news": lambda feed_cache_only: load_macro_news_feed(
            enabled=bool(getattr(runtime_config, "macro_news_feed_enabled", False)) if runtime_config else False,
            source_text=str(getattr(runtime_config, "macro_news_feed_urls", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "macro_news_feed_refresh_min", 30) or 30) if runtime_config else 30,
            symbols=symbols,
            cache_only=feed_cache_only,
        ),
        "macro_data": lambda feed_cache_only: load_macro_data_feed(
            enabled=bool(getattr(runtime_config, "macro_data_feed_enabled", False)) if runtime_config else False,
            spec_source=str(getattr(runtime_config, "macro_data_feed_specs", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "macro_data_feed_refresh_min", 60) or 60) if runtime_config else 60,
            symbols=symbols,
            cache_only=feed_cache_only,
            env=dict(os.environ),  # 透传环境变量：ALPHAVANTAGE_API_KEY / FRED_API_KEY / BLS_API_KEY
        ),
    }

    for key, loader in feed_loaders.items():
        feed_cache_only = bool(cache_only or key in forced_keys)
        feed_started = time.perf_counter()
        try:
            item = dict(loader(feed_cache_only) or {})
        except Exception as exc:  # noqa: BLE001
            item = {
                "status": "error",
                "status_text": f"{key} 后台同步异常：{str(exc or '未知错误').strip()}",
                "error_text": str(exc or "未知错误").strip(),
                "items": [],
                "summary_text": "",
            }
        if key in forced_keys and not cache_only:
            original_status = str(item.get("status", "") or "").strip().lower()
            original_status_text = str(item.get("status_text", "") or "").strip()
            item["status"] = "backoff_cache"
            if original_status_text:
                item["status_text"] = f"已进入退避窗口，本轮跳过外网拉取，{original_status_text}"
            else:
                item["status_text"] = "已进入退避窗口，本轮跳过外网拉取。"
            item["backoff_applied"] = True
            item["backoff_from_status"] = original_status
        elapsed_ms = int((time.perf_counter() - feed_started) * 1000)
        status = str(item.get("status", "") or "").strip().lower()
        status_text = str(item.get("status_text", "") or "").strip()
        sync_meta["feed_metrics"][key] = {
            "elapsed_ms": elapsed_ms,
            "status": status,
            "status_text": status_text,
            "is_slow": elapsed_ms >= MACRO_SYNC_SLOW_THRESHOLD_MS,
            "is_degraded": _is_degraded_feed_status(status, status_text),
            "used_backoff": key in forced_keys and not cache_only,
        }
        bundle[key] = item

    sync_meta["total_elapsed_ms"] = int((time.perf_counter() - started_at) * 1000)
    sync_meta["slow_feed_keys"] = [
        key for key, metric in dict(sync_meta.get("feed_metrics", {}) or {}).items() if bool(metric.get("is_slow", False))
    ]
    bundle["_sync_meta"] = sync_meta
    return bundle


class MonitorWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, symbols: list[str], parent=None):
        super().__init__(parent)
        self.symbols = list(symbols or [])

    def run(self):
        try:
            runtime_config = getattr(self.parent(), "_config", None)
            feed_bundle = _load_external_feeds(runtime_config, self.symbols, cache_only=True)
            feed_result = dict(feed_bundle.get("event_feed", {}) or {})
            schedule_text = merge_event_schedule_texts(
                str(getattr(runtime_config, "event_schedule_text", "") or "") if runtime_config else "",
                str(feed_result.get("schedule_text", "") or "").strip(),
            )
            event_context = resolve_event_risk_context(
                base_mode=getattr(runtime_config, "event_risk_mode", "normal") if runtime_config else "normal",
                auto_enabled=bool(getattr(runtime_config, "event_auto_mode_enabled", False)) if runtime_config else False,
                schedule_text=schedule_text,
                pre_event_lead_min=int(getattr(runtime_config, "event_pre_window_min", 30) or 30) if runtime_config else 30,
                post_event_window_min=int(getattr(runtime_config, "event_post_window_min", 15) or 15) if runtime_config else 15,
                symbols=self.symbols,
            )
            event_context["feed_status_text"] = str(feed_result.get("status_text", "") or "").strip()
            snapshot = run_monitor_cycle(
                self.symbols,
                event_risk_mode=event_context["mode"],
                event_context=event_context,
            )
            snapshot = apply_event_feed_to_snapshot(snapshot, feed_result)
            macro_news_result = dict(feed_bundle.get("macro_news", {}) or {})
            snapshot = apply_macro_news_to_snapshot(snapshot, macro_news_result)
            macro_data_result = dict(feed_bundle.get("macro_data", {}) or {})
            snapshot = apply_macro_data_to_snapshot(snapshot, macro_data_result)
            snapshot = apply_external_signal_context(snapshot, event_context=event_context)
            # 宏观数据注入完成后，构建状态卡片写回快照
            from monitor_cards import build_macro_data_status_card
            snapshot["macro_data_status_cards"] = build_macro_data_status_card(
                macro_data_status_text=str(snapshot.get("macro_data_status_text", "") or ""),
                macro_data_items=list(snapshot.get("macro_data_items", []) or []),
            )
            self.result_ready.emit(snapshot)
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))


class MacroSyncWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, symbols: list[str], force_cache_keys: set[str] | None = None, parent=None):
        super().__init__(parent)
        self.symbols = list(symbols or [])
        self.force_cache_keys = {str(key or "").strip() for key in set(force_cache_keys or set()) if str(key or "").strip()}

    def run(self):
        try:
            runtime_config = getattr(self.parent(), "_config", None)
            self.result_ready.emit(
                _load_external_feeds(
                    runtime_config,
                    self.symbols,
                    cache_only=False,
                    force_cache_keys=self.force_cache_keys,
                )
            )
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))



class AiBriefWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, snapshot: dict, config, parent=None):
        super().__init__(parent)
        self.snapshot = dict(snapshot or {})
        self.config = config

    def run(self):
        try:
            result = request_ai_brief(self.snapshot, self.config)
            self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001
            import traceback
            tb = traceback.format_exc()
            print("AI BRIEF ERROR: ", tb)
            self.error_signal.emit(str(exc))


def _build_snapshot_live_quotes(snapshot: dict) -> dict:
    result = {}
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        symbol = str(item.get("symbol", "") or "").strip().upper()
        latest_price = float(item.get("latest_price", 0.0) or 0.0)
        bid = float(item.get("bid", 0.0) or 0.0)
        ask = float(item.get("ask", 0.0) or 0.0)
        if not symbol or max(latest_price, bid, ask) <= 0:
            continue
        result[symbol] = {
            "latest": latest_price,
            "bid": bid,
            "ask": ask,
        }
    return result


def _pick_execution_price_from_snapshot_item(item: dict, action: str) -> float:
    bid = float(item.get("bid", 0.0) or 0.0)
    ask = float(item.get("ask", 0.0) or 0.0)
    latest = float(item.get("latest_price", 0.0) or 0.0)
    if action == "long":
        return ask if ask > 0 else latest
    if action == "short":
        return bid if bid > 0 else latest
    return latest


def _resolve_snapshot_action_hint(item: dict) -> str:
    for key in ("signal_side", "risk_reward_direction", "multi_timeframe_bias", "breakout_direction", "intraday_bias"):
        value = str(item.get(key, "") or "").strip().lower()
        if value in {"long", "bullish"}:
            return "long"
        if value in {"short", "bearish"}:
            return "short"
    return "neutral"


def _enrich_signal_with_snapshot_context(meta: dict, snapshot: dict) -> dict:
    payload = dict(meta or {})
    symbol = str(payload.get("symbol", "") or "").strip().upper()
    items = [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]
    if not symbol and len(items) == 1:
        symbol = str(items[0].get("symbol", "") or "").strip().upper()
        if symbol:
            payload["symbol"] = symbol
    if not symbol:
        return payload
    action = str(payload.get("action", "") or "").strip().lower()
    for item in items:
        item_symbol = str(item.get("symbol", "") or "").strip().upper()
        if item_symbol != symbol:
            continue
        snapshot_action = _resolve_snapshot_action_hint(item)
        can_fill_execution_levels = (
            action in {"long", "short"}
            and snapshot_action in {"neutral", action}
            and bool(item.get("risk_reward_ready", False))
        )
        if can_fill_execution_levels:
            if float(payload.get("price", 0.0) or 0.0) <= 0:
                payload["price"] = _pick_execution_price_from_snapshot_item(item, action)
            if float(payload.get("sl", 0.0) or 0.0) <= 0:
                payload["sl"] = float(item.get("risk_reward_stop_price", 0.0) or 0.0)
            if float(payload.get("tp", 0.0) or 0.0) <= 0:
                payload["tp"] = float(item.get("risk_reward_target_price", 0.0) or 0.0)
        if float(payload.get("atr14", 0.0) or 0.0) <= 0:
            payload["atr14"] = float(item.get("atr14", 0.0) or 0.0)
        if float(payload.get("atr14_h4", 0.0) or 0.0) <= 0:
            payload["atr14_h4"] = float(item.get("atr14_h4", 0.0) or 0.0)
        if float(payload.get("risk_reward_atr", 0.0) or 0.0) <= 0:
            payload["risk_reward_atr"] = float(item.get("risk_reward_atr", 0.0) or 0.0)
        if float(payload.get("tp2", 0.0) or 0.0) <= 0:
            payload["tp2"] = float(item.get("risk_reward_target_price_2", 0.0) or 0.0)
        if float(payload.get("volume_step", 0.0) or 0.0) <= 0:
            payload["volume_step"] = float(item.get("volume_step", 0.0) or 0.0)
        if float(payload.get("volume_min", 0.0) or 0.0) <= 0:
            payload["volume_min"] = float(item.get("volume_min", 0.0) or 0.0)
        for key in (
            "trade_grade",
            "trade_grade_source",
            "trade_grade_detail",
            "signal_side",
            "signal_side_text",
            "signal_side_reason",
            "setup_kind",
            "risk_reward_ratio",
            "risk_reward_state",
            "risk_reward_direction",
            "entry_zone_side",
            "entry_zone_side_text",
            "model_win_probability",
            "execution_open_probability",
            "multi_timeframe_alignment",
            "multi_timeframe_bias",
            "intraday_bias",
            "intraday_volatility",
            "key_level_state",
            "breakout_state",
            "retest_state",
            "regime_tag",
            "regime_text",
            "execution_note",
            "event_mode_text",
        ):
            if key not in payload or payload.get(key) in ("", None, 0, 0.0):
                value = item.get(key)
                if value not in ("", None):
                    payload[key] = value
        if "snapshot_time" not in payload or not str(payload.get("snapshot_time", "") or "").strip():
            payload["snapshot_time"] = str((snapshot or {}).get("last_refresh_text", "") or "").strip()
        if "event_risk_mode_text" not in payload or not str(payload.get("event_risk_mode_text", "") or "").strip():
            payload["event_risk_mode_text"] = str(item.get("event_mode_text", "") or str((snapshot or {}).get("event_risk_mode_text", "") or "")).strip()
        return payload
    return payload


def _detect_opportunity(snapshot: dict, rr_threshold: float = 2.0) -> bool:
    """检测当前快照中是否存在高质量出手机会。

    优先使用监控快照里的轻量机会评分；旧快照则回退到盈亏比判断。
    """
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        push_level = str(item.get("opportunity_push_level", "") or "").strip().lower()
        action = str(item.get("opportunity_action", "") or "").strip().lower()
        score = float(item.get("opportunity_score", 0.0) or 0.0)
        if action in {"long", "short"} and (push_level == "push" or score >= 80):
            return True
        if bool(item.get("risk_reward_ready", False)):
            rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
            if rr >= rr_threshold:
                return True
    return False


def _resolve_ai_result_signal_meta(result: dict, content: str = "") -> dict:
    """从 AI 返回结果中提取稳定的机器信号，失败时回到观望。"""
    raw_meta = dict(result.get("signal_meta", {}) or {}) if isinstance(result, dict) else {}
    if not raw_meta:
        parsed_meta = extract_signal_meta(content)
        raw_meta = dict(parsed_meta or {}) if isinstance(parsed_meta, dict) else {}
    if not raw_meta:
        raw_meta = {"action": "neutral"}
    raw_meta["action"] = str(raw_meta.get("action", "neutral") or "neutral").strip().lower()
    return raw_meta


def _pick_snapshot_item_by_symbol(snapshot: dict, symbol: str) -> dict:
    target_symbol = str(symbol or "").strip().upper()
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        if str(item.get("symbol", "") or "").strip().upper() == target_symbol:
            return item
    return {}


def _build_meta_from_snapshot_item(item: dict, action: str = "neutral") -> dict:
    symbol = str(item.get("symbol", "") or "").strip().upper()
    meta = {
        "symbol": symbol,
        "action": str(action or item.get("signal_side", "neutral") or "neutral").strip().lower(),
        "price": float(item.get("latest_price", 0.0) or 0.0),
        "sl": float(item.get("risk_reward_stop_price", 0.0) or 0.0),
        "tp": float(item.get("risk_reward_target_price", 0.0) or 0.0),
    }
    return _enrich_signal_with_snapshot_context(meta, {"items": [item]})


def _attempt_sim_execution(
    *,
    source_kind: str,
    snapshot: dict,
    meta: dict,
    signal_signature: str = "",
    user_id: str = "system",
) -> tuple[bool, str]:
    enriched_meta = _enrich_signal_with_snapshot_context(meta, snapshot)
    if int(enriched_meta.get("snapshot_id", 0) or 0) <= 0:
        snapshot_id = resolve_snapshot_binding(
            snapshot=snapshot,
            symbol=str(enriched_meta.get("symbol", "") or "").strip().upper(),
        )
        if snapshot_id > 0:
            enriched_meta["snapshot_id"] = snapshot_id
    try:
        success, message = SIM_ENGINE.execute_signal(enriched_meta, user_id=user_id)
    except TypeError:
        success, message = SIM_ENGINE.execute_signal(enriched_meta)
    record_execution_audit(
        source_kind=source_kind,
        decision_status="opened" if success else "rejected",
        snapshot=snapshot,
        meta=enriched_meta,
        signal_signature=signal_signature,
        result_message=message,
        trade_mode="simulation",
        user_id=user_id,
    )
    return success, message


def _count_today_exploratory_sim_opens(symbol: str = "", strategy_family: str = "") -> int:
    from datetime import datetime
    from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

    day_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    params: list[object] = [day_start]
    symbol_sql = ""
    clean_symbol = str(symbol or "").strip().upper()
    clean_family = str(strategy_family or "").strip().lower()
    if clean_symbol:
        symbol_sql = " AND symbol = ?"
        params.append(clean_symbol)
    family_sql = ""
    if clean_family:
        family_sql = " AND lower(COALESCE(json_extract(meta_json, '$.strategy_family'), '')) = ?"
        params.append(clean_family)
    with open_knowledge_connection(KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM execution_audits
            WHERE occurred_at >= ?
              AND source_kind = 'rule_engine'
              AND trade_mode = 'simulation'
              AND decision_status = 'opened'
              AND json_extract(meta_json, '$.execution_profile') = 'exploratory'
              {symbol_sql}
              {family_sql}
            """,
            tuple(params),
        ).fetchone()
    return int(row["count"] if row else 0)


def _is_exploratory_signal(meta: dict | None) -> bool:
    return str((meta or {}).get("execution_profile", "") or "").strip().lower() == "exploratory"


def _resolve_exploratory_strategy_family(meta: dict | None = None) -> str:
    payload = dict(meta or {})
    return (
        str(payload.get("strategy_family", "") or "").strip().lower()
        or str(payload.get("setup_kind", "") or "").strip().lower()
        or str(payload.get("trade_grade_source", "") or "").strip().lower()
    )


def _resolve_exploratory_daily_limit(meta: dict | None = None) -> int:
    try:
        config = get_runtime_config()
        family = _resolve_exploratory_strategy_family(meta)
        if family:
            return max(0, min(50, int(get_sim_strategy_daily_limit(family, config=config) or 0)))
        return max(0, min(50, int(getattr(config, "sim_exploratory_daily_limit", 3) or 0)))
    except Exception:
        return 3


def _resolve_exploratory_cooldown_min(meta: dict | None = None) -> int:
    try:
        config = get_runtime_config()
        family = _resolve_exploratory_strategy_family(meta)
        if family:
            return max(0, min(240, int(get_sim_strategy_cooldown_min(family, config=config) or 0)))
        return max(0, min(240, int(getattr(config, "sim_exploratory_cooldown_min", 10) or 0)))
    except Exception:
        return 10


def _exploratory_daily_limit_reached(symbol: str = "", meta: dict | None = None) -> bool:
    limit = _resolve_exploratory_daily_limit(meta)
    if limit <= 0:
        return False
    try:
        return _count_today_exploratory_sim_opens(
            symbol=symbol,
            strategy_family=_resolve_exploratory_strategy_family(meta),
        ) >= limit
    except Exception:
        return False


def _exploratory_cooldown_active(symbol: str = "", action: str = "", meta: dict | None = None) -> bool:
    from datetime import datetime, timedelta
    from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

    cooldown_min = _resolve_exploratory_cooldown_min(meta)
    if cooldown_min <= 0:
        return False
    clean_symbol = str(symbol or "").strip().upper()
    clean_action = str(action or "").strip().lower()
    clean_family = _resolve_exploratory_strategy_family(meta)
    if not clean_symbol or clean_action not in ("long", "short"):
        return False
    cutoff = (datetime.now() - timedelta(minutes=cooldown_min)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open_knowledge_connection(KNOWLEDGE_DB_FILE, ensure_schema=True) as conn:
            params: list[object] = [cutoff, clean_symbol, clean_action]
            family_sql = ""
            if clean_family:
                family_sql = " AND lower(COALESCE(json_extract(meta_json, '$.strategy_family'), '')) = ?"
                params.append(clean_family)
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM execution_audits
                WHERE occurred_at >= ?
                  AND source_kind = 'rule_engine'
                  AND trade_mode = 'simulation'
                  AND decision_status = 'opened'
                  AND symbol = ?
                  AND lower(action) = ?
                  AND json_extract(meta_json, '$.execution_profile') = 'exploratory'
                  {family_sql}
                """,
                tuple(params),
            ).fetchone()
        return int(row["count"] if row else 0) > 0
    except Exception:
        return False


def process_snapshot_side_effects(
    snapshot: dict,
    config,
    run_backtest: bool = False,
) -> dict:
    result = {
        "log_lines": [],
        "notify_status_changed": False,
        "refresh_histories": False,
        "sim_data_changed": False,
        "snapshot_ids": [],
        "snapshot_inserted_count": 0,
        "snapshot_bindings": {},
    }

    try:
        knowledge_result = record_snapshot(snapshot)
        inserted_count = int(knowledge_result.get("inserted_count", 0) or 0)
        result["snapshot_inserted_count"] = inserted_count
        result["snapshot_ids"] = [
            int(item)
            for item in list(knowledge_result.get("inserted_snapshot_ids", []) or [])
            if int(item or 0) > 0
        ]
        result["snapshot_bindings"] = {
            str(symbol or "").strip().upper(): int(snapshot_id)
            for symbol, snapshot_id in dict(knowledge_result.get("snapshot_bindings", {}) or {}).items()
            if str(symbol or "").strip() and int(snapshot_id or 0) > 0
        }
        if inserted_count > 0:
            result["log_lines"].append(f"[知识库] 已写入 {inserted_count} 条市场快照样本。")
    except Exception as exc:  # noqa: BLE001
        result["log_lines"].append(f"[知识库] 快照写入失败：{exc}")

    history_entries = build_snapshot_history_entries(snapshot)
    history_count = append_history_entries(history_entries)
    if history_count:
        result["log_lines"].append(f"[提醒留痕] 新增 {history_count} 条关键提醒。")
        result["refresh_histories"] = True

    notify_result = send_notifications(history_entries, config)
    for line in notify_result.get("messages", []):
        result["log_lines"].append(f"[消息推送] {line}")
    for line in notify_result.get("errors", []):
        result["log_lines"].append(f"[消息推送失败] {line}")
    if notify_result.get("messages") or notify_result.get("errors"):
        result["notify_status_changed"] = True
        result["refresh_histories"] = True

    live_quotes = _build_snapshot_live_quotes(snapshot)
    if live_quotes:
        if getattr(config, "trade_mode", "simulation") == "live":
            from mt5_live_engine import LIVE_ENGINE
            rule_signal, rule_reason = build_rule_sim_signal_decision(snapshot)
            if rule_signal:
                open_symbols = {
                    str(item.get("symbol", "") or "").strip().upper()
                    for item in list(LIVE_ENGINE.get_open_positions() or [])
                    if str(item.get("symbol", "") or "").strip()
                }
                if str(rule_signal.get("symbol", "") or "").strip().upper() not in open_symbols:
                    symbol = str(rule_signal.get("symbol", "") or "").strip().upper()
                    live_success, live_message = LIVE_ENGINE.execute_signal(rule_signal)
                    record_execution_audit(
                        source_kind="rule_engine",
                        decision_status="opened" if live_success else "rejected",
                        snapshot=snapshot,
                        meta=rule_signal,
                        result_message=live_message,
                        trade_mode="live",
                    )
                    if live_success:
                        result["log_lines"].append(
                            f"[实盘交易] 🚀 已按结构候选发射实盘开仓：{rule_signal.get('action')} {rule_signal.get('symbol')}。"
                        )
                    else:
                        result["log_lines"].append(f"[实盘挂单被拒] {live_message}")
                else:
                    symbol = str(rule_signal.get("symbol", "") or "").strip().upper()
                    record_execution_audit(
                        source_kind="rule_engine",
                        decision_status="skipped",
                        snapshot=snapshot,
                        meta=rule_signal,
                        reason_key="existing_position",
                        result_message=f"{symbol} 已有活跃持仓，跳过。",
                        trade_mode="live",
                    )
            elif rule_reason:
                blocked_meta = {}
                current_audit = audit_rule_sim_signal_decision(snapshot)
                blocked_row = next((row for row in list(current_audit.get("rows", []) or []) if not bool(row.get("eligible", False))), None)
                if blocked_row:
                    blocked_item = _pick_snapshot_item_by_symbol(snapshot, str(blocked_row.get("symbol", "") or "").strip().upper())
                    blocked_meta = _build_meta_from_snapshot_item(blocked_item, action=str(blocked_row.get("action", "neutral") or "neutral"))
                record_execution_audit(
                    source_kind="rule_engine",
                    decision_status="blocked",
                    snapshot=snapshot,
                    meta=blocked_meta,
                    reason_key=str(blocked_row.get("reason_key", "") if blocked_row else ""),
                    result_message=rule_reason,
                    trade_mode="live",
                )
                result["log_lines"].append(f"[实盘候选未执行] {rule_reason}")
        else:
            SIM_ENGINE.update_prices(live_quotes)
            result["sim_data_changed"] = True
            open_symbols = {
                str(item.get("symbol", "") or "").strip().upper()
                for item in list(SIM_ENGINE.get_open_positions() or [])
                if str(item.get("symbol", "") or "").strip()
            }
            rule_signal, rule_reason = build_rule_sim_signal_decision(snapshot, allow_exploratory=True)
            if rule_signal and str(rule_signal.get("symbol", "") or "").strip().upper() not in open_symbols:
                symbol = str(rule_signal.get("symbol", "") or "").strip().upper()
                if symbol in result["snapshot_bindings"]:
                    rule_signal["snapshot_id"] = result["snapshot_bindings"][symbol]
                action = str(rule_signal.get("action", "") or "").strip().lower()
                if _is_exploratory_signal(rule_signal) and _exploratory_cooldown_active(symbol, action, meta=rule_signal):
                    cooldown_min = _resolve_exploratory_cooldown_min(rule_signal)
                    strategy_family = _resolve_exploratory_strategy_family(rule_signal)
                    strategy_hint = f"{strategy_family} " if strategy_family else ""
                    record_execution_audit(
                        source_kind="rule_engine",
                        decision_status="blocked",
                        snapshot=snapshot,
                        meta=rule_signal,
                        reason_key="exploratory_cooldown",
                        result_message=(
                            f"{symbol} {action} {strategy_hint}探索试仓仍在 {cooldown_min} 分钟同向冷却内，"
                            "本轮只记录机会，不重复试错。"
                        ),
                        trade_mode="simulation",
                    )
                    result["log_lines"].append(
                        f"[模拟盘探索试仓冷却] {symbol} {action} {strategy_hint}仍在 {cooldown_min} 分钟同向冷却内。"
                    )
                elif _is_exploratory_signal(rule_signal) and _exploratory_daily_limit_reached(symbol, meta=rule_signal):
                    exploratory_limit = _resolve_exploratory_daily_limit(rule_signal)
                    strategy_family = _resolve_exploratory_strategy_family(rule_signal)
                    strategy_hint = f"{strategy_family} " if strategy_family else ""
                    record_execution_audit(
                        source_kind="rule_engine",
                        decision_status="blocked",
                        snapshot=snapshot,
                        meta=rule_signal,
                        reason_key="exploratory_daily_limit",
                        result_message=(
                            f"{symbol} 今日{strategy_hint}探索试仓已达到 {exploratory_limit} 次上限，"
                            "本轮只记录机会，不继续加仓试错。"
                        ),
                        trade_mode="simulation",
                    )
                    result["log_lines"].append(
                        f"[模拟盘探索试仓暂停] {symbol} 今日{strategy_hint}已达到 {exploratory_limit} 次上限。"
                    )
                else:
                    sim_success, sim_message = _attempt_sim_execution(
                        source_kind="rule_engine",
                        snapshot=snapshot,
                        meta=rule_signal,
                    )
                    if sim_success:
                        result["sim_data_changed"] = True
                        profile_text = "探索试仓" if _is_exploratory_signal(rule_signal) else "结构候选"
                        result["log_lines"].append(
                            f"[模拟盘规则跟单] 已按{profile_text}开仓：{rule_signal.get('action')} {rule_signal.get('symbol')}。"
                        )
                    else:
                        result["log_lines"].append(f"[模拟盘规则跟单被拒] {sim_message}")
            elif rule_signal:
                record_execution_audit(
                    source_kind="rule_engine",
                    decision_status="skipped",
                    snapshot=snapshot,
                    meta=rule_signal,
                    reason_key="existing_position",
                    result_message=f"{str(rule_signal.get('symbol', '') or '').strip().upper()} 已有活跃持仓，跳过。",
                    trade_mode="simulation",
                )
            elif rule_reason:
                blocked_meta = {}
                current_audit = audit_rule_sim_signal_decision(snapshot, allow_exploratory=True)
                blocked_row = next((row for row in list(current_audit.get("rows", []) or []) if not bool(row.get("eligible", False))), None)
                if blocked_row:
                    blocked_item = _pick_snapshot_item_by_symbol(snapshot, str(blocked_row.get("symbol", "") or "").strip().upper())
                    blocked_meta = _build_meta_from_snapshot_item(blocked_item, action=str(blocked_row.get("action", "neutral") or "neutral"))
                record_execution_audit(
                    source_kind="rule_engine",
                    decision_status="blocked",
                    snapshot=snapshot,
                    meta=blocked_meta,
                    reason_key=str(blocked_row.get("reason_key", "") if blocked_row else ""),
                    result_message=rule_reason,
                    trade_mode="simulation",
                )
                result["log_lines"].append(f"[模拟盘规则候选未执行] {rule_reason}")

    if run_backtest:
        try:
            from backtest_engine import run_backtest_evaluations

            run_backtest_evaluations()
        except Exception as exc:  # noqa: BLE001
            result["log_lines"].append(f"[回测引擎] 评估失败（非致命）：{exc}")
    return result


def run_knowledge_maintenance(config, snapshot_ids: list[int] | None = None) -> dict:
    result = {
        "log_lines": [],
        "notify_status_changed": False,
    }
    snapshot_ids = [int(item) for item in list(snapshot_ids or []) if int(item or 0) > 0]
    match_result = match_rules_to_snapshots(snapshot_ids=snapshot_ids or None)
    if int(match_result.get("matched_count", 0) or 0) > 0:
        result["log_lines"].append(f"[知识库] 已新增 {match_result.get('matched_count', 0)} 条规则-样本映射。")
    outcome_result = backfill_snapshot_outcomes()
    alert_effect_result = backfill_alert_effect_outcomes(horizon_min=30)
    missed_result = backfill_missed_opportunity_samples(horizon_min=30)
    if (
        int(outcome_result.get("labeled_count", 0) or 0) <= 0
        and int(alert_effect_result.get("inserted_count", 0) or 0) <= 0
        and int(missed_result.get("inserted_count", 0) or 0) <= 0
        and not result["log_lines"]
    ):
        return result

    stats_30m = summarize_outcome_stats(horizon_min=30)
    refresh_rule_scores(horizon_min=30)
    refresh_rule_feedback_scores()
    feedback_policy = refresh_feedback_push_policy(days=30)
    refresh_rule_governance(horizon_min=30)
    ml_result = train_probability_model(horizon_min=30)
    execution_ml_result = train_execution_model(horizon_min=888)
    # [UI Thread Stability] 深度挖掘已被抽离至独立的 run_deep_mining 和 DeepMinerWorker，避免阻塞常规知识库同步。
    rule_summary = summarize_rule_scores(horizon_min=30)
    learning_report = build_learning_report(horizon_min=30, persist=True)
    feedback_summary = summarize_feedback_stats(days=30)
    result["log_lines"].append(
        f"[知识库] 已新增 {outcome_result.get('labeled_count', 0)} 条结果回标。{stats_30m.get('summary_text', '')}"
    )
    if int(alert_effect_result.get("inserted_count", 0) or 0) > 0:
        result["log_lines"].append(
            f"[提醒学习] 已新增 {alert_effect_result.get('inserted_count', 0)} 条推送后效果回标。"
        )
    if int(missed_result.get("inserted_count", 0) or 0) > 0:
        result["log_lines"].append(
            f"[漏机会学习] 已沉淀 {missed_result.get('inserted_count', 0)} 条漏机会样本。"
        )
    result["log_lines"].append(f"[知识库] {rule_summary.get('summary_text', '')}")
    if str(ml_result.get("status", "") or "") == "trained":
        result["log_lines"].append(
            f"[本地模型] 已训练 {ml_result.get('model_name', 'naive-edge-v1')}，"
            f"样本 {ml_result.get('sample_count', 0)} 条，基础胜率 {float(ml_result.get('base_win_probability', 0.0) or 0.0) * 100:.0f}%。"
        )
    else:
        result["log_lines"].append(
            f"[本地模型] 样本仍不足，当前仅有 {ml_result.get('sample_count', 0)} 条有效样本。"
        )
    if str(execution_ml_result.get("status", "") or "") == "trained":
        result["log_lines"].append(
            f"[执行模型] 已训练 {execution_ml_result.get('model_name', 'execution-readiness-v1')}，"
            f"样本 {execution_ml_result.get('sample_count', 0)} 条，基础就绪度 {float(execution_ml_result.get('base_win_probability', 0.0) or 0.0) * 100:.0f}%。"
        )
    else:
        result["log_lines"].append(
            f"[执行模型] 样本仍不足，当前仅有 {execution_ml_result.get('sample_count', 0)} 条执行留痕。"
        )
    if int(feedback_summary.get("total_count", 0) or 0) > 0:
        result["log_lines"].append(f"[知识库] {feedback_summary.get('summary_text', '')}")
    if bool(feedback_policy.get("active", False)):
        actions = []
        if feedback_policy.get("advance_warning"):
            actions.append("提前接近位置提醒")
        if feedback_policy.get("reduce_noise"):
            actions.append("提高普通推送门槛")
        if feedback_policy.get("tighten_risk"):
            actions.append("收紧风险推送")
        if actions:
            result["log_lines"].append(f"[推送学习] 已根据用户反馈调整提醒策略：{' / '.join(actions)}。")
    result["log_lines"].append(f"[知识库] 学习摘要：{learning_report.get('summary_text', '')}")
    learning_push_result = send_learning_report_notification(learning_report, config)
    for line in learning_push_result.get("messages", []):
        result["log_lines"].append(f"[学习推送] {line}")
    for line in learning_push_result.get("errors", []):
        result["log_lines"].append(f"[学习推送失败] {line}")
    result["notify_status_changed"] = bool(
        learning_push_result.get("messages") or learning_push_result.get("errors")
    )
    return result


class KnowledgeSyncWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, config, snapshot_ids: list[int] | None = None, parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(config)
        self.snapshot_ids = list(snapshot_ids or [])

    def run(self):
        try:
            self.result_ready.emit(run_knowledge_maintenance(self.config, snapshot_ids=self.snapshot_ids))
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))


def run_deep_mining(config) -> dict:
    result = {
        "log_lines": [],
        "ok": True,
        "error": "",
        "local_mined_patterns": 0,
        "local_inserted_rules": 0,
        "llm_mined_patterns": 0,
        "llm_inserted_rules": 0,
        "llm_raw_candidate_count": 0,
        "llm_prepared_candidate_count": 0,
        "llm_quality_filtered_count": 0,
        "llm_duplicate_skipped_count": 0,
        "llm_duplicate_in_batch_count": 0,
        "llm_duplicate_existing_count": 0,
        "reflection_horizon": 0,
        "total_inserted_rules": 0,
    }
    try:
        from knowledge_miner import mine_frequent_patterns, run_llm_batch_reflection

        # 1. 纯本地特征提取 (纯本地计算，不耗时太久)
        miner_result = mine_frequent_patterns()
        mined_patterns = int(miner_result.get("mined_patterns", 0) or 0)
        inserted_rules = int(miner_result.get("inserted_rules", 0) or 0)
        result["local_mined_patterns"] = mined_patterns
        result["local_inserted_rules"] = inserted_rules
        if mined_patterns > 0:
            result["log_lines"].append(
                f"[深度挖掘] 发现 {mined_patterns} 种高胜率组合，新录入 {inserted_rules} 条候选规则。"
            )

        # 2. 聚类批处理与大模型高级挖掘 (Batch Clustering Reflection)
        # LLM网络请求较长，抽离后不堵塞其他常规 KnowledgeSync
        llm_miner_result = run_llm_batch_reflection(db_path=None, config=config)
        llm_mined_patterns = int(llm_miner_result.get("mined_patterns", 0) or 0)
        llm_inserted_rules = int(llm_miner_result.get("inserted_rules", 0) or 0)
        reflection_horizon = int(llm_miner_result.get("reflection_horizon", 0) or 0)
        result["llm_mined_patterns"] = llm_mined_patterns
        result["llm_inserted_rules"] = llm_inserted_rules
        result["reflection_horizon"] = reflection_horizon
        result["llm_raw_candidate_count"] = int(llm_miner_result.get("raw_candidate_count", 0) or 0)
        result["llm_prepared_candidate_count"] = int(llm_miner_result.get("prepared_candidate_count", 0) or 0)
        result["llm_quality_filtered_count"] = int(llm_miner_result.get("quality_filtered_count", 0) or 0)
        result["llm_duplicate_skipped_count"] = int(llm_miner_result.get("duplicate_skipped_count", 0) or 0)
        result["llm_duplicate_in_batch_count"] = int(llm_miner_result.get("duplicate_in_batch_count", 0) or 0)
        result["llm_duplicate_existing_count"] = int(llm_miner_result.get("duplicate_existing_count", 0) or 0)
        if llm_mined_patterns > 0:
            result["log_lines"].append(
                f"[深入聚类挖掘] 模型成功回放并提炼出 {llm_mined_patterns} 组深层规则，新录入 {llm_inserted_rules} 条。"
            )
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
        result["log_lines"].append(f"[深度挖掘] 异常（非致命）：{exc}")
    result["total_inserted_rules"] = int(result.get("local_inserted_rules", 0) or 0) + int(result.get("llm_inserted_rules", 0) or 0)
    _persist_deep_mining_report(result)
    learning_health_report = _build_learning_health_report(result)
    learning_health_push_result = send_learning_health_notification(learning_health_report, config)
    for line in learning_health_push_result.get("messages", []):
        result["log_lines"].append(f"[学习状态推送] {line}")
    for line in learning_health_push_result.get("errors", []):
        result["log_lines"].append(f"[学习状态推送] {line}")
    return result


def _persist_deep_mining_report(result: dict) -> None:
    try:
        from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

        created_at = time.strftime("%Y-%m-%d %H:%M:%S")
        local_inserted = int(result.get("local_inserted_rules", 0) or 0)
        llm_inserted = int(result.get("llm_inserted_rules", 0) or 0)
        total_inserted = int(result.get("total_inserted_rules", 0) or 0)
        reflection_horizon = int(result.get("reflection_horizon", 0) or 0)
        ok = bool(result.get("ok", False))
        error_text = str(result.get("error", "") or "").strip()
        if ok:
            summary_text = (
                f"最近一次深度挖掘于 {created_at} 完成，"
                f"本地新增 {local_inserted} 条，深度反思新增 {llm_inserted} 条，共 {total_inserted} 条。"
            )
        else:
            summary_text = f"最近一次深度挖掘于 {created_at} 异常结束：{error_text or '未知错误'}"

        payload = {
            "ok": ok,
            "error": error_text,
            "local_mined_patterns": int(result.get("local_mined_patterns", 0) or 0),
            "local_inserted_rules": local_inserted,
            "llm_mined_patterns": int(result.get("llm_mined_patterns", 0) or 0),
            "llm_inserted_rules": llm_inserted,
            "llm_raw_candidate_count": int(result.get("llm_raw_candidate_count", 0) or 0),
            "llm_prepared_candidate_count": int(result.get("llm_prepared_candidate_count", 0) or 0),
            "llm_quality_filtered_count": int(result.get("llm_quality_filtered_count", 0) or 0),
            "llm_duplicate_skipped_count": int(result.get("llm_duplicate_skipped_count", 0) or 0),
            "llm_duplicate_in_batch_count": int(result.get("llm_duplicate_in_batch_count", 0) or 0),
            "llm_duplicate_existing_count": int(result.get("llm_duplicate_existing_count", 0) or 0),
            "reflection_horizon": reflection_horizon,
            "total_inserted_rules": total_inserted,
            "log_lines": list(result.get("log_lines", []) or []),
        }
        with open_knowledge_connection(KNOWLEDGE_DB_FILE) as conn:
            conn.execute(
                """
                INSERT INTO learning_reports (report_type, horizon_min, summary_text, payload_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "deep_mining_status",
                    reflection_horizon if reflection_horizon > 0 else 30,
                    summary_text,
                    json.dumps(payload, ensure_ascii=False),
                    created_at,
                ),
            )
    except Exception:
        return


def _build_learning_health_report(result: dict) -> dict:
    from knowledge_base import KNOWLEDGE_DB_FILE, open_knowledge_connection

    report = {
        "occurred_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status_key": "sample_wait",
        "status_text": "样本积累中",
        "summary_text": "自动学习当前没有新的可反思样本，继续等待样本积累。",
        "latest_rule_text": "",
        "tone": AlertTone.ACCENT.value,
    }
    try:
        with open_knowledge_connection(KNOWLEDGE_DB_FILE) as conn:
            recent_row = conn.execute(
                """
                SELECT COUNT(*) AS total_new_24h
                FROM knowledge_rules
                WHERE datetime(created_at) >= datetime('now', '-1 day')
                """
            ).fetchone()
            latest_rule_row = conn.execute(
                """
                SELECT kr.rule_text, ks.source_type
                FROM knowledge_rules kr
                JOIN knowledge_sources ks ON ks.id = kr.source_id
                ORDER BY kr.id DESC
                LIMIT 1
                """
            ).fetchone()
            health_row = conn.execute(
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
            ).fetchone()
        recent_new_24h = int((recent_row or {}).get("total_new_24h", 0) if isinstance(recent_row, dict) else (recent_row["total_new_24h"] if recent_row else 0))
        usable_888_count = int((health_row or {}).get("usable_888_count", 0) if isinstance(health_row, dict) else (health_row["usable_888_count"] if health_row else 0))
        usable_30m_exec_count = int((health_row or {}).get("usable_30m_exec_count", 0) if isinstance(health_row, dict) else (health_row["usable_30m_exec_count"] if health_row else 0))
        latest_rule_text = ""
        if latest_rule_row:
            source_type = str((latest_rule_row or {}).get("source_type", "") if isinstance(latest_rule_row, dict) else latest_rule_row["source_type"]).strip()
            rule_text = str((latest_rule_row or {}).get("rule_text", "") if isinstance(latest_rule_row, dict) else latest_rule_row["rule_text"]).strip()
            latest_rule_text = f"[{source_type}] {rule_text}".strip() if rule_text else ""
        report["latest_rule_text"] = latest_rule_text
    except Exception:
        recent_new_24h = 0
        usable_888_count = 0
        usable_30m_exec_count = 0

    total_inserted = int(result.get("total_inserted_rules", 0) or 0)
    quality_filtered = int(result.get("llm_quality_filtered_count", 0) or 0)
    duplicate_skipped = int(result.get("llm_duplicate_skipped_count", 0) or 0)

    if not bool(result.get("ok", False)):
        report.update(
            {
                "status_key": "deep_mining_error",
                "status_text": "深挖异常",
                "summary_text": f"自动学习深度挖掘异常结束：{str(result.get('error', '') or '未知错误').strip()}",
                "tone": AlertTone.WARNING.value,
            }
        )
    elif recent_new_24h <= 0:
        report.update(
            {
                "status_key": "stalled_24h",
                "status_text": "24h无新增",
                "summary_text": (
                    f"自动学习最近24小时没有新增规则；当前样本池 888={usable_888_count}，30m={usable_30m_exec_count}。"
                ),
                "tone": AlertTone.WARNING.value,
            }
        )
    elif total_inserted > 0:
        report.update(
            {
                "status_key": "productive",
                "status_text": "恢复产出",
                "summary_text": (
                    f"自动学习已恢复产出：本轮本地新增 {int(result.get('local_inserted_rules', 0) or 0)} 条，"
                    f"深度反思新增 {int(result.get('llm_inserted_rules', 0) or 0)} 条。"
                ),
                "tone": AlertTone.SUCCESS.value,
            }
        )
    elif usable_888_count <= 0 and usable_30m_exec_count <= 0:
        report.update(
            {
                "status_key": "sample_wait",
                "status_text": "样本积累中",
                "summary_text": "自动学习当前没有新的可反思样本，继续等待样本积累。",
                "tone": AlertTone.ACCENT.value,
            }
        )
    elif quality_filtered > duplicate_skipped:
        report.update(
            {
                "status_key": "quality_gate",
                "status_text": "质量闸门拦截",
                "summary_text": (
                    f"自动学习本轮未新增规则，主要被质量闸门拦下 {quality_filtered} 条候选。"
                ),
                "tone": AlertTone.WARNING.value,
            }
        )
    elif duplicate_skipped > 0:
        report.update(
            {
                "status_key": "dedup_blocked",
                "status_text": "去重拦截",
                "summary_text": (
                    f"自动学习本轮未新增规则，主要因去重机制拦下 {duplicate_skipped} 条候选。"
                ),
                "tone": AlertTone.ACCENT.value,
            }
        )
    else:
        report.update(
            {
                "status_key": "low_yield",
                "status_text": "产出偏少",
                "summary_text": (
                    f"自动学习当前可运行，但本轮暂未新增规则；样本池 888={usable_888_count}，30m={usable_30m_exec_count}。"
                ),
                "tone": AlertTone.ACCENT.value,
            }
        )
    return report


class DeepMinerWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = copy.deepcopy(config)

    def run(self):
        try:
            self.result_ready.emit(run_deep_mining(self.config))
        except Exception as exc:  # noqa: BLE001
            self.error_signal.emit(str(exc))


class BackgroundTaskWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def run(self):
        while True:
            try:
                try:
                    task = SNAPSHOT_TASK_QUEUE.get(timeout=1.0)
                except queue.Empty:
                    task = _claim_background_outbox_task()
                    if task is None:
                        continue
                if not isinstance(task, dict):
                    continue
                if str(task.get("kind", "") or "").strip() == "stop":
                    return
                if str(task.get("kind", "") or "").strip() == "outbox_snapshot_side_effects":
                    task = _claim_background_outbox_task(int(task.get("outbox_id", 0) or 0))
                    if task is None:
                        continue
                result = _process_background_task(task)
                if result is not None:
                    self.result_ready.emit(result)
            except Exception as exc:  # noqa: BLE001
                self.error_signal.emit(str(exc))


class MetalMonitorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("贵金属监控终端")
        self.resize(1220, 820)
        self._config = get_runtime_config()
        self._worker = None
        self._ai_worker = None
        self._knowledge_worker = None
        self._snapshot_task_worker = None
        self._macro_worker = None  # MacroSyncWorker 实例
        self._knowledge_sync_pending = False
        self._pending_knowledge_snapshot_ids = set()
        self._polling_enabled = True
        self._last_snapshot = {}
        self._last_ai_funnel_state = {
            "status_text": "待命",
            "action_text": "观望",
            "push_text": "未发生",
            "tone": AlertTone.NEUTRAL.value,
        }
        self._last_ai_auto_time = None  # 上次自动 AI 研判时间
        self._last_knowledge_sync_time = None  # 维护时间戳
        self._ai_auto_is_running = False  # 防止自动触发重叠
        self._last_external_source_warning_digest = ""
        self._last_macro_sync_status_digest = ""
        self._last_macro_sync_perf_digest = ""
        self._macro_sync_refresh_pending = False
        self._macro_source_degraded_counts = {
            "event_feed": 0,
            "macro_news": 0,
            "macro_data": 0,
        }
        self._macro_source_backoff_until = {
            "event_feed": 0.0,
            "macro_news": 0.0,
            "macro_data": 0.0,
        }
        self._build_ui()
        self._start_background_task_worker()
        # 炸弹三修复：保留 _timer 对象（供 closeEvent/toggle_polling 使用），
        # 但不用固定间隔轮询，改为链式 singleShot，由快照回调自己续约。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh_snapshot)
        QTimer.singleShot(120, self.refresh_snapshot)  # 首次延迟 120ms 触发
        # ── MacroSyncWorker 定时器（3.4 修复：宏观数据独立刷新，不堵塞 MT5 报价）──
        self._macro_timer = QTimer(self)
        self._macro_timer.timeout.connect(self._trigger_macro_sync)
        self._macro_timer.start(MACRO_SYNC_INTERVAL_MS)
        QTimer.singleShot(5000, self._trigger_macro_sync)  # 启动后 5 秒首次拉取

        # ── DeepMinerWorker 定时器（防阻塞：独立线程执行大模型挖掘，不堵塞 Knowledge 维护）──
        self._deep_miner_worker = None
        self._deep_miner_timer = QTimer(self)
        self._deep_miner_timer.timeout.connect(self._trigger_deep_mining)
        self._deep_miner_timer.start(60 * 60 * 1000)  # 每 60 分钟触发一次
        QTimer.singleShot(30000, self._trigger_deep_mining)  # 启动后 30 秒首次触发

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 10, 14, 10)
        root_layout.setSpacing(6)

        # ── 顶栏：标题 + 状态徽章 + 按钮（一行） ──
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)
        title = QLabel("贵金属监控终端")
        title.setStyleSheet("font-size:20px;font-weight:800;color:#0f172a;")
        top_bar.addWidget(title)

        self.lbl_status_badge = QLabel("准备中")
        self.lbl_status_badge.setAlignment(Qt.AlignCenter)
        self.lbl_status_badge.setFixedHeight(28)
        self.lbl_status_badge.setStyleSheet(style.STYLE_BADGE_NEUTRAL)
        top_bar.addWidget(self.lbl_status_badge)

        self.lbl_status_hint = QLabel("正在准备连接…")
        self.lbl_status_hint.setStyleSheet("color:#64748b;font-size:12px;")
        top_bar.addWidget(self.lbl_status_hint, 1)

        self.btn_refresh = QPushButton("⟳ 刷新")
        self.btn_poll = QPushButton("⏸ 暂停")
        self.btn_ai = QPushButton("🤖 AI研判")
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_poll.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_ai.setCursor(Qt.PointingHandCursor)
        self.btn_ai.setProperty("type", "primary")

        self.btn_refresh.clicked.connect(self.refresh_snapshot)
        self.btn_poll.clicked.connect(self.toggle_polling)
        self.btn_ai.clicked.connect(self.run_ai_brief)
        self.btn_settings.clicked.connect(self.open_settings)
        top_bar.addWidget(self.btn_refresh)
        top_bar.addWidget(self.btn_poll)
        top_bar.addWidget(self.btn_ai)
        top_bar.addWidget(self.btn_settings)
        root_layout.addLayout(top_bar)

        # ── 紧凑信息行 ──
        self.lbl_notify_status = QLabel("")
        self.lbl_notify_status.setStyleSheet(
            "color:#475569;font-size:11px;background:#f8fafc;"
            "border:1px solid #e2e8f0;border-radius:6px;padding:3px 8px;"
        )
        self.lbl_notify_status.setWordWrap(False)
        root_layout.addWidget(self.lbl_notify_status)

        # ── 主体 Tab ──
        self.main_tabs = QTabWidget()
        self.main_tabs.setStyleSheet(style.STYLE_TAB_WIDGET)
        self.main_tabs.currentChanged.connect(self._on_main_tab_changed)
        self._pending_tab_updates: set[str] = set()

        # ── Tab 1：实时监控 ──
        self._build_tab_monitor(self.main_tabs)

        # ── Tab 2：提醒分析 ──
        self._build_tab_analysis(self.main_tabs)

        # ── Tab 3：历史日志 ──
        self._build_tab_history(self.main_tabs)

        # ── Tab 4：模拟战绩 ──
        self._build_tab_sim_trading(self.main_tabs)

        # ── Tab 5：待审规则 (HITL) ──
        self._build_tab_pending_rules(self.main_tabs)

        root_layout.addWidget(self.main_tabs, 1)
        self.setCentralWidget(root)
        self.left_panel.refresh_histories()
        self._update_notify_status()

    def _current_tab_key(self) -> str:
        index = int(getattr(self.main_tabs, "currentIndex", lambda: 0)())
        if index == 1:
            return "analysis"
        if index == 2:
            return "history"
        if index == 3:
            return "sim"
        if index == 4:
            return "pending"
        return "monitor"

    def _refresh_visible_tab_panel(self, tab_key: str) -> None:
        key = str(tab_key or "").strip().lower()
        snapshot = dict(self._last_snapshot or {})
        if key == "analysis":
            self.insight_panel.update_from_snapshot(snapshot)
            return
        if key == "history":
            self.left_panel.update_from_snapshot(snapshot)
            return
        if key == "sim":
            try:
                self.sim_panel.update_data(snapshot=snapshot)
            except Exception:
                pass
            return
        if key == "pending":
            try:
                self.pending_panel.load_pending_rules()
            except Exception:
                pass

    def _mark_tab_update_pending(self, tab_key: str) -> None:
        key = str(tab_key or "").strip().lower()
        if key:
            self._pending_tab_updates.add(key)

    def _on_main_tab_changed(self, index: int) -> None:
        _ = index
        current_key = self._current_tab_key()
        if current_key in self._pending_tab_updates:
            self._pending_tab_updates.discard(current_key)
            self._refresh_visible_tab_panel(current_key)

    def _build_tab_monitor(self, tabs: QTabWidget):
        """Tab1：实时监控 = 指标卡 + 出手分级 + 品种表格"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        # 指标卡 + 出手分级 → 横排一行
        top = QHBoxLayout()
        top.setSpacing(8)
        self.metrics_panel = DashboardMetricsPanel()
        top.addWidget(self.metrics_panel, 3)

        grade_frame = QFrame()
        grade_frame.setStyleSheet(style.STYLE_CARD_CONTAINER)
        grade_lay = QVBoxLayout(grade_frame)
        grade_lay.setContentsMargins(12, 8, 12, 8)
        grade_lay.setSpacing(4)
        self.lbl_trade_grade = QLabel("出手分级待计算，先获取一轮 MT5 快照。")
        self.lbl_trade_grade.setWordWrap(True)
        self.lbl_trade_grade.setStyleSheet(style.STYLE_PANEL_NEUTRAL_BOLD)
        self.lbl_alert_banner = QLabel("")
        self.lbl_alert_banner.setWordWrap(True)
        self.lbl_alert_banner.setStyleSheet(style.STYLE_PANEL_WARNING_BOLD)
        self.lbl_alert_banner.hide()
        self.lbl_ai_status = QLabel("AI 研判待命。")
        self.lbl_ai_status.setWordWrap(True)
        self.lbl_ai_status.setStyleSheet("color:#1d4ed8;font-size:11px;")
        self.lbl_execution_funnel = QLabel("执行漏斗待计算，先刷新一轮快照。")
        self.lbl_execution_funnel.setWordWrap(True)
        self.lbl_execution_funnel.setStyleSheet(
            "background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px;color:#334155;font-size:11px;line-height:1.55;font-weight:600;"
        )
        grade_lay.addWidget(self.lbl_trade_grade)
        grade_lay.addWidget(self.lbl_alert_banner)
        grade_lay.addWidget(self.lbl_ai_status)
        grade_lay.addWidget(self.lbl_execution_funnel)
        top.addWidget(grade_frame, 5)
        lay.addLayout(top)

        # 观察品种表格
        self.right_table = WatchListTable()
        lay.addWidget(self.right_table, 1)

        tabs.addTab(w, "📊 实时监控")

    def _build_tab_analysis(self, tabs: QTabWidget):
        """Tab2：提醒分析 = MT5状态/时段 + 4组分析面板 + AI简报"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        self.insight_panel = InsightPanel()
        lay.addWidget(self.insight_panel)
        tabs.addTab(w, "🔔 提醒分析")

    def _build_tab_history(self, tabs: QTabWidget):
        """Tab3：历史日志 = AI简报 + 提醒留痕 + 底层日志"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        self.left_panel = LeftTabPanel()
        lay.addWidget(self.left_panel, 1)
        tabs.addTab(w, "📋 历史日志")

    def _build_tab_sim_trading(self, tabs: QTabWidget):
        """Tab4：模拟战绩 = 顶部战绩卡 + 左侧实时持仓 + 右侧历史交割"""
        from ui_panels import SimTradingPanel
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        self.sim_panel = SimTradingPanel()
        lay.addWidget(self.sim_panel)
        tabs.addTab(w, "🏆 模拟战绩")

    def _build_tab_pending_rules(self, tabs: QTabWidget):
        """Tab5：待审规则批准台面板 (HITL)"""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 8, 10, 8)
        self.pending_panel = PendingRulesPanel()
        lay.addWidget(self.pending_panel)
        tabs.addTab(w, "🛡️ 规则审批(HITL)")




    def _append_log(self, message: str):
        if hasattr(self, "left_panel"):
            self.left_panel.append_log(message)

    def _set_status_badge(self, text: str, tone: str):
        if getattr(self._config, "trade_mode", "simulation") == "live" and tone == AlertTone.SUCCESS.value:
            text = "🔴 真实量化实盘运转中 (LIVE CAUTION)"
            self.lbl_status_badge.setText(text)
            self.lbl_status_badge.setStyleSheet("background-color:#fee2e2;color:#dc2626;font-weight:bold;padding:4px 8px;border-radius:6px;border:1px solid #fecaca;")
        else:
            self.lbl_status_badge.setText(text)
            self.lbl_status_badge.setStyleSheet(style.BADGE_STYLE_MAP.get(tone, style.STYLE_BADGE_NEUTRAL))

    def _update_notify_status(self, snapshot: dict | None = None):
        notify_status = get_notification_status(self._config)
        current_snapshot = dict(snapshot or self._last_snapshot or {})
        lines = [f"{notify_status.get('channels_text', '')} | {notify_status.get('cooldown_text', '')}"]
        if current_snapshot:
            discipline_text = (
                f"当前纪律：{current_snapshot.get('event_risk_mode_text', '正常观察')}"
                f"（{current_snapshot.get('event_risk_mode_source_text', '手动模式')}）"
            )
            feed_status = str(current_snapshot.get("event_feed_status_text", "") or "").strip()
            next_name = str(current_snapshot.get("event_next_name", "") or "").strip()
            next_time = str(current_snapshot.get("event_next_time_text", "") or "").strip()
            if next_name and next_time:
                discipline_text += f" | 下个事件：{next_name}（{next_time}）"
            if feed_status:
                discipline_text += f" | 事件源：{feed_status}"
            lines.append(
                f"{discipline_text} | 最近推送：{notify_status.get('last_result_text', '')}"
                f"（{notify_status.get('last_result_time', '--')}）"
            )
            source_parts = []
            macro_news_status = str(current_snapshot.get("macro_news_status_text", "") or "").strip()
            macro_data_status = str(current_snapshot.get("macro_data_status_text", "") or "").strip()
            if macro_news_status:
                source_parts.append(f"资讯流：{macro_news_status}")
            if macro_data_status:
                source_parts.append(f"宏观数据：{macro_data_status}")
            if source_parts:
                lines.append(" | ".join(source_parts))
        else:
            lines.append(
                f"最近推送：{notify_status.get('last_result_text', '')}"
                f"（{notify_status.get('last_result_time', '--')}）"
            )
        self.lbl_notify_status.setText("\n".join(lines))

    def _log_external_source_status_changes(self, snapshot: dict):
        warning_lines = []
        for label, key in (
            ("事件源", "event_feed_status_text"),
            ("资讯流", "macro_news_status_text"),
            ("宏观数据", "macro_data_status_text"),
        ):
            text = str(snapshot.get(key, "") or "").strip()
            if not text:
                continue
            if any(keyword in text for keyword in ("拉取失败", "继续使用", "尚未配置", "未解析", "规格为空")):
                warning_lines.append(f"[{label}] {text}")
        digest = "||".join(warning_lines)
        if digest and digest != self._last_external_source_warning_digest:
            for line in warning_lines:
                self._append_log(line)
        self._last_external_source_warning_digest = digest

    def _start_background_task_worker(self):
        if self._snapshot_task_worker and self._snapshot_task_worker.isRunning():
            return
        recovered_count = _recover_interrupted_background_outbox_tasks()
        if recovered_count > 0:
            self._append_log(f"[后台任务] 已恢复 {recovered_count} 条上次未完成的 outbox 任务。")
        cleaned_count = _cleanup_background_outbox()
        if cleaned_count > 0:
            self._append_log(f"[后台任务] 已清理 {cleaned_count} 条过期 outbox 记录。")
        self._snapshot_task_worker = BackgroundTaskWorker(self)
        self._snapshot_task_worker.result_ready.connect(self._on_background_task_ready)
        self._snapshot_task_worker.error_signal.connect(self._on_background_task_error)
        self._snapshot_task_worker.start()

    def _compute_macro_sync_force_cache_keys(self) -> set[str]:
        now_ts = time.time()
        return {
            key
            for key, until_ts in dict(self._macro_source_backoff_until or {}).items()
            if float(until_ts or 0.0) > now_ts
        }

    def _trigger_macro_sync(self, force: bool = False):
        """3.4 修复：触发宏观数据后台刷新，不影响 MT5 报价线程。"""
        if self._macro_worker and self._macro_worker.isRunning():
            return  # 上一次还没跑完，跳过
        force_cache_keys = set() if force else self._compute_macro_sync_force_cache_keys()
        self._macro_worker = MacroSyncWorker(self._config.symbols, force_cache_keys=force_cache_keys, parent=self)
        self._macro_worker.result_ready.connect(self._on_macro_sync_ready)
        self._macro_worker.error_signal.connect(self._on_macro_sync_error)
        self._macro_worker.start()

    def _trigger_deep_mining(self):
        # 隔离耗时的大模型聚类任务与数据库特征分析
        if self._deep_miner_worker and self._deep_miner_worker.isRunning():
            return
        self._deep_miner_worker = DeepMinerWorker(self._config, parent=self)
        self._deep_miner_worker.result_ready.connect(self._on_deep_mining_ready)
        self._deep_miner_worker.error_signal.connect(self._on_deep_mining_error)
        self._deep_miner_worker.start()

    def _on_deep_mining_ready(self, result: dict):
        self._deep_miner_worker = None
        for line in list((result or {}).get("log_lines", []) or []):
            self._append_log(str(line or "").strip())
        if hasattr(self, "pending_panel") and self.pending_panel:
            current_tab_getter = getattr(self, "_current_tab_key", None)
            current_tab = current_tab_getter() if callable(current_tab_getter) else "pending"
            if current_tab == "pending":
                try:
                    self.pending_panel.load_pending_rules()
                except Exception as exc:  # noqa: BLE001
                    self._append_log(f"[深度挖掘] 刷新待审规则列表失败：{exc}")
            elif hasattr(self, "_mark_tab_update_pending"):
                self._mark_tab_update_pending("pending")

    def _on_deep_mining_error(self, message: str):
        self._deep_miner_worker = None
        self._append_log(f"[深度挖掘] 后台挖掘报错：{str(message or '未知错误').strip()}")

    def _on_macro_sync_ready(self, result: dict):
        self._macro_worker = None
        payload = dict(result or {})
        sync_meta = dict(payload.get("_sync_meta", {}) or {})
        status_parts = []
        perf_parts = []
        refresh_needed = False
        for result_key, snapshot_key, label in (
            ("event_feed", "event_feed_status_text", "事件源"),
            ("macro_news", "macro_news_status_text", "资讯流"),
            ("macro_data", "macro_data_status_text", "宏观数据"),
        ):
            item = dict(payload.get(result_key, {}) or {})
            status_text = str(item.get("status_text", "") or "").strip()
            status = str(item.get("status", "") or "").strip().lower()
            metric = dict(sync_meta.get("feed_metrics", {}) or {}).get(result_key, {}) or {}
            elapsed_ms = int(metric.get("elapsed_ms", 0) or 0)
            is_slow = bool(metric.get("is_slow", False))
            is_degraded = bool(metric.get("is_degraded", False))
            if status_text:
                status_parts.append(f"{label}:{status_text}")
                if status not in {"disabled", "missing"} and self._last_snapshot.get(snapshot_key) != status_text:
                    refresh_needed = True
            if elapsed_ms > 0:
                perf_label = f"{label} {elapsed_ms}ms"
                if is_slow:
                    perf_label += "（偏慢）"
                perf_parts.append(perf_label)
            if is_degraded:
                self._macro_source_degraded_counts[result_key] = int(self._macro_source_degraded_counts.get(result_key, 0) or 0) + 1
                degrade_count = int(self._macro_source_degraded_counts.get(result_key, 0) or 0)
                backoff_rounds = max(0, degrade_count - 1)
                if backoff_rounds > 0:
                    backoff_sec = min(MACRO_SYNC_BACKOFF_MAX_SEC, MACRO_SYNC_BACKOFF_BASE_SEC * (2 ** (backoff_rounds - 1)))
                    self._macro_source_backoff_until[result_key] = max(
                        float(self._macro_source_backoff_until.get(result_key, 0.0) or 0.0),
                        time.time() + float(backoff_sec),
                    )
                if degrade_count >= 3:
                    self._append_log(f"[宏观同步] {label} 已连续 {degrade_count} 轮处于降级状态，当前建议继续依赖缓存并关注外部源稳定性。")
            else:
                self._macro_source_degraded_counts[result_key] = 0
                self._macro_source_backoff_until[result_key] = 0.0
        digest = " | ".join(status_parts)
        if digest and digest != self._last_macro_sync_status_digest:
            self._append_log(f"[宏观同步] {digest}")
        self._last_macro_sync_status_digest = digest

        total_elapsed_ms = int(sync_meta.get("total_elapsed_ms", 0) or 0)
        perf_digest = ""
        if perf_parts:
            perf_digest = f"总耗时 {total_elapsed_ms}ms | " + " | ".join(perf_parts)
            if perf_digest != self._last_macro_sync_perf_digest and (
                total_elapsed_ms >= MACRO_SYNC_SLOW_THRESHOLD_MS or any("偏慢" in part for part in perf_parts)
            ):
                self._append_log(f"[宏观同步耗时] {perf_digest}")
        self._last_macro_sync_perf_digest = perf_digest

        if not refresh_needed:
            return
        if self._worker and self._worker.isRunning():
            self._macro_sync_refresh_pending = True
            return
        self.refresh_snapshot()

    def _on_macro_sync_error(self, message: str):
        self._macro_worker = None
        self._append_log(f"[宏观同步] 后台刷新失败（非致命）：{str(message or '').strip()}")


    def _enqueue_snapshot_side_effects(self, snapshot: dict):
        from datetime import datetime as _dt

        run_backtest = False
        _now = _dt.now()
        _last = getattr(self, "_last_backtest_eval_time", None)
        _interval_min = 10
        if _last is None or (_now - _last).total_seconds() >= _interval_min * 60:
            self._last_backtest_eval_time = _now
            run_backtest = True
        outbox_id = _persist_snapshot_side_effect_task(
            dict(snapshot or {}),
            copy.deepcopy(self._config),
            run_backtest=run_backtest,
        )
        dropped_count = _queue_latest_task({"kind": "outbox_snapshot_side_effects", "outbox_id": outbox_id})
        if dropped_count > 0:
            self._append_log(f"[警告] 后台任务堆积，已丢弃 {dropped_count} 条内存唤醒信号；任务已进入本地 outbox 等待补偿执行。")

    def _schedule_knowledge_sync(self, snapshot_ids: list[int] | None = None):
        for snapshot_id in list(snapshot_ids or []):
            if int(snapshot_id or 0) > 0:
                self._pending_knowledge_snapshot_ids.add(int(snapshot_id))
        from datetime import datetime as _dt
        now_dt = _dt.now()
        if self._last_knowledge_sync_time:
            elapsed_sec = (now_dt - self._last_knowledge_sync_time).total_seconds()
            if elapsed_sec < 600:
                self._knowledge_sync_pending = True
                return

        if self._knowledge_worker and self._knowledge_worker.isRunning():
            if not self._knowledge_sync_pending:
                self._append_log("[知识库] 后台回标仍在运行，本轮已标记为待续跑。")
            self._knowledge_sync_pending = True
            return
        self._last_knowledge_sync_time = now_dt
        self._knowledge_sync_pending = False
        pending_ids = sorted(self._pending_knowledge_snapshot_ids)
        self._pending_knowledge_snapshot_ids.clear()
        self._knowledge_worker = KnowledgeSyncWorker(self._config, snapshot_ids=pending_ids, parent=self)
        self._knowledge_worker.result_ready.connect(self._on_knowledge_sync_ready)
        self._knowledge_worker.error_signal.connect(self._on_knowledge_sync_error)
        self._knowledge_worker.start()

    def _drain_pending_knowledge_sync(self):
        if not self._knowledge_sync_pending:
            return
        self._knowledge_sync_pending = False
        self._schedule_knowledge_sync()

    def _on_knowledge_sync_ready(self, result: dict):
        self._knowledge_worker = None
        for line in list((result or {}).get("log_lines", []) or []):
            self._append_log(str(line or "").strip())
        if bool((result or {}).get("notify_status_changed", False)):
            self._update_notify_status(self._last_snapshot)
        self._drain_pending_knowledge_sync()

    def _on_knowledge_sync_error(self, message: str):
        self._knowledge_worker = None
        self._append_log(f"[知识库] 后台回标失败：{str(message or '未知错误').strip()}")
        self._drain_pending_knowledge_sync()

    def _on_background_task_ready(self, result: dict):
        payload = dict(result or {})
        for line in list(payload.get("log_lines", []) or []):
            self._append_log(str(line or "").strip())
        snapshot_bindings = {
            str(symbol or "").strip().upper(): int(snapshot_id)
            for symbol, snapshot_id in dict(payload.get("snapshot_bindings", {}) or {}).items()
            if str(symbol or "").strip() and int(snapshot_id or 0) > 0
        }
        if snapshot_bindings:
            self.right_table.bind_feedback_snapshot_ids(
                str(self._last_snapshot.get("last_refresh_text", "") or "").strip(),
                snapshot_bindings,
            )
        snapshot_ids = [
            int(item)
            for item in list(payload.get("snapshot_ids", []) or [])
            if int(item or 0) > 0
        ]
        self._schedule_knowledge_sync(snapshot_ids=snapshot_ids)
        if bool(payload.get("notify_status_changed", False)):
            self._update_notify_status(self._last_snapshot)
        if bool(payload.get("refresh_histories", False)):
            if self._current_tab_key() == "history":
                self.left_panel.refresh_histories(self._last_snapshot)
            else:
                self._mark_tab_update_pending("history")
        if bool(payload.get("sim_data_changed", False)):
            if self._current_tab_key() == "sim":
                try:
                    self.sim_panel.update_data(snapshot=self._last_snapshot)
                except Exception:
                    pass
            else:
                self._mark_tab_update_pending("sim")

    def _on_background_task_error(self, message: str):
        self._append_log(f"[后台任务] 执行失败：{str(message or '未知错误').strip()}")

    def toggle_polling(self):
        self._polling_enabled = not self._polling_enabled
        if self._polling_enabled:
            self.btn_poll.setText("暂停轮询")
            self._append_log("已恢复自动轮询。")
            self.refresh_snapshot()  # 炸弹三修复：立即触发一次，后续由链式续约
        else:
            self._timer.stop()  # 停止已排队的 singleShot
            self.btn_poll.setText("恢复轮询")
            self._append_log("已暂停自动轮询。")

    def run_ai_brief(self):
        if self._ai_worker and self._ai_worker.isRunning():
            return
        if not self._last_snapshot:
            self.lbl_ai_status.setText("请先点击「刷新」获取一轮 MT5 快照，再执行 AI 研判。")
            return
        if not str(self._config.ai_api_key or "").strip():
            tip = "尚未配置 AI 密鑰，请点击右上角 ⚙ 设置 → AI与推送 Tab → 填写 AI 密鑰后保存。"
            self.lbl_ai_status.setText(tip)
            self.insight_panel.set_ai_brief(tip)
            return
        self.btn_ai.setEnabled(False)
        self.lbl_ai_status.setText("AI 研判进行中，正在整理当前快照并请求模型...")
        self._ai_worker = AiBriefWorker(self._last_snapshot, self._config, self)
        self._ai_worker.result_ready.connect(self._on_ai_brief_ready)
        self._ai_worker.error_signal.connect(self._on_ai_brief_error)
        self._ai_worker.start()

    def open_settings(self):
        dialog = MetalSettingsDialog(self._config, self)
        if dialog.exec():
            self._config = dialog.runtime_config
            # 炸弹三适配：_timer 已是 singleShot，stop() 后 refresh_snapshot() 回调末尾会按新间隔续约
            self._timer.stop()
            self._append_log("监控设置已保存，正在按新配置刷新。")
            # 自动研判间隔变化时重置上次触发时间，立即应用新设置
            self._last_ai_auto_time = None
            self._update_notify_status()
            self._trigger_macro_sync(force=True)
            self.refresh_snapshot()

    def refresh_snapshot(self):
        if self._worker and self._worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_status_hint.setText("正在读取 MT5 报价、点差和宏观提醒...")
        self._worker = MonitorWorker(self._config.symbols, self)
        self._worker.result_ready.connect(self._on_snapshot_ready)
        self._worker.error_signal.connect(self._on_snapshot_error)
        self._worker.start()

    def _on_snapshot_ready(self, snapshot: dict):
        self._worker = None
        self.btn_refresh.setEnabled(True)
        snapshot = annotate_snapshot_with_model(snapshot)
        snapshot = apply_model_probability_context(snapshot)
        self._last_snapshot = dict(snapshot or {})
        self._set_status_badge(
            snapshot.get("status_badge", "MT5 未连接"),
            snapshot.get("status_tone", AlertTone.NEGATIVE.value),
        )
        self.lbl_status_hint.setText(snapshot.get("status_hint", ""))
        self._update_notify_status(snapshot)

        if str(self._config.ai_api_key or "").strip():
            self.lbl_ai_status.setText(f"AI 已待命：{self._config.ai_model} | 手动触发即可根据当前快照生成简短研判。")
            self._set_ai_funnel_state("待命", action="neutral", push_text="未发生", tone=AlertTone.NEUTRAL.value)
        else:
            self.lbl_ai_status.setText("AI 未配置：请在“监控设置”里补充 AI 密钥后再手动触发研判。")
            self._set_ai_funnel_state("未配置", action="neutral", push_text="未启用", tone=AlertTone.NEUTRAL.value)

        alert_text = str(snapshot.get("alert_text", "") or "").strip()
        self.lbl_alert_banner.setText(alert_text)
        self.lbl_alert_banner.setVisible(bool(alert_text))
        self._update_trade_grade(snapshot)
        self._update_execution_funnel(snapshot)

        self.metrics_panel.update_from_snapshot(snapshot)
        self.right_table.update_from_snapshot(snapshot)
        current_tab = self._current_tab_key()
        if current_tab == "analysis":
            self.insight_panel.update_from_snapshot(snapshot)
        else:
            self._mark_tab_update_pending("analysis")
        if current_tab == "history":
            self.left_panel.update_from_snapshot(snapshot)
        else:
            self._mark_tab_update_pending("history")
        if current_tab == "sim":
            try:
                self.sim_panel.update_data(snapshot=snapshot)
            except Exception:
                pass
        else:
            self._mark_tab_update_pending("sim")
        self._append_log(
            f"[{snapshot.get('last_refresh_text', '--')}] "
            f"{snapshot.get('event_risk_mode_text', '正常观察')}（{snapshot.get('event_risk_mode_source_text', '手动模式')}） | "
            f"{snapshot.get('trade_grade', TradeGrade.OBSERVE_ONLY.value)} | "
            f"{snapshot.get('live_digest', '暂无有效报价')}"
        )
        self._enqueue_snapshot_side_effects(snapshot)
        self._log_external_source_status_changes(snapshot)

        # 快照完成后检查是否要自动触发 AI 研判
        self._check_ai_auto_brief()

        # 炸弹三修复：本轮彻底结束后，精准等待 interval 秒再续约下一轮
        if self._polling_enabled:
            self._timer.start(self._config.refresh_interval_sec * 1000)
        if self._macro_sync_refresh_pending:
            self._macro_sync_refresh_pending = False
            QTimer.singleShot(120, self.refresh_snapshot)

    def _on_snapshot_error(self, message: str):
        self._worker = None
        self.btn_refresh.setEnabled(True)
        self._set_status_badge("刷新失败", AlertTone.NEGATIVE.value)
        self.lbl_status_hint.setText(str(message or "读取监控快照失败。"))
        self._append_log(f"[错误] {message}")
        # M-004 修复：移除锁屏模态框，改为只写日志，避免卡住轮询
        # 炸弹三修复：失败后同样续约，确保轮询持续
        if self._polling_enabled:
            self._timer.start(self._config.refresh_interval_sec * 1000)
        if self._macro_sync_refresh_pending:
            self._macro_sync_refresh_pending = False
            QTimer.singleShot(120, self.refresh_snapshot)

    def _on_ai_brief_ready(self, result: dict):
        self._ai_worker = None
        # DEFECT-005 修复：手动研判完成时也重置自动研判锁，防止竞态导致自动研判永久锁死
        self._ai_auto_is_running = False
        self.btn_ai.setEnabled(True)

        content = str(result.get("content", "") or "").strip()
        model = str(result.get("model", "") or "").strip()
        is_opp = _detect_opportunity(self._last_snapshot)
        push_result = send_ai_brief_notification(result, self._last_snapshot, self._config, is_opportunity=is_opp)
        history_count = append_ai_history_entry(build_ai_history_entry(result, self._last_snapshot, push_result=push_result))
        ai_signal_result = {"inserted_count": 0}
        try:
            ai_signal_result = record_ai_signal(result, self._last_snapshot, push_result=push_result)
        except Exception as exc:
            self._append_log(f"[AI信号] 写入知识库失败（非致命）：{exc}")

        # 实时拦截并执行模拟挂单
        meta = _resolve_ai_result_signal_meta(result, content)
        signal_signature = str(((ai_signal_result or {}).get("entry", {}) or {}).get("signal_signature", "") or "").strip()
        if meta and meta.get("action") in ("long", "short"):
            sim_success, sim_msg = _attempt_sim_execution(
                source_kind="ai_manual",
                snapshot=self._last_snapshot,
                meta=meta,
                signal_signature=signal_signature,
            )
            if sim_success:
                self._append_log(f"[模拟盘跟单成功] {sim_msg}")
            else:
                self._append_log(f"[模拟盘跟单被拒] {sim_msg}")
        elif not meta:
            # N-008 延伸：AI 未输出机器可读信号，明确提示用户
            record_execution_audit(
                source_kind="ai_manual",
                decision_status="skipped",
                snapshot=self._last_snapshot,
                meta={},
                signal_signature=signal_signature,
                reason_key="no_machine_signal",
                result_message="AI 未输出机器信号，本轮仅供参考，无自动跟单操作。",
                trade_mode="simulation",
            )
            self._append_log("[跟单系统] AI 未输出机器信号，本轮仅供参考，无自动跟单操作。")
        else:
            action_hint = str(meta.get("action", "中性") or "中性")
            record_execution_audit(
                source_kind="ai_manual",
                decision_status="skipped",
                snapshot=self._last_snapshot,
                meta=meta,
                signal_signature=signal_signature,
                reason_key="neutral_signal",
                result_message=f"AI 研判方向为「{action_hint}」，未满足开仓条件，本轮跟单考察。",
                trade_mode="simulation",
            )
            self._append_log(f"[跟单系统] AI 研判方向为「{action_hint}」，未满足开仓条件，本轮跟单考察。")


        self.left_panel.set_ai_brief(content or "模型已返回，但内容为空。")
        self.insight_panel.set_ai_brief(content or "模型已返回，但内容为空。")

        is_fallback = bool(result.get("is_fallback", False))
        fallback_reason = str(result.get("fallback_reason", "") or "").strip()

        if is_fallback:
            self._set_ai_funnel_state("规则降级", action="neutral", push_text="未推送", tone=AlertTone.WARNING.value)
            self.lbl_ai_status.setText(
                f"⚠️【规则引擎降级】AI 不可用（{fallback_reason[:40] or '原因未知'}），"
                f"已生成本地规则简报，模拟跟单已禁用。"
            )
            self._append_log(f"[AI降级] 使用规则引擎生成简报，原因：{fallback_reason}")
        elif push_result.get("messages"):
            self._set_ai_funnel_state("已完成", action=str(meta.get("action", "neutral") or "neutral"), push_text="已推送", tone=AlertTone.SUCCESS.value)
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，并已同步推送。")
        elif bool(self._config.ai_push_enabled):
            self._set_ai_funnel_state("已完成", action=str(meta.get("action", "neutral") or "neutral"), push_text="未推送", tone=AlertTone.ACCENT.value)
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，但推送未成功。")
        else:
            self._set_ai_funnel_state("已完成", action=str(meta.get("action", "neutral") or "neutral"), push_text="仅本地生成", tone=AlertTone.ACCENT.value)
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报。")

        if history_count:
            self._append_log("[AI留痕] 已记录本次 AI 研判结果。")
        if int(ai_signal_result.get("inserted_count", 0) or 0) > 0:
            self._append_log("[AI信号] 已写入知识库结构化信号台账。")
        self._append_log(f"[AI研判] {model} 已生成一份新的贵金属快照结论。")
        for line in push_result.get("messages", []):
            self._append_log(f"[AI推送] {line}")
        for line in push_result.get("errors", []):
            self._append_log(f"[AI推送失败] {line}")

        self._update_notify_status(self._last_snapshot)
        self.left_panel.refresh_histories(self._last_snapshot)
        self._update_execution_funnel(self._last_snapshot)

    def _on_ai_brief_error(self, message: str):
        self._ai_worker = None
        self._ai_auto_is_running = False
        self.btn_ai.setEnabled(True)
        error_text = str(message or "AI 研判失败。").strip()
        self.lbl_ai_status.setText(f"AI 研判失败：{error_text}")
        self._append_log(f"[AI研判失败] {error_text}")
        # 根据错误类型生成友好提示（不弹模态框）
        if "401" in error_text or "api key" in error_text.lower() or "invalid" in error_text.lower():
            friendly = (
                "AI 密鑰认证失败（HTTP 401）\n"
                "原因：AI 密鑰无效或已过期。\n"
                "解决：点击右上角 ⚙ 设置 → 「AI与推送」 Tab → 更换有效的 AI 密鑰后保存。"
            )
        elif "403" in error_text:
            friendly = "AI 密鑰权限不足（HTTP 403），请检查密鑰是否有访问该模型的权限。"
        elif "429" in error_text:
            friendly = "AI 接口请求频率超限（HTTP 429），请稍后再试或提升账户限额。"
        elif "timeout" in error_text.lower() or "timed out" in error_text.lower():
            friendly = "AI 请求超时，网络或接口服务繁忙，请稍后重试。"
        else:
            friendly = f"AI 研判失败：{error_text}"
        self.lbl_ai_status.setText(friendly.split("\\n")[0])
        self.insight_panel.set_ai_brief(friendly)
        self._set_ai_funnel_state("AI失败", action="neutral", push_text="未推送", tone=AlertTone.WARNING.value)
        self._update_execution_funnel(self._last_snapshot)

    def _check_ai_auto_brief(self):
        """检查是否到达自动 AI 研判时间并触发（静默，不弹窗）"""
        interval_min = int(getattr(self._config, "ai_auto_interval_min", 0) or 0)
        if interval_min <= 0:
            return  # 关闭自动
        if not str(self._config.ai_api_key or "").strip():
            return  # 未配置 key
        if self._ai_worker and self._ai_worker.isRunning():
            return  # 当前有研判在跑
        if self._ai_auto_is_running:
            return
        if not self._last_snapshot:
            return

        from datetime import datetime
        now = datetime.now()
        if self._last_ai_auto_time is not None:
            elapsed_min = (now - self._last_ai_auto_time).total_seconds() / 60.0
            # 高机会信号：允许 5 分钟后即可再次触发，绕过常规间隔限制
            is_opp = _detect_opportunity(self._last_snapshot)
            min_elapsed = 5 if is_opp else interval_min
            if elapsed_min < min_elapsed:
                return  # 还没到时间

        self._last_ai_auto_time = now
        self._start_ai_auto_brief()

    def _start_ai_auto_brief(self):
        """后台静默运行 AI 自动研判，不弹窗，不禁用手动按钮"""
        self._ai_auto_is_running = True
        self.lbl_ai_status.setText("AI 自动研判进行中（后台静默），完成后更新状态栏...")
        self._ai_worker = AiBriefWorker(self._last_snapshot, self._config, self)
        self._ai_worker.result_ready.connect(self._on_ai_auto_brief_ready)
        self._ai_worker.error_signal.connect(self._on_ai_auto_brief_error)
        self._ai_worker.start()

    def _on_ai_auto_brief_ready(self, result: dict):
        """AI 自动研判完成——写留痕、推送，更新状态栏，不弹窗"""
        self._ai_worker = None
        # N-005 修复：用 try/finally 确保 _ai_auto_is_running 总被重置，
        # 避免推送/写文件异常时标志永远停在 True，导致后续自动研判全被锁死
        try:
            content = str(result.get("content", "") or "").strip()
            model = str(result.get("model", "") or "").strip()
            meta = _resolve_ai_result_signal_meta(result, content)
            is_opp = _detect_opportunity(self._last_snapshot)
            push_result = send_ai_brief_notification(result, self._last_snapshot, self._config, is_opportunity=is_opp)
            history_count = append_ai_history_entry(build_ai_history_entry(result, self._last_snapshot, push_result=push_result))
            ai_signal_result = {"inserted_count": 0}
            try:
                ai_signal_result = record_ai_signal(result, self._last_snapshot, push_result=push_result)
            except Exception as exc:
                self._append_log(f"[AI自动信号] 写入知识库失败（非致命）：{exc}")
            self.left_panel.set_ai_brief(content or "自动研判已完成，模型内容为空。")
            signal_signature = str(((ai_signal_result or {}).get("entry", {}) or {}).get("signal_signature", "") or "").strip()

            if meta and meta.get("action") in ("long", "short"):
                sim_success, sim_msg = _attempt_sim_execution(
                    source_kind="ai_auto",
                    snapshot=self._last_snapshot,
                    meta=meta,
                    signal_signature=signal_signature,
                )
                if sim_success:
                    self._append_log(f"[AI自动模拟跟单成功] {sim_msg}")
                else:
                    self._append_log(f"[AI自动模拟跟单被拒] {sim_msg}")
            elif not meta:
                record_execution_audit(
                    source_kind="ai_auto",
                    decision_status="skipped",
                    snapshot=self._last_snapshot,
                    meta={},
                    signal_signature=signal_signature,
                    reason_key="no_machine_signal",
                    result_message="AI 自动研判未输出机器信号，本轮仅留痕不跟单。",
                    trade_mode="simulation",
                )
                self._append_log("[AI自动跟单] 本轮未输出机器信号，仅保留研判留痕。")
            else:
                action_hint = str(meta.get("action", "中性") or "中性")
                record_execution_audit(
                    source_kind="ai_auto",
                    decision_status="skipped",
                    snapshot=self._last_snapshot,
                    meta=meta,
                    signal_signature=signal_signature,
                    reason_key="neutral_signal",
                    result_message=f"AI 自动研判方向为「{action_hint}」，未满足自动跟单条件。",
                    trade_mode="simulation",
                )
                self._append_log(f"[AI自动跟单] 方向为「{action_hint}」，本轮不自动开仓。")

            interval_min = int(getattr(self._config, "ai_auto_interval_min", 0) or 0)
            opp_tag = "【机会提醒⚡】" if is_opp else ""
            if push_result.get("messages"):
                self._set_ai_funnel_state("自动完成", action=str(meta.get("action", "neutral") or "neutral"), push_text="已推送", tone=AlertTone.SUCCESS.value)
                self.lbl_ai_status.setText(f"{opp_tag}AI 自动研判完成（每 {interval_min} 分钟）：{model} 已推送最新简报。")
            else:
                self._set_ai_funnel_state("自动完成", action=str(meta.get("action", "neutral") or "neutral"), push_text="仅本地生成", tone=AlertTone.ACCENT.value)
                self.lbl_ai_status.setText(f"AI 自动研判完成（每 {interval_min} 分钟）：{model} 已生成简报。")

            if history_count:
                self._append_log("[AI自动留痕] 已记录本次自动 AI 研判结果。")
            if int(ai_signal_result.get("inserted_count", 0) or 0) > 0:
                self._append_log("[AI自动信号] 已写入知识库结构化信号台账。")
            try:
                ai_signal_summary = summarize_recent_ai_signals(days=30)
                self._append_log(f"[AI信号统计] {ai_signal_summary.get('summary_text', '')}")
            except Exception as exc:
                self._append_log(f"[AI信号统计] 汇总失败（非致命）：{exc}")
            self._append_log(f"[AI自动研判] {model} 已完成自动研判。")
            for line in push_result.get("messages", []):
                self._append_log(f"[AI推送] {line}")
            for line in push_result.get("errors", []):
                self._append_log(f"[AI推送失败] {line}")
            self._update_notify_status(self._last_snapshot)
            self.left_panel.refresh_histories(self._last_snapshot)
            self._update_execution_funnel(self._last_snapshot)
        except Exception as exc:
            import logging as _logging
            _logging.exception("[AI自动研判] 回调处理异常")
            self._append_log(f"[AI自动研判失败] 回调异常：{exc}")
        finally:
            self._ai_auto_is_running = False


    def _on_ai_auto_brief_error(self, message: str):
        self._ai_worker = None
        self._ai_auto_is_running = False
        error_text = str(message or "AI 自动研判失败。").strip()
        self.lbl_ai_status.setText(f"AI 自动研判失败：{error_text}")
        self._append_log(f"[AI自动研判失败] {error_text}")
        self._set_ai_funnel_state("AI自动失败", action="neutral", push_text="未推送", tone=AlertTone.WARNING.value)
        self._update_execution_funnel(self._last_snapshot)

    def _update_trade_grade(self, snapshot: dict):
        grade = str(
            snapshot.get("trade_grade", TradeGrade.OBSERVE_ONLY.value)
            or TradeGrade.OBSERVE_ONLY.value
        ).strip()
        tone = str(
            snapshot.get("trade_grade_tone", AlertTone.NEUTRAL.value)
            or AlertTone.NEUTRAL.value
        ).strip()
        tone_styles = {
            AlertTone.SUCCESS.value: "background:#ecfdf5;border:1px solid #bbf7d0;border-radius:12px;padding:10px;color:#166534;font-size:12px;line-height:1.6;font-weight:700;",
            AlertTone.WARNING.value: "background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:10px;color:#9a3412;font-size:12px;line-height:1.6;font-weight:700;",
            AlertTone.ACCENT.value: "background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:10px;color:#1d4ed8;font-size:12px;line-height:1.6;font-weight:700;",
            AlertTone.NEUTRAL.value: style.STYLE_PANEL_NEUTRAL_BOLD,
        }
        self.lbl_trade_grade.setText(
            _build_trade_grade_display_text(
                snapshot,
                trade_mode=str(getattr(self._config, "trade_mode", "simulation") or "simulation"),
            )
        )
        self.lbl_trade_grade.setStyleSheet(
            tone_styles.get(tone, tone_styles[AlertTone.NEUTRAL.value])
        )

    def _set_ai_funnel_state(
        self,
        status_text: str,
        action: str = "neutral",
        push_text: str = "未发生",
        tone: str = AlertTone.NEUTRAL.value,
    ) -> None:
        action_map = {
            "long": "做多",
            "short": "做空",
            "neutral": "观望",
        }
        self._last_ai_funnel_state = {
            "status_text": str(status_text or "待命").strip() or "待命",
            "action_text": action_map.get(str(action or "neutral").strip().lower(), "观望"),
            "push_text": str(push_text or "未发生").strip() or "未发生",
            "tone": str(tone or AlertTone.NEUTRAL.value).strip() or AlertTone.NEUTRAL.value,
        }

    def _update_execution_funnel(self, snapshot: dict | None = None) -> None:
        payload = _build_execution_funnel_payload(dict(snapshot or self._last_snapshot or {}), self._last_ai_funnel_state)
        tone = str(payload.get("tone", AlertTone.NEUTRAL.value) or AlertTone.NEUTRAL.value).strip()
        tone_styles = {
            AlertTone.SUCCESS.value: "background:#ecfdf5;border:1px solid #bbf7d0;border-radius:10px;padding:10px;color:#166534;font-size:11px;line-height:1.55;font-weight:700;",
            AlertTone.WARNING.value: "background:#fff7ed;border:1px solid #fdba74;border-radius:10px;padding:10px;color:#9a3412;font-size:11px;line-height:1.55;font-weight:700;",
            AlertTone.ACCENT.value: "background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:10px;color:#1d4ed8;font-size:11px;line-height:1.55;font-weight:700;",
            AlertTone.NEUTRAL.value: "background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:10px;color:#334155;font-size:11px;line-height:1.55;font-weight:600;",
        }
        self.lbl_execution_funnel.setText(str(payload.get("text", "") or "执行漏斗待计算。").strip())
        self.lbl_execution_funnel.setStyleSheet(tone_styles.get(tone, tone_styles[AlertTone.NEUTRAL.value]))

    def closeEvent(self, event):
        if hasattr(self, "_timer"):
            self._timer.stop()
        if hasattr(self, "_macro_timer"):
            self._macro_timer.stop()
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.wait(1000)
        if self._knowledge_worker and self._knowledge_worker.isRunning():
            self._knowledge_worker.wait(1000)
        if self._macro_worker and self._macro_worker.isRunning():
            self._macro_worker.wait(2000)
        if self._snapshot_task_worker and self._snapshot_task_worker.isRunning():
            _queue_stop_task()
            self._snapshot_task_worker.wait(1500)
        shutdown_connection()
        super().closeEvent(event)

