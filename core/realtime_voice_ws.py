"""
core/realtime_voice_ws.py
=========================
Phase 1: Realtime Voice WebSocket Foundation (Jarvis / XiaoZhi Architecture).

Chức năng:
  - Cung cấp kênh WebSocket hai chiều chuẩn hóa cho Voice Session (/ws/voice & /ws/v1/voice-stream).
  - Định nghĩa Event Protocol có cấu trúc chặt chẽ (session_started, status, text_delta, sentence_ready, tool_start, tool_result, audio_start, session_ended...).
  - Quản lý vòng đời kết nối (Handshake, Ping/Pong, Disconnect cleanup).
  - Hỗ trợ truyền âm thanh nhị phân (Binary Frame) giảm tải Base64, đồng thời giữ chế độ tương thích ngược Base64 (legacy).
  - Quản lý hủy tác vụ (Task Cancellation / Barge-in) tức thời khi người dùng ngắt lời hoặc phát lệnh mới.
  - Đo lường và gắn nhãn độ trễ chi tiết (Trace ID, TTFD, TTFT, TTFA, TTL).

LƯU Ý: Phase 1 CHỈ triển khai nền tảng WebSocket, KHÔNG can thiệp chỉnh sửa logic LLM hay TTS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Set

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Metric Tracing Tracker
# ---------------------------------------------------------------------------

@dataclass
class VoiceRequestTrace:
    """Theo dõi độ trễ từng chặng của một yêu cầu giọng nói."""
    request_id: str
    session_id: str
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex[:12]}")
    t_received: float = field(default_factory=time.monotonic)
    t_first_display: Optional[float] = None
    t_first_token: Optional[float] = None
    t_first_audio: Optional[float] = None
    t_completed: Optional[float] = None
    status_history: list[str] = field(default_factory=list)

    def mark_status(self, status: str) -> None:
        self.status_history.append(f"{status}@{int((time.monotonic() - self.t_received) * 1000)}ms")

    def mark_first_display(self) -> int:
        if self.t_first_display is None:
            self.t_first_display = time.monotonic()
        return int((self.t_first_display - self.t_received) * 1000)

    def mark_first_token(self) -> int:
        if self.t_first_token is None:
            self.t_first_token = time.monotonic()
        return int((self.t_first_token - self.t_received) * 1000)

    def mark_first_audio(self) -> int:
        if self.t_first_audio is None:
            self.t_first_audio = time.monotonic()
        return int((self.t_first_audio - self.t_received) * 1000)

    def mark_completed(self) -> Dict[str, Any]:
        self.t_completed = time.monotonic()
        ttl_ms = int((self.t_completed - self.t_received) * 1000)
        ttfd_ms = int((self.t_first_display - self.t_received) * 1000) if self.t_first_display else None
        ttft_ms = int((self.t_first_token - self.t_received) * 1000) if self.t_first_token else None
        ttfa_ms = int((self.t_first_audio - self.t_received) * 1000) if self.t_first_audio else None

        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "ttfd_ms": ttfd_ms,
            "ttft_ms": ttft_ms,
            "ttfa_ms": ttfa_ms,
            "ttl_ms": ttl_ms,
            "status_steps": self.status_history,
        }


# ---------------------------------------------------------------------------
# Session Manager & Active Task Cancellation Registry
# ---------------------------------------------------------------------------

class RealtimeVoiceSession:
    """Đại diện cho một kết nối WebSocket Realtime của người dùng."""

    def __init__(self, websocket: WebSocket, user_info: dict) -> None:
        self.websocket = websocket
        self.user_info = user_info
        self.session_id: str = str(user_info.get("sub") or user_info.get("username") or f"sess_{uuid.uuid4().hex[:8]}")
        self.active_task: Optional[asyncio.Task] = None
        self.is_connected: bool = True
        self.legacy_base64: bool = False
        self._lock = asyncio.Lock()

    async def send_event(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Gửi gói tin sự kiện JSON (Text Frame) tới client an toàn."""
        if not self.is_connected:
            return False
        payload = {"type": event_type, "timestamp": time.time()}
        if data:
            payload.update(data)
        try:
            async with self._lock:
                await self.websocket.send_text(json.dumps(payload, ensure_ascii=False))
            return True
        except (WebSocketDisconnect, ConnectionResetError):
            self.is_connected = False
            return False
        except Exception as exc:
            logger.debug("[RealtimeVoiceWS] Lỗi gửi event %s: %s", event_type, exc)
            return False

    async def send_binary(self, binary_data: bytes) -> bool:
        """Gửi frame dữ liệu nhị phân (Binary Frame - Raw Audio) tới client."""
        if not self.is_connected or not binary_data:
            return False
        try:
            async with self._lock:
                await self.websocket.send_bytes(binary_data)
            return True
        except (WebSocketDisconnect, ConnectionResetError):
            self.is_connected = False
            return False
        except Exception as exc:
            logger.debug("[RealtimeVoiceWS] Lỗi gửi binary frame: %s", exc)
            return False

    def cancel_active_turn(self, reason: str = "barge_in") -> bool:
        """Hủy tác vụ đang chạy nếu người dùng ngắt lời hoặc gửi lệnh mới."""
        if self.active_task and not self.active_task.done():
            logger.info("[RealtimeVoiceWS] Hủy tác vụ hiện tại [%s] do: %s", self.session_id, reason)
            self.active_task.cancel()
            return True
        return False


