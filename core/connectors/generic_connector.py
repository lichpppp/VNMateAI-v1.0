"""
core/connectors/generic_connector.py
====================================
Phase 62 — Gọi REST API của bất kỳ app doanh nghiệp nào, không cần code mới.

Vấn đề
------
Mọi app doanh nghiệp đều trả JSON qua REST. Khác biệt giữa MISA và Odoo và
KiotViet không nằm ở *cách gọi* (đều là HTTP + header xác thực) mà ở *giá trị*
cấu hình: URL, kiểu xác thực, path lấy báo cáo. Nếu viết một adapter Python
cho từng app thì 90% code lặp lại y hệt nhau.

Cách giải quyết
---------------
`GenericConnector` gom phần chung (dựng header, ghép URL, parse, chuẩn hoá
thành bảng) và nhận phần khác biệt từ khai báo trong
`config/data_sources.json`. Thêm app mới = thêm một mục JSON.

Chuẩn hoá đầu ra
----------------
App ngoài trả về đủ kiểu: `[{...}]`, `{"data": [{...}]}`, `{"result": {"items":
[...]}}`, `{"rows": [...]}`. Người vận hành không nên phải biết app nào bọc
dữ liệu kiểu gì. `GenericConnector` dò các vị trí quen thuộc, trả về luôn
cùng một cấu trúc `{rows, columns, total}` để UI render một kiểu duy nhất.

Bảo mật
-------
- Chỉ GET/POST. Không có method ghi/xoá — data source chỉ để *lấy dữ liệu báo
  cáo*, không phải để thao tác nghiệp vụ.
- `auth_value` không bao giờ nằm trong `ConnectorResult` lẫn message lỗi; lỗi từ
  server ngoài được mask qua `_mask_secrets` trước khi trả lên.
"""

from __future__ import annotations

import base64
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from core.connectors.base_connector import BaseConnector, ConnectorConfig, ConnectorResult

logger = logging.getLogger(__name__)

#: Khoá thường gặp chứa mảng bản ghi trong payload JSON. Thứ tự là thứ tự ưu
#: tiên: khoá nào cụ thể hơn thì kiểm tra trước.
_ROW_CONTAINER_KEYS: Tuple[str, ...] = (
    "rows", "items", "records", "results", "data", "list", "entries", "content", "docs",
)

#: Khoá thường gặp chứa tổng số bản ghi (phân trang).
_TOTAL_KEYS: Tuple[str, ...] = ("total", "total_count", "totalCount", "count", "totalElements")

#: Khoá thường gặp chứa thông báo lỗi của app ngoài — dùng để báo lỗi rõ ràng
#: thay vì chỉ "HTTP 500".
_ERROR_KEYS: Tuple[str, ...] = ("error", "error_message", "message", "detail", "errors")


def _find_rows(payload: Any, depth: int = 0) -> Optional[List[Any]]:
    """
    Tìm mảng bản ghi trong payload lồng nhau.

    Bỏ qua `payload` là dict rỗng — `{"data": {}}` không phải "không có dữ
    liệu", mà là app bọc chưa đúng chỗ. Trả None để phía gọi thử container
    khác, thay vì kết luận sai rằng báo cáo rỗng.
    """
    if depth > 4:  # chống vòng lặp tự tham chiếu / cấu trúc quá sâu
        return None

    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return None

    for key in _ROW_CONTAINER_KEYS:
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, list):
            return value
        if isinstance(value, dict) and value:
            nested = _find_rows(value, depth + 1)
            if nested is not None:
                return nested

    # Không có khoá quen thuộc: coi dict đầu tiên chứa dict/record là danh sách.
    for value in payload.values():
        if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            return value
        if isinstance(value, dict) and value:
            nested = _find_rows(value, depth + 1)
            if nested is not None:
                return nested

    return None


def _find_total(payload: Any, rows_len: int, depth: int = 0) -> Optional[int]:
    """Tìm tổng số bản ghi trong payload; `None` nếu app không báo."""
    if depth > 3 or not isinstance(payload, dict):
        return None
    for key in _TOTAL_KEYS:
        value = payload.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and value >= 0:
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    for value in payload.values():
        if isinstance(value, dict):
            nested = _find_total(value, rows_len, depth + 1)
            if nested is not None:
                return nested
    return None


