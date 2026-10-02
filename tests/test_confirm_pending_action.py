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
