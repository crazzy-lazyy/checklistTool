# -*- coding: utf-8 -*-
"""
AI 配置页：用户在此配置内网 LLM API 的连接参数。
支持保存到本地配置文件、测试连接。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QPushButton,
    QLabel,
    QLineEdit,
    QSpinBox,
    QComboBox,
    QMessageBox,
    QInputDialog,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

from core.ai.ai_client import AI_CONFIG_FILE, AIClient, invalidate_client
from config import get_rules_edit_key
from ui.widgets import card_group


class _ConnectivityWorker(QThread):
    """后台测试 AI 服务连通性。"""
    finished = pyqtSignal(bool, str)  # ok, message

    def __init__(self, base_url: str, api_key: str, timeout: int):
        super().__init__()
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    def run(self):
        try:
            client = AIClient(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
                max_retries=0,
            )
            ok = client.check_connectivity()
            if ok:
                self.finished.emit(True, "连接成功！AI 服务可达。")
            else:
                self.finished.emit(False, "无法连接到 AI 服务，请检查地址和网络。")
        except Exception as e:
            self.finished.emit(False, f"连接测试失败：{e}")


class TabAIConfig(QWidget):
    """AI 配置选项卡。编辑功能需管理员秘钥解锁。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._edit_unlocked = False
        self._editable_widgets = []
        self._setup_ui()
        self._load_saved_config()
        self._apply_edit_lock()

    # ------------------------------------------------------------------
    # 编辑锁
    # ------------------------------------------------------------------
    def _apply_edit_lock(self):
        """根据解锁状态切换表单控件和按钮的可用性。"""
        for w in self._editable_widgets:
            w.setEnabled(self._edit_unlocked)
        self.save_btn.setEnabled(self._edit_unlocked)
        self.lock_btn.setText("🔒 已锁定（点击解锁）" if not self._edit_unlocked else "🔓 已解锁（点击锁定）")

    def _toggle_unlock(self):
        """切换编辑锁定状态：锁定时要求输入秘钥。"""
        if self._edit_unlocked:
            self._edit_unlocked = False
            self._apply_edit_lock()
            return

        key, ok = QInputDialog.getText(
            self, "管理员验证", "请输入管理员秘钥：",
            echo=QLineEdit.EchoMode.Password,
        )
        if not ok or not key:
            return

        expected = get_rules_edit_key()
        if key.strip() == expected:
            self._edit_unlocked = True
            self._apply_edit_lock()
        else:
            QMessageBox.warning(self, "提示", "秘钥错误，无法解锁编辑。")

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # ── 编辑锁按钮 ──
        lock_row = QHBoxLayout()
        self.lock_btn = QPushButton("🔒 已锁定（点击解锁）")
        self.lock_btn.clicked.connect(self._toggle_unlock)
        lock_row.addWidget(self.lock_btn)
        lock_row.addStretch()
        layout.addLayout(lock_row)

        # ── API 设置 ──
        g_api, _api_lay = card_group("API 设置")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        _api_lay.addLayout(form)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://api.deepseek.com/v1  或  http://192.168.1.100:8000/v1")
        form.addRow("API 地址：", self.url_edit)
        self._editable_widgets.append(self.url_edit)

        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("sk-xxx")
        key_row.addWidget(self.key_edit, 1)
        self.show_key_btn = QPushButton("显示")
        self.show_key_btn.setCheckable(True)
        self.show_key_btn.setFixedWidth(60)
        self.show_key_btn.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_btn)
        form.addRow("API 密钥：", key_row)
        self._editable_widgets.append(self.key_edit)
        self._editable_widgets.append(self.show_key_btn)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("例如 deepseek-v3")
        form.addRow("模型名称：", self.model_edit)
        self._editable_widgets.append(self.model_edit)

        self.reasoning_combo = QComboBox()
        self.reasoning_combo.addItem("默认（由模型决定）", "")
        self.reasoning_combo.addItem("关闭思考", "none")
        self.reasoning_combo.addItem("低", "low")
        self.reasoning_combo.addItem("中", "medium")
        self.reasoning_combo.addItem("高", "high")
        self.reasoning_combo.setToolTip(
            "thinking 模型（如 qwen3.5、deepseek-r1）的思考过程会消耗 token。\n"
            "开启推理时建议同时增大下方的 max_tokens（如 4096~8192），\n"
            "否则思考可能耗尽全部 token 导致回复为空。"
        )
        form.addRow("推理力度：", self.reasoning_combo)
        self._editable_widgets.append(self.reasoning_combo)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(256, 32768)
        self.max_tokens_spin.setValue(2048)
        self.max_tokens_spin.setSingleStep(256)
        self.max_tokens_spin.setToolTip(
            "单次请求最大生成 token 数。thinking 模型建议 4096+。"
        )
        form.addRow("Max Tokens：", self.max_tokens_spin)
        self._editable_widgets.append(self.max_tokens_spin)

        timeout_row = QHBoxLayout()
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(5, 300)
        self.timeout_spin.setValue(30)
        self.timeout_spin.setSuffix(" 秒")
        timeout_row.addWidget(self.timeout_spin)
        timeout_row.addWidget(QLabel("重试次数："))
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 10)
        self.retries_spin.setValue(2)
        self.retries_spin.setSuffix(" 次")
        timeout_row.addWidget(self.retries_spin)
        timeout_row.addStretch()
        form.addRow("超时与重试：", timeout_row)
        self._editable_widgets.append(self.timeout_spin)
        self._editable_widgets.append(self.retries_spin)

        layout.addWidget(g_api)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        self.test_btn = QPushButton("测试连接")
        self.test_btn.clicked.connect(self._test_connection)
        btn_row.addWidget(self.test_btn)

        self.save_btn = QPushButton("保存配置")
        self.save_btn.clicked.connect(self._save_config)
        btn_row.addWidget(self.save_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── 状态条 ──
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #666;")
        layout.addWidget(self.status_label)

        # ── 使用说明 ──
        g_help, _help_lay = card_group("使用说明")
        help_text = QLabel(
            "• 支持任何兼容 OpenAI chat/completions 协议的 API（vLLM、Ollama、LocalAI 等）。\n"
            "• API 密钥仅保存在本地的 user_data/ai_config.json 文件中，不会上传。\n"
            "• 环境变量 CHECKLISTTOOL_AI_* 可作为初始默认值，GUI 中保存的配置优先。\n"
            "• 测试连接会发送一条简短对话请求（已自动关闭推理），验证模型是否能正常加载和响应。\n"
            "• 推理力度：使用 thinking 模型（如 qwen3.5、deepseek-r1）时建议选「关闭思考」，\n"
            "  否则大量 token 会被思考过程消耗，导致实际回复为空。"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #777; font-size: 11px; background: transparent;")
        _help_lay.addWidget(help_text)
        layout.addWidget(g_help)

        layout.addStretch()

    # ------------------------------------------------------------------
    # 配置读写
    # ------------------------------------------------------------------
    def _load_saved_config(self):
        """加载已保存的配置到表单。"""
        cfg = AIClient.load_config()
        if cfg:
            self.url_edit.setText(str(cfg.get("base_url", "")))
            self.key_edit.setText(str(cfg.get("api_key", "")))
            self.model_edit.setText(str(cfg.get("model", "")))
            try:
                self.timeout_spin.setValue(int(cfg.get("timeout", 30)))
            except (ValueError, TypeError):
                self.timeout_spin.setValue(30)
            try:
                self.retries_spin.setValue(int(cfg.get("max_retries", 2)))
            except (ValueError, TypeError):
                self.retries_spin.setValue(2)
            # reasoning_effort
            _re = str(cfg.get("reasoning_effort", ""))
            _idx = self.reasoning_combo.findData(_re)
            if _idx >= 0:
                self.reasoning_combo.setCurrentIndex(_idx)
            # max_tokens
            try:
                self.max_tokens_spin.setValue(int(cfg.get("max_tokens", 2048)))
            except (ValueError, TypeError):
                self.max_tokens_spin.setValue(2048)
            self.status_label.setText(f"已加载保存的配置（{AI_CONFIG_FILE}）")
        else:
            self.status_label.setText("尚未保存过配置，将使用环境变量或默认值。")

    def _save_config(self):
        """保存当前表单内容到配置文件。"""
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "配置已锁定，请先点击「🔒 已锁定」按钮并输入管理员秘钥解锁。")
            return
        cfg = {
            "base_url": self.url_edit.text().strip(),
            "api_key": self.key_edit.text().strip(),
            "model": self.model_edit.text().strip(),
            "timeout": self.timeout_spin.value(),
            "max_retries": self.retries_spin.value(),
            "max_tokens": self.max_tokens_spin.value(),
            "reasoning_effort": self.reasoning_combo.currentData() or "",
        }
        if not cfg["base_url"]:
            QMessageBox.warning(self, "提示", "请填写 API 地址。")
            return

        if AIClient.save_config(cfg):
            invalidate_client()  # 使旧客户端失效，下次调用时按新配置重建
            self.status_label.setText(f"配置已保存（{AI_CONFIG_FILE}）")
            QMessageBox.information(self, "提示", "AI 配置已保存，其他页面的 AI 功能将使用新配置。")
        else:
            QMessageBox.warning(self, "提示", f"保存失败，请检查目录是否可写：{AI_CONFIG_FILE}")

    # ------------------------------------------------------------------
    # 操作
    # ------------------------------------------------------------------
    def _toggle_key_visibility(self, checked: bool):
        if checked:
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Normal)
            self.show_key_btn.setText("隐藏")
        else:
            self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
            self.show_key_btn.setText("显示")

    def _test_connection(self):
        """用当前表单参数测试 AI 服务连通性。"""
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请先填写 API 地址。")
            return

        self.test_btn.setEnabled(False)
        self.test_btn.setText("测试中...")
        self.status_label.setText("正在测试连接...")

        self._worker = _ConnectivityWorker(
            base_url=url,
            api_key=self.key_edit.text().strip(),
            timeout=self.timeout_spin.value(),
        )
        self._worker.finished.connect(self._on_connectivity_result)
        self._worker.start()

    def _on_connectivity_result(self, ok: bool, message: str):
        self.test_btn.setEnabled(True)
        self.test_btn.setText("测试连接")
        self.status_label.setText(message)
        if ok:
            self.status_label.setStyleSheet("color: #2a7; font-weight: bold;")
            QMessageBox.information(self, "连接成功", message)
        else:
            self.status_label.setStyleSheet("color: #c33; font-weight: bold;")
            QMessageBox.warning(self, "连接失败", message)
