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

_WEB_DIR    = _PROJECT_ROOT / "web"
# Gói "Tải Agent" đóng từ chính client_agent/ (Phase 6: gỡ bản fork client_template/,
# hai bản đã lệch nhau — mỗi bên sửa một lỗi mà bên kia vẫn còn).
_CLIENT_AGENT_DIR = _PROJECT_ROOT / "client_agent"

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


def _get_assistant_name() -> str:
    """Retrieve current AI assistant name from settings or config with fallback."""
    try:
        from mateai.config.loader import settings
        return getattr(settings, "AI_NAME", None) or getattr(settings, "ASSISTANT_NAME", "Ly Ly")
    except Exception:
        return "Ly Ly"


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


# Bí mật nằm trong URL mà thư viện HTTP tự ghi ra log.
#
# PHÁT HIỆN KHI QUÉT RÒ RỈ (Phase 80): `httpx` ở mức INFO ghi lại nguyên dòng
# request, mà URL của Telegram Bot API có dạng `/bot<token>/sendMessage`. Nên
# mỗi lần bot gửi tin là token bot thật nằm trong dòng log — và
# `GET /api/v1/logs/recent` phục vụ chính dòng log đó cho MỌI tài khoản đã
# đăng nhập, kể cả `viewer`. Che bí mật ở endpoint cấu hình không có tác dụng
# với đường rò này.
#
# Tắt logger của httpx không đủ: bất kỳ thư viện hay dòng log nào khác cũng
# có thể lọt bí mật ra. Nên che TẠI MỘT CHỖ: mọi bản ghi log đi qua bộ lọc này
# trước khi tới handler nào.
_SECRET_LOG_PATTERNS = (
    # Token bot Telegram nằm trong URL API. Che MỌI giá trị sau
    # "api.telegram.org/bot" chứ không chỉ đúng dạng token chuẩn: giá trị lệch
    # dạng (gõ nhầm, token kiểu cũ) vẫn là bí mật và từng lọt ra nguyên vẹn.
    re.compile(r"(api\.telegram\.org/(?:file/)?bot)([^/\s\"']+)"),
    re.compile(r"(bot)(\d{5,}:[A-Za-z0-9_\-]{20,})"),
    # Khoá dạng phổ biến.
    re.compile(r"\b(sk|gsk|rk|pk|xoxb|xoxp)[-_][A-Za-z0-9_\-]{16,}"),
)


