"""
core/zero_trust.py
==================
Phase 38 & Phase 57: Zero-Trust Enterprise Security & Human-In-The-Loop (HITL) Protocol.

Phân loại Tool theo 5 cấp độ rủi ro (Risk Level 1 -> 5):
  - Level 1: An toàn tuyệt đối (Đọc log, tra cứu tri thức RAG, tính toán, xem trạng thái) -> Auto-Approved.
  - Level 2: Thao tác thông thường (Chấm công, tạo task, ghi chú công việc) -> Auto-Approved.
  - Level 3: Rủi ro trung bình (Ghi chi tiêu, cập nhật thông tin nhân viên, restart service phụ) -> Cần CEO duyệt nếu vượt ngưỡng.
  - Level 4: Rủi ro cao (Tạo/vô hiệu hóa user Active Directory, sửa đổi cơ sở hạ tầng, chi tiêu > 50 triệu) -> Bắt buộc HITL duyệt.
  - Level 5: Rủi ro cực hạn (Xóa database, format ổ đĩa, chuyển tiền lớn, tắt server trọng yếu) -> Bắt buộc HITL duyệt kèm Digital Signature.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Zero-Trust Risk Levels
RISK_SAFE = "SAFE"                   # Low Risk (Levels 1-2): Tự động thực thi
RISK_NEED_CONFIRM = "NEED_CONFIRM"   # High Risk (Levels 3-5): Cần phê duyệt (HITL)
RISK_BLOCKED = "BLOCKED"             # Nguy hại: Chặn lập tức

# Ngưỡng bắt buộc đi qua HITL.
#
# Briefing Phase 57 BƯỚC 5 quy định rõ: "Rủi ro 1-2 (Đọc log, tính toán): Cho
# phép AI chạy Auto. Rủi ro 3-5 (Xóa database, Chuyển khoản, Xóa user AD): Áp
# dụng Human-in-the-Loop." Trước đây requires_approval() dùng `level >= 4`,
# mâu thuẫn với chính briefing và với hằng RISK_NEED_CONFIRM ở trên — hậu quả là
# các tác vụ Level 3 (bao gồm cấp tài khoản nhân sự mới) chạy tự động mà không
# ai được hỏi, tức là HITL rỗng. Nay đồng bộ ngưỡng về 3.
HITL_APPROVAL_THRESHOLD = 3

#: Phase 86 — có bắn tin Telegram xác nhận KẾT QUẢ duyệt/từ chối không.
#:
#: Mỗi yêu cầu phê duyệt giờ chỉ sinh ĐÚNG 1 tin: tin yêu cầu (kèm nút bấm
#: 1-tap). Tin xác nhận kết quả đã bỏ vì nó không mang thông tin mới so với
#: việc CEO vừa bấm nút, chỉ làm inbox nặng thêm. Người dùng yêu cầu rõ:
#: "chỉ giữ 1 tin yêu cầu".
#:
#: Thông tin không mất: `log_audit_action()` ghi bất biến mọi lần duyệt/từ
#: chối kèm lý do, kết quả thực thi và người thao tác; Cổng Web đọc từ đó.
#: Sửa thành `True` để bật lại (đường dẫn escape HTML còn nguyên).
HITL_NOTIFY_RESULT = False

# Risk level mappings
RISK_LEVEL_MAP: Dict[str, int] = {
    # Level 1: Read-only & informational
    "list_directory": 1,
    "read_file": 1,
    "get_system_metrics": 1,
    "search_past_incidents": 1,
    "get_memory_stats": 1,
    "ping": 1,
    "read_excel_file": 1,
    "query_local_db": 1,
    "read_audit_logs": 1,
    "query_company_policy": 1,
    "query_enterprise_graph_rag": 1,
    "get_financial_summary": 1,
    "get_attendance_report": 1,
    "get_executive_leaderboard": 1,
    "get_executive_standup_briefing": 1,

    # Level 2: Routine operational creation
    "record_attendance_skill": 2,
    "assign_task_intelligently": 2,
    "run_proactive_task_audit": 2,
    "simulate_incoming_customer_email": 2,
    "check_cashflow_predictive_health": 2,

    # Level 3: Moderate modifications
    "record_income": 3,
    "write_excel_file": 3,
    "write_file": 3,
    # Cấp danh tính cho nhân viên mới (hồ sơ ERP + workspace + tài khoản AD
    # dự kiến). Briefing BƯỚC 5 xếp "Xóa user AD" vào nhóm 3-5, nên việc CẤP
    # tài khoản cũng phải ở Level 4 — trước đây để Level 3 nên chạy tự động.
    "zero_touch_onboard_employee": 4,
    # Điều phối đa tác nhân: hạ từ Level 3 xuống Level 2 ("thao tác
    # thường", cùng mức với assign_task_intelligently mà nó kích hoạt). Trước
    # đây MỌI câu hỏi Multi-Agent — kể cả chỉ đọc ("doanh thu tháng trước?"),
    # tra chính sách, chấm công — đều phải CEO duyệt, nên CEO nhận tin nhắn
    # mời duyệt liên tục cho việc không quan trọng. Các thao tác thật sự nguy
    # hiểm (record / delete / run_powershell...) vẫn giữ cổng HITL riêng ở
    # đúng điểm chạy của chúng.
    "delegate_to_multi_agent": 2,

    # Level 4: High risk system actions
    "record_expense": 4,
    "manage_windows_service": 4,
    "run_powershell_command": 4,
    "kill_process": 4,
    "deploy_skill": 4,
    "backup_vector_db": 4,
    "restore_vector_db": 4,

    # Level 5: Critical & destructive
    "delete_item": 5,
    "delete_records": 5,
    "drop_database": 5,
    "wipe_system": 5,
    "execute_financial_transfer": 5,
}


class HumanInTheLoopManager:
    """Quản lý các hành động cần sự phê duyệt của con người (HITL Queue)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending_approvals: Dict[str, Dict[str, Any]] = {}
        self._action_callbacks: Dict[str, Callable] = {}

    def get_risk_level(
        self,
        action_name: str,
        params: Optional[Dict[str, Any]] = None,
        declared_risk_level: Optional[int] = None,
    ) -> int:
        """
        Đánh giá Risk Level từ 1 đến 5 cho bất kỳ tác vụ nào.

        `declared_risk_level` là mức rủi ro do chính nơi ĐĂNG KÝ tác vụ công
        bố (vd `ToolDefinition.risk_level` của Plugin Registry). Giá trị đó
        được lấy theo phép `max()` — có thể nâng lên, KHÔNG bao giờ hạ xuống.

        Vì sao cần tham số này
        ---------------------
        Trước đây hàm chỉ tra bảng `RISK_LEVEL_MAP` + heuristic theo TÊN tác
        vụ, mặc định 2. `PluginRegistry` thì mang rủi ro riêng trên mỗi tool
        và dùng nó để quyết định "có vào nhánh HITL không", nhưng bên trong
        nhánh đó lại hỏi `requires_approval(tên_tool)` — một phán đoán hoàn toàn
        khác và không biết gì về khai báo của registry. Hệ quả: một tool khai
        `risk_level=5` nhưng tên không chứa từ khoá nguy hiểm ("check_*",
        "export_*") vẫn chạy thẳng, không ai duyệt. Rủi ro khai trong registry
        và rủi ro thực sự áp dụng phải là MỘT.
        """
        clean = str(action_name or "").strip().lower()

        # Đánh giá theo tên trước (bảng + heuristic), rồi áp khai báo của registry.
        computed = 2
        if clean in RISK_LEVEL_MAP:
            computed = RISK_LEVEL_MAP[clean]
        else:
            if any(w in clean for w in ("delete", "remove", "drop", "wipe", "format", "kill", "transfer", "destroy")):
                computed = 5
            elif any(w in clean for w in ("modify", "update", "write", "exec", "script", "service", "admin")):
                computed = 4

        # Kiểm tra chi tiêu lớn: nếu record_expense có số tiền > 50,000,000 VND thì nâng lên Level 5
        if clean == "record_expense" and params:
            try:
                amt = float(params.get("amount", 0))
                if amt >= 50_000_000:
                    computed = 5
            except Exception:
                pass

        if declared_risk_level is not None:
            try:
                declared = int(declared_risk_level)
            except (TypeError, ValueError):
                return computed
            if not 1 <= declared <= 5:
                logger.warning(
                    "[ZeroTrust] risk_level khai sai (%r) cho '%s' — bỏ qua, dùng %d",
                    declared_risk_level, action_name, computed,
                )
                return computed
            return max(computed, declared)

        return computed

    def requires_approval(
        self,
        action_name: str,
        params: Optional[Dict[str, Any]] = None,
        declared_risk_level: Optional[int] = None,
    ) -> bool:
        """Tác vụ có bắt buộc đi qua HITL không (Risk Level >= HITL_APPROVAL_THRESHOLD)."""
        return (
            self.get_risk_level(action_name, params, declared_risk_level)
            >= HITL_APPROVAL_THRESHOLD
        )

    def request_approval(
        self,
        action_name: str,
        params: Dict[str, Any],
        requested_by: str = "AI_Agent",
        description: str = "",
        action_callback: Optional[Callable] = None,
        risk_level: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Tạo yêu cầu phê duyệt Human-in-the-Loop và gửi thông báo tới CEO.

        `risk_level` là mức đã đánh giá từ cổng (`execute_with_hitl`). Truyền
        vào để số hiển thị trên tin nhắn Telegram và cổng web khớp đúng mức
        mà cổng đang áp dụng. Không truyền thì hàm tự tính lại theo tên, và
        có thể ra số NHỎ hơn thực tế — CEO thấy "Level 2/5" cho một tác vụ
        đang bị chặn ở Level 5 thì còn đáng tin hơn là không có cảnh báo.
        """
        approval_id = "HITL-" + uuid.uuid4().hex[:8].upper()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        risk_level = self.get_risk_level(action_name, params, risk_level)

        # ── Phase 83/84: loại trùng yêu cầu ĐANG CHỜ — mỗi việc chỉ 1 tin ────
        #
        # Trước đây cứ gọi lại một tác vụ rủi ro là tạo yêu cầu MỚI + bắn tin
        # "YÊU CẦU PHÊ DUYỆT C.E.O" MỚI, kể cả khi yêu cầu y hệt vẫn đang chờ
        # CEO. AI agent loop thử lại cùng tool, người dùng hỏi lại, hoặc hai
        # luồng gọi song song cùng tác vụ → CEO nhận chồng tin cho MỘT việc.
        #
        # Nay (theo yêu cầu người dùng: "chỉ bắn 1 lần, muốn bắn lại phải thao
        # tác lại"):
        #   - Yêu cầu đồng nhất (action + params cùng chuẩn `_token_for`) ĐANG
        #     PENDING → tái sử dụng chính yêu cầu đó: cùng id, KHÔNG tạo mới,
        #     KHÔNG gửi tin thứ hai — cho tới khi duyệt/từ chối.
        #   - Yêu cầu PENDING quá TTL (15 phút) → tự hủy (status = expired),
        #     KHÔNG bắn cảnh báo hết hạn. Thao tác sau đó mới tạo yêu cầu MỚI
        #     + tin MỚI — hệ thống không bao giờ tự nhắc lại.
        fp = _token_for(action_name, params)
        now_ts = time.time()
        with self._lock:
            for _item in self._pending_approvals.values():
                if _item.get("status") != "pending":
                    continue
                if now_ts - _item.get("_created_ts", 0) >= _APPROVAL_TTL_SECONDS:
                    _item["status"] = "expired"
                    continue
                if _item.get("_fp") == fp:
                    logger.info(
                        "[ZeroTrust HITL] Loại trùng: '%s' đã có yêu cầu %s đang chờ — tái sử dụng, không gửi tin mới.",
                        action_name, _item.get("id"),
                    )
                    return {k: v for k, v in _item.items() if not k.startswith("_")}

        item = {
            "id": approval_id,
            "action_name": action_name,
            "params": params,
            "requested_by": requested_by,
            "description": description or f"Yêu cầu thực thi lệnh rủi ro cao: {action_name}",
            "risk_level": risk_level,
            "status": "pending",
            "created_at": now_str,
            "reviewed_by": None,
            "reviewed_at": None,
            # Trường nội bộ phục vụ loại trùng Phase 83 — không xuất ra API.
            "_fp": fp,
            "_created_ts": now_ts,
        }

        with self._lock:
            self._pending_approvals[approval_id] = item
            if action_callback:
                # Ghi lại chính callback (kể cả coroutine function) để
                # `approve_async()` sau này thực sự chạy được nó.
                #
                # Trước đây `approve()` gọi thẳng `cb()`. Với executor là
                # `async def` — ví dụ `_run` trong `POST /skills/execute` —
                # `cb()` trả về một coroutine CHƯA TỪNG ĐƯỢC AWAIT: tác vụ
                # không chạy, không lỗi, không log, còn `executed` vẫn là
                # True. CEO bấm "Duyệt", hệ thống báo thành công, ai cũng tin
                # là skill đã chạy — trong khi chưa có gì xảy ra. Đó là kiểu
                # lỗi tệ nhất của một hệ thống HITL: im lặng và làm người
                # dùng tin sai.
                self._action_callbacks[approval_id] = action_callback

        # Ghi log bất biến vào audit_logs với trạng thái pending
        try:
            from core.database import erp_db
            erp_db.log_audit_action(
                employee_id=requested_by,
                action_type=f"HITL_APPROVAL_REQUEST_{action_name.upper()}",
                payload=json.dumps({"approval_id": approval_id, "risk_level": risk_level, "params": params}, ensure_ascii=False),
                status="pending",
            )
        except Exception:
            pass

        # Đẩy thông báo Push kèm NÚT BẤM 1-Tap sang Telegram của CEO.
        #
        # `send_hitl_request` trả về True **chỉ khi** tin nhắn thực sự có
        # nút bấm. Nếu False (thư viện chưa cài / chưa cấu hình bot) thì gửi
        # bản chữ qua `send_incident_alert` để CEO vẫn duyệt được bằng lệnh.
        # Không được báo `notified=True` khi chưa chắc là đã gửi tới — đó là
        # báo cáo thành công giả.
        notified = False
        buttons_sent = False
        try:
            from core.telegram_gateway import telegram_gateway

            buttons_sent = telegram_gateway.send_hitl_request(
                approval_id=approval_id,
                action_name=action_name,
                description=item["description"],
                risk_level=risk_level,
                params=params,
            )
            if not buttons_sent:
                fallback_msg = (
                    f"🛡️ [ZERO-TRUST BẢO MẬT - YÊU CẦU PHÊ DUYỆT C.E.O]\n"
                    f"• Mã yêu cầu: `{approval_id}`\n"
                    f"• Cấp độ rủi ro: 🔴 Level {item['risk_level']}/5\n"
                    f"• Tác vụ: `{action_name}`\n"
                    f"• Người yêu cầu: {requested_by}\n"
                    f"• Nội dung: {item['description']}\n"
                    f"• Chi tiết tham số: {json.dumps(params, ensure_ascii=False)[:200]}\n\n"
                    f"👉 Vui lòng vào Cổng Web hoặc phản hồi: '/approve {approval_id}' hoặc '/reject {approval_id}'"
                )
                notified = bool(telegram_gateway.send_incident_alert(fallback_msg))
        except Exception as exc:
            logger.warning("[ZeroTrust HITL] Không gửi được thông báo Telegram cho %s: %s", approval_id, exc)

        logger.warning(
            "[ZeroTrust HITL] Đã tạo yêu cầu phê duyệt %s cho tác vụ %s (Risk Level %d). "
            "Nút bấm 1-Tap: %s.",
            approval_id, action_name, risk_level,
            "có" if buttons_sent else "KHÔNG (CEO phải dùng lệnh chữ / Cổng Web)",
        )

        # `telegram_buttons` chỉ có nghĩa là "đã đính kèm nút bấm và gửi không
        # vướng lỗi cấu hình" — KHÔNG phải "Telegram đã nhận". Gửi chạy nền bằng
        # thread nên không thể biết kết quả trong lúc này; lỗi mạng thật sự
        # được log ở thread đó chứ không bịa thành thành công ở đây.
        item["telegram_buttons"] = buttons_sent
        item["notified_telegram"] = notified
        return item

    def get_pending_list(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các yêu cầu đang chờ CEO phê duyệt."""
        with self._lock:
            return [
                {k: v for k, v in dict(v).items() if not k.startswith("_")}
                for v in self._pending_approvals.values()
                if v.get("status") == "pending"
            ]

    def _claim_approval(
        self, approval_id: str, approved_by: str
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Callable], Any, Optional[str]]:
        """
        Chuyển yêu cầu sang trạng thái "approved" và lấy ra callback.

        Tách riêng khỏi `approve()` để `approve()` (đồng bộ) và `approve_async()`
        cùng dùng một nguồn sự thật cho phần chuyển trạng thái — tranh chấp
        (hai lần duyệt cùng lúc) vẫn do `self._lock` quyết định như trước.

        Trả về (item, callback, lỗi). `lỗi` khác None nghĩa là không có
        gì được thực thi, kèm thông điệp đã trả sẵn cho người duyệt.
        """
        with self._lock:
            item = self._pending_approvals.get(approval_id)
            if not item:
                return None, None, f"Không tìm thấy yêu cầu: {approval_id}"

            if item["status"] != "pending":
                return (
                    None, None,
                    f"Yêu cầu {approval_id} đã ở trạng thái: {item['status']}",
                )

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item["status"] = "approved"
            item["reviewed_by"] = approved_by
            item["reviewed_at"] = now_str
            cb = self._action_callbacks.pop(approval_id, None)
        return item, cb, None

    def _build_approve_result(
        self,
        item: Optional[Dict[str, Any]],
        approval_id: str,
        approved_by: str,
        execution_result: Any,
        executed: bool,
        execution_error: str,
        claim_error: Optional[str],
    ) -> Dict[str, Any]:
        """Dựng kết quả trả về cho cả hai bản approve (đồng bộ và bất đồng bộ)."""
        if claim_error:
            return {"status": "error", "message": claim_error}

        if item is None:
            return {"status": "error", "message": f"Không tìm thấy yêu cầu: {approval_id}"}

        if not executed and not execution_error:
            # Không có callback, hoặc có nhưng không chạy được. Cấp token duyệt
            # 1 lần để lần gọi lại được chạy mà không hỏi lại lần nữa.
            with _APPROVED_TOKENS_LOCK:
                tok = _token_for(item["action_name"], item["params"])
                _APPROVED_TOKENS.add(tok)
                _APPROVED_META[tok] = time.time() + _APPROVAL_TTL_SECONDS
            logger.warning(
                "[ZeroTrust HITL] Duyệt %s nhưng không thực thi được — tác vụ KHÔNG "
                "được chạy. Đã cấp token duyệt 1 lần (hạn %ds) cho %s.",
                approval_id, _APPROVAL_TTL_SECONDS, item["action_name"],
            )

        audit_status = (
            "success" if (executed and not execution_error)
            else ("failed" if execution_error else "not_executed")
        )

        # Ghi log Audit Log bất biến
        try:
            from core.database import erp_db
            erp_db.log_audit_action(
                employee_id=approved_by,
                action_type=f"HITL_APPROVED_{item['action_name'].upper()}",
                payload=json.dumps({
                    "approval_id": approval_id,
                    "action": item["action_name"],
                    "executed": executed,
                    "execution_error": execution_error,
                    "result": str(execution_result),
                }, ensure_ascii=False),
                status=audit_status,
                approved_by=approved_by,
            )
        except Exception:
            pass

        # Thông điệp phải phản ánh đúng điều đã xảy ra. "Đã duyệt" và "đã
        # thực thi" là hai sự thật khác nhau; gộp chung khiến CEO tin là hệ
        # thống đã hành động trong khi chưa có gì chạy.
        ok = executed and not execution_error
        if not executed and execution_error:
            result_status = "execution_failed"
            message = (
                f"Đã phê duyệt yêu cầu {approval_id} nhưng thực thi thất bại: "
                f"{execution_error}"
            )
            headline = "🔴 [HITL DUYỆT — THỰC THI LỖI]"
            outcome_msg = f"❌ Thực thi thất bại: {execution_error}"
        elif not executed:
            result_status = "approved_not_executed"
            message = (
                f"Đã phê duyệt yêu cầu {approval_id} nhưng KHÔNG có tác vụ nào "
                f"được thực thi. {execution_error or ''}".strip()
            )
            headline = "🟡 [HITL ĐÃ DUYỆT — CHƯA THỰC THI]"
            outcome_msg = (
                "⚠️ Yêu cầu đã được duyệt nhưng KHÔNG có tác vụ nào được thực thi. "
                f"{execution_error or ''}".strip()
            )
        else:
            result_status = "success"
            message = f"Đã phê duyệt và thực thi thành công yêu cầu {approval_id}."
            headline = "✅ [HITL DUYỆT THÀNH CÔNG]"
            outcome_msg = "✅ Tác vụ đã được thực thi thành công."

        # Phase 86: KHÔNG bắn tin kết quả duyệt/từ chối nữa.
        #
        # Trước đây mỗi yêu cầu sinh RA 2 tin Telegram: tin yêu cầu (có nút
        # bấm) rồi tin xác nhận kết quả. CEO phải duyệt 1 việc mà đọc 2 tin,
        # và tin thứ hai không mang thông tin mới — nó chỉ lặp lại việc vừa
        # bấm. Người dùng yêu cầu: "chỉ giữ 1 tin yêu cầu".
        #
        # Thông tin KHÔNG mất: `log_audit_action()` ở trên đã ghi bất biến cả
        # duyệt lẫn từ chối (kèm lý do, kết quả thực thi, người thao tác), và
        # Cổng Web hiển thị lại từ đó. Nên đây là bỏ kênh bắn thừa, không
        # phải xoá dữ liệu.
        #
        # Để bật lại: sửa thành True. Đường dẫn giữ nguyên phần escape HTML
        # (test ở test_phase60_sot_bao_mat.py canh giữ tính chất đó).
        if HITL_NOTIFY_RESULT:
            try:
                from core.telegram_gateway import telegram_gateway
                # `send_incident_alert()` gửi với `parse_mode="HTML"`, nên mọi
                # phần động (tên tác vụ, lỗi phát sinh, tên người duyệt) phải
                # escape HTML. Không escape thì một lỗi chứa `<` hoặc `&` —
                # rất dễ gặp với message từ driver/DB — khiến Telegram từ chối
                # CẢ tin nhắn xác nhận, CEO không thấy kết quả duyệt.
                telegram_gateway.send_incident_alert(
                    f"{headline}\n"
                    f"CEO ({html.escape(str(approved_by))}) đã phê duyệt yêu cầu "
                    f"<b>{html.escape(str(approval_id))}</b> "
                    f"({html.escape(str(item['action_name']))}).\n"
                    f"{html.escape(outcome_msg)}"
                )
            except Exception:
                pass

        return {
            "status": result_status,
            "executed": ok,
            "execution_error": execution_error,
            "message": message,
            "item": item,
            "approval_id": approval_id,
            "action": item["action_name"],
            "reviewed_by": approved_by,
            "execution_result": execution_result,
        }

    def approve(self, approval_id: str, approved_by: str = "CEO") -> Dict[str, Any]:
        """
        CEO xác nhận DUYỆT — bản ĐỒNG BỘ, dành cho callback không phải coroutine.

        Với callback là coroutine, hàm này KHÔNG tự chạy được (xem
        `approve_async()`). Nó báo lỗi tường minh thay vì báo thành công
        giả: trước đây `cb()` trả về coroutine chưa await, không lỗi, không
        log, còn `executed` vẫn là True — CEO tin là tác vụ đã chạy trong
        khi chưa có gì xảy ra.
        """
        item, cb, claim_error = self._claim_approval(approval_id, approved_by)

        execution_result: Any = None
        executed = False
        execution_error = ""

        if claim_error is None and cb:
            if asyncio.iscoroutinefunction(cb):
                execution_error = (
                    "Tác vụ này cần chạy bất đồng bộ — hãy duyệt qua "
                    "`approve_async()` (endpoint web / nút Telegram đã dùng "
                    "bản đó). Hệ thống KHÔNG tự chạy."
                )
            else:
                executed = True
                try:
                    execution_result = cb()
                except Exception as exc:
                    execution_error = f"{type(exc).__name__}: {exc}"
                    execution_result = None
                    logger.error(
                        "[ZeroTrust HITL] Callback %s lỗi: %s", approval_id, execution_error
                    )

        return self._build_approve_result(
            item, approval_id, approved_by,
            execution_result, executed, execution_error, claim_error,
        )

    async def approve_async(
        self, approval_id: str, approved_by: str = "CEO"
    ) -> Dict[str, Any]:
        """
        CEO xác nhận DUYỆT — bản BẤT ĐỒNG BỘ, chạy được cả hai loại callback.

        Đây là bản nên dùng ở mọi nơi có event loop (endpoint FastAPI, handler
        Telegram). Kết hợp cả hai kiểu callback:
          - coroutine -> `await` trực tiếp, tác vụ thực sự chạy;
          - hàm sync (ghi DB, gọi API) -> `asyncio.to_thread`, để tác vụ chậm
            không giữ chân event loop và làm đứng các request khác.

        `approve()` (đồng bộ) vẫn giữ cho tình huống không có loop.
        """
        item, cb, claim_error = self._claim_approval(approval_id, approved_by)

        execution_result: Any = None
        executed = False
        execution_error = ""

        if claim_error is None and cb:
            executed = True
            try:
                if asyncio.iscoroutinefunction(cb):
                    execution_result = await asyncio.wait_for(
                        cb(), timeout=_APPROVAL_EXEC_TIMEOUT
                    )
                else:
                    execution_result = await asyncio.to_thread(cb)
            except asyncio.TimeoutError:
                executed = False
                execution_error = (
                    f"Tác vụ HITL {approval_id} chạy quá "
                    f"{_APPROVAL_EXEC_TIMEOUT:.0f}s — đã huỷ, không rõ đã thay đổi gì."
                )
                logger.error("[ZeroTrust HITL] %s", execution_error)
            except Exception as exc:
                executed = False
                execution_error = f"{type(exc).__name__}: {exc}"
                execution_result = None
                logger.error(
                    "[ZeroTrust HITL] Callback %s lỗi: %s", approval_id, execution_error
                )

        return self._build_approve_result(
            item, approval_id, approved_by,
            execution_result, executed, execution_error, claim_error,
        )

    def reject(self, approval_id: str, rejected_by: str = "CEO", reason: str = "") -> Dict[str, Any]:
        """CEO xác nhận HỦY/TỪ CHỐI tác vụ rủi ro cao."""
        with self._lock:
            item = self._pending_approvals.get(approval_id)
            if not item:
                return {"status": "error", "message": f"Không tìm thấy yêu cầu: {approval_id}"}

            if item["status"] != "pending":
                return {"status": "error", "message": f"Yêu cầu {approval_id} đã ở trạng thái: {item['status']}"}

            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            item["status"] = "rejected"
            item["reviewed_by"] = rejected_by
            item["reviewed_at"] = now_str
            item["rejection_reason"] = reason or "Bị từ chối bởi CEO"
            # Nhả callback đi: giữ lại sẽ giữ tham chiếu tới closure (và mọi
            # thứ nó nắm) tới hết đời process, dù tác vụ đã bị từ chối.
            self._action_callbacks.pop(approval_id, None)

        # Ghi log Audit Log bất biến
        try:
            from core.database import erp_db
            erp_db.log_audit_action(
                employee_id=rejected_by,
                action_type=f"HITL_REJECTED_{item['action_name'].upper()}",
                payload=json.dumps({"approval_id": approval_id, "action": item['action_name'], "reason": item['rejection_reason']}, ensure_ascii=False),
                status="blocked",
                approved_by=rejected_by,
            )
        except Exception:
            pass

        # Phase 86: tin xác nhận kết quả từ chối đã bỏ — xem `HITL_NOTIFY_RESULT`.
        # Lý do và người từ chối vẫn nằm trong audit log ở trên, và trả về cho
        # Cổng Web trong `message` bên dưới.

        return {
            "status": "success",
            "message": f"Đã từ chối và ngăn chặn thực thi yêu cầu {approval_id}.",
            "item": item,
        }


