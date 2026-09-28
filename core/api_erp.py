"""
core/api_erp.py
===============
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
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from pydantic import BaseModel
from fastapi import Depends

from core.auth_manager import get_current_user
from core.database import erp_db

logger = logging.getLogger("core.api_erp")

router = APIRouter(prefix="/api/erp", tags=["ERP Organization"])


# ── 1. API SINH FILE TEMPLATE EXCEL ──────────────────────────────────────────

@router.get("/template", summary="Tải file Excel mẫu cấu trúc tổ chức ERP")
async def download_erp_template(
    user: dict = Depends(get_current_user),
) -> StreamingResponse:
    """
    Tạo file Excel (.xlsx) gồm nhiều Sheet:
      - PhongBan
      - NhanVien
      - MayTinh
      - CongViec
      - SoSach
    Mỗi Sheet có Header chuẩn và dữ liệu mẫu hướng dẫn chi tiết.

    Yêu cầu đăng nhập: file mẫu tiết lộ cấu trúc cột của toàn bộ hệ thống ERP
    (nhân sự, máy tính, sổ sách, công việc) nên không nên công khai.
    """
    output = io.BytesIO()

    # Dữ liệu mẫu chuẩn
    data_sheets = {
        "PhongBan": [
            {"TenPhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "MoTa": "Phụ trách hệ thống hạ tầng AI, vận hành RPA và giải pháp tự động hóa"},
            {"TenPhongBan": "Phòng Nhân Sự", "MoTa": "Quản lý nhân sự, tuyển dụng, đào tạo và theo dõi đánh giá KPI toàn công ty"},
            {"TenPhongBan": "Phòng Kế Toán", "MoTa": "Quản trị dòng tiền, báo cáo tài chính, quyết toán thuế và chứng từ sổ sách"},
            {"TenPhongBan": "Phòng Kinh Doanh & Marketing", "MoTa": "Phát triển thị trường, chăm sóc khách hàng và truyền thông thương hiệu"},
        ],
        "NhanVien": [
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "HoTen": "Nguyễn Văn A", "ChucVu": "Kỹ Sư Trưởng AI", "Email": "nguyenvana@company.com", "SoDienThoai": "0901234567"},
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "HoTen": "Lê Hoàng C", "ChucVu": "Chuyên Viên DevOps", "Email": "lehoangc@company.com", "SoDienThoai": "0909876543"},
            {"PhongBan": "Phòng Nhân Sự", "HoTen": "Trần Thị B", "ChucVu": "Trưởng Phòng HR", "Email": "tranthib@company.com", "SoDienThoai": "0912345678"},
            {"PhongBan": "Phòng Kế Toán", "HoTen": "Phạm Minh D", "ChucVu": "Kế Toán Trưởng", "Email": "phamminhd@company.com", "SoDienThoai": "0933221100"},
        ],
        "MayTinh": [
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "TenMay": "SERVER-AI-MASTER", "DiaChiIP": "192.168.1.10", "LoaiThietBi": "Server", "NguoiSuDung": "Nguyễn Văn A"},
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "TenMay": "DEV-STATION-01", "DiaChiIP": "192.168.1.15", "LoaiThietBi": "Workstation", "NguoiSuDung": "Lê Hoàng C"},
            {"PhongBan": "Phòng Nhân Sự", "TenMay": "PC-HR-LEAD", "DiaChiIP": "192.168.1.25", "LoaiThietBi": "Laptop", "NguoiSuDung": "Trần Thị B"},
            {"PhongBan": "Phòng Kế Toán", "TenMay": "PC-ACC-CHIEF", "DiaChiIP": "192.168.1.30", "LoaiThietBi": "Workstation", "NguoiSuDung": "Phạm Minh D"},
        ],
        "CongViec": [
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "TieuDe": "Nâng cấp hệ thống AI Gateway Phase 47", "NguoiPhuTrach": "Nguyễn Văn A", "TrangThai": "in_progress", "HanChot": "2026-10-01"},
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "TieuDe": "Triển khai Agent giám sát máy chủ RPA", "NguoiPhuTrach": "Lê Hoàng C", "TrangThai": "pending", "HanChot": "2026-10-05"},
            {"PhongBan": "Phòng Nhân Sự", "TieuDe": "Tổng hợp kết quả đánh giá KPI Quý 3", "NguoiPhuTrach": "Trần Thị B", "TrangThai": "pending", "HanChot": "2026-09-30"},
            {"PhongBan": "Phòng Kế Toán", "TieuDe": "Quyết toán ngân sách quý và sao kê sổ cái", "NguoiPhuTrach": "Phạm Minh D", "TrangThai": "completed", "HanChot": "2026-09-20"},
        ],
        "SoSach": [
            {"PhongBan": "Phòng Kỹ Thuật & Tự Động Hóa", "TenTaiLieu": "Sơ đồ kiến trúc hạ tầng VN-MateAI Enterprise.pdf", "DuongDan": "/records/tech/architecture_2026.pdf", "NgayTao": "2026-09-25"},
            {"PhongBan": "Phòng Nhân Sự", "TenTaiLieu": "Quy chế xếp loại KPI và thưởng hiệu suất.docx", "DuongDan": "/records/hr/kpi_regulations_2026.docx", "NgayTao": "2026-09-15"},
            {"PhongBan": "Phòng Kế Toán", "TenTaiLieu": "Báo cáo kiểm toán tài chính bán niên.xlsx", "DuongDan": "/records/acc/financial_audit_h1.xlsx", "NgayTao": "2026-07-31"},
        ],
    }

    try:
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            for sheet_name, rows in data_sheets.items():
                df = pd.DataFrame(rows)
                df.to_excel(writer, sheet_name=sheet_name, index=False)

                # Format style cho từng worksheet
                ws = writer.sheets[sheet_name]
                ws.views.sheetView[0].showGridLines = True

                # Header styling
                header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")  # Xanh dương đậm
                header_font = Font(name="Arial", size=11, bold=True, color="FFFFFF")
                border_thin = Border(
                    left=Side(style="thin", color="CCCCCC"),
                    right=Side(style="thin", color="CCCCCC"),
                    top=Side(style="thin", color="CCCCCC"),
                    bottom=Side(style="thin", color="CCCCCC"),
                )

                for cell in ws[1]:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

                ws.row_dimensions[1].height = 28

                # Data rows styling & Auto fit column width
                for col in ws.columns:
                    max_len = 0
                    col_letter = get_column_letter(col[0].column)
                    for idx, cell in enumerate(col):
                        cell.border = border_thin
                        if idx > 0:
                            cell.font = Font(name="Arial", size=10)
                            cell.alignment = Alignment(vertical="center")
                        val_str = str(cell.value or "")
                        max_len = max(max_len, len(val_str))

                    ws.column_dimensions[col_letter].width = max(max_len + 5, 18)

        output.seek(0)
        filename = f"Mau_Nhap_Lieu_ERP_VNMATEAI_{datetime.now().strftime('%Y%m%d')}.xlsx"

        return StreamingResponse(
            output,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except Exception as exc:
        logger.error("Lỗi khi tạo file Excel mẫu: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi tạo file mẫu: {exc}")


# ── 2. API IMPORT DỮ LIỆU HÀNG LOẠT (BULK IMPORT ENGINE) ─────────────────────

@router.post("/import", summary="Import dữ liệu tổ chức ERP từ file Excel")
async def import_erp_data(
    file: UploadFile = File(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Tiếp nhận file Excel, đọc các Sheet `PhongBan`, `NhanVien`, `MayTinh`, `CongViec`, `SoSach`.
    Ánh xạ dữ liệu và thực thi Transaction an toàn (Rollback hoàn toàn nếu có lỗi).
    """
    if not file.filename.endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Chỉ hỗ trợ file định dạng Excel (.xlsx, .xls) hoặc CSV.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Tệp tải lên rỗng.")

    try:
        excel_io = io.BytesIO(contents)
        xls = pd.ExcelFile(excel_io, engine="openpyxl")
        sheet_names = xls.sheet_names

        # Helper đọc sheet an toàn không phân biệt hoa thường
        def read_sheet(candidates: List[str]) -> List[Dict[str, Any]]:
            for c in candidates:
                for actual in sheet_names:
                    if actual.strip().lower() == c.strip().lower():
                        df = pd.read_excel(xls, sheet_name=actual)
                        df = df.fillna("")
                        return df.to_dict(orient="records")
            return []

        depts_data = read_sheet(["PhongBan", "Departments", "Phòng Ban", "Phong_Ban"])
        emps_data = read_sheet(["NhanVien", "Employees", "Nhân Viên", "Nhan_Vien"])
        devices_data = read_sheet(["MayTinh", "Devices", "Máy Tính", "May_Tinh", "ThietBi"])
        tasks_data = read_sheet(["CongViec", "Tasks", "Công Việc", "Cong_Viec"])
        records_data = read_sheet(["SoSach", "Records", "Sổ Sách", "So_Sach", "HoSo", "Hồ Sơ"])

        if not depts_data and not emps_data and not devices_data and not tasks_data:
            raise HTTPException(
                status_code=400,
                detail="Không tìm thấy các Sheet dữ liệu chuẩn ('PhongBan', 'NhanVien', 'MayTinh', 'CongViec'). Vui lòng tải file mẫu để kiểm tra.",
            )

        # Thực thi bulk import với Transaction Rollback
        result = erp_db.bulk_import(
            departments_data=depts_data,
            employees_data=emps_data,
            devices_data=devices_data,
            tasks_data=tasks_data,
            records_data=records_data,
        )

        logger.info(
            "Người dùng '%s' đã import dữ liệu ERP thành công: %s",
            current_user.get("username", "admin"),
            result.get("stats"),
        )

        return {
            "status": "success",
            "message": result.get("message"),
            "stats": result.get("stats"),
        }

    except ValueError as val_err:
        logger.warning("Lỗi kiểm tra dữ liệu import: %s", val_err)
        return {
            "status": "error",
            "message": str(val_err),
        }
    except Exception as exc:
        logger.error("Lỗi khi xử lý import file Excel: %s", exc)
        return {
            "status": "error",
            "message": f"Lỗi xử lý file: {str(exc)}",
        }


# ── 3. API LẤY CÂY CẤU TRÚC TỔ CHỨC ERP ──────────────────────────────────────

@router.get("/structure", summary="Lấy toàn bộ cây tổ chức ERP")
async def get_erp_structure(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trả về danh sách phòng ban và các thực thể con (Nhân viên, Máy tính, Task, Sổ sách)."""
    try:
        tree = erp_db.get_structure_tree()
        return {
            "status": "success",
            "count": len(tree),
            "departments": tree,
        }
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
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Tạo mới phòng ban trực tiếp từ Web Portal."""
    try:
        dept = erp_db.add_department(name=payload.name, description=payload.description or "")
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
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Xóa phòng ban theo ID."""
    try:
        deleted = erp_db.delete_department(dept_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Không tìm thấy phòng ban để xóa.")
        return {
            "status": "success",
            "message": f"Đã xóa phòng ban #{dept_id} thành công!",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Lỗi khi xóa phòng ban: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi xóa phòng ban: {exc}")

