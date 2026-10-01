"""
src/mateai/infrastructure/tts/edge_tts_adapter.py
==================================================
Adapter tổng hợp giọng nói Microsoft Edge-TTS (Streaming TTS Adapter).

Đặc điểm:
- Giọng đọc tiêu chuẩn: vi-VN-HoaiMyNeural (tốc độ +50% chuẩn tiếng Việt nhanh mượt).
- Trả về luồng byte nhị phân (Binary chunks) trực tiếp cho WebSocket.
- Kiểm tra tín hiệu huỷ (Barge-In Cancellation Token) liên tục trong từng frame để dừng ngay nếu người dùng ngắt lời.
- KHÔNG chạy đua (race) nhiều engine cùng lúc để triệt tiêu hoàn toàn lỗi 2 giọng nói đè lên nhau.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import AsyncGenerator, Optional
import edge_tts

from src.mateai.config.settings import settings

logger = logging.getLogger(__name__)


class EdgeTTSAdapter:
    """Adapter kỹ thuật giao tiếp với dịch vụ Microsoft Edge-TTS."""

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
        volume: Optional[str] = None
    ):
        self.voice = voice or settings.voice.tts_voice
        self.rate = rate or settings.voice.tts_rate
        self.volume = volume or settings.voice.tts_volume

    async def synthesize_stream(
        self,
        text: str,
        cancel_event: Optional[asyncio.Event] = None
    ) -> AsyncGenerator[bytes, None]:
        """
        Tổng hợp văn bản thành luồng âm thanh nhị phân MP3.
        Ngừng phát ngay lập tức nếu cancel_event được kích hoạt.
        """
        if not text or not text.strip():
            return

        if cancel_event and cancel_event.is_set():
            return

        try:
            communicate = edge_tts.Communicate(
                text=text.strip(),
                voice=self.voice,
                rate=self.rate,
                volume=self.volume
            )

            async for chunk in communicate.stream():
                if cancel_event and cancel_event.is_set():
                    logger.debug("[EdgeTTSAdapter] Bị huỷ do người dùng ngắt lời (Barge-In).")
                    break

                if chunk["type"] == "audio":
                    yield chunk["data"]

        except asyncio.CancelledError:
            logger.debug("[EdgeTTSAdapter] Tác vụ asyncio bị huỷ an toàn.")
            raise
        except Exception as e:
            logger.error(f"[EdgeTTSAdapter] Lỗi tổng hợp Edge-TTS cho câu '{text[:30]}...': {e}")


# Singleton adapter
edge_tts_adapter = EdgeTTSAdapter()
