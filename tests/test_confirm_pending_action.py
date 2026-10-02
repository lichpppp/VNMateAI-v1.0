"""
tests/test_confirm_pending_action.py
====================================
Luồng "Đồng ý" tiếp tục một tác vụ đang chờ phê duyệt (LLMEngine.ask_async).

Trước Phase 6, tác vụ được duyệt chạy bằng
`asyncio.to_thread(plugin_manager.execute_skill, ...)` — nhưng execute_skill là
hàm async, nên to_thread trả về một coroutine chưa chạy và dòng sau gọi .get()
trên nó: tác vụ đã duyệt KHÔNG BAO GIỜ chạy. Nay tác vụ đi qua cổng chung
run_tool_with_policy (RBAC + audit), kèm confirmed=True. Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.llm_engine import llm_engine  # noqa: E402
from core.state_manager import state_manager  # noqa: E402


async def test_confirm_runs_the_approved_tool_through_the_gate(monkeypatch):
    import core.agent_voice_loop as avl

    gate_calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        gate_calls.append((fn_name, dict(fn_args), kw))
        return {"target_client": "master", "args": fn_args,
                "result": {"success": True, "data": {"status": "ok"}, "error": None}}

    async def fake_call_llm(messages, tools=None, brain_role="controller"):
        msg = SimpleNamespace(content="Dạ, em đã dừng tiến trình theo yêu cầu.", tool_calls=None,
                              reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(avl, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)

    caller = "test-confirm-device"
    state_manager.save_pending_action(
        user_id=caller, tool_name="kill_process", arguments={"pid": 4242},
        target_client="master", query="dừng tiến trình 4242",
        chat_id=None, source_device=caller,
    )
    res = await llm_engine.ask_async("đồng ý", source_device=caller, session_id="t-confirm")

    assert gate_calls, f"tác vụ đã duyệt không được thực thi: {res}"
    name, args, kw = gate_calls[0]
    assert name == "kill_process" and args["pid"] == 4242 and args["confirmed"] is True
    assert kw["caller"] == caller


async def test_confirm_finds_action_saved_under_logged_in_caller(monkeypatch):
    """Portal: cổng lưu pending theo username (caller), source_device="portal".

    Trước đây ask_async tra pending theo source_device nên "Đồng ý" không bao
    giờ tìm thấy tác vụ của người dùng đã đăng nhập.
    """
    import core.agent_voice_loop as avl

    gate_calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        gate_calls.append((fn_name, kw))
        return {"target_client": "master", "args": fn_args,
                "result": {"success": True, "data": {}, "error": None}}

    async def fake_call_llm(messages, tools=None, brain_role="controller"):
        msg = SimpleNamespace(content="Dạ xong.", tool_calls=None, reasoning="", reasoning_content="")
        return SimpleNamespace(model="fake", choices=[SimpleNamespace(message=msg, finish_reason="stop")])

    monkeypatch.setattr(avl, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", fake_call_llm)

    state_manager.save_pending_action(
        user_id="alice-portal", tool_name="kill_process", arguments={"pid": 7},
        target_client="master", query="dừng 7", chat_id=None, source_device="portal",
    )
    await llm_engine.ask_async("đồng ý", source_device="portal", session_id="t-c2",
                               caller="alice-portal")
    assert gate_calls and gate_calls[0][0] == "kill_process"
    assert gate_calls[0][1]["caller"] == "alice-portal"


async def test_rest_voice_command_uses_logged_in_user_not_source_device(monkeypatch):
    """source_device do client tự khai ("hud") không được quyết định RBAC."""
    import core.server as server

    seen = {}

    async def fake_ask_async(**kw):
        seen.update(kw)
        return {"reply": "ok", "route_info": {}}

    async def _noop(*_a, **_k):
        return None

    monkeypatch.setattr(llm_engine, "ask_async", fake_ask_async)
    monkeypatch.setattr(server, "broadcast_hud", _noop)
    monkeypatch.setattr(server, "_broadcast_thinking", _noop)

    payload = server.VoiceCommandRequest(query="tắt máy", source_device="hud", include_audio=False)
    await server.voice_command(payload, user={"username": "bob-manager", "role": "manager"})
    assert seen["caller"] == "bob-manager"
    assert seen["source_device"] == "hud"
