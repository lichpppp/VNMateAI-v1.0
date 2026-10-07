# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
client_agent/core/plugin_manager.py
===================================
Lightweight, Zero-LLM Plugin Loader for VN-MateAI Client Agent.

Responsibilities:
  - Scan the client-side `skills/` directory for Python automation skill modules.
  - Dynamically import / reload modules using importlib.
  - Expose the @export_skill decorator for skill function self-registration.
  - Execute skills locally on the client machine with structured error handling.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import logging
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("client_agent.plugin_manager")

# Resolve client agent directory
_CLIENT_ROOT = Path(__file__).resolve().parent.parent
SKILLS_DIR: Path = _CLIENT_ROOT / "skills"

# Global staging for decorator
_DECORATOR_STAGING: Dict[Callable, Dict[str, Any]] = {}


def export_skill(
    name: str,
    description: str,
    parameters_schema: Dict[str, Any],
) -> Callable:
    """Decorator marking a function as an exportable automation skill."""
    def decorator(func: Callable) -> Callable:
        _DECORATOR_STAGING[func] = {
            "name": name,
            "description": description,
            "parameters": parameters_schema,
        }
        func.__skill_meta__ = {  # type: ignore[attr-defined]
            "name": name,
            "description": description,
            "parameters": parameters_schema,
        }
        return func
    return decorator


class ClientPluginManager:
    """Manages skill discovery, loading, and execution on the client agent."""

    def __init__(self, skills_dir: Optional[Path] = None) -> None:
        self._skills_dir: Path = skills_dir or SKILLS_DIR
        self._registry: Dict[str, Dict[str, Any]] = {}
        self._lock: threading.RLock = threading.RLock()

    def load_plugins(self) -> int:
        """Scan skills directory and load all exported skill functions."""
        with self._lock:
            self._skills_dir.mkdir(parents=True, exist_ok=True)
            self._registry.clear()
            _DECORATOR_STAGING.clear()

            # Ensure client_agent root is in sys.path
            client_root_str = str(_CLIENT_ROOT)
            if client_root_str not in sys.path:
                sys.path.insert(0, client_root_str)

            loaded_count = 0
            for py_file in sorted(self._skills_dir.glob("*.py")):
                if py_file.name.startswith("__"):
                    continue

                module_name = f"skills.{py_file.stem}"
                try:
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec is None or spec.loader is None:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = mod
                    spec.loader.exec_module(mod)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.error("Lỗi nạp module skill '%s': %s", py_file.name, exc)
                    continue

                # Inspect functions in module
                for attr_name in dir(mod):
                    if attr_name.startswith("_"):
                        continue
                    try:
                        obj = getattr(mod, attr_name)
                    except AttributeError:
                        continue

                    if not callable(obj):
                        continue

                    meta = getattr(obj, "__skill_meta__", None) or _DECORATOR_STAGING.get(obj)
                    if meta:
                        skill_name = meta["name"]
                        self._registry[skill_name] = {
                            "name": skill_name,
                            "func": obj,
                            "module": module_name,
                            "meta": meta,
                        }
                        loaded_count += 1

            logger.info("ClientPluginManager: Đã nạp thành công %d kỹ năng trên máy con.", loaded_count)
            return loaded_count

    def get_skill_names(self) -> List[str]:
        with self._lock:
            return sorted(self._registry.keys())

    def get_skill_count(self) -> int:
        with self._lock:
            return len(self._registry)

    def execute_skill(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute a loaded skill by name."""
        with self._lock:
            entry = self._registry.get(name)

        if not entry:
            return {
                "status": "error",
                "skill": name,
                "error": f"Kỹ năng '{name}' không tồn tại trên máy con này.",
            }

        func: Callable = entry["func"]
        args: Dict[str, Any] = arguments or {}

        try:
            # Bind arguments cleanly filtering extra injected metadata
            sig = inspect.signature(func)
            has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            call_args = args if has_var_kw else {k: v for k, v in args.items() if k in sig.parameters}
            bound = sig.bind_partial(**call_args)
            bound.apply_defaults()
            result = func(*bound.args, **bound.kwargs)

            # Normalise output
            if isinstance(result, dict):
                return {"status": "success", "skill": name, **result}
            return {"status": "success", "skill": name, "result": result}
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Lỗi thực thi kỹ năng '%s': %s\n%s", name, exc, traceback.format_exc())
            return {
                "status": "error",
                "skill": name,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }


client_plugin_manager = ClientPluginManager()
