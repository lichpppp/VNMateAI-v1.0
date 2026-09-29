"""
tests/test_phase78_admin_connectors.py
======================================
Kiểm thử Phase 78 — trang Quản trị chỉ hiện connector có thật.

Bối cảnh
--------
Trang Plugin Vault ban đầu gọi `GET /api/v1/plugins` — một endpoint KHÔNG
tồn tại. Kết quả: lưới luôn trống, không rõ là lỗi giao diện hay hệ thống
chưa có gì. Người dùng hỏi "sao không thấy Teams với Outlook".

Sau khi truy nguyên, sự thật là:
  - M365 / Teams / Outlook: KHÔNG tồn tại trong codebase (không có module,
    không có connector, không có skill).
  - "Legacy AI Bot": cũng không. Chữ "legacy" trong core/ chỉ nói về tương
    thích cấu hình cũ, không phải một con bot.
  - Connector CÓ THẬT: aws, oci, paperless, einvoice — đúng 4, lấy từ
    `CONNECTOR_REGISTRY`, và backend đã có endpoint
    `/api/v1/enterprise/connectors/health` từ Phase 59.

Vì vậy test này khẳng định hai điều:
  1. Danh mục trả về đúng bằng CONNECTOR_REGISTRY — không thêm, không bớt,
     và không tự chế ra connector không có.
  2. JSON Schema sinh ra chỉ gồm khoá thật, đánh dấu khoá bí mật, và TUYỆT ĐỐI
     không chứa giá trị bí mật nào của người dùng.

Ngoài ra có một khối riêng cho lỗi CORS preflight: middleware auth từng chặn
`OPTIONS` khiến mọi frontend khác origin không gọi được API nào. Đó là hành
vi ĐÚNG cho request thật, nhưng sai cho preflight.
"""

from __future__ import annotations

import json
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

# ──────────────────────────────────────────────────────────────────────
section("Danh mục connector lấy đúng từ CONNECTOR_REGISTRY")

from core.connectors import CONNECTOR_REGISTRY  # noqa: E402
from core.server import (  # noqa: E402
    _CONNECTOR_DISPLAY,
    _CONNECTOR_SECRET_FIELDS,
    _build_connector_config_schema,
)

check(
    "registry có connector (không rỗng)",
    len(CONNECTOR_REGISTRY) > 0,
    "registry rỗng thì trang quản trị không có gì để hiện",
)
check(
    "registry đúng 4 connector: aws, oci, paperless, einvoice",
    set(CONNECTOR_REGISTRY) == {"aws", "oci", "paperless", "einvoice"},
    f"thấy {sorted(CONNECTOR_REGISTRY)}",
)

# Không được tự chế thêm connector mà hệ thống không có.
for name in _CONNECTOR_DISPLAY:
    check(
        f"'{name}' có nhãn hiển thị thì phải có trong registry",
        name in CONNECTOR_REGISTRY,
        "nhãn cho một connector không tồn tại = giao diện hứa thứ không có",
    )

# M365 / Teams / Outlook KHÔNG tồn tại — và đây là điều cần khẳng định để
# không ai vô tình thêm card giả cho chúng.
import core.server as server_mod  # noqa: E402

_server_src = (PROJECT_ROOT / "core" / "server.py").read_text(encoding="utf-8")
_catalog_src = _server_src[_server_src.index("_CONNECTOR_DISPLAY"):_server_src.index("async def api_connectors_catalog")]

for phantom in ("m365", "teams", "outlook", "legacy_bot", "legacy bot"):
    check(
        f"không có connector bịa '{phantom}' trong danh mục",
        phantom not in _catalog_src.lower(),
        "hệ thống không có kết nối này; hiện card ra là hứa thứ không tồn tại",
    )

# ──────────────────────────────────────────────────────────────────────
section("JSON Schema chỉ gồm khoá thật, không lộ giá trị bí mật")

from core.connectors.base_connector import (  # noqa: E402
    CONNECTOR_DEFAULTS,
    CONNECTOR_REQUIRED_FIELDS,
    missing_required_fields,
)

