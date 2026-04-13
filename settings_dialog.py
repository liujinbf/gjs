from PySide6.QtWidgets import (
    QApplication,
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
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app_config import EVENT_RISK_MODES, MetalMonitorConfig, extract_supported_symbols, save_runtime_config
from event_schedule import format_event_schedule_for_editor, normalize_event_schedule_text
from model_presets import MODEL_PRESETS, find_preset_name
from notification import get_notification_status, send_test_notification


def _is_anthropic_base_url(base_url: str) -> bool:
    return "anthropic.com" in str(base_url or "").strip().lower()


def _build_ai_test_request(base_url: str, api_key: str) -> tuple[str, dict[str, str]]:
    clean_base_url = str(base_url or "").strip().rstrip("/")
    if _is_anthropic_base_url(clean_base_url):
        return (
            f"{clean_base_url}/models",
            {
                "x-api-key": str(api_key or "").strip(),
                "anthropic-version": "2023-06-01",
            },
        )
    return (
        f"{clean_base_url}/models",
        {
            "Authorization": f"Bearer {str(api_key or '').strip()}",
        },
    )


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

        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("QTabWidget::pane { border: 1px solid #e2e8f0; border-radius: 8px; background: white; }")
        
        # --- Tab 1: 基础与MT5设置 ---
        tab_basic = QWidget()
        form_basic = QFormLayout(tab_basic)
        form_basic.setSpacing(10)
        
        self.entry_symbols = QLineEdit(",".join(self._config.symbols))
        self.entry_symbols.setPlaceholderText("例如：黄金, XAGUSD, 欧美")
        
        sym_layout = QHBoxLayout()
        sym_layout.setContentsMargins(0, 0, 0, 0)
        sym_layout.addWidget(self.entry_symbols, 1)
        self.btn_format_symbols = QPushButton("智能识别")
        self.btn_format_symbols.clicked.connect(self._format_symbols)
        # Use primary style for emphasis on this helpful action
        self.btn_format_symbols.setProperty("type", "primary")
        sym_layout.addWidget(self.btn_format_symbols)
        
        form_basic.addRow("观察品种：", sym_layout)

        self.spin_refresh = QSpinBox()
        self.spin_refresh.setRange(5, 600)
        self.spin_refresh.setValue(int(self._config.refresh_interval_sec))
        self.spin_refresh.setSuffix(" 秒")
        form_basic.addRow("复检间隔：", self.spin_refresh)

        self.entry_mt5_path = QLineEdit(self._config.mt5_path)
        self.entry_mt5_path.setPlaceholderText("可选：MT5 终端路径")
        form_basic.addRow("MT5 路径：", self.entry_mt5_path)

        self.entry_mt5_login = QLineEdit(self._config.mt5_login)
        form_basic.addRow("MT5 账号：", self.entry_mt5_login)

        self.entry_mt5_password = QLineEdit(self._config.mt5_password)
        self.entry_mt5_password.setEchoMode(QLineEdit.Password)
        form_basic.addRow("MT5 密码：", self.entry_mt5_password)

        self.entry_mt5_server = QLineEdit(self._config.mt5_server)
        form_basic.addRow("MT5 服务器：", self.entry_mt5_server)

        self.tabs.addTab(tab_basic, "基础与MT5设置")

        # --- Tab 2: 事件与提醒 ---
        tab_event = QWidget()
        form_event = QFormLayout(tab_event)
        form_event.setSpacing(10)

        self.combo_event_mode = QComboBox()
        for key, label in EVENT_RISK_MODES.items():
            self.combo_event_mode.addItem(label, userData=key)
        current_mode = str(self._config.event_risk_mode or "normal").strip().lower()
        idx = max(0, self.combo_event_mode.findData(current_mode))
        self.combo_event_mode.setCurrentIndex(idx)
        form_event.addRow("事件纪律模式：", self.combo_event_mode)

        self.chk_event_auto_mode = QCheckBox("根据事件计划自动切换事件纪律模式")
        self.chk_event_auto_mode.setChecked(bool(self._config.event_auto_mode_enabled))
        self.chk_event_auto_mode.toggled.connect(self._sync_event_auto_controls)
        form_event.addRow("自动事件模式：", self.chk_event_auto_mode)

        self.spin_event_pre_window = QSpinBox()
        self.spin_event_pre_window.setRange(5, 720)
        self.spin_event_pre_window.setValue(int(self._config.event_pre_window_min))
        self.spin_event_pre_window.setSuffix(" 分钟")
        form_event.addRow("事件前高敏窗口：", self.spin_event_pre_window)

        self.spin_event_post_window = QSpinBox()
        self.spin_event_post_window.setRange(5, 720)
        self.spin_event_post_window.setValue(int(self._config.event_post_window_min))
        self.spin_event_post_window.setSuffix(" 分钟")
        form_event.addRow("事件后观察窗口：", self.spin_event_post_window)

        self.txt_event_schedule = QTextEdit()
        self.txt_event_schedule.setPlaceholderText("每行一个事件...")
        self.txt_event_schedule.setFixedHeight(70)
        self.txt_event_schedule.setPlainText(format_event_schedule_for_editor(self._config.event_schedule_text))
        form_event.addRow("事件计划：", self.txt_event_schedule)

        self.chk_event_feed_enabled = QCheckBox("从外部 JSON 事件源自动同步事件计划")
        self.chk_event_feed_enabled.setChecked(bool(getattr(self._config, "event_feed_enabled", False)))
        self.chk_event_feed_enabled.toggled.connect(self._sync_event_feed_controls)
        form_event.addRow("外部事件源：", self.chk_event_feed_enabled)

        self.entry_event_feed_url = QLineEdit(str(getattr(self._config, "event_feed_url", "") or "").strip())
        form_event.addRow("事件源地址：", self.entry_event_feed_url)

        self.spin_event_feed_refresh = QSpinBox()
        self.spin_event_feed_refresh.setRange(5, 1440)
        self.spin_event_feed_refresh.setValue(int(getattr(self._config, "event_feed_refresh_min", 60) or 60))
        self.spin_event_feed_refresh.setSuffix(" 分钟")
        form_event.addRow("事件源缓存：", self.spin_event_feed_refresh)

        self.tabs.addTab(tab_event, "事件与提醒")

        # --- Tab 3: AI与推送 ---
        tab_ai = QWidget()
        form_ai = QFormLayout(tab_ai)
        form_ai.setSpacing(10)

        self.combo_vendor = QComboBox()
        self.combo_vendor.addItems(list(MODEL_PRESETS.keys()))
        self.combo_vendor.currentTextChanged.connect(self._on_vendor_changed)
        form_ai.addRow("模型预设：", self.combo_vendor)

        self.entry_ai_base = QLineEdit(self._config.ai_api_base)
        form_ai.addRow("AI 接口地址：", self.entry_ai_base)

        self.entry_ai_model = QLineEdit(self._config.ai_model)
        form_ai.addRow("AI 模型：", self.entry_ai_model)

        self.entry_ai_key = QLineEdit(self._config.ai_api_key)
        self.entry_ai_key.setEchoMode(QLineEdit.Password)
        
        key_layout = QHBoxLayout()
        key_layout.setContentsMargins(0, 0, 0, 0)
        key_layout.addWidget(self.entry_ai_key, 1)
        self.btn_test_ai_key = QPushButton("测试密钥")
        self.btn_test_ai_key.clicked.connect(self._test_ai_key)
        self.btn_test_ai_key.setProperty("type", "primary") # Use global primary style
        key_layout.addWidget(self.btn_test_ai_key)
        
        form_ai.addRow("AI 密钥：", key_layout)
        self.lbl_ai_key_link = QLabel("")
        self.lbl_ai_key_link.setOpenExternalLinks(True)
        form_ai.addRow("", self.lbl_ai_key_link)

        self.spin_ai_auto_interval = QSpinBox()
        self.spin_ai_auto_interval.setRange(0, 1440)
        self.spin_ai_auto_interval.setValue(int(getattr(self._config, "ai_auto_interval_min", 0) or 0))
        self.spin_ai_auto_interval.setSuffix(" 分钟（0=关闭自动）")
        form_ai.addRow("AI 自动研判：", self.spin_ai_auto_interval)

        self.chk_ai_push_enabled = QCheckBox("手动触发 AI 研判后，同步推送到外部渠道")
        self.chk_ai_push_enabled.setChecked(bool(self._config.ai_push_enabled))
        self.chk_ai_push_enabled.toggled.connect(self._sync_ai_push_controls)
        form_ai.addRow("AI 推送：", self.chk_ai_push_enabled)

        self.chk_ai_push_summary_only = QCheckBox("仅推送 AI 摘要，避免长文本刷屏")
        self.chk_ai_push_summary_only.setChecked(bool(self._config.ai_push_summary_only))
        form_ai.addRow("推送内容：", self.chk_ai_push_summary_only)

        self.entry_webhook = QLineEdit(self._config.dingtalk_webhook)
        self.entry_webhook.setPlaceholderText("钉钉 Webhook")
        form_ai.addRow("钉钉 Webhook：", self.entry_webhook)

        self.entry_pushplus = QLineEdit(self._config.pushplus_token)
        self.entry_pushplus.setPlaceholderText("PushPlus Token")
        form_ai.addRow("PushPlus Token：", self.entry_pushplus)

        self.spin_notify_cooldown = QSpinBox()
        self.spin_notify_cooldown.setRange(5, 1440)
        self.spin_notify_cooldown.setValue(int(self._config.notify_cooldown_min))
        self.spin_notify_cooldown.setSuffix(" 分钟")
        form_ai.addRow("推送冷却：", self.spin_notify_cooldown)

        self.tabs.addTab(tab_ai, "AI与推送")

        layout.addWidget(self.tabs)

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
        self.chk_event_feed_enabled.setEnabled(enabled)
        self._sync_event_feed_controls()

    def _sync_event_feed_controls(self):
        enabled = bool(self.chk_event_auto_mode.isChecked()) and bool(self.chk_event_feed_enabled.isChecked())
        self.entry_event_feed_url.setEnabled(enabled)
        self.spin_event_feed_refresh.setEnabled(enabled)

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
            ai_auto_interval_min=max(0, int(self.spin_ai_auto_interval.value())),
            event_auto_mode_enabled=bool(self.chk_event_auto_mode.isChecked()),
            event_schedule_text=normalize_event_schedule_text(self.txt_event_schedule.toPlainText()),
            event_pre_window_min=int(self.spin_event_pre_window.value()),
            event_post_window_min=int(self.spin_event_post_window.value()),
            event_feed_enabled=bool(self.chk_event_feed_enabled.isChecked()),
            event_feed_url=self.entry_event_feed_url.text().strip(),
            event_feed_refresh_min=int(self.spin_event_feed_refresh.value()),
            macro_news_feed_enabled=bool(getattr(self._config, "macro_news_feed_enabled", False)),
            macro_news_feed_urls=str(getattr(self._config, "macro_news_feed_urls", "") or "").strip(),
            macro_news_feed_refresh_min=int(getattr(self._config, "macro_news_feed_refresh_min", 30) or 30),
            macro_data_feed_enabled=bool(getattr(self._config, "macro_data_feed_enabled", False)),
            macro_data_feed_specs=str(getattr(self._config, "macro_data_feed_specs", "") or "").strip(),
            macro_data_feed_refresh_min=int(getattr(self._config, "macro_data_feed_refresh_min", 60) or 60),
            learning_push_enabled=bool(getattr(self._config, "learning_push_enabled", False)),
            learning_push_min_interval_hour=int(getattr(self._config, "learning_push_min_interval_hour", 12) or 12),
        )

    def _test_ai_key(self):
        import json
        from urllib import request as url_request
        from urllib.error import HTTPError, URLError

        base_url = self.entry_ai_base.text().strip() or "https://api.siliconflow.cn/v1"
        model = self.entry_ai_model.text().strip() or "deepseek-ai/DeepSeek-R1"
        api_key = self.entry_ai_key.text().strip()
        
        if not api_key:
            QMessageBox.warning(self, "测试失败", "请先输入 AI 密钥再进行测试。")
            return
            
        self.btn_test_ai_key.setEnabled(False)
        self.btn_test_ai_key.setText("测试中...")
        QApplication.processEvents()

        try:
            url, headers = _build_ai_test_request(base_url, api_key)
            req = url_request.Request(url, method="GET")
            for key, value in headers.items():
                req.add_header(key, value)
             
            with url_request.urlopen(req, timeout=10) as resp:
                json.loads(resp.read().decode('utf-8'))
                 
            QMessageBox.information(
                self, 
                "测试成功", 
                f"🎉 API 密钥验证通过！\n成功连接到接口。\n\n您配置的模型为：\n{model}"
            )
        except HTTPError as e:
            if e.code == 401:
                QMessageBox.warning(self, "测试失败", "API 密钥无效或已过期 (HTTP 401)。\n请检查您输入的字母是否正确或是否带有空格。")
            elif e.code == 404:
                # Some providers don't have a /models endpoint, fallback test
                QMessageBox.warning(self, "测试失败", "接口地址可能不正确 (HTTP 404)，找不到模型列表端点。")
            else:
                QMessageBox.warning(self, "测试失败", f"接口返回错误代码：{e.code}\n{e.reason}")
        except URLError as e:
            QMessageBox.warning(self, "测试失败", f"无法连接到 API 地址：\n{base_url}\n\n原因：{str(e)}")
        except Exception as e:
            QMessageBox.warning(self, "测试失败", f"发生未知错误：\n{str(e)}")
        finally:
            self.btn_test_ai_key.setEnabled(True)
            self.btn_test_ai_key.setText("测试密钥")

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
            f"外部事件源：{'已开启' if bool(getattr(runtime_config, 'event_feed_enabled', False)) else '未开启'} | "
            f"AI 配置：{'已配置' if bool((runtime_config.ai_api_key or '').strip()) else '未配置'} | "
            f"{runtime_config.ai_model or 'deepseek-ai/DeepSeek-R1'} | "
            f"AI推送：{'已开启' if bool(runtime_config.ai_push_enabled) else '未开启'} | "
            f"AI自动间隔：{'每 ' + str(runtime_config.ai_auto_interval_min) + ' 分钟' if int(runtime_config.ai_auto_interval_min) > 0 else '手动触发'}"
        )

    def _format_symbols(self):
        """将输入框内用户填写的任意别名转换为平台标准MT5代码，供用户保存前确认"""
        raw_text = self.entry_symbols.text()
        if not raw_text.strip():
            return
        
        cleaned = extract_supported_symbols(raw_text)
        if cleaned:
            self.entry_symbols.setText(", ".join(cleaned))
        else:
            QMessageBox.information(self, "识别提示", "未识别到有效的品种名称或输入为空。")

    def _save(self):
        symbols = extract_supported_symbols(self.entry_symbols.text())
        if not symbols:
            QMessageBox.warning(self, "保存失败", "请至少保留一个合法观察品种，例如 XAUUSD 或 EURUSD。")
            return
        if self.chk_event_feed_enabled.isChecked() and not self.entry_event_feed_url.text().strip():
            QMessageBox.warning(self, "保存失败", "已开启外部事件源，请填写合法的 JSON 地址或本地文件路径。")
            return
        if (
            self.chk_event_auto_mode.isChecked()
            and not normalize_event_schedule_text(self.txt_event_schedule.toPlainText())
            and not (self.chk_event_feed_enabled.isChecked() and self.entry_event_feed_url.text().strip())
        ):
            QMessageBox.warning(self, "保存失败", "已开启自动事件模式，请至少填写一条合法事件计划。")
            return

        new_config = self._build_runtime_config()
        save_runtime_config(new_config)
        self._config = new_config
        self.accept()

    @property
    def runtime_config(self) -> MetalMonitorConfig:
        return self._config
