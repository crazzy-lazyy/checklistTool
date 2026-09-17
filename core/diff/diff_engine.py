# -*- coding: utf-8 -*-
"""
清单差异对比引擎
参考 align + 规范化比较：仅在对齐后的对比列上做规范化再逐列比较，避免相同数值被误标为修改。
支持不匹配数据总数等统计。
"""

from typing import List, Optional, Set, Dict, Any, Tuple
import pandas as pd
import numpy as np

try:
    from config import DIFF_ADDED, DIFF_DELETED, DIFF_MODIFIED, DIFF_UNCHANGED
except ImportError:
    DIFF_ADDED = "added"
    DIFF_DELETED = "deleted"
    DIFF_MODIFIED = "modified"
    DIFF_UNCHANGED = "unchanged"

NUMERIC_TOLERANCE = 1e-9
NUMERIC_FORMAT = "%.10g"


def _canonical_scalar(v) -> str:
    """将单值规范为可比较字符串：能转浮点则按容差规范化后格式化为统一小数表示。"""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    try:
        f = float(v)
        if pd.isna(f):
            return ""
        # 统一成相同精度，避免科学计数法与小数混用导致误判
        return NUMERIC_FORMAT % f
    except (TypeError, ValueError):
        return s


def _canonical_series(s: pd.Series) -> pd.Series:
    """对一列做规范化。"""
    return s.map(_canonical_scalar)