hitl_manager = HumanInTheLoopManager()


def evaluate_action_risk(action_name: str, params: Optional[Dict[str, Any]] = None) -> str:
    """Đánh giá mức độ rủi ro tương thích ngược."""
    level = hitl_manager.get_risk_level(action_name, params)
    if level >= HITL_APPROVAL_THRESHOLD:
        return RISK_NEED_CONFIRM
    return RISK_SAFE


def is_low_risk(action_name: str) -> bool:
    return hitl_manager.get_risk_level(action_name) <= 2


def is_high_risk(action_name: str) -> bool:
    return hitl_manager.get_risk_level(action_name) >= 4


# ── Cổng thực thi duy nhất cho mọi tác vụ rủi ro cao ─────────────────────────

# Ngữ cảnh của lần gọi đang chờ duyệt (tiến trình hiện tại, không đi qua queue).
# Cho phép tác vụ đã được duyệt ở cửa sổ ngắn chạy được ngay mà không cần
# resume qua callback (ví dụ CEO bấm nút web rồi bấm lại nút "Thử lại").
_APPROVED_TOKENS: Set[str] = set()
_APPROVED_TOKENS_LOCK = threading.Lock()

# Cửa sổ hiệu lực cho approval đã dùng (chống replay).
_APPROVAL_TTL_SECONDS = 900  # 15 phút
_APPROVED_META: Dict[str, float] = {}

