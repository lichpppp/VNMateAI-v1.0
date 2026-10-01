"""
src/mateai/infrastructure/llm/groq_adapter.py
=============================================
Adapter chuyên dụng cho Groq Cloud (Ultra-Fast Inference / Llama 3).
"""

from __future__ import annotations

from typing import Optional
import httpx

from src.mateai.infrastructure.llm.openai_compatible_adapter import OpenAICompatibleAdapter


class GroqAdapter(OpenAICompatibleAdapter):
    """Adapter kết nối trực tiếp đến Groq API với độ trễ phản hồi cực thấp."""

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.3-70b-versatile",
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 15.0,
        http_client: Optional[httpx.AsyncClient] = None
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_model=model,
            timeout_seconds=timeout_seconds,
            http_client=http_client
        )
