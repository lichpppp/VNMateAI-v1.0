"""
mateai/interfaces/http/routers/security.py
==========================================
Bảo mật: blacklist Zero-Trust, sandbox kiểm tra lệnh, token thiết bị, audit log,
hàng đợi tác vụ chờ duyệt và phê duyệt (HITL của hội thoại / portal).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from core.plugin_manager import run_blocking
from mateai.interfaces.http import enrollment, speech
from mateai.interfaces.http.auth_dependencies import get_current_user, require_roles
from mateai.interfaces.websocket.realtime_hub import broadcast_hud, broadcast_portal_ui

logger = logging.getLogger(__name__)

router = APIRouter()


class BlacklistUpdateRequest(BaseModel):
    """Payload for POST /api/v1/security/blacklist."""
    action: str = Field(..., description="'add' hoặc 'remove'")
    keyword: str = Field(..., min_length=1, description="Từ khóa hoặc hành động hoặc đường dẫn")
    category: Optional[str] = Field(default="blacklist", description="'blacklist', 'confirm_actions', hoặc 'protected_dirs'")


class SecurityInspectRequest(BaseModel):
    """Payload for POST /api/v1/security/inspect."""
    type: str = Field(default="code", description="'code', 'action', hoặc 'text'")
    content: str = Field(..., min_length=1, description="Nội dung mã nguồn, lệnh hoặc prompt")
    params: Optional[Dict[str, Any]] = Field(default=None, description="Tham số đi kèm nếu là action")


class ConfirmActionRequest(BaseModel):
    """Payload for POST /api/v1/security/confirm-action."""
    approved: bool = Field(..., description="Phê duyệt (true) hoặc Hủy bỏ (false)")
    # Optional — can be auto-resolved from StateManager if not provided
    action_id: Optional[str] = Field(default=None, description="Mã định danh tác vụ pending (tùy chọn)")
    client_id: Optional[str] = Field(default=None, description="ID máy trạm thực thi hoặc 'master' (auto-resolved nếu bỏ trống)")
    skill_name: Optional[str] = Field(default=None, description="Tên kỹ năng (auto-resolved từ StateManager nếu bỏ trống)")
    args: Optional[Dict[str, Any]] = Field(default=None, description="Tham số kỹ năng (auto-resolved nếu bỏ trống)")
    user_id: Optional[str] = Field(default=None, description="User ID để tra StateManager (mặc định: 'admin')")


@router.get(
    "/api/v1/security/blacklist",
    summary="Get current blacklist keywords",
    tags=["Security"],
)
async def get_blacklist(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    """Retrieve forbidden keywords from security config."""
    from mateai.config.loader import settings
    return {
        "status": "success",
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@router.post(
    "/api/v1/security/blacklist",
    summary="Add or remove keywords from blacklist, confirm actions, or protected dirs",
    tags=["Security"],
)
async def update_blacklist(
    payload: BlacklistUpdateRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Add or remove an item from the active security policy (blacklist, confirm_actions, protected_dirs)."""
    from mateai.config.loader import settings, read_raw_config, reload_settings, write_raw_config
    from mateai.application.security.safety_guard import security_engine

    actor = str(user.get("username") or "admin")

    category = payload.category or "blacklist"
    kw = payload.keyword.strip()

    try:
        raw_cfg = read_raw_config(strict=True)
        if "security" not in raw_cfg:
            raw_cfg["security"] = {}

        if category == "confirm_actions":
            current_list = raw_cfg["security"].setdefault(
                "require_confirmation_actions",
                list(getattr(settings.security, "require_confirmation_actions", []))
            )
        elif category == "protected_dirs":
            current_list = raw_cfg["security"].setdefault(
                "protected_directories",
                list(getattr(settings.security, "protected_directories", []))
            )
        else:
            current_list = raw_cfg["security"].setdefault(
                "forbidden_keywords",
                list(getattr(settings.security, "forbidden_keywords", []))
            )

        if payload.action == "add":
            if kw and kw not in current_list:
                current_list.append(kw)
                security_engine.log_audit(actor, f"update_{category}", "SAFE", "SUCCESS", {"action": "add", "item": kw, "category": category})
        elif payload.action == "remove":
            if kw in current_list:
                current_list.remove(kw)
                security_engine.log_audit(actor, f"update_{category}", "SAFE", "SUCCESS", {"action": "remove", "item": kw, "category": category})

        write_raw_config(raw_cfg)
        reload_settings()
    except Exception as exc:
        logger.error("Lỗi khi lưu cấu hình bảo mật vào config.json: %s", exc)
        raise HTTPException(status_code=500, detail=f"Lỗi lưu cấu hình: {exc}")

    return {
        "status": "success",
        "action": payload.action,
        "category": category,
        "keyword": kw,
        "forbidden_keywords": getattr(settings.security, "forbidden_keywords", []),
        "require_confirmation_actions": getattr(settings.security, "require_confirmation_actions", []),
        "protected_directories": getattr(settings.security, "protected_directories", []),
    }


