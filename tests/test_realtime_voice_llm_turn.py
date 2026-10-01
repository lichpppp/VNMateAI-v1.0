"""
tests/test_realtime_voice_llm_turn.py
=====================================
Chạy THẬT một lượt qua LLM của pipeline voice canonical
(`core.realtime_voice_ws._execute_voice_turn`), với LLM / TTS / WebSocket giả.

Lý do có test này: commit 4f6464a để lọt `NameError: sanitized_query` ở đường
LLM — mọi câu hỏi không phải lệnh nhanh đều lỗi — mà không test nào bắt được,
vì các test cũ chỉ đi đường fast path hoặc kiểm tra từng mảnh riêng lẻ.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.realtime_voice_ws as rvw  # noqa: E402
from core.llm_engine import llm_engine  # noqa: E402
from core.llm_provider import LLMStreamChunk  # noqa: E402

REPLY = "Dạ, RAID 1 ghi cùng dữ liệu lên hai ổ. Một ổ hỏng thì ổ kia vẫn còn nguyên."
SECRET_QUERY = "máy 192.168.1.27 mật khẩu password=Abc12345 bị chậm, giải thích giúp em"


class FakeWebSocket:
    def __init__(self):
        self.events: list[dict] = []
        self.binary: list[bytes] = []

    async def send_text(self, text: str) -> None:
        self.events.append(json.loads(text))

    async def send_bytes(self, data: bytes) -> None:
        self.binary.append(data)


@pytest.fixture
def turn(monkeypatch):
    seen_messages: list = []

    async def fake_stream(messages, tools=None, brain_role="voice", **kw):
        seen_messages.append(messages)
        for i in range(0, len(REPLY), 7):
            yield LLMStreamChunk(content=REPLY[i:i + 7])

    async def fake_tts_stream(self, text, *a, **k):
        yield ("MP3:" + text).encode("utf-8") * 20

    async def fake_synthesise(self, text, *a, **k):
        return ("MP3:" + text).encode("utf-8") * 20

    async def no_ack(*a, **k):
        return None

    monkeypatch.setattr(llm_engine, "stream", fake_stream)
    monkeypatch.setattr(llm_engine, "classify_intent",
                        staticmethod(lambda q: {"type": "conversation", "ack_needed": False}))
    from core.audio.tts_stream_engine import TTSStreamEngine
    monkeypatch.setattr(TTSStreamEngine, "stream", fake_tts_stream)
    monkeypatch.setattr(TTSStreamEngine, "synthesise", fake_synthesise)

    ws = FakeWebSocket()
    session = rvw.RealtimeVoiceSession(ws, {"username": "test-llm-turn"})
    return {"ws": ws, "session": session, "seen_messages": seen_messages}


async def _run(turn, query: str):
    trace = rvw.VoiceRequestTrace(request_id="req-llm-1", session_id=turn["session"].session_id)
    await rvw._execute_voice_turn(turn["session"], query, {}, trace)
    return [e["type"] for e in turn["ws"].events]


async def test_llm_turn_completes_without_error(turn):
    types = await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    errors = [e for e in turn["ws"].events if e["type"] == "error"]
    assert not errors, f"lượt LLM báo lỗi: {errors}"
    assert "text_delta" in types
    assert "session_ended" in types, f"lượt không kết thúc: {types}"
    text = "".join(e.get("text", "") for e in turn["ws"].events if e["type"] == "text_delta")
    assert text == REPLY


async def test_llm_turn_sends_audio(turn):
    await _run(turn, "Giải thích ngắn gọn RAID 1 là gì.")
    assert turn["ws"].binary, "không có frame audio nào được gửi"


async def test_sensitive_data_masked_before_llm(turn):
    await _run(turn, SECRET_QUERY)
    sent = turn["seen_messages"][0][-1]["content"]
    assert "Abc12345" not in sent, f"mật khẩu lọt sang LLM: {sent}"
    assert "192.168.1.27" not in sent, f"IP nội bộ lọt sang LLM: {sent}"
