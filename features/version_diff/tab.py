# -*- coding: utf-8 -*-
"""
版本对比页：同一清单不同版本，选择两个文件与对比/键字段，执行对比并显示进度。
"""

import traceback
from typing import Optional
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QGroupBox,
    QMessageBox,
    QLineEdit,
    QCheckBox,
    QSpinBox,
    QScrollArea,
    QFrame,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from ui.widgets import FilePathRow, ColumnSelector, ProgressWidget

try:
    from core.parsers import load_table_from_file, ParserError
    from core.diff import DiffEngine
except ImportError:
    load_table_from_file = None
    ParserError = Exception
    DiffEngine = None


class DiffWorker(QThread):
    """后台执行对比，避免界面卡顿。"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(
        self,
        path_a: str,
        path_b: str,
        key_cols: list,
        compare_cols: list,
        *,
        max_rows: int = 0,
        compare_by_position: bool = False,
        header_rows_a: Optional[int] = None,
        skip_top_rows_a: int = 0,
        header_rows_b: Optional[int] = None,
        skip_top_rows_b: int = 0,
    ):
        super().__init__()
        self.path_a = path_a
        self.path_b = path_b
        self.key_cols = key_cols
        self.compare_cols = compare_cols
        self.max_rows = max_rows
        self.compare_by_position = compare_by_position
        self.header_rows_a = header_rows_a  # None=自动，1/2/3=表头行数
        self.skip_top_rows_a = skip_top_rows_a
        self.header_rows_b = header_rows_b
        self.skip_top_rows_b = skip_top_rows_b

    def run(self):
        try:
            kw_a = {}
            kw_b = {}
            if self.max_rows and self.max_rows > 0:
                kw_a["max_rows"] = self.max_rows
                kw_b["max_rows"] = self.max_rows
            if self.header_rows_a is not None and self.header_rows_a > 0:
                kw_a["header_rows"] = self.header_rows_a
            if self.skip_top_rows_a and self.skip_top_rows_a > 0:
                kw_a["skip_top_rows"] = self.skip_top_rows_a
            if self.header_rows_b is not None and self.header_rows_b > 0:
                kw_b["header_rows"] = self.header_rows_b
            if self.skip_top_rows_b and self.skip_top_rows_b > 0:
                kw_b["skip_top_rows"] = self.skip_top_rows_b
            self.progress.emit(10, "正在加载文件 A...")
            df_a, cols_a, _ = load_table_from_file(self.path_a, **kw_a)
            self.progress.emit(40, "正在加载文件 B...")
            df_b, cols_b, _ = load_table_from_file(self.path_b, **kw_b)
            self.progress.emit(60, "正在对比...")
            # 按行号逐行对比：不按键列合并，第 i 行对第 i 行
            if self.compare_by_position:
                keys_for_engine = []
                compare_cols = self.compare_cols or [c for c in df_a.columns if c in df_b.columns and c not in ("__row_index__", "__diff_type__", "__changed_fields__")]
            else:
                use_row_index = (
                    self.max_rows and self.max_rows > 0
                    and "__row_index__" in df_a.columns and "__row_index__" in df_b.columns
                )
                keys_for_engine = list(self.key_cols) + ["__row_index__"] if use_row_index else self.key_cols
                compare_cols = self.compare_cols
            engine = DiffEngine(key_columns=keys_for_engine, compare_columns=compare_cols)
            result = engine.compare_two_tables(df_a, df_b, keys=keys_for_engine, compare_cols=compare_cols)
            self.progress.emit(100, "对比完成")
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e) + "\n" + traceback.format_exc())


class TabVersionDiff(QWidget):
    """版本对比选项卡。"""

    result_ready = pyqtSignal(object)  # DiffResult

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._columns = []
        self._setup_ui()

    def _setup_ui(self):
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(400)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)

        g_files = QGroupBox("文件")
        fl = QVBoxLayout(g_files)
        self.file_a = FilePathRow("版本 A（旧）：")
        self.file_b = FilePathRow("版本 B（新）：")
        self.file_a.path_changed.connect(self._on_file_selected)
        self.file_b.path_changed.connect(self._on_file_selected)
        fl.addWidget(self.file_a)
        fl.addWidget(self.file_b)
        fl.addWidget(QLabel("表头设置：默认 B 与 A 相同；仅在两张表表头行数不一致时再单独设置 B。"))
        hdr_row_a = QHBoxLayout()
        hdr_row_a.addWidget(QLabel("A 表头占用行数："))
        self.header_rows_a_spin = QSpinBox()
        self.header_rows_a_spin.setRange(0, 10)
        self.header_rows_a_spin.setValue(0)
        self.header_rows_a_spin.setToolTip("0=自动检测；1=第1行为表头；2=第1～2行合并为列名…")
        hdr_row_a.addWidget(self.header_rows_a_spin)
        hdr_row_a.addWidget(QLabel("A 跳过顶部行数："))
        self.skip_top_a_spin = QSpinBox()
        self.skip_top_a_spin.setRange(0, 50)
        self.skip_top_a_spin.setValue(0)
        self.skip_top_a_spin.setToolTip("例如第1行是标题、第2行才是列名：跳过=1，表头=1")
        hdr_row_a.addWidget(self.skip_top_a_spin)
        self.b_same_as_a_cb = QCheckBox("B 表头设置与 A 相同")
        self.b_same_as_a_cb.setChecked(True)
        self.b_same_as_a_cb.setToolTip("勾选时：B 的表头参数自动跟随 A；取消勾选才可单独调整 B。")
        self.b_same_as_a_cb.stateChanged.connect(self._sync_b_header_controls)
        hdr_row_a.addWidget(self.b_same_as_a_cb)
        hdr_row_a.addStretch()
        fl.addLayout(hdr_row_a)

        hdr_row_b = QHBoxLayout()
        hdr_row_b.addWidget(QLabel("B 表头占用行数："))
        self.header_rows_b_spin = QSpinBox()
        self.header_rows_b_spin.setRange(0, 10)
        self.header_rows_b_spin.setValue(0)
        self.header_rows_b_spin.setToolTip("0=自动检测；1=第1行为表头；2=第1～2行合并为列名…")
        hdr_row_b.addWidget(self.header_rows_b_spin)
        hdr_row_b.addWidget(QLabel("B 跳过顶部行数："))
        self.skip_top_b_spin = QSpinBox()
        self.skip_top_b_spin.setRange(0, 50)
        self.skip_top_b_spin.setValue(0)
        self.skip_top_b_spin.setToolTip("例如第1行是标题、第2行才是列名：跳过=1，表头=1")
        hdr_row_b.addWidget(self.skip_top_b_spin)
        btn_r = QPushButton("刷新表头列")
        btn_r.setToolTip("按当前 A/B 设置分别读取表头并合并列名列表")
        btn_r.clicked.connect(lambda: self._refresh_columns(True))
        hdr_row_b.addWidget(btn_r)
        hdr_row_b.addStretch()
        fl.addLayout(hdr_row_b)

        fl.addWidget(QLabel("提示：若两张表表头行数不同，请先取消勾选“B 表头设置与 A 相同”，再分别调整 A/B。"))
        self.header_rows_a_spin.valueChanged.connect(lambda _=None: self._refresh_columns(False))
        self.skip_top_a_spin.valueChanged.connect(lambda _=None: self._refresh_columns(False))
        self.header_rows_b_spin.valueChanged.connect(lambda _=None: self._refresh_columns(False))
        self.skip_top_b_spin.valueChanged.connect(lambda _=None: self._refresh_columns(False))
        self.header_rows_a_spin.valueChanged.connect(lambda _=None: self._sync_b_header_controls())
        self.skip_top_a_spin.valueChanged.connect(lambda _=None: self._sync_b_header_controls())
        self._sync_b_header_controls()
        fl.addWidget(QLabel("最大读取行数（仅 Excel，留空自动；若只读到 2 千多行可填如 120000）："))
        self.max_rows_edit = QLineEdit()
        self.max_rows_edit.setPlaceholderText("例如 120000，留空不限制")
        fl.addWidget(self.max_rows_edit)
        layout.addWidget(g_files)

        g_cols = QGroupBox("键列与对比列")
        fl2 = QVBoxLayout(g_cols)
        self.compare_by_position_cb = QCheckBox("按行号逐行对比（不按键列合并；键列相同但后续列不同时勾选）")
        self.compare_by_position_cb.setToolTip("勾选后：第1行对第1行、第2行对第2行…不按键列合并，适合键列重复但每行数据不同的表")
        fl2.addWidget(self.compare_by_position_cb)
        fl2.addWidget(QLabel("键列（用于匹配同一行；未勾选上方时生效）："))
        self.key_selector = ColumnSelector()
        fl2.addWidget(self.key_selector)
        fl2.addWidget(QLabel("参与对比的列（空则除键列外全部）："))
        self.compare_selector = ColumnSelector()
        fl2.addWidget(self.compare_selector)
        layout.addWidget(g_cols)

        self.progress = ProgressWidget()
        layout.addWidget(self.progress)

        btn_run = QPushButton("执行版本对比")
        btn_run.clicked.connect(self._run)
        layout.addWidget(btn_run)
        layout.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_file_selected(self, path: str):
        self._refresh_columns(False)

    def _sync_b_header_controls(self):
        """默认让 B 跟随 A；只有取消勾选时才允许单独设置 B。"""
        same = bool(self.b_same_as_a_cb.isChecked()) if hasattr(self, "b_same_as_a_cb") else False
        if same:
            # 同步值
            if hasattr(self, "header_rows_b_spin") and hasattr(self, "header_rows_a_spin"):
                self.header_rows_b_spin.blockSignals(True)
                self.header_rows_b_spin.setValue(self.header_rows_a_spin.value())
                self.header_rows_b_spin.blockSignals(False)
            if hasattr(self, "skip_top_b_spin") and hasattr(self, "skip_top_a_spin"):
                self.skip_top_b_spin.blockSignals(True)
                self.skip_top_b_spin.setValue(self.skip_top_a_spin.value())
                self.skip_top_b_spin.blockSignals(False)
        # 控件启用状态
        if hasattr(self, "header_rows_b_spin"):
            self.header_rows_b_spin.setEnabled(not same)
        if hasattr(self, "skip_top_b_spin"):
            self.skip_top_b_spin.setEnabled(not same)

    def _refresh_columns(self, show_ok: bool):
        path_a = self.file_a.path()
        path_b = self.file_b.path()
        path = path_a or path_b
        if not path or not load_table_from_file:
            return
        try:
            self._sync_b_header_controls()
            old_k = self.key_selector.get_selected()
            old_c = self.compare_selector.get_selected()
            # A/B 分别读取列名，再做并集，避免两边列顺序/层级不同导致无法选列
            cols_union = []
            if path_a:
                hr_a = self.header_rows_a_spin.value() if self.header_rows_a_spin.value() > 0 else None
                sk_a = int(self.skip_top_a_spin.value())
                kw_a = {}
                if hr_a is not None:
                    kw_a["header_rows"] = hr_a
                if sk_a > 0:
                    kw_a["skip_top_rows"] = sk_a
                _, cols_a, _ = load_table_from_file(path_a, **kw_a)
                for c in cols_a:
                    if c not in cols_union:
                        cols_union.append(c)
            if path_b:
                hr_b = self.header_rows_b_spin.value() if self.header_rows_b_spin.value() > 0 else None
                sk_b = int(self.skip_top_b_spin.value())
                kw_b = {}
                if hr_b is not None:
                    kw_b["header_rows"] = hr_b
                if sk_b > 0:
                    kw_b["skip_top_rows"] = sk_b
                _, cols_b, _ = load_table_from_file(path_b, **kw_b)
                for c in cols_b:
                    if c not in cols_union:
                        cols_union.append(c)
            cols = cols_union
            self._columns = cols
            self.key_selector.set_columns(cols)
            self.compare_selector.set_columns(cols)
            for name in old_k:
                for i in range(self.key_selector.available.count()):
                    it = self.key_selector.available.item(i)
                    if it and it.text() == name:
                        it.setSelected(True)
                        break
                self.key_selector._add()
            for name in old_c:
                for i in range(self.compare_selector.available.count()):
                    it = self.compare_selector.available.item(i)
                    if it and it.text() == name:
                        it.setSelected(True)
                        break
                self.compare_selector._add()
            if show_ok:
                QMessageBox.information(self, "提示", f"已刷新，共 {len(cols)} 列。")
        except Exception as e:
            QMessageBox.warning(self, "提示", f"无法读取表头：{e}")

    def _run(self):
        path_a = self.file_a.path()
        path_b = self.file_b.path()
        if not path_a or not path_b:
            QMessageBox.warning(self, "提示", "请先选择两个文件。")
            return
        key_cols = self.key_selector.get_selected()
        compare_cols = self.compare_selector.get_selected()
        compare_by_position = self.compare_by_position_cb.isChecked()
        self._sync_b_header_controls()
        header_rows_a = self.header_rows_a_spin.value() if self.header_rows_a_spin.value() > 0 else None
        skip_top_rows_a = int(self.skip_top_a_spin.value())
        header_rows_b = self.header_rows_b_spin.value() if self.header_rows_b_spin.value() > 0 else None
        skip_top_rows_b = int(self.skip_top_b_spin.value())
        max_rows = 0
        try:
            t = self.max_rows_edit.text().strip()
            if t:
                max_rows = int(t)
        except ValueError:
            pass
        self.progress.set_busy("对比中...")
        self._worker = DiffWorker(
            path_a,
            path_b,
            key_cols,
            compare_cols,
            max_rows=max_rows,
            compare_by_position=compare_by_position,
            header_rows_a=header_rows_a,
            skip_top_rows_a=skip_top_rows_a,
            header_rows_b=header_rows_b,
            skip_top_rows_b=skip_top_rows_b,
        )
        self._worker.progress.connect(self.progress.set_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, result):
        self.progress.set_idle("对比完成")
        try:
            # 若选择了键列但完全没匹配到任何共同键，通常是表头/跳过行设置不一致导致数据错位
            if (
                result
                and not self.compare_by_position_cb.isChecked()
                and self.key_selector.get_selected()
                and result.count_modified == 0
                and result.count_unchanged == 0
                and result.count_added > 0
                and result.count_deleted > 0
            ):
                QMessageBox.warning(
                    self,
                    "提示",
                    "未匹配到任何共同键，结果可能全部为新增/删除。\n\n"
                    "建议检查：\n"
                    "1) A/B 的「表头占用行数」「跳过顶部行数」是否分别正确；\n"
                    "2) 键列（如 TIME）在两表中是否都确实有值且格式一致；\n"
                    "3) 或临时勾选「按行号逐行对比」。",
                )
        except Exception:
            pass
        self.result_ready.emit(result)

    def _on_error(self, err: str):
        self.progress.set_idle("出错")
        QMessageBox.critical(self, "错误", err)
