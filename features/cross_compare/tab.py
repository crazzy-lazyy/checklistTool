# -*- coding: utf-8 -*-
"""
交叉对比页：基准清单 + 待对比文件，配置键列与对比列后执行。
"""

import traceback
import os
from typing import Optional
import re
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
    QDialog,
    QDialogButtonBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTextEdit,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from ui.widgets import FilePathRow, ColumnSelector, ProgressWidget, card_group, make_hint


def _labeled_checkbox(desc: str, tip: str = ""):
    """QCheckBox 不支持文字换行，长文案右侧会被截断。

    因此用一个「勾选框 + 可换行 QLabel」的组合行替代：勾选框承担勾选状态，
    旁边 QLabel 负责完整显示说明文字。返回 (container, checkbox)。

    关键点：标签与容器行的水平尺寸策略都设为 Ignored——让 QLabel 严格使用
    布局分配的宽度并在卡内换行，绝不让单行文字宽度把行撑宽导致超界截断。
    """
    from PyQt6.QtWidgets import QSizePolicy

    row = QWidget()
    row.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    cb = QCheckBox()
    if tip:
        cb.setToolTip(tip)
    lbl = QLabel(desc)
    lbl.setWordWrap(True)
    lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    lay.addWidget(cb)
    lay.addWidget(lbl, 1)
    return row, cb

try:
    from core.parsers import load_table_from_file, get_columns_from_file, ParserError
    from core.diff import DiffEngine, DiffResult
    from config import DIFF_DELETED
except ImportError:
    load_table_from_file = None
    get_columns_from_file = None
    ParserError = Exception
    DiffEngine = None
    DiffResult = None
    DIFF_DELETED = "deleted"

try:
    from core.ai import match_column_names_semantically
except ImportError:
    match_column_names_semantically = None


