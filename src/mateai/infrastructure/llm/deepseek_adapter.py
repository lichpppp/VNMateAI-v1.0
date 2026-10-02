"""
src/mateai/infrastructure/llm/deepseek_adapter.py
=================================================
Adapter chuyên dụng cho mô hình DeepSeek (DeepSeek Chat / Reasoner).
"""

from __future__ import annotations

from typing import Optional
import httpx

from mateai.infrastructure.llm.openai_compatible_adapter import OpenAICompatibleAdapter


class DeepSeekAdapter(OpenAICompatibleAdapter):
    """Adapter kết nối trực tiếp đến DeepSeek API."""

    def __init__(
        self,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com/v1",
        timeout_seconds: float = 30.0,
        http_client: Optional[httpx.AsyncClient] = None
    ):
        super().__init__(
            base_url=base_url,
            api_key=api_key,
            default_model=model,
            timeout_seconds=timeout_seconds,
            http_client=http_client
        )
