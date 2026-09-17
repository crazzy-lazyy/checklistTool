# -*- coding: utf-8 -*-
"""
规则数据生成引擎：从规则库提取字段域，生成满足所选规则的合规数据行。

设计要点：
- 规则树语义与 core.rules.rule_engine 保持一致：叶子为字段条件；带 field 且有
  children 的节点为“蕴含”（父条件不命中即通过，命中则子条件组必须满足）；
  logic="and"/"or" 组合子节点。
- 生成策略：按字段拓扑序（父字段先于子字段）逐字段赋值；叶子约束按“祖先
  guard 是否命中”惰性求值，O(叶子数×深度)，无需预建索引。
- or 组默认“满足全部活跃叶子”（充分策略，必合规）；仅当某子分支不可满足时
  做有界松弛（只保留可满足的子分支）。
- 每行经 RuleEngine.validate_row 兜底校验，整表再经 validate_dataframe 自检，
  违规行重试替换，保证输出零违规。
- 单值判断复用 RuleEngine._eval_cell（私有方法，需与引擎同步维护），确保与
  校验语义完全一致。
"""

import itertools
import math
import random
import re
import string
from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

try:
    from core.rules import RuleEngine, RuleViolation
except ImportError:
    RuleEngine = None  # type: ignore
    RuleViolation = None  # type: ignore

# 每字段候选值数量上限（防止超大模板撑爆内存/耗时）
MAX_CANDIDATES_PER_FIELD = 1000
# 允许值列表输出上限（去重后截断）
MAX_ALLOWED_VALUES = 50
# 数值约束生成时向外扩展的步数（如 ge 5 -> 5..10）
NUMERIC_SPREAD = 5
# 解析器生成的内部列，不作为业务列
_INTERNAL_COLS = ("__source_row__", "__row_index__")


# -----------------------------------------------------------------------------
# 数据结构
# -----------------------------------------------------------------------------
@dataclass
class Constraint:
    """叶子约束：field 须满足 operator/value；仅当 guard 全部命中时生效。"""

    field: str
    operator: str
    value: Any
    guard: List[Tuple[str, str, Any]] = dc_field(default_factory=list)  # 祖先条件，根到近
    or_path: List[Tuple[str, int]] = dc_field(default_factory=list)  # 外层 or 组 (group_id, child_index)


@dataclass
class Branch:
    """父条件（分支）：用于全覆盖模式与分支覆盖计数。"""

    field: str
    operator: str
    value: Any


@dataclass
class FieldDomain:
    """单个字段的取值域：候选值 + 叶子约束 + 父条件分支。"""

    field: str
    candidates: List[str] = dc_field(default_factory=list)
    constraints: List[Constraint] = dc_field(default_factory=list)
    branches: List[Branch] = dc_field(default_factory=list)


@dataclass
class GenerateConfig:
    mode: str = "random"  # "random"=固定行数随机生成 / "coverage"=按规则分支全覆盖
    row_count: int = 50
    columns: Optional[List[str]] = None  # 模板列；None = 使用规则字段
    value_pools: Optional[Dict[str, List[str]]] = None  # 用户候选池，完全替换对应字段的自动候选值
    max_coverage_combos: int = 500
    max_retries_per_row: int = 20
    max_relax_combos: int = 16


@dataclass
class GenerationReport:
    generated_rows: int
    skipped_rows: int
    self_check_violations: List[Any]  # List[RuleViolation]，理想为空
    branches_covered: int
    branches_total: int
    extra_columns: List[str]
    notes: List[str]

    def to_text(self) -> str:
        """一行中文摘要（用于状态栏/弹窗）。"""
        parts = [f"生成完成：共生成 {self.generated_rows} 行"]
        if self.skipped_rows:
            parts.append(f"跳过 {self.skipped_rows} 行")
        parts.append(f"自检违规 {len(self.self_check_violations)} 条")
        if self.branches_total:
            parts.append(f"分支覆盖 {self.branches_covered}/{self.branches_total}")
        if self.extra_columns:
            parts.append(f"附加列：{'、'.join(self.extra_columns)}")
        if self.notes:
            parts.append("备注：" + "；".join(self.notes))
        return "，".join(parts)


@dataclass
class GenerationResult:
    df: pd.DataFrame
    report: GenerationReport


