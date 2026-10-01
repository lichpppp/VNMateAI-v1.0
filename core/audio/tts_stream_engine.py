"""
core/audio/tts_stream_engine.py
================================
TTS Stream Engine đa nguồn — Race để đảm bảo âm thanh luôn có trong < 600ms.

Thứ tự ưu tiên:
  1. Cache RAM / Disk (0ms) — cho câu ACK và câu thường xuyên lặp lại
  2. Microsoft Edge-TTS stream (150–300ms) — chất lượng cao, giọng Hoài My
  3. ElevenLabs WebSocket Stream (nếu config) — giọng tự nhiên nhất
  4. gTTS (Google Translate TTS) — fallback cuối, luôn hoạt động (~3s)

Race strategy: Edge-TTS và gTTS chạy song song, ai về trước thắng.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_tts_voice() -> str:
    try:
        from core.config_loader import settings
        v = (
            getattr(settings, "TTS_VOICE", None)
            or getattr(settings, "tts_voice", None)
        )
        if not v and hasattr(settings, "audio"):
            audio = settings.audio
            v = (
                (audio.get("tts_voice") or audio.get("TTS_VOICE"))
                if isinstance(audio, dict)
                else (getattr(audio, "tts_voice", None) or getattr(audio, "TTS_VOICE", None))
            )
        return v or "vi-VN-HoaiMyNeural"
    except Exception:
        return "vi-VN-HoaiMyNeural"


def _get_tts_rate() -> str:
    try:
        from core.config_loader import settings
        rate = getattr(settings, "TTS_RATE", None) or getattr(settings, "tts_rate", None)
        if rate is not None:
            return f"+{int(rate)}%" if str(rate).lstrip("+-").isdigit() else str(rate)
        if hasattr(settings, "audio"):
            audio = settings.audio
            rate = (
                (audio.get("speech_rate") or audio.get("TTS_RATE"))
                if isinstance(audio, dict)
                else (getattr(audio, "speech_rate", None) or getattr(audio, "TTS_RATE", None))
            )
            if rate is not None:
                return f"+{int(rate)}%" if str(rate).lstrip("+-").isdigit() else str(rate)
    except Exception:
        pass
    return "+50%"


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_get(text: str) -> Optional[bytes]:
    try:
        from core.audio_cache import get_cached_audio_bytes
        return get_cached_audio_bytes(text)
    except Exception:
        return None


def _cache_set(text: str, data: bytes, voice: str = "") -> None:
    try:
        from core.audio_cache import save_to_cache
        save_to_cache(text, data, voice=voice or _get_tts_voice())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Backend: Edge-TTS streaming (byte chunks — không qua base64)
# ---------------------------------------------------------------------------

async def _stream_edge_tts(
    text: str,
    voice: str,
    rate: str,
) -> AsyncGenerator[bytes, None]:
    """
    Stream raw MP3 bytes từ Edge-TTS Microsoft.
    Chunk đầu tiên thường về trong 150–250ms.
    """
    try:
        import edge_tts  # type: ignore
    except ImportError:
        logger.warning("[TTS] edge_tts chưa cài. pip install edge-tts")
        return

    accumulated = bytearray()
    for attempt in range(2):
        accumulated.clear()
        try:
            communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
            async for item in communicate.stream():
                if item.get("type") == "audio" and item.get("data"):
                    chunk: bytes = item["data"]
                    accumulated.extend(chunk)
                    yield chunk

            if accumulated:
                logger.debug(
                    "[TTS/edge-tts] OK: %d bytes, attempt=%d, voice=%s",
                    len(accumulated), attempt + 1, voice,
                )
                _cache_set(text, bytes(accumulated), voice)
                return

            logger.warning("[TTS/edge-tts] 0 chunks trả về (attempt %d), retry...", attempt + 1)
            await asyncio.sleep(0.2)

        except Exception as exc:
            if accumulated:
                return  # Đã có một phần audio — dùng luôn
            if attempt == 0:
                logger.debug("[TTS/edge-tts] Lỗi attempt 1: %s — retry...", exc)
                await asyncio.sleep(0.15)
                continue
            logger.warning("[TTS/edge-tts] Lỗi sau retry: %s", exc)
            return


# ---------------------------------------------------------------------------
# Backend: gTTS fallback (không phụ thuộc mạng phương Tây)
# ---------------------------------------------------------------------------

async def _synthesise_gtts(text: str) -> Optional[bytes]:
    """
    Synthesise toàn bộ văn bản với gTTS trong executor (non-blocking).
    Thường mất 2–4s nhưng luôn thành công khi có internet.
    """
    try:
        from gtts import gTTS  # type: ignore

        def _synth() -> bytes:
            buf = io.BytesIO()
            gTTS(text=text, lang="vi", slow=False).write_to_fp(buf)
            return buf.getvalue()

        loop = asyncio.get_event_loop()
        data = await asyncio.wait_for(loop.run_in_executor(None, _synth), timeout=12.0)
        if data and len(data) > 100:
            logger.debug("[TTS/gTTS] OK: %d bytes", len(data))
            return data
    except Exception as exc:
        logger.debug("[TTS/gTTS] Lỗi: %s", exc)
    return None


# ---------------------------------------------------------------------------
# Backend: ElevenLabs WebSocket streaming (optional, requires API key)
# ---------------------------------------------------------------------------

async def _stream_elevenlabs_ws(
    text: str,
    voice_id: str,
    api_key: str,
    model: str = "eleven_turbo_v2_5",
) -> AsyncGenerator[bytes, None]:
    """
    Stream audio từ ElevenLabs WebSocket API (không dùng REST để tránh delay 2-3s).
    Kết nối: wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input
    """
    try:
        import websockets  # type: ignore
        import json
        import base64
    except ImportError:
        logger.warning("[TTS/ElevenLabs] websockets chưa cài. pip install websockets")
        return

    uri = f"wss://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream-input?model_id={model}"
    has_yielded = False

    try:
        async with websockets.connect(
            uri,
            additional_headers={"xi-api-key": api_key},
            ping_timeout=10,
            close_timeout=5,
        ) as ws:
            # Gửi config ban đầu
            await ws.send(json.dumps({
                "text": " ",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                "generation_config": {"chunk_length_schedule": [120, 160, 250, 290]},
            }))

            # Gửi text
            await ws.send(json.dumps({"text": text, "try_trigger_generation": True}))

            # Gửi kết thúc
            await ws.send(json.dumps({"text": ""}))

            # Nhận chunks
            async for raw_msg in ws:
                try:
                    msg = json.loads(raw_msg)
                    audio_b64 = msg.get("audio")
                    if audio_b64:
                        chunk = base64.b64decode(audio_b64)
                        if chunk:
                            has_yielded = True
                            yield chunk
                    if msg.get("isFinal"):
                        break
                except Exception:
                    continue

        if has_yielded:
            logger.debug("[TTS/ElevenLabs] WS streaming OK")

    except Exception as exc:
        logger.warning("[TTS/ElevenLabs] WS lỗi: %s", exc)


# ---------------------------------------------------------------------------
# Main TTS Stream Engine
# ---------------------------------------------------------------------------

class TTSStreamEngine:
    """
    Engine tổng hợp giọng đọc đa nguồn, streaming-first.

    Sử dụng:
        engine = TTSStreamEngine()

        # Stream (yield chunks ngay khi có)
        async for chunk in engine.stream(sentence):
            await ws.send_bytes(chunk)

        # Hoặc lấy toàn bộ (cho cache, HUD base64 fallback)
        audio_bytes = await engine.synthesise(sentence)
    """

    def __init__(
        self,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> None:
        self._voice = voice or _get_tts_voice()
        self._rate = rate or _get_tts_rate()
        self._elevenlabs_config = self._load_elevenlabs_config()

    def _load_elevenlabs_config(self) -> Optional[dict]:
        """Đọc ElevenLabs config từ settings nếu người dùng đã cấu hình."""
        try:
            from core.config_loader import settings
            audio = getattr(settings, "audio", {})
            cfg = audio if isinstance(audio, dict) else {}
            api_key = cfg.get("elevenlabs_api_key") or getattr(settings, "ELEVENLABS_API_KEY", "")
            voice_id = cfg.get("elevenlabs_voice_id") or getattr(settings, "ELEVENLABS_VOICE_ID", "")
            tts_engine = cfg.get("tts_engine", "edge-tts")
            if api_key and voice_id and tts_engine == "elevenlabs":
                return {
                    "api_key": api_key,
                    "voice_id": voice_id,
                    "model": cfg.get("elevenlabs_model", "eleven_turbo_v2_5"),
                }
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # stream() — ưu tiên cache → edge-tts streaming chunks
    # ------------------------------------------------------------------

    async def stream(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Yield raw audio bytes ngay khi có — không chờ toàn bộ file hoàn thành.
        Sử dụng cho WebSocket binary frame.
        """
        if not text or not text.strip():
            return

        effective_voice = voice or self._voice
        effective_rate = rate or self._rate

        # 1. Cache hit (0ms)
        cached = _cache_get(text)
        if cached and len(cached) > 100:
            logger.info("[TTSStream] Cache HIT (0ms): '%s'", text[:50])
            yield cached
            return

        t0 = time.monotonic()

        # 2. ElevenLabs WS (nếu đã cấu hình)
        if self._elevenlabs_config:
            cfg = self._elevenlabs_config
            chunks_buf = bytearray()
            async for chunk in _stream_elevenlabs_ws(
                text, cfg["voice_id"], cfg["api_key"], cfg["model"]
            ):
                chunks_buf.extend(chunk)
                yield chunk

            if chunks_buf:
                elapsed = (time.monotonic() - t0) * 1000
                logger.info("[TTSStream] ElevenLabs: %d bytes / %.0fms", len(chunks_buf), elapsed)
                _cache_set(text, bytes(chunks_buf), effective_voice)
                return

        # 3. Edge-TTS stream (chính)
        edge_buf = bytearray()
        async for chunk in _stream_edge_tts(text, effective_voice, effective_rate):
            edge_buf.extend(chunk)
            yield chunk

        if edge_buf:
            elapsed = (time.monotonic() - t0) * 1000
            logger.info("[TTSStream] Edge-TTS: %d bytes / %.0fms", len(edge_buf), elapsed)
            _cache_set(text, bytes(edge_buf), effective_voice)
            return

        # 3.5 9Router Edge-TTS fallback (Bảo tồn 100% giọng Hoài My qua Proxy)
        try:
            from core.audio_processor import audio_engine
            router_bytes = await audio_engine._tts_9router(text, voice=effective_voice)
            if router_bytes and len(router_bytes) > 100:
                elapsed = (time.monotonic() - t0) * 1000
                logger.info("[TTSStream] 9Router Hoài My fallback: %d bytes / %.0fms", len(router_bytes), elapsed)
                _cache_set(text, router_bytes, effective_voice)
                yield router_bytes
                return
        except Exception as exc:
            logger.debug("[TTSStream] 9Router fallback exception: %s", exc)

        # 4. gTTS fallback (toàn bộ rồi yield một lần — cứu cánh cuối cùng)
        logger.warning("[TTSStream] Edge-TTS & 9Router thất bại — chuyển sang gTTS fallback")
        gtts_data = await _synthesise_gtts(text)
        if gtts_data:
            elapsed = (time.monotonic() - t0) * 1000
            logger.info("[TTSStream] gTTS fallback: %d bytes / %.0fms", len(gtts_data), elapsed)
            _cache_set(text, gtts_data, "gtts-vi")
            yield gtts_data
        else:
            logger.error("[TTSStream] TẤT CẢ nguồn TTS thất bại cho: '%s'", text[:60])

    # ------------------------------------------------------------------
    # synthesise() — collect tất cả chunks thành bytes (cho cache/base64)
    # ------------------------------------------------------------------

    async def synthesise(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Tổng hợp toàn bộ câu thành bytes (blocking-style, có cache).
        Dùng cho: Acoustic ACK warmup, HUD base64 fallback, lưu cache.
        """
        cached = _cache_get(text)
        if cached and len(cached) > 100:
            return cached

        buf = bytearray()
        async for chunk in self.stream(text, voice=voice, rate=rate):
            buf.extend(chunk)

        return bytes(buf) if buf else None

    # ------------------------------------------------------------------
    # Race: edge-tts || gTTS — ai về trước thắng (cho _safe_tts)
    # ------------------------------------------------------------------

    async def race_synthesise(self, text: str) -> Optional[bytes]:
        """
        Tổng hợp âm thanh ưu tiên Microsoft Hoài My (Edge-TTS -> 9Router),
        chỉ dùng gTTS khi cả hai nguồn đều không phản hồi.
        """
        cached = _cache_get(text)
        if cached and len(cached) > 100:
            return cached

        # Ưu tiên 1: Edge-TTS
        try:
            buf = bytearray()
            async for chunk in _stream_edge_tts(text, self._voice, self._rate):
                buf.extend(chunk)
            if buf and len(buf) > 100:
                res = bytes(buf)
                _cache_set(text, res, self._voice)
                return res
        except Exception as exc:
            logger.debug("[TTS/Synthesise] Edge-TTS lỗi: %s", exc)

        # Ưu tiên 2: 9Router Hoài My
        try:
            from core.audio_processor import audio_engine
            res = await audio_engine._tts_9router(text, voice=self._voice)
            if res and len(res) > 100:
                _cache_set(text, res, self._voice)
                return res
        except Exception as exc:
            logger.debug("[TTS/Synthesise] 9Router lỗi: %s", exc)

        # Cứu cánh cuối cùng: gTTS
        gtts_data = await _synthesise_gtts(text)
        if gtts_data and len(gtts_data) > 100:
            _cache_set(text, gtts_data, "gtts-vi")
            return gtts_data

        return None


# ---------------------------------------------------------------------------
# Module-level singleton (khởi tạo lazy)
# ---------------------------------------------------------------------------

_tts_engine: Optional[TTSStreamEngine] = None


def get_tts_engine() -> TTSStreamEngine:
    """Lấy singleton TTSStreamEngine (tạo mới nếu cần)."""
    global _tts_engine
    if _tts_engine is None:
        _tts_engine = TTSStreamEngine()
    return _tts_engine


def reset_tts_engine() -> None:
    """Reset singleton (dùng khi config thay đổi)."""
    global _tts_engine
    _tts_engine = None
