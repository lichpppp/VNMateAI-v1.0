"""
core/plugin_registry.py
=======================
Plugin Registry with Circuit Breaker — Phase 60 Enterprise Middleware & Plugin Registry.

Cung cấp Registry Pattern để đăng ký Connector/Tool một cách linh hoạt (loose coupling),
kết hợp Circuit Breaker để bảo vệ Main Thread khỏi blocking do API ngoại vi chậm/treo.

Nguyên tắc:
  - Tuyệt đối KHÔNG block Main Thread / Event Loop.
  - Mọi external call bị wrap trong asyncio.wait_for(timeout=5.0).
  - Circuit Breaker state: CLOSED -> OPEN -> HALF_OPEN.
  - LLM nhận lỗi timeout -> tự nói: "Hệ thống X đang phản hồi chậm, em xin phép báo cáo lại sau."
"""

from __future__ import annotations

import asyncio
import logging
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from core.config_loader import settings

logger = logging.getLogger(__name__)


#: khoá chứa thông điệp lỗi ở bất kỳ tầng nào của payload tool
_ERROR_KEYS = ("error", "message", "detail", "reason", "description")


def _collect_failure_labels(
    node: Any,
    path: str,
    out: List[str],
    max_items: int,
    max_depth: int,
    depth: int = 0,
) -> None:
    """
    Duyệt đệ quy payload, gom lỗi kèm đường dẫn (vd `connectors.paperless`).

    Chỉ nhặt chuỗi nằm dưới khoá lỗi ở `_ERROR_KEYS`. Cố tình KHÔNG suy đoán từ
    kiểu dữ liệu khác: một list 3 phần tử có thể là "3 tài liệu tìm thấy"
    chứ không phải "3 mục thất bại" — đoán sai thì còn tệ hơn là không nói gì.
    """
    if len(out) >= max_items or depth > max_depth:
        return

    if not isinstance(node, dict):
        return

    for key in _ERROR_KEYS:
        val = node.get(key)
        if isinstance(val, str) and val.strip():
            out.append(f"{path}: {val.strip()}" if path else val.strip())
            return

    # Giá trị không phải dict/list tự nhiên bị bỏ qua bởi kiểm tra bên dưới,
    # nên không cần danh sách khoá loại trừ riêng.
    for key, val in node.items():
        if isinstance(val, dict):
            child = f"{path}.{key}" if path else str(key)
            _collect_failure_labels(val, child, out, max_items, max_depth, depth + 1)
            if len(out) >= max_items:
                return


def _summarize_failure(
    payload: Any,
    max_items: int = 4,
    max_depth: int = 3,
) -> str:
    """
    Tóm tắt lý do thất bại từ payload tool, không bịa thêm thông tin.

    Tool của Phase 59 trả cấu trúc lồng khá sâu và KHÔNG có khoá `error`
    cấp ngoài, ví dụ `check_connector_health` trả:

        {"success": false,
         "connectors": {"aws": {"success": false, "error": "Authentication failed"}}}

    Nếu không tóm tắt, registry trả `error: null` kèm `success: false` — tự
    mâu thuẫn, và mất đúng thông tin người vận hành cần nhất để biết connector
    nào hỏng. Giữ kèm đường dẫn để biết lỗi thuộc connector nào.
    """
    parts: List[str] = []
    _collect_failure_labels(payload, "", parts, max_items, max_depth)
    if parts:
        return "; ".join(parts)
    return "Tool báo thất bại nhưng không kèm mô tả lỗi"


# ================================================================
# Circuit Breaker State Machine
# ================================================================

class CircuitState(Enum):
    CLOSED = "closed"       # Normal operation - requests go through
    OPEN = "open"           # Failing - requests blocked immediately
    HALF_OPEN = "half_open" # Testing recovery - limited requests allowed


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5          # Số lỗi liên tiếp trước khi OPEN
    success_threshold: int = 2          # Số thành công liên tiếp trong HALF_OPEN để CLOSE
    timeout_seconds: float = 30.0       # Thời gian OPEN trước khi chuyển HALF_OPEN
    excluded_exceptions: tuple = ()     # Exception types không tính là failure


