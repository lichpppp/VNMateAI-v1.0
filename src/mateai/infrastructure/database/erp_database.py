"""
core/database.py
================
Mô hình cơ sở dữ liệu có quan hệ (Relational ERP Data Model) cho VN-MateAI.
Phase 47: ERP Structure & Bulk Data Import Engine.
Phase 48: RBAC & Immutable Audit Trail, ITSM Task Extensions.

Tables:
  1. departments: id, name, description
  2. employees: id, dept_id, name, position, email, phone, role
  3. devices: id, dept_id, owner_id (nullable), hostname, ip_address, type
  4. tasks: id, dept_id, assignee_id, title, status, due_date, created_by_ai, resolution_notes
  5. records: id, dept_id, document_name, file_path, date_created
  6. audit_logs: id, timestamp, employee_id, action_type, payload, status, approved_by  [IMMUTABLE]
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple



def open_sqlite(
    path: "Path | str",
    *,
    timeout: float = 30.0,
    foreign_keys: bool = False,
    wal: bool = True,
    synchronous: Optional[str] = None,
) -> sqlite3.Connection:
    """
    Nơi DUY NHẤT mở kết nối SQLite trong core/ (RULE-014) — đổi driver / thêm
    tuỳ chọn chung (vd. chuyển PostgreSQL) chỉ sửa ở đây.

    Kết nối là ClosingConnection (thoát khối `with` là ĐÓNG, không chỉ commit),
    row_factory = sqlite3.Row. Probe sức khoẻ dùng wal=False: chỉ đọc, không đổi
    chế độ journal của file.
    """
    conn = sqlite3.connect(str(path), timeout=timeout, check_same_thread=False,
                           factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    if foreign_keys:
        conn.execute("PRAGMA foreign_keys = ON;")
    if wal:
        conn.execute("PRAGMA journal_mode = WAL;")
    if synchronous:
        conn.execute(f"PRAGMA synchronous = {synchronous};")
    return conn


def ensure_tasks_table(cursor: sqlite3.Cursor) -> None:
    """
    Tạo / di trú bảng `tasks` — nơi DUY NHẤT định nghĩa schema của bảng này.

    Bảng do hai luồng dùng chung: giao việc cho máy trạm (mateai.infrastructure.database.db_manager:
    client_id, task_message, sender) và công việc ERP (dept_id, title,
    due_date...). Trước đây mỗi module tự CREATE bản của mình, nên schema thật
    phụ thuộc module nào khởi tạo trước: ERP trước → title NOT NULL → lệnh giao
    việc cho máy trạm (không có title) lỗi "NOT NULL constraint failed".
    """
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks';")
    existing_task_table = cursor.fetchone()

    if not existing_task_table:
        # Bảng `tasks` do hai tầng cùng dùng: `db_manager` (schema cũ,
        # id TEXT) và `ERPDatabase` (các cột ERP thêm sau). Bản CREATE
        # dưới đây phải là hợp của cả hai — trước đây nó chỉ có phần
        # ERP, nên CSDL tạo mới bằng ERPDatabase hỏng ngay:
        #   • `id INTEGER PRIMARY KEY` mà create_erp_task() lại chèn
        #     "erp_<hex>" → SQLite ném "datatype mismatch".
        #   • thiếu created_at → generate_daily_report() ném
        #     "no such column: created_at", ROI Dashboard trắng bảng.
        # Chỉ chạy được ở máy đã có sẵn DB do db_manager tạo trước.
        cursor.execute(
            """
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                timestamp TEXT,
                client_id TEXT,
                task_message TEXT,
                sender TEXT DEFAULT 'AI/ERP',
                created_at TEXT,
                updated_at TEXT,
                dept_id INTEGER,
                assignee_id INTEGER,
                title TEXT,  -- không NOT NULL: lệnh giao việc máy trạm (db_manager) không có tiêu đề riêng
                status TEXT NOT NULL DEFAULT 'pending',
                due_date TEXT,
                created_by_ai INTEGER NOT NULL DEFAULT 0,
                resolution_notes TEXT,
                FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE CASCADE,
                FOREIGN KEY (assignee_id) REFERENCES employees(id) ON DELETE SET NULL
            );
            """
        )
    else:
        # Kiểm tra và thêm các cột thiếu cho tasks (migrations)
        cursor.execute("PRAGMA table_info(tasks);")
        existing_cols = {row["name"] for row in cursor.fetchall()}
        _task_migrations = [
            ("dept_id",          "INTEGER REFERENCES departments(id)"),
            ("assignee_id",       "INTEGER REFERENCES employees(id)"),
            ("title",             "TEXT"),
            ("due_date",          "TEXT"),
            ("created_by_ai",     "INTEGER NOT NULL DEFAULT 0"),
            ("resolution_notes",  "TEXT"),
            # Các cột dưới đây do db_manager tạo. Thiếu chúng thì
            # generate_daily_report() và create_erp_task() đều lỗi,
            # nên phải bổ sung cho CSDL nào chưa có.
            ("timestamp",         "TEXT"),
            ("client_id",         "TEXT"),
            ("task_message",      "TEXT"),
            ("sender",            "TEXT"),
            ("created_at",        "TEXT"),
            ("updated_at",        "TEXT"),
        ]
        for _col, _col_def in _task_migrations:
            if _col not in existing_cols:
                try:
                    cursor.execute(f"ALTER TABLE tasks ADD COLUMN {_col} {_col_def};")
                    logger.info("Phase 48 Migration: Đã thêm cột '%s' vào bảng tasks.", _col)
                except Exception as _e:
                    logger.warning("Không thể thêm cột %s vào tasks: %s", _col, _e)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_client_id ON tasks(client_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_dept ON tasks(dept_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tasks_created_by_ai ON tasks(created_by_ai);")


class ClosingConnection(sqlite3.Connection):
    """Kết nối SQLite đóng hẳn khi ra khỏi khối `with`.

    `with sqlite3.Connection` gốc chỉ commit/rollback, KHÔNG đóng kết nối. Kết
    nối chỉ đóng khi bị thu gom, nên file DB (và -wal/-shm) bị giữ lâu hơn cần
    thiết — trên Windows điều đó làm xoá/di chuyển file DB lỗi `WinError 32`.
    Gọi trực tiếp `conn = ...get_connection()` thì vẫn tự đóng như trước.
    """

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

logger = logging.getLogger("mateai.infrastructure.database.erp_database")

# Thư mục gốc dự án (đúng cả bản đóng gói) — KHÔNG suy từ vị trí file mã nguồn:
# chuyển module mà đường dẫn lệch là máy chủ mở một CSDL rỗng mới.
from core.config_loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
DB_PATH = Path(os.environ.get("VNMATEAI_DB_PATH") or _PROJECT_ROOT / "vnmateai.db")

# Số giờ tiết kiệm ước tính cho mỗi phiếu do AI tự xử lý.
#
# ĐÂY LÀ GIẢ ĐỊNH KINH DOANH, KHÔNG PHẢI SỐ ĐO. Không có thiết bị nào đo được
# "AI tiết kiệm bao nhiêu giờ" — con số này chỉ là quy đổi 1 phiếu = 15 phút.
# Vì vậy nó phải luôn được dán nhãn "ước tính" ở mọi nơi hiển thị, và chỉ được
# khai báo DUY NHẤT một lần tại đây. Trước đây hằng số này bị chép ở hai chỗ
# (database + itsm_skills); sửa một chỗ là hai màn hình lệch nhau.
HOURS_SAVED_PER_AI_TASK = 0.25


class ERPDatabase:
    """Quản lý các bảng dữ liệu tổ chức ERP trong SQLite với ràng buộc Khóa ngoại (Foreign Key)."""

    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        # RLock, KHÔNG phải Lock: một số method (vd `add_finance_record`) giữ
        # lock để INSERT rồi gọi `log_audit_action()` BÊN TRONG khối đó, mà
        # `log_audit_action` -> `write_audit_log` lại `with self._lock:` nữa.
        # Với `Lock` thường, lần lấy thứ hai của CÙNG một luồng sẽ chờ mãi
        # (tự khoá chết). Vì các endpoint gọi việc này ngay trên event loop
        # của FastAPI, một lần treo là TREO CẢ SERVER — `/api/v1/health` cũng
        # không còn trả lời, chứ không chỉ hỏng một request.
        #
        # RLock giữ nguyên bảo đảm loại trừ lẫn nhau giữa các luồng, chỉ cho
        # phép lồng nhau khi là CÙNG một luồng — đúng thứ cần cho trường hợp
        # "ghi dữ liệu + ghi audit bất biến trong một thao tác nguyên tử".
        self._lock = threading.RLock()
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Tạo kết nối SQLite có kích hoạt FOREIGN KEY và Row factory."""
        return open_sqlite(self.db_path, timeout=30.0, foreign_keys=True)

    def init_db(self) -> None:
        """Tạo các bảng ERP và kiểm tra schema migrations."""
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # 1. Bảng departments (Phòng Ban)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS departments (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT UNIQUE NOT NULL,
                        description TEXT
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_departments_name ON departments(name);")

                # 2. Bảng employees (Nhân Sự)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS employees (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dept_id INTEGER NOT NULL,
                        name TEXT NOT NULL,
                        position TEXT,
                        email TEXT,
                        phone TEXT,
                        role TEXT NOT NULL DEFAULT 'viewer'
                            CHECK(role IN ('admin', 'it_support', 'operator', 'viewer')),
                        FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_employees_dept ON employees(dept_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_employees_name ON employees(name);")

                # Phase 48 Migration: Thêm cột role vào employees nếu chưa có (cho DB cũ)
                cursor.execute("PRAGMA table_info(employees);")
                emp_cols = {row["name"] for row in cursor.fetchall()}
                if "role" not in emp_cols:
                    try:
                        cursor.execute(
                            "ALTER TABLE employees ADD COLUMN role TEXT NOT NULL DEFAULT 'viewer' "
                            "CHECK(role IN ('admin', 'it_support', 'operator', 'viewer'));"
                        )
                        logger.info("Phase 48 Migration: Đã thêm cột 'role' vào bảng employees.")
                    except Exception as _e:
                        logger.warning("Không thể thêm cột role vào employees: %s", _e)

                # 3. Bảng devices (Thiết Bị / Máy Tính)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS devices (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dept_id INTEGER NOT NULL,
                        owner_id INTEGER,
                        hostname TEXT NOT NULL,
                        ip_address TEXT,
                        type TEXT DEFAULT 'Workstation',
                        FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE CASCADE,
                        FOREIGN KEY (owner_id) REFERENCES employees(id) ON DELETE SET NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_dept ON devices(dept_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_devices_ip ON devices(ip_address);")

                # 4. Bảng tasks — schema có MỘT chủ: ensure_tasks_table() (đầu module).
                ensure_tasks_table(cursor)

                # 6. Bảng audit_logs (Nhật Ký Bất Biến — Phase 48)
                # Thiết kế: Chỉ cho phép INSERT và SELECT. Không UPDATE, không DELETE.
                # Tầng ORM sẽ không cung cấp phương thức UPDATE/DELETE trên bảng này.
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        employee_id TEXT,
                        action_type TEXT NOT NULL,
                        payload TEXT,
                        status TEXT NOT NULL DEFAULT 'success'
                            CHECK(status IN ('success', 'failed', 'pending', 'blocked')),
                        approved_by TEXT,
                        source_ip TEXT,
                        session_id TEXT
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_employee ON audit_logs(employee_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action_type);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_status ON audit_logs(status);")

                # 5. Bảng records (Sổ Sách / Hồ Sơ)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS records (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        dept_id INTEGER NOT NULL,
                        document_name TEXT NOT NULL,
                        file_path TEXT,
                        date_created TEXT,
                        FOREIGN KEY (dept_id) REFERENCES departments(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_records_dept ON records(dept_id);")

                # 7. Bảng finances (Sổ Quỹ / Tài Chính Doanh Nghiệp — Phase 56)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS finances (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        type TEXT NOT NULL CHECK(type IN ('income', 'expense')),
                        amount REAL NOT NULL,
                        category TEXT,
                        description TEXT,
                        created_by TEXT DEFAULT 'system',
                        date TEXT NOT NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_finances_date ON finances(date);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_finances_type ON finances(type);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_finances_category ON finances(category);")

                # 8. Bảng attendance (Chấm Công & Hiện Diện — Phase 56)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS attendance (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        employee_id INTEGER NOT NULL,
                        check_in_time TEXT NOT NULL,
                        check_out_time TEXT,
                        status TEXT DEFAULT 'present'
                            CHECK(status IN ('present', 'late', 'early_leave', 'absent', 'remote')),
                        FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_emp ON attendance(employee_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_checkin ON attendance(check_in_time);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_attendance_status ON attendance(status);")

                # 9. Enterprise Evolution: enterprise_departments (Phòng Ban Đa Nguồn Doanh Nghiệp)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS enterprise_departments (
                        id TEXT PRIMARY KEY,
                        dept_code TEXT UNIQUE NOT NULL,
                        dept_name TEXT NOT NULL,
                        data_clearance_level INTEGER DEFAULT 1,
                        config_metadata TEXT DEFAULT '{}',
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_ent_dept_code ON enterprise_departments(dept_code);")

                # 10. Enterprise Evolution: department_data_sources (Nguồn Dữ Liệu Phòng Ban)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS department_data_sources (
                        id TEXT PRIMARY KEY,
                        dept_id TEXT NOT NULL,
                        source_name TEXT NOT NULL,
                        source_type TEXT NOT NULL,
                        connection_config TEXT NOT NULL DEFAULT '{}',
                        sync_cron TEXT,
                        is_active INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (dept_id) REFERENCES enterprise_departments(id) ON DELETE CASCADE
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_dept_ds_dept_id ON department_data_sources(dept_id);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_dept_ds_type ON department_data_sources(source_type);")

                # 11. Enterprise Evolution: unified_department_metrics (Chỉ Số Thống Nhất Đa Phòng Ban)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS unified_department_metrics (
                        id TEXT PRIMARY KEY,
                        dept_code TEXT NOT NULL,
                        metric_date TEXT NOT NULL,
                        metrics_data TEXT NOT NULL DEFAULT '{}',
                        clearance_level INTEGER DEFAULT 1,
                        created_at TEXT NOT NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_udm_dept_date ON unified_department_metrics(dept_code, metric_date);")

                # 12. Privacy-Compliant Cache: department_historical_metrics (Chỉ số Lịch sử Phi Định Danh Non-PII)
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS department_historical_metrics (
                        id TEXT PRIMARY KEY,
                        dept_code TEXT NOT NULL,
                        metric_key TEXT NOT NULL,
                        metric_value REAL NOT NULL,
                        recorded_at TEXT NOT NULL
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_dept_key ON department_historical_metrics(dept_code, metric_key);")
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_hist_recorded ON department_historical_metrics(recorded_at);")

                conn.commit()
                logger.info("Khởi tạo và xác thực mô hình dữ liệu ERP (kèm Historical Metrics) thành công.")

    # ── Query Structure Tree ──────────────────────────────────────────────────

    def get_structure_tree(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ cây tổ chức Phòng Ban -> Nhân Viên / Thiết Bị / Công Việc / Sổ Sách."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Lấy danh sách phòng ban
                cursor.execute("SELECT id, name, description FROM departments ORDER BY name ASC;")
                depts = [dict(row) for row in cursor.fetchall()]

                for dept in depts:
                    d_id = dept["id"]

                    # 1. Nhân viên
                    cursor.execute(
                        "SELECT id, dept_id, name, position, email, phone FROM employees WHERE dept_id = ? ORDER BY name ASC;",
                        (d_id,),
                    )
                    dept["employees"] = [dict(r) for r in cursor.fetchall()]

                    # 2. Thiết bị
                    cursor.execute(
                        """
                        SELECT d.id, d.dept_id, d.owner_id, d.hostname, d.ip_address, d.type,
                               COALESCE(e.name, 'Chưa gán') AS owner_name
                        FROM devices d
                        LEFT JOIN employees e ON d.owner_id = e.id
                        WHERE d.dept_id = ?
                        ORDER BY d.hostname ASC;
                        """,
                        (d_id,),
                    )
                    dept["devices"] = [dict(r) for r in cursor.fetchall()]

                    # 3. Công việc
                    cursor.execute(
                        """
                        SELECT t.id, t.dept_id, t.assignee_id,
                               COALESCE(t.title, t.task_message, 'Chưa có tiêu đề') AS title,
                               t.status, t.due_date,
                               COALESCE(e.name, 'Chưa phân công') AS assignee_name
                        FROM tasks t
                        LEFT JOIN employees e ON t.assignee_id = e.id
                        WHERE t.dept_id = ?
                        ORDER BY t.id DESC;
                        """,
                        (d_id,),
                    )
                    dept["tasks"] = [dict(r) for r in cursor.fetchall()]

                    # 4. Sổ sách
                    cursor.execute(
                        "SELECT id, dept_id, document_name, file_path, date_created FROM records WHERE dept_id = ? ORDER BY date_created DESC;",
                        (d_id,),
                    )
                    dept["records"] = [dict(r) for r in cursor.fetchall()]

                return depts

    # ── Bulk Import Engine ────────────────────────────────────────────────────

    def bulk_import(
        self,
        departments_data: List[Dict[str, Any]],
        employees_data: List[Dict[str, Any]],
        devices_data: List[Dict[str, Any]],
        tasks_data: List[Dict[str, Any]],
        records_data: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Nhập dữ liệu hàng loạt theo Transaction với cơ chế Rollback toàn diện.
        Nếu gặp lỗi ở bất kỳ dòng nào, hủy bỏ toàn bộ và ném ra ngoại lệ chỉ rõ dòng lỗi.
        """
        records_data = records_data or []

        with self._lock:
            conn = self.get_connection()
            cursor = conn.cursor()

            try:
                conn.execute("BEGIN TRANSACTION;")

                # Cache map tên phòng ban -> dept_id
                dept_name_to_id: Dict[str, int] = {}
                # Cache map (dept_id, employee_name) -> employee_id
                emp_name_to_id: Dict[Tuple[int, str], int] = {}

                # 1. Quét và nạp trước các phòng ban đã có trong DB
                cursor.execute("SELECT id, name FROM departments;")
                for r in cursor.fetchall():
                    dept_name_to_id[r["name"].strip().lower()] = r["id"]

                # 2. Xử lý Sheet Phòng Ban
                imported_depts = 0
                for row_idx, row in enumerate(departments_data, start=2):
                    name = str(row.get("name") or row.get("TenPhongBan") or row.get("Tên Phòng Ban") or "").strip()
                    if not name:
                        raise ValueError(f"Sheet 'PhongBan', Dòng {row_idx}: Tên phòng ban không được để trống.")
                    desc = str(row.get("description") or row.get("MoTa") or row.get("Mô Tả") or "").strip()

                    key = name.lower()
                    if key in dept_name_to_id:
                        # Cập nhật mô tả nếu đã tồn tại
                        dept_id = dept_name_to_id[key]
                        cursor.execute("UPDATE departments SET description = ? WHERE id = ?;", (desc, dept_id))
                    else:
                        cursor.execute("INSERT INTO departments (name, description) VALUES (?, ?);", (name, desc))
                        dept_id = cursor.lastrowid
                        dept_name_to_id[key] = dept_id
                        imported_depts += 1

                # 3. Nạp cache nhân viên hiện có
                cursor.execute("SELECT id, dept_id, name FROM employees;")
                for r in cursor.fetchall():
                    emp_name_to_id[(r["dept_id"], r["name"].strip().lower())] = r["id"]

                # 4. Xử lý Sheet Nhân Viên
                imported_emps = 0
                for row_idx, row in enumerate(employees_data, start=2):
                    d_name = str(row.get("dept_name") or row.get("PhongBan") or row.get("Phòng Ban") or "").strip()
                    emp_name = str(row.get("name") or row.get("HoTen") or row.get("Họ Tên") or "").strip()

                    if not emp_name:
                        raise ValueError(f"Sheet 'NhanVien', Dòng {row_idx}: Họ tên nhân viên không được để trống.")
                    if not d_name:
                        raise ValueError(f"Sheet 'NhanVien', Dòng {row_idx}: Vui lòng chỉ định phòng ban cho nhân viên '{emp_name}'.")

                    d_key = d_name.lower()
                    if d_key not in dept_name_to_id:
                        raise ValueError(f"Sheet 'NhanVien', Dòng {row_idx}: Phòng ban '{d_name}' chưa tồn tại trong danh sách phòng ban.")

                    dept_id = dept_name_to_id[d_key]
                    pos = str(row.get("position") or row.get("ChucVu") or row.get("Chức Vụ") or "").strip()
                    email = str(row.get("email") or "").strip()
                    phone = str(row.get("phone") or row.get("SoDienThoai") or row.get("Số Điện Thoại") or "").strip()

                    emp_key = (dept_id, emp_name.lower())
                    if emp_key in emp_name_to_id:
                        cursor.execute(
                            "UPDATE employees SET position = ?, email = ?, phone = ? WHERE id = ?;",
                            (pos, email, phone, emp_name_to_id[emp_key]),
                        )
                    else:
                        cursor.execute(
                            "INSERT INTO employees (dept_id, name, position, email, phone) VALUES (?, ?, ?, ?, ?);",
                            (dept_id, emp_name, pos, email, phone),
                        )
                        emp_id = cursor.lastrowid
                        emp_name_to_id[emp_key] = emp_id
                        imported_emps += 1

                # 5. Xử lý Sheet Thiết Bị / Máy Tính
                imported_devices = 0
                for row_idx, row in enumerate(devices_data, start=2):
                    d_name = str(row.get("dept_name") or row.get("PhongBan") or row.get("Phòng Ban") or "").strip()
                    hostname = str(row.get("hostname") or row.get("TenMay") or row.get("Tên Máy") or "").strip()

                    if not hostname:
                        raise ValueError(f"Sheet 'MayTinh', Dòng {row_idx}: Tên máy (hostname) không được để trống.")
                    if not d_name:
                        raise ValueError(f"Sheet 'MayTinh', Dòng {row_idx}: Vui lòng chỉ định phòng ban cho máy '{hostname}'.")

                    d_key = d_name.lower()
                    if d_key not in dept_name_to_id:
                        raise ValueError(f"Sheet 'MayTinh', Dòng {row_idx}: Phòng ban '{d_name}' không tồn tại.")

                    dept_id = dept_name_to_id[d_key]
                    ip = str(row.get("ip_address") or row.get("DiaChiIP") or row.get("Địa Chỉ IP") or "").strip()
                    dev_type = str(row.get("type") or row.get("LoaiThietBi") or row.get("Loại Thiết Bị") or "Workstation").strip()
                    owner_name = str(row.get("owner_name") or row.get("NguoiSuDung") or row.get("Người Sử Dụng") or "").strip()

                    owner_id = None
                    if owner_name:
                        emp_k = (dept_id, owner_name.lower())
                        owner_id = emp_name_to_id.get(emp_k)
                        if not owner_id:
                            # Thử tìm theo tên nhân viên trong toàn công ty
                            cursor.execute("SELECT id FROM employees WHERE LOWER(name) = ? LIMIT 1;", (owner_name.lower(),))
                            found = cursor.fetchone()
                            if found:
                                owner_id = found["id"]

                    cursor.execute(
                        "INSERT INTO devices (dept_id, owner_id, hostname, ip_address, type) VALUES (?, ?, ?, ?, ?);",
                        (dept_id, owner_id, hostname, ip, dev_type),
                    )
                    imported_devices += 1

                # 6. Xử lý Sheet Công Việc
                imported_tasks = 0
                for row_idx, row in enumerate(tasks_data, start=2):
                    d_name = str(row.get("dept_name") or row.get("PhongBan") or row.get("Phòng Ban") or "").strip()
                    title = str(row.get("title") or row.get("TieuDe") or row.get("Tiêu Đề") or "").strip()

                    if not title:
                        raise ValueError(f"Sheet 'CongViec', Dòng {row_idx}: Tiêu đề công việc không được để trống.")
                    if not d_name:
                        raise ValueError(f"Sheet 'CongViec', Dòng {row_idx}: Vui lòng chỉ định phòng ban cho công việc '{title}'.")

                    d_key = d_name.lower()
                    if d_key not in dept_name_to_id:
                        raise ValueError(f"Sheet 'CongViec', Dòng {row_idx}: Phòng ban '{d_name}' không tồn tại.")

                    dept_id = dept_name_to_id[d_key]
                    status = str(row.get("status") or row.get("TrangThai") or row.get("Trạng Thái") or "pending").strip().lower()
                    if status in ("đang làm", "đang thực hiện", "in progress"):
                        status = "in_progress"
                    elif status in ("hoàn thành", "xong", "completed", "done"):
                        status = "completed"
                    else:
                        status = "pending"

                    due_date = str(row.get("due_date") or row.get("HanChot") or row.get("Hạn Chót") or "").strip()
                    assignee_name = str(row.get("assignee_name") or row.get("NguoiPhuTrach") or row.get("Người Phụ Trách") or "").strip()

                    assignee_id = None
                    if assignee_name:
                        emp_k = (dept_id, assignee_name.lower())
                        assignee_id = emp_name_to_id.get(emp_k)
                        if not assignee_id:
                            cursor.execute("SELECT id FROM employees WHERE LOWER(name) = ? LIMIT 1;", (assignee_name.lower(),))
                            found = cursor.fetchone()
                            if found:
                                assignee_id = found["id"]

                    # Tạo ID dạng chuỗi hoặc auto id
                    task_uuid = f"task_{int(datetime.now().timestamp())}_{row_idx}"
                    now_str = datetime.now().isoformat()

                    cursor.execute(
                        """
                        INSERT INTO tasks (id, dept_id, assignee_id, title, status, due_date, timestamp, client_id, task_message, sender, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'master', ?, 'ERP Import', ?, ?);
                        """,
                        (task_uuid, dept_id, assignee_id, title, status, due_date, now_str, title, now_str, now_str),
                    )
                    imported_tasks += 1

                # 7. Xử lý Sheet Sổ Sách / Hồ Sơ
                imported_records = 0
                for row_idx, row in enumerate(records_data, start=2):
                    d_name = str(row.get("dept_name") or row.get("PhongBan") or row.get("Phòng Ban") or "").strip()
                    doc_name = str(row.get("document_name") or row.get("TenTaiLieu") or row.get("Tên Tài Liệu") or "").strip()

                    if not doc_name:
                        raise ValueError(f"Sheet 'SoSach', Dòng {row_idx}: Tên tài liệu không được để trống.")
                    if not d_name:
                        raise ValueError(f"Sheet 'SoSach', Dòng {row_idx}: Vui lòng chỉ định phòng ban cho tài liệu '{doc_name}'.")

                    d_key = d_name.lower()
                    if d_key not in dept_name_to_id:
                        raise ValueError(f"Sheet 'SoSach', Dòng {row_idx}: Phòng ban '{d_name}' không tồn tại.")

                    dept_id = dept_name_to_id[d_key]
                    file_path = str(row.get("file_path") or row.get("DuongDan") or row.get("Đường Dẫn") or "").strip()
                    date_created = str(row.get("date_created") or row.get("NgayTao") or row.get("Ngày Tạo") or datetime.now().strftime("%Y-%m-%d")).strip()

                    cursor.execute(
                        "INSERT INTO records (dept_id, document_name, file_path, date_created) VALUES (?, ?, ?, ?);",
                        (dept_id, doc_name, file_path, date_created),
                    )
                    imported_records += 1

                conn.commit()

                return {
                    "success": True,
                    "message": "Import dữ liệu ERP thành công rực rỡ!",
                    "stats": {
                        "departments": imported_depts,
                        "employees": imported_emps,
                        "devices": imported_devices,
                        "tasks": imported_tasks,
                        "records": imported_records,
                    },
                }

            except Exception as exc:
                conn.rollback()
                logger.error("Lỗi giao dịch khi import dữ liệu ERP (Đã Rollback): %s", exc)
                raise exc
            finally:
                conn.close()

    # ── AI Knowledge Query Tool ───────────────────────────────────────────────

    def query_organization(self, query: str) -> str:
        """
        Tra cứu tri thức tổ chức phục vụ cho LLM / 9router.
        Hỗ trợ tìm kiếm theo IP, Tên nhân viên, Tên máy tính, Tên phòng ban, Công việc.
        """
        query_clean = query.strip().lower()
        if not query_clean:
            return "Vui lòng cung cấp nội dung cần tra cứu về tổ chức."

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                results = []

                # 1. Tìm theo IP hoặc Tên máy tính
                cursor.execute(
                    """
                    SELECT d.hostname, d.ip_address, d.type, dept.name AS dept_name,
                           COALESCE(e.name, 'Chưa gán người dùng') AS owner_name,
                           COALESCE(e.email, '') AS owner_email,
                           COALESCE(e.phone, '') AS owner_phone
                    FROM devices d
                    JOIN departments dept ON d.dept_id = dept.id
                    LEFT JOIN employees e ON d.owner_id = e.id
                    WHERE LOWER(d.ip_address) LIKE ? OR LOWER(d.hostname) LIKE ?;
                    """,
                    (f"%{query_clean}%", f"%{query_clean}%"),
                )
                device_matches = cursor.fetchall()
                if device_matches:
                    results.append("🖥️ [THIẾT BỊ / MÁY TÍNH]:")
                    for m in device_matches:
                        results.append(
                            f"- Máy '{m['hostname']}' (IP: {m['ip_address']}, Loại: {m['type']}) thuộc '{m['dept_name']}'. Người phụ trách: {m['owner_name']} (Email: {m['owner_email']}, ĐT: {m['owner_phone']})."
                        )

                # 2. Tìm theo Tên Nhân Viên hoặc Chức vụ
                cursor.execute(
                    """
                    SELECT e.name, e.position, e.email, e.phone, dept.name AS dept_name
                    FROM employees e
                    JOIN departments dept ON e.dept_id = dept.id
                    WHERE LOWER(e.name) LIKE ? OR LOWER(e.position) LIKE ? OR LOWER(e.email) LIKE ?;
                    """,
                    (f"%{query_clean}%", f"%{query_clean}%", f"%{query_clean}%"),
                )
                emp_matches = cursor.fetchall()
                if emp_matches:
                    results.append("\n👤 [NHÂN SỰ]:")
                    for m in emp_matches:
                        results.append(
                            f"- Nhân viên: {m['name']} | Chức vụ: {m['position']} | Phòng ban: {m['dept_name']} | Email: {m['email']} | SĐT: {m['phone']}"
                        )

                # 3. Tìm theo Phòng Ban & Thống kê
                cursor.execute(
                    """
                    SELECT id, name, description FROM departments
                    WHERE LOWER(name) LIKE ?;
                    """,
                    (f"%{query_clean}%",),
                )
                dept_matches = cursor.fetchall()
                if dept_matches:
                    results.append("\n🏢 [PHÒNG BAN]:")
                    for d in dept_matches:
                        d_id = d["id"]
                        cursor.execute("SELECT COUNT(*) AS c FROM employees WHERE dept_id = ?;", (d_id,))
                        emp_c = cursor.fetchone()["c"]
                        cursor.execute("SELECT COUNT(*) AS c FROM devices WHERE dept_id = ?;", (d_id,))
                        dev_c = cursor.fetchone()["c"]
                        cursor.execute("SELECT COUNT(*) AS c FROM tasks WHERE dept_id = ? AND status != 'completed';", (d_id,))
                        pending_tasks = cursor.fetchone()["c"]

                        results.append(
                            f"- '{d['name']}': {d['description'] or 'Không có mô tả'} (Gồm {emp_c} nhân sự, {dev_c} thiết bị, {pending_tasks} công việc chưa hoàn thành)."
                        )

                # 4. Tìm theo Công Việc / Task
                cursor.execute(
                    """
                    SELECT t.title, t.status, t.due_date, dept.name AS dept_name,
                           COALESCE(e.name, 'Chưa giao') AS assignee_name
                    FROM tasks t
                    LEFT JOIN departments dept ON t.dept_id = dept.id
                    LEFT JOIN employees e ON t.assignee_id = e.id
                    WHERE LOWER(t.title) LIKE ? OR LOWER(t.status) LIKE ?;
                    """,
                    (f"%{query_clean}%", f"%{query_clean}%"),
                )
                task_matches = cursor.fetchall()
                if task_matches:
                    results.append("\n📋 [CÔNG VIỆC LIÊN QUAN]:")
                    for t in task_matches[:10]:
                        results.append(
                            f"- Task: '{t['title']}' | Trạng thái: {t['status']} | Phụ trách: {t['assignee_name']} ({t['dept_name'] or 'Toàn công ty'}) | Hạn chót: {t['due_date'] or 'Không có'}"
                        )

                # 5. Tìm theo Hồ Sơ / Sổ Sách
                cursor.execute(
                    """
                    SELECT r.document_name, r.file_path, r.date_created, dept.name AS dept_name
                    FROM records r
                    JOIN departments dept ON r.dept_id = dept.id
                    WHERE LOWER(r.document_name) LIKE ?;
                    """,
                    (f"%{query_clean}%",),
                )
                rec_matches = cursor.fetchall()
                if rec_matches:
                    results.append("\n📂 [SỔ SÁCH & TÀI LIỆU]:")
                    for r in rec_matches:
                        results.append(
                            f"- Tài liệu: '{r['document_name']}' thuộc '{r['dept_name']}' (Lưu tại: {r['file_path']}, Ngày tạo: {r['date_created']})"
                        )

                if not results:
                    return f"Không tìm thấy thông tin phù hợp trong cơ sở dữ liệu tổ chức cho từ khóa: '{query}'."

                return "\n".join(results)

    def add_department(self, name: str, description: str = "") -> Dict[str, Any]:
        """Tạo mới phòng ban trong cơ sở dữ liệu."""
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Tên phòng ban không được để trống.")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, description FROM departments WHERE LOWER(name) = ?;", (clean_name.lower(),))
            existing = cursor.fetchone()
            if existing:
                raise ValueError(f"Phòng ban '{clean_name}' đã tồn tại (ID #{existing['id']}).")
            cursor.execute(
                "INSERT INTO departments (name, description) VALUES (?, ?);",
                (clean_name, description.strip() if description else ""),
            )
            dept_id = cursor.lastrowid
            conn.commit()
            return {"id": dept_id, "name": clean_name, "description": description.strip() if description else ""}

    def delete_department(self, dept_id: int) -> bool:
        """Xóa phòng ban theo ID (các bản ghi con sẽ bị xóa hoặc cascade theo ràng buộc)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM departments WHERE id = ?;", (dept_id,))
            conn.commit()
            return cursor.rowcount > 0

    # ── Phase 48: Audit Log ORM (INSERT / SELECT only — no UPDATE / DELETE) ──────

    def write_audit_log(
        self,
        action_type: str,
        status: str = "success",
        employee_id: Optional[str] = None,
        payload: Optional[str] = None,
        approved_by: Optional[str] = None,
        source_ip: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> int:
        """
        Ghi một bản ghi vào audit_logs. Đây là phương thức DUY NHẤT được phép ghi.
        Tuyệt đối KHÔNG cung cấp phương thức UPDATE hoặc DELETE trên bảng này.
        Trả về id của bản ghi vừa ghi.
        """
        import json as _json
        now = datetime.utcnow().isoformat()
        if isinstance(payload, (dict, list)):
            payload = _json.dumps(payload, ensure_ascii=False, default=str)
        valid_statuses = {"success", "failed", "pending", "blocked"}
        if status not in valid_statuses:
            status = "failed"
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO audit_logs
                        (timestamp, employee_id, action_type, payload, status, approved_by, source_ip, session_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (now, employee_id, action_type, payload, status, approved_by, source_ip, session_id),
                )
                conn.commit()
                log_id = cursor.lastrowid
                logger.debug(
                    "[AuditLog] #%d | action=%s | status=%s | employee=%s",
                    log_id, action_type, status, employee_id
                )
                return log_id

    def log_audit_action(
        self,
        action_type: str,
        status: str = "success",
        employee_id: Optional[str] = None,
        payload: Optional[str] = None,
        approved_by: Optional[str] = None,
    ) -> int:
        """Alias thân thiện cho write_audit_log — được dùng bởi các Phase 56/57 modules."""
        return self.write_audit_log(
            action_type=action_type,
            status=status,
            employee_id=employee_id,
            payload=payload,
            approved_by=approved_by,
        )

    def get_audit_logs(
        self,
        limit: int = 200,
        employee_id: Optional[str] = None,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Truy vấn nhật ký kiểm toán với bộ lọc tùy chọn. Chỉ SELECT — không thể sửa/xóa."""
        conditions: List[str] = []
        params: List[Any] = []
        if employee_id:
            conditions.append("employee_id = ?")
            params.append(employee_id)
        if action_type:
            conditions.append("action_type LIKE ?")
            params.append(f"%{action_type}%")
        if status:
            conditions.append("status = ?")
            params.append(status)

        where_clause = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(limit, 5000)))

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                f"SELECT * FROM audit_logs {where_clause} ORDER BY id DESC LIMIT ?;",
                params,
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_audit_stats(self) -> Dict[str, Any]:
        """Thống kê tóm tắt nhật ký kiểm toán cho ROI Dashboard."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM audit_logs;")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE status = 'success';")
            success = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE status = 'failed';")
            failed = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE status = 'blocked';")
            blocked = cursor.fetchone()[0]
            cursor.execute(
                "SELECT COUNT(*) FROM audit_logs WHERE status = 'success' AND employee_id LIKE 'ai_%';"
            )
            auto_remediated = cursor.fetchone()[0]
            # Tasks tạo bởi AI
            cursor.execute("SELECT COUNT(*) FROM tasks WHERE created_by_ai = 1;")
            ai_tasks = cursor.fetchone()[0]
        return {
            "total_logs": total,
            "success": success,
            "failed": failed,
            "blocked_attempts": blocked,
            "auto_remediated": auto_remediated,
            "ai_tasks_created": ai_tasks,
            "hours_saved_estimate": round(ai_tasks * HOURS_SAVED_PER_AI_TASK, 2),
            # Nói rõ con số trên từ đâu ra, để UI không bán nó như số đo.
            "hours_saved_per_task": HOURS_SAVED_PER_AI_TASK,
        }

    # ── Phase 48: Task ORM Extensions ────────────────────────────────────────

    def create_erp_task(
        self,
        title: str,
        dept_id: Optional[int] = None,
        assignee_id: Optional[int] = None,
        status: str = "pending",
        due_date: Optional[str] = None,
        created_by_ai: bool = False,
        resolution_notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Tạo mới phiếu công việc ERP với ACID transaction."""
        if not title or not title.strip():
            raise ValueError("Tiêu đề công việc không được để trống.")
        valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
        if status not in valid_statuses:
            status = "pending"
        with self._lock:
            with self.get_connection() as conn:
                conn.execute("BEGIN IMMEDIATE;")
                try:
                    import uuid as _uuid
                    cursor = conn.cursor()
                    now_iso = datetime.utcnow().isoformat()
                    # Sinh TEXT id tương thích bảng tasks cũ (id TEXT PRIMARY KEY)
                    task_id = f"erp_{_uuid.uuid4().hex[:12]}"
                    cursor.execute(
                        """
                        INSERT INTO tasks (
                            id, dept_id, assignee_id, title, status, due_date,
                            created_by_ai, resolution_notes,
                            timestamp, client_id, task_message, sender, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'erp', ?, 'AI/ERP', ?, ?);
                        """,
                        (
                            task_id, dept_id, assignee_id, title.strip(), status, due_date,
                            1 if created_by_ai else 0, resolution_notes,
                            now_iso, title.strip()[:200], now_iso, now_iso,
                        ),
                    )
                    conn.commit()
                    logger.info("ERP Task '%s' tạo thành công: '%s' (by_ai=%s)", task_id, title[:60], created_by_ai)
                    return {
                        "id": task_id, "title": title.strip(), "status": status,
                        "dept_id": dept_id, "assignee_id": assignee_id,
                        "due_date": due_date, "created_by_ai": created_by_ai,
                        "resolution_notes": resolution_notes,
                    }
                except Exception:
                    conn.rollback()
                    raise

    def update_erp_task_status(
        self,
        task_id: Any,
        status: str,
        resolution_notes: Optional[str] = None,
    ) -> bool:
        """Cập nhật trạng thái và ghi chú giải quyết cho phiếu công việc (chấp nhận id dạng TEXT hoặc INT)."""
        valid_statuses = {"pending", "in_progress", "completed", "cancelled"}
        if status not in valid_statuses:
            raise ValueError(f"Trạng thái không hợp lệ: '{status}'. Hợp lệ: {valid_statuses}")
        # Chuẩn hóa task_id về str để match cột TEXT
        task_id_str = str(task_id)
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if resolution_notes:
                    cursor.execute(
                        "UPDATE tasks SET status = ?, resolution_notes = ? WHERE id = ?;",
                        (status, resolution_notes, task_id_str),
                    )
                else:
                    cursor.execute(
                        "UPDATE tasks SET status = ? WHERE id = ?;",
                        (status, task_id_str),
                    )
                conn.commit()
                return cursor.rowcount > 0

    def get_employee_by_identifier(
        self, identifier: str
    ) -> Optional[Dict[str, Any]]:
        """Tìm nhân viên theo ID, email, tên hoặc username của hệ thống portal."""
        ident_lower = identifier.strip().lower()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT e.id, e.dept_id, e.name, e.position, e.email, e.phone, e.role,
                       d.name AS dept_name
                FROM employees e
                LEFT JOIN departments d ON e.dept_id = d.id
                WHERE LOWER(e.email) = ? OR LOWER(e.name) = ? OR CAST(e.id AS TEXT) = ?
                LIMIT 1;
                """,
                (ident_lower, ident_lower, identifier.strip()),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    # ── Phase 56 & 57: Enterprise Finance & Attendance Management ──────────────

    def add_finance_record(
        self,
        finance_type: str,
        amount: float,
        category: str,
        description: str,
        created_by: str = "system",
        date_str: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Thêm giao dịch thu/chi vào sổ quỹ doanh nghiệp."""
        norm_type = finance_type.strip().lower()
        if norm_type in ("thu", "income"):
            norm_type = "income"
        elif norm_type in ("chi", "expense"):
            norm_type = "expense"
        else:
            raise ValueError(f"Loại giao dịch không hợp lệ: {finance_type}. Chỉ chấp nhận 'income'/'thu' hoặc 'expense'/'chi'.")

        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO finances (type, amount, category, description, created_by, date)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (norm_type, float(amount), category.strip(), description.strip(), created_by.strip(), date_str),
                )
                rec_id = cursor.lastrowid
                conn.commit()

                # Immutable audit log
                self.log_audit_action(
                    employee_id=created_by,
                    action_type="RECORD_FINANCE",
                    payload=f"Type={norm_type}, Amount={amount:,.0f} VND, Cat={category}, Desc={description}",
                    status="success",
                )

                return {
                    "id": rec_id,
                    "type": norm_type,
                    "amount": amount,
                    "category": category,
                    "description": description,
                    "created_by": created_by,
                    "date": date_str,
                }

    def get_finances(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        finance_type: Optional[str] = None,
        category: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Truy vấn danh sách sổ quỹ."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT id, type, amount, category, description, created_by, date FROM finances WHERE 1=1"
            params: List[Any] = []

            if start_date:
                query += " AND date >= ?"
                params.append(start_date)
            if end_date:
                query += " AND date <= ?"
                params.append(end_date)
            if finance_type:
                query += " AND type = ?"
                params.append(finance_type)
            if category:
                query += " AND category = ?"
                params.append(category)

            query += " ORDER BY date DESC, id DESC LIMIT ?;"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]

    def get_financial_summary(self, days: int = 30) -> Dict[str, Any]:
        """Tổng hợp tình hình tài chính doanh nghiệp trong N ngày gần nhất."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Tổng thu
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM finances WHERE type = 'income';"
            )
            total_income = float(cursor.fetchone()[0])

            # Tổng chi
            cursor.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM finances WHERE type = 'expense';"
            )
            total_expense = float(cursor.fetchone()[0])

            net_balance = total_income - total_expense

            # Phân bổ chi theo danh mục
            cursor.execute(
                """
                SELECT category, COALESCE(SUM(amount), 0) as total
                FROM finances
                WHERE type = 'expense'
                GROUP BY category
                ORDER BY total DESC;
                """
            )
            category_breakdown = {row["category"]: float(row["total"]) for row in cursor.fetchall()}

            # 10 giao dịch gần nhất
            cursor.execute(
                "SELECT id, type, amount, category, description, created_by, date FROM finances ORDER BY date DESC, id DESC LIMIT 10;"
            )
            recent_txs = [dict(r) for r in cursor.fetchall()]

            # Burn rate & Runway calculation
            cursor.execute(
                """
                SELECT COALESCE(SUM(amount), 0) FROM finances
                WHERE type = 'expense' AND date >= datetime('now', '-30 days');
                """
            )
            expense_last_30d = float(cursor.fetchone()[0])
            daily_burn_rate = round(expense_last_30d / 30.0, 2) if expense_last_30d > 0 else 0.0

            # Dự báo số ngày quỹ còn hoạt động (runway)
            runway_days = round(net_balance / daily_burn_rate, 1) if (daily_burn_rate > 0 and net_balance > 0) else (999.0 if net_balance > 0 else 0.0)

            # Đếm số giao dịch thật. `COALESCE(SUM(...), 0)` khiến "chưa có
            # giao dịch nào" và "tổng bằng 0" cho ra CÙNG một giá trị 0 — không
            # phân biệt được. Người đọc số 0 đó là hiểu nhầm thành "quỹ cạn",
            # và cảnh báo tài chính bắn đỏ lên một sự kiện không tồn tại.
            # Trả kèm số dòng để tầng trên biết khi nào phải nói "chưa có dữ
            # liệu" thay vì đưa ra con số.
            cursor.execute("SELECT COUNT(*) FROM finances;")
            transaction_count = int(cursor.fetchone()[0])

            return {
                "total_income": total_income,
                "total_expense": total_expense,
                "net_balance": net_balance,
                "daily_burn_rate": daily_burn_rate,
                "runway_days": runway_days,
                "is_critical_burn": (daily_burn_rate > 0 and runway_days < 15.0),
                # False = bảng `finances` trống, mọi số 0 ở trên là do COALESCE
                # chứ không phải số đo thật.
                "has_data": transaction_count > 0,
                "transaction_count": transaction_count,
                "category_breakdown": category_breakdown,
                "recent_transactions": recent_txs,
            }

    def record_attendance(
        self,
        employee_id: int,
        check_in_time: Optional[str] = None,
        check_out_time: Optional[str] = None,
        status: str = "present",
    ) -> Dict[str, Any]:
        """Ghi nhận lượt chấm công cho nhân viên."""
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not check_in_time:
            check_in_time = now_str

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO attendance (employee_id, check_in_time, check_out_time, status)
                    VALUES (?, ?, ?, ?);
                    """,
                    (employee_id, check_in_time, check_out_time, status),
                )
                rec_id = cursor.lastrowid
                conn.commit()

                return {
                    "id": rec_id,
                    "employee_id": employee_id,
                    "check_in_time": check_in_time,
                    "check_out_time": check_out_time,
                    "status": status,
                }

    def get_attendance(
        self,
        date_str: Optional[str] = None,
        employee_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Truy vấn danh sách chấm công kết hợp thông tin nhân viên."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = """
                SELECT a.id, a.employee_id, e.name AS employee_name, e.position, d.name AS dept_name,
                       a.check_in_time, a.check_out_time, a.status
                FROM attendance a
                JOIN employees e ON a.employee_id = e.id
                LEFT JOIN departments d ON e.dept_id = d.id
                WHERE 1=1
            """
            params: List[Any] = []
            if date_str:
                query += " AND a.check_in_time LIKE ?"
                params.append(f"{date_str}%")
            if employee_id:
                query += " AND a.employee_id = ?"
                params.append(employee_id)

            query += " ORDER BY a.check_in_time DESC LIMIT ?;"
            params.append(limit)

            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def get_task_leaderboard(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Lấy bảng xếp hạng nhân viên (Leaderboard) hoàn thành nhiều công việc nhất."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT e.id, e.name, e.position, d.name AS dept_name,
                       COUNT(CASE WHEN t.status = 'completed' THEN 1 END) AS completed_tasks,
                       COUNT(CASE WHEN t.status IN ('pending', 'in_progress') THEN 1 END) AS active_tasks,
                       COUNT(t.id) AS total_assigned
                FROM employees e
                LEFT JOIN departments d ON e.dept_id = d.id
                LEFT JOIN tasks t ON t.assignee_id = e.id
                GROUP BY e.id, e.name, e.position, d.name
                ORDER BY completed_tasks DESC, total_assigned DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            leaderboard = []
            for rank, r in enumerate(rows, 1):
                item = dict(r)
                item["rank"] = rank
                leaderboard.append(item)
            return leaderboard

    def get_company_kpi_overview(self) -> Dict[str, Any]:
        """Tóm tắt toàn bộ chỉ số KPI doanh nghiệp cho góc nhìn Tổng Giám Đốc (CEO View)."""
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Tasks statistics
            cursor.execute("SELECT COUNT(*) FROM tasks;")
            total_tasks = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM tasks WHERE status = 'completed';")
            completed_tasks = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM tasks WHERE status IN ('pending', 'in_progress');")
            active_tasks = cursor.fetchone()[0]

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute(
                "SELECT COUNT(*) FROM tasks WHERE status != 'completed' AND due_date IS NOT NULL AND due_date < ?;",
                (now_str,),
            )
            overdue_tasks = cursor.fetchone()[0]

            # Employees & Departments
            cursor.execute("SELECT COUNT(*) FROM employees;")
            total_employees = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM departments;")
            total_departments = cursor.fetchone()[0]

            # Financial summary
            fin = self.get_financial_summary(30)

            return {
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "active_tasks": active_tasks,
                "overdue_tasks": overdue_tasks,
                "completion_rate": round((completed_tasks / total_tasks * 100), 1) if total_tasks > 0 else 0.0,
                "total_employees": total_employees,
                "total_departments": total_departments,
                "finances": fin,
            }

    def purge_sample_data(self) -> Dict[str, int]:
        """Phase 73: Xóa toàn bộ dữ liệu mẫu đã từng được nạp vào CSDL.

        Trước đây module tự chèn sẵn số quỹ/chấm công giả và mã nhân sự mẫu
        nên giao diện hiển thị như dữ liệu thật. Nay CSDL khởi đầu trống và mọi
        ô số liệu báo "chờ kết nối" cho tới khi người dùng nhập/đồng bộ thật.
        """
        tables = ("tasks", "attendance", "finances", "devices", "records", "employees", "departments")
        removed: Dict[str, int] = {}
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Thứ tự trên tôn trọng khoá ngoại: `tasks` tham chiếu `employees`
                # với on_delete=NO ACTION nên phải xóa trước.
                for table in tables:
                    try:
                        cursor.execute(f"DELETE FROM [{table}];")
                        removed[table] = cursor.rowcount
                    except Exception as _e:
                        removed[table] = 0
                        # Không nuốt lỗi: người vận hành cần biết bảng nào còn dữ liệu mẫu.
                        logger.error("purge_sample_data: không xóa được %s — %s", table, _e)
                conn.commit()
        logger.info("Phase 73: đã xóa dữ liệu mẫu — %s", removed)
        return removed

    # ── Enterprise Evolution: Multi-Source Department & Metric Registry ──────

    def register_enterprise_department(
        self,
        dept_code: str,
        dept_name: str,
        data_clearance_level: int = 1,
        config_metadata: Optional[Dict[str, Any]] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Đăng ký hoặc cập nhật phòng ban doanh nghiệp đa nguồn (No-Code Form)."""
        import json as _json
        import uuid as _uuid
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        metadata_json = _json.dumps(config_metadata or {}, ensure_ascii=False)
        code_norm = dept_code.strip().upper()

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id FROM enterprise_departments WHERE dept_code = ?;", (code_norm,))
                row = cursor.fetchone()
                if row:
                    dept_id = row["id"]
                    cursor.execute(
                        """
                        UPDATE enterprise_departments
                        SET dept_name = ?, data_clearance_level = ?, config_metadata = ?, is_active = ?, updated_at = ?
                        WHERE id = ?;
                        """,
                        (dept_name.strip(), int(data_clearance_level), metadata_json, 1 if is_active else 0, now_str, dept_id),
                    )
                else:
                    dept_id = str(_uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO enterprise_departments (id, dept_code, dept_name, data_clearance_level, config_metadata, is_active, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (dept_id, code_norm, dept_name.strip(), int(data_clearance_level), metadata_json, 1 if is_active else 0, now_str, now_str),
                    )
                conn.commit()
                return {
                    "id": dept_id,
                    "dept_code": code_norm,
                    "dept_name": dept_name.strip(),
                    "data_clearance_level": int(data_clearance_level),
                    "is_active": bool(is_active),
                    "updated_at": now_str,
                }

    def get_enterprise_departments(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """Lấy danh sách các phòng ban doanh nghiệp kèm metadata cấu hình."""
        import json as _json
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM enterprise_departments"
                params = ()
                if active_only:
                    query += " WHERE is_active = 1"
                query += " ORDER BY dept_code ASC;"
                cursor.execute(query, params)
                results = []
                for row in cursor.fetchall():
                    d = dict(row)
                    d["is_active"] = bool(d.get("is_active", 1))
                    try:
                        d["config_metadata"] = _json.loads(d.get("config_metadata") or "{}")
                    except Exception:
                        d["config_metadata"] = {}
                    results.append(d)
                return results

    def get_enterprise_department_by_code(self, dept_code: str) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết phòng ban theo mã (VD: 'FIN', 'HR', 'IT')."""
        import json as _json
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM enterprise_departments WHERE dept_code = ?;", (dept_code.strip().upper(),))
                row = cursor.fetchone()
                if not row:
                    return None
                d = dict(row)
                d["is_active"] = bool(d.get("is_active", 1))
                try:
                    d["config_metadata"] = _json.loads(d.get("config_metadata") or "{}")
                except Exception:
                    d["config_metadata"] = {}
                return d

    def bind_department_data_source(
        self,
        dept_code: str,
        source_name: str,
        source_type: str,
        connection_config: Optional[Dict[str, Any]] = None,
        sync_cron: Optional[str] = None,
        is_active: bool = True,
    ) -> Dict[str, Any]:
        """Gắn kết hoặc cập nhật nguồn dữ liệu kết nối tới phòng ban."""
        import json as _json
        import uuid as _uuid
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        config_json = _json.dumps(connection_config or {}, ensure_ascii=False)
        code_norm = dept_code.strip().upper()

        dept = self.get_enterprise_department_by_code(code_norm)
        if not dept:
            # Tự động khởi tạo phòng ban nếu chưa tồn tại
            dept = self.register_enterprise_department(code_norm, f"Phòng Ban {code_norm}")

        dept_id = dept["id"]
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                # Kiểm tra source đã tồn tại với dept_id và source_name chưa
                cursor.execute(
                    "SELECT id FROM department_data_sources WHERE dept_id = ? AND source_name = ?;",
                    (dept_id, source_name.strip()),
                )
                row = cursor.fetchone()
                if row:
                    ds_id = row["id"]
                    cursor.execute(
                        """
                        UPDATE department_data_sources
                        SET source_type = ?, connection_config = ?, sync_cron = ?, is_active = ?
                        WHERE id = ?;
                        """,
                        (source_type.strip(), config_json, sync_cron, 1 if is_active else 0, ds_id),
                    )
                else:
                    ds_id = str(_uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO department_data_sources (id, dept_id, source_name, source_type, connection_config, sync_cron, is_active, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (ds_id, dept_id, source_name.strip(), source_type.strip(), config_json, sync_cron, 1 if is_active else 0, now_str),
                    )
                conn.commit()
                return {
                    "id": ds_id,
                    "dept_id": dept_id,
                    "dept_code": code_norm,
                    "source_name": source_name.strip(),
                    "source_type": source_type.strip(),
                    "sync_cron": sync_cron,
                    "is_active": bool(is_active),
                }

    def get_department_data_sources(self, dept_code: Optional[str] = None, active_only: bool = True) -> List[Dict[str, Any]]:
        """Lấy danh sách các data sources của phòng ban hoặc toàn hệ thống."""
        import json as _json
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = """
                    SELECT ds.*, ed.dept_code, ed.dept_name, ed.data_clearance_level
                    FROM department_data_sources ds
                    JOIN enterprise_departments ed ON ds.dept_id = ed.id
                """
                conditions = []
                params = []
                if dept_code:
                    conditions.append("ed.dept_code = ?")
                    params.append(dept_code.strip().upper())
                if active_only:
                    conditions.append("ds.is_active = 1")

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                query += " ORDER BY ed.dept_code ASC, ds.source_name ASC;"

                cursor.execute(query, tuple(params))
                results = []
                for row in cursor.fetchall():
                    item = dict(row)
                    item["is_active"] = bool(item.get("is_active", 1))
                    try:
                        item["connection_config"] = _json.loads(item.get("connection_config") or "{}")
                    except Exception:
                        item["connection_config"] = {}
                    results.append(item)
                return results

    def record_unified_metric(
        self,
        dept_code: str,
        metrics_data: Dict[str, Any],
        clearance_level: int = 1,
        metric_date: Optional[str] = None,
    ) -> str:
        """Ghi nhận chỉ số/số liệu chuẩn hóa theo ngày kèm clearance level."""
        import json as _json
        import uuid as _uuid
        rec_id = str(_uuid.uuid4())
        date_str = metric_date or datetime.now().strftime("%Y-%m-%d")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data_json = _json.dumps(metrics_data, ensure_ascii=False)

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO unified_department_metrics (id, dept_code, metric_date, metrics_data, clearance_level, created_at)
                    VALUES (?, ?, ?, ?, ?, ?);
                    """,
                    (rec_id, dept_code.strip().upper(), date_str, data_json, int(clearance_level), now_str),
                )
                conn.commit()
                return rec_id

    def get_unified_metrics(
        self,
        dept_code: Optional[str] = None,
        max_clearance_level: int = 4,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Truy vấn các chỉ số phòng ban có đối soát bảo mật clearance level."""
        import json as _json
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM unified_department_metrics WHERE clearance_level <= ?"
                params: List[Any] = [int(max_clearance_level)]
                if dept_code:
                    query += " AND dept_code = ?"
                    params.append(dept_code.strip().upper())
                query += " ORDER BY metric_date DESC, created_at DESC LIMIT ?;"
                params.append(limit)

                cursor.execute(query, tuple(params))
                results = []
                for row in cursor.fetchall():
                    item = dict(row)
                    try:
                        item["metrics_data"] = _json.loads(item.get("metrics_data") or "{}")
                    except Exception:
                        item["metrics_data"] = {}
                    results.append(item)
                return results

    # ── Privacy-Compliant Historical Trends (Non-PII Aggregates) ─────────────

    def record_historical_metric(
        self,
        dept_code: str,
        metric_key: str,
        metric_value: float,
        recorded_at: Optional[str] = None,
    ) -> str:
        """
        Lưu trữ chỉ số lịch sử phi định danh (Non-PII).
        TUYỆT ĐỐI KHÔNG lưu họ tên, email, CCCD, bảng lương hay STK ngân hàng.
        """
        import uuid as _uuid
        rec_id = str(_uuid.uuid4())
        rec_time = recorded_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO department_historical_metrics (id, dept_code, metric_key, metric_value, recorded_at)
                    VALUES (?, ?, ?, ?, ?);
                    """,
                    (rec_id, dept_code.strip().upper(), metric_key.strip(), float(metric_value), rec_time),
                )
                conn.commit()
                return rec_id

    def get_historical_metrics(
        self,
        dept_code: Optional[str] = None,
        metric_key: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Truy vấn chuỗi chỉ số lịch sử phục vụ vẽ biểu đồ xu hướng (Trend Analysis)."""
        with self._lock:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                query = "SELECT * FROM department_historical_metrics"
                conditions = []
                params: List[Any] = []

                if dept_code:
                    conditions.append("dept_code = ?")
                    params.append(dept_code.strip().upper())
                if metric_key:
                    conditions.append("metric_key = ?")
                    params.append(metric_key.strip())

                if conditions:
                    query += " WHERE " + " AND ".join(conditions)

                query += " ORDER BY recorded_at DESC LIMIT ?;"
                params.append(limit)

                cursor.execute(query, tuple(params))
                return [dict(r) for r in cursor.fetchall()]


# Khởi tạo singleton instance. KHÔNG tự chèn dữ liệu mẫu: CSDL bắt đầu trống,
# giao diện hiển thị "chờ kết nối" cho tới khi có nguồn dữ liệu thật.
erp_db = ERPDatabase()

