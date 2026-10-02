"""
mateai/application/agent/tool_gate.py
=====================================
Cổng DUY NHẤT thực thi tool do LLM yêu cầu: `run_tool_with_policy`.

Tác vụ cần duyệt nằm trong hàng đợi HITL duy nhất (`zero_trust.hitl_manager`,
`kind="tool"`); `execute_approved_tool` là executor chạy lại tool qua cổng này
với `approved=True` sau khi người có quyền duyệt — ở bất kỳ kênh nào.

Dùng bởi vòng agent `LLMEngine.ask_async` — vòng mà mọi kênh voice đi qua khi
câu hỏi cần tool (core/voice_turn.py). Phase 3 đã gỡ vòng tool 1-bước riêng của
portal (execute_tool_call, can_synthesize_direct_response, prune_tool_schemas,
prune_tool_payload_for_llm): chọn tool là việc của core/dynamic_skill_router.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from mateai.application.security.safety_guard import security_engine
from mateai.application.security.zero_trust import hitl_manager

logger = logging.getLogger(__name__)

#: Loại yêu cầu HITL do cổng tool tạo — chạy lại bằng `execute_approved_tool`.
TOOL_KIND = "tool"


async def run_tool_with_policy(
    fn_name: str,
    fn_args: Dict[str, Any],
    *,
    caller: str,
    source_device: Optional[str],
    query: str = "",
    session_id: Optional[str] = None,
    registry_names: Optional[Set[str]] = None,
    approved: bool = False,
    client_timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Cổng DUY NHẤT thực thi một tool do LLM yêu cầu (mọi kênh voice + agent).

    Zero-Trust (BLOCKED / NEED_CONFIRM -> HITL) -> RBAC -> thực thi (Plugin
    Registry có HITL riêng / skill cục bộ / máy trạm) -> audit.

    `caller`: danh tính cho RBAC + audit. `source_device`: kênh gọi, dùng cho
    chính sách bỏ qua bước xác nhận của các kênh quản trị (commit f389bbe —
    "full admin bypass", giữ nguyên) và nơi gửi yêu cầu phê duyệt.

    Trước Phase 3 logic này nằm trong `LLMEngine.ask_async`; đường voice
    realtime dùng một bản riêng import `zero_trust.evaluate_risk` (không tồn
    tại) nên mọi tool chạy KHÔNG qua kiểm tra nào.

    `approved`: True CHỈ khi người có quyền đã duyệt tác vụ này (đường resume
    sau "Đồng ý"). Cờ `confirmed` trong `fn_args` bị bỏ đi và không có hiệu
    lực: tham số tool do LLM hoặc client gửi lên, nên ai cũng tự đặt được —
    trước đây `{"confirmed": true}` trong tham số là đủ để bỏ qua HITL.

    `client_timeout`: thời gian chờ máy trạm (giây) khi tool chạy trên máy trạm;
    bỏ trống = mặc định của orchestrator.

    Trả về {"target_client", "args", "result"}.
    """
    from mateai.application.security.zero_trust import evaluate_action_risk
    from core.plugin_manager import plugin_manager

    fn_args = dict(fn_args or {})
    fn_args.pop("confirmed", None)
    target_client = str(
        fn_args.pop("target_client_id", None)
        or fn_args.pop("target_client", "master")
        or "master"
    ).strip()

    risk_level = evaluate_action_risk(fn_name, fn_args)
    _device = str(source_device or "anonymous")
    _is_admin = (
        any(k in _device.lower() for k in ["esp32", "xiaozhi", "telegram", "hud", "console", "portal", "admin"])
        or approved
    )

    def _done(result: Dict[str, Any]) -> Dict[str, Any]:
        return {"target_client": target_client, "args": fn_args, "result": result}

    if risk_level == "BLOCKED":
        logger.warning("Zero-Trust Security: Tác vụ '%s' bị CHẶN HOÀN TOÀN.", fn_name)
        security_engine.log_audit(target_client, fn_name, "BLOCKED", "REJECTED", fn_args)
        return _done({
            "status": "error",
            "message": f"Tác vụ '{fn_name}' bị từ chối do vi phạm chính sách bảo mật.",
        })

    if risk_level == "NEED_CONFIRM" and not _is_admin:
        logger.warning("Zero-Trust Security: Tác vụ '%s' yêu cầu phê duyệt.", fn_name)
        _chat_id = None
        if source_device and "telegram:" in str(source_device):
            _parts = str(source_device).split(":")
            if len(_parts) >= 2:
                _chat_id = _parts[1]
        # Yêu cầu tự ghi audit (HITL_APPROVAL_REQUEST_*, status pending) và báo
        # Telegram cho người duyệt — không ghi thêm dòng PENDING riêng ở đây.
        _req = hitl_manager.request_approval(
            action_name=fn_name,
            params=dict(fn_args),
            requested_by=caller,
            description=query or f"Tác vụ '{fn_name}' trên [{target_client}]",
            kind=TOOL_KIND,
            context={
                "target_client": target_client,
                "query": query,
                "chat_id": _chat_id,
                "source_device": source_device,
                "session_id": session_id,
            },
        )
        return _done({
            "status": "need_confirm",
            "approval_id": _req.get("id"),
            "message": f"Tác vụ '{fn_name}' yêu cầu phê duyệt. Nhắn 'Đồng ý' để em chạy tiếp.",
            "skill": fn_name, "target_client": target_client,
            "args": fn_args, "requires_confirmation": True,
        })

    try:
        from mateai.application.security.security_guard import security_guard as _rbac_guard
    except Exception:  # pragma: no cover
        _rbac_guard = None
    if _rbac_guard is not None:
        _rbac_ok, _rbac_reason = _rbac_guard.check_permission(
            tool_name=fn_name, employee_id=caller,
            session_id=session_id, payload=fn_args,
        )
        if not _rbac_ok:
            logger.warning("[RBAC] BLOCKED | tool=%s | caller=%s", fn_name, caller)
            return _done({"status": "error", "error": _rbac_reason, "code": "RBAC_DENIED"})

    if target_client.lower() in ("master", "local", "server", "chính", "cục bộ"):
        _plugin_registry = None
        if registry_names is None:
            try:
                from mateai.application.skills.plugin_registry import plugin_registry as _plugin_registry
                registry_names = set(_plugin_registry.get_tool_names())
            except Exception:
                registry_names = set()
        if registry_names and fn_name in registry_names:
            if _plugin_registry is None:
                from mateai.application.skills.plugin_registry import plugin_registry as _plugin_registry
            logger.info("[Phase60] Thực thi tool Plugin Registry: '%s'", fn_name)
            _result = await _plugin_registry.execute_tool(fn_name, fn_args, caller_id=caller)
            if _result.get("awaiting_approval"):
                _result = {
                    "status": "awaiting_approval", "success": False,
                    "approval_id": _result.get("approval_id"),
                    "risk_level": _result.get("risk_level"),
                    "message": "Tác vụ này đang chờ phê duyệt. CHƯA được thực thi.",
                }
        else:
            # Chỉ ghi TÊN tham số: giá trị có thể là nội dung tệp sắp ghi, câu lệnh,
            # dữ liệu nhân sự… và `/api/v1/logs/recent` phục vụ log cho cả viewer.
            # Giá trị đầy đủ nằm trong audit_logs (chỉ admin đọc).
            logger.info("Thực thi kỹ năng cục bộ: '%s' tham số=%s", fn_name, sorted(fn_args))
            _result = await plugin_manager.execute_skill(fn_name, fn_args)
    else:
        logger.info("Diều phối kỹ năng '%s' → [%s]", fn_name, target_client)
        from mateai.interfaces.websocket.client_orchestrator import orchestrator
        _sync_kw = {"timeout": client_timeout} if client_timeout else {}
        _result = await asyncio.to_thread(
            orchestrator.execute_on_client_sync, target_client, fn_name, fn_args, **_sync_kw)

    _ok = _result.get("status") == "success" or _result.get("success") is True
    security_engine.log_audit(target_client, fn_name, risk_level, "SUCCESS" if _ok else "FAILED", fn_args)
    return _done(_result)


