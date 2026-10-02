"""
tests/test_public_endpoints_locked.py
=====================================
Endpoint ghi/điều khiển không còn gọi được khi chưa đăng nhập.

Trước đây middleware xác thực có danh sách "public" gồm cả
computer-use/dispatch (điều khiển GUI máy chủ), computer-use/screenshot,
topology save/reset, departments/save, cross-report, ephemeral-cache/flush —
ai trong LAN cũng gọi được không cần JWT. Không gọi mạng.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

import core.server as server  # noqa: E402

LOCKED = [
    ("POST", "/api/v1/computer-use/dispatch"),
    ("GET", "/api/v1/computer-use/screenshot"),
    ("GET", "/api/v1/computer-use/sessions"),
    ("POST", "/api/v1/system/topology/save"),
    ("POST", "/api/v1/system/topology/reset"),
    ("POST", "/api/v1/system/topology/trigger"),
    ("GET", "/api/v1/system/topology"),
    ("POST", "/api/v1/admin/departments/save"),
    ("POST", "/api/v1/admin/cross-report"),
    ("POST", "/api/v1/admin/ephemeral-cache/flush"),
    ("GET", "/api/v1/admin/topology"),
    ("GET", "/api/v1/clients"),
    ("GET", "/api/v1/worknodes/status"),
]


@pytest.mark.parametrize("method,path", LOCKED)
def test_requires_login(method, path):
    client = TestClient(server.app)  # không `with` → không chạy startup
    res = client.request(method, path, json={"task_goal": "mở notepad"})
    assert res.status_code == 401, f"{method} {path} → {res.status_code}"


def test_pre_login_endpoints_still_public():
    client = TestClient(server.app)
    assert client.get("/api/v1/config/assistant-name").status_code == 200
