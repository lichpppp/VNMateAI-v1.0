"""
core/voice_turn.py
==================
Xử lý MỘT lượt nói — implementation duy nhất cho mọi kênh voice (Phase 3).

Trước Phase 3 có 5 đường riêng (portal WS, HUD, ESP32/XiaoZhi, mic máy chủ,
REST), mỗi đường tự ghép lệnh nhanh / LLM / tool / TTS / lịch sử theo cách
riêng. Nay mỗi kênh chỉ còn phần transport + một `VoiceSink` (đầu ra), còn
nghiệp vụ nằm ở đây:

    lệnh nhanh (FastCommandRouter, không qua LLM)
      └─ không khớp →  câu đệm cho tác vụ cần tool (cache, trước khi gọi LLM)
                    →  LLMEngine.stream_voice_response
                         · trò chuyện: stream từng câu
                         · cần tool: vòng agent đầy đủ (ask_async) qua cổng
                           run_tool_with_policy (Zero-Trust, HITL, RBAC, audit)
                    →  StreamingTTSWorkerPipeline (TTS gối đầu, đúng thứ tự, huỷ được)
                    →  sink.on_audio(...)

Lịch sử hội thoại: memory_manager, khoá = session_id (ghi trong LLM layer cho
đường LLM, ghi ở đây cho lệnh nhanh).

Huỷ (barge-in): bên gọi chạy hàm này trong một task và `task.cancel()`; mọi
task TTS / lời đệm được dọn trong `finally`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class VoiceSink:
    """Đầu ra của một kênh. Mặc định không làm gì — kênh chỉ ghi đè cái cần."""

    async def on_status(self, status: str, **info: Any) -> None:
        """status: "thinking" | "speaking" | "done". `speaking` kèm reasoning khi có."""

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        """Một câu sẵn sàng (đã làm sạch). `display_text`: toàn bộ chữ để hiển thị tới lúc này."""

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        """Audio MP3 theo đúng thứ tự câu. kind: "speech" | "ack" | "filler".

        Với "speech", `audio` RỖNG khi TTS câu đó lỗi/timeout — vẫn được gọi để
        kênh hiển thị chữ đúng lúc (HUD: chữ bám theo tiếng)."""


@dataclass
class VoiceTurnResult:
    reply_text: str = ""
    display_text: str = ""
    reasoning: str = ""
    fast_command: Optional[str] = None
    used_agent: bool = False
    sentences: List[str] = field(default_factory=list)
    filler_played: bool = False
    pipeline_metrics: Dict[str, Any] = field(default_factory=dict)


async def process_voice_turn(
    query: str,
    *,
    sink: VoiceSink,
    session_id: str,
    source_device: Optional[str] = None,
    caller: Optional[str] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    fast_path: bool = True,
    pre_ack: bool = True,
    filler_after_s: Optional[float] = None,
    filler_text: Optional[Callable[[str], str]] = None,
) -> VoiceTurnResult:
    """
    Xử lý một lượt nói và đẩy kết quả ra `sink`.

    history=None → lấy từ memory_manager theo session_id.
    caller → danh tính RBAC/audit khi lượt cần chạy tool (mặc định source_device).
    filler_after_s → phát một lời đệm nếu chưa có câu trả lời sau ngần ấy giây
    (HUD 1s, mic máy chủ 18s); filler_text(query) chọn câu, mặc định câu đệm
    theo ngữ cảnh.
    """
    from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine, _get_tts_voice
    from mateai.application.voice.speech_text import shorten_for_speech
    from core.audio_cache import get_cached_audio_bytes

    result = VoiceTurnResult()
    t0 = time.perf_counter()

    # ── 1. Lệnh nhanh tất định ───────────────────────────────────────────
    if fast_path:
        from core.fast_command_router import fast_command_router
        fast_res = await fast_command_router.dispatch(query, synthesize_audio=False)
        if fast_res and fast_res.is_matched:
            reply = fast_res.reply_text
            result.fast_command = fast_res.command_name
            result.reply_text = result.display_text = reply
            result.sentences = [reply]
            await sink.on_status("speaking", fast_path=True)
            await sink.on_sentence(1, reply, reply, fast_path=True)
            audio = get_cached_audio_bytes(reply) or await get_tts_engine().synthesise(reply)
            if audio:
                await sink.on_audio(1, audio, reply, "speech", fast_path=True)
            from mateai.application.conversation.memory_manager import memory_manager
            memory_manager.add_turn(session_id, query, reply)
            await sink.on_status("done", fast_path=True)
            logger.info("[VoiceTurn] Lệnh nhanh '%s' xong sau %.0fms", fast_res.command_name,
                        (time.perf_counter() - t0) * 1000)
            return result

    await sink.on_status("thinking")

    # ── 2. Câu đệm cho tác vụ cần tool (từ cache, trước khi gọi LLM) ───────
    from mateai.application.agent.llm_engine import llm_engine
    acked = False
    if pre_ack:
        intent = llm_engine.classify_intent(query)
        if intent.get("ack_needed"):
            from mateai.infrastructure.tts.acoustic_ack_catalog import select_acoustic_ack
            from mateai.infrastructure.tts.acoustic_ack import get_acoustic_ack_audio
            ack_phrase = select_acoustic_ack(query, domain=intent.get("target_brain"))
            ack_audio = await get_acoustic_ack_audio(phrase=ack_phrase)
            if ack_audio:
                await sink.on_audio(0, ack_audio, ack_phrase, "ack")
                acked = True

    # ── 3. Lời đệm khi LLM chậm ───────────────────────────────────────────
    first_sentence = asyncio.Event()
    filler_task: Optional[asyncio.Task] = None

    async def _filler() -> None:
        await asyncio.sleep(filler_after_s or 0)
        if first_sentence.is_set():
            return
        if filler_text is not None:
            phrase = filler_text(query)
        else:
            from core.voice_controller import get_contextual_filler
            phrase = get_contextual_filler(query)
        audio = get_cached_audio_bytes(phrase) or await get_tts_engine().synthesise(phrase)
        if audio and not first_sentence.is_set():
            result.filler_played = True
            await sink.on_audio(0, audio, phrase, "filler")
            logger.info("[VoiceTurn] Phát lời đệm sau %.1fs: %s", filler_after_s, phrase)

    if filler_after_s is not None and not acked:
        filler_task = asyncio.create_task(_filler())

    # ── 4. LLM (stream / agent) → TTS gối đầu → sink ─────────────────────
    from mateai.infrastructure.tts.tts_queue_pipeline import StreamingTTSWorkerPipeline
    pipeline = StreamingTTSWorkerPipeline(voice=_get_tts_voice(), num_workers=2)
    pipeline.start()
    turn: Dict[str, Any] = {}

    async def _produce() -> None:
        seq = 0
        try:
            async for sentence in llm_engine.stream_voice_response(
                query=query,
                history=history,
                source_device=source_device,
                session_id=session_id,
                turn=turn,
                tool_ack=not acked,
                caller=caller,
            ):
                # Mảnh chỉ có dấu câu ("!", "--") không đọc được — TTS sẽ lỗi.
                if not sentence or not any(ch.isalnum() for ch in sentence):
                    continue
                if not first_sentence.is_set():
                    first_sentence.set()
                    if filler_task is not None and not filler_task.done():
                        filler_task.cancel()
                    await sink.on_status("speaking", reasoning=turn.get("reasoning", ""))
                seq += 1
                result.sentences.append(sentence)
                display = turn.get("display_text") or " ".join(result.sentences)
                await sink.on_sentence(seq, sentence, display)
                # Câu dài (thường là kết quả vòng agent) được rút gọn khi ĐỌC —
                # chữ hiển thị vẫn đầy đủ. Trước Phase 3 chỉ ESP32/HUD làm vậy.
                await pipeline.push_sentence(
                    sequence=seq, text=shorten_for_speech(sentence), request_id=session_id,
                )
        finally:
            await pipeline.mark_complete(seq)

    async def _consume() -> None:
        async for item in pipeline.iterate_audio_results():
            await sink.on_audio(item.sequence, item.audio_bytes or b"", item.text, "speech",
                                tts_latency_ms=item.tts_latency_ms)

    try:
        await asyncio.gather(_produce(), _consume())
    finally:
        pipeline.cancel()
        if filler_task is not None and not filler_task.done():
            filler_task.cancel()

    result.reply_text = " ".join(result.sentences)
    result.display_text = turn.get("display_text") or result.reply_text
    result.reasoning = turn.get("reasoning", "")
    result.used_agent = bool(turn.get("used_agent"))
    result.pipeline_metrics = dict(getattr(pipeline, "metrics", {}) or {})
    await sink.on_status("done")
    logger.info("[VoiceTurn] Xong lượt %s sau %.2fs (%d câu, agent=%s)", session_id,
                time.perf_counter() - t0, len(result.sentences), result.used_agent)
    return result
