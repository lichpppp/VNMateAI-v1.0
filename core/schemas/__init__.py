"""
core/schemas/__init__.py
========================
Data schemas package for VN-MateAI Enterprise RPA.
"""
from core.schemas.computer_use_schema import (
    ActionType,
    GUIActionPayload,
    ActionResult,
    GUITaskRequest,
)

__all__ = [
    "ActionType",
    "GUIActionPayload",
    "ActionResult",
    "GUITaskRequest",
]
