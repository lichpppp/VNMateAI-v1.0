"""
src/mateai/application/voice/barge_in_controller.py
===================================================
Bộ điều khiển ngắt lời thoại tức thì (Barge-In Interruption Controller).

Nhiệm vụ:
- Quản lý vòng đời Cancellation Token cho từng phiên thoại (`session_id`) và lượt thoại (`turn_id`).
- Khi phát hiện người dùng nói chen ngang (Barge-In), kích hoạt tín hiệu hủy tức thì (< 5ms).
- Dọn sạch hàng đợi TTS và giải phóng tác vụ LLM stream đang chạy dở dang.
"""

from __future__ import annotations

import asyncio
import time
from typing import Dict, Optional, Set
from src.mateai.domain.voice.entities import VoiceInterruption


class BargeInController:
    """Quản lý tín hiệu ngắt lời tập trung cho các phiên thoại Realtime."""

    def __init__(self):
        # session_id -> asyncio.Event
        self._cancellation_events: Dict[str, asyncio.Event] = {}
        # session_id -> current active turn_id
        self._active_turns: Dict[str, str] = {}

    def register_turn(self, session_id: str, turn_id: str) -> asyncio.Event:
        """Đăng ký lượt thoại mới kèm theo Cancellation Event."""
        cancel_event = asyncio.Event()
        self._cancellation_events[session_id] = cancel_event
        self._active_turns[session_id] = turn_id
        return cancel_event

    def get_cancellation_event(self, session_id: str) -> Optional[asyncio.Event]:
        """Lấy Event hủy hiện tại của session."""
        return self._cancellation_events.get(session_id)

    def is_cancelled(self, session_id: str, turn_id: Optional[str] = None) -> bool:
        """Kiểm tra xem lượt thoại hiện tại có bị hủy bỏ hay không."""
        event = self._cancellation_events.get(session_id)
        if event and event.is_set():
            return True
        if turn_id and self._active_turns.get(session_id) != turn_id:
            # Đã có turn mới ghi đè, turn cũ coi như bị hủy
            return True
        return False

    def trigger_barge_in(self, session_id: str, reason: str = "user_speech_detected") -> VoiceInterruption:
        """Kích hoạt ngắt lời, lập tức hủy tác vụ downstream trong < 5ms."""
        start_time = time.perf_counter()
        turn_id = self._active_turns.get(session_id, "")
        
        event = self._cancellation_events.get(session_id)
        if event:
            event.set()

        latency_ms = (time.perf_counter() - start_time) * 1000.0
        
        interruption = VoiceInterruption(
            session_id=session_id,
            target_turn_id=turn_id,
            reason=reason
        )
        return interruption

    def cleanup_session(self, session_id: str) -> None:
        """Dọn dẹp bộ nhớ khi kết thúc hoặc đóng phiên thoại."""
        self._cancellation_events.pop(session_id, None)
        self._active_turns.pop(session_id, None)


# Singleton controller
barge_in_controller = BargeInController()
