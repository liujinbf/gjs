from PySide6.QtCore import QThread, QTimer, Signal, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)
import style
from ui_panels import DashboardMetricsPanel, InsightPanel, LeftTabPanel, WatchListTable

from alert_history import (
    append_history_entries,
    build_snapshot_history_entries,
    read_recent_history,
    summarize_effectiveness,
    summarize_recent_history,
)
from ai_history import (
    append_ai_history_entry,
    build_ai_history_entry,
    read_recent_ai_history,
    summarize_recent_ai_history,
)
from ai_briefing import request_ai_brief
from app_config import get_runtime_config
from event_schedule import resolve_event_risk_context
from mt5_gateway import shutdown_connection
from monitor_engine import run_monitor_cycle
from notification import get_notification_status, send_ai_brief_notification, send_notifications
from settings_dialog import MetalSettingsDialog


class MonitorWorker(QThread):
    result_ready = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, symbols: list[str], parent=None):
        super().__init__(parent)
        self.symbols = list(symbols or [])

    def run(self):
        try:
            runtime_config = getattr(self.parent(), "_config", None)
            event_context = resolve_event_risk_context(
                base_mode=getattr(runtime_config, "event_risk_mode", "normal") if runtime_config else "normal",
                auto_enabled=bool(getattr(runtime_config, "event_auto_mode_enabled", False)) if runtime_config else False,
                schedule_text=str(getattr(runtime_config, "event_schedule_text", "") or "") if runtime_config else "",
                pre_event_lead_min=int(getattr(runtime_config, "event_pre_window_min", 30) or 30) if runtime_config else 30,
                post_event_window_min=int(getattr(runtime_config, "event_post_window_min", 15) or 15) if runtime_config else 15,
            )
            snapshot = run_monitor_cycle(self.symbols, event_risk_mode=event_context["mode"], event_context=event_context)
            self.result_ready.emit(snapshot)
        except Exception as exc:
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
        except Exception as exc:
            self.error_signal.emit(str(exc))


class MetalMonitorWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("贵金属监控终端")
        self.resize(1220, 820)
        self._config = get_runtime_config()
        self._worker = None
        self._ai_worker = None
        self._polling_enabled = True
        self._last_snapshot = {}
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_snapshot)
        self._timer.start(self._config.refresh_interval_sec * 1000)
        QTimer.singleShot(120, self.refresh_snapshot)

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        title = QLabel("贵金属监控终端")
        title.setStyleSheet(style.STYLE_TITLE_PRIMARY)
        subtitle = QLabel("只保留贵金属 / 外汇监控、点差提醒与宏观窗口提示，不再混入虚拟币和交易执行功能。")
        subtitle.setStyleSheet(style.STYLE_SUBTITLE)
        root_layout.addWidget(title)
        root_layout.addWidget(subtitle)

        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        self.lbl_status_badge = QLabel("准备中")
        self.lbl_status_badge.setAlignment(Qt.AlignCenter)
        self.lbl_status_badge.setStyleSheet(style.STYLE_BADGE_NEUTRAL)
        top_row.addWidget(self.lbl_status_badge)
        self.lbl_status_hint = QLabel("正在准备 MT5 终端连接。")
        self.lbl_status_hint.setStyleSheet("color:#475569;font-size:12px;")
        top_row.addWidget(self.lbl_status_hint, 1)

        self.btn_refresh = QPushButton("立即刷新")
        self.btn_refresh.clicked.connect(self.refresh_snapshot)
        self.btn_poll = QPushButton("暂停轮询")
        self.btn_poll.clicked.connect(self.toggle_polling)
        self.btn_ai = QPushButton("AI 快速研判")
        self.btn_ai.clicked.connect(self.run_ai_brief)
        self.btn_settings = QPushButton("监控设置")
        self.btn_settings.clicked.connect(self.open_settings)
        for btn in (self.btn_refresh, self.btn_poll, self.btn_ai, self.btn_settings):
            btn.setFixedHeight(34)
        top_row.addWidget(self.btn_refresh)
        top_row.addWidget(self.btn_poll)
        top_row.addWidget(self.btn_ai)
        top_row.addWidget(self.btn_settings)
        root_layout.addLayout(top_row)
        self.lbl_notify_status = QLabel("")
        self.lbl_notify_status.setWordWrap(True)
        self.lbl_notify_status.setStyleSheet(style.STYLE_PANEL_NEUTRAL)
        root_layout.addWidget(self.lbl_notify_status)

        self.metrics_panel = DashboardMetricsPanel()
        root_layout.addWidget(self.metrics_panel)

        self.lbl_alert_banner = QLabel("")
        self.lbl_alert_banner.setWordWrap(True)
        self.lbl_alert_banner.setStyleSheet(style.STYLE_PANEL_WARNING_BOLD)
        self.lbl_alert_banner.hide()
        root_layout.addWidget(self.lbl_alert_banner)

        self.lbl_trade_grade = QLabel("出手分级待计算，先获取一轮 MT5 快照。")
        self.lbl_trade_grade.setWordWrap(True)
        self.lbl_trade_grade.setStyleSheet(style.STYLE_PANEL_NEUTRAL_BOLD)
        root_layout.addWidget(self.lbl_trade_grade)

        self.lbl_ai_status = QLabel("AI 研判待命，当前只支持手动触发。")
        self.lbl_ai_status.setWordWrap(True)
        self.lbl_ai_status.setStyleSheet(style.STYLE_PANEL_ACCENT)
        root_layout.addWidget(self.lbl_ai_status)

        self.insight_panel = InsightPanel()
        root_layout.addWidget(self.insight_panel)

        middle_row = QHBoxLayout()
        middle_row.setSpacing(12)
        
        self.left_panel = LeftTabPanel()
        middle_row.addWidget(self.left_panel, 3)
        
        self.right_table = WatchListTable()
        middle_row.addWidget(self.right_table, 5)

        root_layout.addLayout(middle_row, 1)
        self.setCentralWidget(root)
        self._refresh_ai_history_panel()

    def _append_log(self, message: str):
        if hasattr(self, 'left_panel'):
            self.left_panel.append_log(message)

    def _set_status_badge(self, text: str, tone: str):
        style_map = style.BADGE_STYLE_MAP
        self.lbl_status_badge.setText(text)
        self.lbl_status_badge.setStyleSheet(style_map.get(tone, style.STYLE_BADGE_NEUTRAL))

    def toggle_polling(self):
        self._polling_enabled = not self._polling_enabled
        if self._polling_enabled:
            self._timer.start(self._config.refresh_interval_sec * 1000)
            self.btn_poll.setText("暂停轮询")
            self._append_log("已恢复自动轮询。")
            self.refresh_snapshot()
        else:
            self._timer.stop()
            self.btn_poll.setText("恢复轮询")
            self._append_log("已暂停自动轮询。")

    def run_ai_brief(self):
        if self._ai_worker and self._ai_worker.isRunning():
            return
        if not self._last_snapshot:
            QMessageBox.information(self, "请先刷新", "请先获取一轮最新 MT5 快照，再执行 AI 研判。")
            return
        if not str(self._config.ai_api_key or "").strip():
            QMessageBox.warning(self, "AI 未配置", "当前未配置 AI 密钥，请先在“监控设置”里补充 AI 接口参数。")
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
            self._timer.start(self._config.refresh_interval_sec * 1000)
            self._append_log("监控设置已保存，正在按新配置刷新。")
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
        self.btn_refresh.setEnabled(True)
        self._last_snapshot = dict(snapshot or {})
        self._set_status_badge(snapshot.get("status_badge", "MT5 未连接"), snapshot.get("status_tone", "negative"))
        self.lbl_status_hint.setText(snapshot.get("status_hint", ""))
        notify_status = get_notification_status(self._config)
        discipline_text = f"当前纪律：{snapshot.get('event_risk_mode_text', '正常观察')}（{snapshot.get('event_risk_mode_source_text', '手动模式')}）"
        if str(snapshot.get("event_next_name", "") or "").strip() and str(snapshot.get("event_next_time_text", "") or "").strip():
            discipline_text += f" | 下个事件：{snapshot.get('event_next_name', '')}（{snapshot.get('event_next_time_text', '')}）"
        self.lbl_notify_status.setText(
            f"{notify_status.get('channels_text', '')} | {notify_status.get('cooldown_text', '')}\n"
            f"{discipline_text} | "
            f"最近推送：{notify_status.get('last_result_text', '')}（{notify_status.get('last_result_time', '--')}）"
        )
        if str(self._config.ai_api_key or "").strip():
            self.lbl_ai_status.setText(
                f"AI 已待命：{self._config.ai_model} | 手动触发即可根据当前快照生成简短研判。"
            )
        else:
            self.lbl_ai_status.setText("AI 未配置：请在“监控设置”里补充 AI 密钥后再手动触发研判。")
        self.metrics_panel.update_from_snapshot(snapshot)
        self.left_panel.update_from_snapshot(snapshot)
        self.insight_panel.update_from_snapshot(snapshot)
        self.right_table.update_from_snapshot(snapshot)
        

    def _append_log(self, message: str):
        if hasattr(self, 'left_panel'):
            self.left_panel.append_log(message)

    def _set_status_badge(self, text: str, tone: str):
        style_map = style.BADGE_STYLE_MAP
        self.lbl_status_badge.setText(text)
        self.lbl_status_badge.setStyleSheet(style_map.get(tone, style.STYLE_BADGE_NEUTRAL))

    def toggle_polling(self):
        self._polling_enabled = not self._polling_enabled
        if self._polling_enabled:
            self._timer.start(self._config.refresh_interval_sec * 1000)
            self.btn_poll.setText("暂停轮询")
            self._append_log("已恢复自动轮询。")
            self.refresh_snapshot()
        else:
            self._timer.stop()
            self.btn_poll.setText("恢复轮询")
            self._append_log("已暂停自动轮询。")

    def run_ai_brief(self):
        if self._ai_worker and self._ai_worker.isRunning():
            return
        if not self._last_snapshot:
            QMessageBox.information(self, "请先刷新", "请先获取一轮最新 MT5 快照，再执行 AI 研判。")
            return
        if not str(self._config.ai_api_key or "").strip():
            QMessageBox.warning(self, "AI 未配置", "当前未配置 AI 密钥，请先在“监控设置”里补充 AI 接口参数。")
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
            self._timer.start(self._config.refresh_interval_sec * 1000)
            self._append_log("监控设置已保存，正在按新配置刷新。")
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
        self.btn_refresh.setEnabled(True)
        self._last_snapshot = dict(snapshot or {})
        self._set_status_badge(snapshot.get("status_badge", "MT5 未连接"), snapshot.get("status_tone", "negative"))
        self.lbl_status_hint.setText(snapshot.get("status_hint", ""))
        notify_status = get_notification_status(self._config)
        discipline_text = f"当前纪律：{snapshot.get('event_risk_mode_text', '正常观察')}（{snapshot.get('event_risk_mode_source_text', '手动模式')}）"
        if str(snapshot.get("event_next_name", "") or "").strip() and str(snapshot.get("event_next_time_text", "") or "").strip():
            discipline_text += f" | 下个事件：{snapshot.get('event_next_name', '')}（{snapshot.get('event_next_time_text', '')}）"
        self.lbl_notify_status.setText(
            f"{notify_status.get('channels_text', '')} | {notify_status.get('cooldown_text', '')}\n"
            f"{discipline_text} | "
            f"最近推送：{notify_status.get('last_result_text', '')}（{notify_status.get('last_result_time', '--')}）"
        )
        if str(self._config.ai_api_key or "").strip():
            self.lbl_ai_status.setText(
                f"AI 已待命：{self._config.ai_model} | 手动触发即可根据当前快照生成简短研判。"
            )
        else:
            self.lbl_ai_status.setText("AI 未配置：请在“监控设置”里补充 AI 密钥后再手动触发研判。")
        alert_text = str(snapshot.get("alert_text", "") or "").strip()
        self.lbl_alert_banner.setText(alert_text)
        self.lbl_alert_banner.setVisible(bool(alert_text))
        self._update_trade_grade(snapshot)
        
        # update decoupled panels
        if hasattr(self, 'metrics_panel'):
            self.metrics_panel.update_from_snapshot(snapshot)
        if hasattr(self, 'insight_panel'):
            self.insight_panel.update_from_snapshot(snapshot)
        if hasattr(self, 'left_panel'):
            self.left_panel.update_from_snapshot(snapshot)
        if hasattr(self, 'right_table'):
            self.right_table.update_from_snapshot(snapshot)

        history_entries = build_snapshot_history_entries(snapshot)
        history_count = append_history_entries(history_entries)
        if history_count:
            self._append_log(f"[提醒留痕] 新增 {history_count} 条关键提醒。")
        notify_result = send_notifications(history_entries, self._config)
        for line in notify_result.get("messages", []):
            self._append_log(f"[消息推送] {line}")
        for line in notify_result.get("errors", []):
            self._append_log(f"[消息推送失败] {line}")
        if hasattr(self, 'left_panel'):
            self.left_panel.refresh_histories(snapshot)
        self._append_log(
            f"[{snapshot.get('last_refresh_text', '--')}] "
            f"{snapshot.get('event_risk_mode_text', '正常观察')}（{snapshot.get('event_risk_mode_source_text', '手动模式')}） | "
            f"{snapshot.get('trade_grade', '只适合观察')} | "
            f"{snapshot.get('live_digest', '暂无有效报价')}"
        )

    def _on_snapshot_error(self, message: str):
        self.btn_refresh.setEnabled(True)
        self._set_status_badge("刷新失败", "negative")
        self.lbl_status_hint.setText(str(message or "读取监控快照失败。"))
        self._append_log(f"[错误] {message}")
        QMessageBox.warning(self, "刷新失败", str(message or "读取监控快照失败。"))

    def _on_ai_brief_ready(self, result: dict):
        self.btn_ai.setEnabled(True)
        content = str(result.get("content", "") or "").strip()
        model = str(result.get("model", "") or "").strip()
        push_result = send_ai_brief_notification(result, self._last_snapshot, self._config)
        history_count = append_ai_history_entry(build_ai_history_entry(result, self._last_snapshot, push_result=push_result))
        self.txt_ai_brief.setPlainText(content or "模型已返回，但内容为空。")
        if push_result.get("messages"):
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，并已同步推送。")
        elif bool(self._config.ai_push_enabled):
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报，但推送未成功。")
        else:
            self.lbl_ai_status.setText(f"AI 研判完成：{model} 已生成最新简报。")
        if history_count:
            self._append_log("[AI留痕] 已记录本次 AI 研判结果。")
        self._append_log(f"[AI研判] {model} 已生成一份新的贵金属快照结论。")
        for line in push_result.get("messages", []):
            self._append_log(f"[AI推送] {line}")
        for line in push_result.get("errors", []):
            self._append_log(f"[AI推送失败] {line}")
        notify_status = get_notification_status(self._config)
        self.lbl_notify_status.setText(
            f"{notify_status.get('channels_text', '')} | {notify_status.get('cooldown_text', '')}\n"
            f"最近推送：{notify_status.get('last_result_text', '')}（{notify_status.get('last_result_time', '--')}）"
        )
        if hasattr(self, 'left_panel'):
            self.left_panel.refresh_histories(self._last_snapshot)
    def _on_ai_brief_error(self, message: str):
        self.btn_ai.setEnabled(True)
        error_text = str(message or "AI 研判失败。").strip()
        self.lbl_ai_status.setText(f"AI 研判失败：{error_text}")
        self._append_log(f"[AI研判失败] {error_text}")
        QMessageBox.warning(self, "AI 研判失败", error_text)

    def _update_trade_grade(self, snapshot: dict):
        grade = str(snapshot.get("trade_grade", "只适合观察") or "只适合观察").strip()
        detail = str(snapshot.get("trade_grade_detail", "先完成一轮快照刷新，再评估当前执行环境。") or "先完成一轮快照刷新，再评估当前执行环境。").strip()
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
        if self._worker and self._worker.isRunning():
            self._worker.wait(1000)
        if self._ai_worker and self._ai_worker.isRunning():
            self._ai_worker.wait(1000)
        shutdown_connection()
        super().closeEvent(event)
