"""员工登录资料的本地保存。"""

from dataclasses import asdict, dataclass
from datetime import datetime
import re
from typing import Optional

from config import EMPLOYEE_PROFILE_FILE, load_json_safe, save_json_safe

_EMPLOYEE_ID_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")


def normalize_employee_id(employee_id: str) -> str:
    """规范化工号，并保证其可安全用于目录名称。"""
    normalized = str(employee_id).strip().upper()
    if not _EMPLOYEE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("工号只能包含字母、数字、下划线或连字符，且长度不能超过 32 位。")
    return normalized


@dataclass
class EmployeeProfile:
    employee_id: str
    name: str
    department: str = ""
    last_login: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def save_employee_profile(profile: EmployeeProfile) -> bool:
    profile.employee_id = normalize_employee_id(profile.employee_id)
    profile.last_login = datetime.now().isoformat(timespec="seconds")
    return save_json_safe(EMPLOYEE_PROFILE_FILE, profile.to_dict())


def load_employee_profile() -> Optional[EmployeeProfile]:
    data = load_json_safe(EMPLOYEE_PROFILE_FILE, {})
    if not data.get("employee_id") or not data.get("name"):
        return None
    try:
        employee_id = normalize_employee_id(data["employee_id"])
    except ValueError:
        return None
    return EmployeeProfile(
        employee_id=employee_id,
        name=str(data["name"]),
        department=str(data.get("department", "")),
        last_login=str(data.get("last_login", "")),
    )
