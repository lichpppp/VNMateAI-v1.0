"""
mateai/interfaces/http/routers/auth.py
=======================================
Đăng nhập (cấp JWT) và thông tin người dùng hiện tại.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.application.security.auth_manager import auth_manager
from mateai.config.loader import get_assistant_name, settings
from mateai.interfaces.http import log_stream
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import active_audio_nodes

logger = logging.getLogger(__name__)

router = APIRouter()


LOGIN_MAX_FAILURES = 8
LOGIN_WINDOW_S = 300.0
LOGIN_LOCK_S = 300.0
def _store():
    """Mốc đăng nhập sai ở kho dùng chung (Redis khi có): khoá áp cho MỌI tiến trình (§145)."""
    from mateai.infrastructure.cache import shared_state
    return shared_state.store()


def _login_locked(keys) -> float:
    """Số giây còn phải chờ (0 = được thử). Khoá khi `LOGIN_MAX_FAILURES` lần sai gần
    nhất nằm trong `LOGIN_WINDOW_S`; khoá kéo dài `LOGIN_LOCK_S` kể từ lần sai cuối."""
    now = time.time()
    wait = 0.0
    for k in keys:
        fails = sorted(_store().events_since("login:" + k, now - LOGIN_WINDOW_S - LOGIN_LOCK_S))
        if len(fails) >= LOGIN_MAX_FAILURES and fails[-1] - fails[-LOGIN_MAX_FAILURES] <= LOGIN_WINDOW_S:
            wait = max(wait, LOGIN_LOCK_S - (now - fails[-1]))
    return max(0.0, wait)


def _login_failed(keys) -> None:
    now = time.time()
    for k in keys:
        _store().events_add("login:" + k, now)


def _login_audit(username: str, ip: str, event: str) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(username)[:80], "login", "AUTH", "SUCCESS" if event == "LOGIN_OK" else "REJECTED",
                                  {"event": event, "source_ip": ip})
    except Exception:  # noqa: BLE001
        pass


class LoginRequest(BaseModel):
    """Payload for POST /api/v1/login."""
    username: str = Field(..., min_length=1, description="Tên đăng nhập")
    password: str = Field(..., min_length=1, description="Mật khẩu")


@router.post(
    "/api/v1/login",
    summary="Đăng nhập Web Portal và nhận JWT Access Token",
    tags=["Authentication"],
)
async def login_endpoint(payload: LoginRequest, request: Request) -> Dict[str, Any]:
    """Xác thực người dùng và cấp JWT Bearer Token.

    Giới hạn dò mật khẩu (prompt §78): sai quá `LOGIN_MAX_FAILURES` lần trong
    `LOGIN_WINDOW_S` theo IP HOẶC theo tài khoản -> 429 trong `LOGIN_LOCK_S`. Mọi lần
    đăng nhập (đúng / sai / bị khoá) vào audit (§68). bcrypt chạy ngoài event loop."""
    ip = request.client.host if request.client else "?"
    keys = (f"ip:{ip}", f"user:{payload.username.strip().lower()}")
    wait = _login_locked(keys)
    if wait:
        _login_audit(payload.username, ip, "LOGIN_LOCKED")
        raise HTTPException(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                            detail=f"Đăng nhập sai quá nhiều lần. Thử lại sau {int(wait) + 1} giây.")
    user = await run_blocking(auth_manager.authenticate_user, username=payload.username, password=payload.password)
    if not user:
        _login_failed(keys)
        _login_audit(payload.username, ip, "LOGIN_FAILED")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tên đăng nhập hoặc mật khẩu không chính xác.",
        )
    _store().events_clear("login:" + keys[1])
    _login_audit(user["username"], ip, "LOGIN_OK")

    access_token = auth_manager.create_access_token(
        data={"sub": user["username"], "role": user.get("role", "viewer")},
        expires_delta=timedelta(minutes=60 * 24),
    )
    logger.info("Người dùng '%s' (role: %s) đã đăng nhập thành công.", user["username"], user.get("role"))
    return {
        "status": "success",
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "full_name": user.get("full_name", user["username"]),
            "role": user.get("role", "viewer"),
        },
    }


@router.get(
    "/api/v1/auth/me",
    summary="Lấy thông tin tài khoản người dùng hiện tại",
    tags=["Authentication"],
)
async def get_me_endpoint(current_user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Trả về thông tin và quyền hạn của người dùng đang đăng nhập."""
    return {
        "status": "success",
        "user": current_user,
    }