async def execute_approved_tool(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executor của hàng đợi HITL cho `kind="tool"`: chạy lại đúng tool + tham số
    + máy đích đã lưu, qua cổng này với `approved=True`. RBAC áp theo người
    YÊU CẦU (không phải người duyệt) — duyệt không mở rộng quyền của người hỏi.
    """
    ctx = item.get("context") or {}
    gate = await run_tool_with_policy(
        str(item.get("action_name") or ""),
        {**dict(item.get("params") or {}), "target_client": ctx.get("target_client") or "master"},
        caller=str(item.get("requested_by") or "anonymous"),
        source_device=ctx.get("source_device"),
        query=str(ctx.get("query") or ""),
        session_id=ctx.get("session_id"),
        approved=True,
    )
    return gate["result"]


def pending_view(item: Dict[str, Any]) -> Dict[str, Any]:
    """Dạng hiển thị của một yêu cầu đang chờ (portal, HUD, hội thoại)."""
    ctx = item.get("context") or {}
    return {
        "id": item.get("id"),
        "tool_name": item.get("action_name"),
        "arguments": item.get("params") or {},
        "target_client": ctx.get("target_client") or "master",
        "query": ctx.get("query") or "",
        "description": item.get("description") or "",
        "requested_by": item.get("requested_by"),
        "risk_level": item.get("risk_level"),
        "kind": item.get("kind"),
        "chat_id": ctx.get("chat_id"),
        "source_device": ctx.get("source_device"),
        "created_at": item.get("created_at"),
    }


hitl_manager.register_executor(TOOL_KIND, execute_approved_tool)
