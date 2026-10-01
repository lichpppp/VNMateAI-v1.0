"""
src/mateai/application/skills/tool_executor.py
==============================================
Bộ thực thi công cụ nguyên tử có kiểm soát an ninh (Secure Tool Executor).

Mục tiêu:
- Thực thi công cụ với cơ chế Timeout và cách ly ngoại lệ (Failure Isolation).
- Kiểm tra quyền bảo mật RBAC & Cấp độ rủi ro (Risk Level) trước khi thực thi.
- Tự động phát sinh Audit Event ghi lại vết hành động (Actor, Tool, Status).
- Tuân thủ RULE-007 (Mọi công cụ đặc quyền phải kiểm tra phân quyền).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Coroutine, Dict, Optional

from src.mateai.domain.identity.entities import ClearanceLevel, UserIdentity, UserRole
from src.mateai.domain.skills.entities import ToolDefinition, ToolRiskLevel
from src.mateai.domain.audit.entities import AuditEvent, AuditAction, AuditRiskLevel

logger = logging.getLogger(__name__)


class ToolExecutionError(Exception):
    """Lỗi khi thực thi công cụ."""
    pass


class ToolPermissionDenied(Exception):
    """Lỗi từ chối quyền truy cập công cụ."""
    pass


class ToolExecutor:
    """Bộ điều phối thực thi công cụ kèm theo bảo vệ an ninh và kiểm toán."""

    def __init__(self):
        self._handlers: Dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}

    def register_handler(self, tool_name: str, handler: Callable[..., Coroutine[Any, Any, Any]]) -> None:
        """Đăng ký hàm thực thi kỹ thuật cho công cụ."""
        self._handlers[tool_name] = handler

    async def execute(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        user: Optional[UserIdentity] = None,
        request_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Thực thi an toàn một công cụ nguyên tử:
        1. Kiểm tra quyền hạn Clearance.
        2. Kiểm tra cấp độ rủi ro Risk Level.
        3. Thực thi kèm timeout bảo vệ.
        4. Trả về kết quả và Audit Event.
        """
        start_time = time.perf_counter()

        # 1. Kiểm tra Clearance
        req_clearance = ClearanceLevel(tool.required_clearance) if hasattr(ClearanceLevel, str(tool.required_clearance)) else ClearanceLevel.INTERNAL
        if user and not user.has_permission_for_clearance(req_clearance):
            raise ToolPermissionDenied(
                f"Người dùng '{user.username}' không có quyền truy cập công cụ '{tool.name}' (yêu cầu {req_clearance.value})"
            )

        # 2. Kiểm tra Critical Risk (chỉ ADMIN/SUPERADMIN được chạy CRITICAL)
        if tool.risk_level == ToolRiskLevel.LEVEL_4_CRITICAL:
            if user and not user.is_admin():
                raise ToolPermissionDenied(
                    f"Công cụ '{tool.name}' thuộc mức rủi ro CRITICAL, yêu cầu quyền quản trị viên."
                )

        handler = self._handlers.get(tool.name)
        if not handler:
            raise ToolExecutionError(f"Chưa cấu hình hàm thực thi (handler) cho công cụ '{tool.name}'")

        # 3. Thực thi với Timeout
        is_success = False
        error_msg: Optional[str] = None
        result: Any = None

        try:
            result = await asyncio.wait_for(
                handler(**arguments),
                timeout=tool.timeout_seconds
            )
            is_success = True
        except asyncio.TimeoutError:
            error_msg = f"Công cụ '{tool.name}' bị ngắt do vượt quá thời gian tối đa {tool.timeout_seconds}s"
            logger.error("[ToolExecutor] %s", error_msg)
            raise ToolExecutionError(error_msg)
        except Exception as exc:
            error_msg = str(exc)
            logger.error("[ToolExecutor] Lỗi khi thực thi công cụ '%s': %s", tool.name, exc)
            raise ToolExecutionError(error_msg)
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            audit_risk = AuditRiskLevel.HIGH if tool.risk_level.value >= 3 else AuditRiskLevel.INFO
            # Ghi nhận Audit Event
            audit_event = AuditEvent(
                actor_id=user.username if user else "anonymous",
                action=AuditAction.TOOL_EXECUTE,
                target=tool.name,
                risk_level=audit_risk,
                is_success=is_success,
                details={"latency_ms": elapsed_ms, "error": error_msg},
                request_id=request_id,
                session_id=session_id
            )

        return {
            "tool_name": tool.name,
            "success": is_success,
            "result": result,
            "latency_ms": elapsed_ms,
            "audit_event": audit_event
        }


# Singleton executor
tool_executor = ToolExecutor()
