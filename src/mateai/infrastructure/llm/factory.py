"""
src/mateai/infrastructure/llm/factory.py
========================================
Factory khởi tạo LLM Provider Adapter dựa trên cấu hình hệ thống (LLM Provider Factory).
"""

from __future__ import annotations

from typing import Optional
from src.mateai.config.settings import settings
from src.mateai.infrastructure.llm.provider_interface import LLMProvider
from src.mateai.infrastructure.llm.openai_compatible_adapter import OpenAICompatibleAdapter
from src.mateai.infrastructure.llm.deepseek_adapter import DeepSeekAdapter
from src.mateai.infrastructure.llm.groq_adapter import GroqAdapter
from src.mateai.infrastructure.llm.router_adapter import NineRouterAdapter


def get_llm_provider(
    provider_name: Optional[str] = None,
    model_name: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None
) -> LLMProvider:
    """Tạo hoặc trả về instance LLMProvider phù hợp với cấu hình."""
    cfg = settings.llm
    p_name = (provider_name or cfg.routing_mode).lower()
    
    if p_name == "deepseek" or "deepseek" in (model_name or cfg.model_name).lower():
        return DeepSeekAdapter(
            api_key=api_key or cfg.direct_api_key or cfg.api_key,
            model=model_name or cfg.direct_model or "deepseek-chat",
            base_url=base_url or cfg.direct_url or "https://api.deepseek.com/v1"
        )

    if p_name == "groq" or (base_url and "groq" in base_url.lower()):
        return GroqAdapter(
            api_key=api_key or cfg.api_key,
            model=model_name or "llama-3.3-70b-versatile",
            base_url=base_url or "https://api.groq.com/openai/v1"
        )

    if p_name == "router":
        return NineRouterAdapter(
            base_url=base_url or cfg.base_url,
            api_key=api_key or cfg.api_key,
            model=model_name or cfg.model_name
        )

    # Fallback to general OpenAI compatible
    return OpenAICompatibleAdapter(
        base_url=base_url or cfg.base_url,
        api_key=api_key or cfg.api_key,
        default_model=model_name or cfg.model_name
    )
