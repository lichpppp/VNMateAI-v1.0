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


async def _broadcast_thinking(state: str, text: str = "", query: str = "") -> None:
    """Phase 87 — báo HUD về QUÁ TRÌNH SUY NGHĨ của model.

    Model suy luận ở field `reasoning`, tách hẳn khỏi `content` — nên câu trả
    lời bạn nghe không bị lẫn suy nghĩ, nhưng trước đây phần suy nghĩ bị vứt
    đi hoàn toàn. Giờ nó được gom gọn (`_compact_reasoning`) rồi đẩy sang HUD
    để hiện trong khung gập lại được.

    `state`:
      - "thinking" → model đang suy nghĩ, chưa có nội dung (hiện vòng xoay)
      - "done"     → suy nghĩ đã xong, `text` là nội dung đã gọn
      - "empty"    → không có suy nghĩ để hiện (lỗi, hoặc model không suy luận)
    """
    await broadcast_hud({
        "type": "thinking",
        "status": state,
        "text": text or "",
        "query": query or "",
        "timestamp": datetime.utcnow().isoformat(),
    })


from mateai.config.loader import get_assistant_name  # noqa: E402


from mateai.interfaces.http import speech  # noqa: E402


#: Task đang xử lý lệnh thoại, theo phiên. Lệnh mới tới sẽ HUỶ task cũ.
#:
#: Phase 81: trước đây mỗi lệnh tạo một `asyncio.create_task()` rồi bỏ mặc.
#: Nên khi người dùng nói lệnh thứ hai, lượt thứ nhất vẫn chạy tiếp: vẫn gọi
#: LLM, vẫn sinh TTS, vẫn đẩy `voice_active` xuống HUD. HUD phát hết rồi
#: câu mới mới lên tiếng — người dùng nói xong vẫn phải nghe tiếp, đúng triệu
#: chứng "ra lệnh mà AI không dừng".
#:
#: Huỷ task cũ là đủ: `asyncio.CancelledError` ném ra giữa `await` nên vòng
#: lặp stream LLM và `broadcast_hud` phía sau không chạy nữa.
_hud_voice_tasks: Dict[str, "asyncio.Task"] = {}


def _cancel_hud_voice_task(session_id: str) -> bool:
    """Huỷ lượt đang xử lý của phiên. True nếu có thật sự huỷ được."""
    task = _hud_voice_tasks.get(session_id)
    if task is None or task.done():
        _hud_voice_tasks.pop(session_id, None)
        return False
    task.cancel()
    return True


async def _process_hud_voice_command(cmd_query: str, session_id: str = "hud", *, caller: str) -> None:
    """
    Bọc lượt thoại, tự dọn sổ task khi xong.

    `finally` là chỗ duy nhất đảm bảo sổ không giữ task chết. Hàm thân có
    nhiều nhánh `return` sớm; dọn ở từng nhánh thì sót nhánh là sổ giữ task đã
    chết, và lệnh sau tới sẽ đi huỷ một task vô hại rồi tưởng đã dừng được lượt
    cũ — sai. Ở đây `finally` chạy ở MỌI đường thoát, kể cả `CancelledError`
    do lệnh mới huỷ, nên không sót đường nào.
    """
    task = asyncio.current_task()
    _hud_voice_tasks[session_id] = task  # type: ignore[assignment]
    try:
        await _process_hud_voice_command_body(cmd_query, session_id, caller=caller)
    finally:
        # Chỉ xoá nếu sổ vẫn đang trỏ tới CHÍNH mình. Lệnh mới tới đã ghi đè
        # sổ rồi, xoá vô điều kiện sẽ làm mất task của lượt đang chạy.
        if _hud_voice_tasks.get(session_id) is task:
            _hud_voice_tasks.pop(session_id, None)


class _HudVoiceSink:
    """Đầu ra của HUD (/ws/hud): chữ + audio binary, đồng bộ portal.

    Chữ của một câu được gửi CÙNG LÚC với audio của câu đó (chữ bám theo tiếng);
    TTS lỗi thì vẫn gửi chữ, chỉ không có tiếng.
    """

    def __init__(self, cmd_query: str) -> None:
        self.cmd_query = cmd_query
        self.display_text = ""

    async def on_status(self, status: str, **info: Any) -> None:
        if status == "speaking":
            reasoning = info.get("reasoning") or ""
            await _broadcast_thinking("done" if reasoning else "empty", reasoning, self.cmd_query)

    async def on_sentence(self, seq: int, text: str, display_text: str, **info: Any) -> None:
        self.display_text = display_text

    async def on_audio(self, seq: int, audio: bytes, text: str, kind: str, **info: Any) -> None:
        packet: Dict[str, Any] = {
            "type": "voice_active",
            "status": "speaking",
            "text": text,
            "source_device": "hud",
            "timestamp": datetime.utcnow().isoformat(),
        }
        if kind in ("filler", "ack"):
            packet["is_filler"] = True
        else:
            packet["display_text"] = self.display_text or text
            packet["query"] = self.cmd_query
        await broadcast_hud(packet)
        if audio and len(audio) > 100:
            await broadcast_hud_binary(audio)
        if kind == "speech":
            await broadcast_portal_ui("voice_response", {
                "query": self.cmd_query,
                "reply": text,
                "display_text": self.display_text or text,
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })


