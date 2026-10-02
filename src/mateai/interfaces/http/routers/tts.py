"""
mateai/interfaces/http/routers/tts.py
======================================
TTS một lần (MP3) và danh sách giọng Edge-TTS.
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
from mateai.application.voice.speech_text import sanitise_for_tts, shorten_for_speech
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class TTSRequest(BaseModel):
    """Payload for POST /api/v1/tts."""
    text: str = Field(..., min_length=1, max_length=2000)
    voice: Optional[str] = Field(default="vi-VN-HoaiMyNeural")
    rate: Optional[str] = Field(default=None)


@router.post(
    "/api/v1/tts",
    summary="Synthesise Vietnamese TTS audio (REST, full buffer)",
    tags=["Audio"],
    response_class=Response,
)
async def tts_endpoint(
    payload: TTSRequest,
    user: dict = Depends(require_roles(["viewer", "manager", "admin"])),
) -> Response:
    """
    Convert text to speech using edge-tts.
    Returns raw MP3 bytes (Content-Type: audio/mpeg).
    Assembles entire audio in-memory (io.BytesIO) — no disk I/O.
    """

    try:
        from mateai.infrastructure.tts.tts_stream_engine import get_tts_engine
        audio_bytes = await get_tts_engine().synthesise(
            shorten_for_speech(sanitise_for_tts(payload.text)), voice=payload.voice, rate=payload.rate
        )
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="TTS engine returned empty audio.")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("TTS endpoint error: %s", exc)
        raise HTTPException(status_code=500, detail=f"TTS error: {exc}")


_TTS_VOICES_CACHE: list = []


@router.get(
    "/api/v1/tts/voices",
    summary="Lấy danh sách toàn bộ giọng đọc Edge-TTS (322 giọng, 70+ ngôn ngữ)",
    tags=["Audio"],
)
async def tts_voices_endpoint(user: dict = Depends(require_roles(["viewer", "manager", "admin"]))) -> Dict[str, Any]:
    """
    Trả về danh sách đầy đủ các giọng đọc Edge-TTS từ Microsoft.
    Kết quả được cache trong RAM — chỉ gọi edge_tts.list_voices() một lần duy nhất.
    """
    global _TTS_VOICES_CACHE
    if not _TTS_VOICES_CACHE:
        try:
            import edge_tts as _edge_tts
            raw = await _edge_tts.list_voices()
            _TTS_VOICES_CACHE = [
                {
                    "short_name": v["ShortName"],
                    "friendly_name": v["FriendlyName"],
                    "locale": v["Locale"],
                    "gender": v.get("Gender", ""),
                }
                for v in raw
            ]
            logger.info("Đã tải %d giọng Edge-TTS vào cache.", len(_TTS_VOICES_CACHE))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("Lỗi tải danh sách giọng Edge-TTS: %s", exc)
            return {"success": False, "error": str(exc), "voices": []}

    return {"success": True, "total": len(_TTS_VOICES_CACHE), "voices": _TTS_VOICES_CACHE}
