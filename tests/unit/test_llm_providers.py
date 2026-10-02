"""
tests/unit/test_llm_providers.py
================================
Unit Test Suite cho Bounded Context LLM Provider Abstraction (Phase 5).
Kiểm tra Interface, Adapters, Factory, và LLMOrchestrator.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mateai.infrastructure.llm.provider_interface import (
    LLMProvider,
    LLMResponse,
    StreamChunk,
    ToolCall,
)
from mateai.infrastructure.llm.deepseek_adapter import DeepSeekAdapter
from mateai.infrastructure.llm.groq_adapter import GroqAdapter
from mateai.infrastructure.llm.router_adapter import NineRouterAdapter
from mateai.infrastructure.llm.factory import get_llm_provider
from mateai.application.agent.llm_orchestrator import LLMOrchestrator


class MockLLMProvider(LLMProvider):
    """Mock Provider phục vụ kiểm thử đơn vị độc lập không cần internet."""

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMResponse:
        return LLMResponse(
            content="Xin chào, em là Ly Ly!",
            model="mock-v1",
            prompt_tokens=10,
            completion_tokens=8
        )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        cancel_event: Optional[asyncio.Event] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[StreamChunk, None]:
        tokens = ["Dạ, ", "em ", "chào ", "sếp ", "ạ!"]
        for t in tokens:
            if cancel_event and cancel_event.is_set():
                break
            yield StreamChunk(delta_text=t)
            await asyncio.sleep(0.001)
        yield StreamChunk(is_final=True, finish_reason="stop")


def test_factory_and_adapters():
    print("\n▸ 1. Kiểm thử LLM Factory & Khởi Tạo Adapters")
    ds = get_llm_provider("deepseek", api_key="sk-test")
    assert isinstance(ds, DeepSeekAdapter)
    assert ds.default_model == "deepseek-chat"
    print(f"  ✅ Factory tạo DeepSeekAdapter thành công (model={ds.default_model})")

    groq = get_llm_provider("groq", api_key="sk-groq-test")
    assert isinstance(groq, GroqAdapter)
    assert "groq" in groq.base_url
    print(f"  ✅ Factory tạo GroqAdapter thành công (url={groq.base_url})")

    router = get_llm_provider("router")
    assert isinstance(router, NineRouterAdapter)
    assert "localhost" in router.base_url
    print(f"  ✅ Factory tạo NineRouterAdapter thành công (url={router.base_url})")


async def test_llm_orchestrator_generate_and_stream():
    print("\n▸ 2. Kiểm thử LLMOrchestrator Điều Phối Suy Luận")
    mock_provider = MockLLMProvider()
    orchestrator = LLMOrchestrator(mock_provider)

    # 1. Non-streaming
    resp = await orchestrator.generate_response([{"role": "user", "content": "hello"}])
    assert resp.content == "Xin chào, em là Ly Ly!"
    assert resp.completion_tokens == 8
    print(f"  ✅ Generate response thành công: '{resp.content}'")

    # 2. Streaming
    tokens = []
    async for t in orchestrator.stream_response([{"role": "user", "content": "hi"}]):
        tokens.append(t)
    full_text = "".join(tokens)
    assert full_text == "Dạ, em chào sếp ạ!"
    print(f"  ✅ Stream response thành công: '{full_text}' ({len(tokens)} chunks)")


async def test_llm_orchestrator_cancellation():
    print("\n▸ 3. Kiểm thử Huỷ Luồng Phát Token Tức Thì (Barge-In Interruption)")
    mock_provider = MockLLMProvider()
    orchestrator = LLMOrchestrator(mock_provider)

    cancel_event = asyncio.Event()
    tokens = []
    
    async def cancel_later():
        await asyncio.sleep(0.002)
        cancel_event.set()

    asyncio.create_task(cancel_later())

    async for t in orchestrator.stream_response(
        [{"role": "user", "content": "hi"}],
        cancel_event=cancel_event
    ):
        tokens.append(t)

    # Cần dừng lại trước khi hết 5 tokens do đã bị cancel
    assert len(tokens) < 5
    print(f"  ✅ Đã hủy luồng stream an toàn sau {len(tokens)} chunks khi nhận tín hiệu ngắt lời.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT LLM ABSTRACTION (PHASE 5)")
    print("=" * 65)
    test_factory_and_adapters()
    asyncio.run(test_llm_orchestrator_generate_and_stream())
    asyncio.run(test_llm_orchestrator_cancellation())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 3/3 BÀI KIỂM THỬ LLM ABSTRACTION ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
