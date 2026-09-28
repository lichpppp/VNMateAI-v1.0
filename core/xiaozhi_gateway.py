"""
core/xiaozhi_gateway.py
========================
Phase 43: Xiaozhi Desktop Companion Protocol Gateway.

Key Responsibilities:
  1. LCD/OLED Display & Emotion Control (Step 1):
     - Send UI JSON payloads to ESP32 displays (ST7789, GC9A01, SSD1306):
       * listening  : {"type": "ui", "state": "listening", "emotion": "focused"}
       * processing : {"type": "ui", "state": "processing", "emotion": "thinking"}
       * alert      : {"type": "ui", "state": "alert", "text": "IIS Server 503 Error!"}
       * idle       : {"type": "ui", "state": "idle", "emotion": "sleeping"}
       * speaking   : {"type": "ui", "state": "speaking", "emotion": "happy"}
  2. Barge-in / Interruption Engine (Step 2):
     - Detects interruption frame {"type": "interrupt"} or flag `is_wakeup: true` while speaking.
     - Instantly cancels running LLM / Claude tasks.
     - Flushes / stops ESP32 audio playback buffer.
     - Plays 0ms reflex from local cache (Phase 36): "Dạ, anh nói đi em nghe đây."
     - Transitions UI back to listening / focused state.
  3. High-Fidelity Audio Streaming for I2S (Step 3):
     - 24kHz / 32kHz high-quality audio streaming.
     - Jitter buffer pre-fill burst (primes ESP32 DMA ring buffer) followed by smooth
       duration-based pacing to prevent buffer underrun and crackling on custom DACs/Amps.
  4. Autonomous Push Notification Wake (Step 4):
     - Direct linkage with Autonomous Sentinel.
     - Wakes up Desktop Robot with flashing red eyes / alert face on LCD.
     - Voices: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

from core.audio_cache import check_cached_audio, get_cached_audio_bytes, save_to_cache
from core.audio_processor import audio_engine, clean_text_for_tts
from core.config_loader import settings

logger = logging.getLogger("core.xiaozhi_gateway")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Reflex phrases pre-cached for 0ms latency
REFLEX_BARGE_IN_TEXT = "Dạ, anh nói đi em nghe đây."
REFLEX_SENTINEL_PREFIX = "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."


class XiaozhiNode:
    """Represents a connected Xiaozhi Desktop Companion / ESP32 hardware node."""

    def __init__(self, device_id: str, websocket: WebSocket, client_host: str) -> None:
        self.device_id = device_id
        self.websocket = websocket
        self.client_host = client_host
        self.connected_at = datetime.utcnow().isoformat()
        self.last_active = datetime.utcnow().isoformat()

        # UI & Hardware state
        self.state: str = "idle"             # idle | listening | processing | speaking | alert
        self.emotion: str = "sleeping"       # sleeping | focused | thinking | happy | alert | shocked
        self.screen_text: Optional[str] = None
        self.audio_format: str = "mp3_24k"   # mp3_24k | pcm_24k | opus

        # Firmware metadata — được lấp đầy khi thiết bị gửi frame 'hello'.
        # Trước khi handshake thì là "unknown" / rỗng.
        self.firmware_version: str = "unknown"
        self.capabilities: str = ""

        # Active tasks & cancellation
        self.active_task: Optional[asyncio.Task] = None
        self.cancel_event: asyncio.Event = asyncio.Event()
        self.audio_buffer: io.BytesIO = io.BytesIO()

        # Audio stream lock to prevent overlapping TTS
        self.stream_lock = asyncio.Lock()

        # Phase 50: Silero VAD detector for 500ms zero-wait silence cutoff
        from core.audio_processor import SileroVADDetector
        self.vad_detector = SileroVADDetector(
            threshold=0.5,
            min_silence_duration_ms=500,
            sample_rate=16000,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Telemetry representation for health monitor and Web Portal."""
        return {
            "device_id": self.device_id,
            "client_host": self.client_host,
            "connected_at": self.connected_at,
            "last_active": self.last_active,
            "state": self.state,
            "emotion": self.emotion,
            "screen_text": self.screen_text,
            "audio_format": self.audio_format,
            "firmware_version": self.firmware_version,
            "capabilities": self.capabilities,
            "is_busy": self.active_task is not None and not self.active_task.done(),
        }


