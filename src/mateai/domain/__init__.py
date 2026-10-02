"""
src/mateai/domain
=================
Tầng Nghiệp vụ Cốt lõi (Domain Layer) — Clean Architecture.

Cung cấp các thực thể nghiệp vụ tinh khiết (Pure Python Entities & Value Objects):
- Voice: VoiceSession, VoiceState, VoiceCommand, VoiceInterruption, AudioFrame
- Conversation: Message, MessageRole, ConversationContext
- Agent: BrainType, AgentState, AgentTask, ExecutionStep
- Skills: SkillDomain, ToolRiskLevel, ToolDefinition, SkillDefinition
- Identity: UserIdentity, UserRole, ClearanceLevel, DepartmentContext
- Devices: Device, DeviceType, DeviceStatus
- Tasks: BackgroundTask, TaskPriority, TaskStatus
- Audit: AuditEvent, AuditAction, AuditRiskLevel
"""

from mateai.domain.voice.entities import (
    VoiceSession,
    VoiceState,
    VoiceCommand,
    VoiceInterruption,
    AudioFrame,
    AudioEncoding,
)
from mateai.domain.conversation.entities import (
    Message,
    MessageRole,
    ConversationContext,
)
from mateai.domain.agent.entities import (
    BrainType,
    AgentState,
    AgentTask,
    ExecutionStep,
)
from mateai.domain.skills.entities import (
    SkillDomain,
    ToolRiskLevel,
    ToolDefinition,
    SkillDefinition,
)
from mateai.domain.identity.entities import (
    UserIdentity,
    UserRole,
    ClearanceLevel,
    DepartmentContext,
)
from mateai.domain.devices.entities import (
    Device,
    DeviceType,
    DeviceStatus,
)
from mateai.domain.tasks.entities import (
    BackgroundTask,
    TaskPriority,
    TaskStatus,
)
from mateai.domain.audit.entities import (
    AuditEvent,
    AuditAction,
    AuditRiskLevel,
)

__all__ = [
    "VoiceSession",
    "VoiceState",
    "VoiceCommand",
    "VoiceInterruption",
    "AudioFrame",
    "AudioEncoding",
    "Message",
    "MessageRole",
    "ConversationContext",
    "BrainType",
    "AgentState",
    "AgentTask",
    "ExecutionStep",
    "SkillDomain",
    "ToolRiskLevel",
    "ToolDefinition",
    "SkillDefinition",
    "UserIdentity",
    "UserRole",
    "ClearanceLevel",
    "DepartmentContext",
    "Device",
    "DeviceType",
    "DeviceStatus",
    "BackgroundTask",
    "TaskPriority",
    "TaskStatus",
    "AuditEvent",
    "AuditAction",
    "AuditRiskLevel",
]
