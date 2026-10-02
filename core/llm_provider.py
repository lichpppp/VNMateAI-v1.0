"""
core/llm_provider.py
====================
Phase 2: LLM Streaming & Provider Abstraction (Realtime Voice Revamp).

Kiến trúc:
  - BaseLLMProvider (ABC) định nghĩa interface chuẩn cho mọi Provider:
      async for chunk in provider.stream(messages, tools, ...):
          ...
      async for token in provider.stream_tokens(messages, tools, ...):
          ...
  - DirectLLMProvider: Kết nối trực tiếp (LM Studio / Ollama / DeepSeek / vLLM) bỏ qua proxy.
  - NineRouterLLMProvider: Kết nối qua 9router OpenAI-compatible proxy kèm Auto-Fallback thông minh.
  - TriBrainLLMProvider: Điều phối 3 Bộ Não (Supervisor, Voice, Operations) theo vai trò.
  - LLMStreamEventBus: Kênh phát sóng Token Stream đồng thời tới:
      (1) UI WebSocket (text_delta events)
      (2) SentenceBuffer / TTS Queue
"""

from __future__ import annotations

import abc
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Union

import openai

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models for Streaming
# ---------------------------------------------------------------------------

@dataclass
class LLMStreamChunk:
    """Đại diện cho một gói token hoặc tool call delta từ LLM."""
    content: str = ""
    tool_calls: Optional[List[Dict[str, Any]]] = None
    finish_reason: Optional[str] = None
    model: str = ""
    role: str = "assistant"
    reasoning: Optional[str] = None
    ttft_ms: Optional[int] = None


# ---------------------------------------------------------------------------
# Sức khoẻ model (dùng chung mọi provider / mọi phiên trong tiến trình)
# ---------------------------------------------------------------------------

#: Model lỗi / quá hạn bị xếp xuống cuối danh sách trong ngần ấy giây. Trước
#: Phase 5 mỗi lượt thử lại từ đầu cả danh sách: 6 model hỏng × tới 5s mỗi cái
#: = chữ đầu tiên sau ~22s (đo 2026-10-01).
MODEL_COOLDOWN_S = 120.0
_model_down_until: Dict[str, float] = {}

#: Giá trị mẫu còn sót trong config (vd. YOUR_MODEL_NAME_HERE) — không phải model.
_PLACEHOLDER_RE = re.compile(r"^YOUR_[A-Z0-9_]*_HERE$", re.IGNORECASE)


def _mark_model_failed(model: str, reason: Any) -> None:
    _model_down_until[model] = time.monotonic() + MODEL_COOLDOWN_S
    logger.warning("[LLMProvider] Tạm xếp cuối model '%s' trong %.0fs: %s", model, MODEL_COOLDOWN_S, reason)


def _mark_model_ok(model: str) -> None:
    _model_down_until.pop(model, None)


def model_health() -> Dict[str, float]:
    """Model đang bị xếp cuối -> số giây còn lại (cho chẩn đoán)."""
    now = time.monotonic()
    return {m: round(t - now, 1) for m, t in _model_down_until.items() if t > now}


# ---------------------------------------------------------------------------
# Abstract Base Provider
# ---------------------------------------------------------------------------

