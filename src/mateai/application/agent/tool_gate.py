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
import time
from typing import Any, Dict, Optional, Set

from mateai.application.security.safety_guard import security_engine
from mateai.application.security.zero_trust import hitl_manager

logger = logging.getLogger(__name__)

#: Loại yêu cầu HITL do cổng tool tạo — chạy lại bằng `execute_approved_tool`.
TOOL_KIND = "tool"


_LOCAL_TARGETS = ("master", "local", "server", "chính", "cục bộ")


def _topo(kind: str, **kw: Any) -> None:
    try:
        from mateai.application.operations.topology_events import publish
        publish(kind, **kw)
    except Exception:  # noqa: BLE001 — giám sát không được làm hỏng tác vụ
        pass


async def run_tool_with_policy(fn_name: str, fn_args: Optional[Dict[str, Any]] = None, **kw: Any) -> Dict[str, Any]:
    """Cổng tool (xem `_run_tool_with_policy`) + báo từng bước cho trang giám sát
    `/admin/topology`: bắt đầu, xong / lỗi / bị chặn / chờ duyệt, thời gian chạy."""
    target = str((fn_args or {}).get("target_client_id") or (fn_args or {}).get("target_client") or "master")
    # Chạy trên máy chủ -> ô "core" (trước: "tools" -> "tools", cạnh tự nối vào chính nó).
    where = "core" if target.strip().lower() in _LOCAL_TARGETS else f"worker:{target}"
    _topo("tool", stage="start", source="tools", target=where, status="running",
          detail=f"{fn_name} @ {target}")
    t0 = time.perf_counter()
    try:
        gate = await _run_tool_with_policy(fn_name, fn_args, **kw)
    except Exception as exc:
        _topo("tool", stage="end", source="tools", target=where, status="error",
              ms=(time.perf_counter() - t0) * 1000, detail=f"{fn_name}: {type(exc).__name__}")
        raise
    ms = (time.perf_counter() - t0) * 1000
    res = gate.get("result") if isinstance(gate, dict) else None
    res = res if isinstance(res, dict) else {}
    st = res.get("status")
    if st in ("need_confirm", "awaiting_approval"):
        _topo("approval", stage="request", source="tools", target="hitl", status="waiting", ms=ms,
              detail=f"{fn_name} chờ duyệt (người yêu cầu: {kw.get('caller') or '?'})")
    elif st == "success" or res.get("success") is True:
        _topo("tool", stage="end", source=where, target="tools", status="ok", ms=ms, detail=fn_name)
    else:
        why = str(res.get("code") or res.get("error") or res.get("message") or st or "lỗi")
        _topo("tool", stage="end", source=where, target="tools", status="error", ms=ms,
              detail=f"{fn_name}: {why}")
    return gate


