"""
skills/agent_orchestrator.py
============================
Phase 57: Re-export multi-agent skills for dynamic plugin discovery.
"""

from __future__ import annotations

from core.agents.agent_orchestrator import (
    delegate_to_multi_agent,
    get_enterprise_executive_summary,
    get_executive_standup_briefing,
)

__all__ = [
    "delegate_to_multi_agent",
    "get_enterprise_executive_summary",
    "get_executive_standup_briefing",
]
