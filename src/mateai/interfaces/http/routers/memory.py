"""
mateai/interfaces/http/routers/memory.py
=========================================
Trí nhớ hội thoại và trí nhớ sự cố (vector): lịch sử, thống kê, ghi nhớ, tìm kiếm, sao lưu.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/v1/memory/history",
    summary="Get conversation history for a session (Phase 34 Sliding Window)",
    tags=["Conversational Memory"],
)
async def get_memory_history(
    session_id: str = "default",
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """Retrieve sliding window conversation history for session_id."""
    from mateai.application.conversation.memory_manager import memory_manager
    history = memory_manager.get_history(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "message_count": len(history),
        "history": history,
        "stats": memory_manager.get_stats(),
    }


@router.post(
    "/api/v1/memory/clear",
    summary="Clear conversation history for a session (Phase 34)",
    tags=["Conversational Memory"],
)
async def clear_memory_history(
    session_id: str = "default",
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Reset conversational memory for session_id."""
    from mateai.application.conversation.memory_manager import memory_manager
    memory_manager.clear_history(session_id)
    return {
        "status": "success",
        "session_id": session_id,
        "message": f"Đã xóa lịch sử hội thoại của session '{session_id}'.",
    }


class MemorizeRequest(BaseModel):
    error_signature: str
    root_cause: str
    script: str
    target_client: str = "master"
    metadata: Optional[Dict[str, Any]] = None


class MemorySearchRequest(BaseModel):
    query: str
    n_results: int = 3


@router.get(
    "/api/v1/memory/stats",
    summary="Get Vector DB memory statistics and storage mode",
    tags=["Cognitive Memory"],
)
async def get_memory_stats_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.infrastructure.memory.cognitive_memory import get_memory_stats
    return get_memory_stats()


@router.post(
    "/api/v1/memory/memorize",
    summary="Memorize an incident solution into Vector DB",
    tags=["Cognitive Memory"],
)
async def memorize_solution_endpoint(
    payload: MemorizeRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.infrastructure.memory.cognitive_memory import memorize_solution
    doc_id = memorize_solution(
        error_signature=payload.error_signature,
        root_cause=payload.root_cause,
        script=payload.script,
        target_client=payload.target_client,
        metadata=payload.metadata,
    )
    return {"status": "success", "id": doc_id}


@router.post(
    "/api/v1/memory/search",
    summary="Semantic search past incidents from Vector DB",
    tags=["Cognitive Memory"],
)
async def search_memory_endpoint(
    payload: MemorySearchRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.infrastructure.memory.cognitive_memory import search_past_incidents
    results = search_past_incidents(error_log_snippet=payload.query, n_results=payload.n_results)
    return {"status": "success", "query": payload.query, "results": results}


@router.post(
    "/api/v1/memory/backup",
    summary="Create a portable zip backup of the Vector DB storage folder",
    tags=["Cognitive Memory"],
)
async def backup_memory_endpoint(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    from mateai.infrastructure.memory.cognitive_memory import backup_vector_db
    zip_path = backup_vector_db()
    zip_file = Path(zip_path)
    filename = zip_file.name
    size_mb = round(zip_file.stat().st_size / (1024 * 1024), 2)
    return {
        "status": "success",
        "backup_path": zip_path,
        "filename": filename,
        "size_mb": size_mb,
        "download_url": f"/api/v1/memory/download-backup?filename={filename}",
    }


@router.get(
    "/api/v1/memory/download-backup",
    summary="Download vector database backup zip file",
    tags=["Cognitive Memory"],
)
async def download_backup_endpoint(
    filename: str = Query(...),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    from mateai.infrastructure.memory.cognitive_memory import BACKUPS_DIR
    target = BACKUPS_DIR / filename
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Backup file not found.")
    return FileResponse(
        path=str(target),
        filename=filename,
        media_type="application/zip",
    )
