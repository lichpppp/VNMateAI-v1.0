"""
tests/test_sqlite_single_open.py
================================
mateai.infrastructure.database.erp_database.open_sqlite là nơi duy nhất mở SQLite trong core/ (RULE-014).

Kèm lỗi tìm thấy khi gom: autonomous_sentinel.check_sql_health gọi
`PRAGMA quick_check` nhưng bỏ qua kết quả — quick_check KHÔNG ném lỗi khi file
hỏng mà trả các dòng mô tả, nên CSDL hỏng không bao giờ bị phát hiện.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import core.autonomous_sentinel as sentinel  # noqa: E402
from mateai.infrastructure.database.erp_database import open_sqlite  # noqa: E402


def test_open_sqlite_options_and_closes_on_exit(tmp_path):
    db = tmp_path / "x.db"
    with open_sqlite(db, foreign_keys=True) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert isinstance(conn.execute("SELECT 1 AS a").fetchone(), sqlite3.Row)
    try:
        conn.execute("SELECT 1")
        raise AssertionError("kết nối phải đóng khi ra khỏi khối with")
    except sqlite3.ProgrammingError:
        pass


def test_probe_does_not_switch_journal_mode(tmp_path):
    db = tmp_path / "y.db"
    sqlite3.connect(db).execute("CREATE TABLE t(a)").connection.close()
    with open_sqlite(db, wal=False) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_sentinel_healthy_db_reports_nothing(tmp_path, monkeypatch):
    db = tmp_path / "hr.db"
    sqlite3.connect(db).execute("CREATE TABLE t(a)").connection.close()
    monkeypatch.setattr(sentinel, "_DB_PATH", db)
    assert sentinel.autonomous_sentinel.check_sql_health() is None


def test_sentinel_reports_quick_check_problems(tmp_path, monkeypatch):
    db = tmp_path / "hr.db"
    sqlite3.connect(db).execute("CREATE TABLE t(a)").connection.close()
    monkeypatch.setattr(sentinel, "_DB_PATH", db)

    import mateai.infrastructure.database.erp_database as database
    real = database.open_sqlite

    class Wrapped:
        def __init__(self, conn):
            self._c = conn

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self._c.__exit__(*a)

        def execute(self, sql, *a):
            if "quick_check" in sql:
                return self._c.execute("SELECT 'row 3 missing from index idx_t'")
            return self._c.execute(sql, *a)

        def rollback(self):
            self._c.rollback()

    monkeypatch.setattr(database, "open_sqlite", lambda *a, **k: Wrapped(real(*a, **k)))
    res = sentinel.autonomous_sentinel.check_sql_health()
    assert res and "quick_check" in res["message"] and "missing from index" in res["message"]
