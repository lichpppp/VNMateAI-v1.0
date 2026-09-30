"""
agent.py
========
VN-MateAI Client Agent — Lightweight Worker Node for Enterprise RPA.

Responsibilities:
  - Establish persistent WebSocket connection to Master Server (ws://[MASTER_IP]:5843/ws/client).
  - Register worker identity (client_id, hostname, platform, local IP, available skills).
  - Listen for automation task payloads from Master and execute locally via ClientPluginManager.
  - Return execution results back to Master asynchronously.
  - Support hot-installation of new skills sent remotely by Master.
  - Automatically reconnect with exponential backoff upon network interruption.

Phase 20: Auto-reads config.json injected at download time from VN-MateAI Web Portal
         for zero-configuration startup (no --server argument needed).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure agent root directory is in sys.path (standalone deployment)
_AGENT_ROOT = Path(__file__).resolve().parent
if str(_AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT))
if str(_AGENT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_AGENT_ROOT.parent))

# ---------------------------------------------------------------------------
# Phase 20: Load injected config.json for zero-config deployment
# ---------------------------------------------------------------------------
_CONFIG_PATH = _AGENT_ROOT / "config.json"


def _load_agent_config() -> dict:
    """Load config.json injected by the Master Server at download time."""
    if _CONFIG_PATH.exists():
        try:
            data = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


_INJECTED_CONFIG: dict = _load_agent_config()

# ---------------------------------------------------------------------------
# Phase 29: End-to-End SSL/TLS Certificate Pinning & Secure Agent
# ---------------------------------------------------------------------------
import ssl

_CERT_FILE_NAME = "server_cert.pem"
_CERT_PATH = _AGENT_ROOT / _CERT_FILE_NAME


def get_server_cert_path() -> Optional[str]:
    """
    Resolve path to server_cert.pem.
    Checks:
      1. Agent directory (alongside agent.py)
      2. Parent directory / certs directory (development fallback)
    """
    if _CERT_PATH.exists() and _CERT_PATH.stat().st_size > 0:
        return str(_CERT_PATH)
    dev_cert = _AGENT_ROOT.parent / "certs" / "server.crt"
    if dev_cert.exists() and dev_cert.stat().st_size > 0:
        return str(dev_cert)
    return None


def get_ssl_context() -> Optional[ssl.SSLContext]:
    """
    Build SSLContext enforcing strict certificate validation using pinned server_cert.pem.
    Strictly enforces ssl.CERT_REQUIRED to guarantee anti-MITM protection.
    Never uses cert_reqs=ssl.CERT_NONE or verify=False.
    """
    cert_path = get_server_cert_path()
    if cert_path:
        ctx = ssl.create_default_context(cafile=cert_path)
        ctx.check_hostname = False  # Allows IP-based or custom local domain connection
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx
    # Fallback to default CA bundle (still CERT_REQUIRED)
    return ssl.create_default_context()


def get_websocket_sslopt() -> dict:
    """
    Returns sslopt dictionary for websocket-client (websocket.WebSocketApp).
    Strictly verifies server certificate against pinned server_cert.pem.
    """
    cert_path = get_server_cert_path()
    if cert_path:
        return {
            "ca_certs": cert_path,
            "cert_reqs": ssl.CERT_REQUIRED,
            "check_hostname": False,
        }
    return {"cert_reqs": ssl.CERT_REQUIRED}


def http_request(method: str, url: str, **kwargs) -> Any:
    """
    Secure HTTP helper using requests (or httpx) with server_cert.pem verification.
    Ensures verify='server_cert.pem' is passed for HTTPS.
    """
    cert_path = get_server_cert_path()
    if cert_path and url.startswith("https://"):
        kwargs["verify"] = cert_path

    try:
        import requests
        return requests.request(method, url, **kwargs)
    except ImportError:
        import httpx
        return httpx.request(method, url, **kwargs)

# Setup compatibility alias so skill files importing 'from core.plugin_manager import export_skill' work
try:
    import core.plugin_manager as cpm
except ImportError:
    import client_agent.core.plugin_manager as cpm

import types
if "core" not in sys.modules:
    core_pkg = types.ModuleType("core")
    core_pkg.plugin_manager = cpm
    sys.modules["core"] = core_pkg
    sys.modules["core.plugin_manager"] = cpm
else:
    sys.modules["core.plugin_manager"] = cpm

# Also alias client_agent for skills that import from client_agent
if "client_agent" not in sys.modules:
    ca_pkg = types.ModuleType("client_agent")
    ca_pkg.core = sys.modules.get("core")
    sys.modules["client_agent"] = ca_pkg
    sys.modules["client_agent.core"] = sys.modules.get("core")
    sys.modules["client_agent.core.plugin_manager"] = cpm

try:
    from core.plugin_manager import client_plugin_manager
except ImportError:
    from client_agent.core.plugin_manager import client_plugin_manager

try:
    from skills.monitoring_skills import (
        capture_screen_base64,
        get_active_processes,
        get_network_connections,
        check_peripherals,
        security_audit,
        kill_process,
    )
except ImportError:
    from client_agent.skills.monitoring_skills import (
        capture_screen_base64,
        get_active_processes,
        get_network_connections,
        check_peripherals,
        security_audit,
        kill_process,
    )

import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | [WORKER] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("client_agent")


def get_local_ip() -> str:
    """Detect LAN IP address of this machine."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # Doesn't have to be reachable; just selects outbound interface
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


