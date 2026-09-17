# -*- coding: utf-8 -*-
"""
结果报告页：展示对比或校验结果表格，支持导出 PDF/Excel/CSV。
导出在后台线程执行，避免界面未响应。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QFileDialog,
    QMessageBox,
    QComboBox,
    QGroupBox,
)
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from ui.widgets import card_group

try:
    from core.export import export_to_excel, export_to_csv, export_to_pdf, ExportError
    from config import DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED
except ImportError:
    export_to_excel = export_to_csv = export_to_pdf = None
    ExportError = Exception
    DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED = "added", "deleted", "modified"


class ExportWorker(QThread):
    """后台执行导出，避免阻塞主界面。"""
    finished_ok = pyqtSignal(str)
    finished_err = pyqtSignal(str)
    progress = pyqtSignal(int, str)  # 进度 0-100，提示文字

    def __init__(self, df, path: str, fmt: str, violations_by_row=None, unvalidated_rows=None, highlight_later=False):
        super().__init__()
        self.df = df
        self.path = path
        self.fmt = fmt
        self.violations_by_row = violations_by_row
        self.unvalidated_rows = set(unvalidated_rows or [])
        self.highlight_later = bool(highlight_later)

    def run(self):
        try:
            def on_progress(p, msg=""):
                self.progress.emit(p, msg or "导出中…")
            if self.fmt == "csv":
                export_to_csv(self.df, self.path)
            elif self.fmt == "pdf":
                export_to_pdf(
                    self.df,
                    self.path,
                    violations_by_row=self.violations_by_row,
                    unvalidated_rows=self.unvalidated_rows,
                    highlight_later=self.highlight_later,
                )
            else:
                export_to_excel(
                    self.df, self.path,
                    violations_by_row=self.violations_by_row,
                    unvalidated_rows=self.unvalidated_rows,
                    highlight_later=self.highlight_later,
                    progress_callback=on_progress,
                )
            self.finished_ok.emit(self.path)
        except Exception as e:
            self.finished_err.emit(str(e))


class TabResults(QWidget):
    """结果展示与导出。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._df = None
        self._violations_by_row = None  # {row_index: [RuleViolation, ...]}
        self._unvalidated_rows = set()  # {row_index, ...}
        self._highlight_later = False
        self._export_worker = None
        self._export_btn = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        g_res, res_layout = card_group("结果预览")
        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        res_layout.addWidget(self._hint_label)
        self._summary_label = QLabel("")
        self._summary_label.setStyleSheet("color: #333; font-weight: bold;")
        res_layout.addWidget(self._summary_label)
        self.table = QTableWidget()
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        res_layout.addWidget(self.table)
        layout.addWidget(g_res, 1)

        h = QHBoxLayout()
        h.addWidget(QLabel("导出格式："))
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Excel (含标记与高亮)", "CSV (纯数据)", "PDF (带格式)"])
        h.addWidget(self.format_combo)
        self._export_btn = QPushButton("导出...")
        self._export_btn.clicked.connect(self._export)
        h.addWidget(self._export_btn)
        h.addStretch()
        layout.addLayout(h)

    def set_diff_result(self, diff_result):
        """展示 DiffResult。"""
        df = diff_result.to_dataframe()
        diff_col_names = ("__diff_type__", "__changed_fields__", "__changes_detail__")
        data_cols = [c for c in df.columns if c not in diff_col_names]
        diff_cols = [x for x in diff_col_names if x in df.columns]
        want = data_cols + diff_cols
        df = df[[c for c in want if c in df.columns]]
        self._df = df
        self._violations_by_row = None
        self._unvalidated_rows = set()
        self._highlight_later = False
        self._last_diff_result = diff_result
        summary = ""
        if hasattr(diff_result, "total_mismatched"):
            total = diff_result.total_mismatched
            summary = (
                f"新增 {getattr(diff_result, 'count_added', 0)} | "
                f"删除 {getattr(diff_result, 'count_deleted', 0)} | "
                f"修改 {getattr(diff_result, 'count_modified', 0)} | "
                f"未变 {getattr(diff_result, 'count_unchanged', 0)} | "
                f"不匹配合计 {total}"
            )
            try:
                if "__diff_type__" in df.columns and "__source_row__" in df.columns:
                    def _rows_of(t):
                        sub = df[df["__diff_type__"] == t]
                        rows = [int(x) for x in sub["__source_row__"].tolist() if str(x).strip() != ""]
                        rows = sorted(set(rows))
                        if len(rows) > 20:
                            return ",".join(str(x) for x in rows[:20]) + f"...(共{len(rows)}行)"
                        return ",".join(str(x) for x in rows)
                    add_rows = _rows_of(DIFF_ADDED)
                    del_rows = _rows_of(DIFF_DELETED)
                    mod_rows = _rows_of(DIFF_MODIFIED)
                    parts = []
                    if add_rows:
                        parts.append(f"新增行:{add_rows}")
                    if del_rows:
                        parts.append(f"删除行:{del_rows}")
                    if mod_rows:
                        parts.append(f"修改行:{mod_rows}")
                    if parts:
                        summary = summary + " | " + "；".join(parts)
            except Exception:
                pass
        self._summary_label.setText(summary)
        self._fill_table(df, max_display_rows=30)

    def set_validation_result(self, df, violations, unvalidated_rows=None):
        """展示规则校验结果：df 为原表，violations 为违规列表。"""
        self._last_diff_result = None
        self._highlight_later = True
        self._unvalidated_rows = set(unvalidated_rows or [])
        if hasattr(self, "_summary_label"):
            self._summary_label.setText(
                f"校验违规：{len(violations)} 条 | 未进入验证：{len(self._unvalidated_rows)} 行"
            )
        self._df = df
        by_row = {}
        for v in violations:
            idx = v.row_index
            if idx not in by_row:
                by_row[idx] = []
            by_row[idx].append(v)
        self._violations_by_row = by_row
        # 表增加一列：违规规则
        import pandas as pd
        display = df.copy()
        display["__违规规则__"] = ""
        display["__验证状态__"] = "已验证"
        for idx, viols in by_row.items():
            if idx in display.index:
                display.loc[idx, "__违规规则__"] = "; ".join(v.rule_name for v in viols)
                display.loc[idx, "__验证状态__"] = "违规"
        for idx in self._unvalidated_rows:
            if idx in display.index:
                display.loc[idx, "__验证状态__"] = "未进入验证"
                if not str(display.loc[idx, "__违规规则__"] or "").strip():
                    display.loc[idx, "__违规规则__"] = "未进入验证（未命中规则前置条件）"
        self._df = display
        self._fill_table(display, max_display_rows=30)

    def _fill_table(self, df, max_display_rows=30):
        if df is None or df.empty:
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            return
        # 仅渲染前 max_display_rows 行，保证界面流畅；_df 仍保留全部数据供导出
        display_rows = min(len(df), max_display_rows)
        if len(df) > display_rows:
            self._hint_label.setText(f"仅显示前 {display_rows} 行，共 {len(df)} 行；导出将包含全部数据")
        else:
            self._hint_label.setText("")
        self.table.setColumnCount(len(df.columns))
        self.table.setRowCount(display_rows)
        self.table.setHorizontalHeaderLabels(list(df.columns))
        batch = 80
        for c in range(len(df.columns)):
            for r in range(display_rows):
                val = df.iloc[r, c]
                item = QTableWidgetItem(str(val) if val is not None else "")
                # 差异行着色：新增/删除整行标记，修改只标记变化的单元格
                if "__diff_type__" in df.columns:
                    dt = df.iloc[r].get("__diff_type__", "")
                    if dt == DIFF_ADDED:
                        item.setBackground(QColor("#90EE90"))
                    elif dt == DIFF_DELETED:
                        item.setBackground(QColor("#FFB6C1"))
                    elif dt == DIFF_MODIFIED:
                        changed_fields = df.iloc[r].get("__changed_fields__", [])
                        if isinstance(changed_fields, list) and str(df.columns[c]) in changed_fields:
                            item.setBackground(QColor("#FFFFE0"))
                # 规则违规只高亮 RuleViolation.error_fields 指向的具体单元格。
                # 旧数据若没有字段信息，则回退为原始数据列整行高亮。
                row_key = df.index[r] if r < len(df.index) else r
                error_fields = set()
                if self._violations_by_row and row_key in self._violations_by_row:
                    viols = self._violations_by_row.get(row_key, [])
                    error_fields = {
                        str(field)
                        for violation in viols
                        for field in (getattr(violation, "error_fields", None) or [])
                    }
                    if not error_fields:
                        error_fields = {
                            str(name) for name in df.columns
                            if name not in ("__违规规则__", "__验证状态__")
                        }
                if str(df.columns[c]) in error_fields:
                    item.setBackground(QColor("#FF9999"))
                elif self._highlight_later and str(val).strip().casefold() == "later":
                    item.setBackground(QColor("#FFFF00"))
                # 未进入验证行高亮（整行浅橙，优先级低于违规）
                elif row_key in self._unvalidated_rows:
                    item.setBackground(QColor("#FFD8A8"))
                self.table.setItem(r, c, item)
                if (c * display_rows + r + 1) % batch == 0:
                    QApplication.processEvents()

    def _export(self):
        if self._df is None or self._df.empty:
            QMessageBox.warning(self, "提示", "当前无结果可导出。")
            return
        if self._export_worker and self._export_worker.isRunning():
            QMessageBox.information(self, "提示", "正在导出中，请稍候。")
            return
        idx = self.format_combo.currentIndex()
        fmt = ["xlsx", "csv", "pdf"][idx]
        path, _ = QFileDialog.getSaveFileName(
            self, "导出",
            f"result.{fmt}",
            f"{fmt.upper()} (*.{fmt});;所有 (*.*)",
        )
        if not path:
            return
        # 在后台线程执行导出，避免界面卡死
        self._export_btn.setEnabled(False)
        self._export_btn.setText("导出中...")
        self._export_worker = ExportWorker(
            self._df.copy(),
            path,
            fmt,
            self._violations_by_row,
            self._unvalidated_rows,
            self._highlight_later,
        )
        self._export_worker.finished_ok.connect(self._on_export_done)
        self._export_worker.finished_err.connect(self._on_export_error)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.start()

    def _on_export_progress(self, pct: int, msg: str):
        self._export_btn.setText(f"导出中 {pct}%")

    def _on_export_done(self, path: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("导出...")
        QMessageBox.information(self, "提示", f"已导出到：{path}")
        try:
            from core.usage import get_tracker
            tracker = get_tracker()
            if tracker.has_employee():
                extension = path.rsplit(".", 1)[-1].lower() if "." in path else "未知"
                tracker.track_event("导出", details=f"导出格式：{extension}")
        except Exception:
            pass

    def _on_export_error(self, err: str):
        self._export_btn.setEnabled(True)
        self._export_btn.setText("导出...")
        QMessageBox.critical(self, "错误", f"导出失败：{err}")