class RealtimeVoiceRegistry:
    """Quản lý danh sách các phiên WebSocket Voice đang hoạt động."""

    def __init__(self) -> None:
        self._sessions: Dict[str, RealtimeVoiceSession] = {}
        self._lock = asyncio.Lock()

    async def register(self, session: RealtimeVoiceSession) -> None:
        async with self._lock:
            # Nếu session_id cũ còn task đang chạy, hủy trước khi mở kết nối mới
            old = self._sessions.get(session.session_id)
            if old:
                old.cancel_active_turn(reason="new_session_connected")
            self._sessions[session.session_id] = session
        logger.info("[RealtimeVoiceWS] Đã đăng ký session: %s (Tổng: %d)", session.session_id, len(self._sessions))

    async def unregister(self, session_id: str) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.cancel_active_turn(reason="session_disconnected")
                session.is_connected = False
        logger.info("[RealtimeVoiceWS] Đã hủy đăng ký session: %s", session_id)

    def get(self, session_id: str) -> Optional[RealtimeVoiceSession]:
        return self._sessions.get(session_id)


# Global Singleton Registry
voice_ws_registry = RealtimeVoiceRegistry()


# ---------------------------------------------------------------------------
# WebSocket Main Loop Handler (Phase 1 Foundation)
# ---------------------------------------------------------------------------

