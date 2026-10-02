"""
core/server.py
==============
FastAPI REST + WebSocket + Web Portal server for VN-MateAI — Phase 2 & 4.

Endpoints:
  GET  /                       — Phục vụ Web Portal (web/index.html).
  GET  /static/*               — Static assets (web/app.js, v.v.).
  POST /api/v1/voice-command   — REST: nhận text đã bóc băng, trả về JSON + text TTS.
  POST /api/v1/tts             — REST: nhận text, trả về audio MP3 bytes.
  GET  /api/v1/config          — Đọc config.json hiện tại.
  POST /api/v1/config          — Lưu cấu hình mới vào config.json.
  GET  /api/v1/skills          — Đọc skills/registry.json.
  WS   /ws/audio-stream        — WebSocket: full audio pipeline cho ESP32 (Xiaozhi).
  GET  /health                 — Server status, skill count, audio engine status.
"""

from __future__ import annotations

import asyncio
import collections
import csv
import io
import json
import json as _json
import logging
import os
import re
import secrets
import threading
import psutil
import socket as _socket
import subprocess
import time
import traceback
import unicodedata
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from core.plugin_manager import run_blocking
import sys

# Silence Windows WinError 10054 in asyncio Proactor _call_connection_lost
if sys.platform == "win32":
    try:
        import asyncio.proactor_events
        _orig_call_conn_lost = asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost

        def _safe_call_conn_lost(self, exc):
            try:
                _orig_call_conn_lost(self, exc)
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = _safe_call_conn_lost
    except Exception:
        pass

logger = logging.getLogger(__name__)


# ─── Resolve paths for static files ────────────────────────────────────────
import sys
from pathlib import Path
from urllib.parse import quote

# Thư mục gốc dự án — một nguồn (settings.PROJECT_ROOT, đúng cả bản đóng gói),
# không suy từ vị trí file mã nguồn.
from mateai.config.loader import settings as _settings  # noqa: E402
_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)

from mateai.interfaces.http.routers.pages import _ADMIN_OUT_DIR, _WEB_DIR  # noqa: E402  (dùng cho app.mount)

# Trạng thái kết nối + phát sóng: core/realtime_hub.py (module lõi dùng trực tiếp).
from mateai.interfaces.websocket.realtime_hub import (  # noqa: E402
    active_audio_nodes,
    active_hud_websockets,
    active_portal_websockets,
    active_topology_websockets,
    broadcast_hud,
    broadcast_hud_binary,
    broadcast_portal_ui,
    broadcast_topology_event,
)


from mateai.interfaces.http import hud_voice  # noqa: E402


from mateai.config.loader import get_assistant_name  # noqa: E402


from mateai.interfaces.http import speech  # noqa: E402


# ---------------------------------------------------------------------------
# Real-time WebSocket Log Handler (Phase 21)
# ---------------------------------------------------------------------------

