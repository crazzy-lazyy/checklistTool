# -*- coding: utf-8 -*-
"""
设备数据填写页：内嵌 SQLite 数据库，编辑时按所选规则即时审查（违规单元格高亮）。

- 清单管理：新建（手动列/模板列/Excel 导入）、复制、重命名、删除、全局模板列
- 大表：分页加载（id 键翻页，页 2000 行），编辑 300ms 防抖后批量写库（自动保存）
- 即时审查：编辑行 100ms 防抖后台校验；全表校验按钮整体校验并生成违规明细
- 导入复用 core.parsers，导出复用 core.export（违规高亮）
"""

import traceback
from typing import Dict, List, Optional

import pandas as pd
from PyQt6.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ui.widgets import ProgressWidget, card_group

try:
    from core.parsers import load_table_from_file, ParserError
    from core.rules import RuleEngine
    from core.data import (
        db_connect,
        init_db,
        create_list,
        get_list,
        list_lists,
        rename_list,
        update_columns,
        rename_column,
        delete_list,
        copy_list,
        insert_row,
        insert_rows_batch,
        get_rows_page,
        get_all_rows,
        update_rows_batch,
        delete_row,
        get_template_columns,
        rows_to_df,
        dedupe_columns,
        DataError,
    )
    from core.export import export_to_excel, export_to_csv, export_to_pdf
    from core.usage import get_tracker
except ImportError:
    load_table_from_file = None
    ParserError = Exception
    RuleEngine = None
    db_connect = None
    init_db = None
    create_list = None
    get_list = None
    list_lists = None
    rename_list = None
    update_columns = None
    rename_column = None
    delete_list = None
    copy_list = None
    insert_row = None
    insert_rows_batch = None
    get_rows_page = None
    get_all_rows = None
    update_rows_batch = None
    delete_row = None
    get_template_columns = None
    rows_to_df = None
    dedupe_columns = None
    DataError = Exception
    export_to_excel = None
    export_to_csv = None
    export_to_pdf = None
    get_tracker = None

from .model import ChecklistTableModel, PAGE_SIZE
from .dialogs import NewListDialog, ColumnNameDialog, TemplateColumnsDialog

_INTERNAL_COLS = ("__source_row__", "__row_index__")


def _cell_str(v) -> str:
    """导入单元格转字符串：None/NaN -> 空串，整数值浮点去小数。"""
    if v is None:
        return ""
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v)


# -----------------------------------------------------------------------------
# 通用后台 worker（函数在子线程执行，绝不触碰 UI）
# -----------------------------------------------------------------------------
class GenericWorker(QThread):
    finished_ok = pyqtSignal(object)
    error = pyqtSignal(str)
    progress = pyqtSignal(int, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            result = self.fn(self.progress.emit)
            self.finished_ok.emit(result)
        except Exception as e:
            self.error.emit(str(e) + "\n" + traceback.format_exc())


# -----------------------------------------------------------------------------
# 表格视图（复制/粘贴/删除行/表头列操作菜单）
# -----------------------------------------------------------------------------
class ChecklistTableView(QTableView):
    delete_rows_requested = pyqtSignal(list)
    add_row_requested = pyqtSignal()
    header_column_action = pyqtSignal(int, str)  # (列索引, "insert"/"rename"/"delete")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.setAlternatingRowColors(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setMinimumSectionSize(60)
        self.horizontalHeader().setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.horizontalHeader().customContextMenuRequested.connect(self._on_header_menu)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_body_menu)

    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Delete):
            rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
            row_ids = []
            for r in rows:
                rid = self.model().row_id_at(r)
                if rid is not None and rid > 0:  # 仅已入库行
                    row_ids.append(rid)
            if row_ids:
                self.delete_rows_requested.emit(row_ids)
                return
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_selection()
            return
        if event.matches(QKeySequence.StandardKey.Paste):
            self._paste_clipboard()
            return
        super().keyPressEvent(event)

    def _copy_selection(self):
        indexes = sorted(self.selectionModel().selectedIndexes(), key=lambda i: (i.row(), i.column()))
        if not indexes:
            return
        rows = {}
        for i in indexes:
            rows.setdefault(i.row(), {})[i.column()] = i.data(Qt.ItemDataRole.DisplayRole) or ""
        min_row, max_row = min(rows), max(rows)
        min_col = min(c for row in rows.values() for c in row)
        max_col = max(c for row in rows.values() for c in row)
        lines = []
        for r in range(min_row, max_row + 1):
            cells = [rows.get(r, {}).get(c, "") for c in range(min_col, max_col + 1)]
            lines.append("\t".join(cells))
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText("\r\n".join(lines))

    def _paste_clipboard(self):
        from PyQt6.QtWidgets import QApplication
        text = QApplication.clipboard().text()
        if not text:
            return
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        grid = [line.split("\t") for line in lines if line != ""]
        if not grid:
            return
        current = self.currentIndex()
        anchor_row = current.row() if current.isValid() else (self.model().rowCount() - 1 if self.model().rowCount() else 0)
        anchor_col = current.column() if current.isValid() else 0
        self.model().paste_cells(anchor_row, anchor_col, grid)

    def _on_header_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        col = self.horizontalHeader().logicalIndexAt(pos)
        if col < 0:
            return
        menu = QMenu(self)
        act_insert = menu.addAction("插入列…")
        act_rename = menu.addAction("重命名列…")
        act_delete = menu.addAction("删除列…")
        action = menu.exec(self.horizontalHeader().mapToGlobal(pos))
        if action == act_insert:
            self.header_column_action.emit(col, "insert")
        elif action == act_rename:
            self.header_column_action.emit(col, "rename")
        elif action == act_delete:
            self.header_column_action.emit(col, "delete")

    def _on_body_menu(self, pos):
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        act_add = menu.addAction("新增行")
        act_del = menu.addAction("删除所选行")
        action = menu.exec(self.viewport().mapToGlobal(pos))
        if action == act_add:
            self.add_row_requested.emit()
        elif action == act_del:
            rows = sorted({i.row() for i in self.selectionModel().selectedIndexes()})
            row_ids = [r for r in (self.model().row_id_at(i) for i in rows) if r is not None and r > 0]
            if row_ids:
                self.delete_rows_requested.emit(row_ids)


