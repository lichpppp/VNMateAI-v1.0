"""
core/auth_manager.py
====================
Hệ thống Xác Thực & Phân Quyền (Auth & RBAC) cho VN-MateAI Web Portal.

Quản lý danh sách người dùng, băm mật khẩu, sinh/giải mã JWT token,
và cung cấp các FastAPI Dependencies để bảo vệ REST API theo vai trò (RBAC).
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
import jwt
from fastapi import Depends, HTTPException, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from passlib.context import CryptContext

from core.db_manager import db_manager

logger = logging.getLogger("core.auth_manager")

# ---------------------------------------------------------------------------
# Cấu hình Token & Mật khẩu
# ---------------------------------------------------------------------------
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 giờ

USERS_FILE = Path(__file__).resolve().parent.parent / "users.json"
JWT_SECRET_FILE = Path(__file__).resolve().parent.parent / "certs" / "jwt_secret.key"


def _load_jwt_secret() -> str:
    """
    Nạp khóa ký JWT theo thứ tự ưu tiên:

      1. Biến môi trường ``VNMATEAI_JWT_SECRET`` (chuẩn cho production).
      2. File ``certs/jwt_secret.key`` — tự sinh ngẫu nhiên ở lần chạy đầu.
      3. Nếu không sinh/lưu được file, dừng khởi động (fail-closed).

    Lý do đổi: trước đây có fallback hardcode
    ``"vnmateai-enterprise-secret-key-2026-auth"`` được commit vào git. Ai đọc được
    mã nguồn cũng tự ký được token admin hợp lệ — phủ nhận hoàn toàn Zero-Trust.
    """
    env_secret = os.getenv("VNMATEAI_JWT_SECRET", "").strip()
    if env_secret:
        return env_secret

    try:
        if JWT_SECRET_FILE.exists():
            existing = JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
            if existing:
                return existing

        JWT_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(48)
        JWT_SECRET_FILE.write_text(generated, encoding="utf-8")
        try:
            os.chmod(JWT_SECRET_FILE, 0o600)
        except OSError:
            pass
        logger.warning(
            "Đã sinh khóa ký JWT mới tại %s. Đặt biến môi trường VNMATEAI_JWT_SECRET "
            "để quản lý tập trung trong production.",
            JWT_SECRET_FILE,
        )
        return generated
    except Exception as exc:
        raise RuntimeError(
            "Không thể nạp/sinh khóa ký JWT. Hãy đặt biến môi trường "
            "VNMATEAI_JWT_SECRET, hoặc đảm bảo thư mục 'certs/' ghi được. "
            f"Chi tiết: {exc}"
        ) from exc


JWT_SECRET_KEY = _load_jwt_secret()

pwd_context = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated="auto")
http_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# AuthManager Class
# ---------------------------------------------------------------------------
class AuthManager:
    """Quản trị danh tính, xác thực và lưu trữ người dùng."""

    def __init__(self, users_path: Path = USERS_FILE) -> None:
        self.users_path = users_path
        self._ensure_users_file()

    def _ensure_users_file(self) -> None:
        """
        Tự động tạo file users.json với 3 tài khoản mặc định nếu chưa tồn tại.

        Zero-Trust: mật khẩu mặc định chỉ dùng cho môi trường dev. Đặt biến môi
        trường sau để tự chọn mật khẩu khi triển khai thật:
          VNMATEAI_DEFAULT_ADMIN_PASSWORD
          VNMATEAI_DEFAULT_MANAGER_PASSWORD
          VNMATEAI_DEFAULT_VIEWER_PASSWORD
        """
        if not self.users_path.exists():
            # Mật khẩu mặc định: ưu tiên biến môi trường, không thì dùng giá trị dev.
            fallback_passwords = {
                "admin": "admin123",
                "manager": "manager123",
                "viewer": "viewer123",
            }
            passwords: Dict[str, str] = {}
            used_fallback: List[str] = []
            for username, fallback in fallback_passwords.items():
                env_value = os.getenv(f"VNMATEAI_DEFAULT_{username.upper()}_PASSWORD", "").strip()
                if env_value:
                    passwords[username] = env_value
                else:
                    passwords[username] = fallback
                    used_fallback.append(username)

            if used_fallback:
                logger.warning(
                    "⚠️  Đang khởi tạo %d tài khoản với mật khẩu MẶC ĐỊNH yếu "
                    "(%s). Chỉ chấp nhận được cho môi trường dev. Trước khi triển khai, "
                    "hãy đặt VNMATEAI_DEFAULT_<ROLE>__PASSWORD hoặc đổi mật khẩu qua "
                    "giao diện quản trị.",
                    len(used_fallback), ", ".join(used_fallback),
                )

            default_users = {
                "admin": {
                    "id": "usr_admin",
                    "username": "admin",
                    "full_name": "Quản Trị Viên Hệ Thống",
                    "role": "admin",
                    "hashed_password": self.get_password_hash(passwords["admin"]),
                    "password_hash": self.get_password_hash(passwords["admin"]),
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                },
                "manager": {
                    "id": "usr_manager",
                    "username": "manager",
                    "full_name": "Quản Lý Vận Hành",
                    "role": "manager",
                    "hashed_password": self.get_password_hash(passwords["manager"]),
                    "password_hash": self.get_password_hash(passwords["manager"]),
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                },
                "viewer": {
                    "id": "usr_viewer",
                    "username": "viewer",
                    "full_name": "Nhân Viên Giám Sát",
                    "role": "viewer",
                    "hashed_password": self.get_password_hash(passwords["viewer"]),
                    "password_hash": self.get_password_hash(passwords["viewer"]),
                    "created_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat(),
                },
            }
            try:
                self.users_path.write_text(
                    json.dumps(default_users, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                # users.json chứa hash mật khẩu — chỉ owner được đọc.
                try:
                    os.chmod(self.users_path, 0o600)
                except OSError:
                    pass
                logger.info("Đã khởi tạo file users.json với các tài khoản mặc định (admin, manager, viewer).")
            except Exception as exc:
                logger.error("Không thể ghi file users.json: %s", exc)

    def _load_users(self) -> Dict[str, Dict[str, Any]]:
        """Đọc danh sách người dùng từ file users.json và tự động chuẩn hóa schema."""
        if not self.users_path.exists():
            self._ensure_users_file()
        try:
            users = json.loads(self.users_path.read_text(encoding="utf-8"))
            dirty = False
            for uname, udata in users.items():
                if "id" not in udata:
                    udata["id"] = f"usr_{uname}"
                    dirty = True
                if "hashed_password" not in udata and "password_hash" in udata:
                    udata["hashed_password"] = udata["password_hash"]
                    dirty = True
                if "password_hash" not in udata and "hashed_password" in udata:
                    udata["password_hash"] = udata["hashed_password"]
                    dirty = True
                if "role" not in udata:
                    udata["role"] = "viewer"
                    dirty = True
            if dirty:
                self._save_users(users)
            return users
        except Exception as exc:
            logger.error("Lỗi đọc users.json: %s", exc)
            return {}

    def _save_users(self, users: Dict[str, Dict[str, Any]]) -> None:
        """Lưu danh sách người dùng vào file users.json."""
        try:
            self.users_path.write_text(
                json.dumps(users, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("Lỗi lưu users.json: %s", exc)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        """Kiểm tra mật khẩu nhập vào khớp với chuỗi băm hay không (hỗ trợ bcrypt và pbkdf2 legacy)."""
        try:
            if not hashed_password:
                return False
            if hashed_password.startswith(("$2a$", "$2b$", "$2y$")):
                pwd_bytes = plain_password.encode("utf-8")[:72]
                return bcrypt.checkpw(pwd_bytes, hashed_password.encode("utf-8"))
            return pwd_context.verify(plain_password, hashed_password)
        except Exception as exc:
            logger.error("Lỗi xác minh mật khẩu: %s", exc)
            return False

    @staticmethod
    def get_password_hash(password: str) -> str:
        """Tạo chuỗi băm bcrypt an toàn cho mật khẩu."""
        pwd_bytes = password.encode("utf-8")[:72]
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

    def _find_user_entry(self, user_identifier: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        """Tìm user theo ID hoặc Username."""
        users = self._load_users()
        identifier_clean = user_identifier.strip().lower()
        for key, udata in users.items():
            if (
                str(udata.get("id", "")).lower() == identifier_clean
                or str(udata.get("username", "")).lower() == identifier_clean
                or key.lower() == identifier_clean
            ):
                return key, udata
        return None

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Tìm người dùng theo tên đăng nhập hoặc ID từ SQLite (hoặc users.json)."""
        db_user = db_manager.get_user_by_username_or_id(username)
        if db_user:
            return db_user
        found = self._find_user_entry(username)
        return found[1] if found else None

    def authenticate_user(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Xác thực người dùng và mật khẩu."""
        user = self.get_user(username)
        if not user:
            return None
        h = user.get("password_hash") or user.get("hashed_password") or ""
        if not self.verify_password(password, h):
            return None
        return user

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Trả về danh sách người dùng từ SQLite (KHÔNG bao gồm password hash)."""
        users = db_manager.get_all_users()
        if users:
            return users
        # Fallback to users.json if SQLite has no rows yet
        raw_users = self._load_users()
        result = []
        for uname, udata in raw_users.items():
            result.append({
                "id": udata.get("id", f"usr_{uname}"),
                "username": udata.get("username", uname),
                "full_name": udata.get("full_name", uname),
                "role": udata.get("role", "viewer"),
                "created_at": udata.get("created_at", ""),
                "updated_at": udata.get("updated_at", ""),
            })
        return result

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Mã hóa mật khẩu bằng bcrypt và lưu user mới vào SQLite và users.json.
        Phát sinh lỗi nếu username đã tồn tại hoặc mật khẩu không hợp lệ.
        """
        created = db_manager.create_user(data)
        # Đồng bộ vào users.json
        try:
            users = self._load_users()
            u_entry = db_manager.get_user_by_username_or_id(created["username"])
            if u_entry:
                users[created["username"]] = {
                    "id": u_entry["id"],
                    "username": u_entry["username"],
                    "full_name": u_entry["full_name"],
                    "role": u_entry["role"],
                    "hashed_password": u_entry["password_hash"],
                    "password_hash": u_entry["password_hash"],
                    "created_at": u_entry["created_at"],
                    "updated_at": u_entry["updated_at"],
                }
                self._save_users(users)
        except Exception as exc:
            logger.warning("Không thể đồng bộ user mới vào users.json: %s", exc)

        logger.info("Đã tạo người dùng mới: %s (role: %s, id: %s)", created["username"], created["role"], created["id"])
        return created

    def update_user(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Cập nhật full_name và role của user theo user_id trong SQLite và users.json."""
        updated = db_manager.update_user(user_id, data)
        # Đồng bộ vào users.json
        try:
            found = self._find_user_entry(user_id)
            if found:
                key, _ = found
                users = self._load_users()
                users[key]["full_name"] = updated.get("full_name", users[key].get("full_name"))
                users[key]["role"] = updated.get("role", users[key].get("role"))
                users[key]["updated_at"] = updated.get("updated_at", datetime.utcnow().isoformat())
                self._save_users(users)
        except Exception as exc:
            logger.warning("Không thể đồng bộ cập nhật user vào users.json: %s", exc)

        logger.info("Đã cập nhật người dùng: %s (id: %s)", updated.get("username"), user_id)
        return updated

    def delete_user(self, user_id: str) -> bool:
        """Xóa user theo user_id hoặc username khỏi SQLite và users.json."""
        db_manager.delete_user(user_id)
        # Đồng bộ xóa trong users.json
        try:
            found = self._find_user_entry(user_id)
            if found:
                key, _ = found
                users = self._load_users()
                if key in users:
                    del users[key]
                    self._save_users(users)
        except Exception as exc:
            logger.warning("Không thể đồng bộ xóa user trong users.json: %s", exc)

        logger.info("Đã xóa người dùng khỏi hệ thống: %s", user_id)
        return True

    def change_user_password(self, user_id: str, new_password: str) -> bool:
        """Mã hóa mật khẩu mới và lưu vào SQLite và users.json."""
        db_manager.change_user_password(user_id, new_password)
        # Đồng bộ vào users.json
        try:
            found = self._find_user_entry(user_id)
            if found:
                key, _ = found
                users = self._load_users()
                hashed = self.get_password_hash(new_password)
                users[key]["hashed_password"] = hashed
                users[key]["password_hash"] = hashed
                users[key]["updated_at"] = datetime.utcnow().isoformat()
                self._save_users(users)
        except Exception as exc:
            logger.warning("Không thể đồng bộ đổi mật khẩu vào users.json: %s", exc)

        logger.info("Đã đổi mật khẩu cho người dùng: %s", user_id)
        return True

    @staticmethod
    def create_access_token(
        data: Dict[str, Any],
        expires_delta: Optional[timedelta] = None,
    ) -> str:
        """Sinh JWT token có hạn sử dụng và chứa claim người dùng."""
        to_encode = data.copy()
        now = datetime.utcnow()
        if expires_delta:
            expire = now + expires_delta
        else:
            expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        to_encode.update({"exp": expire, "iat": now})
        return jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

    @staticmethod
    def decode_access_token(token: str) -> Optional[Dict[str, Any]]:
        """Giải mã và xác thực tính hợp lệ của JWT token."""
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("JWT token đã hết hạn sử dụng.")
            return None
        except jwt.InvalidTokenError as exc:
            logger.warning("JWT token không hợp lệ: %s", exc)
            return None


auth_manager = AuthManager()


# ---------------------------------------------------------------------------
# FastAPI Security Dependencies
# ---------------------------------------------------------------------------
async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
    token_query: Optional[str] = Query(default=None, alias="token"),
) -> Dict[str, Any]:
    """
    FastAPI dependency trích xuất và kiểm tra người dùng hiện tại từ:
    1. Header Authorization: Bearer <token>
    2. Query param ?token=<token> (hỗ trợ phát audio trực tiếp hoặc xem tệp)

    Zero-Trust: KHÔNG có bất kỳ fallback nào. Mọi request đều phải mang JWT hợp lệ.
    (Trước đây từng tự cấp quyền admin cho localhost và cho Referer chứa "/hud" —
     cả hai đều dễ bị giả mạo và đã bị gỡ bỏ.)
    """
    raw_token = None
    if credentials and credentials.credentials:
        raw_token = credentials.credentials
    elif token_query:
        raw_token = token_query

    if not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực tài khoản (Thiếu Bearer Token).",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = auth_manager.decode_access_token(raw_token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập không hợp lệ hoặc đã hết hạn.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload.get("sub")
    user = auth_manager.get_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản người dùng không tồn tại.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Trả về thông tin an toàn (bỏ password_hash)
    return {
        "id": user.get("id", f"usr_{user['username']}"),
        "username": user["username"],
        "full_name": user.get("full_name", user["username"]),
        "role": user.get("role", "viewer"),
        "created_at": user.get("created_at"),
    }


def require_roles(allowed_roles: List[str]):
    """
    Dependency factory kiểm tra quyền của người dùng (RBAC).
    Ví dụ: Depends(require_roles(["admin", "manager"]))
    """
    async def role_checker(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        user_role = current_user.get("role", "viewer")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Quyền hạn '{user_role}' không được phép thực hiện tác vụ này. Yêu cầu một trong các quyền: {allowed_roles}.",
            )
        return current_user

    return role_checker
