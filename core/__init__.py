# -*- coding: utf-8 -*-
"""
核心业务逻辑包（按功能块分子包）
- core.parsers: 表格解析
- core.diff: 对比引擎
- core.rules: 规则引擎
- core.export: 导出引擎
- core.generate: 规则数据生成
- core.ai: AI 服务（自然语言→规则、列名语义匹配）
"""
from .parsers import load_table_from_file, get_columns_from_file, ParserError
from .diff import DiffEngine, DiffResult, cross_compare
from .rules import RuleEngine, RuleNode, ValidationRule, RuleViolation
from .export import (
    export_to_excel,
    export_to_csv,
    export_to_pdf,
    export_diff_result,
    ExportError,
)
from .generate import (
    generate_from_rules,
    extract_domains,
    resolve_columns,
    GenerateConfig,
    GenerationReport,
    GenerationResult,
)
from .ai import (
    AIClient,
    AIClientError,
    get_client as get_ai_client,
    parse_natural_language_to_rule,
    match_column_names_semantically,
)

__all__ = [
    "load_table_from_file",
    "get_columns_from_file",
    "ParserError",
    "DiffEngine",
    "DiffResult",
    "cross_compare",
    "RuleEngine",
    "RuleNode",
    "ValidationRule",
    "RuleViolation",
    "export_to_excel",
    "export_to_csv",
    "export_to_pdf",
    "export_diff_result",
    "ExportError",
    "generate_from_rules",
    "extract_domains",
    "resolve_columns",
    "GenerateConfig",
    "GenerationReport",
    "GenerationResult",
    "AIClient",
    "AIClientError",
    "get_ai_client",
    "parse_natural_language_to_rule",
    "match_column_names_semantically",
]
