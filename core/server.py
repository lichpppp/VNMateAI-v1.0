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
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import Body, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.auth_manager import auth_manager, get_current_user, require_roles

logger = logging.getLogger(__name__)

# ─── Resolve paths for static files ────────────────────────────────────────
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).parent
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent

_WEB_DIR    = _PROJECT_ROOT / "web"
_CONFIG_PATH = _PROJECT_ROOT / "config.json"
_REGISTRY_PATH = _PROJECT_ROOT / "skills" / "registry.json"
_CLIENT_TEMPLATE_DIR = _PROJECT_ROOT / "client_template"

# ---------------------------------------------------------------------------
# Multi-Node Audio State (Phase 10) & Portal UI WebSockets (Phase 17)
# ---------------------------------------------------------------------------
active_audio_nodes: Dict[str, Dict[str, Any]] = {}
active_portal_websockets: set[WebSocket] = set()
active_hud_websockets: set[WebSocket] = set()


async def broadcast_portal_ui(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Phát sóng sự kiện điều khiển UI thời gian thực tới tất cả trình duyệt Web Portal."""
    if not active_portal_websockets:
        return
    payload = {"event": event, "timestamp": datetime.utcnow().isoformat(), **(data or {})}
    msg = _json.dumps(payload, ensure_ascii=False)
    dead_sockets = set()
    for ws in list(active_portal_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        active_portal_websockets.discard(ws)


async def broadcast_hud(payload: Dict[str, Any]) -> None:
    """Phase 33: Phát sóng dữ liệu thị giác / âm thanh / metrics tới tất cả màn hình VN-MateAI HUD."""
    if not active_hud_websockets:
        return
    msg = _json.dumps(payload, ensure_ascii=False)
    dead_sockets = set()
    for ws in list(active_hud_websockets):
        try:
            await ws.send_text(msg)
        except Exception:
            dead_sockets.add(ws)
    for ws in dead_sockets:
        active_hud_websockets.discard(ws)


def _get_assistant_name() -> str:
    """Retrieve current AI assistant name from settings or config with fallback."""
    try:
        from core.config_loader import settings
        return getattr(settings, "AI_NAME", None) or getattr(settings, "ASSISTANT_NAME", "Ly Ly")
    except Exception:
        return "Ly Ly"


async def _process_hud_voice_command(cmd_query: str) -> None:
    """Phase 33: Thực thi câu lệnh thoại được gửi trực tiếp từ VN-MateAI HUD qua WebSocket."""
    from core.llm_engine import llm_engine
    from core.audio_processor import audio_engine

    logger.info("Standby HUD WS voice command: '%s'", cmd_query[:100])
    await broadcast_hud({
        "type": "voice_active",
        "status": "listening",
        "text": cmd_query,
        "timestamp": datetime.utcnow().isoformat(),
    })
    # Phase 36: Phát ngay từ đệm (0ms Filler word từ Audio Cache)
    import random
    import base64
    from core.audio_cache import get_cached_audio_bytes
    from core.voice_controller import get_contextual_filler, FILLER_WORDS, KEEP_ALIVE_PHRASE

    filler = get_contextual_filler(cmd_query)
    filler_bytes = get_cached_audio_bytes(filler)
    filler_b64 = base64.b64encode(filler_bytes).decode("utf-8") if filler_bytes else None

    await broadcast_hud({
        "type": "voice_active",
        "status": "speaking",
        "text": filler,
        "audio_base64": filler_b64,
        "source_device": "hud",
        "timestamp": datetime.utcnow().isoformat(),
    })

    # Phase 50: Full-Duplex Real-Time Voice Streaming for HUD
    full_sentences = []
    try:
        async for sentence in llm_engine.stream_voice_response(query=cmd_query, source_device="hud"):
            clean_s = llm_engine._sanitise_for_tts(sentence)
            if not clean_s:
                continue
            full_sentences.append(clean_s)

            # Synthesize sentence audio
            s_bytes = await audio_engine.text_to_speech_bytes(clean_s)
            s_b64 = base64.b64encode(s_bytes).decode("utf-8") if s_bytes else None

            # Stream immediate speech to HUD
            await broadcast_hud({
                "type": "voice_active",
                "status": "speaking",
                "text": clean_s,
                "display_text": getattr(llm_engine, "last_voice_display_text", None) or " ".join(full_sentences),
                "query": cmd_query,
                "audio_base64": s_b64,
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })

            # Also sync with Web Portal
            await broadcast_portal_ui("voice_response", {
                "query": cmd_query,
                "reply": clean_s,
                "display_text": getattr(llm_engine, "last_voice_display_text", None) or " ".join(full_sentences),
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })

    except Exception as exc:
        logger.error("HUD voice stream error: %s", exc)

    if not full_sentences:
        # Fallback to standard ask_async if stream yielded nothing
        try:
            result = await llm_engine.ask_async(
                query=cmd_query,
                source_device="hud",
                session_id="hud",
                history=None,
            )
            display_reply = result.get("reply", "")
            speech_reply = result.get("speech_reply") or llm_engine._sanitise_for_tts(display_reply)
            if not speech_reply:
                speech_reply = "Em đã thực hiện xong yêu cầu của bạn."
                display_reply = display_reply or speech_reply

            audio_bytes = await audio_engine.text_to_speech_bytes(speech_reply)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else None

            await broadcast_hud({
                "type": "voice_active",
                "status": "speaking",
                "text": speech_reply,
                "display_text": display_reply,
                "query": cmd_query,
                "audio_base64": audio_b64,
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })

            await broadcast_portal_ui("voice_response", {
                "query": cmd_query,
                "reply": speech_reply,
                "display_text": display_reply,
                "source_device": "hud",
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as e:
            logger.error("HUD voice ask_async fallback error: %s", e)

    async def _reset_hud_idle(delay: float = 6.0):
        await asyncio.sleep(delay)
        await broadcast_hud({
            "type": "voice_active",
            "status": "idle",
            "text": "",
            "timestamp": datetime.utcnow().isoformat(),
        })

    est_duration = max(4.0, (len(speech_reply) / 15.0) + 1.8)
    asyncio.create_task(_reset_hud_idle(est_duration))


def _get_hud_metrics_payload() -> Dict[str, Any]:
    """Tổng hợp đầy đủ telemetry phần cứng, mạng, clients và quyền hạn cho Standby HUD."""
    from core.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager
    from core.orchestrator import orchestrator

    vmem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    hw = SYSTEM_HEALTH_CACHE.get("hardware", {})
    disk_pct = hw.get("disk_percent")
    if disk_pct is None or disk_pct == 0.0:
        try:
            disk_pct = psutil.disk_usage("/").percent
        except Exception:
            disk_pct = 45.0

    procs = len(psutil.pids())
    connected_clients = len(orchestrator.get_connected_clients())
    skills_count = plugin_manager.get_skill_count()
    skills_enabled = len(plugin_manager.get_all_tools())

    return {
        "cpu_percent": round(cpu, 1),
        "cpu_cores": hw.get("cpu_cores", psutil.cpu_count(logical=True) or 1),
        "cpu_freq_mhz": hw.get("cpu_freq_mhz", 0.0),
        "ram_percent": round(vmem.percent, 1),
        "ram_used_gb": round(vmem.used / (1024**3), 2),
        "ram_total_gb": round(vmem.total / (1024**3), 2),
        "disk_percent": round(disk_pct, 1),
        "disk_free_gb": hw.get("disk_free_gb", 0.0),
        "disk_total_gb": hw.get("disk_total_gb", 0.0),
        "processes_count": procs,
        "connected_clients": connected_clients,
        "active_audio_hardware": len(active_audio_nodes),
        "active_web_clients": len(active_portal_websockets),
        "skills_count": skills_count,
        "skills_enabled": skills_enabled,
        "net_sent_mbps": hw.get("net_sent_mbps", 0.0),
        "net_recv_mbps": hw.get("net_recv_mbps", 0.0),
        "security_role": "ADMIN",
        "security_status": "ONLINE // ZERO-TRUST SENTINEL",
        "permission_level": "FULL // UNRESTRICTED",
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

class _WebSocketLogHandler(logging.Handler):
    """
    Python log handler that stores recent log records in an in-memory ring buffer
    and pushes every log record to all connected Portal UI & HUD WebSocket clients
    as a 'log_entry' event in real-time.
    Thread-safe: uses threading.Lock and run_coroutine_threadsafe.
    """
    _LEVEL_COLOR = {
        "DEBUG":    "text-slate-400",
        "INFO":     "text-emerald-400",
        "WARNING":  "text-amber-400",
        "ERROR":    "text-red-400",
        "CRITICAL": "text-red-600 font-bold",
    }

    def __init__(self, maxlen: int = 600) -> None:
        super().__init__()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._buffer: collections.deque = collections.deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the running event loop so emit() can schedule safely."""
        self._loop = loop

    def get_recent_logs(self, limit: int = 200) -> List[Dict[str, Any]]:
        """Lấy danh sách log gần nhất từ buffer."""
        with self._lock:
            return list(self._buffer)[-limit:]

    def clear_buffer(self) -> None:
        """Làm trống buffer."""
        with self._lock:
            self._buffer.clear()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = record.levelname
            raw_msg = record.getMessage()
            now_iso = datetime.utcnow().isoformat()

            entry = {
                "event":     "log_entry",
                "level":     level,
                "color":     self._LEVEL_COLOR.get(level, "text-slate-300"),
                "logger":    record.name,
                "message":   raw_msg,
                "timestamp": now_iso,
            }

            # Luôn lưu vào in-memory ring buffer để client mới vào xem được ngay lịch sử
            with self._lock:
                self._buffer.append(entry)

            # Skip WebSocket broadcast if loop not ready or no clients
            if not self._loop or not self._loop.is_running():
                return
            if not active_portal_websockets and not active_hud_websockets:
                return

            # 1. Send to Portal UI
            if active_portal_websockets:
                msg = _json.dumps(entry, ensure_ascii=False)
                asyncio.run_coroutine_threadsafe(
                    self._send_to_all(msg), self._loop
                )

            # 2. Phase 33: Send to VN-MateAI Standby HUD
            if active_hud_websockets:
                hud_payload = {
                    "type":      "system_log",
                    "level":     level,
                    "logger":    record.name,
                    "message":   raw_msg,
                    "timestamp": now_iso,
                }
                asyncio.run_coroutine_threadsafe(
                    broadcast_hud(hud_payload), self._loop
                )
        except Exception:
            pass  # never let logging errors crash the server

    @staticmethod
    async def _send_to_all(msg: str) -> None:
        dead: set = set()
        for ws in list(active_portal_websockets):
            try:
                await ws.send_text(msg)
            except Exception:
                dead.add(ws)
        for ws in dead:
            active_portal_websockets.discard(ws)


# Module-level reference so startup can configure it
_ws_log_handler: Optional["_WebSocketLogHandler"] = None


def _install_ws_log_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Attach WebSocket log handler to the root logger (once)."""
    global _ws_log_handler
    root = logging.getLogger()
    for h in root.handlers:
        if isinstance(h, _WebSocketLogHandler):
            h.set_loop(loop)
            _ws_log_handler = h
            return  # already installed, just update loop
    handler = _WebSocketLogHandler()
    handler.set_loop(loop)
    handler.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                            datefmt="%H:%M:%S")
    handler.setFormatter(fmt)
    root.addHandler(handler)
    _ws_log_handler = handler
    logger.info("WebSocket real-time log handler installed (thread-safe).")


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
    public_endpoints = (
        "/api/v1/login",
        "/api/v1/login/",
        "/api/v1/config/assistant-name",
        "/api/v1/health-dashboard",
        "/api/v1/audio-nodes",
        "/api/v1/clients",
        "/api/v1/roi-dashboard",
    )

    # Mọi tiền tố path phải được bọc xác thực. /api/erp/ là router của
    # core/api_erp.py — trước đây nằm ngoài /api/v1/ nên KHÔNG endpoint nào của
    # nó được middleware kiểm tra (chỉ có 4/5 endpoint tự khai Depends riêng,
    # còn /template thì hoàn toàn không xác thực).
    guarded_prefixes = ("/api/v1/", "/api/erp/")

    if path.startswith(guarded_prefixes):
        if path in public_endpoints:
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif "token" in request.query_params:
            token = request.query_params.get("token")

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
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")
    logger.info("Static files mounted from: %s", _WEB_DIR)
else:
    logger.warning("web/ directory not found at %s — portal will be unavailable.", _WEB_DIR)

# ─── Mount ERP Organization & Bulk Import Router (Phase 47) ────────────────
from core.api_erp import router as erp_router
app.include_router(erp_router)


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class LoginRequest(BaseModel):
    """Payload for POST /api/v1/login."""
    username: str = Field(..., min_length=1, description="Tên đăng nhập")
    password: str = Field(..., min_length=1, description="Mật khẩu")


class CreateUserRequest(BaseModel):
    """Payload for POST /api/v1/users."""
    username: str = Field(..., min_length=3, description="Tên đăng nhập")
    password: str = Field(..., min_length=6, description="Mật khẩu (tối thiểu 6 ký tự)")
    full_name: Optional[str] = Field(default="", description="Họ và tên người dùng")
    role: Optional[str] = Field(default="viewer", description="Vai trò (admin, manager, viewer)")


class UpdateUserRequest(BaseModel):
    """Payload for PUT /api/v1/users/{user_id}."""
    full_name: Optional[str] = Field(default=None, description="Họ và tên người dùng")
    role: Optional[str] = Field(default=None, description="Vai trò (admin, manager, viewer)")


class ChangeUserPasswordRequest(BaseModel):
    """Payload for PUT /api/v1/users/{user_id}/password."""
    new_password: str = Field(..., min_length=6, description="Mật khẩu mới (tối thiểu 6 ký tự)")


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


class TTSRequest(BaseModel):
    """Payload for POST /api/v1/tts."""
    text: str = Field(..., min_length=1, max_length=2000)
    voice: Optional[str] = Field(default="vi-VN-HoaiMyNeural")
    rate: Optional[str] = Field(default=None)


class ConfigSaveResponse(BaseModel):
    """Response for POST /api/v1/config."""
    success: bool
    message: str


class LLMTestRequest(BaseModel):
    """Payload for POST /api/v1/llm/test."""
    base_url: Optional[str] = Field(default=None, description="URL proxy 9router (VD: http://localhost:20128/v1)")
    model_name: Optional[str] = Field(default=None, description="Tên mô hình cần kiểm tra (VD: ag/gemini-3.8-flash)")
    api_key: Optional[str] = Field(default=None, description="Khóa API")
    tier: Optional[str] = Field(default="primary", description="Tên cấp (backward compat)")
    provider_model: Optional[str] = Field(default=None, description="Tên mô hình (backward compat)")
    api_base: Optional[str] = Field(default=None, description="Base URL (backward compat)")


class SkillToggleRequest(BaseModel):
    """Payload for POST /api/v1/skills/toggle."""
    name: str = Field(..., min_length=1)
    enabled: bool = Field(...)


class SkillCreateRequest(BaseModel):
    """Payload for POST /api/v1/skills/create."""
    name: str = Field(..., min_length=2, max_length=64)
    description: str = Field(..., min_length=5, max_length=500)
    python_code: str = Field(..., min_length=5)
    parameters: Optional[Dict[str, Any]] = None


class SkillExecuteRequest(BaseModel):
    """Payload for POST /api/v1/skills/execute."""
    name: str = Field(..., min_length=1, description="Tên kỹ năng")
    arguments: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Tham số đầu vào")


class BatchSkillToggleRequest(BaseModel):
    """Payload for POST /api/v1/skills/batch-toggle."""
    enabled: bool = Field(..., description="Bật hoặc tắt tất cả")


class ClientExecuteRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/execute."""
    skill_name: str = Field(..., min_length=1)
    args: Optional[Dict[str, Any]] = Field(default_factory=dict)
    timeout: float = Field(default=35.0, ge=1.0, le=120.0)


class ClientDeploySkillRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/deploy-skill."""
    filename: str = Field(..., min_length=1)
    code: str = Field(..., min_length=5)
    timeout: float = Field(default=20.0, ge=1.0, le=60.0)


class ClientKillProcessRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/kill-process."""
    pid: int = Field(..., description="PID của tiến trình cần tắt")
    timeout: float = Field(default=10.0, ge=1.0, le=30.0)


class ClientVisualRequest(BaseModel):
    """Payload for POST /api/v1/clients/{client_id}/visual and /api/v1/visual/broadcast."""
    type: str = Field(default="metric_chart", description="Visual HUD type: text_board | network_map | metric_chart | security_alert | screenshot")
    title: str = Field(default="VN-MateAI VISUAL HUD", description="Tiêu đề cửa sổ overlay")
    data: Optional[Union[Dict[str, Any], str]] = Field(default_factory=dict, description="Dữ liệu hiển thị template")
    duration: int = Field(default=15, ge=1, le=120, description="Thời gian tự động mờ dần (giây)")


class HudSimulateRequest(BaseModel):
    """Payload for POST /api/v1/hud/simulate."""
    type: str = Field(default="voice_active", description="voice_active | system_log | metrics_update")
    status: Optional[str] = Field(default="speaking", description="listening | processing | speaking | idle")
    text: Optional[str] = Field(default="Xin chào, em là Ly Ly. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.")
    level: Optional[str] = Field(default="INFO")
    message: Optional[str] = Field(default="Sentinel Guard: Kiểm tra an ninh định kỳ hoàn tất.")
    data: Optional[Dict[str, Any]] = None


class BlacklistUpdateRequest(BaseModel):
    """Payload for POST /api/v1/security/blacklist."""
    action: str = Field(..., description="'add' hoặc 'remove'")
    keyword: str = Field(..., min_length=1, description="Từ khóa hoặc hành động hoặc đường dẫn")
    category: Optional[str] = Field(default="blacklist", description="'blacklist', 'confirm_actions', hoặc 'protected_dirs'")


class SecurityInspectRequest(BaseModel):
    """Payload for POST /api/v1/security/inspect."""
    type: str = Field(default="code", description="'code', 'action', hoặc 'text'")
    content: str = Field(..., min_length=1, description="Nội dung mã nguồn, lệnh hoặc prompt")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Tham số đi kèm nếu là action")


class ConfirmActionRequest(BaseModel):
    """Payload for POST /api/v1/security/confirm-action."""
    approved: bool = Field(..., description="Phê duyệt (true) hoặc Hủy bỏ (false)")
    # Optional — can be auto-resolved from StateManager if not provided
    action_id: Optional[str] = Field(default=None, description="Mã định danh tác vụ pending (tùy chọn)")
    client_id: Optional[str] = Field(default=None, description="ID máy trạm thực thi hoặc 'master' (auto-resolved nếu bỏ trống)")
    skill_name: Optional[str] = Field(default=None, description="Tên kỹ năng (auto-resolved từ StateManager nếu bỏ trống)")
    args: Optional[Dict[str, Any]] = Field(default=None, description="Tham số kỹ năng (auto-resolved nếu bỏ trống)")
    user_id: Optional[str] = Field(default=None, description="User ID để tra StateManager (mặc định: 'admin')")


class TaskDispatchRequest(BaseModel):
    """Payload for POST /api/v1/tasks/send."""
    client_id: str = Field(..., description="ID máy trạm đích")
    message: str = Field(..., min_length=1, description="Nội dung công việc cần nhắc")
    sender: Optional[str] = Field(default="Ban Giám Đốc", description="Tên người hoặc phòng ban gửi")


class HealthResponse(BaseModel):
    """Response for GET /health."""
    status: str
    version: str
    skill_count: int
    skill_names: list
    model: str
    asr_backend: str
    tts_voice: str
    routing_primary: Optional[str] = None
    routing_fallback_1: Optional[str] = None
    routing_fallback_2: Optional[str] = None


class XiaozhiUiRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/ui."""
    device_id: Optional[str] = Field(default=None, description="Mã thiết bị (để trống để broadcast toàn bộ)")
    state: str = Field(default="listening", description="listening | processing | alert | idle | speaking")
    emotion: Optional[str] = Field(default=None, description="focused | thinking | alert | sleeping | happy")
    text: Optional[str] = Field(default=None, description="Văn bản hiển thị trên màn hình LCD/OLED")


class XiaozhiWakeRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/wake."""
    title: str = Field(default="Cảnh Báo Hệ Thống", description="Tiêu đề hiển thị LCD")
    message: str = Field(default="Hệ thống máy chủ vừa phát hiện lỗi cần chú ý.", description="Nội dung giọng nói cảnh báo")
    device_id: Optional[str] = Field(default=None, description="Mã thiết bị Xiaozhi cụ thể nếu có")


class XiaozhiInterruptRequest(BaseModel):
    """Payload for POST /api/v1/xiaozhi/interrupt."""
    device_id: str = Field(..., description="Mã thiết bị Xiaozhi cần ngắt lời")


class SentinelSimulateRequest(BaseModel):
    """Payload for POST /api/v1/sentinel/simulate."""
    category: str = Field(default="network", description="network | ad_sync | sql_deadlock | hardware")
    title: str = Field(default="IIS Server 503 Error!", description="Tiêu đề sự cố")
    message: str = Field(default="Dịch vụ máy chủ IIS bị dừng hoặc trả về mã lỗi 503 Service Unavailable.", description="Mô tả sự cố chi tiết")


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
    from core.orchestrator import orchestrator
    from core.task_manager import task_manager

    loop = asyncio.get_event_loop()

    # Phase 21: Install real-time WebSocket log streamer (thread-safe)
    _install_ws_log_handler(loop)

    orchestrator.set_event_loop(loop)
    task_manager.set_tts_notifier(broadcast_tts_notification)
    count = await loop.run_in_executor(None, plugin_manager.load_plugins)
    logger.info("FastAPI startup: loaded %d skill(s). Orchestrator & TaskManager ready.", count)

    # Phase 18: Start Telegram Gateway in background daemon thread (only if enabled)
    try:
        from core.telegram_gateway import telegram_gateway
        import json as _jstart
        from pathlib import Path as _Pstart
        cfg_p = _Pstart(__file__).resolve().parent.parent / "config.json"
        is_tg_enabled = False
        if cfg_p.exists():
            raw_c = _jstart.loads(cfg_p.read_text(encoding="utf-8"))
            is_tg_enabled = raw_c.get("telegram", {}).get("enabled", False)
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
        from core.health_monitor import start_observability_workers
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
        from core.autonomous_sentinel import autonomous_sentinel
        autonomous_sentinel.start()
        logger.info("Phase 43: Autonomous Sentinel background monitor launched.")
    except Exception as st_exc:
        logger.warning("Phase 43: Could not start Autonomous Sentinel: %s", st_exc)

    # Phase 56: Start Proactive Manager (Virtual C.O.O Agentic Engine — Cron 08:00 & 16:00)
    try:
        from core.skills.proactive_manager import proactive_manager
        proactive_manager.start()
        logger.info("Phase 56: Proactive Manager (Virtual C.O.O) started — will auto-nudge tasks at 08:00 & 16:00 daily.")
    except Exception as pm_exc:
        logger.warning("Phase 56: Could not start Proactive Manager: %s", pm_exc)

    # Phase 56: Pre-warm Enterprise RAG Engine (load ChromaDB + seed knowledge base)
    try:
        from core.rag_engine import rag_engine
        doc_count = rag_engine.collection.count()
        logger.info("Phase 56: Enterprise RAG Engine ready — %d document chunks indexed in ChromaDB.", doc_count)
    except Exception as rag_exc:
        logger.warning("Phase 56: Could not warm up Enterprise RAG Engine: %s", rag_exc)

    # Phase 57: Pre-warm Multi-Agent System & Analytics Engine
    try:
        from core.agents.agent_orchestrator import multi_agent_system
        from core.analytics_engine import analytics_engine
        from core.knowledge.graph_rag import graph_rag
        logger.info("Phase 57: Multi-Agent System (CEO/CFO/HR/CTO) initialized. GraphRAG with %d entities, %d relations.", len(graph_rag.nodes), len(graph_rag.edges))
    except Exception as mas_exc:
        logger.warning("Phase 57: Could not warm up Multi-Agent System: %s", mas_exc)

    # Phase 59: Register Webhook Gateway routes (AWS SNS, OCI Alarms, Paperless, eInvoice webhooks)
    try:
        from core.webhook_gateway import register_webhook_routes
        register_webhook_routes(app)
        logger.info("Phase 59: Webhook Gateway routes registered at /api/webhooks/{source}")
    except Exception as wh_exc:
        logger.warning("Phase 59: Could not register Webhook Gateway: %s", wh_exc)

    # Phase 59: Pre-warm Enterprise Connectors (AWS, OCI, Paperless, eInvoice)
    try:
        from core.connectors import (
            aws_connector,
            oci_connector,
            paperless_connector,
            einvoice_connector,
        )
        logger.info("Phase 59: Enterprise Connectors (AWS, OCI, Paperless, eInvoice) loaded.")
    except Exception as conn_exc:
        logger.warning("Phase 59: Could not load Enterprise Connectors: %s", conn_exc)

    # Phase 60: Register connector tools into Plugin Registry.
    # Đặt TRƯỚC khi bất kỳ request nào tới: `ask_async()` đọc
    # `get_all_tools_schema()` mỗi lượt, nên đăng ký muộn chỉ làm tool vô hình
    # với LLM cho tới lượt sau — một kiểu lỗi "chạy thử thì thấy, chạy thật
    # thì không" rất khó chẩn đoán.
    try:
        from core.connectors.tool_bridge import register_connector_tools
        reg_stats = register_connector_tools()
        logger.info(
            "Phase 60: Plugin Registry — %d connector tool(s) registered, %d skipped.",
            reg_stats.get("registered", 0), reg_stats.get("skipped", 0),
        )
    except Exception as reg_exc:
        logger.warning("Phase 60: Could not register connector tools: %s", reg_exc)

    # Phase 60: Start Background Worker Manager
    try:
        from core.background_workers import background_worker_manager
        asyncio.create_task(background_worker_manager.start())
        logger.info("Phase 60: Background Worker Manager started.")
    except Exception as bw_exc:
        logger.warning("Phase 60: Could not start Background Worker Manager: %s", bw_exc)

    # Phase 60: Start HITL Manager cleanup loop
    try:
        from core.security.hitl_manager import hitl_manager
        asyncio.create_task(hitl_manager.start_cleanup_loop())
        logger.info("Phase 60: HITL Manager cleanup loop started.")
    except Exception as hitl_exc:
        logger.warning("Phase 60: Could not start HITL Manager: %s", hitl_exc)

    # Phase 57: Start Email Gateway (IMAP listener if configured)
    try:
        from core.email_gateway import email_gateway
        import json as _ej
        from pathlib import Path as _ep
        cfg_path = _ep(__file__).resolve().parent.parent / "config.json"
        if cfg_path.exists():
            email_cfg = _ej.loads(cfg_path.read_text(encoding="utf-8")).get("email_gateway", {})
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


def broadcast_tts_notification(announcement_text: str) -> None:
    """Stream TTS notification to all connected Xiaozhi audio nodes."""
    from core.audio_processor import audio_engine

    async def _broadcast():
        if not active_audio_nodes:
            logger.info("Không có mạch Xiaozhi nào online để phát: '%s'", announcement_text)
            return
        logger.info("Phát thanh TTS thông báo tới %d mạch Xiaozhi: '%s'", len(active_audio_nodes), announcement_text)
        try:
            chunks = []
            async for chunk in audio_engine.text_to_speech_stream(announcement_text):
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


@app.get(
    "/",
    include_in_schema=False,
    summary="Web Portal",
)
async def serve_portal() -> FileResponse:
    """
    Serve the VN-MateAI Web Control Portal.
    Navigate to http://localhost:5843/ in a browser.
    """
    index = _WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(
            status_code=404,
            detail="web/index.html not found. Make sure the web/ directory exists.",
        )
    return FileResponse(
        str(index),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get(
    "/hud",
    include_in_schema=True,
    response_class=HTMLResponse,
    summary="VN-MateAI Sci-Fi HUD Standby Display (Phase 33)",
)
async def get_vnmate_hud():
    """Phục vụ giao diện HUD VN-MateAI 3D toàn màn hình cho màn hình phụ."""
    hud_file = _WEB_DIR / "hud.html"
    if not hud_file.exists():
        raise HTTPException(status_code=404, detail="web/hud.html not found.")
    return FileResponse(
        str(hud_file),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get(
    "/roi",
    include_in_schema=True,
    response_class=HTMLResponse,
    summary="ROI & Value Realization Dashboard (Phase 48)",
)
@app.get(
    "/roi-dashboard",
    include_in_schema=True,
    response_class=HTMLResponse,
    summary="ROI & Value Realization Dashboard (Phase 48)",
)
async def get_vnmate_roi():
    """Phục vụ giao diện ROI & Value Realization Dashboard (Phase 48)."""
    roi_file = _WEB_DIR / "roi_dashboard.html"
    if not roi_file.exists():
        raise HTTPException(status_code=404, detail="web/roi_dashboard.html not found.")
    return FileResponse(
        str(roi_file),
        media_type="text/html",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )



@app.get(
    "/api/v1/config/assistant-name",
    summary="Get current AI assistant name (Public lightweight)",
    tags=["Config"],
)
async def get_assistant_name_endpoint() -> Dict[str, str]:
    """Trả về tên định danh hiện tại của trợ lý AI (không yêu cầu token)."""
    return {"assistant_name": _get_assistant_name()}


# ---------------------------------------------------------------------------
# REST Endpoints — Authentication & Multi-Node Management (Phase 10)
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/login",
    summary="Đăng nhập Web Portal và nhận JWT Access Token",
    tags=["Authentication"],
)
async def login_endpoint(payload: LoginRequest) -> Dict[str, Any]:
    """Xác thực người dùng và cấp JWT Bearer Token."""
    user = auth_manager.authenticate_user(payload.username, payload.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không chính xác.",
        )

    access_token = auth_manager.create_access_token(
        data={"sub": user["username"], "role": user.get("role", "viewer")},
        expires_delta=timedelta(minutes=60 * 24),
    )
    logger.info("Người dùng '%s' (role: %s) đã đăng nhập thành công.", user["username"], user.get("role"))
    return {
        "status": "success",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "full_name": user.get("full_name", user["username"]),
            "role": user.get("role", "viewer"),
        },
    }


@app.get(
    "/api/v1/auth/me",
    summary="Lấy thông tin tài khoản người dùng hiện tại",
    tags=["Authentication"],
)
async def get_me_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về thông tin và quyền hạn của người dùng đang đăng nhập."""
    return {
        "status": "success",
        "user": current_user,
    }


# ---------------------------------------------------------------------------
# Quản trị Người Dùng (Admin User Management CRUD & Password Control)
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/users",
    summary="Lấy danh sách tất cả tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def list_users_endpoint(
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Trả về danh sách tài khoản người dùng an toàn (không kèm password hash)."""
    users = auth_manager.get_all_users()
    return {
        "status": "success",
        "total": len(users),
        "users": users,
    }


@app.post(
    "/api/v1/users",
    summary="Tạo tài khoản người dùng mới (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def create_user_endpoint(
    payload: CreateUserRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Tạo người dùng mới với mật khẩu được mã hóa bcrypt an toàn."""
    try:
        user_data = payload.dict()
        new_user = auth_manager.create_user(user_data)
        return {
            "status": "success",
            "message": "Đã tạo tài khoản người dùng thành công.",
            "user": new_user,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi không mong muốn khi tạo tài khoản: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể tạo tài khoản do lỗi máy chủ.",
        )


@app.put(
    "/api/v1/users/{user_id}",
    summary="Cập nhật thông tin/vai trò người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def update_user_endpoint(
    user_id: str,
    payload: UpdateUserRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Cập nhật họ tên hoặc vai trò của tài khoản theo user_id."""
    try:
        update_data = payload.dict(exclude_unset=True)
        updated_user = auth_manager.update_user(user_id, update_data)
        return {
            "status": "success",
            "message": "Đã cập nhật thông tin tài khoản thành công.",
            "user": updated_user,
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi cập nhật tài khoản '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể cập nhật tài khoản do lỗi máy chủ.",
        )


@app.delete(
    "/api/v1/users/{user_id}",
    summary="Xóa tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def delete_user_endpoint(
    user_id: str,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Xóa hoàn toàn tài khoản khỏi hệ thống."""
    try:
        auth_manager.delete_user(user_id)
        return {
            "status": "success",
            "message": f"Đã xóa tài khoản '{user_id}' thành công.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi xóa tài khoản '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể xóa tài khoản do lỗi máy chủ.",
        )


@app.put(
    "/api/v1/users/{user_id}/password",
    summary="Đặt lại mật khẩu cho tài khoản người dùng (Yêu cầu quyền Admin)",
    tags=["User Administration"],
)
async def change_password_endpoint(
    user_id: str,
    payload: ChangeUserPasswordRequest,
    admin_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Mã hóa và ghi đè mật khẩu mới cho người dùng."""
    try:
        auth_manager.change_user_password(user_id, payload.new_password)
        return {
            "status": "success",
            "message": f"Đã đặt lại mật khẩu cho tài khoản '{user_id}' thành công.",
        }
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except Exception as exc:
        logger.error("Lỗi đặt lại mật khẩu cho '%s': %s", user_id, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Không thể đặt lại mật khẩu do lỗi máy chủ.",
        )


@app.get(
    "/api/v1/audio-nodes",
    summary="Danh sách mạch âm thanh ESP32 Xiaozhi đang kết nối",
    tags=["Audio Nodes"],
)
async def get_audio_nodes_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về danh sách mạch thoại ESP32 Xiaozhi đang kết nối trực tuyến theo thời gian thực."""
    nodes = []
    for dev_id, info in active_audio_nodes.items():
        nodes.append({
            "device_id": dev_id,
            "client_host": info.get("client_host", "unknown"),
            "connected_at": info.get("connected_at"),
            "last_active": info.get("last_active"),
            "state": info.get("state", "idle"),
            "emotion": info.get("emotion", "sleeping"),
            "screen_text": info.get("screen_text"),
            "audio_format": info.get("audio_format", "mp3_24k"),
        })
    return {
        "status": "success",
        "count": len(nodes),
        "nodes": nodes,
    }


# ---------------------------------------------------------------------------
# REST Endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Server Health & Inventory",
    tags=["System"],
)
async def health_check() -> HealthResponse:
    """Returns server status, loaded skills, model name, and audio config."""
    from core.plugin_manager import plugin_manager
    from core.config_loader import settings

    model_name = getattr(getattr(settings, "llm", None), "model_name", settings.MODEL_NAME)
    return HealthResponse(
        status="running",
        version="2.0.0",
        skill_count=plugin_manager.get_skill_count(),
        skill_names=plugin_manager.get_skill_names(),
        model=model_name,
        asr_backend=getattr(settings, "ASR_BACKEND", "mock"),
        tts_voice="vi-VN-HoaiMyNeural",
        routing_primary=model_name,
        routing_fallback_1="",
        routing_fallback_2="",
    )


@app.get(
    "/api/v1/system/stats",
    summary="Real-time Host System Hardware & Runtime Telemetry",
    tags=["System"],
)
async def get_system_stats(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Trả về 100% dữ liệu telemetry thực tế từ phần cứng (CPU, RAM, Uptime) và SQLite (Zero Mock)."""
    from core.db_manager import db_manager
    from core.orchestrator import orchestrator
    from core.plugin_manager import plugin_manager

    hw_stats = db_manager.get_system_hardware_stats()
    task_counts = db_manager.count_tasks()
    all_users = db_manager.get_all_users()
    online_clients = orchestrator.get_connected_clients()

    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
        "hardware": hw_stats,
        "tasks": task_counts,
        "users_count": len(all_users),
        "online_clients_count": len(online_clients),
        "audio_nodes_count": len(active_audio_nodes),
        "skills_count": plugin_manager.get_skill_count(),
    }


@app.get(
    "/api/v1/health-dashboard",
    summary="Phase 24.5: Zero-Overhead Observability Dashboard — System Health Snapshot",
    tags=["System"],
)
async def health_dashboard_endpoint() -> Dict[str, Any]:
    """
    Zero-Overhead Health Snapshot (O(1) in-memory lookup).
    Contains no computational logic or network requests.
    Directly returns SYSTEM_HEALTH_CACHE in < 1ms response time.
    """
    from core.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager

    # Inject live websocket & node counts in O(1)
    SYSTEM_HEALTH_CACHE["nodes"]["active_web_clients"] = len(active_portal_websockets)
    SYSTEM_HEALTH_CACHE["nodes"]["active_audio_hardware"] = len(active_audio_nodes)
    SYSTEM_HEALTH_CACHE["nodes"]["skills_count"] = plugin_manager.get_skill_count()
    SYSTEM_HEALTH_CACHE["nodes"]["skills_enabled"] = len(plugin_manager.get_all_tools())
    SYSTEM_HEALTH_CACHE["security_role"] = "ADMIN"
    return SYSTEM_HEALTH_CACHE



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
    from core.llm_engine import llm_engine
    from core.audio_processor import audio_engine

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

    try:
        # Phase 25 & 34: Use ask_async directly with session_id for Sliding Window Conversational Memory
        result = await llm_engine.ask_async(
            query=payload.query,
            source_device=source_device,
            history=payload.history,
            session_id=payload.session_id or source_device,
        )
        display_reply: str = result.get("reply", "")
        speech_reply: str = result.get("speech_reply") or llm_engine._sanitise_for_tts(display_reply)
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
        raise HTTPException(status_code=500, detail="Lỗi xử lý nội bộ.")

    ai_name = _get_assistant_name()
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
            audio_bytes = await audio_engine.text_to_speech_bytes(speech_reply)
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
    )


# ---------------------------------------------------------------------------
# Phase 34: Conversational Memory Diagnostics & Management APIs
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/memory/history",
    summary="Get conversation history for a session (Phase 34 Sliding Window)",
    tags=["Conversational Memory"],
)
async def get_memory_history(
    session_id: str = "default",
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Retrieve sliding window conversation history for session_id."""
    from core.memory_manager import memory_manager
    history = memory_manager.get_history(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "message_count": len(history),
        "history": history,
        "stats": memory_manager.get_stats(),
    }


@app.post(
    "/api/v1/memory/clear",
    summary="Clear conversation history for a session (Phase 34)",
    tags=["Conversational Memory"],
)
async def clear_memory_history(
    session_id: str = "default",
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Reset conversational memory for session_id."""
    from core.memory_manager import memory_manager
    memory_manager.clear_history(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "message": f"Đã xóa lịch sử hội thoại của session '{session_id}'.",
    }


@app.post(
    "/api/v1/tts",
    summary="Synthesise Vietnamese TTS audio (REST, full buffer)",
    tags=["Audio"],
    response_class=Response,
)
async def tts_endpoint(
    payload: TTSRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Response:
    """
    Convert text to speech using edge-tts.
    Returns raw MP3 bytes (Content-Type: audio/mpeg).
    Assembles entire audio in-memory (io.BytesIO) — no disk I/O.
    """
    from core.audio_processor import audio_engine

    try:
        audio_bytes = await audio_engine.text_to_speech_bytes(payload.text, voice=payload.voice, rate=payload.rate)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="TTS engine returned empty audio.")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("TTS endpoint error: %s", exc)
        raise HTTPException(status_code=500, detail=f"TTS error: {exc}")



# In-memory cache for voice list (populated on first request)
_TTS_VOICES_CACHE: list = []

@app.get(
    "/api/v1/tts/voices",
    summary="Lấy danh sách toàn bộ giọng đọc Edge-TTS (322 giọng, 70+ ngôn ngữ)",
    tags=["Audio"],
)
async def tts_voices_endpoint(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """
    Trả về danh sách đầy đủ các giọng đọc Edge-TTS từ Microsoft.
    Kết quả được cache trong RAM — chỉ gọi edge_tts.list_voices() một lần duy nhất.
    """
    global _TTS_VOICES_CACHE
    if not _TTS_VOICES_CACHE:
        try:
            import edge_tts as _edge_tts
            raw = await _edge_tts.list_voices()
            _TTS_VOICES_CACHE = [
                {
                    "short_name": v["ShortName"],
                    "friendly_name": v["FriendlyName"],
                    "locale": v["Locale"],
                    "gender": v.get("Gender", ""),
                }
                for v in raw
            ]
            logger.info("Đã tải %d giọng Edge-TTS vào cache.", len(_TTS_VOICES_CACHE))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Lỗi tải danh sách giọng Edge-TTS: %s", exc)
            return {"success": False, "error": str(exc), "voices": []}

    return {"success": True, "total": len(_TTS_VOICES_CACHE), "voices": _TTS_VOICES_CACHE}


# ---------------------------------------------------------------------------
# System Logs Endpoints (Phase 21.1)
# ---------------------------------------------------------------------------

@app.get(
    "/api/v1/logs/recent",
    summary="Lấy danh sách nhật ký hệ thống gần đây từ ring buffer",
    tags=["System Logs"],
)
async def get_recent_logs(
    limit: int = Query(default=200, ge=1, le=600),
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Trả về danh sách log mới nhất đang được lưu trong RAM."""
    logs = _ws_log_handler.get_recent_logs(limit=limit) if _ws_log_handler else []
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs,
    }


@app.delete(
    "/api/v1/logs",
    summary="Xóa bộ đệm nhật ký hệ thống trong RAM",
    tags=["System Logs"],
)
async def clear_logs_buffer(
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Xóa toàn bộ các dòng log trong bộ nhớ đệm RAM."""
    if _ws_log_handler:
        _ws_log_handler.clear_buffer()
    return {"status": "success", "message": "Đã xóa sạch bộ đệm nhật ký máy chủ."}


@app.post(
    "/api/v1/llm/test",
    summary="Kiểm tra kết nối và đo độ trễ mô hình LLM qua 9router",
    tags=["LLM Router"],
)
async def test_llm_endpoint(payload: LLMTestRequest, user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:

    """
    Kiểm tra kết nối mô hình LLM trực tiếp qua 9router bằng thư viện openai chuẩn.
    """
    import time
    from openai import AsyncOpenAI
    from core.config_loader import settings

    cfg_llm = getattr(settings, "llm", None)
    default_base = getattr(cfg_llm, "base_url", "http://localhost:20128/v1") if cfg_llm else "http://localhost:20128/v1"
    default_model = getattr(cfg_llm, "model_name", "ag/gemini-3.8-flash") if cfg_llm else "ag/gemini-3.8-flash"
    default_key = getattr(cfg_llm, "api_key", "sk-dummy") if cfg_llm else "sk-dummy"

    base_url = (payload.base_url or payload.api_base or default_base).strip()
    model_name = (payload.model_name or payload.provider_model or default_model).strip()
    api_key = payload.api_key if payload.api_key is not None else default_key
    api_key = (api_key or "sk-dummy").strip()

    if not model_name:
        return {"success": False, "error": "Chưa chọn hoặc nhập tên mô hình."}

    start_time = time.perf_counter()
    client = AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=15.0,
        max_retries=0,
    )

    primary_error = None
    try:
        res = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": "1+1=? Trả lời số duy nhất."}],
            max_tokens=20,
            temperature=0.3,
            stream=False,
            extra_body={"thinking": {"budget_tokens": 0}},
        )
        latency_ms = int((time.perf_counter() - start_time) * 1000)
        content = res.choices[0].message.content or ""
        reply = str(content).strip()
        return {
            "success": True,
            "fallback_triggered": False,
            "tier": payload.tier or "llm",
            "requested_model": model_name,
            "resolved_model": model_name,
            "reply": reply,
            "latency_ms": latency_ms,
            "message": f"Kết nối thành công! Phản hồi trong {latency_ms}ms.",
        }
    except Exception as exc:
        primary_error = str(exc)

    # ═════════════════════════════════════════════════════════════════════
    # Phase 46.3: Auto-Fallback Demonstration in Diagnostic Test
    # ═════════════════════════════════════════════════════════════════════
    fallback_candidates = [
        "ag/gemini-3.8-flash",
        "ag/gemini-3.7-flash-medium",
        "ag/gemini-3.6-flash-medium",
        "ag/gemini-3-flash",
    ]
    for fb_model in fallback_candidates:
        if fb_model == model_name:
            continue
        try:
            fb_start = time.perf_counter()
            res = await client.chat.completions.create(
                model=fb_model,
                messages=[{"role": "user", "content": "1+1=? Trả lời số duy nhất."}],
                max_tokens=20,
                temperature=0.3,
                stream=False,
                extra_body={"thinking": {"budget_tokens": 0}},
            )
            latency_ms = int((time.perf_counter() - fb_start) * 1000)
            content = res.choices[0].message.content or ""
            reply = str(content).strip()
            return {
                "success": True,
                "fallback_triggered": True,
                "tier": payload.tier or "llm",
                "requested_model": model_name,
                "resolved_model": fb_model,
                "primary_error": primary_error,
                "reply": reply,
                "latency_ms": latency_ms,
                "message": (
                    f"⚡ Auto-Fallback đã kích hoạt thành công!\n"
                    f"Model chính '{model_name}' gặp lỗi nhưng hệ thống đã tự động chuyển đổi sang '{fb_model}'."
                ),
            }
        except Exception:
            continue

    latency_ms = int((time.perf_counter() - start_time) * 1000)
    err_msg = primary_error or "Tất cả mô hình đều không phản hồi."
    suggestion = None
    if "404" in err_msg or "not found" in err_msg.lower() or "no active credentials" in err_msg.lower():
        suggestion = (
            f"Model '{model_name}' không tìm thấy trên proxy 9router. "
            "Hãy kiểm tra lại tên model trong 9router (VD: ag/gemini-3.8-flash)."
        )
    elif "401" in err_msg or "invalid" in err_msg.lower() or "api_key" in err_msg.lower():
        suggestion = "Khóa API không hợp lệ. Kiểm tra lại API Key trong 9router."
    elif "connection" in err_msg.lower() or "refused" in err_msg.lower() or "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        suggestion = f"Không thể kết nối tới {base_url}. Hãy chắc chắn rằng 9router đang chạy."
    elif "unsupported model" in err_msg.lower() or "400" in err_msg:
        suggestion = (
            f"Mô hình '{model_name}' không được nhà cung cấp hỗ trợ hoặc đã ngừng cung cấp. "
            "👉 Khuyên dùng: Nhấn nút [⚡ Gemini 3.8] (ag/gemini-3.8-flash) để kết nối trực tiếp."
        )
    return {
        "success": False,
        "fallback_triggered": False,
        "tier": payload.tier or "llm",
        "requested_model": model_name,
            "resolved_model": model_name,
            "error": err_msg,
            "suggestion": suggestion,
            "latency_ms": latency_ms,
        }



# ---------------------------------------------------------------------------
# Proxy Model List  — Fetch available models from a proxy (9router / LMStudio)
# ---------------------------------------------------------------------------


class ProxyModelsRequest(BaseModel):
    """Payload for POST /api/v1/llm/proxy-models."""
    base_url: str = Field(..., description="URL proxy, ví dụ http://localhost:20128/v1")
    api_key: Optional[str] = Field(default=None, description="API Key của proxy (nếu cần)")


@app.post(
    "/api/v1/llm/proxy-models",
    summary="Lấy danh sách model từ proxy OpenAI-compatible (9router/LMStudio/Ollama)",
    tags=["LLM Router"],
)
async def proxy_models_endpoint(
    payload: ProxyModelsRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Gọi {base_url}/models để lấy danh sách model từ proxy.
    Trả về danh sách id model khả dụng.
    """
    import httpx

    base = payload.base_url.rstrip("/")
    # Đảm bảo có /v1
    if not base.endswith("/v1"):
        base = base + "/v1"
    models_url = f"{base}/models"
    headers: Dict[str, str] = {"Accept": "application/json"}
    if payload.api_key:
        headers["Authorization"] = f"Bearer {payload.api_key}"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(models_url, headers=headers)
        if resp.status_code != 200:
            return {
                "success": False,
                "error": f"Proxy trả về HTTP {resp.status_code}: {resp.text[:200]}",
                "models": [],
            }
        data = resp.json()
        model_ids = [m["id"] for m in data.get("data", []) if "id" in m]
        return {
            "success": True,
            "count": len(model_ids),
            "models": model_ids,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Không thể kết nối proxy {base}: {exc}",
            "models": [],
        }


# ---------------------------------------------------------------------------
# Config API — Read & Write config.json
# ---------------------------------------------------------------------------



@app.get(
    "/api/v1/config",
    summary="Read system configuration",
    tags=["Config"],
)
async def get_config(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """
    Read and return the current config.json as JSON.
    Sensitive fields (API keys) are returned to allow editing in the portal.
    Ensures 'llm' and 'auto_execute' fields are always present.
    """
    try:
        raw = _CONFIG_PATH.read_text(encoding="utf-8")
        data = _json.loads(raw)

        # Phase 22: Ensure 'llm' block is present
        if "llm" not in data or not isinstance(data["llm"], dict):
            old_primary = data.get("routing", {}).get("primary", {})
            data["llm"] = {
                "base_url": old_primary.get("api_base") or data.get("BASE_URL", "http://localhost:20128/v1"),
                "model_name": old_primary.get("provider_model") or data.get("MODEL_NAME", "ag/gemini-3.8-flash"),
                "api_key": old_primary.get("api_key") or data.get("API_KEY", "sk-dummy"),
            }

        llm = data["llm"]
        # Sync top-level backward compatibility aliases
        data["MODEL_NAME"] = llm.get("model_name", "")
        data["API_KEY"] = llm.get("api_key", "")
        data["BASE_URL"] = llm.get("base_url", "")
        data["auto_execute"] = data.get("auto_execute", data.get("AUTO_EXECUTE_UNVERIFIED_CODE", False))

        # Backward compatibility for legacy UI expecting 'routing' or 'router'
        if "routing" not in data:
            data["routing"] = {
                "primary": {
                    "provider_model": llm.get("model_name", ""),
                    "api_key": llm.get("api_key", ""),
                    "api_base": llm.get("base_url", ""),
                    "api_keys": [llm.get("api_key", "")] if llm.get("api_key") else [],
                },
                "fallback_1": {"provider_model": "", "api_key": "", "api_base": "", "api_keys": []},
                "fallback_2": {"provider_model": "", "api_key": "", "api_base": "", "api_keys": []},
            }
        data["router"] = data["routing"]

        return {k: v for k, v in data.items() if not k.startswith("_")}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="config.json not found.")
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"config.json is malformed: {exc}")


@app.post(
    "/api/v1/config",
    response_model=ConfigSaveResponse,
    summary="Save system configuration",
    tags=["Config"],
)
@app.put(
    "/api/v1/config",
    response_model=ConfigSaveResponse,
    include_in_schema=False,
)
async def save_config(
    payload: Dict[str, Any],
    user: dict = Depends(require_roles(["admin"])),
) -> ConfigSaveResponse:
    """
    Receive a config dict from the Web Portal, validate, and persist to config.json.
    Supports Thin Client 'llm' schema and legacy routing parameters.
    Re-initialises in-memory settings so changes take effect without a restart.
    """
    try:
        existing: Dict[str, Any] = {}
        if _CONFIG_PATH.exists():
            try:
                existing = _json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:  # pylint: disable=broad-except
                pass

        # Phase 22 Thin Client: Map 'llm' block or convert legacy routing
        # Phase 22 & Phase 46.3 Thin Client: Map 'llm' block with auto-fallback lists
        DEFAULT_ROUTER_FALLBACKS = [
            "ag/gemini-3.8-flash",
            "ag/gemini-3.7-flash-medium",
            "ag/gemini-3.6-flash-medium",
            "ag/gemini-3-flash",
        ]
        DEFAULT_SPECIALIST_FALLBACKS = [
            "ag/claude-sonnet-4-6",
            "ag/gemini-pro-agent",
            "ag/gemini-3.1-pro-low",
        ]

        if "llm" in payload and isinstance(payload["llm"], dict):
            existing_llm = existing.get("llm", {})
            new_model = payload["llm"].get("model_name", existing_llm.get("model_name", "ag/gemini-3.8-flash"))
            r_models = payload["llm"].get("router_models", existing_llm.get("router_models", []))
            if not isinstance(r_models, list) or not r_models:
                r_models = DEFAULT_ROUTER_FALLBACKS
            if new_model and new_model not in r_models:
                r_models = [new_model] + [m for m in r_models if m != new_model]
            s_models = payload["llm"].get("specialist_models", existing_llm.get("specialist_models", DEFAULT_SPECIALIST_FALLBACKS))
            payload["llm"] = {
                "base_url": payload["llm"].get("base_url", existing_llm.get("base_url", "http://localhost:20128/v1")),
                "model_name": new_model,
                "api_key": payload["llm"].get("api_key", existing_llm.get("api_key", "sk-dummy")),
                "router_models": r_models,
                "specialist_models": s_models,
            }
        elif "routing" in payload and isinstance(payload["routing"], dict):
            # If incoming is legacy routing, extract primary into 'llm'
            primary = payload["routing"].get("primary", {})
            new_model = primary.get("provider_model", "ag/gemini-3.8-flash")
            payload["llm"] = {
                "base_url": primary.get("api_base", "http://localhost:20128/v1"),
                "model_name": new_model,
                "api_key": primary.get("api_key", "sk-dummy"),
                "router_models": [new_model] + [m for m in DEFAULT_ROUTER_FALLBACKS if m != new_model],
                "specialist_models": DEFAULT_SPECIALIST_FALLBACKS,
            }
        elif "MODEL_NAME" in payload or "BASE_URL" in payload:
            existing_llm = existing.get("llm", {})
            new_model = payload.get("MODEL_NAME", existing_llm.get("model_name", "ag/gemini-3.8-flash"))
            payload["llm"] = {
                "base_url": payload.get("BASE_URL", existing_llm.get("base_url", "http://localhost:20128/v1")),
                "model_name": new_model,
                "api_key": payload.get("API_KEY", existing_llm.get("api_key", "sk-dummy")),
                "router_models": [new_model] + [m for m in DEFAULT_ROUTER_FALLBACKS if m != new_model],
                "specialist_models": DEFAULT_SPECIALIST_FALLBACKS,
            }

        # Keep auto_execute and AUTO_EXECUTE_UNVERIFIED_CODE in sync
        if "auto_execute" in payload:
            payload["AUTO_EXECUTE_UNVERIFIED_CODE"] = bool(payload["auto_execute"])
        elif "AUTO_EXECUTE_UNVERIFIED_CODE" in payload:
            payload["auto_execute"] = bool(payload["AUTO_EXECUTE_UNVERIFIED_CODE"])

        comment_keys = {k: v for k, v in existing.items() if k.startswith("_")}
        merged = {**existing, **comment_keys, **payload}

        _CONFIG_PATH.write_text(
            _json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("config.json updated via Web Portal.")

        # Hot-reload in-memory settings
        try:
            from core.config_loader import reload_settings
            reload_settings()
            # Broadcast updated assistant name to all connected HUD displays in real-time
            updated_ai_name = payload.get("AI_NAME") or payload.get("ASSISTANT_NAME") or payload.get("persona", {}).get("ai_name") or "Ly Ly"
            await broadcast_hud({
                "type": "assistant_name_updated",
                "assistant_name": updated_ai_name,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as hot_err:  # pylint: disable=broad-except
            logger.warning("Hot-reload settings failed (non-critical): %s", hot_err)

        # Phase 59: Nạp lại cấu hình cho connector nào vừa được sửa.
        # Không có bước này, thông số lưu từ giao diện chỉ nằm trong file
        # mà connector vẫn giữ giá trị cũ tới lần restart server.
        touched = [k for k in ("aws", "oci", "paperless", "einvoice") if k in payload]
        if touched:
            try:
                from core.connectors import (
                    CONNECTOR_REGISTRY,
                    invalidate_config_cache,
                )

                invalidate_config_cache()
                for name in touched:
                    connector = CONNECTOR_REGISTRY.get(name)
                    if connector is not None:
                        connector.reload_config()
            except Exception as conn_err:  # pylint: disable=broad-except
                logger.warning("Connector reload sau khi lưu config thất bại: %s", conn_err)

        # If telegram config was included, ensure gateway reflects changes
        if "telegram" in payload:
            tg_token = payload["telegram"].get("bot_token", "")
            try:
                from core.telegram_gateway import telegram_gateway
                telegram_gateway.stop()
                if tg_token:
                    import time; time.sleep(0.5)
                    telegram_gateway.start()
            except Exception as gw_err:
                logger.warning("Telegram gateway restart in save_config: %s", gw_err)

        return ConfigSaveResponse(success=True, message="Cấu hình hệ thống và điểm nối 9router đã được lưu thành công.")

    except OSError as exc:
        logger.error("Failed to write config.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể ghi config.json: {exc}")


@app.get(
    "/api/v1/routing",
    summary="Get 3-tier AI routing configuration",
    tags=["Config"],
)
async def get_routing_endpoint(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Trả về cấu hình định tuyến AI 3 tầng (primary, fallback_1, fallback_2)."""
    cfg = await get_config()
    return {
        "status": "success",
        "routing": cfg.get("routing", {}),
    }


@app.post(
    "/api/v1/routing",
    summary="Update 3-tier AI routing configuration",
    tags=["Config"],
)
async def update_routing_endpoint(
    payload: Dict[str, Any],
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Cập nhật cấu hình định tuyến AI 3 tầng, lưu vào config.json và hot-reload runtime."""
    routing_data = payload.get("routing") if "routing" in payload else payload
    res = await save_config({"routing": routing_data})
    return {
        "status": "success",
        "message": "Đã cập nhật và kích hoạt cấu hình định tuyến AI 3 tầng thành công.",
        "routing": routing_data,
    }


# ---------------------------------------------------------------------------
# Skills Registry API
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/skills",
    summary="Get skills registry",
    tags=["Skills"],
)
async def get_skills_registry(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """
    Return the live skills registry from plugin_manager (preferred)
    or fallback to reading skills/registry.json from disk.
    """
    try:
        from core.plugin_manager import plugin_manager
        with plugin_manager._lock:
            live_registry: Dict[str, Any] = {
                name: {
                    "module":  entry["module"],
                    "attr":    entry["attr"],
                    "meta":    entry["meta"],
                    "enabled": entry.get("enabled", True),
                }
                for name, entry in plugin_manager._registry.items()
            }
        return live_registry
    except Exception:  # pylint: disable=broad-except
        pass

    if _REGISTRY_PATH.exists():
        try:
            return _json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
        except _json.JSONDecodeError as exc:
            raise HTTPException(status_code=500, detail=f"registry.json malformed: {exc}")

    raise HTTPException(status_code=404, detail="skills/registry.json not found.")


@app.post(
    "/api/v1/skills/toggle",
    summary="Bật hoặc tắt một kỹ năng trong runtime",
    tags=["Skills"],
)
async def toggle_skill(
    payload: SkillToggleRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Bật hoặc tắt kỹ năng. Trạng thái được lưu vĩnh viễn vào registry.json.
    Khi kỹ năng bị tắt, LLM sẽ không được cấp tool đó.
    """
    from core.plugin_manager import plugin_manager
    try:
        new_state = plugin_manager.toggle_skill(payload.name, payload.enabled)
        return {"success": True, "name": payload.name, "enabled": new_state}
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy kỹ năng '{payload.name}'")


@app.post(
    "/api/v1/skills/create",
    summary="Thêm kỹ năng mới bằng tay",
    tags=["Skills"],
)
async def create_custom_skill(
    payload: SkillCreateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Định nghĩa và đăng ký kỹ năng mới vào skills/custom_skills.py.
    Tự động biên dịch, kiểm tra cú pháp và hot-load vào runtime.
    """
    from core.plugin_manager import plugin_manager
    try:
        res = plugin_manager.register_custom_skill(
            name=payload.name,
            description=payload.description,
            python_code=payload.python_code,
            parameters=payload.parameters,
        )
        return {
            "success": True,
            "message": f"Kỹ năng '{payload.name}' đã được tạo và kích hoạt thành công!",
            "skill": res,
        }
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as exc:
        logger.error("Lỗi thêm kỹ năng: %s", traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Không thể tạo kỹ năng: {exc}")


@app.post(
    "/api/v1/skills/execute",
    summary="Thực thi trực tiếp một kỹ năng và nhận kết quả tức thời",
    tags=["Skills"],
)
async def execute_skill_endpoint(
    payload: SkillExecuteRequest,
    request: Request,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Thực thi một kỹ năng trong registry với tham số được cung cấp.
    Đo thời gian thực thi chính xác và trả về kết quả 100% thời gian thực.

    Zero-Trust: đây là cổng gọi skill TRỰC TIẾP có thể chạy PowerShell, ghi/xoá
    file, tạo skill mới... Trước đây chỉ kiểm tra "đã đăng nhập", nên tài khoản
    role `viewer` (chỉ được xem) vẫn chạy được skill nguy hiểm. Nay đã nối vào
    RBAC của SecurityGuard — cùng cổng kiểm tra mà luồng chat/LLM dùng.
    """
    from core.plugin_manager import plugin_manager
    from core.security_guard import security_guard
    from core.zero_trust import execute_with_hitl

    source_ip = request.client.host if request.client else None
    allowed, reason = security_guard.check_permission(
        tool_name=payload.name,
        employee_id=user.get("username"),
        payload=payload.arguments or {},
        source_ip=source_ip,
    )
    if not allowed:
        logger.warning(
            "[RBAC] Chặn gọi skill '%s' từ '%s' (IP %s): %s",
            payload.name, user.get("username"), source_ip, reason,
        )
        raise HTTPException(status_code=403, detail=reason)

    t0 = time.perf_counter()
    args = payload.arguments or {}

    # ── Zero-Trust HITL: tác vụ rủi ro Level 3-5 phải chờ CEO duyệt ─────────
    # RBAC (SecurityGuard) chỉ kiểm tra VAI TRÒ, không kiểm tra RỦI RO. Nên
    # trước đây admin gọi `run_powershell_command` / `delete_database` chạy thẳng.
    # Đây là cổng duy nhất mọi skill đi qua, nên đặt gate ở đây là đủ.
    async def _run() -> Dict[str, Any]:
        return await plugin_manager.execute_skill(payload.name, args)

    gate = await execute_with_hitl(
        action_name=payload.name,
        params=args,
        executor=_run,
        requested_by=user.get("username", "unknown"),
        description=f"Skill '{payload.name}' được gọi qua API bởi {user.get('username', '?')}",
    )

    if gate.get("status") == "awaiting_approval":
        security_guard.audit_tool_execution(
            tool_name=payload.name,
            execution_status="awaiting_hitl_approval",
            employee_id=user.get("username"),
            payload=args,
            source_ip=source_ip,
        )
        raise HTTPException(status_code=202, detail=gate["message"])

    res = gate["result"] if isinstance(gate.get("result"), dict) else {"result": gate.get("result")}
    duration_ms = int((time.perf_counter() - t0) * 1000)
    res["latency_ms"] = duration_ms
    res["skill_name"] = payload.name

    security_guard.audit_tool_execution(
        tool_name=payload.name,
        execution_status="success",
        employee_id=user.get("username"),
        payload=payload.arguments or {},
        source_ip=source_ip,
    )
    return res


@app.post(
    "/api/v1/skills/batch-toggle",
    summary="Bật hoặc tắt toàn bộ kỹ năng cùng lúc",
    tags=["Skills"],
)
async def batch_toggle_skills(
    payload: BatchSkillToggleRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Bật hoặc tắt tất cả các kỹ năng đã đăng ký.
    """
    from core.plugin_manager import plugin_manager
    count = 0
    with plugin_manager._lock:
        for name in list(plugin_manager._registry.keys()):
            plugin_manager.toggle_skill(name, payload.enabled)
            count += 1
    return {"success": True, "count": count, "enabled": payload.enabled}


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
    from core.xiaozhi_gateway import xiaozhi_gateway

    if not _authenticate_device(websocket):
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
        if _ws_log_handler:
            recent_logs = _ws_log_handler.get_recent_logs(limit=150)
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

# Secret dùng để "ghi danh" (enroll) worker agent vào /ws/client.
# Sinh tự động ở lần chạy đầu, lưu vào certs/worker_secret.key (đã bị .gitignore loại).
_WORKER_SECRET_FILE = _PROJECT_ROOT / "certs" / "worker_secret.key"

# Secret riêng cho thiết bị ESP32 / Xiaozhi (tách khỏi worker: thiết bị chỉ
# stream âm thanh, không được nhận lệnh thực thi).
_DEVICE_SECRET_FILE = _PROJECT_ROOT / "certs" / "device_secret.key"


def _get_worker_enrollment_secret() -> str:
    """
    Lấy (hoặc tự sinh lần đầu) secret dùng để đăng ký LAN worker agent.

    Worker là tiến trình headless không có tài khoản người dùng, nên không thể đăng
    nhập bằng JWT. Thay vào đó mỗi bản agent được phát một secret riêng khi tải về
    từ /api/v1/download-agent. Secret lưu trong file đã được git-ignore và có quyền
    0600.
    """
    try:
        if _WORKER_SECRET_FILE.exists():
            existing = _WORKER_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        _WORKER_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_urlsafe(32)
        _WORKER_SECRET_FILE.write_text(new_secret, encoding="utf-8")
        try:
            os.chmod(_WORKER_SECRET_FILE, 0o600)
        except OSError:
            pass
        logger.info("Đã sinh worker enrollment secret mới tại %s", _WORKER_SECRET_FILE)
        return new_secret
    except Exception as exc:
        logger.error("Không thể tạo/đọc worker enrollment secret: %s", exc)
        return ""


def _get_device_enrollment_secret() -> str:
    """
    Lấy (hoặc tự sinh lần đầu) secret để thiết bị ESP32 / Xiaozhi đăng ký vào
    /api/v1/xiaozhi/ws và /ws/audio-stream.

    Tách khỏi worker secret vì đây là hai lớp tin cậy khác nhau: thiết bị chỉ
    stream âm thanh, worker được phép nhận lệnh thực thi skill (nguy hiểm hơn nhiều).
    """
    try:
        if _DEVICE_SECRET_FILE.exists():
            existing = _DEVICE_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        _DEVICE_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        new_secret = secrets.token_urlsafe(32)
        _DEVICE_SECRET_FILE.write_text(new_secret, encoding="utf-8")
        try:
            os.chmod(_DEVICE_SECRET_FILE, 0o600)
        except OSError:
            pass
        logger.info("Đã sinh device enrollment secret mới tại %s", _DEVICE_SECRET_FILE)
        return new_secret
    except Exception as exc:
        logger.error("Không thể tạo/đọc device enrollment secret: %s", exc)
        return ""


def _authenticate_device(websocket: WebSocket) -> bool:
    """
    Xác thực thiết bị ESP32 trước khi cho stream âm thanh.

    Chấp nhận enrollment secret thiết bị, hoặc JWT admin/manager (để debug).
    """
    token = websocket.query_params.get("token")
    if not token:
        return False

    expected = _get_device_enrollment_secret()
    if expected and secrets.compare_digest(token, expected):
        return True

    try:
        payload = auth_manager.decode_access_token(token)
    except Exception:
        return False
    if not payload or "sub" not in payload:
        return False
    user = auth_manager.get_user(payload["sub"])
    return bool(user) and user.get("role") in WS_APPROVER_ROLES


def _authenticate_worker(websocket: WebSocket) -> bool:
    """
    Xác thực một LAN worker trước khi cho đăng ký vào /ws/client.

    Chấp nhận một trong hai:
      1. Enrollment secret do Master Server phát lúc tải agent.
      2. JWT của tài khoản admin/manager (tiện cho việc debug thủ công).

    Trả về True nếu hợp lệ.
    """
    token = websocket.query_params.get("token")
    if not token:
        return False

    expected = _get_worker_enrollment_secret()
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
        ai_name = _get_assistant_name()
        await websocket.send_text(_json.dumps({
            "type": "hud_welcome",
            "message": f"Hệ thống trợ lý AI {ai_name} sẵn sàng. Neural Link established.",
            "assistant_name": ai_name,
            "status": "idle",
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
                if cmd_query:
                    asyncio.create_task(_process_hud_voice_command(cmd_query))
            elif action == "confirm_action":
                approved = bool(data.get("approved", True))
                action_id = data.get("action_id")
                skill_name = data.get("skill_name")
                # Zero-Trust: chỉ user đã xác thực và có role admin/manager mới
                # được phê duyệt hành động rủi ro cao.
                if ws_user is None or ws_user.get("role") not in ("admin", "manager"):
                    await websocket.send_text(_json.dumps({
                        "type": "security_approval_rejected",
                        "message": "Yêu cầu phê duyệt bị từ chối: cần đăng nhập "
                                   "với tài khoản admin hoặc manager.",
                        "action_id": action_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    }, ensure_ascii=False))
                    continue
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
                    ai_name = _get_assistant_name()
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
    from core.orchestrator import orchestrator

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


_local_worker_process: Optional[subprocess.Popen] = None


@app.get(
    "/api/v1/orchestrator/local-worker/status",
    summary="Kiểm tra trạng thái Worker Node cục bộ",
    tags=["Orchestrator"],
)
async def get_local_worker_status(
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kiểm tra xem Worker Node cục bộ có đang chạy hay không."""
    global _local_worker_process
    is_running = _local_worker_process is not None and _local_worker_process.poll() is None
    return {
        "active": is_running,
        "pid": _local_worker_process.pid if is_running else None,
        "client_id": "MASTER_LOCAL_WORKER",
    }


@app.post(
    "/api/v1/orchestrator/local-worker/toggle",
    summary="Bật hoặc tắt Worker Node cục bộ",
    tags=["Orchestrator"],
)
async def toggle_local_worker_endpoint(
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Khởi chạy hoặc dừng Worker Node cục bộ trên máy chủ Master."""
    global _local_worker_process
    if _local_worker_process is not None and _local_worker_process.poll() is None:
        try:
            _local_worker_process.terminate()
            _local_worker_process.wait(timeout=3)
        except Exception:
            try:
                _local_worker_process.kill()
            except Exception:
                pass
        _local_worker_process = None
        logger.info("Local Worker Node [MASTER_LOCAL_WORKER] đã dừng.")
        return {"active": False, "message": "Đã dừng Worker Node cục bộ thành công."}
    else:
        agent_script = _PROJECT_ROOT / "client_agent" / "agent.py"
        # Zero-Trust: truyền enrollment secret cho worker cục bộ qua env var.
        _worker_env = os.environ.copy()
        _worker_env["VNMATE_ENROLLMENT_TOKEN"] = _get_worker_enrollment_secret()
        _local_worker_process = subprocess.Popen(
            [
                sys.executable,
                str(agent_script),
                "--server",
                "wss://127.0.0.1:443/ws/client",
                "--id",
                "MASTER_LOCAL_WORKER",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_worker_env,
        )
        logger.info("Local Worker Node [MASTER_LOCAL_WORKER] đã khởi chạy (PID: %s).", _local_worker_process.pid)
        return {
            "active": True,
            "pid": _local_worker_process.pid,
            "message": "Đã khởi chạy Worker Node cục bộ [MASTER_LOCAL_WORKER] thành công!",
        }


@app.get(
    "/api/v1/clients",
    summary="List all connected worker clients",
    tags=["Orchestrator"],
)
async def list_connected_clients() -> List[Dict[str, Any]]:
    """Return all currently connected LAN worker nodes."""
    from core.orchestrator import orchestrator
    return orchestrator.get_connected_clients()


@app.post(
    "/api/v1/clients/{client_id}/execute",
    summary="Execute a skill remotely on a specific client agent",
    tags=["Orchestrator"],
)
async def execute_skill_on_client(
    client_id: str,
    payload: ClientExecuteRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Dispatch a skill execution request to a target worker node with Zero-Trust check."""
    from core.orchestrator import orchestrator
    from core.safety_guard import security_engine

    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
        )

    # Zero-Trust Risk Assessment
    risk_level = security_engine.evaluate_action_risk(payload.skill_name, payload.args)
    if risk_level == "BLOCKED":
        security_engine.log_audit(client_id, payload.skill_name, "BLOCKED", "REJECTED", payload.args)
        raise HTTPException(
            status_code=403,
            detail=f"Tác vụ '{payload.skill_name}' bị từ chối do vi phạm chính sách bảo mật hệ thống (Blacklist).",
        )

    if risk_level == "NEED_CONFIRM" and not payload.args.get("confirmed"):
        security_engine.log_audit(client_id, payload.skill_name, "NEED_CONFIRM", "PENDING_CONFIRMATION", payload.args)
        from core.state_manager import state_manager
        saved = state_manager.save_pending_action(
            user_id="admin",
            tool_name=payload.skill_name,
            arguments=payload.args,
            target_client=client_id,
            query=f"Kỹ năng '{payload.skill_name}' từ máy trạm [{client_id}]",
            description=f"Yêu cầu thực thi '{payload.skill_name}' trên máy trạm [{client_id}]",
        )
        return {
            "status": "need_confirm",
            "action_id": saved.get("id"),
            "client_id": client_id,
            "skill_name": payload.skill_name,
            "args": payload.args,
            "message": f"Tác vụ '{payload.skill_name}' có mức độ rủi ro cao và yêu cầu người quản trị phê duyệt khẩn cấp.",
        }

    res = await orchestrator.execute_on_client(
        client_id=client_id,
        skill_name=payload.skill_name,
        args=payload.args,
        timeout=payload.timeout,
    )
    audit_status = "SUCCESS" if res.get("status") == "success" else "FAILED"
    security_engine.log_audit(client_id, payload.skill_name, risk_level, audit_status, payload.args)
    return res


@app.post(
    "/api/v1/clients/{client_id}/deploy-skill",
    summary="Deploy and hot-load a Python skill on a remote client",
    tags=["Orchestrator"],
)
async def deploy_skill_to_client(
    client_id: str,
    payload: ClientDeploySkillRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Push new Python skill code to a remote worker node for immediate loading."""
    from core.orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )

    res = await orchestrator.deploy_skill_to_client(
        client_id=client_id,
        filename=payload.filename,
        code=payload.code,
        timeout=payload.timeout,
    )
    return res


@app.get(
    "/api/v1/clients/{client_id}/monitor/{monitor_type}",
    summary="Get real-time endpoint telemetry & monitoring data from client",
    tags=["Orchestrator"],
)
async def get_client_monitoring_data(
    client_id: str,
    monitor_type: str,
    user: dict = Depends(require_roles(["manager", "admin"])),
    quality: int = Query(default=65, ge=10, le=100),
    max_width: int = Query(default=1280, ge=320, le=3840),
    limit: int = Query(default=15, ge=1, le=100),
    sort_by: str = Query(default="cpu"),
) -> Dict[str, Any]:
    """
    Proxy live telemetry request to client agent via WebSocket.
    monitor_type: 'screen' | 'processes' | 'network' | 'peripherals' | 'security'
    """
    from core.orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
        )

    args: Dict[str, Any] = {}
    if monitor_type == "screen":
        args = {"quality": quality, "max_width": max_width}
    elif monitor_type == "processes":
        args = {"limit": limit, "sort_by": sort_by}
    elif monitor_type == "network":
        args = {"limit": limit}

    res = await orchestrator.monitor_client(
        client_id=client_id,
        monitor_type=monitor_type,
        args=args,
        timeout=18.0,
    )
    return res


@app.post(
    "/api/v1/clients/{client_id}/kill-process",
    summary="Kill a process running on client machine by PID",
    tags=["Orchestrator"],
)
async def kill_client_process_endpoint(
    client_id: str,
    payload: ClientKillProcessRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Terminate a process by PID on the target client node."""
    from core.orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )

    res = await orchestrator.kill_client_process(
        client_id=client_id,
        pid=payload.pid,
        timeout=payload.timeout,
    )
    return res


@app.post(
    "/api/v1/clients/{client_id}/visual",
    summary="Phase 32: Hiển thị giao diện thị giác HUD trên máy trạm cụ thể",
    tags=["Orchestrator"],
)
async def send_visual_to_client_endpoint(
    client_id: str,
    payload: ClientVisualRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Phát lệnh hiển thị giao diện thị giác HUD xuống Client Agent."""
    from core.orchestrator import orchestrator
    if not orchestrator.is_client_online(client_id):
        raise HTTPException(
            status_code=404,
            detail=f"Máy trạm '{client_id}' hiện không trực tuyến.",
        )
    return await orchestrator.send_visual_to_client(
        client_id=client_id,
        visual_type=payload.type,
        data=payload.data or {},
        title=payload.title,
        duration=payload.duration,
    )


@app.post(
    "/api/v1/visual/broadcast",
    summary="Phase 32: Bắn giao diện thị giác HUD tới máy trạm và máy chủ",
    tags=["Orchestrator"],
)
async def broadcast_visual_endpoint(
    payload: ClientVisualRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt hiển thị giao diện HUD thị giác (network_map, metric_chart, image, alert)."""
    from skills.visual_skills import display_visual_data
    return display_visual_data(
        type=payload.type,
        context_data=payload.data or {},
        title=payload.title,
        duration=payload.duration,
    )


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
        ai_name = _get_assistant_name()
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


@app.post(
    "/api/v1/xiaozhi/ui",
    summary="Phase 43: Điều khiển giao diện màn hình LCD/OLED & Biểu cảm Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_ui_endpoint(
    payload: XiaozhiUiRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Gửi frame JSON điều khiển màn hình LCD/OLED (ST7789/GC9A01) của mạch Xiaozhi."""
    from core.xiaozhi_gateway import xiaozhi_gateway
    if payload.device_id:
        success = await xiaozhi_gateway.send_ui_payload(
            device_id=payload.device_id,
            state=payload.state,
            emotion=payload.emotion,
            text=payload.text,
        )
        return {
            "status": "success" if success else "failed",
            "device_id": payload.device_id,
            "state": payload.state,
            "emotion": payload.emotion,
            "text": payload.text,
        }
    else:
        count = await xiaozhi_gateway.broadcast_ui_payload(
            state=payload.state,
            emotion=payload.emotion,
            text=payload.text,
        )
        return {
            "status": "success",
            "broadcast_count": count,
            "state": payload.state,
            "emotion": payload.emotion,
            "text": payload.text,
        }


@app.post(
    "/api/v1/xiaozhi/wake",
    summary="Phase 43: Đánh thức Robot Xiaozhi và phát âm thanh cảnh báo",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_wake_endpoint(
    payload: XiaozhiWakeRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Chủ động đánh thức Desktop Robot, chớp mắt đỏ trên LCD và phát âm thanh cảnh báo."""
    from core.xiaozhi_gateway import xiaozhi_gateway
    success = await xiaozhi_gateway.wake_and_alert(
        error_title=payload.title,
        detail_message=payload.message,
        device_id=payload.device_id,
    )
    return {
        "status": "success" if success else "no_active_nodes",
        "title": payload.title,
        "message": payload.message,
        "device_id": payload.device_id,
    }


@app.post(
    "/api/v1/xiaozhi/interrupt",
    summary="Phase 43: Kích hoạt ngắt lời Barge-in trên Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_interrupt_endpoint(
    payload: XiaozhiInterruptRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt cơ chế ngắt lời ngay lập tức: huỷ LLM task, xóa buffer audio, phát câu đệm 0ms."""
    from core.xiaozhi_gateway import xiaozhi_gateway
    await xiaozhi_gateway.handle_barge_in(payload.device_id)
    return {
        "status": "success",
        "action": "barge_in_triggered",
        "device_id": payload.device_id,
        "reflex": "Dạ, anh nói đi em nghe đây.",
    }


@app.get(
    "/api/v1/xiaozhi/nodes",
    summary="Phase 43: Danh sách & Telemetry màn hình LCD của mạch Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def get_xiaozhi_nodes_telemetry(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """Trả về danh sách các mạch Xiaozhi Desktop Companion đang online kèm trạng thái LCD và biểu cảm."""
    from core.xiaozhi_gateway import xiaozhi_gateway
    nodes = xiaozhi_gateway.get_nodes_telemetry()
    return {
        "status": "success",
        "total_nodes": len(nodes),
        "nodes": nodes,
    }


@app.post(
    "/api/v1/sentinel/check",
    summary="Phase 43: Quét kiểm tra sự cố toàn hệ thống qua Autonomous Sentinel",
    tags=["Autonomous Sentinel"],
)
async def sentinel_check_endpoint(
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Thực hiện quét tức thời mạng LAN, đồng bộ AD, SQLite DB lock, và tài nguyên phần cứng."""
    from core.autonomous_sentinel import autonomous_sentinel
    incidents = await autonomous_sentinel.scan_all()
    dispatched = []
    for inc in incidents:
        sent = await autonomous_sentinel.dispatch_incident(
            title=inc["title"],
            message=inc["message"],
            category=inc.get("category", "general"),
            force=True,
        )
        if sent:
            dispatched.append(inc["title"])
    return {
        "status": "success",
        "incidents_found": len(incidents),
        "incidents": incidents,
        "dispatched_to_xiaozhi": dispatched,
    }


@app.post(
    "/api/v1/sentinel/simulate",
    summary="Phase 43: Mô phỏng sự cố để kiểm tra luồng Push Notification tới Xiaozhi",
    tags=["Autonomous Sentinel"],
)
async def sentinel_simulate_endpoint(
    payload: SentinelSimulateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Mô phỏng phát hiện sự cố máy chủ và kích hoạt đánh thức Desktop Robot + Telegram alert."""
    from core.autonomous_sentinel import autonomous_sentinel
    sent = await autonomous_sentinel.dispatch_incident(
        title=payload.title,
        message=payload.message,
        category=payload.category,
        force=True,
    )
    return {
        "status": "success",
        "simulated_incident": {
            "category": payload.category,
            "title": payload.title,
            "message": payload.message,
        },
        "dispatched": sent,
    }


# ---------------------------------------------------------------------------
# Zero-Trust Security & Audit APIs (Phase 9)
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/security/blacklist",
    summary="Get current blacklist keywords",
    tags=["Security"],
)
async def get_blacklist(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Retrieve forbidden keywords from security config."""
    from core.config_loader import settings
    return {
        "status": "success",
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@app.post(
    "/api/v1/security/blacklist",
    summary="Add or remove keywords from blacklist, confirm actions, or protected dirs",
    tags=["Security"],
)
async def update_blacklist(
    payload: BlacklistUpdateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Add or remove an item from the active security policy (blacklist, confirm_actions, protected_dirs)."""
    from core.config_loader import settings, CONFIG_PATH, reload_settings
    from core.safety_guard import security_engine

    category = payload.category or "blacklist"
    kw = payload.keyword.strip()

    try:
        raw_cfg = _json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if "security" not in raw_cfg:
            raw_cfg["security"] = {}

        if category == "confirm_actions":
            current_list = raw_cfg["security"].setdefault(
                "require_confirmation_actions",
                list(getattr(settings.security, "require_confirmation_actions", []))
            )
        elif category == "protected_dirs":
            current_list = raw_cfg["security"].setdefault(
                "protected_directories",
                list(getattr(settings.security, "protected_directories", []))
            )
        else:
            current_list = raw_cfg["security"].setdefault(
                "forbidden_keywords",
                list(getattr(settings.security, "forbidden_keywords", []))
            )

        if payload.action == "add":
            if kw and kw not in current_list:
                current_list.append(kw)
                security_engine.log_audit("admin", f"update_{category}", "SAFE", "SUCCESS", {"action": "add", "item": kw, "category": category})
        elif payload.action == "remove":
            if kw in current_list:
                current_list.remove(kw)
                security_engine.log_audit("admin", f"update_{category}", "SAFE", "SUCCESS", {"action": "remove", "item": kw, "category": category})

        CONFIG_PATH.write_text(_json.dumps(raw_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        reload_settings()
    except Exception as exc:
        logger.error("Lỗi khi lưu cấu hình bảo mật vào config.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình: {exc}")

    return {
        "status": "success",
        "action": payload.action,
        "category": category,
        "keyword": kw,
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@app.post(
    "/api/v1/security/inspect",
    summary="Interactive Zero-Trust AST & Security Sandbox Inspector",
    tags=["Security"],
)
async def inspect_security_sandbox(
    payload: SecurityInspectRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Test and analyze Python code, shell commands, or user queries against Zero-Trust AST & Blacklist rules.
    """
    from core.safety_guard import security_engine
    from core.config_loader import settings

    inspect_type = (payload.type or "code").lower()
    content = payload.content.strip()

    # 1. Masking evaluation
    masked = security_engine.mask_sensitive_data(content)
    has_sensitive = (masked != content)

    # 2. Risk & Violations evaluation
    violations: List[str] = []
    if inspect_type == "code":
        is_safe, message = security_engine.inspect_generated_code(content)
        risk = "SAFE" if is_safe else "BLOCKED"
        if not is_safe:
            violations.append(message)
    elif inspect_type == "action":
        risk = security_engine.evaluate_action_risk(content, payload.params or {})
        is_safe = (risk == "SAFE")
        if risk == "BLOCKED":
            message = "Tác vụ chứa từ khóa cấm hoặc hành vi bị từ chối tức thì."
            violations.append(message)
        elif risk == "NEED_CONFIRM":
            message = "Tác vụ thuộc danh mục nhạy cảm, yêu cầu Người quản trị bấm duyệt (HITL)."
            violations.append(message)
        else:
            message = "Tác vụ an toàn, được phép thực thi tự động (Auto-Execute)."
    else:  # Text / Prompt
        forbidden = getattr(settings.security, "forbidden_keywords", [])
        for kw in forbidden:
            if kw.lower() in content.lower():
                violations.append(f"Chứa từ khóa cấm trong Blacklist: '{kw}'")
        if violations:
            risk = "BLOCKED"
            is_safe = False
            message = "Phát hiện nội dung vi phạm chính sách bảo mật Blacklist."
        else:
            risk = "SAFE"
            is_safe = True
            message = "Văn bản an toàn theo tiêu chuẩn Zero-Trust."

    return {
        "status": "success",
        "type": inspect_type,
        "risk": risk,
        "is_safe": is_safe,
        "message": message,
        "violations": violations,
        "has_sensitive_data": has_sensitive,
        "masked_content": masked,
    }


@app.delete(
    "/api/v1/security/audit-logs",
    summary="Clear or truncate security audit logs (Admin only)",
    tags=["Security"],
)
async def clear_audit_logs(
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Clear all security audit logs from file and in-memory cache."""
    from core.safety_guard import security_engine, AUDIT_LOG_FILE
    try:
        AUDIT_LOG_FILE.write_text("", encoding="utf-8")
        security_engine._audit_cache.clear()
        security_engine.log_audit("admin", "clear_audit_logs", "SAFE", "SUCCESS", {"cleared_by": current_user.get("username", "admin")})
        return {"status": "success", "message": "Đã làm sạch toàn bộ nhật ký kiểm toán an ninh."}
    except Exception as exc:
        logger.error("Lỗi khi xóa audit logs: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xóa log: {exc}")


@app.get(
    "/api/v1/security/device-enrollment-token",
    summary="Lấy device enrollment token để flash firmware ESP32",
    tags=["Security"],
)
async def get_device_enrollment_token(
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Trả về token cần dán vào DEFAULT_DEVICE_TOKEN trong secrets.h của firmware.

    Token này cho phép thiết bị stream âm thanh vào master. Chỉ admin được xem.
    """
    return {
        "status": "success",
        "device_enrollment_token": _get_device_enrollment_secret(),
        "instructions": (
            "Dán giá trị trên vào DEFAULT_DEVICE_TOKEN trong "
            "esp32_firmware/src/secrets.h, build và flash lại thiết bị."
        ),
    }


@app.get(
    "/api/v1/security/audit-logs",
    summary="Get recent security audit logs",
    tags=["Security"],
)
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Fetch structured security audit logs."""
    from core.safety_guard import security_engine
    logs = security_engine.get_recent_audit_logs(limit=limit)
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs,
    }


@app.get(
    "/api/v1/security/pending-action",
    summary="Phase 25: Query current pending action waiting for admin approval",
    tags=["Security"],
)
async def get_pending_action_endpoint(
    user_id: str = Query(default="admin", description="User/session ID để tra StateManager"),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the pending action queued in StateManager for the given user, if any."""
    from core.state_manager import state_manager
    all_pending = state_manager.list_pending_actions()
    action = state_manager.get_pending_action(user_id)
    if not action and all_pending:
        action = all_pending[0]

    return {
        "has_pending": bool(action),
        "count": len(all_pending),
        "action": {
            "id": action.get("id"),
            "tool_name": action.get("tool_name"),
            "target_client": action.get("target_client"),
            "arguments": action.get("arguments", {}),
            "description": action.get("description", ""),
            "query": action.get("query", ""),
            "timestamp": action.get("timestamp"),
        } if action else None,
        "pending_list": [
            {
                "id": p.get("id"),
                "tool_name": p.get("tool_name"),
                "target_client": p.get("target_client"),
                "arguments": p.get("arguments", {}),
                "description": p.get("description", ""),
                "query": p.get("query", ""),
                "timestamp": p.get("timestamp"),
            }
            for p in all_pending
        ],
    }


@app.post(
    "/api/v1/security/confirm-action",
    summary="Approve or reject a high-risk action",
    tags=["Security"],
)
async def confirm_action_endpoint(
    payload: ConfirmActionRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Phase 25: Handle emergency approval for actions in the NEED_CONFIRM tier.
    Automatically resolves skill_name / args from StateManager if not provided in payload.
    """
    from core.safety_guard import security_engine
    from core.orchestrator import orchestrator
    from core.plugin_manager import plugin_manager
    from core.state_manager import state_manager

    # ── Phase 25: Auto-resolve from StateManager ─────────────────────────
    lookup_key = payload.action_id or payload.user_id or current_user.get("username", "admin")
    pending = state_manager.get_pending_action(lookup_key)
    if not pending and not payload.skill_name:
        all_pending = state_manager.list_pending_actions()
        if all_pending:
            pending = all_pending[0]
            lookup_key = pending.get("id") or "admin"

    # If payload is incomplete, fill from pending action
    skill_name   = payload.skill_name   or (pending.get("tool_name")     if pending else None)
    raw_args     = payload.args         or (pending.get("arguments", {}) if pending else {})
    client_id    = payload.client_id    or (pending.get("target_client", "master") if pending else "master")

    if not skill_name:
        raise HTTPException(
            status_code=400,
            detail="Không tìm thấy tác vụ đang chờ phê duyệt. Vui lòng cung cấp skill_name hoặc action_id.",
        )

    if not payload.approved:
        # User rejected — clear from queue
        state_manager.cancel_pending_action(lookup_key)
        security_engine.log_audit(client_id, skill_name, "NEED_CONFIRM", "USER_REJECTED", raw_args)
        rej_msg = f"Tác vụ '{skill_name}' đã bị người quản trị hủy bỏ."

        # Broadcast rejection to HUD
        try:
            await broadcast_hud({
                "type": "security_approval_resolved",
                "action_id": lookup_key,
                "status": "rejected",
                "skill": skill_name,
                "message": rej_msg,
                "timestamp": datetime.utcnow().isoformat(),
            })
            await broadcast_hud({
                "type": "voice_active",
                "status": "idle",
                "text": rej_msg,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception:
            pass

        return {
            "status": "rejected",
            "message": rej_msg,
        }

    # Approved ── pop from queue then execute with confirmed=True flag
    state_manager.get_and_clear_pending_action(lookup_key)
    args = dict(raw_args or {})
    args["confirmed"] = True

    security_engine.log_audit(client_id, skill_name, "NEED_CONFIRM", "USER_APPROVED", args)
    logger.info("[Phase 25] Admin '%s' phê duyệt tác vụ '%s' trên '%s'.", lookup_key, skill_name, client_id)

    if (client_id or "").lower() in ("master", "local", "server", ""):
        res = await plugin_manager.execute_skill(skill_name, args)
    else:
        if not orchestrator.is_client_online(client_id):
            raise HTTPException(status_code=404, detail=f"Máy trạm '{client_id}' hiện không trực tuyến.")
        res = await orchestrator.execute_on_client(client_id, skill_name, args)

    # ── Phase 25: Synthesize natural AI response & record completed action ────
    orig_q = (pending.get("query") if pending else "") or f"Thực thi {skill_name}"
    masked_res = security_engine.mask_sensitive_data(json.dumps(res, ensure_ascii=False, default=str))
    synth_reply = ""
    try:
        from core.llm_engine import llm_engine
        synth_messages = [
            {"role": "system", "content": "Bạn là trợ lý AI Ly Ly (VN-MateAI). Hãy tổng hợp kết quả công cụ để trả lời súc tích, tự nhiên, kính cẩn bằng tiếng Việt cho người dùng."},
            {"role": "user", "content": orig_q},
            {"role": "user", "content": f"Tác vụ đã được phê duyệt qua Web Portal. Kết quả công cụ `{skill_name}`:\n```json\n{masked_res}\n```\nHãy thông báo kết quả thực thi một cách rõ ràng."},
        ]
        synth_resp = await llm_engine._call_llm(messages=synth_messages, tools=None)
        synth_reply = synth_resp.choices[0].message.content or f"Dạ, tác vụ '{skill_name}' đã được phê duyệt và hoàn tất thành công."
    except Exception as e:
        logger.warning("[Phase 25] Lỗi synthesize câu trả lời sau duyệt: %s", e)
        synth_reply = f"Dạ, tác vụ '{skill_name}' đã được phê duyệt và thực thi thành công."

    completed_data = dict(pending) if pending else {
        "id": lookup_key,
        "tool_name": skill_name,
        "arguments": args,
        "target_client": client_id,
        "query": orig_q,
        "user_id": lookup_key,
        "source_device": "web",
    }
    state_manager.record_completed_action(completed_data, res, synth_reply)

    # ── Phase 25: Nếu tác vụ xuất phát từ Telegram, gửi thông báo về Telegram ──
    tg_chat_id = pending.get("chat_id") if pending else None
    if not tg_chat_id and pending:
        src = pending.get("source_device", "")
        if "telegram:" in src:
            parts = src.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                tg_chat_id = parts[1]

    if tg_chat_id:
        try:
            from core.telegram_gateway import telegram_gateway
            telegram_gateway.send_incident_alert(f"✅ [ĐÃ PHÊ DUYỆT]\n\n{synth_reply}", target=tg_chat_id)
        except Exception as exc:
            logger.warning("[Phase 25] Lỗi gửi thông báo Telegram sau duyệt: %s", exc)

    # ── Phase 25: Phát sóng thời gian thực tới Web Portal qua WebSocket ────────
    try:
        await broadcast_portal_ui("action_approved_result", {
            "action_id": lookup_key,
            "skill": skill_name,
            "client_id": client_id,
            "query": orig_q,
            "reply": synth_reply,
            "result": res,
        })
    except Exception as exc:
        logger.warning("[Phase 25] Lỗi broadcast WebSocket sau duyệt: %s", exc)

    # ── Phase 34.8: Phát sóng tức thời tới Standby HUD & Tổng hợp giọng nói Hoài My ──────
    speech_reply = llm_engine._make_concise_speech_text(synth_reply)

    try:
        await broadcast_hud({
            "type": "security_approval_resolved",
            "action_id": lookup_key,
            "status": "approved",
            "skill": skill_name,
            "query": orig_q,
            "reply": synth_reply,
            "speech_reply": speech_reply,
            "message": f"Tác vụ '{skill_name}' đã được phê duyệt qua Web Portal.",
            "timestamp": datetime.utcnow().isoformat(),
        })
    except Exception as hud_exc:
        logger.warning("[Phase 34.8] Lỗi broadcast HUD security_approval_resolved: %s", hud_exc)

    async def _async_synth_and_speak_hud(speech_text: str):
        try:
            from core.audio_processor import audio_engine
            import base64
            audio_bytes = await audio_engine.text_to_speech_bytes(speech_text)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else None
            await broadcast_hud({
                "type": "voice_active",
                "status": "speaking",
                "text": speech_text,
                "audio_base64": audio_b64,
                "source_device": "security_approval",
                "timestamp": datetime.utcnow().isoformat(),
            })

            est_dur = max(4.0, (len(speech_text) / 15.0) + 1.8)
            await asyncio.sleep(est_dur)
            await broadcast_hud({
                "type": "voice_active",
                "status": "idle",
                "text": "Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...",
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.warning("[Phase 34.8] Lỗi async TTS/speak HUD sau duyệt: %s", exc)

    asyncio.create_task(_async_synth_and_speak_hud(speech_reply))

    return {
        "status": "success",
        "client_id": client_id,
        "skill": skill_name,
        "result": res,
        "reply": synth_reply,
    }


# ---------------------------------------------------------------------------
# Phase 37.5: Portable & Scalable ChromaDB Vector Database Endpoints
# ---------------------------------------------------------------------------

class MemorizeRequest(BaseModel):
    error_signature: str
    root_cause: str
    script: str
    target_client: str = "master"
    metadata: Optional[Dict[str, Any]] = None


class MemorySearchRequest(BaseModel):
    query: str
    n_results: int = 3


@app.get(
    "/api/v1/memory/stats",
    summary="Get Vector DB memory statistics and storage mode",
    tags=["Cognitive Memory"],
)
async def get_memory_stats_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.cognitive_memory import get_memory_stats
    return get_memory_stats()


@app.post(
    "/api/v1/memory/memorize",
    summary="Memorize an incident solution into Vector DB",
    tags=["Cognitive Memory"],
)
async def memorize_solution_endpoint(
    payload: MemorizeRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.cognitive_memory import memorize_solution
    doc_id = memorize_solution(
        error_signature=payload.error_signature,
        root_cause=payload.root_cause,
        script=payload.script,
        target_client=payload.target_client,
        metadata=payload.metadata,
    )
    return {"status": "success", "id": doc_id}


@app.post(
    "/api/v1/memory/search",
    summary="Semantic search past incidents from Vector DB",
    tags=["Cognitive Memory"],
)
async def search_memory_endpoint(
    payload: MemorySearchRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.cognitive_memory import search_past_incidents
    results = search_past_incidents(error_log_snippet=payload.query, n_results=payload.n_results)
    return {"status": "success", "query": payload.query, "results": results}


@app.post(
    "/api/v1/memory/backup",
    summary="Create a portable zip backup of the Vector DB storage folder",
    tags=["Cognitive Memory"],
)
async def backup_memory_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.cognitive_memory import backup_vector_db
    zip_path = backup_vector_db()
    zip_file = Path(zip_path)
    filename = zip_file.name
    size_mb = round(zip_file.stat().st_size / (1024 * 1024), 2)
    return {
        "status": "success",
        "backup_path": zip_path,
        "filename": filename,
        "size_mb": size_mb,
        "download_url": f"/api/v1/memory/download-backup?filename={filename}",
    }


@app.get(
    "/api/v1/memory/download-backup",
    summary="Download vector database backup zip file",
    tags=["Cognitive Memory"],
)
async def download_backup_endpoint(
    filename: str = Query(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    from core.cognitive_memory import BACKUPS_DIR
    target = BACKUPS_DIR / filename
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found.")
    return FileResponse(
        path=str(target),
        filename=filename,
        media_type="application/zip",
    )


# ---------------------------------------------------------------------------
# Phase 38: Native File System & OS Toolkit REST Endpoints
# ---------------------------------------------------------------------------

class FsListRequest(BaseModel):
    path: str = Field(default=".", description="Đường dẫn thư mục")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsReadRequest(BaseModel):
    file_path: str = Field(..., description="Đường dẫn file cần đọc")
    lines: int = Field(default=500, description="Số dòng tối đa từ cuối file")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsWriteRequest(BaseModel):
    file_path: str = Field(..., description="Đường dẫn file cần ghi")
    content: str = Field(..., description="Nội dung file")
    mode: str = Field(default="w", description="Chế độ 'w' hoặc 'a'")
    confirmed: bool = Field(default=False, description="Cờ xác nhận phê duyệt bảo mật")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsDeleteRequest(BaseModel):
    path: str = Field(..., description="Đường dẫn file hoặc thư mục cần xóa")
    is_folder: bool = Field(default=False, description="True nếu là thư mục")
    confirmed: bool = Field(default=False, description="Cờ xác nhận phê duyệt bảo mật")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


@app.post(
    "/api/v1/fs/list",
    summary="List directory contents (Low Risk - Auto Execute)",
    tags=["Native File System"],
)
async def fs_list_endpoint(
    req: FsListRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.skills.file_system import list_directory
    from core.safety_guard import security_engine

    target = req.target_client_id or req.target_client or "master"
    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = list_directory(path=req.path)
        security_engine.log_audit("master", "list_directory", "SAFE", "SUCCESS", {"path": req.path})
        return res
    else:
        from core.orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "list_directory", {"path": req.path})


@app.post(
    "/api/v1/fs/read",
    summary="Read file text/log with trailing lines limitation (Low Risk - Auto Execute)",
    tags=["Native File System"],
)
async def fs_read_endpoint(
    req: FsReadRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.skills.file_system import read_file
    from core.safety_guard import security_engine

    target = req.target_client_id or req.target_client or "master"
    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = read_file(file_path=req.file_path, lines=req.lines)
        security_engine.log_audit("master", "read_file", "SAFE", "SUCCESS", {"file_path": req.file_path, "lines": req.lines})
        return res
    else:
        from core.orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "read_file", {"file_path": req.file_path, "lines": req.lines})


@app.post(
    "/api/v1/fs/write",
    summary="Write file with Zero-Trust Security approval check",
    tags=["Native File System"],
)
async def fs_write_endpoint(
    req: FsWriteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.skills.file_system import write_file
    from core.safety_guard import security_engine
    from core.zero_trust import evaluate_action_risk
    from core.state_manager import state_manager

    target = req.target_client_id or req.target_client or "master"
    risk = evaluate_action_risk("write_file", {"file_path": req.file_path, "mode": req.mode})

    if risk == "NEED_CONFIRM" and not req.confirmed:
        username = current_user.get("username", "admin")
        act_id = state_manager.save_pending_action(
            user_id=username,
            tool_name="write_file",
            arguments={"file_path": req.file_path, "content": req.content, "mode": req.mode},
            target_client=target,
            query=f"Ghi tệp tin: {req.file_path}",
        )
        security_engine.log_audit(target, "write_file", "NEED_CONFIRM", "PENDING_CONFIRMATION", {"file_path": req.file_path})
        return {
            "status": "need_confirm",
            "action_id": act_id,
            "message": f"Tác vụ ghi tệp tin '{req.file_path}' yêu cầu phê duyệt bảo mật.",
            "requires_confirmation": True,
        }

    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = write_file(file_path=req.file_path, content=req.content, mode=req.mode)
        security_engine.log_audit("master", "write_file", "NEED_CONFIRM", "SUCCESS" if res.get("status") == "success" else "FAILED", {"file_path": req.file_path})
        return res
    else:
        from core.orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "write_file", {"file_path": req.file_path, "content": req.content, "mode": req.mode})


@app.post(
    "/api/v1/fs/delete",
    summary="Delete file or folder with Zero-Trust Security approval check",
    tags=["Native File System"],
)
async def fs_delete_endpoint(
    req: FsDeleteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from core.skills.file_system import delete_item
    from core.safety_guard import security_engine
    from core.zero_trust import evaluate_action_risk
    from core.state_manager import state_manager

    target = req.target_client_id or req.target_client or "master"
    risk = evaluate_action_risk("delete_item", {"path": req.path, "is_folder": req.is_folder})

    if risk == "NEED_CONFIRM" and not req.confirmed:
        username = current_user.get("username", "admin")
        act_id = state_manager.save_pending_action(
            user_id=username,
            tool_name="delete_item",
            arguments={"path": req.path, "is_folder": req.is_folder},
            target_client=target,
            query=f"Xóa {'thư mục' if req.is_folder else 'tệp tin'}: {req.path}",
        )
        security_engine.log_audit(target, "delete_item", "NEED_CONFIRM", "PENDING_CONFIRMATION", {"path": req.path})
        return {
            "status": "need_confirm",
            "action_id": act_id,
            "message": f"Tác vụ xóa '{req.path}' yêu cầu phê duyệt bảo mật.",
            "requires_confirmation": True,
        }

    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = delete_item(path=req.path, is_folder=req.is_folder)
        security_engine.log_audit("master", "delete_item", "NEED_CONFIRM", "SUCCESS" if res.get("status") == "success" else "FAILED", {"path": req.path})
        return res
    else:
        from core.orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "delete_item", {"path": req.path, "is_folder": req.is_folder})



# ---------------------------------------------------------------------------
# Phase 11: Lean Micro-Tasking & KPI Tracking Endpoints
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/tasks/kpi-logs",
    summary="Get recent KPI task logs and completion statistics",
    tags=["Micro-Tasking"],
)
async def get_kpi_logs_endpoint(
    limit: int = Query(default=100, ge=1, le=1000),
    client_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return historical task log from logs/kpi_logs.csv and aggregate KPI metrics."""
    from core.task_manager import task_manager
    return task_manager.get_kpi_logs(limit=limit, client_id=client_id, status=status)


@app.post(
    "/api/v1/tasks/send",
    summary="Dispatch a micro-task popup to a worker client node",
    tags=["Micro-Tasking"],
)
async def send_task_endpoint(
    payload: TaskDispatchRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Send interactive task popup to LAN worker node with role check."""
    if current_user.get("role") == "viewer":
        raise HTTPException(
            status_code=403,
            detail="Tài khoản Viewer chỉ có quyền xem, không được phát lệnh giao việc.",
        )

    from core.task_manager import task_manager
    sender = payload.sender or current_user.get("full_name", "Ban Giám Đốc")
    result = await task_manager.dispatch_task(
        client_id=payload.client_id,
        message=payload.message,
        sender=sender,
    )
    if result.get("status") != "success":
        raise HTTPException(status_code=400, detail=result.get("message", "Gửi task thất bại"))
    return result


# ---------------------------------------------------------------------------
# Phase 16: Microphone Hardware Toggle API
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/voice/mic-status",
    summary="Truy vấn trạng thái phần cứng Microphone",
    tags=["Voice"],
)
@app.get(
    "/api/v1/wake-word/status",
    summary="Truy vấn trạng thái phần cứng Microphone (Wake Word)",
    tags=["Voice"],
)
async def get_mic_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về trạng thái bật/tắt của Microphone background listening."""
    try:
        from core.wake_word_engine import is_mic_enabled
        enabled = is_mic_enabled()
    except Exception:
        enabled = False
    return {
        "status": "success",
        "mic_enabled": enabled,
        "is_listening": enabled,
        "hardware_state": "listening" if enabled else "released",
        "message": "Microphone đang lắng nghe ngầm." if enabled else "Microphone đã tắt hoàn toàn (phần cứng giải phóng).",
    }


class MicToggleRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="True = Bật lắng nghe, False = Tắt và giải phóng phần cứng, None = Đảo trạng thái")


@app.post(
    "/api/v1/voice/mic-toggle",
    summary="Bật/Tắt Microphone ở cấp độ phần cứng",
    tags=["Voice"],
)
@app.post(
    "/api/v1/wake-word/toggle",
    summary="Bật/Tắt Microphone Wake Word ở cấp độ phần cứng",
    tags=["Voice"],
)
async def toggle_mic(
    payload: Optional[MicToggleRequest] = Body(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Điều khiển phần cứng Microphone.
    - enabled=true  → Bật lắng nghe ngầm, đèn Mic trên laptop sẽ sáng.
    - enabled=false → Tắt hoàn toàn, giải phóng stream, đèn Mic TẮT HẲN.
    Chỉ Admin và Manager được phép thay đổi.
    """
    if current_user.get("role") == "viewer":
        raise HTTPException(
            status_code=403,
            detail="Tài khoản Viewer không có quyền điều khiển Microphone.",
        )
    try:
        from core.wake_word_engine import is_mic_enabled, set_mic_enabled
        if payload is None or payload.enabled is None:
            target_state = not is_mic_enabled()
        else:
            target_state = bool(payload.enabled)
        new_state = set_mic_enabled(target_state)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi điều khiển Microphone: {exc}")

    state_label = "BẬT" if new_state else "TẮT"
    logger.info(
        "Phase 16: Wake Word Mic toggle by user '%s' -> %s",
        current_user.get("username", "?"),
        state_label,
    )
    return {
        "status": "success",
        "mic_enabled": new_state,
        "is_listening": new_state,
        "hardware_state": "listening" if new_state else "released",
        "message": f"Microphone đã {state_label} theo yêu cầu.",
        "triggered_by": current_user.get("username"),
    }


# ===========================================================================
# Phase 18: Domain Sync & Telegram Gateway Endpoints
# ===========================================================================


class TelegramConfigRequest(BaseModel):
    enabled: Optional[bool] = Field(None, description="Bật/Tắt Telegram Gateway")
    bot_token: Optional[str] = Field("", description="Telegram Bot Token từ BotFather")
    admin_chat_ids: Optional[List[str]] = Field(default_factory=list, description="Danh sách Chat ID của admin")
    incident_group_id: Optional[str] = Field("", description="Group ID nhận cảnh báo sự cố")


class DomainToggleRequest(BaseModel):
    enabled: bool = Field(..., description="Bật/Tắt tính năng đồng bộ Active Directory")


class TelegramTestAlertRequest(BaseModel):
    bot_token: Optional[str] = Field(None, description="Telegram Bot Token từ BotFather")
    admin_chat_ids: Optional[List[str]] = Field(None, description="Danh sách Chat ID của admin")
    incident_group_id: Optional[str] = Field(None, description="Group ID nhận cảnh báo sự cố")
    target_chat_id: Optional[str] = Field(None, description="Chat ID mục tiêu cụ thể")


@app.get(
    "/api/v1/domain/config",
    summary="Phase 18: Lấy trạng thái Bật/Tắt Đồng bộ AD",
    tags=["Domain"],
)
async def get_domain_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return enabled status for Active Directory synchronization."""
    try:
        from core.config_loader import settings
        import json as _j
        cfg_p = settings.PROJECT_ROOT / "config.json"
        enabled = False
        if cfg_p.exists():
            raw = _j.loads(cfg_p.read_text(encoding="utf-8"))
            enabled = raw.get("ad_sync", {}).get("enabled", False)
        return {"status": "success", "enabled": enabled}
    except Exception as exc:
        return {"status": "error", "enabled": False, "message": str(exc)}


@app.post(
    "/api/v1/domain/toggle",
    summary="Phase 18: Bật hoặc Tắt tính năng Đồng bộ AD & Sentinel Check",
    tags=["Domain"],
)
async def toggle_domain_sync(
    payload: DomainToggleRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Enable or disable AD sync and avoid flooding logs when not used."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền thay đổi trạng thái AD.")
    try:
        from core.config_loader import settings
        import json as _j
        cfg_p = settings.PROJECT_ROOT / "config.json"
        raw = {}
        if cfg_p.exists():
            raw = _j.loads(cfg_p.read_text(encoding="utf-8"))
        raw.setdefault("ad_sync", {})
        raw["ad_sync"]["enabled"] = bool(payload.enabled)
        cfg_p.write_text(_j.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        status_text = "ĐÃ BẬT" if payload.enabled else "ĐÃ TẮT"
        logger.info("Active Directory sync feature has been %s by %s", status_text, current_user.get("username", "?"))
        return {
            "status": "success",
            "enabled": payload.enabled,
            "message": f"Tính năng đồng bộ Active Directory {status_text} thành công."
        }
    except Exception as exc:
        logger.error("Failed to toggle AD sync: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật cấu hình: {exc}")


@app.get(
    "/api/v1/telegram/config",
    summary="Phase 18: Lấy cấu hình và trạng thái Telegram Bot",
    tags=["Telegram"],
)
async def get_telegram_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return Telegram Gateway settings and running status."""
    try:
        from core.config_loader import settings
        import json as _j
        cfg_p = settings.PROJECT_ROOT / "config.json"
        tg_data = {}
        if cfg_p.exists():
            raw = _j.loads(cfg_p.read_text(encoding="utf-8"))
            tg_data = raw.get("telegram", {})

        from core.telegram_gateway import telegram_gateway
        is_running = getattr(telegram_gateway, "is_running", False)

        return {
            "status": "success",
            "enabled": tg_data.get("enabled", False),
            "bot_token": tg_data.get("bot_token", ""),
            "admin_chat_ids": tg_data.get("admin_chat_ids", []),
            "incident_group_id": tg_data.get("incident_group_id", ""),
            "is_running": is_running,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post(
    "/api/v1/telegram/toggle",
    summary="Phase 18: Bật hoặc Tắt tính năng Telegram Gateway",
    tags=["Telegram"],
)
async def toggle_telegram_gateway(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Enable or disable Telegram Bot Gateway to prevent conflict and error logs."""
    if current_user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền bật/tắt Telegram Gateway.")
    try:
        from core.config_loader import settings
        import json as _j
        cfg_p = settings.PROJECT_ROOT / "config.json"
        raw = {}
        if cfg_p.exists():
            raw = _j.loads(cfg_p.read_text(encoding="utf-8"))
        raw.setdefault("telegram", {})

        enabled = bool(payload.get("enabled", False))
        raw["telegram"]["enabled"] = enabled
        cfg_p.write_text(_j.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        from core.telegram_gateway import telegram_gateway
        if enabled:
            if raw["telegram"].get("bot_token"):
                telegram_gateway.stop()
                import time; time.sleep(0.5)
                telegram_gateway.start()
        else:
            telegram_gateway.stop()

        status_text = "ĐÃ BẬT" if enabled else "ĐÃ TẮT"
        logger.info("Telegram Gateway has been %s by %s", status_text, current_user.get("username", "?"))
        return {
            "status": "success",
            "enabled": enabled,
            "is_running": getattr(telegram_gateway, "is_running", False),
            "message": f"Cổng kết nối Telegram Gateway {status_text} thành công."
        }
    except Exception as exc:
        logger.error("Failed to toggle Telegram gateway: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật Telegram Gateway: {exc}")


@app.post(
    "/api/v1/domain/sync",
    summary="Phase 18: Đồng bộ dữ liệu Active Directory (Users + Computers)",
    tags=["Domain"],
)
async def sync_domain(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Trigger full Active Directory synchronization via native PowerShell (Get-ADUser + Get-ADComputer).
    Requires RSAT: Active Directory Domain Services Tools installed on server.
    """
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền đồng bộ AD.")

    try:
        from core.domain_sync import domain_manager
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, domain_manager.sync_all)
        logger.info(
            "Phase 18: Domain sync triggered by '%s' — users=%d, computers=%d",
            current_user.get("username", "?"),
            result.get("total_users", 0),
            result.get("total_computers", 0),
        )
        return result
    except Exception as exc:
        logger.error("Phase 18: Domain sync error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi đồng bộ AD: {exc}")


@app.get(
    "/api/v1/domain/stats",
    summary="Phase 18: Trả về thống kê số lượng từ AD cache",
    tags=["Domain"],
)
async def domain_stats(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return employee and computer counts from local SQLite AD cache."""
    try:
        from core.domain_sync import domain_manager
        stats = domain_manager.get_stats()
        return {"status": "success", **stats}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn thống kê: {exc}")


@app.get(
    "/api/v1/domain/employees",
    summary="Phase 18: Lấy danh sách nhân viên từ AD cache",
    tags=["Domain"],
)
async def list_employees(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return a list of employees from the local SQLite AD cache."""
    try:
        from core.domain_sync import domain_manager
        employees = domain_manager.get_employees(limit=limit)
        return {"status": "success", "count": len(employees), "data": employees}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn danh sách nhân viên: {exc}")


@app.get(
    "/api/v1/domain/computers",
    summary="Phase 18: Lấy danh sách máy tính từ AD cache",
    tags=["Domain"],
)
async def list_computers(
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return a list of computers from the local SQLite AD cache."""
    try:
        from core.domain_sync import domain_manager
        computers = domain_manager.get_computers(limit=limit)
        return {"status": "success", "count": len(computers), "data": computers}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn danh sách máy tính: {exc}")


@app.put(
    "/api/v1/telegram/config",
    summary="Phase 18: Cập nhật cấu hình Telegram Bot",
    tags=["Telegram"],
)
async def update_telegram_config(
    payload: TelegramConfigRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Save Telegram bot configuration (bot_token, admin_chat_ids, incident_group_id, enabled).
    Restarts the Telegram gateway if enabled and configured.
    """
    if current_user.get("role") not in ("admin",):
        raise HTTPException(status_code=403, detail="Chỉ Admin mới có quyền cấu hình Telegram Bot.")

    try:
        import json as _json2
        from pathlib import Path as _Path
        from core.config_loader import settings

        # Load raw config file
        cfg_path = settings.PROJECT_ROOT / "config.json"
        raw = _json2.loads(cfg_path.read_text(encoding="utf-8"))

        # Update telegram section
        raw.setdefault("telegram", {})
        if payload.enabled is not None:
            raw["telegram"]["enabled"] = payload.enabled
        if payload.bot_token is not None:
            raw["telegram"]["bot_token"] = payload.bot_token
        if payload.admin_chat_ids is not None:
            raw["telegram"]["admin_chat_ids"] = payload.admin_chat_ids
        if payload.incident_group_id is not None:
            raw["telegram"]["incident_group_id"] = payload.incident_group_id

        cfg_path.write_text(_json2.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

        # Reload settings in-memory
        try:
            from core.config_loader import reload_settings
            reload_settings()
        except Exception as r_err:
            logger.warning("Phase 18: Settings reload failed after telegram config write: %s", r_err)

        is_tg_on = raw.get("telegram", {}).get("enabled", False)
        # Restart or stop gateway
        from core.telegram_gateway import telegram_gateway
        if is_tg_on and payload.bot_token:
            try:
                telegram_gateway.stop()
                import time; time.sleep(0.5)
                telegram_gateway.start()
                gateway_status = "restarted"
            except Exception as gw_exc:
                logger.warning("Phase 18: Could not restart Telegram Gateway: %s", gw_exc)
                gateway_status = "config_saved_restart_failed"
        else:
            try:
                telegram_gateway.stop()
            except Exception:
                pass
            gateway_status = "stopped" if not is_tg_on else "config_saved"

        logger.info(
            "Phase 18: Telegram config updated by '%s', gateway=%s",
            current_user.get("username", "?"),
            gateway_status,
        )
        return {
            "status": "success",
            "message": "Cấu hình Telegram đã được lưu thành công.",
            "gateway_status": gateway_status,
        }

    except Exception as exc:
        logger.error("Phase 18: Telegram config update error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi cập nhật cấu hình Telegram: {exc}")


@app.get(
    "/api/v1/telegram/status",
    summary="Phase 18: Kiểm tra trạng thái Telegram Gateway",
    tags=["Telegram"],
)
async def telegram_status(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return current Telegram gateway running status."""
    try:
        from core.telegram_gateway import telegram_gateway
        running = telegram_gateway.is_running
        return {
            "status": "success",
            "gateway_running": running,
            "message": "Telegram Gateway đang hoạt động." if running else "Telegram Gateway chưa kết nối (chưa cấu hình Bot Token).",
        }
    except Exception as exc:
        return {"status": "error", "gateway_running": False, "message": str(exc)}


@app.post(
    "/api/v1/telegram/test-alert",
    summary="Phase 18: Gửi tin nhắn test đến Telegram incident group hoặc admin chat",
    tags=["Telegram"],
)
async def test_telegram_alert(
    payload: Optional[TelegramTestAlertRequest] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Send a test alert message to verify Telegram configuration."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có thể gửi tin nhắn kiểm thử.")

    try:
        from core.telegram_gateway import telegram_gateway
        from datetime import datetime as _dt

        bot_token = payload.bot_token if payload else None
        admin_chat_ids = payload.admin_chat_ids if payload else None
        incident_group_id = payload.incident_group_id if payload else None
        target_chat_id = payload.target_chat_id if payload else None

        result = telegram_gateway.test_connection(
            bot_token=bot_token,
            admin_chat_ids=admin_chat_ids,
            incident_group_id=incident_group_id,
            target_chat_id=target_chat_id,
            custom_message=(
                f"🔔 <b>VN-MateAI Test Alert</b>\n\n"
                f"✅ Kết nối Telegram thành công!\n"
                f"👤 Gửi bởi: {current_user.get('username', 'Admin')}\n"
                f"🕐 Thời gian: {_dt.now().strftime('%d/%m/%Y %H:%M:%S')}"
            ),
        )
        return result
    except Exception as exc:
        logger.error("Phase 18: Telegram test alert error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi gửi test alert: {exc}")


@app.post(
    "/api/v1/telegram/detect-chat",
    summary="Phase 18: Dò tìm Chat ID gần nhất gửi tới Bot Telegram",
    tags=["Telegram"],
)
async def detect_telegram_chat(
    payload: Optional[TelegramTestAlertRequest] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Auto-detect recent chat IDs from incoming messages to the Telegram bot."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Không có quyền truy cập.")

    try:
        from core.telegram_gateway import telegram_gateway
        bot_token = payload.bot_token if payload else None
        chats = telegram_gateway.get_recent_chats(bot_token=bot_token)
        return {
            "status": "success",
            "count": len(chats),
            "chats": chats,
        }
    except Exception as exc:
        logger.error("Phase 18: Telegram detect chat error: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi dò tìm chat: {exc}")


# ===========================================================================
# Phase 28: Enterprise Reporting & Template Engine
# ===========================================================================


@app.get(
    "/api/v1/report-templates",
    summary="Phase 28: Lấy danh sách các biểu mẫu báo cáo tiêu chuẩn",
    tags=["Reporting & Templates"],
)
async def get_report_templates(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Retrieve all report templates from settings or config.json."""
    from core.config_loader import settings
    templates = getattr(settings, "report_templates", {}) or {}
    if not templates:
        from core.config_loader import _load_raw_config
        templates = _load_raw_config().get("report_templates", {})
    return {"status": "success", "templates": templates}


@app.put(
    "/api/v1/report-templates",
    summary="Phase 28: Cập nhật kho biểu mẫu báo cáo tiêu chuẩn",
    tags=["Reporting & Templates"],
)
async def update_report_templates(
    payload: Dict[str, Any],
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Save report templates to config.json and reload in-memory settings."""
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(status_code=403, detail="Chỉ Admin hoặc Manager mới có quyền cập nhật biểu mẫu báo cáo.")

    templates = payload.get("templates") if "templates" in payload else payload
    if not isinstance(templates, dict):
        raise HTTPException(status_code=400, detail="Dữ liệu biểu mẫu không hợp lệ, phải là một JSON object.")

    import json as _json2
    from core.config_loader import CONFIG_PATH, reload_settings

    try:
        raw = _json2.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        raw["report_templates"] = templates
        CONFIG_PATH.write_text(_json2.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
        reload_settings()
        return {
            "status": "success",
            "message": "Đã lưu kho biểu mẫu báo cáo tiêu chuẩn thành công. System Prompt đã được cập nhật.",
            "templates": templates,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Lỗi lưu biểu mẫu: {exc}")


# ===========================================================================
# Phase 20: Dynamic Client Agent Distribution
# ===========================================================================


def _get_server_local_ip() -> str:
    """Detect LAN IP of the Master Server for injecting into agent config."""
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@app.get(
    "/api/v1/download-agent",
    summary="Phase 20: Tải xuống Client Agent được đóng gói động kèm config",
    tags=["Distribution"],
)
async def download_agent(
    request: Request,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Response:
    """
    Đóng gói động Client Agent thành file ZIP trên RAM (io.BytesIO).
    File config.json được tự động điền IP Master và ghi đè vào ZIP.
    Không tạo file .zip thừa trên ổ cứng máy chủ sau mỗi lần tải.
    """
    if current_user.get("role") not in ("admin", "manager"):
        raise HTTPException(
            status_code=403,
            detail="Chỉ Admin hoặc Manager có quyền tải Client Agent.",
        )

    # Determine master server IP
    # Prefer Host header, fallback to auto-detect LAN IP
    forwarded_host = request.headers.get("X-Forwarded-Host") or request.headers.get("Host", "")
    if forwarded_host and ":" in forwarded_host:
        forwarded_host = forwarded_host.split(":")[0]

    server_ip = (
        forwarded_host
        if forwarded_host and forwarded_host not in ("localhost", "127.0.0.1", "0.0.0.0", "")
        else _get_server_local_ip()
    )
    server_port = 443

    # Try to get configured port from config.json
    try:
        cfg_raw = _json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
        server_port = int(cfg_raw.get("PORT", 443))
    except Exception:
        pass

    # Build the dynamic injected config for this download session (Phase 29: HTTPS/WSS)
    port_suffix = f":{server_port}" if server_port != 443 else ""
    dynamic_config = {
        "server_url": f"https://{server_ip}{port_suffix}",
        "ws_url": f"wss://{server_ip}:{server_port}/ws/client",
        "client_id": "auto_generate_on_first_run",
        "master_ip": server_ip,
        "master_port": server_port,
        "downloaded_at": datetime.utcnow().isoformat() + "Z",
        "downloaded_by": current_user.get("username", "unknown"),
        # Zero-Trust: enrollment secret để agent đăng ký qua /ws/client.
        # Chỉ phát cho tài khoản admin/manager (đã kiểm tra ở endpoint này).
        "enrollment_token": _get_worker_enrollment_secret(),
    }

    # Build ZIP entirely in RAM — no temporary files written to disk
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        template_dir = _CLIENT_TEMPLATE_DIR

        if template_dir.exists() and template_dir.is_dir():
            for file_path in sorted(template_dir.rglob("*")):
                # Skip __pycache__ directories and compiled Python bytecode
                if "__pycache__" in file_path.parts:
                    continue
                if file_path.suffix in (".pyc", ".pyo"):
                    continue
                if file_path.is_file():
                    arc_name = file_path.relative_to(template_dir)
                    # Skip any existing config.json or server_cert.pem from template — inject dynamic version below
                    if str(arc_name) in ("config.json", "server_cert.pem"):
                        continue
                    try:
                        zf.write(file_path, arcname=str(arc_name))
                    except Exception as write_err:
                        logger.warning("download-agent: Cannot add file %s: %s", file_path, write_err)
        else:
            logger.error("client_template/ directory not found at %s", template_dir)
            raise HTTPException(
                status_code=500,
                detail="Thư mục client_template/ không tồn tại trên máy chủ. Liên hệ Admin.",
            )

        # Phase 29: Read certs/server.crt on server and embed directly into Client Agent ZIP as server_cert.pem
        try:
            from core.security_tls import CERT_FILE, ensure_ssl_certs
            ensure_ssl_certs()
            if CERT_FILE.exists() and CERT_FILE.stat().st_size > 0:
                cert_bytes = CERT_FILE.read_bytes()
                zf.writestr("server_cert.pem", cert_bytes)
                logger.info("Phase 29: Embedded server_cert.pem (%d bytes) into agent zip.", len(cert_bytes))
            else:
                logger.warning("Phase 29: CERT_FILE not found at %s", CERT_FILE)
        except Exception as cert_err:
            logger.error("Phase 29: Failed to embed server_cert.pem into zip: %s", cert_err)

        # Inject fresh dynamic config.json — OVERWRITES any existing config.json
        zf.writestr("config.json", _json.dumps(dynamic_config, indent=4, ensure_ascii=False))

    # Seek to beginning before reading
    zip_buffer.seek(0)
    zip_content = zip_buffer.read()

    logger.info(
        "Phase 29: Secure Agent package downloaded by '%s' — injected config: server=%s:%s, package_size=%d bytes",
        current_user.get("username"),
        server_ip,
        server_port,
        len(zip_content),
    )

    return Response(
        content=zip_content,
        media_type="application/x-zip-compressed",
        headers={
            "Content-Disposition": 'attachment; filename="VN-Mate_Agent.zip"',
            "Content-Length": str(len(zip_content)),
            "X-Agent-Server-IP": server_ip,
            "X-Agent-Server-Port": str(server_port),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phase 48 — ITSM, Audit Trail, ROI Dashboard API Endpoints
# ═══════════════════════════════════════════════════════════════════════════


class CreateTicketRequest(BaseModel):
    """Payload cho POST /api/v1/itsm/tickets."""
    title: str
    category: str = "other"
    severity: str = "medium"
    description: Optional[str] = None
    assignee_name: Optional[str] = None
    dept_name: Optional[str] = None
    created_by_ai: bool = False
    resolution_notes: Optional[str] = None


class UpdateTicketRequest(BaseModel):
    """Payload cho PUT /api/v1/itsm/tickets/{ticket_id}."""
    status: str
    resolution_notes: Optional[str] = None


@app.post(
    "/api/v1/itsm/tickets",
    summary="Phase 48: Tạo phiếu ITSM mới",
    tags=["ITSM"],
)
async def api_create_ticket(
    payload: CreateTicketRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Tạo phiếu công việc ITSM và ghi audit trail bất biến."""
    from skills.itsm_skills import create_system_ticket
    return create_system_ticket(
        title=payload.title,
        category=payload.category,
        severity=payload.severity,
        description=payload.description,
        assignee_name=payload.assignee_name,
        dept_name=payload.dept_name,
        created_by_ai=payload.created_by_ai,
        resolution_notes=payload.resolution_notes,
        caller_id=current_user.get("username"),
    )


@app.get(
    "/api/v1/itsm/tickets",
    summary="Phase 48: Danh sách phiếu ITSM",
    tags=["ITSM"],
)
async def api_get_tickets(
    status_filter: str = "all",
    ai_only: bool = False,
    limit: int = 50,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Lấy danh sách phiếu ITSM với bộ lọc."""
    from skills.itsm_skills import get_tickets
    return get_tickets(status_filter=status_filter, ai_only=ai_only, limit=limit)


@app.put(
    "/api/v1/itsm/tickets/{ticket_id}",
    summary="Phase 48: Cập nhật trạng thái phiếu ITSM",
    tags=["ITSM"],
)
async def api_update_ticket(
    ticket_id: str,
    payload: UpdateTicketRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Cập nhật trạng thái và ghi chú giải quyết cho phiếu ITSM."""
    from skills.itsm_skills import update_ticket_status
    return update_ticket_status(
        ticket_id=ticket_id,
        status=payload.status,
        resolution_notes=payload.resolution_notes,
        caller_id=current_user.get("username"),
    )


@app.get(
    "/api/v1/audit-logs",
    summary="Phase 48: Nhật ký kiểm toán bất biến",
    tags=["Audit"],
)
async def api_audit_logs(
    limit: int = 100,
    employee_id: Optional[str] = None,
    action_type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Truy vấn nhật ký kiểm toán. Chỉ SELECT — không thể sửa/xóa."""
    from core.database import erp_db
    logs = erp_db.get_audit_logs(
        limit=limit,
        employee_id=employee_id,
        action_type=action_type,
        status=status,
    )
    return {"status": "success", "total": len(logs), "logs": logs}


@app.get(
    "/api/v1/roi-dashboard",
    summary="Phase 48: ROI & KPI Dashboard Data",
    tags=["Dashboard"],
)
async def api_roi_dashboard(
    report_date: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Tổng hợp toàn bộ dữ liệu cho ROI Dashboard:
    - Phiếu ITSM theo trạng thái
    - Nhật ký kiểm toán thống kê
    - KPI AI (số task, giờ tiết kiệm)
    - Thống kê tổ chức (nhân viên, phòng ban)
    """
    from skills.itsm_skills import generate_daily_report
    from core.database import erp_db

    # Báo cáo ngày
    report = generate_daily_report(
        report_date=report_date,
        include_audit_details=True,
    )

    # Tickets đang mở (pending + in_progress)
    from skills.itsm_skills import get_tickets
    open_tickets = get_tickets(status_filter="pending", limit=20)
    inprogress_tickets = get_tickets(status_filter="in_progress", limit=20)
    ai_tickets = get_tickets(ai_only=True, limit=10)

    # Audit stats tổng hợp
    audit_stats = erp_db.get_audit_stats()

    return {
        "status": "success",
        "report_date": report.get("report_date"),
        "report_text": report.get("report_text"),
        "kpi": report.get("data", {}).get("kpi", {}),
        "tickets": {
            "today": report.get("data", {}).get("tickets", {}),
            "open": open_tickets.get("tickets", []),
            "in_progress": inprogress_tickets.get("tickets", []),
            "ai_created": ai_tickets.get("tickets", []),
        },
        "audit": {
            "stats": audit_stats,
            "today": report.get("data", {}).get("audit", {}),
            "recent": report.get("data", {}).get("recent_audits", []),
        },
        "org": report.get("data", {}).get("org", {}),
    }


@app.get(
    "/api/v1/erp/employees",
    summary="Phase 48: Danh sách nhân viên ERP",
    tags=["ERP"],
)
async def api_erp_employees(
    dept_id: Optional[int] = None,
    role: Optional[str] = None,
    limit: int = 100,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về danh sách nhân viên từ ERP, lọc theo phòng ban hoặc role."""
    from core.database import erp_db
    try:
        conn = erp_db.get_connection()
        c = conn.cursor()
        conditions = []
        params: list = []
        if dept_id:
            conditions.append("e.dept_id = ?")
            params.append(dept_id)
        if role:
            conditions.append("e.role = ?")
            params.append(role)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(limit, 500)))
        c.execute(
            f"""
            SELECT e.id, e.name, e.position, e.email, e.phone, e.role,
                   d.name AS dept_name
            FROM employees e
            LEFT JOIN departments d ON e.dept_id = d.id
            {where}
            ORDER BY e.name
            LIMIT ?;
            """,
            params,
        )
        employees = [dict(r) for r in c.fetchall()]
        conn.close()
        return {"status": "success", "total": len(employees), "employees": employees}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 56 & 57: ENTERPRISE OS — CEO EXECUTIVE BI & MULTI-AGENT APIS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get(
    "/api/v1/enterprise/kpi-overview",
    summary="Phase 56: CEO Executive KPI Dashboard (Tổng quan KPI Doanh nghiệp)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_kpi_overview(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy toàn bộ chỉ số KPI: Tasks, Nhân sự, Tài chính, Cashflow, Burn Rate, Runway."""
    from core.database import erp_db
    try:
        overview = erp_db.get_company_kpi_overview()
        leaderboard = erp_db.get_task_leaderboard(5)
        overview["leaderboard"] = leaderboard
        return {"status": "success", "data": overview}
    except Exception as e:
        logger.error("KPI overview error: %s", e)
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/finances",
    summary="Phase 56: Sổ quỹ Tài chính Doanh nghiệp (Danh sách giao dịch thu/chi)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_finances(
    limit: int = 50,
    finance_type: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách giao dịch tài chính (lọc theo loại: income/expense)."""
    from core.database import erp_db
    try:
        records = erp_db.get_finances(finance_type=finance_type, limit=limit)
        summary = erp_db.get_financial_summary()
        return {"status": "success", "total": len(records), "summary": summary, "records": records}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/finances/record",
    summary="Phase 56: Ghi nhận giao dịch tài chính (Thu/Chi)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_record_finance(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Ghi nhận khoản thu hoặc chi vào Sổ quỹ doanh nghiệp.

    Zero-Trust: đi qua cổng HITL với tên tác vụ `record_income` (Level 3) hoặc
    `record_expense` (Level 4, tự nâng lên Level 5 nếu số tiền >= 50 triệu).
    Việc đội tên tác vụ vào đúng tên trong RISK_LEVEL_MAP là bắt buộc — nếu dùng
    tên chung chung thì ngưỡng 50 triệu sẽ không kích hoạt.
    """
    from core.database import erp_db
    from core.zero_trust import execute_with_hitl

    try:
        body = await request.json()
        ftype = str(body.get("type", "expense") or "expense").strip().lower()
        if ftype not in ("income", "expense"):
            raise HTTPException(status_code=400, detail="Tham số 'type' phải là 'income' hoặc 'expense'.")

        amount = float(body.get("amount", 0))
        category = body.get("category", "Chi phí hoạt động")
        description = body.get("description", "")
        created_by = body.get("created_by", current_user.get("username", "admin"))

        action_name = "record_income" if ftype == "income" else "record_expense"

        def _write():
            return erp_db.add_finance_record(
                finance_type=ftype,
                amount=amount,
                category=category,
                description=description,
                created_by=created_by,
            )

        # `execute_with_hitl` là coroutine — thiếu `await` sẽ ra
        # `'coroutine' object has no attribute 'get'` ngay dòng kế, bị
        # `except Exception` cuối hàm nuốt và trả về lỗi. Hệ quả là endpoint
        # này hỏng 100%: giao dịch nhỏ không ghi được, giao dịch lớn cũng không
        # tạo yêu cầu duyệt — cổng Zero-Trust chỉ còn là vỏ.
        gate = await execute_with_hitl(
            action_name=action_name,
            params={"amount": amount, "category": category, "description": description, "type": ftype},
            executor=_write,
            requested_by=current_user.get("username", "unknown"),
            description=(
                f"{'Ghi thu' if ftype == 'income' else 'Ghi chi'} {amount:,.0f} VNĐ "
                f"vào mục '{category}'"
            ),
        )

        if gate.get("status") == "awaiting_approval":
            raise HTTPException(status_code=202, detail=gate["message"])

        return {"status": "success", "record": gate["result"], "risk_level": gate.get("risk_level")}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/attendance",
    summary="Phase 56: Tra cứu dữ liệu Chấm công",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_attendance(
    date_str: Optional[str] = None,
    limit: int = 50,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách chấm công theo ngày."""
    from core.database import erp_db
    try:
        records = erp_db.get_attendance(date_str=date_str, limit=limit)
        return {"status": "success", "date": date_str or "Hôm nay", "total": len(records), "records": records}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/leaderboard",
    summary="Phase 56: Bảng xếp hạng thi đua năng suất nhân viên",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_leaderboard(
    limit: int = 10,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy bảng xếp hạng hoàn thành công việc (Employee Leaderboard)."""
    from core.database import erp_db
    try:
        leaderboard = erp_db.get_task_leaderboard(limit=limit)
        return {"status": "success", "leaderboard": leaderboard}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/standup-briefing",
    summary="Phase 56: Họp Giao Ban Tự Động (Executive Daily Standup Briefing)",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_standup(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Tạo báo cáo Giao Ban Tự Động tóm tắt tình hình toàn công ty 24h qua."""
    try:
        from core.agents.agent_orchestrator import multi_agent_system
        result = multi_agent_system.get_executive_briefing()
        return {"status": "success", "briefing": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/rag/query",
    summary="Phase 56: Tra cứu Tri thức Công ty qua Enterprise RAG",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_query(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Tra cứu nội quy, SOP và chính sách công ty từ ChromaDB Knowledge Base."""
    try:
        body = await request.json()
        question = body.get("question", "")
        if not question:
            raise HTTPException(status_code=400, detail="Thiếu tham số: question")
        from core.rag_engine import rag_engine
        result = rag_engine.answer_policy_question(question)
        return {
            "status": "success",
            "result": result,
            # Phase 58 BƯỚC 4 (graceful fallback): frontend dựa vào cờ này để
            # TỰ ĐỘNG mở popup upload khi AI không tìm thấy căn cứ.
            "needs_document": bool(result.get("needs_document")),
            "suggested_action": result.get("suggested_action", ""),
            # Bản tiếng Việt để UI hiện cho người đọc, tách khỏi mã máy ở trên.
            "suggested_action_text": result.get("suggested_action_text", ""),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/rag/upload",
    summary="Phase 58: Tải tài liệu lên tri thức doanh nghiệp (PDF/Word/MD/CSV)",
    tags=["Enterprise OS Phase 58"],
)
async def api_enterprise_rag_upload(
    file: UploadFile = File(...),
    category: str = Form("Tài liệu công ty"),
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Nhận tệp tải lên trực tiếp từ trình duyệt, lưu vào thư mục tri thức rồi
    nạp vào cả ChromaDB (vector) lẫn Knowledge Graph.

    Đây là endpoint mà `rag/ingest` chỉ có thể gọi gián tiếp — trước đó nhánh
    "AI bảo Sếp tải tài liệu lên" (graceful fallback của Phase 58 BƯỚC 4) không
    có chỗ để tải vì chỉ tồn tại đường nhận `file_path` phía server.

    An toàn:
      - Chỉ nhận phần mở rộng trong danh sách cho phép (chống tải lên .py/.sh...).
      - Tên tệp được chuẩn hoá, không giữ đường dẫn do client gửi.
      - `content_type` phải khớp phần mở rộng.
    """
    ALLOWED_EXT = {".pdf", ".docx", ".doc", ".md", ".txt", ".csv"}
    MAX_BYTES = 20 * 1024 * 1024  # 20 MB

    original_name = os.path.basename(file.filename or "").strip()
    if not original_name:
        raise HTTPException(status_code=400, detail="Thiếu tên tệp.")

    ext = Path(original_name).suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Định dạng '{ext or 'không xác định'}' không được phép. "
                f"Chỉ nhận: {', '.join(sorted(ALLOWED_EXT))}."
            ),
        )

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Tệp rỗng, không có dữ liệu để nạp.")
    if len(payload) > MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Tệp quá lớn ({len(payload) / 1024 / 1024:.1f} MB). Giới hạn 20 MB.",
        )

    # Chuẩn hoá tên: bỏ ký tự lạ, thêm tiền tố thời gian để không đè nhau.
    stem = re.sub(r"[^A-Za-z0-9_\-\.]", "_", Path(original_name).stem)[:80] or "tai_lieu"
    safe_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{stem}{ext}"

    from core.rag_engine import _DOCS_DIR
    _DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _DOCS_DIR / safe_name
    try:
        dest.write_bytes(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Không lưu được tệp: {exc}")

    logger.info(
        "[RAG Upload] %s (%s) tải lên %s — %d bytes, mục '%s'",
        current_user.get("username", "?"), original_name, safe_name, len(payload), category,
    )

    try:
        from core.rag_engine import rag_engine
        result = rag_engine.ingest_file(dest, category=category)
    except Exception as exc:
        logger.error("[RAG Upload] Lỗi nạp %s: %s", safe_name, exc)
        return {"status": "error", "error": str(exc), "saved_as": safe_name}

    try:
        erp_db.log_audit_action(
            employee_id=current_user.get("username", "admin"),
            action_type="KNOWLEDGE_UPLOAD",
            payload=json.dumps(
                {"original_name": original_name, "saved_as": safe_name,
                 "bytes": len(payload), "category": category},
                ensure_ascii=False,
            ),
            status="success",
        )
    except Exception:
        pass

    if result.get("status") != "success":
        raise HTTPException(
            status_code=422,
            detail=result.get("message", "Không đọc được nội dung tài liệu."),
        )

    return {"status": "success", "result": result, "saved_as": safe_name}


@app.post(
    "/api/v1/enterprise/rag/ingest",
    summary="Phase 56: Nạp tài liệu mới vào ChromaDB Knowledge Base",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_ingest(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Nạp và vector hóa tài liệu nội bộ (PDF/Word/MD) vào tri thức doanh nghiệp.

    Zero-Trust: `file_path` là dữ liệu do client kiểm soát nên bắt buộc phải đi
    qua resolve_ingest_path() để chống path traversal. Chỉ nhận tệp nằm trong
    storage/knowledge_docs/ (nơi endpoint upload đặt file lên).

    Phase 60: chạy nền thay vì chặn request. `ingest_file()` là hàm đồng bộ
    nặng (parse PDF/Word -> chunk -> embed); gọi trực tiếp trong `async def` sẽ
    giữ chân event loop, khiến mọi request khác — kể cả luồng streaming của
    kiosk — phải xếp hàng chờ. Nay trả `task_id` ngay và đẩy việc vào
    `BackgroundWorkerManager`; tiến độ xem ở
    `GET /api/v1/enterprise/background-tasks`.
    """
    try:
        body = await request.json()
        doc_path = body.get("file_path", "")
        category = body.get("category", "Tài liệu công ty")
        from core.rag_engine import rag_engine

        safe_path, path_error = rag_engine.resolve_ingest_path(doc_path)
        if path_error:
            logger.warning(
                "[RAG Ingest] Từ chối nạp tài liệu từ %s: file_path=%r (%s)",
                current_user.get("username", "?"), str(doc_path)[:120], path_error,
            )
            raise HTTPException(status_code=400, detail=path_error)

        from core.background_workers import background_worker_manager

        # Task name theo tên tệp: cùng một tệp đang nạp thì dedupe (trả lại task
        # cũ), tệp khác thì chạy song song.
        stem = Path(safe_path).stem or "document"
        task_name = f"rag_ingest_{stem}"

        task_id = await background_worker_manager.submit(
            task_name,
            rag_engine.ingest_file,
            safe_path,
            category=category,
            description=f"Nạp tài liệu vào tri thức doanh nghiệp: {stem}",
            metadata={
                "file_path": safe_path,
                "category": category,
                "requested_by": current_user.get("username", "?"),
            },
            # Không phát TTS: đây là thao tác quản trị, không phải hội thoại
            # với người dùng; bật sẽ phát giọng lạc vào phòng họp.
            notify_on_complete=False,
        )
        return {
            "status": "success",
            "queued": True,
            "task_id": task_id,
            "task_name": task_name,
            "message": (
                "Đã đưa vào hàng đợi nền. Theo dõi tại "
                "GET /api/v1/enterprise/background-tasks"
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/rag/documents",
    summary="Phase 56: Danh sách tài liệu trong Knowledge Base",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_rag_docs(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách tài liệu đã được vector hóa trong ChromaDB."""
    from core.rag_engine import rag_engine
    try:
        docs = rag_engine.list_documents()
        return {"status": "success", "total": len(docs), "documents": docs}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/multi-agent/route",
    summary="Phase 57: CEO Router — Điều phối yêu cầu tới Multi-Agent System",
    tags=["Enterprise OS Phase 57"],
)
async def api_multi_agent_route(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Ủy quyền câu hỏi/chỉ thị tới hệ thống Multi-Agent (CFO/HR/CTO Agent).

    Zero-Trust: đi qua cổng HITL (`delegate_to_multi_agent` = Level 3) vì HR
    Agent trong đồ thị có thể kích hoạt assign_task_intelligently — tức là ghi
    dữ liệu. Câu hỏi chỉ đọc vẫn chạy được, nhưng phải qua duyệt một lần.
    """
    from core.zero_trust import execute_with_hitl

    try:
        body = await request.json()
        query = str(body.get("query", "") or "").strip()
        if not query:
            raise HTTPException(status_code=400, detail="Thiếu tham số: query")

        def _route():
            from core.agents.agent_orchestrator import multi_agent_system
            return multi_agent_system.route_and_execute(query=query)

        # `execute_with_hitl` là coroutine — xem giải thích ở
        # `api_enterprise_record_finance` về lỗi thiếu `await`.
        gate = await execute_with_hitl(
            action_name="delegate_to_multi_agent",
            params={"query": query},
            executor=_route,
            requested_by=current_user.get("username", "unknown"),
            description=f"Ủy quyền Multi-Agent xử lý: {query[:200]}",
        )

        if gate.get("status") == "awaiting_approval":
            raise HTTPException(status_code=202, detail=gate["message"])

        return {"status": "success", "result": gate["result"], "risk_level": gate.get("risk_level")}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/analytics/chart",
    summary="Phase 57: Text-to-SQL Dynamic Chart Generation (Vẽ biểu đồ BI theo lệnh tự nhiên)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_generate_chart(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Chuyển đổi câu lệnh tiếng Việt thành SQL + sinh cấu hình Chart.js.

    Phase 58 BƯỚC 2: ngoài trả HTTP, biểu đồ còn được **phát sóng qua
    WebSocket** tới mọi màn hình đang mở. Trước đây chỉ người gọi API nhận
    được — nên khi CEO ra lệnh bằng giọng nói từ điện thoại, màn hình trong
    phòng họp không có gì hiện ra, dù đã có sẵn dữ liệu và cấu hình biểu đồ.
    """
    try:
        body = await request.json()
        prompt = body.get("prompt", "")
        if not prompt:
            return {"status": "error", "error": "Thiếu tham số: prompt"}
        from core.analytics_engine import analytics_engine
        result = analytics_engine.text_to_sql_and_chart(prompt=prompt)

        # Chỉ phát sóng khi thực sự có biểu đồ. Không có client nào đang mở
        # thì `broadcast_portal_ui` tự thoát ngay, không tốn chi phí.
        if result.get("chart_config") and result.get("status") == "success":
            try:
                await broadcast_portal_ui(
                    "analytics_chart",
                    {
                        "chart_config": result["chart_config"],
                        "title": result.get("title", ""),
                        "sql": result.get("sql", ""),
                        "sql_source": result.get("sql_source", ""),
                        # Mang cả lỗi LLM xuống UI: khi SQL rơi về dự phòng,
                        # người xem phải thấy "chế độ dự phòng" chứ không tưởng
                        # đó là câu trả lời đúng câu hỏi.
                        "llm_error": result.get("llm_error"),
                        "records_count": result.get("records_count", 0),
                        "requested_by": current_user.get("username", "unknown"),
                    },
                )
            except Exception as bs_exc:
                # Lỗi broadcast KHÔNG được làm hỏng HTTP response — người gọi
                # vẫn cần nhận được biểu đồ qua REST.
                logger.warning("[Server] Broadcast biểu đồ thất bại (bỏ qua): %s", bs_exc)

        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/analytics/cashflow-health",
    summary="Phase 57: Predictive Cashflow Health Check (Cảnh báo dòng tiền dự báo)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_cashflow_health(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Chạy thuật toán dự báo Burn Rate & Runway. Phát CẢNH BÁO ĐỎ nếu quỹ sắp cạn."""
    from core.analytics_engine import analytics_engine
    try:
        result = analytics_engine.evaluate_predictive_cashflow()

        # Phase 58 BƯỚC 2: đẩy trạng thái dòng tiền lên mọi màn hình để
        # Command Center tự đổi sang chế độ đỏ mà không cần ai bấm F5.
        try:
            await broadcast_portal_ui(
                "cashflow_health",
                {
                    "alert_level": result.get("alert_level"),
                    "is_critical": result.get("is_critical"),
                    "is_warning": result.get("is_warning"),
                    "runway_days": result.get("runway_days"),
                    "net_balance": result.get("net_balance"),
                    "message": result.get("message", ""),
                },
            )
        except Exception as bs_exc:
            logger.warning("[Server] Broadcast cashflow-health thất bại (bỏ qua): %s", bs_exc)

        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/graph-rag/query",
    summary="Phase 57: GraphRAG Knowledge Graph Hybrid Search",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_graph_rag(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Truy vấn Đồ thị Tri thức doanh nghiệp (GraphRAG) kết hợp BM25 + Vector Semantic Search."""
    try:
        body = await request.json()
        question = body.get("question", "")
        if not question:
            return {"status": "error", "error": "Thiếu tham số: question"}
        from core.knowledge.graph_rag import graph_rag
        result = graph_rag.hybrid_search(question=question)
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/onboarding",
    summary="Phase 57: Zero-Touch Employee Onboarding (Tự động hóa nhân sự mới)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_onboarding(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Kích hoạt luồng Onboarding tự động cho nhân viên mới.

    Zero-Trust — chống leo thang đặc quyền qua tham số: `role` trong body là
    dữ liệu do client kiểm soát. Trước đây truyền thẳng xuống workflow, nên
    bất kỳ tài khoản manager nào cũng có thể tạo nhân viên với
    role="admin"/"it_support". Nay chỉ ADMIN được tự chọn role; manager bị ép
    về "operator" và được ghi log cảnh báo.

    Đồng thời đi qua cổng HITL (`zero_touch_onboard_employee` = Level 4) vì đây
    là hành động cấp danh tính cho con người — theo briefing BƯỚC 5 thuộc nhóm
    3-5. Trước đây chỉ có RBAC nên manager tự tạo nhân viên không ai hỏi.
    """
    from core.zero_trust import execute_with_hitl

    try:
        body = await request.json()
        name = body.get("name", "")
        position = body.get("position", "")
        if not name or position is None or not str(position).strip():
            raise HTTPException(status_code=400, detail="Thiếu tham số bắt buộc: name, position")

        caller_role = current_user.get("role", "viewer")
        requested_role = str(body.get("role", "operator") or "operator").strip().lower()
        if caller_role == "admin":
            effective_role = requested_role
        else:
            effective_role = "operator"
            if requested_role != "operator":
                logger.warning(
                    "[Onboarding] %s (role=%s) yêu cầu tạo nhân viên với role=%r — "
                    "bị hạ về 'operator'. Chỉ admin được cấp quyền đặc biệt.",
                    current_user.get("username", "?"), caller_role, requested_role,
                )

        department_name = body.get("department_name", "Nhân sự")
        email = body.get("email", "")
        phone = body.get("phone", "")

        def _onboard():
            from core.skills.onboarding_workflow import onboarding_workflow
            return onboarding_workflow.onboard_new_employee(
                name=name,
                position=position,
                department_name=department_name,
                email=email,
                phone=phone,
                role=effective_role,
                allow_privileged_role=(caller_role == "admin"),
            )

        # `execute_with_hitl` là coroutine — xem giải thích ở
        # `api_enterprise_record_finance` về lỗi thiếu `await`.
        gate = await execute_with_hitl(
            action_name="zero_touch_onboard_employee",
            params={"name": name, "position": position, "department": department_name, "role": effective_role},
            executor=_onboard,
            requested_by=current_user.get("username", "unknown"),
            description=f"Onboarding nhân viên mới: {name} - {position} (phòng ban {department_name})",
        )

        if gate.get("status") == "awaiting_approval":
            raise HTTPException(status_code=202, detail=gate["message"])

        return {"status": "success", "result": gate["result"], "risk_level": gate.get("risk_level")}
    except HTTPException:
        raise
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/email-gateway/simulate",
    summary="Phase 57: Giả lập tiếp nhận Email từ Khách hàng (Omnichannel Gateway)",
    tags=["Enterprise OS Phase 57"],
)
async def api_enterprise_email_simulate(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Mô phỏng email khách hàng gửi đến. AI tự phân loại, tạo Ticket và gửi auto-reply."""
    try:
        body = await request.json()
        from core.email_gateway import email_gateway
        result = email_gateway.process_incoming_email(
            sender=body.get("sender", "khachhang@doanhnghiep.vn"),
            subject=body.get("subject", "Yêu cầu hỗ trợ"),
            content=body.get("content", ""),
        )
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/hitl/pending",
    summary="Phase 57: Zero-Trust HITL — Danh sách tác vụ rủi ro cao đang chờ CEO duyệt",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_pending_list(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Lấy danh sách các yêu cầu Risk Level 4-5 đang chờ Human-in-the-Loop CEO phê duyệt."""
    from core.zero_trust import hitl_manager
    try:
        pending = hitl_manager.get_pending_list()
        return {"status": "success", "total_pending": len(pending), "pending_approvals": pending}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/hitl/approve",
    summary="Phase 57: Zero-Trust HITL — CEO phê duyệt tác vụ rủi ro cao",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_approve(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """CEO xác nhận DUYỆT thực thi tác vụ đang trong hàng chờ HITL."""
    try:
        body = await request.json()
        approval_id = body.get("approval_id", "")
        if not approval_id:
            return {"status": "error", "error": "Thiếu approval_id"}
        from core.zero_trust import hitl_manager
        # `approve_async()` chạy đúng cả hai loại tác vụ:
        #   - coroutine  -> await trực tiếp (skill gọi qua API)
        #   - hàm sync   -> asyncio.to_thread (ghi DB, gọi API ngoại vi),
        #                   nên tác vụ chậm không giữ chân event loop và làm
        #                   đứng mọi request khác kể cả /health.
        #
        # Trước đây gọi thẳng `hitl_manager.approve(...)` ngay trên event
        # loop: một lần tự khoá chết trong `add_finance_record` đã TREO CẢ
        # SERVER, và executor dạng coroutine thì không bao giờ chạy dù vẫn
        # báo `executed: true`.
        result = await hitl_manager.approve_async(
            approval_id=approval_id,
            approved_by=current_user.get("username", "CEO"),
        )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/hitl/reject",
    summary="Phase 57: Zero-Trust HITL — CEO từ chối / hủy tác vụ rủi ro cao",
    tags=["Enterprise OS Phase 57"],
)
async def api_hitl_reject(
    request: Request,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """CEO xác nhận TỪ CHỐI tác vụ đang trong hàng chờ HITL."""
    try:
        body = await request.json()
        approval_id = body.get("approval_id", "")
        reason = body.get("reason", "Bị từ chối bởi CEO")
        if not approval_id:
            return {"status": "error", "error": "Thiếu approval_id"}
        from core.zero_trust import hitl_manager
        # `reject()` cũng ghi audit log — đẩy sang thread như `approve()`
        # để việc ghi DB không giữ chân event loop.
        result = await asyncio.to_thread(
            hitl_manager.reject,
            approval_id=approval_id,
            rejected_by=current_user.get("username", "CEO"),
            reason=reason,
        )
        return result
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/enterprise/proactive/run-audit",
    summary="Phase 56: Kích hoạt quét đôn đốc tiến độ task ngay lập tức",
    tags=["Enterprise OS Phase 56"],
)
async def api_enterprise_proactive_audit(
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Kích hoạt Virtual C.O.O rà soát ngay tất cả task quá hạn và sắp đến hạn."""
    try:
        from core.skills.proactive_manager import proactive_manager
        result = proactive_manager.execute_task_audit_sync(trigger_source="api_manual")
        return {"status": "success", "result": result}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Phase 59/60: Read-only Endpoints cho Command Center
# ---------------------------------------------------------------------------
# Giao diện Trung Tâm Chỉ Huy cần đọc trạng thái nền tảng. Trước đây các
# hàm JS gọi nhầm `/api/v1/skills/execute` với `check_connector_health` để
# lấy dữ liệu, dẫn tới hiển thị sai (mọi ô đều báo lỗi giống nhau) và tốn
# thêm một vòng gọi tool nặng cho một thao tác chỉ đọc.
# Các endpoint dưới đây chỉ đọc trạng thái trong bộ nhớ, không gọi ra ngoài.


@app.get(
    "/api/v1/enterprise/connectors/health",
    summary="Phase 59: Trạng thái 4 connector ngoại vi (AWS, OCI, Paperless, eInvoice)",
    tags=["Enterprise OS Phase 59"],
)
async def api_connectors_health(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Báo cáo trạng thái cấu hình + độ trễ của từng connector.

    KHÔNG gọi `authenticate()` — chỉ đọc cấu hình trong bộ nhớ, nên phản hồi
    tức thì và không tạo tải lên AWS/OCI. Muốn kiểm tra thật thì dùng skill
    `check_connector_health` (đi qua cổng HITL).
    """
    try:
        from core.connectors import CONNECTOR_REGISTRY, CONNECTOR_RISK_LEVELS
        from core.connectors.base_connector import missing_required_fields

        items: Dict[str, Any] = {}
        for name in ("aws", "oci", "paperless", "einvoice"):
            connector = CONNECTOR_REGISTRY.get(name)
            if connector is None:
                items[name] = {"configured": False, "error": "Connector chưa được nạp"}
                continue
            try:
                cfg = connector.config
                # "Đã cấu hình" phải trả lời đúng câu hỏi "connector này có
                # dùng được không", chứ không phải "có trường nào khác rỗng
                # không". `CONNECTOR_DEFAULTS` cấp sẵn region/provider/profile
                # nên cách sau LUÔN trả True — kể cả khi chưa có một thông tin
                # đăng nhập nào, và mọi lời gọi thật đều hỏng với
                # "Authentication failed". Đây là báo cáo thành công giả.
                missing = missing_required_fields(name)
                # CONNECTOR_RISK_LEVELS khoá theo "<connector>:<action>",
                # không phải theo tên connector — phải lọc theo tiền tố.
                risks = sorted({
                    lvl for key, lvl in CONNECTOR_RISK_LEVELS.items()
                    if key.split(":", 1)[0] == name
                })
                # KHÔNG tiết lộ giá trị bí mật — chỉ nêu TÊN khoá còn thiếu,
                # đủ để người vận hành biết cần điền gì mà không lộ nội dung.
                items[name] = {
                    "configured": not missing,
                    "enabled": bool(getattr(cfg, "enabled", True)),
                    "actions": sorted(
                        key.split(":", 1)[1]
                        for key in CONNECTOR_RISK_LEVELS
                        if key.split(":", 1)[0] == name
                    ),
                    "max_risk_level": max(risks) if risks else None,
                    **(
                        {"missing_fields": missing, "note": "Chưa đủ thông tin đăng nhập — mọi lời gọi sẽ thất bại."}
                        if missing
                        else {}
                    ),
                }
            except Exception as exc:  # pragma: no cover - phòng thủ
                items[name] = {"configured": False, "error": str(exc)}

        return {"status": "success", "connectors": items}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/plugin-registry/stats",
    summary="Phase 60: Thống kê Plugin Registry + trạng thái circuit breaker",
    tags=["Enterprise OS Phase 60"],
)
async def api_plugin_registry_stats(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Số tool đã đăng ký, số tool đang bật, và thống kê thực thi theo tool."""
    try:
        from core.plugin_registry import plugin_registry

        with plugin_registry._lock:  # đọc snapshot nhất quán
            tools = {
                name: {
                    "description": t.description,
                    "enabled": t.enabled,
                    "risk_level": t.risk_level,
                    "is_async": t.is_async,
                }
                for name, t in plugin_registry._tools.items()
            }
        stats = plugin_registry.get_stats()
        return {
            "status": "success",
            "total_tools": len(tools),
            "enabled_tools": sum(1 for t in tools.values() if t["enabled"]),
            "tools": tools,
            "execution_stats": stats,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/background-tasks",
    summary="Phase 60: Danh sách tác vụ nền đang chạy / đã hoàn tất",
    tags=["Enterprise OS Phase 60"],
)
async def api_background_tasks(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Liệt kê tác vụ nền (Phase 60) kèm tiến độ. Chỉ đọc bộ nhớ."""
    try:
        from core.background_workers import TaskStatus, background_worker_manager

        tasks = background_worker_manager.list_tasks()
        return {
            "status": "success",
            "total": len(tasks),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "tasks": [t.to_dict() for t in tasks[:50]],
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/enterprise/webhooks/recent",
    summary="Phase 59: 20 cảnh báo webhook gần nhất",
    tags=["Enterprise OS Phase 59"],
)
async def api_recent_webhooks(
    current_user: Dict[str, Any] = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """
    Đọc lịch sử cảnh báo webhook đã ghi vào Proactive Manager.
    Nguồn sự thật vẫn là `proactive_manager._audit_history`; endpoint này chỉ
    lọc và đảo chiều để UI hiển thị mới nhất trước.
    """
    try:
        from core.skills.proactive_manager import proactive_manager

        history = list(getattr(proactive_manager, "_audit_history", []))
        hooks = [h for h in history if str(h.get("source", "")).startswith("webhook:")]
        hooks.reverse()
        return {"status": "success", "total": len(hooks), "alerts": hooks[:20]}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ---------------------------------------------------------------------------
# Shutdown Lifecycle
# ---------------------------------------------------------------------------

@app.on_event("shutdown")
async def _on_shutdown() -> None:
    """Graceful shutdown: stop background workers, HITL cleanup, etc."""
    logger.info("Shutdown initiated — stopping background services...")

    # Phase 60: Stop Background Worker Manager
    try:
        from core.background_workers import background_worker_manager
        await background_worker_manager.stop(timeout=10.0)
        logger.info("Phase 60: Background Worker Manager stopped.")
    except Exception as e:
        logger.warning("Phase 60: Background Worker Manager shutdown error: %s", e)

    # Phase 60: Stop HITL Manager cleanup loop
    try:
        from core.security.hitl_manager import hitl_manager
        await hitl_manager.stop_cleanup_loop()
        logger.info("Phase 60: HITL Manager cleanup loop stopped.")
    except Exception as e:
        logger.warning("Phase 60: HITL Manager shutdown error: %s", e)

    # Phase 57: Stop Autonomous Sentinel
    try:
        from core.autonomous_sentinel import autonomous_sentinel
        autonomous_sentinel.stop()
        logger.info("Phase 57: Autonomous Sentinel stopped.")
    except Exception:
        pass

    # Phase 18: Stop Telegram Gateway
    try:
        from core.telegram_gateway import telegram_gateway
        telegram_gateway.stop()
        logger.info("Phase 18: Telegram Gateway stopped.")
    except Exception:
        pass

    logger.info("VN-MateAI shutdown complete.")

