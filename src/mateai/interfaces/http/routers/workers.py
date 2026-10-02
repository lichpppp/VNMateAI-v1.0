"""
mateai/interfaces/http/routers/workers.py
=========================================
Worker node (client agent): bật/tắt worker cục bộ trên máy chủ, đóng gói
"Tải Agent" cho máy trạm, và địa chỉ máy chủ mà worker phải nối tới.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import socket as _socket
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, JSONResponse

from mateai.config.loader import settings
from mateai.interfaces.http import enrollment
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()

_PROJECT_ROOT = Path(settings.PROJECT_ROOT)
# Gói "Tải Agent" đóng từ chính client_agent/ (Phase 6: gỡ bản fork client_template/,
# hai bản đã lệch nhau — mỗi bên sửa một lỗi mà bên kia vẫn còn).
_CLIENT_AGENT_DIR = _PROJECT_ROOT / "client_agent"


_local_worker_process: Optional[subprocess.Popen] = None


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


@router.get(
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


@router.post(
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


@router.get(
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
        zf.writestr("config.json", json.dumps(dynamic_config, indent=4, ensure_ascii=False))

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
