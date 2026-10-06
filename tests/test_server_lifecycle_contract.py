"""
tests/test_server_lifecycle_contract.py
=======================================
Supervisor Phase 10 (§198): `server.py` chỉ dựng app — khởi động / tắt dịch vụ nền nằm
ở `interfaces/http/lifecycle.py`, đăng ký route ở `interfaces/http/routes.py`.

Cố định hành vi trước khi chuyển:
  - bảng route (thứ tự + method) giống hệt bản chụp trước khi tách
    (`fixtures/route_table_p10.json`, chụp từ app thật ngày 2026-10-06);
  - một bước khởi động lỗi KHÔNG chặn các bước sau, và lỗi được ghi lại (trước đây
    chỉ có dòng log cảnh báo); bước lõi lỗi thì khởi động dừng như cũ;
  - chạy hai lần (hai listener uvicorn) chỉ khởi động một lần;
  - task nền được giữ tham chiếu (asyncio chỉ giữ tham chiếu yếu);
  - beacon UDP không trả IP cứng, dùng cổng IoT trong cấu hình;
  - phát thanh KPI gọi từ thread khác vẫn tới được event loop.
Không gọi mạng, không mở socket thật.
"""
from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path

import pytest

import mateai.interfaces.http.server as server
from mateai.interfaces.http import lifecycle

#: Route thêm có chủ đích sau bản chụp (không đổi thứ tự route cũ).
NEW_ROUTES = {"/api/v1/health/startup",
              # prompt cuối: model registry §79, sự cố §88, mục tiêu §42, toàn vẹn audit §73
              "/api/v1/llm/model-registry", "/api/v1/ops/incidents", "/api/v1/ops/incidents/{task_id}/phase",
              "/api/v1/ops/goals", "/api/v1/security/audit-logs/verify",
              "/api/v1/memory/{doc_id}/verify"}   # memory trust §59


def _rows(app):
    return [[type(r).__name__, getattr(r, "path", ""), sorted(getattr(r, "methods", None) or []),
             getattr(r, "name", "")] for r in app.routes]


def test_route_table_unchanged_by_extraction():
    snap = json.loads((Path(__file__).parent / "fixtures" / "route_table_p10.json").read_text(encoding="utf-8"))
    now = [r for r in _rows(server.app) if r[1] not in NEW_ROUTES]
    assert now == snap


@pytest.fixture
def fresh_state(monkeypatch):
    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState())
    return lifecycle.STATE


async def test_failed_step_does_not_stop_later_steps_and_is_reported(fresh_state):
    ran = []

    def ok(ctx):
        ran.append("a")

    def boom(ctx):
        raise RuntimeError("hỏng")

    def skip(ctx):
        raise lifecycle.StepSkipped("tắt trong cấu hình")

    async def later(ctx):
        ran.append("c")
        return "3 mục"

    steps = [lifecycle.Step("a", ok), lifecycle.Step("b", boom), lifecycle.Step("s", skip), lifecycle.Step("c", later)]
    await lifecycle.run_startup(server.app, steps=steps)
    assert ran == ["a", "c"] and fresh_state.complete is True
    rep = fresh_state.report
    assert rep["a"]["status"] == "ok" and rep["c"]["detail"] == "3 mục"
    assert rep["b"]["status"] == "error" and rep["b"]["detail"] == "RuntimeError: hỏng"
    assert rep["s"] == {**rep["s"], "status": "skipped", "detail": "tắt trong cấu hình"}


async def test_critical_step_failure_aborts_startup(fresh_state):
    def boom(ctx):
        raise RuntimeError("lõi hỏng")

    with pytest.raises(RuntimeError):
        await lifecycle.run_startup(server.app, steps=[lifecycle.Step("core", boom, critical=True)])
    assert fresh_state.complete is False and fresh_state.report["core"]["status"] == "error"


async def test_second_listener_does_not_start_twice(fresh_state):
    n = []
    steps = [lifecycle.Step("x", lambda ctx: n.append(1))]
    await lifecycle.run_startup(server.app, steps=steps)
    await lifecycle.run_startup(server.app, steps=steps)
    assert n == [1]


async def test_background_tasks_are_referenced_until_done():
    gate = asyncio.Event()

    async def worker():
        await gate.wait()

    t = lifecycle.spawn(worker(), "test-worker")
    assert t in lifecycle._BACKGROUND
    gate.set()
    await t
    await asyncio.sleep(0)
    assert t not in lifecycle._BACKGROUND


def test_default_steps_keep_order_and_names():
    names = [s.name for s in lifecycle.default_steps()]
    assert names[0] == "core" and names[-1] == "email_gateway"
    assert len(names) == len(set(names))
    # thứ tự cũ: đăng ký tool connector trước tác vụ nền (tool phải có trước request đầu tiên)
    assert names.index("connector_tools") < names.index("background_workers")
    assert [s.name for s in lifecycle.default_steps() if s.critical] == ["core", "alert_dispatcher"]


def test_beacon_reply_uses_probed_ip_and_configured_port(monkeypatch):
    from mateai.config.loader import settings
    from mateai.interfaces.iot import discovery_beacon as b
    monkeypatch.setattr(settings, "IOT_PORT", 8123)
    monkeypatch.setattr(b, "_local_ip_towards", lambda ip: "10.0.0.5")
    assert b.reply_for(b"VNMATE_DISCOVER", ("10.0.0.9", 5000)) == b"VNMATE_BEACON:10.0.0.5:8123"
    assert b.reply_for(b"hello", ("10.0.0.9", 5000)) is None
    monkeypatch.setattr(b, "_local_ip_towards", lambda ip: None)            # không dò được IP
    assert b.reply_for(b"VNMATE_DISCOVER", ("10.0.0.9", 5000)) is None     # KHÔNG trả IP đoán


async def test_tts_broadcast_from_worker_thread_reaches_loop(monkeypatch):
    from mateai.interfaces.websocket import audio_announce as aa
    got = []

    async def fake_broadcast(text):
        got.append(text)

    monkeypatch.setattr(aa, "_broadcast", fake_broadcast)
    aa.bind_loop(asyncio.get_running_loop())
    th = threading.Thread(target=aa.broadcast_tts_notification, args=("máy A xong việc",))
    th.start()
    th.join()
    for _ in range(20):
        if got:
            break
        await asyncio.sleep(0.01)
    assert got == ["máy A xong việc"]


def test_startup_report_endpoint_is_admin_only(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import mateai.interfaces.http.routers.health as health
    from mateai.interfaces.http.auth_dependencies import get_current_user
    monkeypatch.setattr(lifecycle, "STATE", lifecycle.LifecycleState())
    lifecycle.STATE.report["telegram"] = {"status": "skipped", "detail": "tắt", "ms": 0}

    def client(role):
        app = FastAPI()
        app.include_router(health.router)
        app.dependency_overrides[get_current_user] = lambda: {"username": "u", "role": role}
        return TestClient(app)

    assert client("viewer").get("/api/v1/health/startup").status_code == 403
    body = client("admin").get("/api/v1/health/startup").json()
    assert body["complete"] is False and body["steps"]["telegram"]["status"] == "skipped"
