"""
src/mateai/domain/conversation/entities.py
==========================================
Tầng Nghiệp vụ Đàm thoại (Conversation Domain Entities & Context).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python dataclasses, không phụ thuộc LLM SDK hay Web framework.
- Định nghĩa thông điệp, ngữ cảnh hội thoại, lịch sử lượt trao đổi và chính sách rút gọn context.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    """Đại diện cho một thông điệp trong cuộc trò chuyện."""
    role: MessageRole
    content: str
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"role": self.role.value, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class ConversationContext:
    """Ngữ cảnh hội thoại quản lý danh sách thông điệp và chính sách cắt tỉa."""
    conversation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    system_prompt: str = ""
    messages: List[Message] = field(default_factory=list)
    max_turns: int = 15
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def add_user_message(self, text: str) -> Message:
        msg = Message(role=MessageRole.USER, content=text)
        self.messages.append(msg)
        return msg

    def add_assistant_message(self, text: str) -> Message:
        msg = Message(role=MessageRole.ASSISTANT, content=text)
        self.messages.append(msg)
        return msg

    def get_messages_for_llm(self) -> List[Dict[str, Any]]:
        """Trả về payload danh sách message chuẩn bị gửi cho LLM Engine."""
        result: List[Dict[str, Any]] = []
        if self.system_prompt:
            result.append({"role": MessageRole.SYSTEM.value, "content": self.system_prompt})
        for msg in self.messages[- (self.max_turns * 2):]:
            result.append(msg.to_dict())
        return result
