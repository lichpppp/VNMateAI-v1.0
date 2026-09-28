"""
core/skills/data_source_tools.py
================================
Phase 63 — Tool cho AI Ly Ly đọc và xuất báo cáo từ các nguồn dữ liệu tùy chỉnh.

Vấn đề
------
Phase 62/63 đã có đầy đủ khoản trống kỹ thuật: khai báo app doanh nghiệp bằng
form, kéo dữ liệu về chuẩn hoá `{rows, columns, total}`, xuất Excel/CSV. Nhưng
UI thì người dùng phải tự bấm. AI Ly Ly — phần được giao việc *"tra cứu và
lập báo cáo cho tôi"* — lại không thấy mảnh khối đó: danh sách tool chỉ có 11
tool Phase 59 (AWS/OCI/Paperless/eInvoice), không một tool nào chạm tới nguồn
tùy chỉnh. Hệ thống trả lời được câu hỏi về hạ tầng cloud mà không trả lời
được câu hỏi về ERP của chính công ty — lệch về mặt sản phẩm.

Ba tool, chia theo cách AI thực sự dùng
---------------------------------------
1. `list_data_sources`      — AI không biết công ty đã khai báo những gì. Gọi
                              cái này trước, không thì nó đoán mò tên.
2. `fetch_data_source`      — lấy dữ liệu để tự trả lời/tóm tắt. Đây là
                              tool dùng hằng ngày.
3. `prepare_data_source_export` — xuất file. Trả về *đường dẫn tải*, KHÔNG
                              trả bytes: một LLM không đọc nổi file .xlsx,
                              và nhét base64 vào ngữ cảnh chỉ tốn token rồi
                              bị bỏ qua. Việc tải là của người dùng.

Vì sao mọi tool đều đi qua cổng HITL
-----------------------------------
Các tool này gọi ra hệ thống bên ngoài — mạng của khách hàng, không phải của
bạn. Cho phép vòng lặp AI tự do gọi là mở đường để nó dò nhiều hệ thống ngoài
mà không ai duyệt. Đi qua `execute_with_hitl` giữ nguyên đúng mô hình Zero-Trust
sẵn có, và mỗi lần gọi đều để lại dấu vết trong nhật ký.

Bảo mật
-------
- Danh sách trả về KHÔNG có khoá bí mật (`mask_source` chỉ trả cờ `has_auth`).
  LLM không cần biết giá trị khoá, và đưa nó vào ngữ cảnh là tự tạo rủi ro rò
  rỉ qua log hội thoại.
- Số dòng bị chặn trần. Không có trần thì AI có thể kéo 1 triệu dòng về rồi
  làm vỡ ngữ cảnh.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

#: Trần số dòng cho một lần gọi từ AI. Bảng hàng trăm nghìn dòng đưa vào ngữ
#: cảnh sẽ làm vỡ cửa sổ ngữ cảnh và tốn kém — người dùng muốn file đầy đủ
#: thì dùng tool xuất, không phải để AI đọc hết vào đầu.
AI_ROW_LIMIT = 200


async def _run_with_hitl(
    action_name: str,
    params: Dict[str, Any],
    executor,
    description: str,
    flatten: bool = True,
):
    """
    Bọc executor qua cổng HITL rồi bóc envelope về kết quả phẳng.

    `execute_with_hitl` trả về `{status, risk_level, result: {...}}`. Đưa
    nguyên envelope cho LLM là sai: nó phải tự đào vào `result` mới thấy dữ
    liệu, dễ tưởng là lỗi và báo "không có kết quả". Ở đây bóc sẵn và, khi cần
    chờ duyệt, trả về đúng trạng thái để AI nói với người dùng là phải duyệt.

    Cùng cách `_execute_connector_action` trong `integration_tools.py` làm.
    """
    from core.zero_trust import execute_with_hitl

    result = await execute_with_hitl(
        action_name=action_name,
        params=params,
        executor=executor,
        requested_by="AI_Agent",
        description=description,
    )

    if not flatten:
        return result

    # Cần người dùng duyệt — AI phải nói rõ, không được báo "lỗi".
    if result.get("status") == "awaiting_approval":
        return {
            "success": False,
            "awaiting_approval": True,
            "approval_id": result.get("approval_id"),
            "risk_level": result.get("risk_level"),
            "message": (
                f"{result.get('message', 'Yêu cầu cần được duyệt.')} "
                "Hãy nói với người dùng rằng cần phê duyệt trong hàng đợi HITL, "
                "và cung cấp mã phiếu để họ đối chiếu."
            ),
        }

    return result.get("result") or {"success": False, "error": "Cổng HITL không trả về kết quả"}


@export_skill(
    name="list_data_sources",
    description=(
        "Liệt kê các nguồn dữ liệu doanh nghiệp đã được khai báo (MISA, Odoo, SAP, "
        "ERP nội bộ...) kèm các báo cáo mỗi nguồn cung cấp. Gọi tool này TRƯỚC khi "
        "muốn lấy báo cáo, để biết chính xác tên nguồn và tên báo cáo."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Lọc theo nhóm: 'connector', 'reporting', 'analytics'. Bỏ trống = tất cả.",
            }
        },
        "required": [],
    },
)
async def list_data_sources(category: Optional[str] = None) -> Dict[str, Any]:
    """
    Tool: xem công ty đã kết nối những nguồn dữ liệu nào.

    Dùng khi user hỏi: "công ty mình đang kết nối app nào?", "có báo cáo doanh
    thu ở đâu không?".
    """
    try:
        from core.connectors import custom_registry

        wanted = (category or "").strip().lower() or None
        sources = custom_registry.list_sources(include_secrets=False)
        if wanted:
            sources = [s for s in sources if s.get("category") == wanted]

        items: List[Dict[str, Any]] = []
        for s in sources:
            items.append({
                "id": s.get("id"),
                "title": s.get("title"),
                "description": s.get("description") or "",
                "category": s.get("category"),
                # KHÔNG trả base_url cho AI: nó không cần biết hệ thống của
                # khách nằm ở đâu, và URL nội bộ trong ngữ cảnh là thông tin
                # không cần thiết phải lộ.
                "has_auth": bool(s.get("has_auth")),
                "enabled": bool(s.get("enabled", True)),
                "available_reports": s.get("available_paths") or [],
                "default_report": s.get("default_path") or "/",
            })

        return {
            "success": True,
            "count": len(items),
            "data_sources": items,
            "hint": (
                "Dùng fetch_data_source với source_id và report để lấy dữ liệu. "
                "Nếu danh sách rỗng thì chưa có nguồn nào được khai báo — cần người "
                "dùng thêm trong tab Tích Hợp → Kết Nối."
            ) if not items else None,
        }
    except Exception as e:  # pylint: disable=broad-except
        logger.exception("[Phase63] list_data_sources lỗi")
        return {"success": False, "error": str(e)}


@export_skill(
    name="fetch_data_source",
    description=(
        "Lấy dữ liệu báo cáo từ một nguồn đã khai báo (ERP, kế toán, CRM...). "
        "Trả về các dòng dữ liệu và tên cột. Dùng để trả lời câu hỏi cần số liệu, "
        "ví dụ 'doanh thu tháng này bao nhiêu'."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "Mã nguồn dữ liệu (ví dụ: 'misa-amh'). Xem qua list_data_sources.",
            },
            "report": {
                "type": "string",
                "description": "Tên báo cáo đã khai báo (ví dụ: 'doanh thu'). Bỏ trống = dùng báo cáo mặc định.",
            },
            "limit": {
                "type": "integer",
                "description": f"Số dòng tối đa cần xem. Mặc định {AI_ROW_LIMIT}, tối đa {AI_ROW_LIMIT}.",
            },
        },
        "required": ["source_id"],
    },
)
async def fetch_data_source(
    source_id: str,
    report: Optional[str] = None,
    limit: int = AI_ROW_LIMIT,
) -> Dict[str, Any]:
    """
    Tool: lấy dữ liệu báo cáo từ nguồn tùy chỉnh.

    Dùng khi user hỏi: "doanh thu tháng 3 bao nhiêu?", "tồn kho hiện tại ra sao?".
    """
    from core.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=False)
    if not source:
        return {
            "success": False,
            "error": f"Không có nguồn dữ liệu '{source_id}'",
            "hint": "Gọi list_data_sources để xem các nguồn đang có.",
        }

    rows_cap = max(1, min(AI_ROW_LIMIT, int(limit or AI_ROW_LIMIT)))
    params: Dict[str, Any] = {"limit": rows_cap}
    if report:
        params["path"] = report

    async def _executor() -> Dict[str, Any]:
        from core.connectors import fetch_data_source as _fetch
        result = await _fetch(source_id, params)
        if not result.success:
            return {"success": False, "error": result.error}
        data = result.data or {}
        return {
            "success": True,
            "source": source.get("title"),
            "report": report or source.get("default_path"),
            "columns": data.get("columns", []),
            "rows": data.get("rows", []),
            "returned": data.get("returned", 0),
            "total": data.get("total", 0),
            "truncated": bool(data.get("truncated")),
        }

    return await _run_with_hitl(
        action_name="data_source_fetch",
        params={"source_id": source_id, "report": report, "limit": rows_cap},
        executor=_executor,
        description=(
            f"AI Ly Ly yêu cầu báo cáo '{report or source.get('default_path')}' "
            f"từ nguồn {source.get('title')}"
        ),
    )


@export_skill(
    name="prepare_data_source_export",
    description=(
        "Tạo file báo cáo để tải xuống (Excel .xlsx hoặc CSV). Trả về đường dẫn "
        "tải — bạn KHÔNG cần đọc nội dung file, chỉ cần đưa đường dẫn đó cho "
        "người dùng. Dùng khi user nói 'xuất ra Excel', 'gửi tôi file CSV'."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "source_id": {
                "type": "string",
                "description": "Mã nguồn dữ liệu. Xem qua list_data_sources.",
            },
            "report": {
                "type": "string",
                "description": "Tên báo cáo đã khai báo. Bỏ trống = dùng báo cáo mặc định.",
            },
            "format": {
                "type": "string",
                "enum": ["xlsx", "csv"],
                "description": "Định dạng file. Mặc định 'xlsx'.",
            },
            "max_rows": {
                "type": "integer",
                "description": "Số dòng tối đa. Mặc định 1000, tối đa 10000.",
            },
        },
        "required": ["source_id"],
    },
)
async def prepare_data_source_export(
    source_id: str,
    report: Optional[str] = None,
    format: str = "xlsx",
    max_rows: int = 1000,
) -> Dict[str, Any]:
    """
    Tool: chuẩn bị file báo cáo để người dùng tải.

    KHÔNG sinh file ngay ở đây. Endpoint export cần token đăng nhập, và token
    đó không được đưa vào kết quả trả về cho LLM. Thay vào đó trả đường dẫn
    để giao diện tải giúp bằng phiên đăng nhập sẵn có.
    """
    from core.connectors import custom_registry

    source = custom_registry.get_source(source_id, include_secrets=False)
    if not source:
        return {
            "success": False,
            "error": f"Không có nguồn dữ liệu '{source_id}'",
            "hint": "Gọi list_data_sources để xem các nguồn đang có.",
        }

    fmt = (format or "xlsx").strip().lower()
    if fmt not in ("xlsx", "csv"):
        return {"success": False, "error": "format chỉ nhận 'xlsx' hoặc 'csv'"}

    rows = max(1, min(10000, int(max_rows or 1000)))

    # Kiểm tra nguồn còn sống TRƯỚC khi hứa sẽ có file. Nếu app chết, nói
    # ngay thà không có file, còn hơn đưa đường dẫn tải về rồi báo lỗi.
    async def _executor() -> Dict[str, Any]:
        from core.connectors import fetch_data_source as _fetch
        result = await _fetch(source_id, {"path": report} if report else {"limit": 1})
        if not result.success:
            return {
                "success": False,
                "error": f"Không lấy được dữ liệu để xuất: {result.error}",
            }
        return {"ok": True, "total": ((result.data or {}).get("total") or 0)}

    check = await _run_with_hitl(
        action_name="data_source_export_check",
        params={"source_id": source_id, "report": report},
        executor=_executor,
        description=f"AI Ly Ly kiểm tra nguồn {source.get('title')} trước khi xuất file",
    )
    if check.get("awaiting_approval"):
        return check
    if not check.get("success") and check.get("error"):
        return {"success": False, "error": check["error"]}

    from urllib.parse import quote

    title = source.get("title") or source_id
    download_url = f"/api/v1/enterprise/data-sources/{quote(source_id, safe='')}/export"

    return {
        "success": True,
        "source": title,
        "report": report or source.get("default_path"),
        "format": fmt,
        "download_url": download_url,
        "requested_rows": rows,
        "available_rows": check.get("total") or 0,
        "user_instructions": (
            f"Báo cáo '{report or source.get('default_path')}' của {title} đã sẵn sàng. "
            f"Nhấn nút «Xuất {fmt.upper()}» trên thẻ nguồn dữ liệu trong tab Tích Hợp để tải, "
            f"hoặc mở đường dẫn: {download_url}"
        ),
    }
