# 项目结构说明（按功能块划分）

每个功能块独立一个文件夹，便于后续开发与维护。

## 目录结构

```
ChecklistTool/
├── main.py                 # 程序入口
├── config/                 # 配置
│   ├── __init__.py
│   └── app_config.py       # 路径、常量、规则库编辑秘钥等
├── core/                   # 核心逻辑（按子包划分）
│   ├── __init__.py         # 统一导出
│   ├── diff/               # 对比引擎（版本对比、交叉对比共用）
│   │   ├── __init__.py
│   │   └── diff_engine.py
│   ├── parsers/            # 表格解析（Excel/CSV/TSV/Word）
│   │   ├── __init__.py
│   │   └── parsers.py
│   ├── rules/              # 规则引擎（规则库、校验）
│   │   ├── __init__.py
│   │   └── rule_engine.py
│   ├── generate/           # 数据生成引擎（按规则生成合规数据）
│   │   ├── __init__.py
│   │   └── generate_engine.py
│   ├── data/               # 设备数据内嵌数据库（SQLite 存储层）
│   │   ├── __init__.py
│   │   ├── errors.py
│   │   └── db.py
│   └── export/             # 导出引擎（Excel/CSV/PDF）
│       ├── __init__.py
│       └── export_engine.py
├── features/               # 功能模块（一个功能一个文件夹）
│   ├── version_diff/       # 版本对比
│   │   ├── __init__.py
│   │   └── tab.py
│   ├── cross_compare/      # 交叉对比
│   │   ├── __init__.py
│   │   └── tab.py
│   ├── rule_validate/      # 规则校验
│   │   ├── __init__.py
│   │   └── tab.py
│   ├── rule_generate/      # 数据生成（根据规则生成合规数据）
│   │   ├── __init__.py
│   │   └── tab.py
│   ├── data_entry/         # 设备数据填写（内嵌数据库+即时审查）
│   │   ├── __init__.py
│   │   ├── model.py
│   │   ├── dialogs.py
│   │   └── tab.py
│   ├── rules_lib/          # 规则库
│   │   ├── __init__.py
│   │   ├── tab.py
│   │   └── rule_tree_editor.py
│   └── results/            # 结果报告与导出
│       ├── __init__.py
│       └── tab.py
└── ui/                     # 公共 UI（主窗口、通用组件）
    ├── __init__.py
    ├── main_window.py      # 主窗口与选项卡集成
    └── widgets.py          # FilePathRow、ColumnSelector、ProgressWidget 等
```

## 导入约定

- **功能页**：`from features.xxx import TabXxx`
- **核心逻辑**：`from core.parsers import ...`、`from core.diff import ...`、`from core.rules import ...`、`from core.generate import ...`、`from core.export import ...`
- **配置**：`from config import ...`
- **公共 UI**：`from ui.widgets import ...`

## 扩展新功能

1. 在 `features/` 下新建文件夹，如 `features/new_feature/`。
2. 添加 `__init__.py` 并导出该功能的 Tab 或入口类。
3. 在 `ui/main_window.py` 中导入并添加新选项卡。

## 扩展新核心能力

- 新解析格式：在 `core/parsers/parsers.py` 中增加函数，或在 `core/parsers/` 下新增模块再在 `__init__.py` 中导出。
- 新对比/规则/导出逻辑：在对应 `core/diff`、`core/rules`、`core/export` 下增文件或扩展现有模块。
