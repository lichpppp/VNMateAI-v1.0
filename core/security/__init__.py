"""
core/security/__init__.py
=========================
Security Package — Phase 60 Enterprise Middleware.
"""

from __future__ import annotations

from core.security.hitl_manager import HITLManager, hitl_manager, ApprovalRequest, ApprovalStatus
from core.zero_trust import (
    hitl_manager as zt_hitl_manager,
    evaluate_action_risk,
    execute_with_hitl,
    log_security_audit,
    RISK_SAFE,
    RISK_NEED_CONFIRM,
    RISK_BLOCKED,
    HITL_APPROVAL_THRESHOLD,
)

__all__ = [
    # Enhanced HITL Manager
    "HITLManager",
    "hitl_manager",
    "ApprovalRequest",
    "ApprovalStatus",
    # Zero-Trust (backward compatible)
    "evaluate_action_risk",
    "execute_with_hitl",
    "log_security_audit",
    "RISK_SAFE",
    "RISK_NEED_CONFIRM",
    "RISK_BLOCKED",
    "HITL_APPROVAL_THRESHOLD",
]