# -*- coding: utf-8 -*-
"""
规则校验页：上传文件，选择要应用的规则，执行校验，结果中高亮违规行与规则名。
"""

import traceback
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QGroupBox,
    QListWidget,
    QMessageBox,
    QSpinBox,
    QLineEdit,
    QListWidgetItem,
    QScrollArea,
    QFrame,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from ui.widgets import FilePathRow, ProgressWidget, card_group, make_hint

try:
    from core.parsers import load_table_from_file, ParserError
    from core.rules import RuleEngine
except ImportError:
    load_table_from_file = None
    ParserError = Exception
    RuleEngine = None


class ValidateWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)  # df, payload
    error = pyqtSignal(str)

    def __init__(
        self,
        path: str,
        rule_ids: list,
        header_rows=None,
        skip_top_rows: int = 0,
        max_rows: int = 0,
    ):
        super().__init__()
        self.path = path
        self.rule_ids = rule_ids
        self.header_rows = header_rows
        self.skip_top_rows = skip_top_rows
        self.max_rows = max_rows

    def run(self):
        try:
            self.progress.emit(20, "加载文件...")
            kwargs = {}
            if self.header_rows is not None and self.header_rows > 0:
                kwargs["header_rows"] = self.header_rows
            if self.skip_top_rows and self.skip_top_rows > 0:
                kwargs["skip_top_rows"] = self.skip_top_rows
            if self.max_rows and self.max_rows > 0:
                kwargs["max_rows"] = self.max_rows
            df, _, _ = load_table_from_file(self.path, **kwargs)
            self.progress.emit(50, "加载规则并校验...")
            engine = RuleEngine()
            engine.load_rules()
            if hasattr(engine, "validate_dataframe_with_coverage"):
                violations, unvalidated_rows = engine.validate_dataframe_with_coverage(
                    df,
                    rule_ids=self.rule_ids or None,
                )
            else:
                violations = engine.validate_dataframe(df, rule_ids=self.rule_ids or None)
                unvalidated_rows = []
            self.progress.emit(100, "校验完成")
            self.finished.emit(
                df,
                {
                    "violations": violations,
                    "unvalidated_rows": unvalidated_rows,
                },
            )
        except Exception as e:
            self.error.emit(str(e) + "\n" + traceback.format_exc())


