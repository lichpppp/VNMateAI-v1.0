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

    # Trường hợp 1: Công cụ trả về lỗi
    if tool_result.get("success") is False or "error" in tool_result:
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

async def execute_tool_call(
    tool_call: Dict[str, Any],
    user_info: Optional[Dict[str, Any]] = None,
) -> ToolExecutionResult:
    """Thực thi một tool call với đo lường thời gian và Zero-Trust."""
    fn_name = tool_call.get("name", "")
    call_id = tool_call.get("id", f"call_{int(time.time()*1000)}")
    raw_args = tool_call.get("arguments", "{}")

    t0 = time.monotonic()
    try:
        args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
    except Exception:
        args = {}

    # Kiểm tra Zero-Trust
    try:
        from core.zero_trust import evaluate_risk
        risk = evaluate_risk(fn_name, args)
        if risk == "BLOCKED":
            return ToolExecutionResult(
                tool_call_id=call_id,
                tool_name=fn_name,
                arguments=args,
                success=False,
                data=None,
                error="Bị chặn bởi chính sách bảo mật Zero-Trust",
                execution_time_ms=(time.monotonic() - t0) * 1000,
            )
    except Exception:
        pass

    # Thực thi qua Plugin Manager
    try:
        from core.plugin_manager import plugin_manager
        res = await plugin_manager.execute_skill(fn_name, args)
        exec_ms = (time.monotonic() - t0) * 1000

        direct_text = can_synthesize_direct_response(fn_name, res)
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name=fn_name,
            arguments=args,
            success=res.get("success", False),
            data=res.get("data"),
            error=res.get("error"),
            execution_time_ms=exec_ms,
            direct_response=direct_text,
        )
    except Exception as exc:
        exec_ms = (time.monotonic() - t0) * 1000
        return ToolExecutionResult(
            tool_call_id=call_id,
            tool_name=fn_name,
            arguments=args,
            success=False,
            data=None,
            error=str(exc),
            execution_time_ms=exec_ms,
            direct_response=f"Dạ, lệnh {fn_name} gặp lỗi: {str(exc)[:100]} ạ.",
        )


def _deaccent(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in norm if not unicodedata.combining(c)).lower()