def _extract_error(payload: Any, depth: int = 0) -> str:
    """
    Rút thông báo lỗi của app ngoài, nếu có.

    Nhiều app bọc lỗi trong thân phản hồi thành công (`{"success": false,
    "data": {"error_message": "..."}}`) — nên phải dò cả các tầng bọc, không
    chỉ khoá ở tầng ngoài cùng.
    """
    if depth > 4 or not isinstance(payload, dict):
        return ""

    for key in _ERROR_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]

    for value in payload.values():
        if isinstance(value, dict) and value:
            nested = _extract_error(value, depth + 1)
            if nested:
                return nested

    return ""


def _columns_of(rows: List[Any], max_cols: int = 12) -> List[str]:
    """
    Tập cột hiển thị: hợp nhất khoá của các bản ghi đầu, giữ đúng thứ tự xuất
    hiện. Giới hạn số cột vì bảng báo cáo rộng sẽ vỡ layout.
    """
    columns: List[str] = []
    for row in rows[:5]:  # 5 dòng đủ để thấy phần lớn cột
        if not isinstance(row, dict):
            continue
        for key in row:
            if key not in columns:
                columns.append(str(key))
                if len(columns) >= max_cols:
                    return columns
    return columns


class GenericConnector(BaseConnector):
    """
    Connector động: đọc khai báo từ sổ đăng ký, dựng request, chuẩn hoá kết quả.

    Không giữ state giữa các lần gọi ngoài cache token của `BaseConnector` —
    xác thực kiểu bearer/header/query không có bước bắt tay nên mọi lần gọi đều
    tự trọn.
    """

    def __init__(self, source: Dict[str, Any], connector_config: Optional[ConnectorConfig] = None):
        """
        Args:
            source: bản ghi từ `custom_registry.get_source()` (có `auth_value`).
            connector_config: ghi đè cấu hình (chủ yếu dùng cho test).
        """
        if connector_config is None:
            timeout = float(source.get("timeout_seconds") or 10.0)
            extra = {k: v for k, v in source.items() if k != "id"}
            connector_config = ConnectorConfig(
                name=f"ds:{source.get('id', 'unknown')}",
                enabled=bool(source.get("enabled", True)),
                timeout_seconds=timeout,
                extra=extra,
            )
        super().__init__(connector_config)
        self.source: Dict[str, Any] = dict(source)
        self._headers: Dict[str, str] = self._build_headers()

    # ── Chuẩn bị request ──────────────────────────────────────────────

    def _build_headers(self) -> Dict[str, str]:
        """Dựng header xác thực theo `auth_type` của khai báo."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "VN-MateAI/1.0 (data-source)",
        }
        auth_type = str(self.source.get("auth_type") or "none").lower()
        secret = str(self.source.get("auth_value") or "").strip()

        if not secret or auth_type == "none":
            return headers

        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {secret}"
        elif auth_type == "basic":
            raw = str(self.source.get("auth_value") or "")
            # Người dùng nhập "user:pass"; cũng chấp nhận sẵn base64.
            if ":" in raw:
                encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")
            else:
                encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        elif auth_type == "header":
            name = str(self.source.get("auth_header") or "X-Api-Key").strip()
            headers[name] = secret
        elif auth_type == "query":
            pass  # xử lý ở `build_url` vì query string không thuộc header

        return headers

    def build_url(self, path: Optional[str] = None) -> str:
        """Ghép `base_url` + path, cộng tham số query của kiểu xác thực."""
        base = str(self.source.get("base_url") or "").rstrip("/")
        rel = _normalise_join(path if path is not None else self.source.get("default_path"))

        url = f"{base}{rel}" if rel else base

        # auth_type=query: khoá nằm trên URL chứ không trong header.
        if str(self.source.get("auth_type") or "").lower() == "query":
            key = str(self.source.get("auth_query") or "api_key").strip() or "api_key"
            secret = str(self.source.get("auth_value") or "").strip()
            if secret:
                from urllib.parse import quote
                url = f"{url}{'&' if '?' in url else '?'}{quote(key)}={quote(secret)}"

        return url

    # ── BaseConnector interface ────────────────────────────────────────

    async def authenticate(self) -> bool:
        """
        Kiểm tra khai báo đủ dùng để gọi chưa.

        Không bắt tay với server: với các kiểu xác thực ở trên, chỉ có cách duy
        nhất biết khoá đúng là gọi thật. Trả False kèm lý do để UI báo ngay
        mà không tốn một vòng gọi ra ngoài.
        """
        if not str(self.source.get("base_url") or "").strip():
            return False

        auth_type = str(self.source.get("auth_type") or "none").lower()
        if auth_type != "none" and not str(self.source.get("auth_value") or "").strip():
            return False
        return True

    async def health_check(self) -> ConnectorResult:
        """
        Gọi thật 1 request tới `default_path` để xác nhận app còn sống.

        Cố tình dùng `row_limit=1`: chỉ cần biết endpoint trả về gì, không cần
        kéo cả bảng chỉ để đặt chấm xanh — nên không làm nặng app của khách.
        """
        if not await self.authenticate():
            return ConnectorResult(
                success=False,
                error="Thiếu base_url hoặc khoá xác thực",
                source=self.config.name,
            )

        t0 = time.monotonic()
        result = await self._call("GET", path=None, query=None, body=None, limit=1)
        latency = (time.monotonic() - t0) * 1000

        if not result.success:
            return ConnectorResult(
                success=False,
                error=result.error,
                latency_ms=latency,
                source=self.config.name,
                metadata={"sample": result.metadata.get("sample")},
            )

        return ConnectorResult(
            success=True,
            data={"status": "healthy"},
            latency_ms=latency,
            source=self.config.name,
            metadata={
                "http_status": result.metadata.get("http_status"),
                "rows_returned": (result.data or {}).get("total", 0) if isinstance(result.data, dict) else 0,
            },
        )

    async def fetch_data(self, params: Optional[Dict[str, Any]] = None) -> ConnectorResult:
        """
        Kéo dữ liệu báo cáo theo `params`.

        Args:
            params: tuỳ chọn, các khoá được đọc:
                - `path`     (str)  ghi đè path — hoặc tên path đã khai báo
                - `method`   (str)  GET (mặc định) hoặc POST
                - `query`    (dict) tham số truy vấn
                - `body`     (dict) body cho POST
                - `limit`    (int)  giới hạn số bản ghi trả về
        """
        params = params or {}
        if not await self.authenticate():
            return ConnectorResult(
                success=False,
                error="Thiếu base_url hoặc khoá xác thực",
                source=self.config.name,
            )

        method = str(params.get("method") or self.source.get("method") or "GET").upper()
        if method not in ("GET", "POST"):
            return ConnectorResult(
                success=False,
                error="method chỉ nhận GET hoặc POST",
                source=self.config.name,
            )

        path = params.get("path")
        # Cho phép gọi bằng TÊN path đã khai báo ("doanh thu") thay vì URL thô,
        # để AI và UI không phải nhớ đường dẫn.
        if path and not str(path).startswith("/"):
            named = (self.source.get("paths") or {}).get(str(path))
            if not named:
                return ConnectorResult(
                    success=False,
                    error=(
                        f"Không có path nào tên '{path}'. "
                        f"Đã khai báo: {', '.join((self.source.get('paths') or {}).keys()) or '(chưa có)'}"
                    ),
                    source=self.config.name,
                )
            path = named

        limit = _coerce_limit(params.get("limit"), self.source.get("row_limit"))

        return await self._call(
            method=method,
            path=path,
            query=params.get("query") if isinstance(params.get("query"), dict) else None,
            body=params.get("body") if isinstance(params.get("body"), dict) else None,
            limit=limit,
        )

    # ── Nội bộ ─────────────────────────────────────────────────────────

    async def _call(
        self,
        method: str,
        path: Optional[str],
        query: Optional[Dict[str, Any]],
        body: Optional[Dict[str, Any]],
        limit: int,
    ) -> ConnectorResult:
        """Gọi HTTP rồi chuẩn hoá kết quả về `{rows, columns, total}`."""
        url = self.build_url(path)

        if method == "GET" and body:
            # GET không có body — chuyển tham số sang query để không bị mất.
            query = {**(query or {}), **body}
            body = None

        result = await self._request_with_retry(
            method=method,
            url=url,
            headers=self._headers,
            json_body=body,
            params=query,
        )
        if not result.success:
            # Lỗi từ server ngoài có thể chứa chuỗi lệch khoá bí mật.
            return ConnectorResult(
                success=False,
                error=self._mask_secrets(result.error or "Không xác định"),
                latency_ms=result.latency_ms,
                source=self.config.name,
                # Giữ lại http_status để UI phân biệt "token sai" (401) với
                # "app chết" (502) — hai lỗi cần hai cách sửa khác nhau.
                metadata=result.metadata,
            )

        payload = result.data
        if isinstance(payload, str):
            # Endpoint trả text/plain hoặc HTML: không phải báo cáo.
            return ConnectorResult(
                success=False,
                error="Endpoint không trả JSON — kiểm tra lại path",
                latency_ms=result.latency_ms,
                source=self.config.name,
                metadata={"http_status": result.metadata.get("http_status")},
            )

        rows = _find_rows(payload)
        if rows is None:
            return ConnectorResult(
                success=False,
                error=(
                    f"Không tìm thấy danh sách dữ liệu trong phản hồi. "
                    f"Phản hồi có khoá: {', '.join(list(payload)[:6]) if isinstance(payload, dict) else type(payload).__name__}"
                ),
                latency_ms=result.latency_ms,
                source=self.config.name,
                metadata={"http_status": result.metadata.get("http_status")},
            )

        total = _find_total(payload, len(rows)) or len(rows)
        trimmed = rows[:limit]

        return ConnectorResult(
            success=True,
            data={
                "rows": trimmed,
                "columns": _columns_of(trimmed),
                "total": total,
                "returned": len(trimmed),
                "truncated": total > len(trimmed),
            },
            latency_ms=result.latency_ms,
            source=self.config.name,
            metadata={
                "http_status": result.metadata.get("http_status"),
                "path": path or self.source.get("default_path"),
            },
        )


# ── Hàm cấp module ───────────────────────────────────────────────────────

def _normalise_join(path: Any) -> str:
    """Chuẩn hoá path tương đối thành `/a/b` để ghép với base_url."""
    raw = str(path or "").strip()
    if not raw or raw == "/":
        return ""
    if raw.startswith(("http://", "https://")):
        return ""  # path tuyệt đối -> dùng luôn base_url, bỏ qua base
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def _coerce_limit(value: Any, fallback: Any) -> int:
    """Giới hạn số bản ghi trả về (tránh kéo cả bảng 100k dòng về UI)."""
    raw = value if value is not None else fallback
    try:
        num = int(raw)
    except (TypeError, ValueError):
        num = 50
    return max(1, min(500, num))


async def probe_data_source(source_id: str) -> ConnectorResult:
    """
    Kiểm tra sức khoẻ một data source tùy chỉnh theo id.

    Trả `success=False` kèm lý do nếu id không tồn tại hoặc cấu hình hỏng —
    thông điệp này hiện thẳng lên UI nên phải cụ thể.
    """
    from core.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=True)
    if not source:
        return ConnectorResult(
            success=False,
            error=f"Không tìm thấy nguồn dữ liệu '{source_id}'",
            source=f"ds:{source_id}",
        )
    if not source.get("enabled", True):
        return ConnectorResult(
            success=False,
            error="Nguồn dữ liệu đang bị tắt",
            source=f"ds:{source_id}",
        )

    return await GenericConnector(source).health_check()


async def fetch_data_source(source_id: str, params: Optional[Dict[str, Any]] = None) -> ConnectorResult:
    """Kéo dữ liệu báo cáo từ một data source tùy chỉnh theo id."""
    from core.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=True)
    if not source:
        return ConnectorResult(
            success=False,
            error=f"Không tìm thấy nguồn dữ liệu '{source_id}'",
            source=f"ds:{source_id}",
        )
    if not source.get("enabled", True):
        return ConnectorResult(
            success=False,
            error="Nguồn dữ liệu đang bị tắt",
            source=f"ds:{source_id}",
        )

    return await GenericConnector(source).fetch_data(params or {})
