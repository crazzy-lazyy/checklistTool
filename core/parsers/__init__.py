# -*- coding: utf-8 -*-
"""多格式表格解析：Excel、CSV、TSV、Word。"""
from .parsers import (
    ParserError,
    load_table_from_file,
    get_columns_from_file,
)

__all__ = ["ParserError", "load_table_from_file", "get_columns_from_file"]