class CircuitBreaker:
    """
    Circuit Breaker pattern implementation.
    Thread-safe cho multi-threaded access.
    """

    def __init__(self, name: str, config: Optional[CircuitBreakerConfig] = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float = 0.0
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if timeout elapsed -> transition to HALF_OPEN
                if time.time() - self._last_failure_time >= self.config.timeout_seconds:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info("[CircuitBreaker] %s: OPEN -> HALF_OPEN (timeout elapsed)", self.name)
            return self._state

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    logger.info("[CircuitBreaker] %s: HALF_OPEN -> CLOSED (recovered)", self.name)

    def record_failure(self, exc: Exception) -> None:
        with self._lock:
            # Don't count excluded exceptions
            if isinstance(exc, self.config.excluded_exceptions):
                return

            self._failure_count += 1
            self._success_count = 0
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN -> back to OPEN
                self._state = CircuitState.OPEN
                logger.warning("[CircuitBreaker] %s: HALF_OPEN -> OPEN (failure in test)", self.name)
            elif self._state == CircuitState.CLOSED and self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning("[CircuitBreaker] %s: CLOSED -> OPEN (threshold reached: %d failures)",
                               self.name, self._failure_count)

    def can_execute(self) -> bool:
        """Check if request can proceed."""
        return self.state != CircuitState.OPEN

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "state": self.state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "last_failure_time": self._last_failure_time,
            }

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = 0.0


# ================================================================
# Tool Definition
# ================================================================

