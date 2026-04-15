import copy
import os
import queue

from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget


import style
from alert_history import append_history_entries, build_snapshot_history_entries
from ai_briefing import request_ai_brief
from ai_history import append_ai_history_entry, build_ai_history_entry
from app_config import get_runtime_config
from mt5_sim_trading import SIM_ENGINE
from backtest_engine import extract_signal_meta
from event_feed import apply_event_feed_to_snapshot, load_event_feed, merge_event_schedule_texts
from event_schedule import resolve_event_risk_context
from external_signal_context import apply_external_signal_context
from knowledge_feedback import refresh_rule_feedback_scores, summarize_feedback_stats
from knowledge_governance import build_learning_report, refresh_rule_governance
from knowledge_ai_signals import record_ai_signal, summarize_recent_ai_signals
from knowledge_ml import annotate_snapshot_with_model, apply_model_probability_context, train_probability_model
from knowledge_runtime import backfill_snapshot_outcomes, record_snapshot, summarize_outcome_stats
from knowledge_scoring import match_rules_to_snapshots, refresh_rule_scores, summarize_rule_scores
from macro_data_feed import apply_macro_data_to_snapshot, load_macro_data_feed
from macro_news_feed import apply_macro_news_to_snapshot, load_macro_news_feed
from monitor_engine import run_monitor_cycle
from mt5_gateway import shutdown_connection
from notification import get_notification_status, send_ai_brief_notification, send_learning_report_notification, send_notifications
from quote_models import SnapshotItem
from settings_dialog import MetalSettingsDialog
from sim_signal_bridge import build_rule_sim_signal_decision
from ui_panels import DashboardMetricsPanel, InsightPanel, LeftTabPanel, WatchListTable

SNAPSHOT_TASK_QUEUE: queue.Queue = queue.Queue(maxsize=100)
MACRO_SYNC_INTERVAL_MS = 15 * 60 * 1000


def _normalize_snapshot_item(item: dict | SnapshotItem | None) -> dict:
    """统一 UI 主链消费的快照项字段契约。"""
    return SnapshotItem.from_payload(item).to_dict()


def _queue_latest_task(task_payload: dict) -> int:
    """将最新后台任务入队；若队列已满，优先淘汰最旧任务。"""
    dropped_count = 0
    while True:
        try:
            SNAPSHOT_TASK_QUEUE.put(task_payload, block=False)
            return dropped_count
        except queue.Full:
            try:
                dropped = SNAPSHOT_TASK_QUEUE.get_nowait()
            except queue.Empty as exc:
                raise queue.Full from exc
            if isinstance(dropped, dict) and str(dropped.get("kind", "") or "").strip() == "stop":
                # 理论上运行期不会遇到 stop；若遇到则保守失败，避免吞掉停机信号。
                try:
                    SNAPSHOT_TASK_QUEUE.put(dropped, block=False)
                except queue.Full:
                    pass
                raise queue.Full
            dropped_count += 1


def _queue_stop_task() -> None:
    """关闭窗口时确保停机信号可入队；必要时丢弃过期快照任务。"""
    stop_task = {"kind": "stop"}
    while True:
        try:
            SNAPSHOT_TASK_QUEUE.put(stop_task, block=False)
            return
        except queue.Full:
            try:
                SNAPSHOT_TASK_QUEUE.get_nowait()
            except queue.Empty:
                return


def _load_external_feeds(runtime_config, symbols: list[str], cache_only: bool) -> dict:
    return {
        "event_feed": load_event_feed(
            enabled=bool(getattr(runtime_config, "event_feed_enabled", False)) if runtime_config else False,
            source=str(getattr(runtime_config, "event_feed_url", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "event_feed_refresh_min", 60) or 60) if runtime_config else 60,
            cache_only=cache_only,
        ),
        "macro_news": load_macro_news_feed(
            enabled=bool(getattr(runtime_config, "macro_news_feed_enabled", False)) if runtime_config else False,
            source_text=str(getattr(runtime_config, "macro_news_feed_urls", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "macro_news_feed_refresh_min", 30) or 30) if runtime_config else 30,
            symbols=symbols,
            cache_only=cache_only,
        ),
        "macro_data": load_macro_data_feed(
            enabled=bool(getattr(runtime_config, "macro_data_feed_enabled", False)) if runtime_config else False,
            spec_source=str(getattr(runtime_config, "macro_data_feed_specs", "") or "") if runtime_config else "",
            refresh_min=int(getattr(runtime_config, "macro_data_feed_refresh_min", 60) or 60) if runtime_config else 60,
            symbols=symbols,
            cache_only=cache_only,
            env=dict(os.environ),  # 透传环境变量：ALPHAVANTAGE_API_KEY / FRED_API_KEY / BLS_API_KEY
        ),
    }


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

    def __init__(self, symbols: list[str], parent=None):
        super().__init__(parent)
        self.symbols = list(symbols or [])

    def run(self):
        try:
            runtime_config = getattr(self.parent(), "_config", None)
            self.result_ready.emit(_load_external_feeds(runtime_config, self.symbols, cache_only=False))
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


