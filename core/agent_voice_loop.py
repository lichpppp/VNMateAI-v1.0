"""
core/agent_voice_loop.py
========================
Phase 7: Agent Loop Optimization & Tool Pruning.

Chức năng:
  - Tối ưu hóa vòng lặp Agent Tool Calling: Bỏ qua LLM Round 2 (Direct Response Synthesis)
    khi công cụ trả về kết quả tự giải thích hoặc xác nhận hành động.
  - Cắt tỉa schema công cụ (Tool Pruning): Chỉ nạp 3–5 công cụ khớp ý định thay vì nạp toàn bộ 78+ skills.
  - Giới hạn cứng số vòng lặp: MAX_ROUNDS = 2 (triệt tiêu nguy cơ lặp vô tận / ngốn token).
  - Thu gọn dữ liệu Tool (Payload Pruning): Lọc bỏ metadata rác, stacktrace trước khi đưa vào LLM Round 2.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Set, Tuple

from core.safety_guard import security_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionResult:
    """Kết quả thực thi một tool call."""
    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    success: bool
    data: Any
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    direct_response: Optional[str] = None


@dataclass
class AgentTurnMetrics:
    """Số liệu hiệu năng của vòng lặp Agent."""
    total_rounds: int = 1
    tools_called: List[str] = field(default_factory=list)
    bypassed_llm_round_2: bool = False
    tool_execution_ms: float = 0.0
    schema_pruning_count: int = 0


# ---------------------------------------------------------------------------
# 1. Tool Schema Pruning (Cắt tỉa schema trước khi gọi LLM)
# ---------------------------------------------------------------------------

def prune_tool_schemas(query: str, max_tools: int = 5) -> List[Dict[str, Any]]:
    """
    Lọc bỏ các schema công cụ không liên quan dựa trên phân loại ý định và chỉ mục miền (Phase 8).
    - Nếu là câu chào hỏi / đàm thoại thông thường: Trả về [] (0 tools, giảm TTFT).
    - Nếu là lệnh tác vụ kỹ thuật: Chọn tối đa max_tools kỹ năng có liên quan nhất.
    """
    try:
        from core.dynamic_skill_router import dynamic_skill_router
        return dynamic_skill_router.get_tools_for_query(query, max_tools=max_tools)
    except Exception as exc:
        logger.warning("[ToolPruning] Lỗi khi gọi dynamic_skill_router, fallback sang logic cơ sở: %s", exc)
        from core.plugin_manager import plugin_manager
        return plugin_manager.get_all_tools()[:max_tools]


# ---------------------------------------------------------------------------
# 2. Direct Response Synthesis (Bỏ qua LLM Round 2)
# ---------------------------------------------------------------------------

def can_synthesize_direct_response(
    tool_name: str,
    tool_result: Dict[str, Any],
) -> Optional[str]:
    """
    Kiểm tra xem kết quả thực thi công cụ có thể tự tổng hợp thành câu nói tự nhiên
    mà KHÔNG CẦN gọi LLM Round 2 hay không.

    Nếu có: Trả về câu trả lời tự nhiên dạng chuỗi (tiết kiệm 2.0–3.5s độ trễ).
    Nếu không: Trả về None để nhường cho LLM Round 2 tổng hợp.
    """
    if not isinstance(tool_result, dict):
        return None

    # Trường hợp 1: Công cụ trả về lỗi. Xét GIÁ TRỊ của "error", không xét có
    # khoá hay không: plugin_manager luôn trả {"success", "data", "error": None},
    # nên điều kiện cũ (`"error" in tool_result`) coi mọi kết quả thành công là lỗi.
    if tool_result.get("success") is False or tool_result.get("error"):
        err = tool_result.get("error") or "không xác định"
        # Bỏ qua lỗi kỹ thuật dài dòng
        clean_err = str(err).split("\n")[0][:120]
        return f"Dạ, tác vụ {tool_name} không thực hiện được do: {clean_err} ạ."

    data = tool_result.get("data") if "data" in tool_result else tool_result

    # Trường hợp 2: Công cụ đã có sẵn trường message / summary / text rõ ràng
    if isinstance(data, dict):
        for msg_key in ("message", "summary", "text", "msg", "result_str"):
            val = data.get(msg_key)
            if isinstance(val, str) and 8 <= len(val.strip()) <= 200:
                clean_val = val.strip().rstrip(".! ")
                return f"Dạ, {clean_val} ạ."

    # Trường hợp 3: Các công cụ hành động tiêu biểu (Action Tools)
    ACTION_TOOL_TEMPLATES: Dict[str, Callable[[Any], str]] = {
        "kill_process": lambda d: "Dạ, em đã dừng tiến trình được yêu cầu thành công ạ.",
        "set_system_volume": lambda d: "Dạ, em đã điều chỉnh âm lượng hệ thống xong rồi ạ.",
        "mute_audio": lambda d: "Dạ, em đã tắt tiếng hệ thống rồi ạ.",
        "open_application": lambda d: "Dạ, em đã khởi chạy ứng dụng thành công ạ.",
        "capture_screen": lambda d: "Dạ, em đã chụp ảnh màn hình thành công ạ.",
        "set_clipboard": lambda d: "Dạ, em đã lưu nội dung vào bộ nhớ tạm rồi ạ.",
        "send_telegram_message": lambda d: "Dạ, em đã gửi tin nhắn Telegram thành công ạ.",
        "delete_file": lambda d: "Dạ, em đã xóa tệp tin thành công ạ.",
        "write_file": lambda d: "Dạ, em đã ghi dữ liệu vào tệp tin xong rồi ạ.",
        "toggle_device": lambda d: "Dạ, em đã điều khiển thiết bị thành công ạ.",
    }

    if tool_name in ACTION_TOOL_TEMPLATES:
        try:
            return ACTION_TOOL_TEMPLATES[tool_name](data)
        except Exception:
            pass

    # Trường hợp 4: Kết quả trả về rất ngắn gọn dạng confirmation
    if isinstance(data, dict) and len(data) <= 2:
        if data.get("status") in ("success", "ok", "done", True):
            return f"Dạ, em đã thực hiện xong lệnh {tool_name} thành công ạ."

    # Nếu kết quả phức tạp (dữ liệu bảng, danh sách dài...) -> cần LLM Round 2
    return None


# ---------------------------------------------------------------------------
# 3. Payload Pruning (Thu gọn dữ liệu gửi cho LLM Round 2)
# ---------------------------------------------------------------------------

def prune_tool_payload_for_llm(data: Any, max_chars: int = 1500) -> str:
    """
    Cắt gọt các trường dư thừa (stack trace, debug logs, binary data)
    trước khi nhồi vào prompt cho LLM Round 2.
    """
    if isinstance(data, dict):
        pruned = {}
        for k, v in data.items():
            # Loại bỏ các trường rác kỹ thuật
            if k in ("traceback", "stacktrace", "debug", "raw_headers", "binary", "base64"):
                continue
            if isinstance(v, (str, int, float, bool)):
                pruned[k] = v
            elif isinstance(v, list) and len(v) > 10:
                pruned[k] = v[:10]  # Giới hạn tối đa 10 phần tử
            elif isinstance(v, dict):
                pruned[k] = {sk: sv for sk, sv in list(v.items())[:5]}
            else:
                pruned[k] = v
        dumped = json.dumps(pruned, ensure_ascii=False, default=str)
    else:
        dumped = str(data)

    if len(dumped) > max_chars:
        dumped = dumped[:max_chars] + "... [dữ liệu đã được rút gọn]"
    return dumped


# ---------------------------------------------------------------------------
# 4. Tool Execution Helper
# ---------------------------------------------------------------------------

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


#: Kết quả cần đọc nguyên văn cho người dùng, không để LLM diễn giải lại.
_GATE_STATUSES = ("need_confirm", "awaiting_approval")


async def execute_tool_call(
    tool_call: Dict[str, Any],
    user_info: Optional[Dict[str, Any]] = None,
    source_device: str = "portal",
) -> ToolExecutionResult:
    """Tool call của đường voice realtime — đi qua `run_tool_with_policy`."""
    fn_name = tool_call.get("name", "")
    call_id = tool_call.get("id", f"call_{int(time.time()*1000)}")
    raw_args = tool_call.get("arguments", "{}")

    t0 = time.perf_counter()
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except Exception:
        args = {}

    user_info = user_info or {}
    caller = str(user_info.get("sub") or user_info.get("username") or "anonymous")
    try:
        out = await run_tool_with_policy(fn_name, args, caller=caller, source_device=source_device)
    except Exception as exc:
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name=fn_name,
            arguments=args,
            success=False,
            data=None,
            error=str(exc),
            execution_time_ms=(time.perf_counter() - t0) * 1000,
            direct_response=f"Dạ, lệnh {fn_name} gặp lỗi: {str(exc)[:100]} ạ.",
        )

    res = out["result"] or {}
    success = res.get("success") is True or res.get("status") == "success"
    data = res.get("data", res) if success else None
    error = None if success else (res.get("error") or res.get("message") or str(res))
    gated = res.get("status") in _GATE_STATUSES or res.get("code") == "RBAC_DENIED" or (
        res.get("status") == "error" and "chính sách bảo mật" in str(res.get("message", ""))
    )
    if gated:
        direct_text = str(res.get("message") or res.get("error") or "")
    else:
        direct_text = can_synthesize_direct_response(fn_name, {"success": success, "data": data, "error": error})
    return ToolExecutionResult(
        tool_call_id=call_id,
        tool_name=fn_name,
        arguments=out["args"],
        success=success,
        data=data,
        error=error,
        execution_time_ms=(time.perf_counter() - t0) * 1000,
        direct_response=direct_text or None,
    )


def _deaccent(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()
