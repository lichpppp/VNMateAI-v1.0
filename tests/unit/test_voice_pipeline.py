"""
tests/unit/test_voice_pipeline.py
=================================
Unit Test Suite cho Bounded Context Voice Pipeline (Phase 4).
Kiểm tra SentenceBuffer, BargeInController, và Voice Use Cases.
"""

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mateai.domain.voice.entities import VoiceSession, VoiceState
from src.mateai.application.voice.sentence_buffer import SentenceBuffer, sanitize_text_for_speech
from src.mateai.application.voice.barge_in_controller import barge_in_controller
from src.mateai.application.voice.use_cases import ProcessVoiceTurnUseCase, InterruptVoiceSessionUseCase


def test_sanitize_text():
    print("\n▸ 1. Kiểm thử Làm Sạch Văn Bản Cho TTS (Text Sanitizer)")
    raw = "Chào bạn! ```python print('hello') ``` Xem thêm tại https://example.com # Header"
    cleaned = sanitize_text_for_speech(raw)
    assert "https://" not in cleaned
    assert "```" not in cleaned
    assert "em đã thực thi xong" in cleaned
    print(f"  ✅ Text sau khi làm sạch: '{cleaned}'")


def test_sentence_buffer_segmentation():
    print("\n▸ 2. Kiểm thử Phân Đoạn Câu SentenceBuffer")
    buffer = SentenceBuffer(min_soft_words=5, min_chars=6)
    
    # Feed tokens simulating streaming LLM
    tokens = ["Dạ, ", "em ", "chào ", "sếp ", "ạ. ", "Hôm ", "nay ", "thời ", "tiết ", "rất ", "đẹp! ", "Chúc ", "sếp "]
    collected = []
    for t in tokens:
        sentences = buffer.feed(t)
        if sentences:
            collected.extend(sentences)
            
    remaining = buffer.flush()
    if remaining:
        collected.append(remaining)

    assert len(collected) >= 2
    assert "Dạ, em chào sếp ạ." in collected[0] or "đẹp!" in collected[1]
    print(f"  ✅ Đã tách thành {len(collected)} câu hoàn chỉnh:")
    for i, s in enumerate(collected):
        print(f"     [{i+1}] {s}")


def test_barge_in_controller():
    print("\n▸ 3. Kiểm thử Ngắt Lời BargeInController (< 5ms)")
    session_id = "test-session-001"
    turn_1 = "turn-001"
    
    cancel_event = barge_in_controller.register_turn(session_id, turn_1)
    assert not cancel_event.is_set()
    assert not barge_in_controller.is_cancelled(session_id, turn_1)

    # Trigger interruption
    t0 = time.perf_counter()
    interruption = barge_in_controller.trigger_barge_in(session_id)
    latency_ms = (time.perf_counter() - t0) * 1000.0

    assert cancel_event.is_set()
    assert barge_in_controller.is_cancelled(session_id, turn_1)
    assert latency_ms < 5.0, f"Độ trễ ngắt lời quá cao: {latency_ms:.3f}ms"
    print(f"  ✅ Ngắt lời thành công trong {latency_ms:.4f}ms (< 5ms chuẩn), event đã kích hoạt.")


async def test_voice_use_cases():
    print("\n▸ 4. Kiểm thử Voice Use Cases (ProcessTurn & Interrupt)")
    session = VoiceSession(session_id="session-demo-use-case")
    
    # Mock fast router
    class MockFastRouter:
        async def dispatch(self, text, synthesize_audio=False):
            if "mấy giờ" in text:
                class Res:
                    command_name = "get_current_time"
                    reply_text = "Bây giờ là 21 giờ ạ."
                    latency_ms = 0.02
                return Res()
            return None

    use_case = ProcessVoiceTurnUseCase(fast_router=MockFastRouter())
    events = []
    async for ev in use_case.execute(session, "mấy giờ rồi", synthesize_audio=False):
        events.append(ev["event_type"])

    assert "status" in events
    assert "fast_path" in events
    assert "done" in events
    print(f"  ✅ ProcessVoiceTurnUseCase phát chuỗi sự kiện chuẩn: {events}")

    # Interrupt use case
    interrupt_use_case = InterruptVoiceSessionUseCase()
    cancel_ev = interrupt_use_case.execute(session, reason="user_interrupt")
    assert cancel_ev["event_type"] == "cancelled"
    assert session.state == VoiceState.INTERRUPTED
    print(f"  ✅ InterruptVoiceSessionUseCase chuyển trạng thái phiên sang INTERRUPTED an toàn.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT VOICE PIPELINE (PHASE 4)")
    print("=" * 65)
    test_sanitize_text()
    test_sentence_buffer_segmentation()
    test_barge_in_controller()
    asyncio.run(test_voice_use_cases())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ VOICE PIPELINE ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