async def handle_realtime_voice_endpoint(
    websocket: WebSocket,
    user: dict,
) -> None:
    """
    Điểm vào chính của WebSocket Realtime Voice (/ws/voice và /ws/v1/voice-stream).
    Thực hiện:
      1. Khởi tạo phiên kết nối chuẩn hóa.
      2. Lắng nghe thông điệp, điều phối sự kiện theo Protocol chuẩn.
      3. Xử lý Ping/Pong, Barge-in Cancellation, và gọi Voice Engine.
      4. Thu thập số liệu đo lường TTFD, TTFT, TTFA, TTL.
    """
    await websocket.accept()
    session = RealtimeVoiceSession(websocket=websocket, user_info=user)
    await voice_ws_registry.register(session)

    # Báo hiệu phiên đã sẵn sàng
    await session.send_event("session_started", {
        "session_id": session.session_id,
        "protocol_version": "1.0",
        "supported_features": ["binary_audio", "base64_fallback", "cancellation", "latency_metrics"],
    })
    await session.send_event("status", {"status": "idle"})

    try:
        while session.is_connected:
            try:
                # Nhận thông điệp dạng text hoặc JSON từ client
                raw_text = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.debug("[RealtimeVoiceWS] Disconnect hoặc lỗi đọc socket: %s", e)
                break

            if not raw_text.strip():
                continue

            try:
                msg = json.loads(raw_text)
            except json.JSONDecodeError:
                await session.send_event("error", {"code": "INVALID_JSON", "message": "Payload JSON không hợp lệ."})
                continue

            msg_type = (msg.get("type") or "query").lower()

            # ── 1. Heartbeat Ping / Pong ──
            if msg_type == "ping":
                await session.send_event("pong", {"client_time": msg.get("time")})
                continue

            # ── 2. Barge-in / Cancel Request ──
            if msg_type in ("cancel_request", "barge_in", "stop"):
                req_id = msg.get("request_id")
                session.cancel_active_turn(reason=f"client_{msg_type}")
                await session.send_event("cancelled", {
                    "request_id": req_id,
                    "reason": "Yêu cầu đã được hủy theo lệnh người dùng.",
                })
                await session.send_event("status", {"status": "idle"})
                continue

            # ── 3. Voice Query Request ──
            query = (msg.get("query") or msg.get("text") or "").strip()
            if not query:
                await session.send_event("error", {"code": "EMPTY_QUERY", "message": "Trường 'query' không được để trống."})
                continue

            request_id = str(msg.get("request_id") or uuid.uuid4().hex[:10])
            trace = VoiceRequestTrace(request_id=request_id, session_id=session.session_id)
            if msg.get("format") == "base64" or msg.get("legacy_audio") is True:
                session.legacy_base64 = True

            # Hủy câu lệnh trước đó nếu người dùng đang nói tiếp câu mới (Barge-In)
            session.cancel_active_turn(reason="new_query_arrived")

            # Chạy pipeline xử lý câu lệnh trong một async Task riêng biệt
            # để vòng lặp WebSocket vẫn tiếp nhận được lệnh 'cancel_request' hoặc 'ping'
            session.active_task = asyncio.create_task(
                _execute_voice_turn(session, query, msg, trace)
            )

    finally:
        await voice_ws_registry.unregister(session.session_id)