class TabRuleValidate(QWidget):
    result_ready = pyqtSignal(object, object)  # df, payload

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._collapsed_set_ids = set()
        self._known_set_ids = set()
        self._setup_ui()
        self._refresh_rules()

    def _setup_ui(self):
        # 整体放入滚动容器：窗口缩放 / 高 DPI 下内容不会被裁掉，而是出现滚动条
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        layout = QVBoxLayout(inner)

        g, fl = card_group("待校验文件")
        self.file_row = FilePathRow("文件：")
        self.file_row.path_changed.connect(self._on_file_selected)
        fl.addWidget(self.file_row)
        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("表头占用行数："))
        self.header_rows_spin = QSpinBox()
        self.header_rows_spin.setRange(0, 10)
        self.header_rows_spin.setValue(0)
        self.header_rows_spin.setToolTip(
            "0=Excel 自动检测表头行数。\n"
            "1=仅第1行为表头，数据从第2行起；2=第1～2行合并为表头，数据从第3行起。"
        )
        header_row.addWidget(self.header_rows_spin)
        header_row.addWidget(QLabel("跳过顶部行数："))
        self.skip_top_spin = QSpinBox()
        self.skip_top_spin.setRange(0, 50)
        self.skip_top_spin.setValue(0)
        self.skip_top_spin.setToolTip("例如第1行为大标题、第2行才是列名：跳过=1，表头=1")
        header_row.addWidget(self.skip_top_spin)
        header_row.addStretch()
        fl.addLayout(header_row)

        max_row = QHBoxLayout()
        max_row.addWidget(QLabel("最大读取行数（仅 Excel，留空自动）："))
        self.max_rows_edit = QLineEdit()
        self.max_rows_edit.setPlaceholderText("例如 120000，留空不限制")
        max_row.addWidget(self.max_rows_edit)
        fl.addLayout(max_row)
        _hint = make_hint(
            "说明：填 2 表示用第1、2行合并成列名，数据从第3行开始。若仅第2行是表头，请设 跳过顶部行数=1、表头占用行数=1。"
        )
        fl.addWidget(_hint)
        layout.addWidget(g)

        g2, fl2 = card_group("选择校验规则（不选则应用全部）")
        self.rule_list = QListWidget()
        self.rule_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.rule_list.itemDoubleClicked.connect(self._on_rule_item_double_clicked)
        fl2.addWidget(self.rule_list)
        layout.addWidget(g2)

        self.progress = ProgressWidget()
        layout.addWidget(self.progress)
        btn = QPushButton("执行校验")
        btn.clicked.connect(self._run)
        layout.addWidget(btn)
        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def _refresh_rules(self):
        selected_ids = set(self._collect_selected_rule_ids())
        self.rule_list.clear()
        if not RuleEngine:
            return
        engine = RuleEngine()
        engine.load_rules()
        sets = engine.get_rule_sets() if hasattr(engine, "get_rule_sets") else []
        rules = engine.get_rules()
        rule_map = {r.rule_id: r for r in rules}
        grouped_ids = set()

        existing_set_ids = {s.set_id for s in sets}
        new_set_ids = existing_set_ids - self._known_set_ids
        self._known_set_ids = set(existing_set_ids)
        self._collapsed_set_ids = {sid for sid in self._collapsed_set_ids if sid in existing_set_ids}
        self._collapsed_set_ids.update(new_set_ids)

        for rs in sets:
            item_rule_ids = [rid for rid in rs.rule_ids if rid in rule_map]
            if not item_rule_ids:
                continue
            grouped_ids.update(item_rule_ids)

            group_item = QListWidgetItem(f"[规则集] {rs.name} ({len(item_rule_ids)} 条)")
            group_item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "item_type": "set",
                    "set_id": rs.set_id,
                    "rule_ids": item_rule_ids,
                },
            )
            self.rule_list.addItem(group_item)
            if selected_ids.intersection(item_rule_ids):
                group_item.setSelected(True)

            if rs.set_id in self._collapsed_set_ids:
                continue

            for rid in item_rule_ids:
                r = rule_map[rid]
                item = QListWidgetItem(f"   └ {r.name} ({r.rule_id})")
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "item_type": "rule",
                        "rule_id": r.rule_id,
                    },
                )
                self.rule_list.addItem(item)
                if r.rule_id in selected_ids:
                    item.setSelected(True)

        for r in rules:
            if r.rule_id in grouped_ids:
                continue
            item = QListWidgetItem(f"{r.name} ({r.rule_id})")
            item.setData(
                Qt.ItemDataRole.UserRole,
                {
                    "item_type": "rule",
                    "rule_id": r.rule_id,
                },
            )
            self.rule_list.addItem(item)
            if r.rule_id in selected_ids:
                item.setSelected(True)

    def _on_rule_item_double_clicked(self, item: QListWidgetItem):
        item_data = item.data(Qt.ItemDataRole.UserRole) or {}
        if item_data.get("item_type") != "set":
            return
        set_id = item_data.get("set_id")
        if not set_id:
            return
        if set_id in self._collapsed_set_ids:
            self._collapsed_set_ids.remove(set_id)
        else:
            self._collapsed_set_ids.add(set_id)
        self._refresh_rules()

    def _collect_selected_rule_ids(self) -> list:
        ids = []
        for item in self.rule_list.selectedItems():
            item_data = item.data(Qt.ItemDataRole.UserRole) or {}
            if item_data.get("item_type") == "rule":
                rid = item_data.get("rule_id")
                if rid:
                    ids.append(rid)
            elif item_data.get("item_type") == "set":
                for rid in item_data.get("rule_ids", []) or []:
                    ids.append(rid)
        # 去重且保持顺序
        seen = set()
        out = []
        for rid in ids:
            if rid not in seen:
                seen.add(rid)
                out.append(rid)
        return out

    def _on_file_selected(self, path: str):
        pass

    def _run(self):
        path = self.file_row.path()
        if not path:
            QMessageBox.warning(self, "提示", "请先选择文件。")
            return
        max_rows_text = self.max_rows_edit.text().strip()
        max_rows = 0
        if max_rows_text:
            if not max_rows_text.isdigit() or int(max_rows_text) <= 0:
                QMessageBox.warning(self, "提示", "最大读取行数需为正整数，或留空。")
                return
            max_rows = int(max_rows_text)

        header_rows = self.header_rows_spin.value()
        skip_top_rows = self.skip_top_spin.value()
        ids = self._collect_selected_rule_ids()
        self.progress.set_busy("校验中...")
        self._worker = ValidateWorker(
            path,
            ids,
            header_rows=header_rows if header_rows > 0 else None,
            skip_top_rows=skip_top_rows,
            max_rows=max_rows,
        )
        self._worker.progress.connect(self.progress.set_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, df, violations):
        self.progress.set_idle("校验完成")
        self.result_ready.emit(df, violations)

    def _on_error(self, err: str):
        self.progress.set_idle("出错")
        QMessageBox.critical(self, "错误", err)

    def refresh_rules_list(self):
        self._refresh_rules()
