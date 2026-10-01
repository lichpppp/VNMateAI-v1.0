"""
src/mateai/application/agent/llm_orchestrator.py
================================================
Bộ điều phối tương tác LLM tại tầng Application (LLM Orchestrator).

Nhiệm vụ:
- Tiếp nhận yêu cầu suy luận từ Agent hoặc Voice Turn Use Case.
- Tương tác với LLM thông qua giao diện trừu tượng `LLMProvider` (Dependency Inversion).
- Truyền phát token theo thời gian thực và tự động dừng khi nhận tín hiệu huỷ (Barge-In).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional

from src.mateai.infrastructure.llm.provider_interface import LLMProvider, StreamChunk, LLMResponse

logger = logging.getLogger(__name__)


class LLMOrchestrator:
    """Bộ điều phối suy luận ngôn ngữ độc lập với công nghệ cụ thể của nhà cung cấp."""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    async def generate_response(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7
    ) -> LLMResponse:
        """Thực hiện suy luận một lượt."""
        return await self.provider.generate(
            messages=messages,
            tools=tools,
            temperature=temperature
        )

    async def stream_response(
        self,
        messages: List[Dict[str, Any]],
        cancel_event: Optional[asyncio.Event] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7
    ) -> AsyncGenerator[str, None]:
        """
        Phát luồng token văn bản trả về.
        Kiểm tra cancel_event tại mỗi token để dừng ngay khi có Barge-In.
        """
        async for chunk in self.provider.stream(
            messages=messages,
            cancel_event=cancel_event,
            tools=tools,
            temperature=temperature
        ):
            if cancel_event and cancel_event.is_set():
                logger.debug("[LLMOrchestrator] Dừng phát luồng token do Barge-In.")
                break

            if chunk.delta_text:
                yield chunk.delta_text
