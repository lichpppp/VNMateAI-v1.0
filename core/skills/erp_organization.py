"""
core/skills/erp_organization.py
===============================
Kỹ năng tra cứu dữ liệu tổ chức doanh nghiệp (ERP Organization Skill) cho VN-MateAI.
Phase 47: ERP Structure & Bulk Data Import Engine.
"""

from typing import Any, Dict
from core.database import erp_db


def query_organization_data(query: str) -> Dict[str, Any]:
    """
    Tra cứu thông tin cơ sở dữ liệu tổ chức ERP doanh nghiệp:
    Tìm kiếm phòng ban, nhân sự, máy tính/thiết bị theo IP/hostname, công việc và hồ sơ tài liệu.
    """
    try:
        data = erp_db.query_organization(query)
        return {
            "status": "success",
            "query": query,
            "data": data,
        }
    except Exception as exc:
        return {
            "status": "error",
            "query": query,
            "error": str(exc),
        }
