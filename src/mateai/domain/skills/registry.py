"""
src/mateai/domain/skills/registry.py
====================================
Sổ đăng ký công cụ chuẩn hóa duy nhất của hệ thống (Canonical Tool Registry).

Nhiệm vụ:
- Quản lý danh mục toàn bộ các công cụ nguyên tử (Atomic Tools) thuộc 10 Miền Nghiệp Vụ.
- Cung cấp schema Function Calling cho LLM.
- Đảm bảo tính nhất quán, không đăng ký trùng lặp hoặc mâu thuẫn tên công cụ.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from src.mateai.domain.skills.entities import SkillDomain, ToolDefinition, ToolRiskLevel

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Kho lưu trữ và đăng ký công cụ nguyên tử tập trung."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Đăng ký một công cụ mới vào hệ thống."""
        if tool.name in self._tools:
            logger.debug("[ToolRegistry] Cập nhật công cụ đã tồn tại: %s", tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Optional[ToolDefinition]:
        """Truy xuất định nghĩa công cụ theo tên."""
        return self._tools.get(name)

    def get_all(self) -> List[ToolDefinition]:
        """Lấy danh sách tất cả các công cụ đã đăng ký."""
        return list(self._tools.values())

    def get_by_domain(self, domain: SkillDomain) -> List[ToolDefinition]:
        """Lấy danh sách công cụ thuộc một miền nghiệp vụ cụ thể."""
        return [t for t in self._tools.values() if t.domain == domain]

    def get_domain_stats(self) -> Dict[str, int]:
        """Thống kê số lượng công cụ trên từng miền nghiệp vụ."""
        stats: Dict[str, int] = {d.value: 0 for d in SkillDomain}
        for t in self._tools.values():
            stats[t.domain.value] = stats.get(t.domain.value, 0) + 1
        return stats

    def to_openai_tools(self, tool_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Xuất danh sách schema Function Call cho LLM."""
        if tool_names is None:
            return [t.to_openai_tool_schema() for t in self._tools.values()]
        return [
            self._tools[name].to_openai_tool_schema()
            for name in tool_names
            if name in self._tools
        ]


# Singleton tool registry
tool_registry = ToolRegistry()
