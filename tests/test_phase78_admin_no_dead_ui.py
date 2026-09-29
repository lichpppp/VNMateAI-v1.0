"""
tests/test_phase78_admin_no_dead_ui.py
======================================
Kiểm thử Phase 78 (phần 3) — thu hợp trùng lặp, không còn UI giả.

Bối cảnh
--------
Sau khi làm xong trang Plugins, kiểm tra lại thì phát hiện SÁNG trang Dashboard
cũng gọi endpoint không tồn tại: /dashboard/stats, /dashboard/audit-logs,
/dashboard/approvals, /workers — cả 4 đều trả 404. Đây là lần thứ BA cùng một
lớp lỗi: dựng giao diện trước, rồi giả định có API.

Ngoài ra hai trang khác cũng "có vẻ chạy" nhưng không:
  - /admin/routing: bảng rỗng + nút Lưu không đi đâu, vì không có bảng
    `routing_rules` và GET /routing/rules trả 404.
  - /admin/settings: form tĩnh, nút "Lưu" không gọi API nào.

Nguyên tắc xuyên suốt: giao diện không được trông như có khả năng khi thật sự
không có. Nếu backend chưa có thì phải nói rõ là chưa có, kèm phần còn thiếu.

Test này quét TOÀN BỘ endpoint mà frontend Admin gọi và đối chiếu với
FastAPI xem cái nào thật sự tồn tại.
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
ADMIN = PROJECT_ROOT / "admin"


def code_only(path: Path) -> str:
    """
    Bỏ comment trước khi quét.

    Cần thiết: nhiều file giải thích chi tiết những thứ ĐÃ bị gỡ ("trước đây
    gọi /dashboard/stats", "Vẽ 17/17 là bịa..."). Đó là tài liệu cần giữ, không
    phải code còn sót. Quét cả comment thì mọi assertion "không còn X" đều đỏ.
    """
    src = path.read_text(encoding="utf-8")
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    src = re.sub(r"^\s*//.*$", "", src, flags=re.M)
    src = re.sub(r"//.*$", "", src, flags=re.M)
    return src

# ──────────────────────────────────────────────────────────────────────
section("Mọi endpoint Admin gọi đều thật sự tồn tại")

import core.server as server_mod  # noqa: E402

_server_src = Path(server_mod.__file__).read_text(encoding="utf-8")

# Gom mọi đường dẫn dạng this.request<...>('/xxx') trong lib/api.ts.
# Bỏ comment trước: file có chú thích giải thích endpoint đã gỡ.
_api = code_only(ADMIN / "lib" / "api.ts")
_paths = sorted(
    {
        p.rstrip("/")  # template literal `.../${id}` bị bắt thành "routing/rules/"
        for p in re.findall(r"this\.request<[^>]*>\(\s*[`'\"]/?([a-z0-9/_-]+)", _api)
    }
)

check("có endpoint để quét", len(_paths) > 0, "không tìm thấy lời gọi nào")

# Những endpoint biết trước là CHƯA có backend — phải được khai báo tường minh
# để test này chấp nhận, thay vì im lặng bỏ qua.
CHUA_CO_BACKEND = {
    "routing/rules": "chưa có bảng routing_rules và chưa có endpoint CRUD",
}

for path in _paths:
    if any(path.startswith(k) for k in CHUA_CO_BACKEND):
        check(
            f"/{path} được khai báo là chưa có backend",
            any(path.startswith(k) for k in CHUA_CO_BACKEND),
            "phải nêu rõ lý do thay vì trông như chạy được",
        )
        continue

    # Route trong server.py viết đầy đủ "/api/v1/...".
    check(
        f"/api/v1/{path} có trong backend",
        f'"/api/v1/{path}"' in _server_src,
        "frontend gọi endpoint không tồn tại — trang sẽ trắng hoặc báo lỗi",
    )

# ──────────────────────────────────────────────────────────────────────
section("Không còn endpoint bịa của các phase trước")

_api_client = _api  # đã bỏ comment
for gone in ("/dashboard/stats", "/dashboard/audit-logs", "/dashboard/approvals", "request<'/workers'"):
    check(
        f"không còn gọi {gone}",
        gone not in _api_client,
        "endpoint này trả 404 — đã làm trang tương ứng trắng hoàn toàn",
    )

# Trang dashboard nay phải dùng endpoint mà portal đang dùng.
for real in ("/system/stats", "/logs/recent", "/enterprise/hitl/pending"):
    check(
        f"dashboard dùng endpoint thật {real}",
        real in _api_client,
        "phải dùng chung nguồn với portal để hai nơi không lệch nhau",
    )

# ──────────────────────────────────────────────────────────────────────
section("Nút 'Kiểm tra' ping thật, không đọc lại cấu hình")

_hooks_plugins = (ADMIN / "hooks" / "usePlugins.ts").read_text(encoding="utf-8")
check(
    "dùng skill check_connector_health để ping thật",
    "pingConnector" in _hooks_plugins and "check_connector_health" in _api_client,
    "đọc lại cấu hình trong bộ nhớ KHÔNG phải kiểm tra kết nối",
)
check(
    "báo lỗi thật do dịch vụ trả về",
    "c.error" in _hooks_plugins,
    "giấu lỗi gốc thì người vận hành không biết vì sao hỏng",
)

# ──────────────────────────────────────────────────────────────────────
section("Trang thiếu nguồn dữ liệu phải nói rõ, không dựng UI giả")

_routing = (ADMIN / "app" / "admin" / "routing" / "page.tsx").read_text(encoding="utf-8")
check(
    "trang Routing nói rõ chưa có nơi lưu quy tắc",
    "routing_rules" in _routing and "Chưa có nơi lưu quy tắc" in _routing,
    "bảng rỗng + nút Lưu không lưu được là UI giả",
)
check(
    "trang Routing KHÔNG còn form DynamicForm gắn vào nút lưu",
    "<DynamicForm" not in _routing,
    "form nhận dữ liệu rồi bỏ đi = mất thông tin mà không báo",
)
check(
    "trang Routing nêu rõ phần backend còn thiếu",
    "routing/rules" in _routing,
    "phải chỉ rõ cần endpoint nào thì làm được",
)

_workers = code_only(ADMIN / "app" / "admin" / "workers" / "page.tsx")
check(
    "trang Worker nói rõ chưa có nguồn dữ liệu",
    "Chưa có nguồn dữ liệu" in _workers,
    "'17/17 Mac Minis Online' là số không có nguồn",
)
# Đã bỏ comment — chỉ còn code hiển thị. "17/17" trong file nằm ở chú thích
# giải thích lý do không vẽ, nên không được tính vào.
check(
    "trang Worker KHÔNG vẽ danh sách máy bịa",
    "17/17" not in _workers and "Mac Minis Online" not in _workers,
    "bịa cụm máy không tồn tại",
)
check(
    "trang Worker nêu endpoint cần có",
    "/api/v1/workers" in _workers,
    "phải chỉ rõ cần gì để lấp đầy",
)

_settings = code_only(ADMIN / "app" / "admin" / "settings" / "page.tsx")
check(
    "trang Settings dẫn về trình chỉnh của portal",
    "#config" in _settings,
    "portal đã có trình chỉnh cấu hình — không cần dựng thêm",
)
# Bản cũ có 4 nút "Lưu ..." (Lưu Changes / Lưu Security Settings / Lưu
# Notification Settings / Lưu Database Config) — tất cả đều không gọi API nào.
# Kiểm tra đúng các nhãn nút đó. Dùng ">\s*Lưu\b" sẽ dính nhầm vào câu
# "Lưu ý bảo mật" vốn là văn bản cảnh báo hợp lệ.
_OLD_SAVE_LABELS = (
    "Lưu Changes",
    "Lưu Security Settings",
    "Lưu Notification Settings",
    "Lưu Database Config",
)
check(
    "trang Settings KHÔNG còn 4 nút Lưu giả",
    not any(lbl in _settings for lbl in _OLD_SAVE_LABELS),
    "nút Lưu không gọi API nào thì bấm xong không có gì xảy ra",
)
check(
    "trang Settings cảnh báo endpoint cấu hình trả khoá thật",
    "/api/v1/config" in _settings,
    "GET /api/v1/config trả khoá API dạng chữ thường cho manager/admin",
)

# ──────────────────────────────────────────────────────────────────────
section("Không còn component chết")

_dashboard_page = (ADMIN / "app" / "admin" / "dashboard" / "page.tsx").read_text(encoding="utf-8")
check(
    "trang dashboard không còn nhúng WorkerStatus",
    "WorkerStatus" not in _dashboard_page,
    "component đã bị gỡ thì không được import nữa",
)
check(
    "WorkerStatus.tsx đã bị xoá khỏi đĩa",
    not (ADMIN / "components" / "admin" / "WorkerStatus.tsx").exists(),
    "để lại component không ai dùng là gánh nặng vô nghĩa",
)
check(
    "getConnectorHealth đã gỡ khỏi api.ts",
    "getConnectorHealth" not in _api_client,
    "bị thay hoàn toàn bởi pingConnector",
)

# ──────────────────────────────────────────────────────────────────────
section("Trùng lặp: portal và Admin cùng đọc một nguồn")

# Nếu Admin đọc nguồn khác portal thì hai màn hình sẽ hiện hai con số khác nhau
# cho cùng một thứ — người dùng không biết tin nào.
_portal = (PROJECT_ROOT / "web" / "app.js").read_text(encoding="utf-8")
for shared, why in (
    ("/api/v1/system/stats", "số liệu tổng quan"),
    ("/api/v1/logs/recent", "nhật ký hoạt động"),
    ("/api/v1/enterprise/hitl/pending", "phê duyệt chờ"),
):
    check(
        f"portal và Admin cùng dùng {shared} ({why})",
        shared in _portal and (shared.replace("/api/v1", "") in _api_client),
        "hai nguồn khác nhau = hai con số khác nhau cho cùng một thứ",
    )

# Với connector thì không dùng chung endpoint: portal hiện "đã cấu hình hay
# chưa" qua /connectors/health, còn Admin đọc /connectors/catalog (có thêm
# schema cấu hình). Cả hai đều dẫn tền cùng một nguồn sự thật là
# CONNECTOR_REGISTRY, và điều quan trọng hơn: nút bấm kiểm tra của cả hai nơi
# đều gọi CHUNG một skill nên không thể cho hai kết quả khác nhau.
check(
    "Admin và portal cùng ping bằng skill check_connector_health",
    "check_connector_health" in _api_client and "check_connector_health" in _portal,
    "hai nơi dùng hai cách kiểm tra là hai kết quả khác nhau cho cùng một dịch vụ",
)
check(
    "Admin lấy danh mục connector từ registry của backend",
    "/enterprise/connectors/catalog" in _api_client,
    "không tự chế danh sách connector ở frontend",
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
