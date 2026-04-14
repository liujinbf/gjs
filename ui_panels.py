from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QTabWidget, QVBoxLayout, QWidget, QHeaderView
)
import style
from alert_history import (
    read_recent_history, summarize_effectiveness, summarize_recent_history
)
from ai_history import (
    read_recent_ai_history, summarize_recent_ai_history
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
            safe.append({"title": "暂无", "detail": "", "tone": "neutral"})
        for lbl, card in zip(labels, safe):
            title  = str(card.get("title", "") or "").strip()
            detail = str(card.get("detail", "") or "").strip()
            tone   = str(card.get("tone", "neutral") or "neutral").strip()
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
                   [rc[0]] if rc else [{"title": "等待刷新", "detail": "", "tone": "neutral"}])
        self._fill([self.session_status_label],
                   [rc[1]] if len(rc) > 1 else [{"title": "等待刷新", "detail": "", "tone": "neutral"}])
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
        if not recent_ai:
            self.lbl_ai_history_summary.setText("最近还没有 AI 研判留痕。完成一次手动研判后，这里会显示最近一次结论和是否已推送。")
        else:
            latest = recent_ai[0]
            ls = str(latest.get("summary_line", "最近一次 AI 研判未返回摘要。") or "最近一次 AI 研判未返回摘要。").strip()
            lt = str(latest.get("occurred_at", "--") or "--").strip()
            lp = "已推送" if bool(latest.get("push_sent")) else "未推送"
            self.lbl_ai_history_summary.setText(f"{stats_ai.get('summary_text', '')}\n最近一次：{ls}（{lt}，{lp}）")

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
    def __init__(self, parent=None):
        super().__init__(parent)
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
        self.table.setColumnWidth(2, 190)
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
        items = snapshot.get("items", [])
        snapshot_time = str(snapshot.get("last_refresh_text", "") or "").strip()
        self.table.setRowCount(len(items))
        tone_bg = style.TABLE_ROW_BG_MAP
        for row_index, item in enumerate(items):
            signal_side_text = str(item.get("signal_side_text", "") or "").strip()
            exec_note = str(item.get("execution_note", "--") or "--").strip()
            exec_display = f"{signal_side_text} {exec_note}".strip() if signal_side_text else exec_note
            transition = str(item.get("alert_state_transition_text", "") or "").strip()
            alert_cell = (
                f"{item.get('alert_state_text', '--')}\n{transition}"
                if transition else item.get("alert_state_text", "--")
            )
            values = [
                item.get("symbol", "--"),
                item.get("latest_text", "--"),
                item.get("quote_text", "--"),
                item.get("status_text", "--"),
                item.get("macro_focus", "--"),
                alert_cell,
                exec_display,
            ]
            for col_index, value in enumerate(values):
                cell = QTableWidgetItem(str(value or "--"))
                cell.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                bg_str = tone_bg.get(str(item.get("tone", "neutral") or "neutral"), "#f8fafc")
                cell.setBackground(QColor(bg_str))
                self.table.setItem(row_index, col_index, cell)
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
        try:
            from knowledge_feedback import record_user_feedback
            result = record_user_feedback(
                symbol=symbol,
                snapshot_id=int(target.get("snapshot_id", 0) or 0),
                snapshot_time=str(target.get("snapshot_time", "") or "").strip(),
                feedback_label=label,
                source="ui_quick",
            )
        except Exception as exc:  # noqa: BLE001
            self._lbl_feedback_hint.setText(f"⚠️ 【{symbol}】反馈记录失败：{str(exc) or '未知错误'}")
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
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)

        # 1. 顶部四大表盘
        top_bar = QHBoxLayout()
        top_bar.setSpacing(10)
        self.lbl_equity = self._build_card("可用净值 (Equity)", "$100,000.00")
        self.lbl_profit = self._build_card("累计盈亏 (Profit)", "$0.00", color="#475569")
        self.lbl_margin = self._build_card("已用保证金 (Margin)", "$0.00")
        self.lbl_win_rate = self._build_card("历史胜率 (Win Rate)", "--%")
        top_bar.addWidget(self.lbl_equity)
        top_bar.addWidget(self.lbl_profit)
        top_bar.addWidget(self.lbl_margin)
        top_bar.addWidget(self.lbl_win_rate)
        layout.addLayout(top_bar)

        # 2. 中部表格区分两列
        tables_lay = QHBoxLayout()
        tables_lay.setSpacing(12)

        # 左侧：持仓区
        left_layout = QVBoxLayout()
        left_layout.setSpacing(4)
        lbl_active = QLabel("🟢 正在持仓 (Open Positions)")
        lbl_active.setStyleSheet("font-weight: 800; font-size: 13px; color: #1e293b;")
        left_layout.addWidget(lbl_active)
        self.tbl_positions = QTableWidget(0, 7)
        self.tbl_positions.setHorizontalHeaderLabels(["标的", "方向", "手数", "入场价", "止损价", "止盈价", "浮动盈亏"])
        self.tbl_positions.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_positions.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_positions.setAlternatingRowColors(True)
        left_layout.addWidget(self.tbl_positions)
        tables_lay.addLayout(left_layout, 1)

        # 右侧：历史区
        right_layout = QVBoxLayout()
        right_layout.setSpacing(4)
        lbl_history = QLabel("📑 历史交割 (Trade History)")
        lbl_history.setStyleSheet("font-weight: 800; font-size: 13px; color: #1e293b;")
        right_layout.addWidget(lbl_history)
        self.tbl_history = QTableWidget(0, 6)
        self.tbl_history.setHorizontalHeaderLabels(["标的", "方向", "平仓价", "盈亏", "时间", "原因"])
        self.tbl_history.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tbl_history.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tbl_history.setAlternatingRowColors(True)
        right_layout.addWidget(self.tbl_history)
        tables_lay.addLayout(right_layout, 1)

        layout.addLayout(tables_lay)

    def _build_card(self, title: str, value: str, color: str = "#1d4ed8") -> QLabel:
        card = QLabel()
        card.setAlignment(Qt.AlignCenter)
        card.setFixedHeight(75)
        # 简单做个好看的富文本
        card.setText(f"<div style='font-size:12px;color:#64748b;margin-bottom:4px;'>{title}</div>"
                     f"<div style='font-size:18px;font-weight:800;color:{color};'>{value}</div>")
        card.setStyleSheet(style.STYLE_METRIC_CARD)
        return card

    def update_data(self):
        """拉取 SIM_ENGINE 渲染表格"""
        from mt5_sim_trading import SIM_ENGINE
        import sqlite3
        # 获取账户
        account = SIM_ENGINE.get_account()
        equity = float(account.get("equity", 100000.0))
        profit = float(account.get("total_profit", 0.0))
        margin = float(account.get("used_margin", 0.0))
        wins = int(account.get("win_count", 0))
        losses = int(account.get("loss_count", 0))
        total_trades = wins + losses
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
        
        profit_color = "#16a34a" if profit > 0 else ("#dc2626" if profit < 0 else "#475569")
        profit_sign = "+" if profit > 0 else ""

        self.lbl_equity.setText(f"<div style='font-size:12px;color:#64748b;'>可用净值 (Equity)</div><div style='font-size:18px;font-weight:800;color:#1e293b;'>${equity:,.2f}</div>")
        self.lbl_profit.setText(f"<div style='font-size:12px;color:#64748b;'>累计盈亏 (Profit)</div><div style='font-size:18px;font-weight:800;color:{profit_color};'>{profit_sign}${profit:,.2f}</div>")
        self.lbl_margin.setText(f"<div style='font-size:12px;color:#64748b;'>已用保证金 (Margin)</div><div style='font-size:18px;font-weight:800;color:#f59e0b;'>${margin:,.2f}</div>")
        self.lbl_win_rate.setText(f"<div style='font-size:12px;color:#64748b;'>历史胜率 (Win Rate)</div><div style='font-size:18px;font-weight:800;color:#1d4ed8;'>{win_rate:.1f}% ({wins}W/{losses}L)</div>")

        # 持仓表
        pos_list = SIM_ENGINE.get_open_positions()
        self.tbl_positions.setRowCount(0)
        for i, pos in enumerate(pos_list):
            self.tbl_positions.insertRow(i)
            pnl = float(pos["floating_pnl"])
            pnl_str = f"+${pnl:,.2f}" if pnl > 0 else f"-${abs(pnl):,.2f}"
            c_pnl = QColor("#e6ffe6") if pnl > 0 else (QColor("#ffe6e6") if pnl < 0 else QColor("#ffffff"))

            items = [
                pos["symbol"],
                "做多" if pos["action"] == "long" else "做空",
                f"{float(pos['quantity']):.2f}",
                f"{float(pos['entry_price']):.2f}",
                f"{float(pos['stop_loss']):.2f}",
                f"{float(pos['take_profit']):.2f}",
                pnl_str
            ]
            for col, val in enumerate(items):
                cell = QTableWidgetItem(val)
                cell.setTextAlignment(Qt.AlignCenter)
                if col == 6:  # 给盈亏上色
                    cell.setBackground(c_pnl)
                self.tbl_positions.setItem(i, col, cell)

        # 历史表 (抓取最后 50 条)
        try:
            with sqlite3.connect(SIM_ENGINE.db_file) as conn:
                conn.row_factory = sqlite3.Row
                history_rows = conn.execute("SELECT * FROM sim_trades ORDER BY id DESC LIMIT 50").fetchall()
        except sqlite3.OperationalError:
            history_rows = []

        self.tbl_history.setRowCount(0)
        for i, row in enumerate(history_rows):
            self.tbl_history.insertRow(i)
            profit = float(row["profit"])
            pnl_str = f"+${profit:,.2f}" if profit > 0 else f"-${abs(profit):,.2f}"
            c_pnl = QColor("#e6ffe6") if profit > 0 else (QColor("#ffe6e6") if profit < 0 else QColor("#ffffff"))
            time_short = str(row["closed_at"])[5:16] # MM-DD HH:MM

            items = [
                row["symbol"],
                "做多" if row["action"] == "long" else "做空",
                f"{float(row['exit_price']):.2f}",
                pnl_str,
                time_short,
                row["reason"]
            ]
            for col, val in enumerate(items):
                cell = QTableWidgetItem(str(val))
                cell.setTextAlignment(Qt.AlignCenter)
                if col == 3:  # 盈亏颜色
                    cell.setBackground(c_pnl)
                self.tbl_history.setItem(i, col, cell)

