"""
tests/test_hud_voice_pipeline_behavior.py
=========================================
Kiểm tra HÀNH VI đường thoại HUD (không grep mã nguồn):

  1. Audio xuống HUD đúng thứ tự câu dù TTS câu sau xong trước câu trước.
  2. Không mất câu nào, kể cả câu cuối còn nằm trong hàng đợi.
  3. TTS lỗi/timeout -> vẫn gửi chữ của câu đó.
  4. Barge-in (huỷ task) -> không còn task TTS nào chạy dở.
  5. Vòng lặp LLM không chờ TTS từng câu (TTS chạy gối đầu).

LLM, TTS và kênh broadcast đều được thay bằng bản giả — không gọi mạng.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.server as server  # noqa: E402
from core.llm_engine import llm_engine  # noqa: E402

SENTENCES = [f"Đây là câu số {i} của câu trả lời." for i in range(1, 6)]
# Câu 1 sinh TTS chậm nhất, câu sau nhanh dần -> nếu phát theo thứ tự hoàn
# thành thay vì thứ tự câu thì audio sẽ bị đảo.
TTS_DELAY = {s: 0.25 - i * 0.05 for i, s in enumerate(SENTENCES)}


@pytest.fixture
def hud(monkeypatch):
    events: list = []
    llm_yield_times: list = []
    started: list = []
    cancelled: list = []

    async def fake_stream(query, history=None, source_device=None):
        for s in SENTENCES:
            llm_yield_times.append(asyncio.get_running_loop().time())
            yield s
            await asyncio.sleep(0)

    async def fake_tts_bytes(_engine, text, timeout_s=14.0):
        started.append(text)
        try:
            await asyncio.sleep(TTS_DELAY.get(text, 0.01))
        except asyncio.CancelledError:
            cancelled.append(text)
            raise
        if "lỗi" in text:
            return None
        return ("AUDIO:" + text).encode("utf-8") * 10

    async def fake_broadcast_hud(payload):
        events.append(("json", payload))

    async def fake_broadcast_binary(data):
        events.append(("bin", data))

    async def fake_portal(*_a, **_k):
        return None

    async def fake_thinking(*_a, **_k):
        return None

    monkeypatch.setattr(llm_engine, "stream_voice_response", fake_stream)
    monkeypatch.setattr(llm_engine, "last_voice_display_text", None, raising=False)
    monkeypatch.setattr(server, "_tts_bytes", fake_tts_bytes)
    # Thay cả engine canonical, để test đúng bất kể HUD gọi TTS qua đường nào.
    from core.audio.tts_stream_engine import TTSStreamEngine

    async def fake_synthesise(self, text, *a, **k):
        return await fake_tts_bytes(None, text)

    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synthesise)
    monkeypatch.setattr(server, "broadcast_hud", fake_broadcast_hud)
    monkeypatch.setattr(server, "broadcast_hud_binary", fake_broadcast_binary)
    monkeypatch.setattr(server, "broadcast_portal_ui", fake_portal)
    monkeypatch.setattr(server, "_broadcast_thinking", fake_thinking)

    from core.voice_session import voice_sessions
    session_id = "test-hud-behavior"
    voice_sessions.get(session_id).clear_expecting_reply()

    return {
        "events": events,
        "started": started,
        "cancelled": cancelled,
        "llm_yield_times": llm_yield_times,
        "session_id": session_id,
    }


def _spoken_audio(events):
    return [data.decode("utf-8").split("AUDIO:")[1] for kind, data in events if kind == "bin"]


def _spoken_text(events):
    return [
        p["text"] for kind, p in events
        if kind == "json" and p.get("status") == "speaking" and not p.get("is_filler")
    ]


async def test_audio_in_sentence_order_and_nothing_lost(hud):
    await server._process_hud_voice_command_body("câu hỏi thử", hud["session_id"])

    audio = [a[: len(SENTENCES[0])] for a in _spoken_audio(hud["events"])]
    assert audio == SENTENCES, f"audio sai thứ tự hoặc thiếu câu: {audio}"
    assert _spoken_text(hud["events"]) == SENTENCES


async def test_tts_runs_ahead_of_playback(hud):
    """Mọi câu đã được giao cho TTS trước khi câu đầu kịp phát xong."""
    await server._process_hud_voice_command_body("câu hỏi thử", hud["session_id"])
    # Câu 1 mất 0.25s; nếu TTS tuần tự thì câu 4 chỉ bắt đầu sau ~0.6s.
    # Gối đầu 3 câu -> 4 câu đầu được khởi động gần như cùng lúc.
    assert hud["started"][:4] == SENTENCES[:4]
    spread = hud["llm_yield_times"][3] - hud["llm_yield_times"][0]
    assert spread < 0.1, f"vòng lặp LLM bị TTS chặn: {spread:.3f}s"


async def test_tts_failure_still_sends_text(hud, monkeypatch):
    bad = "Câu này gây lỗi TTS."

    async def stream_with_bad(query, history=None, source_device=None):
        for s in [SENTENCES[0], bad, SENTENCES[1]]:
            yield s

    monkeypatch.setattr(llm_engine, "stream_voice_response", stream_with_bad)
    await server._process_hud_voice_command_body("câu hỏi thử", hud["session_id"])

    assert _spoken_text(hud["events"]) == [SENTENCES[0], bad, SENTENCES[1]]
    audio = [a[: len(SENTENCES[0])] for a in _spoken_audio(hud["events"])]
    assert audio == [SENTENCES[0], SENTENCES[1]]


async def test_barge_in_leaves_no_tts_task_running(hud):
    task = asyncio.create_task(
        server._process_hud_voice_command_body("câu hỏi thử", hud["session_id"])
    )
    # Đợi đến khi hàng đợi TTS đã có việc, rồi huỷ như khi người dùng ngắt lời.
    for _ in range(100):
        if len(hud["started"]) >= 3:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)  # quá thời gian TTS dài nhất

    unfinished = set(hud["started"]) - set(_spoken_audio_prefixes(hud["events"]))
    assert unfinished, "phải có câu bị huỷ giữa chừng thì test mới có ý nghĩa"
    assert set(hud["cancelled"]) == unfinished, (
        f"task TTS mồ côi: bắt đầu {hud['started']}, huỷ {hud['cancelled']}"
    )
    leftover = [
        t for t in asyncio.all_tasks()
        if t is not asyncio.current_task() and "fake_tts_bytes" in repr(t.get_coro())
    ]
    assert not leftover


def _spoken_audio_prefixes(events):
    return [a[: len(SENTENCES[0])] for a in _spoken_audio(events)]
