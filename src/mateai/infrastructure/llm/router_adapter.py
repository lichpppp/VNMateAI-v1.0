"""
src/mateai/infrastructure/llm/router_adapter.py
===============================================
Adapter kết nối với 9Router Gateway cục bộ (Local Model Router).
"""

from __future__ import annotations

from typing import Optional
import httpx

from mateai.infrastructure.llm.openai_compatible_adapter import OpenAICompatibleAdapter


class NineRouterAdapter(OpenAICompatibleAdapter):
    """Adapter kết nối cổng 9Router phục vụ định tuyến đa model linh hoạt."""

    def __init__(
        self,
        base_url: str = "http://localhost:20128/v1",
        api_key: str = "sk-dummy",
        model: str = "ag/gemini-3.6-flash-high",
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
