"""
core/agent_voice_loop.py
========================
Cổng DUY NHẤT thực thi tool do LLM yêu cầu: `run_tool_with_policy`.

Dùng bởi vòng agent `LLMEngine.ask_async` — vòng mà mọi kênh voice đi qua khi
câu hỏi cần tool (core/voice_turn.py). Phase 3 đã gỡ vòng tool 1-bước riêng của
portal (execute_tool_call, can_synthesize_direct_response, prune_tool_schemas,
prune_tool_payload_for_llm): chọn tool là việc của core/dynamic_skill_router.py.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional, Set

from core.safety_guard import security_engine

logger = logging.getLogger(__name__)


async def run_tool_with_policy(
    fn_name: str,
    fn_args: Dict[str, Any],
    *,
    caller: str,
    source_device: Optional[str],
    query: str = "",
    session_id: Optional[str] = None,
    registry_names: Optional[Set[str]] = None,
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

    Trả về {"target_client", "args", "result"}.
    """
    from core.zero_trust import evaluate_action_risk
    from core.plugin_manager import plugin_manager

    fn_args = dict(fn_args or {})
    target_client = str(
        fn_args.pop("target_client_id", None)
        or fn_args.pop("target_client", "master")
        or "master"
    ).strip()

    risk_level = evaluate_action_risk(fn_name, fn_args)
    _device = str(source_device or "anonymous")
    _is_admin = (
        any(k in _device.lower() for k in ["esp32", "xiaozhi", "telegram", "hud", "console", "portal", "admin"])
        or fn_args.get("confirmed")
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
        security_engine.log_audit(target_client, fn_name, "NEED_CONFIRM", "PENDING_CONFIRMATION", fn_args)
        from core.state_manager import state_manager as _sm
        _chat_id = None
        if source_device and "telegram:" in str(source_device):
            _parts = str(source_device).split(":")
            if len(_parts) >= 2:
                _chat_id = _parts[1]
        _sm.save_pending_action(
            user_id=caller, tool_name=fn_name, arguments=dict(fn_args),
            target_client=target_client, query=query,
            chat_id=_chat_id, source_device=source_device,
        )
        return _done({
            "status": "need_confirm",
            "message": f"Tác vụ '{fn_name}' yêu cầu phê duyệt. Nhắn 'Đồng ý' để em chạy tiếp.",
            "skill": fn_name, "target_client": target_client,
            "args": fn_args, "requires_confirmation": True,
        })

    try:
        from core.security_guard import security_guard as _rbac_guard
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
                from core.plugin_registry import plugin_registry as _plugin_registry
                registry_names = set(_plugin_registry.get_tool_names())
            except Exception:
                registry_names = set()
        if registry_names and fn_name in registry_names:
            if _plugin_registry is None:
                from core.plugin_registry import plugin_registry as _plugin_registry
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
            logger.info("Thực thi kỹ năng cục bộ: '%s' tham số=%s", fn_name, fn_args)
            _result = await plugin_manager.execute_skill(fn_name, fn_args)
            _not_found = (
                (not _result.get("success", True) or _result.get("status") == "error")
                and ("not found" in str(_result.get("error", "")).lower()
                     or "không tìm thấy" in str(_result.get("error", "")).lower())
            )
            if _not_found:
                if fn_name in ("list_directory", "read_file", "write_file", "delete_item"):
                    from core.skills import file_system
                    _fs = getattr(file_system, fn_name, None)
                    if _fs:
                        _result = await asyncio.to_thread(_fs, **fn_args)
                elif fn_name == "delegate_to_specialist":
                    from core.skills import ai_delegation
                    _result = await ai_delegation.delegate_to_specialist_async(**fn_args)
                elif fn_name == "display_visual_data":
                    from skills.visual_skills import display_visual_data
                    _result = await asyncio.to_thread(display_visual_data, **fn_args)
                elif fn_name == "query_organization_data":
                    from core.database import erp_db
                    _result = {"status": "success", "data": erp_db.query_organization(fn_args.get("query", ""))}
    else:
        logger.info("Diều phối kỹ năng '%s' → [%s]", fn_name, target_client)
        from core.orchestrator import orchestrator
        _result = await asyncio.to_thread(orchestrator.execute_on_client_sync, target_client, fn_name, fn_args)

    _ok = _result.get("status") == "success" or _result.get("success") is True
    security_engine.log_audit(target_client, fn_name, risk_level, "SUCCESS" if _ok else "FAILED", fn_args)
    return _done(_result)