@router.post(
    "/api/v1/security/inspect",
    summary="Interactive Zero-Trust AST & Security Sandbox Inspector",
    tags=["Security"],
)
async def inspect_security_sandbox(
    payload: SecurityInspectRequest,
    user: dict = Depends(require_roles(["manager", "admin"])),
) -> Dict[str, Any]:
    """
    Test and analyze Python code, shell commands, or user queries against Zero-Trust AST & Blacklist rules.
    """
    from mateai.application.security.safety_guard import security_engine
    from mateai.config.loader import settings

    inspect_type = (payload.type or "code").lower()
    content = payload.content.strip()

    # 1. Masking evaluation
    masked = security_engine.mask_sensitive_data(content)
    has_sensitive = (masked != content)

    # 2. Risk & Violations evaluation
    violations: List[str] = []
    if inspect_type == "code":
        is_safe, message = security_engine.inspect_generated_code(content)
        risk = "SAFE" if is_safe else "BLOCKED"
        if not is_safe:
            violations.append(message)
    elif inspect_type == "action":
        risk = security_engine.evaluate_action_risk(content, payload.params or {})
        is_safe = (risk == "SAFE")
        if risk == "BLOCKED":
            message = "Tác vụ chứa từ khóa cấm hoặc hành vi bị từ chối tức thì."
            violations.append(message)
        elif risk == "NEED_CONFIRM":
            message = "Tác vụ thuộc danh mục nhạy cảm, yêu cầu Người quản trị bấm duyệt (HITL)."
            violations.append(message)
        else:
            message = "Tác vụ an toàn, được phép thực thi tự động (Auto-Execute)."
    else:  # Text / Prompt
        forbidden = getattr(settings.security, "forbidden_keywords", [])
        for kw in forbidden:
            if kw.lower() in content.lower():
                violations.append(f"Chứa từ khóa cấm trong Blacklist: '{kw}'")
        if violations:
            risk = "BLOCKED"
            is_safe = False
            message = "Phát hiện nội dung vi phạm chính sách bảo mật Blacklist."
        else:
            risk = "SAFE"
            is_safe = True
            message = "Văn bản an toàn theo tiêu chuẩn Zero-Trust."

    return {
        "status": "success",
        "type": inspect_type,
        "risk": risk,
        "is_safe": is_safe,
        "message": message,
        "violations": violations,
        "has_sensitive_data": has_sensitive,
        "masked_content": masked,
    }


_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


class DeviceTokenRequest(BaseModel):
    device_id: str


