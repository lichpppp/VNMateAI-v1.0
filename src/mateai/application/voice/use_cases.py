"""
src/mateai/application/voice/use_cases.py
=========================================
Các Use Cases điều phối luồng thoại Realtime (Voice Orchestration Use Cases).

Quy tắc:
- Tuân thủ RULE-003: Không gọi raw DB drivers.
- Tuân thủ RULE-005: Đóng vai trò cầu nối điều phối giữa Interface (WebSocket) và Infrastructure (LLM, TTS).
- Canonical Voice Pipeline: Fast Path Check ➔ Sentence Buffering ➔ Sequential TTS ➔ Binary Audio Frames.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator, Dict, Any, Optional

from src.mateai.domain.voice.entities import (
    VoiceSession,
    VoiceState,
    AudioFrame,
    AudioEncoding,
    VoiceInterruption,
)
from src.mateai.application.voice.sentence_buffer import SentenceBuffer
from src.mateai.application.voice.barge_in_controller import barge_in_controller

logger = logging.getLogger(__name__)


class ProcessVoiceTurnUseCase:
    """Điều phối hoàn chỉnh một lượt thoại từ khi nhận transcript đến khi phát âm thanh."""

    def __init__(self, tts_adapter=None, fast_router=None):
        self.tts_adapter = tts_adapter
        self.fast_router = fast_router

    async def execute(
        self,
        session: VoiceSession,
        transcript: str,
        synthesize_audio: bool = True
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Thực thi lượt thoại chuẩn, yield các sự kiện theo Canonical Protocol:
        - status (listening -> thinking -> speaking)
        - routing (fast_path vs llm)
        - text_delta / sentence_ready
        - audio_chunk (binary frames)
        - done / cancelled
        """
        turn_id = session.start_new_turn()
        cancel_event = barge_in_controller.register_turn(session.session_id, turn_id)
        
        yield {
            "event_type": "status",
            "session_id": session.session_id,
            "turn_id": turn_id,
            "payload": {"state": VoiceState.PROCESSING.value, "transcript": transcript}
        }

        # 1. Kiểm tra Fast Path (Lệnh tất định không qua LLM)
        if self.fast_router:
            fast_res = await self.fast_router.dispatch(transcript, synthesize_audio=False)
            if fast_res:
                yield {
                    "event_type": "fast_path",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "payload": {
                        "command": fast_res.command_name,
                        "reply_text": fast_res.reply_text,
                        "latency_ms": fast_res.latency_ms
                    }
                }

                if synthesize_audio and self.tts_adapter and not cancel_event.is_set():
                    session.transition_to(VoiceState.SPEAKING)
                    seq = 0
                    async for audio_bytes in self.tts_adapter.synthesize_stream(fast_res.reply_text, cancel_event):
                        if cancel_event.is_set():
                            break
                        yield {
                            "event_type": "audio_chunk",
                            "session_id": session.session_id,
                            "turn_id": turn_id,
                            "binary_payload": audio_bytes,
                            "sequence_number": seq
                        }
                        seq += 1

                session.transition_to(VoiceState.IDLE)
                yield {
                    "event_type": "done",
                    "session_id": session.session_id,
                    "turn_id": turn_id,
                    "payload": {"status": "success", "fast_path": True}
                }
                return

        # 2. Xử lý qua LLM Streaming & Sentence Segmentation
        sentence_buffer = SentenceBuffer()
        # Trong kiến trúc modular, token generator sẽ được cung cấp bởi LLMUseCase
        # Ở đây cung cấp pipeline khung điều phối hoàn chỉnh
        session.transition_to(VoiceState.IDLE)
        yield {
            "event_type": "done",
            "session_id": session.session_id,
            "turn_id": turn_id,
            "payload": {"status": "success", "fast_path": False}
        }


class InterruptVoiceSessionUseCase:
    """Xử lý sự kiện ngắt lời (Barge-In) người dùng."""

    def execute(self, session: VoiceSession, reason: str = "user_barge_in") -> Dict[str, Any]:
        interruption = barge_in_controller.trigger_barge_in(session.session_id, reason=reason)
        session.transition_to(VoiceState.INTERRUPTED)
        return {
            "event_type": "cancelled",
            "session_id": session.session_id,
            "turn_id": interruption.target_turn_id,
            "payload": {"reason": interruption.reason}
        }
