# -*- coding: utf-8 -*-
"""
数据生成页：根据所选规则生成满足规则的合规数据行。
可选模板文件提供列结构与示例候选值；支持固定行数随机生成与按规则分支全覆盖两种模式。
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
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QRadioButton,
    QButtonGroup,
    QHeaderView,
    QScrollArea,
    QFrame,
)
from PyQt6.QtCore import QThread, pyqtSignal, Qt

from ui.widgets import FilePathRow, ProgressWidget, card_group, make_hint

try:
    from core.parsers import load_table_from_file, ParserError
    from core.rules import RuleEngine
    from core.generate import (
        generate_from_rules,
        extract_domains,
        resolve_columns,
        GenerateConfig,
    )
except ImportError:
    load_table_from_file = None
    ParserError = Exception
    RuleEngine = None
    generate_from_rules = None
    extract_domains = None
    resolve_columns = None
    GenerateConfig = None

_INTERNAL_COLS = ("__source_row__", "__row_index__")


class GenerateWorker(QThread):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object, object)  # df, payload
    error = pyqtSignal(str)

    def __init__(
        self,
        rule_ids: list,
        mode: str,
        row_count: int,
        template_path=None,
        value_pools=None,
        header_rows=None,
        skip_top_rows: int = 0,
    ):
        super().__init__()
        self.rule_ids = rule_ids
        self.mode = mode
        self.row_count = row_count
        self.template_path = template_path
        self.value_pools = value_pools
        self.header_rows = header_rows
        self.skip_top_rows = skip_top_rows

    def run(self):
        try:
            template_cols = None
            template_df = None
            if self.template_path:
                self.progress.emit(15, "加载模板...")
                kwargs = {}
                if self.header_rows is not None and self.header_rows > 0:
                    kwargs["header_rows"] = self.header_rows
                if self.skip_top_rows and self.skip_top_rows > 0:
                    kwargs["skip_top_rows"] = self.skip_top_rows
                df, cols, _ = load_table_from_file(self.template_path, **kwargs)
                template_cols = [c for c in cols if c not in _INTERNAL_COLS]
                template_df = df[template_cols] if template_cols else df
            self.progress.emit(30, "加载规则...")
            engine = RuleEngine()
            engine.load_rules()
            config = GenerateConfig(
                mode=self.mode,
                row_count=self.row_count,
                columns=template_cols,
                value_pools=self.value_pools,
            )
            result = generate_from_rules(
                engine,
                self.rule_ids,
                config,
                template_df=template_df,
                progress_callback=lambda p, m: self.progress.emit(p, m),
            )
            self.progress.emit(100, "生成完成")
            self.finished.emit(result.df, {"report": result.report})
        except Exception as e:
            self.error.emit(str(e) + "\n" + traceback.format_exc())


class TabRuleGenerate(QWidget):
    result_ready = pyqtSignal(object, object)  # df, payload

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._collapsed_set_ids = set()
        self._known_set_ids = set()
        self._template_cols = []
        self._template_samples = {}
        self._user_edited = set()
        self._refreshing = False
        self._field_rows = {}
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

        # 模板文件（可选）
        g, fl = card_group("模板文件（可选：仅取列结构与示例值）")
        self.file_row = FilePathRow("模板文件：")
        self.file_row.path_changed.connect(self._on_template_selected)
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
        _hint = make_hint("说明：不选模板时，生成列取所选规则中出现的字段。模板中的示例数据行将作为对应列的候选值。")
        fl.addWidget(_hint)
        layout.addWidget(g)

        # 选择规则
        g2, fl2 = card_group("选择规则（生成数据须满足所选规则）")
        self.rule_list = QListWidget()
        self.rule_list.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
        self.rule_list.itemDoubleClicked.connect(self._on_rule_item_double_clicked)
        self.rule_list.itemSelectionChanged.connect(self._on_rule_selection_changed)
        fl2.addWidget(self.rule_list)
        layout.addWidget(g2)

        # 生成方式
        g3, fl3 = card_group("生成方式")
        mode_row = QHBoxLayout()
        self.radio_random = QRadioButton("固定行数随机生成")
        self.radio_coverage = QRadioButton("按规则分支全覆盖")
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.radio_random)
        self.mode_group.addButton(self.radio_coverage)
        self.radio_random.setChecked(True)
        self.row_spin = QSpinBox()
        self.row_spin.setRange(1, 100000)
        self.row_spin.setValue(50)
        self.radio_random.toggled.connect(lambda checked: self.row_spin.setEnabled(checked))
        mode_row.addWidget(self.radio_random)
        mode_row.addWidget(QLabel("行数："))
        mode_row.addWidget(self.row_spin)
        mode_row.addStretch()
        fl3.addLayout(mode_row)
        cov_row = QHBoxLayout()
        cov_row.addWidget(self.radio_coverage)
        cov_row.addStretch()
        fl3.addLayout(cov_row)
        _cov_hint = make_hint("全覆盖说明：对每条规则父条件（分支）的取值组合逐一生成一行，行数由分支取值决定。")
        fl3.addWidget(_cov_hint)
        layout.addWidget(g3)

        # 字段候选值
        g4, fl4 = card_group("字段候选值（可选，留空自动）")
        self.pool_table = QTableWidget(0, 2)
        self.pool_table.setHorizontalHeaderLabels(["字段", "候选值（逗号分隔，留空自动）"])
        self.pool_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.pool_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.pool_table.setMaximumHeight(180)
        self.pool_table.itemChanged.connect(self._on_pool_item_changed)
        fl4.addWidget(self.pool_table)
        pool_btn_row = QHBoxLayout()
        self.refresh_pool_btn = QPushButton("重新提取候选值")
        self.refresh_pool_btn.clicked.connect(self._on_refresh_pool_clicked)
        pool_btn_row.addWidget(self.refresh_pool_btn)
        pool_btn_row.addStretch()
        fl4.addLayout(pool_btn_row)
        layout.addWidget(g4)

        # 生成列预览
        self.columns_label = QLabel("生成列：待选择规则或模板")
        self.columns_label.setWordWrap(True)
        layout.addWidget(self.columns_label)

        self.progress = ProgressWidget()
        layout.addWidget(self.progress)
        btn = QPushButton("生成数据")
        btn.clicked.connect(self._run)
        layout.addWidget(btn)
        layout.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll)

    # ------------------------------------------------------------------
    # 规则列表（与规则校验页保持一致）
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # 模板与候选值表
    # ------------------------------------------------------------------
    def _on_template_selected(self, path: str):
        if not path:
            self._template_cols = []
            self._template_samples = {}
        self._load_template_info()

    def _on_rule_selection_changed(self):
        # 规则列表重建时会触发选中变化，候选值表随选中规则刷新
        self._refresh_candidate_table()
        self._refresh_columns_preview()

    def _on_refresh_pool_clicked(self):
        self._load_template_info()

    def _load_template_info(self):
        """读取模板列与示例候选值（≤200 行），刷新候选值表与列预览。"""
        self._template_cols = []
        self._template_samples = {}
        path = self.file_row.path()
        if path and load_table_from_file:
            try:
                kwargs = {"max_rows": 200}
                header_rows = self.header_rows_spin.value()
                skip_top = self.skip_top_spin.value()
                if header_rows > 0:
                    kwargs["header_rows"] = header_rows
                if skip_top > 0:
                    kwargs["skip_top_rows"] = skip_top
                df, cols, _ = load_table_from_file(path, **kwargs)
                self._template_cols = [c for c in cols if c not in _INTERNAL_COLS]
                for c in self._template_cols:
                    if c in df.columns:
                        series = df[c].dropna().astype(str).str.strip()
                        vals = []
                        for v in series.head(200):
                            if v and v not in vals:
                                vals.append(v)
                        self._template_samples[c] = vals[:50]
            except Exception:
                self._template_cols = []
                self._template_samples = {}
        self._refresh_candidate_table()
        self._refresh_columns_preview()

    def _refresh_candidate_table(self):
        """按所选规则与模板示例值重建候选值表；用户手动编辑过的单元格保留。"""
        self._refreshing = True
        try:
            preserved = {}
            for f in list(self._user_edited):
                if f in self._field_rows:
                    row = self._field_rows[f]
                    cell = self.pool_table.item(row, 1)
                    if cell:
                        preserved[f] = cell.text()
            self._user_edited = set()
            self._field_rows = {}
            self.pool_table.setRowCount(0)

            domains = {}
            ids = self._collect_selected_rule_ids()
            if ids and extract_domains and RuleEngine:
                try:
                    engine = RuleEngine()
                    engine.load_rules()
                    domains = extract_domains(engine, ids, value_pools=None)
                except Exception:
                    domains = {}

            row = 0
            for f, d in domains.items():
                row = self._add_pool_row(row, f, self._pool_candidates(f, d))
            for c in self._template_cols:
                if c in domains:
                    continue
                if c in self._template_samples or c in preserved:
                    row = self._add_pool_row(row, c, self._pool_candidates(c, None))
            # 恢复用户编辑过的单元格
            for f, text in preserved.items():
                if f in self._field_rows:
                    cell = self.pool_table.item(self._field_rows[f], 1)
                    if cell:
                        cell.setText(text)
        finally:
            self._refreshing = False

    def _pool_candidates(self, field: str, domain) -> list:
        """字段候选值预览：规则候选 + 模板示例值。"""
        pool = list(domain.candidates) if domain is not None else []
        for v in self._template_samples.get(field, []):
            if v not in pool:
                pool.append(v)
        return pool[:20]

    def _add_pool_row(self, row: int, field: str, candidates: list) -> int:
        self.pool_table.insertRow(row)
        field_item = QTableWidgetItem(field)
        field_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.pool_table.setItem(row, 0, field_item)
        self.pool_table.setItem(row, 1, QTableWidgetItem("，".join(candidates)))
        self._field_rows[field] = row
        return row + 1

    def _on_pool_item_changed(self, item: QTableWidgetItem):
        if self._refreshing:
            return
        if item.column() != 1:
            return
        field_item = self.pool_table.item(item.row(), 0)
        if not field_item:
            return
        if item.text().strip():
            self._user_edited.add(field_item.text())
        else:
            self._user_edited.discard(field_item.text())

    def _refresh_columns_preview(self):
        ids = self._collect_selected_rule_ids()
        cols = list(self._template_cols)
        extra = []
        if ids and resolve_columns and RuleEngine:
            try:
                engine = RuleEngine()
                engine.load_rules()
                cols, extra = resolve_columns(engine, ids, template_columns=cols)
            except Exception:
                pass
        if not cols:
            self.columns_label.setText("生成列：待选择规则或模板")
            return
        shown = "、".join(cols[:12])
        text = f"生成列（{len(cols)} 列）：{shown}"
        if len(cols) > 12:
            text += "…"
        if extra:
            text += f"（含附加列：{'、'.join(extra)}）"
        self.columns_label.setText(text)

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------
    def _run(self):
        path = self.file_row.path()
        ids = self._collect_selected_rule_ids()
        if not ids and not path:
            QMessageBox.warning(self, "提示", "请至少选择一条规则，或提供模板文件。")
            return
        if not RuleEngine or not generate_from_rules:
            QMessageBox.warning(self, "提示", "核心模块未加载，无法生成。")
            return
        mode = "coverage" if self.radio_coverage.isChecked() else "random"
        row_count = self.row_spin.value()
        # 仅传递用户手动编辑过的候选池，避免自动预览截断（前 20 个）造成候选值丢失
        value_pools = {}
        for f in sorted(self._user_edited):
            if f not in self._field_rows:
                continue
            cell = self.pool_table.item(self._field_rows[f], 1)
            if not cell:
                continue
            vals = [v.strip() for v in cell.text().split("，") if v.strip()] or [
                v.strip() for v in cell.text().split(",") if v.strip()
            ]
            if vals:
                value_pools[f] = vals
        header_rows = self.header_rows_spin.value()
        skip_top_rows = self.skip_top_spin.value()
        self.progress.set_busy("生成中...")
        self._worker = GenerateWorker(
            ids,
            mode,
            row_count,
            template_path=path or None,
            value_pools=value_pools,
            header_rows=header_rows if header_rows > 0 else None,
            skip_top_rows=skip_top_rows,
        )
        self._worker.progress.connect(self.progress.set_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_finished(self, df, payload):
        self.progress.set_idle("生成完成")
        report = payload.get("report") if isinstance(payload, dict) else None
        if report is not None and report.generated_rows == 0:
            QMessageBox.warning(
                self, "提示", f"{report.to_text()}\n未生成任何数据行，请检查规则或候选值配置。"
            )
        self.result_ready.emit(df, payload)

    def _on_error(self, err: str):
        self.progress.set_idle("出错")
        QMessageBox.critical(self, "错误", err)

    def refresh_rules_list(self):
        self._refresh_rules()
