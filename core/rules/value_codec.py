# -*- coding: utf-8 -*-
"""规则编辑器列表值的转义编码与解析。"""

from typing import Any, Iterable, List


def parse_escaped_list(text: str) -> List[str]:
    r"""按未转义逗号拆分列表；支持 ``\,`` 和 ``\\``。"""
    items: List[str] = []
    current: List[str] = []
    escaped = False

    for char in str(text or ""):
        if escaped:
            if char in {",", "\\"}:
                current.append(char)
            else:
                # 未定义的转义序列原样保留，避免吞掉用户的反斜杠。
                current.extend(("\\", char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    if escaped:
        current.append("\\")
    items.append("".join(current).strip())
    return items


def format_escaped_list(values: Iterable[Any]) -> str:
    """把列表格式化为编辑器文本，确保再次解析时可无损还原。"""
    encoded = []
    for value in values:
        item = str(value).replace("\\", "\\\\").replace(",", "\\,")
        encoded.append(item)
    return ",".join(encoded)
