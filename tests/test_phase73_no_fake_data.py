"""
tests/test_phase73_no_fake_data.py
==================================
Kiểm thử Phase 73 — không còn dữ liệu mẫu/giả lập ở tầng backend.

Bối cảnh: trước đây hệ thống tự chèn số quỹ + chấm công giả khi CSDL rỗng,
và backend STT "mock" trả câu văn bản mẫu theo độ dài byte âm thanh. Cả hai
đều hiển thị ra giao diện như dữ liệu thật. Nay:
  - CSDL khởi tạo trống, không tự sinh dữ liệu
  - không engine ASR nào nhận dạng được thì trả chuỗi rỗng
  - "mock" bị từ chối ở tầng cấu hình
  - file Excel mẫu chỉ có hàng tiêu đề cột
"""

from __future__ import annotations

import asyncio
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def count_rows(db, table: str) -> int:
    """Đếm số dòng của một bảng (mở/đóng kết nối đúng cách)."""
    with db.get_connection() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM [{table}];").fetchone()[0]


# ──────────────────────────────────────────────────────────────────────
section("CSDL mới khởi tạo phải TRỐNG, không tự sinh dữ liệu mẫu")

from core.database import ERPDatabase  # noqa: E402

# Bảng ERP từng bị nạp dữ liệu mẫu
ERP_TABLES = ("finances", "attendance", "departments", "employees", "devices", "records")

with tempfile.TemporaryDirectory() as tmpdir:
    fresh = ERPDatabase(Path(tmpdir) / "fresh.db")
    for table in ERP_TABLES:
        n = count_rows(fresh, table)
        check(
            f"bảng {table} rỗng ngay khi khởi tạo",
            n == 0,
            f"đang có {n} dòng — nghĩa là vẫn còn seed tự động",
        )


# ──────────────────────────────────────────────────────────────────────
section("purge_sample_data() xóa sạch dữ liệu mẫu và chạy lại được")

from core.database import erp_db  # noqa: E402

with tempfile.TemporaryDirectory() as tmpdir:
    polluted = ERPDatabase(Path(tmpdir) / "polluted.db")
    with polluted.get_connection() as conn:
        # Nhồi dữ liệu giả tương tự trạng thái trước Phase 73
        conn.execute("INSERT INTO departments (name, description) VALUES ('Phòng Mẫu', 'mô tả mẫu');")
        conn.execute("INSERT INTO employees (dept_id, name, position) VALUES (1, 'Nguyễn Văn A', 'Kỹ sư');")
        conn.execute("INSERT INTO devices (dept_id, owner_id, hostname, ip_address, type) VALUES (1, 1, 'SERVER-01', '192.168.1.10', 'Server');")
        conn.execute("INSERT INTO finances (type, amount, category, description, date) VALUES ('income', 250000000.0, 'Doanh thu', 'hợp đồng mẫu', '2026-09-01 10:00:00');")
        conn.execute("INSERT INTO attendance (employee_id, check_in_time, status) VALUES (1, '2026-09-01 08:15:00', 'present');")
        conn.execute("INSERT INTO tasks (title, status, created_by_ai) VALUES ('Nhiệm vụ mẫu', 'pending', 0);")
        conn.commit()

    check("dữ liệu mẫu đã được nhồi sẵn trước khi gọi hàm", count_rows(polluted, "finances") == 1)

    removed = polluted.purge_sample_data()
    check("purge_sample_data() trả về số dòng đã xóa", isinstance(removed, dict) and "finances" in removed, str(removed))

    for table in ERP_TABLES + ("tasks",):
        n = count_rows(polluted, table)
        check(f"{table} còn 0 dòng sau khi purge", n == 0, f"còn {n}")

    # Chạy lần hai không được nổ lỗi (idempotent)
    again = polluted.purge_sample_data()
    check("purge chạy lại lần hai vẫn an toàn", isinstance(again, dict), str(again))

    # Thứ tự khoá ngoại: bảng `tasks` ở DB thật tham chiếu `employees` với
    # on_delete=NO ACTION, nên phải xóa trước. Xóa sai thứ tự sẽ bị chặn và
    # dữ liệu mẫu còn sót lại — hãy khẳng định thứ tự trên mã nguồn.
    db_src = (PROJECT_ROOT / "core" / "database.py").read_text(encoding="utf-8")
    order_line = next(
        (ln for ln in db_src.splitlines() if ln.strip().startswith("tables = (")),
        "",
    )
    declared = [t.strip().strip('"') for t in order_line.split("(", 1)[-1].split(")")[0].split(",")] if order_line else []
    check("có khai báo thứ tự bảng cần xóa", bool(declared), "không tìm thấy dòng tables = (...)")
    if declared:
        check(
            "tasks nằm TRƯỚC employees trong thứ tự xóa",
            "tasks" in declared
            and "employees" in declared
            and declared.index("tasks") < declared.index("employees"),
            f"thứ tự hiện tại: {declared}",
        )
        check(
            "attendance nằm trước employees (bảng con)",
            declared.index("attendance") < declared.index("employees"),
            f"thứ tự hiện tại: {declared}",
        )

    # Bản thân hàm vẫn phải xóa trọn vẹn khi cả employee lẫn task đều tồn tại
    with polluted.get_connection() as conn:
        cur = conn.execute("INSERT INTO departments (name, description) VALUES ('Phòng Mẫu 2', 'mô tả mẫu');")
        new_dept = cur.lastrowid
        conn.execute("INSERT INTO employees (dept_id, name, position) VALUES (?, 'Nguyễn Văn A', 'Kỹ sư');", (new_dept,))
        cur = conn.execute("INSERT INTO employees (dept_id, name, position) VALUES (?, 'Lê Hoàng C', 'DevOps');", (new_dept,))
        new_emp = cur.lastrowid
        conn.execute("INSERT INTO tasks (title, status, created_by_ai, assignee_id) VALUES ('Nhiệm vụ', 'pending', 0, ?);", (new_emp,))
        conn.commit()
    polluted.purge_sample_data()
    check(
        "xóa được cả khi tasks còn trỏ tới employees",
        count_rows(polluted, "employees") == 0 and count_rows(polluted, "tasks") == 0,
        "còn sót nhân viên hoặc nhiệm vụ",
    )