async def _process_hud_voice_command_body(cmd_query: str, session_id: str = "hud", *, caller: str) -> None:
    """
    Một lượt nói của HUD. Nghiệp vụ ở mateai.application.voice.voice_turn.process_voice_turn (dùng
    chung mọi kênh); ở đây chỉ còn phần riêng của HUD: câu "thôi/dừng" khi đang
    chờ trả lời, lời đệm sau 1s, trạng thái chờ admin trả lời, về idle.
    """
    from mateai.application.voice.voice_session import voice_sessions, is_stop_reply, looks_like_question
    from mateai.application.conversation.memory_manager import detect_and_handle_context_lifecycle
    from mateai.application.voice.voice_turn import process_voice_turn

    # Ephemeral Data Lifecycle
    detect_and_handle_context_lifecycle(session_id, cmd_query)

    session = voice_sessions.get(session_id)
    if is_stop_reply(cmd_query) and session.expecting_reply:
        session.clear_expecting_reply()
        await broadcast_hud({
            "type": "voice_active", "status": "idle",
            "text": "Đã dừng. Em không hỏi gì nữa ạ.",
            "source_device": "hud", "session_id": session_id,
            "timestamp": datetime.utcnow().isoformat(),
        })
        logger.info("[HUD] Admin dừng hội thoại tại phiên %s", session_id)
        return

    session.clear_expecting_reply()
    logger.info("Standby HUD WS voice command: '%s' (phiên %s)", cmd_query[:100], session_id)

    await broadcast_hud({
        "type": "voice_active",
        "status": "listening",
        "text": cmd_query,
        "source_device": "hud",
        "timestamp": datetime.utcnow().isoformat(),
    })
    await _broadcast_thinking("thinking", query=cmd_query)

    sink = _HudVoiceSink(cmd_query)
    t_start = time.perf_counter()
    try:
        result = await process_voice_turn(
            cmd_query,
            sink=sink,
            session_id=session_id,
            source_device="hud",
            # RBAC theo người đã đăng nhập trên HUD, không theo nhãn "hud".
            caller=caller,
            filler_after_s=1.0,  # Phase 67/70: lời đệm chỉ khi câu thật chưa về sau 1s
        )
    except asyncio.CancelledError:
        logger.info("[HUD/Stream] Task bị huỷ (lệnh mới đến)")
        raise
    except Exception as exc:
        logger.error("[HUD/Stream] Lỗi lượt nói: %s", exc, exc_info=True)
        await _broadcast_thinking("empty", query=cmd_query)
        return

    logger.info(
        "[HUD/Stream] Hoàn tất lượt nói sau %.2fs (%d câu, %d câu đệm)",
        time.perf_counter() - t_start, len(result.sentences), 1 if result.filler_played else 0,
    )

    said = result.reply_text.strip()
    if said:
        waiting = looks_like_question(said)
        if waiting:
            session.mark_expecting_reply(said)
        else:
            session.clear_expecting_reply()
        await broadcast_hud({
            "type": "voice_state",
            "session_id": session_id,
            "expecting_reply": waiting,
            "question": said if waiting else "",
            "reask_count": session.reask_count,
            "timestamp": datetime.utcnow().isoformat(),
        })
    else:
        await _broadcast_thinking("empty", query=cmd_query)

    # Đặt HUD về idle sau khi ước tính xong thời gian nói
    async def _reset_hud_idle(delay: float) -> None:
        await asyncio.sleep(delay)
        await broadcast_hud({
            "type": "voice_active",
            "status": "idle",
            "text": "",
            "source_device": "hud",
            "timestamp": datetime.utcnow().isoformat(),
        })

    est_duration = max(4.0, (len(said) / 15.0) + 1.8)
    asyncio.create_task(_reset_hud_idle(est_duration))


def _get_hud_metrics_payload() -> Dict[str, Any]:
    """
    Tổng hợp telemetry phần cứng, mạng và clients cho Standby HUD.

    Phase 76: KHÔNG còn trường quyền hạn (`security_role` / `security_status` /
    `permission_level`). Bản cũ trả cứng "ADMIN / ZERO-TRUST SENTINEL / FULL
    UNRESTRICTED" cho MỌI kết nối — kể cả khi chưa đăng nhập — vì đây là payload
    broadcast chung, không biết ai đang xem. Vai trò thật nay được gửi riêng
    trong gói `hud_welcome` của từng kết nối (xem `websocket_hud_endpoint`).

    Số đo thiếu được trả `None` (JSON null) để giao diện hiện "chờ kết nối",
    tuyệt đối không bịa giá trị thay thế.
    """
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    vmem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    hw = SYSTEM_HEALTH_CACHE.get("hardware", {})

    # Đĩa: lấy từ cache health; cache còn giá trị mặc định 0.0 (chưa đo) thì
    # đo trực tiếp bằng psutil. Nếu psutil cũng lỗi mới trả None → giao diện
    # hiện "chờ kết nối". Trước đây nhánh lỗi trả 45.0 — một con số bịa.
    disk_pct = hw.get("disk_percent")
    disk_free_gb = hw.get("disk_free_gb")
    disk_total_gb = hw.get("disk_total_gb")
    if not disk_pct or not disk_free_gb or not disk_total_gb:
        try:
            disk_root = psutil.disk_usage("/")
            disk_pct = disk_root.percent
            disk_free_gb = round(disk_root.free / (1024 ** 3), 2)
            disk_total_gb = round(disk_root.total / (1024 ** 3), 2)
        except Exception:
            logger.warning("HUD: không đọc được dung lượng đĩa — trả null thay vì số bịa.")
            disk_pct = None
            disk_free_gb = None
            disk_total_gb = None

    # Xung nhịp CPU: cache 0.0 nghĩa là chưa đo được → thử psutil, không được thì None.
    cpu_freq_mhz = hw.get("cpu_freq_mhz")
    if not cpu_freq_mhz:
        try:
            freq = psutil.cpu_freq()
            cpu_freq_mhz = round(freq.current, 0) if freq else None
        except Exception:
            cpu_freq_mhz = None

    procs = len(psutil.pids())
    connected_clients = len(orchestrator.get_connected_clients())
    skills_count = plugin_manager.get_skill_count()
    skills_enabled = len(plugin_manager.get_all_tools())

    return {
        "cpu_percent": round(cpu, 1),
        "cpu_cores": hw.get("cpu_cores", psutil.cpu_count(logical=True) or 1),
        "cpu_freq_mhz": cpu_freq_mhz,
        "ram_percent": round(vmem.percent, 1),
        "ram_used_gb": round(vmem.used / (1024**3), 2),
        "ram_total_gb": round(vmem.total / (1024**3), 2),
        "disk_percent": None if disk_pct is None else round(disk_pct, 1),
        "disk_free_gb": disk_free_gb,
        "disk_total_gb": disk_total_gb,
        "processes_count": procs,
        "connected_clients": connected_clients,
        "active_audio_hardware": len(active_audio_nodes),
        "active_web_clients": len(active_portal_websockets),
        "skills_count": skills_count,
        "skills_enabled": skills_enabled,
        "net_sent_mbps": hw.get("net_sent_mbps"),
        "net_recv_mbps": hw.get("net_recv_mbps"),
        "timestamp": datetime.utcnow().isoformat(),
    }