async def _run_tool_with_policy(
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
    agent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Cổng DUY NHẤT thực thi một tool do LLM yêu cầu (mọi kênh voice + agent).

    Quyết định: `policy_engine.authorize()` (kill switch -> L5/từ khoá cấm -> RBAC
    -> rủi ro -> duyệt / uỷ quyền). DENY luôn thắng, kể cả admin, kể cả `approved`.
    Rủi ro >= 3 cần duyệt cho MỌI vai trò, trừ khi có uỷ quyền còn hạn cho đúng
    danh tính + tool (L4). Thay quy tắc cũ "admin bỏ qua duyệt" và f389bbe
    (prompt Supervisor thay thế hoàn toàn, 2026-10-05).

    `caller`: danh tính người/thiết bị cho RBAC + audit. `source_device`: kênh gọi
    — dùng để gửi yêu cầu duyệt về đúng nơi và suy ra tác nhân (`agent_id`).

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
    from mateai.application.security import policy_engine
    from core.plugin_manager import plugin_manager

    fn_args = dict(fn_args or {})
    fn_args.pop("confirmed", None)
    target_client = str(
        fn_args.pop("target_client_id", None)
        or fn_args.pop("target_client", "master")
        or "master"
    ).strip()
    agent = agent_id or policy_engine.agent_id_for(source_device)
    # ABAC: skill đọc dữ liệu theo phòng ban / cấp bảo mật của NGƯỜI được phục vụ (danh tính
    # máy chủ xác thực, không phải tham số LLM). Ngữ cảnh con của lời gọi này — không lan.
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    CURRENT_PRINCIPAL.set(caller)

    def _done(result: Dict[str, Any]) -> Dict[str, Any]:
        return {"target_client": target_client, "args": fn_args, "result": result}

    declared = None
    try:  # rủi ro do nơi đăng ký tool khai (chỉ nâng, không hạ)
        from mateai.application.skills.plugin_registry import plugin_registry as _reg
        _def = _reg.get_tool(fn_name)
        declared = _def.risk_level if _def is not None else None
    except Exception:  # noqa: BLE001
        declared = None
    decision = policy_engine.authorize(fn_name, fn_args, caller=caller, agent_id=agent,
                                       approved=approved, declared_risk=declared, session_id=session_id)
    audit_ctx = {"agent_id": agent, "target": target_client, "session_id": session_id,
                 "decision": decision.effect, "level": decision.level, "rule": decision.rule,
                 "policy_version": decision.policy_version, "args": fn_args}

    from mateai.application.tasks import ledger
    from mateai.application.tasks.verification import verify
    op_task = ledger.CURRENT_TASK.get()
    audit_ctx["op_task_id"] = op_task

    if decision.effect == policy_engine.DENY:
        logger.warning("[Policy] DENY tool=%s caller=%s agent=%s rule=%s", fn_name, caller, agent, decision.rule)
        security_engine.log_audit(str(caller), fn_name, str(decision.risk), "REJECTED",
                                  {**audit_ctx, "reason": decision.reasons[0]})
        ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                           result={"status": "denied", "error": decision.reasons[0]},
                           verification={"level": "NONE", "status": "failed",
                                         "checks": [f"chính sách từ chối ({decision.rule}): {decision.reasons[0]}"]})
        return _done({
            "status": "error", "success": False,
            "code": "RBAC_DENIED" if decision.rule == "rbac" else "POLICY_DENIED",
            "rule": decision.rule, "error": decision.reasons[0], "message": decision.reasons[0],
        })

    if decision.effect == policy_engine.REQUIRE_APPROVAL:
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
                "agent_id": agent,
                "op_task_id": op_task,
            },
            risk_level=decision.risk,
        )
        _pending = {"status": "need_confirm", "approval_id": _req.get("id")}
        ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                           result=_pending, verification=verify(fn_name, fn_args, _pending, decision.risk, target_client))
        return _done({
            "status": "need_confirm",
            "approval_id": _req.get("id"),
            "message": f"Tác vụ '{fn_name}' yêu cầu phê duyệt. Nhắn 'Đồng ý' để em chạy tiếp.",
            "skill": fn_name, "target_client": target_client,
            "args": fn_args, "requires_confirmation": True,
        })

    if decision.rule == "delegated":
        security_engine.log_audit(str(caller), fn_name, str(decision.risk), "APPROVAL_REMEMBERED", audit_ctx)

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
            _result = await _plugin_registry.execute_tool(fn_name, fn_args, caller_id=caller, authorized=True)
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
    # Kiểm chứng sau hành động (§29): "tool báo đã chạy" chưa phải "đã xong".
    _verification = verify(fn_name, fn_args, _result, decision.risk, target_client)
    if isinstance(_result, dict):
        _result = {**_result, "verification": {k: _verification[k] for k in ("level", "status", "summary")}}
    ledger.record_step(op_task, tool=fn_name, target=target_client, args=fn_args, decision=decision,
                       result=_result, verification=_verification)
    security_engine.log_audit(str(caller), fn_name, str(decision.risk), "SUCCESS" if _ok else "FAILED",
                              {**audit_ctx, "verification": _verification["status"]})
    return _done(_result)


async def execute_approved_tool(item: Dict[str, Any]) -> Dict[str, Any]:
    """
    Executor của hàng đợi HITL cho `kind="tool"`: chạy lại đúng tool + tham số
    + máy đích đã lưu, qua cổng này với `approved=True`. RBAC áp theo người
    YÊU CẦU (không phải người duyệt) — duyệt không mở rộng quyền của người hỏi.
    """
    ctx = item.get("context") or {}
    _remember_approval(item)
    from mateai.application.tasks import ledger
    _token = ledger.CURRENT_TASK.set(ctx.get("op_task_id"))
    _topo("approval", stage="approved", source="hitl", target="tools", status="ok",
          detail=f"{item.get('action_name')} được duyệt bởi {item.get('reviewed_by') or '?'}")
    gate = await run_tool_with_policy(
        str(item.get("action_name") or ""),
        {**dict(item.get("params") or {}), "target_client": ctx.get("target_client") or "master"},
        caller=str(item.get("requested_by") or "anonymous"),
        source_device=ctx.get("source_device"),
        query=str(ctx.get("query") or ""),
        session_id=ctx.get("session_id"),
        approved=True,
        agent_id=ctx.get("agent_id"),
    )
    ledger.settle(ctx.get("op_task_id"))
    ledger.CURRENT_TASK.reset(_token)
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


def _remember_approval(item: Dict[str, Any]) -> None:
    """Người duyệt vừa đồng ý tác vụ do robot / kênh Telegram yêu cầu -> uỷ quyền
    có hạn (L4) cho đúng danh tính + tool (`policy_engine.remember_delegation`)."""
    from mateai.application.security.policy_engine import remember_delegation
    remember_delegation(item.get("requested_by"), str(item.get("action_name") or ""),
                        str(item.get("reviewed_by") or ""))


hitl_manager.register_executor(TOOL_KIND, execute_approved_tool)