from mateai.interfaces.http import log_stream  # noqa: E402


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="VN-MateAI",
    description=(
        "Autonomous Voice-RPA Windows Assistant API — Phase 2, 4 & 10. "
        "Supports REST voice commands, streaming TTS, real-time "
        "ESP32 Multi-Node WebSocket audio pipeline, Auth & RBAC Web Portal."
    ),
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Zero-Trust CORS
#
# Trước đây: allow_origins=["*"] + allow_credentials=True — tổ hợp nguy hiểm cho
# phép mọi website trên internet gọi API có kèm credential của người dùng.
#
# Nay: web portal được phục vụ từ CHÍNH server này nên request là same-origin và
# không cần CORS header. Danh sách origin cho phép lấy từ biến môi trường
# (VNMATEAI_CORS_ORIGINS, phân tách bằng dấu phẩy) cho các triển khai tách frontend.
# allow_credentials=False: xác thực dùng Bearer token trong header, không dùng
# cookie, nên không có gì để trình duyệt tự động gửi đi cả.
_allowed_origins = [
    o.strip()
    for o in os.getenv("VNMATEAI_CORS_ORIGINS", "").split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


# ─── Authentication Middleware (Zero-Trust RBAC Protection) ─────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """
    Bảo vệ tất cả các REST API endpoint.

    Zero-Trust: KHÔNG có fallback. Mọi endpoint ngoài danh sách public đều bắt buộc
    phải mang JWT hợp lệ.

    (Trước đây từng tự cấp toàn quyền khi request đến từ loopback HOẶC khi header
    Referer chứa "/hud". Cả hai đều do phía client kiểm soát được, nên bất kỳ máy
    nào trong LAN cũng giành được quyền admin. Đã gỡ bỏ.)
    """
    path = request.url.path
    # Chỉ những gì PHẢI chạy trước khi đăng nhập. Mọi endpoint ghi/điều khiển
    # (computer-use/dispatch, screenshot, topology save/reset, departments/save,
    # cross-report, ephemeral-cache/flush...) từng nằm ở đây — tức ai trong LAN
    # cũng gọi được không cần JWT. Đã chuyển hết sang yêu cầu đăng nhập.
    # Chỉ những gì PHẢI chạy trước khi đăng nhập. Mọi endpoint ghi/điều khiển
    # (computer-use/dispatch, screenshot, topology save/reset, departments/save,
    # cross-report, ephemeral-cache/flush...) từng nằm ở đây — tức ai trong LAN
    # cũng gọi được không cần JWT. Đã chuyển hết sang yêu cầu đăng nhập.
    # Chỉ những gì PHẢI chạy trước khi đăng nhập. Mọi endpoint ghi/điều khiển
    # (computer-use/dispatch, screenshot, topology save/reset, departments/save,
    # cross-report, ephemeral-cache/flush...) từng nằm ở đây — tức ai trong LAN
    # cũng gọi được không cần JWT. Đã chuyển hết sang yêu cầu đăng nhập.
    public_endpoints = (
        "/api/v1/login",
        "/api/v1/login/",
        "/api/v1/config/assistant-name",   # màn hình HUD/đăng nhập
        "/api/v1/health-dashboard",        # telemetry HUD chế độ xem
    )
    # Endpoint của máy worker (không có tài khoản người dùng): chỉ nhận
    # enrollment secret của worker hoặc JWT admin/manager — cùng luật với
    # /ws/client. JWT của người dùng thường KHÔNG đủ để giả làm worker.
    worker_endpoints = ("/api/v1/worknodes/heartbeat",)

    # Mọi tiền tố path phải được bọc xác thực. /api/erp/ là router của
    # core/api_erp.py — trước đây nằm ngoài /api/v1/ nên KHÔNG endpoint nào của
    # nó được middleware kiểm tra (chỉ có 4/5 endpoint tự khai Depends riêng,
    # còn /template thì hoàn toàn không xác thực).
    guarded_prefixes = ("/api/v1/", "/api/erp/")

    # Phase 78: bỏ qua preflight CORS (OPTIONS).
    #
    # Trình duyệt gửi OPTIONS KHÔNG kèm Authorization, nên middleware này trả
    # 401 và CORSMiddleware không bao giờ kịp gắn header. Kết quả: mọi
    # frontend chạy khác origin (trang Admin ở cổng 3001) không gọi được API
    # nào — lỗi "Failed to fetch" dù backend vẫn chạy bình thường.
    #
    # Đây KHÔNG phải nới lỏng xác thực: preflight chỉ hỏi "có cho phép
    # method/header này không", không mang dữ liệu, không chạy nghiệp vụ, và
    # không đọc được gì. Request thật (GET/POST/...) vẫn qua đủ kiểm tra JWT
    # bên dưới.
    if request.method == "OPTIONS":
        return await call_next(request)

    if path.startswith(guarded_prefixes):
        if path in public_endpoints:
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "token" in request.query_params:
            token = request.query_params.get("token")

        if path in worker_endpoints:
            if token and ws_auth.is_valid_worker_token(token):
                return await call_next(request)
            return JSONResponse(
                status_code=401,
                content={"detail": "Worker chưa xác thực (thiếu/sai enrollment token)."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Yêu cầu xác thực tài khoản (Thiếu Bearer Token)."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        payload = auth_manager.decode_access_token(token)
        if not payload or "sub" not in payload:
            return JSONResponse(
                status_code=401,
                content={"detail": "Phiên đăng nhập đã hết hạn hoặc không hợp lệ."},
                headers={"WWW-Authenticate": "Bearer"},
            )
    return await call_next(request)


# ─── Mount static files (web/ directory) ───────────────────────────────────
class _NoStaleStatic(StaticFiles):
    """
    Phục vụ file tĩnh nhưng LUÔN buộc trình duyệt kiểm tra lại.

    Phase 69. `StaticFiles` mặc định không gửi `Cache-Control`, nên trình duyệt
    tự cache theo heuristics — thường là 10% khoảng thời gian kể từ
    `Last-Modified`. Hậu quả rất khó chịu: HTML được phục vụ kèm `no-cache`
    nên luôn mới, nhưng file JS/CSS mà HTML đó trỏ tới thì vẫn là bản CŨ.

    Người dùng thấy giao diện mới, nhưng hành vi là bản cũ, và không có cách
    nào đoán ra. Chính xác tình trạng đang xảy ra khi sửa `hud.js`: HUD vẫn báo
    lỗi mic kiểu cũ vì trình duyệt không bao giờ hỏi lại server.

    `no-cache` KHÔNG có nghĩa là không cache: trình duyệt vẫn giữ bản cũ và gửi
    `If-None-Match`; server trả 304 (vài chục byte) nếu không đổi. Rẻ mà luôn
    đúng.
    """

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
        return resp


if _WEB_DIR.exists():
    app.mount("/static", _NoStaleStatic(directory=str(_WEB_DIR)), name="static")
    logger.info("Static files mounted from: %s (no-cache: luôn kiểm tra bản mới)", _WEB_DIR)
else:
    logger.warning("web/ directory not found at %s — portal will be unavailable.", _WEB_DIR)

# ─── Mount ERP Organization & Bulk Import Router (Phase 47) ────────────────
from mateai.interfaces.http.api_erp import router as erp_router
app.include_router(erp_router)

# ─── Mount Enterprise Admin & Elastic Standby Grid Router ──────────────────
from mateai.interfaces.http.api_admin import router as admin_router
app.include_router(admin_router)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Startup / Shutdown lifecycle
# ---------------------------------------------------------------------------


#: main.py chạy HAI uvicorn server trên CÙNG một đối tượng `app` (HTTPS 443 và
#: IoT WS 8000) trong cùng event loop, nên lifespan của FastAPI được kích hoạt
#: HAI LẦN. Mọi thành phần bên dưới vốn chạy kép: skill plugin nạp 2 lần,
#: Sentinel monitor tạo 2 task, Background Worker/HITL loop khởi động 2 lần.
#: Cờ này chặn lần thứ hai — không sửa `main.py` vì phải giữ nguyên hành vi
#: của luồng streaming/WebSocket hiện tại.
_STARTUP_DONE = False
#: Đặt ở CUỐI `_on_startup` (cờ trên đặt ở đầu để chặn lần chạy thứ hai).
_STARTUP_COMPLETE = False


# ── Health probes (Kubernetes-style) ─────────────────────────────────────────
# Ngoài /api/v1/ nên middleware JWT không chặn; chỉ trả trạng thái, không lộ
# cấu hình. /api/v1/health-dashboard là telemetry cho HUD, không phải probe.

@app.get("/livez", include_in_schema=False)
async def livez() -> Dict[str, str]:
    """Tiến trình còn sống và event loop còn phục vụ request."""
    return {"status": "ok"}


@app.get("/startupz", include_in_schema=False)
async def startupz() -> JSONResponse:
    """200 khi lifecycle startup đã chạy xong."""
    done = _STARTUP_COMPLETE
    return JSONResponse(status_code=200 if done else 503,
                        content={"status": "ok" if done else "starting"})


def _check_database() -> None:
    from mateai.infrastructure.database.erp_database import erp_db
    erp_db.ping()


# ── Listener IoT không TLS (cổng 8000) ─────────────────────────────────────
# ESP32/Xiaozhi nói WS thường (TLS làm tràn heap của chip). Trước đây cổng này
# phục vụ NGUYÊN app: đăng nhập, mọi API, portal — mật khẩu và JWT đi qua LAN
# dạng rõ. Nay chỉ cho qua đường của thiết bị + health probe; còn lại dùng HTTPS.
_IOT_PORT_PREFIXES = ("/api/v1/xiaozhi/ws", "/ws/audio-stream")
_IOT_PORT_EXACT = {"/livez", "/readyz", "/startupz"}


def _iot_port_allows(path: str) -> bool:
    return path in _IOT_PORT_EXACT or any(path == p or path.startswith(p + "/") for p in _IOT_PORT_PREFIXES)


async def iot_listener_app(scope: Dict[str, Any], receive: Any, send: Any) -> None:
    """ASGI app cho listener cổng 8000: lọc đường dẫn rồi chuyển cho `app`."""
    if scope["type"] in ("http", "websocket") and not _iot_port_allows(scope.get("path", "")):
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 404,
                        "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
            await send({"type": "http.response.body",
                        "body": "Không phục vụ trên cổng IoT không mã hoá — dùng HTTPS.".encode("utf-8")})
        else:
            await receive()  # websocket.connect
            await send({"type": "websocket.close", "code": 1008})
        return
    await app(scope, receive, send)


@app.get("/readyz", include_in_schema=False)
async def readyz() -> JSONResponse:
    """Sẵn sàng nhận việc: startup xong, DB đọc được, đã nạp skill."""
    checks: Dict[str, str] = {"startup": "ok" if _STARTUP_COMPLETE else "starting"}
    try:
        await asyncio.wait_for(asyncio.to_thread(_check_database), timeout=3.0)
        checks["database"] = "ok"
    except Exception as exc:  # pylint: disable=broad-except
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        from core.plugin_manager import plugin_manager
        checks["skills"] = "ok" if plugin_manager.get_skill_count() > 0 else "none loaded"
    except Exception as exc:  # pylint: disable=broad-except
        checks["skills"] = f"error: {type(exc).__name__}"
    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ready else 503,
                        content={"status": "ok" if ready else "not_ready", "checks": checks})



@app.on_event("startup")
async def _on_startup() -> None:
    """Load skill plugins, warm up audio engine, and start Telegram gateway on server start."""
    global _STARTUP_DONE
    if _STARTUP_DONE:
        logger.info(
            "Startup lifecycle already initialised (dual uvicorn listener) — skipping second run."
        )
        return
    _STARTUP_DONE = True

    from core.plugin_manager import plugin_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    from mateai.application.devices.task_manager import task_manager

    loop = asyncio.get_event_loop()

    # Phase 21: Install real-time WebSocket log streamer (thread-safe)
    log_stream.install(loop)

    orchestrator.set_event_loop(loop)
    task_manager.set_tts_notifier(broadcast_tts_notification)

    # Hàng đợi duyệt duy nhất: khôi phục yêu cầu còn hạn (tác vụ từ cổng tool)
    # sau khi khởi động lại. Executor "tool" đăng ký khi import tool_gate.
    import mateai.application.agent.tool_gate  # noqa: F401
    from mateai.application.security.zero_trust import hitl_manager
    hitl_manager.restore_pending_from_audit()
    count = await loop.run_in_executor(None, plugin_manager.load_plugins)
    logger.info("FastAPI startup: loaded %d skill(s). Orchestrator & TaskManager ready.", count)

    # Ephemeral Data Lifecycle: Khởi động Background Sweeper tự hủy dữ liệu RAM mỗi 60s
    try:
        from mateai.infrastructure.cache.ephemeral_cache import ephemeral_cache
        asyncio.create_task(ephemeral_cache.start_sweeper_loop())
    except Exception as _e_sweep:
        logger.warning("[Startup] Không thể khởi động Ephemeral Cache Sweeper: %s", _e_sweep)

    # Phase 18: Start Telegram Gateway in background daemon thread (only if enabled)
    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        from mateai.config.loader import get_config_section
        is_tg_enabled = get_config_section("telegram").get("enabled", False)
        if is_tg_enabled:
            started = telegram_gateway.start()
            if started:
                logger.info("Phase 18: Telegram Gateway daemon thread launched successfully.")
            else:
                logger.info("Phase 18: Telegram Gateway skipped (bot_token not configured).")
        else:
            logger.info("Phase 18: Telegram Gateway is DISABLED in config. Skipping start.")
    except Exception as tg_exc:
        logger.warning("Phase 18: Could not start Telegram Gateway: %s", tg_exc)

    # Phase 24.5: Start Zero-Overhead Observability Background Async Workers
    try:
        from mateai.application.operations.health_monitor import start_observability_workers
        asyncio.create_task(start_observability_workers())
        logger.info("Phase 24.5: Zero-Overhead Observability async workers launched.")
    except Exception as hm_exc:
        logger.warning("Phase 24.5: Could not start Observability Workers: %s", hm_exc)

    # Phase 33: Start VN-MateAI Standby HUD background telemetry loop
    try:
        asyncio.create_task(hud_voice.telemetry_loop())
        logger.info("Phase 33: VN-MateAI Standby HUD telemetry loop launched.")
    except Exception as hud_exc:
        logger.warning("Phase 33: Could not start HUD telemetry loop: %s", hud_exc)

    # Phase 43: Start Autonomous Sentinel Background Incident Monitor
    try:
        from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
        autonomous_sentinel.start()
        logger.info("Phase 43: Autonomous Sentinel background monitor started.")
    except Exception as _sentinel_exc:
        logger.warning("Phase 43: Could not start Autonomous Sentinel: %s", _sentinel_exc)

    # Phase 53: Robot Auto-Discovery UDP Beacon on port 8888
    try:
        def _start_udp_beacon() -> None:
            import socket as _s
            sock = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
            sock.setsockopt(_s.SOL_SOCKET, _s.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", 8888))
            while True:
                try:
                    data, addr = sock.recvfrom(1024)
                    msg = data.decode("utf-8", errors="ignore").strip()
                    if "VNMATE_DISCOVER" in msg:
                        server_ip = "192.168.100.128"
                        try:
                            s_probe = _s.socket(_s.AF_INET, _s.SOCK_DGRAM)
                            s_probe.connect((addr[0], 80))
                            server_ip = s_probe.getsockname()[0]
                            s_probe.close()
                        except Exception:
                            pass
                        reply = f"VNMATE_BEACON:{server_ip}:8000"
                        sock.sendto(reply.encode("utf-8"), addr)
                except Exception:
                    pass
        t_beacon = threading.Thread(target=_start_udp_beacon, daemon=True, name="vnmate-udp-beacon")
        t_beacon.start()
        logger.info("[Robotics] Đã khởi chạy UDP Discovery Beacon trên cổng 8888.")
    except Exception as _udp_exc:
        logger.warning("[Robotics] Lỗi khởi động UDP Beacon: %s", _udp_exc)

    # Phase 56: Start Proactive Manager (Virtual C.O.O Agentic Engine — Cron 08:00 & 16:00)
    try:
        from mateai.application.skills.builtin.proactive_manager import proactive_manager
        proactive_manager.start()
        logger.info("Phase 56: Proactive Manager (Virtual C.O.O) started — will auto-nudge tasks at 08:00 & 16:00 daily.")
    except Exception as pm_exc:
        logger.warning("Phase 56: Could not start Proactive Manager: %s", pm_exc)

    # Phase 56: Pre-warm Enterprise RAG Engine (load ChromaDB + seed knowledge base)
    try:
        from mateai.application.knowledge.rag_engine import rag_engine
        doc_count = rag_engine.collection.count()
        logger.info("Phase 56: Enterprise RAG Engine ready — %d document chunks indexed in ChromaDB.", doc_count)
    except Exception as rag_exc:
        logger.warning("Phase 56: Could not warm up Enterprise RAG Engine: %s", rag_exc)

    # Phase 57: Pre-warm Multi-Agent System & Analytics Engine
    try:
        from mateai.application.agent.agent_orchestrator import multi_agent_system
        from mateai.application.analytics.analytics_engine import analytics_engine
        from mateai.application.knowledge.graph_rag import graph_rag
        logger.info("Phase 57: Multi-Agent System (CEO/CFO/HR/CTO) initialized. GraphRAG with %d entities, %d relations.", len(graph_rag.nodes), len(graph_rag.edges))
    except Exception as mas_exc:
        logger.warning("Phase 57: Could not warm up Multi-Agent System: %s", mas_exc)

    # Phase 59: Register Webhook Gateway routes (AWS SNS, OCI Alarms, Paperless, eInvoice webhooks)
    try:
        from mateai.interfaces.http.webhook_gateway import register_webhook_routes
        register_webhook_routes(app)
        logger.info("Phase 59: Webhook Gateway routes registered at /api/webhooks/{source}")
    except Exception as wh_exc:
        logger.warning("Phase 59: Could not register Webhook Gateway: %s", wh_exc)

    # Phase 59: Pre-warm Enterprise Connectors (AWS, OCI, Paperless, eInvoice)
    try:
        from mateai.infrastructure.connectors import (
            aws_connector,
            oci_connector,
            paperless_connector,
            einvoice_connector,
        )
        logger.info("Phase 59: Enterprise Connectors (AWS, OCI, Paperless, eInvoice) loaded.")
    except Exception as conn_exc:
        logger.warning("Phase 59: Could not load Enterprise Connectors: %s", conn_exc)

    # Phase 92: Pre-warm Voice Streaming Acoustic ACK cache (TTFA < 150ms for tools)
    try:
        from mateai.infrastructure.tts.acoustic_ack import warmup_acoustic_ack_cache
        asyncio.create_task(warmup_acoustic_ack_cache())
        logger.info("Phase 92: Voice streaming acoustic ACK cache warmup initiated.")
    except Exception as _v_exc:
        logger.warning("Phase 92: Could not warm up voice streaming cache: %s", _v_exc)

    # Phase 60: Register connector tools into Plugin Registry.
    # Đặt TRƯỚC khi bất kỳ request nào tới: `ask_async()` đọc
    # `get_all_tools_schema()` mỗi lượt, nên đăng ký muộn chỉ làm tool vô hình
    # với LLM cho tới lượt sau — một kiểu lỗi "chạy thử thì thấy, chạy thật
    # thì không" rất khó chẩn đoán.
    try:
        from mateai.infrastructure.connectors.tool_bridge import register_connector_tools
        reg_stats = register_connector_tools()
        logger.info(
            "Phase 60: Plugin Registry — %d connector tool(s) registered, %d skipped.",
            reg_stats.get("registered", 0), reg_stats.get("skipped", 0),
        )
    except Exception as reg_exc:
        logger.warning("Phase 60: Could not register connector tools: %s", reg_exc)

    # Phase 90: Register Computer-Use & RPA Worker Tool into Plugin Registry
    try:
        from mateai.application.skills.computer_use_plugin import register_computer_use_tool
        cu_stats = register_computer_use_tool()
        logger.info(
            "Phase 90: Computer-Use Plugin registered (%d tool).",
            cu_stats.get("registered", 0),
        )
    except Exception as cu_exc:
        logger.warning("Phase 90: Could not register computer-use tool: %s", cu_exc)

    # Phase 60: Start Background Worker Manager
    try:
        from mateai.application.operations.background_workers import background_worker_manager
        asyncio.create_task(background_worker_manager.start())
        logger.info("Phase 60: Background Worker Manager started.")
    except Exception as bw_exc:
        logger.warning("Phase 60: Could not start Background Worker Manager: %s", bw_exc)

    # Phase 57: Start Email Gateway (IMAP listener if configured)
    try:
        from mateai.interfaces.email.email_gateway import email_gateway
        from mateai.config.loader import get_config_section
        email_cfg = get_config_section("email_gateway")
        if email_cfg.get("enabled") and email_cfg.get("username"):
            email_gateway.configure(
                username=email_cfg["username"],
                password=email_cfg.get("password", ""),
                imap_host=email_cfg.get("imap_host", "imap.gmail.com"),
                smtp_host=email_cfg.get("smtp_host", "smtp.gmail.com"),
                enabled=True,
            )
            email_gateway.start()
            logger.info("Phase 57: Email Gateway started — monitoring inbox: %s", email_cfg["username"])
        else:
            logger.info("Phase 57: Email Gateway is disabled or unconfigured in config.json.")
    except Exception as eg_exc:
        logger.warning("Phase 57: Could not start Email Gateway: %s", eg_exc)

    global _STARTUP_COMPLETE
    _STARTUP_COMPLETE = True
    logger.info("Startup hoàn tất — /startupz và /readyz sẵn sàng.")


def broadcast_tts_notification(announcement_text: str) -> None:
    """Stream TTS notification to all connected Xiaozhi audio nodes."""

    async def _broadcast():
        if not active_audio_nodes:
            logger.info("Không có mạch Xiaozhi nào online để phát: '%s'", announcement_text)
            return
        logger.info("Phát thanh TTS thông báo tới %d mạch Xiaozhi: '%s'", len(active_audio_nodes), announcement_text)
        try:
            chunks = []
            from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
            async for chunk in get_tts_engine().stream(
                shorten_for_speech(sanitise_for_tts(announcement_text))
            ):
                chunks.append(chunk)

            for dev_id, info in list(active_audio_nodes.items()):
                ws = info.get("websocket")
                if ws:
                    try:
                        await ws.send_text(_json.dumps({"type": "tts_start", "text": announcement_text, "source": "kpi_notification"}))
                        for chunk in chunks:
                            await ws.send_bytes(chunk)
                        await ws.send_text(_json.dumps({"type": "tts_end", "chunk_count": len(chunks)}))
                    except Exception as err:
                        logger.warning("Không thể stream TTS tới mạch [%s]: %s", dev_id, err)
        except Exception as e:
            logger.error("Lỗi stream TTS phát thanh thông báo: %s", e)

    try:
        loop = asyncio.get_event_loop()
        if loop and loop.is_running():
            asyncio.create_task(_broadcast())
        else:
            asyncio.run(_broadcast())
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Web Portal — Serve index.html at root
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import pages as _r_pages  # noqa: E402
app.include_router(_r_pages.router)


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 81: GỠ app Admin (Next.js) ─────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════
# Phase 78 dựng một app Next.js riêng ở /admin. Nay đã gỡ, vì nó TRÙNG chức
# năng với portal mà bản trùng lại thì yếu hơn:
#
#   Trang              Vấn đề
#   ──────────────────  ────────────────────────────────────────────────────
#   /admin/dashboard   nạp đúng /system/stats, /logs/recent, /hitl/pending —
#                      ba nguồn đã gộp vào Bảng Điều Khiển của portal (Phase 79)
#   /admin/plugins     đọc connector, nhưng nút Lưu KHÔNG chạy (không có
#                      endpoint ghi). Bản ở tab Tích Hợp thì lưu được thật.
#   /admin/routing     tự báo "Chưa có nơi lưu quy tắc"
#   /admin/workers     tự báo "Chưa có nguồn dữ liệu"
#   /admin/settings    chỉ trỏ về portal
#
# Tức 4/5 trang hoặc trùng portal, hoặc tự thừa nhận chưa có backend. Giữ một
# bản yếu hơn chỉ để "có giao diện đẹp" là đúng loại UI giả dự án cấm.
#
# PHẦN ĐÁNG GIỮ LẠI đã chuyển về portal: DynamicForm sinh từ JSON Schema, nay
# dựng form connector từ `GET /api/v1/enterprise/connectors/catalog` (xem
# `renderConnectorForms` trong web/app.js). Nhờ vậy:
#   - thêm connector mới không phải viết HTML
#   - form không còn lệch tên với khóa cấu hình (bản viết tay lệch 7 trường)
#   - và chỉ có MỘT nơi sửa connector, với nút Lưu chạy thật.


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 88: VISUAL WORKFLOW TOPOLOGY (n8n-style Node Graph) ───────────────
# ═══════════════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 90: COMPUTER-USE & WORKER CONSOLE (Mac Mini Headless UI) ─────────
# ═══════════════════════════════════════════════════════════════════════════

if (_ADMIN_OUT_DIR / "_next").exists():
    app.mount("/admin/_next", _NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next")
    app.mount("/_next", _NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next_root")



from mateai.interfaces.http.routers import config as _r_config  # noqa: E402
app.include_router(_r_config.router)


# ---------------------------------------------------------------------------
# REST Endpoints — Authentication & Multi-Node Management (Phase 10)
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import auth as _r_auth  # noqa: E402
app.include_router(_r_auth.router)


# ---------------------------------------------------------------------------
# Quản trị Người Dùng (Admin User Management CRUD & Password Control)
# ---------------------------------------------------------------------------

from mateai.interfaces.http.routers import users as _r_users  # noqa: E402
app.include_router(_r_users.router)


from mateai.interfaces.http.routers import health as _r_health  # noqa: E402
app.include_router(_r_health.router)


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import system as _r_system  # noqa: E402
app.include_router(_r_system.router)


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 88: TOPOLOGY API (<10ms, in-memory, zero blocking) ────────────────
# ═══════════════════════════════════════════════════════════════════════════



from mateai.interfaces.http.routers import voice as _r_voice  # noqa: E402
app.include_router(_r_voice.router)


# ---------------------------------------------------------------------------
# Phase 34: Conversational Memory Diagnostics & Management APIs
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import memory as _r_memory  # noqa: E402
app.include_router(_r_memory.router)


from mateai.interfaces.http.routers import tts as _r_tts  # noqa: E402
app.include_router(_r_tts.router)



# In-memory cache for voice list (populated on first request)
# ---------------------------------------------------------------------------
# System Logs Endpoints (Phase 21.1)
# ---------------------------------------------------------------------------

from mateai.interfaces.http.routers import logs as _r_logs  # noqa: E402
app.include_router(_r_logs.router)



# ---------------------------------------------------------------------------
# Proxy Model List  — Fetch available models from a proxy (9router / LMStudio)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Config API — Read & Write config.json
# ---------------------------------------------------------------------------

from mateai.interfaces.http.secret_masking import (  # noqa: E402
    _SECRET_MASK,
    _is_secret_field,
    _mask_secrets,
    _restore_masked_secrets,
)


# ---------------------------------------------------------------------------
# Skills Registry API
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import skills as _r_skills  # noqa: E402
app.include_router(_r_skills.router)


# ---------------------------------------------------------------------------
# Pairing Code Endpoints (6-Digit Dynamic Robot Sync)
# ---------------------------------------------------------------------------

from mateai.interfaces.http.routers import pairing as _r_pairing  # noqa: E402
app.include_router(_r_pairing.router)


# ---------------------------------------------------------------------------
# WebSocket — Full Audio Pipeline for ESP32 / Xiaozhi (Multi-Node Hardware Multiplexing)
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import websockets as _r_websockets  # noqa: E402
app.include_router(_r_websockets.router)


# ---------------------------------------------------------------------------
# Portal UI Realtime WebSocket (Phase 17)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Phase 33: VN-MateAI Standby HUD WebSocket
# ---------------------------------------------------------------------------

from mateai.interfaces.http import ws_auth  # noqa: E402




# ---------------------------------------------------------------------------
# Phase 92: Voice-to-Voice Full-Duplex Pipeline Streaming WebSocket (TTFA < 800ms)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Enterprise Orchestrator — WebSocket & REST for LAN Client Agents
# ---------------------------------------------------------------------------


# Bao lâu thì coi worker là "không lên được" rồi tự dừng. Đủ dài cho tiến
# trình Python khởi động, import thư viện và mở WebSocket trên máy này.
from mateai.interfaces.http.routers import workers as _r_workers  # noqa: E402
app.include_router(_r_workers.router)


from mateai.interfaces.http.routers import clients as _r_clients  # noqa: E402
app.include_router(_r_clients.router)


from mateai.interfaces.http.routers import hud as _r_hud  # noqa: E402
app.include_router(_r_hud.router)


# ---------------------------------------------------------------------------
# Phase 43: Xiaozhi Desktop Companion & Autonomous Sentinel APIs
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import xiaozhi as _r_xiaozhi  # noqa: E402
app.include_router(_r_xiaozhi.router)


from mateai.interfaces.http.routers import sentinel as _r_sentinel  # noqa: E402
app.include_router(_r_sentinel.router)


# ---------------------------------------------------------------------------
# Zero-Trust Security & Audit APIs (Phase 9)
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import security as _r_security  # noqa: E402
app.include_router(_r_security.router)


# ---------------------------------------------------------------------------
# Phase 37.5: Portable & Scalable ChromaDB Vector Database Endpoints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Phase 38: Native File System & OS Toolkit REST Endpoints
# ---------------------------------------------------------------------------

from mateai.interfaces.http.routers import files as _r_files  # noqa: E402
app.include_router(_r_files.router)



# ---------------------------------------------------------------------------
# Phase 11: Lean Micro-Tasking & KPI Tracking Endpoints
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import tasks as _r_tasks  # noqa: E402
app.include_router(_r_tasks.router)


# ---------------------------------------------------------------------------
# Phase 16: Microphone Hardware Toggle API
# ---------------------------------------------------------------------------


from mateai.interfaces.http.routers import wake_word as _r_wake_word  # noqa: E402
app.include_router(_r_wake_word.router)


# ===========================================================================
# Phase 18: Domain Sync & Telegram Gateway Endpoints
# ===========================================================================


from mateai.interfaces.http.routers import domain as _r_domain  # noqa: E402
app.include_router(_r_domain.router)


from mateai.interfaces.http.routers import telegram as _r_telegram  # noqa: E402
app.include_router(_r_telegram.router)


# ===========================================================================
# Phase 28: Enterprise Reporting & Template Engine
# ===========================================================================


from mateai.interfaces.http.routers import report_templates as _r_report_templates  # noqa: E402
app.include_router(_r_report_templates.router)


# ===========================================================================
# Phase 20: Dynamic Client Agent Distribution
# ===========================================================================


# ═══════════════════════════════════════════════════════════════════════════
# Phase 48 — ITSM, Audit Trail, ROI Dashboard API Endpoints
# ═══════════════════════════════════════════════════════════════════════════


from mateai.interfaces.http.routers import itsm as _r_itsm  # noqa: E402
app.include_router(_r_itsm.router)


from mateai.interfaces.http.routers import analytics as _r_analytics  # noqa: E402
app.include_router(_r_analytics.router)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 56 & 57: ENTERPRISE OS — CEO EXECUTIVE BI & MULTI-AGENT APIS
# ═══════════════════════════════════════════════════════════════════════════════

from mateai.interfaces.http.routers import enterprise as _r_enterprise  # noqa: E402
app.include_router(_r_enterprise.router)


# ---------------------------------------------------------------------------
# Phase 59/60: Read-only Endpoints cho Command Center
# ---------------------------------------------------------------------------
# Giao diện Trung Tâm Chỉ Huy cần đọc trạng thái nền tảng. Trước đây các
# hàm JS gọi nhầm `/api/v1/skills/execute` với `check_connector_health` để
# lấy dữ liệu, dẫn tới hiển thị sai (mọi ô đều báo lỗi giống nhau) và tốn
# thêm một vòng gọi tool nặng cho một thao tác chỉ đọc.
# Các endpoint dưới đây chỉ đọc trạng thái trong bộ nhớ, không gọi ra ngoài.


# Nhãn hiển thị + mô tả cho từng connector. Tách riêng khỏi base_connector vì
# đây là thứ CHỈ giao diện dùng; lõi connector không biết tới chuyện hiển thị.


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 62: Data source tùy chỉnh ────────────────────────────────────────
# Cho phép khai báo app doanh nghiệp mới (MISA, Odoo, KiotViet, SAP...) chỉ
# bằng API, không cần viết connector Python. Xem `core/connectors/
# custom_registry.py` để hiểu vì sao lưu file riêng thay vì nhét config.json.
# ═══════════════════════════════════════════════════════════════════════════




# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 65: Phiên hội thoại HUD ─────────────────────────────────────────
# HUD cần biết Ly Ly đang chờ admin đáp, và cần một cách nói "admin không
# trả lời, hãy hỏi lại" mà không phải tự dựng lại chuỗi đã nói.
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 64: Hàng đợi tải file (AI xuất báo cáo, giao diện tự tải) ────────
# AI dựng file rồi xếp vào hàng đợi; giao diện thấy là tải về máy bằng phiên
# đăng nhập sẵn có. Không có endpoint nào ở đây trả nội dung file trong JSON —
# xem `core/download_queue.py` để hiểu vì sao không đưa link có token cho AI.
# ═══════════════════════════════════════════════════════════════════════════


# ===========================================================================
# Phase 90: Computer-Use & Self-Healing Worker Console APIs
# ===========================================================================

from mateai.interfaces.http.routers import computer_use as _r_computer_use  # noqa: E402
app.include_router(_r_computer_use.router)


# ---------------------------------------------------------------------------
# Shutdown Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """Graceful shutdown: stop background workers, HITL cleanup, etc."""
    logger.info("Shutdown initiated — stopping background services...")

    # Phase 60: Stop Background Worker Manager
    try:
        from mateai.application.operations.background_workers import background_worker_manager
        await background_worker_manager.stop(timeout=10.0)
        logger.info("Phase 60: Background Worker Manager stopped.")
    except Exception as e:
        logger.warning("Phase 60: Background Worker Manager shutdown error: %s", e)

    # Phase 57: Stop Autonomous Sentinel
    try:
        from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
        autonomous_sentinel.stop()
        logger.info("Phase 57: Autonomous Sentinel stopped.")
    except Exception:
        pass

    # Phase 18: Stop Telegram Gateway
    try:
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        telegram_gateway.stop()
        logger.info("Phase 18: Telegram Gateway stopped.")
    except Exception:
        pass

    logger.info("VN-MateAI shutdown complete.")

