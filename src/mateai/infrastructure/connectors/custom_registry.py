"""
core/connectors/custom_registry.py
===================================
Phase 62 — Sổ đăng ký data source tùy chỉnh.

Vấn đề Phase 59/61 giải quyết
-----------------------------
Phase 59 hardcode 4 connector (AWS/OCI/Paperless/eInvoice) trong
`CONNECTOR_REGISTRY`. Thêm app thứ 5 (MISA, Odoo, KiotViet, SAP...) thì phải
viết Python mới, sửa `__init__.py`, sửa `CONNECTOR_RISK_LEVELS`, sửa UI, rồi
deploy lại. Với mỗi khách hàng một bộ app khác nhau thì đó là chi phí lặp lại
cho từng lần onboard.

Cách giải quyết ở đây
---------------------
Tách phần *khai báo* (app nào, URL nào, xác thực kiểu gì, path nào lấy báo
cáo) khỏi phần *gọi HTTP* (giống nhau với mọi REST API). Khai báo lưu vào
`config/data_sources.json`; `GenericConnector` đọc khai báo đó để gọi. Thêm
app mới = thêm một mục JSON, không cần deploy.

Vì sao lưu file riêng chứ không nhét vào `config.json`
-----------------------------------------------------
1. `config.json` đi qua `AppSettings` với `extra="ignore"` — khóa lạ bị pydantic
   loại khỏi object singleton (xem `_read_config_json_block` trong
   `base_connector.py`, phải đọc thẳng file vì lý do đúng y vậy).
2. Credential của app doanh nghiệp là dữ liệu bí mật của *khách hàng*, không
   phải cấu hình hệ thống. Tách file giúp `.gitignore` gọn và tránh rò rỉ khi
   ai đó paste config.json vào ticket.

Bất biến an toàn
----------------
- KHÔNG bao giờ trả về giá trị secret (`auth_value`). Chỉ trả tên khoá đã
  điền / chưa điền. Xem `mask_source`.
- `id` là slug an toàn để ghép vào URL path, không phải chuỗi tự do.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Thư mục config/ (không commit — chứa credential khách hàng).
# Thư mục gốc dự án — không suy từ vị trí file mã nguồn (chuyển module mà lệch
# đường dẫn là mọi nguồn dữ liệu đã cấu hình biến mất).
from mateai.config.loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
STORE_PATH = _PROJECT_ROOT / "config" / "data_sources.json"

#: Khoá bí mật của một data source. Giá trị KHÔNG BAO GIỜ đọc lên UI.
SECRET_FIELDS = ("auth_value",)

#: `id` phải an toàn để ghép vào URL path server (`/data-sources/{id}/fetch`).
#: Cho phép: chữ thường, số, gạch dưới, gạch ngang. 2–49 ký tự.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")

#: Các kiểu xác thực mà `GenericConnector` hiểu.
AUTH_TYPES = ("none", "bearer", "basic", "header", "query")

#: Cấu hình mặc định khi tạo mục mới.
DEFAULTS: Dict[str, Any] = {
    "auth_type": "none",
    "auth_header": "X-Api-Key",
    "auth_query": "api_key",
    "method": "GET",
    "default_path": "/",
    "timeout_seconds": 10.0,
    "row_limit": 50,
}

#: Khoá trong JSON mà sẽ tự bị chặn. Đây là đường đánh cắp credential của hệ
#: thống ngoài; app doanh nghiệp không bao giờ cần endpoint metadata này.
_BLOCKED_URL_FRAGMENTS = (
    "169.254.169.254",   # AWS/Azure/GCP instance metadata
    "metadata.google.internal",
    "100.100.100.200",   # Alibaba Cloud
)

_write_lock = threading.Lock()


# ── Lưu trữ ──────────────────────────────────────────────────────────────

def _read_raw() -> Dict[str, Any]:
    """Đọc file khai báo. Trả `{"sources": {}}` nếu chưa có/hỏng."""
    if not STORE_PATH.exists():
        return {"sources": {}}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        # File hỏng thì KHÔNG ném — endpoint list vẫn phải trả được danh sách
        # rỗng thay vì 500. Người vận hành sẽ thấy warning trong log.
        logger.warning("[DataSource] Không đọc được %s: %s", STORE_PATH, exc)
        return {"sources": {}}

    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        return {"sources": {}}
    # Bỏ mục hỏng thay vì làm hỏng cả danh sách.
    clean = {k: v for k, v in data["sources"].items() if isinstance(v, dict) and _ID_RE.match(str(k))}
    return {"sources": clean}


def _write_raw(data: Dict[str, Any]) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    # Ghi file tạm rồi đổi tên: đứt giữa chừng không để lại file nửa vời.
    os.replace(tmp, STORE_PATH)
    try:
        os.chmod(STORE_PATH, 0o600)  # chỉ owner đọc/ghi — chứa credential
    except OSError:  # pragma: no cover - filesystem không hỗ trợ
        pass


# ── Chuẩn hoá ────────────────────────────────────────────────────────────

def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _coerce_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    # Chặn ngoài khoảng hợp lý: timeout 0 là treo, timeout 600 là treo cả server.
    return max(lo, min(hi, num))


def _coerce_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, num))


def validate_base_url(url: str) -> str:
    """
    Kiểm tra `base_url` và trả về bản đã bỏ dấu `/` cuối.

    Raise ValueError kèm lý do nếu không dùng được — thông báo này hiện thẳng
    lên UI nên phải nói rõ là người dùng cần sửa gì.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Thiếu base_url — ví dụ: https://erp.congty.vn/api")
    if not raw.lower().startswith(("http://", "https://")):
        raise ValueError("base_url phải bắt đầu bằng http:// hoặc https://")
    if any(frag in raw for frag in _BLOCKED_URL_FRAGMENTS):
        raise ValueError("URL này là endpoint metadata của cloud provider — bị chặn để tránh lộ credential")
    return raw.rstrip("/")


