"""使用记录共享目录设置。编辑功能需管理员秘钥解锁。"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget, QInputDialog,
)

from config import SYS_CONFIG_FILE, get_network_share_path, get_rules_edit_key, load_json_safe, save_json_safe
from core.usage import get_client_id, get_tracker
from ui.widgets import card_group, make_hint


class TabSysConfig(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._edit_unlocked = False
        self._editable_widgets = []
        self._setup_ui()
        self._load()
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


    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ── 编辑锁按钮 ──
        lock_row = QHBoxLayout()
        self.lock_btn = QPushButton("🔒 已锁定（点击解锁）")
        self.lock_btn.clicked.connect(self._toggle_unlock)
        lock_row.addWidget(self.lock_btn)
        lock_row.addStretch()
        layout.addLayout(lock_row)

        g, gl = card_group("共享目录同步")

        hint = make_hint("可选：将本机的使用记录同步到指定共享目录。未启用时，记录只保存在本机。")
        gl.addWidget(hint)
        client_label = QLabel(f"当前客户端 ID：{get_client_id()}")
        client_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        gl.addWidget(client_label)
        structure_hint = QLabel("共享目录结构：月份 / 工号 / 客户端ID.json")
        structure_hint.setStyleSheet("color: #666;")
        gl.addWidget(structure_hint)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.auto_sync = QCheckBox("启用自动同步")
        form.addRow("同步方式：", self.auto_sync)
        self._editable_widgets.append(self.auto_sync)
        path_layout = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(r"例如 \\server\share\ChecklistTool")
        browse = QPushButton("浏览...")
        browse.clicked.connect(self._browse)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(browse)
        form.addRow("共享目录：", path_layout)
        self._editable_widgets.append(self.path_edit)
        self._editable_widgets.append(browse)
        gl.addLayout(form)
        buttons = QHBoxLayout()
        self.save_btn = QPushButton("保存设置")
        self.save_btn.clicked.connect(self._save)
        sync = QPushButton("立即同步")
        sync.clicked.connect(self._sync_now)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(sync)
        buttons.addStretch()
        gl.addLayout(buttons)
        layout.addWidget(g)
        layout.addStretch()

    def _load(self) -> None:
        cfg = load_json_safe(SYS_CONFIG_FILE, {})
        self.auto_sync.setChecked(bool(cfg.get("auto_sync_enabled", False)))
        self.path_edit.setText(get_network_share_path())

    def _browse(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择共享目录", self.path_edit.text())
        if path:
            self.path_edit.setText(path)

    def _save(self) -> None:
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "配置已锁定，请先点击「🔒 已锁定」按钮并输入管理员秘钥解锁。")
            return
        path = self.path_edit.text().strip()
        if self.auto_sync.isChecked() and not path:
            QMessageBox.warning(self, "提示", "启用自动同步前，请填写共享目录。")
            return
        if save_json_safe(SYS_CONFIG_FILE, {"auto_sync_enabled": self.auto_sync.isChecked(), "network_share_path": path}):
            QMessageBox.information(self, "提示", "系统配置已保存。")
        else:
            QMessageBox.warning(self, "提示", "配置保存失败。")

    def _sync_now(self) -> None:
        QMessageBox.information(self, "同步结果", get_tracker().sync_now())
