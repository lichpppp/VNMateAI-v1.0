"""
mateai/interfaces/http/api_erp.py
=================================
APIs cho cấu trúc tổ chức ERP và cơ chế Import dữ liệu hàng loạt từ Excel.
Phase 47: ERP Structure & Bulk Data Import Engine.

Endpoints:
  1. GET  /api/erp/template   -> Xuất file Excel mẫu đa sheet (PhongBan, NhanVien, MayTinh, CongViec, SoSach)
  2. POST /api/erp/import     -> Nhận file upload, validate dữ liệu và import giao dịch (Rollback an toàn)
  3. GET  /api/erp/structure  -> Lấy cây cấu trúc phòng ban và các thực thể con
"""

from __future__ import annotations

import io
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.plugin_manager import run_blocking
from mateai.application.enterprise import erp_import
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.infrastructure.database.erp_database import erp_db

logger = logging.getLogger("mateai.interfaces.http.api_erp")

router = APIRouter(prefix="/api/erp", tags=["ERP Organization"])


# ── 1. API SINH FILE TEMPLATE EXCEL ──────────────────────────────────────────

@router.get("/template", summary="Tải file Excel mẫu cấu trúc tổ chức ERP")
async def download_erp_template(
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """
    File .xlsx gồm các Sheet PhongBan, NhanVien, MayTinh, CongViec, SoSach — CHỈ hàng
    tiêu đề (Phase 73: không dữ liệu mẫu bịa). Yêu cầu đăng nhập: file tiết lộ cấu trúc
    cột của toàn bộ hệ thống ERP. Dựng file: `application/enterprise/erp_import`.
    """
    try:
        content, filename = await run_blocking(erp_import.build_template)
    except Exception as exc:
        logger.error("Lỗi khi tạo file Excel mẫu: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi tạo file mẫu: {exc}")
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── 2. API IMPORT DỮ LIỆU HÀNG LOẠT (BULK IMPORT ENGINE) ─────────────────────

@router.post("/import", summary="Import dữ liệu tổ chức ERP từ file Excel")
async def import_erp_data(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Nhận file .xlsx, đọc các Sheet `PhongBan`, `NhanVien`, `MayTinh`, `CongViec`, `SoSach`
    và nhập trong một transaction (rollback toàn bộ nếu một dòng lỗi). Đọc file + ghi DB
    chạy ngoài event loop (Supervisor P10: trước đây pandas chạy ngay trong hàm async).
    """
    contents = await file.read()
    actor = str(current_user.get("username", "admin"))
    try:
        result = await run_blocking(erp_import.import_workbook, actor=actor,
                                    filename=file.filename or "", contents=contents)
    except erp_import.ImportRejected as bad:
        # Trước P10 lỗi 400 "thiếu sheet" bị `except Exception` bên dưới nuốt thành 200.
        raise HTTPException(status_code=400, detail=str(bad))
    except ValueError as val_err:
        logger.warning("Lỗi kiểm tra dữ liệu import: %s", val_err)
        return {"status": "error", "message": str(val_err)}
    except Exception as exc:
        logger.error("Lỗi khi xử lý import file Excel: %s", exc)
        return {"status": "error", "message": f"Lỗi xử lý file: {str(exc)}"}
    return {"status": "success", "message": result.get("message"), "stats": result.get("stats")}


def _audit(user: Dict[str, Any], action: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username")), action, "ERP", "SUCCESS", details)
    except Exception:  # noqa: BLE001
        pass


# ── 3. API LẤY CÂY CẤU TRÚC TỔ CHỨC ERP ──────────────────────────────────────

@router.get("/structure", summary="Lấy toàn bộ cây tổ chức ERP")
async def get_erp_structure(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về danh sách phòng ban và các thực thể con (Nhân viên, Máy tính, Task, Sổ sách).

    ABAC (prompt cuối §155): chỉ phòng ban của người hỏi; admin thấy toàn bộ."""
    from mateai.application.security.security_guard import scope_rows, security_guard
    try:
        tree = await run_blocking(erp_db.get_structure_tree)
        who = await run_blocking(security_guard.principal, employee_id=current_user.get("username"))
        visible = scope_rows(tree, who, dept_key="name")
        out = {"status": "success", "count": len(visible), "departments": visible}
        if not who["all_departments"] and not who["department"]:
            out["scope_note"] = "Tài khoản chưa được gán phòng ban — nhờ quản trị gán ở Quản lý người dùng."
        return out
    except Exception as exc:
        logger.error("Lỗi khi lấy cây cấu trúc ERP: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi truy vấn dữ liệu ERP: {exc}")


# ── 4. API THÊM / XÓA PHÒNG BAN MỚI (PHASE 47.1) ──────────────────────────────

class CreateDepartmentRequest(BaseModel):
    name: str
    description: Optional[str] = ""


@router.post("/department", summary="Tạo mới phòng ban trong hệ thống ERP")
async def create_department(
    payload: CreateDepartmentRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Tạo mới phòng ban trực tiếp từ Web Portal."""
    try:
        dept = await run_blocking(erp_db.add_department, name=payload.name, description=payload.description or "")
        _audit(current_user, "erp_department_create", {"id": dept.get("id"), "name": dept.get("name")})
        return {
            "status": "success",
            "message": f"Đã tạo phòng ban '{dept['name']}' thành công!",
            "department": dept,
        }
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as exc:
        logger.error("Lỗi khi tạo phòng ban: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi tạo phòng ban: {exc}")


@router.delete("/department/{dept_id}", summary="Xóa phòng ban trong hệ thống ERP")
async def delete_department(
    dept_id: int,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Xóa phòng ban theo ID."""
    try:
        deleted = await run_blocking(erp_db.delete_department, dept_id=dept_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng ban để xóa.")
        _audit(current_user, "erp_department_delete", {"id": dept_id})
        return {
            "status": "success",
            "message": f"Đã xóa phòng ban #{dept_id} thành công!",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Lỗi khi xóa phòng ban: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xóa phòng ban: {exc}")