for name in sorted(CONNECTOR_REGISTRY):
    schema = _build_connector_config_schema(name)
    props = schema["properties"]

    check(f"{name}: schema là object", schema["type"] == "object")
    check(
        f"{name}: có trường trong form",
        len(props) > 0,
        "connector không có khoá nào để cấu hình thì form rỗng, vô nghĩa",
    )

    # Mọi trường BẮT BUỘC phải có mặt trong schema, nếu không thì người
    # dùng không biết phải điền gì để connector chạy được.
    for key in CONNECTOR_REQUIRED_FIELDS.get(name, ()):
        check(
            f"{name}: trường bắt buộc '{key}' có trong form",
            key in props,
            "thiếu trường bắt buộc thì form cho cảm giác đã xong trong khi "
            "mọi lời gọi vẫn hỏng",
        )
        check(
            f"{name}: '{key}' nằm trong danh sách required",
            key in schema.get("required", []),
            "không required thì người dùng bỏ trống cũng không bị báo lỗi",
        )

    # Trường đã cấu hình KHÔNG được tự điền giá trị — kể cả khoá bí mật.
    for key, prop in props.items():
        if key in _CONNECTOR_SECRET_FIELDS:
            check(
                f"{name}.{key} được đánh dấu format=secret (hiển thị ***)",
                prop.get("format") == "secret",
                f"thấy format={prop.get('format')!r}",
            )
            check(
                f"{name}.{key} KHÔNG bị điền sẵn giá trị bí mật",
                "default" not in prop,
                f"default={prop.get('default')!r} — lộ khoá bí mật ra giao diện",
            )

    # Mọi trường đã có cấu hình rồi cũng không nên điền sẵn giá trị thật
    # (region, base_url... có thể chứa thông tin nội bộ của khách hàng).
    for key, prop in props.items():
        if key not in _CONNECTOR_SECRET_FIELDS:
            default = prop.get("default")
            if default is not None:
                check(
                    f"{name}.{key} chỉ có default lấy từ CONNECTOR_DEFAULTS",
                    default == CONNECTOR_DEFAULTS.get(name, {}).get(key),
                    f"default={default!r} không khớp bảng khai báo",
                )

    check(
        f"{name}: có title cho mọi trường (form cần nhãn để hiển thị)",
        all("title" in p for p in props.values()),
        "thiếu title thì form hiện trường không tên",
    )

# ──────────────────────────────────────────────────────────────────────
section("Schema phản ánh đúng tình trạng thật của máy")

for name in sorted(CONNECTOR_REGISTRY):
    missing = set(missing_required_fields(name))
    required = set(CONNECTOR_REQUIRED_FIELDS.get(name, ()))
    schema = _build_connector_config_schema(name)

    # Mọi khoá đang thiếu đều phải nằm trong `required` để form chặn bỏ trống.
    check(
        f"{name}: khoá đang thiếu đều là required",
        missing.issubset(set(schema.get("required", []))),
        f"thiếu {sorted(missing - set(schema.get('required', [])))}",
    )
    check(
        f"{name}: required chỉ gồm khoá thật sự bắt buộc",
        set(schema.get("required", [])) == required,
        f"schema={sorted(schema.get('required', []))} bảng={sorted(required)}",
    )

# ──────────────────────────────────────────────────────────────────────
section("Mọi connector thiếu khoá thì phải hiện 'chờ kết nối', không phải 'lỗi'")

# Quy ước đã dùng ở Phase 73-76: chưa cấu hình KHÔNG phải sự cố kỹ thuật.
# Hiện "lỗi" khiến người vận hành đi tìm bug thay vì điền thông tin đăng nhập.
for name in sorted(CONNECTOR_REGISTRY):
    missing = missing_required_fields(name)
    configured = not missing
    check(
        f"{name}: configured = {configured} (thiếu {len(missing)} khoá)",
        isinstance(configured, bool),
        "configured phải là bool để giao diện rẽ nhánh được",
    )

_admin_src = (PROJECT_ROOT / "admin" / "components" / "admin" / "PluginCard.tsx").read_text(encoding="utf-8")
check(
    "PluginCard hiện nhãn 'chờ kết nối' cho connector chưa cấu hình",
    "chờ kết nối" in _admin_src,
    "thiếu nhãn chờ — người dùng tưởng connector hỏng",
)
check(
    "PluginCard KHÔNG gắn nhãn 'lỗi' cho connector chưa cấu hình",
    "isWaiting" in _admin_src and "Đã cấu hình" in _admin_src,
    "phải phân biệt 'chưa cấu hình' với 'lỗi kỹ thuật'",
)

# ──────────────────────────────────────────────────────────────────────
section("Giao diện lấy đúng endpoint có thật, không gọi endpoint bịa")

_plugins_hook = (PROJECT_ROOT / "admin" / "hooks" / "usePlugins.ts").read_text(encoding="utf-8")
_api_lib = (PROJECT_ROOT / "admin" / "lib" / "api.ts").read_text(encoding="utf-8")

check(
    "không còn gọi endpoint '/plugins' không tồn tại",
    "'/plugins'" not in _api_lib and '"/plugins"' not in _api_lib,
    "endpoint này không có trong backend — đó là lý do trang từng trống",
)
check(
    "gọi endpoint thật /enterprise/connectors/catalog",
    "/enterprise/connectors/catalog" in _api_lib,
    "phải đọc danh mục từ registry của backend",
)
check(
    "không tự chế danh sách connector trong frontend",
    "m365" not in _plugins_hook.lower() and "outlook" not in _plugins_hook.lower(),
    "danh sách bịa ở frontend sẽ lệch với hệ thống thật",
)