def _normalise_path(path: Any) -> str:
    """
    Chuẩn hoá path thành dạng `/a/b`. Path rỗng = gốc.
    """
    raw = str(path or "").strip()
    if not raw or raw == "/":
        return "/"
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _normalise_source(source_id: str, payload: Dict[str, Any], previous: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Dựng bản ghi hoàn chỉnh từ payload của UI + bản cũ (để giữ secret khi
    người dùng để trống ô mật khẩu).

    Raise ValueError nếu dữ liệu không dùng được.
    """
    if not _ID_RE.match(source_id):
        raise ValueError(
            "Mã nguồn dữ liệu chỉ gồm chữ thường, số, gạch dưới, gạch ngang "
            "(2–49 ký tự) — ví dụ: misa-amh, odoo-erp"
        )

    title = str(payload.get("title") or "").strip()
    if not title:
        raise ValueError("Thiếu tên hiển thị")
    if len(title) > 80:
        raise ValueError("Tên hiển thị tối đa 80 ký tự")

    auth_type = str(payload.get("auth_type") or DEFAULTS["auth_type"]).strip().lower()
    if auth_type not in AUTH_TYPES:
        raise ValueError(f"Kiểu xác thực không hợp lệ — chỉ nhận: {', '.join(AUTH_TYPES)}")

    record: Dict[str, Any] = {
        "id": source_id,
        "title": title,
        "description": str(payload.get("description") or "").strip()[:160],
        "category": str(payload.get("category") or "custom").strip() or "custom",
        "base_url": validate_base_url(payload.get("base_url")),
        "default_path": _normalise_path(payload.get("default_path", "/")),
        "auth_type": auth_type,
        # Ô secret trống = giữ giá trị đang lưu (giống hành vi form connector).
        "auth_value": str(payload.get("auth_value") or "").strip()
        or str((previous or {}).get("auth_value") or ""),
        "auth_header": (str(payload.get("auth_header") or "").strip() or DEFAULTS["auth_header"])[:64],
        "auth_query": (str(payload.get("auth_query") or "").strip() or DEFAULTS["auth_query"])[:64],
        "method": (str(payload.get("method") or "GET").strip().upper() or "GET"),
        "timeout_seconds": _coerce_float(payload.get("timeout_seconds"), DEFAULTS["timeout_seconds"], 1.0, 60.0),
        "row_limit": _coerce_int(payload.get("row_limit"), DEFAULTS["row_limit"], 1, 500),
        "enabled": _coerce_bool(payload.get("enabled"), True),
        "created_at": (previous or {}).get("created_at") or datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    if record["method"] not in ("GET", "POST"):
        raise ValueError("method chỉ nhận GET hoặc POST — chỉ đọc dữ liệu báo cáo")

    # paths: tên -> path tương đối, để gọi nhiều báo cáo trên cùng một app
    # (vd. "doanh thu" -> /reports/revenue, "tồn kho" -> /reports/stock).
    raw_paths = payload.get("paths")
    paths: Dict[str, str] = {}
    if isinstance(raw_paths, dict):
        for key, val in raw_paths.items():
            name = str(key).strip()
            if name and len(name) <= 40:
                paths[name] = _normalise_path(val)
    record["paths"] = paths

    return record


# ── API công khai ────────────────────────────────────────────────────────

def mask_source(source: Dict[str, Any]) -> Dict[str, Any]:
    """
    Bản an toàn để trả về client: có `has_auth` nhưng KHÔNG có `auth_value`.

    UI chỉ cần biết "đã điền khoá chưa" để quyết định có gợi ý nhập lại hay
    không; tuyệt đối không cần — và không được — thấy giá trị.
    """
    out = {k: v for k, v in source.items() if k not in SECRET_FIELDS}
    out["has_auth"] = bool(source.get("auth_value"))
    out["available_paths"] = list((source.get("paths") or {}).keys())
    out.pop("paths", None)
    return out


def list_sources(include_secrets: bool = False) -> List[Dict[str, Any]]:
    """Danh sách data source tùy chỉnh, sắp theo `title`."""
    sources = _read_raw()["sources"].values()
    items = [dict(s) for s in sources]
    if not include_secrets:
        items = [mask_source(s) for s in items]
    return sorted(items, key=lambda s: str(s.get("title") or s.get("id") or "").lower())


def get_source(source_id: str, include_secrets: bool = True) -> Optional[Dict[str, Any]]:
    """
    Lấy một bản ghi. Mặc định kèm secret vì đây là đường nội bộ — chỉ
    `GenericConnector` dùng. Endpoint HTTP phải gọi `include_secrets=False`.
    """
    record = _read_raw()["sources"].get(source_id)
    if not record:
        return None
    record = dict(record)
    if not include_secrets:
        return mask_source(record)
    return record


def upsert_source(source_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tạo mới hoặc cập nhật một data source. Trả về bản đã mask.

    Raise ValueError nếu payload không hợp lệ.
    """
    source_id = str(source_id or "").strip().lower()
    with _write_lock:
        data = _read_raw()
        previous = data["sources"].get(source_id)
        record = _normalise_source(source_id, payload, previous)
        data["sources"][source_id] = record
        _write_raw(data)

    action = "Cập nhật" if previous else "Thêm"
    logger.info("[DataSource] %s nguồn '%s' (%s)", action, record["title"], source_id)
    return mask_source(record)


def delete_source(source_id: str) -> bool:
    """Xoá một data source. Trả `False` nếu không tồn tại."""
    source_id = str(source_id or "").strip().lower()
    with _write_lock:
        data = _read_raw()
        if source_id not in data["sources"]:
            return False
        removed = data["sources"].pop(source_id)
        _write_raw(data)

    logger.info("[DataSource] Xoá nguồn '%s' (%s)", removed.get("title"), source_id)
    return True
