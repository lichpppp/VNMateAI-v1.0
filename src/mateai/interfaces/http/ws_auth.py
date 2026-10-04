"""
mateai/interfaces/http/ws_auth.py
=================================
Xác thực kết nối WebSocket:
  - thiết bị ESP32/Xiaozhi (token riêng → token chung → JWT admin/manager),
  - LAN worker (/ws/client: enrollment secret hoặc JWT admin/manager),
  - người dùng trình duyệt (JWT qua ``?token=``).

Gọi qua module (`ws_auth.authenticate_websocket(ws)`) để test thay một chỗ.
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

from fastapi import WebSocket

from mateai.application.security.auth_manager import auth_manager
from mateai.interfaces.http import enrollment

logger = logging.getLogger(__name__)


# Role mà JWT được dùng THAY token thiết bị / enrollment secret (debug thủ công).
# Không phải quyền duyệt tác vụ — duyệt chỉ admin (routers/security.py).
JWT_FALLBACK_ROLES = ("admin", "manager")


def authenticate_device(websocket: WebSocket, device_id: str = "esp32-default") -> bool:
    """Thiết bị được phép kết nối không (xem `device_auth_method`)."""
    return device_auth_method(websocket, device_id) is not None


#: Cách thiết bị xác thực — chỉ "device_token" (token RIÊNG gắn với đúng device_id)
#: mới chứng minh được danh tính thiết bị; quyền riêng của thiết bị chỉ áp dụng khi đó.
DEVICE_TOKEN = "device_token"
SHARED_SECRET = "shared_secret"
USER_JWT = "jwt"


def device_auth_method(websocket: WebSocket, device_id: str = "esp32-default") -> Optional[str]:
    """
    Xác thực thiết bị ESP32 trước khi cho stream âm thanh. Trả cách xác thực
    (DEVICE_TOKEN / SHARED_SECRET / USER_JWT) hoặc None nếu bị từ chối.

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
                return DEVICE_TOKEN
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[Xiaozhi] Lỗi kiểm token thiết bị '%s': %s", device_id, exc)

        expected = enrollment.get_device_enrollment_secret()
        if expected and secrets.compare_digest(token, expected):
            from mateai.config.loader import get_config_section
            if get_config_section("security").get("require_per_device_token", False):
                logger.warning("[Xiaozhi] Từ chối '%s': token dùng chung đã bị tắt "
                               "(security.require_per_device_token).", device_id)
                return None
            logger.warning("[Xiaozhi] Thiết bị '%s' dùng token CHUNG — hãy cấp token riêng "
                           "(POST /api/v1/security/devices).", device_id)
            return SHARED_SECRET

        try:
            payload = auth_manager.decode_access_token(token)
            if payload and "sub" in payload:
                user = auth_manager.get_user(payload["sub"])
                if bool(user) and user.get("role") in JWT_FALLBACK_ROLES:
                    return USER_JWT
        except Exception:
            pass

    return None


def authenticate_worker(websocket: WebSocket) -> bool:
    """
    Xác thực một LAN worker trước khi cho đăng ký vào /ws/client.

    Chấp nhận một trong hai:
      1. Enrollment secret do Master Server phát lúc tải agent.
      2. JWT của tài khoản admin/manager (tiện cho việc debug thủ công).

    Trả về True nếu hợp lệ.
    """
    return is_valid_worker_token(websocket.query_params.get("token") or "")


def is_valid_worker_token(token: str) -> bool:
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
    return bool(user) and user.get("role") in JWT_FALLBACK_ROLES


def authenticate_websocket(websocket: WebSocket) -> Optional[dict]:
    """
    Xác thực WebSocket bằng JWT truyền qua query param ``?token=``.

    Trả về dict thông tin người dùng đã xác thực, hoặc None nếu thiếu/sai token.

    Lưu ý: browser WebSocket API không cho gắn header Authorization, nên query param
    là cách duy nhất phía client. Đổi lại token có thể lọt vào access log — vì vậy các
    endpoint nhạy cảm nên kiểm tra thêm role (xem JWT_FALLBACK_ROLES).
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