@router.post("/api/v1/security/devices", summary="Cấp / xoay token riêng cho một thiết bị IoT", tags=["Security"])
async def issue_device_token_endpoint(
    payload: DeviceTokenRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Token gốc chỉ trả về MỘT lần (máy chủ chỉ lưu hash). Cấp lại = token cũ hết hiệu lực."""
    device_id = payload.device_id.strip()
    if not _DEVICE_ID_RE.match(device_id):
        raise HTTPException(status_code=422, detail="device_id chỉ gồm chữ, số, '_', '-', '.', tối đa 64 ký tự.")
    from mateai.infrastructure.database.db_manager import db_manager
    token = await run_blocking(db_manager.issue_device_token, device_id=device_id, created_by=str(user.get("username", "")))
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), "issue_device_token", "SAFE", "SUCCESS",
                                  {"device_id": device_id})
    except Exception:  # pylint: disable=broad-except
        pass
    return {
        "status": "success",
        "device_id": device_id,
        "device_token": token,
        "ws_path": f"/api/v1/xiaozhi/ws/{device_id}",
        "instructions": (
            "Dán token vào DEFAULT_DEVICE_TOKEN và đặt DEFAULT_DEVICE_ID = device_id trong "
            "esp32_firmware/src/secrets.h, build và nạp lại. Token chỉ hiện một lần."
        ),
    }


@router.get("/api/v1/security/devices", summary="Danh sách thiết bị có token riêng", tags=["Security"])
async def list_device_tokens_endpoint(user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.infrastructure.database.db_manager import db_manager
    from mateai.config.loader import get_config_section
    return {
        "status": "success",
        "devices": await run_blocking(db_manager.list_device_tokens),
        "require_per_device_token": bool(get_config_section("security").get("require_per_device_token", False)),
    }


@router.delete("/api/v1/security/devices/{device_id}", summary="Thu hồi token của một thiết bị", tags=["Security"])
async def revoke_device_token_endpoint(device_id: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.infrastructure.database.db_manager import db_manager
    if not await run_blocking(db_manager.revoke_device_token, device_id=device_id):
        raise HTTPException(status_code=404, detail=f"Không có token cho thiết bị '{device_id}'.")
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), "revoke_device_token", "SAFE", "SUCCESS",
                                  {"device_id": device_id})
    except Exception:  # pylint: disable=broad-except
        pass
    return {"status": "success", "device_id": device_id}


@router.get(
    "/api/v1/security/device-enrollment-token",
    summary="Lấy device enrollment token để flash firmware ESP32",
    tags=["Security"],
)
async def get_device_enrollment_token(
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Trả về token cần dán vào DEFAULT_DEVICE_TOKEN trong secrets.h của firmware.

    Token này cho phép thiết bị stream âm thanh vào master. Chỉ admin được xem.
    """
    return {
        "status": "success",
        "device_enrollment_token": enrollment.get_device_enrollment_secret(),
        "instructions": (
            "Dán giá trị trên vào DEFAULT_DEVICE_TOKEN trong "
            "esp32_firmware/src/secrets.h, build và flash lại thiết bị."
        ),
    }


@router.get(
    "/api/v1/security/audit-logs",
    summary="Get recent security audit logs",
    tags=["Security"],
)
async def get_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Fetch structured security audit logs."""
    from mateai.application.security.safety_guard import security_engine
    logs = security_engine.get_recent_audit_logs(limit=limit)
    return {
        "status": "success",
        "count": len(logs),
        "logs": logs,
    }


@router.get(
    "/api/v1/security/pending-action",
    summary="Phase 25: Query current pending action waiting for admin approval",
    tags=["Security"],
)
async def get_pending_action_endpoint(
    user_id: str = Query(default="admin", description="User/session ID để tra StateManager"),
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Tác vụ đang chờ duyệt (kèm tham số). Chỉ admin — người duy nhất duyệt được,
    và tham số có thể chứa nội dung nhạy cảm (vd nội dung tệp sắp ghi)."""
    from mateai.application.agent.state_manager import state_manager
    all_pending = state_manager.list_pending_actions()
    action = state_manager.get_pending_action(user_id)
    if not action and all_pending:
        action = all_pending[0]

    return {
        "has_pending": bool(action),
        "count": len(all_pending),
        "action": {
            "id": action.get("id"),
            "tool_name": action.get("tool_name"),
            "target_client": action.get("target_client"),
            "arguments": action.get("arguments", {}),
            "description": action.get("description", ""),
            "query": action.get("query", ""),
            "timestamp": action.get("timestamp"),
        } if action else None,
        "pending_list": [
            {
                "id": p.get("id"),
                "tool_name": p.get("tool_name"),
                "target_client": p.get("target_client"),
                "arguments": p.get("arguments", {}),
                "description": p.get("description", ""),
                "query": p.get("query", ""),
                "timestamp": p.get("timestamp"),
            }
            for p in all_pending
        ],
    }


@router.post(
    "/api/v1/security/confirm-action",
    summary="Approve or reject a high-risk action",
    tags=["Security"],
)
async def confirm_action_endpoint(
    payload: ConfirmActionRequest,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """
    Phase 25: Duyệt / huỷ một tác vụ NEED_CONFIRM đang nằm trong hàng đợi.

    Chỉ admin. Chỉ thực thi ĐÚNG tác vụ đang chờ (tên tool, tham số, máy đích
    lấy từ StateManager) — `skill_name` / `args` / `client_id` trong body chỉ
    để đối chiếu. Trước đây mọi người đã đăng nhập (kể cả viewer) gửi
    `{approved: true, skill_name, args}` là chạy thẳng skill bất kỳ trên máy
    chủ, không qua Zero-Trust/RBAC. Nay tác vụ đã duyệt chạy qua cổng tool
    chung với `approved=True`; RBAC áp theo người YÊU CẦU tác vụ.
    """
    from mateai.application.security.safety_guard import security_engine
    from mateai.application.agent.state_manager import state_manager
    from mateai.application.agent.tool_gate import run_tool_with_policy

    # ── Phase 25: Auto-resolve from StateManager ─────────────────────────
    lookup_key = payload.action_id or payload.user_id or current_user.get("username", "admin")
    pending = state_manager.get_pending_action(lookup_key)
    # Chỉ lấy "tác vụ đang chờ đầu tiên" khi request KHÔNG chỉ định action_id.
    # Có action_id mà không thấy (đã xử lý / hết hạn) thì không được duyệt nhầm
    # sang một tác vụ khác trong hàng đợi.
    if not pending and not payload.action_id and not payload.skill_name:
        all_pending = state_manager.list_pending_actions()
        if all_pending:
            pending = all_pending[0]
            lookup_key = pending.get("id") or "admin"

    if not pending:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tác vụ đang chờ phê duyệt (có thể đã được xử lý hoặc hết hạn).",
        )
    skill_name = pending.get("tool_name") or ""
    raw_args = dict(pending.get("arguments") or {})
    client_id = pending.get("target_client") or "master"
    if payload.skill_name and payload.skill_name != skill_name:
        raise HTTPException(
            status_code=409,
            detail=f"Tác vụ đang chờ là '{skill_name}', không phải '{payload.skill_name}'.",
        )

    if not payload.approved:
        # User rejected — clear from queue
        state_manager.cancel_pending_action(lookup_key)
        security_engine.log_audit(client_id, skill_name, "NEED_CONFIRM", "USER_REJECTED", raw_args)
        rej_msg = f"Tác vụ '{skill_name}' đã bị người quản trị hủy bỏ."

        # Broadcast rejection to HUD
        try:
            await broadcast_hud({
                "type": "security_approval_resolved",
                "action_id": lookup_key,
                "status": "rejected",
                "skill": skill_name,
                "message": rej_msg,
                "timestamp": datetime.utcnow().isoformat(),
            })
            await broadcast_hud({
                "type": "voice_active",
                "status": "idle",
                "text": rej_msg,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception:
            pass

        return {
            "status": "rejected",
            "message": rej_msg,
        }

    # Approved ── pop from queue then execute through the single tool gate
    state_manager.get_and_clear_pending_action(lookup_key)
    args = raw_args
    approver = str(current_user.get("username") or "admin")

    security_engine.log_audit(client_id, skill_name, "NEED_CONFIRM", "USER_APPROVED", {**args, "approved_by": approver})
    logger.info("[Phase 25] Admin '%s' phê duyệt tác vụ '%s' trên '%s'.", approver, skill_name, client_id)

    orig_q = pending.get("query") or f"Thực thi {skill_name}"
    _gate = await run_tool_with_policy(
        skill_name,
        {**args, "target_client": client_id},
        caller=str(pending.get("user_id") or approver),
        source_device=pending.get("source_device") or "http:approval",
        query=orig_q,
        approved=True,
    )
    res = _gate["result"]

    # ── Phase 25: Synthesize natural AI response & record completed action ────
    masked_res = security_engine.mask_sensitive_data(json.dumps(res, ensure_ascii=False, default=str))
    synth_reply = ""
    try:
        from mateai.application.agent.llm_engine import llm_engine
        synth_messages = [
            {"role": "system", "content": "Bạn là trợ lý AI Ly Ly (VN-MateAI). Hãy tổng hợp kết quả công cụ để trả lời súc tích, tự nhiên, kính cẩn bằng tiếng Việt cho người dùng."},
            {"role": "user", "content": orig_q},
            {"role": "user", "content": f"Tác vụ đã được phê duyệt qua Web Portal. Kết quả công cụ `{skill_name}`:\n```json\n{masked_res}\n```\nHãy thông báo kết quả thực thi một cách rõ ràng."},
        ]
        synth_resp = await llm_engine._call_llm(messages=synth_messages, tools=None)
        synth_reply = synth_resp.choices[0].message.content or f"Dạ, tác vụ '{skill_name}' đã được phê duyệt và hoàn tất thành công."
    except Exception as e:
        logger.warning("[Phase 25] Lỗi synthesize câu trả lời sau duyệt: %s", e)
        synth_reply = f"Dạ, tác vụ '{skill_name}' đã được phê duyệt và thực thi thành công."

    state_manager.record_completed_action(dict(pending), res, synth_reply)

    # ── Phase 25: Nếu tác vụ xuất phát từ Telegram, gửi thông báo về Telegram ──
    tg_chat_id = pending.get("chat_id")
    if not tg_chat_id:
        src = pending.get("source_device", "")
        if "telegram:" in src:
            parts = src.split(":")
            if len(parts) >= 2 and parts[1].isdigit():
                tg_chat_id = parts[1]

    if tg_chat_id:
        try:
            from mateai.interfaces.telegram.telegram_gateway import telegram_gateway
            telegram_gateway.send_incident_alert(f"✅ [ĐÃ PHÊ DUYỆT]\n\n{synth_reply}", target=tg_chat_id)
        except Exception as exc:
            logger.warning("[Phase 25] Lỗi gửi thông báo Telegram sau duyệt: %s", exc)

    # ── Phase 25: Phát sóng thời gian thực tới Web Portal qua WebSocket ────────
    try:
        await broadcast_portal_ui("action_approved_result", {
            "action_id": lookup_key,
            "skill": skill_name,
            "client_id": client_id,
            "query": orig_q,
            "reply": synth_reply,
            "result": res,
        })
    except Exception as exc:
        logger.warning("[Phase 25] Lỗi broadcast WebSocket sau duyệt: %s", exc)

    # ── Phase 34.8: Phát sóng tức thời tới Standby HUD & Tổng hợp giọng nói Hoài My ──────
    speech_reply = llm_engine._make_concise_speech_text(synth_reply)

    try:
        await broadcast_hud({
            "type": "security_approval_resolved",
            "action_id": lookup_key,
            "status": "approved",
            "skill": skill_name,
            "query": orig_q,
            "reply": synth_reply,
            "speech_reply": speech_reply,
            "message": f"Tác vụ '{skill_name}' đã được phê duyệt qua Web Portal.",
            "timestamp": datetime.utcnow().isoformat(),
        })
    except Exception as hud_exc:
        logger.warning("[Phase 34.8] Lỗi broadcast HUD security_approval_resolved: %s", hud_exc)

    async def _async_synth_and_speak_hud(speech_text: str):
        try:
            import base64
            audio_bytes = await speech.tts_bytes(speech_text)
            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8") if audio_bytes else None
            await broadcast_hud({
                "type": "voice_active",
                "status": "speaking",
                "text": speech_text,
                "audio_base64": audio_b64,
                "source_device": "security_approval",
                "timestamp": datetime.utcnow().isoformat(),
            })

            est_dur = max(4.0, (len(speech_text) / 15.0) + 1.8)
            await asyncio.sleep(est_dur)
            await broadcast_hud({
                "type": "voice_active",
                "status": "idle",
                "text": "Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...",
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.warning("[Phase 34.8] Lỗi async TTS/speak HUD sau duyệt: %s", exc)

    asyncio.create_task(_async_synth_and_speak_hud(speech_reply))

    return {
        "status": "success",
        "client_id": client_id,
        "skill": skill_name,
        "result": res,
        "reply": synth_reply,
    }


@router.get(
    "/api/v1/audit-logs",
    summary="Phase 48: Nhật ký kiểm toán bất biến",
    tags=["Audit"],
)
async def api_audit_logs(
    limit: int = 100,
    employee_id: Optional[str] = None,
    action_type: Optional[str] = None,
    status: Optional[str] = None,
    current_user: Dict[str, Any] = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Truy vấn nhật ký kiểm toán. Chỉ đọc — không thể sửa/xoá. Chỉ admin (cùng
    quy tắc với /api/v1/security/audit-logs): payload audit chứa tham số tác vụ,
    vd nội dung tệp ghi qua /api/v1/fs/write."""
    from mateai.infrastructure.database.erp_database import erp_db
    logs = erp_db.get_audit_logs(
        limit=limit,
        employee_id=employee_id,
        action_type=action_type,
        status=status,
    )
    return {"status": "success", "total": len(logs), "logs": logs}
