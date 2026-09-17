# -*- coding: utf-8 -*-
"""
规则条件树可视化编辑器（拖拽构建）。
将 RuleNode 树与 QTreeWidget 绑定，支持添加/删除/编辑节点，逻辑与/或。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QPushButton,
    QComboBox,
    QLineEdit,
    QLabel,
    QDialog,
    QFormLayout,
    QDialogButtonBox,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from core.rules import (
    RuleNode,
    RuleEngine,
    format_escaped_list,
    parse_escaped_list,
)


MAX_NODE_LABEL_CHARS = 120


def _op_label(op: str) -> str:
    m = {
        "eq": "等于 (=)",
        "ne": "不等于 (≠)",
        "gt": "大于 (>)",
        "ge": "大于等于 (≥)",
        "lt": "小于 (<)",
        "le": "小于等于 (≤)",
        "in": "属于/在列表中",
        "not_in": "不属于/不在列表中",
        "not_empty": "非空",
        "empty": "为空",
        "is_num": "是数字",
        "regex": "正则匹配",
        "contains": "包含",
    }
    return m.get(op, op)


def _logic_label(logic: str) -> str:
    return "且(and)" if logic == "and" else ("或(or)" if logic == "or" else str(logic))


def _format_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        # 列表值（如 in/not_in）更易读的展示
        return "[" + ", ".join(str(x) for x in value) + "]"
    return str(value)


def _elide_text(text: str, max_chars: int = MAX_NODE_LABEL_CHARS) -> str:
    """超长文本截断，避免树节点内容把界面撑宽。"""
    s = str(text or "")
    if max_chars <= 0 or len(s) <= max_chars:
        return s
    return s[: max_chars - 1] + "..."


def _format_leaf_brief(node: RuleNode) -> str:
    if not node or not getattr(node, "field", ""):
        return "根"
    v = _format_value(getattr(node, "value", None))
    v_part = f" {v}" if v else ""
    rn = (getattr(node, "rule_name", "") or "").strip()
    rn_part = f" | 规则名:{rn}" if rn else ""
    return f"{node.field} {_op_label(node.operator)}{v_part}{rn_part}"


def _collect_leaf_briefs(node: RuleNode, limit: int = 3) -> list:
    """收集若干叶子条件的简短预览，用于条件组节点的摘要显示。"""
    briefs = []
    stack = [node]
    while stack and len(briefs) < limit:
        n = stack.pop(0)
        children = list(getattr(n, "children", []) or [])
        if children:
            stack.extend(children)
        else:
            # 叶子：有 field 才算“具体条件”；否则跳过（根节点）
            if getattr(n, "field", ""):
                briefs.append(_format_leaf_brief(n))
    return briefs


def _count_conditions(node: RuleNode) -> int:
    """统计该节点下“叶子条件”的数量。"""
    if not node:
        return 0
    children = list(getattr(node, "children", []) or [])
    if children:
        return sum(_count_conditions(c) for c in children)
    return 1 if getattr(node, "field", "") else 0


class RuleNodeEditDialog(QDialog):
    """编辑单个条件节点：字段、运算符、值、逻辑。"""

    def __init__(self, node: RuleNode, columns: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑条件节点")
        # 限制弹窗宽度，防止超长字段名/值把窗口撑满屏。
        self.setMinimumWidth(520)
        self.setMaximumWidth(900)
        self.resize(680, 280)
        self.node = node
        self.columns = columns or []
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)
        self.field_combo = QComboBox()
        self.field_combo.setEditable(True)  # 可手动输入列名（如 系统|专业），无需先加载文件
        self.field_combo.setMinimumContentsLength(24)
        self.field_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.field_combo.addItem("（无/根节点）", "")
        for c in self.columns:
            self.field_combo.addItem(c, c)
        try:
            self.field_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        except Exception:
            pass
        layout.addRow("字段（可下拉选择或直接输入列名）:", self.field_combo)
        self.op_combo = QComboBox()
        for op in RuleEngine.OPERATORS:
            self.op_combo.addItem(_op_label(op), op)
        self.op_combo.currentIndexChanged.connect(self._sync_value_input_state)
        layout.addRow("运算符:", self.op_combo)
        self.value_edit = QLineEdit()
        self.value_edit.setPlaceholderText(
            r"值（列表用逗号分隔；值内逗号写作 \,；is_num/为空/非空无需填写）"
        )
        layout.addRow("值:", self.value_edit)
        self.logic_combo = QComboBox()
        self.logic_combo.addItem("且 (and)", "and")
        self.logic_combo.addItem("或 (or)", "or")
        layout.addRow("子节点逻辑:", self.logic_combo)
        self.rule_name_edit = QLineEdit()
        self.rule_name_edit.setPlaceholderText("规则名称（用于报告）")
        layout.addRow("规则名称:", self.rule_name_edit)

        # 回填：有预选项则选中，否则设为可编辑的当前值
        idx = self.field_combo.findData(self.node.field)
        if idx >= 0:
            self.field_combo.setCurrentIndex(idx)
        elif self.node.field:
            self.field_combo.setCurrentText(self.node.field)
        idx = self.op_combo.findData(self.node.operator)
        if idx >= 0:
            self.op_combo.setCurrentIndex(idx)
        if self.node.value is not None:
            if isinstance(self.node.value, list):
                self.value_edit.setText(format_escaped_list(self.node.value))
            else:
                self.value_edit.setText(str(self.node.value))
        self.logic_combo.setCurrentIndex(0 if self.node.logic == "and" else 1)
        self.rule_name_edit.setText(self.node.rule_name or "")
        self._sync_value_input_state()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def _resolve_field_value(self) -> str:
        """解析字段值：优先使用下拉项 data；仅在手动输入自定义列名时回退文本。"""
        idx = self.field_combo.currentIndex()
        if idx >= 0:
            data = self.field_combo.itemData(idx)
            if data is not None:
                # 包含 ""（无/根节点）在内，均按 data 作为真实值
                return str(data)
        # 未命中下拉项（手动输入）时，使用文本
        return self.field_combo.currentText().strip()

    @staticmethod
    def _operator_requires_value(op: str) -> bool:
        return op in {"eq", "in", "not_in"}

    def _sync_value_input_state(self, _index=None):
        """无参数运算符禁用值输入框，避免误以为 is_num 需要比较值。"""
        op = self.op_combo.currentData() or "eq"
        self.value_edit.setEnabled(op not in {"is_num", "empty", "not_empty"})

    def _on_accept(self):
        # 仅当该节点配置了字段条件时，才校验“值是否必填”。
        field_val = self._resolve_field_value()
        op = self.op_combo.currentData() or "eq"
        raw_val = self.value_edit.text().strip()
        if (field_val or "").strip() and self._operator_requires_value(op) and raw_val == "":
            QMessageBox.warning(self, "提示", "当前运算符需要填写“值”，否则条件无效。")
            return
        self.accept()

    def get_node(self) -> RuleNode:
        val = self.value_edit.text().strip()
        op = self.op_combo.currentData() or "eq"
        if op in {"in", "not_in"}:
            value = parse_escaped_list(val) if val else None
        else:
            value = val if val else None
        # 优先用下拉选中项 data；手动输入时回退文本
        field_val = self._resolve_field_value()
        self.node.field = field_val or ""
        self.node.operator = op
        self.node.value = value
        self.node.logic = self.logic_combo.currentData() or "and"
        self.node.rule_name = self.rule_name_edit.text().strip()
        return self.node


def _node_to_item(node: RuleNode) -> QTreeWidgetItem:
    """将 RuleNode 转为 QTreeWidgetItem（仅当前节点，不递归）。"""
    if node.children:
        rn = (node.rule_name or "").strip()
        count = _count_conditions(node)
        briefs = _collect_leaf_briefs(node, limit=3)
        briefs_part = (" | 示例: " + "；".join(briefs) + (" …" if count > len(briefs) else "")) if briefs else ""
        rn_part = f" | 规则名:{rn}" if rn else ""
        full_label = f"条件组({_logic_label(node.logic)}) | 条件数:{count}{rn_part}{briefs_part}"
    else:
        full_label = _format_leaf_brief(node)
    item = QTreeWidgetItem([_elide_text(full_label)])
    item.setToolTip(0, full_label)
    item.setData(0, Qt.ItemDataRole.UserRole, node)
    return item


def _build_children(item: QTreeWidgetItem, node: RuleNode):
    for c in node.children:
        child_item = _node_to_item(c)
        item.addChild(child_item)
        _build_children(child_item, c)


def _item_to_node(item: QTreeWidgetItem) -> RuleNode:
    """从 QTreeWidgetItem 恢复 RuleNode（递归）。"""
    node = item.data(0, Qt.ItemDataRole.UserRole)
    if not isinstance(node, RuleNode):
        node = RuleNode()
    node.children = []
    for i in range(item.childCount()):
        node.children.append(_item_to_node(item.child(i)))
    return node


class RuleTreeEditor(QWidget):
    """规则条件树编辑器：树形展示，支持添加子节点、编辑、删除。"""

    def __init__(self, columns: list = None, parent=None):
        super().__init__(parent)
        self.columns = columns or []
        self._editable = True
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["条件"])
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.tree)
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("添加根/子条件")
        self.add_btn.clicked.connect(self._add_node)
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.clicked.connect(self._edit_node)
        self.del_btn = QPushButton("删除")
        self.del_btn.clicked.connect(self._del_node)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.edit_btn)
        btn_layout.addWidget(self.del_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)
        self.set_editable(self._editable)

    def set_editable(self, editable: bool):
        """控制是否允许修改条件树（查看/展开不受影响）。"""
        self._editable = bool(editable)
        if hasattr(self, "add_btn"):
            self.add_btn.setEnabled(self._editable)
        if hasattr(self, "edit_btn"):
            self.edit_btn.setEnabled(self._editable)
        if hasattr(self, "del_btn"):
            self.del_btn.setEnabled(self._editable)

    def set_columns(self, columns: list):
        self.columns = list(columns)

    def load_node(self, root: RuleNode):
        """从 RuleNode 加载树。"""
        self.tree.clear()
        if not root:
            return
        item = _node_to_item(root)
        _build_children(item, root)
        self.tree.addTopLevelItem(item)
        item.setExpanded(True)
        self.tree.expandAll()

    def get_root_node(self) -> RuleNode:
        """导出当前树为 RuleNode。"""
        if self.tree.topLevelItemCount() == 0:
            return RuleNode()
        item = self.tree.topLevelItem(0)
        return _item_to_node(item)

    def _current_item(self) -> QTreeWidgetItem:
        return self.tree.currentItem()

    def _add_node(self):
        """添加子节点：若已选中某节点则在其下添加子条件，否则添加为顶层节点。"""
        if not self._editable:
            return
        item = self._current_item()
        node = RuleNode(field="", operator="eq", value=None, logic="and")
        new_item = _node_to_item(node)
        if item:
            item.addChild(new_item)
            item.setExpanded(True)
        else:
            self.tree.addTopLevelItem(new_item)
        self.tree.setCurrentItem(new_item)
        self._edit_node()

    def _edit_node(self):
        if not self._editable:
            return
        item = self._current_item()
        if not item:
            return
        node = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(node, RuleNode):
            node = RuleNode()
        dlg = RuleNodeEditDialog(node, self.columns, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.get_node()
            display_item = _node_to_item(node)
            item.setText(0, display_item.text(0))
            item.setToolTip(0, display_item.toolTip(0))
            item.setData(0, Qt.ItemDataRole.UserRole, node)

    def _del_node(self):
        if not self._editable:
            return
        item = self._current_item()
        if not item:
            return
        parent = item.parent()
        if parent:
            parent.removeChild(item)
        else:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(item))
