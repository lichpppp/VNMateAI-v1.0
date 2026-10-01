"""
src/mateai/domain/skills/entities.py
====================================
Tầng Nghiệp vụ Kỹ năng & Công cụ (Skills & Tools Domain Entities).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc PluginManager hay FastApi.
- Định nghĩa phân loại 10 miền nghiệp vụ chuẩn, cấp độ rủi ro, và định nghĩa công cụ nguyên tử.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SkillDomain(str, Enum):
    SYSTEM_OPS = "system_ops"
    NETWORK_SECURITY = "network_security"
    FILE_STORAGE = "file_storage"
    DATABASE_ERP = "database_erp"
    PC_AUTOMATION = "pc_automation"
    KNOWLEDGE_RAG = "knowledge_rag"
    ITSM_WORKFLOW = "itsm_workflow"
    MULTI_AGENT = "multi_agent"
    VISUAL_MEDIA = "visual_media"
    GENERAL_TOOLS = "general_tools"


class ToolRiskLevel(int, Enum):
    LEVEL_0_READ_ONLY = 0    # Đọc an toàn (xem giờ, kiểm tra ram)
    LEVEL_1_LOW = 1          # Thao tác thấp (chụp màn hình, ping host)
    LEVEL_2_MEDIUM = 2       # Thao tác có tác động (mở ứng dụng, tạo ticket)
    LEVEL_3_HIGH = 3         # Tác động cao (dừng tiến trình, chạy query SQL)
    LEVEL_4_CRITICAL = 4     # Nguy hiểm cao (xóa dữ liệu, restart máy chủ)


@dataclass
class ToolDefinition:
    """Đại diện cho một công cụ nguyên tử (Atomic Tool) trong hệ thống."""
    name: str
    description: str
    domain: SkillDomain
    parameters: Dict[str, Any] = field(default_factory=dict)
    risk_level: ToolRiskLevel = ToolRiskLevel.LEVEL_0_READ_ONLY
    required_clearance: str = "INTERNAL"
    is_async: bool = True
    timeout_seconds: float = 15.0

    def to_openai_tool_schema(self) -> Dict[str, Any]:
        """Chuyển đổi thành định dạng Function Call tiêu chuẩn của LLM."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}}
            }
        }


@dataclass
class SkillDefinition:
    """Đại diện cho một năng lực nghiệp vụ cấp cao (Skill Capability)."""
    skill_id: str
    name: str
    domain: SkillDomain
    description: str
    tools: List[ToolDefinition] = field(default_factory=list)