async def _hud_telemetry_loop() -> None:
    """Phase 33 & 51: Vòng lặp phát số liệu telemetry đầy đủ tới Standby HUD mỗi 2 giây."""
    while True:
        try:
            if active_hud_websockets:
                payload = _get_hud_metrics_payload()
                await broadcast_hud({
                    "type": "metrics_update",
                    "data": payload,
                })
        except Exception as exc:
            logger.debug("HUD telemetry loop error: %s", exc)
        await asyncio.sleep(2.0)


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
            if token and _is_valid_worker_token(token):
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



class VoiceCommandRequest(BaseModel):
    """Payload for POST /api/v1/voice-command (text-based REST)."""
    query: str = Field(..., min_length=1, max_length=2000,
                       description="Câu lệnh giọng nói đã bóc băng (STT output).",
                       examples=["Khởi động lại dịch vụ IIS"])
    session_id: Optional[str] = Field(default=None)
    source_device: Optional[str] = Field(
        default="web",
        description="Định danh nguồn gửi lệnh: 'web', 'telegram:<chat_id>', hoặc tên thiết bị.",
    )
    history: Optional[List[Dict[str, Any]]] = Field(
        default=None,
        description="Lịch sử hội thoại trước (list các message dict role/content) để duy trì ngữ cảnh đa lượt.",
    )
    include_audio: bool = Field(
        default=False,
        description="Nếu true, trả về audio TTS dạng base64 trong response JSON.",
    )


class VoiceCommandResponse(BaseModel):
    """Response for POST /api/v1/voice-command."""
    success: bool
    reply: str = Field(description="Văn bản hiển thị chi tiết (Markdown) trên giao diện.")
    speech_reply: Optional[str] = Field(default=None, description="Văn bản tóm tắt tự nhiên để phát qua giọng nói TTS (Phase 34).")
    tool_calls_made: list = Field(default_factory=list)
    requires_confirmation: bool = Field(default=False)
    error: Optional[str] = None
    session_id: Optional[str] = None
    audio_base64: Optional[str] = Field(
        default=None,
        description="Base64-encoded MP3 audio (chỉ khi include_audio=true).",
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Phase 87: quá trình suy nghĩ của model (đã gọn), tách khỏi câu trả lời.",
    )


class HudSimulateRequest(BaseModel):
    """Payload for POST /api/v1/hud/simulate."""
    type: str = Field(default="voice_active", description="voice_active | system_log | metrics_update")
    status: Optional[str] = Field(default="speaking", description="listening | processing | speaking | idle")
    text: Optional[str] = Field(default="Xin chào, em là Ly Ly. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.")
    level: Optional[str] = Field(default="INFO")
    message: Optional[str] = Field(default="Sentinel Guard: Kiểm tra an ninh định kỳ hoàn tất.")
    data: Optional[Dict[str, Any]] = None


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
        asyncio.create_task(_hud_telemetry_loop())
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



