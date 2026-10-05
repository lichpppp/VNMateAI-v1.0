"""
mateai/interfaces/http/routers/system.py
=========================================
Hệ thống: số liệu máy chủ, sơ đồ topology (đọc / phát sự kiện / lưu / đặt lại).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import active_audio_nodes

logger = logging.getLogger(__name__)

router = APIRouter()


class TopologyTriggerRequest(BaseModel):
    """Payload for POST /api/v1/system/topology/trigger."""
    source: str = Field(default="core", description="Node phát nguồn (vd: 'core', 'agent_ceo')")
    target: str = Field(default="plugin_m365", description="Node đích (vd: 'plugin_m365', 'worker_cluster')")
    action: Optional[str] = Field(default="", description="Tên hành động hoặc thông điệp")


class TopologySaveRequest(BaseModel):
    """Payload for POST /api/v1/system/topology/save."""
    nodes: List[Dict[str, Any]] = Field(default_factory=list, description="Danh sách nodes")
    edges: List[Dict[str, Any]] = Field(default_factory=list, description="Danh sách edges")


@router.get(
    "/api/v1/system/stats",
    summary="Real-time Host System Hardware & Runtime Telemetry",
    tags=["System"],
)
async def get_system_stats(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Trả về 100% dữ liệu telemetry thực tế từ phần cứng (CPU, RAM, Uptime) và SQLite (Zero Mock)."""
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.interfaces.websocket.client_orchestrator import orchestrator
    from core.plugin_manager import plugin_manager

    hw_stats = db_manager.get_system_hardware_stats()
    task_counts = db_manager.count_tasks()
    all_users = db_manager.get_all_users()
    online_clients = orchestrator.get_connected_clients()

    return {
        "status": "success",
        "timestamp": datetime.utcnow().isoformat(),
        "hardware": hw_stats,
        "tasks": task_counts,
        "users_count": len(all_users),
        "online_clients_count": len(online_clients),
        "audio_nodes_count": len(active_audio_nodes),
        "skills_count": plugin_manager.get_skill_count(),
    }


_CUSTOM_TOPOLOGY_PATH = Path(settings.PROJECT_ROOT) / "storage" / "custom_topology.json"


