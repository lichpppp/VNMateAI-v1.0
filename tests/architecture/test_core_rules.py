"""
tests/architecture/test_core_rules.py
=====================================
Quy tắc chống trùng lặp RULE-011…015 (docs/architecture/dependency-rules.md §IV)
áp lên CODE ĐANG CHẠY: core/, skills/, workers/, main.py.

Cơ chế "bánh cóc":
  * Vi phạm đang có được ghi ở `core_rules_baseline.json` (file -> số lần).
  * Vi phạm MỚI (file mới, hoặc số lần tăng) -> FAIL.
  * Số lần GIẢM so với baseline -> FAIL kèm lời nhắc hạ baseline, để mức tốt
    hơn được khoá lại và không thể lùi.

Cập nhật baseline sau khi đã xoá bớt vi phạm:
    python tests/architecture/test_core_rules.py --update-baseline
"""
from __future__ import annotations

import ast
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE_PATH = Path(__file__).with_name("core_rules_baseline.json")

SCAN_GLOBS = ["core/**/*.py", "src/mateai/**/*.py", "skills/*.py", "workers/*.py", "main.py"]

#: Nơi duy nhất được phép làm việc đó (bản canonical).
ALLOWED = {
    # audio_processor dùng SDK OpenAI cho Whisper (STT), không phải gọi LLM.
    "RULE-011": {"src/mateai/infrastructure/llm/llm_provider.py", "core/audio_processor.py"},
    "RULE-012": {"src/mateai/infrastructure/tts/tts_stream_engine.py"},
    "RULE-013": {"core/config_loader.py"},
    "RULE-014": {"src/mateai/infrastructure/database/erp_database.py"},
    "RULE-015": set(),
}

DESCRIPTIONS = {
    "RULE-011": "tạo client OpenAI / gọi chat.completions.create ngoài provider LLM",
    "RULE-012": "tổng hợp TTS (edge_tts.Communicate, gTTS, /audio/speech) ngoài TTS engine",
    "RULE-013": "tự mở config.json ngoài config_loader",
    "RULE-014": "sqlite3.connect ngoài tầng persistence",
    "RULE-015": "module lõi import ngược core.server",
}


def _files():
    seen = set()
    for pattern in SCAN_GLOBS:
        for p in sorted(ROOT.glob(pattern)):
            rel = p.relative_to(ROOT).as_posix()
            if rel not in seen and "__pycache__" not in rel:
                seen.add(rel)
                yield rel, p


def _call_name(node: ast.Call) -> str:
    return ast.unparse(node.func)


def scan() -> dict:
    """Trả về {rule: Counter({file: số lần})}."""
    found = {rule: Counter() for rule in ALLOWED}
    for rel, path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                if name.split(".")[-1] in ("OpenAI", "AsyncOpenAI") or name.endswith("chat.completions.create"):
                    found["RULE-011"][rel] += 1
                if name.split(".")[-1] in ("Communicate", "gTTS"):
                    found["RULE-012"][rel] += 1
                if name in ("sqlite3.connect",):
                    found["RULE-014"][rel] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if "/audio/speech" in node.value and not node.value.strip().startswith(("\n", "Tạo", "9Router")):
                    found["RULE-012"][rel] += 1
            elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if isinstance(node.right, ast.Constant) and node.right.value == "config.json":
                    found["RULE-013"][rel] += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                if any(m == "core.server" or m.startswith("core.server.") for m in mods):
                    if rel != "core/server.py" and rel != "main.py":
                        found["RULE-015"][rel] += 1
    for rule, allowed in ALLOWED.items():
        for rel in allowed:
            found[rule].pop(rel, None)
    return found


def _load_baseline() -> dict:
    if not BASELINE_PATH.exists():
        return {rule: {} for rule in ALLOWED}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def _check(rule: str) -> None:
    actual = scan()[rule]
    baseline = _load_baseline().get(rule, {})
    new, improved = [], []
    for rel, n in sorted(actual.items()):
        b = baseline.get(rel, 0)
        if n > b:
            new.append(f"{rel}: {n} (baseline {b})")
        elif n < b:
            improved.append(f"{rel}: {n} (baseline {b})")
    for rel, b in sorted(baseline.items()):
        if rel not in actual:
            improved.append(f"{rel}: 0 (baseline {b})")
    msg = []
    if new:
        msg.append(f"{rule} — vi phạm MỚI ({DESCRIPTIONS[rule]}):\n  " + "\n  ".join(new))
    if improved:
        msg.append(
            f"{rule} — đã giảm so với baseline, hãy khoá mức mới bằng "
            f"`python tests/architecture/test_core_rules.py --update-baseline`:\n  "
            + "\n  ".join(improved)
        )
    assert not msg, "\n".join(msg)


def test_rule_011_llm_client_only_in_provider():
    _check("RULE-011")


def test_rule_012_tts_only_in_tts_engine():
    _check("RULE-012")


def test_rule_013_config_only_via_loader():
    _check("RULE-013")


def test_rule_014_sqlite_only_in_persistence():
    _check("RULE-014")


def test_rule_015_core_does_not_import_server():
    _check("RULE-015")


if __name__ == "__main__":
    if "--update-baseline" in sys.argv:
        data = {rule: dict(sorted(c.items())) for rule, c in scan().items()}
        BASELINE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Đã ghi {BASELINE_PATH.relative_to(ROOT)}")
    for rule, counter in scan().items():
        print(f"{rule} ({DESCRIPTIONS[rule]}): {sum(counter.values())} lần / {len(counter)} file")
        for rel, n in sorted(counter.items()):
            print(f"    {n:3d}  {rel}")