# Kiểm tra DB thật đã được dọn
try:
    check(
        "DB thật vnmateai.db không còn dữ liệu mẫu",
        all(count_rows(erp_db, t) == 0 for t in ERP_TABLES),
        f"còn sót: { {t: count_rows(erp_db, t) for t in ERP_TABLES if count_rows(erp_db, t)} }",
    )
    check("DB thật vẫn giữ tài khoản người dùng (không xóa nhầm)", count_rows(erp_db, "users") > 0)
except Exception as exc:  # pragma: no cover
    check("đọc được DB thật", False, str(exc))


# ──────────────────────────────────────────────────────────────────────
section("Backend STT giả lập đã bị gỡ — không còn bịa lời nói")

import core.audio_processor as ap  # noqa: E402

src = (PROJECT_ROOT / "core" / "audio_processor.py").read_text(encoding="utf-8")
check("không còn hàm _transcribe_mock", "_transcribe_mock" not in src.replace('trả câu mẫu theo độ dài', ''))
check("không còn nhánh backend == 'mock'", 'backend == "mock"' not in src)
check(
    "nhánh fallback cuối trả chuỗi rỗng, không trả câu mẫu",
    "return await self._transcribe_mock" not in src,
)

# Hành vi thật: ép mọi engine nhận dạng đều rỗng -> kết quả phải rỗng
proc = ap.AudioEngine() if hasattr(ap, "AudioEngine") else None
check("tìm thấy lớp AudioEngine để kiểm thử hành vi", proc is not None, "không có lớp AudioEngine")

if proc is not None:
    async def _all_engines_blank(_audio: bytes) -> str:
        return ""

    originals = {}
    for name in ("_transcribe_local_whisper", "_transcribe_groq", "_transcribe_whisper", "_transcribe_google"):
        if hasattr(proc, name):
            originals[name] = getattr(proc, name)
            setattr(proc, name, _all_engines_blank)
    try:
        got = asyncio.run(proc.transcribe_audio(b"\x00" * 30_000))
        check(
            "không nhận dạng được thì trả chuỗi rỗng (không bịa câu lệnh)",
            got == "",
            f"nhận về {got!r} — đây là nội dung bịa",
        )
    finally:
        for name, fn in originals.items():
            setattr(proc, name, fn)


# ──────────────────────────────────────────────────────────────────────
section("Cấu hình từ chối ASR_BACKEND='mock'")

from core.config_loader import AppSettings  # noqa: E402

validate = AppSettings._validate_asr_backend
for raw in ("mock", "MOCK", "  Mock  "):
    got = validate(raw)
    check(f"ASR_BACKEND={raw!r} bị đổi về google", got == "google", f"trả về {got!r}")

for ok_raw in ("google", "groq", "whisper", "local_whisper", "GROQ"):
    got = validate(ok_raw)
    check(f"ASR_BACKEND={ok_raw!r} hợp lệ được giữ", got == ok_raw.lower(), f"trả về {got!r}")


# ──────────────────────────────────────────────────────────────────────
section("File Excel mẫu chỉ có hàng tiêu đề, không có dữ liệu bịa")

from core.api_erp import download_erp_template  # noqa: E402


async def _get_template_bytes() -> bytes:
    resp = await download_erp_template(user={"username": "admin", "role": "admin"})
    buf = io.BytesIO()
    async for chunk in resp.body_iterator:
        buf.write(chunk)
    return buf.getvalue()


try:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(asyncio.run(_get_template_bytes())))
    expected = {"PhongBan", "NhanVien", "MayTinh", "CongViec", "SoSach"}
    check("có đủ 5 sheet", set(wb.sheetnames) == expected, str(wb.sheetnames))
    for ws in wb.worksheets:
        check(
            f"sheet {ws.title} chỉ có 1 hàng (tiêu đề), không có dòng dữ liệu",
            ws.max_row == 1,
            f"có {ws.max_row} hàng",
        )
        check(
            f"sheet {ws.title} vẫn giữ đủ tên cột",
            all(c.value for c in ws[1]),
            f"tiêu đề rỗng: {[c.value for c in ws[1]]}",
        )
    # Không còn chuỗi dữ liệu mẫu cũ
    body = " ".join(
        str(c.value) for ws in wb.worksheets for row in ws.iter_rows() for c in row if c.value
    )
    for junk in ("Nguyễn Văn A", "192.168.1.10", "SERVER-AI-MASTER", "Phòng Kỹ Thuật"):
        check(f"không còn dữ liệu bịa {junk!r}", junk not in body)
except ImportError:
    check("có openpyxl để đọc file mẫu", False, "thiếu openpyxl")


# ──────────────────────────────────────────────────────────────────────
section("Giao diện không còn chọn được backend STT giả lập")

index_html = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
check('không còn <option value="mock">', '<option value="mock">' not in index_html)
check("không còn nhãn 'Mock (Giả lập'", "Mock (Giả lập" not in index_html)
check("không còn nhãn 'Giả lập in-memory'", "Giả lập in-memory" not in index_html)


# ──────────────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
print(f"Tổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
if FAILURES:
    print("\n❌ CÓ LỖI:")
    for f in FAILURES:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("\n✅ TẤT CẢ PASS")
