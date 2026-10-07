# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
mateai/interfaces/http/routers/playbooks.py
===========================================
API Kịch bản vận hành (docs/integrations/playbooks.md). Router chỉ xác thực / phân quyền / dịch lỗi; logic ở application/playbooks.
Xem + chạy thử: manager + admin. Lưu / xoá / chạy / huỷ / xác nhận: admin (và mọi bước vẫn qua chính sách + duyệt).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from mateai.application.playbooks import definition as D
from mateai.application.playbooks import engine as E
from mateai.interfaces.http.auth_dependencies import require_roles

logger = logging.getLogger(__name__)
router = APIRouter()
_READ = Depends(require_roles(["manager", "admin"]))
_WRITE = Depends(require_roles(["admin"]))
_TAG = ["Playbooks"]


def _who(user: Dict[str, Any]) -> str:
    return str(user.get("username") or "admin")


def _guard(fn):
    try:
        return fn()
    except D.PlaybookError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/playbooks", summary="Danh sách kịch bản vận hành", tags=_TAG)
async def list_playbooks(user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"playbooks": E.list_playbooks()}


@router.put("/api/v1/playbooks", summary="Lưu (tạo / cập nhật) một kịch bản — kiểm tra công cụ, tham chiếu, điều kiện", tags=_TAG)
async def save_playbook(body: Dict[str, Any] = Body(...), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    return _guard(lambda: E.save_playbook(body, actor=_who(user)))


@router.get("/api/v1/playbooks/{playbook_id}", summary="Chi tiết một kịch bản", tags=_TAG)
async def get_playbook(playbook_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    pb = E.get_playbook(playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail="Không có kịch bản này")
    return pb


@router.post("/api/v1/playbooks/{playbook_id}/enable", summary="Bật / tắt kịch bản", tags=_TAG)
async def enable_playbook(playbook_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    if not E.set_enabled(playbook_id, bool(body.get("enabled", True)), _who(user)):
        raise HTTPException(status_code=404, detail="Không có kịch bản này")
    return {"status": "success", "enabled": bool(body.get("enabled", True))}


@router.delete("/api/v1/playbooks/{playbook_id}", summary="Xoá kịch bản (lịch sử các lượt chạy được giữ)", tags=_TAG)
async def delete_playbook(playbook_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    if not E.delete_playbook(playbook_id, _who(user)):
        raise HTTPException(status_code=404, detail="Không có kịch bản này")
    return {"status": "success"}


@router.post("/api/v1/playbooks/{playbook_id}/plan", summary="CHẠY THỬ: từng bước sẽ được cho phép / chờ duyệt / bị chặn thế nào (không thực thi)", tags=_TAG)
async def plan_playbook(playbook_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    pb = E.get_playbook(playbook_id)
    if not pb:
        raise HTTPException(status_code=404, detail="Không có kịch bản này")
    return _guard(lambda: E.plan(pb["definition"], body.get("params"), caller=_who(user), human=True))


@router.post("/api/v1/playbooks/{playbook_id}/run", summary="Chạy kịch bản (qua chính sách; có bước cần duyệt thì xin MỘT phiếu cho cả kế hoạch)", tags=_TAG)
async def run_playbook(playbook_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    try:
        return await E.start(playbook_id, body.get("params"), caller=_who(user), human=True, idempotency_key=(body.get("idempotency_key") or None))
    except D.PlaybookError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/api/v1/playbook-runs", summary="Các lượt chạy gần đây", tags=_TAG)
async def list_runs(playbook_id: str = Query(""), limit: int = Query(50, ge=1, le=200), user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    return {"runs": E.list_runs(playbook_id or None, limit)}


@router.get("/api/v1/playbook-runs/{run_id}", summary="Chi tiết lượt chạy: kế hoạch, từng bước, kiểm chứng", tags=_TAG)
async def get_run(run_id: str, user: Dict[str, Any] = _READ) -> Dict[str, Any]:
    run = E.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Không có lượt chạy này")
    return run


@router.post("/api/v1/playbook-runs/{run_id}/cancel", summary="Huỷ lượt chạy (dừng sau bước đang chạy; không tự hoàn tác)", tags=_TAG)
async def cancel_run(run_id: str, user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    out = E.cancel(run_id, _who(user))
    if out["status"] == "not_found":
        raise HTTPException(status_code=404, detail="Không có lượt chạy này")
    return out


@router.post("/api/v1/playbook-runs/{run_id}/confirm", summary="Người xác nhận kết quả của lượt chạy chưa kiểm chứng", tags=_TAG)
async def confirm_run(run_id: str, body: Dict[str, Any] = Body(default={}), user: Dict[str, Any] = _WRITE) -> Dict[str, Any]:
    out = E.confirm(run_id, _who(user), str(body.get("note") or "")[:300])
    if out["status"] != "confirmed":
        raise HTTPException(status_code=409, detail=out.get("error") or "Không xác nhận được")
    return out