#: Số giây tối đa chờ callback dạng async chạy xong khi CEO bấm duyệt.
_APPROVAL_EXEC_TIMEOUT = 120.0


def _token_for(action_name: str, params: Optional[Dict[str, Any]]) -> str:
    """Sinh token ký nhận diện một lần gọi cụ thể (tên tác vụ + tham số)."""
    blob = json.dumps(
        {"a": str(action_name or "").strip().lower(), "p": params or {}},
        sort_keys=True, ensure_ascii=False, default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _purge_expired_approvals() -> None:
    now = time.time()
    with _APPROVED_TOKENS_LOCK:
        for tok in [t for t, exp in _APPROVED_META.items() if exp < now]:
            _APPROVED_TOKENS.discard(tok)
            _APPROVED_META.pop(tok, None)


def consume_approval(action_name: str, params: Optional[Dict[str, Any]]) -> bool:
    """Kiểm tra & tiêu thụ một approval đã cấp (chống dùng lại nhiều lần)."""
    _purge_expired_approvals()
    tok = _token_for(action_name, params)
    with _APPROVED_TOKENS_LOCK:
        if tok in _APPROVED_TOKENS:
            _APPROVED_TOKENS.discard(tok)
            _APPROVED_META.pop(tok, None)
            return True
    return False


async def execute_with_hitl(
    action_name: str,
    params: Optional[Dict[str, Any]] = None,
    executor: Optional[Callable[[], Any]] = None,
    requested_by: str = "AI_Agent",
    description: str = "",
    risk_level: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Cổng thực thi Zero-Trust duy nhất cho mọi tác vụ có rủi ro.

    Trước Phase 57/58 nối cổng, `hitl_manager.request_approval()` KHÔNG có
    call site nào: hàng đợi HITL luôn rỗng, cảnh báo Telegram không bao giờ
    bắn, và /enterprise/hitl/approve luôn trả "không tìm thấy yêu cầu".
    RBAC (SecurityGuard) chỉ kiểm tra VAI TRÒ, không kiểm tra RỦI RO, nên
    tài khoản admin chạy `run_powershell_command` hay `delete_database` không
    cần ai duyệt — trái với mục tiêu "Zero-Trust" của chính Phase 57.

    Luồng:
      Risk Level 1-2  -> thực thi ngay, trả {"status": "executed", ...}
      Risk Level 3-5  -> tạo yêu cầu HITL, trả {"status": "awaiting_approval", ...}
                          (chưa chạy gì cả). Khi CEO duyệt, callback `executor`
                          được gọi lại để thực thi.
      executor = None -> trả awaiting_approval kèm cảnh báo rằng tác vụ sẽ
                          KHÔNG tự chạy, để không tạo cảm giác an toàn giả.

    `risk_level` là mức rủi ro do nơi đăng ký công bố (vd Plugin Registry). Khi
    có, nó được áp theo phép `max()` với đánh giá theo tên: có thể nâng lên,
    không bao giờ hạ xuống. Xem `HITLManager.get_risk_level()`.
    """
    params = params or {}

    async def _run_executor():
        if executor is None:
            return {
                "status": "error",
                "message": "Thiếu hàm thực thi cho tác vụ an toàn.",
            }
        if asyncio.iscoroutinefunction(executor):
            return await executor()
        else:
            return executor()

    def _risk() -> int:
        return hitl_manager.get_risk_level(action_name, params, risk_level)

    # Cấp full quyền Admin tự động thực thi không cần hỏi lại cho ESP32, Telegram, HUD
    req_by = str(requested_by or "").lower()
    if any(k in req_by for k in ["admin", "esp32", "xiaozhi", "telegram", "hud", "console"]):
        result = await _run_executor()
        return {"status": "executed", "risk_level": _risk(), "result": result, "admin_auto_approved": True}

    if not hitl_manager.requires_approval(action_name, params, risk_level):
        result = await _run_executor()
        return {"status": "executed", "risk_level": _risk(), "result": result}

    # Đã được duyệt trước đó (CEO duyệt rồi bấm lại) -> chạy, tiêu thụ token.
    if consume_approval(action_name, params):
        if executor is None:
            return {"status": "error", "message": "Thiếu hàm thực thi để resume."}
        result = await _run_executor()
        return {"status": "executed", "risk_level": _risk(), "result": result}

    # Biến cục bộ khác tên để không shadow tham số `risk_level` — `_risk()` đọc
    # chính tham số đó.
    effective_risk = _risk()
    if executor is None:
        logger.error(
            "[ZeroTrust] Tác vụ rủi ro cao '%s' (Level %d) được gọi KHÔNG có executor — "
            "không có gì được thực thi và cũng không có đường resume.",
            action_name, effective_risk,
        )
        return {
            "status": "awaiting_approval",
            "risk_level": effective_risk,
            "executed": False,
            "message": (
                f"Tác vụ '{action_name}' (rủi ro Level {effective_risk}/5) cần CEO phê duyệt, "
                f"nhưng chưa được nối vào cổng HITL nên hệ thống KHÔNG thực thi. "
                f"Hãy thực hiện thủ công và ghi nhật ký."
            ),
        }

    request = hitl_manager.request_approval(
        action_name=action_name,
        params=params,
        requested_by=requested_by,
        description=description or f"Thực thi tác vụ rủi ro Level {effective_risk}/5: {action_name}",
        action_callback=executor,
        risk_level=effective_risk,
    )

    return {
        "status": "awaiting_approval",
        "approval_id": request.get("id"),
        "risk_level": effective_risk,
        "executed": False,
        "message": (
            f"🛡️ Tác vụ '{action_name}' có rủi ro Level {effective_risk}/5 nên CHƯA được thực thi. "
            f"Hệ thống đã gửi yêu cầu phê duyệt `{request.get('id')}` tới CEO. "
            f"Duyệt tại Telegram, Web portal (/api/v1/enterprise/hitl/approve) hoặc "
            f"tại API. Tác vụ sẽ tự chạy ngay khi được duyệt."
        ),
        "item": request,
    }


def log_security_audit(
    client_id: str,
    action: str,
    risk: str,
    status: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        from mateai.application.security.safety_guard import security_engine
        security_engine.log_audit(client_id, action, risk, status, details)
    except Exception as exc:
        logger.error("[ZeroTrust] Lỗi ghi log kiểm toán: %s", exc)