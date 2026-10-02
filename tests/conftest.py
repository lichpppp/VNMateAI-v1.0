"""
tests/conftest.py
=================
Cho phép chạy TOÀN BỘ test bằng một lệnh: `python -m pytest`.

Repo có ba kiểu test:
  1. Hàm `test_*` chuẩn pytest — pytest tự thu thập như thường.
  2. File dạng script: chạy check ngay khi import rồi `sys.exit(...)` ở cấp
     module. Import trong tiến trình pytest sẽ làm hỏng cả phiên, nên mỗi file
     được chạy ở một tiến trình Python riêng; mã thoát 0 = pass.
  3. File `.mjs` (Node) — chạy `node <file>` ở tiến trình riêng.

Ngoài ra conftest sao lưu các file cấu hình thật trước phiên test và trả lại
nguyên trạng sau phiên, vì một số test cũ ghi thẳng vào chúng.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# DB thật không được đụng tới khi chạy test: đặt trước khi bất kỳ test nào
# import `core` (các singleton như `erp_db` đọc đường dẫn lúc import). Tiến
# trình con (test dạng script) kế thừa biến môi trường này.
# Trước khi có bước này, test HITL ghi sự kiện giả vào bảng audit_logs thật
# (bảng chỉ-ghi) và test repository chèn task giả vào vnmateai.db.
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="vnmateai-test-data-"))
os.environ["VNMATEAI_DB_PATH"] = str(_TEST_DATA_DIR / "vnmateai.db")
os.environ["VNMATEAI_HR_DB_PATH"] = str(_TEST_DATA_DIR / "hr_kpi.db")
# config.json thật có token bot Telegram thật: test không được gửi tin chủ động
# (cảnh báo, yêu cầu duyệt) vào nhóm vận hành. Tiến trình con kế thừa biến này.
os.environ["VNMATEAI_TELEGRAM_OUTBOUND"] = "off"

#: File cấu hình thật mà test có thể ghi vào. DB SQLite KHÔNG nằm trong danh
#: sách: server có thể đang mở DB, chép đè lên DB đang mở sẽ làm hỏng dữ liệu.
_PROTECTED_FILES = [
    ROOT / "config.json",
    ROOT / "users.json",
    ROOT / "skills" / "registry.json",
    ROOT / "config" / "data_sources.json",
]

#: Nhãn cho các file script (không gắn được decorator vào file script).
_SCRIPT_MARKERS = {
    # Gọi model thật qua 9Router; kết quả đổi theo danh sách model ngoài mạng.
    "test_phase68_no_hardcoded_models.py": ["network"],
}

_SCRIPT_TIMEOUT_S = 240


def _is_main_guard(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.If)
        and "__name__" in ast.unparse(node.test)
        and "__main__" in ast.unparse(node.test)
    )


def _is_script_style(path: Path) -> bool:
    """File chạy check ngay khi import (có `sys.exit` ngoài `if __name__ == ...`)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in tree.body:
        if _is_main_guard(node):
            continue
        if "sys.exit" in ast.unparse(node):
            return True
    return False


def _subprocess_env() -> dict:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


class ScriptItem(pytest.Item):
    def __init__(self, *, command, **kwargs):
        super().__init__(**kwargs)
        self.command = command
        for marker in _SCRIPT_MARKERS.get(self.path.name, []):
            self.add_marker(marker)

    def runtest(self):
        try:
            proc = subprocess.run(
                self.command,
                cwd=ROOT,
                env=_subprocess_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_SCRIPT_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScriptFailure(f"quá {_SCRIPT_TIMEOUT_S}s", str(exc.stdout or ""))
        if proc.returncode != 0:
            raise ScriptFailure(f"mã thoát {proc.returncode}", proc.stdout + proc.stderr)

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, ScriptFailure):
            reason, output = excinfo.value.args
            tail = "\n".join(output.strip().splitlines()[-40:])
            return f"{self.path.name}: {reason}\n{tail}"
        return super().repr_failure(excinfo)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"


class ScriptFailure(Exception):
    pass


class PythonScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(
            self, name=self.path.name, command=[sys.executable, str(self.path)]
        )


class NodeScriptFile(pytest.File):
    def collect(self):
        node = shutil.which("node")
        item = ScriptItem.from_parent(
            self, name=self.path.name, command=[node or "node", str(self.path)]
        )
        if node is None:
            item.add_marker(pytest.mark.skip(reason="không tìm thấy node"))
        yield item


def pytest_pycollect_makemodule(module_path, parent):
    if _is_script_style(Path(module_path)):
        return PythonScriptFile.from_parent(parent, path=Path(module_path))
    return None


def pytest_collect_file(parent, file_path):
    if file_path.suffix == ".mjs" and file_path.name.startswith("test_"):
        return NodeScriptFile.from_parent(parent, path=file_path)
    return None


@pytest.fixture(scope="session", autouse=True)
def _protect_real_config_files():
    backup_dir = Path(tempfile.mkdtemp(prefix="vnmateai-test-backup-"))
    saved = {}
    for path in _PROTECTED_FILES:
        if path.exists():
            dst = backup_dir / f"{len(saved)}_{path.name}"
            shutil.copy2(path, dst)
            saved[path] = dst
    try:
        yield
    finally:
        for path in _PROTECTED_FILES:
            if path in saved:
                shutil.copy2(saved[path], path)
            elif path.exists():
                # Test tạo ra file vốn không tồn tại -> xoá để trả lại nguyên trạng.
                path.unlink()
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.rmtree(_TEST_DATA_DIR, ignore_errors=True)