class XiaozhiGateway:
    """Singleton Manager for Xiaozhi Desktop Companion Protocol."""

    def __init__(self) -> None:
        self._nodes: Dict[str, XiaozhiNode] = {}
        self._lock = asyncio.Lock()

    # -----------------------------------------------------------------------
    # Node Registry
    # -----------------------------------------------------------------------

    def get_node(self, device_id: str) -> Optional[XiaozhiNode]:
        return self._nodes.get(device_id)

    def get_all_nodes(self) -> Dict[str, XiaozhiNode]:
        return dict(self._nodes)

    def get_connected_count(self) -> int:
        return len(self._nodes)

    def get_nodes_telemetry(self) -> List[Dict[str, Any]]:
        return [node.to_dict() for node in self._nodes.values()]

    # -----------------------------------------------------------------------
    # STEP 1: LCD / OLED Display & Emotion Control
    # -----------------------------------------------------------------------

    async def send_ui_payload(
        self,
        device_id: str,
        state: str,
        emotion: Optional[str] = None,
        text: Optional[str] = None,
    ) -> bool:
        """
        Send formatted UI frame to Xiaozhi LCD / OLED display.
        Payload spec:
          - listening  : {"type": "ui", "state": "listening", "emotion": "focused"}
          - processing : {"type": "ui", "state": "processing", "emotion": "thinking"}
          - alert      : {"type": "ui", "state": "alert", "text": "..."}
          - idle       : {"type": "ui", "state": "idle", "emotion": "sleeping"}
          - speaking   : {"type": "ui", "state": "speaking", "emotion": "happy"}
        """
        node = self._nodes.get(device_id)
        if not node or not node.websocket:
            return False

        # Set sensible defaults based on state
        if not emotion:
            if state == "listening":
                emotion = "focused"
            elif state == "processing":
                emotion = "thinking"
            elif state == "alert":
                emotion = "alert"
            elif state == "idle":
                emotion = "sleeping"
            elif state == "speaking":
                emotion = "happy"
            else:
                emotion = "normal"

        payload: Dict[str, Any] = {
            "type": "ui",
            "state": state,
            "emotion": emotion,
        }
        if text:
            payload["text"] = text

        node.state = state
        node.emotion = emotion
        node.screen_text = text
        node.last_active = datetime.utcnow().isoformat()

        try:
            await node.websocket.send_text(json.dumps(payload, ensure_ascii=False))
            logger.debug("[Xiaozhi UI] Sent frame to [%s]: %s", device_id, payload)
            return True
        except Exception as exc:
            logger.warning("[Xiaozhi UI] Failed to send UI frame to [%s]: %s", device_id, exc)
            return False

    async def broadcast_ui_payload(
        self,
        state: str,
        emotion: Optional[str] = None,
        text: Optional[str] = None,
    ) -> int:
        """Send UI frame to all online Xiaozhi companion nodes."""
        success_count = 0
        for dev_id in list(self._nodes.keys()):
            if await self.send_ui_payload(dev_id, state=state, emotion=emotion, text=text):
                success_count += 1
        return success_count

    # -----------------------------------------------------------------------
    # STEP 3: High-Fidelity Audio Streaming for I2S
    # -----------------------------------------------------------------------

    async def _stream_audio_smooth(
        self,
        node: XiaozhiNode,
        audio_stream: AsyncGenerator[bytes, None],
        text_summary: str = "",
        sample_rate: int = 24000,
        bitrate: int = 48000,
        chunk_size: int = 2048,
    ) -> int:
        """
        Stream audio to ESP32 I2S DAC with anti-underrun pacing:
          1. Jitter buffer pre-fill: The first 3 chunks are sent immediately to
             prime the ESP32 DMA ring buffer.
          2. Paced delivery: Subsequent chunks are throttled at ~80% of playback
             duration to stay comfortably ahead of the DAC without buffer starvation
             or overflow.
          3. Immediate cancellation check: Listens to node.cancel_event to break instantly.
        """
        ws = node.websocket
        cancel_ev = node.cancel_event
        cancel_ev.clear()

        # Send TTS start header with high-fidelity audio metadata
        await ws.send_text(json.dumps({
            "type": "tts_start",
            "format": "audio/mp3",
            "sample_rate": sample_rate,
            "channels": 1,
            "bitrate": bitrate,
            "text": text_summary,
        }, ensure_ascii=False))

        chunk_count = 0
        prefill_count = 3  # Initial burst count

        # Approximate bytes per second for pacing
        bytes_per_sec = bitrate // 8  # e.g., 48000 / 8 = 6000 bytes/sec
        if bytes_per_sec <= 0:
            bytes_per_sec = 6000

        try:
            full_mp3 = bytearray()
            async for chunk in audio_stream:
                if cancel_ev.is_set():
                    break
                if chunk:
                    full_mp3.extend(chunk)

            if full_mp3 and not cancel_ev.is_set():
                from pydub import AudioSegment
                import io
                seg = AudioSegment.from_file(io.BytesIO(full_mp3), format="mp3")
                seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
                pcm_data = seg.raw_data

                # Send TTS start header with PCM metadata
                await ws.send_text(json.dumps({
                    "type": "tts_start",
                    "format": "audio/pcm",
                    "sample_rate": 16000,
                    "channels": 1,
                    "text": text_summary,
                }, ensure_ascii=False))

                for offset in range(0, len(pcm_data), chunk_size):
                    if cancel_ev.is_set():
                        break
                    sub_chunk = pcm_data[offset : offset + chunk_size]
                    await ws.send_bytes(sub_chunk)
                    chunk_count += 1
                    await asyncio.sleep(0.025)

        except (WebSocketDisconnect, ConnectionResetError):
            logger.info("[Xiaozhi I2S] WebSocket disconnected during playback on [%s]", node.device_id)
            return chunk_count
        except Exception as exc:
            logger.error("[Xiaozhi I2S] Error streaming audio to [%s]: %s", node.device_id, exc)

        if not cancel_ev.is_set():
            try:
                await ws.send_text(json.dumps({
                    "type": "tts_end",
                    "chunk_count": chunk_count,
                }))
            except Exception:
                pass

        return chunk_count

    async def _stream_audio_chunks(self, node: XiaozhiNode, audio_bytes: bytes) -> None:
        """Stream raw audio bytes converted to 16kHz PCM down to node."""
        try:
            from pydub import AudioSegment
            import io
            seg = AudioSegment.from_file(io.BytesIO(audio_bytes), format="mp3")
            seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
            pcm_data = seg.raw_data

            await node.websocket.send_text(json.dumps({
                "type": "tts_start",
                "format": "audio/pcm",
                "sample_rate": 16000,
            }))

            for offset in range(0, len(pcm_data), 1024):
                if node.cancel_event.is_set():
                    break
                await node.websocket.send_bytes(pcm_data[offset : offset + 1024])
                await asyncio.sleep(0.025)

            await node.websocket.send_text(json.dumps({
                "type": "tts_end",
            }))
        except Exception as exc:
            logger.error("[Xiaozhi] Error streaming audio chunks: %s", exc)

    # -----------------------------------------------------------------------
    # STEP 2: Barge-in / Interruption Engine
    # -----------------------------------------------------------------------

    async def handle_barge_in(self, device_id: str) -> None:
        """
        Handle Wake Word or Barge-in Interruption during active generation or playback:
          1. Cancel active 9router/Claude generation task.
          2. Clear ESP32 hardware audio buffer (send tts_stop).
          3. Play 0ms reflex from Cache: "Dạ, anh nói đi em nghe đây."
          4. Set UI to listening / focused.
        """
        node = self._nodes.get(device_id)
        if not node:
            return

        logger.info("[Xiaozhi Barge-in] Kích hoạt ngắt lời ngay lập tức trên mạch [%s]", device_id)

        # 1. Trigger cancel event to stop audio streamer loop
        node.cancel_event.set()

        # 2. Cancel active background LLM task if running
        if node.active_task and not node.active_task.done():
            logger.info("[Xiaozhi Barge-in] Huỷ bỏ tác vụ LLM/Claude đang chạy trên [%s]", device_id)
            node.active_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(node.active_task), timeout=0.1)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
            node.active_task = None

        # 3. Instruct ESP32 to drop playback buffer immediately
        try:
            await node.websocket.send_text(json.dumps({"type": "tts_stop"}))
            await node.websocket.send_text(json.dumps({"type": "clear_buffer"}))
        except Exception as e:
            logger.debug("[Xiaozhi Barge-in] Error sending tts_stop: %s", e)

        # 4. Update UI: listening / focused
        await self.send_ui_payload(device_id, state="listening", emotion="focused")

        # 5. Play 0ms Reflex confirmation from Cache
        reflex_bytes = get_cached_audio_bytes(REFLEX_BARGE_IN_TEXT)
        if reflex_bytes:
            logger.info("[Xiaozhi Barge-in] Phát câu đệm 0ms từ Cache cho [%s]: '%s'", device_id, REFLEX_BARGE_IN_TEXT)
            async def _cached_gen():
                yield reflex_bytes

            # Reset cancel event for the quick reflex speech
            node.cancel_event.clear()
            await self._stream_audio_smooth(node, _cached_gen(), text_summary=REFLEX_BARGE_IN_TEXT)

        # Reset audio input buffer to record user's new utterance
        node.audio_buffer = io.BytesIO()
        logger.info("[Xiaozhi Barge-in] Mạch [%s] đã sẵn sàng nhận lệnh mới.", device_id)

    # -----------------------------------------------------------------------
    # STEP 4: Push Notification / Autonomous Sentinel Wake Integration
    # -----------------------------------------------------------------------

    async def wake_and_alert(
        self,
        error_title: str,
        detail_message: str,
        device_id: Optional[str] = None,
    ) -> bool:
        """
        Autonomously wake up the Desktop Robot and voice an alert:
          1. Wakes up LCD with blinking red eyes / alert state:
             {"type": "ui", "state": "alert", "text": error_title}
          2. Voices prompt: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
             followed by the concise error detail.
        """
        targets = [self._nodes[device_id]] if device_id and device_id in self._nodes else list(self._nodes.values())
        if not targets:
            logger.warning("[Xiaozhi Push] Không có mạch Xiaozhi nào trực tuyến để phát cảnh báo: '%s'", error_title)
            return False

        logger.info("[Xiaozhi Push] Đang phát cảnh báo tới %d mạch Xiaozhi: '%s'", len(targets), error_title)

        # Build clean speech text
        spoken_text = f"{REFLEX_SENTINEL_PREFIX} {detail_message}"
        clean_spoken = clean_text_for_tts(spoken_text)

        for node in targets:
            # 1. Update LCD Screen immediately
            await self.send_ui_payload(node.device_id, state="alert", emotion="alert", text=error_title)

            # 2. Stream voice alert
            try:
                async with node.stream_lock:
                    await self._stream_audio_smooth(
                        node,
                        audio_engine.text_to_speech_stream(clean_spoken),
                        text_summary=clean_spoken,
                        sample_rate=24000,
                    )
            except Exception as exc:
                logger.error("[Xiaozhi Push] Lỗi phát thanh cảnh báo tới [%s]: %s", node.device_id, exc)

        return True

    # -----------------------------------------------------------------------
    # Core Pipeline: Speech / Query Execution
    # -----------------------------------------------------------------------

    async def _execute_pipeline(self, node: XiaozhiNode, text_query: str) -> None:
        """
        Phase 50 Full-Duplex Pipeline:
        Streams LLM tokens into sentence chunks (< 200ms) and immediately streams
        Edge-TTS audio chunks directly down the WebSocket to ESP32 without waiting.
        """
        from core.llm_engine import llm_engine

        device_id = node.device_id
        node.last_active = datetime.utcnow().isoformat()

        # Update UI: processing / thinking
        await self.send_ui_payload(device_id, state="processing", emotion="thinking")
        await node.websocket.send_text(json.dumps({"type": "llm_start"}))

        full_reply_parts = []
        is_first_sentence = True

        try:
            async with node.stream_lock:
                async for sentence in llm_engine.stream_voice_response(
                    query=text_query,
                    source_device=device_id,
                ):
                    if node.cancel_event.is_set():
                        logger.info("[Xiaozhi] Pipeline bị huỷ bởi ngắt lời trên [%s]", device_id)
                        return

                    clean_sentence = clean_text_for_tts(sentence)
                    if not clean_sentence:
                        continue

                    full_reply_parts.append(clean_sentence)

                    if is_first_sentence:
                        # Update UI: speaking / happy immediately on first chunk!
                        await self.send_ui_payload(
                            device_id, state="speaking", emotion="happy", text=clean_sentence[:60]
                        )
                        is_first_sentence = False

                    # Phase 50 Step 4: Convert TTS to 16kHz PCM and stream directly to MAX98357A
                    mp3_data = await audio_engine.text_to_speech_bytes(clean_sentence)
                    if mp3_data and not node.cancel_event.is_set():
                        from pydub import AudioSegment
                        import io
                        seg = AudioSegment.from_file(io.BytesIO(mp3_data), format="mp3")
                        seg = seg.set_frame_rate(16000).set_channels(1).set_sample_width(2)
                        pcm_data = seg.raw_data

                        for offset in range(0, len(pcm_data), 1024):
                            if node.cancel_event.is_set():
                                break
                            await node.websocket.send_bytes(pcm_data[offset : offset + 1024])
                            await asyncio.sleep(0.025)

            # Signal completion
            if not node.cancel_event.is_set():
                await node.websocket.send_text(json.dumps({
                    "type": "tts_end",
                    "text": " ".join(full_reply_parts),
                }))

        except asyncio.CancelledError:
            logger.info("[Xiaozhi] Pipeline bị huỷ bởi ngắt lời trên [%s]", device_id)
            return
        except Exception as exc:
            logger.error("[Xiaozhi] Lỗi LLM trên [%s]: %s", device_id, exc)
            await self.send_ui_payload(device_id, state="alert", text=f"Lỗi: {str(exc)[:40]}")
            await node.websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Lỗi LLM: {exc}",
            }))
            return

        # If not cancelled, return to idle / sleeping state after a brief pause
        if not node.cancel_event.is_set():
            await asyncio.sleep(1.0)
            await self.send_ui_payload(device_id, state="idle", emotion="sleeping")

    # -----------------------------------------------------------------------
    # Main Connection Handler for WebSocket Endpoint
    # -----------------------------------------------------------------------

    async def handle_client(self, websocket: WebSocket, device_id: str) -> None:
        """
        Handle WebSocket connection lifecycle for a Xiaozhi ESP32 device:
          - Manages binary audio stream from mic.
          - Processes control frames (barge-in, query_text, end_of_speech).
          - Handles state transitions and LCD sync.
        """
        await websocket.accept()
        client_host = websocket.client.host if websocket.client else "unknown"
        logger.info("[Xiaozhi] Thiết bị [%s] đã kết nối từ IP %s.", device_id, client_host)

        node = XiaozhiNode(device_id=device_id, websocket=websocket, client_host=client_host)
        async with self._lock:
            self._nodes[device_id] = node

        # Sync with global active_audio_nodes for server compatibility
        from core.server import active_audio_nodes
        active_audio_nodes[device_id] = {
            "websocket": websocket,
            "client_host": client_host,
            "connected_at": node.connected_at,
            "last_active": node.last_active,
            "state": node.state,
            "emotion": node.emotion,
        }

        # Send initial idle UI frame to LCD screen
        await self.send_ui_payload(device_id, state="idle", emotion="sleeping")

        try:
            while True:
                message = await websocket.receive()
                node.last_active = datetime.utcnow().isoformat()
                active_audio_nodes[device_id]["last_active"] = node.last_active

                # Frame nhị phân: Micro chunks từ ESP32
                if message.get("bytes") is not None:
                    raw_chunk: bytes = message["bytes"]
                    node.audio_buffer.write(raw_chunk)

                    # Phase 50 Step 1.2: Silero VAD real-time streaming analysis at 16kHz
                    vad_res = node.vad_detector.process_pcm16(raw_chunk, incoming_rate=16000)

                    if vad_res.get("speech_started") and node.state != "listening":
                        await self.send_ui_payload(device_id, state="listening", emotion="focused")

                    # Phase 50 Step 1.3: Dứt lời 500ms -> lập tức cắt luồng bytes và chuyển sang ASR In-Memory
                    if vad_res.get("speech_ended"):
                        audio_data = node.audio_buffer.getvalue()
                        node.audio_buffer = io.BytesIO()
                        node.vad_detector.reset()

                        if audio_data and len(audio_data) >= 1600:
                            logger.info(
                                "[Phase50 Silero VAD] Dứt lời sau 500ms im lặng trên [%s] (%d bytes) -> Chạy ASR tức thì!",
                                device_id, len(audio_data),
                            )
                            await self.send_ui_payload(device_id, state="listening", emotion="focused")
                            await websocket.send_text(json.dumps({"type": "asr_start"}))

                            transcribed: str = await audio_engine.transcribe_audio(audio_data)

                            if transcribed:
                                await websocket.send_text(json.dumps({
                                    "type": "asr_result",
                                    "text": transcribed,
                                }))
                                node.active_task = asyncio.create_task(
                                    self._execute_pipeline(node, transcribed)
                                )
                            else:
                                await websocket.send_text(json.dumps({
                                    "type": "asr_result",
                                    "text": "",
                                    "error": "ASR không nhận diện được giọng nói.",
                                }))
                                await self.send_ui_payload(device_id, state="idle", emotion="sleeping")

                # Frame văn bản: JSON điều khiển
                elif message.get("text") is not None:
                    raw_text: str = message["text"]
                    try:
                        ctrl: Dict[str, Any] = json.loads(raw_text)
                    except json.JSONDecodeError:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": "Invalid JSON control frame.",
                        }))
                        continue

                    msg_type: str = str(ctrl.get("type", "")).lower()
                    action: str = str(ctrl.get("action", "")).lower()
                    is_wakeup: bool = bool(ctrl.get("is_wakeup", False))

                    # -------------------------------------------------------
                    # Phase 52 Step 3: ToF Edge / Cliff Detection Interlock Alert
                    # -------------------------------------------------------
                    if msg_type == "alert" or ctrl.get("msg") == "edge_detected":
                        logger.warning("[Robotics Safety] CẢNH BÁO TOF: Phát hiện mép bàn/vực trên robot [%s]! Motor đã tự động ngắt điện.", device_id)
                        # Set alert face and text on OLED
                        await self.send_ui_payload(device_id, state="alert", emotion="shocked", text="Mép bàn! Đã phanh khẩn cấp.")
                        
                        # Broadcast alert to Web Portal & Standby HUD
                        try:
                            from core.server import broadcast_portal_ui, broadcast_hud
                            await broadcast_portal_ui("system_alert", {
                                "level": "WARNING",
                                "title": "ToF Safety Alert",
                                "message": f"Robot [{device_id}] phát hiện mép bàn! Motor đã dừng khẩn cấp.",
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                            await broadcast_hud({
                                "type": "system_log",
                                "level": "WARNING",
                                "message": f"[ROBOT SAFETY] ⚠️ Robot [{device_id}] gặp mép bàn! Đã ngắt điện motor.",
                                "timestamp": datetime.utcnow().isoformat(),
                            })
                        except Exception as b_err:
                            logger.debug("Broadcast error: %s", b_err)

                        # Synthesize voice warning to node
                        edge_text = "Dạ, phía trước là mép bàn, em không đi được nữa đâu ạ."
                        try:
                            edge_audio = await audio_engine.text_to_speech_bytes(edge_text)
                            if edge_audio:
                                asyncio.create_task(self._stream_audio_chunks(node, edge_audio))
                        except Exception as spk_err:
                            logger.debug("Edge alert voice error: %s", spk_err)
                        continue

                    # -------------------------------------------------------
                    # Phase 52 Step 4: Touch Sensor Wake (GPIO 17)
                    # -------------------------------------------------------
                    if msg_type == "touch" or action == "wake" or ctrl.get("event") == "touch_wake":
                        logger.info("[Robotics Touch] Cảm biến chạm GPIO 17 kích hoạt trên robot [%s]. Đánh thức robot!", device_id)
                        await self.send_ui_payload(device_id, state="listening", emotion="focused", text="Dạ, em nghe đây!")
                        node.audio_buffer = io.BytesIO()
                        node.vad_detector.reset()

                        # Fast reflex wake chime or voice filler
                        from core.audio_cache import get_cached_audio_bytes
                        wake_filler = "Dạ, em nghe đây ạ."
                        cached_bytes = get_cached_audio_bytes(wake_filler)
                        if cached_bytes:
                            asyncio.create_task(self._stream_audio_chunks(node, cached_bytes))
                        continue

                    # -------------------------------------------------------
                    # Step 2: Barge-in / Interruption Signal Check
                    # -------------------------------------------------------
                    if msg_type in ("interrupt", "abort") or action == "interrupt" or is_wakeup:
                        logger.info("[Xiaozhi] Tín hiệu ngắt lời (Barge-in) từ [%s]", device_id)
                        await self.handle_barge_in(device_id)
                        continue

                    # -------------------------------------------------------
                    # Micro speech completed -> Transcribe & Run Pipeline
                    # -------------------------------------------------------
                    if msg_type == "end_of_speech":
                        audio_data = node.audio_buffer.getvalue()
                        node.audio_buffer = io.BytesIO()

                        if not audio_data:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": "Không có dữ liệu âm thanh trong buffer.",
                            }))
                            continue

                        logger.info("[Xiaozhi] end_of_speech từ [%s] (%d bytes)", device_id, len(audio_data))
                        await self.send_ui_payload(device_id, state="listening", emotion="focused")
                        await websocket.send_text(json.dumps({"type": "asr_start"}))

                        transcribed: str = await audio_engine.transcribe_audio(audio_data)

                        if not transcribed:
                            await websocket.send_text(json.dumps({
                                "type": "asr_result",
                                "text": "",
                                "error": "ASR không nhận diện được giọng nói.",
                            }))
                            await self.send_ui_payload(device_id, state="idle", emotion="sleeping")
                            continue

                        await websocket.send_text(json.dumps({
                            "type": "asr_result",
                            "text": transcribed,
                        }))

                        # Spawn pipeline execution task
                        node.active_task = asyncio.create_task(
                            self._execute_pipeline(node, transcribed)
                        )

                    # -------------------------------------------------------
                    # Direct text query
                    # -------------------------------------------------------
                    elif msg_type == "query_text":
                        query_str = ctrl.get("text", "").strip()
                        if not query_str:
                            await websocket.send_text(json.dumps({
                                "type": "error",
                                "message": "'text' field is empty.",
                            }))
                            continue

                        node.active_task = asyncio.create_task(
                            self._execute_pipeline(node, query_str)
                        )

                    # -------------------------------------------------------
                    # Ping / Pong & Diagnostics
                    # -------------------------------------------------------
                    elif msg_type == "ping":
                        await websocket.send_text(json.dumps({
                            "type": "pong",
                            "timestamp": time.time(),
                        }))

                    elif msg_type == "clear_buffer":
                        node.audio_buffer = io.BytesIO()
                        await websocket.send_text(json.dumps({
                            "type": "ack",
                            "status": "buffer_cleared",
                        }))

                    elif msg_type == "ui":
                        # Client sending UI ack or custom state request
                        req_state = ctrl.get("state", "idle")
                        req_emotion = ctrl.get("emotion")
                        req_text = ctrl.get("text")
                        await self.send_ui_payload(device_id, state=req_state, emotion=req_emotion, text=req_text)

                    elif msg_type == "hello":
                        # Handshake thiết bị gửi ngay sau khi WebSocket mở
                        # (xem esp32_firmware/src/main.cpp, WStype_CONNECTED).
                        #
                        # Trước đây KHÔNG có nhánh xử lý 'hello', nên mọi lần thiết bị
                        # kết nối đều rơi vào nhánh else và nhận về
                        # {"type":"error","message":"Unknown control type: 'hello'"}.
                        # Firmware bỏ qua frame lạ nên lỗi này im lặng — nhưng server
                        # không bao giờ biết firmware version/tính năng, và thiết bị
                        # không nhận được phản hồi xác nhận handshake.
                        node.firmware_version = str(ctrl.get("version", "unknown"))[:32]
                        node.capabilities = str(ctrl.get("features", ""))[:256]
                        logger.info(
                            "[Xiaozhi] Handshake từ [%s]: firmware=%s, features=%s",
                            device_id, node.firmware_version, node.capabilities or "(không khai báo)",
                        )
                        node.state = "idle"
                        node.emotion = "happy"
                        active_audio_nodes[device_id]["firmware_version"] = node.firmware_version
                        active_audio_nodes[device_id]["capabilities"] = node.capabilities
                        await websocket.send_text(json.dumps({
                            "type": "hello_ack",
                            "device_id": device_id,
                            "message": "Handshake thành công.",
                            "server_time": datetime.utcnow().isoformat(),
                        }))
                        # Trả trạng thái idle để thiết bị đồng bộ OLED ngay
                        await self.send_ui_payload(device_id, state="idle", emotion="happy")

                    else:
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "message": f"Unknown control type: '{msg_type}'",
                        }))

        except WebSocketDisconnect:
            logger.info("[Xiaozhi] Thiết bị [%s] đã ngắt kết nối.", device_id)
        except Exception as exc:
            logger.error("[Xiaozhi] Lỗi ngoài dự kiến trên thiết bị [%s]: %s", device_id, exc)
        finally:
            async with self._lock:
                self._nodes.pop(device_id, None)
            from core.server import active_audio_nodes
            active_audio_nodes.pop(device_id, None)
            logger.info("[Xiaozhi] Đã dọn dẹp kết nối [%s]. Còn lại: %d thiết bị.", device_id, len(self._nodes))
            try:
                await websocket.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
xiaozhi_gateway = XiaozhiGateway()
