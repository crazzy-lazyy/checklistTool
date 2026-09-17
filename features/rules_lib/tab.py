# -*- coding: utf-8 -*-
"""
规则库管理页：默认只读；需要输入秘钥解锁编辑（添加/编辑/删除规则）。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLabel,
    QGroupBox,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QInputDialog,
    QLineEdit,
    QTextEdit,
    QSplitter,
    QSpinBox,
    QFileDialog,
    QComboBox,
    QMenu,
    QScrollArea,
    QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread

from features.rules_lib.rule_tree_editor import RuleTreeEditor
from core.rules import RuleEngine, ValidationRule, RuleNode
from ui.widgets import FilePathRow, card_group, style_group_card

try:
    from core.parsers import load_table_from_file
except ImportError:
    load_table_from_file = None

try:
    from core.ai import parse_natural_language_to_rule, AIClientError
except ImportError:
    parse_natural_language_to_rule = None
    AIClientError = Exception

try:
    from config.app_config import get_rules_edit_key, DEFAULT_RULES_EDIT_KEY
except Exception:
    def get_rules_edit_key() -> str:
        return "admin"
    DEFAULT_RULES_EDIT_KEY = "admin"


class _AiParseWorker(QThread):
    """后台线程：调用 AI 将自然语言转换为规则树。"""
    finished = pyqtSignal(object)  # dict: {rule_name, description, target_columns, root}
    error = pyqtSignal(str)

    def __init__(self, user_input: str, columns: list):
        super().__init__()
        self.user_input = user_input
        self.columns = columns

    def run(self):
        try:
            if not parse_natural_language_to_rule:
                raise Exception("AI 模块未加载")
            result = parse_natural_language_to_rule(self.user_input, self.columns)
            if result is None:
                raise Exception("AI 未能解析出有效规则，请调整描述后重试。")
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(getattr(e, "message", str(e)) if hasattr(e, "message") else str(e))


class TabRulesLib(QWidget):
    """规则库：列表 + 详情（条件树 + 说明）。默认只读，秘钥解锁编辑。"""
    rules_updated = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._engine = RuleEngine()
        self._current_rule_id = None
        self._current_set_id = None
        self._edit_unlocked = False
        self._collapsed_set_ids = set()
        self._known_set_ids = set()
        self._ai_worker = None
        self._setup_ui()
        self._load_rules()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        # 使用说明（规则库建立步骤）
        help_text = QLabel(
            "【使用说明】\n"
            "1. 点击「新增规则」输入规则名称，新规则会出现在左侧列表并自动选中。\n"
            "2. 右侧条件树默认有一个「根」节点；点击「添加根/子条件」可添加子条件，选中某节点后点「编辑」设置：字段（可手动输入列名，如 系统|专业）、运算符、值。\n"
            "3. 根节点可作为条件组，子节点为具体条件；编辑时「字段」选（无/根节点）表示该节点不参与单条判断。\n"
            "   若父节点既配置了字段又有子节点：语义为“父条件命中后，子节点必须满足”（即 A -> B）。\n"
            "   “属于/不属于”使用逗号分隔列表；值本身含英文逗号时写作 \\,。\n"
            "4. 编辑完成后务必点击「保存当前规则」才会写入规则库；若列表为空可点「刷新列表」从文件重新加载。"
        )
        help_text.setWordWrap(True)
        help_text.setStyleSheet("color: #777; background: transparent; font-size: 11px; padding: 0;")
        help_scroll = QScrollArea()
        help_scroll.setWidget(help_text)
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QFrame.Shape.NoFrame)
        help_scroll.setStyleSheet(
            "QScrollArea { background: #f5f5f5; border: 1px solid #e5e7eb; border-radius: 4px; }"
        )
        help_scroll.setFixedHeight(120)
        help_text.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(help_scroll)
        self._path_label = QLabel("规则库保存位置：加载中…")
        self._path_label.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(self._path_label)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        left = QWidget()
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        g_rules, g_rules_layout = card_group("规则列表")
        self.rule_list = QListWidget()
        self.rule_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.rule_list.currentItemChanged.connect(self._on_rule_selected)
        self.rule_list.itemDoubleClicked.connect(self._on_rule_item_double_clicked)
        self.rule_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rule_list.customContextMenuRequested.connect(self._show_rule_list_context_menu)
        g_rules_layout.addWidget(self.rule_list)
        btn_refresh = QPushButton("刷新列表")
        btn_refresh.clicked.connect(self._load_rules)
        g_rules_layout.addWidget(btn_refresh)
        left_layout.addWidget(g_rules)
        split.addWidget(left)

        # ── 右侧：整体可滚动 ──
        right = QWidget()
        right.setMinimumWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 8, 0)

        inner_layout.addWidget(QLabel("规则详情与条件树"))
        inner_layout.addWidget(QLabel("用于表头下拉的示例文件（可选）："))
        self.sample_file = FilePathRow("文件：")
        self.sample_file.path_changed.connect(self._on_sample_file_selected)
        inner_layout.addWidget(self.sample_file)
        sample_opts = QHBoxLayout()
        sample_opts.addWidget(QLabel("表头占用行数："))
        self.sample_header_rows_spin = QSpinBox()
        self.sample_header_rows_spin.setRange(0, 10)
        self.sample_header_rows_spin.setValue(0)
        self.sample_header_rows_spin.setToolTip(
            "0=Excel 自动检测表头行数。\n"
            "1=仅第1行为表头；2=第1～2行合并为表头。"
        )
        sample_opts.addWidget(self.sample_header_rows_spin)
        sample_opts.addWidget(QLabel("跳过顶部行数："))
        self.sample_skip_top_rows_spin = QSpinBox()
        self.sample_skip_top_rows_spin.setRange(0, 50)
        self.sample_skip_top_rows_spin.setValue(0)
        self.sample_skip_top_rows_spin.setToolTip("例如第1行为大标题、第2行才是列名：跳过=1，表头=1")
        sample_opts.addWidget(self.sample_skip_top_rows_spin)
        self.btn_reload_sample_cols = QPushButton("按设置重读表头")
        self.btn_reload_sample_cols.clicked.connect(self._reload_sample_columns)
        sample_opts.addWidget(self.btn_reload_sample_cols)
        sample_opts.addStretch()
        inner_layout.addLayout(sample_opts)
        self.sample_header_rows_spin.valueChanged.connect(lambda _=None: self._reload_sample_columns())
        self.sample_skip_top_rows_spin.valueChanged.connect(lambda _=None: self._reload_sample_columns())

        # ── AI 辅助输入 ──
        g_ai = QGroupBox("AI 辅助")
        g_ai.setCheckable(True)
        g_ai.setChecked(False)  # 默认折叠
        style_group_card(g_ai)
        ai_layout = QVBoxLayout(g_ai)
        ai_layout.setContentsMargins(18, 16, 18, 18)
        ai_layout.setSpacing(10)
        ai_intro = QLabel("用自然语言描述规则，AI 自动生成条件树：")
        ai_intro.setWordWrap(True)
        ai_layout.addWidget(ai_intro)
        self.ai_input = QTextEdit()
        self.ai_input.setPlaceholderText("例：当系统列为'消防'时，专业必须为'给排水'")
        self.ai_input.setMaximumHeight(72)
        ai_layout.addWidget(self.ai_input)
        ai_row = QHBoxLayout()
        ai_row.setSpacing(8)
        self.ai_parse_btn = QPushButton("AI 解析 ▷")
        self.ai_parse_btn.clicked.connect(self._on_ai_parse)
        ai_row.addWidget(self.ai_parse_btn)
        ai_row.addSpacing(6)
        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        self.ai_status.setStyleSheet("color: #6b7280; font-size: 11px;")
        ai_row.addWidget(self.ai_status, 1)
        ai_row.addStretch()
        ai_layout.addLayout(ai_row)
        inner_layout.addWidget(g_ai)

        self.desc_edit = QTextEdit()
        self.desc_edit.setPlaceholderText("规则说明（解锁编辑后可修改）")
        self.desc_edit.setMaximumHeight(80)
        inner_layout.addWidget(self.desc_edit)
        self.tree_editor = RuleTreeEditor(columns=[])
        self.tree_editor.setMinimumHeight(200)
        inner_layout.addWidget(self.tree_editor, stretch=1)

        self.btn_unlock = QPushButton("解锁编辑（需秘钥）")
        self.btn_unlock.clicked.connect(self._toggle_unlock)
        inner_layout.addWidget(self.btn_unlock)

        self.btn_add = QPushButton("新增规则")
        self.btn_add.clicked.connect(self._add_rule)
        self.import_template_combo = QComboBox()
        self.import_template_combo.addItem("模板1：基准列+case列", "template1")
        self.import_template_combo.addItem("模板2：多条件=>结果", "template2")
        self.import_template_combo.setMinimumWidth(170)
        self.btn_import_rules = QPushButton("导入规则表")
        self.btn_import_rules.clicked.connect(self._import_rules_by_template)
        self.btn_assign_set = QPushButton("加入规则集")
        self.btn_assign_set.clicked.connect(self._assign_selected_rules_to_set)
        self.btn_remove_from_set = QPushButton("移出规则集")
        self.btn_remove_from_set.clicked.connect(self._remove_selected_rules_from_set)
        self.btn_save = QPushButton("保存当前规则")
        self.btn_save.clicked.connect(self._save_rule)
        self.btn_del = QPushButton("删除当前规则")
        self.btn_del.clicked.connect(self._del_rule)
        action_grid = QGridLayout()
        action_grid.setHorizontalSpacing(8)
        action_grid.setVerticalSpacing(8)
        action_grid.addWidget(self.btn_add, 0, 0)
        action_grid.addWidget(self.btn_save, 0, 1)
        action_grid.addWidget(self.btn_del, 0, 2)

        action_grid.addWidget(self.import_template_combo, 1, 0)
        action_grid.addWidget(self.btn_import_rules, 1, 1)
        action_grid.addWidget(self.btn_assign_set, 1, 2)
        action_grid.addWidget(self.btn_remove_from_set, 2, 2)

        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        action_grid.setColumnStretch(2, 1)
        inner_layout.addLayout(action_grid)

        scroll.setWidget(inner)
        right_layout.addWidget(scroll)
        self._apply_edit_lock()
        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 5)
        split.setSizes([320, 780])
        layout.addWidget(split, stretch=1)

    def _apply_edit_lock(self):
        """根据解锁状态启用/禁用编辑控件。"""
        unlocked = bool(self._edit_unlocked)
        self.desc_edit.setReadOnly(not unlocked)
        # 条件树始终可查看/展开；仅在未解锁时禁用“添加/编辑/删除”等修改入口
        self.tree_editor.setEnabled(True)
        if hasattr(self.tree_editor, "set_editable"):
            self.tree_editor.set_editable(unlocked)
        self.btn_add.setEnabled(unlocked)
        self.import_template_combo.setEnabled(unlocked)
        self.btn_import_rules.setEnabled(unlocked)
        self.btn_assign_set.setEnabled(unlocked)
        self.btn_remove_from_set.setEnabled(unlocked)
        self.btn_save.setEnabled(unlocked)
        self.btn_del.setEnabled(unlocked)
        self.btn_unlock.setText("锁定编辑" if unlocked else "解锁编辑（需秘钥）")

    def _import_rules_by_template(self):
        tpl = self.import_template_combo.currentData() or "template1"
        if tpl == "template2":
            self._import_rules_template2()
            return
        self._import_rules_template1()

    def _toggle_unlock(self):
        if self._edit_unlocked:
            self._edit_unlocked = False
            self._apply_edit_lock()
            return
        key, ok = self._ask_text(
            title="解锁规则库编辑",
            label="请输入秘钥：",
            echo_mode=QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        expected = get_rules_edit_key()
        if not expected or expected.strip() == "":
            QMessageBox.warning(self, "提示", "未配置规则库编辑秘钥。请设置环境变量后重启程序。")
            return
        if key.strip() != expected:
            QMessageBox.warning(self, "提示", "秘钥错误，无法解锁编辑。")
            return
        self._edit_unlocked = True
        self._apply_edit_lock()
        if expected == DEFAULT_RULES_EDIT_KEY:
            QMessageBox.information(
                self,
                "提示",
                "你正在使用默认秘钥 admin。\n建议通过环境变量 CHECKLISTTOOL_RULES_EDIT_KEY 设置为你自己的秘钥。",
            )

    def _load_rules(self):
        """从文件重新加载规则并刷新列表。"""
        self._engine.load_rules()
        existing_ids = {s.set_id for s in self._engine.get_rule_sets()}
        new_ids = existing_ids - self._known_set_ids
        self._known_set_ids = set(existing_ids)
        self._collapsed_set_ids = {sid for sid in self._collapsed_set_ids if sid in existing_ids}
        self._collapsed_set_ids.update(new_ids)
        self._refresh_list_from_engine()
        self._path_label.setText("规则库保存位置：" + (self._engine.rules_file or "—"))

    def _refresh_list_from_engine(self):
        """仅根据当前内存中的规则刷新列表（不读文件），用于新增后立即显示。"""
        prev_rule_id = self._current_rule_id
        self.rule_list.blockSignals(True)
        self.rule_list.clear()

        rules = self._engine.get_rules()
        rule_map = {r.rule_id: r for r in rules}
        grouped_ids = set()

        for rs in self._engine.get_rule_sets():
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

            if rs.set_id in self._collapsed_set_ids:
                continue

            for rid in item_rule_ids:
                r = rule_map[rid]
                child_item = QListWidgetItem(f"   └ {r.name} ({r.rule_id})")
                child_item.setData(
                    Qt.ItemDataRole.UserRole,
                    {
                        "item_type": "rule",
                        "rule_id": r.rule_id,
                    },
                )
                self.rule_list.addItem(child_item)

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

        self.rule_list.blockSignals(False)

        if prev_rule_id:
            self._select_rule_item(prev_rule_id)

    def _on_rule_selected(self, current: QListWidgetItem, previous):
        if not current:
            self._current_rule_id = None
            self._current_set_id = None
            return
        item_data = current.data(Qt.ItemDataRole.UserRole) or {}
        if item_data.get("item_type") == "set":
            self._current_rule_id = None
            self._current_set_id = item_data.get("set_id")
            return
        if item_data.get("item_type") != "rule":
            self._current_rule_id = None
            self._current_set_id = None
            return
        self._current_set_id = None
        rule_id = item_data.get("rule_id") or ""
        for r in self._engine.get_rules():
            if r.rule_id == rule_id:
                self._current_rule_id = rule_id
                self.desc_edit.setPlainText(r.description or "")
                self.tree_editor.load_node(r.root)
                return
        self._current_rule_id = None

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
        self._refresh_list_from_engine()

    def _select_rule_item(self, rule_id: str):
        for rs in self._engine.get_rule_sets():
            if rule_id in rs.rule_ids and rs.set_id in self._collapsed_set_ids:
                self._collapsed_set_ids.remove(rs.set_id)
                self._refresh_list_from_engine()
                break
        for i in range(self.rule_list.count()):
            item = self.rule_list.item(i)
            item_data = item.data(Qt.ItemDataRole.UserRole) or {}
            if item_data.get("item_type") == "rule" and item_data.get("rule_id") == rule_id:
                self.rule_list.setCurrentRow(i)
                return

    def _selected_rule_ids(self) -> list:
        ids = []
        for item in self.rule_list.selectedItems():
            item_data = item.data(Qt.ItemDataRole.UserRole) or {}
            if item_data.get("item_type") == "rule":
                rid = item_data.get("rule_id")
                if rid:
                    ids.append(rid)
        return list(dict.fromkeys(ids))

    def _selected_set_ids(self) -> list:
        ids = []
        for item in self.rule_list.selectedItems():
            item_data = item.data(Qt.ItemDataRole.UserRole) or {}
            if item_data.get("item_type") == "set":
                sid = item_data.get("set_id")
                if sid:
                    ids.append(sid)
        return list(dict.fromkeys(ids))

    def _show_rule_list_context_menu(self, pos):
        item = self.rule_list.itemAt(pos)
        if not item:
            return
        self.rule_list.setCurrentItem(item)
        item_data = item.data(Qt.ItemDataRole.UserRole) or {}
        item_type = item_data.get("item_type")
        if item_type not in ("rule", "set"):
            return

        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        action = menu.exec(self.rule_list.mapToGlobal(pos))
        if action != rename_action:
            return
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return

        if item_type == "rule":
            self._rename_rule_item(item_data)
            return
        self._rename_set_item(item_data)

    def _rename_rule_item(self, item_data: dict):
        rule_id = item_data.get("rule_id") or ""
        if not rule_id:
            return
        old_name = ""
        for r in self._engine.get_rules():
            if r.rule_id == rule_id:
                old_name = r.name
                break
        if not old_name:
            QMessageBox.warning(self, "提示", "未找到该规则。")
            return

        new_name, ok = self._ask_text(title="重命名规则", label="请输入新规则名称：")
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "规则名称不能为空。")
            return

        if not self._engine.rename_rule(rule_id, new_name):
            QMessageBox.warning(self, "提示", "重命名失败。")
            return
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "保存失败，请检查规则库路径是否可写。")
            return
        self._load_rules()
        self._select_rule_item(rule_id)
        self.rules_updated.emit()
        QMessageBox.information(self, "提示", f"规则已重命名：{old_name} -> {new_name}")

    def _rename_set_item(self, item_data: dict):
        set_id = item_data.get("set_id") or ""
        if not set_id:
            return
        old_name = ""
        for rs in self._engine.get_rule_sets():
            if rs.set_id == set_id:
                old_name = rs.name
                break
        if not old_name:
            QMessageBox.warning(self, "提示", "未找到该规则集。")
            return

        new_name, ok = self._ask_text(title="重命名规则集", label="请输入新规则集名称：")
        if not ok:
            return
        new_name = new_name.strip()
        if not new_name:
            QMessageBox.warning(self, "提示", "规则集名称不能为空。")
            return

        if not self._engine.rename_rule_set(set_id, new_name):
            QMessageBox.warning(self, "提示", "重命名失败：可能是名称重复。")
            return
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "保存失败，请检查规则库路径是否可写。")
            return
        self._load_rules()
        self.rules_updated.emit()
        QMessageBox.information(self, "提示", f"规则集已重命名：{old_name} -> {new_name}")

    def _assign_selected_rules_to_set(self):
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        rule_ids = self._selected_rule_ids()
        if not rule_ids:
            QMessageBox.warning(self, "提示", "请先选中至少一条规则（可 Ctrl/Shift 多选）。")
            return
        set_name, ok = self._ask_text(title="规则集名称", label="请输入规则集名称：")
        if not ok or not set_name.strip():
            return
        self._engine.assign_rules_to_set(rule_ids, set_name.strip(), source="manual")
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "保存规则集失败，请检查规则库路径是否可写。")
            return
        self._load_rules()
        self.rules_updated.emit()
        QMessageBox.information(self, "提示", f"已将 {len(rule_ids)} 条规则加入规则集：{set_name.strip()}。")

    def _remove_selected_rules_from_set(self):
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        rule_ids = self._selected_rule_ids()
        if not rule_ids:
            QMessageBox.warning(self, "提示", "请先选中至少一条规则（可 Ctrl/Shift 多选）。")
            return
        self._engine.unassign_rules_from_set(rule_ids)
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "保存规则集失败，请检查规则库路径是否可写。")
            return
        self._load_rules()
        self.rules_updated.emit()
        QMessageBox.information(self, "提示", f"已将 {len(rule_ids)} 条规则移出规则集。")

    # ------------------------------------------------------------------
    # AI 解析
    # ------------------------------------------------------------------
    def _on_ai_parse(self):
        """触发 AI 自然语言→规则树转换。"""
        user_input = self.ai_input.toPlainText().strip()
        if not user_input:
            QMessageBox.warning(self, "提示", "请输入规则描述。")
            return
        if not parse_natural_language_to_rule:
            QMessageBox.warning(self, "提示", "AI 模块未加载，请检查依赖。")
            return

        # 收集当前可用的列名（优先从示例文件，其次从编辑器已有列）
        columns = list(self.tree_editor.columns or [])
        if not columns and self.sample_file.path():
            try:
                kwargs = {}
                hr = self.sample_header_rows_spin.value()
                st = self.sample_skip_top_rows_spin.value()
                if hr > 0:
                    kwargs["header_rows"] = hr
                if st > 0:
                    kwargs["skip_top_rows"] = st
                _, cols, _ = load_table_from_file(self.sample_file.path(), **kwargs)
                columns = list(cols or [])
            except Exception:
                pass

        self.ai_parse_btn.setEnabled(False)
        self.ai_parse_btn.setText("解析中...")
        self.ai_status.setText("正在调用 AI...")

        self._ai_worker = _AiParseWorker(user_input, columns)
        self._ai_worker.finished.connect(self._on_ai_parse_finished)
        self._ai_worker.error.connect(self._on_ai_parse_error)
        self._ai_worker.start()

    def _on_ai_parse_finished(self, result: dict):
        """AI 解析成功，填充到编辑器。"""
        self.ai_parse_btn.setEnabled(True)
        self.ai_parse_btn.setText("AI 解析 ▷")
        self.ai_status.setText("解析完成，请检查并保存。")
        self.ai_status.setStyleSheet("color: #2a7; font-size: 11px;")

        rule_name = str(result.get("rule_name", "") or "").strip()
        description = str(result.get("description", "") or "").strip()
        target_columns = list(result.get("target_columns", []) or [])
        root_dict = result.get("root")

        if not root_dict:
            QMessageBox.warning(self, "提示", "AI 返回的结果中缺少条件树，请调整描述重试。")
            return

        # 填入描述
        if description:
            self.desc_edit.setPlainText(description)

        # 构造 RuleNode
        try:
            from core.rules import RuleNode
            root = RuleNode.from_dict(root_dict)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"条件树解析失败：{e}")
            return

        # 填入规则名称（如果有当前选中规则则用于后续保存）
        if rule_name and self._current_rule_id:
            for r in self._engine.get_rules():
                if r.rule_id == self._current_rule_id:
                    r.name = rule_name
                    r.root = root
                    r.description = description
                    r.target_columns = target_columns
                    break

        # 刷新条件树
        self.tree_editor.load_node(root)

        # 若 AI 组是折叠状态，展开它以便用户看到结果
        g_ai = self.findChild(QGroupBox, "AI 辅助")
        if g_ai is None:
            for child in self.findChildren(QGroupBox):
                if child.title() == "AI 辅助":
                    g_ai = child
                    break
        if g_ai and not g_ai.isChecked():
            g_ai.setChecked(True)

    def _on_ai_parse_error(self, err: str):
        """AI 解析失败。"""
        self.ai_parse_btn.setEnabled(True)
        self.ai_parse_btn.setText("AI 解析 ▷")
        self.ai_status.setText(f"失败：{err}")
        self.ai_status.setStyleSheet("color: #c33; font-size: 11px;")

    def _add_rule(self):
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        name, ok = self._ask_text(title="新增规则", label="规则名称：")
        if not ok or not name.strip():
            return
        rule = ValidationRule(rule_id="", name=name.strip(), description="", root=RuleNode())
        self._engine.add_rule(rule)
        if not self._engine.save_rules():
            QMessageBox.warning(
                self, "提示",
                "规则保存失败。请检查程序目录或用户目录「清单对比校审工具/rules」是否可写。",
            )
            return
        self._path_label.setText("规则库保存位置：" + (self._engine.rules_file or "—"))
        # 从内存刷新列表，确保新规则立即显示
        self._refresh_list_from_engine()
        new_id = rule.rule_id
        self._select_rule_item(new_id)
        if self._current_rule_id != new_id:
            self._current_rule_id = new_id
            self.desc_edit.setPlainText(rule.description or "")
            self.tree_editor.load_node(rule.root)
        self.rules_updated.emit()
        QMessageBox.information(self, "提示", "已添加规则，请编辑条件树后点击“保存当前规则”。")

    def _save_rule(self):
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        if not self._current_rule_id:
            QMessageBox.warning(self, "提示", "请先选择一条规则。")
            return
        for r in self._engine.get_rules():
            if r.rule_id == self._current_rule_id:
                r.description = self.desc_edit.toPlainText().strip()
                r.root = self.tree_editor.get_root_node()
                self._engine.save_rules()
                self.rules_updated.emit()
                self._path_label.setText("规则库保存位置：" + (self._engine.rules_file or "—"))
                QMessageBox.information(self, "提示", "已保存到：%s" % (self._engine.rules_file or ""))
                return
        QMessageBox.warning(self, "提示", "未找到当前规则。")

    def _import_rules_template1(self):
        """
        模板1导入：
        - 一列基准列（如 base_name）
        - 若干 case 列（如 case1, case2, case3）
        每行生成一条独立规则：base_col=base_val -> case_col1=v1 且 case_col2=v2 ...
        """
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        if not load_table_from_file:
            QMessageBox.warning(self, "提示", "缺少表格解析模块，无法导入。")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择规则表（模板1）",
            "",
            "Excel 文件 (*.xlsx *.xls);;所有文件 (*.*)",
        )
        if not path:
            return

        try:
            kwargs = {}
            hr = self.sample_header_rows_spin.value()
            st = self.sample_skip_top_rows_spin.value()
            if hr > 0:
                kwargs["header_rows"] = hr
            if st > 0:
                kwargs["skip_top_rows"] = st
            df, cols, _ = load_table_from_file(path, **kwargs)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"读取规则表失败：{e}")
            return

        usable_cols = [c for c in (cols or []) if c != "__source_row__"]
        if len(usable_cols) < 2:
            QMessageBox.warning(self, "提示", "规则表至少需要 2 列：1 列基准列 + 至少 1 列 case 列。")
            return

        default_base_col = "base_name" if "base_name" in usable_cols else usable_cols[0]
        base_col, ok = QInputDialog.getItem(
            self,
            "选择基准列",
            "请选择基准列（例如 base_name）：",
            usable_cols,
            max(0, usable_cols.index(default_base_col)),
            False,
        )
        if not ok or not base_col:
            return

        case_cols = [c for c in usable_cols if c != base_col]
        if not case_cols:
            QMessageBox.warning(self, "提示", "未找到 case 列，请检查模板内容。")
            return

        created_count = 0
        skipped_rows = 0
        last_rule_id = None
        imported_rule_ids = []

        import os
        file_base_name = os.path.splitext(os.path.basename(path))[0]

        for i, row in df.iterrows():
            base_raw = row.get(base_col, None)
            base_val = "" if base_raw is None else str(base_raw).strip()
            if base_val == "":
                skipped_rows += 1
                continue

            leaves = []
            for case_col in case_cols:
                v_raw = row.get(case_col, None)
                v = "" if v_raw is None else str(v_raw).strip()
                if v == "":
                    continue
                leaves.append(RuleNode(field=case_col, operator="eq", value=v, logic="and", children=[]))

            if not leaves:
                skipped_rows += 1
                continue

            source_row = row.get("__source_row__", None)
            row_mark = str(source_row).strip() if source_row is not None and str(source_row).strip() else str(int(i) + 2)
            rule_name = f"导入模板1_{file_base_name}_{base_val}_R{row_mark}"
            row_case_cols = [n.field for n in leaves]

            rule = ValidationRule(
                rule_id="",
                name=rule_name,
                description=f"模板1导入：基准列={base_col}, 基准值={base_val}",
                root=RuleNode(field=base_col, operator="eq", value=base_val, logic="and", children=leaves),
                target_columns=row_case_cols,
            )
            self._engine.add_rule(rule)
            created_count += 1
            last_rule_id = rule.rule_id
            imported_rule_ids.append(rule.rule_id)

        if created_count == 0:
            QMessageBox.warning(self, "提示", "未导入任何规则：请检查基准列和 case 列是否有有效值。")
            return

        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "导入后保存失败，请检查规则库路径是否可写。")
            return

        self._engine.assign_rules_to_new_import_set(
            imported_rule_ids,
            base_name=f"导入模板1_{file_base_name}",
            source="import_template1",
        )
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "规则集保存失败，请检查规则库路径是否可写。")
            return

        self._path_label.setText("规则库保存位置：" + (self._engine.rules_file or "—"))
        self._refresh_list_from_engine()
        self.set_columns_for_editor(usable_cols)

        if last_rule_id:
            self._select_rule_item(last_rule_id)

        self.rules_updated.emit()
        QMessageBox.information(
            self,
            "提示",
            f"导入完成：新增规则 {created_count} 条。\n"
            f"跳过无效行: {skipped_rows} 条。",
        )

    def _import_rules_template2(self):
        """
        模板2导入：
        - 多个条件列（如 condition1/2/3）
        - 1个结果列（如 result）
        每行生成一条独立规则：当所有条件列同时满足该行值时，结果列必须等于该行值。
        """
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        if not load_table_from_file:
            QMessageBox.warning(self, "提示", "缺少表格解析模块，无法导入。")
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择规则表（模板2）",
            "",
            "Excel 文件 (*.xlsx *.xls);;所有文件 (*.*)",
        )
        if not path:
            return

        try:
            kwargs = {}
            hr = self.sample_header_rows_spin.value()
            st = self.sample_skip_top_rows_spin.value()
            if hr > 0:
                kwargs["header_rows"] = hr
            if st > 0:
                kwargs["skip_top_rows"] = st
            df, cols, _ = load_table_from_file(path, **kwargs)
        except Exception as e:
            QMessageBox.warning(self, "提示", f"读取规则表失败：{e}")
            return

        usable_cols = [c for c in (cols or []) if c != "__source_row__"]
        if len(usable_cols) < 2:
            QMessageBox.warning(self, "提示", "规则表至少需要 2 列：条件列 + 结果列。")
            return

        default_result_col = "result" if "result" in usable_cols else usable_cols[-1]
        result_col, ok = QInputDialog.getItem(
            self,
            "选择结果列",
            "请选择结果列（例如 result）：",
            usable_cols,
            max(0, usable_cols.index(default_result_col)),
            False,
        )
        if not ok or not result_col:
            return

        condition_cols = [c for c in usable_cols if c != result_col]
        if not condition_cols:
            QMessageBox.warning(self, "提示", "未找到条件列，请检查模板内容。")
            return

        created_count = 0
        skipped_rows = 0
        last_rule_id = None
        imported_rule_ids = []

        import os
        file_base_name = os.path.splitext(os.path.basename(path))[0]

        for i, row in df.iterrows():
            cond_pairs = []
            for c in condition_cols:
                raw = row.get(c, None)
                val = "" if raw is None else str(raw).strip()
                if val == "":
                    continue
                cond_pairs.append((c, val))

            result_raw = row.get(result_col, None)
            result_val = "" if result_raw is None else str(result_raw).strip()

            if not cond_pairs or result_val == "":
                skipped_rows += 1
                continue

            # 用“链式蕴含”表达多条件同时满足时约束结果：
            # c1=v1 -> (c2=v2 -> (... -> result=rv))
            node = RuleNode(field=result_col, operator="eq", value=result_val, logic="and", children=[])
            for cond_col, cond_val in reversed(cond_pairs):
                node = RuleNode(field=cond_col, operator="eq", value=cond_val, logic="and", children=[node])

            source_row = row.get("__source_row__", None)
            row_mark = str(source_row).strip() if source_row is not None and str(source_row).strip() else str(int(i) + 2)
            cond_mark = "_".join(f"{k}={v}" for k, v in cond_pairs)
            if len(cond_mark) > 48:
                cond_mark = cond_mark[:48]
            rule_name = f"导入模板2_{file_base_name}_R{row_mark}_{cond_mark}"

            rule = ValidationRule(
                rule_id="",
                name=rule_name,
                description=(
                    f"模板2导入：当 {' 且 '.join(f'{k}={v}' for k, v in cond_pairs)} 时，"
                    f"{result_col} 必须等于 {result_val}"
                ),
                root=node,
                target_columns=[result_col],
            )
            self._engine.add_rule(rule)
            created_count += 1
            last_rule_id = rule.rule_id
            imported_rule_ids.append(rule.rule_id)

        if created_count == 0:
            QMessageBox.warning(self, "提示", "未导入任何规则：请检查条件列与结果列是否有有效值。")
            return

        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "导入后保存失败，请检查规则库路径是否可写。")
            return

        self._engine.assign_rules_to_new_import_set(
            imported_rule_ids,
            base_name=f"导入模板2_{file_base_name}",
            source="import_template2",
        )
        if not self._engine.save_rules():
            QMessageBox.warning(self, "提示", "规则集保存失败，请检查规则库路径是否可写。")
            return

        self._path_label.setText("规则库保存位置：" + (self._engine.rules_file or "—"))
        self._refresh_list_from_engine()
        self.set_columns_for_editor(usable_cols)

        if last_rule_id:
            self._select_rule_item(last_rule_id)

        self.rules_updated.emit()
        QMessageBox.information(
            self,
            "提示",
            f"导入完成：新增规则 {created_count} 条。\n"
            f"跳过无效行: {skipped_rows} 条。",
        )

    def _del_rule(self):
        if not self._edit_unlocked:
            QMessageBox.warning(self, "提示", "规则库处于只读状态，请先点击“解锁编辑（需秘钥）”。")
            return
        selected_rule_ids = self._selected_rule_ids()
        selected_set_ids = self._selected_set_ids()

        if selected_rule_ids:
            if len(selected_rule_ids) > 1:
                msg = "确定删除这些规则？"
            else:
                msg = "确定删除该规则？"
            if QMessageBox.question(self, "确认", msg) != QMessageBox.StandardButton.Yes:
                return

            deleted = 0
            for rid in selected_rule_ids:
                if self._engine.remove_rule(rid):
                    deleted += 1
            self._engine.save_rules()
            self._current_rule_id = None
            self._current_set_id = None
            self._load_rules()
            self.rules_updated.emit()
            if deleted > 1:
                QMessageBox.information(self, "提示", f"已删除 {deleted} 条规则。")
            elif deleted == 1:
                QMessageBox.information(self, "提示", "已删除规则。")
            else:
                QMessageBox.warning(self, "提示", "未找到可删除的规则。")
            return

        if selected_set_ids:
            if len(selected_set_ids) > 1:
                msg = "确定删除这些规则集？"
            else:
                msg = "确定删除该规则集？"
            if QMessageBox.question(self, "确认", msg) != QMessageBox.StandardButton.Yes:
                return

            deleted = 0
            for sid in selected_set_ids:
                if self._engine.remove_rule_set(sid):
                    deleted += 1
            self._engine.save_rules()
            self._current_set_id = None
            self._load_rules()
            self.rules_updated.emit()
            if deleted > 1:
                QMessageBox.information(self, "提示", f"已删除 {deleted} 个规则集。")
            elif deleted == 1:
                QMessageBox.information(self, "提示", "已删除规则集。")
            else:
                QMessageBox.warning(self, "提示", "未找到可删除的规则集。")
            return

        if not self._current_rule_id and not self._current_set_id:
            QMessageBox.warning(self, "提示", "请先选择一条规则或规则集。")
            return

    def set_columns_for_editor(self, columns: list):
        """从外部传入当前文件的列名，供条件树选择字段。"""
        self.tree_editor.set_columns(columns or [])

    def _on_sample_file_selected(self, path: str):
        if not path or not load_table_from_file:
            return
        self._reload_sample_columns()

    def _reload_sample_columns(self):
        path = self.sample_file.path()
        if not path or not load_table_from_file:
            return
        try:
            kwargs = {}
            hr = self.sample_header_rows_spin.value()
            st = self.sample_skip_top_rows_spin.value()
            if hr > 0:
                kwargs["header_rows"] = hr
            if st > 0:
                kwargs["skip_top_rows"] = st
            _, cols, _ = load_table_from_file(path, **kwargs)
            self.set_columns_for_editor(cols or [])
        except Exception as e:
            QMessageBox.warning(self, "提示", f"无法读取表头：{e}")

    def _ask_text(self, title: str, label: str, echo_mode=QLineEdit.EchoMode.Normal):
        """统一文本输入弹窗：限制宽度，避免被长文本撑开。"""
        dlg = QInputDialog(self)
        dlg.setWindowTitle(title)
        dlg.setLabelText(label)
        dlg.setTextEchoMode(echo_mode)
        dlg.setMinimumWidth(360)
        dlg.setMaximumWidth(560)
        dlg.resize(420, 120)
        ok = dlg.exec() == QInputDialog.DialogCode.Accepted
        return dlg.textValue(), ok


