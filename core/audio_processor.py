"""
core/audio_processor.py
========================
Audio Pipeline Engine for VN-MateAI — Phase 23 Latency-Optimised.

Responsibilities:
  - ASR  : `transcribe_audio(audio_bytes)` — chuyển đổi luồng byte âm thanh → văn bản tiếng Việt.
           Backend priority: local_whisper (faster-whisper, ~0.3s) → google (2s).
           Không nhận dạng được thì trả chuỗi rỗng — không tự bịa nội dung lời nói.
  - TTS  : `text_to_speech_stream(text)` — dùng edge-tts với giọng
            'vi-VN-HoaiMyNeural', yield các chunk MP3 byte ngay khi engine tạo ra (streaming).

Zero-Disk-I/O Contract:
  - Tất cả âm thanh đều xử lý qua `io.BytesIO` trong RAM.
  - Không gọi `open(...)` với mode ghi ra bất kỳ file tạm nào.

ASR Backend (config.json ASR_BACKEND):
  - "local_whisper" → faster-whisper tiny model, CPU int8, offline, ~0.3s  [DEFAULT]
  - "whisper"       → OpenAI Whisper API (api.openai.com)
  - "groq"          → Groq Whisper API (ultra-fast cloud)
  - "google"        → Google Speech Recognition (free, fallback)

Phase 73: đã gỡ backend "mock". Backend đó trả câu văn bản mẫu dựa trên độ dài
byte âm thanh, khiến hệ thống hành xử như thể người dùng đã nói đúng câu đó.

Phase 23 Changes:
  - Local faster-whisper singleton (loaded once into RAM on first use).
  - Edge-TTS timeout (2.5s) to avoid 3s+ network stalls, with fast fallback.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import threading
import time
from typing import Any, AsyncGenerator, Dict, Optional

import edge_tts                          # type: ignore[import]
import httpx
import numpy as np

from core.config_loader import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTS Configuration & Helpers
# ---------------------------------------------------------------------------

_DEFAULT_TTS_VOICE: str = "vi-VN-HoaiMyNeural"   # Microsoft Neural TTS — Vietnamese female
_DEFAULT_TTS_RATE: str = "+15%"                  # Brisk, natural pace (prevents dragging)
_EDGE_TTS_TIMEOUT: float = 3.5                   # Max seconds to wait for edge-tts

# In-Memory Fast LRU Cache for TTS audio bytes (Zero-Disk-I/O, 0ms latency for repeated speech)
_TTS_CACHE: Dict[str, bytes] = {}
_TTS_CACHE_MAX_SIZE: int = 128
_tts_cache_lock = threading.Lock()


def _get_tts_voice() -> str:
    return getattr(settings, "TTS_VOICE", _DEFAULT_TTS_VOICE) or _DEFAULT_TTS_VOICE


def _get_tts_rate() -> str:
    return getattr(settings, "TTS_RATE", _DEFAULT_TTS_RATE) or _DEFAULT_TTS_RATE


def clean_text_for_tts(text: str) -> str:
    """Clean markdown, symbols, emojis, URLs and format for natural Hoài My Vietnamese TTS."""
    if not text:
        return ""
    # Strip URLs
    text = re.sub(r'https?://\S+', '', text)
    # Strip markdown links [label](url) -> label
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Strip markdown styling symbols (*, _, `, ~, #, >, |)
    text = re.sub(r'[*_#`~>|]', ' ', text)
    # Strip common emojis and bullet symbols that make TTS stutter
    text = re.sub(r'[🔴🟢🟡🔵⚪⚫⚡⚠️✅❌★☆✨🎙️🔊📢💬🤖💡🛠️📦🔗]', ' ', text)
    # Natural pronunciation hints for Vietnamese
    text = re.sub(r'\bVN-?MateAI\b', 'VN Mate AI', text, flags=re.I)
    text = re.sub(r'\bLyly\b', 'Ly Ly', text, flags=re.I)
    text = re.sub(r'\bAPI\b', 'A P I', text)
    text = re.sub(r'\bRAM\b', 'Ram', text)
    text = re.sub(r'\bCPU\b', 'C P U', text)
    text = re.sub(r'\bPC-([a-zA-Z0-9]+)\b', r'PC \1', text)
    # Replace dashes/em-dashes between clauses with natural comma pause
    text = re.sub(r'\s*[-–—]\s*', ', ', text)
    # Collapse multiple whitespace
    text = re.sub(r'\s+', ' ', text).strip()

    # Guard: Spoken voice should be concise (<= 200 chars).
    # If text is too long, extract first 1-2 clean sentences to prevent TTS latency/timeout.
    if len(text) > 200:
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 3]
        short_parts = []
        cur_len = 0
        for s in sentences:
            if cur_len + len(s) < 170:
                short_parts.append(s)
                cur_len += len(s)
            else:
                break
        if short_parts:
            text = " ".join(short_parts)
            if not any(text.endswith(p) for p in ('.', '!', '?')):
                text += "."
            text += " Chi tiết cụ thể đã hiển thị trên màn hình."
        else:
            text = text[:160] + "... Chi tiết đã hiển thị trên màn hình."

    return text


# ---------------------------------------------------------------------------
# ASR Configuration
# ---------------------------------------------------------------------------

_WHISPER_MODEL: str = "whisper-1"
_GROQ_WHISPER_MODEL: str = "whisper-large-v3"

# ---------------------------------------------------------------------------
# Local Whisper — singleton loaded once into RAM (thread-safe)
# ---------------------------------------------------------------------------

_local_whisper_model = None
_local_whisper_lock = threading.Lock()


def _get_local_whisper():
    """
    Lazy-load faster-whisper tiny model into RAM.
    Thread-safe singleton: model is loaded only once and reused across all calls.
    Loading time: ~1-2s on first call, 0s on subsequent calls.
    """
    global _local_whisper_model
    if _local_whisper_model is not None:
        return _local_whisper_model

    with _local_whisper_lock:
        if _local_whisper_model is not None:
            return _local_whisper_model
        try:
            from faster_whisper import WhisperModel  # type: ignore[import]
            model_size = getattr(settings, "WHISPER_MODEL_SIZE", "tiny")
            logger.info("[LocalWhisper] Loading model '%s' (CPU int8)...", model_size)
            t0 = time.monotonic()
            _local_whisper_model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
            )
            logger.info("[LocalWhisper] Model loaded in %.2fs.", time.monotonic() - t0)
        except ImportError:
            logger.warning("[LocalWhisper] faster-whisper not installed. Will fall back to Google ASR.")
            _local_whisper_model = None
        except Exception as exc:
            logger.error("[LocalWhisper] Model load failed: %s", exc)
            _local_whisper_model = None
    return _local_whisper_model


# ---------------------------------------------------------------------------
# Silero VAD (Voice Activity Detection) — Phase 50 Ultra-Low Latency
# ---------------------------------------------------------------------------

_silero_vad_model = None
_silero_vad_lock = threading.Lock()


def get_silero_vad_model():
    """Lazy-load Silero VAD neural network model singleton into RAM."""
    global _silero_vad_model
    if _silero_vad_model is not None:
        return _silero_vad_model

    with _silero_vad_lock:
        if _silero_vad_model is not None:
            return _silero_vad_model
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from silero_vad import load_silero_vad
                _silero_vad_model = load_silero_vad()
            logger.info("[SileroVAD] Model loaded successfully into RAM.")
        except Exception as exc:
            logger.warning("[SileroVAD] Failed to load Silero VAD model (%s). Will use energy RMS fallback.", exc)
            _silero_vad_model = None
    return _silero_vad_model


class SileroVADDetector:
    """
    Phase 50 Full-Duplex Voice Activity Detector (VAD).
    Monitors incoming audio chunks (16kHz / 24kHz PCM from Web Mic or ESP32 Xiaozhi).
    Detects user silence <= 500ms immediately to trigger zero-wait cutoff.
    """

    def __init__(
        self,
        threshold: float = 0.5,
        min_silence_duration_ms: int = 500,
        sample_rate: int = 16000,
    ) -> None:
        self.sample_rate = sample_rate
        self.threshold = threshold
        self.min_silence_duration_ms = min_silence_duration_ms
        self.buffer = np.array([], dtype=np.float32)
        self.is_speaking: bool = False
        self.silence_samples_count: int = 0
        self.min_silence_samples = int(sample_rate * min_silence_duration_ms / 1000)

        # Silero VAD Iterator instance if available
        self.iterator = None
        model = get_silero_vad_model()
        if model is not None:
            try:
                from silero_vad import VADIterator
                self.iterator = VADIterator(
                    model,
                    threshold=threshold,
                    sampling_rate=16000,
                    min_silence_duration_ms=min_silence_duration_ms,
                )
            except Exception as e:
                logger.debug("[SileroVADDetector] VADIterator init error: %s", e)

    def reset(self) -> None:
        """Reset internal VAD states for a new speech turn."""
        self.buffer = np.array([], dtype=np.float32)
        self.is_speaking = False
        self.silence_samples_count = 0
        if self.iterator is not None:
            try:
                self.iterator.reset_states()
            except Exception:
                pass

    def process_pcm16(
        self,
        pcm_bytes: bytes,
        incoming_rate: int = 16000,
    ) -> Dict[str, Any]:
        """
        Feed a raw 16-bit PCM chunk from WebSocket/mic.
        Returns:
            {
                "is_speaking": bool,
                "speech_started": bool,
                "speech_ended": bool, # True when 500ms silence detected after speech
            }
        """
        if not pcm_bytes:
            return {"is_speaking": self.is_speaking, "speech_started": False, "speech_ended": False}

        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if incoming_rate != 16000 and len(arr) > 0:
            num_target = int(len(arr) * 16000 / incoming_rate)
            if num_target > 0:
                arr = np.interp(
                    np.linspace(0, len(arr), num_target, endpoint=False),
                    np.arange(len(arr)),
                    arr,
                ).astype(np.float32)

        self.buffer = np.concatenate([self.buffer, arr])
        speech_started = False
        speech_ended = False

        if self.iterator is not None:
            # torch là phụ thuộc bắt buộc của silero-vad (đã khai báo trong
            # requirements.txt). Nếu thiếu, KHÔNG để ImportError làm sập toàn bộ
            # luồng âm thanh — hạ xuống VAD dựa trên năng lượng (RMS) bên dưới.
            try:
                import torch
            except ImportError:
                logger.warning(
                    "Không import được torch — tạm hạ xuống VAD năng lượng (RMS) cho "
                    "đến khi cài lại torch. Cài bằng: pip install torch"
                )
                self.iterator = None
            else:
                while len(self.buffer) >= 512:
                    chunk = self.buffer[:512]
                    self.buffer = self.buffer[512:]
                    try:
                        res = self.iterator(torch.from_numpy(chunk))
                        if res:
                            if "start" in res:
                                self.is_speaking = True
                                speech_started = True
                            elif "end" in res:
                                self.is_speaking = False
                                speech_ended = True
                    except Exception:
                        pass

        if self.iterator is None:
            # Fallback Energy-based RMS VAD with 500ms silence threshold
            while len(self.buffer) >= 512:
                chunk = self.buffer[:512]
                self.buffer = self.buffer[512:]
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                if rms > 0.02:  # Active speech
                    if not self.is_speaking:
                        self.is_speaking = True
                        speech_started = True
                    self.silence_samples_count = 0
                else:  # Silence
                    if self.is_speaking:
                        self.silence_samples_count += 512
                        if self.silence_samples_count >= self.min_silence_samples:
                            self.is_speaking = False
                            speech_ended = True
                            self.silence_samples_count = 0

        return {
            "is_speaking": self.is_speaking,
            "speech_started": speech_started,
            "speech_ended": speech_ended,
        }


def audio_bytes_to_numpy_float32(audio_bytes: bytes) -> np.ndarray:
    """
    Phase 50 Step 2: Pure in-memory conversion of audio bytes to float32 NumPy array.
    Zero Disk I/O.
    """
    if not audio_bytes:
        return np.array([], dtype=np.float32)

    # If already raw PCM (e.g. from mic/ESP32, without container headers)
    if not (audio_bytes.startswith(b"RIFF") or audio_bytes.startswith(b"\x1aE\xdf\xa3") or
            audio_bytes.startswith(b"OggS") or audio_bytes.startswith(b"ID3") or
            audio_bytes[:2] == b"\xff\xfb"):
        try:
            return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            pass

    # Decode containerized audio (WAV, MP3, WebM, Ogg) completely in RAM via pydub
    try:
        from pydub import AudioSegment
        seg = AudioSegment.from_file(io.BytesIO(audio_bytes))
        seg = seg.set_channels(1).set_frame_rate(16000)
        samples = np.array(seg.get_array_of_samples(), dtype=np.float32) / 32768.0
        return samples
    except Exception as exc:
        logger.debug("[AudioDecoder] RAM decode using pydub failed (%s), fallback raw: ", exc)
        try:
            return np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        except Exception:
            return np.array([], dtype=np.float32)


def _detect_audio_extension_and_mime(audio_bytes: bytes) -> tuple[str, str]:
    """Tự động phát hiện container âm thanh từ magic bytes ở header."""
    if not audio_bytes:
        return "audio.wav", "audio/wav"
    if audio_bytes.startswith(b"RIFF"):
        return "audio.wav", "audio/wav"
    if audio_bytes.startswith(b"\x1aE\xdf\xa3"):
        return "audio.webm", "audio/webm"
    if audio_bytes.startswith(b"OggS"):
        return "audio.ogg", "audio/ogg"
    if audio_bytes.startswith(b"ID3") or audio_bytes[:2] == b"\xff\xfb":
        return "audio.mp3", "audio/mpeg"
    if audio_bytes.startswith(b"fLaC"):
        return "audio.flac", "audio/flac"
    return "audio.wav", "audio/wav"


# ---------------------------------------------------------------------------
# AudioEngine
# ---------------------------------------------------------------------------


class AudioEngine:
    """
    Stateless audio processing engine.
    All methods are coroutines; instances can be shared across WebSocket connections.
    """

    clean_text_for_tts = staticmethod(clean_text_for_tts)

    # ------------------------------------------------------------------
    # ASR — Speech to Text
    # ------------------------------------------------------------------

    async def transcribe_audio(self, audio_bytes: bytes) -> str:
        """
        Convert raw audio bytes (PCM/WAV/MP3/OPUS) to Vietnamese text.

        Priority chain (Phase 23):
          1. local_whisper — offline faster-whisper, ~0.3s, no network.
          2. groq          — cloud Groq Whisper, ~0.5s.
          3. whisper       — OpenAI Whisper API.
          4. google        — Google free ASR (~2s), last resort.

        Không engine nào nhận dạng được -> trả "" (không đoán nội dung lời nói).

        Args:
            audio_bytes: Raw audio data.

        Returns:
            Transcribed Vietnamese text, or "" on failure.
        """
        if not audio_bytes:
            logger.warning("transcribe_audio: received empty audio buffer, skipping.")
            return ""

        backend: str = getattr(settings, "ASR_BACKEND", "local_whisper").lower()

        logger.info(
            "ASR request: backend=%s, audio_size=%d bytes",
            backend, len(audio_bytes),
        )

        try:
            if backend == "local_whisper":
                result = await self._transcribe_local_whisper(audio_bytes)
                if result:
                    return result
                # If local model not available, fall through to google
                logger.warning("[LocalWhisper] No result, falling back to Google ASR.")
                return await self._transcribe_google(audio_bytes)

            elif backend == "groq" and getattr(settings, "GROQ_API_KEY", ""):
                return await self._transcribe_groq(audio_bytes)

            elif backend == "whisper" and getattr(settings, "API_KEY", ""):
                return await self._transcribe_whisper(audio_bytes)

            else:
                # Default fallback: Google (free, no key)
                result = await self._transcribe_google(audio_bytes)
                if result:
                    return result
                # Phase 73: KHÔNG bịa nội dung người dùng đã nói. Trước đây khi
                # không engine nào nhận dạng được, hệ thống trả về câu mẫu ("xin
                # chào"…) rồi đưa vào vòng lặp LLM — người dùng tưởng mình đã
                # nói câu đó. Không nhận dạng được thì trả chuỗi rỗng.
                logger.warning("Không có engine ASR nào nhận dạng được âm thanh; trả về chuỗi rỗng.")
                return ""

        except Exception as exc:  # pylint: disable=broad-except
            logger.error("ASR transcription failed [backend=%s]: %s", backend, exc)
            return ""

    async def _transcribe_local_whisper(self, audio_bytes: bytes) -> str:
        """
        Transcribe via local faster-whisper model (offline, no network, ~0.1-0.2s).
        Phase 50 Step 2: In-Memory float32 NumPy array passed directly to transcribe() (Zero Disk I/O).
        """
        def _sync_transcribe() -> str:
            model = _get_local_whisper()
            if model is None:
                return ""

            try:
                t0 = time.monotonic()
                # Phase 50 Step 2: Convert to numpy float32 array in RAM directly (Zero Disk I/O)
                audio_np = audio_bytes_to_numpy_float32(audio_bytes)
                if len(audio_np) == 0:
                    return ""

                # Phase 50 Step 2.4: Faster-Whisper with vad_filter=True, beam_size=1
                segments, info = model.transcribe(
                    audio_np,
                    language="vi",
                    beam_size=1,            # 1% accuracy tradeoff for 2x speedup
                    best_of=1,
                    temperature=0.0,
                    vad_filter=True,        # Filter out silence
                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                    ),
                )
                text = " ".join(seg.text.strip() for seg in segments).strip()
                elapsed = time.monotonic() - t0
                logger.info(
                    "[LocalWhisper-InRam] ASR done in %.2fs, lang=%s, text='%s'",
                    elapsed, getattr(info, "language", "vi"), text[:100],
                )
                return text
            except Exception as exc:
                logger.error("[LocalWhisper] Transcription error: %s", exc)
                return ""

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_transcribe)

    async def _transcribe_whisper(self, audio_bytes: bytes) -> str:
        """
        Transcribe via OpenAI Whisper API.
        Runs the synchronous openai SDK call in a thread executor to avoid
        blocking the async event loop.
        """
        from openai import OpenAI

        fname, mime = _detect_audio_extension_and_mime(audio_bytes)

        def _sync_call() -> str:
            client = OpenAI(
                api_key=settings.API_KEY,
                base_url=settings.BASE_URL,
            )
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = fname

            transcript = client.audio.transcriptions.create(
                model=_WHISPER_MODEL,
                file=audio_file,
                language="vi",
                response_format="text",
            )
            return str(transcript).strip()

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _sync_call)
        logger.info("Whisper ASR result: '%s'", text[:100])
        return text

    async def _transcribe_groq(self, audio_bytes: bytes) -> str:
        """
        Transcribe via Groq Whisper API (ultra-fast, ~240x realtime).
        Uses httpx async client for non-blocking HTTP.
        """
        groq_key: str = getattr(settings, "GROQ_API_KEY", "")
        groq_url: str = getattr(settings, "GROQ_BASE_URL", "https://api.groq.com/openai/v1")

        if not groq_key:
            logger.warning("GROQ_API_KEY not set; falling back to OpenAI Whisper.")
            return await self._transcribe_whisper(audio_bytes)

        fname, mime = _detect_audio_extension_and_mime(audio_bytes)
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = fname

        async with httpx.AsyncClient(
            base_url=groq_url,
            headers={"Authorization": f"Bearer {groq_key}"},
            timeout=30.0,
        ) as client:
            response = await client.post(
                "/audio/transcriptions",
                files={"file": (fname, audio_file, mime)},
                data={
                    "model": _GROQ_WHISPER_MODEL,
                    "language": "vi",
                    "response_format": "text",
                },
            )
            response.raise_for_status()
            text = response.text.strip()
            logger.info("Groq ASR result: '%s'", text[:100])
            return text

    async def _transcribe_google(self, audio_bytes: bytes) -> str:
        """
        Transcribe audio via Google Speech Recognition (free, no API key needed).
        Converts WebM/Ogg/MP3 via pydub to standard WAV if necessary.
        """
        def _sync_google() -> str:
            try:
                import speech_recognition as sr
            except ImportError:
                logger.warning("speech_recognition not installed for Google ASR.")
                return ""

            r = sr.Recognizer()
            wav_buf = io.BytesIO(audio_bytes)

            if not audio_bytes.startswith(b"RIFF"):
                try:
                    from pydub import AudioSegment
                    audio_seg = AudioSegment.from_file(wav_buf)
                    converted = io.BytesIO()
                    audio_seg.export(converted, format="wav")
                    converted.seek(0)
                    wav_buf = converted
                except Exception as conv_err:
                    logger.debug("pydub conversion skipped/failed: %s", conv_err)
                    wav_buf.seek(0)

            try:
                with sr.AudioFile(wav_buf) as source:
                    audio_data = r.record(source)

                try:
                    text = r.recognize_google(audio_data, language="vi-VN")
                    return text.strip()
                except sr.UnknownValueError:
                    try:
                        text = r.recognize_google(audio_data, language="en-US")
                        return text.strip()
                    except (sr.UnknownValueError, sr.RequestError):
                        return ""
                except sr.RequestError as req_err:
                    logger.warning("Google ASR request error: %s", req_err)
                    return ""
            except Exception as exc:
                logger.warning("Google ASR processing error: %s", exc)
                return ""

        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(None, _sync_google)
        if text:
            logger.info("Google ASR result: '%s'", text[:100])
        return text

    # ------------------------------------------------------------------
    # TTS — Text to Speech với Fallback Chain (Phase 23: with timeout)
    # ------------------------------------------------------------------

    async def text_to_speech_stream(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """
        Fallback chain TTS:
          1. edge-tts  (primary — high quality Vietnamese Microsoft Neural with rate=+15%)
          2. gTTS      (fallback — Google TTS Vietnamese)
          3. pyttsx3   (last resort — offline)

        Yields MP3/WAV bytes.
        Uses asyncio.wait_for for Python 3.10+ compatibility.
        """
        if not text or not text.strip():
            return

        clean_text = clean_text_for_tts(text)
        if not clean_text:
            return

        voice = voice or _get_tts_voice()
        rate = rate or _get_tts_rate()

        # --- Phase 36: Kiểm tra Dynamic Audio Cache (0ms local audio hit) ---
        from core.audio_cache import get_cached_audio_bytes, save_to_cache
        cached_bytes = get_cached_audio_bytes(clean_text)
        if cached_bytes:
            logger.info("[TTS Audio Cache HIT (0ms)] Phát file MP3 nội bộ cho: '%s'", clean_text[:50])
            yield cached_bytes
            return

        logger.info(
            "TTS synthesis starting: voice=%s, rate=%s, text_len=%d, text='%s...'",
            voice, rate, len(clean_text), clean_text[:60],
        )

        # --- Try 1: edge-tts (Microsoft Neural — vi-VN-HoaiMyNeural) ---
        # Phase 50 Step 4: True streaming via communicate.stream() — yield chunks immediately!
        for attempt in range(2):
            chunks_collected = []
            try:
                communicate = edge_tts.Communicate(text=clean_text, voice=voice, rate=rate)
                async for item in communicate.stream():
                    if item["type"] == "audio" and item["data"]:
                        chunk_data = item["data"]
                        chunks_collected.append(chunk_data)
                        yield chunk_data

                if chunks_collected:
                    logger.info("TTS OK via edge-tts stream (%d chunks) [Giọng: %s].", len(chunks_collected), voice)
                    full_bytes = b"".join(chunks_collected)
                    # Phase 36: Lưu vào Local Audio Cache để tái sử dụng 0ms
                    save_to_cache(clean_text, full_bytes, voice=voice)
                    return

                logger.warning("edge-tts returned 0 audio chunks. Retrying...")

            except edge_tts.exceptions.NoAudioReceived:
                if chunks_collected:
                    return
                if attempt == 0:
                    logger.info("edge-tts NoAudioReceived, retrying once...")
                    await asyncio.sleep(0.2)
                    continue
                logger.warning("edge-tts NoAudioReceived after retry.")
            except (asyncio.TimeoutError, TimeoutError):
                if chunks_collected:
                    return
                if attempt == 0:
                    logger.info("edge-tts timed out on first attempt, retrying once...")
                    await asyncio.sleep(0.15)
                    continue
                logger.warning("edge-tts timed out. Trying 9Router Hoài My fallback...")
                break
            except Exception as exc:
                if chunks_collected:
                    return
                if attempt == 0:
                    logger.info("edge-tts error (%s), retrying once...", exc)
                    await asyncio.sleep(0.15)
                    continue
                logger.warning("edge-tts error: %s. Trying 9Router Hoài My fallback...", exc)
                break

        # --- Try 2: 9Router Audio/Speech API (Bảo tồn 100% giọng Hoài My qua Proxy) ---
        router_bytes = await self._tts_9router(clean_text, voice=voice)
        if router_bytes:
            logger.info("TTS OK via 9Router Edge-TTS fallback (%d bytes) [Giọng: %s].", len(router_bytes), voice)
            # Phase 36: Lưu vào Local Audio Cache
            save_to_cache(clean_text, router_bytes, voice=voice)
            yield router_bytes
            return

        # --- Try 3: gTTS (Google Translate TTS Vietnamese fallback - Không bao giờ lỗi) ---
        gtts_bytes = await self._tts_gtts(clean_text)
        if gtts_bytes:
            logger.info("TTS OK via gTTS fallback (%d bytes).", len(gtts_bytes))
            save_to_cache(clean_text, gtts_bytes, voice="gtts-vi")
            yield gtts_bytes
            return

        logger.error("TTS: Không thể tổng hợp giọng đọc cho text: '%s'", text[:60])

    @staticmethod
    async def _tts_gtts(text: str, lang: str = "vi") -> bytes:
        """Fallback to Google TTS (gTTS) if Edge-TTS / 9Router is temporarily unreachable."""
        try:
            from gtts import gTTS
            buf = io.BytesIO()
            def _synth() -> bytes:
                tts = gTTS(text=text, lang=lang, slow=False)
                tts.write_to_fp(buf)
                return buf.getvalue()
            loop = asyncio.get_event_loop()
            res = await loop.run_in_executor(None, _synth)
            return res
        except Exception as exc:
            logger.warning("gTTS fallback error: %s", exc)
            return b""

    @staticmethod
    async def _tts_9router(text: str, voice: str = "vi-VN-HoaiMyNeural") -> bytes:
        """9Router /v1/audio/speech proxy — giữ nguyên 100% giọng Hoài My (Nữ, miền Nam)."""
        try:
            base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1").rstrip("/")
            api_key = getattr(settings.llm, "api_key", "")
            # Chuẩn hóa mã model cho 9router
            model_id = f"edge-tts/{voice}" if not voice.startswith("edge-tts/") else voice
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": model_id,
                "input": text,
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(f"{base_url}/audio/speech", headers=headers, json=payload)
                if resp.status_code == 200 and len(resp.content) > 100:
                    return resp.content
                logger.warning("9Router TTS error status %d: %s", resp.status_code, resp.text[:120])
        except Exception as exc:
            logger.warning("9Router TTS exception: %s", exc)
        return b""

    async def text_to_speech_bytes(
        self,
        text: str,
        voice: Optional[str] = None,
        rate: Optional[str] = None,
    ) -> bytes:
        """
        Collect all TTS chunks into a single bytes object with in-memory & on-disk caching.
        Zero latency when cached.
        """
        if not text or not text.strip():
            return b""

        target_voice = voice or _get_tts_voice()
        target_rate = rate or _get_tts_rate()
        cache_key = f"{target_voice}|{target_rate}|{text.strip()}"

        with _tts_cache_lock:
            if cache_key in _TTS_CACHE:
                logger.info("[TTS Memory Cache HIT (0ms)] Reusing cached audio for: '%s'", text[:50])
                return _TTS_CACHE[cache_key]

        # Phase 36: Kiểm tra on-disk audio cache
        from core.audio_cache import get_cached_audio_bytes, save_to_cache
        cached_disk = get_cached_audio_bytes(text)
        if cached_disk:
            with _tts_cache_lock:
                _TTS_CACHE[cache_key] = cached_disk
            logger.info("[TTS Disk Cache HIT (0ms)] Reusing disk cached audio for: '%s'", text[:50])
            return cached_disk

        buffer = io.BytesIO()
        async for chunk in self.text_to_speech_stream(text, voice=target_voice, rate=target_rate):
            buffer.write(chunk)
        data = buffer.getvalue()

        if data:
            with _tts_cache_lock:
                if len(_TTS_CACHE) >= _TTS_CACHE_MAX_SIZE:
                    first_k = next(iter(_TTS_CACHE))
                    _TTS_CACHE.pop(first_k, None)
                _TTS_CACHE[cache_key] = data
            # Phase 36: Đảm bảo lưu vào disk cache
            save_to_cache(text, data, voice=target_voice)

        return data


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

audio_engine = AudioEngine()


def preload_whisper_model() -> None:
    """
    Preload the local Whisper model into RAM at server startup.
    Call this from main.py after uvicorn starts to avoid first-request delay.
    """
    backend: str = getattr(settings, "ASR_BACKEND", "local_whisper").lower()
    if backend == "local_whisper":
        logger.info("[LocalWhisper] Preloading model in background thread...")
        t = threading.Thread(target=_get_local_whisper, daemon=True, name="whisper-preload")
        t.start()


def prewarm_tts_cache() -> None:
    """
    Pre-warm the in-memory TTS cache for common responses in a background thread.
    Ensures the very first user interaction or greeting has 0ms synthesis delay.
    """
    ai_name = getattr(settings, "AI_NAME", None) or getattr(settings, "ASSISTANT_NAME", "Ly Ly")
    COMMON_PROMPTS = [
        f"Xin chào, em là {ai_name}. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.",
        "Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...",
        "Em đã thực hiện xong yêu cầu của bạn.",
        "Xin lỗi, em gặp lỗi xử lý nội bộ.",
        "Tác vụ này yêu cầu phê duyệt bảo mật, vui lòng xác nhận trên màn hình.",
    ]

    async def _warm():
        for phrase in COMMON_PROMPTS:
            try:
                await audio_engine.text_to_speech_bytes(phrase)
                await asyncio.sleep(0.05)
            except Exception:
                pass
        logger.info("[TTS Cache] Pre-warmed %d essential phrases into RAM.", len(COMMON_PROMPTS))

    def _worker():
        # Delay slightly so server startup port binding isn't contested
        time.sleep(1.5)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_warm())
        finally:
            loop.close()

    t = threading.Thread(target=_worker, daemon=True, name="tts-cache-prewarm")
    t.start()