class BaseLLMProvider(abc.ABC):
    """Lớp cơ sở trừu tượng cho mọi LLM Provider."""

    @abc.abstractmethod
    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        """Stream chunks từ model từng token một."""
        pass

    async def stream_tokens(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream chỉ lấy token văn bản thô (text delta) để phát ra loa hoặc render UI."""
        async for chunk in self.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        ):
            if chunk.content:
                yield chunk.content

    @abc.abstractmethod
    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        """Thực hiện gọi non-streaming thông thường (khi cần chạy tool loop sâu)."""
        pass


# ---------------------------------------------------------------------------
# Direct Mode Provider (Local LM Studio / Ollama / Direct endpoints)
# ---------------------------------------------------------------------------

class DirectLLMProvider(BaseLLMProvider):
    """
    Gọi thẳng vào endpoint cục bộ (LM Studio, Ollama, vLLM) không qua 9router proxy.
    Giảm ~150-300ms độ trễ mạng cho chế độ Voice Realtime.
    """

    def __init__(self, direct_client: openai.AsyncOpenAI, direct_model: str, direct_url: str) -> None:
        self.client = direct_client
        self.model = direct_model
        self.url = direct_url

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        kwargs_api: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            kwargs_api["tools"] = tools
            kwargs_api["tool_choice"] = "auto"

        logger.info("[DirectLLMProvider] Mở stream trực tiếp tới %s (model=%s)", self.url, self.model)
        t0 = time.monotonic()
        t_first_token: Optional[float] = None

        stream = await asyncio.wait_for(
            self.client.chat.completions.create(**kwargs_api),
            timeout=5.0,
        )

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta
            token = getattr(delta, "content", "") or ""
            reasoning = getattr(delta, "reasoning", "") or getattr(delta, "reasoning_content", "") or None
            tool_calls = None
            if getattr(delta, "tool_calls", None):
                tool_calls = [
                    {
                        "index": tc.index,
                        "id": getattr(tc, "id", ""),
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                    }
                    for tc in delta.tool_calls
                ]

            ttft = None
            if token and t_first_token is None:
                t_first_token = time.monotonic()
                ttft = int((t_first_token - t0) * 1000)

            yield LLMStreamChunk(
                content=token,
                tool_calls=tool_calls,
                finish_reason=getattr(choice, "finish_reason", None),
                model=self.model,
                reasoning=reasoning,
                ttft_ms=ttft,
            )

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        kwargs_api: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            kwargs_api["tools"] = tools
            kwargs_api["tool_choice"] = "auto"

        return await asyncio.wait_for(
            self.client.chat.completions.create(**kwargs_api),
            timeout=8.0,
        )


# ---------------------------------------------------------------------------
# 9Router Provider (OpenAI-compatible Multi-model Fallback)
# ---------------------------------------------------------------------------

class NineRouterLLMProvider(BaseLLMProvider):
    """
    Gọi qua 9router OpenAI-compatible proxy với danh sách dự phòng (Auto-Fallback).
    Tự động Fast-Failover trong 5s nếu model đầu tiên bị rate-limit hoặc nghẽn.
    """

    def __init__(self, client: openai.AsyncOpenAI, primary_model: str, router_models: List[str]) -> None:
        self.client = client
        self.primary_model = primary_model
        self.router_models = [m for m in router_models if m]

    def _resolve_candidate_models(self, preferred_model: Optional[str] = None) -> List[str]:
        """Thứ tự thử: model ưu tiên -> danh sách dự phòng; model vừa hỏng xếp cuối."""
        ordered: List[str] = []
        for m in [preferred_model or self.primary_model, *self.router_models]:
            clean = str(m or "").strip()
            if clean and clean not in ordered and not _PLACEHOLDER_RE.match(clean):
                ordered.append(clean)
        now = time.monotonic()
        healthy = [m for m in ordered if _model_down_until.get(m, 0.0) <= now]
        cooling = [m for m in ordered if _model_down_until.get(m, 0.0) > now]
        return healthy + cooling

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        models = self._resolve_candidate_models(kwargs.get("model"))
        if not models:
            raise ValueError("Chưa cấu hình model nào cho NineRouterLLMProvider.")

        stream = None
        used_model = models[0]
        last_err = None
        t0 = time.monotonic()

        for model_name in models:
            kwargs_api: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "extra_body": {"thinking": {"budget_tokens": 0}},
            }
            if tools:
                kwargs_api["tools"] = tools
                kwargs_api["tool_choice"] = "auto"

            try:
                logger.info("[NineRouterLLMProvider] Thử kết nối stream: role=%s, model=%s", brain_role, model_name)
                stream = await asyncio.wait_for(
                    self.client.chat.completions.create(**kwargs_api),
                    timeout=5.0,
                )
                used_model = model_name
                break
            except asyncio.TimeoutError:
                logger.warning("[NineRouter TIMEOUT] Model %s không phản hồi sau 5s → Fast Failover!", model_name)
                last_err = TimeoutError(f"Model {model_name} timed out after 5s")
                _mark_model_failed(model_name, "timeout 5s")
                continue
            except Exception as exc:
                logger.warning("[NineRouter FALLBACK] Model %s lỗi: %s → Thử model dự phòng...", model_name, exc)
                last_err = exc
                _mark_model_failed(model_name, exc)
                continue

        if stream is not None:
            _mark_model_ok(used_model)
        if stream is None:
            raise RuntimeError(f"Tất cả model {models} đều không phản hồi streaming: {last_err}")

        t_first_token: Optional[float] = None
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta
            token = getattr(delta, "content", "") or ""
            reasoning = getattr(delta, "reasoning", "") or getattr(delta, "reasoning_content", "") or None
            tool_calls = None
            if getattr(delta, "tool_calls", None):
                tool_calls = [
                    {
                        "index": tc.index,
                        "id": getattr(tc, "id", ""),
                        "name": getattr(getattr(tc, "function", None), "name", ""),
                        "arguments": getattr(getattr(tc, "function", None), "arguments", ""),
                    }
                    for tc in delta.tool_calls
                ]

            ttft = None
            if token and t_first_token is None:
                t_first_token = time.monotonic()
                ttft = int((t_first_token - t0) * 1000)

            yield LLMStreamChunk(
                content=token,
                tool_calls=tool_calls,
                finish_reason=getattr(choice, "finish_reason", None),
                model=used_model,
                reasoning=reasoning,
                ttft_ms=ttft,
            )

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        **kwargs: Any,
    ) -> Any:
        models = self._resolve_candidate_models(kwargs.get("model"))
        # timeout / extra_body chỉnh được cho tác vụ dài (vd. chuyên gia phân tích sâu 180s);
        # mặc định giữ hành vi cũ: 8s, tắt "thinking".
        timeout_s = float(kwargs.get("timeout", 8.0))
        extra_body = kwargs.get("extra_body", {"thinking": {"budget_tokens": 0}})
        last_err = None
        for model_name in models:
            kwargs_api: Dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": False,
            }
            if extra_body:
                kwargs_api["extra_body"] = extra_body
            if tools:
                kwargs_api["tools"] = tools
                kwargs_api["tool_choice"] = "auto"
            try:
                response = await asyncio.wait_for(
                    self.client.chat.completions.create(**kwargs_api),
                    timeout=timeout_s,
                )
                _mark_model_ok(model_name)
                return response
            except asyncio.TimeoutError:
                last_err = TimeoutError(f"Model {model_name} timed out after {timeout_s:.0f}s")
                _mark_model_failed(model_name, f"timeout {timeout_s:.0f}s")
                continue
            except Exception as exc:
                last_err = exc
                _mark_model_failed(model_name, exc)
                continue
        if not models:
            raise ValueError("Chưa cấu hình model nào cho NineRouterLLMProvider.")
        raise RuntimeError(f"Tất cả model {models} đều thất bại: {last_err}")


# ---------------------------------------------------------------------------
# Tri-Brain Dispatcher Provider (Phân luồng Bộ Não Chuyên Biệt)
# ---------------------------------------------------------------------------

class TriBrainLLMProvider(BaseLLMProvider):
    """
    Bộ điều phối Tri-Brain Provider cấp cao:
      - Quản lý DirectProvider hoặc NineRouterProvider dựa theo routing_mode ('direct', 'router', 'auto').
      - Phân luồng ý định (Supervisor Controller) sang đúng model của vai trò:
          * voice: Model giao tiếp siêu tốc, 0 tool overhead.
          * ops: Model chuyên gia hệ thống, nạp kỹ năng.
    """

    def __init__(self, direct_provider: Optional[DirectLLMProvider], router_provider: NineRouterLLMProvider) -> None:
        self.direct_provider = direct_provider
        self.router_provider = router_provider

    async def stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        brain_role: str = "voice",
        routing_mode: str = "router",
        **kwargs: Any,
    ) -> AsyncGenerator[LLMStreamChunk, None]:
        # Nếu cấu hình direct hoặc auto, thử direct trước
        if routing_mode in ("direct", "auto") and self.direct_provider:
            try:
                async for chunk in self.direct_provider.stream(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    brain_role=brain_role,
                    **kwargs,
                ):
                    yield chunk
                return
            except Exception as direct_err:
                logger.warning("[TriBrainProvider] Direct stream thất bại: %s → Fallback sang router", direct_err)
                if routing_mode == "direct":
                    raise

        # Sử dụng router provider
        async for chunk in self.router_provider.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        ):
            yield chunk

    async def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        brain_role: str = "controller",
        routing_mode: str = "router",
        **kwargs: Any,
    ) -> Any:
        if routing_mode in ("direct", "auto") and self.direct_provider:
            try:
                return await self.direct_provider.complete(
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    brain_role=brain_role,
                    **kwargs,
                )
            except Exception as direct_err:
                logger.warning("[TriBrainProvider] Direct complete thất bại: %s → Fallback sang router", direct_err)
                if routing_mode == "direct":
                    raise

        return await self.router_provider.complete(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            brain_role=brain_role,
            **kwargs,
        )


# ---------------------------------------------------------------------------
# LLM Stream Event Bus (Phát sóng Token Stream song song)
# ---------------------------------------------------------------------------

class LLMStreamEventBus:
    """
    Event Bus đa kênh cho LLM Token Stream:
      Token Stream từ LLM
           │
      ┌────┴────────────────────────┐
      ▼                             ▼
    Kênh 1: UI Stream             Kênh 2: Sentence Buffer
    (Phát text_delta ra WS)       (Gom câu chuẩn bị cho TTS)
    """

    def __init__(self) -> None:
        self._listeners: List[Callable[[str], Any]] = []

    def subscribe(self, callback: Callable[[str], Any]) -> None:
        """Đăng ký listener nhận token delta."""
        self._listeners.append(callback)

    async def publish_token(self, token: str) -> None:
        """Phát token tới tất cả listeners đồng thời."""
        if not token:
            return
        for listener in self._listeners:
            try:
                res = listener(token)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as exc:
                logger.debug("[EventBus] Lỗi listener: %s", exc)
