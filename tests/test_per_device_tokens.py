"""
tests/test_per_device_tokens.py
===============================
Token RIÊNG cho từng thiết bị IoT, ràng buộc với đúng device_id.

Trước đây mọi robot dùng CHUNG một device secret, còn device_id do thiết bị tự
đặt trên URL → lộ secret của một robot = giả được mọi robot. Token chung vẫn
được nhận để không ngắt robot cũ, trừ khi bật security.require_per_device_token.
DB tạm, không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.db_manager as dbm  # noqa: E402
import core.server as server  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(dbm, "USERS_JSON_PATH", tmp_path / "none.json")
    s = dbm.DatabaseManager(db_path=tmp_path / "t.db")
    monkeypatch.setattr(dbm, "db_manager", s)
    monkeypatch.setattr(server, "_get_device_enrollment_secret", lambda: "shared-secret-xyz")
    return s


def _ws(token):
    return SimpleNamespace(query_params={"token": token}, headers={}, client=SimpleNamespace(host="192.168.1.9"))


def test_token_is_bound_to_its_device(store):
    tok_a = store.issue_device_token("robot_a")
    store.issue_device_token("robot_b")
    assert server._authenticate_device(_ws(tok_a), "robot_a") is True
    assert server._authenticate_device(_ws(tok_a), "robot_b") is False, "token của A không được mở B"
    assert server._authenticate_device(_ws(tok_a), "esp32-default") is False


def test_rotate_and_revoke(store):
    old = store.issue_device_token("robot_a")
    new = store.issue_device_token("robot_a")
    assert server._authenticate_device(_ws(old), "robot_a") is False
    assert server._authenticate_device(_ws(new), "robot_a") is True
    assert store.revoke_device_token("robot_a") is True
    assert server._authenticate_device(_ws(new), "robot_a") is False
    assert "token" not in str(store.list_device_tokens()).lower().replace("device_tokens", "")


def test_shared_secret_can_be_switched_off(store, monkeypatch):
    assert server._authenticate_device(_ws("shared-secret-xyz"), "robot_x") is True
    monkeypatch.setattr("core.config_loader.get_config_section",
                        lambda name: {"require_per_device_token": True} if name == "security" else {})
    assert server._authenticate_device(_ws("shared-secret-xyz"), "robot_x") is False
    tok = store.issue_device_token("robot_x")
    assert server._authenticate_device(_ws(tok), "robot_x") is True


def test_only_hash_is_stored(store):
    tok = store.issue_device_token("robot_h")
    import sqlite3
    raw = sqlite3.connect(store.db_path).execute("SELECT * FROM device_tokens").fetchall()
    assert tok not in str(raw)


async def test_admin_api_issue_list_revoke(store):
    admin = {"username": "admin", "role": "admin"}
    res = await server.issue_device_token_endpoint(server.DeviceTokenRequest(device_id="kitchen_bot"), user=admin)
    assert res["ws_path"] == "/api/v1/xiaozhi/ws/kitchen_bot" and len(res["device_token"]) > 30
    listed = await server.list_device_tokens_endpoint(user=admin)
    assert [d["device_id"] for d in listed["devices"]] == ["kitchen_bot"]
    assert res["device_token"] not in str(listed), "danh sách không được lộ token"
    await server.revoke_device_token_endpoint("kitchen_bot", user=admin)
    with pytest.raises(server.HTTPException):
        await server.revoke_device_token_endpoint("kitchen_bot", user=admin)
    with pytest.raises(server.HTTPException):
        await server.issue_device_token_endpoint(server.DeviceTokenRequest(device_id="../etc"), user=admin)
