"""
src/mateai/domain/agent/entities.py
===================================
Tầng Nghiệp vụ Tác tử AI (Agent Domain Entities & Tri-Brain Concepts).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc OpenAI SDK hay FastAPI.
- Phân định rõ 3 Bộ não chuyên biệt (BrainType), Kế hoạch hành động (Plan) và Trạng thái tác tử.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class BrainType(str, Enum):
    CONTROLLER = "controller"   # Bộ não điều phối ý định (Supervisor)
    VOICE = "voice"             # Bộ não phản hồi thoại tức thì (Zero-tool, low-latency)
    OPERATIONS = "operations"   # Bộ não vận hành tác vụ & công cụ (79 Skills loaded)


class AgentState(str, Enum):
    IDLE = "idle"
    PLANNING = "planning"
    EXECUTING = "executing"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ExecutionStep:
    """Từng bước thực thi độc lập trong kế hoạch của Agent."""
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    tool_name: str = ""
    tool_arguments: Dict[str, Any] = field(default_factory=dict)
    expected_outcome: str = ""
    actual_outcome: Optional[str] = None
    is_completed: bool = False
    error: Optional[str] = None


@dataclass
class AgentTask:
    """Đại diện cho một nhiệm vụ cấp cao được giao cho Agent xử lý."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    goal: str = ""
    assigned_brain: BrainType = BrainType.OPERATIONS
    state: AgentState = AgentState.IDLE
    steps: List[ExecutionStep] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
