"""
src/mateai/domain/repository_ports.py
=====================================
Giao diện cổng truy xuất dữ liệu trừu tượng (Repository Interfaces / Ports).

Tuân thủ:
- RULE-001 & RULE-002: Hoàn toàn tinh khiết, không import SQLite, PostgreSQL hay ORM.
- Dependency Inversion: Tầng Domain & Application chỉ phụ thuộc vào giao diện này.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from mateai.domain.identity.entities import UserIdentity
from mateai.domain.audit.entities import AuditEvent
from mateai.domain.tasks.entities import BackgroundTask
from mateai.domain.devices.entities import Device


class UserRepositoryPort(ABC):
    """Cổng truy xuất dữ liệu Người dùng."""

    @abstractmethod
    async def get_by_id(self, user_id: str) -> Optional[UserIdentity]:
        pass

    @abstractmethod
    async def get_by_username(self, username: str) -> Optional[UserIdentity]:
        pass

    @abstractmethod
    async def list_users(self) -> List[UserIdentity]:
        pass

    @abstractmethod
    async def create_user(self, user: UserIdentity, password_hash: str) -> bool:
        pass


class AuditRepositoryPort(ABC):
    """Cổng lưu trữ và truy vấn Nhật ký kiểm toán an ninh."""

    @abstractmethod
    async def record_event(self, event: AuditEvent) -> bool:
        pass

    @abstractmethod
    async def list_events(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def count_events(self) -> int:
        pass


class TaskRepositoryPort(ABC):
    """Cổng lưu trữ tác vụ nền."""

    @abstractmethod
    async def create_task(self, task: BackgroundTask) -> bool:
        pass

    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        pass

    @abstractmethod
    async def update_status(self, task_id: str, status: str) -> bool:
        pass

    @abstractmethod
    async def list_tasks(self, limit: int = 50) -> List[BackgroundTask]:
        pass


class DeviceRepositoryPort(ABC):
    """Cổng quản lý thiết bị kết nối."""

    @abstractmethod
    async def register_device(self, device: Device) -> bool:
        pass

    @abstractmethod
    async def get_device(self, device_id: str) -> Optional[Device]:
        pass

    @abstractmethod
    async def list_devices(self) -> List[Device]:
        pass