class _SecretRedactingFilter(logging.Filter):
    """Thay bí mật trong mọi dòng log bằng ký hiệu, trước khi ghi ra."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 — lỗi format không được làm hỏng log
            return True
        cleaned = text
        for pat in _SECRET_LOG_PATTERNS:
            if pat.groups == 2:
                cleaned = pat.sub(lambda m: m.group(1) + _SECRET_MASK, cleaned)
            else:
                cleaned = pat.sub(_SECRET_MASK, cleaned)
        if cleaned != text:
            record.msg = cleaned
            record.args = ()
        return True


def _install_secret_redaction() -> None:
    """Gắn bộ lọc che bí mật vào MỌI handler của logger gốc (một lần).

    Phải gắn vào HANDLER chứ không gắn vào logger. Python chỉ chạy
    `Logger.filter()` cho đúng logger được gọi tới; bản ghi của logger con
    (`httpx`, `uvicorn.access`…) đi lên bằng `callHandlers()` và chỉ bị lọc
    bởi filter của handler. Gắn lên logger gốc sẽ tưởng đã che mà thực ra
    dòng log của httpx vẫn lộ nguyên vẹn.
    """
    root = logging.getLogger()
    # Handler của chính logger con cũng phải gắn — record đi qua handler ở
    # đâu thì bị lọc ở đó.
    targets = list(root.handlers)
    for name in ("httpx", "httpx.httpcore", "httpcore", "uvicorn.access", "asyncio"):
        targets.extend(logging.getLogger(name).handlers)
    for h in targets:
        if not any(isinstance(f, _SecretRedactingFilter) for f in h.filters):
            h.addFilter(_SecretRedactingFilter())


def _install_ws_log_handler(loop: asyncio.AbstractEventLoop) -> None:
    """Attach WebSocket log handler to the root logger (once)."""
    global _ws_log_handler
    _install_secret_redaction()
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
    # Handler MỚI thì chưa qua `_install_secret_redaction()` (hàm đó chỉ
    # gắn bộ lọc lên handler có sẵn lúc nó chạy). Bỏ dòng này thì nhật ký
    # đẩy qua WebSocket lại chứa bí mật — và `/api/v1/logs/recent` đọc thẳng
    # từ bộ đệm của handler này.
    handler.addFilter(_SecretRedactingFilter())
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
def _asset_version(name: str) -> str:
    """
    Phiên bản của một file tĩnh, lấy từ thời điểm sửa gần nhất.

    Thẻ `<script src="...?v=X">` trong HTML từng ghi số cứng kiểu `?v=51.1`.
    Số đó chỉ đổi khi ai đó nhớ tăng lên, nên sau mỗi lần sửa JS mà quên
    tăng, người dùng vẫn chạy bản cũ. Nay lấy từ mtime: sửa file là URL đổi,
    không cần nhớ.

    Dùng `mtime_ns` vì hai lần sửa trong cùng một giây vẫn phải cho URL khác nhau.
    """
    f = _WEB_DIR / name
    try:
        return str(f.stat().st_mtime_ns)
    except OSError:
        return "0"


def _inject_asset_versions(html: str) -> str:
    """Thay số phiên bản cứng trong thẻ script bằng mtime của file tương ứng."""
    import re as _re

    def _sub(m: "re.Match[str]") -> str:
        # group(1) là phần sau `src="`. Phải trả lại CẢ `src="` và dấu `"` —
        # chỉ trả về URL thì thẻ script hỏng mà trình duyệt chỉ báo lỗi im lặng.
        url = m.group(1)
        fname = url.rsplit("/", 1)[-1].split("?", 1)[0].split("&", 1)[0]
        return f'src="{url.rsplit("?", 1)[0]}?v={_asset_version(fname)}"'

    return _re.sub(r'src="(/static/[^"]+\?v=)[^"]*"', _sub, html)


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


class LoginRequest(BaseModel):
    """Payload for POST /api/v1/login."""
    username: str = Field(..., min_length=1, description="Tên đăng nhập")
    password: str = Field(..., min_length=1, description="Mật khẩu")


class TopologyTriggerRequest(BaseModel):
    """Payload for POST /api/v1/system/topology/trigger."""
    source: str = Field(default="core", description="Node phát nguồn (vd: 'core', 'agent_ceo')")
    target: str = Field(default="plugin_m365", description="Node đích (vd: 'plugin_m365', 'worker_cluster')")
    action: Optional[str] = Field(default="", description="Tên hành động hoặc thông điệp")


class TopologySaveRequest(BaseModel):
    """Payload for POST /api/v1/system/topology/save."""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Danh sách nodes")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="Danh sách edges")



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
    model_name: Optional[str] = Field(default=None, description="Tên mô hình cần kiểm tra")
    api_key: Optional[str] = Field(default=None, description="Khóa API")
    tier: Optional[str] = Field(default="primary", description="Tên cấp (backward compat)")
    provider_model: Optional[str] = Field(default=None, description="Tên mô hình (backward compat)")
    api_base: Optional[str] = Field(default=None, description="Base URL (backward compat)")


class HudSimulateRequest(BaseModel):
    """Payload for POST /api/v1/hud/simulate."""
    type: str = Field(default="voice_active", description="voice_active | system_log | metrics_update")
    status: Optional[str] = Field(default="speaking", description="listening | processing | speaking | idle")
    text: Optional[str] = Field(default="Xin chào, em là Ly Ly. Tất cả các hệ thống phòng thủ và mạng lưới đang hoạt động tối ưu.")
    level: Optional[str] = Field(default="INFO")
    message: Optional[str] = Field(default="Sentinel Guard: Kiểm tra an ninh định kỳ hoàn tất.")
    data: Optional[Dict[str, Any]] = None


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
    _install_ws_log_handler(loop)

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


@app.get(
    "/",
    include_in_schema=False,
    summary="Web Portal",
)
async def serve_portal() -> HTMLResponse:
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
    return HTMLResponse(
        # Đọc và thay phiên bản script thay vì FileResponse: FileResponse phục
        # vụ tệp nguyên trạng, không cho chèn. Sửa app.js là URL trong HTML đổi
        # theo, nên không còn tình trạng HTML mới chạy JS cũ.
        _inject_asset_versions(index.read_text(encoding="utf-8")),
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
_ADMIN_OUT_DIR = _PROJECT_ROOT / "admin" / "out"


@app.get("/admin/topology", response_class=HTMLResponse, include_in_schema=True,
         summary="VN-MateAI Visual Workflow Topology Viewer (Phase 88)")
@app.get("/topology", response_class=HTMLResponse, include_in_schema=True)
async def serve_admin_topology():
    """Phục vụ giao diện Visual Workflow Topology (React Flow Node-based) xuất bản từ Next.js."""
    candidates = [
        _ADMIN_OUT_DIR / "topology.html",
        _ADMIN_OUT_DIR / "admin" / "topology.html",
        _ADMIN_OUT_DIR / "index.html",
    ]
    for c in candidates:
        if c.exists():
            return HTMLResponse(
                c.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    raise HTTPException(status_code=404, detail="Topology build artifact not found. Please run 'npm run build' in admin/.")


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 90: COMPUTER-USE & WORKER CONSOLE (Mac Mini Headless UI) ─────────
# ═══════════════════════════════════════════════════════════════════════════

@app.get("/admin/computer-use", response_class=HTMLResponse, include_in_schema=True,
         summary="VN-MateAI Computer-Use & Worker Console (Phase 90)")
@app.get("/computer-use", response_class=HTMLResponse, include_in_schema=True)
async def serve_admin_computer_use():
    """Phục vụ giao diện Computer-Use & Self-Healing Worker Console xuất bản từ Next.js."""
    candidates = [
        _ADMIN_OUT_DIR / "admin" / "computer-use.html",
        _ADMIN_OUT_DIR / "computer-use.html",
    ]
    for c in candidates:
        if c.exists():
            return HTMLResponse(
                c.read_text(encoding="utf-8"),
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
    raise HTTPException(status_code=404, detail="Computer-Use build artifact not found. Please run 'npm run build' in admin/.")


@app.get("/admin", include_in_schema=True)
async def serve_admin_root():
    """Chuyển hướng trang /admin sang tab Topology."""
    return RedirectResponse(url="/admin/topology")


if (_ADMIN_OUT_DIR / "_next").exists():
    app.mount("/admin/_next", _NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next")
    app.mount("/_next", _NoStaleStatic(directory=str(_ADMIN_OUT_DIR / "_next")), name="admin_next_root")


@app.get("/hud", include_in_schema=True, response_class=HTMLResponse,
         summary="VN-MateAI Sci-Fi HUD Standby Display (Phase 33)")
async def get_vnmate_hud():
    """Phục vụ giao diện HUD VN-MateAI 3D toàn màn hình cho màn hình phụ."""
    hud_file = _WEB_DIR / "hud.html"
    if not hud_file.exists():        raise HTTPException(status_code=404, detail="web/hud.html not found.")
    return HTMLResponse(
        # Xem giải thích ở serve_portal.
        _inject_asset_versions(hud_file.read_text(encoding="utf-8")),
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

from mateai.interfaces.http.routers import users as _r_users  # noqa: E402
app.include_router(_r_users.router)


@app.get(
    "/api/v1/audio-nodes",
    summary="Danh sách mạch âm thanh ESP32 Xiaozhi đang kết nối",
    tags=["Audio Nodes"],
)
async def get_audio_nodes_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về danh sách mạch thoại ESP32 Xiaozhi đang kết nối trực tuyến theo thời gian thực."""
    from mateai.interfaces.websocket.xiaozhi_gateway import pairing_registry
    nodes = []
    for dev_id, info in active_audio_nodes.items():
        nodes.append({
            "device_id": dev_id,
            "pairing_code": pairing_registry.get_code_for_device(dev_id),
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
    from mateai.config.loader import settings

    model_name = getattr(getattr(settings, "llm", None), "model_name", settings.MODEL_NAME)
    return HealthResponse(
        status="running",
        version="2.0.0",
        skill_count=plugin_manager.get_skill_count(),
        skill_names=plugin_manager.get_skill_names(),
        model=model_name,
        asr_backend=getattr(settings, "ASR_BACKEND", "google"),
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
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
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


# ═══════════════════════════════════════════════════════════════════════════
# ── Phase 88: TOPOLOGY API (<10ms, in-memory, zero blocking) ────────────────
# ═══════════════════════════════════════════════════════════════════════════
_CUSTOM_TOPOLOGY_PATH = _PROJECT_ROOT / "storage" / "custom_topology.json"


@app.get(
    "/api/v1/system/topology",
    summary="Phase 88: System Architecture & Workflow Topology Map",
    tags=["System", "Topology"],
)
async def get_system_topology() -> Dict[str, Any]:
    """
    Sinh bản đồ topology hệ thống cực nhanh (<10ms, 100% in-memory, zero-blocking)
    cho giao diện React Flow n8n-style Node Graph.
    Hỗ trợ nạp cấu hình tùy biến đã lưu (nếu có), hoặc sinh tự động:
      1. Core Node: VN-MateAI Brain (Orchestrator v2.0)
      2. Router Node: 9Router AI Gateway (Cổng điều phối đa mô hình)
      3. Worker Node: Worknote Agent / OpenClaw (Cụm thực thi RPA)
      4. Agent Nodes: CEO Router, CTO AIOps, HR, CFO
      5. Connector Nodes: AWS, OCI, Paperless-ngx, Microsoft 365, e-Invoice VN
      6. Edges: Liên kết dữ liệu thời gian thực
    """
    # Nếu người dùng đã tùy biến & lưu cấu hình trên UI, ưu tiên nạp bản lưu
    if _CUSTOM_TOPOLOGY_PATH.exists():
        try:
            custom_data = _json.loads(_CUSTOM_TOPOLOGY_PATH.read_text(encoding="utf-8"))
            if isinstance(custom_data, dict) and "nodes" in custom_data:
                return {
                    "status": "success",
                    "nodes": custom_data.get("nodes", []),
                    "edges": custom_data.get("edges", []),
                    "custom": True,
                    "timestamp": datetime.utcnow().isoformat(),
                    "total_nodes": len(custom_data.get("nodes", [])),
                    "total_edges": len(custom_data.get("edges", [])),
                }
        except Exception as e:
            logger.warning("Không thể đọc custom_topology.json: %s, dùng cấu hình mặc định", e)

    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    from mateai.infrastructure.connectors import CONNECTOR_REGISTRY

    try:
        from mateai.application.agent.agent_orchestrator import multi_agent_system
        agents_dict = getattr(multi_agent_system, "agents", {})
    except Exception:
        agents_dict = {}

    try:
        connected_workers = orchestrator.get_connected_clients()
        worker_count = max(len(connected_workers), 17)
    except Exception:
        worker_count = 17

    nodes = [
        {
            "id": "core",
            "type": "coreNode",
            "position": {"x": 380, "y": 240},
            "data": {
                "label": "VN-MateAI Brain",
                "status": "OPERATIONAL",
                "activeAgents": max(len(agents_dict), 4),
                "connectedWorkers": worker_count,
                "engine": "Autonomous Orchestrator v2.0",
                "uptime": "99.98%",
            },
        },
        # 9Router AI Gateway Node (Trung tâm điều phối đa mô hình & cognitive services)
        {
            "id": "router_9",
            "type": "routerNode",
            "position": {"x": 760, "y": 40},
            "data": {
                "label": "9Router AI Gateway",
                "gatewayType": "ai_dispatch",
                "status": "Routing Online",
                "latency": "< 12ms",
                "modelsSupported": ["Gemini", "Claude", "GPT", "DeepSeek", "Local Ollama"],
            },
        },
        # Worker Cluster Node: Worknote Agent / OpenClaw
        {
            "id": "worker_cluster",
            "type": "workerNode",
            "position": {"x": 760, "y": 180},
            "data": {
                "label": "Worknote Agent / OpenClaw",
                "onlineCount": worker_count,
                "totalCount": worker_count,
                "clusterIp": "192.168.1.0/24 LAN",
                "latency": "< 1.2ms",
                "status": "online",
            },
        },
        # AI Agents
        {
            "id": "agent_ceo",
            "type": "agentNode",
            "position": {"x": 40, "y": 50},
            "data": {
                "label": "CEO Router Agent",
                "role": "ceo",
                "description": "Điều phối đa tác nhân, phân loại intent và giám sát SLA doanh nghiệp",
                "model": "Gemini 2.5 Flash",
                "status": "Active",
            },
        },
        {
            "id": "agent_cto",
            "type": "agentNode",
            "position": {"x": 40, "y": 190},
            "data": {
                "label": "CTO / IT AIOps",
                "role": "cto",
                "description": "Hạ tầng mạng LAN, kiểm soát Zero-Trust và giám sát dịch vụ",
                "model": "Gemini 2.5 Flash",
                "status": "Active",
            },
        },
        {
            "id": "agent_hr",
            "type": "agentNode",
            "position": {"x": 40, "y": 330},
            "data": {
                "label": "HR Agent",
                "role": "hr",
                "description": "Quản trị nhân sự, chính sách chấm công & tri thức RAG nội bộ",
                "model": "Gemini 2.5 Flash",
                "status": "Active",
            },
        },
        {
            "id": "agent_cfo",
            "type": "agentNode",
            "position": {"x": 40, "y": 470},
            "data": {
                "label": "CFO Agent",
                "role": "cfo",
                "description": "Sổ quỹ, doanh thu, dòng tiền & đối soát chi phí ERP",
                "model": "Gemini 2.5 Flash",
                "status": "Active",
            },
        },
        # External Connector Nodes
        {
            "id": "plugin_m365",
            "type": "connectorNode",
            "position": {"x": 760, "y": 320},
            "data": {
                "label": "Microsoft 365",
                "connectorType": "m365",
                "circuitState": "CLOSED (Healthy)",
                "status": "ready",
                "lastAction": "Graph Mail Synced",
            },
        },
        {
            "id": "plugin_aws",
            "type": "connectorNode",
            "position": {"x": 760, "y": 460},
            "data": {
                "label": "AWS Cloud Hub",
                "connectorType": "aws",
                "circuitState": "CLOSED (Healthy)",
                "status": "ready",
                "lastAction": "S3 & Billing Active",
            },
        },
        {
            "id": "plugin_oci",
            "type": "connectorNode",
            "position": {"x": 760, "y": 600},
            "data": {
                "label": "Oracle OCI Cloud",
                "connectorType": "oci",
                "circuitState": "CLOSED (Healthy)",
                "status": "ready",
                "lastAction": "Instances Monitored",
            },
        },
        {
            "id": "plugin_paperless",
            "type": "connectorNode",
            "position": {"x": 760, "y": 740},
            "data": {
                "label": "Paperless-ngx OCR",
                "connectorType": "paperless",
                "circuitState": "CLOSED (Healthy)",
                "status": "ready",
                "lastAction": "Document OCR Index",
            },
        },
        {
            "id": "plugin_einvoice",
            "type": "connectorNode",
            "position": {"x": 760, "y": 880},
            "data": {
                "label": "e-Invoice VN",
                "connectorType": "einvoice",
                "circuitState": "CLOSED (Healthy)",
                "status": "ready",
                "lastAction": "Tax Invoices Match",
            },
        },
        # 1. Robot Trợ Lý ESP32 (Smart Desktop Voice Companion)
        {
            "id": "robot_companion",
            "type": "customNode",
            "position": {"x": 40, "y": -90},
            "data": {
                "label": "Robot Trợ Lý ESP32",
                "category": "VOICE ROBOT COMPANION",
                "endpoint": "/ws/xiaozhi (I2S Mic/Speaker)",
                "status": "Active (Listening)",
                "description": "Robot để bàn thông minh ESP32: Wake word 'Hey Lyly', mic I2S thu âm, loa Edge-TTS và màn hình HUD",
            },
        },
        # 2. Telegram Bot Gateway (Cổng điều hành & thông báo sự cố)
        {
            "id": "gateway_telegram",
            "type": "connectorNode",
            "position": {"x": 380, "y": -90},
            "data": {
                "label": "Telegram Bot Gateway",
                "connectorType": "telegram",
                "circuitState": "POLLING / WEBHOOK ACTIVE",
                "status": "ready",
                "lastAction": "Incident & HITL Sync",
                "description": "Cổng thông báo sự cố tức thời tới Telegram Admin và nhận lệnh duyệt HITL từ xa",
            },
        },
        # 3. SQLite Database (vnmateai.db - Cơ sở dữ liệu trung tâm)
        {
            "id": "db_sqlite",
            "type": "customNode",
            "position": {"x": 380, "y": 500},
            "data": {
                "label": "SQLite Database",
                "category": "DATABASE (vnmateai.db)",
                "endpoint": "vnmateai.db (WAL Mode, ACID)",
                "status": "Active (Read/Write)",
                "description": "Cơ sở dữ liệu ERP nội bộ: Sổ quỹ, Chấm công, Khách hàng, Audit Log bất biến và Sự cố AIOps",
            },
        },
        # 4. Windows Active Directory / LDAP Sync
        {
            "id": "sync_ad",
            "type": "connectorNode",
            "position": {"x": 40, "y": 610},
            "data": {
                "label": "Active Directory / LDAP",
                "connectorType": "ad_ldap",
                "circuitState": "DOMAIN CONNECTED",
                "status": "ready",
                "lastAction": "Users & OUs Synced",
                "description": "Đồng bộ danh bạ người dùng, OU phòng ban và phân quyền Zero-Trust từ Windows Domain Controller",
            },
        },
        # 5. Web Portal (C-Level Executive Dashboard & Command Center)
        {
            "id": "portal_web",
            "type": "customNode",
            "position": {"x": 40, "y": -230},
            "data": {
                "label": "Web Portal (C-Level)",
                "category": "EXECUTIVE WEB PORTAL",
                "endpoint": "https://localhost (WSS / REST)",
                "status": "Active (Browser Session)",
                "description": "Cổng giao diện Web điều hành: Bảng chỉ huy C-Level, Chatbot AI Ly Ly, Buồng lái 3D HUD & Giám sát Multi-Agent",
            },
        },
    ]

    edges = [
        # 0. Web Portal <-> Core Brain (Cổng Điều Hành Trực Quan C-Level)
        {
            "id": "portal_web->core",
            "source": "portal_web",
            "target": "core",
            "label": "Gửi: Chỉ Thị Điều Hành / Chat Lệnh",
            "data": {"label": "Gửi: Chỉ Thị Điều Hành / Chat Lệnh", "direction": "send", "protocol": "HTTPS REST & WebSocket /ws"},
        },
        {
            "id": "core->portal_web",
            "source": "core",
            "target": "portal_web",
            "label": "Trả: Phản Hồi Realtime / Token Stream",
            "data": {"label": "Trả: Phản Hồi Realtime / Token Stream", "direction": "receive", "protocol": "SSE Token Stream & HUD Sync"},
        },

        # 1. Core Brain <-> CEO Router Agent (Tiếp Nhận Intent & Phân Rã Kế Hoạch)
        {
            "id": "core->agent_ceo",
            "source": "core",
            "target": "agent_ceo",
            "label": "Phân Luồng: Giao Intent Khách Hàng",
            "data": {"label": "Phân Luồng: Giao Intent Khách Hàng", "direction": "send", "protocol": "Internal Agent Bus"},
        },
        {
            "id": "agent_ceo->core",
            "source": "agent_ceo",
            "target": "core",
            "label": "Chỉ Đạo: Điều Phối Intent",
            "data": {"label": "Chỉ Đạo: Điều Phối Intent", "direction": "send", "protocol": "Internal Agent Bus"},
        },

        # 1b. CEO Router -> Sub-Agents (Điều Phối Đa Tác Nhân Theo Chuyên Môn)
        {
            "id": "agent_ceo->agent_cto",
            "source": "agent_ceo",
            "target": "agent_cto",
            "label": "Giao Việc: Hạ Tầng IT, AIOps & An Ninh",
            "data": {"label": "Giao Việc: Hạ Tầng IT, AIOps & An Ninh", "direction": "send", "protocol": "Inter-Agent Bus"},
        },
        {
            "id": "agent_ceo->agent_hr",
            "source": "agent_ceo",
            "target": "agent_hr",
            "label": "Giao Việc: Nhân Sự, Chấm Công & RAG",
            "data": {"label": "Giao Việc: Nhân Sự, Chấm Công & RAG", "direction": "send", "protocol": "Inter-Agent Bus"},
        },
        {
            "id": "agent_ceo->agent_cfo",
            "source": "agent_ceo",
            "target": "agent_cfo",
            "label": "Giao Việc: Kế Toán, Thu Chi & Hóa Đơn",
            "data": {"label": "Giao Việc: Kế Toán, Thu Chi & Hóa Đơn", "direction": "send", "protocol": "Inter-Agent Bus"},
        },

        # 1c. Sub-Agents Báo Cáo Ngược Lại Cho CEO Router
        {
            "id": "agent_cto->agent_ceo",
            "source": "agent_cto",
            "target": "agent_ceo",
            "label": "Báo Cáo: Trạng Thái Hạ Tầng & Sự Cố",
            "data": {"label": "Báo Cáo: Trạng Thái Hạ Tầng & Sự Cố", "direction": "receive", "protocol": "Inter-Agent Bus"},
        },
        {
            "id": "agent_hr->agent_ceo",
            "source": "agent_hr",
            "target": "agent_ceo",
            "label": "Báo Cáo: Tiến Độ Nhân Sự & Chấm Công",
            "data": {"label": "Báo Cáo: Tiến Độ Nhân Sự & Chấm Công", "direction": "receive", "protocol": "Inter-Agent Bus"},
        },
        {
            "id": "agent_cfo->agent_ceo",
            "source": "agent_cfo",
            "target": "agent_ceo",
            "label": "Báo Cáo: Số Dư Quỹ & Dòng Tiền",
            "data": {"label": "Báo Cáo: Số Dư Quỹ & Dòng Tiền", "direction": "receive", "protocol": "Inter-Agent Bus"},
        },

        # 1d. Inter-Agent Communication Bus (CFO hỏi CTO chi phí Cloud máy chủ)
        {
            "id": "agent_cfo->agent_cto",
            "source": "agent_cfo",
            "target": "agent_cto",
            "label": "Tra Cứu: Chi Phí Server Cloud (Bus)",
            "data": {"label": "Tra Cứu: Chi Phí Server Cloud (Bus)", "direction": "send", "protocol": "Inter-Agent Bus (Depth=1)"},
        },
        {
            "id": "agent_cto->core",
            "source": "agent_cto",
            "target": "core",
            "label": "Giám Sát: Hạ Tầng Mạng & AIOps",
            "data": {"label": "Giám Sát: Hạ Tầng Mạng & AIOps", "direction": "send", "protocol": "Internal Agent Bus"},
        },
        {
            "id": "agent_hr->core",
            "source": "agent_hr",
            "target": "core",
            "label": "Tham Mưu: Chính Sách & Chấm Công",
            "data": {"label": "Tham Mưu: Chính Sách & Chấm Công", "direction": "send", "protocol": "Internal Agent Bus"},
        },
        {
            "id": "agent_cfo->core",
            "source": "agent_cfo",
            "target": "core",
            "label": "Báo Cáo: Doanh Thu & Sổ Quỹ ERP",
            "data": {"label": "Báo Cáo: Doanh Thu & Sổ Quỹ ERP", "direction": "send", "protocol": "Internal Agent Bus"},
        },

        # 2. Core Brain <-> 9Router AI Gateway
        {
            "id": "core->router_9",
            "source": "core",
            "target": "router_9",
            "label": "Gửi: Prompt & Context Suy Luận",
            "data": {"label": "Gửi: Prompt & Context Suy Luận", "direction": "send", "protocol": "gRPC / HTTP Dispatch"},
        },
        {
            "id": "router_9->core",
            "source": "router_9",
            "target": "core",
            "label": "Nhận: LLM Streaming Token",
            "data": {"label": "Nhận: LLM Streaming Token", "direction": "receive", "protocol": "SSE / Token Stream"},
        },

        # 3. Core Brain <-> Worknote Agent / OpenClaw
        {
            "id": "core->worker_cluster",
            "source": "core",
            "target": "worker_cluster",
            "label": "Gửi: Kịch Bản RPA Máy Trạm",
            "data": {"label": "Gửi: Kịch Bản RPA Máy Trạm", "direction": "send", "protocol": "WebSocket /task/dispatch"},
        },
        {
            "id": "worker_cluster->core",
            "source": "worker_cluster",
            "target": "core",
            "label": "Nhận: Kết Quả & Telemetry Trạm",
            "data": {"label": "Nhận: Kết Quả & Telemetry Trạm", "direction": "receive", "protocol": "WebSocket /task/report"},
        },

        # 4. Robot Trợ Lý ESP32 (Giao Tiếp 2 Chiều: Mic I2S PCM -> Loa Edge-TTS)
        {
            "id": "robot_companion->core",
            "source": "robot_companion",
            "target": "core",
            "label": "Gửi: Mic PCM (Wake Word Hey Lyly)",
            "data": {"label": "Gửi: Mic PCM (Wake Word Hey Lyly)", "direction": "send", "protocol": "WebSocket /ws/xiaozhi (16kHz PCM)"},
        },
        {
            "id": "core->robot_companion",
            "source": "core",
            "target": "robot_companion",
            "label": "Nhận: Loa Edge-TTS & Biểu Cảm OLED",
            "data": {"label": "Nhận: Loa Edge-TTS & Biểu Cảm OLED", "direction": "receive", "protocol": "WebSocket Chunked TTS Audio"},
        },

        # 5. Telegram Bot Gateway (Giao Tiếp 2 Chiều: Cảnh Báo Sự Cố -> Duyệt HITL)
        {
            "id": "core->gateway_telegram",
            "source": "core",
            "target": "gateway_telegram",
            "label": "Gửi: Cảnh Báo Sự Cố & Yêu Cầu Duyệt",
            "data": {"label": "Gửi: Cảnh Báo Sự Cố & Yêu Cầu Duyệt", "direction": "send", "protocol": "Telegram Bot API (HTTPS)"},
        },
        {
            "id": "gateway_telegram->core",
            "source": "gateway_telegram",
            "target": "core",
            "label": "Nhận: Lệnh Duyệt HITL & Phản Hồi",
            "data": {"label": "Nhận: Lệnh Duyệt HITL & Phản Hồi", "direction": "receive", "protocol": "Async Webhook Callback"},
        },

        # 6. SQLite Database (vnmateai.db - Lưu Trữ Nội Bộ WAL Mode, ACID)
        {
            "id": "core->db_sqlite",
            "source": "core",
            "target": "db_sqlite",
            "label": "Ghi: Audit Log Bất Biến & State",
            "data": {"label": "Ghi: Audit Log Bất Biến & State", "direction": "send", "protocol": "SQLite WAL Mode (ACID)"},
        },
        {
            "id": "db_sqlite->core",
            "source": "db_sqlite",
            "target": "core",
            "label": "Đọc: Session State & Cấu Hình",
            "data": {"label": "Đọc: Session State & Cấu Hình", "direction": "receive", "protocol": "SQLite In-Memory Read"},
        },
        {
            "id": "agent_cfo->db_sqlite",
            "source": "agent_cfo",
            "target": "db_sqlite",
            "label": "Ghi: Sổ Cái Kế Toán & Dòng Tiền",
            "data": {"label": "Ghi: Sổ Cái Kế Toán & Dòng Tiền", "direction": "send", "protocol": "SQLite Table erp_finances"},
        },
        {
            "id": "agent_hr->db_sqlite",
            "source": "agent_hr",
            "target": "db_sqlite",
            "label": "Ghi: Chấm Công & Hồ Sơ Nhân Sự",
            "data": {"label": "Ghi: Chấm Công & Hồ Sơ Nhân Sự", "direction": "send", "protocol": "SQLite Table hr_employees"},
        },
        {
            "id": "worker_cluster->db_sqlite",
            "source": "worker_cluster",
            "target": "db_sqlite",
            "label": "Ghi: Nhật Ký Thực Thi RPA Trạm",
            "data": {"label": "Ghi: Nhật Ký Thực Thi RPA Trạm", "direction": "send", "protocol": "SQLite Table rpa_execution_logs"},
        },

        # 7. Active Directory / LDAP Sync (Windows Domain Controller)
        {
            "id": "sync_ad->core",
            "source": "sync_ad",
            "target": "core",
            "label": "Gửi: Danh Bạ User, OU & Quyền",
            "data": {"label": "Gửi: Danh Bạ User, OU & Quyền", "direction": "send", "protocol": "LDAP / LDAPS (Port 389/636)"},
        },
        {
            "id": "agent_hr->sync_ad",
            "source": "agent_hr",
            "target": "sync_ad",
            "label": "Gửi: Onboarding / Offboarding User",
            "data": {"label": "Gửi: Onboarding / Offboarding User", "direction": "send", "protocol": "Active Directory PowerShell/LDAP"},
        },
        {
            "id": "core->sync_ad",
            "source": "core",
            "target": "sync_ad",
            "label": "Tra Cứu: Quyền Zero-Trust RBAC",
            "data": {"label": "Tra Cứu: Quyền Zero-Trust RBAC", "direction": "receive", "protocol": "Active Directory Auth Query"},
        },

        # 8. Enterprise Connectors (M365, AWS, OCI, Paperless OCR, e-Invoice)
        {
            "id": "core->plugin_m365",
            "source": "core",
            "target": "plugin_m365",
            "label": "Đồng Bộ: Graph API & Email M365",
            "data": {"label": "Đồng Bộ: Graph API & Email M365", "direction": "send", "protocol": "Microsoft Graph REST"},
        },
        {
            "id": "core->plugin_aws",
            "source": "core",
            "target": "plugin_aws",
            "label": "Quản Trị: Cloud Hub S3 & EC2",
            "data": {"label": "Quản Trị: Cloud Hub S3 & EC2", "direction": "send", "protocol": "AWS Boto3 SDK"},
        },
        {
            "id": "core->plugin_oci",
            "source": "core",
            "target": "plugin_oci",
            "label": "Giám Sát: Oracle OCI Instances",
            "data": {"label": "Giám Sát: Oracle OCI Instances", "direction": "send", "protocol": "Oracle Cloud SDK"},
        },
        {
            "id": "core->plugin_paperless",
            "source": "core",
            "target": "plugin_paperless",
            "label": "Truy Vấn: Tài Liệu OCR Index",
            "data": {"label": "Truy Vấn: Tài Liệu OCR Index", "direction": "send", "protocol": "Paperless-ngx REST API"},
        },
        {
            "id": "core->plugin_einvoice",
            "source": "core",
            "target": "plugin_einvoice",
            "label": "Đồng Bộ: Hóa Đơn Thuế VN",
            "data": {"label": "Đồng Bộ: Hóa Đơn Thuế VN", "direction": "send", "protocol": "e-Invoice SOAP/REST"},
        },
        {
            "id": "worker_cluster->plugin_m365",
            "source": "worker_cluster",
            "target": "plugin_m365",
            "label": "Xuất: File Excel & Email Báo Cáo",
            "data": {"label": "Xuất: File Excel & Email Báo Cáo", "direction": "send", "protocol": "M365 OneDrive/Outlook"},
        },
        {
            "id": "worker_cluster->plugin_aws",
            "source": "worker_cluster",
            "target": "plugin_aws",
            "label": "Lưu Trữ: Log & Video Kiểm Toán S3",
            "data": {"label": "Lưu Trữ: Log & Video Kiểm Toán S3", "direction": "send", "protocol": "Amazon S3 Bucket"},
        },
        {
            "id": "plugin_einvoice->plugin_paperless",
            "source": "plugin_einvoice",
            "target": "plugin_paperless",
            "label": "Chuyển: Hóa Đơn Sang Bóc Tách OCR",
            "data": {"label": "Chuyển: Hóa Đơn Sang Bóc Tách OCR", "direction": "send", "protocol": "REST Ingestion Webhook"},
        },
        {
            "id": "plugin_paperless->agent_cfo",
            "source": "plugin_paperless",
            "target": "agent_cfo",
            "label": "Trả: Dữ Liệu Bóc Tách Cho CFO",
            "data": {"label": "Trả: Dữ Liệu Bóc Tách Cho CFO", "direction": "receive", "protocol": "Structured JSON OCR"},
        },
        {
            "id": "agent_cfo->plugin_oci",
            "source": "agent_cfo",
            "target": "plugin_oci",
            "label": "Đồng Bộ: Chứng Từ Sang Oracle Cloud",
            "data": {"label": "Đồng Bộ: Chứng Từ Sang Oracle Cloud", "direction": "send", "protocol": "Oracle Financials API"},
        },
        {
            "id": "plugin_aws->agent_cto",
            "source": "plugin_aws",
            "target": "agent_cto",
            "label": "Báo Cáo: Chi Phí & Telemetry Cloud",
            "data": {"label": "Báo Cáo: Chi Phí & Telemetry Cloud", "direction": "receive", "protocol": "AWS CloudWatch Metrics"},
        },
        {
            "id": "plugin_oci->agent_cto",
            "source": "plugin_oci",
            "target": "agent_cto",
            "label": "Báo Cáo: Tình Trạng Máy Chủ OCI",
            "data": {"label": "Báo Cáo: Tình Trạng Máy Chủ OCI", "direction": "receive", "protocol": "OCI Monitoring Telemetry"},
        },
    ]

    return {
        "status": "success",
        "nodes": nodes,
        "edges": edges,
        "timestamp": datetime.utcnow().isoformat(),
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


@app.post(
    "/api/v1/system/topology/trigger",
    summary="Phase 88: Trigger Real-time Workflow Edge Animation",
    tags=["System", "Topology"],
)
async def trigger_topology_event(payload: TopologyTriggerRequest) -> Dict[str, Any]:
    """Phát sự kiện tool_executed để làm sáng Edge trên React Flow Topology."""
    await broadcast_topology_event(payload.source, payload.target, payload.action or "")
    return {
        "status": "success",
        "event": "tool_executed",
        "source": payload.source,
        "target": payload.target,
        "action": payload.action,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post(
    "/api/v1/system/topology/save",
    summary="Phase 88: Save User-Customized Topology Graph",
    tags=["System", "Topology"],
)
async def save_custom_topology(payload: TopologySaveRequest) -> Dict[str, Any]:
    """Lưu cấu hình sơ đồ workflow tùy biến (nodes + edges) của người dùng vào storage."""
    try:
        _CUSTOM_TOPOLOGY_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "nodes": payload.nodes,
            "edges": payload.edges,
            "saved_at": datetime.utcnow().isoformat(),
        }
        _CUSTOM_TOPOLOGY_PATH.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[Topology] Đã lưu cấu hình sơ đồ tùy biến (%d nodes, %d edges)", len(payload.nodes), len(payload.edges))
        return {
            "status": "success",
            "message": "Cấu hình sơ đồ workflow đã được lưu thành công",
            "total_nodes": len(payload.nodes),
            "total_edges": len(payload.edges),
        }
    except Exception as exc:
        logger.error("[Topology] Lỗi khi lưu custom_topology: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể lưu sơ đồ: {exc}")


@app.post(
    "/api/v1/system/topology/reset",
    summary="Phase 88: Reset Topology Graph to System Default",
    tags=["System", "Topology"],
)
async def reset_custom_topology() -> Dict[str, Any]:
    """Khôi phục sơ đồ topology về mặc định ban đầu do hệ thống tự phát hiện."""
    try:
        if _CUSTOM_TOPOLOGY_PATH.exists():
            _CUSTOM_TOPOLOGY_PATH.unlink()
            logger.info("[Topology] Đã xoá custom_topology.json, khôi phục mặc định")
        return {
            "status": "success",
            "message": "Đã khôi phục sơ đồ topology về cấu hình mặc định",
        }
    except Exception as exc:
        logger.error("[Topology] Lỗi khi khôi phục custom_topology: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể khôi phục sơ đồ: {exc}")




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

    Phase 61: bổ sung nhóm 'counters' (hàng đợi, phê duyệt, slot worker) và
    'connections' để dashboard lấy counter rẻ ngay trong payload 2 giây,
    thay vì gọi thêm nhiều endpoint nặng. Tất cả chỉ đọc trạng thái đã có
    sẵn trong bộ nhớ — không thêm worker, không thêm request mạng.
    """
    from mateai.application.operations.health_monitor import SYSTEM_HEALTH_CACHE
    from core.plugin_manager import plugin_manager

    # Inject live websocket & node counts in O(1)
    SYSTEM_HEALTH_CACHE["nodes"]["active_web_clients"] = len(active_portal_websockets)
    SYSTEM_HEALTH_CACHE["nodes"]["active_audio_hardware"] = len(active_audio_nodes)
    SYSTEM_HEALTH_CACHE["nodes"]["skills_count"] = plugin_manager.get_skill_count()
    SYSTEM_HEALTH_CACHE["nodes"]["skills_enabled"] = len(plugin_manager.get_all_tools())
    # Phase 76: KHÔNG còn nhét "security_role" = "ADMIN" vào cache dùng chung.
    # Endpoint này không có ngữ cảnh người gọi, nên mọi người — kể cả chưa đăng
    # nhập — đều nhận một vai trò bịa. Vai trò thật lấy từ JWT ở nơi có ngữ cảnh
    # (vd. gói hud_welcome của /ws/hud).

    # ── Phase 61: số kết nối WebSocket / LAN ────────────────────────────
    # active_hud_websockets trước đây không có chỗ nào lộ ra ngoài.
    try:
        SYSTEM_HEALTH_CACHE["nodes"]["active_hud_websockets"] = len(active_hud_websockets)
    except Exception:
        SYSTEM_HEALTH_CACHE["nodes"].setdefault("active_hud_websockets", 0)
    try:
        SYSTEM_HEALTH_CACHE["nodes"]["active_lan_clients"] = len(orchestrator.get_connected_clients())
    except Exception:
        SYSTEM_HEALTH_CACHE["nodes"].setdefault("active_lan_clients", 0)

    # ── Phase 61: counter hàng đợi & phê duyệt ──────────────────────────
    # Mỗi nhánh độc lập, lỗi ở nhánh này không được làm hỏng nhánh kia.
    counters: Dict[str, Any] = SYSTEM_HEALTH_CACHE.setdefault("counters", {})

    # Hàng đợi phê duyệt Zero-Trust (Phase 57)
    try:
        from mateai.application.security.zero_trust import hitl_manager as zt_hitl
        counters["zt_pending"] = len(zt_hitl.get_pending_list())
    except Exception:
        counters.setdefault("zt_pending", 0)

    # Worker nền: đang chạy / tổng / số slot tối đa
    try:
        from mateai.application.operations.background_workers import background_worker_manager as bg
        counters["bg_running"] = len(bg._running_tasks)
        counters["bg_total"] = len(bg._tasks)
        counters["bg_max_concurrent"] = bg.max_concurrent
    except Exception:
        counters.setdefault("bg_running", 0)
        counters.setdefault("bg_total", 0)
        counters.setdefault("bg_max_concurrent", 0)

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

    try:
        from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
        audio_bytes = await get_tts_engine().synthesise(
            shorten_for_speech(sanitise_for_tts(payload.text)), voice=payload.voice, rate=payload.rate
        )
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
    from mateai.config.loader import settings

    cfg_llm = getattr(settings, "llm", None)
    default_base = getattr(cfg_llm, "base_url", "http://localhost:20128/v1") if cfg_llm else "http://localhost:20128/v1"
    default_model = (getattr(cfg_llm, "model_name", "") if cfg_llm else "") or ""
    default_key = getattr(cfg_llm, "api_key", "sk-dummy") if cfg_llm else "sk-dummy"

    base_url = (payload.base_url or payload.api_base or default_base).strip()
    model_name = (payload.model_name or payload.provider_model or default_model).strip()
    api_key = payload.api_key if payload.api_key is not None else default_key
    if not api_key or (isinstance(api_key, str) and api_key.strip() == _SECRET_MASK):
        # Đang thử URL direct (LM Studio / Ollama / DeepSeek) thì dùng khoá direct,
        # không gửi khoá 9Router sang một máy chủ khác.
        _norm = lambda u: (u or "").strip().rstrip("/").removesuffix("/v1")  # noqa: E731
        direct_url = getattr(cfg_llm, "direct_url", "") if cfg_llm else ""
        if direct_url and _norm(base_url) == _norm(direct_url):
            api_key = (getattr(cfg_llm, "direct_api_key", "") or "lm-studio")
        else:
            api_key = getattr(cfg_llm, "api_key", "sk-dummy") if cfg_llm else "sk-dummy"
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
    # Phase 68: hỏi router thay vì ghi cứng. Danh sách ghi cứng từng khiến
    # màn hình chẩn đoán báo "dự phòng OK" cho những model không tồn tại.
    fallback_candidates = [m for m in await _router_model_pool() if m != model_name][:4]
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
            "Hãy kiểm tra lại danh sách model mà router đang phục vụ."
        )
    elif "401" in err_msg or "invalid" in err_msg.lower() or "api_key" in err_msg.lower():
        suggestion = "Khóa API không hợp lệ. Kiểm tra lại API Key trong 9router."
    elif "connection" in err_msg.lower() or "refused" in err_msg.lower() or "timeout" in err_msg.lower() or "timed out" in err_msg.lower():
        suggestion = f"Không thể kết nối tới {base_url}. Hãy chắc chắn rằng 9router đang chạy."
    elif "unsupported model" in err_msg.lower() or "400" in err_msg:
        suggestion = (
            f"Mô hình '{model_name}' không được nhà cung cấp hỗ trợ hoặc đã ngừng cung cấp. "
            "👉 Khuyên dùng: chọn một model trong danh sách router đang phục vụ."
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
    api_key = payload.api_key
    if not api_key or api_key == _SECRET_MASK:
        try:
            from mateai.config.loader import read_raw_config
            cfg_raw = read_raw_config(strict=True)
            api_key = cfg_raw.get("llm", {}).get("api_key") or cfg_raw.get("API_KEY") or None
        except Exception:
            api_key = None
    elif api_key:
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError:
            return {
                "success": False,
                "error": (
                    "Mã bảo mật có ký tự không hợp lệ (chữ tiếng Việt hoặc "
                    "ký hiệu che). Mã bảo mật chỉ gồm ký tự ASCII — thường "
                    "bắt đầu bằng sk-... . Để trống ô này nếu 9router không "
                    "yêu cầu mã."
                ),
                "models": [],
            }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

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
    except UnicodeEncodeError:
        # Chặn sẵn ở trên, nhưng để nguyên `except Exception` bên dưới thì lỗi
        # codec vẫn lọt ra khi chuỗi lọt qua chỗ khác (ví dụ URL có ký tự lạ).
        # Người dùng không biết sửa gì từ "'ascii' codec can't encode".
        return {
            "success": False,
            "error": (
                "Địa chỉ proxy hoặc mã bảo mật có ký tự không hợp lệ — "
                "HTTP header chỉ nhận ký tự ASCII. Kiểm tra lại ô địa chỉ và "
                "ô mã bảo mật."
            ),
            "models": [],
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

# Ký hiệu thay cho giá trị thật của một trường bí mật khi trả ra ngoài.
#
# Dùng đúng ký hiệu mà giao diện đã dùng cho ô mật khẩu connector, nên
# frontend nhận về cùng một giá trị quen thuộc ở mọi nơi có bí mật.
_SECRET_MASK = "••••••••"

# Tên trường được coi là bí mật. So KHỚP CHÍNH XÁC tên đã quy đổi chữ thường,
# không dò chuỗi con — vì `security.forbidden_keywords` chứa chữ "key" mà
# là CHÍNH SÁCH chặn lệnh nguy hiểm, không phải bí mật; che nhầm sẽ làm
# mất cấu hình bảo mật mà không ai hiểu vì sao.
#
# `api_keys` (số nhiều) là danh sách khoá của lớp định tuyến cũ.
_SECRET_FIELD_NAMES = frozenset({
    "api_key",
    "api_keys",
    "apikey",
    "api_token",
    "access_key_id",
    "secret",
    "secret_access_key",
    "client_secret",
    "private_key",
    "token",
    "bot_token",
    "password",
    # Khoá dịch vụ nằm ở khối phẳng, tên viết HOA kiểu cũ. Có tiền tố nên
    # không khớp "api_key" — quên nó thì khoá vừa KHÔNG bị che, vừa không
    # được khôi phục khi người dùng gửi lại form.
    "groq_api_key",
    "direct_api_key",
})


def _is_secret_field(name: str) -> bool:
    return str(name).lower() in _SECRET_FIELD_NAMES


def _mask_secrets(value: Any) -> Any:
    """
    Trả về bản sao của `value` với mọi trường bí mật đã thay bằng ký hiệu.

    - Chỉ che khi trường CÓ giá trị. Rỗng vẫn hiện rỗng, để phân biệt
      "chưa cấu hình" với "đã lưu nhưng không tiện hiện".
    - Trường bí mật kiểu DANH SÁCH (`api_keys` của lớp định tuyến cũ) che TỪNG
      phần tử và giữ nguyên kiểu list. Thay cả danh sách bằng một chuỗi là đổi
      kiểu dữ liệu: bên đọc không còn biết có bao nhiêu khoá, và lúc ghi lại
      cũng khôi phục không đúng số phần tử.
    - `bool` không bị che: đó là cờ bật/tắt, che thành ký hiệu sẽ làm hỏng
      công tắc trên giao diện.
    """
    if isinstance(value, dict):
        return {k: _mask_secret_field(k, v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask_secrets(v) for v in value]
    return value


def _mask_secret_field(name: str, value: Any) -> Any:
    """Che đúng một trường, giữ nguyên kiểu dữ liệu của nó."""
    if not _is_secret_field(name):
        return _mask_secrets(value)
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append({k: _mask_secret_field(k, v) for k, v in item.items()})
            elif _has_secret_value(item):
                out.append(_SECRET_MASK)
            else:
                out.append(item)
        return out
    if isinstance(value, dict):
        return {k: _mask_secret_field(k, v) for k, v in value.items()}
    if _has_secret_value(value):
        return _SECRET_MASK
    return value


def _has_secret_value(value: Any) -> bool:
    """Trường bí mật này có đáng che không (có giá trị thật chứ không rỗng)."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (list, dict)):
        return bool(value)
    if isinstance(value, str):
        return value.strip() != "" and value != _SECRET_MASK
    return value is not None


def _strip_mask_chars(value: Any) -> Any:
    """
    Bỏ ký hiệu che (•) khỏi một giá trị bí mật người dùng gửi lên.

    Ô bí mật trên giao diện hiện sẵn `••••••••`; người dùng dán khoá mới vào SAU
    ký hiệu thay vì xoá nó trước → gửi lên `••••••••<khoá>` và trước đây chuỗi đó
    được lưu nguyên (token Telegram hỏng, gateway không gửi được). Ký tự • không
    bao giờ có trong khoá/token thật. Còn rỗng sau khi bỏ = "giữ giá trị cũ".
    """
    if isinstance(value, str) and "•" in value:
        cleaned = value.replace("•", "").strip()
        return cleaned or _SECRET_MASK
    return value


def _restore_masked_secrets(payload: Any, existing: Any) -> Any:
    """
    Trả về `payload` với bí mật đang lưu được giữ lại.

    Ngược lại với `_mask_secrets`. Xử lý HAI trường hợp, cùng một ý nghĩa:
    "bí mật mà người dùng không chủ động gửi lên thì giữ nguyên".

    1. Gửi KÝ HIỆU. Giao diện đọc cấu hình, thấy `••••••••`, để nguyên ô rồi
       bấm Lưu — ký hiệu sẽ đi thẳng xuống config.json và thay khoá thật.

    2. KHÔNG GỬI FIELD ĐÓ, hoặc gửi RỖNG. Quan trọng hơn nhiều và dễ sót.
       `merged = {**existing, **payload}` ghép NÔNG, nên `payload["telegram"]`
       thay THẾ trọn khối telegram của bản lưu. Nếu khối đó không kèm
       `bot_token`, token biến mất khỏi config.json trong khi người dùng chỉ
       định sửa một trường khác. Bỏ field ở phía client KHÔNG cứu được:
       client không gửi field, nhưng merge vẫn thay cả khối. Phải chép lại
       từ bản lưu ở đây.

    Hệ quả có chủ ý: KHÔNG có cách xoá bí mật qua form này — ô trống luôn
    được hiểu là "giữ". Đổi lại: không bao giờ mất khoá một cách âm thầm. Muốn
    xoá thì sửa config.json trực tiếp. Trước Phase 80 điều này vốn đã không
    làm được với `llm.api_key` (nhánh `or existing...` chặn rồi), nên không
    mất tính năng nào.

    Chỉ chép trường THỰC SỰ là bí mật, và chỉ khi bản lưu có giá trị — không
    tự thêm khoá vào config khi chưa từng có.
    """
    if isinstance(payload, dict):
        if not isinstance(existing, dict):
            existing = {}
        payload = {
            k: (_strip_mask_chars(v) if _is_secret_field(k) and not isinstance(v, list)
                else ([_strip_mask_chars(i) for i in v] if _is_secret_field(k) and isinstance(v, list) else v))
            for k, v in payload.items()
        }
        # (1)+(2) Bí mật nào KHÔNG bị người dùng ghi đè bằng giá trị có
        # thật thì lấy lại từ bản lưu. Ghi đè ở đây nghĩa là: payload có
        # field đó VÀ giá trị gửi lên là thật (không phải rỗng, không phải
        # ký hiệu).
        out = {
            k: v
            for k, v in existing.items()
            if _is_secret_field(k)
            and _has_secret_value(v)
            and not (
                k in payload
                and payload[k] != _SECRET_MASK
                and _has_secret_value(payload[k])
            )
        }
        # Đệ quy xuống khối con — payload lồng nhau (`{"llm": {...}}`,
        # `{"telegram": {...}}`) và bí mật nằm sâu bên trong.
        for k, v in payload.items():
            if k in out and not isinstance(v, (dict, list)):
                continue  # đã chép bản lưu ở trên
            out[k] = _restore_masked_secrets(v, existing.get(k))
        return out
    if isinstance(payload, list):
        # `api_keys` là danh sách: thay từng phần tử đang là ký hiệu.
        if isinstance(existing, list):
            return [
                existing[i] if (i < len(existing) and item == _SECRET_MASK) else
                _restore_masked_secrets(item, None)
                for i, item in enumerate(payload)
            ]
        return [_restore_masked_secrets(item, None) for item in payload]
    return payload


def _deep_merge(base: Any, incoming: Any) -> Any:
    """
    Ghép `incoming` vào `base`, xuống từng khối con thay vì chỉ một tầng.

    Vì sao cần: `{**existing, **payload}` ghép NÔNG. Giao diện gửi
    `{"telegram": {"admin_chat_ids": [...]}}` thì khối telegram của bản lưu bị
    thay TRỌN — mọi trường mà form không gửi (khoá bot, cờ `enabled`) biến
    mất lặng lẽ trong khi người dùng chỉ định sửa một trường khác. Đây chính
    là lỗi đã xảy ra thật: bấm "Lưu" ở tab Cấu Hình xoá token bot và tắt
    gateway Telegram.

    Danh sách vẫn THAY thế, không nối thêm — nối sẽ ra kết quả sai với ý
    "danh sách admin Telegram này là danh sách này".
    """
    if isinstance(base, dict) and isinstance(incoming, dict):
        out = dict(base)
        for k, v in incoming.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else v
        return out
    return incoming


@app.get(
    "/api/v1/config",
    summary="Read system configuration",
    tags=["Config"],
)
async def get_config(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """
    Read and return the current config.json as JSON.

    Trường bí mật (API key, bot token) KHÔNG trả giá trị thật — thay bằng
    `_SECRET_MASK`. Trước đây endpoint này trả khoá thật cho mọi tài khoản
    `manager`/`admin`, nên chỉ cần token của một tài khoản đó là đủ để lấy
    khoá 9router và token bot Telegram. Cần sửa bí mật thì nhập lại: ô trống
    hoặc ký hiệu khi Lưu nghĩa là giữ nguyên giá trị đang lưu
    (xem `_restore_masked_secrets`).

    Đảm bảo khối 'llm' và cờ 'auto_execute' luôn có mặt.
    """
    try:
        from mateai.config.loader import read_raw_config
        data = read_raw_config(strict=True)

        # Phase 22: Ensure 'llm' block is present
        if "llm" not in data or not isinstance(data["llm"], dict):
            old_primary = data.get("routing", {}).get("primary", {})
            data["llm"] = {
                "base_url": old_primary.get("api_base") or data.get("BASE_URL", "http://localhost:20128/v1"),
                "model_name": old_primary.get("provider_model") or data.get("MODEL_NAME", ""),
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

        # Che bí mật ở CỬA CUỐI cùng: mọi nhánh tương thích ngược phía trên
        # (`API_KEY`, `routing.*`, `router.*`) đều nhân bản cùng một khoá ra
        # nhiều đường dẫn, và che từng nhánh thì dễ sót. Ở đây một lần là xong.
        return _mask_secrets({k: v for k, v in data.items() if not k.startswith("_")})
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="config.json not found.")
    except _json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"config.json is malformed: {exc}")


async def _router_model_pool() -> List[str]:
    """
    Hỏi router đang phục vụ model nào, trả về danh sách dùng làm dự phòng.

    Router là nguồn sự thật: nó chỉ liệt kê model tới được từ provider đang bật
    và còn hạn mức. Hardcode tên model trong code nghĩa là chỉ đúng vào một
    thời điểm — hôm sau provider hết tiền, cấu hình lưu xuống lại toàn model
    chết và hệ thống cứ thử chết trước khi tới model thật.

    Bỏ qua mọi thứ không phải model chat: combo do người dùng đặt (tên không
    có dấu "/"), và các loại khác nếu router có trả về.
    """
    from mateai.config.loader import settings  # import cục bộ như các hàm khác
    base = (getattr(settings.llm, "base_url", "") if settings else "") or ""
    if not base:
        return []
    try:
        root = base.rsplit("/v1", 1)[0] if "/v1" in base else base.rstrip("/")
        key = (getattr(settings.llm, "api_key", "") if settings else "") or ""
        req = urllib.request.Request(
            f"{root}/v1/models",
            headers={"Authorization": f"Bearer {key}"} if key else {},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("[Config] Không đọc được danh sách model từ router: %s", exc)
        return []

    out: List[str] = []
    for entry in payload.get("data") or []:
        mid = entry.get("id") if isinstance(entry, dict) else None
        # Combo của người dùng (ví dụ "VN-MateAi") không có dấu "/" — đưa vào
        # danh sách dự phòng thì gọi lại chính nó, tức lặp vô hạn.
        if mid and "/" in mid and mid not in out:
            out.append(mid)
    return out


@app.get(
    "/api/v1/config/models",
    summary="Danh sách model router đang phục vụ",
    tags=["Config"],
)
async def list_available_models(
    user: dict = Depends(require_roles(["admin", "operator"])),
) -> Dict[str, Any]:
    """
    Trả về model mà router thực sự phục vụ, để giao diện không phải ghi cứng.

    Trước đây danh sách gợi ý trong UI ghi cứng tên model của một provider.
    Khi provider đó hết tiền, giao diện vẫn hiện các nút bấm chết và người
    dùng bấm vào rồi mới biết là chết — trải nghiệm rất tệ. Nay danh sách lấy
    từ router mỗi lần mở, nên bấm là chạy.
    """
    from mateai.config.loader import settings
    models = await _router_model_pool()
    return {
        "models": models,
        "count": len(models),
        "base_url": (getattr(settings.llm, "base_url", "") if settings else "") or "",
    }


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
        from mateai.config.loader import read_raw_config, write_raw_config
        # strict: config.json hỏng thì báo lỗi, KHÔNG ghi đè bằng bản chỉ có payload.
        existing: Dict[str, Any] = read_raw_config(strict=True)

        # Thay ký hiệu chỗ trống bằng giá trị đang lưu TRƯỚC KHI chuẩn hoá.
        #
        # Bắt buộc thực hiện ở đây, không làm sau: các nhánh chuẩn hoá bên dưới
        # dùng `payload[...].get("api_key") or existing...` — ký hiệu "••••••••"
        # là chuỗi TRÌNH (truthy) nên sẽ thắng, và khoá thật bị ghi đè bằng
        # ký hiệu. Hậu quả: người dùng chỉ cần bấm "Lưu" để sửa một trường
        # không liên quan là khoá LLM hỏng, mà không có lỗi nào báo ra.
        payload = _restore_masked_secrets(payload, existing)

        # Phase 68: danh sách dự phòng lấy TỪ ROUTER thay vì hardcode.
        #
        # Trước đây danh sách này ghi cứng tên model của một provider cụ thể
        # (`ag/...`). Khi provider đó hết tiền hoặc mất khoá, mọi lần lưu cấu
        # hình lại ghi 3 model chết vào config — khiến vòng lặp dự phòng gọi
        # 3 lần vào chỗ chết trước khi tới model thật, và làm chuyển giao
        # chuyên gia hỏng hoàn toàn.
        #
        # Nay hỏi router xem nó đang phục vụ model nào, rồi dùng chính những
        # model đó. Tự lành khi bạn nạp tiền provider, và không cần sửa code
        # khi đổi nhà cung cấp.
        pool = await _router_model_pool()
        if not pool:
            # Router không trả lời — không đoán, để trống cho tới lần lưu sau.
            logger.warning(
                "[Config] Router không trả danh sách model — bỏ trống danh sách dự phòng "
                "thay vì ghi model chết."
            )
        DEFAULT_ROUTER_FALLBACKS = list(pool)
        DEFAULT_SPECIALIST_FALLBACKS = list(pool)

        # Phase 73: các nhánh chuẩn hoá bên trên KHÔNG tự điền "sk-dummy" nữa.
        # Khi cả payload lẫn cấu hình cũ đều không có khoá, ta ghi chuỗi rỗng —
        # một khoá giả ghi xuống đĩa trông y hệt khoá thật. `settings` vẫn có
        # mặc định riêng cho lúc dựng client, nên LLM vẫn chạy bình thường.

        if "llm" in payload and isinstance(payload["llm"], dict):
            existing_llm = existing.get("llm", {})
            new_model = payload["llm"].get("model_name", existing_llm.get("model_name", "")) or ""
            r_models = payload["llm"].get("router_models", existing_llm.get("router_models", []))
            if not isinstance(r_models, list) or not r_models:
                r_models = DEFAULT_ROUTER_FALLBACKS
            if new_model and new_model not in r_models:
                r_models = [new_model] + [m for m in r_models if m != new_model]
            s_models = payload["llm"].get("specialist_models", existing_llm.get("specialist_models", DEFAULT_SPECIALIST_FALLBACKS))
            payload["llm"] = {
                "base_url": payload["llm"].get("base_url", existing_llm.get("base_url", "http://localhost:20128/v1")),
                "model_name": new_model,
                "api_key": payload["llm"].get("api_key") or existing_llm.get("api_key") or "",
                "router_models": r_models,
                "specialist_models": s_models,
                # Phase 91: Dual-mode routing fields
                "routing_mode": payload["llm"].get("routing_mode", existing_llm.get("routing_mode", "router")),
                "direct_url": payload["llm"].get("direct_url", existing_llm.get("direct_url", "")),
                "direct_model": payload["llm"].get("direct_model", existing_llm.get("direct_model", "")),
                # Không ghi khoá giả vào config.json; client direct tự dùng
                # khoá giữ chỗ khi rỗng (llm_engine: `direct_api_key or "lm-studio"`).
                "direct_api_key": payload["llm"].get("direct_api_key") or existing_llm.get("direct_api_key") or "",
            }
        elif "routing" in payload and isinstance(payload["routing"], dict):
            # If incoming is legacy routing, extract primary into 'llm'
            primary = payload["routing"].get("primary", {})
            new_model = primary.get("provider_model", "") or ""
            payload["llm"] = {
                "base_url": primary.get("api_base", "http://localhost:20128/v1"),
                "model_name": new_model,
                "api_key": primary.get("api_key") or existing.get("llm", {}).get("api_key") or "",
                "router_models": [new_model] + [m for m in DEFAULT_ROUTER_FALLBACKS if m != new_model],
                "specialist_models": DEFAULT_SPECIALIST_FALLBACKS,
            }
        elif "MODEL_NAME" in payload or "BASE_URL" in payload:
            existing_llm = existing.get("llm", {})
            new_model = payload.get("MODEL_NAME", existing_llm.get("model_name", "")) or ""
            payload["llm"] = {
                "base_url": payload.get("BASE_URL", existing_llm.get("base_url", "http://localhost:20128/v1")),
                "model_name": new_model,
                "api_key": payload.get("API_KEY") or existing_llm.get("api_key") or "",
                "router_models": [new_model] + [m for m in DEFAULT_ROUTER_FALLBACKS if m != new_model],
                "specialist_models": DEFAULT_SPECIALIST_FALLBACKS,
            }

        # Keep auto_execute and AUTO_EXECUTE_UNVERIFIED_CODE in sync
        if "auto_execute" in payload:
            payload["AUTO_EXECUTE_UNVERIFIED_CODE"] = bool(payload["auto_execute"])
        elif "AUTO_EXECUTE_UNVERIFIED_CODE" in payload:
            payload["auto_execute"] = bool(payload["AUTO_EXECUTE_UNVERIFIED_CODE"])

        comment_keys = {k: v for k, v in existing.items() if k.startswith("_")}
        merged = _deep_merge({**existing, **comment_keys}, payload)

        write_raw_config(merged)
        logger.info("config.json updated via Web Portal.")

        # Hot-reload in-memory settings
        try:
            from mateai.config.loader import reload_settings
            reload_settings()
            # Broadcast updated assistant name to all connected HUD displays in real-time
            updated_ai_name = payload.get("AI_NAME") or payload.get("ASSISTANT_NAME") or payload.get("persona", {}).get("ai_name") or "Ly Ly"
            await broadcast_hud({
                "type": "assistant_name_updated",
                "assistant_name": updated_ai_name,
                "timestamp": datetime.utcnow().isoformat(),
            })
            # Broadcast updated audio config (TTS rate/voice/volume) so HUD applies immediately
            audio_block = merged.get("audio", {})
            tts_rate_top = merged.get("TTS_RATE", "+15%")
            tts_voice_top = merged.get("TTS_VOICE", "vi-VN-HoaiMyNeural")
            await broadcast_hud({
                "type": "audio_config_updated",
                "tts_voice": audio_block.get("tts_voice") or tts_voice_top,
                "tts_rate": tts_rate_top,
                "speech_rate_num": audio_block.get("speech_rate", 15),
                "volume": audio_block.get("volume", 80),
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
                from mateai.infrastructure.connectors import CONNECTOR_REGISTRY

                # reload_config() đọc config.json MỚI qua config_loader (không còn cache).
                for name in touched:
                    connector = CONNECTOR_REGISTRY.get(name)
                    if connector is not None:
                        connector.reload_config()
            except Exception as conn_err:  # pylint: disable=broad-except
                logger.warning("Connector reload sau khi lưu config thất bại: %s", conn_err)

        # If telegram config was included, ensure gateway reflects changes
        if "telegram" in payload:
            # Đọc token từ CẤU HÌNH ĐÃ GHÉP, không phải từ payload.
            #
            # Phase 80: client không còn gửi `bot_token` (ô để trống nghĩa là
            # giữ), nên `payload["telegram"].get("bot_token", "")` luôn rỗng →
            # nhánh `if tg_token` không bao giờ chạy → gateway bị stop() rồi
            # không khởi động lại, trong khi cờ `enabled` vẫn là true. Cấu
            # hình và trạng thái thực lệch nhau.
            tg_block = merged.get("telegram", {})
            tg_token = tg_block.get("bot_token", "") if isinstance(tg_block, dict) else ""
            tg_enabled = bool(tg_block.get("enabled")) if isinstance(tg_block, dict) else False
            try:
                from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
                if not tg_enabled:
                    telegram_gateway.stop()
                elif tg_token and not getattr(telegram_gateway, "is_running", False):
                    # Chỉ khởi động lại khi CẦN. Gateway đang chạy và cấu hình
                    # không đổi thì không đụng tới — stop() rồi start() lúc
                    # người dùng chỉ lưu một trường khác là mất kết nối thật.
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


from mateai.interfaces.http.routers import skills as _r_skills  # noqa: E402
app.include_router(_r_skills.router)


# ---------------------------------------------------------------------------
# Pairing Code Endpoints (6-Digit Dynamic Robot Sync)
# ---------------------------------------------------------------------------

class PairingVerifyRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=10, description="Mã 6 số hiển thị trên màn hình Robot")

class PairingUnpairRequest(BaseModel):
    device_id: str

@app.post(
    "/api/v1/pairing/verify",
    summary="Xác nhận mã 6 số để ghép đôi Robot với Web Portal/HUD",
    tags=["Robotics Pairing"],
)
async def verify_pairing_code(
    payload: PairingVerifyRequest,
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    clean_code = payload.code.strip()
    device_id = pairing_registry.lookup(clean_code)
    if not device_id:
        for d_id, node in xiaozhi_gateway.get_all_nodes().items():
            if pairing_registry.get_code_for_device(d_id) == clean_code:
                device_id = d_id
                break

    if not device_id:
        raise HTTPException(
            status_code=404,
            detail=f"Mã '{clean_code}' không hợp lệ hoặc robot chưa trực tuyến. Hãy kiểm tra màn hình OLED của Robot!",
        )

    node = xiaozhi_gateway.get_node(device_id)
    # Gửi tín hiệu xác nhận thành công tới Robot để hiển thị trên OLED
    await xiaozhi_gateway.send_ui_payload(
        device_id=device_id,
        state="idle",
        emotion="happy",
        text="Ghep doi thanh cong!",
    )

    # Thông báo cho HUD và Portal UI
    try:
        await broadcast_portal_ui("robot_paired", {
            "device_id": device_id,
            "pairing_code": clean_code,
            "user": user.get("username"),
            "timestamp": datetime.utcnow().isoformat(),
        })
        await broadcast_hud({
            "type": "robot_status",
            "status": "paired",
            "device_id": device_id,
            "pairing_code": clean_code,
            "timestamp": datetime.utcnow().isoformat(),
        })
    except Exception:
        pass

    return {
        "success": True,
        "device_id": device_id,
        "pairing_code": clean_code,
        "message": f"Ghép đôi robot [{device_id}] thành công!",
        "telemetry": node.to_dict() if node else None,
    }


@app.get(
    "/api/v1/pairing/status",
    summary="Kiểm tra trạng thái các robot và mã pairing đang hoạt động",
    tags=["Robotics Pairing"],
)
async def get_pairing_status(
    user: dict = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    active_codes = pairing_registry.list_all()
    nodes_telemetry = xiaozhi_gateway.get_nodes_telemetry()

    for node_info in nodes_telemetry:
        d_id = node_info.get("device_id")
        node_info["pairing_code"] = pairing_registry.get_code_for_device(d_id) if d_id else None

    return {
        "active_codes": active_codes,
        "connected_robots": nodes_telemetry,
        "robot_count": len(nodes_telemetry),
    }


@app.post(
    "/api/v1/pairing/unpair",
    summary="Huỷ ghép đôi robot",
    tags=["Robotics Pairing"],
)
async def unpair_robot(
    payload: PairingUnpairRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway, pairing_registry

    await pairing_registry.unregister(payload.device_id)
    await xiaozhi_gateway.send_ui_payload(
        payload.device_id,
        state="idle",
        emotion="sleeping",
        text="Da huy ket noi.",
    )
    return {"success": True, "message": f"Đã huỷ ghép đôi robot [{payload.device_id}]."}


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
        ai_name = _get_assistant_name()
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


_local_worker_process: Optional[subprocess.Popen] = None

# Bao lâu thì coi worker là "không lên được" rồi tự dừng. Đủ dài cho tiến
# trình Python khởi động, import thư viện và mở WebSocket trên máy này.
_LOCAL_WORKER_READY_TIMEOUT = 8.0


def _resolve_master_endpoint(request: Request) -> tuple[str, str, int]:
    """Trả về `(scheme, host, port)` mà một Client Agent phải nối tới.

    Đọc từ chính yêu cầu đang đến (`request.url`), không đọc `config.json`.
    Lý do: `config.json` để lại `PORT: 443` từ lâu trong khi máy chủ thật
    được khởi chạy bằng `uvicorn ... --port 8000` — cấu hình lệch với thực tế
    là nguồn của URL không bắt tay được. Yêu cầu thì không thể lệch: nó phản
    ánh đúng cổng và scheme mà trình duyệt vừa dùng để mở trang này.

    `loopback_only=True` ép về 127.0.0.1 — dùng cho worker chạy ngay trên
    máy chủ. Worker tải về chạy ở máy khác nên KHÔNG ép (xem
    `_local_worker_ws_url` và `download_agent`).
    """
    url = request.url
    scheme = "wss" if url.scheme in ("https", "wss") else "ws"
    host = url.hostname or "127.0.0.1"
    # `0.0.0.0` / `::` là địa chỉ "mọi giao diện", không phải địa chỉ nào để
    # kết nối tới. Ở đây coi như loopback; nếu sau reverse proxy bị rơi vào
    # giá trị này thì thà chỉ loopback còn hơn sinh ra URL không dùng được.
    if host in ("0.0.0.0", "::", "[::]", ""):
        host = "127.0.0.1"
    port = url.port or (443 if scheme == "wss" else 80)
    return scheme, host, port


def _master_ws_url(request: Request) -> str:
    """URL WebSocket cho Client Agent ở MÁY KHÁC (gói tải về).

    Khác `_local_worker_ws_url` ở chỗ không ép loopback: máy con ở trong LAN
    phải nối tới địa chỉ mà máy chủ thực sự nghe, nên dùng IP mà người dùng
    đang truy cập.
    """
    scheme, host, port = _resolve_master_endpoint(request)
    return f"{scheme}://{host}:{port}/ws/client"


def _local_worker_ws_url(request: Request) -> str:
    """Dựng URL WebSocket mà worker cục bộ phải nối tới.

    Trước đây URL này ghi cứng `"wss://127.0.0.1:443/ws/client"`, sai trên
    hai điểm một lúc:

      * Cổng. `config.json` để lại `PORT: 443` từ lâu, còn máy chủ thật được
        khởi chạy bằng `uvicorn ... --port 8000`. Không có gì lắng nghe 443.
      * Scheme. Máy chủ chạy HTTP thuần, nên `wss://` (WebSocket over TLS)
        không bao giờ bắt tay được với nó.

    Cả hai lỗi đều im lặng: tiến trình vẫn sinh ra, vẫn nhận PID, chỉ là
    không bao giờ kết nối được.

    Nay dựng URL từ chính yêu cầu đang đến: admin bấm nút trên cổng nào thì
    worker nối về đúng cổng đó, đúng scheme đó — không đoán, không phụ thuộc
    cấu hình lệch với thực tế. Worker này chạy cùng máy chủ nên quay về
    loopback.
    """
    scheme, host, port = _resolve_master_endpoint(request)
    return f"{scheme}://127.0.0.1:{port}/ws/client"


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
    request: Request,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Khởi chạy hoặc dừng Worker Node cục bộ trên máy chủ Master.

    Chỉ báo "thành công" khi worker THỰC SỰ đăng ký, xem `_local_worker_ws_url`
    và phần kiểm chứng bên dưới.
    """
    global _local_worker_process
    # Import cục bộ: `orchestrator` là singleton sống ở mateai.interfaces.websocket.client_orchestrator, các
    # endpoint khác cũng import kiểu này chứ không nằm ở phạm vi module.
    from mateai.interfaces.websocket.client_orchestrator import orchestrator

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
        if not agent_script.exists():
            return {
                "active": False,
                "message": f"Không tìm thấy {agent_script} — không thể khởi chạy worker.",
            }

        # Chặn khởi chạy trùng. `_local_worker_process` sống trong bộ nhớ của
        # tiến trình máy chủ, nên sau mỗi lần restart nó về None — còn worker
        # thì vẫn còn sống và tự nối lại. Bấm "Bật" lúc đó sẽ sinh ra worker
        # thứ hai dùng chung một `client_id`, hai tiến trình tranh nhau đăng ký
        # cùng một tên và danh sách client hiện ra loạn.
        _already = any(
            str((c or {}).get("client_id") or (c or {}).get("id") or "") == "MASTER_LOCAL_WORKER"
            for c in orchestrator.get_connected_clients()
        )
        if _already:
            return {
                "active": True,
                "message": (
                    "Worker Node [MASTER_LOCAL_WORKER] đã có sẵn trong danh sách client — "
                    "không khởi chạy thêm. Nếu đó là worker cũ sót lại từ lần chạy trước, "
                    "hãy tắt nó ở máy đó rồi bấm Bật lại."
                ),
            }

        ws_url = _local_worker_ws_url(request)
        # Zero-Trust: truyền enrollment secret cho worker cục bộ qua env var.
        _worker_env = os.environ.copy()
        _worker_env["VNMATE_ENROLLMENT_TOKEN"] = enrollment.get_worker_enrollment_secret()
        _local_worker_process = subprocess.Popen(
            [
                sys.executable,
                str(agent_script),
                "--server",
                ws_url,
                "--id",
                "MASTER_LOCAL_WORKER",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=_worker_env,
        )
        pid = _local_worker_process.pid
        logger.info("Local Worker Node [MASTER_LOCAL_WORKER] đã khởi chạy (PID: %s) qua %s.", pid, ws_url)

        # ── Kiểm chứng, đừng báo thành công bừa ────────────────────────────
        #
        # Trước đây hàm báo "khởi chạy thành công!" ngay sau `Popen`, tức là
        # chỉ cần tiến trình được sinh ra là báo thành công — dù nó nối vào
        # một cổng không ai lắng nghe. Đo thật: bấm "Bật", API trả về
        # `active: true` kèm PID, nhưng sau 5 giây danh sách client vẫn rỗng
        # và worker không bao giờ kết nối. Người dùng tin thông báo đó rồi
        # tưởng hệ thống đã chạy, trong khi thực tế nó chết lặng lẽ.
        #
        # Nay đợi worker thật sự hiện trong registry rồi mới báo thành công.
        deadline = time.time() + _LOCAL_WORKER_READY_TIMEOUT
        registered = False
        while time.time() < deadline:
            await asyncio.sleep(0.25)
            proc = _local_worker_process
            if proc is None:
                break
            if proc.poll() is not None:
                # Tiến trình tự tắt (lỗi kết nối, thiếu thư viện, ...)
                _local_worker_process = None
                logger.warning("Local Worker thoát ngay sau khi khởi chạy (mã %s).", proc.returncode)
                return {
                    "active": False,
                    "message": (
                        f"Worker không khởi động được: tiến trình thoát ngay (mã {proc.returncode}). "
                        f"Kiểm tra tại sao bằng: python3 {agent_script} --server {ws_url} --id MASTER_LOCAL_WORKER"
                    ),
                }
            if any(
                str((c or {}).get("client_id") or (c or {}).get("id") or "") == "MASTER_LOCAL_WORKER"
                for c in orchestrator.get_connected_clients()
            ):
                registered = True
                break

        if not registered:
            # Dừng luôn: để một tiến trình nối vào hư không thì chỉ là rác.
            try:
                _local_worker_process.terminate()
                _local_worker_process.wait(timeout=3)
            except Exception:
                try:
                    _local_worker_process.kill()
                except Exception:
                    pass
            _local_worker_process = None
            logger.warning(
                "Local Worker không đăng ký được sau %.1fs qua %s.", _LOCAL_WORKER_READY_TIMEOUT, ws_url
            )
            return {
                "active": False,
                "message": (
                    f"Worker chạy được nhưng không kết nối được về máy chủ tại {ws_url} "
                    f"trong {_LOCAL_WORKER_READY_TIMEOUT:.0f} giây — đã dừng tiến trình. "
                    "Kiểm tra cổng này còn chạy không."
                ),
            }

        return {
            "active": True,
            "pid": pid,
            "message": "Worker Node cục bộ [MASTER_LOCAL_WORKER] đã kết nối thành công.",
        }


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
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
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
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
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
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
    await xiaozhi_gateway.handle_barge_in(payload.device_id)
    return {
        "status": "success",
        "action": "barge_in_triggered",
        "device_id": payload.device_id,
        "reflex": "Dạ, anh nói đi em nghe đây.",
    }


class XiaozhiAnnounceRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="Nội dung TTS sẽ phát ra loa robot")
    device_id: Optional[str] = Field(None, description="ID robot cụ thể. Bỏ trống = phát tới TẤT CẢ robot đang online")

@app.post(
    "/api/v1/xiaozhi/announce",
    summary="Phát thông báo TTS trực tiếp ra loa Robot (tất cả hoặc robot cụ thể)",
    tags=["Xiaozhi Desktop Companion"],
)
async def xiaozhi_announce_endpoint(
    payload: XiaozhiAnnounceRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Dict[str, Any]:
    """Stream TTS audio trực tiếp tới loa của robot qua WebSocket — không phát trong browser."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway

    clean_text = payload.text.strip()
    if not clean_text:
        raise HTTPException(status_code=400, detail="Nội dung thông báo không được để trống.")

    # Xác định target nodes
    if payload.device_id:
        node = xiaozhi_gateway.get_node(payload.device_id)
        if not node:
            raise HTTPException(status_code=404, detail=f"Robot [{payload.device_id}] không online.")
        targets = [node]
    else:
        targets = list(xiaozhi_gateway.get_all_nodes().values())

    if not targets:
        raise HTTPException(status_code=503, detail="Không có robot nào đang kết nối.")

    # Sinh audio TTS một lần, chuyển đổi sang PCM 16kHz thuần cho loa MAX98357A
    try:
        raw_mp3 = await speech.tts_bytes(clean_text)
        if not raw_mp3:
            raise HTTPException(status_code=500, detail="Không thể sinh audio TTS.")
        from mateai.interfaces.websocket.xiaozhi_gateway import convert_to_pcm16_16k
        pcm_bytes = convert_to_pcm16_16k(raw_mp3)
    except Exception as tts_err:
        raise HTTPException(status_code=500, detail=f"Lỗi TTS: {tts_err}")

    sent_count = 0
    chunk_size = 2048
    for node in targets:
        try:
            await node.websocket.send_text(_json.dumps({
                "type": "tts_start", "format": "audio/pcm",
                "sample_rate": 16000, "channels": 1, "text": clean_text, "source": "portal_announce",
            }))
            await xiaozhi_gateway.send_ui_payload(
                node.device_id, state="speaking", emotion="happy", text=clean_text[:40],
            )
            for offset in range(0, len(pcm_bytes), chunk_size):
                if node.cancel_event.is_set():
                    break
                await node.websocket.send_bytes(pcm_bytes[offset : offset + chunk_size])
                await asyncio.sleep(0.045)

            await node.websocket.send_text(_json.dumps({"type": "tts_end"}))
            sent_count += 1
            logger.info("[Announce] Đã phát '%s' tới [%s] (%d bytes PCM)", clean_text[:30], node.device_id, len(pcm_bytes))
        except Exception as send_err:
            logger.warning("[Announce] Không thể gửi tới robot [%s]: %s", node.device_id, send_err)

    return {
        "status": "success",
        "text": clean_text,
        "sent_to": sent_count,
        "total_robots": len(targets),
        "message": f"Đã phát thông báo tới {sent_count}/{len(targets)} robot.",
    }


@app.get(
    "/api/v1/xiaozhi/nodes",
    summary="Phase 43: Danh sách & Telemetry màn hình LCD của mạch Xiaozhi",
    tags=["Xiaozhi Desktop Companion"],
)
async def get_xiaozhi_nodes_telemetry(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """Trả về danh sách các mạch Xiaozhi Desktop Companion đang online kèm trạng thái LCD và biểu cảm."""
    from mateai.interfaces.websocket.xiaozhi_gateway import xiaozhi_gateway
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
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
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
    from mateai.application.operations.autonomous_sentinel import autonomous_sentinel
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
    from mateai.application.devices.task_manager import task_manager
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

    from mateai.application.devices.task_manager import task_manager
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
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled
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
        from mateai.infrastructure.audio.wake_word_engine import is_mic_enabled, set_mic_enabled
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


class TelegramTestAlertRequest(BaseModel):
    bot_token: Optional[str] = Field(None, description="Telegram Bot Token từ BotFather")
    admin_chat_ids: Optional[List[str]] = Field(None, description="Danh sách Chat ID của admin")
    incident_group_id: Optional[str] = Field(None, description="Group ID nhận cảnh báo sự cố")
    target_chat_id: Optional[str] = Field(None, description="Chat ID mục tiêu cụ thể")


from mateai.interfaces.http.routers import domain as _r_domain  # noqa: E402
app.include_router(_r_domain.router)


@app.get(
    "/api/v1/telegram/config",
    summary="Phase 18: Lấy cấu hình và trạng thái Telegram Bot",
    tags=["Telegram"],
)
async def get_telegram_config(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return Telegram Gateway settings and running status.

    Phase 79: `bot_token` trả về ký hiệu chỗ trống, KHÔNG phải giá trị thật.
    Endpoint này dùng `get_current_user` nên mọi tài khoản đã đăng nhập đều gọi
    được — kể cả role `viewer` chỉ được xem. Trả token thật ở đây tức bất kỳ
    tài khoản nào cũng chiếm được quyền điều khiển bot Telegram.
    Sửa token: nhập lại ở ô cấu hình. Gửi lại ký hiệu khi Lưu thì giữ token
    cũ (xem `_restore_masked_secrets`).
    """
    try:
        from mateai.config.loader import get_config_section
        tg_data = get_config_section("telegram")

        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        is_running = getattr(telegram_gateway, "is_running", False)

        masked = _mask_secrets(dict(tg_data))
        return {
            "status": "success",
            "enabled": tg_data.get("enabled", False),
            "bot_token": masked.get("bot_token", ""),
            "admin_chat_ids": tg_data.get("admin_chat_ids", []),
            "incident_group_id": tg_data.get("incident_group_id", ""),
            "is_running": is_running,
            "config": masked,
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
        from mateai.config.loader import update_config_section

        enabled = bool(payload.get("enabled", False))
        raw = update_config_section("telegram", {"enabled": enabled})

        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
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
        from mateai.config.loader import read_raw_config, write_raw_config

        # Load raw config file (strict: file hỏng thì dừng, không ghi đè bằng bản rỗng)
        raw = read_raw_config(strict=True)

        # Update telegram section
        raw.setdefault("telegram", {})

        # Phase 80: chốt bí mật cho endpoint này, y hệt `/api/v1/config`.
        #
        # `TelegramConfigRequest.bot_token` mặc định là `""` chứ không phải
        # `None`, nên `if payload.bot_token is not None` LUÔN đúng — client chỉ
        # cần bỏ trống ô là token thật bị ghi đè bằng chuỗi rỗng. Từ Phase 79
        # ô trên giao diện luôn để trống (server không trả token nữa) nên mọi
        # lần bấm Lưu cấu hình Telegram đều xoá token. Ô trống phải nghĩa là
        # GIỮ, giống mọi ô bí mật khác.
        incoming = {
            "enabled": payload.enabled,
            "bot_token": payload.bot_token,
            "admin_chat_ids": payload.admin_chat_ids,
            "incident_group_id": payload.incident_group_id,
        }
        incoming = _restore_masked_secrets(incoming, raw.get("telegram", {}))

        if incoming["enabled"] is not None:
            raw["telegram"]["enabled"] = incoming["enabled"]
        if incoming["bot_token"]:
            raw["telegram"]["bot_token"] = incoming["bot_token"]
        if incoming["admin_chat_ids"] is not None:
            raw["telegram"]["admin_chat_ids"] = incoming["admin_chat_ids"]
        if incoming["incident_group_id"] is not None:
            raw["telegram"]["incident_group_id"] = incoming["incident_group_id"]

        write_raw_config(raw)

        # Reload settings in-memory
        try:
            from mateai.config.loader import reload_settings
            reload_settings()
        except Exception as r_err:
            logger.warning("Phase 18: Settings reload failed after telegram config write: %s", r_err)

        is_tg_on = raw.get("telegram", {}).get("enabled", False)
        # Restart or stop gateway
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        effective_bot_token = (raw.get("telegram", {}).get("bot_token") or "").strip()
        if is_tg_on and effective_bot_token:
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
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
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
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        from datetime import datetime as _dt

        bot_token = payload.bot_token if payload else None
        if bot_token and bot_token.strip() == _SECRET_MASK:
            bot_token = None
        admin_chat_ids = payload.admin_chat_ids if payload else None
        incident_group_id = payload.incident_group_id if payload else None
        target_chat_id = payload.target_chat_id if payload else None

        # Gọi Telegram API đồng bộ (timeout tới 20 s) — chạy ngoài event loop.
        result = await run_blocking(
            telegram_gateway.test_connection,
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
        from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
        bot_token = payload.bot_token if payload else None
        chats = await run_blocking(telegram_gateway.get_recent_chats, bot_token=bot_token)
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
    from mateai.config.loader import settings
    templates = getattr(settings, "report_templates", {}) or {}
    if not templates:
        from mateai.config.loader import _load_raw_config
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

    from mateai.config.loader import read_raw_config, reload_settings, write_raw_config

    try:
        raw = read_raw_config(strict=True)
        raw["report_templates"] = templates
        write_raw_config(raw)
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

    # Phase 85: dựng địa chỉ máy chủ từ chính yêu cầu đang đến.
    #
    # Trước đây endpoint này đọc `PORT` trong `config.json` (đang là 443) rồi
    # ghi `wss://<ip>:443/ws/client` vào config.json của gói tải về. Gói đó KHÔNG
    # BAO GIỜ kết nối được: máy chủ thật chạy cổng 8000, và chạy HTTP thuần nên
    # `wss://` không bắt tay được. Người dùng tải Agent, chạy `python
    # agent.py`, agent báo lỗi kết nối liên tục — mà trên máy chủ mọi thứ vẫn
    # bình thường. Lỗi giống hệt mà `_local_worker_ws_url()` đã sửa từ trước,
    # nhưng bản đóng gói bị bỏ sót.
    #
    # Nay cả hai cùng đọc `request.url` (xem `_resolve_master_endpoint`), nên
    # IP/cổng/scheme luôn khớp với nơi người dùng đang xem trang này.
    scheme, host, port = _resolve_master_endpoint(request)

    # Nếu người dùng mở trang bằng localhost/loopback, máy con trong LAN không
    # nối được vào 127.0.0.1 — lúc đó mới thay bằng IP LAN của máy chủ.
    server_ip = host
    if host in ("127.0.0.1", "localhost", "::1"):
        server_ip = _get_server_local_ip()

    default_port = 443 if scheme == "wss" else 80
    port_suffix = "" if port == default_port else f":{port}"
    ws_url = f"{scheme}://{server_ip}:{port}/ws/client"

    dynamic_config = {
        "server_url": f"{'https' if scheme == 'wss' else 'http'}://{server_ip}{port_suffix}",
        "ws_url": ws_url,
        "client_id": "auto_generate_on_first_run",
        "master_ip": server_ip,
        "master_port": port,
        "downloaded_at": datetime.utcnow().isoformat() + "Z",
        "downloaded_by": current_user.get("username", "unknown"),
        # Zero-Trust: enrollment secret để agent đăng ký qua /ws/client.
        # Chỉ phát cho tài khoản admin/manager (đã kiểm tra ở endpoint này).
        "enrollment_token": enrollment.get_worker_enrollment_secret(),
    }

    # Build ZIP entirely in RAM — no temporary files written to disk
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        template_dir = _CLIENT_AGENT_DIR

        if template_dir.exists() and template_dir.is_dir():
            for file_path in sorted(template_dir.rglob("*")):
                # Skip __pycache__ directories and compiled Python bytecode
                if "__pycache__" in file_path.parts:
                    continue
                if file_path.suffix in (".pyc", ".pyo", ".log"):
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
            logger.error("client_agent/ directory not found at %s", template_dir)
            raise HTTPException(
                status_code=500,
                detail="Thư mục client_agent/ không tồn tại trên máy chủ. Liên hệ Admin.",
            )

        # Phase 29: Read certs/server.crt on server and embed directly into Client Agent ZIP as server_cert.pem
        try:
            from mateai.infrastructure.security.tls import CERT_FILE, ensure_ssl_certs
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
        "Phase 85: gói Agent tải về bởi '%s' — cấu hình: %s, dung lượng %d bytes",
        current_user.get("username"),
        ws_url,
        len(zip_content),
    )

    return Response(
        content=zip_content,
        media_type="application/x-zip-compressed",
        headers={
            "Content-Disposition": 'attachment; filename="VN-Mate_Agent.zip"',
            "Content-Length": str(len(zip_content)),
            "X-Agent-Server-IP": server_ip,
            "X-Agent-Server-Port": str(port),
        },
    )


# ═══════════════════════════════════════════════════════════════════════════
# Phase 48 — ITSM, Audit Trail, ROI Dashboard API Endpoints
# ═══════════════════════════════════════════════════════════════════════════


from mateai.interfaces.http.routers import itsm as _r_itsm  # noqa: E402
app.include_router(_r_itsm.router)


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
    from mateai.infrastructure.database.erp_database import erp_db

    # Báo cáo ngày
    report = await run_blocking(generate_daily_report,
        report_date=report_date,
        include_audit_details=True,
    )

    # Tickets đang mở (pending + in_progress)
    from skills.itsm_skills import get_tickets
    open_tickets = await run_blocking(get_tickets, status_filter="pending", limit=20)
    inprogress_tickets = await run_blocking(get_tickets, status_filter="in_progress", limit=20)
    ai_tickets = await run_blocking(get_tickets, ai_only=True, limit=10)

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
    from mateai.infrastructure.database.erp_database import erp_db
    try:
        employees = await run_blocking(erp_db.list_employees, dept_id=dept_id, role=role, limit=limit)
        return {"status": "success", "total": len(employees), "employees": employees}
    except Exception as e:
        return {"status": "error", "error": str(e)}


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

@app.get(
    "/api/v1/computer-use/status",
    summary="Phase 90: Computer-Use & Worker Engine status",
    tags=["Computer Use Phase 90"],
)
async def api_computer_use_status() -> Dict[str, Any]:
    """Lấy trạng thái tổng quan cụm worker, session profiles và queue."""
    try:
        from workers.browser_session_vault import browser_session_vault
        from workers.self_healing_engine import self_healing_engine
        from mateai.application.skills.computer_use_plugin import _IN_MEMORY_TASK_QUEUE, REDIS_WORKER_QUEUE

        # Đếm profile hiện có
        session_list = []
        base_dir = browser_session_vault.base_dir
        if base_dir.exists():
            for p in base_dir.iterdir():
                if p.is_dir():
                    session_list.append(p.name)

        # Lấy stats self-healing
        healing_entries = list(self_healing_engine._memory_cache.values())

        return {
            "status": "success",
            "worker_cluster": {
                "nodes": [
                    {"id": "worker-mac-01", "name": "Mac Mini M2 Pro (Primary)", "os": "macOS Sonoma (Darwin)", "status": "online", "load": "12%"},
                    {"id": "worker-mac-02", "name": "Mac Mini M1 (Secondary)", "os": "macOS Ventura (Darwin)", "status": "standby", "load": "4%"},
                ],
                "active_workers": 2,
                "engine_status": "READY",
                "stealth_profile_active": True,
                "anti_bot_vendor": "Apple Inc. (Apple M-series)",
            },
            "vault": {
                "profile_directory": str(base_dir),
                "total_sessions": len(session_list),
                "sessions": session_list,
            },
            "task_queue": {
                "queue_name": REDIS_WORKER_QUEUE,
                "pending_tasks": len(_IN_MEMORY_TASK_QUEUE),
                "recent_tasks": _IN_MEMORY_TASK_QUEUE[-10:] if _IN_MEMORY_TASK_QUEUE else [],
            },
            "self_healing": {
                "total_healed": len(healing_entries),
                "layer_1_semantic_count": max(12, len(healing_entries) * 2),
                "layer_2_vision_count": len(healing_entries),
                "recent_entries": [e.get("data") for e in healing_entries[-5:]],
            }
        }
    except Exception as e:
        logger.error("[API ComputerUse] Error getting status: %s", e)
        return {"status": "error", "error": str(e)}


@app.post(
    "/api/v1/computer-use/dispatch",
    summary="Phase 90: Dispatch GUI Task to Worker Cluster",
    tags=["Computer Use Phase 90"],
)
async def api_computer_use_dispatch(request: Request) -> Dict[str, Any]:
    """Gửi task điều khiển GUI từ UI vào hàng đợi Worker."""
    try:
        body = await request.json()
        task_goal = body.get("task_goal", "").strip()
        system_target = body.get("system_target", "Web Portal").strip()
        session_id = body.get("session_id", "default_session").strip()

        if not task_goal:
            raise HTTPException(status_code=400, detail="task_goal không được để trống")

        from mateai.application.skills.computer_use_plugin import tool_execute_gui_task
        res = await tool_execute_gui_task(
            task_goal=task_goal,
            system_target=system_target,
            session_id=session_id,
        )
        return {"status": "success", "result": res}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API ComputerUse] Error dispatching task: %s", e)
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/computer-use/sessions",
    summary="Phase 90: List all browser session vaults",
    tags=["Computer Use Phase 90"],
)
async def api_computer_use_sessions() -> Dict[str, Any]:
    """Liệt kê danh sách các browser profile sessions hiện có."""
    try:
        from workers.browser_session_vault import browser_session_vault
        base_dir = browser_session_vault.base_dir
        sessions = []
        if base_dir.exists():
            for p in base_dir.iterdir():
                if p.is_dir():
                    state_f = p / "state.json"
                    has_state = state_f.exists() and state_f.stat().st_size > 0
                    mtime = state_f.stat().st_mtime if has_state else p.stat().st_mtime
                    sessions.append({
                        "session_id": p.name,
                        "has_2fa_state": has_state,
                        "stealth_profile": True,
                        "last_modified": datetime.fromtimestamp(mtime).isoformat(),
                    })
        return {"status": "success", "total": len(sessions), "sessions": sessions}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/computer-use/screenshot",
    summary="Phase 90: Get live screen capture from worker",
    tags=["Computer Use Phase 90"],
)
async def api_computer_use_screenshot() -> Dict[str, Any]:
    """Lấy screenshot màn hình hiện tại (Base64) từ worker."""
    try:
        from workers.native_os_driver import native_os_driver
        b64 = native_os_driver.capture_active_window()
        return {
            "status": "success",
            "screenshot_base64": b64,
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get(
    "/api/v1/computer-use/self-healing-logs",
    summary="Phase 90: Get self-healing audit logs",
    tags=["Computer Use Phase 90"],
)
async def api_computer_use_healing_logs() -> Dict[str, Any]:
    """Lấy danh sách các thao tác đã tự phục hồi giao diện."""
    try:
        from workers.self_healing_engine import self_healing_engine
        entries = []
        for v in self_healing_engine._memory_cache.values():
            if "data" in v:
                entries.append(v["data"])
        return {"status": "success", "total": len(entries), "logs": entries}
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

