"""
src/mateai/domain/voice/entities.py
===================================
Tầng Nghiệp vụ Thoại (Voice Domain Entities & Value Objects).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Tuyệt đối không import FastAPI, WebSocket, hay thư viện âm thanh cụ thể.
- Chỉ mô hình hóa các khái niệm nghiệp vụ: Phiên thoại, Trạng thái, Lệnh thoại, Token ngắt lời (Barge-In), Khung âm thanh.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
import uuid


class VoiceState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    ERROR = "error"


class AudioEncoding(str, Enum):
    PCM_16BIT = "pcm_16bit"
    OPUS = "opus"
    MP3 = "mp3"


@dataclass(frozen=True)
class AudioFrame:
    """Đại diện cho một khung âm thanh nhị phân trong luồng âm thanh."""
    payload: bytes
    sequence_number: int
    encoding: AudioEncoding = AudioEncoding.MP3
    sample_rate: int = 24000
    is_final: bool = False
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def byte_length(self) -> int:
        return len(self.payload)


@dataclass
class VoiceCommand:
    """Lệnh thoại nhận diện được từ tầng STT."""
    command_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    raw_transcript: str = ""
    is_final: bool = False
    confidence: float = 1.0
    detected_language: str = "vi"
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class VoiceInterruption:
    """Sự kiện người dùng ngắt lời (Barge-In)."""
    interruption_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    target_turn_id: str = ""
    reason: str = "user_speech_detected"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class VoiceSession:
    """Đại diện cho toàn bộ vòng đời của một phiên thoại giữa người dùng và VN-MateAI."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    client_id: str = "web_portal"
    state: VoiceState = VoiceState.IDLE
    current_turn_id: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, str] = field(default_factory=dict)

    def transition_to(self, new_state: VoiceState) -> None:
        """Chuyển đổi trạng thái phiên thoại an toàn."""
        self.state = new_state
        self.last_activity_at = datetime.now(timezone.utc)

    def start_new_turn(self) -> str:
        """Bắt đầu một lượt thoại mới, trả về turn_id duy nhất."""
        self.current_turn_id = str(uuid.uuid4())
        self.state = VoiceState.PROCESSING
        self.last_activity_at = datetime.now(timezone.utc)
        return self.current_turn_id

    def interrupt(self) -> VoiceInterruption:
        """Kích hoạt sự kiện ngắt lời của phiên thoại."""
        self.state = VoiceState.INTERRUPTED
        self.last_activity_at = datetime.now(timezone.utc)
        return VoiceInterruption(
            session_id=self.session_id,
            target_turn_id=self.current_turn_id or ""
        )
