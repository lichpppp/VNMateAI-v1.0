"""
scripts/backup.py — sao lưu / kiểm chứng / khôi phục VN-MateAI (prompt Supervisor §116).

  python scripts/backup.py create                 # tạo backups/<thời điểm>/ + manifest.json
  python scripts/backup.py verify backups/<dir>   # kiểm toàn vẹn: sha256, integrity_check, số dòng
  python scripts/backup.py restore backups/<dir> --yes   # DỪNG máy chủ trước; tự lưu trạng thái hiện tại

"Có file backup" chưa đủ: `create` tự chạy `verify` ngay sau khi chép, và `restore`
chỉ ghi đè khi bản sao lưu kiểm chứng đạt.

Bản sao lưu chứa BÍ MẬT (config.json, khoá giải mã cấu hình, khoá ký JWT, khoá thiết
bị, khoá TLS) — thư mục `backups/` không được commit (.gitignore) và phải cất ở nơi
được bảo vệ như chính máy chủ.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

#: (đường dẫn tương đối, loại). "sqlite" = sao lưu trực tuyến bằng API backup của SQLite.
ITEMS: List[tuple] = [
    ("vnmateai.db", "sqlite"),
    ("hr_kpi.db", "sqlite"),
    ("config.json", "file"),
    ("certs/config_secret.key", "file"),   # thiếu khoá này thì bí mật trong config.json không giải được
    ("certs/jwt_secret.key", "file"),
    ("certs/device_secret.key", "file"),
    ("certs/worker_secret.key", "file"),
    ("certs/server.key", "file"),
    ("certs/server.crt", "file"),
    ("storage/vector_db", "dir"),
    ("skills/registry.json", "file"),
    ("identity_core.md", "file"),
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _row_counts(db: Path) -> Dict[str, int]:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")]
        return {t: con.execute(f'SELECT COUNT(*) FROM "{t}";').fetchone()[0] for t in tables}
    finally:
        con.close()


def _integrity(db: Path) -> str:
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        return str(con.execute("PRAGMA integrity_check;").fetchone()[0])
    finally:
        con.close()


def create(root: Path = ROOT, out_dir: Path | None = None) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = out_dir or (root / "backups" / stamp)
    dest.mkdir(parents=True, exist_ok=False)
    t0 = time.perf_counter()
    manifest: Dict[str, Any] = {"created_at": datetime.now().isoformat(timespec="seconds"), "items": {}}
    for rel, kind in ITEMS:
        src = root / rel
        if not src.exists():
            manifest["items"][rel] = {"kind": kind, "present": False}
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if kind == "sqlite":
            # API backup: bản chụp nhất quán kể cả khi máy chủ đang ghi (WAL).
            src_con = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
            dst_con = sqlite3.connect(target)
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
                src_con.close()
            manifest["items"][rel] = {"kind": kind, "present": True, "sha256": _sha256(target),
                                      "rows": _row_counts(target)}
        elif kind == "dir":
            shutil.copytree(src, target)
            files = {p.relative_to(target).as_posix(): _sha256(p) for p in sorted(target.rglob("*")) if p.is_file()}
            manifest["items"][rel] = {"kind": kind, "present": True, "files": files}
        else:
            shutil.copy2(src, target)
            manifest["items"][rel] = {"kind": kind, "present": True, "sha256": _sha256(target)}
    manifest["duration_s"] = round(time.perf_counter() - t0, 2)
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    problems = verify(dest)
    if problems:
        raise RuntimeError("Sao lưu vừa tạo KHÔNG đạt kiểm chứng: " + "; ".join(problems))
    return dest


def verify(dest: Path) -> List[str]:
    """Danh sách lỗi (rỗng = đạt)."""
    mf = dest / "manifest.json"
    if not mf.is_file():
        return ["thiếu manifest.json"]
    manifest = json.loads(mf.read_text(encoding="utf-8"))
    problems: List[str] = []
    for rel, info in manifest["items"].items():
        if not info.get("present"):
            continue
        path = dest / rel
        if info["kind"] == "dir":
            for f, digest in info["files"].items():
                p = path / f
                if not p.is_file() or _sha256(p) != digest:
                    problems.append(f"{rel}/{f}: thiếu hoặc sai sha256")
            continue
        if not path.is_file():
            problems.append(f"{rel}: thiếu")
            continue
        if _sha256(path) != info["sha256"]:
            problems.append(f"{rel}: sai sha256")
        if info["kind"] == "sqlite":
            ok = _integrity(path)
            if ok != "ok":
                problems.append(f"{rel}: integrity_check = {ok}")
            if _row_counts(path) != info["rows"]:
                problems.append(f"{rel}: số dòng không khớp manifest")
    return problems


def restore(dest: Path, root: Path = ROOT) -> Path:
    """Ghi đè trạng thái hiện tại bằng bản sao lưu ĐÃ kiểm chứng. Trước đó lưu trạng thái
    hiện tại vào backups/pre-restore-<thời điểm>/ để quay lại được."""
    problems = verify(dest)
    if problems:
        raise RuntimeError("Không khôi phục: bản sao lưu không đạt kiểm chứng — " + "; ".join(problems))
    safety = create(root, out_dir=root / "backups" / f"pre-restore-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    manifest = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
    for rel, info in manifest["items"].items():
        if not info.get("present"):
            continue
        src, target = dest / rel, root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if info["kind"] == "dir":
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src, target)
        else:
            for suffix in ("-wal", "-shm"):   # nhật ký WAL cũ không được áp lên bản khôi phục
                side = Path(str(target) + suffix)
                if side.exists():
                    side.unlink()
            shutil.copy2(src, target)
    return safety


def main() -> int:
    ap = argparse.ArgumentParser(description="Sao lưu / kiểm chứng / khôi phục VN-MateAI")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("create")
    v = sub.add_parser("verify")
    v.add_argument("path")
    r = sub.add_parser("restore")
    r.add_argument("path")
    r.add_argument("--yes", action="store_true", help="xác nhận đã DỪNG máy chủ và muốn ghi đè")
    args = ap.parse_args()
    if args.cmd == "create":
        dest = create()
        m = json.loads((dest / "manifest.json").read_text(encoding="utf-8"))
        print(f"OK {dest} ({m['duration_s']} s) — đã kiểm chứng. Thư mục chứa BÍ MẬT, cất ở nơi an toàn.")
        return 0
    if args.cmd == "verify":
        problems = verify(Path(args.path))
        print("ĐẠT" if not problems else "KHÔNG ĐẠT:\n  " + "\n  ".join(problems))
        return 0 if not problems else 2
    if not args.yes:
        print("Khôi phục ghi đè dữ liệu hiện tại. Dừng máy chủ rồi chạy lại với --yes.")
        return 1
    safety = restore(Path(args.path))
    print(f"Đã khôi phục từ {args.path}. Trạng thái trước khi khôi phục: {safety}")
    return 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