class _AiColumnMatchWorker(QThread):
    """后台线程：加载文件 → 提取列名+前5行数据 → 调用 AI 列名语义匹配。"""
    finished = pyqtSignal(object)  # dict: {matches, unmatched_base, unmatched_target}
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, base_path: str, target_path: str,
                 kw_base: dict, kw_other: dict):
        super().__init__()
        self.base_path = base_path
        self.target_path = target_path
        self.kw_base = kw_base
        self.kw_other = kw_other

    @staticmethod
    def _load_columns_and_sample(path: str, kw: dict):
        """从文件加载列名和前5行样本数据。全部在后台线程执行，不阻塞 UI。"""
        if not load_table_from_file:
            raise Exception("表格解析模块未加载")

        # 1) 先获取列名（轻量）
        if get_columns_from_file:
            cols = get_columns_from_file(
                path,
                header_rows=kw.get("header_rows"),
                skip_top_rows=kw.get("skip_top_rows", 0),
            )
        else:
            _, cols, _ = load_table_from_file(path, **kw)

        # 过滤内部列
        cols = [c for c in (cols or []) if c not in ("__source_row__", "__row_index__")]
        if not cols:
            raise Exception(f"未能从文件中读取到列名：{path}")

        # 2) 读取前几行样本数据（设置 max_rows 避免加载全表）
        sample_kw = dict(kw)
        sample_kw["max_rows"] = 5
        df, _, _ = load_table_from_file(path, **sample_kw)

        sample = {}
        for col in cols:
            if col in df.columns:
                vals = df[col].dropna().head(5).tolist()
                sample[col] = vals
            else:
                sample[col] = []
        return cols, sample

    def run(self):
        try:
            if not match_column_names_semantically:
                raise Exception("AI 模块未加载")

            self.progress.emit("正在读取基准表…")
            base_cols, base_sample = self._load_columns_and_sample(
                self.base_path, self.kw_base,
            )

            self.progress.emit("正在读取待对比表…")
            target_cols, target_sample = self._load_columns_and_sample(
                self.target_path, self.kw_other,
            )

            self.progress.emit("AI 正在分析列名…")
            result = match_column_names_semantically(
                base_cols, target_cols,
                base_sample_data=base_sample,
                target_sample_data=target_sample,
            )
            if result is None:
                raise Exception("AI 未能完成列名匹配，请稍后重试。")
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class _AiMatchDialog(QDialog):
    """展示 AI 列名匹配结果，每行一个「采用」按钮，支持「全部采用」。"""

    ADOPTED_ROLE = Qt.ItemDataRole.UserRole + 1  # 存储 (base_col, target_col)

    def __init__(self, matches: list, unmatched_base: list, unmatched_target: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI 列名智能匹配结果")
        self.setMinimumWidth(700)
        self.resize(900, 520)
        self.setSizeGripEnabled(True)  # 允许用户拖拽右下角调整窗口大小
        self._adopted_pairs: list = []  # [(base_col, target_col), ...]
        self._adopt_buttons: list = []  # 所有采用按钮引用

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"发现 {len(matches)} 对可能匹配的列："))

        # ── 表格 ──
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["序号", "基准表列名", "匹配为 →", "置信度", "操作"])
        self._table.setRowCount(len(matches))
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setAlternatingRowColors(True)

        for i, m in enumerate(matches):
            base = str(m.get("base_column", ""))
            target = str(m.get("target_column", ""))
            conf = float(m.get("confidence", 0))
            reasoning = str(m.get("reasoning", ""))

            pct = f"{int(conf * 100)}%"
            tooltip = reasoning if reasoning else f"{base} → {target} (置信度: {pct})"

            # 序号
            idx_item = QTableWidgetItem(str(i + 1))
            idx_item.setToolTip(tooltip)
            self._table.setItem(i, 0, idx_item)

            # 基准列名
            base_item = QTableWidgetItem(base)
            base_item.setToolTip(tooltip)
            self._table.setItem(i, 1, base_item)

            # 匹配为 →
            arrow_item = QTableWidgetItem(f"→  {target}")
            arrow_item.setToolTip(tooltip)
            self._table.setItem(i, 2, arrow_item)

            # 置信度
            conf_item = QTableWidgetItem(pct)
            conf_item.setToolTip(tooltip)
            if conf >= 0.85:
                conf_item.setForeground(Qt.GlobalColor.darkGreen)
            elif conf >= 0.70:
                conf_item.setForeground(Qt.GlobalColor.darkYellow)
            self._table.setItem(i, 3, conf_item)

            # 采用按钮
            btn = QPushButton("采用")
            btn.setToolTip(f"将「{target}」重命名为「{base}」")
            btn.clicked.connect(lambda checked, r=i: self._on_adopt_row(r))
            self._table.setCellWidget(i, 4, btn)
            self._adopt_buttons.append(btn)

        # 列宽
        self._table.horizontalHeader().setStretchLastSection(False)
        try:
            self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
            self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
            self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        except AttributeError:
            # PyQt6 旧版本兼容
            self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
            self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
            self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
            self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._table.setMinimumHeight(180)
        layout.addWidget(self._table, 1)

        # ── 未匹配列名信息 ──
        if unmatched_base or unmatched_target:
            info_parts = []
            if unmatched_base:
                info_parts.append(f"基准表中未匹配的列：{', '.join(unmatched_base[:20])}")
            if unmatched_target:
                info_parts.append(f"待对比表中未匹配的列：{', '.join(unmatched_target[:20])}")
            info_label = QLabel("\n".join(info_parts))
            info_label.setWordWrap(True)
            info_label.setStyleSheet("color: #666; font-size: 11px; padding-top: 4px;")
            layout.addWidget(info_label)

        # ── 底部按钮 ──
        btn_row = QHBoxLayout()
        self._adopt_all_btn = QPushButton("全部采用")
        self._adopt_all_btn.clicked.connect(self._on_adopt_all)
        btn_row.addWidget(self._adopt_all_btn)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_adopt_row(self, row: int):
        """采用某一行的匹配。"""
        base = self._table.item(row, 1).text().strip()
        target = self._table.item(row, 2).text().replace("→", "").strip()
        # 去重
        if (base, target) not in self._adopted_pairs:
            self._adopted_pairs.append((base, target))
        # 禁用该行的采用按钮
        btn = self._table.cellWidget(row, 4)
        if btn:
            btn.setEnabled(False)
            btn.setText("已采用")
        # 该行变灰
        for col in range(5):
            item = self._table.item(row, col)
            if item:
                item.setForeground(Qt.GlobalColor.gray)

    def _on_adopt_all(self):
        """全部采用：将所有未禁用的行逐行采用后关闭。"""
        for row in range(self._table.rowCount()):
            btn = self._table.cellWidget(row, 4)
            if btn and btn.isEnabled():
                self._on_adopt_row(row)
        if self._adopted_pairs:
            self.accept()

    def get_adopted_pairs(self) -> list:
        """返回用户已采用的列对 [(base_col, target_col), ...]"""
        return list(self._adopted_pairs)


class CrossCompareWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(list)  # List[DiffResult]
    error = pyqtSignal(str)

    def __init__(
        self,
        base_path: str,
        other_path: str,
        key_cols: list,
        compare_cols: list,
        max_rows: int = 0,
        compare_by_position: bool = False,
        compare_key_common_only: bool = False,
        missing_items_only: bool = False,
        header_rows_base: Optional[int] = None,
        skip_top_rows_base: int = 0,
        header_rows_other: Optional[int] = None,
        skip_top_rows_other: int = 0,
        strip_unit_prefix_in_keys: bool = False,
        column_rename_map: dict = None,
    ):
        super().__init__()
        self.base_path = base_path
        self.other_path = other_path
        self.key_cols = key_cols
        self.compare_cols = compare_cols
        self.max_rows = max_rows
        self.compare_by_position = compare_by_position
        self.compare_key_common_only = compare_key_common_only
        self.missing_items_only = missing_items_only
        self.header_rows_base = header_rows_base
        self.skip_top_rows_base = skip_top_rows_base
        self.header_rows_other = header_rows_other
        self.skip_top_rows_other = skip_top_rows_other
        self.strip_unit_prefix_in_keys = strip_unit_prefix_in_keys
        self.column_rename_map = column_rename_map or {}

    def run(self):
        try:
            kw_base = {}
            kw_other = {}
            if self.max_rows and self.max_rows > 0:
                kw_base["max_rows"] = self.max_rows
                kw_other["max_rows"] = self.max_rows
            if self.header_rows_base is not None and self.header_rows_base > 0:
                kw_base["header_rows"] = self.header_rows_base
            if self.skip_top_rows_base and self.skip_top_rows_base > 0:
                kw_base["skip_top_rows"] = self.skip_top_rows_base
            if self.header_rows_other is not None and self.header_rows_other > 0:
                kw_other["header_rows"] = self.header_rows_other
            if self.skip_top_rows_other and self.skip_top_rows_other > 0:
                kw_other["skip_top_rows"] = self.skip_top_rows_other
            self.progress.emit(10, "加载基准表...")
            base_df, _, _ = load_table_from_file(self.base_path, **kw_base)
            # 应用 AI 列名映射：将待对比文件列名重命名为基准列名
            rename_map = getattr(self, "column_rename_map", None) or {}
            self.progress.emit(30, "加载待对比文件...")
            df_other, _, _ = load_table_from_file(self.other_path, **kw_other)
            if rename_map and not df_other.empty:
                applicable = {t: b for t, b in rename_map.items() if t in df_other.columns}
                if applicable:
                    df_other = df_other.rename(columns=applicable)
            other_dfs = [df_other]
            self.progress.emit(80, "执行交叉对比...")
            if self.strip_unit_prefix_in_keys and self.key_cols:
                pat = re.compile(r"^\s*(\d{1,2})(?:\s*(?:#|＃|号|机组))?\s*[-_./\s]*")

                def _strip(v):
                    if v is None:
                        return v
                    s = str(v).strip()
                    if not s:
                        return s
                    return pat.sub("", s, count=1).strip()

                def _apply(df):
                    if df is None or df.empty:
                        return df
                    out = df.copy()
                    for c in self.key_cols:
                        if c in out.columns:
                            out[c] = out[c].map(_strip)
                    return out

                base_df = _apply(base_df)
                other_dfs = [_apply(df) for df in other_dfs]
            def _data_columns(base, other):
                return [c for c in base.columns if c in other.columns and c not in ("__row_index__", "__source_row__", "__diff_type__", "__changed_fields__")]
            def _index_by_key(df, keys, engine):
                m = {}
                for i in range(len(df)):
                    k = engine._row_key(df.iloc[i], keys)
                    m.setdefault(k, []).append(i)
                return m
            if self.missing_items_only:
                if not self.key_cols:
                    raise Exception("缺项检测需要先选择键列。")
                engine = DiffEngine(key_columns=list(self.key_cols), compare_columns=[])
                results = []
                for idx, df in enumerate(other_dfs):
                    p_other = self.other_path
                    if len(base_df) >= len(df):
                        more_df, more_path = base_df, self.base_path
                        less_df, less_path = df, p_other
                    else:
                        more_df, more_path = df, p_other
                        less_df, less_path = base_df, self.base_path

                    keys = list(self.key_cols)
                    key_to_more = _index_by_key(more_df, keys, engine)
                    key_to_less = _index_by_key(less_df, keys, engine)
                    missing_rows = []
                    for k, idxs_more in key_to_more.items():
                        idxs_less = key_to_less.get(k, [])
                        if len(idxs_more) > len(idxs_less):
                            missing_rows.extend(idxs_more[len(idxs_less):])

                    res = DiffResult()
                    base_name = os.path.basename(more_path) if more_path else "基准"
                    missing_in_name = os.path.basename(less_path) if less_path else "待对比"
                    extra_cols = ["__base_file__", "__missing_in__", "__base_row__"]
                    res.columns = list(more_df.columns) + [c for c in extra_cols if c not in more_df.columns]
                    res.key_columns = keys
                    res.compare_columns = []
                    res.count_deleted = len(missing_rows)
                    for i_more in missing_rows:
                        row_dict = more_df.iloc[i_more].to_dict()
                        row_dict["__base_file__"] = base_name
                        row_dict["__missing_in__"] = missing_in_name
                        row_dict["__base_row__"] = int(i_more) + 1
                        row_dict["__diff_type__"] = DIFF_DELETED
                        row_dict["__changed_fields__"] = []
                        row_dict["__changes_detail__"] = "缺少该项"
                        res.rows.append(row_dict)
                    results.append(res)
                self.progress.emit(100, "完成")
                self.finished.emit(results)
                return
            if self.compare_key_common_only and self.key_cols:
                keys_for_engine = list(self.key_cols)
            elif self.compare_by_position:
                keys_for_engine = []
            else:
                use_row_index = (
                    self.max_rows and self.max_rows > 0
                    and "__row_index__" in base_df.columns
                )
                keys_for_engine = list(self.key_cols) + ["__row_index__"] if use_row_index else self.key_cols
            engine = DiffEngine(key_columns=keys_for_engine, compare_columns=self.compare_cols or [])
            results = []
            for df in other_dfs:
                compare_cols_i = self.compare_cols or _data_columns(base_df, df)
                if self.compare_key_common_only and self.key_cols:
                    keys = list(self.key_cols)
                    key_to_base = _index_by_key(base_df, keys, engine)
                    key_to_other = _index_by_key(df, keys, engine)
                    idx_base = []
                    idx_other = []
                    for k in key_to_base:
                        if k not in key_to_other:
                            continue
                        la = key_to_base.get(k, [])
                        lb = key_to_other.get(k, [])
                        n_pair = min(len(la), len(lb))
                        idx_base.extend(la[:n_pair])
                        idx_other.extend(lb[:n_pair])
                    base_aligned = base_df.iloc[idx_base].reset_index(drop=True)
                    other_aligned = df.iloc[idx_other].reset_index(drop=True)
                    res = engine.compare_two_tables(base_aligned, other_aligned, keys=keys, compare_cols=compare_cols_i)
                else:
                    res = engine.compare_two_tables(base_df, df, keys=keys_for_engine, compare_cols=compare_cols_i)
                results.append(res)
            self.progress.emit(100, "完成")
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e) + "\n" + traceback.format_exc())


