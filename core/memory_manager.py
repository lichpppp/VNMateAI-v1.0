"""
core/memory_manager.py
======================
Phase 34: Conversational Memory & Sliding Window Context Manager.

Features:
- Global CHAT_HISTORY dictionary storing message history keyed by session/user_id.
- Sliding Window mechanism keeping the last 10-15 messages (configurable, default 14).
- Automatic pruning of older messages to conserve token budget and prevent context drift.
- Thread-safe read/write operations with mutex locking.
- Session timestamp tracking for idle timeout cleanup.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.memory_manager")

# Default sliding window capacity: 14 messages (approx. 7 user-assistant conversational turns)
DEFAULT_MAX_WINDOW_SIZE: int = 14

# Global chat history storage as requested in Phase 34
# Structure: {"user_id": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
CHAT_HISTORY: Dict[str, List[Dict[str, str]]] = {}

# Session last active timestamp map: {"user_id": float_timestamp}
_SESSION_TIMESTAMPS: Dict[str, float] = {}

# Thread safety lock
_lock = threading.Lock()


class MemoryManager:
    """
    Manages multi-turn conversation memory with a sliding window for all sessions
    (Web Portal, REST API, Standby HUD, Telegram, LAN Client Agents).
    """

    def __init__(self, default_window_size: int = DEFAULT_MAX_WINDOW_SIZE) -> None:
        self.default_window_size = default_window_size

    def get_history(
        self,
        session_id: str = "default",
        max_messages: Optional[int] = None,
    ) -> List[Dict[str, str]]:
        """
        Retrieve sliding window conversation history for the given session.
        Returns a shallow copy of message dicts ready for 9router / OpenAI 'messages'.
        """
        limit = max_messages or self.default_window_size
        with _lock:
            history = CHAT_HISTORY.get(session_id, [])
            return [dict(msg) for msg in history[-limit:]]

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        max_messages: Optional[int] = None,
    ) -> None:
        """
        Append a single message (user, assistant, or system) to the session history.
        Enforces the sliding window limit immediately.
        """
        if not content:
            return

        limit = max_messages or self.default_window_size
        with _lock:
            if session_id not in CHAT_HISTORY:
                CHAT_HISTORY[session_id] = []

            CHAT_HISTORY[session_id].append({
                "role": role,
                "content": content.strip(),
            })
            _SESSION_TIMESTAMPS[session_id] = time.time()

            # Sliding Window eviction: keep only the latest `limit` messages
            if len(CHAT_HISTORY[session_id]) > limit:
                evicted_count = len(CHAT_HISTORY[session_id]) - limit
                CHAT_HISTORY[session_id] = CHAT_HISTORY[session_id][-limit:]
                logger.debug(
                    "[MemoryManager] Session '%s' evicted %d old message(s). Window size: %d",
                    session_id,
                    evicted_count,
                    len(CHAT_HISTORY[session_id]),
                )

    def add_turn(
        self,
        session_id: str,
        user_content: str,
        assistant_content: str,
        max_messages: Optional[int] = None,
    ) -> None:
        """Convenience method to record a complete user → assistant conversational turn."""
        if user_content:
            self.add_message(session_id, "user", user_content, max_messages)
        if assistant_content:
            self.add_message(session_id, "assistant", assistant_content, max_messages)

    def clear_history(self, session_id: str) -> None:
        """Clear memory for a given session (e.g. on user reset or goodbye)."""
        with _lock:
            if session_id in CHAT_HISTORY:
                del CHAT_HISTORY[session_id]
            if session_id in _SESSION_TIMESTAMPS:
                del _SESSION_TIMESTAMPS[session_id]
            logger.info("[MemoryManager] Cleared conversation history for session '%s'.", session_id)

    def get_session_count(self) -> int:
        """Return total active conversation sessions in memory."""
        with _lock:
            return len(CHAT_HISTORY)

    def get_stats(self) -> Dict[str, Any]:
        """Return memory diagnostics for telemetry and debugging."""
        with _lock:
            total_msgs = sum(len(m) for m in CHAT_HISTORY.values())
            return {
                "active_sessions": len(CHAT_HISTORY),
                "total_messages_stored": total_msgs,
                "default_window_size": self.default_window_size,
                "sessions": {k: len(v) for k, v in CHAT_HISTORY.items()},
            }


# Global singleton instance
memory_manager = MemoryManager()


# Module-level convenience functions mirroring the singleton
def get_history(session_id: str = "default", max_messages: Optional[int] = None) -> List[Dict[str, str]]:
    return memory_manager.get_history(session_id, max_messages)


def add_message(session_id: str, role: str, content: str, max_messages: Optional[int] = None) -> None:
    memory_manager.add_message(session_id, role, content, max_messages)


def add_turn(session_id: str, user_content: str, assistant_content: str, max_messages: Optional[int] = None) -> None:
    memory_manager.add_turn(session_id, user_content, assistant_content, max_messages)


def clear_history(session_id: str) -> None:
    memory_manager.clear_history(session_id)
