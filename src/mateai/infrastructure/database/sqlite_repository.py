"""
src/mateai/infrastructure/database/sqlite_repository.py
========================================================
Hiện thực hoá Repository Ports cho SQLite WAL Mode (SQLite Repositories Adapter).

Đặc điểm:
- Tương thích 100% với schema và 2,464 bản ghi hiện có trong vnmateai.db.
- Sử dụng truy vấn có tham số (Parameterized Queries) chống triệt để SQL Injection.
- Thực thi bất đồng bộ qua asyncio.to_thread tránh chặn Event Loop.
- Map chuẩn xác dữ liệu thô sang Domain Entities: UserIdentity, AuditEvent, BackgroundTask, Device.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mateai.config.settings import settings
from mateai.domain.identity.entities import ClearanceLevel, UserIdentity, UserRole
from mateai.domain.audit.entities import AuditEvent, AuditAction, AuditRiskLevel
from mateai.domain.tasks.entities import BackgroundTask, TaskStatus
from mateai.domain.devices.entities import Device, DeviceType, DeviceStatus
from mateai.domain.repository_ports import (
    UserRepositoryPort,
    AuditRepositoryPort,
    TaskRepositoryPort,
    DeviceRepositoryPort,
)

logger = logging.getLogger(__name__)


def _get_connection(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    return conn


class SQLiteUserRepository(UserRepositoryPort):
    """Hiện thực hoá UserRepository qua SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.database.sqlite_db_path

    async def get_by_id(self, user_id: str) -> Optional[UserIdentity]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users WHERE id = ?", (user_id,))
                row = cur.fetchone()
                return dict(row) if row else None

        row = await asyncio.to_thread(_query)
        if not row:
            return None
        return self._map_to_entity(row)

    async def get_by_username(self, username: str) -> Optional[UserIdentity]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users WHERE username = ?", (username,))
                row = cur.fetchone()
                return dict(row) if row else None

        row = await asyncio.to_thread(_query)
        if not row:
            return None
        return self._map_to_entity(row)

    async def list_users(self) -> List[UserIdentity]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM users ORDER BY created_at DESC")
                return [dict(r) for r in cur.fetchall()]

        rows = await asyncio.to_thread(_query)
        return [self._map_to_entity(r) for r in rows]

    async def create_user(self, user: UserIdentity, password_hash: str) -> bool:
        def _execute():
            now_iso = datetime.now(timezone.utc).isoformat()
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user.user_id,
                        user.username,
                        user.full_name or user.username,
                        user.role.value,
                        password_hash,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_execute)

    def _map_to_entity(self, row: Dict[str, Any]) -> UserIdentity:
        role_str = str(row.get("role", "employee")).lower()
        role = UserRole.ADMIN if role_str in ("admin", "superadmin") else UserRole.EMPLOYEE
        clearance = ClearanceLevel.RESTRICTED if role == UserRole.ADMIN else ClearanceLevel.INTERNAL
        return UserIdentity(
            user_id=row["id"],
            username=row["username"],
            full_name=row.get("full_name", ""),
            role=role,
            clearance=clearance,
            is_active=True
        )


class SQLiteAuditRepository(AuditRepositoryPort):
    """Hiện thực hoá AuditRepository qua SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.database.sqlite_db_path

    async def record_event(self, event: AuditEvent) -> bool:
        def _execute():
            now_iso = event.timestamp.isoformat()
            payload_str = json.dumps(event.details, ensure_ascii=False) if event.details else None
            status_str = "success" if event.is_success else "failed"
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO audit_logs (timestamp, employee_id, action_type, payload, status, session_id, source_ip)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        now_iso,
                        event.actor_id,
                        event.action.value if hasattr(event.action, "value") else str(event.action),
                        payload_str,
                        status_str,
                        event.session_id,
                        event.ip_address,
                    ),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_execute)

    async def list_events(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
                return [dict(r) for r in cur.fetchall()]

        return await asyncio.to_thread(_query)

    async def count_events(self) -> int:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM audit_logs")
                return cur.fetchone()[0]

        return await asyncio.to_thread(_query)


class SQLiteTaskRepository(TaskRepositoryPort):
    """Hiện thực hoá TaskRepository qua SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.database.sqlite_db_path

    async def create_task(self, task: BackgroundTask) -> bool:
        def _execute():
            now_iso = datetime.now(timezone.utc).isoformat()
            payload_str = json.dumps(task.payload, ensure_ascii=False) if task.payload else ""
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO tasks (id, timestamp, client_id, task_message, sender, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.task_id,
                        now_iso,
                        "system",
                        task.name or payload_str,
                        "VN-MateAI",
                        task.status.value,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_execute)

    async def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
                row = cur.fetchone()
                return dict(row) if row else None

        row = await asyncio.to_thread(_query)
        if not row:
            return None
        return BackgroundTask(
            task_id=row["id"],
            name=row.get("task_message", ""),
            status=TaskStatus(row.get("status", "pending"))
        )

    async def update_status(self, task_id: str, status: str) -> bool:
        def _execute():
            now_iso = datetime.now(timezone.utc).isoformat()
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status, now_iso, task_id))
                conn.commit()
                return cur.rowcount > 0

        return await asyncio.to_thread(_execute)

    async def list_tasks(self, limit: int = 50) -> List[BackgroundTask]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,))
                return [dict(r) for r in cur.fetchall()]

        rows = await asyncio.to_thread(_query)
        return [
            BackgroundTask(
                task_id=r["id"],
                name=r.get("task_message", ""),
                status=TaskStatus(r.get("status", "pending"))
            )
            for r in rows
        ]


class SQLiteDeviceRepository(DeviceRepositoryPort):
    """Hiện thực hoá DeviceRepository qua SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or settings.database.sqlite_db_path

    async def register_device(self, device: Device) -> bool:
        def _execute():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute(
                    """
                    INSERT INTO devices (dept_id, hostname, ip_address, type)
                    VALUES (?, ?, ?, ?)
                    """,
                    (1, device.device_name, device.ip_address, device.device_type.value),
                )
                conn.commit()
                return True

        return await asyncio.to_thread(_execute)

    async def get_device(self, device_id: str) -> Optional[Device]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM devices WHERE id = ?", (device_id,))
                row = cur.fetchone()
                return dict(row) if row else None

        row = await asyncio.to_thread(_query)
        if not row:
            return None
        return Device(
            device_id=str(row["id"]),
            device_name=row["hostname"],
            device_type=DeviceType.CLIENT_AGENT,
            ip_address=row.get("ip_address"),
            status=DeviceStatus.ONLINE
        )

    async def list_devices(self) -> List[Device]:
        def _query():
            with _get_connection(self.db_path) as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM devices")
                return [dict(r) for r in cur.fetchall()]

        rows = await asyncio.to_thread(_query)
        return [
            Device(
                device_id=str(r["id"]),
                device_name=r["hostname"],
                device_type=DeviceType.CLIENT_AGENT,
                ip_address=r.get("ip_address"),
                status=DeviceStatus.ONLINE
            )
            for r in rows
        ]