def _canonical_df(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    """仅对指定列做规范化，返回与 df 同索引的 DataFrame。"""
    out = pd.DataFrame(index=df.index)
    for c in cols:
        if c in df.columns:
            out[c] = _canonical_series(df[c])
        else:
            out[c] = ""
    return out


class DiffResult:
    """
    单次对比结果：行数据 + __diff_type__ + __changed_fields__；
    可选统计：count_added, count_deleted, count_modified, count_unchanged, total_mismatched。
    """

    def __init__(self):
        self.rows: List[Dict[str, Any]] = []
        self.columns: List[str] = []
        self.key_columns: List[str] = []
        self.compare_columns: List[str] = []
        self.count_added: int = 0
        self.count_deleted: int = 0
        self.count_modified: int = 0
        self.count_unchanged: int = 0

    @property
    def total_mismatched(self) -> int:
        """不匹配的数据总数（新增 + 删除 + 修改）。"""
        return self.count_added + self.count_deleted + self.count_modified

    def to_dataframe(self) -> pd.DataFrame:
        """返回完整结果表：数据列在前（与源表一致），最后为 __diff_type__、__changed_fields__、__changes_detail__。"""
        diff_cols = ["__diff_type__", "__changed_fields__", "__changes_detail__"]
        if not self.rows:
            return pd.DataFrame(columns=self.columns + diff_cols)
        df = pd.DataFrame(self.rows)
        if "__diff_type__" not in df.columns:
            df["__diff_type__"] = DIFF_UNCHANGED
        if "__changed_fields__" not in df.columns:
            df["__changed_fields__"] = [[]] * len(df)
        if "__changes_detail__" not in df.columns:
            df["__changes_detail__"] = ""
        # 数据列 = result.columns 中非差异列（保持顺序）+ 行里多出的非差异列，避免行号限制等导致只显示 3 列
        data_cols_ordered = [c for c in self.columns if c not in diff_cols]
        for c in data_cols_ordered:
            if c not in df.columns:
                df[c] = ""
        extra_data = [c for c in df.columns if c not in diff_cols and c not in data_cols_ordered]
        out_cols = data_cols_ordered + extra_data + [x for x in diff_cols if x in df.columns]
        df = df[out_cols]
        return df


class DiffEngine:
    """
    两表对比：键列匹配行，对比列做规范化后逐列比较（align 思路），
    仅当规范化结果不同才标为 modified，并记录 __changed_fields__。
    """

    def __init__(
        self,
        key_columns: Optional[List[str]] = None,
        compare_columns: Optional[List[str]] = None,
        ignore_case: bool = False,
        trim_strings: bool = True,
    ):
        self.key_columns = key_columns or []
        self.compare_columns = compare_columns or []
        self.ignore_case = ignore_case
        self.trim_strings = trim_strings

    def _row_key(self, row: pd.Series, keys: List[str]) -> tuple:
        return tuple(_canonical_scalar(row.get(k, "")) for k in keys)

    def compare_two_tables(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        keys: Optional[List[str]] = None,
        compare_cols: Optional[List[str]] = None,
    ) -> DiffResult:
        keys = keys or self.key_columns
        all_cols = list(df_a.columns)
        for c in df_b.columns:
            if c not in all_cols:
                all_cols.append(c)
        if not compare_cols:
            compare_cols = [
                c for c in all_cols
                if c not in ("__diff_type__", "__changed_fields__", "__row_index__", "__source_row__")
                and c in df_b.columns and c in df_a.columns
            ]

        result = DiffResult()
        result.columns = all_cols
        result.key_columns = keys
        result.compare_columns = compare_cols

        if df_a.empty and df_b.empty:
            return result

        if not keys:
            return self._compare_by_position(df_a, df_b, compare_cols, result)

        # 键列匹配（支持重复键）：key -> [row_indices]
        def _index_by_key(df: pd.DataFrame, ks: List[str]) -> Dict[tuple, List[int]]:
            m: Dict[tuple, List[int]] = {}
            for i in range(len(df)):
                k = self._row_key(df.iloc[i], ks)
                m.setdefault(k, []).append(i)
            return m

        key_to_a = _index_by_key(df_a, keys)
        key_to_b = _index_by_key(df_b, keys)

        # 1) 仅在 B 中有的键 -> added
        # 2) 仅在 A 中有的键 -> deleted
        # 3) 两边都有的键 -> 对齐后对 compare_cols 做规范化再比较
        common_keys = [k for k in key_to_b if k in key_to_a]
        only_a = [k for k in key_to_a if k not in key_to_b]
        only_b = [k for k in key_to_b if k not in key_to_a]

        # deleted（仅在 A 中存在的键：其所有行都为 deleted）
        for k in only_a:
            for i in key_to_a.get(k, []):
                row_dict = df_a.iloc[i].to_dict()
                row_dict["__diff_type__"] = DIFF_DELETED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_deleted += 1

        # added（仅在 B 中存在的键：其所有行都为 added）
        for k in only_b:
            for j in key_to_b.get(k, []):
                row_dict = df_b.iloc[j].to_dict()
                row_dict["__diff_type__"] = DIFF_ADDED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_added += 1

        # 对齐后比较：对每个 common_key，按出现顺序配对 min(lenA, lenB) 行
        if not common_keys:
            return result

        idx_a: List[int] = []
        idx_b: List[int] = []
        for k in common_keys:
            la = key_to_a.get(k, [])
            lb = key_to_b.get(k, [])
            n_pair = min(len(la), len(lb))
            idx_a.extend(la[:n_pair])
            idx_b.extend(lb[:n_pair])
            # 多出来的视为删除/新增
            for i in la[n_pair:]:
                row_dict = df_a.iloc[i].to_dict()
                row_dict["__diff_type__"] = DIFF_DELETED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_deleted += 1
            for j in lb[n_pair:]:
                row_dict = df_b.iloc[j].to_dict()
                row_dict["__diff_type__"] = DIFF_ADDED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_added += 1
        old_common = _canonical_df(df_a.iloc[idx_a].reset_index(drop=True), compare_cols)
        new_common = _canonical_df(df_b.iloc[idx_b].reset_index(drop=True), compare_cols)
        old_raw = df_a.iloc[idx_a].reset_index(drop=True)
        new_raw = df_b.iloc[idx_b].reset_index(drop=True)
        # 确保列顺序一致
        old_common = old_common[compare_cols]
        new_common = new_common[compare_cols]
        diff_bool = old_common.ne(new_common)
        modified_mask = diff_bool.any(axis=1)

        for pos in range(len(idx_b)):
            j = idx_b[pos]
            row_dict = df_b.iloc[j].to_dict()
            if modified_mask.iloc[pos]:
                changed = list(diff_bool.iloc[pos].index[diff_bool.iloc[pos]].tolist())
                row_dict["__diff_type__"] = DIFF_MODIFIED
                row_dict["__changed_fields__"] = changed
                # 具体差异：列名 → 旧值→新值
                parts = []
                for col in changed:
                    if col in old_raw.columns and col in new_raw.columns:
                        ov = old_raw.iloc[pos][col]
                        nv = new_raw.iloc[pos][col]
                        so = str(ov) if ov is not None and not (isinstance(ov, float) and pd.isna(ov)) else ""
                        sn = str(nv) if nv is not None and not (isinstance(nv, float) and pd.isna(nv)) else ""
                        parts.append(f"{col}: {so}→{sn}")
                row_dict["__changes_detail__"] = "; ".join(parts)
                result.count_modified += 1
            else:
                row_dict["__diff_type__"] = DIFF_UNCHANGED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.count_unchanged += 1
            result.rows.append(row_dict)

        return result

    def _compare_by_position(
        self,
        df_a: pd.DataFrame,
        df_b: pd.DataFrame,
        compare_cols: List[str],
        result: DiffResult,
    ) -> DiffResult:
        """无键列时按行号对齐比较。"""
        n_a, n_b = len(df_a), len(df_b)
        for i in range(max(n_a, n_b)):
            if i >= n_a:
                row_dict = df_b.iloc[i].to_dict()
                row_dict["__diff_type__"] = DIFF_ADDED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_added += 1
            elif i >= n_b:
                row_dict = df_a.iloc[i].to_dict()
                row_dict["__diff_type__"] = DIFF_DELETED
                row_dict["__changed_fields__"] = []
                row_dict["__changes_detail__"] = ""
                result.rows.append(row_dict)
                result.count_deleted += 1
            else:
                old_common = _canonical_df(df_a.iloc[i : i + 1], compare_cols)
                new_common = _canonical_df(df_b.iloc[i : i + 1], compare_cols)
                diff_bool = old_common.ne(new_common)
                changed = list(diff_bool.columns[diff_bool.any()].tolist())
                row_dict = df_b.iloc[i].to_dict()
                if changed:
                    row_dict["__diff_type__"] = DIFF_MODIFIED
                    row_dict["__changed_fields__"] = changed
                    parts = []
                    for col in changed:
                        if col in df_a.columns and col in df_b.columns:
                            ov = df_a.iloc[i][col]
                            nv = df_b.iloc[i][col]
                            so = str(ov) if ov is not None and not (isinstance(ov, float) and pd.isna(ov)) else ""
                            sn = str(nv) if nv is not None and not (isinstance(nv, float) and pd.isna(nv)) else ""
                            parts.append(f"{col}: {so}→{sn}")
                    row_dict["__changes_detail__"] = "; ".join(parts)
                    result.count_modified += 1
                else:
                    row_dict["__diff_type__"] = DIFF_UNCHANGED
                    row_dict["__changed_fields__"] = []
                    row_dict["__changes_detail__"] = ""
                    result.count_unchanged += 1
                result.rows.append(row_dict)
        return result

    def compare_versions(
        self,
        df_old: pd.DataFrame,
        df_new: pd.DataFrame,
        key_columns: Optional[List[str]] = None,
        compare_columns: Optional[List[str]] = None,
    ) -> DiffResult:
        return self.compare_two_tables(
            df_old, df_new, keys=key_columns, compare_cols=compare_columns
        )


def cross_compare(
    base_df: pd.DataFrame,
    other_dfs: List[pd.DataFrame],
    key_columns: List[str],
    compare_columns: List[str],
    base_name: str = "基准",
    other_names: Optional[List[str]] = None,
) -> List[DiffResult]:
    engine = DiffEngine(key_columns=key_columns, compare_columns=compare_columns)
    return [
        engine.compare_two_tables(base_df, odf, keys=key_columns, compare_cols=compare_columns)
        for odf in other_dfs
    ]
