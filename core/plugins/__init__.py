"""
core/plugins/__init__.py
========================
Plugins and Extension Modules for VN-MateAI.
"""

from core.plugins.computer_use_plugin import (
    tool_execute_gui_task,
    register_computer_use_tool,
)

__all__ = [
    "tool_execute_gui_task",
    "register_computer_use_tool",
]
