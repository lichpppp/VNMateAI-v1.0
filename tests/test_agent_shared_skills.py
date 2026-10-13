# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
tests/test_agent_shared_skills.py
=================================
Máy chủ chạy Docker (Mac / Linux) điều phối máy trạm Windows qua Agent — 2026-10-13:

  - kỹ năng máy chủ đẩy / đóng gói xuống máy trạm từng lỗi TypeError vì `export_skill` của Agent không nhận
    `needs_desktop=…` (auto_play_music) → không nạp được;
  - phím media, mở nhạc / YouTube / Notepad chỉ có trên máy chủ → gọi tới máy trạm báo "không tìm thấy kỹ năng";
  - open_text_file_notepad báo "thành công" khi không thấy file và mở được tệp ngoài Desktop (`..\\..\\x`);
  - gói cài máy chủ không kèm VNMateAgent.exe (dist/ không có trong git) → máy trạm phải tự cài Python.
Không gọi mạng, không mở cửa sổ.
"""
from __future__ import annotations

import ast
import io
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = [ln.strip() for ln in (ROOT / "client_agent" / "shared_skills.txt").read_text(encoding="utf-8").splitlines()
          if ln.strip() and not ln.startswith("#")]


@pytest.fixture
def agent_pm():
    """`core.plugin_manager` của AGENT (không phải của máy chủ) — hoàn nguyên sys.modules sau test."""
    saved_modules, saved_path = dict(sys.modules), list(sys.path)
    for name in [m for m in sys.modules if m == "core" or m.startswith("core.") or m == "skills" or m.startswith("skills.")]:
        del sys.modules[name]
    sys.path.insert(0, str(ROOT / "client_agent"))
    try:
        import core.plugin_manager as pm
        assert Path(pm.__file__).resolve().parent.parent == ROOT / "client_agent"
        yield pm
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path


def test_agent_accepts_server_contract_kwargs(agent_pm, tmp_path):
    (tmp_path / "s.py").write_text(
        "from core.plugin_manager import export_skill\n"
        "@export_skill(name='x', description='d', parameters_schema={'type': 'object', 'properties': {}},\n"
        "              needs_desktop=True, data_classification='INTERNAL')\n"
        "def x():\n    return {'ok': 1}\n", encoding="utf-8")
    pm = agent_pm.ClientPluginManager(skills_dir=tmp_path)
    assert pm.load_plugins() == 1
    assert pm.execute_skill("x") == {"status": "success", "skill": "x", "ok": 1}


def test_agent_loads_desktop_skills_from_project_checkout(agent_pm):
    pm = agent_pm.ClientPluginManager()
    pm.load_plugins()
    names = set(pm.get_skill_names())
    assert {"media_control", "auto_play_music", "open_youtube", "open_text_file_notepad"} <= names
    assert pm.execute_skill("media_control", {"action": "rm -rf"})["status"] == "error"


def test_shared_skills_exist_and_only_use_stdlib(agent_pm):
    assert SHARED, "shared_skills.txt rỗng"
    for name in SHARED:
        path = ROOT / "skills" / name
        assert path.is_file(), name
        assert not (ROOT / "client_agent" / "skills" / name).exists(), f"{name}: một tính năng một bản mã"
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                   [node.module] if isinstance(node, ast.ImportFrom) else []
            for mod in mods:
                top = mod.split(".")[0]
                assert top in sys.stdlib_module_names or mod == "core.plugin_manager", f"{name} import {mod}"


def test_source_package_ships_shared_skills():
    from mateai.interfaces.http import agent_packages
    names = set(zipfile.ZipFile(io.BytesIO(agent_packages._source_zip())).namelist())
    assert {f"skills/{n}" for n in SHARED} <= names
    assert "skills/registry.json" not in names and "shared_skills.txt" in names


def test_notepad_refuses_paths_outside_desktop(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_np", ROOT / "skills" / "auto_open_notepad_file.py")
    np_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(np_mod)
    desk = tmp_path / "Desktop"
    desk.mkdir()
    (tmp_path / "secret.txt").write_text("x")
    (desk / "ghi_chu.txt").write_text("y")
    opened = []
    monkeypatch.setattr(np_mod.os, "name", "nt")
    monkeypatch.setattr(np_mod, "_get_desktop_paths", lambda: [str(desk)])
    monkeypatch.setattr(np_mod.subprocess, "Popen", lambda args, **kw: opened.append(args))

    r = np_mod.open_text_file_notepad("..\\secret.txt")
    assert r["status"] == "error" and not opened                      # chỉ tên tệp trên Desktop
    assert np_mod.open_text_file_notepad("khong_co.txt")["status"] == "error"
    ok = np_mod.open_text_file_notepad("ghi_chu.txt")
    assert ok["status"] == "success" and opened == [["notepad.exe", str(desk / "ghi_chu.txt")]]


def test_release_refuses_stale_agent_build(monkeypatch, tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_br", ROOT / "scripts" / "build_release.py")
    br = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(br)
    fake_root, app = tmp_path / "root", tmp_path / "app"
    (app / "client_agent").mkdir(parents=True)
    (app / "client_agent" / "agent.py").write_text('AGENT_VERSION = "9.9.9"\n')
    win = fake_root / "dist" / "agent" / "windows"
    win.mkdir(parents=True)
    (win / "VNMateAgent.exe").write_bytes(b"MZ")
    (win / "version.txt").write_text("9.9.8")
    monkeypatch.setattr(br, "ROOT", fake_root)
    with pytest.raises(SystemExit):
        br.copy_agent_builds(app)
    (win / "version.txt").write_text("9.9.9")
    br.copy_agent_builds(app)
    assert (app / "dist" / "agent" / "windows" / "VNMateAgent.exe").read_bytes() == b"MZ"
