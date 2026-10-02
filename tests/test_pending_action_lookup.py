"""
tests/test_pending_action_lookup.py
===================================
Tra cứu tác vụ chờ duyệt phải khớp CHÍNH XÁC, và "đồng ý"/"huỷ" trong hội
thoại chỉ chạm được tác vụ của người khác khi người nói là admin.

Trước đây `StateManager.get_pending_action` khớp chuỗi con rồi rơi về "tác vụ
mới nhất của bất kỳ ai", và vòng agent cũng tự rơi về tác vụ đầu hàng đợi —
nên một viewer nói "đồng ý" là duyệt được tác vụ rủi ro cao của người khác.
"""
from __future__ import annotations

import pytest

from mateai.application.agent import llm_engine as le
from mateai.application.agent.state_manager import StateManager
from mateai.application.security.security_guard import security_guard


@pytest.fixture
def sm(monkeypatch):
    m = StateManager()
    for a in m.list_pending_actions():
        m.cancel_pending_action(a["id"])
    import mateai.application.agent.state_manager as smod
    monkeypatch.setattr(smod, "state_manager", m)
    return m


def _save(m, user, tool="write_file"):
    return m.save_pending_action(user_id=user, tool_name=tool, arguments={}, target_client="master", query="q")


def test_lookup_is_exact(sm):
    a = _save(sm, "requester_u")
    assert sm.get_pending_action(a["id"])["id"] == a["id"]
    assert sm.get_pending_action("requester_u")["id"] == a["id"]
    assert sm.get_pending_action("u") is None              # không khớp chuỗi con
    assert sm.get_pending_action("viewer_bob") is None     # không rơi về tác vụ người khác
    assert sm.get_pending_action("act_does_not_exist") is None


def test_non_admin_confirm_cannot_take_someone_elses_action(sm, monkeypatch):
    a = _save(sm, "requester_u")
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "operator")
    assert le._take_pending_action("viewer_bob") is None
    assert sm.get_pending_action(a["id"]) is not None


def test_own_action_is_taken(sm, monkeypatch):
    a = _save(sm, "requester_u")
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "operator")
    assert le._take_pending_action("requester_u")["id"] == a["id"]
    assert sm.get_pending_action(a["id"]) is None


def test_admin_may_take_the_latest_pending_action(sm, monkeypatch):
    a = _save(sm, "requester_u")
    monkeypatch.setattr(security_guard, "resolve_role", lambda who: "admin")
    assert le._take_pending_action("admin_alice")["id"] == a["id"]
