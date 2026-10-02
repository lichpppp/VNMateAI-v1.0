"""
src/mateai/application/devices/xiaozhi_service.py
=================================================
Dịch vụ quản lý thiết bị thông minh ESP32 XiaoZhi (XiaoZhi Device Service).

Nhiệm vụ:
- Quản lý vòng đời phiên kết nối của các thiết bị ESP32-S3 IoT.
- Đồng bộ mã ghép nối (Pairing Code) 6 chữ số giữa phần cứng và hệ thống.
- Chuyển tiếp luồng thoại và sự kiện ngắt lời (Hardware Barge-In) vào Voice Pipeline.
- Tuân thủ RULE-003 & RULE-005: Độc lập với framework mạng và giao thức truyền tải thô.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from mateai.domain.devices.entities import Device, DeviceType, DeviceStatus
from mateai.domain.voice.entities import VoiceSession, VoiceState
from mateai.infrastructure.websocket.xiaozhi_protocol import (
    XiaoZhiProtocol,
    XiaoZhiState,
    XiaoZhiEmotion,
)
from mateai.application.voice.barge_in_controller import barge_in_controller

logger = logging.getLogger(__name__)


class XiaoZhiDeviceService:
    """Dịch vụ điều phối thiết bị thông minh XiaoZhi."""

    def __init__(self):
        # pairing_code -> device_id
        self._pairing_codes: Dict[str, str] = {}
        # device_id -> active VoiceSession
        self._device_sessions: Dict[str, VoiceSession] = {}

    def handle_hello(
        self,
        device_id: str,
        pairing_code: Optional[str] = None,
        sample_rate: int = 16000
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Xử lý khi thiết bị ESP32 khởi động và gửi gói 'hello':
        Trả về (ack_frame, ui_frame).
        """
        if pairing_code:
            self._pairing_codes[pairing_code] = device_id

        session = VoiceSession(client_id=f"xiaozhi_{device_id}")
        self._device_sessions[device_id] = session

        ack = XiaoZhiProtocol.create_hello_ack(
            session_id=session.session_id,
            sample_rate=sample_rate,
            audio_format="pcm16"
        )
        ui = XiaoZhiProtocol.create_ui_frame(
            state=XiaoZhiState.IDLE,
            emotion=XiaoZhiEmotion.SLEEPING,
            text="VN-MateAI Ready"
        )
        return ack, ui

    def handle_listen(self, device_id: str) -> Dict[str, Any]:
        """Xử lý khi thiết bị bắt đầu thu âm giọng nói người dùng."""
        session = self._device_sessions.get(device_id)
        if session:
            session.transition_to(VoiceState.LISTENING)

        return XiaoZhiProtocol.create_ui_frame(
            state=XiaoZhiState.LISTENING,
            emotion=XiaoZhiEmotion.FOCUSED,
            text="Đang lắng nghe..."
        )

    def handle_hardware_barge_in(self, device_id: str) -> Dict[str, Any]:
        """
        Xử lý khi người dùng nhấn nút ngắt lời hoặc phát hiện giọng nói ngắt lời trên ESP32:
        Hủy ngay lập tức các tác vụ LLM/TTS đang phát và chuyển màn hình sang Focused.
        """
        session = self._device_sessions.get(device_id)
        if session:
            barge_in_controller.trigger_barge_in(session.session_id, reason="esp32_hardware_barge_in")
            session.transition_to(VoiceState.INTERRUPTED)

        return XiaoZhiProtocol.create_ui_frame(
            state=XiaoZhiState.LISTENING,
            emotion=XiaoZhiEmotion.FOCUSED,
            text="Dạ, em nghe đây ạ!"
        )

    def get_device_by_pairing_code(self, pairing_code: str) -> Optional[str]:
        """Tìm kiếm device_id thông qua mã 6 số."""
        return self._pairing_codes.get(pairing_code)

    def disconnect_device(self, device_id: str) -> None:
        """Dọn dẹp session khi thiết bị ngắt kết nối."""
        session = self._device_sessions.pop(device_id, None)
        if session:
            barge_in_controller.cleanup_session(session.session_id)


# Singleton device service
xiaozhi_device_service = XiaoZhiDeviceService()
