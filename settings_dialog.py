from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from app_config import EVENT_RISK_MODES, MetalMonitorConfig, extract_supported_symbols, save_runtime_config
from event_schedule import format_event_schedule_for_editor, normalize_event_schedule_text
from model_presets import MODEL_PRESETS, find_preset_name
from notification import get_notification_status, send_test_notification


class MetalSettingsDialog(QDialog):
    def __init__(self, config: MetalMonitorConfig, parent=None):
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("贵金属监控设置")
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        tip = QLabel("当前项目只服务于贵金属 / 宏观品种监控。MT5、推送和 AI 关键配置会优先沿用老项目，后续只需要在这里维护。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#475569;font-size:12px;")
        layout.addWidget(tip)
        self.lbl_notify_status = QLabel("")
        self.lbl_notify_status.setWordWrap(True)
        self.lbl_notify_status.setStyleSheet("background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;padding:10px;color:#334155;font-size:12px;line-height:1.5;")
        layout.addWidget(self.lbl_notify_status)

        form = QFormLayout()
        form.setSpacing(10)

        self.combo_vendor = QComboBox()
        self.combo_vendor.addItems(list(MODEL_PRESETS.keys()))
        self.combo_vendor.currentTextChanged.connect(self._on_vendor_changed)
        form.addRow("模型预设：", self.combo_vendor)

        self.entry_symbols = QLineEdit(",".join(self._config.symbols))
        self.entry_symbols.setPlaceholderText("例如：XAUUSD,XAGUSD,EURUSD,USDJPY")
        form.addRow("观察品种：", self.entry_symbols)

        self.spin_refresh = QSpinBox()
        self.spin_refresh.setRange(5, 600)
        self.spin_refresh.setValue(int(self._config.refresh_interval_sec))
        self.spin_refresh.setSuffix(" 秒")
        form.addRow("复检间隔：", self.spin_refresh)

        self.combo_event_mode = QComboBox()
        for key, label in EVENT_RISK_MODES.items():
            self.combo_event_mode.addItem(label, userData=key)
        current_mode = str(self._config.event_risk_mode or "normal").strip().lower()
        idx = max(0, self.combo_event_mode.findData(current_mode))
        self.combo_event_mode.setCurrentIndex(idx)
        form.addRow("事件纪律模式：", self.combo_event_mode)

        self.chk_event_auto_mode = QCheckBox("根据事件计划自动切换事件纪律模式")
        self.chk_event_auto_mode.setChecked(bool(self._config.event_auto_mode_enabled))
        self.chk_event_auto_mode.toggled.connect(self._sync_event_auto_controls)
        form.addRow("自动事件模式：", self.chk_event_auto_mode)

        self.spin_event_pre_window = QSpinBox()
        self.spin_event_pre_window.setRange(5, 720)
        self.spin_event_pre_window.setValue(int(self._config.event_pre_window_min))
        self.spin_event_pre_window.setSuffix(" 分钟")
        form.addRow("事件前高敏窗口：", self.spin_event_pre_window)

        self.spin_event_post_window = QSpinBox()
        self.spin_event_post_window.setRange(5, 720)
        self.spin_event_post_window.setValue(int(self._config.event_post_window_min))
        self.spin_event_post_window.setSuffix(" 分钟")
        form.addRow("事件后观察窗口：", self.spin_event_post_window)

        self.txt_event_schedule = QTextEdit()
        self.txt_event_schedule.setPlaceholderText("每行一个事件，例如：\n2026-04-15 20:30|美国 CPI\n2026-04-16 02:00|联储利率决议")
        self.txt_event_schedule.setFixedHeight(96)
        self.txt_event_schedule.setPlainText(format_event_schedule_for_editor(self._config.event_schedule_text))
        form.addRow("事件计划：", self.txt_event_schedule)

        self.entry_mt5_path = QLineEdit(self._config.mt5_path)
        self.entry_mt5_path.setPlaceholderText("可选：MT5 终端路径，例如 C:\\Program Files\\MetaTrader 5\\terminal64.exe")
        form.addRow("MT5 路径：", self.entry_mt5_path)

        self.entry_mt5_login = QLineEdit(self._config.mt5_login)
        form.addRow("MT5 账号：", self.entry_mt5_login)

        self.entry_mt5_password = QLineEdit(self._config.mt5_password)
        self.entry_mt5_password.setEchoMode(QLineEdit.Password)
        form.addRow("MT5 密码：", self.entry_mt5_password)

        self.entry_mt5_server = QLineEdit(self._config.mt5_server)
        form.addRow("MT5 服务器：", self.entry_mt5_server)

        self.entry_webhook = QLineEdit(self._config.dingtalk_webhook)
        self.entry_webhook.setPlaceholderText("可选：钉钉机器人 Webhook")
        form.addRow("钉钉 Webhook：", self.entry_webhook)

        self.entry_pushplus = QLineEdit(self._config.pushplus_token)
        self.entry_pushplus.setPlaceholderText("可选：PushPlus Token")
        form.addRow("PushPlus Token：", self.entry_pushplus)

        self.spin_notify_cooldown = QSpinBox()
        self.spin_notify_cooldown.setRange(5, 1440)
        self.spin_notify_cooldown.setValue(int(self._config.notify_cooldown_min))
        self.spin_notify_cooldown.setSuffix(" 分钟")
        form.addRow("推送冷却：", self.spin_notify_cooldown)

        self.entry_ai_base = QLineEdit(self._config.ai_api_base)
        self.entry_ai_base.setPlaceholderText("可选：AI 接口地址，例如 https://api.siliconflow.cn/v1")
        form.addRow("AI 接口地址：", self.entry_ai_base)

        self.entry_ai_model = QLineEdit(self._config.ai_model)
        self.entry_ai_model.setPlaceholderText("可选：模型名，例如 deepseek-ai/DeepSeek-R1")
        form.addRow("AI 模型：", self.entry_ai_model)

        self.entry_ai_key = QLineEdit(self._config.ai_api_key)
        self.entry_ai_key.setEchoMode(QLineEdit.Password)
        self.entry_ai_key.setPlaceholderText("可选：AI_API_KEY")
        form.addRow("AI 密钥：", self.entry_ai_key)
        self.lbl_ai_key_link = QLabel("")
        self.lbl_ai_key_link.setOpenExternalLinks(True)
        self.lbl_ai_key_link.setWordWrap(True)
        self.lbl_ai_key_link.setStyleSheet("color:#3b82f6;font-size:11px;padding:2px 0 0 2px;")
        form.addRow("", self.lbl_ai_key_link)

        self.chk_ai_push_enabled = QCheckBox("手动触发 AI 研判后，同步推送到外部消息渠道")
        self.chk_ai_push_enabled.setChecked(bool(self._config.ai_push_enabled))
        self.chk_ai_push_enabled.toggled.connect(self._sync_ai_push_controls)
        form.addRow("AI 推送：", self.chk_ai_push_enabled)

        self.chk_ai_push_summary_only = QCheckBox("仅推送 AI 摘要，避免长文本刷屏")
        self.chk_ai_push_summary_only.setChecked(bool(self._config.ai_push_summary_only))
        form.addRow("推送内容：", self.chk_ai_push_summary_only)

        layout.addLayout(form)

        btn_row = QHBoxLayout()
        test_btn = QPushButton("测试消息推送")
        test_btn.clicked.connect(self._test_notification)
        btn_row.addWidget(test_btn)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet("background-color:#2563eb;color:white;font-weight:bold;padding:8px 16px;border-radius:8px;")
        cancel_btn.clicked.connect(self.reject)
        save_btn.clicked.connect(self._save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        self.combo_vendor.setCurrentText(find_preset_name(self.entry_ai_base.text(), self.entry_ai_model.text()))
        self._on_vendor_changed(self.combo_vendor.currentText())
        self._sync_ai_push_controls()
        self._sync_event_auto_controls()
        self._refresh_notify_status()

    def _on_vendor_changed(self, text: str):
        preset = MODEL_PRESETS.get(str(text or "").strip())
        if not preset:
            return
        if text != "【自定义配置】":
            self.entry_ai_base.setText(str(preset.get("url", "") or "").strip())
            self.entry_ai_model.setText(str(preset.get("model", "") or "").strip())
        link = str(preset.get("link", "") or "").strip()
        if link:
            self.lbl_ai_key_link.setText(f"<a href='{link}'>点击前往官方申请或管理该模型的 API Key</a>")
        else:
            self.lbl_ai_key_link.setText("")
        self._refresh_notify_status()

    def _sync_ai_push_controls(self):
        self.chk_ai_push_summary_only.setEnabled(bool(self.chk_ai_push_enabled.isChecked()))

    def _sync_event_auto_controls(self):
        enabled = bool(self.chk_event_auto_mode.isChecked())
        self.spin_event_pre_window.setEnabled(enabled)
        self.spin_event_post_window.setEnabled(enabled)
        self.txt_event_schedule.setEnabled(enabled)

    def _build_runtime_config(self) -> MetalMonitorConfig:
        return MetalMonitorConfig(
            symbols=extract_supported_symbols(self.entry_symbols.text()),
            refresh_interval_sec=int(self.spin_refresh.value()),
            event_risk_mode=str(self.combo_event_mode.currentData() or "normal"),
            mt5_path=self.entry_mt5_path.text().strip(),
            mt5_login=self.entry_mt5_login.text().strip(),
            mt5_password=self.entry_mt5_password.text().strip(),
            mt5_server=self.entry_mt5_server.text().strip(),
            dingtalk_webhook=self.entry_webhook.text().strip(),
            pushplus_token=self.entry_pushplus.text().strip(),
            notify_cooldown_min=int(self.spin_notify_cooldown.value()),
            ai_api_key=self.entry_ai_key.text().strip(),
            ai_api_base=self.entry_ai_base.text().strip() or "https://api.siliconflow.cn/v1",
            ai_model=self.entry_ai_model.text().strip() or "deepseek-ai/DeepSeek-R1",
            ai_push_enabled=bool(self.chk_ai_push_enabled.isChecked()),
            ai_push_summary_only=bool(self.chk_ai_push_summary_only.isChecked()),
            event_auto_mode_enabled=bool(self.chk_event_auto_mode.isChecked()),
            event_schedule_text=normalize_event_schedule_text(self.txt_event_schedule.toPlainText()),
            event_pre_window_min=int(self.spin_event_pre_window.value()),
            event_post_window_min=int(self.spin_event_post_window.value()),
        )

    def _test_notification(self):
        config = self._build_runtime_config()
        result = send_test_notification(config)
        messages = list(result.get("messages", []))
        errors = list(result.get("errors", []))
        self._refresh_notify_status(config)
        if errors and not messages:
            QMessageBox.warning(self, "测试失败", "\n".join(errors))
            return
        text_parts = []
        if messages:
            text_parts.append("\n".join(messages))
        if errors:
            text_parts.append("\n".join(errors))
        QMessageBox.information(self, "测试完成", "\n".join(text_parts))

    def _refresh_notify_status(self, config: MetalMonitorConfig | None = None):
        runtime_config = config or self._build_runtime_config()
        status = get_notification_status(runtime_config)
        self.lbl_notify_status.setText(
            f"{status.get('channels_text', '')} | {status.get('cooldown_text', '')}\n"
            f"最近结果：{status.get('last_result_text', '')}（{status.get('last_result_time', '--')}）\n"
            f"事件纪律：{EVENT_RISK_MODES.get(runtime_config.event_risk_mode, '正常观察')} | "
            f"自动事件：{'已开启' if bool(runtime_config.event_auto_mode_enabled) else '未开启'} | "
            f"AI 配置：{'已配置' if bool((runtime_config.ai_api_key or '').strip()) else '未配置'} | "
            f"{runtime_config.ai_model or 'deepseek-ai/DeepSeek-R1'} | "
            f"AI推送：{'已开启' if bool(runtime_config.ai_push_enabled) else '未开启'}"
        )

    def _save(self):
        symbols = extract_supported_symbols(self.entry_symbols.text())
        if not symbols:
            QMessageBox.warning(self, "保存失败", "请至少保留一个合法观察品种，例如 XAUUSD 或 EURUSD。")
            return
        if self.chk_event_auto_mode.isChecked() and not normalize_event_schedule_text(self.txt_event_schedule.toPlainText()):
            QMessageBox.warning(self, "保存失败", "已开启自动事件模式，请至少填写一条合法事件计划。")
            return

        new_config = self._build_runtime_config()
        save_runtime_config(new_config)
        self._config = new_config
        self.accept()

    @property
    def runtime_config(self) -> MetalMonitorConfig:
        return self._config
