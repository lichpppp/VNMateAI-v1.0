"""
core/api_admin.py
=================
API Quản Trị Trung Tâm Điều Hành Doanh Nghiệp (Enterprise Admin Control Center).
Cung cấp các endpoint:
  1. GET  /api/v1/admin/topology: Sinh bản đồ kiến trúc đa trạm, agents và connectors.
  2. GET  /api/v1/admin/departments/overview: Danh sách phòng ban, nguồn dữ liệu và cảnh báo.
  3. POST /api/v1/admin/departments/save: Lưu cấu hình phòng ban & nguồn dữ liệu động (No-Code Form).
  4. POST /api/v1/worknodes/heartbeat: Nhận ping từ các máy trạm Mac Mini OpenClaw và phát sóng live event.
  5. GET  /api/v1/worknodes/status: Thống kê trạng thái cụm Elastic Grid.
  6. POST /api/v1/admin/cross-report: Tạo báo cáo điều hành liên phòng ban tức thì.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from mateai.infrastructure.database.erp_database import erp_db
from mateai.application.enterprise.department_engine import department_engine
from core.worknodes.elastic_grid_manager import elastic_grid_manager
from mateai.application.agent.agent_orchestrator import multi_agent_system

logger = logging.getLogger("core.api_admin")

router = APIRouter(prefix="/api/v1", tags=["Enterprise Admin & Elastic Grid"])


# ─── Pydantic Models ────────────────────────────────────────────────────────

class DepartmentSaveRequest(BaseModel):
    dept_code: str = Field(..., min_length=1, description="Mã phòng ban (vd: FIN, HR, IT, OPS)")
    dept_name: str = Field(..., min_length=1, description="Tên phòng ban")
    data_clearance_level: int = Field(default=1, ge=1, le=4, description="Cấp độ bảo mật (1-4)")
    config_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Metadata JSON động")
    data_source: Optional[Dict[str, Any]] = Field(default=None, description="Cấu hình nguồn dữ liệu nếu có")
    is_active: bool = Field(default=True)


class WorknodeHeartbeatRequest(BaseModel):
    node_id: str = Field(..., description="Mã định danh máy trạm (vd: mac-mini-01)")
    ip: str = Field(default="127.0.0.1", description="Địa chỉ IP")
    capabilities: Optional[List[str]] = Field(default_factory=lambda: ["GUI_OPENCLAW", "LOCAL_OCR"])
    status: str = Field(default="READY")
    cpu_percent: float = Field(default=0.0)
    ram_percent: float = Field(default=0.0)
    active_tasks: int = Field(default=0)
    platform: Optional[str] = Field(default="")


class CrossReportRequest(BaseModel):
    scope: Optional[List[str]] = Field(default=None, description="Danh sách phòng ban cần đối soát")
    clearance_level: int = Field(default=3, description="Mức bảo mật của người yêu cầu")


# ─── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/admin/topology")
async def get_admin_topology() -> Dict[str, Any]:
    """
    Trả về cấu trúc toàn bộ đồ thị mạng lưới:
    - VN-MateAI Brain Core
    - 9Router Multi-Model Gateway
    - Sub-Agents (CFO, HR, CTO)
    - Phòng ban & Data Connectors (eInvoice, M365, AWS, Paperless)
    - Elastic Standby Worker Grid (Các máy Mac Mini Online/Standby)
    """
    grid_info = elastic_grid_manager.get_grid_overview()
    depts = erp_db.get_enterprise_departments()

    nodes: List[Dict[str, Any]] = [
        # Core & Hub
        {
            "id": "core",
            "type": "core",
            "label": "VN-MateAI Brain Core",
            "subtitle": "Ubuntu Master (FastAPI :443)",
            "status": "online",
            "icon": "brain",
            "x": 400,
            "y": 200,
        },
        {
            "id": "router_9",
            "type": "gateway",
            "label": "9Router AI Gateway",
            "subtitle": "Port :20128 Multi-Model",
            "status": "online",
            "icon": "network",
            "x": 400,
            "y": 50,
        },
        # Agents
        {
            "id": "agent_ceo",
            "type": "agent",
            "label": "CEO Router Agent",
            "subtitle": "Virtual COO & Inter-Agent Bus",
            "status": "online",
            "icon": "crown",
            "x": 200,
            "y": 120,
        },
        {
            "id": "agent_cfo",
            "type": "agent",
            "label": "CFO Agent",
            "subtitle": "Finance & Cashflow",
            "status": "online",
            "icon": "cash",
            "x": 100,
            "y": 220,
        },
        {
            "id": "agent_hr",
            "type": "agent",
            "label": "HR Agent",
            "subtitle": "People & Policy RAG",
            "status": "online",
            "icon": "users",
            "x": 100,
            "y": 320,
        },
        {
            "id": "agent_cto",
            "type": "agent",
            "label": "CTO Agent",
            "subtitle": "Cloud & AIOps",
            "status": "online",
            "icon": "server",
            "x": 200,
            "y": 420,
        },
        # Connectors
        {
            "id": "conn_m365",
            "type": "connector",
            "label": "Microsoft 365",
            "subtitle": "Teams & Outlook Graph",
            "status": "active",
            "icon": "mail",
            "x": 650,
            "y": 80,
        },
        {
            "id": "conn_einvoice",
            "type": "connector",
            "label": "eInvoice Hub",
            "subtitle": "VNPT / Viettel / MISA",
            "status": "active",
            "icon": "receipt",
            "x": 650,
            "y": 180,
        },
        {
            "id": "conn_paperless",
            "type": "connector",
            "label": "Paperless DMS",
            "subtitle": "OCR & Legal Vault",
            "status": "active",
            "icon": "file-text",
            "x": 650,
            "y": 280,
        },
        {
            "id": "conn_cloud",
            "type": "connector",
            "label": "AWS & OCI Cloud",
            "subtitle": "FinOps Cost Monitoring",
            "status": "active",
            "icon": "cloud",
            "x": 650,
            "y": 380,
        },
        # Elastic Grid Hub
        {
            "id": "worker_grid_cluster",
            "type": "worker_cluster",
            "label": "Elastic Standby Grid",
            "subtitle": f"{grid_info['online_nodes_count']} Online | {grid_info['standby_queue_length']} Standby Tasks",
            "status": "online" if grid_info["online_nodes_count"] > 0 else "standby",
            "icon": "cpu",
            "x": 400,
            "y": 420,
        },
    ]

    edges: List[Dict[str, Any]] = [
        {"id": "e_core_9r", "source": "router_9", "target": "core", "label": "LLM Stream", "active": True},
        {"id": "e_core_ceo", "source": "core", "target": "agent_ceo", "label": "Orchestrate", "active": True},
        {"id": "e_ceo_cfo", "source": "agent_ceo", "target": "agent_cfo", "label": "Bus Dispatch"},
        {"id": "e_ceo_hr", "source": "agent_ceo", "target": "agent_hr", "label": "Bus Dispatch"},
        {"id": "e_ceo_cto", "source": "agent_ceo", "target": "agent_cto", "label": "Bus Dispatch"},
        {"id": "e_core_m365", "source": "core", "target": "conn_m365", "label": "Graph API"},
        {"id": "e_cfo_inv", "source": "agent_cfo", "target": "conn_einvoice", "label": "Invoice Data"},
        {"id": "e_core_paperless", "source": "core", "target": "conn_paperless", "label": "RAG Sync"},
        {"id": "e_cto_cloud", "source": "agent_cto", "target": "conn_cloud", "label": "Cost Metrics"},
        {"id": "e_core_grid", "source": "core", "target": "worker_grid_cluster", "label": "Zero-Trust RPA", "active": grid_info["online_nodes_count"] > 0},
    ]

    # Đưa các node Mac Mini thật vào sơ đồ nếu đang Online
    y_offset = 480
    for idx, wn in enumerate(grid_info["nodes"]):
        if wn["is_online"]:
            wn_id = f"node_{wn['node_id']}"
            nodes.append({
                "id": wn_id,
                "type": "worknode",
                "label": f"Mac Mini: {wn['node_id']}",
                "subtitle": f"{wn['ip']} | CPU {wn['cpu_percent']}% | RAM {wn['ram_percent']}%",
                "status": "online",
                "icon": "desktop",
                "x": 250 + (idx % 3) * 180,
                "y": y_offset,
            })
            edges.append({
                "id": f"e_grid_{wn_id}",
                "source": "worker_grid_cluster",
                "target": wn_id,
                "label": "Heartbeat",
                "active": True,
            })

    return {
        "status": "success",
        "nodes": nodes,
        "edges": edges,
        "grid_summary": grid_info,
        "departments_count": len(depts),
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/admin/departments/overview")
async def get_departments_overview() -> Dict[str, Any]:
    """Lấy danh sách các phòng ban kèm các nguồn dữ liệu đang kết nối."""
    depts = erp_db.get_enterprise_departments()
    result = []
    for d in depts:
        code = d["dept_code"]
        sources = erp_db.get_department_data_sources(code)
        cached_summary = department_engine.get_cached_summary(code)
        result.append({
            **d,
            "sources": sources,
            "sources_count": len(sources),
            "cached_summary": cached_summary or "Chưa thực hiện quét dữ liệu",
        })
    return {
        "status": "success",
        "total_departments": len(result),
        "departments": result,
    }


@router.post("/admin/departments/save")
async def save_department_config(req: DepartmentSaveRequest) -> Dict[str, Any]:
    """Khai báo hoặc cập nhật cấu hình phòng ban & nguồn dữ liệu qua No-Code Web UI."""
    saved_dept = department_engine.register_department({
        "dept_code": req.dept_code,
        "dept_name": req.dept_name,
        "data_clearance_level": req.data_clearance_level,
        "config_metadata": req.config_metadata,
        "is_active": req.is_active,
    })

    bound_source = None
    if req.data_source:
        bound_source = department_engine.bind_data_source(
            dept_code=req.dept_code,
            source_dict=req.data_source,
        )

    # Thu thập thử dữ liệu ngay sau khi cấu hình
    try:
        await department_engine.ingest_department_data(req.dept_code, force_refresh=True)
    except Exception as e:
        logger.warning("Không thể tự động ingest ngay cho %s: %s", req.dept_code, e)

    return {
        "status": "success",
        "department": saved_dept,
        "data_source": bound_source,
        "message": f"Đã lưu cấu hình phòng ban {req.dept_code} thành công.",
    }


@router.post("/worknodes/heartbeat")
async def receive_worknode_heartbeat(req: WorknodeHeartbeatRequest) -> Dict[str, Any]:
    """
    Endpoint nhận ping từ máy Mac Mini OpenClaw hoặc trạm ngoại vi.
    Tự động phát hiện node mới và phát sóng sự kiện qua WebSocket Topology.
    """
    ack = elastic_grid_manager.record_heartbeat(
        node_id=req.node_id,
        ip=req.ip,
        capabilities=req.capabilities,
        status=req.status,
        cpu_percent=req.cpu_percent,
        ram_percent=req.ram_percent,
        active_tasks=req.active_tasks,
    )

    # Phát sóng sự kiện WebSocket Topology để Web UI cập nhật ngay mà không cần reload trang
    try:
        from core.realtime_hub import broadcast_topology_event
        await broadcast_topology_event(
            source=f"worknode_{req.node_id}",
            target="worker_grid_cluster",
            action=f"heartbeat_{req.status}",
        )
    except Exception:
        pass

    return ack


@router.get("/worknodes/status")
async def get_worknodes_status() -> Dict[str, Any]:
    """Lấy danh sách các trạm ngoại vi và hàng đợi Standby."""
    return {
        "status": "success",
        "grid": elastic_grid_manager.get_grid_overview(),
    }


@router.post("/admin/cross-report")
async def generate_cross_report_api(req: CrossReportRequest) -> Dict[str, Any]:
    """Kích hoạt báo cáo liên phòng ban tức thời từ Admin Web UI."""
    return multi_agent_system.generate_cross_domain_report(
        query_context="Yêu cầu từ Admin Web UI",
        requested_departments=req.scope,
        clearance_level=req.clearance_level,
    )


@router.get("/admin/ephemeral-cache")
async def get_ephemeral_cache_stats() -> Dict[str, Any]:
    """Giám sát bộ đệm RAM tự hủy theo tiêu chuẩn GDPR / Nghị định 13."""
    from core.ephemeral_cache import ephemeral_cache
    return {
        "status": "success",
        "cache_stats": ephemeral_cache.get_stats(),
        "policy": {
            "sliding_ttl_minutes": 15,
            "hard_timeout_minutes": 30,
            "sweeper_interval_seconds": 60,
            "compliance": "GDPR / Decree 13 Non-PII In-Memory Only",
        },
    }


@router.post("/admin/ephemeral-cache/flush")
async def flush_ephemeral_cache_all() -> Dict[str, Any]:
    """Tiêu hủy khẩn cấp toàn bộ dữ liệu tạm trên RAM (Emergency RAM Flush)."""
    from core.ephemeral_cache import ephemeral_cache
    flushed = ephemeral_cache.sweep_expired()
    # Hoặc xóa toàn bộ cửa sổ cache hiện tại
    with ephemeral_cache._lock:
        total_purged = len(ephemeral_cache._store)
        ephemeral_cache._store.clear()
        ephemeral_cache._active_domain_by_session.clear()

    return {
        "status": "success",
        "purged_items_count": total_purged,
        "message": f"Đã tiêu hủy an toàn {total_purged} mục dữ liệu tạm trên RAM.",
    }

