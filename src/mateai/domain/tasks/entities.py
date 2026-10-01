"""
src/mateai/domain/tasks/entities.py
===================================
Tầng Nghiệp vụ Tác vụ Bất đồng bộ (Tasks Domain Entities).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc Celery/Redis Streams.
- Định nghĩa mô tả công việc nền, độ ưu tiên, và tiến độ hoàn thành.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional
import uuid


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """Đại diện cho một tác vụ chạy nền không đồng bộ (Async Job)."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    result: Optional[Any] = None