# -----------------------------------------------------------------------------
# 字段域提取
# -----------------------------------------------------------------------------
def _dedup(values: List[str]) -> List[str]:
    seen = set()
    out = []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _column_samples(df: pd.DataFrame, col: str) -> List[str]:
    """提取模板某列的非空示例值（≤200 行原始数据）。"""
    try:
        series = df[col].dropna().astype(str).str.strip()
    except Exception:
        return []
    return _dedup([v for v in series.head(200) if v])


def _walk_tree(
    node,
    guard: List[Tuple[str, str, Any]],
    or_path: List[Tuple[str, int]],
    domains: Dict[str, FieldDomain],
    counter: List[int],
) -> None:
    """递归遍历规则树，收集字段域、候选值、叶子约束与分支。"""
    field = getattr(node, "field", "") or ""
    operator = getattr(node, "operator", "eq") or "eq"
    value = getattr(node, "value", None)
    logic = getattr(node, "logic", "and") or "and"
    children = list(getattr(node, "children", []) or [])

    if field:
        d = domains.setdefault(field, FieldDomain(field))
        if operator == "eq":
            cand = str(value if value is not None else "").strip()
            if cand not in d.candidates:
                d.candidates.append(cand)
        elif operator == "in":
            for v in value or []:
                cand = str(v).strip()
                if cand not in d.candidates:
                    d.candidates.append(cand)
        elif operator in ("ge", "gt", "le", "lt"):
            # 数值分支：把边界值与边界外一个值加入候选，保证全覆盖模式能命中该分支
            try:
                fv = float(str(value).strip())
            except Exception:
                fv = None
            if fv is not None:
                for cand in (str(fv), str(fv + 1) if operator in ("ge", "gt") else str(fv - 1)):
                    if cand not in d.candidates:
                        d.candidates.append(cand)

    if not children:
        if field:
            c = Constraint(field, operator, value, list(guard), list(or_path))
            for existing in domains[field].constraints:
                if (
                    existing.operator == c.operator
                    and existing.value == c.value
                    and existing.guard == c.guard
                    and existing.or_path == c.or_path
                ):
                    break
            else:
                domains[field].constraints.append(c)
        return

    # 有子节点：本节点条件成为后代叶子的 guard；logic=or 时新建 or 组
    new_guard = guard + ([(field, operator, value)] if field else [])
    if logic == "or":
        counter[0] += 1
        gid = f"g{counter[0]}"
        for i, child in enumerate(children):
            _walk_tree(child, new_guard, or_path + [(gid, i)], domains, counter)
    else:
        for child in children:
            _walk_tree(child, new_guard, or_path, domains, counter)

    if field:
        b = Branch(field, operator, value)
        for existing in domains[field].branches:
            if existing.operator == b.operator and str(existing.value) == str(b.value):
                break
        else:
            domains[field].branches.append(b)


def extract_domains(
    engine,
    rule_ids: Optional[List[str]],
    template_df: Optional[pd.DataFrame] = None,
    value_pools: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, FieldDomain]:
    """从所选规则提取字段域；可选并入模板示例值与用户候选池。"""
    domains: Dict[str, FieldDomain] = {}
    counter = [0]
    rules = [r for r in engine.get_rules() if not rule_ids or r.rule_id in rule_ids]
    for rule in rules:
        if getattr(rule, "root", None) is not None:
            _walk_tree(rule.root, [], [], domains, counter)

    # 模板示例值并入候选（未被用户池覆盖的字段）
    if template_df is not None:
        for col in template_df.columns:
            samples = _column_samples(template_df, col)
            if col in domains:
                if not (value_pools and col in value_pools):
                    for v in samples:
                        if v not in domains[col].candidates:
                            domains[col].candidates.append(v)
            elif samples:
                domains[col] = FieldDomain(col, candidates=samples)

    # 用户候选池完全替换对应字段的候选值
    if value_pools:
        for f, vals in value_pools.items():
            d = domains.setdefault(f, FieldDomain(f))
            d.candidates = _dedup([str(v).strip() for v in vals if str(v).strip()])

    for d in domains.values():
        if len(d.candidates) > MAX_CANDIDATES_PER_FIELD:
            d.candidates = d.candidates[:MAX_CANDIDATES_PER_FIELD]
    return domains


