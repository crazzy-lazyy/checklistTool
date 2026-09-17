"""本地使用记录及按工号、客户端隔离的共享目录同步。"""

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from typing import Optional

from config import APP_VERSION, USAGE_DIR, get_network_share_path, get_usage_sync_config, load_json_safe
from .client import get_client_id
from .employee import EmployeeProfile, normalize_employee_id

logger = logging.getLogger(__name__)


class UsageTracker:
    """记录使用事件；每个工号、每个客户端、每月使用一个独立 JSON。"""

    def __init__(self):
        self._employee: Optional[EmployeeProfile] = None
        self._client_id = get_client_id()
        self._session_id = ""
        self._write_lock = threading.Lock()

    @property
    def client_id(self) -> str:
        return self._client_id

    def start_session(self, employee: EmployeeProfile) -> None:
        employee.employee_id = normalize_employee_id(employee.employee_id)
        self._employee = employee
        self._session_id = str(uuid.uuid4())
        self.track_event("app_launch")

    def has_employee(self) -> bool:
        return self._employee is not None

    def track_event(self, feature: str, duration_s: float = 0, details: str = "") -> None:
        if not self._employee:
            return
        now = datetime.now()
        event = {
            "event_id": str(uuid.uuid4()),
            "employee_id": self._employee.employee_id,
            "employee_name": self._employee.name,
            "department": self._employee.department,
            "client_id": self._client_id,
            "session_id": self._session_id,
            "timestamp": now.isoformat(timespec="seconds"),
            "feature": str(feature),
            "duration_s": round(float(duration_s), 1),
            "details": str(details),
        }
        try:
            self._save_local(event, now.strftime("%Y-%m"))
            if get_usage_sync_config()["auto_sync_enabled"]:
                self.sync_now(silent=True)
        except Exception:
            logger.exception("保存使用记录失败")

    def _save_local(self, event: dict, month: str) -> None:
        path = self._usage_file(month, event["employee_id"])
        with self._write_lock:
            data = load_json_safe(path, {})
            if not data:
                data = {
                    "schema_version": 2,
                    "month": month,
                    "employee_id": event["employee_id"],
                    "employee_name": event["employee_name"],
                    "department": event["department"],
                    "client_id": self._client_id,
                    "app_version": APP_VERSION,
                    "events": [],
                }
            data["employee_name"] = event["employee_name"]
            data["department"] = event["department"]
            data["app_version"] = APP_VERSION
            data.setdefault("events", []).append(event)
            self._save_json_atomic(path, data)

    def _usage_file(self, month: str, employee_id: str) -> str:
        safe_employee_id = normalize_employee_id(employee_id)
        return os.path.join(USAGE_DIR, month, safe_employee_id, f"{self._client_id}.json")

    @staticmethod
    def _save_json_atomic(path: str, data: dict) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        temp_path = f"{path}.{uuid.uuid4().hex}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass

    @staticmethod
    def local_usage_files() -> list[str]:
        """仅返回新结构中的记录，旧版 usage_YYYY-MM.json 保留但不再同步。"""
        files = []
        try:
            for month in os.listdir(USAGE_DIR):
                month_path = os.path.join(USAGE_DIR, month)
                if len(month) != 7 or month[4:5] != "-" or not os.path.isdir(month_path):
                    continue
                for employee_id in os.listdir(month_path):
                    employee_path = os.path.join(month_path, employee_id)
                    if not os.path.isdir(employee_path):
                        continue
                    for name in os.listdir(employee_path):
                        if name.endswith(".json"):
                            files.append(os.path.join(employee_path, name))
            return sorted(files)
        except OSError:
            return []

    def sync_now(self, silent: bool = False) -> str:
        """将本地结构原样同步为：共享目录/月/工号/client_id.json。"""
        share_path = get_network_share_path()
        if not share_path:
            return "尚未配置共享目录。"
        files = self.local_usage_files()
        if not files:
            return "本地暂无新格式的使用记录可同步。"
        try:
            copied = 0
            usage_root = os.path.abspath(USAGE_DIR)
            share_root = os.path.abspath(share_path)
            for source in files:
                relative_path = os.path.relpath(os.path.abspath(source), usage_root)
                if relative_path.startswith(".."):
                    continue
                destination = os.path.abspath(os.path.join(share_root, relative_path))
                if os.path.commonpath((share_root, destination)) != share_root:
                    continue
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                temp_destination = f"{destination}.{uuid.uuid4().hex}.tmp"
                try:
                    shutil.copy2(source, temp_destination)
                    os.replace(temp_destination, destination)
                    copied += 1
                finally:
                    try:
                        if os.path.exists(temp_destination):
                            os.remove(temp_destination)
                    except OSError:
                        pass
            return f"同步完成，已复制 {copied} 个客户端记录文件。"
        except OSError as exc:
            if not silent:
                logger.warning("使用记录同步失败: %s", exc)
            return f"同步失败：{exc}"


_tracker: Optional[UsageTracker] = None


def get_tracker() -> UsageTracker:
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