async def _execute_voice_turn(
    session: RealtimeVoiceSession,
    query: str,
    payload: Dict[str, Any],
    trace: VoiceRequestTrace,
) -> None:
    """
    Thực thi 1 lượt tương tác giọng nói với đầy đủ các sự kiện của Protocol.
    NOTE (Phase 1): Sử dụng logic voice hiện tại mà không sửa đổi LLM hay TTS.
    """
    request_id = trace.request_id
    try:
        # Báo nhận lệnh & chuyển trạng thái
        trace.mark_status("routing")
        await session.send_event("status", {"status": "routing", "request_id": request_id})
        trace.mark_first_display()

        # Phase 5: Fast Command Router (Tất định không qua LLM, phản hồi tức thì < 1ms)
        from core.fast_command_router import fast_command_router
        fast_res = await fast_command_router.dispatch(query, synthesize_audio=False)
        if fast_res and fast_res.is_matched:
            trace.mark_status("fast_path")
            trace.mark_first_token()
            trace.mark_first_display()
            await session.send_event("status", {"status": "speaking", "request_id": request_id, "fast_path": True})

            # Hiển thị text_delta ngay lập tức (< 1ms visual latency)
            await session.send_event("text_delta", {
                "request_id": request_id,
                "text": fast_res.reply_text,
                "content": fast_res.reply_text,
                "is_fast_path": True,
            })
            await session.send_event("sentence_ready", {
                "request_id": request_id,
                "sequence": 1,
                "text": fast_res.reply_text,
            })

            # Lấy âm thanh từ RAM cache (0ms) hoặc tổng hợp nhanh
            from core.audio_cache import get_cached_audio_bytes
            from core.audio.tts_stream_engine import get_tts_engine
            audio_bytes = get_cached_audio_bytes(fast_res.reply_text)
            if not audio_bytes:
                tts_engine = get_tts_engine()
                audio_bytes = await tts_engine.synthesise(fast_res.reply_text)

            # Phát Audio
            trace.mark_first_audio()
            await session.send_event("audio_start", {
                "sequence": 1,
                "request_id": request_id,
                "format": "mp3",
                "text": fast_res.reply_text,
                "is_fast_path": True,
            })
            if audio_bytes:
                from core.audio.binary_transport import dispatch_binary_audio
                await dispatch_binary_audio(
                    session=session,
                    audio_bytes=audio_bytes,
                    sequence=1,
                    request_id=request_id,
                )

            # Lưu lịch sử hội thoại
            from core.memory_manager import memory_manager
            memory_manager.add_turn(session.session_id, query, fast_res.reply_text)

            # Hoàn tất phiên tương tác
            trace.mark_status("done")
            await session.send_event("status", {"status": "done", "request_id": request_id})
            metrics = trace.mark_completed()
            metrics["fast_path"] = {
                "command": fast_res.command_name,
                "execution_latency_ms": fast_res.latency_ms,
            }

            await session.send_event("audio_stream_complete", {
                "request_id": request_id,
                "metrics": metrics,
                "ttfa_ms": metrics.get("ttfa_ms"),
            })
            await session.send_event("session_ended", {
                "request_id": request_id,
                "metrics": metrics,
            })
            logger.info(
                "[RealtimeVoiceWS/FastPath] Hoàn tất lệnh nhanh '%s' trong %.1fms (TTFT=%sms, TTFA=%sms, TTL=%sms)",
                fast_res.command_name, fast_res.latency_ms, metrics.get("ttft_ms"), metrics.get("ttfa_ms"), metrics.get("ttl_ms")
            )
            return

        trace.mark_status("thinking")
        await session.send_event("status", {"status": "thinking", "request_id": request_id})

        from core.llm_engine import llm_engine, build_system_prompt
        from core.safety_guard import security_engine
        from core.memory_manager import memory_manager
        from core.audio.sentence_buffer import SentenceBuffer
        from core.audio.tts_queue_pipeline import StreamingTTSWorkerPipeline
        from core.audio.streaming_tts_pipeline import (
            get_acoustic_ack_audio,
            _sanitise_for_tts,
            _get_tts_voice,
        )

        # Phân loại ý định qua Bộ Não Kiểm Soát
        intent = llm_engine.classify_intent(query)
        if intent["type"] == "conversation":
            tools = None
            active_brain = "voice"
        else:
            # Phase 7: Tool Schema Pruning — chỉ nạp tối đa 5 công cụ liên quan nhất
            from core.agent_voice_loop import prune_tool_schemas
            tools = prune_tool_schemas(query, max_tools=5)
            active_brain = "ops"

        # Nếu cần Acoustic ACK (câu đệm tức thì cho tác vụ kỹ thuật):
        if intent.get("ack_needed"):
            from core.audio.acoustic_ack_catalog import select_acoustic_ack
            ack_phrase = select_acoustic_ack(query, domain=intent.get("target_brain"))
            ack_audio = await get_acoustic_ack_audio(phrase=ack_phrase)
            if ack_audio:
                trace.mark_first_audio()
                await session.send_event("audio_start", {
                    "sequence": 0,
                    "is_ack": True,
                    "format": "mp3",
                    "text": ack_phrase,
                    "request_id": request_id,
                })
                # Phase 11: Binary Transport trực tiếp (Zero Base64 overhead)
                from core.audio.binary_transport import dispatch_binary_audio
                await dispatch_binary_audio(
                    session=session,
                    audio_bytes=ack_audio,
                    sequence=0,
                    is_ack=True,
                    request_id=request_id,
                )

        # Xây dựng ngữ cảnh hội thoại
        system_content = build_system_prompt()
        # Phase 9: History & Context Pruning cho Voice (Sliding Window + Nén lược bỏ bảng/code rác)
        from core.history_pruner import prune_history_for_voice
        raw_history = payload.get("history") or memory_manager.get_history(session.session_id)
        pruned_history = prune_history_for_voice(raw_history, max_turns=4, max_total_chars=1200)

        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        if pruned_history:
            messages.extend(pruned_history)
        messages.append({"role": "user", "content": sanitized_query})

        # Phase 4: Khởi chạy Streaming TTS Worker Pipeline gối đầu
        pipeline = StreamingTTSWorkerPipeline(voice=_get_tts_voice(), num_workers=2)
        pipeline.start()
        sentence_buffer = SentenceBuffer(min_chars=8)
        full_reply_text = ""
        first_token = True

        async def _stream_llm_and_feed_sentences() -> None:
            nonlocal first_token, full_reply_text
            seq = 0
            pending_tool_calls: Dict[int, dict] = {}

            # ── VÒNG 1 (ROUND 1): Stream LLM và thu thập Tool Calls ──
            async for chunk in llm_engine.stream(messages, tools if tools else None, brain_role=active_brain):
                if first_token:
                    trace.mark_first_token()
                    first_token = False
                    trace.mark_status("speaking")
                    await session.send_event("status", {"status": "speaking", "request_id": request_id})

                # Thu thập tool_calls từ stream
                if getattr(chunk, "tool_calls", None):
                    for tc in chunk.tool_calls:
                        idx = tc.get("index", 0)
                        if idx not in pending_tool_calls:
                            pending_tool_calls[idx] = {
                                "id": tc.get("id") or f"call_{idx}",
                                "name": tc.get("name") or "",
                                "arguments": "",
                            }
                        if tc.get("name"):
                            pending_tool_calls[idx]["name"] = tc["name"]
                        if tc.get("arguments"):
                            pending_tool_calls[idx]["arguments"] += tc["arguments"]

                token = chunk.content
                if token:
                    full_reply_text += token
                    # Phát sự kiện text_delta chuẩn cho UI render realtime (cả text và content)
                    await session.send_event("text_delta", {
                        "request_id": request_id,
                        "text": token,
                        "content": token,
                    })

                    # Ngắt câu thông minh tiếng Việt (Phase 3 SentenceBuffer)
                    ready_sentences = sentence_buffer.add_token(token)
                    for sent in ready_sentences:
                        seq += 1
                        await session.send_event("sentence_ready", {
                            "request_id": request_id,
                            "sequence": seq,
                            "text": sent,
                        })
                        await pipeline.push_sentence(
                            sequence=seq,
                            text=sent,
                            request_id=request_id,
                        )

            # ── XỬ LÝ TOOL CALLS (PHASE 7 OPTIMIZATION) ──
            if pending_tool_calls:
                from core.agent_voice_loop import execute_tool_call, prune_tool_payload_for_llm

                tool_list = list(pending_tool_calls.values())
                # Báo hiệu UI bắt đầu thực thi tools
                for tc in tool_list:
                    await session.send_event("tool_start", {"tool": tc["name"], "request_id": request_id})

                # Thực thi các tool SONG SONG
                tool_results = await asyncio.gather(
                    *[execute_tool_call(tc, session.user_info) for tc in tool_list],
                    return_exceptions=False,
                )

                for tr in tool_results:
                    await session.send_event("tool_result", {
                        "tool": tr.tool_name,
                        "success": tr.success,
                        "request_id": request_id,
                    })

                # Kiểm tra Direct Response Synthesis (Bỏ qua LLM Round 2 nếu kết quả tự giải thích)
                all_direct = all(tr.direct_response is not None for tr in tool_results)
                if all_direct:
                    logger.info("[AgentVoiceLoop] Kích hoạt Direct Synthesis — Bỏ qua LLM Round 2 (~2.5s độ trễ saved)!")
                    for tr in tool_results:
                        direct_text = tr.direct_response
                        if direct_text:
                            full_reply_text += (" " if full_reply_text else "") + direct_text
                            await session.send_event("text_delta", {
                                "request_id": request_id,
                                "text": direct_text,
                                "content": direct_text,
                            })
                            seq += 1
                            await session.send_event("sentence_ready", {
                                "request_id": request_id,
                                "sequence": seq,
                                "text": direct_text,
                            })
                            await pipeline.push_sentence(
                                sequence=seq,
                                text=direct_text,
                                request_id=request_id,
                            )
                else:
                    # Kết quả phức tạp -> Chạy LLM Round 2 có giới hạn (Cắt gọt payload)
                    logger.info("[AgentVoiceLoop] Kết quả dữ liệu phức tạp — Chạy LLM Round 2 với payload thu gọn...")
                    messages.append({
                        "role": "assistant",
                        "content": full_reply_text or "",
                        "tool_calls": [
                            {
                                "id": tc.get("id"),
                                "type": "function",
                                "function": {
                                    "name": tc.get("name", ""),
                                    "arguments": tc.get("arguments", "{}"),
                                },
                            }
                            for tc in tool_list
                        ],
                    })

                    for tr in tool_results:
                        pruned_str = prune_tool_payload_for_llm(tr.data if tr.success else tr.error)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tr.tool_call_id,
                            "content": security_engine.mask_sensitive_data(pruned_str),
                        })

                    # Stream LLM Round 2 (Không truyền tools nữa để tránh vòng lặp lần 3)
                    async for chunk2 in llm_engine.stream(messages, tools=None, brain_role=active_brain):
                        token2 = chunk2.content
                        if token2:
                            full_reply_text += token2
                            await session.send_event("text_delta", {
                                "request_id": request_id,
                                "text": token2,
                                "content": token2,
                            })
                            for sent2 in sentence_buffer.add_token(token2):
                                seq += 1
                                await session.send_event("sentence_ready", {
                                    "request_id": request_id,
                                    "sequence": seq,
                                    "text": sent2,
                                })
                                await pipeline.push_sentence(
                                    sequence=seq,
                                    text=sent2,
                                    request_id=request_id,
                                )

            # Flush các câu còn lại trong buffer khi LLM kết thúc token
            remaining = sentence_buffer.flush()
            for sent in remaining:
                seq += 1
                await session.send_event("sentence_ready", {
                    "request_id": request_id,
                    "sequence": seq,
                    "text": sent,
                })
                await pipeline.push_sentence(
                    sequence=seq,
                    text=sent,
                    request_id=request_id,
                )

            # Báo hiệu pipeline đã đẩy hết tất cả các câu
            await pipeline.mark_complete(seq)

        async def _consume_and_stream_audio() -> None:
            async for audio_item in pipeline.iterate_audio_results():
                if not audio_item.audio_bytes:
                    continue

                if not trace.t_first_audio:
                    trace.mark_first_audio()

                await session.send_event("audio_start", {
                    "sequence": audio_item.sequence,
                    "request_id": request_id,
                    "format": "mp3",
                    "text": audio_item.text,
                    "tts_latency_ms": audio_item.tts_latency_ms,
                })
                # Phase 11: Binary Transport trực tiếp
                from core.audio.binary_transport import dispatch_binary_audio
                await dispatch_binary_audio(
                    session=session,
                    audio_bytes=audio_item.audio_bytes,
                    sequence=audio_item.sequence,
                    request_id=request_id,
                )

        try:
            await asyncio.gather(
                _stream_llm_and_feed_sentences(),
                _consume_and_stream_audio(),
            )
        finally:
            pipeline.cancel()

        # Lưu lịch sử hội thoại
        if full_reply_text.strip():
            clean_display = _sanitise_for_tts(full_reply_text)
            memory_manager.add_turn(session.session_id, query, clean_display)

        # Hoàn tất phiên tương tác và gửi metrics
        trace.mark_status("done")
        await session.send_event("status", {"status": "done", "request_id": request_id})
        metrics = trace.mark_completed()
        metrics["pipeline"] = pipeline.metrics

        # Báo hiệu kết thúc cho cả giao diện web mới và cũ
        await session.send_event("audio_stream_complete", {
            "request_id": request_id,
            "metrics": metrics,
            "ttfa_ms": metrics.get("ttfa_ms"),
        })
        await session.send_event("session_ended", {
            "request_id": request_id,
            "metrics": metrics,
        })
        logger.info(
            "[RealtimeVoiceWS] Lượt tương tác hoàn tất: %s (TTFA=%sms, TTL=%sms, Sentences=%d, PeakQueue=%d)",
            request_id,
            metrics.get("ttfa_ms"),
            metrics.get("ttl_ms"),
            pipeline.metrics.get("sentences_processed", 0),
            pipeline.metrics.get("queue_depth_peak", 0),
        )

    except asyncio.CancelledError:
        logger.info("[RealtimeVoiceWS] Tác vụ lượt [%s] đã bị hủy (Barge-In).", request_id)
        await session.send_event("cancelled", {"request_id": request_id})
    except Exception as exc:
        logger.error("[RealtimeVoiceWS] Lỗi xử lý lượt [%s]: %s", request_id, exc, exc_info=True)
        await session.send_event("error", {
            "code": "INTERNAL_ERROR",
            "message": f"Lỗi xử lý giọng nói: {str(exc)[:150]}",
            "request_id": request_id,
        })
    finally:
        await session.send_event("status", {"status": "idle"})
