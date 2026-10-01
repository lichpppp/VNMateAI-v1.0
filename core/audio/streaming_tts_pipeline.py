"""
core/audio/streaming_tts_pipeline.py
=====================================
Streaming TTS Pipeline — Full-Duplex Voice Latency Optimisation.

Mục tiêu: TTFA (Time-To-First-Audio) < 600ms.

Kiến trúc:
  LLM tokens → SentenceStreamer → TTSStreamEngine → WebSocket Binary Frame
  Tool call  → AcousticACK (cache 0ms) → immediate audio frame

Các lớp/hàm chính:
  - SentenceBoundaryStreamer: tương thích ngược với code cũ (api_voice_stream.py)
  - get_acoustic_ack_audio():  lấy câu ACK từ cache hoặc tổng hợp nhanh
  - warmup_acoustic_ack_cache(): pre-warm khi server khởi động
  - edge_tts_stream_audio():   stream audio bytes từ edge-tts (cho HUD binary)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncGenerator, List, Optional, Callable, Awaitable

# Re-export từ module mới để API cũ vẫn hoạt động
from core.audio.sentence_streamer import (
    SentenceStreamer,
    sanitise_for_tts as _sanitise_for_tts,
    MIN_SENTENCE_CHARS as _MIN_SENTENCE_LEN,
)
from core.audio.tts_stream_engine import TTSStreamEngine, get_tts_engine, _get_tts_voice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Acoustic ACK phrases (Context-Aware Catalog Re-export)
# ---------------------------------------------------------------------------

from core.audio.acoustic_ack_catalog import (
    ACOUSTIC_ACK_CATALOG,
    ALL_ACOUSTIC_ACK_PHRASES,
    select_acoustic_ack,
    get_all_ack_phrases,
)

ACOUSTIC_ACK_PHRASES: List[str] = ACOUSTIC_ACK_CATALOG["GENERAL_GENERIC"]
_ack_phrase_index: int = 0


# ---------------------------------------------------------------------------
# Backward-compatible SentenceBoundaryStreamer
# ---------------------------------------------------------------------------

class SentenceBoundaryStreamer:
    """
    Wrapper tương thích ngược với api_voice_stream.py.

    Sử dụng StreamingTTSWorkerPipeline để tổng hợp giọng nói gối đầu đa luồng
    với bảo đảm tuyệt đối thứ tự âm thanh (In-Order Guaranteed).
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        on_sentence_ready: Optional[Callable[[str], Awaitable[None]]] = None,
        num_workers: int = 2,
    ) -> None:
        self._voice = voice
        self._on_sentence_ready = on_sentence_ready
        self._num_workers = num_workers
        self._sentence_streamer = SentenceStreamer()
        self._tts_engine = TTSStreamEngine(voice=voice)
        self._start_time: float = time.monotonic()
        self._first_audio_time: Optional[float] = None
        self._sentences_streamed: int = 0
        self._total_audio_bytes: int = 0

    async def stream(
        self,
        token_generator: AsyncGenerator[str, None],
        voice: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Nhận token generator, gom câu qua SentenceBuffer, tổng hợp gối đầu đa luồng,
        yield audio bytes theo đúng thứ tự câu.
        """
        from core.audio.tts_queue_pipeline import StreamingTTSWorkerPipeline

        pipeline = StreamingTTSWorkerPipeline(
            voice=voice or self._voice,
            num_workers=self._num_workers,
        )
        pipeline.start()

        async def _produce() -> None:
            seq = 0
            try:
                async for sentence in self._sentence_streamer.stream(token_generator):
                    seq += 1
                    if self._on_sentence_ready:
                        try:
                            await self._on_sentence_ready(sentence)
                        except Exception:
                            pass
                    await pipeline.push_sentence(seq, sentence, request_id="stream")
                await pipeline.mark_complete(seq)
            except Exception as exc:
                logger.warning("[SentenceBoundaryStreamer] Producer error: %s", exc)
                await pipeline.mark_complete(seq)

        producer_task = asyncio.create_task(_produce())
        try:
            async for audio_item in pipeline.iterate_audio_results():
                if audio_item.audio_bytes:
                    if self._first_audio_time is None:
                        self._first_audio_time = time.monotonic()
                        ttfa = (self._first_audio_time - self._start_time) * 1000
                        logger.info(
                            "[SentenceBoundaryStreamer] TTFA = %.0fms (câu #%d: '%s')",
                            ttfa, self._sentences_streamed + 1, audio_item.text[:40]
                        )
                    self._sentences_streamed += 1
                    self._total_audio_bytes += len(audio_item.audio_bytes)
                    yield audio_item.audio_bytes
        finally:
            if not producer_task.done():
                producer_task.cancel()
            pipeline.cancel()

    def get_ttfa_ms(self) -> Optional[float]:
        """Thời gian từ init đến chunk audio đầu tiên (ms)."""
        if self._first_audio_time is None:
            return None
        return (self._first_audio_time - self._start_time) * 1000


# ---------------------------------------------------------------------------
# edge_tts_stream_audio — backward compat cho các nơi import trực tiếp
# ---------------------------------------------------------------------------

async def edge_tts_stream_audio(
    text: str,
    voice: Optional[str] = None,
) -> AsyncGenerator[bytes, None]:
    """
    Yield raw audio bytes từ edge-tts (có cache + fallback).
    Backward-compatible function — dùng TTSStreamEngine bên trong.
    """
    engine = TTSStreamEngine(voice=voice)
    async for chunk in engine.stream(text, voice=voice):
        yield chunk


# ---------------------------------------------------------------------------
# Acoustic ACK helpers (Phase 6: Context-Aware & Pre-warmed)
# ---------------------------------------------------------------------------

async def get_acoustic_ack_audio(
    phrase: Optional[str] = None,
    query: Optional[str] = None,
    domain: Optional[str] = None,
) -> Optional[bytes]:
    """
    Trả về bytes âm thanh câu đệm phản xạ (từ RAM cache nếu có — 0ms).
    - Nếu có phrase cụ thể: sử dụng phrase đó.
    - Nếu có query: dùng select_acoustic_ack(query) để chọn câu đệm phù hợp ngữ cảnh.
    - Mặc định: luân phiên trong danh mục GENERAL_GENERIC.
    """
    from core.audio_cache import get_cached_audio_bytes

    if phrase is None:
        if query:
            phrase = select_acoustic_ack(query, domain=domain)
        else:
            global _ack_phrase_index
            phrase = ACOUSTIC_ACK_PHRASES[_ack_phrase_index % len(ACOUSTIC_ACK_PHRASES)]
            _ack_phrase_index += 1

    # 1. Tra cứu RAM Cache tức thì (0ms)
    cached_audio = get_cached_audio_bytes(phrase)
    if cached_audio:
        logger.debug("[AcousticACK] Cache HIT (0ms): '%s'", phrase)
        return cached_audio

    # 2. Tổng hợp dự phòng qua TTSStreamEngine nếu chưa cache
    engine = get_tts_engine()
    audio = await engine.synthesise(phrase)

    if audio:
        logger.info("[AcousticACK] Audio sẵn sàng (%d bytes): '%s'", len(audio), phrase)
    return audio


async def get_acoustic_ack_for_query(
    query: str,
    domain: Optional[str] = None,
) -> Tuple[str, Optional[bytes]]:
    """
    Lựa chọn câu đệm theo ngữ cảnh và trả về cả (phrase_text, audio_bytes).
    """
    phrase = select_acoustic_ack(query, domain=domain)
    audio = await get_acoustic_ack_audio(phrase=phrase)
    return phrase, audio


async def warmup_acoustic_ack_cache() -> None:
    """
    Pre-warm TTS cache cho TOÀN BỘ câu ACK theo danh mục ngữ cảnh (Phase 6).
    Đảm bảo 100% câu đệm sẵn sàng trong RAM Cache (0ms TTFA) khi có tác vụ kỹ thuật.
    """
    from core.audio_cache import get_cached_audio_bytes

    total_phrases = len(ALL_ACOUSTIC_ACK_PHRASES)
    logger.info(
        "[AcousticACK] Pre-warm TTS cache cho %d câu đệm ngữ cảnh (Phase 6)...",
        total_phrases,
    )
    engine = get_tts_engine()
    ok_count = 0

    for phrase in ALL_ACOUSTIC_ACK_PHRASES:
        try:
            cached = get_cached_audio_bytes(phrase)
            if cached and len(cached) > 100:
                ok_count += 1
                logger.debug("[AcousticACK] Đã có trong cache (0ms): '%s'", phrase)
            else:
                audio = await engine.synthesise(phrase)
                if audio and len(audio) > 100:
                    ok_count += 1
                    logger.info("[AcousticACK] Đã tổng hợp & cache: '%s'", phrase)
                await asyncio.sleep(0.1)
        except Exception as exc:
            logger.debug("[AcousticACK] Lỗi pre-warm '%s': %s", phrase, exc)

    logger.info(
        "[AcousticACK] Pre-warm hoàn thành: %d/%d câu đệm đã sẵn sàng trong RAM/Disk Cache (0ms TTFA).",
        ok_count, total_phrases,
    )
