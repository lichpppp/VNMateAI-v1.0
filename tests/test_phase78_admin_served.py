"""
tests/test_phase78_admin_served.py
==================================
Kiểm thử Phase 78 (phần 2) — Admin phải mở được từ chính portal.

Vấn đề gốc: Admin chạy thành một server Next.js RIÊNG ở cổng 3001. Người dùng
mở http://127.0.0.1:8000/#dashboard thì không thấy gì dẫn tới Admin, và nếu
quên chạy server 3001 thì thấy "Failed to fetch" — dễ tưởng là lỗi hệ thống.

Cách sửa: build tĩnh Admin (`output: 'export'` + `basePath: '/admin'`) rồi để
FastAPI phục vụ tại /admin. Một cổng, một origin, không cần tiến trình node.

Test này canh giữ:
  - Admin có nằm trong hệ thống file và được build ra không
  - FastAPI có route phục vụ /admin và phân loại đúng loại file
  - Bảo mật: chặn path traversal, không lộ file ngoài admin/out
  - Không nới lỏng xác thực chỉ để cho Admin chạy
  - Không còn "nút giả" (link chết, nút bấm không làm gì)
"""

from __future__ import annotations

import re
import sys
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
ADMIN_DIR = PROJECT_ROOT / "admin"
OUT_DIR = ADMIN_DIR / "out"

# ──────────────────────────────────────────────────────────────────────
section("Admin được build tĩnh và nằm trong cùng hệ thống file")

check("thư mục admin/ tồn tại", ADMIN_DIR.is_dir(), "không thấy admin/")
check(
    "admin/out đã được build",
    OUT_DIR.is_dir(),
    "chạy: cd admin && npm install && npm run build",
)
for page in ("dashboard", "plugins", "routing", "settings", "workers"):
    check(
        f"có trang đã build: {page}.html",
        (OUT_DIR / "admin" / f"{page}.html").is_file(),
        "thiếu file trong admin/out/admin/",
    )
check(
    "có thư mục asset _next",
    (OUT_DIR / "_next").is_dir(),
    "thiếu asset — trang sẽ không có JS",
)

# Bản build KHÔNG được commit (nó sinh ra lại từ mã nguồn).
_gitignore = (ADMIN_DIR / ".gitignore").read_text(encoding="utf-8")
for pattern in (".next/", "node_modules/", ".env.local"):
    check(
        f".gitignore loại '{pattern}'",
        pattern in _gitignore,
        "commit build output làm repo phình và gây conflict mỗi lần build",
    )
check(
    "admin/out được git ignore",
    "out/" in _gitignore or ".next/" in _gitignore,
    "bản export tĩnh cũng là build output",
)

# ──────────────────────────────────────────────────────────────────────
section("Cấu hình build đúng cho bản tĩnh")

_cfg = (ADMIN_DIR / "next.config.js").read_text(encoding="utf-8")
check("bật output: 'export'", "output: 'export'" in _cfg, "không export tĩnh thì cần next start")
check("đặt basePath: '/admin'", "basePath: '/admin'" in _cfg, "thiếu basePath thì gọi nhầm vào portal")
check(
    "KHÔNG dùng rewrites trỏ cổng khác nữa",
    "localhost:8000" not in _cfg,
    "đã bỏ cổng riêng — Admin dùng chung origin với backend",
)

# ──────────────────────────────────────────────────────────────────────
section("FastAPI phục vụ /admin")

from core.server import _ADMIN_DIST, _admin_file  # noqa: E402

check(
    "_ADMIN_DIST trỏ đúng admin/out",
    Path(_ADMIN_DIST) == OUT_DIR,
    f"thấy {_ADMIN_DIST}",
)

_server_src = (PROJECT_ROOT / "core" / "server.py").read_text(encoding="utf-8")
check('có route GET "/admin"', '@app.get("/admin"' in _server_src, "thiếu route /admin")
check(
    'có route bắt mọi thứ dưới /admin',
    '@app.get("/admin/{rel:path}"' in _server_src,
    "thiếu route cho asset/trang con",
)
check(
    "phục vụ asset _next dưới /admin",
    '_next/' in _server_src,
    "Next gọi asset ở /admin/_next/... nhưng file nằm ở out/_next/...",
)
check(
    "phục vụ payload .txt cho client router",
    '".txt"' in _server_src,
    "App Router điều hướng phía client cần file .txt",
)
check(
    "trả HTML hướng dẫn khi chưa build, không im lặng 404",
    "_ADMIN_MISSING_HTML" in _server_src and "503" in _server_src,
    "404 trần khiến người dùng tưởng tính năng không tồn tại",
)

# Ánh xạ file hoạt động đúng.
check("_admin_file('plugins.html') -> tìm thấy", _admin_file("plugins.html") is not None)
check("_admin_file('plugins.txt') -> tìm thấy", _admin_file("plugins.txt") is not None)

