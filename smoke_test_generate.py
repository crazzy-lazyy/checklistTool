# -*- coding: utf-8 -*-
"""数据生成引擎冒烟测试（临时脚本，验证通过后可删除）。"""
import os
import sys

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

from core.rules import RuleEngine, RuleNode, ValidationRule
from core.generate import generate_from_rules, GenerateConfig


def make_engine():
    eng = RuleEngine()
    eng.load_rules()
    return eng


# 用例1：蕴含规则 + 随机模式
eng = make_engine()
eng.add_rule(ValidationRule(
    rule_id="smoke1", name="冒烟1",
    root=RuleNode(
        field="系统", operator="eq", value="给排水", logic="and",
        children=[
            RuleNode(field="专业", operator="eq", value="消防"),
            RuleNode(field="管径", operator="ge", value="50"),
        ],
    ),
))
res = generate_from_rules(eng, ["smoke1"], GenerateConfig(mode="random", row_count=20))
print("用例1 random:", res.report.to_text())
print(res.df.head(3).to_string())
assert res.report.generated_rows == 20, "应生成20行"
assert res.report.skipped_rows == 0, "不应有跳过行"
assert len(res.report.self_check_violations) == 0, res.report.self_check_violations
assert set(res.df["系统"].unique()) == {"给排水"}
assert set(res.df["专业"].unique()) == {"消防"}
assert all(float(v) >= 50 for v in res.df["管径"])
assert res.report.branches_total == 1 and res.report.branches_covered == 1

# 用例2：coverage 模式（单分支 → 1 行）
res2 = generate_from_rules(eng, ["smoke1"], GenerateConfig(mode="coverage"))
print("用例2 coverage:", res2.report.to_text())
assert res2.report.generated_rows >= 1
assert len(res2.report.self_check_violations) == 0

# 用例3：or 组松弛（B 无候选值 → 走 C 分支）
eng2 = make_engine()
eng2.add_rule(ValidationRule(
    rule_id="smoke2", name="冒烟2",
    root=RuleNode(
        field="系统", operator="eq", value="给排水", logic="or",
        children=[
            RuleNode(field="专业", operator="eq", value="消防"),
            RuleNode(field="管材", operator="eq", value="钢管"),
        ],
    ),
))
res3 = generate_from_rules(
    eng2, ["smoke2"], GenerateConfig(mode="random", row_count=10, value_pools={"专业": []})
)
print("用例3 or松弛:", res3.report.to_text())
print(res3.df.head(5).to_string())
assert len(res3.report.self_check_violations) == 0, res3.report.self_check_violations
assert res3.report.generated_rows == 10
for _, row in res3.df.iterrows():
    assert row["系统"] == "给排水", row.to_dict()
    assert row["专业"] == "消防" or row["管材"] == "钢管", row.to_dict()

# 用例4：模板列 + 示例值并入候选
import pandas as pd
tpl = pd.DataFrame({
    "系统": ["给排水", "电气", "消防"],
    "专业": ["消防", "照明", "给水"],
    "备注": ["A", "B", "C"],
})
res4 = generate_from_rules(
    eng, ["smoke1"], GenerateConfig(mode="random", row_count=5), template_df=tpl
)
print("用例4 模板:", res4.report.to_text())
print(res4.df.to_string())
assert list(res4.df.columns) == ["系统", "专业", "备注", "管径"], res4.df.columns
assert set(res4.report.extra_columns) == {"管径"}
assert len(res4.report.self_check_violations) == 0
assert set(res4.df["备注"]).issubset({"A", "B", "C"})
for _, row in res4.df.iterrows():
    if row["系统"] == "给排水":
        assert row["专业"] == "消防"
        assert float(row["管径"]) >= 50

# 用例4b：coverage 模式 + 模板（确定性：系统候选含给排水/电气/消防，仅给排水命中分支）
res4b = generate_from_rules(
    eng, ["smoke1"], GenerateConfig(mode="coverage"), template_df=tpl
)
print("用例4b coverage模板:", res4b.report.to_text())
assert res4b.report.branches_total == 1 and res4b.report.branches_covered == 1
assert len(res4b.report.self_check_violations) == 0

# 用例5：in 运算符全局约束
eng3 = make_engine()
eng3.add_rule(ValidationRule(
    rule_id="smoke3", name="冒烟3",
    root=RuleNode(field="专业", operator="in", value=["消防", "给水", "照明"]),
))
res5 = generate_from_rules(eng3, ["smoke3"], GenerateConfig(mode="random", row_count=15))
print("用例5 in:", res5.report.to_text())
assert len(res5.report.self_check_violations) == 0
assert set(res5.df["专业"].unique()).issubset({"消防", "给水", "照明"})

print("\n全部冒烟测试通过 ✔")
