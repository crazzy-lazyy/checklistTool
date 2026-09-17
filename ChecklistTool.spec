# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec：一次 Analysis 同时产出两种分发形态
  - onedir  -> dist\ChecklistTool\ChecklistTool.exe   （免安装文件夹，主推分发）
  - onefile -> dist\ChecklistTool.exe                 （单文件 exe，自解压、首次启动慢）
两者均 console=False、带图标与版本资源。
"""

import os

from PyInstaller.utils.hooks import collect_submodules

# 所有路径相对 spec 文件解析，构建时工作目录无影响
ROOT = os.path.dirname(os.path.abspath(SPEC))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(os.path.join(ROOT, "assets"), "assets")],
    hiddenimports=[
        # 函数内延迟导入、静态分析不可见的库（见 core/parsers、core/export）
        "docx",            # python-docx（parsers.py 函数内导入）
        "openpyxl",        # parsers.py 函数内导入
        "xlrd",            # 仅以 pandas engine 字符串 "xlrd" 出现
        "xlsxwriter",      # export_engine.py 函数内导入
        "reportlab",       # export_engine.py 函数内导入（触发 hook-reportlab，收集字体数据）
        "pdfplumber",      # parsers.py 函数内导入
        "pdfminer",        # pdfplumber 底层解析库
    ]
    # collect_submodules 返回子模块而不含包本身，故裸包名已在上方显式列出
    + collect_submodules("pdfminer")
    + collect_submodules("pdfplumber"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest", "pdb", "lib2to3"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

# ---- onedir 产物：dist\ChecklistTool\ChecklistTool.exe ----
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ChecklistTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,            # UPX 易触发杀软误报并可能破坏 Qt DLL，保持关闭
    console=False,        # 窗口程序，不弹控制台
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=os.path.join(ROOT, "version_info.txt"),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ChecklistTool",
)

# ---- onefile 产物：dist\ChecklistTool.exe ----
exe_onefile = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ChecklistTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=os.path.join(ROOT, "assets", "icon.ico"),
    version=os.path.join(ROOT, "version_info.txt"),
)