@dataclass
class ToolDefinition:
    """
    Định nghĩa một tool có thể gọi bởi LLM.
    """
    name: str                           # Tên tool (unique)
    description: str                    # Mô tả cho LLM
    parameters_schema: Dict[str, Any]   # JSON Schema cho parameters
    function: Callable                  # Sync hoặc async function
    is_async: bool = False              # True nếu function là async
    risk_level: int = 1                 # 1-5 (Zero-Trust)
    timeout_seconds: float = 5.0        # Timeout cho circuit breaker
    tags: List[str] = field(default_factory=list)  # Tags cho filtering
    enabled: bool = True                # Có thể disable runtime

    def to_openai_schema(self) -> Dict[str, Any]:
        """Convert to OpenAI Function Calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }


# ================================================================
# Plugin Registry
# ================================================================

class PluginRegistry:
    """
    Trung tâm quản lý Plugin/Tool cho VN-MateAI.

    Features:
      - Registry Pattern: đăng ký tool động (không hard-code).
      - Circuit Breaker: mỗi tool có breaker riêng.
      - Timeout Enforcement: asyncio.wait_for() bắt buộc.
      - Risk Level Integration: tự động qua HITL nếu risk >= 3.
      - Background Execution: tool nặng đẩy vào background task.
    """

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()
        self._execution_stats: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_tool(
        self,
        tool_name: str,
        function: Callable,
        description: str,
        parameters_schema: Dict[str, Any],
        is_async: bool = False,
        risk_level: int = 1,
        timeout_seconds: float = 5.0,
        tags: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> None:
        """
        Đăng ký một tool mới.

        Args:
            tool_name: Tên unique (vd: "check_aws_cost")
            function: Callable (sync hoặc async)
            description: Mô tả cho LLM
            parameters_schema: JSON Schema
            is_async: True nếu function là async
            risk_level: 1-5 (Zero-Trust)
            timeout_seconds: Timeout cho circuit breaker
            tags: Tags để group/filter
            enabled: Trạng thái ban đầu
        """
        with self._lock:
            if tool_name in self._tools:
                logger.warning("[PluginRegistry] Tool '%s' already registered, overwriting", tool_name)

            tool_def = ToolDefinition(
                name=tool_name,
                description=description,
                parameters_schema=parameters_schema,
                function=function,
                is_async=is_async,
                risk_level=risk_level,
                timeout_seconds=timeout_seconds,
                tags=tags or [],
                enabled=enabled,
            )

            self._tools[tool_name] = tool_def
            self._breakers[tool_name] = CircuitBreaker(
                tool_name,
                CircuitBreakerConfig(
                    failure_threshold=5,
                    timeout_seconds=30.0,
                ),
            )
            self._execution_stats[tool_name] = {
                "total_calls": 0,
                "successful_calls": 0,
                "failed_calls": 0,
                "timeout_calls": 0,
                "circuit_open_calls": 0,
                "avg_latency_ms": 0.0,
            }
            logger.info("[PluginRegistry] Registered tool: %s (risk=%d, async=%s, timeout=%.1fs)",
                        tool_name, risk_level, is_async, timeout_seconds)

    def unregister_tool(self, tool_name: str) -> bool:
        """Gỡ đăng ký tool."""
        with self._lock:
            if tool_name in self._tools:
                del self._tools[tool_name]
                del self._breakers[tool_name]
                del self._execution_stats[tool_name]
                logger.info("[PluginRegistry] Unregistered tool: %s", tool_name)
                return True
            return False

    def toggle_tool(self, tool_name: str, enabled: Optional[bool] = None) -> bool:
        """Bật/tắt tool."""
        with self._lock:
            if tool_name not in self._tools:
                raise KeyError(f"Tool '{tool_name}' not found")
            if enabled is None:
                enabled = not self._tools[tool_name].enabled
            self._tools[tool_name].enabled = enabled
            logger.info("[PluginRegistry] Tool '%s' enabled=%s", tool_name, enabled)
            return enabled

    # ------------------------------------------------------------------
    # Discovery (for LLM)
    # ------------------------------------------------------------------

    def get_all_tools_schema(self) -> List[Dict[str, Any]]:
        """Trả về danh sách tool schema cho LLM (OpenAI Function Calling format)."""
        with self._lock:
            return [
                tool.to_openai_schema()
                for tool in self._tools.values()
                if tool.enabled
            ]

    def get_tool_names(self) -> List[str]:
        """Danh sách tên tool enabled."""
        with self._lock:
            return sorted([name for name, tool in self._tools.items() if tool.enabled])

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """Lấy tool definition."""
        with self._lock:
            return self._tools.get(tool_name)

    # ------------------------------------------------------------------
    # Execution with Circuit Breaker & Timeout
    # ------------------------------------------------------------------

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        caller_id: str = "AI_Agent",
        source_device: str = "web",
    ) -> Dict[str, Any]:
        """
        Thực thi tool với Circuit Breaker + Timeout + HITL.

        Returns:
            {
                "success": bool,
                "data": Any,
                "error": str | None,
                "awaiting_approval": bool,      # Nếu cần HITL
                "approval_id": str | None,
                "circuit_state": str,           # closed/open/half_open
                "latency_ms": float,
            }
        """
        with self._lock:
            tool = self._tools.get(tool_name)
            if not tool:
                return {
                    "success": False,
                    "error": f"Tool '{tool_name}' not found in registry",
                    "circuit_state": "unknown",
                }

            if not tool.enabled:
                return {
                    "success": False,
                    "error": f"Tool '{tool_name}' is disabled",
                    "circuit_state": "disabled",
                }

            breaker = self._breakers[tool_name]

        # Check Circuit Breaker
        if not breaker.can_execute():
            breaker_status = breaker.get_status()
            logger.warning("[PluginRegistry] Tool '%s' blocked by Circuit Breaker (state=%s)",
                           tool_name, breaker_status["state"])
            self._record_stat(tool_name, "circuit_open_calls")
            return {
                "success": False,
                "error": f"Hệ thống {tool_name} đang trong trạng thái Circuit Breaker OPEN (quá nhiều lỗi). Vui lòng thử lại sau.",
                "circuit_state": breaker_status["state"],
                "circuit_details": breaker_status,
            }

        # Check HITL requirement (risk_level >= 3)
        if tool.risk_level >= 3:
            from core.zero_trust import hitl_manager, execute_with_hitl

            def _sync_executor() -> Dict[str, Any]:
                """
                Executor HÀNG ĐỘNG cho nhánh HITL. Bắt buộc là hàm sync, vì
                `HITLManager.approve()` (zero_trust) gọi callback bằng `cb()`
                một cách đồng bộ — truyền hàm async vào đây sẽ tạo coroutine
                chưa được await, tức là "duyệt xong nhưng không chạy gì cả".

                Bản gốc của hàm này gọi `tool.function(**arguments)` TỚI BA LẦN
                trong nhánh sync (dead code `run_in_executor`, rồi `wait_for`,
                rồi mới gọi thật) — với tool có tác dụng phụ (ghi log, ghi DB,
                gọi API ngoài) đó là hậu quả thật, không phải lỗi hình thức.
                """
                import asyncio
                import threading

                def _run_coroutine_in_thread() -> Any:
                    # Luôn dựng event loop RIÊNG trong thread riêng. Nếu dùng
                    # `asyncio.run()` ngay trong luồng gọi, sẽ ném
                    # "asyncio.run() cannot be called from a running event loop"
                    # vì execute_tool() đang chạy trong event loop của server.
                    out: Dict[str, Any] = {}

                    def _worker() -> None:
                        try:
                            out["value"] = asyncio.run(
                                asyncio.wait_for(
                                    tool.function(**arguments),
                                    timeout=tool.timeout_seconds,
                                )
                            )
                        except BaseException as exc:  # noqa: BLE001
                            out["error"] = exc

                    th = threading.Thread(target=_worker, daemon=True)
                    th.start()
                    th.join(timeout=tool.timeout_seconds + 1.0)
                    if th.is_alive():
                        raise TimeoutError(f"Timeout sau {tool.timeout_seconds}s")
                    if "error" in out:
                        raise out["error"]
                    return out.get("value")

                try:
                    if tool.is_async:
                        return _run_coroutine_in_thread()
                    return tool.function(**arguments)
                except Exception as e:
                    return {"success": False, "error": str(e)}

            # `execute_with_hitl` là coroutine. Bản gốc gọi nó không `await`
            # rồi gọi `.get()` ngay trên coroutine -> `AttributeError:
            # 'coroutine' object has no attribute 'get'` cho MỌI tool có
            # risk_level >= 3. Hiện chưa tool nào đạt ngưỡng nên lỗi chưa lộ,
            # nhưng chỉ cần khai báo một connector action rủi ro cao là nổ.
            #
            # `risk_level=tool.risk_level` truyền mức rủi ro đã khai của tool
            # vào cổng. Không có nó, cổng chỉ tra bảng rủi ro theo TÊN tác vụ
            # và mặc định 2 — nên tool khai level 5 với tên trung tính
            # ("check_*", "export_*") sẽ lọt qua mà không ai duyệt.
            hitl_result = await execute_with_hitl(
                action_name=tool_name,
                params=arguments,
                executor=_sync_executor,
                requested_by=caller_id,
                description=f"Tool '{tool_name}' (risk level {tool.risk_level}/5) được gọi bởi {caller_id}",
                risk_level=tool.risk_level,
            )

            if hitl_result.get("status") == "awaiting_approval":
                return {
                    "success": False,
                    "awaiting_approval": True,
                    "approval_id": hitl_result.get("approval_id"),
                    "message": hitl_result.get("message"),
                    "risk_level": hitl_result.get("risk_level"),
                    "circuit_state": breaker.get_status()["state"],
                }

            # If executed, extract result
            exec_result = hitl_result.get("result", {})
            return self._finalize_result(tool_name, breaker, exec_result)

        # Low risk - execute directly with timeout
        return await self._execute_with_timeout(tool_name, tool, arguments, breaker)

    async def _execute_with_timeout(
        self,
        tool_name: str,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        breaker: CircuitBreaker,
    ) -> Dict[str, Any]:
        """Execute tool with asyncio.wait_for timeout."""
        t0 = time.monotonic()
        try:
            if tool.is_async:
                result = await asyncio.wait_for(
                    tool.function(**arguments),
                    timeout=tool.timeout_seconds,
                )
            else:
                # Run sync function in thread pool
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: tool.function(**arguments)),
                    timeout=tool.timeout_seconds,
                )

            latency_ms = (time.monotonic() - t0) * 1000
            breaker.record_success()
            return self._finalize_result(tool_name, breaker, result, latency_ms)

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - t0) * 1000
            breaker.record_failure(TimeoutError(f"Timeout after {tool.timeout_seconds}s"))
            self._record_stat(tool_name, "timeout_calls")
            logger.warning("[PluginRegistry] Tool '%s' TIMEOUT after %.1fs", tool_name, tool.timeout_seconds)
            return {
                "success": False,
                "error": f"Hệ thống {tool_name} phản hồi quá chậm (>{tool.timeout_seconds}s). Em xin phép báo cáo lại sau.",
                "circuit_state": breaker.get_status()["state"],
                "timeout": True,
                "latency_ms": latency_ms,
            }
        except Exception as e:
            latency_ms = (time.monotonic() - t0) * 1000
            breaker.record_failure(e)
            logger.error("[PluginRegistry] Tool '%s' ERROR: %s", tool_name, e, exc_info=True)
            return self._finalize_result(tool_name, breaker, {"success": False, "error": str(e)}, latency_ms)

    def _finalize_result(
        self,
        tool_name: str,
        breaker: CircuitBreaker,
        result: Any,
        latency_ms: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Chuẩn hóa kết quả và update stats."""
        success = False
        data = None
        error = None

        if isinstance(result, dict):
            success = bool(result.get("success", False))
            data = result.get("data")
            error = result.get("error")
            # Không phải tool nào cũng nest kết quả dưới khoá `data`.
            # `check_connector_health` chẳng hạn đặt chi tiết từng connector ở
            # khoá `connectors` — chỉ lấy `data` sẽ trả `data: null` và bỏ mất
            # toàn bộ thông tin. Giữ nguyên vật payload thay vì tự dựng lại.
            if data is None:
                data = result
        elif result is not None:
            success = True
            data = result
        else:
            error = "Tool returned None"

        # `success: false` mà `error: null` là câu trả lời tự mâu thuẫn: UI
        # đọc `success` để bật ✔ xanh, đọc `error` để hiện lý do — thiếu một
        # trong hai thì người dùng thấy kết quả vừa "thành công" vừa không có
        # lý do. Không tự bịa nội dung lỗi, chỉ tóm tắt từ chính payload.
        if not success and not error:
            error = _summarize_failure(data)

        # Update stats
        self._record_stat(tool_name, "total_calls")
        if success:
            self._record_stat(tool_name, "successful_calls")
        else:
            self._record_stat(tool_name, "failed_calls")

        if latency_ms is not None:
            self._update_avg_latency(tool_name, latency_ms)

        return {
            "success": success,
            "data": data,
            "error": error,
            "awaiting_approval": False,
            "circuit_state": breaker.get_status()["state"],
            "latency_ms": latency_ms,
        }

    def _record_stat(self, tool_name: str, key: str) -> None:
        with self._lock:
            if tool_name in self._execution_stats:
                self._execution_stats[tool_name][key] = self._execution_stats[tool_name].get(key, 0) + 1

    def _update_avg_latency(self, tool_name: str, latency_ms: float) -> None:
        with self._lock:
            stats = self._execution_stats.get(tool_name)
            if stats:
                total = stats["total_calls"]
                current_avg = stats.get("avg_latency_ms", 0.0)
                # Running average
                stats["avg_latency_ms"] = ((current_avg * (total - 1)) + latency_ms) / total

    def get_stats(self) -> Dict[str, Any]:
        """Lấy thống kê execution."""
        with self._lock:
            return {
                tool_name: {**stats, "circuit_breaker": self._breakers[tool_name].get_status()}
                for tool_name, stats in self._execution_stats.items()
            }

    def reset_circuit(self, tool_name: str) -> bool:
        """Reset circuit breaker thủ công (admin action)."""
        with self._lock:
            if tool_name in self._breakers:
                self._breakers[tool_name].reset()
                logger.info("[PluginRegistry] Circuit breaker reset for '%s'", tool_name)
                return True
            return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
plugin_registry = PluginRegistry()