def _enrich_signal_with_snapshot_context(meta: dict, snapshot: dict) -> dict:
    payload = dict(meta or {})
    symbol = str(payload.get("symbol", "") or "").strip().upper()
    if not symbol:
        return payload
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        item_symbol = str(item.get("symbol", "") or "").strip().upper()
        if item_symbol != symbol:
            continue
        if float(payload.get("atr14", 0.0) or 0.0) <= 0:
            payload["atr14"] = float(item.get("atr14", 0.0) or 0.0)
        if float(payload.get("atr14_h4", 0.0) or 0.0) <= 0:
            payload["atr14_h4"] = float(item.get("atr14_h4", 0.0) or 0.0)
        if float(payload.get("risk_reward_atr", 0.0) or 0.0) <= 0:
            payload["risk_reward_atr"] = float(item.get("risk_reward_atr", 0.0) or 0.0)
        if float(payload.get("tp2", 0.0) or 0.0) <= 0:
            payload["tp2"] = float(item.get("risk_reward_target_price_2", 0.0) or 0.0)
        return payload
    return payload


def _detect_opportunity(snapshot: dict, rr_threshold: float = 2.0) -> bool:
    """检测当前快照中是否存在高质量出手机会。

    判定条件：任意观察品种的 risk_reward_ratio ≥ rr_threshold（2.0）
    且 risk_reward_ready == True。
    """
    for item in [_normalize_snapshot_item(item) for item in list((snapshot or {}).get("items", []) or [])]:
        if bool(item.get("risk_reward_ready", False)):
            rr = float(item.get("risk_reward_ratio", 0.0) or 0.0)
            if rr >= rr_threshold:
                return True
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
        SIM_ENGINE.update_prices(live_quotes)
        result["sim_data_changed"] = True
        open_symbols = {
            str(item.get("symbol", "") or "").strip().upper()
            for item in list(SIM_ENGINE.get_open_positions() or [])
            if str(item.get("symbol", "") or "").strip()
        }
        rule_signal, rule_reason = build_rule_sim_signal_decision(snapshot)
        if rule_signal and str(rule_signal.get("symbol", "") or "").strip().upper() not in open_symbols:
            sim_success, sim_message = SIM_ENGINE.execute_signal(rule_signal)
            if sim_success:
                result["sim_data_changed"] = True
                result["log_lines"].append(
                    f"[模拟盘规则跟单] 已按结构候选开仓：{rule_signal.get('action')} {rule_signal.get('symbol')}。"
                )
            else:
                result["log_lines"].append(f"[模拟盘规则跟单被拒] {sim_message}")
        elif rule_reason:
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
    if int(outcome_result.get("labeled_count", 0) or 0) <= 0 and not result["log_lines"]:
        return result

    stats_30m = summarize_outcome_stats(horizon_min=30)
    refresh_rule_scores(horizon_min=30)
    refresh_rule_feedback_scores()
    refresh_rule_governance(horizon_min=30)
    ml_result = train_probability_model(horizon_min=30)
    rule_summary = summarize_rule_scores(horizon_min=30)
    learning_report = build_learning_report(horizon_min=30, persist=True)
    feedback_summary = summarize_feedback_stats(days=30)
    result["log_lines"].append(
        f"[知识库] 已新增 {outcome_result.get('labeled_count', 0)} 条结果回标。{stats_30m.get('summary_text', '')}"
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
    if int(feedback_summary.get("total_count", 0) or 0) > 0:
        result["log_lines"].append(f"[知识库] {feedback_summary.get('summary_text', '')}")
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


class BackgroundTaskWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def run(self):
        while True:
            try:
                task = SNAPSHOT_TASK_QUEUE.get()
                if not isinstance(task, dict):
                    continue
                if str(task.get("kind", "") or "").strip() == "stop":
                    return
                if str(task.get("kind", "") or "").strip() == "snapshot_side_effects":
                    self.result_ready.emit(
                        process_snapshot_side_effects(
                            dict(task.get("snapshot", {}) or {}),
                            task.get("config"),
                            run_backtest=bool(task.get("run_backtest", False)),
                        )
                    )
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
        self._last_ai_auto_time = None  # 上次自动 AI 研判时间
        self._ai_auto_is_running = False  # 防止自动触发重叠
        self._last_external_source_warning_digest = ""
        self._last_macro_sync_status_digest = ""
        self._macro_sync_refresh_pending = False
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
        main_tabs = QTabWidget()
        main_tabs.setStyleSheet(style.STYLE_TAB_WIDGET)

        # ── Tab 1：实时监控 ──
        self._build_tab_monitor(main_tabs)

        # ── Tab 2：提醒分析 ──
        self._build_tab_analysis(main_tabs)

        # ── Tab 3：历史日志 ──
        self._build_tab_history(main_tabs)

        # ── Tab 4：模拟战绩 ──
        self._build_tab_sim_trading(main_tabs)

        root_layout.addWidget(main_tabs, 1)
        self.setCentralWidget(root)
        self.left_panel.refresh_histories()
        self._update_notify_status()

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
        grade_lay.addWidget(self.lbl_trade_grade)
        grade_lay.addWidget(self.lbl_alert_banner)
        grade_lay.addWidget(self.lbl_ai_status)
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



    def _append_log(self, message: str):
        if hasattr(self, "left_panel"):
            self.left_panel.append_log(message)

    def _set_status_badge(self, text: str, tone: str):
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
        self._snapshot_task_worker = BackgroundTaskWorker(self)
        self._snapshot_task_worker.result_ready.connect(self._on_background_task_ready)
        self._snapshot_task_worker.error_signal.connect(self._on_background_task_error)
        self._snapshot_task_worker.start()

    def _trigger_macro_sync(self):
        """3.4 修复：触发宏观数据后台刷新，不影响 MT5 报价线程。"""
        if self._macro_worker and self._macro_worker.isRunning():
            return  # 上一次还没跑完，跳过
        self._macro_worker = MacroSyncWorker(self._config.symbols, self)
        self._macro_worker.result_ready.connect(self._on_macro_sync_ready)
        self._macro_worker.error_signal.connect(self._on_macro_sync_error)
        self._macro_worker.start()

    def _on_macro_sync_ready(self, result: dict):
        self._macro_worker = None
        payload = dict(result or {})
        status_parts = []
        refresh_needed = False
        for result_key, snapshot_key, label in (
            ("event_feed", "event_feed_status_text", "事件源"),
            ("macro_news", "macro_news_status_text", "资讯流"),
            ("macro_data", "macro_data_status_text", "宏观数据"),
        ):
            item = dict(payload.get(result_key, {}) or {})
            status_text = str(item.get("status_text", "") or "").strip()
            status = str(item.get("status", "") or "").strip().lower()
            if status_text:
                status_parts.append(f"{label}:{status_text}")
                if status not in {"disabled", "missing"} and self._last_snapshot.get(snapshot_key) != status_text:
                    refresh_needed = True
        digest = " | ".join(status_parts)
        if digest and digest != self._last_macro_sync_status_digest:
            self._append_log(f"[宏观同步] {digest}")
        self._last_macro_sync_status_digest = digest

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
        dropped_count = _queue_latest_task(
            {
                "kind": "snapshot_side_effects",
                "snapshot": dict(snapshot or {}),
                "config": copy.deepcopy(self._config),
                "run_backtest": run_backtest,
            }
        )
        if dropped_count > 0:
            self._append_log(f"[警告] 后台任务堆积，已丢弃 {dropped_count} 条过期风控与通知任务。")

    def _schedule_knowledge_sync(self, snapshot_ids: list[int] | None = None):
        for snapshot_id in list(snapshot_ids or []):
            if int(snapshot_id or 0) > 0:
                self._pending_knowledge_snapshot_ids.add(int(snapshot_id))
        if self._knowledge_worker and self._knowledge_worker.isRunning():
            if not self._knowledge_sync_pending:
                self._append_log("[知识库] 后台回标仍在运行，本轮已标记为待续跑。")
            self._knowledge_sync_pending = True
            return
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
            self.left_panel.refresh_histories(self._last_snapshot)
        if bool(payload.get("sim_data_changed", False)):
            try:
                self.sim_panel.update_data()
            except Exception:
                pass

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
            self._trigger_macro_sync()
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
        self._set_status_badge(snapshot.get("status_badge", "MT5 未连接"), snapshot.get("status_tone", "negative"))
        self.lbl_status_hint.setText(snapshot.get("status_hint", ""))
        self._update_notify_status(snapshot)

        if str(self._config.ai_api_key or "").strip():
            self.lbl_ai_status.setText(f"AI 已待命：{self._config.ai_model} | 手动触发即可根据当前快照生成简短研判。")
        else:
            self.lbl_ai_status.setText("AI 未配置：请在“监控设置”里补充 AI 密钥后再手动触发研判。")

        alert_text = str(snapshot.get("alert_text", "") or "").strip()
        self.lbl_alert_banner.setText(alert_text)
        self.lbl_alert_banner.setVisible(bool(alert_text))
        self._update_trade_grade(snapshot)

        self.metrics_panel.update_from_snapshot(snapshot)
        self.insight_panel.update_from_snapshot(snapshot)
        self.left_panel.update_from_snapshot(snapshot)
        self.right_table.update_from_snapshot(snapshot)
        self._append_log(
            f"[{snapshot.get('last_refresh_text', '--')}] "
            f"{snapshot.get('event_risk_mode_text', '正常观察')}（{snapshot.get('event_risk_mode_source_text', '手动模式')}） | "
            f"{snapshot.get('trade_grade', '只适合观察')} | "
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
        self._set_status_badge("刷新失败", "negative")
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
        meta = dict(result.get("signal_meta", {}) or {}) or extract_signal_meta(content)
        if meta and meta.get("action") in ("long", "short"):
            meta = _enrich_signal_with_snapshot_context(meta, self._last_snapshot)
            sim_success, sim_msg = SIM_ENGINE.execute_signal(meta)
            if sim_success:
                self._append_log(f"[模拟盘跟单成功] {sim_msg}")
            else:
                self._append_log(f"[模拟盘跟单被拒] {sim_msg}")
        elif meta is None:
            # N-008 延伸：AI 未输出机器可读信号，明确提示用户
            self._append_log("[跟单系统] AI 未输出机器信号，本轮仅供参考，无自动跟单操作。")
        else:
            action_hint = str(meta.get("action", "中性") or "中性")
            self._append_log(f"[跟单系统] AI 研判方向为「{action_hint}」，未满足开仓条件，本轮跟单考察。")


        self.left_panel.set_ai_brief(content or "模型已返回，但内容为空。")
        self.insight_panel.set_ai_brief(content or "模型已返回，但内容为空。")

        is_fallback = bool(result.get("is_fallback", False))
        fallback_reason = str(result.get("fallback_reason", "") or "").strip()

        if is_fallback:
            self.lbl_ai_status.setText(
                f"⚠️【规则引擎降级】AI 不可用（{fallback_reason[:40] or '原因未知'}），"
                f"已生成本地规则简报，模拟跟单已禁用。"
            )
            self._append_log(f"[AI降级] 使用规则引擎生成简报，原因：{fallback_reason}")
        elif push_result.get("messages"):
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，并已同步推送。")
        elif bool(self._config.ai_push_enabled):
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，但推送未成功。")
        else:
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
            is_opp = _detect_opportunity(self._last_snapshot)
            push_result = send_ai_brief_notification(result, self._last_snapshot, self._config, is_opportunity=is_opp)
            history_count = append_ai_history_entry(build_ai_history_entry(result, self._last_snapshot, push_result=push_result))
            ai_signal_result = {"inserted_count": 0}
            try:
                ai_signal_result = record_ai_signal(result, self._last_snapshot, push_result=push_result)
            except Exception as exc:
                self._append_log(f"[AI自动信号] 写入知识库失败（非致命）：{exc}")
            self.left_panel.set_ai_brief(content or "自动研判已完成，模型内容为空。")

            interval_min = int(getattr(self._config, "ai_auto_interval_min", 0) or 0)
            opp_tag = "【机会提醒⚡】" if is_opp else ""
            if push_result.get("messages"):
                self.lbl_ai_status.setText(f"{opp_tag}AI 自动研判完成（每 {interval_min} 分钟）：{model} 已推送最新简报。")
            else:
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

    def _update_trade_grade(self, snapshot: dict):
        grade = str(snapshot.get("trade_grade", "只适合观察") or "只适合观察").strip()
        detail = str(
            snapshot.get("trade_grade_detail", "先完成一轮快照刷新，再评估当前执行环境。")
            or "先完成一轮快照刷新，再评估当前执行环境。"
        ).strip()
        next_review = str(snapshot.get("trade_next_review", "下一轮轮询后再看。") or "下一轮轮询后再看。").strip()
        tone = str(snapshot.get("trade_grade_tone", "neutral") or "neutral").strip()
        tone_styles = {
            "success": "background:#ecfdf5;border:1px solid #bbf7d0;border-radius:12px;padding:10px;color:#166534;font-size:12px;line-height:1.6;font-weight:700;",
            "warning": "background:#fff7ed;border:1px solid #fdba74;border-radius:12px;padding:10px;color:#9a3412;font-size:12px;line-height:1.6;font-weight:700;",
            "accent": "background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:10px;color:#1d4ed8;font-size:12px;line-height:1.6;font-weight:700;",
            "neutral": style.STYLE_PANEL_NEUTRAL_BOLD,
        }
        self.lbl_trade_grade.setText(f"出手分级：{grade}\n原因：{detail}\n下一次复核：{next_review}")
        self.lbl_trade_grade.setStyleSheet(tone_styles.get(tone, tone_styles["neutral"]))

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