# ──────────────────────────────────────────────────────────────────────
section("Lỗi CORS preflight: OPTIONS được qua, request thật vẫn phải xác thực")

# Preflight OPTIONS không mang Authorization (trình duyệt không gửi), nên nếu
# middleware auth kiểm tra nó thì mọi frontend khác origin chết với 401 và
# không bao giờ nhận được header CORS.
check(
    "auth_middleware bỏ qua preflight OPTIONS",
    'if request.method == "OPTIONS"' in _server_src,
    "thiếu nhánh này thì trang Admin ở cổng khác luôn lỗi 'Failed to fetch'",
)
check(
    "nhánh bỏ qua OPTIONS nằm TRƯỚC khi kiểm tra token",
    _server_src.index('if request.method == "OPTIONS"') < _server_src.index('auth_header = request.headers.get("Authorization")'),
    "đặt sau thì preflight vẫn bị chặn 401 trước khi tới nhánh bỏ qua",
)
check(
    "request thật vẫn bắt buộc có token",
    'auth_header = request.headers.get("Authorization")' in _server_src
    and "Yêu cầu xác thực tài khoản" in _server_src,
    "bỏ qua OPTIONS không được nới lỏng xác thực của GET/POST/PUT/DELETE",
)
check(
    "CORS vẫn lấy danh sách origin từ biến môi trường",
    "VNMATEAI_CORS_ORIGINS" in _server_src,
    "cần cơ chế có sẵn thay vì hardcode cổng của admin",
)
# allow_origins=["*"] có thể xuất hiện trong COMMENT giải thích lý do đã bỏ.
# Phải kiểm tra lời gọi add_middleware thật, không kiểm tra cả file.
_cors_call = _server_src[_server_src.index("app.add_middleware("):]
_cors_call = _cors_call[: _cors_call.index(")") + 1]
check(
    "lời gọi add_middleware KHÔNG dùng allow_origins=['*']",
    'allow_origins=["*"]' not in _cors_call and "allow_origins=_allowed_origins" in _cors_call,
    "CORS Zero-Trust phải lấy danh sách origin từ VNMATEAI_CORS_ORIGINS",
)

# ──────────────────────────────────────────────────────────────────────
section("DynamicForm chặn được trường bắt buộc để trống")

# z.string() của Zod CHẤP NHẬN chuỗi rỗng, nên trường required để trống vẫn
# qua validate và onSubmit vẫn chạy — giao diện báo lỗi nhưng dữ liệu vẫn đi.
_df = (PROJECT_ROOT / "admin" / "components" / "admin" / "DynamicForm.tsx").read_text(encoding="utf-8")
check(
    "DynamicForm chặn chuỗi rỗng cho trường bắt buộc",
    ".min(1," in _df and "schema.required?.includes(key)" in _df,
    "không có .min(1) thì form 'hợp lệ' với khoá trống",
)
check(
    "DynamicForm lấy default từ schema (vd region của AWS)",
    "initialValues" in _df and "prop.default" in _df,
    "defaultValues rỗng của caller sẽ xoá mất mặc định backend khai báo",
)
check(
    "DynamicForm vẽ boolean thành công tắc, không phải ô text",
    "prop.type === 'boolean'" in _df,
    "khoá true/false để trong ô text thì không ai bật/tắt được",
)

# ──────────────────────────────────────────────────────────────────────
section("Không báo thành công khi chưa thật sự làm gì")

# Nút Lưu chưa có endpoint ghi cấu hình. Báo "đã lưu" là báo cáo thành công
# giả — người dùng đóng hộp thoại rồi mất thông tin đăng nhập.
check(
    "PluginCard báo rõ chưa lưu được (không giả vờ đã lưu)",
    "Chưa lưu được" in _admin_src,
    "báo 'đã lưu' khi chưa gửi đi đâu là báo cáo thành công giả",
)
# Thông điệp "đây không phải ping dịch vụ" nằm ở hook, vì hook mới là nơi
# thực hiện lời gọi. PluginCard chỉ render kết quả.
check(
    "nút Kiểm tra nói rõ chưa gửi lời gọi thật (không hứa thành công)",
    "lời gọi thật" in _plugins_hook,
    "đọc lại cấu hình trong bộ nhớ không phải là ping dịch vụ — phải nói rõ, "
    "nếu không người dùng tưởng đã kiểm tra kết nối thật",
)
check(
    "nút Kiểm tra báo đúng tình trạng thiếu khoá",
    "missing_fields" in _plugins_hook and "Chưa cấu hình xong" in _plugins_hook,
    "phải nêu tên khoá còn thiếu, không chỉ nói chung chung",
)
_hook = (PROJECT_ROOT / "admin" / "hooks" / "useToast.ts").read_text(encoding="utf-8")
check(
    "toast KHÔNG được persist qua trang mới",
    "persist(" not in _hook,
    "toast cũ hiện lại sau khi tải trang = báo cáo sự kiện chưa xảy ra ở hiện tại",
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