def resolve_columns(
    engine,
    rule_ids: Optional[List[str]],
    template_columns: Optional[List[str]] = None,
    domains: Optional[Dict[str, FieldDomain]] = None,
) -> Tuple[List[str], List[str]]:
    """确定生成列：模板列在前，规则中出现但模板缺失的字段追加在后。

    返回 (全部列, 附加列)。
    """
    if domains is None:
        domains = extract_domains(engine, rule_ids)
    cols = list(template_columns or [])
    extra = [f for f in domains if f not in cols]
    return cols + extra, extra


# -----------------------------------------------------------------------------
# 约束求值
# -----------------------------------------------------------------------------
def _cell_matches(engine, value: Any, operator: str, expected: Any) -> bool:
    """与规则引擎一致的单值判断（复用 _eval_cell，需与引擎同步维护）。"""
    return bool(engine._eval_cell(value, operator, expected))


def _guard_hit(engine, guard: List[Tuple[str, str, Any]], assignments: Dict[str, str]) -> bool:
    for (f, op, v) in guard:
        if not _cell_matches(engine, assignments.get(f, ""), op, v):
            return False
    return True


def _active_musts(
    engine,
    field: str,
    domains: Dict[str, FieldDomain],
    assignments: Dict[str, str],
    relax: Dict[str, Any],
) -> List[Constraint]:
    """返回当前赋值下生效的叶子约束（guard 全部命中且未被松弛掉）。"""
    out = []
    for c in domains[field].constraints:
        if c.guard and not _guard_hit(engine, c.guard, assignments):
            continue
        dropped = False
        for (gid, ci) in c.or_path:
            keep = relax.get(gid)
            if keep is not None and keep != "ALL" and keep != ci:
                dropped = True
                break
        if not dropped:
            out.append(c)
    return out


def _topo_order(domains: Dict[str, FieldDomain]) -> List[str]:
    """字段拓扑排序：作为 guard 的父字段先于受约束的子字段。"""
    edges = set()
    for d in domains.values():
        for c in d.constraints:
            for (gf, _, _) in c.guard:
                if gf in domains and gf != d.field:
                    edges.add((gf, d.field))
    fields = list(domains.keys())
    order: List[str] = []
    remaining = set(fields)
    while remaining:
        progress = False
        for f in fields:
            if f not in remaining:
                continue
            if all(g not in remaining for (g, c) in edges if c == f):
                order.append(f)
                remaining.discard(f)
                progress = True
        if not progress:
            # 环形依赖（异常规则）：按首次出现顺序追加，靠最终校验兜底
            for f in fields:
                if f in remaining:
                    order.append(f)
                    remaining.discard(f)
            break
    return order


# -----------------------------------------------------------------------------
# 允许值计算
# -----------------------------------------------------------------------------
def _numeric_bounds(musts: List[Constraint]) -> Tuple[Optional[float], Optional[float]]:
    """从 gt/ge/lt/le 约束中计算数值下/上界；非数值约束值忽略（同引擎退化为字符串比较）。"""
    lo: Optional[float] = None
    hi: Optional[float] = None
    for m in musts:
        if m.operator not in ("gt", "ge", "lt", "le"):
            continue
        try:
            v = float(str(m.value).strip())
        except Exception:
            continue
        if m.operator == "ge":
            lo = v if lo is None else max(lo, v)
        elif m.operator == "gt":
            lo = (v + 1) if lo is None else max(lo, v + 1)
        elif m.operator == "le":
            hi = v if hi is None else min(hi, v)
        elif m.operator == "lt":
            hi = (v - 1) if hi is None else min(hi, v - 1)
    if lo is not None and hi is not None and hi < lo:
        return None, None  # 不可满足
    return lo, hi


def _regex_generate(pattern: str) -> Optional[str]:
    """内置小表：为常见正则模式生成一个匹配样本；不支持的模式返回 None（不做通用正则生成器）。"""
    p = (pattern or "").strip()
    if not p:
        return None
    m = re.fullmatch(r"\\d\{(\d+)\}", p)
    if m:
        n = min(int(m.group(1)), 12)
        return "".join(str(random.randint(0, 9)) for _ in range(n))
    if re.fullmatch(r"\\d\+", p):
        return str(random.randint(10, 99999))
    m = re.fullmatch(r"\[A-Z\]\+", p)
    if m:
        return "".join(random.choice(string.ascii_uppercase) for _ in range(4))
    m = re.fullmatch(r"\[A-Za-z\]\+", p)
    if m:
        return "".join(random.choice(string.ascii_letters) for _ in range(4))
    return None