@app.post(
    "/api/v1/voice-command",
    response_model=VoiceCommandResponse,
    summary="Process a transcribed voice command (REST)",
    tags=["Voice RPA"],
)
async def voice_command(
    payload: VoiceCommandRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> VoiceCommandResponse:
    """
    Receive a pre-transcribed text query, run through the LLM agentic loop,
    execute skills, and return a TTS-ready natural-language response.
    Phase 25: Passes source_device and history through to ask_async for
    proper StateManager matching and multi-turn conversation continuity.
    Optionally includes base64-encoded audio in the response.
    """
    from mateai.application.agent.llm_engine import llm_engine

    source_device = payload.source_device or "web"

    logger.info(
        "REST voice command [session=%s, device=%s]: '%s'",
        payload.session_id or "anon",
        source_device,
        payload.query[:120],
    )

    # Phase 33: Notify Standby HUD of incoming voice query
    await broadcast_hud({
        "type": "voice_active",
        "status": "listening",
        "text": payload.query,
        "timestamp": datetime.utcnow().isoformat(),
    })
    await broadcast_hud({
        "type": "voice_active",
        "status": "processing",
        "text": "Đang phân tích câu lệnh & truy xuất kỹ năng...",
        "timestamp": datetime.utcnow().isoformat(),
    })
    # Phase 87: báo HUD bắt đầu suy nghĩ. Đường REST này đi qua vòng agentic
    # (ask_async) nên không có suy nghĩ từng bước — chỉ có bản tổng sau cùng.
    await _broadcast_thinking("thinking", query=payload.query)

    try:
        # Phase 25 & 34: Use ask_async directly with session_id for Sliding Window Conversational Memory
        result = await llm_engine.ask_async(
            query=payload.query,
            source_device=source_device,
            history=payload.history,
            session_id=payload.session_id or source_device,
            # RBAC theo người đã đăng nhập, KHÔNG theo source_device do client tự
            # khai (gửi source_device="hud" từng đủ để nhận quyền admin).
            caller=str(user.get("username") or user.get("sub") or "anonymous"),
        )
        display_reply: str = result.get("reply", "")
        # Phase 87: suy nghĩ của lượt này đi kèm trong kết quả. Đọc từ đây
        # chứ không phải thuộc tính chung — lượt song song sẽ ghi đè lẫn nhau.
        await _broadcast_thinking(
            "done" if result.get("reasoning") else "empty",
            result.get("reasoning", ""),
            payload.query,
        )
        speech_reply: str = result.get("speech_reply") or sanitise_for_tts(display_reply)
        if not speech_reply:
            if result.get("success"):
                speech_reply = "Em đã thực hiện xong yêu cầu của bạn."
                display_reply = display_reply or speech_reply
            else:
                err = result.get("error", "")
                speech_reply = f"Xin lỗi, em gặp lỗi: {err[:80]}" if err else "Em không thể thực hiện yêu cầu này."
                display_reply = display_reply or speech_reply
        tool_calls_made = result.get("tool_calls_made", [])
        requires_confirmation = result.get("requires_confirmation", False)
    except Exception:  # pylint: disable=broad-except
        logger.error("voice_command error:\n%s", traceback.format_exc())
        # Phase 87: lỗi -> tắt vòng xoay suy nghĩ trên HUD, không để nó quay mãi.
        await _broadcast_thinking("empty", query=payload.query)
        raise HTTPException(status_code=500, detail="Lỗi xử lý nội bộ.")

    ai_name = get_assistant_name()
    await broadcast_hud({
        "type": "voice_active",
        "status": "processing",
        "text": f"Đang tổng hợp giọng nói {ai_name}...",
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Phase 34: Synthesise TTS audio from speech_reply (concise natural speech)
    audio_b64: Optional[str] = None
    if payload.include_audio and speech_reply:
        try:
            import base64
            # Nhánh fallback: cũng bọc timeout để TTS treo không làm treo lượt nói.
            audio_bytes = await speech.tts_bytes(speech_reply)
            if audio_bytes:
                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("TTS synthesis for REST response failed: %s", exc)

    # Broadcast security approval required if action needs confirmation
    if requires_confirmation:
        tool_name = tool_calls_made[0].get("skill", "") if tool_calls_made else "Tác vụ hệ thống"
        await broadcast_hud({
            "type": "security_approval_required",
            "action_id": source_device,
            "skill": tool_name,
            "query": payload.query,
            "message": speech_reply,
            "timestamp": datetime.utcnow().isoformat(),
        })

    # NOW broadcast to Standby HUD with speech_reply, display_reply and audio_base64!
    await broadcast_hud({
        "type": "voice_active",
        "status": "speaking",
        "text": speech_reply,
        "display_text": display_reply,
        "query": payload.query,
        "audio_base64": audio_b64,
        "source_device": source_device,
        "timestamp": datetime.utcnow().isoformat(),
    })

    await broadcast_portal_ui("voice_response", {
        "query": payload.query,
        "reply": display_reply,
        "speech_reply": speech_reply,
        "audio_base64": audio_b64,
        "source_device": source_device,
        "timestamp": datetime.utcnow().isoformat(),
    })

    async def _reset_hud_idle(delay: float = 6.0):
        await asyncio.sleep(delay)
        await broadcast_hud({
            "type": "voice_active",
            "status": "idle",
            "text": "",
            "timestamp": datetime.utcnow().isoformat(),
        })

    # Estimate duration: ~15 chars/second + 2s buffer
    est_duration = max(4.0, (len(speech_reply) / 15.0) + 1.8)
    asyncio.create_task(_reset_hud_idle(est_duration))

    return VoiceCommandResponse(
        success=True,
        reply=display_reply,
        speech_reply=speech_reply,
        tool_calls_made=tool_calls_made,
        requires_confirmation=requires_confirmation,
        session_id=payload.session_id,
        audio_base64=audio_b64,
        # Phase 87: suy nghĩ của lượt này, để client đọc được của đúng lượt
        # thay vì đọc thuộc tính chung (lượt song song ghi đè lẫn nhau).
        reasoning=result.get("reasoning") or None,
    )


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


async def _handle_audio_stream(websocket: WebSocket, device_id: str) -> None:
    """
    Xử lý luồng âm thanh WebSocket thời gian thực cho từng mạch ESP32 Xiaozhi độc lập.
    Phase 43: Xiaozhi Desktop Companion Protocol (LCD UI, Barge-in, High-Fidelity I2S, Sentinel Wake).

    Zero-Trust: bắt buộc device enrollment secret (hoặc JWT admin/manager) qua
    ``?token=``. Trước đây bất kỳ host nào cũng mở được luồng âm thanh của thiết
    bị — có nghĩa là nghe/ghi được mọi thứ người dùng nói trong nhà.
    """
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway

    if not _authenticate_device(websocket, device_id):
        logger.warning(
            "Từ chối thiết bị '%s' kết nối từ %s: thiếu hoặc sai enrollment token.",
            device_id,
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu enrollment token hợp lệ.")
        return

    await xiaozhi_gateway.handle_client(websocket, device_id)


@app.websocket("/ws/audio-stream/{device_id}")
@app.websocket("/api/v1/xiaozhi/ws/{device_id}")
async def audio_stream_device_ws(websocket: WebSocket, device_id: str) -> None:
    """WebSocket âm thanh đa điểm (Hardware Multiplexing) theo mã thiết bị/phòng."""
    await _handle_audio_stream(websocket, device_id)


@app.websocket("/ws/audio-stream")
@app.websocket("/api/v1/xiaozhi/ws")
async def audio_stream_legacy_ws(websocket: WebSocket) -> None:
    """Endpoint tương thích ngược gán mặc định device_id='esp32-default'."""
    await _handle_audio_stream(websocket, device_id="esp32-default")


# ---------------------------------------------------------------------------
# Portal UI Realtime WebSocket (Phase 17)
# ---------------------------------------------------------------------------


@app.websocket("/ws/portal-ui")
async def websocket_portal_ui(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for real-time Web Portal UI synchronization.
    Supports switch_tab, show_toast, ping/pong, and telemetry broadcasting.

    Zero-Trust: bắt buộc JWT hợp lệ qua ``?token=``. Endpoint này chỉ phục vụ
    portal đã đăng nhập, đồng thời stream log hệ thống và cảnh báo bảo mật —
    trước đây ai cũng mở được và đọc được toàn bộ log.
    """
    ws_user = _authenticate_websocket(websocket)
    if ws_user is None:
        logger.warning(
            "Từ chối Portal-UI WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return

    await websocket.accept()
    active_portal_websockets.add(websocket)
    logger.info(
        "Portal UI WebSocket connected (%d active client tabs, user=%s).",
        len(active_portal_websockets), ws_user.get("username"),
    )
    try:
        # Welcome frame
        await websocket.send_text(_json.dumps({
            "event": "connected",
            "message": "Kết nối WebSocket Portal-UI thành công.",
            "timestamp": datetime.utcnow().isoformat(),
        }, ensure_ascii=False))

        # Push recent log entries so client immediately gets history
        _h = log_stream.get_handler()
        if _h:
            recent_logs = _h.get_recent_logs(limit=150)
            if recent_logs:
                await websocket.send_text(_json.dumps({
                    "event": "log_history",
                    "logs": recent_logs,
                    "count": len(recent_logs),
                }, ensure_ascii=False))

        while True:
            raw_text = await websocket.receive_text()
            try:
                data = _json.loads(raw_text)
            except _json.JSONDecodeError:
                continue

            action = data.get("action") or data.get("event")
            if action == "ping":
                await websocket.send_text(_json.dumps({"event": "pong"}))
            elif action == "switch_tab":
                tab_name = data.get("tab") or data.get("target_tab")
                if tab_name:
                    await broadcast_portal_ui("switch_tab", {"tab": tab_name})
            elif action == "show_toast":
                msg = data.get("message") or data.get("text") or ""
                t_type = data.get("type", "info")
                if msg:
                    await broadcast_portal_ui("show_toast", {"message": msg, "type": t_type})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Portal UI WebSocket error: %s", exc)
    finally:
        active_portal_websockets.discard(websocket)
        logger.info("Portal UI WebSocket disconnected. (%d remaining).", len(active_portal_websockets))


# ---------------------------------------------------------------------------
# Phase 33: VN-MateAI Standby HUD WebSocket
# ---------------------------------------------------------------------------

# Role được phép phê duyệt hành động rủi ro cao qua WebSocket.
WS_APPROVER_ROLES = ("admin", "manager")

from mateai.interfaces.http import enrollment  # noqa: E402


def _authenticate_device(websocket: WebSocket, device_id: str = "esp32-default") -> bool:
    """
    Xác thực thiết bị ESP32 trước khi cho stream âm thanh.

    Chấp nhận (một trong ba):
      0. Token RIÊNG của đúng `device_id` này (bảng device_tokens, cấp ở
         POST /api/v1/security/devices). Lộ token của robot A không giả được robot B.
      1. Device enrollment secret DÙNG CHUNG (tương thích firmware cũ) — trừ khi
         `security.require_per_device_token` = true trong config.json. — header `Authorization: Bearer <token>` (chuẩn
         firmware xiaozhi-esp32) hoặc `?token=`. Lấy ở /api/v1/security/device-enrollment-token,
         dán vào DEFAULT_DEVICE_TOKEN của firmware.
      2. JWT của tài khoản admin/manager (debug thủ công).

    Không còn nhánh "Zero-Config LAN": trước đây mọi IP nội bộ được nhận KHÔNG cần
    token, lại tự đặt device_id trên URL (id `esp32*`/`xiaozhi*` được quyền admin)
    → mọi máy trong LAN/Wi-Fi văn phòng ra lệnh được với quyền admin.
    """
    token = websocket.query_params.get("token")
    if not token:
        auth_hdr = websocket.headers.get("authorization", "")
        if auth_hdr.lower().startswith("bearer "):
            token = auth_hdr[7:].strip()

    if token:
        from mateai.infrastructure.database.db_manager import db_manager
        try:
            if db_manager.verify_device_token(device_id, token):
                return True
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[Xiaozhi] Lỗi kiểm token thiết bị '%s': %s", device_id, exc)

        expected = enrollment.get_device_enrollment_secret()
        if expected and secrets.compare_digest(token, expected):
            from mateai.config.loader import get_config_section
            if get_config_section("security").get("require_per_device_token", False):
                logger.warning("[Xiaozhi] Từ chối '%s': token dùng chung đã bị tắt "
                               "(security.require_per_device_token).", device_id)
                return False
            logger.warning("[Xiaozhi] Thiết bị '%s' dùng token CHUNG — hãy cấp token riêng "
                           "(POST /api/v1/security/devices).", device_id)
            return True

        try:
            payload = auth_manager.decode_access_token(token)
            if payload and "sub" in payload:
                user = auth_manager.get_user(payload["sub"])
                if bool(user) and user.get("role") in WS_APPROVER_ROLES:
                    return True
        except Exception:
            pass

    return False


def _authenticate_worker(websocket: WebSocket) -> bool:
    """
    Xác thực một LAN worker trước khi cho đăng ký vào /ws/client.

    Chấp nhận một trong hai:
      1. Enrollment secret do Master Server phát lúc tải agent.
      2. JWT của tài khoản admin/manager (tiện cho việc debug thủ công).

    Trả về True nếu hợp lệ.
    """
    return _is_valid_worker_token(websocket.query_params.get("token") or "")


def _is_valid_worker_token(token: str) -> bool:
    """Enrollment secret của worker, hoặc JWT của admin/manager."""
    if not token:
        return False

    expected = enrollment.get_worker_enrollment_secret()
    if expected and secrets.compare_digest(token, expected):
        return True

    # Dự phòng: JWT của admin/manager
    try:
        payload = auth_manager.decode_access_token(token)
    except Exception:
        return False
    if not payload or "sub" not in payload:
        return False
    user = auth_manager.get_user(payload["sub"])
    return bool(user) and user.get("role") in WS_APPROVER_ROLES


def _authenticate_websocket(websocket: WebSocket) -> Optional[dict]:
    """
    Xác thực WebSocket bằng JWT truyền qua query param ``?token=``.

    Trả về dict thông tin người dùng đã xác thực, hoặc None nếu thiếu/sai token.

    Lưu ý: browser WebSocket API không cho gắn header Authorization, nên query param
    là cách duy nhất phía client. Đổi lại token có thể lọt vào access log — vì vậy các
    endpoint nhạy cảm nên kiểm tra thêm role (xem WS_APPROVER_ROLES).
    """
    token = websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = auth_manager.decode_access_token(token)
    except Exception:
        return None
    if not payload or "sub" not in payload:
        return None
    user = auth_manager.get_user(payload["sub"])
    if not user:
        return None
    return {
        "id": user.get("id", f"usr_{user['username']}"),
        "username": user["username"],
        "full_name": user.get("full_name", user["username"]),
        "role": user.get("role", "viewer"),
    }


@app.websocket("/ws/topology")
async def websocket_topology_endpoint(websocket: WebSocket) -> None:
    """
    Phase 88: WebSocket cho giao diện Topology & Real-time Live Flow Animation.
    Nhận sự kiện tool_executed và đồng bộ trạng thái đường nối trên canvas.

    Zero-Trust: bắt buộc JWT (?token=). Trước đây ai cũng xem được luồng tool
    đang chạy và bơm sự kiện "trigger" giả lên sơ đồ.
    """
    if _authenticate_websocket(websocket) is None:
        logger.warning(
            "Từ chối Topology WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return
    await websocket.accept()
    active_topology_websockets.add(websocket)
    logger.info("Topology viewer connected (%d active sessions).", len(active_topology_websockets))
    try:
        # Gói tin chào mừng
        await websocket.send_text(_json.dumps({
            "event": "connected",
            "message": "Topology WebSocket synchronized",
            "active_nodes": 11,
            "timestamp": datetime.utcnow().isoformat(),
        }, ensure_ascii=False))

        while True:
            data = await websocket.receive_text()
            try:
                msg = _json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_text(_json.dumps({"event": "pong"}))
                elif msg.get("event") == "trigger":
                    src = msg.get("source", "core")
                    tgt = msg.get("target", "plugin_m365")
                    await broadcast_topology_event(src, tgt, msg.get("action", ""))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("Topology websocket disconnected: %s", exc)
    finally:
        active_topology_websockets.discard(websocket)
        logger.info("Topology viewer disconnected (%d remaining).", len(active_topology_websockets))


@app.websocket("/ws/hud")
async def websocket_hud_endpoint(websocket: WebSocket) -> None:
    """
    Phase 33: WebSocket endpoint for VN-MateAI Sci-Fi Standby HUD.
    Pushes real-time audio visualizer state, speech typewriter text,
    system telemetry metrics and streaming terminal logs.

    Zero-Trust: client phải nối kèm JWT hợp lệ (?token=). Kết nối không token vẫn
    được phép để xem telemetry, nhưng mọi hành động đặc quyền (confirm_action) sẽ bị
    từ chối. Trước đây endpoint này tự cấp quyền admin cho bất kỳ ai gửi
    confirm_action — không cần đăng nhập.
    """
    await websocket.accept()

    # Xác thực (nếu có) — browser WebSocket API không cho set header Authorization,
    # nên token được truyền qua query param.
    ws_user = _authenticate_websocket(websocket)

    active_hud_websockets.add(websocket)
    logger.info(
        "VN-MateAI HUD connected (%d active HUD displays, authenticated=%s).",
        len(active_hud_websockets),
        ws_user is not None,
    )

    try:
        # 1. Send Welcome Packet with dynamic Assistant Name (default Ly Ly)
        ai_name = get_assistant_name()
        await websocket.send_text(_json.dumps({
            "type": "hud_welcome",
            "message": f"Hệ thống trợ lý AI {ai_name} sẵn sàng. Neural Link established.",
            "assistant_name": ai_name,
            "status": "idle",
            # Phase 76: vai trò THẬT của riêng kết nối này (None nếu chưa đăng nhập).
            # Trước đây quyền hạn bị nhét vào payload telemetry broadcast chung nên
            # luôn hiện "ADMIN" cho mọi người, kể cả khách chưa xác thực.
            "authenticated": ws_user is not None,
            "username": (ws_user or {}).get("username"),
            "role": (ws_user or {}).get("role"),
            "timestamp": datetime.utcnow().isoformat(),
        }, ensure_ascii=False))

        # 2. Push initial rich telemetry immediately
        payload = _get_hud_metrics_payload()
        await websocket.send_text(_json.dumps({
            "type": "metrics_update",
            "data": payload,
        }, ensure_ascii=False))

        # 3. Nếu chưa xác thực, báo HUD biết chỉ ở chế độ xem
        if ws_user is None:
            await websocket.send_text(_json.dumps({
                "type": "auth_required",
                "message": "HUD chưa xác thực: chỉ xem được telemetry, "
                           "các hành động phê duyệt sẽ bị từ chối. "
                           "Hãy đăng nhập trên portal để kích hoạt.",
                "timestamp": datetime.utcnow().isoformat(),
            }, ensure_ascii=False))

        while True:
            raw_text = await websocket.receive_text()
            try:
                data = _json.loads(raw_text)
            except _json.JSONDecodeError:
                continue

            action = data.get("action") or data.get("type")
            if action == "ping":
                await websocket.send_text(_json.dumps({"type": "pong"}))
            elif action == "voice_command":
                cmd_query = (data.get("query") or "").strip()
                if cmd_query and ws_user is None:
                    # Zero-Trust: HUD chưa đăng nhập chỉ xem telemetry. Trước đây
                    # lệnh chạy dưới danh tính "hud" = quyền admin cho bất kỳ ai
                    # mở được cổng 443.
                    await websocket.send_text(_json.dumps({
                        "type": "auth_required",
                        "message": "HUD chưa đăng nhập: không nhận lệnh thoại. "
                                   "Hãy đăng nhập trên portal rồi mở lại HUD.",
                        "timestamp": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False))
                elif cmd_query:
                    # Phase 81: huỷ lượt cũ trước khi nhận lượt mới. HUD đã
                    # dừng phát audio phía trình duyệt, nhưng nếu lượt cũ còn
                    # chạy ở đây thì nó vẫn sinh TTS và đẩy xuống — HUD sẽ phát
                    # tiếp lời của lượt cũ sau khi lượt mới đã bắt đầu, nghe
                    # như hai người nói chồng.
                    if _cancel_hud_voice_task("hud"):
                        logger.info("[HUD] Có lệnh mới — huỷ lượt thoại đang chạy")
                        await broadcast_hud({
                            "type": "voice_active",
                            "status": "listening",
                            "text": "",
                            "interrupted": True,
                            "timestamp": datetime.utcnow().isoformat(),
                        })
                    asyncio.create_task(_process_hud_voice_command(
                        cmd_query, caller=str(ws_user.get("username") or "anonymous")))
            elif action == "confirm_action":
                approved = bool(data.get("approved", True))
                action_id = data.get("action_id")
                skill_name = data.get("skill_name")
                # Zero-Trust: chỉ admin được phê duyệt hành động rủi ro cao — cùng
                # quy tắc với POST /api/v1/security/confirm-action (hàm dưới được gọi
                # thẳng nên Depends(require_roles) của nó KHÔNG chạy ở đây).
                if ws_user is None or ws_user.get("role") != "admin":
                    await websocket.send_text(_json.dumps({
                        "type": "security_approval_rejected",
                        "message": "Yêu cầu phê duyệt bị từ chối: cần đăng nhập "
                                   "với tài khoản admin.",
                        "action_id": action_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False))
                    continue
                from mateai.interfaces.http.routers.security import (
                    ConfirmActionRequest,
                    confirm_action_endpoint,
                )
                confirm_req = ConfirmActionRequest(
                    approved=approved,
                    action_id=action_id,
                    skill_name=skill_name,
                )
                asyncio.create_task(confirm_action_endpoint(
                    payload=confirm_req,
                    current_user=ws_user,
                ))
            elif action == "simulate":
                sim_type = data.get("simulate_type", "voice_active")
                if sim_type == "voice_active":
                    ai_name = get_assistant_name()
                    await broadcast_hud({
                        "type": "voice_active",
                        "status": data.get("status", "speaking"),
                        "text": data.get("text", f"Hệ thống VN-MateAI {ai_name} đang ở trạng thái sẵn sàng cao nhất."),
                        "timestamp": datetime.utcnow().isoformat(),
                    })
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("HUD WebSocket error: %s", exc)
    finally:
        active_hud_websockets.discard(websocket)
        logger.info("VN-MateAI HUD disconnected (%d remaining).", len(active_hud_websockets))


# ---------------------------------------------------------------------------
# Phase 92: Voice-to-Voice Full-Duplex Pipeline Streaming WebSocket (TTFA < 800ms)
# ---------------------------------------------------------------------------


@app.websocket("/ws/voice")
@app.websocket("/ws/v1/voice-stream")
async def websocket_realtime_voice_endpoint(websocket: WebSocket) -> None:
    """
    Phase 1: Realtime Voice WebSocket Endpoint (/ws/voice & /ws/v1/voice-stream).
    Hỗ trợ Event Protocol chuẩn hóa, truyền âm thanh Binary Frame, và Barge-In Cancellation.
    """
    # Zero-Trust: bắt buộc JWT. Trước đây kết nối ẩn danh chạy dưới tên
    # "web_user" (quyền viewer) — vẫn đọc được dữ liệu tổ chức qua tool và tốn
    # chi phí LLM mà không gắn với ai.
    ws_user = _authenticate_websocket(websocket)
    if ws_user is None:
        logger.warning(
            "Từ chối Voice WebSocket từ %s: thiếu hoặc sai token.",
            websocket.client.host if websocket.client else "unknown",
        )
        await websocket.close(code=1008, reason="Unauthorized: thiếu token hợp lệ.")
        return
    from mateai.interfaces.websocket.realtime_voice_ws import handle_realtime_voice_endpoint
    await handle_realtime_voice_endpoint(websocket, user=ws_user)


# ---------------------------------------------------------------------------
# Enterprise Orchestrator — WebSocket & REST for LAN Client Agents
# ---------------------------------------------------------------------------


@app.websocket("/ws/client")
async def websocket_client_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket endpoint for LAN worker nodes (client agents).
    Manages persistent connection, handshake registration, and message routing.

    Zero-Trust: bắt buộc enrollment secret (hoặc JWT admin/manager) qua query param
    ``?token=``. Trước đây bất kỳ máy nào trong LAN cũng đăng ký được làm worker và
    nhận lệnh thực thi skill — tức là remote code execution không cần xác thực.
    """
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

    if not _authenticate_worker(websocket):
        logger.warning(
            "Từ chối worker WebSocket từ %s: thiếu hoặc sai enrollment token.",
            websocket.client.host if websocket.client else "unknown",
        )
        # Chặn ở tầng WebSocket (code 1008 = Policy Violation) trước khi accept.
        await websocket.close(code=1008, reason="Unauthorized: thiếu enrollment token hợp lệ.")
        return

    await websocket.accept()
    client_id = "unknown"
    try:
        # First message is registration handshake
        init_raw = await websocket.receive_text()
        init_data = _json.loads(init_raw)
        client_id = init_data.get("client_id") or (websocket.client.host if websocket.client else "unknown_worker")
        await orchestrator.register_client(client_id, websocket, init_data)

        # Message loop
        while True:
            msg_raw = await websocket.receive_text()
            try:
                msg_data = _json.loads(msg_raw)
            except _json.JSONDecodeError:
                continue
            orchestrator.handle_incoming_message(client_id, msg_data)

    except WebSocketDisconnect:
        logger.info("Worker client [%s] disconnected.", client_id)
    except Exception as exc:
        logger.error("Worker WebSocket error [%s]: %s", client_id, exc)
    finally:
        await orchestrator.unregister_client(client_id)


# Bao lâu thì coi worker là "không lên được" rồi tự dừng. Đủ dài cho tiến
# trình Python khởi động, import thư viện và mở WebSocket trên máy này.
from mateai.interfaces.http.routers import workers as _r_workers  # noqa: E402
app.include_router(_r_workers.router)


from mateai.interfaces.http.routers import clients as _r_clients  # noqa: E402
app.include_router(_r_clients.router)


@app.post(
    "/api/v1/hud/simulate",
    summary="Phase 33: Mô phỏng phát tín hiệu tới VN-MateAI Standby HUD",
    tags=["Orchestrator"],
)
async def simulate_hud_endpoint(
    payload: HudSimulateRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Phát thử gói tin sự kiện xuống toàn bộ màn hình HUD Standby để kiểm tra visualizer."""
    if payload.type == "voice_active":
        ai_name = get_assistant_name()
        pkt = {
            "type": "voice_active",
            "status": payload.status or "speaking",
            "text": payload.text or f"{ai_name} Audio Stream Active",
            "timestamp": datetime.utcnow().isoformat(),
        }
    elif payload.type == "system_log":
        pkt = {
            "type": "system_log",
            "level": payload.level or "INFO",
            "message": payload.message or "System Log Test",
            "timestamp": datetime.utcnow().isoformat(),
        }
    else:
        pkt = {
            "type": payload.type,
            "data": payload.data or {},
            "timestamp": datetime.utcnow().isoformat(),
        }
    await broadcast_hud(pkt)
    return {"status": "success", "sent": pkt, "active_hud_count": len(active_hud_websockets)}


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


@app.get(
    "/api/v1/voice/session/{session_id}",
    summary="Phase 65: Trạng thái phiên hội thoại HUD",
    tags=["Voice Phase 65"],
)
async def api_voice_session(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """HUD gọi để đồng bộ trạng thái sau khi trang bị mất kết nối rồi mở lại."""
    from mateai.application.voice.voice_session import voice_sessions

    return {"status": "success", "session": voice_sessions.get(session_id).to_client()}


@app.post(
    "/api/v1/voice/session/{session_id}/reask",
    summary="Phase 65: Nhắc Ly Ly hỏi lại khi admin im lặng",
    tags=["Voice Phase 65"],
)
async def api_voice_reask(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đếm một lần hỏi lại và trả lại câu hỏi đang chờ để HUD phát lại.

    Số lần hỏi lại do HUD quyết định giới hạn (mặc định 2) — máy chủ chỉ đếm và
    báo lại. Đặt ngưỡng ở đây thì muốn đổi cấu hình phải sửa cả hai đầu.
    """
    from mateai.application.voice.voice_session import voice_sessions

    session = voice_sessions.get(session_id)
    if not session.expecting_reply:
        return {"status": "success", "reask": False, "reason": "Không có câu hỏi đang chờ"}

    count = session.bump_reask()
    return {
        "status": "success",
        "reask": True,
        "reask_count": count,
        "question": session.pending_question,
        "waiting_seconds": round(session.waiting_seconds(), 1),
    }


@app.post(
    "/api/v1/voice/session/{session_id}/close",
    summary="Phase 65: Đóng phiên hội thoại (hết lượt hỏi lại)",
    tags=["Voice Phase 65"],
)
async def api_voice_session_close(
    session_id: str,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đóng lắng nghe: bỏ cờ chờ, giữ lịch sử để lượt sau còn nhớ.

    Xoá hẳn phiên thì Ly Ly quên mất hết và lại hỏi lại từ đầu — đúng cái
    lỗi đang sửa. Nên chỉ tắt trạng thái chờ, không xoá lịch sử.
    """
    from mateai.application.voice.voice_session import voice_sessions

    session = voice_sessions.get(session_id)
    session.clear_expecting_reply()
    return {"status": "success", "closed": True, "session": session.to_client()}


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

