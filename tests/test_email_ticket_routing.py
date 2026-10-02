"""
tests/test_email_ticket_routing.py
==================================
Định tuyến ticket email: tìm phòng ban theo từ khoá tên, lấy nhân viên đầu tiên.
Truy vấn nằm ở tầng dữ liệu (ERPDatabase) — trước đây email_gateway (tầng giao
diện) tự chạy SQL thô (RULE-004). CSDL tạm.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mateai.infrastructure.database.erp_database import ERPDatabase  # noqa: E402


def test_department_lookup_and_first_employee(tmp_path):
    db = ERPDatabase(db_path=tmp_path / "erp.db")
    fin = db.add_department("Phòng Tài Chính")["id"]
    it = db.add_department("Phòng Kỹ Thuật")["id"]
    with db.get_connection() as conn:
        conn.execute("INSERT INTO employees (dept_id, name, position, email, phone) VALUES (?, ?, ?, ?, ?);",
                     (it, "Nguyễn Văn A", "Kỹ sư", "a@x.vn", "0900"))
        conn.commit()
    assert db.find_department_id_by_name(["tài chính", "kế toán"]) == fin
    assert db.find_department_id_by_name(["kỹ thuật", "it"]) == it
    assert db.find_department_id_by_name(["không tồn tại"]) is None
    assert db.first_employee_id_in_department(it) is not None
    assert db.first_employee_id_in_department(fin) is None