def _allowed_values(engine, domain: FieldDomain, musts: List[Constraint], placeholder_src: Callable[[], str]) -> List[str]:
    """计算字段在 musts 约束下的允许值（去重、≤50 个）。"""

    def ok(v: Any) -> bool:
        return all(_cell_matches(engine, v, m.operator, m.value) for m in musts)

    out = [c for c in domain.candidates if ok(c)]

    # 数值约束：在 [lo, hi] 内向外生成 NUMERIC_SPREAD 个整数
    lo, hi = _numeric_bounds(musts)
    if lo is not None or hi is not None:
        if lo is None:
            lo = (hi or 0) - NUMERIC_SPREAD
        if hi is None:
            hi = lo + NUMERIC_SPREAD
        start, end = int(math.ceil(lo)), int(math.floor(hi))
        if end >= start:
            for x in range(start, min(end, start + NUMERIC_SPREAD) + 1):
                s = str(x)
                if s not in out and ok(s):
                    out.append(s)

    if not out:
        # 形状约束兜底：is_num / not_empty / empty / regex
        for m in musts:
            if m.operator == "is_num" and ok("1"):
                out.append("1")
            elif m.operator == "not_empty":
                p = placeholder_src()
                if ok(p):
                    out.append(p)
            elif m.operator == "empty" and ok(""):
                out.append("")
            elif m.operator == "regex":
                g = _regex_generate(str(m.value))
                if g is not None and ok(g):
                    out.append(g)

    if not out and not musts:
        out = [placeholder_src()]

    seen = set()
    final = []
    for v in out:
        if v not in seen:
            seen.add(v)
            final.append(v)
        if len(final) >= MAX_ALLOWED_VALUES:
            break
    return final


def _try_relax(
    engine,
    field: str,
    domains: Dict[str, FieldDomain],
    assignments: Dict[str, str],
    relax: Dict[str, Any],
    placeholder_src: Callable[[], str],
    max_combos: int,
) -> Tuple[Optional[List[str]], Optional[Dict[str, Any]]]:
    """or 组松弛：当某字段无允许值时，尝试只保留 or 组中单个子分支的约束。

    候选子分支取全部约束中出现的子索引（而非仅当前字段），否则无法“放弃”
    导致失败的那个子分支。返回 (允许值, 新 relax) 或 (None, None)。
    """
    # 当前字段生效约束涉及的 or 组及其“正在失败”的子分支
    failing_cis: Dict[str, set] = {}
    for c in domains[field].constraints:
        if c.guard and not _guard_hit(engine, c.guard, assignments):
            continue
        for (gid, ci) in c.or_path:
            failing_cis.setdefault(gid, set()).add(ci)
    if not failing_cis:
        return None, None

    # 全部约束中每个 or 组出现过的子分支（含其他字段的约束）
    all_cis: Dict[str, set] = {}
    for d in domains.values():
        for c in d.constraints:
            for (gid, ci) in c.or_path:
                all_cis.setdefault(gid, set()).add(ci)

    gids = list(failing_cis.keys())
    option_lists = []
    for gid in gids:
        non_failing = sorted(all_cis.get(gid, set()) - failing_cis.get(gid, set()))
        failing = sorted(failing_cis.get(gid, set()))
        # 优先尝试“放弃失败子分支”（保留其他子分支），ALL 与失败子分支靠后
        option_lists.append(["ALL"] + non_failing + failing)

    tested = 0
    for choice in itertools.product(*option_lists):
        if tested >= max_combos:
            break
        tested += 1
        new_relax = dict(relax)
        new_relax.update(zip(gids, choice))
        if new_relax == relax:
            continue
        musts = _active_musts(engine, field, domains, assignments, new_relax)
        allowed = _allowed_values(engine, domains[field], musts, placeholder_src)
        if allowed:
            return allowed, new_relax
    return None, None


# -----------------------------------------------------------------------------
# 行生成
# -----------------------------------------------------------------------------
def _fill_cell(col: str, assignments: Dict[str, str], placeholder_src: Callable[[], str]) -> str:
    """为生成行填充单元格：已赋值字段取赋值，其余模板列用顺序占位值。"""
    if col in assignments:
        return assignments[col]
    return placeholder_src()


def _row_valid(engine, row: pd.Series, rules) -> bool:
    """逐规则兜底校验：任一条规则违规即无效。"""
    return all(engine.validate_row(row, rule) is None for rule in rules)


