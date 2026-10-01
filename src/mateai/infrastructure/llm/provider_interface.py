"""
src/mateai/infrastructure/llm/provider_interface.py
===================================================
Giao diện trừu tượng duy nhất cho các nhà cung cấp mô hình ngôn ngữ lớn (LLM Provider Interface).

Tuân thủ:
- Single Responsibility: Chỉ đảm nhận giao tiếp mô hình, sinh văn bản và phát luồng (streaming).
- Provider Abstraction: Cách ly hoàn toàn mã nguồn nghiệp vụ khỏi các SDK riêng biệt của DeepSeek, Groq, OpenAI.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional


@dataclass
class ToolCall:
    """Mô tả một lệnh gọi hàm từ LLM."""
    tool_call_id: str
    function_name: str
    arguments_json: str


@dataclass
class StreamChunk:
    """Một đoạn token văn bản trong luồng phát trực tiếp (Streaming Delta)."""
    delta_text: str = ""
    is_final: bool = False
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None


@dataclass
class LLMResponse:
    """Kết quả hoàn chỉnh của một lượt gọi LLM."""
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = "stop"


class LLMProvider(ABC):
    """Giao diện chuẩn cho tất cả LLM Provider Adapters."""

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMResponse:
        """Sinh phản hồi một lần (Non-streaming)."""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        cancel_event: Optional[asyncio.Event] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[StreamChunk, None]:
        """Phát luồng token trực tiếp (Streaming). Dừng ngay khi cancel_event được kích hoạt."""
        pass

    def supports_tools(self) -> bool:
        """Kiểm tra xem provider có hỗ trợ Function Calling hay không."""
        return True

    def supports_streaming(self) -> bool:
        """Kiểm tra xem provider có hỗ trợ Streaming hay không."""
        return True
