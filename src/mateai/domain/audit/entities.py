"""
src/mateai/domain/audit/entities.py
===================================
Tầng Nghiệp vụ Nhật ký Kiểm toán (Audit Domain Entities).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc database hay web frameworks.
- Định nghĩa sự kiện kiểm toán bất biến (Actor, Action, Target, Risk, Result).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import uuid


class AuditAction(str, Enum):
    USER_LOGIN = "user_login"
    USER_LOGOUT = "user_logout"
    PERMISSION_CHANGE = "permission_change"
    TOOL_EXECUTE = "tool_execute"
    SYSTEM_CONFIG_CHANGE = "system_config_change"
    DATA_ACCESS = "data_access"
    DEVICE_CONTROL = "device_control"
    BARGE_IN_TRIGGERED = "barge_in_triggered"


class AuditRiskLevel(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class AuditEvent:
    """Sự kiện kiểm toán an ninh hệ thống bất biến (Immutable Audit Event)."""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    actor_id: str = "system"
    action: AuditAction = AuditAction.TOOL_EXECUTE
    target: str = ""
    risk_level: AuditRiskLevel = AuditRiskLevel.INFO
    is_success: bool = True
    details: Dict[str, Any] = field(default_factory=dict)
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
