"""
tests/test_tool_policy_gate.py
==============================
Cổng thực thi tool chung `mateai.application.agent.tool_gate.run_tool_with_policy`.

Trước Phase 3, đường voice realtime (portal) gọi tool qua một bản riêng import
`zero_trust.evaluate_risk` — hàm không tồn tại — rồi nuốt lỗi, nên mọi tool chạy
KHÔNG qua Zero-Trust, RBAC hay audit. Nay mọi kênh đi qua vòng agent `ask_async`,
và vòng đó chỉ chạy tool qua cổng này. Không chạy skill thật.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import mateai.application.agent.tool_gate as avl  # noqa: E402


@pytest.fixture
def gate(monkeypatch):
    calls = {"executed": [], "audit": [], "rbac": []}
    state = {"risk": "SAFE", "rbac_ok": True}

    import core.zero_trust as zt
    import core.security_guard as sg
    import core.plugin_manager as pm
    import core.plugin_registry as pr

    monkeypatch.setattr(zt, "evaluate_action_risk", lambda name, args=None: state["risk"])

    def fake_check(tool_name, employee_id, session_id=None, payload=None):
        calls["rbac"].append((tool_name, employee_id))
        return state["rbac_ok"], "không đủ quyền"

    monkeypatch.setattr(sg.security_guard, "check_permission", fake_check)

    async def fake_exec(name, args):
        calls["executed"].append((name, args))
        return {"success": True, "data": {"status": "ok"}, "error": None}

    monkeypatch.setattr(pm.plugin_manager, "execute_skill", fake_exec)
    monkeypatch.setattr(pr.plugin_registry, "get_tool_names", lambda: [])
    monkeypatch.setattr(avl.security_engine, "log_audit",
                        lambda *a, **k: calls["audit"].append(a[1:3]))
    import core.state_manager as smod
    monkeypatch.setattr(smod.state_manager, "save_pending_action", lambda **kw: None)
    return calls, state


async def test_blocked_tool_never_executes(gate):
    calls, state = gate
    state["risk"] = "BLOCKED"
    out = await avl.run_tool_with_policy("format_disk", {}, caller="admin", source_device="portal")
    assert calls["executed"] == []
    assert "chính sách bảo mật" in out["result"]["message"]
    assert ("format_disk", "BLOCKED") in calls["audit"]


async def test_rbac_uses_logged_in_user_and_denies(gate):
    calls, state = gate
    state["rbac_ok"] = False
    out = await avl.run_tool_with_policy("kill_process", {"pid": 1}, caller="viewer_user",
                                         source_device="portal")
    assert calls["rbac"] == [("kill_process", "viewer_user")]
    assert calls["executed"] == []
    assert out["result"]["code"] == "RBAC_DENIED"


async def test_need_confirm_from_non_admin_channel_waits_for_approval(gate):
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    out = await avl.run_tool_with_policy("kill_process", {"pid": 1},
                                         caller="someone", source_device="web-widget")
    assert out["result"]["status"] == "need_confirm"
    assert calls["executed"] == []


async def test_safe_tool_runs_and_is_audited(gate):
    calls, _ = gate
    out = await avl.run_tool_with_policy("get_cpu", {}, caller="admin", source_device="portal")
    assert calls["executed"] == [("get_cpu", {})]
    assert out["result"]["success"] is True
    assert ("get_cpu", "SAFE") in calls["audit"]


async def test_caller_identity_reaches_rbac_from_agent_loop(gate, monkeypatch):
    """ask_async truyền `caller` (portal: username) tới cổng — không dùng tên kênh."""
    calls, _ = gate
    from mateai.application.agent.llm_engine import llm_engine
    import inspect
    assert "caller" in inspect.signature(llm_engine.ask_async).parameters
    assert "caller" in inspect.signature(llm_engine.stream_voice_response).parameters
