"""
src/mateai/infrastructure/websocket/xiaozhi_protocol.py
========================================================
Giao thức truyền thông thiết bị thông minh ESP32 XiaoZhi (Versioned IoT Protocol).

Nhiệm vụ:
- Định nghĩa các cấu trúc khung truyền tin (Frames) giữa Server và vi điều khiển ESP32-S3.
- Hỗ trợ màn hình LCD/OLED: Đồng bộ biểu cảm (Emotions) và trạng thái hiển thị.
- Hỗ trợ giải mã âm thanh nhị phân cho DAC I2S (MAX98357A / ES8311).
- Xử lý sự kiện ngắt lời phần cứng (Hardware Button / Wake Word Interruption).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class XiaoZhiEmotion(str, Enum):
    SLEEPING = "sleeping"   # Trạng thái ngủ / chờ
    FOCUSED = "focused"     # Đang lắng nghe
    THINKING = "thinking"   # Đang suy luận
    HAPPY = "happy"         # Đang phát âm thanh vui vẻ
    ALERT = "alert"         # Cảnh báo an ninh / lỗi hệ thống


class XiaoZhiState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    ALERT = "alert"


class XiaoZhiProtocol:
    """Bộ mã hoá / giải mã khung truyền tin giao thức XiaoZhi IoT."""

    VERSION = "2.0-enterprise"

    @staticmethod
    def create_ui_frame(
        state: XiaoZhiState,
        emotion: XiaoZhiEmotion,
        text: Optional[str] = None
    ) -> Dict[str, Any]:
        """Tạo khung điều khiển giao diện LCD/OLED trên ESP32."""
        payload: Dict[str, Any] = {
            "type": "ui",
            "state": state.value,
            "emotion": emotion.value,
            "version": XiaoZhiProtocol.VERSION
        }
        if text:
            payload["text"] = text
        return payload

    @staticmethod
    def create_hello_ack(
        session_id: str,
        sample_rate: int = 16000,
        audio_format: str = "pcm16"
    ) -> Dict[str, Any]:
        """Tạo khung phản hồi bắt tay (Handshake ACK) khi thiết bị kết nối."""
        return {
            "type": "hello_ack",
            "session_id": session_id,
            "sample_rate": sample_rate,
            "audio_format": audio_format,
            "version": XiaoZhiProtocol.VERSION
        }

    @staticmethod
    def parse_client_frame(raw_text_or_json: str) -> Tuple[str, Dict[str, Any]]:
        """
        Phân tích cú pháp khung tin nhận từ ESP32:
        Trả về (frame_type, payload).
        Các loại frame: hello, listen, abort/interrupt, ping, audio.
        """
        try:
            data = json.loads(raw_text_or_json)
            frame_type = data.get("type", "unknown")
            return frame_type, data
        except Exception:
            return "raw_text", {"text": raw_text_or_json}
