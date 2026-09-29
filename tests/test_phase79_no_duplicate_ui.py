"""
tests/test_phase79_no_duplicate_ui.py
=====================================
Kiểm thử Phase 79 — cấu trúc tab không còn trùng lặp.

Bối cảnh
---------
Người dùng báo "các tab trùng lặp rất nhiều, cần gom lại". Khi truy nguyên
thì ra hai loại vấn đề khác nhau:

  1. Trùng vì ĐỂ Ý ĐỊNH — cùng một dữ liệu ở nhiều tab:
       - Nhật ký: 5 nơi (tab Nhật Ký, tab Bảo Mật, tab Công Việc,
         Trung Tâm Chỉ Huy, Bảng Điều Khiển)
       - Hàng đợi phê duyệt: 3 nơi, lấy từ 2 endpoint KHÁC NHAU
         (/enterprise/hitl/pending và /security/pending-action) nên số liệu
         có thể lệch nhau
       - Mạch ESP32: 2 nơi
     Nay: nhật ký gom về tab Nhật Ký (3 chế độ), phê duyệt về Bảng Điều Khiển,
     ESP32 về tab Trợ Lý Thoại.

  2. Trùng vì LỖI KỸ THUẬT — HÀM TRÙNG TÊN trong app.js. Trong JS định
     nghĩa sau đè định nghĩa trước, nên bản "thắng" âm thầm khiến bản kia
     chết. Đã phát hiện và sửa 3 cặp:
       - renderDevicesTable      → bảng tab Thiết Bị trống, không giải thích
       - loadAudioNodes          → ô đếm ESP32 kẹt ở số 0 viết cứng trong HTML
       - togglePasswordVisibility → biểu tượng mắt không bao giờ đổi

Loại 2 nguy hiểm hơn nhiều: trùng lặp về mặt giao diện chỉ gây rối, còn trùng
tên hàm âm thầm phá chức năng mà không có lỗi nào hiện ra console. Nên test này
quét toàn file thay vì kiểm từng chỗ.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  ✅ {name}")
    else:
        FAILED += 1
        FAILURES.append(f"{name} — {detail}")
        print(f"  ❌ {name}  {detail}")


def section(title: str) -> None:
    print(f"\n▸ {title}")


ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

JS_CODE = re.sub(r"/\*[\s\S]*?\*/", "", JS)
JS_CODE = re.sub(r"^\s*//.*$", "", JS_CODE, flags=re.M)
JS_CODE = re.sub(r"//.*$", "", JS_CODE, flags=re.M)


def section_html(tab: str) -> str:
    m = re.search(r'id="tab-%s"[\s\S]*?\n    </section>' % tab, HTML)
    return m.group(0) if m else ""


# ──────────────────────────────────────────────────────────────────────
section("Không còn hàm trùng tên trong app.js")

# Đây là loại lỗi đã phá chức năng 3 lần mà không có dấu hiệu gì trên UI.
defs: dict[str, list[int]] = defaultdict(list)
for m in re.finditer(r"^(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", JS_CODE, re.M):
    defs[m.group(1)].append(JS_CODE[: m.start()].count("\n") + 1)

dupes = {k: v for k, v in defs.items() if len(v) > 1}
check(
    "không còn hàm nào được định nghĩa 2 lần",
    not dupes,
    "; ".join(f"{k} tại dòng {v}" for k, v in list(dupes.items())[:5]),
)
check(
    "số hàm cấp module vẫn hợp lý",
    len(defs) > 200,
    f"chỉ còn {len(defs)} hàm — có thể đã xoá nhầm",
)

# 3 cặp từng gây lỗi — khẳng định chúng không quay lại.
for name in ("renderDevicesTable", "loadAudioNodes", "togglePasswordVisibility"):
    check(
        f"hàm '{name}' chỉ còn 1 bản định nghĩa",
        len(defs.get(name, [])) <= 1,
        f"định nghĩa tại dòng {defs.get(name)}",
    )
check(
    "bản ERP đã đổi tên thành renderDeptDevicesTable",
    "renderDeptDevicesTable" in JS and "renderDevicesTable(list)" in JS,
    "hai chức năng khác nhau phải có tên khác nhau",
)

# ──────────────────────────────────────────────────────────────────────
section("Nhật ký chỉ còn  một chỗ")

for tab, ids in (
    ("logs", ("audit-table-body", "kpi-logs-table-body", "log-output")),
    ("security", ("audit-table-body",)),
    ("tasks", ("kpi-logs-table-body",)),
    ("command-center", ("cc-ops-log",)),
):
    body = section_html(tab)
    for eid in ids:
        if eid in ("audit-table-body", "kpi-logs-table-body"):
            if tab == "logs":
                check(f"tab Nhật Ký chứa #{eid}", f'id="{eid}"' in body)
            else:
                check(
                    f"tab {tab} KHÔNG còn #{eid}",
                    f'id="{eid}"' not in body,
                    "bảng nhật ký phải ở một chỗ duy nhất",
                )

check(
    "tab Nhật Ký có bộ chuyển 3 chế độ",
    all(f'data-log-view="{v}"' in section_html("logs") for v in ("stream", "security", "kpi")),
    "thiếu chế độ nghĩa là bảng nhật ký bị giấu không tìm thấy",
)
check(
    "hàm switchLogView tồn tại",
    "function switchLogView" in JS,
    "không có hàm thì bộ chuyển không hoạt động",
)
check(
    "bảng nhật ký cũ có nút dẫn tới nơi mới",
    "switchLogView('security')" in section_html("security")
    and "switchLogView('kpi')" in section_html("tasks"),
    "bỏ bảng đi mà không có đường dẫn thì người dùng mất tính năng",
)

# ──────────────────────────────────────────────────────────────────────
section("Hàng đợi phê duyệt chỉ có một bản đầy đủ")

check(
    "Bảng Điều Khiển là chủ sở hữu (có danh sách)",
    'id="dash-pending-list"' in section_html("dashboard"),
    "chủ sở hữu phải giữ danh sách đầy đủ",
)
for tab, eid in (("command-center", "cc-pending-list"), ("security", "security-pending-list")):
    check(
        f"tab {tab} không còn danh sách trùng",
        f'id="{eid}"' not in section_html(tab),
        "hai bản đầy đủ lấy từ 2 endpoint khác nhau nên số liệu có thể lệch",
    )
for tab in ("command-center", "security"):
    check(
        f"tab {tab} còn ô đếm + nút dẫn tới chủ sở hữu",
        "dash-pending-list" in section_html(tab),
        "bỏ hẳn thì mất thông tin; phải để lại đường dẫn",
    )
check(
    "loadPending() ghi vào id của chủ sở hữu",
    "$('dash-pending-list')" in JS,
    "ghi vào id đã xoá thì danh sách không bao giờ có dữ liệu",
)

# ──────────────────────────────────────────────────────────────────────
section("Mạch ESP32 chỉ ở tab Trợ Lý Thoại")

check("tab Trợ Lý Thoại giữ danh sách ESP32", 'id="audio-nodes-list"' in section_html("voice"))
check(
    "bảng Điều Khiển không còn vẽ lại danh sách ESP32",
    'id="audio-nodes-container"' not in section_html("dashboard"),
    "cùng dữ liệu ở hai tab là chỗ dễ phát sinh lệch số liệu",
)
check(
    "Bảng Điều Khiển còn ô tóm tắt + link sang tab Trợ Lý Thoại",
    'id="badge-audio-nodes-count"' in section_html("dashboard")
    and "switchTab('voice')" in section_html("dashboard"),
    "bỏ hẳn thì mất thông tin; phải để lại đường dẫn",
)
check(
    "cả hai ô đếm cùng chạy một hàm",
    JS.count("loadAudioNodes") >= 1 and "badge-audio-nodes-count" in JS,
    "hai hàm riêng sẽ cho hai con số khác nhau",
)

# ──────────────────────────────────────────────────────────────────────
section("Số tab đã giảm và không còn tab rỗng")

tabs = re.findall(r'<section[^>]*id="tab-([a-z0-9-]+)"', HTML)
check(
    "còn 10 tab (từ 12)",
    len(tabs) == 10,
    f"hiện có {len(tabs)}: {tabs}",
)
navs = re.findall(r'id="nav-([a-z0-9-]+)"', HTML)
check(
    "không còn nút nav cho tab đã gộp",
    "users" not in navs and "devices" not in navs,
    f"còn: {navs}",
)
valid = re.search(r"const VALID_TABS = \[([^\]]*)\]", JS)
valid_tabs = re.findall(r"'([a-z0-9-]+)'", valid.group(1)) if valid else []
check(
    "VALID_TABS khớp đúng các section còn lại",
    set(valid_tabs) == set(tabs),
    f"VALID_TABS={sorted(valid_tabs)} | sections={sorted(tabs)}",
)
check(
    "link cũ #users / #devices tự rơi về tab hợp lệ",
    "users" not in valid_tabs and "devices" not in valid_tabs,
    "tab không có trong VALID_TABS sẽ rơi về dashboard — đây là hành vi muốn",
)

# Không để lại id trùng do di chuyển khối (lỗi rất dễ gây ra khi cắt/dán HTML).
ids = re.findall(r'\bid="([^"]+)"', HTML)
dup = sorted({i for i in ids if ids.count(i) > 1})
check("không có id nào bị trùng trong index.html", not dup, ", ".join(dup[:8]))

# Mỗi section phải cân bằng thẻ div, nếu lệch thì bố cục sẽ vỡ.
for t in tabs:
    b = section_html(t)
    check(
        f"tab {t} cân bằng thẻ",
        b.count("<div") == b.count("</div>"),
        f"thiếu {b.count('<div') - b.count('</div>')} thẻ mở",
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
