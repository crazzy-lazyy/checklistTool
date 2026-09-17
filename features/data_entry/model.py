# -*- coding: utf-8 -*-
"""
设备数据填写表格模型（项目首个 model/view 实现）。

- 分页加载：canFetchMore/fetchMore（id 键翻页，页 2000 行），不阻塞 UI
- 编辑：setData 仅改缓存（绝不阻塞），300ms 防抖后由 tab 后台批量写库
- 校验着色：BackgroundRole 优先级 违规 #FF9999 > 未进入验证 #FFD8A8 > 未保存 #E8F0FF
- 临时新行：负数 id，flush 成功后由 on_flush_results 重映射为真实 id
"""

from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor

COLOR_ERROR_FIELD = QColor("#FF9999")
COLOR_UNVALIDATED = QColor("#FFD8A8")
COLOR_DIRTY = QColor("#E8F0FF")

PAGE_SIZE = 2000
_TEMP_ID_BASE = -1


class ChecklistTableModel(QAbstractTableModel):
    """清单行分页编辑模型。"""

    row_committed = pyqtSignal(int)  # 行有编辑提交（行 id，临时行为负数）-> 校验调度
    flush_requested = pyqtSignal()  # 防抖到期 -> tab 执行批量写库
    page_requested = pyqtSignal(int, int)  # (after_id, limit) -> tab 取下一页

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns: List[str] = []
        self._row_ids: List[int] = []  # 按 id 升序（临时新行 id 为负数，始终在末尾）
        self._rows: Dict[int, dict] = {}  # id -> {"values": {col: str}, "dirty": bool}
        self._total_rows: int = 0
        self._fetching: bool = False
        self._pending: Dict[int, Dict[str, str]] = {}  # id -> {col: value} 未 flush 编辑
        self._violations: Dict[int, Dict[str, list]] = {}  # id -> {col: [RuleViolation]}
        self._unvalidated: set = set()  # 行 id
        self._temp_counter = _TEMP_ID_BASE - 1  # 下一个临时行 id
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(300)
        self._flush_timer.timeout.connect(self._on_flush_timeout)

    # ------------------------------------------------------------------
    # 基础接口
    # ------------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._row_ids)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def columns(self) -> List[str]:
        return list(self._columns)

    def row_id_at(self, row: int) -> Optional[int]:
        if 0 <= row < len(self._row_ids):
            return self._row_ids[row]
        return None

    def index_of_row_id(self, row_id: int) -> int:
        try:
            return self._row_ids.index(row_id)
        except ValueError:
            return -1

    def row_values(self, row_id: int) -> Dict[str, str]:
        """当前缓存的行值（含未 flush 编辑）。"""
        row = self._rows.get(row_id)
        if row is None:
            return {}
        return dict(row["values"])

    def row_dirty(self, row_id: int) -> bool:
        row = self._rows.get(row_id)
        return bool(row and row["dirty"])

    def loaded_count(self) -> int:
        return len(self._row_ids)

    def total_rows(self) -> int:
        return self._total_rows

    def loaded_row_ids(self) -> List[int]:
        return list(self._row_ids)

    def last_real_id(self) -> int:
        """已加载行中最后一个真实行 id（无则 0），用于继续翻页。"""
        real_ids = [i for i in self._row_ids if i > 0]
        return real_ids[-1] if real_ids else 0

    # ------------------------------------------------------------------
    # data / flags / setData
    # ------------------------------------------------------------------
    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row_id = self._row_ids[index.row()]
        col = self._columns[index.column()]
        row = self._rows.get(row_id)
        if row is None:
            return None
        value = row["values"].get(col, "")
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return value
        if role == Qt.ItemDataRole.BackgroundRole:
            viols = self._violations.get(row_id, {})
            if viols.get(col):
                return COLOR_ERROR_FIELD
            if row_id in self._unvalidated:
                return COLOR_UNVALIDATED
            if row["dirty"]:
                return COLOR_DIRTY
            return None
        if role == Qt.ItemDataRole.ToolTipRole:
            viols = self._violations.get(row_id, {})
            if viols.get(col):
                msgs = []
                for v in viols[col]:
                    if getattr(v, "message", ""):
                        msgs.append(v.message)
                    elif getattr(v, "rule_name", ""):
                        msgs.append(f"违反规则: {v.rule_name}")
                return "\n".join(dict.fromkeys(msgs))
            return None
        if role == Qt.ItemDataRole.UserRole:
            return row_id
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def setData(self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        row_id = self._row_ids[index.row()]
        col = self._columns[index.column()]
        row = self._rows.get(row_id)
        if row is None:
            return False
        new_value = "" if value is None else str(value)
        if row["values"].get(col, "") == new_value:
            return False
        row["values"][col] = new_value
        row["dirty"] = True
        self._pending.setdefault(row_id, {})[col] = new_value
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.BackgroundRole])
        self.row_committed.emit(row_id)
        self._flush_timer.start()
        return True

    # ------------------------------------------------------------------
    # 分页
    # ------------------------------------------------------------------
    def canFetchMore(self, parent=QModelIndex()) -> bool:
        return (
            not parent.isValid()
            and not self._fetching
            and self._total_rows > len(self._row_ids)
        )

    def fetchMore(self, parent=QModelIndex()) -> None:
        if parent.isValid() or self._fetching:
            return
        last_id = self._row_ids[-1] if self._row_ids else 0
        if self._row_ids and last_id < 0:  # 末尾存在临时新行时按最后一个真实行翻页
            real_ids = [i for i in self._row_ids if i > 0]
            last_id = real_ids[-1] if real_ids else 0
        self._fetching = True
        self.page_requested.emit(last_id, PAGE_SIZE)

    def apply_page(self, rows: List[dict], total: int) -> None:
        """追加一页（后台已按 id 升序返回）。"""
        self._total_rows = total
        self._fetching = False
        if not rows:
            return
        first = len(self._row_ids)
        last = first + len(rows) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        for r in rows:
            self._row_ids.append(r["id"])
            self._rows[r["id"]] = {"values": dict(r["values"]), "dirty": False}
        self.endInsertRows()

    def page_fetch_failed(self) -> None:
        """取页失败时复位标记，允许视图再次触发翻页。"""
        self._fetching = False

    def reset_model(self, columns: List[str], rows: List[dict], total: int) -> None:
        """切换清单/重建：重置列与首页数据。"""
        self.beginResetModel()
        self._columns = list(columns)
        self._row_ids = []
        self._rows = {}
        self._total_rows = total
        self._fetching = False
        self._pending = {}
        self._violations = {}
        self._unvalidated = set()
        self._temp_counter = _TEMP_ID_BASE - 1
        for r in rows:
            self._row_ids.append(r["id"])
            self._rows[r["id"]] = {"values": dict(r["values"]), "dirty": False}
        self.endResetModel()

    def set_columns(self, columns: List[str]) -> None:
        """列结构变化：值按键名保留，缺省列显示空串。"""
        self.beginResetModel()
        self._columns = list(columns)
        self._violations = {}
        self._unvalidated = set()
        self.endResetModel()

    # ------------------------------------------------------------------
    # 编辑 flush（tab 调用）
    # ------------------------------------------------------------------
    def _on_flush_timeout(self) -> None:
        self.flush_requested.emit()

    def has_pending(self) -> bool:
        return bool(self._pending)

    def take_pending(self) -> dict:
        """取出待写数据并清空缓冲：{"updates": [{"id","values"}], "inserts": [{"temp_id","values"}]}。"""
        updates: List[dict] = []
        inserts: List[dict] = []
        for row_id in list(self._pending.keys()):
            row = self._rows.get(row_id)
            if row is None:
                continue
            values = dict(row["values"])  # flush 时刻的完整行值
            if row_id < 0:
                inserts.append({"temp_id": row_id, "values": values})
            else:
                updates.append({"id": row_id, "values": values})
        self._pending = {}
        return {"updates": updates, "inserts": inserts}

    def on_flush_results(self, results: dict) -> None:
        """写库结果回填：{"updated": [row_dict], "inserted": {temp_id: row_dict}, "failed_updates": [id], "failed_inserts": [temp_id]}。"""
        for row in results.get("updated", []):
            self._rows[row["id"]]["dirty"] = False
            idx = self.index_of_row_id(row["id"])
            if idx >= 0:
                self.dataChanged.emit(
                    self.index(idx, 0), self.index(idx, self.columnCount() - 1),
                    [Qt.ItemDataRole.BackgroundRole],
                )
        inserted = results.get("inserted", {})
        for temp_id, row in inserted.items():
            idx = self.index_of_row_id(temp_id)
            if idx < 0:
                continue
            self._row_ids[idx] = row["id"]
            self._rows[row["id"]] = {"values": dict(row["values"]), "dirty": False}
            del self._rows[temp_id]
            self.dataChanged.emit(
                self.index(idx, 0), self.index(idx, self.columnCount() - 1),
                [Qt.ItemDataRole.BackgroundRole],
            )
        self._total_rows += len(inserted)
        # 失败的行保持 dirty，等待重试
        _ = results.get("failed_updates", []), results.get("failed_inserts", [])

    # ------------------------------------------------------------------
    # 行增删与粘贴（tab/视图调用）
    # ------------------------------------------------------------------
    def add_new_row(self) -> int:
        """末尾新增临时行，返回临时 id（负数）。"""
        self._temp_counter -= 1
        temp_id = self._temp_counter
        self.beginInsertRows(QModelIndex(), len(self._row_ids), len(self._row_ids))
        self._row_ids.append(temp_id)
        self._rows[temp_id] = {"values": {c: "" for c in self._columns}, "dirty": True}
        self.endInsertRows()
        return temp_id

    def remove_rows(self, row_ids: List[int]) -> None:
        """删除已入库行（后台已删除成功后调用）。"""
        for row_id in list(row_ids):
            idx = self.index_of_row_id(row_id)
            if idx < 0:
                continue
            self.beginRemoveRows(QModelIndex(), idx, idx)
            self._row_ids.pop(idx)
            self.endRemoveRows()
            self._rows.pop(row_id, None)
            self._pending.pop(row_id, None)
            self._violations.pop(row_id, None)
            self._unvalidated.discard(row_id)
        self._total_rows = max(0, self._total_rows - len(row_ids))

    def paste_cells(self, anchor_row: int, anchor_col: int, grid: List[List[str]]) -> None:
        """粘贴 TSV 网格（超出底部自动追加临时行；超出右边界忽略）。"""
        if not grid:
            return
        while anchor_row + len(grid) > len(self._row_ids):
            self.add_new_row()
        for dr, line in enumerate(grid):
            row_id = self._row_ids[anchor_row + dr]
            row = self._rows[row_id]
            for dc, value in enumerate(line):
                col_idx = anchor_col + dc
                if col_idx >= len(self._columns):
                    break
                col = self._columns[col_idx]
                if row["values"].get(col, "") == value:
                    continue
                row["values"][col] = value
                row["dirty"] = True
                self._pending.setdefault(row_id, {})[col] = value
        self.dataChanged.emit(
            self.index(anchor_row, anchor_col),
            self.index(min(anchor_row + len(grid) - 1, len(self._row_ids) - 1),
                       min(anchor_col + max(len(l) for l in grid) - 1, len(self._columns) - 1)),
            [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.BackgroundRole],
        )
        for row_id in set(self._row_ids[anchor_row:anchor_row + len(grid)]):
            self.row_committed.emit(row_id)
        self._flush_timer.start()

    # ------------------------------------------------------------------
    # 校验结果注入
    # ------------------------------------------------------------------
    def set_validation(self, row_id: int, violations_by_col: Dict[str, list], unvalidated: bool) -> None:
        idx = self.index_of_row_id(row_id)
        if idx < 0:
            return
        self._violations[row_id] = {c: v for c, v in violations_by_col.items() if v}
        if unvalidated:
            self._unvalidated.add(row_id)
        else:
            self._unvalidated.discard(row_id)
        self.dataChanged.emit(
            self.index(idx, 0), self.index(idx, self.columnCount() - 1),
            [Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ToolTipRole],
        )

    def clear_validation(self) -> None:
        self._violations = {}
        self._unvalidated = set()
        self.dataChanged.emit(
            self.index(0, 0),
            self.index(max(0, len(self._row_ids) - 1), max(0, len(self._columns) - 1)),
            [Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.ToolTipRole],
        )
