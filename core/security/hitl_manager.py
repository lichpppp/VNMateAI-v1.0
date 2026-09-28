"""
core/security/hitl_manager.py
=============================
Enhanced Human-In-The-Loop Manager — Phase 60 Enterprise Middleware & Plugin Registry.

Module độc lập quản lý quy trình phê duyệt (approval workflow) cho các Tool
có risk_level >= 3. Tách biệt khỏi zero_trust.py để Single Responsibility.

Features:
  - Execution ID tracking (unique per invocation)
  - AsyncEvent-based waiting (non-blocking)
  - Telegram Webhook callback handling (1-Tap buttons)
  - Timeout 15 phút auto-reject
  - Audit log integration
  - Web Portal polling support
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.config_loader import settings
from core.telegram_gateway import telegram_gateway
from core.zero_trust import log_security_audit

logger = logging.getLogger(__name__)


# ================================================================
# Models
# ================================================================

class ApprovalStatus(Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"


@dataclass
class ApprovalRequest:
    """
    Một yêu cầu phê duyệt HITL.
    """
    id: str                                     # Execution ID (unique)
    tool_name: str                              # Tên tool/action
    parameters: Dict[str, Any]                  # Tham số tool
    requested_by: str                           # AI_Agent, user_id, etc.
    description: str                            # Mô tả human-readable
    risk_level: int                             # 1-5
    status: ApprovalStatus = ApprovalStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None       # TTL 15 phút
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    execution_result: Any = None
    execution_error: Optional[str] = None
    telegram_message_id: Optional[int] = None   # For callback editing
    telegram_chat_id: Optional[str] = None
    callback_event: asyncio.Event = field(default_factory=asyncio.Event)  # For async wait
    executor_callback: Optional[Callable] = None  # Sync/async function to execute after approval

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "parameters": self.parameters,
            "requested_by": self.requested_by,
            "description": self.description,
            "risk_level": self.risk_level,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "rejection_reason": self.rejection_reason,
            "execution_result": self.execution_result,
            "execution_error": self.execution_error,
        }

    def is_expired(self) -> bool:
        if self.expires_at:
            return datetime.utcnow() >= self.expires_at
        return False


# ================================================================
# HITL Manager
# ================================================================

class HITLManager:
    """
    Quản lý hàng đợi phê duyệt Human-In-The-Loop.

    Thread-safe cho multi-thread access (FastAPI + Telegram bot thread).
    """

    def __init__(self, default_ttl_seconds: int = 900):  # 15 phút
        self.default_ttl = default_ttl_seconds
        self._lock = threading.RLock()
        self._pending: Dict[str, ApprovalRequest] = {}      # id -> request
        self._by_tool: Dict[str, List[str]] = {}            # tool_name -> [ids]
        self._by_requester: Dict[str, List[str]] = {}       # requested_by -> [ids]
        self._cleanup_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start_cleanup_loop(self) -> None:
        """Khởi động background cleanup loop (chạy trong event loop chính)."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            logger.info("[HITLManager] Cleanup loop started")

    async def stop_cleanup_loop(self) -> None:
        """Dừng cleanup loop."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("[HITLManager] Cleanup loop stopped")

    async def _cleanup_loop(self) -> None:
        """Dọn dẹp request hết hạn mỗi 60s."""
        while True:
            try:
                await asyncio.sleep(60)
                await self._expire_old_requests()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[HITLManager] Cleanup loop error: %s", e)

    async def _expire_old_requests(self) -> None:
        """Đánh dấu EXPIRED các request quá TTL."""
        now = datetime.utcnow()
        expired_ids = []

        with self._lock:
            for req_id, req in self._pending.items():
                if req.status == ApprovalStatus.PENDING and req.expires_at and now >= req.expires_at:
                    expired_ids.append(req_id)

        for req_id in expired_ids:
            await self._handle_expiration(req_id)

    async def _handle_expiration(self, request_id: str) -> None:
        """Xử lý request hết hạn."""
        with self._lock:
            req = self._pending.get(request_id)
            if not req or req.status != ApprovalStatus.PENDING:
                return
            req.status = ApprovalStatus.EXPIRED

        # Wake up waiter
        req.callback_event.set()

        # Notify Telegram
        try:
            await telegram_gateway.send_incident_alert(
                f"⏰ [HITL HẾT HẠN] Yêu cầu `{request_id}` ({req.tool_name}) "
                f"đã quá 15 phút không được duyệt. Tác vụ bị hủy tự động."
            )
        except Exception:
            pass

        # Log audit
        log_security_audit(
            client_id=req.requested_by,
            action=f"HITL_EXPIRED_{req.tool_name.upper()}",
            risk="WARNING",
            status="EXPIRED",
            details={"approval_id": request_id, "tool": req.tool_name},
        )

        logger.warning("[HITLManager] Request %s expired", request_id)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def create_request(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        requested_by: str,
        description: str,
        risk_level: int,
        executor_callback: Optional[Callable] = None,
        ttl_seconds: Optional[int] = None,
    ) -> ApprovalRequest:
        """
        Tạo yêu cầu phê duyệt mới.

        Args:
            tool_name: Tên tool (vd: "connector_aws_billing_summary")
            parameters: Dict tham số
            requested_by: Ai yêu cầu (AI_Agent, user_id...)
            description: Mô tả cho CEO
            risk_level: 1-5
            executor_callback: Function để execute sau khi duyệt (sync hoặc async)
            ttl_seconds: TTL tùy chỉnh (mặc định 15 phút)

        Returns:
            ApprovalRequest object
        """
        request_id = f"HITL-{uuid.uuid4().hex[:8].upper()}"
        ttl = ttl_seconds or self.default_ttl
        expires_at = datetime.utcnow().timestamp() + ttl

        request = ApprovalRequest(
            id=request_id,
            tool_name=tool_name,
            parameters=parameters,
            requested_by=requested_by,
            description=description,
            risk_level=risk_level,
            expires_at=datetime.fromtimestamp(expires_at),
            executor_callback=executor_callback,
        )

        with self._lock:
            self._pending[request_id] = request
            self._by_tool.setdefault(tool_name, []).append(request_id)
            self._by_requester.setdefault(requested_by, []).append(request_id)

        # Log audit
        log_security_audit(
            client_id=requested_by,
            action=f"HITL_REQUEST_{tool_name.upper()}",
            risk="PENDING",
            status="PENDING",
            details={"approval_id": request_id, "risk_level": risk_level, "params": parameters},
        )

        # Send Telegram notification with 1-Tap buttons
        self._send_telegram_notification(request)

        logger.info("[HITLManager] Created request %s for %s (risk=%d)", request_id, tool_name, risk_level)
        return request

    def _send_telegram_notification(self, request: ApprovalRequest) -> None:
        """Gửi thông báo Telegram với nút 1-Tap (fire-and-forget thread)."""
        def _send():
            try:
                # Build message
                risk_emoji = {1: "🟢", 2: "🟢", 3: "🟡", 4: "🟠", 5: "🔴"}.get(request.risk_level, "⚪")
                message = (
                    f"{risk_emoji} *YÊU CẦU PHÊ DUYỆT C.E.O*\n"
                    f"Mã: `{request.id}`\n"
                    f"Cấp độ rủi ro: Level {request.risk_level}/5\n"
                    f"Công cụ: `{request.tool_name}`\n"
                    f"Người yêu cầu: {request.requested_by}\n"
                    f"Mô tả: {request.description}\n"
                    f"Tham số: `{str(request.parameters)[:200]}`\n\n"
                    f"⏰ Hết hạn sau 15 phút (tự động hủy)."
                )

                # Send with buttons
                keyboard = telegram_gateway.build_hitl_keyboard(request.id)
                chat_id = telegram_gateway._get_config()
                target_chat = chat_id.incident_group_id if chat_id else ""
                if not target_chat and chat_id and chat_id.admin_chat_ids:
                    target_chat = str(chat_id.admin_chat_ids[0])

                if target_chat and keyboard:
                    import httpx
                    import json
                    payload = {
                        "chat_id": target_chat,
                        "text": message,
                        "parse_mode": "Markdown",
                        "reply_markup": keyboard.to_dict(),
                    }
                    url = f"https://api.telegram.org/bot{chat_id.bot_token}/sendMessage"
                    resp = httpx.post(url, json=payload, timeout=15.0)
                    if resp.status_code == 200:
                        data = resp.json()
                        msg_id = data.get("result", {}).get("message_id")
                        with self._lock:
                            # KHÔNG viết `if rid := request.id in self._pending`:
                            # walrus bị toán tử `in` nuốt, biến nhận giá trị bool
                            # (`True`), và `self._pending[True]` ném KeyError bị
                            # `except` bên ngoài nuốt mất. Hệ quả là
                            # `telegram_message_id` không bao giờ được ghi, nên
                            # `approve()` không xoá được nút bấm và CEO bấm lại
                            # thấy "đã duyệt rồi" dù tin nhắn vẫn còn nút.
                            stored = self._pending.get(request.id)
                            if stored is not None and msg_id:
                                stored.telegram_message_id = msg_id
                                stored.telegram_chat_id = target_chat
                        logger.info("[HITLManager] Telegram notification sent for %s", request.id)
                    else:
                        logger.warning("[HITLManager] Telegram send failed: %s", resp.text)
                elif target_chat:
                    # Fallback: text only
                    telegram_gateway.send_incident_alert(message)
            except Exception as e:
                logger.error("[HITLManager] Telegram notification error: %s", e)

        threading.Thread(target=_send, daemon=True, name="hitl-telegram").start()

    # ------------------------------------------------------------------
    # Wait for Approval (Async)
    # ------------------------------------------------------------------

    async def wait_for_approval(self, request_id: str, timeout: float = 900.0) -> ApprovalRequest:
        """
        Chờ phê duyệt (async non-blocking).
        Dùng trong PluginRegistry.execute_tool khi risk_level >= 3.

        Args:
            request_id: ID của request
            timeout: Timeout chờ (giây), mặc định 15 phút

        Returns:
            ApprovalRequest với status cập nhật (APPROVED/REJECTED/EXPIRED)
        """
        with self._lock:
            req = self._pending.get(request_id)
            if not req:
                raise KeyError(f"Request {request_id} not found")

        try:
            await asyncio.wait_for(req.callback_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Timeout sẽ được handle bởi cleanup loop, nhưng double-check
            pass

        with self._lock:
            return self._pending.get(request_id, req)

    # ------------------------------------------------------------------
    # Decision Handling (from Telegram Webhook / Web Portal)
    # ------------------------------------------------------------------

    async def approve(
        self,
        request_id: str,
        approved_by: str,
        resume_execution: bool = True,
    ) -> Dict[str, Any]:
        """
        CEO duyệt yêu cầu.
        Nếu resume_execution=True và có executor_callback, chạy callback.
        """
        with self._lock:
            req = self._pending.get(request_id)
            if not req:
                return {"status": "error", "message": f"Request {request_id} not found"}
            if req.status != ApprovalStatus.PENDING:
                return {"status": "error", "message": f"Request already {req.status.value}"}

            req.status = ApprovalStatus.APPROVED
            req.reviewed_by = approved_by
            req.reviewed_at = datetime.utcnow()
            callback = req.executor_callback

        # Wake up waiter
        req.callback_event.set()

        # Edit Telegram message (remove buttons)
        try:
            if req.telegram_chat_id and req.telegram_message_id:
                from core.telegram_gateway import telegram_gateway
                await telegram_gateway._handle_hitl_callback_edit(
                    chat_id=req.telegram_chat_id,
                    message_id=req.telegram_message_id,
                    text=f"✅ *ĐÃ DUYỆT* bởi {approved_by}\nMã: `{request_id}` ({req.tool_name})",
                )
        except Exception:
            pass

        # Log audit
        log_security_audit(
            client_id=approved_by,
            action=f"HITL_APPROVED_{req.tool_name.upper()}",
            risk="APPROVED",
            status="APPROVED",
            details={"approval_id": request_id, "tool": req.tool_name},
        )

        # Execute callback if requested
        execution_result = None
        execution_error = None
        if resume_execution and callback:
            req.status = ApprovalStatus.EXECUTED
            try:
                if asyncio.iscoroutinefunction(callback):
                    execution_result = await callback()
                else:
                    # Run sync in thread pool
                    loop = asyncio.get_event_loop()
                    execution_result = await loop.run_in_executor(None, callback)
                logger.info("[HITLManager] Callback executed for %s", request_id)
            except Exception as e:
                execution_error = str(e)
                req.status = ApprovalStatus.EXECUTION_FAILED
                req.execution_error = execution_error
                logger.error("[HITLManager] Callback failed for %s: %s", request_id, e)

        req.execution_result = execution_result
        req.execution_error = execution_error

        # Notify Telegram result
        try:
            if execution_error:
                msg = f"⚠️ *DUYỆT NHƯNG THỰC THI LỖI*\nMã: `{request_id}`\nLỗi: {execution_error}"
            elif execution_result is not None:
                msg = f"✅ *ĐÃ DUYỆT VÀ THỰC THI*\nMã: `{request_id}` ({req.tool_name})"
            else:
                msg = f"✅ *ĐÃ DUYỆT* (không có callback thực thi)\nMã: `{request_id}`"
            await telegram_gateway.send_incident_alert(msg)
        except Exception:
            pass

        logger.info("[HITLManager] Request %s APPROVED by %s", request_id, approved_by)
        return {
            "status": req.status.value,
            "executed": execution_error is None and callback is not None,
            "execution_error": execution_error,
            "execution_result": execution_result,
            "message": f"Đã phê duyệt {request_id}",
        }

    async def reject(
        self,
        request_id: str,
        rejected_by: str,
        reason: str = "Từ chối bởi CEO",
    ) -> Dict[str, Any]:
        """CEO từ chối yêu cầu."""
        with self._lock:
            req = self._pending.get(request_id)
            if not req:
                return {"status": "error", "message": f"Request {request_id} not found"}
            if req.status != ApprovalStatus.PENDING:
                return {"status": "error", "message": f"Request already {req.status.value}"}

            req.status = ApprovalStatus.REJECTED
            req.reviewed_by = rejected_by
            req.reviewed_at = datetime.utcnow()
            req.rejection_reason = reason

        # Wake up waiter
        req.callback_event.set()

        # Edit Telegram message
        try:
            if req.telegram_chat_id and req.telegram_message_id:
                from core.telegram_gateway import telegram_gateway
                await telegram_gateway._handle_hitl_callback_edit(
                    chat_id=req.telegram_chat_id,
                    message_id=req.telegram_message_id,
                    text=f"⛔ *ĐÃ TỪ CHỐI* bởi {rejected_by}\nMã: `{request_id}` ({req.tool_name})\nLý do: {reason}",
                )
        except Exception:
            pass

        # Log audit
        log_security_audit(
            client_id=rejected_by,
            action=f"HITL_REJECTED_{req.tool_name.upper()}",
            risk="REJECTED",
            status="REJECTED",
            details={"approval_id": request_id, "tool": req.tool_name, "reason": reason},
        )

        # Notify
        try:
            await telegram_gateway.send_incident_alert(
                f"🛑 *HITL TỪ CHỐI*\n{rejected_by} đã hủy `{request_id}` ({req.tool_name}). {reason}"
            )
        except Exception:
            pass

        logger.info("[HITLManager] Request %s REJECTED by %s", request_id, rejected_by)
        return {
            "status": "rejected",
            "message": f"Đã từ chối {request_id}: {reason}",
        }

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_request(self, request_id: str) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._pending.get(request_id)

    def get_pending_list(self, requested_by: Optional[str] = None) -> List[ApprovalRequest]:
        """Lấy danh sách request đang chờ."""
        with self._lock:
            if requested_by:
                ids = self._by_requester.get(requested_by, [])
                return [self._pending[req_id] for req_id in ids if req_id in self._pending and self._pending[req_id].status == ApprovalStatus.PENDING]
            return [req for req in self._pending.values() if req.status == ApprovalStatus.PENDING]

    def get_requests_by_tool(self, tool_name: str) -> List[ApprovalRequest]:
        with self._lock:
            ids = self._by_tool.get(tool_name, [])
            return [self._pending[req_id] for req_id in ids if req_id in self._pending]


# ================================================================
# Module-level singleton
# ---------------------------------------------------------------------------
hitl_manager = HITLManager()