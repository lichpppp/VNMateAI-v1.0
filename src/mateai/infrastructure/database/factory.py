"""
src/mateai/infrastructure/database/factory.py
=============================================
Factory cung cấp Repository instances theo cấu hình hệ thống (Database Repository Factory).
"""

from __future__ import annotations

from typing import Optional
from mateai.config.settings import settings
from mateai.domain.repository_ports import (
    UserRepositoryPort,
    AuditRepositoryPort,
    TaskRepositoryPort,
    DeviceRepositoryPort,
)
from mateai.infrastructure.database.sqlite_repository import (
    SQLiteUserRepository,
    SQLiteAuditRepository,
    SQLiteTaskRepository,
    SQLiteDeviceRepository,
)


class RepositoryRegistry:
    """Tập hợp các repository hoạt động (Unit of Repositories)."""

    def __init__(
        self,
        user_repo: UserRepositoryPort,
        audit_repo: AuditRepositoryPort,
        task_repo: TaskRepositoryPort,
        device_repo: DeviceRepositoryPort
    ):
        self.users = user_repo
        self.audits = audit_repo
        self.tasks = task_repo
        self.devices = device_repo


def get_repository_registry(db_path: Optional[str] = None) -> RepositoryRegistry:
    """Khởi tạo và cung cấp tập hợp Repositories."""
    # Mặc định sử dụng SQLite Adapter (kế thừa vnmateai.db)
    user_repo = SQLiteUserRepository(db_path)
    audit_repo = SQLiteAuditRepository(db_path)
    task_repo = SQLiteTaskRepository(db_path)
    device_repo = SQLiteDeviceRepository(db_path)

    return RepositoryRegistry(
        user_repo=user_repo,
        audit_repo=audit_repo,
        task_repo=task_repo,
        device_repo=device_repo
    )


# Singleton registry
db_repositories = get_repository_registry()
