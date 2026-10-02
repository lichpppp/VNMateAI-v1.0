"""
tests/test_confirm_action_endpoint.py
=====================================
`POST /api/v1/security/confirm-action` — duyệt tác vụ NEED_CONFIRM.

Trước đây mọi người đã đăng nhập (kể cả viewer) gửi
`{approved: true, skill_name, args}` là chạy thẳng skill bất kỳ trên máy chủ
qua `plugin_manager.execute_skill`, không qua Zero-Trust/RBAC, không cần tác vụ
nào đang chờ. Nay: chỉ admin, chỉ đúng tác vụ trong hàng đợi, chạy qua cổng
tool chung với `approved=True`. Không gọi mạng, không chạy skill thật.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import mateai.application.agent.tool_gate as tool_gate
import mateai.interfaces.http.server as server
from mateai.application.agent.llm_engine import llm_engine
from mateai.application.agent.state_manager import state_manager
from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http.auth_dependencies import get_current_user


@pytest.fixture
def env(monkeypatch):
    calls = []

    async def fake_gate(fn_name, fn_args, **kw):
        calls.append((fn_name, dict(fn_args), kw))
        return {"target_client": "master", "args": fn_args, "result": {"status": "success"}}

    async def no_llm(*a, **k):
        raise RuntimeError("không gọi LLM trong test")

    async def noop(*a, **k):
        return None

    async def no_tts(*a, **k):
        return b""

    monkeypatch.setattr(tool_gate, "run_tool_with_policy", fake_gate)
    monkeypatch.setattr(llm_engine, "_call_llm", no_llm)
    monkeypatch.setattr(server, "broadcast_hud", noop)
    monkeypatch.setattr(server, "broadcast_portal_ui", noop)
    monkeypatch.setattr(server, "_tts_bytes", no_tts)
    created = []
    yield calls, created
    for act_id in created:
        state_manager.cancel_pending_action(act_id)
    server.app.dependency_overrides.pop(get_current_user, None)


def _client(role: str) -> TestClient:
    # Middleware xác thực đòi Bearer token thật; vai trò lấy qua override.
    server.app.dependency_overrides[get_current_user] = lambda: {"username": f"{role}_u", "role": role}
    tok = auth_manager.create_access_token(data={"sub": f"{role}_u", "role": role})
    return TestClient(server.app, headers={"Authorization": f"Bearer {tok}"})


def _pending(created, tool="write_file", args=None, user="requester_u"):
    saved = state_manager.save_pending_action(
        user_id=user, tool_name=tool, arguments=args or {"file_path": "a.txt", "content": "x"},
        target_client="master", query="ghi tệp", source_device="http:fs",
    )
    created.append(saved["id"])
    return saved["id"]


@pytest.mark.parametrize("role", ["viewer", "manager"])
def test_non_admin_cannot_approve_or_run_arbitrary_skill(env, role):
    calls, _ = env
    r = _client(role).post("/api/v1/security/confirm-action",
                           json={"approved": True, "skill_name": "delete_item", "args": {"path": "C:/"}})
    assert r.status_code == 403
    assert calls == []


def test_without_pending_action_nothing_runs(env, monkeypatch):
    calls, _ = env
    monkeypatch.setattr(state_manager, "list_pending_actions", lambda: [])
    monkeypatch.setattr(state_manager, "get_pending_action", lambda key: None)
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "skill_name": "delete_item", "args": {"path": "C:/"}})
    assert r.status_code == 404
    assert calls == []


def test_approval_runs_exactly_the_queued_action_through_the_gate(env):
    calls, created = env
    act_id = _pending(created)
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "action_id": act_id,
                                    "args": {"file_path": "C:/Windows/evil.txt", "content": "pwn"}})
    assert r.status_code == 200, r.text
    name, args, kw = calls[0]
    assert name == "write_file"
    assert args["file_path"] == "a.txt" and args["content"] == "x"
    assert kw["approved"] is True
    assert kw["caller"] == "requester_u"
    assert state_manager.get_pending_action(act_id) is None


def test_skill_name_mismatch_is_rejected(env):
    calls, created = env
    act_id = _pending(created)
    r = _client("admin").post("/api/v1/security/confirm-action",
                              json={"approved": True, "action_id": act_id, "skill_name": "delete_item"})
    assert r.status_code == 409
    assert calls == []


def test_replayed_action_id_does_not_fall_back_to_another_pending_action(env):
    calls, created = env
    act_id = _pending(created)
    _pending(created, args={"file_path": "b.txt", "content": "y"}, user="other_u")
    c = _client("admin")
    assert c.post("/api/v1/security/confirm-action", json={"approved": True, "action_id": act_id}).status_code == 200
    r = c.post("/api/v1/security/confirm-action", json={"approved": True, "action_id": act_id})
    assert r.status_code == 404
    assert len(calls) == 1
