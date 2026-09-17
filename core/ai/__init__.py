# -*- coding: utf-8 -*-
"""
AI 功能模块
AI features module.

提供：
- AIClient: 内网 LLM API 客户端（OpenAI 兼容接口）
- parse_natural_language_to_rule: 自然语言 → RuleNode 条件树
- match_column_names_semantically: 两表列名语义匹配
"""

import json

from .ai_client import AIClient, AIClientError, get_client
from .prompts import (
    RULE_GENERATION_SYSTEM_PROMPT,
    RULE_GENERATION_USER_TEMPLATE,
    COLUMN_MATCHING_SYSTEM_PROMPT,
    COLUMN_MATCHING_USER_TEMPLATE,
)


def parse_natural_language_to_rule(
    user_input: str,
    columns: list,
    client: AIClient = None,
) -> dict:
    """
    将自然语言规则描述转换为结构化条件树 dict。

    参数:
        user_input: 用户输入的自然语言规则描述。
        columns: 当前表格的列名列表（用于字段匹配）。
        client: 可选，指定 AI 客户端；默认使用全局单例。

    返回:
        {
            "rule_name": "...",
            "description": "...",
            "target_columns": [...],
            "root": { ... RuleNode dict ... }
        }
        失败时返回 None。
    """
    if client is None:
        client = get_client()

    messages = [
        {"role": "system", "content": RULE_GENERATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": RULE_GENERATION_USER_TEMPLATE.format(
                columns="\n".join(f"- {c}" for c in (columns or [])),
                user_input=user_input,
            ),
        },
    ]

    result = client.chat_json(messages, temperature=0.1, expect_json=True)
    return result


def _format_sample_data(columns: list, sample_data: dict) -> str:
    """将列名和样本数据格式化为 AI prompt 所需的文本。"""
    if not columns:
        return "（无列名）"
    lines = []
    for c in columns:
        c = str(c or "").strip()
        vals = sample_data.get(c, []) if sample_data else []
        if vals:
            # 截断过长的值，避免 prompt 过大
            short_vals = [str(v)[:60] for v in vals[:5]]
            lines.append(f"- {c}: [{', '.join(short_vals)}]")
        else:
            lines.append(f"- {c}: （无样本数据）")
    return "\n".join(lines)


def match_column_names_semantically(
    base_columns: list,
    target_columns: list,
    base_sample_data: dict = None,
    target_sample_data: dict = None,
    client: AIClient = None,
) -> dict:
    """
    对两个列名列表进行语义匹配，找出名称不同但含义相同的列对。

    参数:
        base_columns: 基准表的列名列表。
        target_columns: 待对比表的列名列表。
        base_sample_data: 基准表每列的前5行数据，格式 {列名: [值列表]}。
        target_sample_data: 待对比表每列的前5行数据，格式 {列名: [值列表]}。
        client: 可选，指定 AI 客户端；默认使用全局单例。

    返回:
        {
            "matches": [
                {
                    "base_column": "...",
                    "target_column": "...",
                    "confidence": 0.85,
                    "reasoning": "...",
                },
                ...
            ],
            "unmatched_base": [...],
            "unmatched_target": [...],
        }
        失败时返回 None。
    """
    if client is None:
        client = get_client()

    base_cols_str = "\n".join(f"- {c}" for c in (base_columns or []))
    target_cols_str = "\n".join(f"- {c}" for c in (target_columns or []))
    base_sample_str = _format_sample_data(base_columns, base_sample_data or {})
    target_sample_str = _format_sample_data(target_columns, target_sample_data or {})

    messages = [
        {"role": "system", "content": COLUMN_MATCHING_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": COLUMN_MATCHING_USER_TEMPLATE.format(
                base_sample=base_sample_str,
                target_sample=target_sample_str,
                base_columns=base_cols_str,
                target_columns=target_cols_str,
            ),
        },
    ]

    # 先用 chat_json（含 response_format json_object）尝试
    try:
        result = client.chat_json(messages, temperature=0.1, expect_json=True)
        if result is not None:
            return result
    except AIClientError as e:
        # thinking 模型 token 不足时，回退到 chat 也无济于事，直接抛出
        if "思考过程消耗了全部" in str(e):
            raise
        # 其他错误（如 response_format 不支持），下面用 chat 兜底

    # 兜底：用普通 chat 获取原始回复，手动提取 JSON
    raw = client.chat(messages, temperature=0.1)
    if raw:
        # 尝试从原始回复中提取 JSON
        import re as _re
        for opener, closer in (("{", "}"), ("[", "]")):
            start = raw.find(opener)
            end = raw.rfind(closer)
            if start != -1 and end > start:
                candidate = raw[start:end + 1].strip()
                try:
                    return json.loads(candidate)
                except (json.JSONDecodeError, ValueError):
                    pass
        # 提取失败：抛出包含原始回复的错误
        snippet = raw[:500] if len(raw) > 500 else raw
        raise AIClientError(
            f"AI 返回的内容无法解析为 JSON。\n"
            f"原始回复（前500字符）：\n{snippet}"
        )
    raise AIClientError("AI 返回了空内容，请检查模型是否可用。")


__all__ = [
    "AIClient",
    "AIClientError",
    "get_client",
    "parse_natural_language_to_rule",
    "match_column_names_semantically",
    "RULE_GENERATION_SYSTEM_PROMPT",
    "RULE_GENERATION_USER_TEMPLATE",
    "COLUMN_MATCHING_SYSTEM_PROMPT",
    "COLUMN_MATCHING_USER_TEMPLATE",
]
