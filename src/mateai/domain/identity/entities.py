"""
src/mateai/domain/identity/entities.py
======================================
Tầng Nghiệp vụ Định danh & Phân quyền (Identity & RBAC Domain Entities).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc database hay web cookies/tokens.
- Định nghĩa các cấp độ phân loại dữ liệu (Clearance Level), vai trò (Role), và bối cảnh phòng ban.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional, Set
import uuid


class ClearanceLevel(str, Enum):
    PUBLIC = "PUBLIC"              # Thông tin công khai
    INTERNAL = "INTERNAL"          # Nội bộ công ty
    CONFIDENTIAL = "CONFIDENTIAL"  # Bảo mật (Tài chính, Nhân sự, KPI)
    RESTRICTED = "RESTRICTED"      # Tối mật (Hệ thống cốt lõi, Khóa bảo mật)


class UserRole(str, Enum):
    GUEST = "guest"
    EMPLOYEE = "employee"
    OPERATOR = "operator"
    MANAGER = "manager"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"


@dataclass
class DepartmentContext:
    """Đại diện cho bối cảnh phân quyền phòng ban trong doanh nghiệp."""
    department_id: str
    department_name: str
    allowed_domains: Set[str] = field(default_factory=set)


@dataclass
class UserIdentity:
    """Đại diện cho danh tính người dùng hoặc nhân viên được hệ thống xác thực."""
    user_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    username: str = ""
    full_name: str = ""
    email: Optional[str] = None
    role: UserRole = UserRole.EMPLOYEE
    clearance: ClearanceLevel = ClearanceLevel.INTERNAL
    department: Optional[DepartmentContext] = None
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def has_permission_for_clearance(self, required: ClearanceLevel) -> bool:
        """Kiểm tra quyền truy cập dựa trên cấp độ bảo mật."""
        hierarchy = {
            ClearanceLevel.PUBLIC: 0,
            ClearanceLevel.INTERNAL: 1,
            ClearanceLevel.CONFIDENTIAL: 2,
            ClearanceLevel.RESTRICTED: 3,
        }
        return hierarchy.get(self.clearance, 0) >= hierarchy.get(required, 0)

    def is_admin(self) -> bool:
        return self.role in {UserRole.ADMIN, UserRole.SUPERADMIN}
