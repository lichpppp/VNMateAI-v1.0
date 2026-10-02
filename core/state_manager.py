"""
core/state_manager.py
=====================
Phase 25: State Management & Action Resumption for VN-MateAI.

Provides:
  - PENDING_ACTIONS: High-speed in-memory state dictionary for actions waiting on security approval.
  - Multi-channel key resolution (Telegram chat_id, username, Web session, or global fallback).
  - Time-To-Live (TTL) expiration (default 15 minutes) to avoid stale action execution.
  - Full CRUD operations: save, get, pop/clear, and cancel pending actions.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# TTL for pending actions: 15 minutes (900 seconds)
DEFAULT_ACTION_TTL = 900.0


class StateManager:
    """
    Thread-safe State Manager for storing pending actions that require administrator confirmation.
    """

    def __init__(self, ttl_seconds: float = DEFAULT_ACTION_TTL) -> None:
        self._pending_actions: Dict[str, Dict[str, Any]] = {}
        self._completed_actions: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._ttl_seconds = ttl_seconds
        self.load_from_audit_logs()

    def load_from_audit_logs(self) -> None:
        """Khôi phục các tác vụ PENDING_CONFIRMATION chưa được duyệt/hủy từ audit_logs, và tải các tác vụ vừa được duyệt gần nhất."""
        try:
            from core.safety_guard import security_engine
            # Mới nhất trước → đảo lại theo thời gian. 2000 dòng phủ dư cửa sổ 2 giờ.
            entries = list(reversed(security_engine.get_recent_audit_logs(limit=2000)))

            # Track resolved actions & approved actions
            pending_candidates: Dict[str, Dict[str, Any]] = {}
            recent_approved: List[Dict[str, Any]] = []
            for entry in entries:
                try:
                    action_name = entry.get("action")
                    client_id = entry.get("client_id", "master")
                    status = (entry.get("status") or "").upper()
                    sig = f"{client_id}:{action_name}"

                    if status == "PENDING_CONFIRMATION":
                        pending_candidates[sig] = entry
                    elif status == "USER_APPROVED":
                        pending_candidates.pop(sig, None)
                        recent_approved.append(entry)
                    elif status in ("USER_REJECTED", "SUCCESS", "FAILED"):
                        pending_candidates.pop(sig, None)
                except Exception:
                    continue

            # Restore uncompleted pending actions
            for sig, entry in pending_candidates.items():
                act_time = entry.get("timestamp_epoch") or time.time()
                # If within 2 hours
                if time.time() - act_time < 7200.0:
                    tool_name = entry.get("action", "")
                    details = entry.get("details", {})
                    client_id = entry.get("client_id", "master")
                    act_id = f"act_{int(act_time * 1000)}"
                    action_data = {
                        "id": act_id,
                        "user_id": "admin",
                        "norm_key": "admin",
                        "tool_name": tool_name,
                        "arguments": details,
                        "target_client": client_id,
                        "query": details.get("command", "") or f"Lệnh {tool_name}",
                        "prompt": "",
                        "description": f"Tác vụ '{tool_name}' trên [{client_id}]",
                        "timestamp": act_time,
                    }
                    self._pending_actions[act_id] = action_data
                    self._pending_actions["admin"] = action_data
                    logger.info("[StateManager] Khôi phục tác vụ pending từ log: %s (id=%s)", tool_name, act_id)

            # Restore recent approved actions so AI remembers what was approved
            for entry in reversed(recent_approved[-10:]):
                act_time = entry.get("timestamp_epoch") or time.time()
                if time.time() - act_time < 7200.0:
                    tool_name = entry.get("action", "")
                    details = entry.get("details", {})
                    client_id = entry.get("client_id", "master")
                    self._completed_actions.append({
                        "id": f"done_{int(act_time * 1000)}",
                        "tool_name": tool_name,
                        "arguments": details,
                        "target_client": client_id,
                        "query": details.get("command", "") or f"Lệnh {tool_name}",
                        "user_id": "admin",
                        "source_device": "web",
                        "result": details,
                        "reply": f"Tác vụ '{tool_name}' đã được phê duyệt và hoàn tất.",
                        "timestamp": act_time,
                    })
        except Exception as e:
            logger.warning("[StateManager] Lỗi khi nạp từ audit log: %s", e)

    def _cleanup_expired(self) -> None:
        """Remove actions that have exceeded their TTL."""
        now = time.time()
        expired_keys = [
            k for k, v in self._pending_actions.items()
            if now - v.get("timestamp", 0) > self._ttl_seconds
        ]
        for k in expired_keys:
            action = self._pending_actions.pop(k, None)
            if action:
                logger.info("[StateManager] Action '%s' for key '%s' expired and was discarded.", action.get("tool_name"), k)

    def _normalize_key(self, user_id: str) -> str:
        """Normalize user / chat identifiers for robust matching."""
        if not user_id:
            return "default"
        s = str(user_id).strip().lower()
        if s.startswith("telegram:"):
            s = s[len("telegram:"):]
        if ":" in s:
            s = s.split(":")[-1]
        if s.startswith("@"):
            s = s[1:]
        return s

    def save_pending_action(
        self,
        user_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        target_client: str = "master",
        query: str = "",
        prompt: str = "",
        description: str = "",
        chat_id: Optional[str] = None,
        source_device: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Store an action that is blocked by security policies and awaiting confirmation.
        """
        norm_key = self._normalize_key(user_id)
        act_id = f"act_{int(time.time() * 1000)}"
        action_data: Dict[str, Any] = {
            "id": act_id,
            "user_id": user_id,
            "norm_key": norm_key,
            "tool_name": tool_name,
            "arguments": arguments,
            "target_client": target_client or "master",
            "query": query,
            "prompt": prompt,
            "description": description or f"Tác vụ '{tool_name}'",
            "chat_id": chat_id,
            "source_device": source_device or user_id,
            "timestamp": time.time(),
        }

        with self._lock:
            self._cleanup_expired()
            self._pending_actions[act_id] = action_data
            self._pending_actions[norm_key] = action_data
            if norm_key != str(user_id):
                self._pending_actions[str(user_id)] = action_data
            if chat_id:
                self._pending_actions[str(chat_id)] = action_data
            if source_device:
                self._pending_actions[str(source_device)] = action_data
                norm_src = self._normalize_key(source_device)
                if norm_src:
                    self._pending_actions[norm_src] = action_data
            # Bug #7 fix: Only store under 'admin'/'default' if the actual caller IS admin.
            # Previously, every action overwrote these keys so any user saying 'ok' could
            # trigger another user's pending action.
            if norm_key in ("admin", "default", "web"):
                self._pending_actions["admin"] = action_data
                self._pending_actions["default"] = action_data

        logger.info(
            "[StateManager] Saved pending action '%s' (id=%s) for user '%s' (chat_id=%s). Total unique: %d",
            tool_name, act_id, user_id, chat_id, len(self.list_pending_actions()),
        )
        return action_data

    def get_pending_action(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Inspect pending action for a user without removing it.
        Supports lookup by action_id, chat_id, username, and global/admin fallback.
        """
        norm_key = self._normalize_key(user_id)
        with self._lock:
            self._cleanup_expired()

            # 1. Exact or action_id lookup
            if str(user_id) in self._pending_actions:
                return self._pending_actions[str(user_id)]
            if norm_key in self._pending_actions:
                return self._pending_actions[norm_key]

            # 2. Check if any key contains norm_key or vice versa
            for k, v in self._pending_actions.items():
                if norm_key and (norm_key in k or k in norm_key):
                    return v

            # 3. Admin / Global fallback: Return the most recent action
            unique_actions = self.list_pending_actions()
            if unique_actions:
                # Return most recent pending action
                return unique_actions[0]

            return None

    def get_and_clear_pending_action(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve and remove the pending action from queue for execution.
        """
        action = self.get_pending_action(user_id)
        if not action:
            return None

        act_id = action.get("id")

        with self._lock:
            # Remove all key references pointing to this action object
            keys_to_del = [k for k, v in self._pending_actions.items() if v is action or (act_id and v.get("id") == act_id)]
            for k in keys_to_del:
                self._pending_actions.pop(k, None)

        logger.info(
            "[StateManager] Retrieved and cleared pending action '%s' (id=%s) for user '%s'. Remaining: %d",
            action.get("tool_name"), act_id, user_id, len(self.list_pending_actions()),
        )
        return action

    def cancel_pending_action(self, user_id: str) -> bool:
        """Cancel and remove a pending action."""
        action = self.get_and_clear_pending_action(user_id)
        if action:
            logger.info("[StateManager] Cancelled pending action '%s' for user '%s'.", action.get("tool_name"), user_id)
            return True
        return False

    def list_pending_actions(self) -> List[Dict[str, Any]]:
        """List all currently active unique pending actions, sorted newest first."""
        with self._lock:
            self._cleanup_expired()
            seen_ids = set()
            res = []
            for act in reversed(list(self._pending_actions.values())):
                act_id = act.get("id") or str(id(act))
                if act_id not in seen_ids:
                    seen_ids.add(act_id)
                    res.append(act)
            res.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
            return res

    def record_completed_action(
        self,
        action_data: Dict[str, Any],
        result: Any,
        reply: str = "",
    ) -> None:
        """
        Record an action that was approved and executed so AI retains memory
        even after the pending entry is cleared.
        """
        now = time.time()
        record = {
            "id": action_data.get("id"),
            "tool_name": action_data.get("tool_name"),
            "arguments": action_data.get("arguments"),
            "target_client": action_data.get("target_client", "master"),
            "query": action_data.get("query", ""),
            "user_id": action_data.get("user_id", "admin"),
            "source_device": action_data.get("source_device", "web"),
            "result": result,
            "reply": reply,
            "timestamp": now,
        }
        with self._lock:
            self._completed_actions.insert(0, record)
            # Retain up to 20 completed actions
            self._completed_actions = self._completed_actions[:20]
        logger.info(
            "[StateManager] Ghi nhận tác vụ hoàn thành '%s' (query='%s', target='%s')",
            record.get("tool_name"), record.get("query"), record.get("target_client"),
        )

    def get_recent_completed_action(
        self,
        user_id: Optional[str] = None,
        max_age: float = 1800.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Return the most recently completed action within max_age seconds (default 30 mins).
        """
        now = time.time()
        norm_key = self._normalize_key(user_id) if user_id else None
        with self._lock:
            for act in self._completed_actions:
                if now - act.get("timestamp", 0) > max_age:
                    continue
                if not norm_key or norm_key in ("admin", "default", "web", "master"):
                    return act
                act_user = self._normalize_key(act.get("user_id", ""))
                act_src = self._normalize_key(act.get("source_device", ""))
                if norm_key in (act_user, act_src) or act_user in norm_key:
                    return act
            return self._completed_actions[0] if (self._completed_actions and (now - self._completed_actions[0].get("timestamp", 0) <= max_age)) else None

    def list_completed_actions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """List recently completed actions."""
        with self._lock:
            return list(self._completed_actions[:limit])


# Global singleton
state_manager = StateManager()

# Global alias functions as required by briefing
def save_pending_action(
    user_id: str,
    tool_name: str,
    arguments: Dict[str, Any],
    target_client: str = "master",
    query: str = "",
    prompt: str = "",
) -> Dict[str, Any]:
    return state_manager.save_pending_action(
        user_id=user_id,
        tool_name=tool_name,
        arguments=arguments,
        target_client=target_client,
        query=query,
        prompt=prompt,
    )

def get_and_clear_pending_action(user_id: str) -> Optional[Dict[str, Any]]:
    return state_manager.get_and_clear_pending_action(user_id)

def get_pending_action(user_id: str) -> Optional[Dict[str, Any]]:
    return state_manager.get_pending_action(user_id)

def cancel_pending_action(user_id: str) -> bool:
    return state_manager.cancel_pending_action(user_id)

def record_completed_action(action_data: Dict[str, Any], result: Any, reply: str = "") -> None:
    state_manager.record_completed_action(action_data, result, reply)

def get_recent_completed_action(user_id: Optional[str] = None, max_age: float = 1800.0) -> Optional[Dict[str, Any]]:
    return state_manager.get_recent_completed_action(user_id, max_age)

