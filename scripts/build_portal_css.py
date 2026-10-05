"""
scripts/build_portal_css.py
===========================
Build CSS Tailwind cho Portal: web/tailwind/input.css -> web/tailwind.css (đã nén).
Quét web/index.html + web/app.js (cấu hình: web/tailwind/tailwind.config.js).

Dùng Tailwind CLI có sẵn trong admin/node_modules (cài bằng `npm install` trong admin/).
Chạy lại mỗi khi thêm lớp Tailwind MỚI vào index.html / app.js:

    python scripts/build_portal_css.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "admin" / "node_modules" / ".bin" / ("tailwindcss.cmd" if os.name == "nt" else "tailwindcss")


def main() -> None:
    for stream in (sys.stdout, sys.stderr):          # console Windows cp1252
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not CLI.exists():
        sys.exit(f"Không thấy Tailwind CLI ({CLI}). Chạy `npm install` trong thư mục admin/ trước.")
    out = ROOT / "web" / "tailwind.css"
    subprocess.run([str(CLI), "-c", "tailwind.config.js", "-i", "input.css", "-o", str(out), "--minify"],
                   cwd=ROOT / "web" / "tailwind", check=True)
    print(f"Đã build {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