def _generate_row_random(
    engine,
    domains: Dict[str, FieldDomain],
    order: List[str],
    columns: List[str],
    rules,
    config: GenerateConfig,
    placeholder_src: Callable[[], str],
    rng: random.Random,
) -> Optional[pd.Series]:
    """随机模式：生成一行合规数据；失败返回 None（调用方计数跳过）。"""
    for _ in range(config.max_retries_per_row):
        assignments: Dict[str, str] = {}
        relax: Dict[str, Any] = {}
        ok = True
        for f in order:
            musts = _active_musts(engine, f, domains, assignments, relax)
            allowed = _allowed_values(engine, domains[f], musts, placeholder_src)
            if not allowed:
                allowed, new_relax = _try_relax(
                    engine, f, domains, assignments, relax, placeholder_src, config.max_relax_combos
                )
                if allowed:
                    relax = new_relax
                else:
                    ok = False
                    break
            assignments[f] = rng.choice(allowed)
        if not ok:
            continue
        row = pd.Series(
            {c: _fill_cell(c, assignments, placeholder_src) for c in columns}, dtype=object
        )
        if _row_valid(engine, row, rules):
            return row
    return None


def _generate_rows_coverage(
    engine,
    domains: Dict[str, FieldDomain],
    order: List[str],
    columns: List[str],
    rules,
    config: GenerateConfig,
    placeholder_src: Callable[[], str],
    rng: random.Random,
) -> Tuple[List[pd.Series], int, bool]:
    """全覆盖模式：决策字段候选值按“分支激活签名”去重后做笛卡尔积，每组合生成一行。

    返回 (行列表, 跳过组合数, 是否因超上限截断)。
    """
    decision_fields = [f for f in order if domains[f].branches]
    if not decision_fields:
        return [], 0, False

    sig_values: Dict[str, List[str]] = {}
    for f in decision_fields:
        d = domains[f]
        vals: List[str] = []
        seen = set()
        for v in d.candidates or [placeholder_src()]:
            sig = tuple(
                i for i, b in enumerate(d.branches) if _cell_matches(engine, v, b.operator, b.value)
            )
            if sig not in seen:
                seen.add(sig)
                vals.append(v)
        sig_values[f] = vals

    rows: List[pd.Series] = []
    skipped = 0
    truncated = False
    for combo in itertools.product(*[sig_values[f] for f in decision_fields]):
        if len(rows) >= config.max_coverage_combos:
            truncated = True
            break
        assignments = dict(zip(decision_fields, combo))
        relax: Dict[str, Any] = {}
        ok = True
        for f in order:
            musts = _active_musts(engine, f, domains, assignments, relax)
            if f in assignments:
                # 决策字段：组合值须满足当前活跃约束，否则尝试松弛
                if not all(_cell_matches(engine, assignments[f], m.operator, m.value) for m in musts):
                    allowed, new_relax = _try_relax(
                        engine, f, domains, assignments, relax, placeholder_src, config.max_relax_combos
                    )
                    if allowed and assignments[f] in allowed:
                        relax = new_relax
                    else:
                        ok = False
                        break
            else:
                allowed = _allowed_values(engine, domains[f], musts, placeholder_src)
                if not allowed:
                    allowed, new_relax = _try_relax(
                        engine, f, domains, assignments, relax, placeholder_src, config.max_relax_combos
                    )
                    if allowed:
                        relax = new_relax
                    else:
                        ok = False
                        break
                assignments[f] = allowed[0]  # 全覆盖模式取第一个允许值，保证确定性
        if not ok:
            skipped += 1
            continue
        row = pd.Series(
            {c: _fill_cell(c, assignments, placeholder_src) for c in columns}, dtype=object
        )
        if _row_valid(engine, row, rules):
            rows.append(row)
        else:
            skipped += 1
    return rows, skipped, truncated


# -----------------------------------------------------------------------------
# 自检与统计
# -----------------------------------------------------------------------------
def _self_check(
    engine,
    df: pd.DataFrame,
    rules,
    domains: Dict[str, FieldDomain],
    order: List[str],
    columns: List[str],
    config: GenerateConfig,
    placeholder_src: Callable[[], str],
    rng: random.Random,
) -> List[Any]:
    """自检：违规行重试替换（≤3 次），返回最终违规列表（理想为空）。"""
    if df.empty:
        return []
    rule_ids = [r.rule_id for r in rules]
    violations = engine.validate_dataframe(df, rule_ids=rule_ids)
    if not violations:
        return []
    retry_rows = set()
    for v in violations:
        retry_rows.add(v.row_index)
    for idx in retry_rows:
        for _ in range(3):
            new_row = _generate_row_random(
                engine, domains, order, columns, rules, config, placeholder_src, rng
            )
            if new_row is not None:
                df.iloc[idx] = new_row.values
                break
    return engine.validate_dataframe(df, rule_ids=rule_ids)


