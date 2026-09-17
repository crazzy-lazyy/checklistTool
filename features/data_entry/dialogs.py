# -*- coding: utf-8 -*-
"""
设备数据填写页对话框：新建清单（四种列来源）、列名输入、全局模板列管理。
"""

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from ui.widgets import FilePathRow

try:
    from config import get_rules_edit_key
except ImportError:
    get_rules_edit_key = None


class NewListDialog(QDialog):
    """新建清单：名称 + 四种列来源（模板列 / Excel 仅列 / Excel 列+数据 / 手动输入）。"""

    def __init__(self, parent=None, template_columns: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("新建清单")
        self.setMinimumWidth(460)
        self._template_columns = list(template_columns or [])
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("清单名称，如：设计清单-8月")
        form.addRow("清单名称：", self.name_edit)
        layout.addLayout(form)

        # 列来源单选
        self.radio_manual = QRadioButton("手动输入列名（每行一列）")
        self.radio_template = QRadioButton(
            f"使用全局模板列{('（' + '、'.join(self._template_columns) + '）') if self._template_columns else '（模板为空，暂不可用）'}"
        )
        self.radio_excel_cols = QRadioButton("从 Excel 导入（仅列结构）")
        self.radio_excel_full = QRadioButton("从 Excel 导入（列结构 + 数据）")
        self.radio_manual.setChecked(True)
        if not self._template_columns:
            self.radio_template.setEnabled(False)
        for r in (self.radio_manual, self.radio_template, self.radio_excel_cols, self.radio_excel_full):
            r.toggled.connect(self._update_enabled)
            layout.addWidget(r)

        self.columns_edit = QTextEdit()
        self.columns_edit.setPlaceholderText("系统\n专业\n设备名称")
        self.columns_edit.setMaximumHeight(110)
        layout.addWidget(self.columns_edit)

        self.file_row = FilePathRow("Excel 文件：")
        layout.addWidget(self.file_row)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("表头占用行数："))
        self.header_rows_spin = QSpinBox()
        self.header_rows_spin.setRange(0, 10)
        self.header_rows_spin.setValue(0)
        self.header_rows_spin.setToolTip("0=自动检测；1=第1行为表头；2=第1~2行合并为表头")
        header_row.addWidget(self.header_rows_spin)
        header_row.addWidget(QLabel("跳过顶部行数："))
        self.skip_top_spin = QSpinBox()
        self.skip_top_spin.setRange(0, 50)
        self.skip_top_spin.setValue(0)
        header_row.addWidget(self.skip_top_spin)
        header_row.addStretch()
        layout.addLayout(header_row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_enabled()

    def _update_enabled(self):
        excel = self.radio_excel_cols.isChecked() or self.radio_excel_full.isChecked()
        self.file_row.setEnabled(excel)
        self.header_rows_spin.setEnabled(excel)
        self.skip_top_spin.setEnabled(excel)
        self.columns_edit.setEnabled(self.radio_manual.isChecked())

    def _on_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "请填写清单名称。")
            return
        if self.radio_manual.isChecked():
            cols = [c.strip() for c in self.columns_edit.toPlainText().splitlines() if c.strip()]
            if not cols:
                QMessageBox.warning(self, "提示", "请至少输入一个列名。")
                return
            if len(set(cols)) != len(cols):
                QMessageBox.warning(self, "提示", "列名不能重复。")
                return
        elif self.radio_excel_cols.isChecked() or self.radio_excel_full.isChecked():
            if not self.file_row.path():
                QMessageBox.warning(self, "提示", "请选择 Excel 文件。")
                return
        self.accept()

    def result(self) -> dict:
        """返回 {"name", "columns": [...]|None, "file": path|None, "header_rows", "skip_top_rows", "with_data": bool}。"""
        out = {
            "name": self.name_edit.text().strip(),
            "columns": None,
            "file": None,
            "header_rows": self.header_rows_spin.value(),
            "skip_top_rows": self.skip_top_spin.value(),
            "with_data": False,
        }
        if self.radio_manual.isChecked():
            out["columns"] = [c.strip() for c in self.columns_edit.toPlainText().splitlines() if c.strip()]
        elif self.radio_template.isChecked():
            out["columns"] = list(self._template_columns)
        elif self.radio_excel_cols.isChecked():
            out["file"] = self.file_row.path()
        elif self.radio_excel_full.isChecked():
            out["file"] = self.file_row.path()
            out["with_data"] = True
        return out


