"""
mateai/interfaces/http/routers/files.py
=======================================
Thao tác tệp trên máy chủ qua portal (liệt kê / đọc / ghi / xoá). Ghi và xoá
cần phê duyệt (Zero-Trust) — xem từng endpoint.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


class FsListRequest(BaseModel):
    path: str = Field(default=".", description="Đường dẫn thư mục")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsReadRequest(BaseModel):
    file_path: str = Field(..., description="Đường dẫn file cần đọc")
    lines: int = Field(default=500, description="Số dòng tối đa từ cuối file")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsWriteRequest(BaseModel):
    file_path: str = Field(..., description="Đường dẫn file cần ghi")
    content: str = Field(..., description="Nội dung file")
    mode: str = Field(default="w", description="Chế độ 'w' hoặc 'a'")
    confirmed: bool = Field(default=False, description="Cờ xác nhận phê duyệt bảo mật")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


class FsDeleteRequest(BaseModel):
    path: str = Field(..., description="Đường dẫn file hoặc thư mục cần xóa")
    is_folder: bool = Field(default=False, description="True nếu là thư mục")
    confirmed: bool = Field(default=False, description="Cờ xác nhận phê duyệt bảo mật")
    target_client: Optional[str] = Field(default="master", description="Máy trạm đích")
    target_client_id: Optional[str] = Field(default=None, description="Bí danh máy trạm đích")


@router.post(
    "/api/v1/fs/list",
    summary="List directory contents (Low Risk - Auto Execute)",
    tags=["Native File System"],
)
async def fs_list_endpoint(
    req: FsListRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.application.skills.builtin.file_system import list_directory
    from mateai.application.security.safety_guard import security_engine

    target = req.target_client_id or req.target_client or "master"
    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = await run_blocking(list_directory, path=req.path)
        security_engine.log_audit("master", "list_directory", "SAFE", "SUCCESS", {"path": req.path})
        return res
    else:
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "list_directory", {"path": req.path})


@router.post(
    "/api/v1/fs/read",
    summary="Read file text/log with trailing lines limitation (Low Risk - Auto Execute)",
    tags=["Native File System"],
)
async def fs_read_endpoint(
    req: FsReadRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.application.skills.builtin.file_system import read_file
    from mateai.application.security.safety_guard import security_engine

    target = req.target_client_id or req.target_client or "master"
    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = await run_blocking(read_file, file_path=req.file_path, lines=req.lines)
        security_engine.log_audit("master", "read_file", "SAFE", "SUCCESS", {"file_path": req.file_path, "lines": req.lines})
        return res
    else:
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "read_file", {"file_path": req.file_path, "lines": req.lines})


@router.post(
    "/api/v1/fs/write",
    summary="Write file with Zero-Trust Security approval check",
    tags=["Native File System"],
)
async def fs_write_endpoint(
    req: FsWriteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.application.skills.builtin.file_system import write_file
    from mateai.application.security.safety_guard import security_engine
    from mateai.application.security.zero_trust import evaluate_action_risk
    from mateai.application.agent.state_manager import state_manager

    target = req.target_client_id or req.target_client or "master"
    risk = evaluate_action_risk("write_file", {"file_path": req.file_path, "mode": req.mode})

    if risk == "NEED_CONFIRM" and not req.confirmed:
        username = current_user.get("username", "admin")
        act_id = state_manager.save_pending_action(
            user_id=username,
            tool_name="write_file",
            arguments={"file_path": req.file_path, "content": req.content, "mode": req.mode},
            target_client=target,
            query=f"Ghi tệp tin: {req.file_path}",
        )
        security_engine.log_audit(target, "write_file", "NEED_CONFIRM", "PENDING_CONFIRMATION", {"file_path": req.file_path})
        return {
            "status": "need_confirm",
            "action_id": act_id,
            "message": f"Tác vụ ghi tệp tin '{req.file_path}' yêu cầu phê duyệt bảo mật.",
            "requires_confirmation": True,
        }

    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = await run_blocking(write_file, file_path=req.file_path, content=req.content, mode=req.mode)
        security_engine.log_audit("master", "write_file", "NEED_CONFIRM", "SUCCESS" if res.get("status") == "success" else "FAILED", {"file_path": req.file_path})
        return res
    else:
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "write_file", {"file_path": req.file_path, "content": req.content, "mode": req.mode})


@router.post(
    "/api/v1/fs/delete",
    summary="Delete file or folder with Zero-Trust Security approval check",
    tags=["Native File System"],
)
async def fs_delete_endpoint(
    req: FsDeleteRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.application.skills.builtin.file_system import delete_item
    from mateai.application.security.safety_guard import security_engine
    from mateai.application.security.zero_trust import evaluate_action_risk
    from mateai.application.agent.state_manager import state_manager

    target = req.target_client_id or req.target_client or "master"
    risk = evaluate_action_risk("delete_item", {"path": req.path, "is_folder": req.is_folder})

    if risk == "NEED_CONFIRM" and not req.confirmed:
        username = current_user.get("username", "admin")
        act_id = state_manager.save_pending_action(
            user_id=username,
            tool_name="delete_item",
            arguments={"path": req.path, "is_folder": req.is_folder},
            target_client=target,
            query=f"Xóa {'thư mục' if req.is_folder else 'tệp tin'}: {req.path}",
        )
        security_engine.log_audit(target, "delete_item", "NEED_CONFIRM", "PENDING_CONFIRMATION", {"path": req.path})
        return {
            "status": "need_confirm",
            "action_id": act_id,
            "message": f"Tác vụ xóa '{req.path}' yêu cầu phê duyệt bảo mật.",
            "requires_confirmation": True,
        }

    if target.lower() in ("master", "local", "server", "chính", "cục bộ"):
        res = await run_blocking(delete_item, path=req.path, is_folder=req.is_folder)
        security_engine.log_audit("master", "delete_item", "NEED_CONFIRM", "SUCCESS" if res.get("status") == "success" else "FAILED", {"path": req.path})
        return res
    else:
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        return await orchestrator.execute_on_client(target, "delete_item", {"path": req.path, "is_folder": req.is_folder})