def _count_branch_coverage(engine, domains: Dict[str, FieldDomain], df: pd.DataFrame) -> Tuple[int, int]:
    """统计分支覆盖：(已覆盖数, 总数)。"""
    total = sum(len(d.branches) for d in domains.values())
    covered = 0
    for d in domains.values():
        for b in d.branches:
            if b.field in df.columns and any(
                _cell_matches(engine, v, b.operator, b.value) for v in df[b.field]
            ):
                covered += 1
    return covered, total


# -----------------------------------------------------------------------------
# 主入口
# -----------------------------------------------------------------------------
def generate_from_rules(
    engine,
    rule_ids: Optional[List[str]],
    config: GenerateConfig,
    template_df: Optional[pd.DataFrame] = None,
    progress_callback: Optional[Callable[[int, str], None]] = None,
) -> GenerationResult:
    """根据所选规则生成合规数据。

    - engine: 已 load_rules() 的 RuleEngine
    - rule_ids: 所选规则 id；None/空列表 = 全部规则
    - config: 生成配置（模式、行数、列、候选池等）
    - template_df: 模板数据（用于列结构与示例值，可无数据行）
    - progress_callback: (百分比, 消息)
    """

    def emit(p: int, msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(p, msg)
            except Exception:
                pass

    emit(10, "提取字段域...")
    rules = [r for r in engine.get_rules() if not rule_ids or r.rule_id in rule_ids]
    domains = extract_domains(engine, rule_ids, template_df=template_df, value_pools=config.value_pools)
    # 未显式指定列时，优先取模板列结构，其次取规则字段
    template_columns = config.columns
    if template_columns is None and template_df is not None:
        template_columns = [c for c in template_df.columns if c not in _INTERNAL_COLS]
    columns, extra_columns = resolve_columns(
        engine, rule_ids, template_columns=template_columns, domains=domains
    )
    order = _topo_order(domains)
    rng = random.Random()
    counter = [0]

    def make_placeholder() -> str:
        counter[0] += 1
        return f"占位_{counter[0]:03d}"

    notes: List[str] = []
    mode = config.mode if rules else "random"
    if mode == "coverage" and not any(domains[f].branches for f in order):
        mode = "random"
        notes.append("所选规则无父条件分支，已按固定行数生成")

    if mode == "coverage":
        emit(40, "按规则分支全覆盖生成...")
        rows, skipped, truncated = _generate_rows_coverage(
            engine, domains, order, columns, rules, config, make_placeholder, rng
        )
        if truncated:
            notes.append(f"分支组合数超过 {config.max_coverage_combos}，仅生成前 {config.max_coverage_combos} 行")
    else:
        total = max(1, config.row_count)
        emit(40, f"生成 {total} 行...")
        rows: List[pd.Series] = []
        skipped = 0
        for i in range(total):
            row = _generate_row_random(
                engine, domains, order, columns, rules, config, make_placeholder, rng
            )
            if row is None:
                skipped += 1
            else:
                rows.append(row)
            if (i + 1) % max(1, total // 20) == 0:
                emit(40 + int(40 * (i + 1) / total), f"已生成 {len(rows)}/{total} 行...")

    emit(85, "自检验证...")
    df = pd.DataFrame(rows, columns=columns) if rows else pd.DataFrame(columns=columns)
    if not df.empty:
        df = df.astype(object)
    violations = _self_check(engine, df, rules, domains, order, columns, config, make_placeholder, rng)
    covered, total_branches = _count_branch_coverage(engine, domains, df)
    if total_branches and covered < total_branches:
        notes.append(f"部分分支未被覆盖（覆盖 {covered}/{total_branches}）")

    emit(100, "生成完成")
    report = GenerationReport(
        generated_rows=len(df),
        skipped_rows=skipped,
        self_check_violations=violations,
        branches_covered=covered,
        branches_total=total_branches,
        extra_columns=extra_columns,
        notes=notes,
    )
    return GenerationResult(df=df, report=report)
