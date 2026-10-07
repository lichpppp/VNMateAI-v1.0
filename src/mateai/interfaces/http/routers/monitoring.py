# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/monitoring.py
============================================
API Giám sát hạ tầng (Prometheus / Grafana) — docs/integrations/monitoring.md. Chỉ ĐỌC dữ liệu từ nguồn; không ghi vào Prometheus / Grafana.
Xem: manager + admin. Làm mới ngay + chạy PromQL: manager + admin (chỉ đọc). Router chỉ xác thực / dịch lỗi; logic ở application/monitoring.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from mateai.application.monitoring import infra_monitor as im
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)
router = APIRouter()
_USER = Depends(require_roles(["manager", "admin"]))
_ADMIN = Depends(require_roles(["admin"]))
_TAG = ["Monitoring"]


@router.get("/api/v1/monitoring/overview", summary="Tổng quan giám sát: cảnh báo, target, CPU/RAM/đĩa, dashboard", tags=_TAG)
async def overview(force: bool = Query(False), user: Dict[str, Any] = _USER) -> Dict[str, Any]:
    return await im.snapshot(force=force)


@router.post("/api/v1/monitoring/query", summary="Chạy một truy vấn PromQL (chỉ đọc, có giới hạn)", tags=_TAG)
async def promql(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _USER) -> Dict[str, Any]:
    try:
        return await im.query_promql(str(body.get("promql") or ""), source_id=(body.get("source_id") or None),
                                     limit=int(body.get("limit") or 30))
    except im.MonitorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/api/v1/monitoring/refresh", summary="Thu thập ngay + đồng bộ sự cố (không chờ chu kỳ)", tags=_TAG)
async def refresh(user: Dict[str, Any] = _ADMIN) -> Dict[str, Any]:
    return await im.poll_once()
