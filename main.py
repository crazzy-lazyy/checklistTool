# -*- coding: utf-8 -*-
"""
清单对比与校审工具 - 程序入口
Checklist Comparison & Validation Tool - Entry point.

功能概要：
  - 同一清单不同版本的自动化差异对比（可配置键列与对比列）
  - 基于用户自定义规则的清单数据正确性校验（树形条件，管理员维护规则库）
  - 多清单交叉对比（如设计清单与采购清单）
  - 结果导出：PDF（带格式）、Excel（含标记与高亮）、CSV（纯数据）
  - 规则库编辑：需要输入秘钥解锁编辑功能

运行方式：python main.py
日志与崩溃信息会写入 user_data/logs/app.log，便于排查问题。
"""

import sys
import os
import traceback
import logging

# 确保项目根在路径中
APP_ROOT = os.path.dirname(os.path.abspath(__file__))
if APP_ROOT not in sys.path:
    sys.path.insert(0, APP_ROOT)

# 简单日志：写入 user_data/logs，便于排查崩溃原因
try:
    from config.app_config import LOG_DIR
    log_file = os.path.join(LOG_DIR, "app.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
except Exception:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main():
    """启动 PyQt6 主窗口，捕获未处理异常避免静默崩溃。"""
    # 诊断旗标：无界面自检（导入重型库、读写回环），用于开发与冻结版打包验证。
    # 结果写入 user_data/logs/smoke_test.log，退出码 0=通过 1=失败。
    if "--smoke-test" in sys.argv:
        from core.smoke_test import run_smoke_test
        sys.exit(run_smoke_test())
    try:
        from PyQt6.QtWidgets import QApplication, QDialog
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QFont
    except ImportError as e:
        logging.error("缺少 PyQt6，请安装: pip install PyQt6\n%s", e)
        sys.exit(1)

    app = QApplication(sys.argv)
    try:
        from config.app_config import APP_VERSION
        app.setApplicationName(f"清单对比与校审工具 v{APP_VERSION}")
    except Exception:
        app.setApplicationName("清单对比与校审工具")
    # 窗口/任务栏图标（随包资源，开发与冻结环境均从 RESOURCE_DIR 读取）
    try:
        from PyQt6.QtGui import QIcon
        from config.app_config import RESOURCE_DIR
        _icon = os.path.join(RESOURCE_DIR, "assets", "icon.ico")
        if os.path.isfile(_icon):
            app.setWindowIcon(QIcon(_icon))
    except Exception:
        pass
    # 高 DPI 支持
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    if hasattr(Qt.ApplicationAttribute, "AA_UseHighDpiPixmaps"):
        app.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps, True)

    # 应用级全局样式：页面底色 / 卡片体系 / Tab 选中态 / 组内浅灰输入
    try:
        from ui.widgets import apply_app_style
        apply_app_style(app)
    except Exception:
        pass

    def excepthook(etype, value, tb):
        msg = "".join(traceback.format_exception(etype, value, tb))
        logging.error("未捕获异常:\n%s", msg)
        try:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(None, "程序错误", f"发生错误，请查看日志。\n\n{value}")
        except Exception:
            pass

    sys.excepthook = excepthook

    employee = None
    try:
        from features.login import LoginDialog, get_current_employee
        login_dialog = LoginDialog()
        if login_dialog.exec() != QDialog.DialogCode.Accepted:
            logging.info("用户取消登录，退出程序")
            return 0
        employee = get_current_employee()
        if employee is None:
            raise RuntimeError("登录信息未正确保存")
        from core.usage import get_tracker
        get_tracker().start_session(employee)
    except Exception as e:
        logging.exception("员工登录或使用记录初始化失败: %s", e)
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "启动失败", "无法初始化员工登录信息，请查看日志。")
        return 1

    try:
        from ui.main_window import MainWindow
        win = MainWindow()
        win.set_employee(employee)
        win.show()
        sys.exit(app.exec())
    except Exception as e:
        logging.exception("启动失败: %s", e)
        excepthook(type(e), e, e.__traceback__)
        sys.exit(1)


if __name__ == "__main__":
    main()
