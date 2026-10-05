"""
core/db_manager.py
==================
Quản lý cơ sở dữ liệu SQLite trung tâm (vnmateai.db) cho VN-MateAI.

Tính năng:
  - Tự động tạo bảng (CREATE TABLE IF NOT EXISTS) cho `users` và `tasks` khi khởi động.
  - Tự động tạo tài khoản quản trị viên mặc định 'admin' (mật khẩu bcrypt 'admin123').
  - Hỗ trợ đồng bộ hai chiều với `users.json` và `logs/kpi_logs.csv` để đảm bảo tương thích 100%.
  - Cung cấp API truy vấn dữ liệu thực tế (CRUD User, Task KPI, System Hardware Stats).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import bcrypt
import psutil

from mateai.infrastructure.database.erp_database import ensure_tasks_table, open_sqlite

logger = logging.getLogger("mateai.infrastructure.database.db_manager")

# Thư mục gốc dự án (đúng cả bản đóng gói) — KHÔNG suy từ vị trí file mã nguồn:
# chuyển module mà đường dẫn lệch là máy chủ mở một CSDL rỗng mới.
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
DB_PATH = Path(os.environ.get("VNMATEAI_DB_PATH") or _PROJECT_ROOT / "vnmateai.db")
USERS_JSON_PATH = _PROJECT_ROOT / "users.json"
KPI_CSV_PATH = _PROJECT_ROOT / "logs" / "kpi_logs.csv"


class DatabaseManager:
    """Quản lý kết nối và thao tác dữ liệu trên SQLite database."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self.init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite với Row factory để dễ dàng truy xuất cột dạng dict."""
        return open_sqlite(self.db_path, timeout=20.0)

    def init_db(self) -> None:
        """Tự động tạo bảng và dữ liệu khởi tạo ban đầu nếu chưa tồn tại."""
        with self._lock:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                with self._get_connection() as conn:
                    cursor = conn.cursor()

                    # 1. Bảng users
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS users (
                            id TEXT PRIMARY KEY,
                            username TEXT UNIQUE NOT NULL,
                            full_name TEXT NOT NULL,
                            role TEXT NOT NULL DEFAULT 'viewer',
                            password_hash TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL
                        );
                        """
                    )
                    cursor.execute(
                        "CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);"
                    )

                    # 2. Bảng tasks — schema chung, định nghĩa ở mateai.infrastructure.database.erp_database.
                    ensure_tasks_table(cursor)

                    # 3. Token riêng của từng thiết bị IoT — chỉ lưu SHA-256.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS device_tokens (
                            device_id TEXT PRIMARY KEY,
                            token_sha256 TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            created_by TEXT,
                            last_seen_at TEXT
                        );
                        """
                    )
                    # Quyền riêng của thiết bị (NULL = chưa đặt). Thêm cột cho DB cũ.
                    _cols = {r[1] for r in cursor.execute("PRAGMA table_info(device_tokens);").fetchall()}
                    if "role" not in _cols:
                        cursor.execute("ALTER TABLE device_tokens ADD COLUMN role TEXT;")

                    # 5. Máy trạm (Agent): mã đăng ký DÙNG MỘT LẦN + khoá riêng từng máy.
                    #    Chỉ lưu SHA-256; thu hồi từng máy (revoked_at) mà không ảnh hưởng máy khác.
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS worker_enroll_codes (
                            code_sha256 TEXT PRIMARY KEY,
                            label TEXT,
                            created_by TEXT,
                            created_at TEXT NOT NULL,
                            expires_at TEXT NOT NULL,
                            used_at TEXT,
                            used_by_client TEXT
                        );
                        """
                    )
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS worker_devices (
                            client_id TEXT PRIMARY KEY,
                            token_sha256 TEXT NOT NULL UNIQUE,
                            label TEXT,
                            hostname TEXT,
                            platform TEXT,
                            package TEXT,
                            agent_version TEXT,
                            enrolled_at TEXT NOT NULL,
                            enrolled_by TEXT,
                            last_seen_at TEXT,
                            revoked_at TEXT,
                            revoked_by TEXT
                        );
                        """
                    )

                    # 4. Phê duyệt đã nhớ: thiết bị đã được duyệt tác vụ X một lần thì
                    #    lần sau không hỏi lại (chủ hệ thống chọn, thu hồi ở Web Portal).
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS approval_grants (
                            principal TEXT NOT NULL,
                            tool_name TEXT NOT NULL,
                            granted_by TEXT,
                            granted_at TEXT NOT NULL,
                            PRIMARY KEY (principal, tool_name)
                        );
                        """
                    )

                    conn.commit()

                # Bảng users là kho tài khoản DUY NHẤT. Chỉ khi bảng rỗng (lần
                # đầu): nhập từ users.json cũ trước — giữ mật khẩu người dùng đã
                # đặt — rồi mới tạo tài khoản mặc định nếu vẫn rỗng. Thứ tự cũ
                # (tạo mặc định trước) làm mật khẩu riêng trong users.json bị
                # thay bằng admin123; nhập lại MỖI lần khởi động thì tài khoản đã
                # xoá sống lại.
                if self._user_count() == 0:
                    self._import_users_json_once()
                if self._user_count() == 0:
                    self._seed_default_users()
                logger.info("Khởi tạo cơ sở dữ liệu SQLite thành công tại: %s", self.db_path)
            except Exception as exc:
                logger.error("Lỗi khởi tạo SQLite database: %s", exc)

    @staticmethod
    def hash_password(password: str) -> str:
        """Tạo chuỗi băm bcrypt an toàn."""
        pwd_bytes = password.encode("utf-8")[:72]
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(pwd_bytes, salt).decode("utf-8")

    def _user_count(self) -> int:
        with self._get_connection() as conn:
            return conn.execute("SELECT COUNT(*) FROM users;").fetchone()[0]

    def _seed_default_users(self) -> None:
        """
        Tạo 3 tài khoản mặc định khi bảng users rỗng.

        Mật khẩu: biến môi trường VNMATEAI_DEFAULT_<ROLE>_PASSWORD, không có thì
        giá trị dev (kèm cảnh báo). Trước đây gán cứng admin123/... nên đặt biến
        môi trường khi triển khai không có tác dụng.
        """
        fallback = {"admin": "admin123", "manager": "manager123", "viewer": "viewer123"}
        names = {"admin": "Quản Trị Viên Hệ Thống", "manager": "Quản Lý Vận Hành",
                 "viewer": "Nhân Viên Giám Sát"}
        used_fallback = []
        now_str = datetime.utcnow().isoformat()
        rows = []
        for role, dev_pwd in fallback.items():
            pwd = os.getenv(f"VNMATEAI_DEFAULT_{role.upper()}_PASSWORD", "").strip()
            if not pwd:
                pwd = dev_pwd
                used_fallback.append(role)
            rows.append((f"usr_{role}", role, names[role], role, self.hash_password(pwd), now_str, now_str))
        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                rows,
            )
            conn.commit()
        if used_fallback:
            logger.warning(
                "Đã tạo tài khoản với mật khẩu MẶC ĐỊNH yếu (%s) — chỉ dùng cho dev. Đặt "
                "VNMATEAI_DEFAULT_<ROLE>_PASSWORD trước lần khởi động đầu, hoặc đổi mật "
                "khẩu ngay trong trang quản trị.", ", ".join(used_fallback),
            )
        logger.info("Đã khởi tạo 3 tài khoản mặc định trong SQLite: admin, manager, viewer.")

    def _import_users_json_once(self) -> None:
        """
        Di trú một lần từ users.json (kho cũ) khi bảng users còn rỗng. File không
        bị sửa hay xoá. Tài khoản không có hash mật khẩu bị BỎ QUA — trước đây
        được gán mật khẩu "123456".
        """
        if not USERS_JSON_PATH.exists():
            return
        try:
            import json
            data = json.loads(USERS_JSON_PATH.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Không đọc được users.json để di trú: %s", exc)
            return
        now_str = datetime.utcnow().isoformat()
        imported, skipped = [], []
        with self._get_connection() as conn:
            for uname, uinfo in (data or {}).items():
                if not isinstance(uinfo, dict):
                    continue
                username = (uinfo.get("username") or uname).strip().lower()
                pwd_hash = uinfo.get("password_hash") or uinfo.get("hashed_password")
                if not username or not pwd_hash:
                    skipped.append(username or uname)
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (uinfo.get("id") or f"usr_{username}", username, uinfo.get("full_name") or username,
                     uinfo.get("role") or "viewer", pwd_hash,
                     uinfo.get("created_at") or now_str, uinfo.get("updated_at") or now_str),
                )
                imported.append(username)
            conn.commit()
        logger.info("Đã di trú %d tài khoản từ users.json vào SQLite.", len(imported))
        if skipped:
            logger.warning("Bỏ qua tài khoản không có hash mật khẩu trong users.json: %s", ", ".join(skipped))

    # -----------------------------------------------------------------------
    # User CRUD Operations
    # -----------------------------------------------------------------------

    def get_all_users(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách người dùng (KHÔNG trả về password_hash)."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, username, full_name, role, created_at, updated_at
                    FROM users
                    ORDER BY created_at ASC;
                    """
                )
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    def get_user_by_username_or_id(self, identifier: str) -> Optional[Dict[str, Any]]:
        """Tìm người dùng theo username hoặc id."""
        clean_id = identifier.strip().lower()
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT id, username, full_name, role, password_hash, created_at, updated_at
                    FROM users
                    WHERE LOWER(username) = ? OR LOWER(id) = ?;
                    """,
                    (clean_id, clean_id),
                )
                row = cursor.fetchone()
                return dict(row) if row else None

    def create_user(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Tạo người dùng mới trong SQLite."""
        username = str(data.get("username", "")).strip().lower()
        if not username or len(username) < 3:
            raise ValueError("Tên đăng nhập phải có ít nhất 3 ký tự.")

        raw_password = data.get("password") or data.get("plain_password") or ""
        if not raw_password or len(raw_password) < 6:
            raise ValueError("Mật khẩu phải có độ dài tối thiểu 6 ký tự.")

        role = str(data.get("role", "viewer")).strip().lower()
        if role not in ("admin", "manager", "viewer"):
            role = "viewer"

        full_name = str(data.get("full_name", "")).strip() or username
        user_id = data.get("id") or f"usr_{uuid.uuid4().hex[:8]}"
        hashed = self.hash_password(raw_password)
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                # Kiểm tra trùng lặp
                cursor.execute("SELECT id FROM users WHERE username = ?;", (username,))
                if cursor.fetchone():
                    raise ValueError(f"Tên đăng nhập '{username}' đã tồn tại trong hệ thống.")

                cursor.execute(
                    """
                    INSERT INTO users (id, username, full_name, role, password_hash, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?);
                    """,
                    (user_id, username, full_name, role, hashed, now_str, now_str),
                )
                conn.commit()

        logger.info("Đã tạo người dùng mới trong SQLite: %s (%s)", username, role)
        return {
            "id": user_id,
            "username": username,
            "full_name": full_name,
            "role": role,
            "created_at": now_str,
        }

    def update_user(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Cập nhật full_name hoặc role của user."""
        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        full_name = data.get("full_name")
        role = data.get("role")
        now_str = datetime.utcnow().isoformat()

        updates = []
        params = []
        if full_name is not None:
            updates.append("full_name = ?")
            params.append(str(full_name).strip())
        if role is not None:
            r = str(role).strip().lower()
            if r in ("admin", "manager", "viewer"):
                updates.append("role = ?")
                params.append(r)

        if not updates:
            return existing

        updates.append("updated_at = ?")
        params.append(now_str)
        params.append(existing["id"])

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    f"UPDATE users SET {', '.join(updates)} WHERE id = ?;",
                    params,
                )
                conn.commit()

        updated = self.get_user_by_username_or_id(existing["id"])
        return updated or {}

    def delete_user(self, user_id: str) -> None:
        """Xóa tài khoản người dùng."""
        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        if existing.get("username") == "admin":
            raise ValueError("Không thể xóa tài khoản 'admin' mặc định của hệ thống.")

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM users WHERE id = ?;", (existing["id"],))
                conn.commit()
        logger.info("Đã xóa tài khoản user id: %s (%s)", existing["id"], existing["username"])

    def change_user_password(self, user_id: str, new_password: str) -> None:
        """Cấp lại mật khẩu mới cho người dùng."""
        if not new_password or len(new_password) < 6:
            raise ValueError("Mật khẩu mới phải có tối thiểu 6 ký tự.")

        existing = self.get_user_by_username_or_id(user_id)
        if not existing:
            raise ValueError(f"Không tìm thấy người dùng với định danh '{user_id}'.")

        hashed = self.hash_password(new_password)
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?;",
                    (hashed, now_str, existing["id"]),
                )
                conn.commit()
        logger.info("Đã đặt lại mật khẩu cho user: %s", existing["username"])

    # -----------------------------------------------------------------------
    # Task Operations
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Device tokens (một token cho MỘT thiết bị)
    # -----------------------------------------------------------------------

    @staticmethod
    def _sha256(token: str) -> str:
        import hashlib
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue_device_token(self, device_id: str, created_by: str = "") -> str:
        """Cấp (hoặc xoay) token cho device_id. Trả token GỐC — chỉ hiện một lần."""
        import secrets as _secrets
        token = _secrets.token_urlsafe(32)
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO device_tokens (device_id, token_sha256, created_at, created_by, last_seen_at)
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(device_id) DO UPDATE SET
                        token_sha256 = excluded.token_sha256,
                        created_at = excluded.created_at,
                        created_by = excluded.created_by,
                        last_seen_at = NULL;
                    """,
                    (device_id, self._sha256(token), now, created_by),
                )
                conn.commit()
        return token

    def verify_device_token(self, device_id: str, token: str) -> bool:
        """Token khớp với ĐÚNG device_id này (so sánh hằng thời gian trên hash)."""
        import hmac
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT token_sha256 FROM device_tokens WHERE device_id = ?;", (device_id,)
            ).fetchone()
            if not row or not hmac.compare_digest(row["token_sha256"], self._sha256(token)):
                return False
            conn.execute("UPDATE device_tokens SET last_seen_at = ? WHERE device_id = ?;",
                         (datetime.utcnow().isoformat(), device_id))
            conn.commit()
            return True

    def list_device_tokens(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT device_id, created_at, created_by, last_seen_at, role FROM device_tokens ORDER BY device_id;"
            ).fetchall()
            return [dict(r) for r in rows]

    def set_device_role(self, device_id: str, role: Optional[str]) -> bool:
        """Đặt quyền của thiết bị đã có token (None = bỏ, về mặc định). False nếu không có thiết bị."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("UPDATE device_tokens SET role = ? WHERE device_id = ?;", (role, device_id))
                conn.commit()
                return cur.rowcount > 0

    def get_device_role(self, device_id: str) -> Optional[str]:
        with self._get_connection() as conn:
            row = conn.execute("SELECT role FROM device_tokens WHERE device_id = ?;", (device_id,)).fetchone()
            return row["role"] if row and row["role"] else None

    def add_approval_grant(self, principal: str, tool_name: str, granted_by: str = "") -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO approval_grants (principal, tool_name, granted_by, granted_at) "
                    "VALUES (?, ?, ?, ?);",
                    (principal, tool_name, granted_by, datetime.utcnow().isoformat()),
                )
                conn.commit()

    def has_approval_grant(self, principal: str, tool_name: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute(
                "SELECT 1 FROM approval_grants WHERE principal = ? AND tool_name = ?;", (principal, tool_name)
            ).fetchone() is not None

    def list_approval_grants(self, principal: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            if principal:
                rows = conn.execute("SELECT * FROM approval_grants WHERE principal = ? ORDER BY tool_name;",
                                    (principal,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM approval_grants ORDER BY principal, tool_name;").fetchall()
            return [dict(r) for r in rows]

    def revoke_approval_grant(self, principal: str, tool_name: Optional[str] = None) -> int:
        """Thu hồi một phê duyệt đã nhớ (hoặc tất cả của principal khi tool_name=None)."""
        with self._lock:
            with self._get_connection() as conn:
                if tool_name:
                    cur = conn.execute("DELETE FROM approval_grants WHERE principal = ? AND tool_name = ?;",
                                       (principal, tool_name))
                else:
                    cur = conn.execute("DELETE FROM approval_grants WHERE principal = ?;", (principal,))
                conn.commit()
                return cur.rowcount

    # ── Máy trạm: mã đăng ký dùng một lần + khoá riêng từng máy ─────────────

    def add_worker_enroll_code(self, code_sha256: str, label: str, created_by: str, expires_at: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO worker_enroll_codes (code_sha256, label, created_by, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?);",
                    (code_sha256, label, created_by, datetime.utcnow().isoformat(), expires_at),
                )
                conn.commit()

    def consume_worker_enroll_code(self, code_sha256: str, client_id: str) -> Optional[Dict[str, Any]]:
        """Đánh dấu mã đã dùng — NGUYÊN TỬ: chỉ một lần gọi thắng. None nếu sai / hết hạn / đã dùng."""
        now = datetime.utcnow().isoformat()
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE worker_enroll_codes SET used_at = ?, used_by_client = ? "
                    "WHERE code_sha256 = ? AND used_at IS NULL AND expires_at > ?;",
                    (now, client_id, code_sha256, now),
                )
                conn.commit()
                if cur.rowcount != 1:
                    return None
                row = conn.execute("SELECT * FROM worker_enroll_codes WHERE code_sha256 = ?;",
                                   (code_sha256,)).fetchone()
                return dict(row) if row else None

    def list_worker_enroll_codes(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT substr(code_sha256, 1, 12) AS code_ref, label, created_by, created_at, expires_at, "
                "used_at, used_by_client FROM worker_enroll_codes ORDER BY created_at DESC LIMIT 200;"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_worker_enroll_code(self, code_ref: str) -> int:
        """Huỷ mã CHƯA dùng theo 12 ký tự đầu của hash (giao diện không bao giờ thấy mã thật)."""
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "DELETE FROM worker_enroll_codes WHERE substr(code_sha256, 1, 12) = ? AND used_at IS NULL;",
                    (code_ref,))
                conn.commit()
                return cur.rowcount

    def worker_client_id_exists(self, client_id: str) -> bool:
        with self._get_connection() as conn:
            return conn.execute("SELECT 1 FROM worker_devices WHERE client_id = ?;",
                                (client_id,)).fetchone() is not None

    def add_worker_device(self, client_id: str, token_sha256: str, label: str, hostname: str,
                          platform: str, package: str, agent_version: str, enrolled_by: str) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO worker_devices (client_id, token_sha256, label, hostname, platform, package, "
                    "agent_version, enrolled_at, enrolled_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);",
                    (client_id, token_sha256, label, hostname, platform, package, agent_version,
                     datetime.utcnow().isoformat(), enrolled_by),
                )
                conn.commit()

    def get_worker_device_by_token(self, token_sha256: str) -> Optional[Dict[str, Any]]:
        """Máy trạm CÒN HIỆU LỰC (chưa thu hồi) có khoá này."""
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM worker_devices WHERE token_sha256 = ? AND revoked_at IS NULL;", (token_sha256,)
            ).fetchone()
            return dict(row) if row else None

    def touch_worker_device(self, client_id: str, agent_version: Optional[str] = None,
                            package: Optional[str] = None) -> None:
        with self._lock:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE worker_devices SET last_seen_at = ?, agent_version = COALESCE(?, agent_version), "
                    "package = COALESCE(?, package) WHERE client_id = ?;",
                    (datetime.utcnow().isoformat(), agent_version, package, client_id),
                )
                conn.commit()

    def list_worker_devices(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            rows = conn.execute(
                "SELECT client_id, label, hostname, platform, package, agent_version, enrolled_at, enrolled_by, "
                "last_seen_at, revoked_at, revoked_by FROM worker_devices ORDER BY enrolled_at DESC;"
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_worker_device(self, client_id: str, revoked_by: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute(
                    "UPDATE worker_devices SET revoked_at = ?, revoked_by = ? "
                    "WHERE client_id = ? AND revoked_at IS NULL;",
                    (datetime.utcnow().isoformat(), revoked_by, client_id),
                )
                conn.commit()
                return cur.rowcount > 0

    def revoke_device_token(self, device_id: str) -> bool:
        with self._lock:
            with self._get_connection() as conn:
                cur = conn.execute("DELETE FROM device_tokens WHERE device_id = ?;", (device_id,))
                conn.commit()
                return cur.rowcount > 0

    def add_or_update_task(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Lưu hoặc cập nhật tác vụ vào bảng tasks."""
        task_id = task_data.get("task_id") or task_data.get("id") or f"task_{uuid.uuid4().hex[:8]}"
        timestamp = task_data.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        client_id = task_data.get("client_id", "unknown")
        task_message = task_data.get("task_message") or task_data.get("message", "")
        sender = task_data.get("sender", "Ban Giám Đốc")
        status = task_data.get("status", "pending")
        now_str = datetime.utcnow().isoformat()

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO tasks (id, timestamp, client_id, task_message, sender, status, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        updated_at = excluded.updated_at;
                    """,
                    # title: cột NOT NULL khi bảng do phía ERP tạo trước.
                    (task_id, timestamp, client_id, task_message, sender, status,
                     task_message[:200], now_str, now_str),
                )
                conn.commit()

        return {
            "id": task_id,
            "timestamp": timestamp,
            "client_id": client_id,
            "task_message": task_message,
            "sender": sender,
            "status": status,
        }

    def get_tasks(
        self,
        limit: int = 100,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Truy vấn danh sách công việc từ SQLite."""
        query = "SELECT id as task_id, timestamp, client_id, task_message, sender, status FROM tasks"
        clauses = []
        params = []

        if client_id:
            clauses.append("LOWER(client_id) = ?")
            params.append(client_id.strip().lower())
        if status:
            clauses.append("LOWER(status) = ?")
            params.append(status.strip().lower())

        if clauses:
            query += " WHERE " + " AND ".join(clauses)

        query += " ORDER BY timestamp DESC, rowid DESC LIMIT ?;"
        params.append(limit)

        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]

    def count_tasks(self) -> Dict[str, int]:
        """Thống kê tổng số task theo trạng thái."""
        with self._lock:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM tasks;")
                total = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM tasks WHERE LOWER(status) = 'completed';")
                completed = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM tasks WHERE LOWER(status) IN ('issue', 'error');")
                issues = cursor.fetchone()[0]

                return {
                    "total": total,
                    "completed": completed,
                    "issues": issues,
                    "pending": max(0, total - completed - issues),
                }

    # -----------------------------------------------------------------------
    # System Telemetry & Hardware Stats (100% Real, Zero Mock)
    # -----------------------------------------------------------------------

    def get_system_hardware_stats(self) -> Dict[str, Any]:
        """Thu thập thông số phần cứng thực tế (CPU, RAM, Disk, Uptime) qua psutil."""
        try:
            cpu_percent = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            boot_time = psutil.boot_time()
            uptime_seconds = int(datetime.now().timestamp() - boot_time)

            return {
                "cpu_percent": round(cpu_percent, 1),
                "ram_percent": round(mem.percent, 1),
                "ram_used_gb": round(mem.used / (1024 ** 3), 2),
                "ram_total_gb": round(mem.total / (1024 ** 3), 2),
                "disk_percent": round(disk.percent, 1),
                "uptime_seconds": uptime_seconds,
            }
        except Exception as exc:
            logger.warning("Lỗi thu thập thông số phần cứng: %s", exc)
            return {
                "cpu_percent": 0.0,
                "ram_percent": 0.0,
                "ram_used_gb": 0.0,
                "ram_total_gb": 0.0,
                "disk_percent": 0.0,
                "uptime_seconds": 0,
            }


# Singleton instance
db_manager = DatabaseManager()