# Asset có thư mục con (chunks/, media/...), nên phải thử ĐƯỜNG DẪN ĐẦY ĐỦ
# tính từ out/, chứ không phải chỉ tên file — chỉ tên file sẽ không tồn tại và
# test báo đỏ oan.
_assets = [p for p in (OUT_DIR / "_next").rglob("*") if p.is_file()]
check(
    "có asset _next để phục vụ",
    len(_assets) > 0,
    "thiếu asset — trang sẽ không có JS/CSS",
)
_mapped = sum(
    1
    for p in _assets
    if _admin_file(str(p.relative_to(OUT_DIR))) is not None
)
check(
    "mọi asset _next đều ánh xạ được từ /admin/_next/...",
    _mapped == len(_assets),
    f"chỉ map được {_mapped}/{len(_assets)} asset",
)

# ──────────────────────────────────────────────────────────────────────
section("Bảo mật: không lộ file ngoài admin/out")

# Đây là bản build tĩnh phục vụ theo đường dẫn người dùng gõ, nên phải chặn
# `..` — nếu không, /admin/../../etc/passwd sẽ đọc được file ngoài dự án.
for evil in ("../server.py", "../../etc/passwd", "../../config.json", "..%2Fconfig.json"):
    check(
        f"chặn path traversal: {evil}",
        _admin_file(evil) is None,
        "đọc được file ngoài admin/out qua đường dẫn ../",
    )
check(
    "không phục vụ cả thư mục admin/out cho tải tự do",
    'StaticFiles(directory=str(_ADMIN_DIST))' not in _server_src,
    "mount thẳng cả thư mục là mất kiểm soát đường dẫn",
)

# ──────────────────────────────────────────────────────────────────────
section("Không nới lỏng xác thực chỉ để cho Admin chạy")

check(
    "/admin không nằm trong danh sách endpoint public",
    '"/admin"' not in _server_src.split("public_endpoints")[1].split(")")[0],
    "giao diện quản trị phải sau đăng nhập, không phải ai cũng mở được",
)
check(
    "API vẫn yêu cầu Bearer token",
    "Yêu cầu xác thực tài khoản" in _server_src,
    "không được bỏ xác thực để cho tiện",
)

# ──────────────────────────────────────────────────────────────────────
section("Dùng chung phiên đăng nhập với portal")

_api = (ADMIN_DIR / "lib" / "api.ts").read_text(encoding="utf-8")
check(
    "đọc token của portal (vnmateai_token)",
    "vnmateai_token" in _api,
    "mở /admin mà phải đăng nhập lần hai thì rất phiền",
)
check(
    "vẫn đọc được khoá cũ vnmate_token",
    "vnmate_token" in _api,
    "giữ tương thích với phiên đăng nhập đã có",
)
check(
    "API gọi bằng đường dẫn tương đối /api/v1 (cùng origin, hết CORS)",
    "'/api/v1'" in _api,
    "gọi tuyệt đối qua cổng khác là cần CORS + cấu hình thêm",
)

# ──────────────────────────────────────────────────────────────────────
section("Không còn nút giả trong giao diện")

_layout = (ADMIN_DIR / "components" / "admin" / "Layout.tsx").read_text(encoding="utf-8")

# Link tới trang không tồn tại = bấm ra 404.
for dead in ("/profile", "/security", "/help"):
    check(
        f"không còn link chết {dead}",
        f'href="{dead}"' not in _layout,
        "trang này không được build — bấm là 404",
    )

# Nút bấm không làm gì.
check(
    "không còn onClick rỗng (nút giả)",
    not re.search(r"onClick=\{\(\)\s*=>\s*\{\s*\}\s*\}", _layout),
    "nút không làm gì nhưng vẫn trông như có tác dụng",
)
check(
    "nút Đăng xuất xoá token thật",
    "removeItem" in _layout and "handleSignOut" in _layout,
    "nút đăng xuất không xoá token thì phiên vẫn còn",
)

# ──────────────────────────────────────────────────────────────────────
section("Portal có đường dẫn tới Admin")

_index = (PROJECT_ROOT / "web" / "index.html").read_text(encoding="utf-8")
check(
    "portal có nút mở Trung Tâm Quản Trị",
    'id="nav-admin-center"' in _index,
    "không có đường dẫn thì không ai biết mở Admin ở đâu",
)
check(
    "nút trỏ tới /admin/dashboard",
    'href="/admin/dashboard"' in _index,
    "trỏ sai đường dẫn thì ra 404",
)
check(
    "nút mở tab mới (app riêng, không phải tab-pane của portal)",
    'target="_blank"' in _index,
    "Admin là ứng dụng riêng — nhét vào switchTab sẽ hỏng",
)
check(
    "có rel=noopener",
    'rel="noopener"' in _index,
    "mở tab mới mà không noopener thì tab cũ bị chiếm quyền window.opener",
)
check(
    "nút dùng thẻ <a> chứ không phải div onclick",
    re.search(r'<a[^>]*id="nav-admin-center"', _index) is not None,
    "<a> mở được bằng chuột phải và Ctrl+click; div thì không",
)

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
