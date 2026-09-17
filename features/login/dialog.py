"""启动时必填的员工信息对话框。"""

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
    QGroupBox, QLabel, QLineEdit, QMessageBox, QVBoxLayout,
)

from core.usage.employee import (
    EmployeeProfile, load_employee_profile, normalize_employee_id, save_employee_profile,
)

_PRESET_DEPARTMENTS = ["主辅系统室", "布置室", "三废系统室", "力学室", "在退役室"]
_current_employee: Optional[EmployeeProfile] = None


def get_current_employee() -> Optional[EmployeeProfile]:
    return _current_employee


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("员工登录 — 清单对比与校审工具")
        self.setMinimumWidth(420)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._setup_ui()
        self._load_last_profile()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        title = QLabel("清单对比与校审工具")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 14px; font-weight: bold; margin-bottom: 4px;")
        layout.addWidget(title)
        hint = QLabel(
            "请填写员工信息，用于记录软件使用情况。记录保存在本机；仅在管理员启用共享目录同步后，"
            "才会复制到该目录。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; margin-bottom: 8px;")
        layout.addWidget(hint)
        box = QGroupBox("员工信息")
        form = QFormLayout(box)
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("请输入工号，如 E001")
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("请输入姓名")
        self.dept_combo = QComboBox()
        self.dept_combo.setEditable(True)
        self.dept_combo.addItem("")
        self.dept_combo.addItems(_PRESET_DEPARTMENTS)
        form.addRow("工号：", self.id_edit)
        form.addRow("姓名：", self.name_edit)
        form.addRow("部门：", self.dept_combo)
        layout.addWidget(box)
        self.remember_check = QCheckBox("记住登录信息（下次启动自动填充）")
        self.remember_check.setChecked(True)
        layout.addWidget(self.remember_check)
        buttons = QDialogButtonBox()
        buttons.addButton("确定", QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _load_last_profile(self) -> None:
        profile = load_employee_profile()
        if not profile:
            return
        self.id_edit.setText(profile.employee_id)
        self.name_edit.setText(profile.name)
        self.dept_combo.setEditText(profile.department)
        self.id_edit.setFocus()
        self.id_edit.selectAll()

    def _on_accept(self) -> None:
        employee_id = self.id_edit.text().strip()
        name = self.name_edit.text().strip()
        if not employee_id or not name:
            QMessageBox.warning(self, "提示", "请填写工号和姓名。")
            (self.id_edit if not employee_id else self.name_edit).setFocus()
            return
        try:
            employee_id = normalize_employee_id(employee_id)
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            self.id_edit.setFocus()
            self.id_edit.selectAll()
            return
        global _current_employee
        _current_employee = EmployeeProfile(employee_id, name, self.dept_combo.currentText().strip())
        if self.remember_check.isChecked():
            save_employee_profile(_current_employee)
        self.accept()
