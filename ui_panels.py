from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QTextEdit, QTabWidget, QVBoxLayout, QWidget, QHeaderView
)
import style
from alert_history import (
    read_recent_history, summarize_effectiveness, summarize_recent_history
)
from ai_history import (
    read_recent_ai_history, summarize_recent_ai_history
)

class DashboardMetricsPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        
        self.lbl_watch_count = self._build_metric_card("观察品种", "--")
        self.lbl_live_count = self._build_metric_card("实时报价", "--")
        self.lbl_refresh_time = self._build_metric_card("最近刷新", "--")
        
        layout.addWidget(self.lbl_watch_count)
        layout.addWidget(self.lbl_live_count)
        layout.addWidget(self.lbl_refresh_time)

    def _build_metric_card(self, title: str, value: str) -> QLabel:
        card = QLabel(f"{title}\n{value}")
        card.setAlignment(Qt.AlignCenter)
        card.setFixedHeight(86)
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def update_from_snapshot(self, snapshot: dict):
        self.lbl_watch_count.setText(f"观察品种\n{snapshot.get('watch_count', 0)}")
        self.lbl_live_count.setText(f"实时报价\n{snapshot.get('live_count', 0)}")
        self.lbl_refresh_time.setText(f"最近刷新\n{snapshot.get('last_refresh_text', '--')}")

class InsightPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        
        runtime_row = QHBoxLayout()
        runtime_row.setSpacing(12)
        
        self.runtime_status_label = self._build_panel_box(runtime_row, "MT5 连接状态")
        self.session_status_label = self._build_panel_box(runtime_row, "时段 / 休市提醒")
        layout.addLayout(runtime_row)
        
        insight_row = QHBoxLayout()
        insight_row.setSpacing(12)
        
        self.spread_focus_labels = self._build_multi_panel_box(insight_row, "点差高亮", 3)
        self.event_window_labels = self._build_multi_panel_box(insight_row, "事件窗口面板", 3)
        layout.addLayout(insight_row)

    def _build_panel_box(self, parent_layout, title_text):
        panel = QFrame()
        panel.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        title = QLabel(title_text)
        title.setStyleSheet(style.STYLE_CARD_TITLE)
        layout.addWidget(title)
        label = QLabel("等待首次刷新...")
        label.setWordWrap(True)
        label.setStyleSheet(style.STYLE_PANEL_NEUTRAL)
        layout.addWidget(label)
        parent_layout.addWidget(panel, 1)
        return label

    def _build_multi_panel_box(self, parent_layout, title_text, count):
        panel = QFrame()
        panel.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)
        title = QLabel(title_text)
        title.setStyleSheet(style.STYLE_CARD_TITLE)
        layout.addWidget(title)
        labels = []
        for _ in range(count):
            label = QLabel("等待首次刷新...")
            label.setWordWrap(True)
            label.setStyleSheet(style.STYLE_PANEL_NEUTRAL)
            layout.addWidget(label)
            labels.append(label)
        parent_layout.addWidget(panel, 1)
        return labels

    def _fill_focus_cards(self, labels, cards):
        tone_styles = style.PANEL_STYLE_MAP
        safe_cards = list(cards or [])
        while len(safe_cards) < len(labels):
            safe_cards.append({"title": "等待刷新", "detail": "当前暂无额外提示。", "tone": "neutral"})

        for label, card in zip(labels, safe_cards):
            title = str(card.get("title", "等待刷新") or "等待刷新").strip()
            detail = str(card.get("detail", "当前暂无额外提示。") or "当前暂无额外提示。").strip()
            tone = str(card.get("tone", "neutral") or "neutral").strip()
            label.setText(f"{title}\n{detail}")
            label.setStyleSheet(tone_styles.get(tone, style.STYLE_PANEL_NEUTRAL))

    def update_from_snapshot(self, snapshot: dict):
        runtime_cards = list(snapshot.get("runtime_status_cards", []) or [])
        self._fill_focus_cards(
            [self.runtime_status_label],
            [runtime_cards[0]] if runtime_cards else [{"title": "等待刷新", "detail": "正在读取 MT5 连接状态。", "tone": "neutral"}],
        )
        self._fill_focus_cards(
            [self.session_status_label],
            [runtime_cards[1]] if len(runtime_cards) > 1 else [{"title": "等待刷新", "detail": "正在判断当前是否休市或报价偏静。", "tone": "neutral"}],
        )
        self._fill_focus_cards(self.spread_focus_labels, snapshot.get("spread_focus_cards", []))
        self._fill_focus_cards(self.event_window_labels, snapshot.get("event_window_cards", []))


class LeftTabPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(style.STYLE_TAB_WIDGET)
        
        # Tab 1
        tab_overview = QWidget()
        tab_ov_lay = QVBoxLayout(tab_overview)
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
        self.txt_ai_brief.setPlainText("点击上方“AI 快速研判”后，这里会显示基于当前快照的简短中文结论。")
        tab_ov_lay.addWidget(self.txt_ai_brief, 3)
        self.tabs.addTab(tab_overview, "大盘与AI简报")
        
        # Tab 2
        tab_history = QWidget()
        tab_hist_lay = QVBoxLayout(tab_history)
        tab_hist_lay.setContentsMargins(8, 8, 8, 8)
        hist_metrics = QHBoxLayout()
        self.lbl_history_total = self._build_metric_card("近7天提醒", "--")
        self.lbl_history_spread = self._build_metric_card("点差异常", "--")
        self.lbl_history_effective = self._build_metric_card("有效提醒", "--")
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
        tab_hist_lay.addWidget(self.txt_history, 1)
        self.tabs.addTab(tab_history, "提醒留痕历史")
        
        # Tab 3
        tab_logs = QWidget()
        tab_logs_lay = QVBoxLayout(tab_logs)
        tab_logs_lay.setContentsMargins(8, 8, 8, 8)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet(style.STYLE_TEXT_LOG)
        tab_logs_lay.addWidget(self.txt_log, 1)
        self.tabs.addTab(tab_logs, "底层运行日志")
        
        layout.addWidget(self.tabs)
        self.refresh_histories()

    def _build_metric_card(self, title: str, value: str) -> QLabel:
        card = QLabel(f"{title}\n{value}")
        card.setAlignment(Qt.AlignCenter)
        card.setFixedHeight(60) # smaller
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def append_log(self, text: str):
        if text.strip():
            self.txt_log.append(text.strip())

    def update_from_snapshot(self, snapshot: dict):
        self.txt_summary.setPlainText(snapshot.get("summary_text", ""))
        self.refresh_histories(snapshot)

    def refresh_histories(self, snapshot: dict = None):
        # AI History
        stats_ai = summarize_recent_ai_history(days=7)
        recent_ai = read_recent_ai_history(limit=1)
        if not recent_ai:
            self.lbl_ai_history_summary.setText("最近还没有 AI 研判留痕。完成一次手动研判后，这里会显示最近一次结论和是否已推送。")
        else:
            latest = recent_ai[0]
            ls = str(latest.get("summary_line", "最近一次 AI 研判未返回摘要。") or "最近一次 AI 研判未返回摘要。").strip()
            lt = str(latest.get("occurred_at", "--") or "--").strip()
            lp = "已推送" if bool(latest.get("push_sent")) else "未推送"
            self.lbl_ai_history_summary.setText(f"{stats_ai.get('summary_text', '')}\n最近一次：{ls}（{lt}，{lp}）")
        
        # Alert History
        stats = summarize_recent_history(days=7)
        self.lbl_history_total.setText(f"近7天提醒\n{stats.get('total_count', 0)}")
        self.lbl_history_spread.setText(f"点差异常\n{stats.get('spread_count', 0)}")
        effectiveness = summarize_effectiveness(snapshot or {})
        self.lbl_history_effective.setText(f"有效提醒\n{effectiveness.get('effective_count', 0)}")
        latest_title = str(stats.get("latest_title", "暂无异常") or "暂无异常").strip()
        latest_time = str(stats.get("latest_time", "--") or "--").strip()
        self.lbl_history_summary.setText(f"{stats.get('summary_text', '')}\n最近一次：{latest_title}（{latest_time}）")
        self.lbl_effectiveness_summary.setText(
            f"{effectiveness.get('summary_text', '')}\n"
            f"最近一次进入评估窗口：{effectiveness.get('latest_title', '暂无可评估提醒')}（{effectiveness.get('latest_time', '--')}）"
        )

        entries = read_recent_history(limit=8)
        if not entries:
            self.txt_history.setPlainText("最近还没有需要留痕的提醒。后续出现点差放大、休市或重要宏观提醒时，会自动记录在这里。")
        else:
            lines = []
            for item in entries:
                oa = str(item.get("occurred_at", "--") or "--").strip()
                tt = str(item.get("title", "提醒") or "提醒").strip()
                dt = str(item.get("detail", "") or "").strip()
                lines.append(f"[{oa}] {tt}\n{dt}")
            self.txt_history.setPlainText("\n\n".join(lines))


class WatchListTable(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(style.STYLE_CARD_CONTAINER)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        title = QLabel("观察品种")
        title.setStyleSheet(style.STYLE_SECTION_TITLE)
        layout.addWidget(title)
        
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["品种", "最新价", "报价结构", "报价状态", "宏观提醒", "出手建议"])
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.setColumnWidth(0, 80)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 120)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table, 1)

    def update_from_snapshot(self, snapshot: dict):
        items = snapshot.get("items", [])
        self.table.setRowCount(len(items))
        tone_bg = style.TABLE_ROW_BG_MAP
        for row_index, item in enumerate(items):
            values = [
                item.get("symbol", "--"),
                item.get("latest_text", "--"),
                item.get("quote_text", "--"),
                item.get("status_text", "--"),
                item.get("macro_focus", "--"),
                item.get("execution_note", "--"),
            ]
            for col_index, value in enumerate(values):
                cell = QTableWidgetItem(str(value or "--"))
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                bg = tone_bg.get(str(item.get("tone", "neutral") or "neutral"), "#f8fafc")
                cell.setBackground(bg)
                self.table.setItem(row_index, col_index, cell)