class ClientAgent:
    """Worker Agent that communicates with VN-MateAI Master Server."""

    def __init__(self, server_url: str, client_id: Optional[str] = None) -> None:
        self.server_url = server_url
        self.hostname = socket.gethostname()
        self.client_id = client_id or self.hostname
        self.ip_address = get_local_ip()
        self.platform_str = f"{platform.system()} {platform.release()} ({platform.machine()})"
        self._running = True
        self._skills_dir = _AGENT_ROOT / "skills"
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        # Phase 29: Prepare pinned SSL context for secure WSS/HTTPS
        self.ssl_context: Optional[ssl.SSLContext] = None
        if self.server_url.startswith("wss://") or self.server_url.startswith("https://"):
            self.ssl_context = get_ssl_context()

    def init_skills(self) -> int:
        """Load local skills into memory."""
        count = client_plugin_manager.load_plugins()
        logger.info("Đã nạp sẵn %d kỹ năng trên máy con [%s].", count, self.client_id)
        return count

    async def run(self) -> None:
        """Main loop maintaining persistent connection to Master Server."""
        self.init_skills()
        backoff = 2
        max_backoff = 20

        logger.info("=== VN-MateAI Client Agent khởi động ===")
        logger.info("Client ID    : %s", self.client_id)
        logger.info("IP Máy Con   : %s", self.ip_address)
        logger.info("Máy Chủ Đích : %s", self.server_url)

        while self._running:
            try:
                logger.info("Đang kết nối tới Máy Chủ Master: %s ...", self.server_url)
                ws_connect_kwargs: Dict[str, Any] = {
                    "ping_interval": 20,
                    "ping_timeout": 15,
                    "max_size": 10 * 1024 * 1024,  # 10MB max payload
                }
                if self.server_url.startswith("wss://"):
                    ws_connect_kwargs["ssl"] = self.ssl_context or get_ssl_context()

                async with websockets.connect(
                    self.server_url,
                    **ws_connect_kwargs,
                ) as ws:
                    logger.info("✅ Đã kết nối thành công tới Master Server!")
                    backoff = 2  # Reset backoff upon successful connection

                    # 1. Gửi bản tin đăng ký (Handshake)
                    handshake = {
                        "action": "register",
                        "client_id": self.client_id,
                        "hostname": self.hostname,
                        "platform": self.platform_str,
                        "ip": self.ip_address,
                        "skills": client_plugin_manager.get_skill_names(),
                        "status": "online",
                    }
                    await ws.send(json.dumps(handshake, ensure_ascii=False))
                    logger.info("Đã gửi bản tin xác thực danh tính [%s] tới Master.", self.client_id)

                    # 2. Lắng nghe và xử lý tác vụ từ Master
                    async for raw_message in ws:
                        try:
                            data = json.loads(raw_message)
                        except json.JSONDecodeError:
                            logger.warning("Nhận dữ liệu không phải JSON: %s", raw_message[:100])
                            continue

                        await self._dispatch_message(ws, data)

            except (websockets.exceptions.ConnectionClosedError, websockets.exceptions.ConnectionClosedOK) as e:
                logger.warning("Mất kết nối tới Master Server: %s. Sẽ thử lại sau %ds...", e, backoff)
            except Exception as e:
                logger.error("Lỗi kết nối tới Master Server: %s. Thử lại sau %ds...", e, backoff)

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)

    async def _dispatch_message(self, ws: websockets.WebSocketClientProtocol, data: Dict[str, Any]) -> None:
        """Route incoming server instructions."""
        action = data.get("action")
        task_id = data.get("task_id", "")

        # ---- Yêu cầu 1: Thực thi kỹ năng automation / Native File System (Phase 38) ----
        if action in ("execute", "execute_skill", "file_system", "file_io"):
            skill_name = data.get("skill_name") or data.get("skill") or data.get("command", "")
            args = data.get("args") or data.get("parameters") or {}
            logger.info("Nhận lệnh thực thi: skill='%s', args=%s, task_id=%s", skill_name, args, task_id)

            # Chạy skill trong threadpool để không block asyncio loop
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, client_plugin_manager.execute_skill, skill_name, args)
            if result.get("status") == "error" and "Không tìm thấy kỹ năng" in str(result.get("error", "")):
                if skill_name in ("list_directory", "read_file", "write_file", "delete_item"):
                    try:
                        from client_template.skills import file_system
                        fn = getattr(file_system, skill_name, None)
                        if fn:
                            result = await loop.run_in_executor(None, lambda: fn(**args))
                    except Exception as fs_err:
                        result = {"status": "error", "error": str(fs_err)}

            response = {
                "action": "result",
                "task_id": task_id,
                "client_id": self.client_id,
                "success": bool(result.get("status") == "success" or result.get("success") is True or not result.get("error")),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã hoàn thành và gửi kết quả task [%s] về Master.", task_id)

        # ---- Yêu cầu 2: Cài đặt kỹ năng mới từ xa ----
        elif action == "install_skill":
            filename = data.get("filename", "")
            code = data.get("code", "")
            logger.info("Nhận kỹ năng mới từ Master: '%s' (%d bytes)", filename, len(code))

            success = False
            err_msg = None
            try:
                if not filename.endswith(".py"):
                    filename += ".py"
                target_file = self._skills_dir / filename
                target_file.write_text(code, encoding="utf-8")
                # Hot reload
                total = client_plugin_manager.load_plugins()
                success = True
                logger.info("Đã cài đặt và nạp nóng kỹ năng '%s'. Tổng kỹ năng: %d", filename, total)
            except Exception as exc:
                err_msg = str(exc)
                logger.error("Lỗi cài đặt kỹ năng '%s': %s", filename, exc)

            response = {
                "action": "install_result",
                "task_id": task_id,
                "client_id": self.client_id,
                "filename": filename,
                "success": success,
                "error": err_msg,
                "skills": client_plugin_manager.get_skill_names(),
            }
            await ws.send(json.dumps(response, ensure_ascii=False))

        # ---- Yêu cầu 3: Ping kiểm tra nhịp tim ----
        elif action == "ping":
            pong = {
                "action": "pong",
                "client_id": self.client_id,
                "timestamp": time.time(),
            }
            await ws.send(json.dumps(pong))

        # ---- Yêu cầu 4: Giám sát chuyên sâu (Deep Endpoint Monitoring) ----
        elif action == "monitor":
            monitor_type = data.get("type", "")
            args = data.get("args", {})
            logger.info("Nhận yêu cầu giám sát: type='%s', task_id=%s", monitor_type, task_id)

            loop = asyncio.get_event_loop()
            if monitor_type == "screen":
                quality = int(args.get("quality", 65))
                max_width = int(args.get("max_width", 1280))
                result = await loop.run_in_executor(None, capture_screen_base64, quality, max_width)
            elif monitor_type == "processes":
                limit = int(args.get("limit", 15))
                sort_by = str(args.get("sort_by", "cpu"))
                result = await loop.run_in_executor(None, get_active_processes, limit, sort_by)
            elif monitor_type == "network":
                limit = int(args.get("limit", 30))
                result = await loop.run_in_executor(None, get_network_connections, limit)
            elif monitor_type == "peripherals":
                result = await loop.run_in_executor(None, check_peripherals)
            elif monitor_type == "security":
                result = await loop.run_in_executor(None, security_audit)
            else:
                result = {"status": "error", "message": f"Loại giám sát '{monitor_type}' không được hỗ trợ."}

            response = {
                "action": "monitor_result",
                "task_id": task_id,
                "type": monitor_type,
                "client_id": self.client_id,
                "success": result.get("status") in ("success", "partial_success"),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã gửi dữ liệu giám sát [%s:%s] về Master.", monitor_type, task_id)

        # ---- Yêu cầu 5: Tắt tiến trình (Kill Process) ----
        elif action == "kill_process":
            pid = data.get("pid")
            logger.info("Nhận yêu cầu tắt tiến trình PID %s từ Master (task_id: %s)", pid, task_id)
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, kill_process, pid)
            response = {
                "action": "kill_result",
                "task_id": task_id,
                "client_id": self.client_id,
                "pid": pid,
                "success": bool(result.get("status") == "success" or result.get("success") is True or not result.get("error")),
                "result": result,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            logger.info("Đã hoàn thành lệnh tắt tiến trình PID %s và phản hồi về Master.", pid)

        # ---- Yêu cầu 6: Cửa sổ nhắc việc / Micro-Tasking Popup (Phase 11) ----
        elif action == "task_popup":
            message = data.get("message", "")
            sender = data.get("sender", "Ban Giám Đốc")
            logger.info("Nhận lệnh nhắc việc dạng Popup từ [%s]: '%s' (task_id: %s)", sender, message, task_id)
            asyncio.create_task(self._spawn_task_popup(ws, task_id, message, sender))

        # ---- Yêu cầu 7: Giao diện thị giác Ly Ly / Visual Overlay (Phase 32) ----
        elif action == "show_visual":
            visual_type = data.get("type", "metric_chart")
            visual_data = data.get("data", {})
            title = data.get("title", "TRỢ LÝ AI LY LY — HUD")
            duration = int(data.get("duration", 15))
            logger.info("Nhận lệnh hiển thị Visual Overlay [%s]: '%s' (task_id: %s)", visual_type, title, task_id)
            asyncio.create_task(self._spawn_visual_overlay(ws, task_id, visual_type, visual_data, title, duration))

        else:
            logger.debug("Bỏ qua thông điệp không xác định: %s", action)

    async def _spawn_task_popup(
        self,
        ws: websockets.WebSocketClientProtocol,
        task_id: str,
        message: str,
        sender: str,
    ) -> None:
        """
        Spawn standalone Tkinter popup in its own process.
        Ensures main-thread GUI compatibility across macOS Cocoa & Windows,
        runs asynchronously without blocking event loop, and returns user response.
        """
        popup_script = _AGENT_ROOT / "popup_ui.py"
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(popup_script),
                task_id,
                message,
                sender,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            status = stdout.decode().strip() or "completed"
            logger.info("Nhân viên đã phản hồi task [%s]: %s", task_id, status)

            response_payload = {
                "action": "task_response",
                "task_id": task_id,
                "client_id": self.client_id,
                "status": status,
                "message": message,
                "timestamp": time.time(),
            }
            await ws.send(json.dumps(response_payload, ensure_ascii=False))
            logger.info("Đã gửi phản hồi task_response [%s] về Master.", task_id)

        except Exception as exc:
            logger.error("Lỗi khi mở giao diện Popup trên máy con: %s", exc)
            response_payload = {
                "action": "task_response",
                "task_id": task_id,
                "client_id": self.client_id,
                "status": "issue",
                "error": str(exc),
            }
            await ws.send(json.dumps(response_payload, ensure_ascii=False))

    async def _spawn_visual_overlay(
        self,
        ws: websockets.WebSocketClientProtocol,
        task_id: Optional[str],
        visual_type: str,
        data: Dict[str, Any],
        title: str = "TRỢ LÝ AI LY LY — HUD",
        duration: int = 15,
    ) -> None:
        """
        Phase 32: Spawn standalone VN-MateAI Visual Overlay in its own process.
        Non-blocking, non-stealing-focus Cyberpunk HUD window.
        """
        overlay_script = _AGENT_ROOT / "overlay_ui.py"
        payload_json = json.dumps({
            "type": visual_type,
            "title": title,
            "data": data,
            "duration": duration,
        }, ensure_ascii=False)

        import tempfile
        try:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
            json.dump({
                "type": visual_type,
                "title": title,
                "data": data,
                "duration": duration,
            }, tmp, ensure_ascii=False)
            tmp.close()

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(overlay_script),
                tmp.name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            logger.info("Visual Overlay HUD [%s] đã kích hoạt hiển thị (PID: %s).", visual_type, proc.pid)

            if task_id:
                response = {
                    "action": "visual_result",
                    "task_id": task_id,
                    "client_id": self.client_id,
                    "type": visual_type,
                    "status": "displayed",
                }
                await ws.send(json.dumps(response, ensure_ascii=False))

        except Exception as exc:
            logger.error("Lỗi khi mở giao diện Visual Overlay trên máy con: %s", exc)
            if task_id:
                response = {
                    "action": "visual_result",
                    "task_id": task_id,
                    "client_id": self.client_id,
                    "type": visual_type,
                    "status": "error",
                    "error": str(exc),
                }
                await ws.send(json.dumps(response, ensure_ascii=False))


def main() -> None:
    # Phase 20 & 29: Default values from injected config.json (if available)
    _injected_ws = _INJECTED_CONFIG.get("ws_url", "")
    _injected_id = _INJECTED_CONFIG.get("client_id", "")

    # Phase 85: fallback cũ ghi cứng "wss://127.0.0.1:443/ws/client".
    #
    # Hai lỗi trong một chuỗi: máy chủ mặc định chạy HTTP cổng 8000 (không có
    # gì lắng nghe 443), và `wss://` cần TLS nên không bắt tay được với máy
    # chủ HTTP. Người dùng chạy agent không có config.json vẫn thấy agent cố
    # nối tới một địa chỉ không tồn tại rồi lặp lại mãi.
    #
    # Nay mặc định là `ws://127.0.0.1:8000` — khớp với cách máy chủ được
    # khởi chạy. Muốn nối máy chủ khác thì truyền `--server` hoặc đặt
    # VNMATE_MASTER_URL (gói tải từ nút "Tải Agent" đã điền sẵn cả hai).
    _default_server = (
        _injected_ws
        or os.getenv("VNMATE_MASTER_URL", "ws://127.0.0.1:8000/ws/client")
    )

    # Zero-Trust: Master Server yêu cầu enrollment token cho /ws/client.
    # Token do Master phát trong config.json lúc tải agent; có thể override bằng
    # biến môi trường VNMATE_ENROLLMENT_TOKEN.
    _enrollment_token = (
        os.getenv("VNMATE_ENROLLMENT_TOKEN", "")
        or _INJECTED_CONFIG.get("enrollment_token", "")
    )
    if _enrollment_token and "token=" not in _default_server:
        _sep = "&" if "?" in _default_server else "?"
        _default_server = f"{_default_server}{_sep}token={_enrollment_token}"

    _default_id = (
        (None if _injected_id in ("", "auto_generate_on_first_run") else _injected_id)
        or os.getenv("VNMATE_CLIENT_ID", socket.gethostname())
    )

    parser = argparse.ArgumentParser(description="VN-MateAI Client Agent (Worker Node)")
    parser.add_argument(
        "--server", "-s",
        default=_default_server,
        help=f"WebSocket URL của Master Server (mặc định từ config.json: {_default_server})",
    )
    parser.add_argument(
        "--id", "-i",
        default=_default_id,
        help=f"Định danh Client ID cho máy con (mặc định: {_default_id})",
    )
    args = parser.parse_args()

    cert_path = get_server_cert_path()
    cert_status = f"✅ Pinned ({cert_path})" if cert_path else "⚠️ Chế độ mặc định (Không có server_cert.pem)"

    # Print banner with connection info
    print("=" * 60)
    print("  VN-MateAI Client Agent — Secure Worker Node (Phase 29)")
    print(f"  Master Server : {args.server}")
    print(f"  Client ID     : {args.id or socket.gethostname()}")
    print(f"  SSL/TLS Cert  : {cert_status}")
    print("=" * 60)

    agent = ClientAgent(server_url=args.server, client_id=args.id or socket.gethostname())
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        logger.info("Client Agent dừng theo yêu cầu người dùng (Ctrl+C).")


if __name__ == "__main__":
    main()