@router.get(
    "/api/v1/system/topology",
    summary="Sơ đồ hệ thống — trạng thái THẬT từng thành phần",
    tags=["System", "Topology"],
)
async def get_system_topology(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """
    Các thành phần + cạnh nối, trạng thái ok / degraded / down / off / unknown, số
    đo thật và lý do (`interfaces/http/topology.snapshot`). Bố cục người dùng đã
    lưu chỉ giữ VỊ TRÍ các ô — trạng thái luôn lấy thật (trước đây bản lưu thay
    cả trạng thái, và bản mặc định là trạng thái viết cứng "online / Active").
    """
    from mateai.interfaces.http.topology import snapshot
    snap = await run_blocking(snapshot)
    snap["layout"] = _saved_layout()
    return snap


def _saved_layout() -> Dict[str, Dict[str, float]]:
    """Vị trí ô người dùng đã kéo thả: {node_id: {x, y}} (bản lưu cũ có cả nodes -> lấy position)."""
    if not _CUSTOM_TOPOLOGY_PATH.exists():
        return {}
    try:
        data = json.loads(_CUSTOM_TOPOLOGY_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được bố cục topology đã lưu: %s", exc)
        return {}
    if isinstance(data.get("positions"), dict):
        return data["positions"]
    out = {}
    for n in data.get("nodes") or []:
        pos = n.get("position") if isinstance(n, dict) else None
        if isinstance(pos, dict) and "x" in pos and "y" in pos:
            out[str(n.get("id"))] = {"x": pos["x"], "y": pos["y"]}
    return out


@router.get(
    "/api/v1/system/topology/events",
    summary="Sự kiện bước xử lý gần đây (vòng 300 sự kiện)",
    tags=["System", "Topology"],
)
async def get_topology_events(
    since: int = Query(0, ge=0, description="Chỉ lấy sự kiện có seq lớn hơn"),
    limit: int = Query(150, ge=1, le=300),
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    from mateai.application.operations import topology_events
    return {"status": "success", "events": topology_events.recent(limit=limit, since_seq=since)}


@router.post(
    "/api/v1/system/topology/trigger",
    summary="Sự kiện MÔ PHỎNG (thử giao diện) — chỉ admin",
    tags=["System", "Topology"],
)
async def trigger_topology_event(
    payload: TopologyTriggerRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Sự kiện thử, gắn `simulated` — giao diện hiện nhãn MÔ PHỎNG. Trước đây ai đăng
    nhập cũng phát được, và sự kiện giả hiện như thật trên màn hình mọi người xem."""
    from mateai.application.operations.topology_events import publish
    ev = publish("simulation", source=payload.source, target=payload.target, status="ok",
                 detail=f"[MÔ PHỎNG bởi {user.get('username')}] {payload.action or ''}", simulated=True)
    return {"status": "success", "event": ev}


@router.post(
    "/api/v1/system/topology/save",
    summary="Phase 88: Save User-Customized Topology Graph",
    tags=["System", "Topology"],
)
async def save_custom_topology(
    payload: TopologySaveRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Lưu BỐ CỤC (vị trí các ô) — trạng thái không lưu, luôn lấy thật."""
    try:
        _CUSTOM_TOPOLOGY_PATH.parent.mkdir(parents=True, exist_ok=True)
        positions = {
            str(n.get("id")): {"x": n["position"]["x"], "y": n["position"]["y"]}
            for n in payload.nodes
            if isinstance(n, dict) and isinstance(n.get("position"), dict)
            and "x" in n["position"] and "y" in n["position"]
        }
        data = {"positions": positions, "saved_at": datetime.utcnow().isoformat(),
                "saved_by": user.get("username")}
        _CUSTOM_TOPOLOGY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("[Topology] Đã lưu bố cục sơ đồ (%d ô)", len(positions))
        return {
            "status": "success",
            "message": "Đã lưu bố cục sơ đồ (vị trí các ô)",
            "total_nodes": len(positions),
        }
    except Exception as exc:
        logger.error("[Topology] Lỗi khi lưu custom_topology: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể lưu sơ đồ: {exc}")


@router.post(
    "/api/v1/system/topology/reset",
    summary="Phase 88: Reset Topology Graph to System Default",
    tags=["System", "Topology"],
)
async def reset_custom_topology(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Khôi phục sơ đồ topology về mặc định ban đầu do hệ thống tự phát hiện."""
    try:
        if _CUSTOM_TOPOLOGY_PATH.exists():
            _CUSTOM_TOPOLOGY_PATH.unlink()
            logger.info("[Topology] Đã xoá custom_topology.json, khôi phục mặc định")
        return {
            "status": "success",
            "message": "Đã khôi phục sơ đồ topology về cấu hình mặc định",
        }
    except Exception as exc:
        logger.error("[Topology] Lỗi khi khôi phục custom_topology: %s", exc)
        raise HTTPException(status_code=500, detail=f"Không thể khôi phục sơ đồ: {exc}")


# ── Khâu cảnh báo chung (application/operations/alert_dispatcher) ─────────────

class AlertTestRequest(BaseModel):
    channel: Optional[str] = Field(default=None, description="id kênh (vd alert_teams, telegram); bỏ trống = mọi kênh đã kết nối")


@router.get(
    "/api/v1/system/notifications",
    summary="Kênh cảnh báo: đã kết nối / chờ kết nối, kết quả gửi gần nhất, lịch sử",
    tags=["System", "Alerts"],
)
async def get_notification_channels(user: dict = Depends(require_roles(["manager", "admin"]))) -> Dict[str, Any]:
    """Chỉ trả TÊN khoá còn thiếu — không bao giờ trả URL webhook / mật khẩu."""
    from mateai.application.operations import alert_dispatcher
    from mateai.infrastructure.notifications import load_rules
    return {
        "status": "success",
        "rules": await run_blocking(load_rules),
        "channels": await run_blocking(alert_dispatcher.channel_status),
        "history": list(alert_dispatcher.HISTORY)[:20],
    }


@router.post(
    "/api/v1/system/notifications/test",
    summary="Gửi cảnh báo THỬ tới một kênh (hoặc mọi kênh đã kết nối) — chỉ admin",
    tags=["System", "Alerts"],
)
async def test_notification_channel(
    payload: AlertTestRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    from mateai.application.operations import alert_dispatcher
    from mateai.infrastructure.notifications import CHANNELS
    if payload.channel and payload.channel not in CHANNELS and payload.channel != alert_dispatcher.TELEGRAM:
        raise HTTPException(status_code=400, detail=f"Không có kênh '{payload.channel}'")
    return await alert_dispatcher.dispatch(
        "Gửi thử cảnh báo",
        f"Tin thử do {user.get('username')} gửi từ VN-MateAI để kiểm tra kênh cảnh báo. Không cần xử lý.",
        severity="info", category=f"test:{payload.channel or 'all'}", source="Kiểm tra kết nối",
        force=True, only=payload.channel)
