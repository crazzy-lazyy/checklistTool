# -*- coding: utf-8 -*-
"""
设备数据 SQLite 存储层（本机单用户填写）。

- 纯标准库 sqlite3，无 Qt 依赖；WAL + busy_timeout，同一时间一个实例写入
- 时间统一 UTC ISO："YYYY-MM-DDTHH:MM:SSZ"
- 行数据以 JSON 键值存储（键=列名），列结构随清单保存在 lists.columns_json
- 分页用 id 键（WHERE id>?）而非 OFFSET，配合 AUTOINCREMENT 保证稳定
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

try:
    from config import CHECKLIST_DB_FILE
except ImportError:
    CHECKLIST_DB_FILE = "user_data/checklist_db.sqlite"

from .errors import DataError

META_TEMPLATE_COLUMNS = "template_columns"
PAGE_SIZE = 2000


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    """打开数据库连接（每线程自开连接，不做跨线程共享）。"""
    conn = sqlite3.connect(db_path or CHECKLIST_DB_FILE, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """建表（幂等）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS lists (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT    NOT NULL,
            columns_json TEXT    NOT NULL DEFAULT '[]',
            source       TEXT    NOT NULL DEFAULT 'manual',
            created_at   TEXT    NOT NULL,
            updated_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS list_rows (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id       INTEGER NOT NULL REFERENCES lists(id) ON DELETE CASCADE,
            row_data_json TEXT    NOT NULL DEFAULT '{}',
            editor_id     TEXT    NOT NULL DEFAULT '',
            created_at    TEXT    NOT NULL,
            updated_at    TEXT    NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_list_rows_list_id ON list_rows(list_id, id);
        CREATE TABLE IF NOT EXISTS meta (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    conn.commit()


# -----------------------------------------------------------------------------
# 工具
# -----------------------------------------------------------------------------
def _validate_columns(columns: List[str]) -> List[str]:
    """校验列名：非空且不重复；非法抛 ValueError。"""
    cols = [str(c).strip() for c in columns]
    if not cols:
        raise ValueError("列名不能为空")
    seen = set()
    for c in cols:
        if not c:
            raise ValueError("列名不能为空")
        if c in seen:
            raise ValueError(f"列名重复: {c}")
        seen.add(c)
    return cols


def dedupe_columns(columns: List[str]) -> List[str]:
    """导入路径列名去重：重复列追加 _2/_3 后缀（对齐解析器行为）。"""
    out: List[str] = []
    counts: Dict[str, int] = {}
    for c in columns:
        c = str(c).strip()
        if c in counts:
            counts[c] += 1
            out.append(f"{c}_{counts[c]}")
        else:
            counts[c] = 1
            out.append(c)
    return out


def _normalize_values(values: Optional[Dict[str, Any]]) -> Dict[str, str]:
    """值归一化为字符串 dict：None/NaN -> 空串，其余 str() 原样保留。"""
    out: Dict[str, str] = {}
    for k, v in (values or {}).items():
        if v is None or (isinstance(v, float) and pd.isna(v)):
            out[str(k)] = ""
        else:
            out[str(k)] = str(v)
    return out


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "values": json.loads(row["row_data_json"]),
        "editor_id": row["editor_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# -----------------------------------------------------------------------------
# 清单
# -----------------------------------------------------------------------------
def create_list(conn: sqlite3.Connection, name: str, columns: List[str], source: str = "manual") -> dict:
    name = str(name).strip()
    if not name:
        raise ValueError("清单名称不能为空")
    columns = _validate_columns(columns)
    now = utc_now()
    cur = conn.execute(
        "INSERT INTO lists(name, columns_json, source, created_at, updated_at) VALUES(?,?,?,?,?)",
        (name, json.dumps(columns, ensure_ascii=False), source, now, now),
    )
    conn.commit()
    return get_list(conn, cur.lastrowid)


def get_list(conn: sqlite3.Connection, list_id: int) -> Optional[dict]:
    row = conn.execute("SELECT * FROM lists WHERE id=?", (list_id,)).fetchone()
    if row is None:
        return None
    d = {
        "id": row["id"],
        "name": row["name"],
        "columns": json.loads(row["columns_json"]),
        "source": row["source"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    d["row_count"] = conn.execute(
        "SELECT COUNT(*) FROM list_rows WHERE list_id=?", (list_id,)
    ).fetchone()[0]
    return d


def list_lists(conn: sqlite3.Connection) -> List[dict]:
    rows = conn.execute("SELECT id FROM lists ORDER BY name").fetchall()
    return [get_list(conn, r["id"]) for r in rows]


def rename_list(conn: sqlite3.Connection, list_id: int, new_name: str) -> dict:
    new_name = str(new_name).strip()
    if not new_name:
        raise ValueError("清单名称不能为空")
    cur = conn.execute(
        "UPDATE lists SET name=?, updated_at=? WHERE id=?",
        (new_name, utc_now(), list_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise DataError(f"清单不存在: {list_id}")
    return get_list(conn, list_id)


def update_columns(conn: sqlite3.Connection, list_id: int, columns: List[str]) -> dict:
    """整体替换列结构（插入/删除/调整顺序）；原行数据按列名匹配，缺省补空串。"""
    columns = _validate_columns(columns)
    cur = conn.execute(
        "UPDATE lists SET columns_json=?, updated_at=? WHERE id=?",
        (json.dumps(columns, ensure_ascii=False), utc_now(), list_id),
    )
    conn.commit()
    if cur.rowcount == 0:
        raise DataError(f"清单不存在: {list_id}")
    return get_list(conn, list_id)


def rename_column(conn: sqlite3.Connection, list_id: int, old_name: str, new_name: str) -> dict:
    """重命名列：全表 re-key 行数据（单事务），并更新列结构。"""
    new_name = str(new_name).strip()
    lst = get_list(conn, list_id)
    if lst is None:
        raise DataError(f"清单不存在: {list_id}")
    if not new_name:
        raise ValueError("列名不能为空")
    if old_name not in lst["columns"]:
        raise DataError(f"列不存在: {old_name}")
    if new_name in lst["columns"] and new_name != old_name:
        raise ValueError(f"列名重复: {new_name}")
    now = utc_now()
    with conn:
        rows = conn.execute(
            "SELECT id, row_data_json FROM list_rows WHERE list_id=?", (list_id,)
        ).fetchall()
        for r in rows:
            data = json.loads(r["row_data_json"])
            if old_name in data:
                data[new_name] = data.pop(old_name)
            conn.execute(
                "UPDATE list_rows SET row_data_json=?, updated_at=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), now, r["id"]),
            )
        new_cols = [new_name if c == old_name else c for c in lst["columns"]]
        conn.execute(
            "UPDATE lists SET columns_json=?, updated_at=? WHERE id=?",
            (json.dumps(new_cols, ensure_ascii=False), now, list_id),
        )
    return get_list(conn, list_id)


def delete_list(conn: sqlite3.Connection, list_id: int) -> bool:
    """删除清单（行级联删除）；返回是否存在。"""
    cur = conn.execute("DELETE FROM lists WHERE id=?", (list_id,))
    conn.commit()
    return cur.rowcount > 0


def copy_list(conn: sqlite3.Connection, list_id: int, new_name: str) -> dict:
    """复制清单（列结构 + 全部行数据）。"""
    lst = get_list(conn, list_id)
    if lst is None:
        raise DataError(f"清单不存在: {list_id}")
    new = create_list(conn, new_name, lst["columns"], source="copy")
    rows = get_all_rows(conn, list_id)
    insert_rows_batch(conn, new["id"], [r["values"] for r in rows], editor_id="")
    return get_list(conn, new["id"])


# -----------------------------------------------------------------------------
# 行
# -----------------------------------------------------------------------------
def insert_row(conn: sqlite3.Connection, list_id: int, values: Dict[str, Any], editor_id: str = "") -> dict:
    now = utc_now()
    cur = conn.execute(
        "INSERT INTO list_rows(list_id, row_data_json, editor_id, created_at, updated_at) VALUES(?,?,?,?,?)",
        (list_id, json.dumps(_normalize_values(values), ensure_ascii=False), editor_id, now, now),
    )
    conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (now, list_id))
    conn.commit()
    return get_row(conn, list_id, cur.lastrowid)


def insert_rows_batch(conn: sqlite3.Connection, list_id: int, rows: List[Dict[str, Any]], editor_id: str = "") -> List[int]:
    """批量插入（单事务），返回新增行 id 列表。"""
    now = utc_now()
    ids: List[int] = []
    with conn:
        for values in rows:
            cur = conn.execute(
                "INSERT INTO list_rows(list_id, row_data_json, editor_id, created_at, updated_at) VALUES(?,?,?,?,?)",
                (list_id, json.dumps(_normalize_values(values), ensure_ascii=False), editor_id, now, now),
            )
            ids.append(cur.lastrowid)
        conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (now, list_id))
    return ids


def get_row(conn: sqlite3.Connection, list_id: int, row_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM list_rows WHERE id=? AND list_id=?", (row_id, list_id)
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


def get_rows_page(
    conn: sqlite3.Connection, list_id: int, after_id: int = 0, limit: int = PAGE_SIZE
) -> Tuple[List[dict], int]:
    """分页读取：id 键翻页（WHERE id>after_id ORDER BY id），返回 (行列表, 总行数)。"""
    total = conn.execute(
        "SELECT COUNT(*) FROM list_rows WHERE list_id=?", (list_id,)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT * FROM list_rows WHERE list_id=? AND id>? ORDER BY id LIMIT ?",
        (list_id, after_id, limit),
    ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def get_all_rows(conn: sqlite3.Connection, list_id: int) -> List[dict]:
    """全量读取（导出/全表校验用）。"""
    rows = conn.execute(
        "SELECT * FROM list_rows WHERE list_id=? ORDER BY id", (list_id,)
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def update_row(
    conn: sqlite3.Connection, list_id: int, row_id: int, values: Dict[str, Any], editor_id: str = ""
) -> dict:
    now = utc_now()
    cur = conn.execute(
        "UPDATE list_rows SET row_data_json=?, editor_id=?, updated_at=? WHERE id=? AND list_id=?",
        (json.dumps(_normalize_values(values), ensure_ascii=False), editor_id, now, row_id, list_id),
    )
    if cur.rowcount == 0:
        raise DataError(f"行不存在: {row_id}")
    conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (now, list_id))
    conn.commit()
    return get_row(conn, list_id, row_id)


def update_rows_batch(
    conn: sqlite3.Connection,
    list_id: int,
    updates: List[dict],
    editor_id: str = "",
) -> List[dict]:
    """批量更新（单事务）：updates = [{"id": int, "values": dict}]；返回更新成功的行。"""
    now = utc_now()
    out: List[dict] = []
    with conn:
        for u in updates:
            cur = conn.execute(
                "UPDATE list_rows SET row_data_json=?, editor_id=?, updated_at=? WHERE id=? AND list_id=?",
                (
                    json.dumps(_normalize_values(u["values"]), ensure_ascii=False),
                    editor_id,
                    now,
                    u["id"],
                    list_id,
                ),
            )
            if cur.rowcount:
                out.append(get_row(conn, list_id, u["id"]))
        if out:
            conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (now, list_id))
    return out


def delete_row(conn: sqlite3.Connection, list_id: int, row_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM list_rows WHERE id=? AND list_id=?", (row_id, list_id)
    )
    conn.execute("UPDATE lists SET updated_at=? WHERE id=?", (utc_now(), list_id))
    conn.commit()
    return cur.rowcount > 0


# -----------------------------------------------------------------------------
# meta 与导出辅助
# -----------------------------------------------------------------------------
def get_meta(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row is not None else None


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_template_columns(conn: sqlite3.Connection) -> List[str]:
    raw = get_meta(conn, META_TEMPLATE_COLUMNS)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def set_template_columns(conn: sqlite3.Connection, columns: List[str]) -> None:
    columns = _validate_columns(columns)
    set_meta(conn, META_TEMPLATE_COLUMNS, json.dumps(columns, ensure_ascii=False))


def rows_to_df(rows: List[dict], columns: List[str]) -> pd.DataFrame:
    """行列表转 DataFrame：index = 行 id（供 RuleEngine 校验，RuleViolation.row_index 即行 id）。"""
    data = [{c: r["values"].get(c, "") for c in columns} for r in rows]
    if data:
        return pd.DataFrame(data, index=[r["id"] for r in rows], dtype=object)
    return pd.DataFrame(columns=columns, dtype=object)
