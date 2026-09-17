# -*- coding: utf-8 -*-
"""
应用全局配置模块
Application global configuration.
集中管理路径、常量、默认值，便于后续扩展与维护。
"""

import os
import sys
import json

# -----------------------------------------------------------------------------
# 路径配置
# -----------------------------------------------------------------------------
# 应用根目录：打包后以 EXE 所在目录为根，源码运行时以 config 上级目录为根。
APP_ROOT = (
    os.path.dirname(os.path.abspath(sys.executable))
    if getattr(sys, "frozen", False)
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)

# 打包资源目录：冻结后为 PyInstaller 解压目录（onedir 的 _internal 或 onefile 的临时目录），
# 源码运行时即应用根目录。assets/ 等随包资源从该目录读取。
RESOURCE_DIR = getattr(sys, "_MEIPASS", None) or APP_ROOT

# 用户数据目录：规则库、导出结果、日志等
USER_DATA_DIR = os.path.join(APP_ROOT, "user_data")
RULES_DIR = os.path.join(USER_DATA_DIR, "rules")
EXPORT_DIR = os.path.join(USER_DATA_DIR, "exports")
LOG_DIR = os.path.join(USER_DATA_DIR, "logs")
USAGE_DIR = os.path.join(USER_DATA_DIR, "usage")

# 设备数据内嵌数据库（SQLite）
CHECKLIST_DB_FILE = os.path.join(USER_DATA_DIR, "checklist_db.sqlite")

# 客户端标识保存在程序目录外，替换或升级程序时仍可复用。
_LOCAL_APP_DATA = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
CLIENT_DATA_DIR = os.path.join(_LOCAL_APP_DATA, "ChecklistTool")
CLIENT_ID_FILE = os.path.join(CLIENT_DATA_DIR, "client.json")
CLIENT_ID_FALLBACK_FILE = os.path.join(USER_DATA_DIR, "client.json")

for _dir in (USER_DATA_DIR, RULES_DIR, EXPORT_DIR, LOG_DIR, USAGE_DIR):
    try:
        os.makedirs(_dir, exist_ok=True)
    except OSError:
        pass
# 若项目下 rules 不可写（如只读盘），改用用户目录，保证规则可长期保存
try:
    _t = os.path.join(RULES_DIR, ".w")
    with open(_t, "w") as _f:
        pass
    os.remove(_t)
except OSError:
    RULES_DIR = os.path.join(os.path.expanduser("~"), "清单对比校审工具", "rules")
    os.makedirs(RULES_DIR, exist_ok=True)

# -----------------------------------------------------------------------------
# 支持的文件格式
# -----------------------------------------------------------------------------
APP_VERSION = "2.2"

SUPPORTED_EXTENSIONS = (".xlsx", ".xls", ".csv", ".tsv", ".docx", ".pdf", ".et")
# 注：.et 若需支持，可后续接入转换或专用库

# -----------------------------------------------------------------------------
# 规则库编辑秘钥（用于解锁“规则库”编辑功能）
# -----------------------------------------------------------------------------
# 建议通过环境变量配置，不要把真实秘钥写进仓库。
# Windows PowerShell 示例：
#   $env:CHECKLISTTOOL_RULES_EDIT_KEY="你的秘钥"
RULES_EDIT_KEY_ENV = "CHECKLISTTOOL_RULES_EDIT_KEY"
# 若未配置环境变量时的默认秘钥（仅用于开发演示；生产环境请务必修改）
DEFAULT_RULES_EDIT_KEY = "admin"


def get_rules_edit_key() -> str:
    """获取规则库编辑秘钥（优先环境变量，其次默认值）。"""
    return (os.environ.get(RULES_EDIT_KEY_ENV) or DEFAULT_RULES_EDIT_KEY).strip()

# -----------------------------------------------------------------------------
# 差异类型（用于导出高亮）
# -----------------------------------------------------------------------------
DIFF_ADDED = "added"
DIFF_DELETED = "deleted"
DIFF_MODIFIED = "modified"
DIFF_UNCHANGED = "unchanged"

# -----------------------------------------------------------------------------
# 规则库文件名
# -----------------------------------------------------------------------------
RULES_DB_FILE = os.path.join(RULES_DIR, "rules_db.json")

# 员工登录与使用统计
EMPLOYEE_PROFILE_FILE = os.path.join(USER_DATA_DIR, "employee_profile.json")
SYS_CONFIG_FILE = os.path.join(USER_DATA_DIR, "sys_config.json")


def get_rules_fallback_path() -> str:
    """规则库在项目目录不可写时使用的用户目录路径（便于长期保存）。"""
    return os.path.join(os.path.expanduser("~"), "清单对比校审工具", "rules", "rules_db.json")

# -----------------------------------------------------------------------------
# AI 服务配置（内网 LLM API，OpenAI 兼容接口）
# -----------------------------------------------------------------------------
# 建议通过环境变量配置，不要把密钥写进仓库。
# Windows PowerShell 示例：
#   $env:CHECKLISTTOOL_AI_API_URL="http://192.168.1.100:8000/v1"
#   $env:CHECKLISTTOOL_AI_API_KEY="your-api-key"
AI_API_BASE_URL = (os.environ.get("CHECKLISTTOOL_AI_API_URL") or "http://127.0.0.1:8000/v1").strip()
AI_API_KEY = (os.environ.get("CHECKLISTTOOL_AI_API_KEY") or "").strip()
AI_MODEL_NAME = (os.environ.get("CHECKLISTTOOL_AI_MODEL") or "default").strip()
AI_TIMEOUT_SECONDS = int(os.environ.get("CHECKLISTTOOL_AI_TIMEOUT") or "30")
AI_MAX_RETRIES = int(os.environ.get("CHECKLISTTOOL_AI_MAX_RETRIES") or "2")
AI_MAX_TOKENS = int(os.environ.get("CHECKLISTTOOL_AI_MAX_TOKENS") or "2048")
AI_REASONING_EFFORT = (os.environ.get("CHECKLISTTOOL_AI_REASONING_EFFORT") or "").strip() or None

# -----------------------------------------------------------------------------
# 默认导出与界面
# -----------------------------------------------------------------------------
DEFAULT_EXPORT_FORMAT = "xlsx"
DEFAULT_ENCODING = "utf-8"

def load_json_safe(path: str, default=None):
    """安全加载 JSON 文件，缺失或异常时返回 default。"""
    if default is None:
        default = {}
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return default

def save_json_safe(path: str, data: dict) -> bool:
    """安全保存 JSON 文件。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except (IOError, TypeError):
        return False


def get_usage_sync_config() -> dict:
    """读取使用记录同步设置。"""
    cfg = load_json_safe(SYS_CONFIG_FILE, {})
    return {
        "auto_sync_enabled": bool(cfg.get("auto_sync_enabled", False)),
        "network_share_path": str(cfg.get("network_share_path", "")).strip(),
    }


def get_network_share_path() -> str:
    """读取共享目录路径；未配置时返回空字符串。"""
    return get_usage_sync_config()["network_share_path"]