class ColumnNameDialog(QDialog):
    """输入列名（插入/重命名共用），校验非空与唯一性。"""

    def __init__(self, parent=None, title: str = "列名", current_columns: Optional[List[str]] = None,
                 old_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(320)
        self._current_columns = list(current_columns or [])
        self._old_name = old_name
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(old_name)
        self.name_edit.setPlaceholderText("列名（不可与现有列重复）")
        form.addRow("列名：", self.name_edit)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self):
        name = self.name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "提示", "列名不能为空。")
            return
        others = [c for c in self._current_columns if c != self._old_name]
        if name in others:
            QMessageBox.warning(self, "提示", f"列名重复: {name}")
            return
        self.accept()

    def column_name(self) -> str:
        return self.name_edit.text().strip()


class TemplateColumnsDialog(QDialog):
    """全局模板列管理（需管理员秘钥解锁后编辑）。"""

    def __init__(self, parent=None, columns: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("全局模板列（管理员）")
        self.setMinimumSize(380, 420)
        self._columns = list(columns or [])
        layout = QVBoxLayout(self)

        _hint = QLabel("新建清单可选用全局模板列，保证各清单字段规范统一。")
        _hint.setWordWrap(True)
        layout.addWidget(_hint)
        self.list_widget = QListWidget()
        layout.addWidget(self.list_widget)

        btn_row = QHBoxLayout()
        self.add_btn = QPushButton("添加")
        self.remove_btn = QPushButton("删除")
        self.up_btn = QPushButton("上移")
        self.down_btn = QPushButton("下移")
        self.save_btn = QPushButton("保存")
        self.lock_btn = QPushButton("🔒 已锁定（点击解锁）")
        for b in (self.add_btn, self.remove_btn, self.up_btn, self.down_btn, self.save_btn):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)
        layout.addWidget(self.lock_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.reject)
        layout.addWidget(close_btn)

        self.add_btn.clicked.connect(self._add)
        self.remove_btn.clicked.connect(self._remove)
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.save_btn.clicked.connect(self._save)
        self.lock_btn.clicked.connect(self._toggle_unlock)

        self._unlocked = False
        self._refresh_list()
        self._apply_lock()

    # ------------------------------------------------------------------
    def _apply_lock(self):
        for w in (self.list_widget, self.add_btn, self.remove_btn, self.up_btn, self.down_btn, self.save_btn):
            w.setEnabled(self._unlocked)
        self.lock_btn.setText("🔒 已锁定（点击解锁）" if not self._unlocked else "🔓 已解锁（点击锁定）")

    def _toggle_unlock(self):
        if not self._unlocked:
            key, ok = QInputDialog.getText(
                self, "解锁", "请输入管理员秘钥：",
                echo=QLineEdit.EchoMode.Password,
            )
            if not ok:
                return
            expected = get_rules_edit_key() if get_rules_edit_key else ""
            if key != expected:
                QMessageBox.warning(self, "提示", "秘钥错误。")
                return
        self._unlocked = not self._unlocked
        self._apply_lock()

    def _refresh_list(self):
        self.list_widget.clear()
        self.list_widget.addItems(self._columns)

    def _add(self):
        name, ok = QInputDialog.getText(self, "添加列", "列名：")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._columns:
            QMessageBox.warning(self, "提示", f"列名重复: {name}")
            return
        self._columns.append(name)
        self._refresh_list()

    def _remove(self):
        row = self.list_widget.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择要删除的列。")
            return
        self._columns.pop(row)
        self._refresh_list()

    def _move(self, delta: int):
        row = self.list_widget.currentRow()
        target = row + delta
        if row < 0 or not (0 <= target < len(self._columns)):
            return
        self._columns[row], self._columns[target] = self._columns[target], self._columns[row]
        self._refresh_list()
        self.list_widget.setCurrentRow(target)

    def _save(self):
        if not self._columns:
            QMessageBox.warning(self, "提示", "模板列不能为空。")
            return
        try:
            from core.data import db_connect, init_db, set_template_columns
            conn = db_connect()
            try:
                init_db(conn)
                set_template_columns(conn, self._columns)
            finally:
                conn.close()
            QMessageBox.information(self, "提示", "全局模板列已保存。")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存失败: {e}")
