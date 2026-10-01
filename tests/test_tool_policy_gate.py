"""
tests/test_tool_policy_gate.py
==============================
Cổng thực thi tool chung `core.agent_voice_loop.run_tool_with_policy`.

Trước Phase 3, đường voice realtime (portal) gọi tool qua một bản riêng import
`zero_trust.evaluate_risk` — hàm không tồn tại — rồi nuốt lỗi, nên mọi tool chạy
KHÔNG qua Zero-Trust, RBAC hay audit. Test này khoá lại: đường realtime đi đúng
cùng cổng với vòng agent `ask_async`. Không chạy skill thật.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.agent_voice_loop as avl  # noqa: E402


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
    res = await avl.execute_tool_call({"id": "c1", "name": "format_disk", "arguments": "{}"},
                                      {"username": "admin"})
    assert calls["executed"] == []
    assert res.success is False
    assert "chính sách bảo mật" in (res.direct_response or "")
    assert ("format_disk", "BLOCKED") in calls["audit"]


async def test_rbac_uses_logged_in_user_and_denies(gate):
    calls, state = gate
    state["rbac_ok"] = False
    res = await avl.execute_tool_call({"id": "c2", "name": "kill_process", "arguments": '{"pid": 1}'},
                                      {"username": "viewer_user"})
    assert calls["rbac"] == [("kill_process", "viewer_user")]
    assert calls["executed"] == []
    assert res.success is False and res.direct_response == "không đủ quyền"


async def test_need_confirm_from_non_admin_channel_waits_for_approval(gate):
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    out = await avl.run_tool_with_policy("kill_process", {"pid": 1},
                                         caller="someone", source_device="web-widget")
    assert out["result"]["status"] == "need_confirm"
    assert calls["executed"] == []


async def test_safe_tool_runs_and_is_audited(gate):
    calls, _ = gate
    res = await avl.execute_tool_call({"id": "c3", "name": "get_cpu", "arguments": "{}"},
                                      {"username": "admin"})
    assert calls["executed"] == [("get_cpu", {})]
    assert res.success is True
    assert ("get_cpu", "SAFE") in calls["audit"]
    assert "không thực hiện được" not in (res.direct_response or "")
