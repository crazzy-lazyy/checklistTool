"""生成并持久化客户端唯一标识。"""

import json
import os
import socket
import uuid
from datetime import datetime
from typing import Optional

from config import CLIENT_ID_FALLBACK_FILE, CLIENT_ID_FILE, load_json_safe

_cached_client_id: Optional[str] = None


def _read_client_id(path: str) -> Optional[str]:
    value = str(load_json_safe(path, {}).get("client_id", "")).strip()
    try:
        return str(uuid.UUID(value)) if value else None
    except ValueError:
        return None


def _create_client_id(path: str) -> str:
    """仅在文件不存在时创建，避免两个首次启动进程生成不同标识。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    candidate = str(uuid.uuid4())
    payload = {
        "client_id": candidate,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "computer_name": socket.gethostname(),
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        return candidate
    except FileExistsError:
        existing = _read_client_id(path)
        if existing:
            return existing
        raise RuntimeError(f"客户端标识文件无效：{path}")


def get_client_id() -> str:
    """读取稳定的客户端 ID；首次运行时生成 UUID v4。"""
    global _cached_client_id
    if _cached_client_id:
        return _cached_client_id

    for path in (CLIENT_ID_FILE, CLIENT_ID_FALLBACK_FILE):
        existing = _read_client_id(path)
        if existing:
            _cached_client_id = existing
            return existing
        try:
            _cached_client_id = _create_client_id(path)
            return _cached_client_id
        except (OSError, RuntimeError):
            continue

    raise OSError("无法创建客户端标识文件，请检查本地目录写入权限。")
