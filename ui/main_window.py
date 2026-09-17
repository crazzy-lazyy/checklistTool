# -*- coding: utf-8 -*-
"""
主窗口：集成规则校验、数据生成、设备数据、交叉对比、规则库、结果报告等选项卡。
（已移除“版本对比”界面：两文件对比可通过“交叉对比”添加 1 个待对比文件实现。）
"""

import sys
import os
import time

# 将项目根目录加入路径，便于各模块导入 config、core
if getattr(sys, "frozen", False):
    _root = os.path.dirname(sys.executable)
else:
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTabWidget,
    QLabel,
    QStatusBar,
    QMessageBox,
)
from PyQt6.QtCore import Qt

from features.rule_validate import TabRuleValidate
from features.rule_generate import TabRuleGenerate
from features.data_entry import TabDataEntry
from features.cross_compare import TabCrossCompare
from features.rules_lib import TabRulesLib
from features.results import TabResults
from features.ai_config import TabAIConfig
from features.sys_config import TabSysConfig
from core.usage import get_tracker
from ui.widgets import HeaderWidget

_FEATURE_NAMES = {
    0: "规则校验", 1: "数据生成", 2: "设备数据", 3: "交叉对比",
    4: "规则库", 5: "结果报告", 6: "AI 配置", 7: "系统配置",
}


class MainWindow(QMainWindow):
    """清单对比与校审工具主窗口。"""

    def __init__(self):
        super().__init__()
        try:
            from config import APP_VERSION
            self._app_version = APP_VERSION
        except Exception:
            self._app_version = None
        self.setWindowTitle(
            f"清单对比与校审工具 v{self._app_version}" if self._app_version else "清单对比与校审工具"
        )
        self.setMinimumSize(900, 650)
        self.resize(1000, 700)
        self._tab_results = None
        self._tab_rules = None
        self._employee = None
        self._active_tab_index = 0
        self._tab_started_at = time.monotonic()
        self._session_started_at = time.monotonic()
        self._setup_ui()

    def set_employee(self, employee):
        self._employee = employee
        if employee is not None:
            self.statusBar().showMessage(f"当前用户：{employee.name}（{employee.employee_id}） | 就绪")

    def _setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # 顶部品牌条：Logo + 名称 + 右侧标语
        layout.addWidget(HeaderWidget())

        tabs = QTabWidget()
        tab_validate = TabRuleValidate()
        tab_validate.result_ready.connect(self._on_validate_result)
        tabs.addTab(tab_validate, "规则校验")

        tab_generate = TabRuleGenerate()
        tab_generate.result_ready.connect(self._on_generate_result)
        tabs.addTab(tab_generate, "数据生成")

        tab_data_entry = TabDataEntry()
        tabs.addTab(tab_data_entry, "设备数据")

        tab_cross = TabCrossCompare()
        tab_cross.result_ready.connect(self._on_cross_result)
        tabs.addTab(tab_cross, "交叉对比")

        self._tab_rules = TabRulesLib()
        self._tab_rules.rules_updated.connect(tab_validate.refresh_rules_list)
        self._tab_rules.rules_updated.connect(tab_generate.refresh_rules_list)
        self._tab_rules.rules_updated.connect(tab_data_entry.refresh_rules_combo)
        tabs.addTab(self._tab_rules, "规则库")

        self._tab_results = TabResults()
        tabs.addTab(self._tab_results, "结果报告")

        tab_ai_config = TabAIConfig()
        tabs.addTab(tab_ai_config, "AI 配置")

        tabs.addTab(TabSysConfig(), "系统配置")
        tabs.currentChanged.connect(self._on_tab_changed)

        layout.addWidget(tabs)
        self.setStatusBar(QStatusBar())
        if self._app_version:
            self.statusBar().addPermanentWidget(QLabel(f"v{self._app_version}"))
        self.statusBar().showMessage("就绪")

    def _on_validate_result(self, df, payload):
        if isinstance(payload, dict):
            violations = payload.get("violations", []) or []
            unvalidated_rows = payload.get("unvalidated_rows", []) or []
        else:
            violations = payload or []
            unvalidated_rows = []
        self._tab_results.set_validation_result(df, violations, unvalidated_rows=unvalidated_rows)
        self.statusBar().showMessage(
            f"校验完成，违规 {len(violations)} 条，未进入验证 {len(unvalidated_rows)} 行"
        )
        self._track_feature("规则校验", f"违规 {len(violations)} 条")

    def _on_cross_result(self, results):
        if not results:
            return
        self._tab_results.set_diff_result(results[0])
        if len(results) > 1:
            self.statusBar().showMessage(f"交叉对比完成，共 {len(results)} 组结果，已展示第一组")
        else:
            self.statusBar().showMessage("交叉对比完成，请查看结果报告页")
        self._track_feature("交叉对比", f"{len(results)} 组结果")

    def _on_generate_result(self, df, payload):
        report = payload.get("report") if isinstance(payload, dict) else None
        text = report.to_text() if report is not None else "生成完成"
        self._tab_results.set_validation_result(df, [], unvalidated_rows=[])
        self.statusBar().showMessage(text)
        self._track_feature("数据生成", text)

    def _on_tab_changed(self, index: int):
        elapsed = time.monotonic() - self._tab_started_at
        self._track_feature(_FEATURE_NAMES.get(self._active_tab_index, "功能页"), "页面停留", elapsed)
        self._active_tab_index = index
        self._tab_started_at = time.monotonic()

    def _track_feature(self, feature: str, details: str = "", duration_s: float = 0) -> None:
        try:
            tracker = get_tracker()
            if tracker.has_employee():
                tracker.track_event(feature, duration_s=duration_s, details=details)
        except Exception:
            pass

    def closeEvent(self, event):
        self._on_tab_changed(self._active_tab_index)
        elapsed = time.monotonic() - self._session_started_at
        self._track_feature("app_exit", "退出程序", elapsed)
        event.accept()