# -----------------------------------------------------------------------------
# 页面
# -----------------------------------------------------------------------------
class TabDataEntry(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: Dict[str, GenericWorker] = {}
        self._current_list: Optional[dict] = None
        self._current_list_name = ""
        self._employee_id = self._get_employee_id()
        self._engine = RuleEngine() if RuleEngine else None
        if self._engine is not None:
            try:
                self._engine.load_rules()
            except Exception:
                pass
        self._violations_by_row: Dict[int, list] = {}
        self._unvalidated_rows: set = set()
        self._live_validate_rows: set = set()
        self._live_validate_timer = QTimer(self)
        self._live_validate_timer.setSingleShot(True)
        self._live_validate_timer.setInterval(100)
        self._live_validate_timer.timeout.connect(self._run_live_validation)
        self._flush_in_flight = False
        self._flush_queued = False
        self._pending_load_list: Optional[int] = None
        self._page_in_flight = False
        self._pending_jump_row: Optional[int] = None
        self._setup_ui()
        self._refresh_lists()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左栏：清单列表 + 操作
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        g_left, g_left_layout = card_group("清单列表")
        self.list_widget = QListWidget()
        self.list_widget.currentItemChanged.connect(self._on_list_selected)
        g_left_layout.addWidget(self.list_widget, 1)
        for text, handler in (
            ("新建清单", lambda: self._on_new_list(False)),
            ("从Excel导入", lambda: self._on_new_list(True)),
            ("复制清单", self._on_copy_list),
            ("重命名", self._on_rename_list),
            ("删除清单", self._on_delete_list),
            ("全局模板列", self._on_template_columns),
        ):
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            g_left_layout.addWidget(btn)
        left_layout.addWidget(g_left, 1)
        splitter.addWidget(left)

        # 右栏：工具栏 + 表格 + 状态 + 违规明细
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)

        toolbar = QHBoxLayout()
        self.list_name_label = QLabel("未选择清单")
        f = self.list_name_label.font()
        f.setBold(True)
        self.list_name_label.setFont(f)
        toolbar.addWidget(self.list_name_label)
        toolbar.addSpacing(12)
        toolbar.addWidget(QLabel("审查规则："))
        self.rules_combo = QComboBox()
        self.rules_combo.setMinimumWidth(160)
        self.rules_combo.currentIndexChanged.connect(self._on_rules_changed)
        toolbar.addWidget(self.rules_combo)
        self.validate_btn = QPushButton("全表校验")
        self.validate_btn.clicked.connect(self._run_full_validation)
        toolbar.addWidget(self.validate_btn)
        self.export_btn = QPushButton("导出")
        self.export_btn.clicked.connect(self._do_export)
        toolbar.addWidget(self.export_btn)
        self.add_row_btn = QPushButton("新增行")
        self.add_row_btn.clicked.connect(self._add_new_row)
        toolbar.addWidget(self.add_row_btn)
        toolbar.addStretch()
        right_layout.addLayout(toolbar)

        self.model = ChecklistTableModel(self)
        self.view = ChecklistTableView()
        self.view.setModel(self.model)
        self.model.row_committed.connect(self._on_row_committed)
        self.model.flush_requested.connect(self._on_flush_requested)
        self.model.page_requested.connect(self._on_page_requested)
        self.view.delete_rows_requested.connect(self._on_delete_rows)
        self.view.add_row_requested.connect(self._add_new_row)
        self.view.header_column_action.connect(self._on_header_column_action)
        right_layout.addWidget(self.view, 1)

        status_row = QHBoxLayout()
        self.progress = ProgressWidget()
        status_row.addWidget(self.progress, 1)
        self.summary_label = QLabel("违规 0 条，未进入验证 0 行")
        status_row.addWidget(self.summary_label)
        right_layout.addLayout(status_row)

        g_vio, g_vio_layout = card_group("违规明细（点击跳转）")
        self.violation_list = QListWidget()
        self.violation_list.setMaximumHeight(130)
        self.violation_list.itemClicked.connect(self._on_violation_clicked)
        g_vio_layout.addWidget(self.violation_list)
        right_layout.addWidget(g_vio)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([230, 900])
        layout.addWidget(splitter)

        self._refresh_rules_combo()

    # ------------------------------------------------------------------
    # 清单列表
    # ------------------------------------------------------------------
    def _refresh_lists(self, select_id: Optional[int] = None):
        if not db_connect:
            return
        try:
            conn = db_connect()
            try:
                init_db(conn)
                lists = list_lists(conn)
            finally:
                conn.close()
        except Exception as e:
            self.progress.set_idle("数据库不可用")
            QMessageBox.critical(self, "错误", f"数据库初始化失败（目录需可写）：\n{e}")
            return
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for lst in lists:
            item = QListWidgetItem(f"{lst['name']}（{lst['row_count']} 行）")
            item.setData(Qt.ItemDataRole.UserRole, lst["id"])
            self.list_widget.addItem(item)
            if select_id is not None and lst["id"] == select_id:
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)
        if select_id is not None and any(l["id"] == select_id for l in lists):
            # 选中项在信号屏蔽期间已设置，手动触发加载
            self._load_list(select_id)
        elif lists:
            self.list_widget.setCurrentRow(0)
            self._load_list(lists[0]["id"])
        else:
            self._clear_model()

    def _on_list_selected(self, current, previous):
        if current is None:
            return
        list_id = current.data(Qt.ItemDataRole.UserRole)
        if list_id is None:
            return
        self._load_list(list_id)

    def _clear_model(self):
        self._current_list = None
        self._current_list_name = ""
        self.model.reset_model([], [], 0)
        self.list_name_label.setText("未选择清单")
        self._violations_by_row = {}
        self._unvalidated_rows = set()
        self.violation_list.clear()
        self._update_summary()

    def _load_list(self, list_id: int):
        # 有未写库的编辑时，先 flush 再加载
        if self._flush_in_flight or self.model.has_pending():
            self._pending_load_list = list_id
            if not self._flush_in_flight:
                self._on_flush_requested()
            return
        try:
            conn = db_connect()
            try:
                init_db(conn)
                lst = get_list(conn, list_id)
                rows, total = get_rows_page(conn, list_id, limit=PAGE_SIZE) if lst else ([], 0)
            finally:
                conn.close()
        except Exception as e:
            self.progress.set_idle("加载失败")
            QMessageBox.critical(self, "错误", f"加载清单失败：\n{e}")
            return
        if lst is None:
            return
        self._current_list = lst
        self._current_list_name = lst["name"]
        self._violations_by_row = {}
        self._unvalidated_rows = set()
        self.violation_list.clear()
        self.model.reset_model(lst["columns"], rows, total)
        self.list_name_label.setText(f"清单：{lst['name']}（{total} 行）")
        self._update_summary()

    # ------------------------------------------------------------------
    # 编辑自动保存（防抖批量写库）
    # ------------------------------------------------------------------
    def _on_flush_requested(self):
        if self._current_list is None:
            self.model.take_pending()
            self._maybe_load_pending()
            return
        if self._flush_in_flight:
            self._flush_queued = True
            return
        payload = self.model.take_pending()
        if not payload["updates"] and not payload["inserts"]:
            self._maybe_load_pending()
            return
        self._flush_in_flight = True
        worker = GenericWorker(
            _make_flush_fn(self._current_list["id"], payload["updates"], payload["inserts"], self._employee_id)
        )
        worker.finished_ok.connect(self._on_flush_done)
        worker.error.connect(self._on_flush_error)
        self._workers["flush"] = worker
        worker.start()

    def _on_flush_done(self, result):
        self._flush_in_flight = False
        self.model.on_flush_results({**result, "failed_updates": [], "failed_inserts": []})
        if self._current_list is not None:
            self.list_name_label.setText(f"清单：{self._current_list_name}（{self.model.total_rows()} 行）")
        if self._flush_queued or self.model.has_pending():
            self._flush_queued = False
            self._on_flush_requested()
        self._maybe_load_pending()

    def _on_flush_error(self, err):
        self._flush_in_flight = False
        self.progress.set_idle("保存失败")
        QMessageBox.critical(self, "错误", f"数据保存失败，修改未写入数据库：\n{err}")

    def _maybe_load_pending(self):
        if self._pending_load_list is not None:
            list_id = self._pending_load_list
            self._pending_load_list = None
            self._load_list(list_id)

    # ------------------------------------------------------------------
    # 分页加载
    # ------------------------------------------------------------------
    def _on_page_requested(self, after_id: int, limit: int):
        if self._current_list is None or self._page_in_flight:
            return
        self._fetch_page(after_id, limit)

    def _fetch_page(self, after_id: int, limit: int = PAGE_SIZE):
        if self._current_list is None or self._page_in_flight:
            return
        self._page_in_flight = True
        worker = GenericWorker(_make_page_fn(self._current_list["id"], after_id, limit))
        worker.finished_ok.connect(self._on_page_done)
        worker.error.connect(self._on_page_error)
        self._workers["page"] = worker
        worker.start()

    def _on_page_done(self, result):
        self._page_in_flight = False
        rows, total = result
        self.model.apply_page(rows, total)
        # 新加载行注入已有校验结果
        for r in rows:
            if r["id"] in self._violations_by_row:
                by_col = {}
                for v in self._violations_by_row[r["id"]]:
                    for f in v.error_fields or []:
                        by_col.setdefault(f, []).append(v)
                self.model.set_validation(r["id"], by_col, r["id"] in self._unvalidated_rows)
            elif r["id"] in self._unvalidated_rows:
                self.model.set_validation(r["id"], {}, True)
        if self._pending_jump_row is not None:
            rid = self._pending_jump_row
            idx = self.model.index_of_row_id(rid)
            if idx >= 0:
                self._pending_jump_row = None
                self.view.scrollTo(self.model.index(idx, 0))
                self.view.selectRow(idx)
            elif self.model.total_rows() > self.model.loaded_count():
                self._fetch_page(self.model.last_real_id())
            else:
                self._pending_jump_row = None

    def _on_page_error(self, err):
        self._page_in_flight = False
        self.model.page_fetch_failed()
        self.progress.set_idle("加载失败")
        QMessageBox.critical(self, "错误", f"分页加载失败：\n{err}")

    # ------------------------------------------------------------------
    # 即时审查
    # ------------------------------------------------------------------
    def _on_row_committed(self, row_id: int):
        self._live_validate_rows.add(row_id)
        self._live_validate_timer.start()

    def _run_live_validation(self):
        if self._current_list is None or self._engine is None:
            self._live_validate_rows.clear()
            return
        ids = [i for i in self._live_validate_rows if self.model.index_of_row_id(i) >= 0]
        self._live_validate_rows.clear()
        if not ids:
            return
        rows_values = [{"id": i, "values": self.model.row_values(i)} for i in ids]
        worker = GenericWorker(
            _make_row_validate_fn(rows_values, self.model.columns(), self._selected_rule_ids())
        )
        worker.finished_ok.connect(self._on_live_validation_done)
        worker.error.connect(lambda err: self.progress.set_idle("校验出错"))
        self._workers["live"] = worker
        worker.start()

    def _on_live_validation_done(self, result):
        for row_id, r in result.items():
            if self.model.index_of_row_id(row_id) < 0:
                continue  # 行已不在当前清单视图
            self.model.set_validation(row_id, r["by_col"], r["unvalidated"])
            if r["violations"]:
                self._violations_by_row[row_id] = r["violations"]
            else:
                self._violations_by_row.pop(row_id, None)
            if r["unvalidated"]:
                self._unvalidated_rows.add(row_id)
            else:
                self._unvalidated_rows.discard(row_id)
        self._update_summary()
        self._refresh_violation_list()

    def _run_full_validation(self):
        if self._current_list is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        self.validate_btn.setEnabled(False)
        self.progress.set_busy("全表校验中...")
        worker = GenericWorker(
            _make_full_validate_fn(self._current_list["id"], self.model.columns(), self._selected_rule_ids())
        )
        worker.finished_ok.connect(self._on_full_validation_done)
        worker.error.connect(self._on_worker_error)
        self._workers["validate"] = worker
        worker.start()

    def _on_full_validation_done(self, result):
        self.validate_btn.setEnabled(True)
        self.progress.set_idle("校验完成")
        violations = result["violations"]
        self._violations_by_row = {}
        for v in violations:
            self._violations_by_row.setdefault(v.row_index, []).append(v)
        self._unvalidated_rows = set(result["unvalidated"])
        for rid in self.model.loaded_row_ids():
            by_col = {}
            for v in self._violations_by_row.get(rid, []):
                for f in v.error_fields or []:
                    by_col.setdefault(f, []).append(v)
            self.model.set_validation(rid, by_col, rid in self._unvalidated_rows)
        self._update_summary()
        self._refresh_violation_list()
        self._track("全表校验", f"违规 {len(violations)} 条")

    def _update_summary(self):
        total = sum(len(v) for v in self._violations_by_row.values())
        self.summary_label.setText(f"违规 {total} 条，未进入验证 {len(self._unvalidated_rows)} 行")

    def _refresh_violation_list(self):
        self.violation_list.clear()
        items = []
        for rid, vs in sorted(self._violations_by_row.items()):
            for v in vs:
                items.append((rid, v))
        for rid, v in items[:500]:
            fields = "、".join(v.error_fields or [])
            item = QListWidgetItem(f"行 {rid} | {v.rule_name} | {fields}")
            item.setData(Qt.ItemDataRole.UserRole, rid)
            self.violation_list.addItem(item)
        if len(items) > 500:
            self.violation_list.addItem(f"… 共 {len(items)} 条违规，仅显示前 500 条")

    def _on_violation_clicked(self, item: QListWidgetItem):
        rid = item.data(Qt.ItemDataRole.UserRole)
        if rid is None:
            return
        idx = self.model.index_of_row_id(rid)
        if idx >= 0:
            self.view.scrollTo(self.model.index(idx, 0))
            self.view.selectRow(idx)
            return
        self._pending_jump_row = rid
        self._fetch_page(self.model.last_real_id())

    # ------------------------------------------------------------------
    # 规则下拉
    # ------------------------------------------------------------------
    def _refresh_rules_combo(self):
        current = self.rules_combo.currentData()
        self.rules_combo.blockSignals(True)
        self.rules_combo.clear()
        self.rules_combo.addItem("全部规则", None)
        selected = 0
        if self._engine is not None:
            try:
                sets = self._engine.get_rule_sets() if hasattr(self._engine, "get_rule_sets") else []
            except Exception:
                sets = []
            for i, rs in enumerate(sets):
                self.rules_combo.addItem(rs.name, rs.set_id)
                if current == rs.set_id:
                    selected = i + 1
        self.rules_combo.setCurrentIndex(selected)
        self.rules_combo.blockSignals(False)

    def _selected_rule_ids(self) -> Optional[list]:
        if self._engine is None:
            return None
        set_id = self.rules_combo.currentData()
        if not set_id:
            return None
        if hasattr(self._engine, "get_rule_ids_in_set"):
            ids = self._engine.get_rule_ids_in_set(set_id)
            return ids or None
        return None

    def _on_rules_changed(self, _index):
        if self._current_list is not None:
            self._run_full_validation()

    def refresh_rules_combo(self):
        """规则库变更（rules_updated）联动：刷新下拉并重校验。"""
        if self._engine is not None:
            try:
                self._engine.load_rules()
            except Exception:
                pass
        self._refresh_rules_combo()
        if self._current_list is not None:
            self._run_full_validation()

    # ------------------------------------------------------------------
    # 清单操作
    # ------------------------------------------------------------------
    def _on_new_list(self, excel_preselect: bool):
        try:
            conn = db_connect()
            try:
                init_db(conn)
                template = get_template_columns(conn)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"数据库不可用：\n{e}")
            return
        dlg = NewListDialog(self, template_columns=template)
        if excel_preselect:
            dlg.radio_excel_full.setChecked(True)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        options = dlg.result()
        if options["file"]:
            self._run_import(options)
            return
        self.progress.set_busy("创建清单...")
        worker = GenericWorker(_make_create_list_fn(options))
        worker.finished_ok.connect(lambda lst: self._on_list_created(lst["id"]))
        worker.error.connect(self._on_worker_error)
        self._workers["create"] = worker
        worker.start()

    def _on_list_created(self, list_id: int):
        self.progress.set_idle("清单已创建")
        self._refresh_lists(select_id=list_id)

    def _run_import(self, options: dict):
        self.progress.set_busy("导入中...")
        worker = GenericWorker(_make_import_fn(options, self._employee_id))
        worker.progress.connect(self.progress.set_progress)
        worker.finished_ok.connect(lambda lst: self._on_import_done(lst["id"], options.get("with_data", False)))
        worker.error.connect(self._on_worker_error)
        self._workers["import"] = worker
        worker.start()

    def _on_import_done(self, list_id: int, with_data: bool):
        self.progress.set_idle("导入完成")
        self._refresh_lists(select_id=list_id)
        self._track("导入", "从Excel导入清单（含数据）" if with_data else "从Excel导入列结构")

    def _selected_list_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_copy_list(self):
        list_id = self._selected_list_id()
        if list_id is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        name, ok = QInputDialog.getText(self, "复制清单", "新清单名称：", text=f"{self._current_list_name}-副本")
        if not ok or not name.strip():
            return
        worker = GenericWorker(_make_copy_list_fn(list_id, name.strip()))
        worker.finished_ok.connect(lambda lst: self._refresh_lists(select_id=lst["id"]))
        worker.error.connect(self._on_worker_error)
        self._workers["copy"] = worker
        worker.start()

    def _on_rename_list(self):
        list_id = self._selected_list_id()
        if list_id is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        name, ok = QInputDialog.getText(self, "重命名清单", "新名称：", text=self._current_list_name)
        if not ok or not name.strip():
            return
        worker = GenericWorker(_make_rename_list_fn(list_id, name.strip()))
        worker.finished_ok.connect(lambda lst: self._on_list_renamed(lst))
        worker.error.connect(self._on_worker_error)
        self._workers["rename"] = worker
        worker.start()

    def _on_list_renamed(self, lst):
        self._current_list_name = lst["name"]
        self._current_list = lst
        self.list_name_label.setText(f"清单：{lst['name']}（{self.model.total_rows()} 行）")
        self._refresh_lists(select_id=lst["id"])

    def _on_delete_list(self):
        list_id = self._selected_list_id()
        if list_id is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除清单“{self._current_list_name}”及其全部数据？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        worker = GenericWorker(_make_delete_list_fn(list_id))
        worker.finished_ok.connect(lambda _ok: self._after_list_deleted(list_id))
        worker.error.connect(self._on_worker_error)
        self._workers["delete"] = worker
        worker.start()

    def _after_list_deleted(self, list_id):
        if self._current_list and self._current_list["id"] == list_id:
            self._clear_model()
        self._refresh_lists()

    def _on_template_columns(self):
        try:
            conn = db_connect()
            try:
                init_db(conn)
                columns = get_template_columns(conn)
            finally:
                conn.close()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取模板列失败：\n{e}")
            return
        dlg = TemplateColumnsDialog(self, columns=columns)
        dlg.exec()

    # ------------------------------------------------------------------
    # 行操作
    # ------------------------------------------------------------------
    def _add_new_row(self):
        if self._current_list is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        temp_id = self.model.add_new_row()
        idx = self.model.index_of_row_id(temp_id)
        if idx < 0:
            return
        self.view.scrollToBottom()
        self.view.setCurrentIndex(self.model.index(idx, 0))
        self.view.edit(self.model.index(idx, 0))

    def _on_delete_rows(self, row_ids: list):
        if self._current_list is None:
            return
        ret = QMessageBox.question(
            self, "确认删除",
            f"确定删除所选 {len(row_ids)} 行？此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ret != QMessageBox.StandardButton.Yes:
            return
        worker = GenericWorker(_make_delete_rows_fn(self._current_list["id"], row_ids))
        worker.finished_ok.connect(lambda _n: self._on_rows_deleted(row_ids))
        worker.error.connect(self._on_worker_error)
        self._workers["delete_rows"] = worker
        worker.start()

    def _on_rows_deleted(self, row_ids: list):
        self.model.remove_rows(row_ids)
        for rid in row_ids:
            self._violations_by_row.pop(rid, None)
            self._unvalidated_rows.discard(rid)
        self._update_summary()
        self._refresh_violation_list()

    # ------------------------------------------------------------------
    # 列操作
    # ------------------------------------------------------------------
    def _on_header_column_action(self, col: int, action: str):
        if self._current_list is None:
            return
        columns = self.model.columns()
        if not (0 <= col < len(columns)):
            return
        if action == "insert":
            dlg = ColumnNameDialog(self, "插入列", current_columns=columns)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            new_cols = columns + [dlg.column_name()]
            self._run_update_columns(new_cols, f"新列已插入（现有行该列值为空）")
        elif action == "rename":
            dlg = ColumnNameDialog(self, "重命名列", current_columns=columns, old_name=columns[col])
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            worker = GenericWorker(_make_rename_column_fn(self._current_list["id"], columns[col], dlg.column_name()))
            worker.finished_ok.connect(lambda lst: self._on_columns_changed(lst["columns"], "列已重命名"))
            worker.error.connect(self._on_worker_error)
            self._workers["rename_col"] = worker
            worker.start()
        elif action == "delete":
            ret = QMessageBox.question(
                self, "确认删除列",
                f"确定删除列“{columns[col]}”？该列数据将不再显示（可从列结构恢复）。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ret != QMessageBox.StandardButton.Yes:
                return
            new_cols = [c for i, c in enumerate(columns) if i != col]
            self._run_update_columns(new_cols, "列已删除")

    def _run_update_columns(self, new_cols: list, done_msg: str):
        worker = GenericWorker(_make_update_columns_fn(self._current_list["id"], new_cols))
        worker.finished_ok.connect(lambda lst: self._on_columns_changed(lst["columns"], done_msg))
        worker.error.connect(self._on_worker_error)
        self._workers["cols"] = worker
        worker.start()

    def _on_columns_changed(self, columns: list, msg: str):
        self.model.set_columns(columns)
        if self._current_list is not None:
            self._current_list["columns"] = columns
        self.progress.set_idle(msg)
        self._run_full_validation()

    # ------------------------------------------------------------------
    # 导出
    # ------------------------------------------------------------------
    def _do_export(self):
        if self._current_list is None:
            QMessageBox.warning(self, "提示", "请先选择清单。")
            return
        default = f"{self._current_list_name}.xlsx"
        path, _fmt = QFileDialog.getSaveFileName(
            self, "导出", default,
            "Excel (*.xlsx);;CSV (*.csv);;PDF (*.pdf)",
        )
        if not path:
            return
        fmt = "xlsx"
        if path.lower().endswith(".csv"):
            fmt = "csv"
        elif path.lower().endswith(".pdf"):
            fmt = "pdf"
        self.progress.set_busy("导出中...")
        worker = GenericWorker(
            _make_export_fn(
                self._current_list["id"], self.model.columns(), fmt, path,
                self._violations_by_row, self._unvalidated_rows,
            )
        )
        worker.progress.connect(self.progress.set_progress)
        worker.finished_ok.connect(lambda out: self._on_export_done(out, fmt))
        worker.error.connect(self._on_worker_error)
        self._workers["export"] = worker
        worker.start()

    def _on_export_done(self, out_path: str, fmt: str):
        self.progress.set_idle("导出完成")
        QMessageBox.information(self, "提示", f"导出完成：\n{out_path}")
        self._track("导出", f"格式 {fmt}")

    # ------------------------------------------------------------------
    # 通用
    # ------------------------------------------------------------------
    def _on_worker_error(self, err: str):
        self.validate_btn.setEnabled(True)
        self.progress.set_idle("出错")
        QMessageBox.critical(self, "错误", err)

    def _track(self, action: str, details: str = ""):
        if get_tracker is None:
            return
        try:
            tracker = get_tracker()
            if tracker.has_employee():
                tracker.track_event("设备数据", details=f"{action} {details}".strip())
        except Exception:
            pass

    @staticmethod
    def _get_employee_id() -> str:
        try:
            from features.login import get_current_employee
            emp = get_current_employee()
            return emp.employee_id if emp is not None else ""
        except Exception:
            return ""


# -----------------------------------------------------------------------------
# 后台函数（子线程执行，不触碰 UI）
# -----------------------------------------------------------------------------
def _make_flush_fn(list_id: int, updates: list, inserts: list, employee_id: str):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            updated = update_rows_batch(conn, list_id, updates, editor_id=employee_id) if updates else []
            inserted = {}
            for ins in inserts:
                row = insert_row(conn, list_id, ins["values"], editor_id=employee_id)
                inserted[ins["temp_id"]] = row
            return {"updated": updated, "inserted": inserted}
        finally:
            conn.close()

    return fn


def _make_page_fn(list_id: int, after_id: int, limit: int):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return get_rows_page(conn, list_id, after_id=after_id, limit=limit)
        finally:
            conn.close()

    return fn


def _make_row_validate_fn(rows_values: list, columns: list, rule_ids):
    def fn(progress):
        engine = RuleEngine()
        engine.load_rules()
        df = rows_to_df(rows_values, columns)
        violations, unvalidated = engine.validate_dataframe_with_coverage(df, rule_ids=rule_ids)
        result = {}
        for r in rows_values:
            vs = [v for v in violations if v.row_index == r["id"]]
            by_col = {}
            for v in vs:
                for f in v.error_fields or []:
                    by_col.setdefault(f, []).append(v)
            result[r["id"]] = {
                "by_col": by_col,
                "unvalidated": r["id"] in unvalidated,
                "violations": vs,
            }
        return result

    return fn


def _make_full_validate_fn(list_id: int, columns: list, rule_ids):
    def fn(progress):
        conn = db_connect()
        try:
            rows = get_all_rows(conn, list_id)
        finally:
            conn.close()
        progress(30, f"校验 {len(rows)} 行...")
        df = rows_to_df(rows, columns)
        engine = RuleEngine()
        engine.load_rules()
        violations, unvalidated = engine.validate_dataframe_with_coverage(df, rule_ids=rule_ids)
        progress(100, "校验完成")
        return {"violations": violations, "unvalidated": unvalidated, "total": len(rows)}

    return fn


def _make_create_list_fn(options: dict):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return create_list(conn, options["name"], options["columns"] or [])
        finally:
            conn.close()

    return fn


def _make_import_fn(options: dict, employee_id: str):
    def fn(progress):
        kwargs = {}
        if options.get("header_rows"):
            kwargs["header_rows"] = options["header_rows"]
        if options.get("skip_top_rows"):
            kwargs["skip_top_rows"] = options["skip_top_rows"]
        progress(10, "解析文件...")
        df, cols, _ = load_table_from_file(options["file"], **kwargs)
        columns = dedupe_columns([c for c in cols if c not in _INTERNAL_COLS])
        if not columns:
            raise ValueError("文件中未找到列结构")
        progress(30, f"创建清单（{len(columns)} 列）...")
        conn = db_connect()
        try:
            init_db(conn)
            lst = create_list(conn, options["name"], columns, source="import")
            if options.get("with_data") and not df.empty:
                sub = df[[c for c in columns if c in df.columns]]
                rows = [
                    {c: _cell_str(v) for c, v in row.items()}
                    for _, row in sub.iterrows()
                ]
                chunk = 500
                for i in range(0, len(rows), chunk):
                    part = rows[i:i + chunk]
                    insert_rows_batch(conn, lst["id"], part, editor_id=employee_id)
                    done = min(i + chunk, len(rows))
                    progress(30 + int(60 * done / max(1, len(rows))), f"写入 {done}/{len(rows)} 行...")
            return get_list(conn, lst["id"])
        finally:
            conn.close()

    return fn


def _make_copy_list_fn(list_id: int, new_name: str):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return copy_list(conn, list_id, new_name)
        finally:
            conn.close()

    return fn


def _make_rename_list_fn(list_id: int, new_name: str):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return rename_list(conn, list_id, new_name)
        finally:
            conn.close()

    return fn


def _make_delete_list_fn(list_id: int):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return delete_list(conn, list_id)
        finally:
            conn.close()

    return fn


def _make_delete_rows_fn(list_id: int, row_ids: list):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            count = 0
            for rid in row_ids:
                if delete_row(conn, list_id, rid):
                    count += 1
            return count
        finally:
            conn.close()

    return fn


def _make_update_columns_fn(list_id: int, columns: list):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return update_columns(conn, list_id, columns)
        finally:
            conn.close()

    return fn


def _make_rename_column_fn(list_id: int, old_name: str, new_name: str):
    def fn(progress):
        conn = db_connect()
        try:
            init_db(conn)
            return rename_column(conn, list_id, old_name, new_name)
        finally:
            conn.close()

    return fn


def _make_export_fn(list_id: int, columns: list, fmt: str, path: str,
                    violations_by_row: dict, unvalidated_rows: set):
    def fn(progress):
        conn = db_connect()
        try:
            rows = get_all_rows(conn, list_id)
        finally:
            conn.close()
        df = rows_to_df(rows, columns)
        if fmt == "xlsx":
            return export_to_excel(
                df, path,
                violations_by_row=violations_by_row,
                unvalidated_rows=set(unvalidated_rows),
                progress_callback=progress,
            )
        if fmt == "csv":
            return export_to_csv(df, path)
        return export_to_pdf(
            df, path,
            violations_by_row=violations_by_row,
            unvalidated_rows=set(unvalidated_rows),
        )

    return fn
