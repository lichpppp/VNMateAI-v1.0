"""
core/skills/proactive_manager.py
================================
Phase 56: Proactive Agentic Engine & Auto-Delegation.
Hệ thống tự động rà soát tiến độ, đôn đốc công việc quá hạn/sắp đến hạn,
và tự động phân công nhiệm vụ thông minh theo vị trí / chuyên môn nhân sự.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from mateai.infrastructure.database.erp_database import erp_db
from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


class ProactiveManager:
    """Quản lý các chu kỳ đôn đốc công việc tự chủ (Virtual C.O.O Standup)."""

    def __init__(self) -> None:
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_audit_date: Optional[str] = None
        self._last_audit_slot: Optional[str] = None
        self._audit_history: List[Dict[str, Any]] = []

    def start(self) -> None:
        """Khởi động luồng giám sát đôn đốc chạy ngầm."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="proactive-manager-loop", daemon=True)
        self._thread.start()
        logger.info("[ProactiveManager] Đã khởi động Agentic Proactive Engine (Cron 08:00 & 16:00).")

    def stop(self) -> None:
        """Dừng luồng đôn đốc."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        logger.info("[ProactiveManager] Đã dừng Proactive Engine.")

    def _loop(self) -> None:
        """Vòng lặp kiểm tra thời gian thực hiện rà soát vào 08:00 và 16:00 mỗi ngày."""
        while self._running:
            try:
                now = datetime.now()
                hour = now.hour
                minute = now.minute
                today_str = now.strftime("%Y-%m-%d")

                # Slot 1: 08:00 - 08:15 sáng
                # Slot 2: 16:00 - 16:15 chiều
                current_slot = None
                if hour == 8 and minute <= 15:
                    current_slot = "morning_08"
                elif hour == 16 and minute <= 15:
                    current_slot = "afternoon_16"

                if current_slot and (self._last_audit_date != today_str or self._last_audit_slot != current_slot):
                    self._last_audit_date = today_str
                    self._last_audit_slot = current_slot
                    logger.info("[ProactiveManager] Tới khung giờ %s, bắt đầu quét đôn đốc tự động...", current_slot)
                    self.execute_task_audit_sync(trigger_source=f"cron_{current_slot}")

            except Exception as exc:
                logger.error("[ProactiveManager] Lỗi vòng lặp đôn đốc: %s", exc)

            # Ngủ 60 giây giữa các lần kiểm tra giờ
            time.sleep(60.0)

    def execute_task_audit_sync(self, trigger_source: str = "manual") -> Dict[str, Any]:
        """Thực hiện quét các task sắp đến hạn hoặc quá hạn và gửi tin nhắn đôn đốc."""
        now = datetime.now()
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        overdue_tasks: List[Dict[str, Any]] = []
        upcoming_tasks: List[Dict[str, Any]] = []

        with erp_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT t.id, t.dept_id, t.assignee_id,
                       COALESCE(t.title, 'Không có tiêu đề') AS title,
                       t.status, t.due_date,
                       e.name AS assignee_name, e.email AS assignee_email, e.phone AS assignee_phone,
                       d.name AS dept_name
                FROM tasks t
                LEFT JOIN employees e ON t.assignee_id = e.id
                LEFT JOIN departments d ON t.dept_id = d.id
                WHERE t.status IN ('pending', 'in_progress')
                  AND t.due_date IS NOT NULL AND t.due_date != '';
                """
            )
            rows = [dict(r) for r in cursor.fetchall()]

        for task in rows:
            due_str = task["due_date"].strip()
            try:
                # Thử parse các định dạng ngày phổ biến
                due_dt = None
                for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        due_dt = datetime.strptime(due_str, fmt)
                        break
                    except ValueError:
                        continue

                if not due_dt:
                    continue

                if due_dt < now:
                    days_late = (now - due_dt).days or 1
                    task["days_late"] = days_late
                    overdue_tasks.append(task)
                elif due_dt <= (now + timedelta(hours=24)):
                    hours_left = max(1, int((due_dt - now).total_seconds() // 3600))
                    task["hours_left"] = hours_left
                    upcoming_tasks.append(task)
            except Exception:
                continue

        reminders_sent: List[Dict[str, Any]] = []

        # Xây dựng và gửi tin nhắn đôn đốc
        for t in overdue_tasks:
            emp_name = t.get("assignee_name") or "Anh/Chị"
            title = t["title"]
            days = t.get("days_late", 1)
            msg = (
                f"⏰ [VN-MateAI Đôn đốc Tiến độ]\n"
                f"Chào {emp_name}, công việc '{title}' của phòng {t.get('dept_name', 'Ban')} "
                f"đang bị chậm {days} ngày (Hạn chót: {t['due_date']}).\n"
                f"Anh/chị có cần em hỗ trợ tự động xuất data, lập báo cáo hoặc điều phối thêm nguồn lực kỹ thuật không ạ?"
            )
            self._dispatch_reminder(msg, t)
            reminders_sent.append({"task_id": t["id"], "type": "overdue", "message": msg, "target": emp_name})

        for t in upcoming_tasks:
            emp_name = t.get("assignee_name") or "Anh/Chị"
            title = t["title"]
            hours = t.get("hours_left", 12)
            msg = (
                f"⏳ [VN-MateAI Nhắc Hẹn Task]\n"
                f"Chào {emp_name}, công việc '{title}' sẽ đến hạn trong khoảng {hours} giờ nữa ({t['due_date']}).\n"
                f"Chúc anh/chị hoàn thành tốt, hãy báo em khi cần hỗ trợ nhé!"
            )
            self._dispatch_reminder(msg, t)
            reminders_sent.append({"task_id": t["id"], "type": "upcoming", "message": msg, "target": emp_name})

        summary_result = {
            "status": "success",
            "timestamp": now_str,
            "trigger_source": trigger_source,
            "total_overdue": len(overdue_tasks),
            "total_upcoming": len(upcoming_tasks),
            "reminders_dispatched": len(reminders_sent),
            "details": reminders_sent,
        }

        self._audit_history.append(summary_result)
        if len(self._audit_history) > 50:
            self._audit_history.pop(0)

        # Ghi log bất biến vào audit_logs
        erp_db.log_audit_action(
            employee_id="virtual_coo",
            action_type="PROACTIVE_TASK_AUDIT",
            payload=f"Overdue={len(overdue_tasks)}, Upcoming={len(upcoming_tasks)}, Trigger={trigger_source}",
            status="success",
        )

        logger.info(
            "[ProactiveManager] Hoàn thành rà soát: %d task quá hạn, %d task sắp tới hạn.",
            len(overdue_tasks),
            len(upcoming_tasks),
        )
        return summary_result

    def _dispatch_reminder(self, message: str, task: Dict[str, Any]) -> None:
        """Gửi thông điệp đôn đốc tới Telegram hoặc kênh thông báo."""
        try:
            from core.telegram_gateway import telegram_gateway
            telegram_gateway.send_incident_alert(message)
        except Exception as exc:
            logger.debug("[ProactiveManager] Telegram alert skip: %s", exc)


proactive_manager = ProactiveManager()


# ── AI Skills Export ─────────────────────────────────────────────────────────

@export_skill(
    name="assign_task_intelligently",
    description="Tự động phân tích yêu cầu công việc (VD: 'Lên kế hoạch sinh nhật công ty', 'Kiểm tra backup server'), tìm nhân viên có chuyên môn phù hợp nhất trong cơ sở dữ liệu (HR, IT, Kế toán...), gán việc, đặt deadline và gửi thông báo.",
    parameters_schema={
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Nội dung yêu cầu công việc cần phân bổ (ví dụ: 'Chuẩn bị quà và hoa cho sinh nhật công ty tuần sau').",
            },
            "deadline_days": {
                "type": "integer",
                "description": "Số ngày deadline tính từ hôm nay (mặc định: 3 ngày).",
                "default": 3,
            },
            "preferred_role_or_dept": {
                "type": "string",
                "description": "Gợi ý vai trò hoặc phòng ban: 'hr', 'it', 'kế toán', 'admin' (nếu có).",
                "default": "",
            },
        },
        "required": ["description"],
    },
)
def assign_task_intelligently(
    description: str,
    deadline_days: int = 3,
    preferred_role_or_dept: str = "",
) -> Dict[str, Any]:
    """Phân công công việc tự động dựa trên phân tích từ khóa và cơ sở dữ liệu nhân sự."""
    try:
        desc_lower = description.lower()
        role_hint = preferred_role_or_dept.strip().lower()

        # Suy luận phòng ban & vai trò phù hợp nếu chưa có gợi ý rõ ràng
        if not role_hint:
            if any(k in desc_lower for k in ("sinh nhật", "du lịch", "liên hoan", "tuyển dụng", "hợp đồng nhân viên", "bảo hiểm", "nghỉ phép", "chấm công")):
                role_hint = "hr"
            elif any(k in desc_lower for k in ("server", "máy chủ", "backup", "mạng", "wifi", "it", "phần mềm", "cài win", "sql", "active directory")):
                role_hint = "it_support"
            elif any(k in desc_lower for k in ("chi phí", "sổ quỹ", "hóa đơn", "thuế", "chuyển khoản", "tiền lương", "kế toán", "ngân sách")):
                role_hint = "cfo"
            else:
                role_hint = "admin"

        target_emp = None
        with erp_db.get_connection() as conn:
            cursor = conn.cursor()
            # 1. Tìm nhân viên theo role hoặc phòng ban
            if role_hint in ("hr", "nhân sự"):
                cursor.execute(
                    """
                    SELECT e.id, e.dept_id, e.name, e.position, e.role, d.name as dept_name
                    FROM employees e
                    LEFT JOIN departments d ON e.dept_id = d.id
                    WHERE LOWER(d.name) LIKE '%nhân sự%' OR LOWER(e.position) LIKE '%hr%' OR LOWER(e.position) LIKE '%nhân sự%' OR e.role = 'admin'
                    LIMIT 1;
                    """
                )
                row = cursor.fetchone()
                if row:
                    target_emp = dict(row)

            elif role_hint in ("it_support", "it", "kỹ thuật"):
                cursor.execute(
                    """
                    SELECT e.id, e.dept_id, e.name, e.position, e.role, d.name as dept_name
                    FROM employees e
                    LEFT JOIN departments d ON e.dept_id = d.id
                    WHERE e.role = 'it_support' OR LOWER(d.name) LIKE '%kỹ thuật%' OR LOWER(d.name) LIKE '%it%'
                    LIMIT 1;
                    """
                )
                row = cursor.fetchone()
                if row:
                    target_emp = dict(row)

            elif role_hint in ("cfo", "tài chính", "kế toán", "ke toan", "finance"):
                # TRƯỚC ĐÂY THIẾU NHÁNH NÀY. Bộ phân loại ở trên sinh ra
                # role_hint="cfo" cho mọi câu chứa "chi phí / sổ quỹ / kế toán",
                # nhưng if/elif chỉ xử lý "hr" và "it_support" — nên mọi việc tài
                # chính rơi xuống fallback "ít task nhất" và có thể được giao cho
                # bất kỳ ai, kể cả nhân viên kỹ thuật. Nay có nhánh riêng.
                cursor.execute(
                    """
                    SELECT e.id, e.dept_id, e.name, e.position, e.role, d.name as dept_name
                    FROM employees e
                    LEFT JOIN departments d ON e.dept_id = d.id
                    WHERE LOWER(e.position) LIKE '%kế toán%'
                       OR LOWER(e.position) LIKE '%ke toan%'
                       OR LOWER(e.position) LIKE '%tài chính%'
                       OR LOWER(e.position) LIKE '%tai chinh%'
                       OR LOWER(d.name) LIKE '%tài chính%'
                       OR LOWER(d.name) LIKE '%ke toan%'
                       OR LOWER(d.name) LIKE '%kế toán%'
                    LIMIT 1;
                    """
                )
                row = cursor.fetchone()
                if row:
                    target_emp = dict(row)

            # Fallback nếu chưa tìm thấy: lấy nhân viên có ít task nhất
            if not target_emp:
                cursor.execute(
                    """
                    SELECT e.id, e.dept_id, e.name, e.position, e.role, d.name as dept_name,
                           COUNT(t.id) as active_count
                    FROM employees e
                    LEFT JOIN departments d ON e.dept_id = d.id
                    LEFT JOIN tasks t ON t.assignee_id = e.id AND t.status IN ('pending', 'in_progress')
                    GROUP BY e.id
                    ORDER BY active_count ASC
                    LIMIT 1;
                    """
                )
                row = cursor.fetchone()
                if row:
                    target_emp = dict(row)

        if not target_emp:
            return {"status": "error", "message": "Không tìm thấy nhân viên nào trong hệ thống để phân bổ."}

        # Tính deadline
        due_dt = datetime.now() + timedelta(days=max(1, deadline_days))
        due_str = due_dt.strftime("%Y-%m-%d 17:30:00")

        # Thêm task vào cơ sở dữ liệu
        task_res = erp_db.create_erp_task(
            dept_id=target_emp["dept_id"],
            assignee_id=target_emp["id"],
            title=description.strip(),
            due_date=due_str,
            created_by_ai=1,
            resolution_notes=f"Tự động phân bổ bởi AI COO cho {target_emp['name']} ({target_emp.get('dept_name', 'Ban')})",
        )

        notify_msg = (
            f"📋 [PHÂN CÔNG CÔNG VIỆC TỰ ĐỘNG - VIRTUAL C.O.O]\n"
            f"• Nhiệm vụ: {description}\n"
            f"• Người phụ trách: {target_emp['name']} ({target_emp.get('position', 'Nhân viên')})\n"
            f"• Phòng ban: {target_emp.get('dept_name', 'Ban chuyên trách')}\n"
            f"• Hạn chót (Deadline): {due_str}\n"
            f"• Mã công việc: #{task_res.get('id', 'N/A')}"
        )

        try:
            from core.telegram_gateway import telegram_gateway
            telegram_gateway.send_incident_alert(notify_msg)
        except Exception:
            pass

        return {
            "status": "success",
            "message": notify_msg,
            "task": task_res,
            "assignee": target_emp,
        }
    except Exception as exc:
        logger.error("Lỗi assign_task_intelligently: %s", exc)
        return {"status": "error", "message": f"Không thể tự động phân công task: {str(exc)}"}


@export_skill(
    name="run_proactive_task_audit",
    description="Kích hoạt quét toàn bộ task trong công ty ngay lập tức để đôn đốc các công việc trễ hạn hoặc sắp đến deadline.",
    parameters_schema={"type": "object", "properties": {}},
)
def run_proactive_task_audit() -> Dict[str, Any]:
    """Kích hoạt rà soát tiến độ công việc ngay lập tức."""
    return proactive_manager.execute_task_audit_sync(trigger_source="manual_skill_call")
