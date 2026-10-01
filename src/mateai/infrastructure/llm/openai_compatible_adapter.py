"""
src/mateai/infrastructure/llm/openai_compatible_adapter.py
==========================================================
Adapter giao tiếp với các dịch vụ tương thích chuẩn OpenAI Chat Completions API.

Đặc điểm:
- Tái sử dụng HTTP Client Pool với HTTP/2 và Keep-Alive 300s (zero TCP/TLS handshake overhead).
- Xử lý SSE (Server-Sent Events) streaming delta text.
- Kiểm tra Barge-In Cancellation Event tại từng chunk.
- Xử lý function calling / tool calls chuẩn OpenAPI schema.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator, Dict, List, Optional
import httpx

from src.mateai.infrastructure.llm.provider_interface import (
    LLMProvider,
    LLMResponse,
    StreamChunk,
    ToolCall,
)

logger = logging.getLogger(__name__)


class OpenAICompatibleAdapter(LLMProvider):
    """Adapter kỹ thuật kết nối các API tương thích OpenAI qua HTTP/2."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_seconds: float = 30.0,
        http_client: Optional[httpx.AsyncClient] = None
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model
        self.timeout_seconds = timeout_seconds
        self._custom_client = http_client

    def _get_client(self) -> httpx.AsyncClient:
        if self._custom_client and not self._custom_client.is_closed:
            return self._custom_client
        # Cấu hình persistent pool kết nối
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50, keepalive_expiry=300.0)
        return httpx.AsyncClient(timeout=self.timeout_seconds, http2=True, limits=limits)

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        client = self._get_client()
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""
        finish_reason = choice.get("finish_reason", "stop")

        tool_calls: List[ToolCall] = []
        if "tool_calls" in msg and msg["tool_calls"]:
            for tc in msg["tool_calls"]:
                tool_calls.append(
                    ToolCall(
                        tool_call_id=tc.get("id", ""),
                        function_name=tc.get("function", {}).get("name", ""),
                        arguments_json=tc.get("function", {}).get("arguments", "{}")
                    )
                )

        usage = data.get("usage", {})
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=data.get("model", self.default_model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=finish_reason
        )

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        cancel_event: Optional[asyncio.Event] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> AsyncGenerator[StreamChunk, None]:
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload: Dict[str, Any] = {
            "model": self.default_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        client = self._get_client()
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if cancel_event and cancel_event.is_set():
                    logger.debug("[OpenAIAdapter] Dừng stream do có tín hiệu ngắt lời (Barge-In).")
                    break

                line = line.strip()
                if not line or not line.startswith("data: "):
                    continue

                raw_data = line[6:]
                if raw_data == "[DONE]":
                    yield StreamChunk(is_final=True, finish_reason="stop")
                    break

                try:
                    chunk_json = json.loads(raw_data)
                    choice = chunk_json.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    content_piece = delta.get("content", "")
                    finish_reason = choice.get("finish_reason")

                    if content_piece:
                        yield StreamChunk(
                            delta_text=content_piece,
                            is_final=bool(finish_reason),
                            finish_reason=finish_reason
                        )
                except Exception as e:
                    logger.debug(f"[OpenAIAdapter] Parse error on chunk: {e}")
                    continue
