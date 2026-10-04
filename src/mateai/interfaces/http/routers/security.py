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


class DeviceRoleRequest(BaseModel):
    #: "admin" | "it_support" | "operator" | "viewer"; null = bỏ, về quy tắc mặc định theo id.
    role: Optional[str] = None


def _audit(user: dict, action: str, details: Dict[str, Any]) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(str(user.get("username", "")), action, "SAFE", "SUCCESS", details)
    except Exception:  # pylint: disable=broad-except
        pass


@router.put("/api/v1/security/devices/{device_id}/role", summary="Đặt quyền cho một thiết bị", tags=["Security"])
async def set_device_role_endpoint(
    device_id: str,
    payload: DeviceRoleRequest,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """Quyền chỉ áp dụng khi thiết bị kết nối bằng token RIÊNG của nó (danh tính
    "device:<id>" do máy chủ gán) — token dùng chung không mang quyền này.
    Tác vụ rủi ro cao vẫn cần duyệt; duyệt một lần thì thiết bị được nhớ."""
    from mateai.application.security.security_guard import DEVICE_ROLES
    from mateai.infrastructure.database.db_manager import db_manager
    role = (payload.role or "").strip().lower() or None
    if role is not None and role not in DEVICE_ROLES:
        raise HTTPException(status_code=422, detail=f"Quyền phải là một trong: {', '.join(DEVICE_ROLES)}.")
    if not await run_blocking(db_manager.set_device_role, device_id=device_id, role=role):
        raise HTTPException(status_code=404, detail=f"Thiết bị '{device_id}' chưa có token riêng — cấp token trước.")
    _audit(user, "set_device_role", {"device_id": device_id, "role": role})
    return {"status": "success", "device_id": device_id, "role": role}


@router.get("/api/v1/security/devices/{device_id}/approvals",
            summary="Tác vụ thiết bị đã được duyệt (không hỏi lại)", tags=["Security"])
async def list_device_approvals_endpoint(device_id: str, user: dict = Depends(require_roles(["admin"]))) -> Dict[str, Any]:
    from mateai.application.security.security_guard import DEVICE_PRINCIPAL_PREFIX
    from mateai.infrastructure.database.db_manager import db_manager
    grants = await run_blocking(db_manager.list_approval_grants, principal=f"{DEVICE_PRINCIPAL_PREFIX}{device_id}")
    return {"status": "success", "device_id": device_id, "approvals": grants}


@router.delete("/api/v1/security/devices/{device_id}/approvals",
               summary="Thu hồi phê duyệt đã nhớ của thiết bị", tags=["Security"])
async def revoke_device_approvals_endpoint(
    device_id: str,
    tool_name: Optional[str] = None,
    user: dict = Depends(require_roles(["admin"])),
) -> Dict[str, Any]:
    """tool_name bỏ trống = thu hồi TẤT CẢ (lần sau mọi tác vụ rủi ro cao lại hỏi duyệt)."""
    from mateai.application.security.security_guard import DEVICE_PRINCIPAL_PREFIX
    from mateai.infrastructure.database.db_manager import db_manager
    n = await run_blocking(db_manager.revoke_approval_grant,
                           principal=f"{DEVICE_PRINCIPAL_PREFIX}{device_id}", tool_name=tool_name)
    _audit(user, "revoke_device_approvals", {"device_id": device_id, "tool_name": tool_name, "removed": n})
    return {"status": "success", "device_id": device_id, "removed": n}


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
        "total": len(logs),
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
    """Tác vụ đang chờ duyệt (kèm tham số), từ hàng đợi HITL duy nhất. Chỉ admin —
    người duy nhất duyệt được, và tham số có thể chứa nội dung nhạy cảm (vd nội
    dung tệp sắp ghi). `action` = yêu cầu mới nhất của `user_id`, nếu không có
    thì yêu cầu mới nhất."""
    from mateai.application.agent.tool_gate import pending_view
    from mateai.application.security.zero_trust import hitl_manager

    all_pending = [pending_view(it) for it in hitl_manager.get_pending_list()]
    own = [p for p in all_pending if user_id in (p.get("requested_by"), p.get("source_device"))]
    action = (own or all_pending or [None])[-1]
    return {
        "has_pending": bool(action),
        "count": len(all_pending),
        "action": action,
        "pending_list": list(reversed(all_pending)),
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
    Phase 25: Duyệt / huỷ một tác vụ đang nằm trong hàng đợi HITL duy nhất.

    Chỉ admin. Chỉ thực thi ĐÚNG tác vụ đang chờ (tên tool, tham số, máy đích
    lấy từ hàng đợi) — `skill_name` / `args` / `client_id` trong body chỉ
    để đối chiếu. Trước đây mọi người đã đăng nhập (kể cả viewer) gửi
    `{approved: true, skill_name, args}` là chạy thẳng skill bất kỳ trên máy
    chủ, không qua Zero-Trust/RBAC. Nay tác vụ đã duyệt chạy qua cổng tool
    chung với `approved=True`; RBAC áp theo người YÊU CẦU tác vụ.
    """
    from mateai.application.security.safety_guard import security_engine
    from mateai.application.agent.state_manager import state_manager
    from mateai.application.agent.tool_gate import pending_view
    from mateai.application.security.zero_trust import hitl_manager

    approver = str(current_user.get("username") or "admin")
    # Có action_id → đúng yêu cầu đó (đã xử lý / hết hạn → 404, KHÔNG duyệt nhầm
    # sang yêu cầu khác). Không có → yêu cầu mới nhất (modal cũ của portal).
    if payload.action_id:
        item = hitl_manager.get_pending(payload.action_id)
    elif not payload.skill_name:
        _all = hitl_manager.get_pending_list()
        item = _all[-1] if _all else None
    else:
        _match = [it for it in hitl_manager.get_pending_list() if it.get("action_name") == payload.skill_name]
        item = _match[-1] if _match else None

    if not item:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy tác vụ đang chờ phê duyệt (có thể đã được xử lý hoặc hết hạn).",
        )
    pending = pending_view(item)
    lookup_key = pending["id"]
    skill_name = pending["tool_name"] or ""
    client_id = pending["target_client"]
    if payload.skill_name and payload.skill_name != skill_name:
        raise HTTPException(
            status_code=409,
            detail=f"Tác vụ đang chờ là '{skill_name}', không phải '{payload.skill_name}'.",
        )

    if not payload.approved:
        rej = hitl_manager.reject(lookup_key, rejected_by=approver, reason="Từ chối trên Cổng Web")
        if rej.get("status") != "success":
            raise HTTPException(status_code=409, detail=rej.get("message"))
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

    # Duyệt qua hàng đợi chung: executor chạy tool qua cổng với approved=True,
    # RBAC theo người YÊU CẦU; audit HITL_APPROVED_* ghi người duyệt.
    logger.info("[Phase 25] Admin '%s' phê duyệt tác vụ '%s' trên '%s'.", approver, skill_name, client_id)
    approval = await hitl_manager.approve_async(lookup_key, approved_by=approver)
    if approval.get("status") == "error":
        raise HTTPException(status_code=409, detail=approval.get("message"))
    res = approval.get("execution_result")
    if res is None:
        res = {"status": "error", "error": approval.get("execution_error") or approval.get("message")}
    orig_q = pending.get("query") or f"Thực thi {skill_name}"

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

    state_manager.record_completed_action(pending, res, synth_reply)

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
