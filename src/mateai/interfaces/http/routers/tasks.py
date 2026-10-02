"""
mateai/interfaces/http/routers/tasks.py
========================================
Giao việc vi mô (micro-task) và nhật ký KPI.
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

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class TaskDispatchRequest(BaseModel):
    """Payload for POST /api/v1/tasks/send."""
    client_id: str = Field(..., description="ID máy trạm đích")
    message: str = Field(..., min_length=1, description="Nội dung công việc cần nhắc")
    sender: Optional[str] = Field(default="Ban Giám Đốc", description="Tên người hoặc phòng ban gửi")


@router.get(
    "/api/v1/tasks/kpi-logs",
    summary="Get recent KPI task logs and completion statistics",
    tags=["Micro-Tasking"],
)
async def get_kpi_logs_endpoint(
    limit: int = Query(default=100, ge=1, le=1000),
    client_id: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return historical task log from logs/kpi_logs.csv and aggregate KPI metrics."""
    from mateai.application.devices.task_manager import task_manager
    return task_manager.get_kpi_logs(limit=limit, client_id=client_id, status=status)


@router.post(
    "/api/v1/tasks/send",
    summary="Dispatch a micro-task popup to a worker client node",
    tags=["Micro-Tasking"],
)
async def send_task_endpoint(
    payload: TaskDispatchRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """Send interactive task popup to LAN worker node with role check."""
    if current_user.get("role") == "viewer":
        raise HTTPException(
            status_code=403,
            detail="Tài khoản Viewer chỉ có quyền xem, không được phát lệnh giao việc.",
        )

    from mateai.application.devices.task_manager import task_manager
    sender = payload.sender or current_user.get("full_name", "Ban Giám Đốc")
    result = await task_manager.dispatch_task(
        client_id=payload.client_id,
        message=payload.message,
        sender=sender,
    )
    if result.get("status") != "success":
        raise HTTPException(status_code=400, detail=result.get("message", "Gửi task thất bại"))
    return result