class TabCrossCompare(QWidget):
    result_ready = pyqtSignal(list)  # List[DiffResult]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._ai_match_worker = None
        self._columns = []
        self._column_rename_map: dict = {}  # {target_col: base_col}
        self._setup_ui()

    def _setup_ui(self):
        # 使用滚动区域，避免内容过高时基准清单等被遮挡
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(400)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(0, 0, 8, 0)

        g_files, fl = card_group("文件")
        self.base_file = FilePathRow("基准清单（旧）：")
        self.base_file.path_changed.connect(self._on_base_selected)
        fl.addWidget(self.base_file)
        self.other_file = FilePathRow("待对比文件：")
        fl.addWidget(self.other_file)
        hdr_row = QHBoxLayout()
        hdr_row.addWidget(QLabel("基准表头占用行数："))
        self.header_rows_base_spin = QSpinBox()
        self.header_rows_base_spin.setRange(0, 10)
        self.header_rows_base_spin.setValue(0)
        self.header_rows_base_spin.setToolTip(
            "0=Excel 自动检测表头行数。\n"
            "1=仅第1行为表头，数据从第2行起；2=第1～2行合并为表头，数据从第3行起。"
        )
        hdr_row.addWidget(self.header_rows_base_spin)
        hdr_row.addWidget(QLabel("基准跳过顶部行数："))
        self.skip_top_base_spin = QSpinBox()
        self.skip_top_base_spin.setRange(0, 50)
        self.skip_top_base_spin.setValue(0)
        self.skip_top_base_spin.setToolTip("例如第1行为大标题、第2行才是列名：跳过=1，表头=1")
        hdr_row.addWidget(self.skip_top_base_spin)
        btn_refresh_hdr = QPushButton("刷新表头列")
        btn_refresh_hdr.setToolTip("按当前设置重新读取基准文件原始表头（不含 AI 匹配），无需重新选文件")
        btn_refresh_hdr.clicked.connect(lambda: self._refresh_base_columns(True))
        hdr_row.addWidget(btn_refresh_hdr)
        btn_check_hdr = QPushButton("表头一致性检查")
        btn_check_hdr.setToolTip("检查基准与待对比文件的表头列名是否一致，并输出不一致项")
        btn_check_hdr.clicked.connect(self._check_headers_consistency)
        hdr_row.addWidget(btn_check_hdr)
        self.btn_ai_match = QPushButton("AI 智能匹配列名")
        self.btn_ai_match.setToolTip("通过 AI 语义分析，找出两表中名称不同但含义相同的列名")
        self.btn_ai_match.clicked.connect(self._on_ai_match_columns)
        hdr_row.addWidget(self.btn_ai_match)
        hdr_row.addStretch()
        fl.addLayout(hdr_row)
        hdr_other = QHBoxLayout()
        self.other_same_as_base_cb = QCheckBox("待对比文件表头设置与基准相同")
        self.other_same_as_base_cb.setChecked(True)
        self.other_same_as_base_cb.setToolTip("勾选时：待对比文件加载时自动沿用基准的表头参数；取消勾选才可单独设置。")
        self.other_same_as_base_cb.stateChanged.connect(self._sync_other_header_controls)
        hdr_other.addWidget(self.other_same_as_base_cb)
        hdr_other.addWidget(QLabel("待对比表头占用行数："))
        self.header_rows_other_spin = QSpinBox()
        self.header_rows_other_spin.setRange(0, 10)
        self.header_rows_other_spin.setValue(0)
        self.header_rows_other_spin.setToolTip("0=自动检测；其余含义同上。")
        hdr_other.addWidget(self.header_rows_other_spin)
        hdr_other.addWidget(QLabel("待对比跳过顶部行数："))
        self.skip_top_other_spin = QSpinBox()
        self.skip_top_other_spin.setRange(0, 50)
        self.skip_top_other_spin.setValue(0)
        self.skip_top_other_spin.setToolTip("例如第1行为大标题、第2行才是列名：跳过=1，表头=1")
        hdr_other.addWidget(self.skip_top_other_spin)
        hdr_other.addStretch()
        fl.addLayout(hdr_other)
        _hint = make_hint(
            "说明：填「2」表示用第1、2行合并成列名，数据从第3行开始——若您本意是「只有第2行是表头」，"
            "请把「跳过顶部行数」设为 1，「表头占用行数」设为 1。"
        )
        fl.addWidget(_hint)
        self.header_rows_base_spin.valueChanged.connect(lambda _=None: self._refresh_base_columns(False))
        self.skip_top_base_spin.valueChanged.connect(lambda _=None: self._refresh_base_columns(False))
        self.header_rows_base_spin.valueChanged.connect(lambda _=None: self._sync_other_header_controls())
        self.skip_top_base_spin.valueChanged.connect(lambda _=None: self._sync_other_header_controls())
        self._sync_other_header_controls()
        _max_hint = make_hint("最大读取行数（仅 Excel，留空自动；若只读到 2 千多行可填如 120000）：")
        fl.addWidget(_max_hint)
        self.max_rows_edit = QLineEdit()
        self.max_rows_edit.setPlaceholderText("例如 120000，留空不限制")
        fl.addWidget(self.max_rows_edit)
        layout.addWidget(g_files)

        g_cols, fl2 = card_group("键列与对比列")
        _row, self.strip_unit_prefix_cb = _labeled_checkbox(
            "键列去除机组号前缀（1~2 位数字）后再匹配对比",
            "用于两张表键列仅机组号不同的场景。\n示例：01-ABC123 与 02-ABC123，会先去掉 01/02 再按 ABC123 匹配。",
        )
        fl2.addWidget(_row)
        _row, self.missing_items_only_cb = _labeled_checkbox(
            "缺项检测（以行数更多的文件为基准，找出另一份缺少的项并标注基准行号）",
            "勾选后将只输出“少了哪一项”，不做逐列差异对比；需要先选择键列。",
        )
        fl2.addWidget(_row)
        _row, self.compare_key_common_only_cb = _labeled_checkbox(
            "仅对比两表共有键的行（行数不同时勾选，按键列对齐后只对共有键做差异化）",
            "勾选后：按所选键列匹配行，只对「基准与待对比文件中键列值相同的行」做差异化比对；仅存在于基准的行标为删除，仅存在于待对比的标为新增。行数不同时推荐勾选。",
        )
        fl2.addWidget(_row)
        _row, self.compare_by_position_cb = _labeled_checkbox(
            "按行号逐行对比（不按键列合并；键列相同但后续列不同时勾选）",
            "勾选后：第1行对第1行、第2行对第2行…不按键列合并，适合键列重复但每行数据不同的表",
        )
        fl2.addWidget(_row)
        _key_label = QLabel("键列（用于匹配同一行；「仅对比共有键」或未勾选按行号时生效）：")
        _key_label.setWordWrap(True)
        fl2.addWidget(_key_label)
        self.key_selector = ColumnSelector()
        fl2.addWidget(self.key_selector)
        cmp_label_row = QHBoxLayout()
        cmp_label_row.addWidget(QLabel("参与对比的列（空则除键列外全部）："))
        self.btn_refresh_ai_header = QPushButton("刷新 AI 匹配表头")
        self.btn_refresh_ai_header.setToolTip("按 AI 匹配结果刷新表头列名，将待对比文件列名替换为基准列名")
        self.btn_refresh_ai_header.clicked.connect(self._refresh_ai_matched_columns)
        cmp_label_row.addWidget(self.btn_refresh_ai_header)
        cmp_label_row.addStretch()
        fl2.addLayout(cmp_label_row)
        self.compare_selector = ColumnSelector()
        self.compare_selector.setMinimumHeight(180)
        fl2.addWidget(self.compare_selector, 1)  # stretch=1 拉伸填充
        layout.addWidget(g_cols)

        self.progress = ProgressWidget()
        layout.addWidget(self.progress)
        btn = QPushButton("执行交叉对比")
        btn.clicked.connect(self._run)
        layout.addWidget(btn)
        layout.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

    def _on_base_selected(self, path: str):
        self._refresh_base_columns(False)

    def _get_header_kwargs_base(self) -> dict:
        hr = self.header_rows_base_spin.value() if self.header_rows_base_spin.value() > 0 else None
        sk = int(self.skip_top_base_spin.value())
        kw = {}
        if hr is not None:
            kw["header_rows"] = hr
        if sk > 0:
            kw["skip_top_rows"] = sk
        return kw

    def _get_header_kwargs_other(self) -> dict:
        hr = self.header_rows_other_spin.value() if self.header_rows_other_spin.value() > 0 else None
        sk = int(self.skip_top_other_spin.value())
        kw = {}
        if hr is not None:
            kw["header_rows"] = hr
        if sk > 0:
            kw["skip_top_rows"] = sk
        return kw

    def _read_columns_only(self, path: str, kw: dict) -> list:
        if not path:
            return []
        if get_columns_from_file:
            return get_columns_from_file(path, header_rows=kw.get("header_rows"), skip_top_rows=kw.get("skip_top_rows", 0))
        if load_table_from_file:
            _, cols, _ = load_table_from_file(path, **kw)
            return cols or []
        return []

    def _on_ai_match_columns(self):
        """触发 AI 列名语义匹配（文件读取+AI调用全部在后台线程）。"""
        base_path = self.base_file.path()
        if not base_path:
            QMessageBox.warning(self, "提示", "请先选择基准清单。")
            return
        other_path = self.other_file.path()
        if not other_path:
            QMessageBox.warning(self, "提示", "请选择待对比文件。")
            return
        if not match_column_names_semantically:
            QMessageBox.warning(self, "提示", "AI 模块未加载，请检查依赖。")
            return

        self._sync_other_header_controls()
        kw_base = self._get_header_kwargs_base()
        kw_other = self._get_header_kwargs_other()

        # 所有文件加载和 AI 调用都在后台线程执行，不阻塞 UI
        self.btn_ai_match.setEnabled(False)
        self.btn_ai_match.setText("匹配中...")
        self.progress.set_busy("正在读取文件...")

        self._ai_match_worker = _AiColumnMatchWorker(
            base_path, other_path,
            kw_base=kw_base,
            kw_other=kw_other,
        )
        self._ai_match_worker.progress.connect(lambda msg: self.progress.set_busy(msg))
        self._ai_match_worker.finished.connect(self._on_ai_match_finished)
        self._ai_match_worker.error.connect(self._on_ai_match_error)
        self._ai_match_worker.start()

    def _on_ai_match_finished(self, result: dict):
        """AI 列名匹配完成，弹出结果对话框。"""
        self.btn_ai_match.setEnabled(True)
        self.btn_ai_match.setText("AI 智能匹配列名")
        self.progress.set_idle("匹配完成")

        matches = list(result.get("matches", []) or [])
        unmatched_base = list(result.get("unmatched_base", []) or [])
        unmatched_target = list(result.get("unmatched_target", []) or [])

        if not matches:
            QMessageBox.information(self, "提示", "AI 未发现可匹配的列对。")
            return

        try:
            dlg = _AiMatchDialog(matches, unmatched_base, unmatched_target, self)
            dlg.exec()  # 用户可逐行采用或全部采用，点击关闭后获取结果
        except Exception as e:
            QMessageBox.warning(self, "提示", f"显示匹配结果对话框失败：{e}")
            return

        pairs = dlg.get_adopted_pairs()
        if not pairs:
            return

        # 存储列名重命名映射：{target_col: base_col}（以基准为标准）
        for base_col, target_col in pairs:
            if target_col != base_col:
                self._column_rename_map[target_col] = base_col

        # 保存当前已选列（后续需要恢复）
        key_selected = self.key_selector.get_selected()
        cmp_selected = self.compare_selector.get_selected()

        # 自动刷新为 AI 匹配后的表头：重新加载基准文件，只显示基准列名
        rename_map = self._column_rename_map or {}
        old_k = [rename_map.get(c, c) for c in key_selected]
        old_c = [rename_map.get(c, c) for c in cmp_selected]

        path = self.base_file.path()
        try:
            kw = self._get_header_kwargs_base()
            _, cols_base, _ = load_table_from_file(path, **kw)
            cols = [c for c in cols_base if c not in ("__source_row__", "__row_index__")]
            self._columns = cols
            self.key_selector.set_columns(cols)
            self.compare_selector.set_columns(cols)
            for name in old_k:
                items = self.key_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.key_selector._add()
            for name in old_c:
                items = self.compare_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.compare_selector._add()
        except Exception:
            pass

        QMessageBox.information(
            self, "提示",
            f"已采用 {len(pairs)} 对列名匹配，表头已自动更新为基准列名。\n"
            "如需恢复原始表头请点击「刷新表头列」按键，\n"
            "如需重新应用 AI 匹配请点击「刷新 AI 匹配表头」按键。",
        )

    def _on_ai_match_error(self, err: str):
        """AI 列名匹配失败。"""
        self.btn_ai_match.setEnabled(True)
        self.btn_ai_match.setText("AI 智能匹配列名")
        self.progress.set_idle("匹配失败")
        # 限制错误信息长度，避免对话框过大
        msg = str(err).strip()
        if len(msg) > 800:
            msg = msg[:800] + "\n…（已截断）"
        QMessageBox.warning(
            self, "AI 匹配失败",
            f"列名智能匹配未完成。\n\n{msg}\n\n"
            "请检查：\n"
            "1. AI 服务是否正常运行\n"
            "2. API 地址和密钥是否正确\n"
            "3. 模型是否可用",
        )

    def _check_headers_consistency(self):
        base_path = self.base_file.path()
        if not base_path:
            QMessageBox.warning(self, "提示", "请先选择基准清单。")
            return
        other_path = self.other_file.path()
        if not other_path:
            QMessageBox.warning(self, "提示", "请选择待对比文件。")
            return

        self._sync_other_header_controls()
        kw_base = self._get_header_kwargs_base()
        kw_other = self._get_header_kwargs_other()

        try:
            cols_base = self._read_columns_only(base_path, kw_base)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"读取基准表头失败：{e}")
            return

        def _norm(s: str) -> str:
            s = "" if s is None else str(s)
            s = s.strip().lower()
            s = re.sub(r"\s+", "", s)
            return s

        base_set = set(cols_base or [])
        base_norm = {_norm(c): c for c in cols_base or [] if str(c).strip()}

        blocks = []
        ok_all = True
        for p in [other_path]:
            try:
                cols_o = self._read_columns_only(p, kw_other)
            except Exception as e:
                ok_all = False
                blocks.append(f"文件：{os.path.basename(p)}\n- 读取表头失败：{e}")
                continue

            other_set = set(cols_o or [])
            missing = [c for c in cols_base if c not in other_set]
            extra = [c for c in cols_o if c not in base_set]

            # 可能只是文本差异：按归一化映射猜测对应
            other_norm = {_norm(c): c for c in cols_o or [] if str(c).strip()}
            maybe_same = []
            for c in missing:
                k = _norm(c)
                if k and k in other_norm:
                    maybe_same.append((c, other_norm[k]))

            if not missing and not extra:
                blocks.append(f"文件：{os.path.basename(p)}\n- 表头一致")
                continue

            ok_all = False
            lines = [f"文件：{os.path.basename(p)}"]
            if maybe_same:
                lines.append("- 可能仅是空格/大小写等文本差异：")
                for a, b in maybe_same[:30]:
                    lines.append(f"  - 基准：{a}  <->  待对比：{b}")
            if missing:
                lines.append(f"- 基准有但待对比没有：{len(missing)}")
                for c in missing[:80]:
                    lines.append(f"  - {c}")
                if len(missing) > 80:
                    lines.append("  - ...")
            if extra:
                lines.append(f"- 待对比有但基准没有：{len(extra)}")
                for c in extra[:80]:
                    lines.append(f"  - {c}")
                if len(extra) > 80:
                    lines.append("  - ...")
            blocks.append("\n".join(lines))

        if ok_all:
            QMessageBox.information(self, "表头一致性检查", "基准与所有待对比文件的表头列名一致。")
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("表头一致性检查")
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText("检测到表头不一致。可在'详情'中查看每个文件的差异。")
        msg.setDetailedText("\n\n".join(blocks))
        msg.setSizeGripEnabled(True)  # 允许用户拖拽右下角调整窗口大小
        # 给详细文本区域设置合理的最小尺寸，避免长文本被压缩到看不清
        for child in msg.findChildren(QTextEdit):
            child.setMinimumWidth(500)
            child.setMinimumHeight(200)
            break
        msg.exec()

    def _refresh_base_columns(self, show_ok: bool):
        path = self.base_file.path()
        if not path or not load_table_from_file:
            return
        try:
            self._sync_other_header_controls()
            kw = self._get_header_kwargs_base()
            old_k = self.key_selector.get_selected()
            old_c = self.compare_selector.get_selected()
            _, cols_base, _ = load_table_from_file(path, **kw)
            cols_union = []
            for c in cols_base:
                if c not in cols_union:
                    cols_union.append(c)
            other_path = self.other_file.path()
            if other_path:
                kw_o = self._get_header_kwargs_other()
                try:
                    _, cols_o, _ = load_table_from_file(other_path, **kw_o)
                    for c in cols_o:
                        if c not in cols_union:
                            cols_union.append(c)
                except Exception:
                    pass
            cols = cols_union
            self._columns = cols

            self.key_selector.set_columns(cols)
            self.compare_selector.set_columns(cols)
            for name in old_k:
                items = self.key_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.key_selector._add()
            for name in old_c:
                items = self.compare_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.compare_selector._add()
            if show_ok:
                QMessageBox.information(self, "提示", f"已刷新原始表头，共 {len(cols)} 列。")
        except Exception as e:
            QMessageBox.warning(self, "提示", f"无法读取表头：{e}")

    def _refresh_ai_matched_columns(self):
        """按 AI 匹配结果刷新表头列名（只显示基准列名）。"""
        path = self.base_file.path()
        if not path or not load_table_from_file:
            return
        rename_map = self._column_rename_map or {}
        if not rename_map:
            QMessageBox.information(self, "提示", "当前没有 AI 匹配结果，请先执行「AI 智能匹配列名」并采用匹配对。")
            return
        try:
            self._sync_other_header_controls()
            kw = self._get_header_kwargs_base()
            old_k = self.key_selector.get_selected()
            old_c = self.compare_selector.get_selected()

            # 将旧选择中的列名应用 rename_map
            old_k = [rename_map.get(c, c) for c in old_k]
            old_c = [rename_map.get(c, c) for c in old_c]

            _, cols_base, _ = load_table_from_file(path, **kw)
            cols = [c for c in cols_base if c not in ("__source_row__", "__row_index__")]
            self._columns = cols

            self.key_selector.set_columns(cols)
            self.compare_selector.set_columns(cols)
            for name in old_k:
                items = self.key_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.key_selector._add()
            for name in old_c:
                items = self.compare_selector.available.findItems(name, Qt.MatchFlag.MatchExactly)
                if items:
                    items[0].setSelected(True)
            self.compare_selector._add()
            QMessageBox.information(self, "提示", f"已刷新 AI 匹配后的表头，共 {len(cols)} 列。")
        except Exception as e:
            QMessageBox.warning(self, "提示", f"无法读取表头：{e}")

    def _run(self):
        base_path = self.base_file.path()
        if not base_path:
            QMessageBox.warning(self, "提示", "请选择基准清单。")
            return
        other_path = self.other_file.path()
        if not other_path:
            QMessageBox.warning(self, "提示", "请选择待对比文件。")
            return
        key_cols = self.key_selector.get_selected()
        compare_cols = self.compare_selector.get_selected()
        compare_by_position = self.compare_by_position_cb.isChecked()
        compare_key_common_only = self.compare_key_common_only_cb.isChecked()
        missing_items_only = self.missing_items_only_cb.isChecked()
        if missing_items_only and not key_cols:
            QMessageBox.warning(self, "提示", "勾选「缺项检测」时请先选择键列。")
            return
        if missing_items_only:
            compare_by_position = False
            compare_key_common_only = False
        if compare_key_common_only and not key_cols:
            QMessageBox.warning(self, "提示", "勾选「仅对比两表共有键的行」时请先选择键列。")
            return
        if compare_key_common_only and compare_by_position:
            compare_by_position = False
        self._sync_other_header_controls()
        header_rows_base = self.header_rows_base_spin.value() if self.header_rows_base_spin.value() > 0 else None
        skip_top_rows_base = int(self.skip_top_base_spin.value())
        header_rows_other = self.header_rows_other_spin.value() if self.header_rows_other_spin.value() > 0 else None
        skip_top_rows_other = int(self.skip_top_other_spin.value())
        strip_unit = bool(self.strip_unit_prefix_cb.isChecked()) if hasattr(self, "strip_unit_prefix_cb") else False
        max_rows = 0
        try:
            t = self.max_rows_edit.text().strip()
            if t:
                max_rows = int(t)
        except ValueError:
            pass
        self.progress.set_busy("交叉对比中...")
        self._worker = CrossCompareWorker(
            base_path,
            other_path,
            key_cols,
            compare_cols,
            max_rows=max_rows,
            compare_by_position=compare_by_position,
            compare_key_common_only=compare_key_common_only,
            missing_items_only=missing_items_only,
            header_rows_base=header_rows_base,
            skip_top_rows_base=skip_top_rows_base,
            header_rows_other=header_rows_other,
            skip_top_rows_other=skip_top_rows_other,
            strip_unit_prefix_in_keys=strip_unit,
            column_rename_map=self._column_rename_map,
        )
        self._worker.progress.connect(self.progress.set_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, results: list):
        self.progress.set_idle("完成")
        self.result_ready.emit(results)

    def _on_error(self, err: str):
        self.progress.set_idle("出错")
        QMessageBox.critical(self, "错误", err)

    def _sync_other_header_controls(self):
        """默认让待对比文件沿用基准；只有取消勾选时才允许单独设置。"""
        same = bool(self.other_same_as_base_cb.isChecked()) if hasattr(self, "other_same_as_base_cb") else False
        if same:
            if hasattr(self, "header_rows_other_spin") and hasattr(self, "header_rows_base_spin"):
                self.header_rows_other_spin.blockSignals(True)
                self.header_rows_other_spin.setValue(self.header_rows_base_spin.value())
                self.header_rows_other_spin.blockSignals(False)
            if hasattr(self, "skip_top_other_spin") and hasattr(self, "skip_top_base_spin"):
                self.skip_top_other_spin.blockSignals(True)
                self.skip_top_other_spin.setValue(self.skip_top_base_spin.value())
                self.skip_top_other_spin.blockSignals(False)
        if hasattr(self, "header_rows_other_spin"):
            self.header_rows_other_spin.setEnabled(not same)
        if hasattr(self, "skip_top_other_spin"):
            self.skip_top_other_spin.setEnabled(not same)
