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

    import mateai.application.security.zero_trust as zt
    import mateai.application.security.security_guard as sg
    import core.plugin_manager as pm
    import mateai.application.skills.plugin_registry as pr

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
    calls["requests"] = []

    def fake_request(**kw):
        calls["requests"].append(kw)
        return {"id": "HITL-TEST", **kw}

    monkeypatch.setattr(avl.hitl_manager, "request_approval", fake_request)
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


async def test_confirmed_flag_in_tool_args_cannot_bypass_hitl(gate):
    """Tham số tool đến từ LLM/client — `confirmed: true` trong đó không được bỏ qua HITL."""
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    out = await avl.run_tool_with_policy("kill_process", {"pid": 1, "confirmed": True},
                                         caller="someone", source_device="web-widget")
    assert out["result"]["status"] == "need_confirm"
    assert calls["executed"] == []
    assert "confirmed" not in out["args"]


async def test_approved_resume_runs_need_confirm_tool(gate):
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    await avl.run_tool_with_policy("kill_process", {"pid": 1}, caller="admin",
                                   source_device="web-widget", approved=True)
    assert calls["executed"] == [("kill_process", {"pid": 1})]


async def test_client_timeout_reaches_workstation_call(gate, monkeypatch):
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    seen = {}

    def fake_sync(client_id, skill, args, timeout=35.0):
        seen.update(client=client_id, timeout=timeout)
        return {"status": "success"}

    monkeypatch.setattr(orchestrator, "execute_on_client_sync", fake_sync)
    await avl.run_tool_with_policy("get_cpu", {"target_client": "pc-01"}, caller="admin",
                                   source_device="portal", client_timeout=20)
    assert seen == {"client": "pc-01", "timeout": 20}


async def test_tool_arg_values_are_not_logged(gate, caplog):
    """`/api/v1/logs/recent` phục vụ log cho cả viewer — chỉ ghi TÊN tham số."""
    import logging
    with caplog.at_level(logging.INFO):
        await avl.run_tool_with_policy("write_note", {"content": "BÍ-MẬT-NỘI-DUNG"},
                                       caller="admin", source_device="portal")
    assert "BÍ-MẬT-NỘI-DUNG" not in caplog.text
    assert "content" in caplog.text



async def test_need_confirm_goes_to_the_single_hitl_queue(gate):
    """Yêu cầu duyệt từ cổng tool nằm trong hàng đợi HITL chung (kind="tool")."""
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    out = await avl.run_tool_with_policy("kill_process", {"pid": 7, "target_client": "pc-01"},
                                         caller="bob", source_device="web-widget", query="dừng tiến trình 7")
    req = calls["requests"][0]
    assert req["kind"] == "tool" and req["requested_by"] == "bob"
    assert req["params"] == {"pid": 7} and req["context"]["target_client"] == "pc-01"
    assert out["result"]["approval_id"] == "HITL-TEST"


async def test_executor_reruns_the_queued_tool_as_the_requester(gate):
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    item = {"id": "HITL-X", "action_name": "kill_process", "params": {"pid": 7}, "requested_by": "bob",
            "kind": "tool", "context": {"target_client": "master", "source_device": "web-widget", "query": "q"}}
    await avl.execute_approved_tool(item)
    assert calls["executed"] == [("kill_process", {"pid": 7})]
    assert calls["rbac"] == [("kill_process", "bob")]


# ── Bỏ qua bước duyệt chỉ cho TÀI KHOẢN admin (2026-10-05, thay "full admin bypass" f389bbe) ──

@pytest.fixture
def users(monkeypatch):
    from mateai.infrastructure.database.db_manager import db_manager
    table = {"boss": {"username": "boss", "role": "admin"}, "op": {"username": "op", "role": "manager"}}
    monkeypatch.setattr(db_manager, "get_user_by_username_or_id", lambda c: table.get(c.strip().lower()))
    grants = set()
    monkeypatch.setattr(db_manager, "has_approval_grant", lambda p, t: (p, t) in grants)
    monkeypatch.setattr(db_manager, "add_approval_grant", lambda p, t, by="": grants.add((p, t)))
    return grants


@pytest.mark.parametrize("device", ["portal", "hud", "telegram:1:x", "esp32-a", "xiaozhi", "console", "admin"])
async def test_channel_name_no_longer_skips_approval(gate, users, device):
    """Trước đây chỉ cần source_device chứa 'portal'/'hud'/… là chạy thẳng — kể cả người không phải admin."""
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    out = await avl.run_tool_with_policy("kill_process", {"pid": 999999}, caller="op", source_device=device)
    assert out["result"]["status"] == "need_confirm" and calls["executed"] == []


async def test_logged_in_admin_account_runs_without_approval(gate, users):
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    await avl.run_tool_with_policy("kill_process", {"pid": 1}, caller="boss", source_device="web-widget")
    assert calls["executed"] == [("kill_process", {"pid": 1})]
    # Danh tính có dấu ":" (thiết bị / kênh) không bao giờ là tài khoản người dùng.
    assert avl._is_admin_user("device:boss") is False and avl._is_admin_user("") is False


async def test_telegram_chat_follows_option_a(gate, users):
    """Telegram: duyệt lần đầu, nhớ theo chat_id (tên người gửi đổi không ảnh hưởng)."""
    calls, state = gate
    state["risk"] = "NEED_CONFIRM"
    first = await avl.run_tool_with_policy("kill_process", {"pid": 1}, caller="telegram:42:An", source_device="telegram:42:An")
    assert first["result"]["status"] == "need_confirm" and calls["executed"] == []
    avl._remember_approval({"action_name": "kill_process", "requested_by": "telegram:42:An", "reviewed_by": "boss"})
    assert users == {("telegram:42", "kill_process")}
    await avl.run_tool_with_policy("kill_process", {"pid": 2}, caller="telegram:42:Bình", source_device="telegram:42:Bình")
    assert calls["executed"] == [("kill_process", {"pid": 2})]
    other = await avl.run_tool_with_policy("kill_process", {"pid": 3}, caller="telegram:77:An", source_device="telegram:77:An")
    assert other["result"]["status"] == "need_confirm"
