"""
core/task_manager.py
====================
Lean Micro-Tasking & KPI Tracking Engine for VN-MateAI.

Responsibilities:
  - Keep track of active/pending tasks dispatched to LAN clients in memory (RAM).
  - Push interactive popup payloads to Client Agents via WebSocket.
  - Handle task responses ('completed' or 'issue') from workers.
  - Append audit records to logs/kpi_logs.csv (Timestamp, Client_ID, Task_Message, Status).
  - Trigger instant TTS voice alerts to Xiaozhi audio nodes and Master speakers.
  - Provide fast KPI statistics and monthly summaries for AI skills and Web Portal.
"""

from __future__ import annotations

import csv
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mateai.infrastructure.database.db_manager import db_manager

logger = logging.getLogger(__name__)

# Base directory for logs
# Thư mục gốc dự án (đúng cả bản đóng gói) — không suy từ vị trí file mã nguồn.
from core.config_loader import settings as _settings  # noqa: E402

_PROJECT_ROOT = Path(_settings.PROJECT_ROOT)
_LOGS_DIR = _PROJECT_ROOT / "logs"
_KPI_CSV_PATH = _LOGS_DIR / "kpi_logs.csv"
_CSV_HEADERS = ["Timestamp", "Client_ID", "Task_Message", "Status"]


class TaskManager:
    """
    Lightweight, high-speed task coordinator and KPI tracker.
    Prioritizes < 0.5s round-trip latency without database overhead.
    """

    def __init__(self, csv_path: Optional[Path] = None) -> None:
        self.csv_path = csv_path or _KPI_CSV_PATH
        # In-memory RAM cache for pending and recent tasks: task_id -> dict
        self._pending_tasks: Dict[str, Dict[str, Any]] = {}
        self._recent_history: List[Dict[str, Any]] = []
        # Callback to trigger TTS notification on Xiaozhi audio nodes
        self._tts_notifier: Optional[Callable[[str], Any]] = None
        self._init_csv()

    def _init_csv(self) -> None:
        """Ensure logs directory and kpi_logs.csv exist with headers."""
        try:
            self.csv_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.csv_path.exists() or self.csv_path.stat().st_size == 0:
                with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow(_CSV_HEADERS)
                logger.info("Đã khởi tạo file nhật ký KPI: %s", self.csv_path)
        except Exception as exc:
            logger.error("Lỗi khởi tạo file KPI CSV: %s", exc)

    def set_tts_notifier(self, notifier_func: Callable[[str], Any]) -> None:
        """Register a callback to broadcast TTS voice announcements."""
        self._tts_notifier = notifier_func

    # ------------------------------------------------------------------
    # Dispatching Tasks
    # ------------------------------------------------------------------

    async def dispatch_task(
        self,
        client_id: str,
        message: str,
        sender: str = "Ban Giám Đốc",
        timeout: float = 300.0,
    ) -> Dict[str, Any]:
        """
        Dispatch a task to a worker node via WebSocket.
        Saves task to in-memory dict and returns dispatch confirmation.
        """
        from mateai.interfaces.websocket.client_orchestrator import orchestrator

        if not orchestrator.is_client_online(client_id):
            return {
                "status": "error",
                "client_id": client_id,
                "message": f"Máy trạm '{client_id}' hiện không trực tuyến hoặc chưa kết nối vào mạng LAN.",
            }

        task_id = f"task_{uuid.uuid4().hex[:8]}"
        now_ts = time.time()
        now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Save to RAM
        self._pending_tasks[task_id] = {
            "task_id": task_id,
            "client_id": client_id,
            "message": message,
            "sender": sender,
            "status": "pending",
            "dispatched_at": now_ts,
            "dispatched_at_iso": now_iso,
        }

        # Save to SQLite
        try:
            db_manager.add_or_update_task({
                "task_id": task_id,
                "timestamp": now_iso,
                "client_id": client_id,
                "task_message": message,
                "sender": sender,
                "status": "pending",
            })
        except Exception as dbe:
            logger.warning("TaskManager: Lỗi lưu task vào SQLite: %s", dbe)

        # Send WebSocket payload to client
        payload = {
            "action": "task_popup",
            "task_id": task_id,
            "message": message,
            "sender": sender,
            "timestamp": now_ts,
        }

        try:
            session = orchestrator._clients.get(client_id)
            if not session or not session.get("websocket"):
                raise ConnectionError(f"Phiên kết nối của máy trạm '{client_id}' không tồn tại.")

            import json
            ws = session["websocket"]
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
            logger.info("TaskManager: Đã đẩy nhắc việc tới [%s] (Task ID: %s): '%s'", client_id, task_id, message)

            return {
                "status": "success",
                "task_id": task_id,
                "client_id": client_id,
                "message": f"Đã gửi nhiệm vụ tới máy trạm '{client_id}' thành công.",
                "dispatched_at": now_iso,
            }

        except Exception as exc:
            logger.error("Lỗi khi gửi task tới máy trạm [%s]: %s", client_id, exc)
            self._pending_tasks.pop(task_id, None)
            return {
                "status": "error",
                "client_id": client_id,
                "message": f"Lỗi gửi tin nhắn qua WebSocket tới máy trạm '{client_id}': {exc}",
            }

    # ------------------------------------------------------------------
    # Handling Responses & Recording KPI
    # ------------------------------------------------------------------

    def handle_task_response(
        self,
        client_id: str,
        task_id: str,
        status: str,
        message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Process worker response ('completed' or 'issue').
        1. Updates RAM
        2. Appends to kpi_logs.csv
        3. Fires TTS voice notification
        """
        task_info = self._pending_tasks.pop(task_id, {})
        task_msg = message or task_info.get("message") or "Công việc được giao"
        timestamp_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Update in-memory recent history (keep last 200 items in RAM)
        record = {
            "timestamp": timestamp_iso,
            "client_id": client_id,
            "task_id": task_id,
            "task_message": task_msg,
            "status": status,
        }
        self._recent_history.insert(0, record)
        if len(self._recent_history) > 200:
            self._recent_history = self._recent_history[:200]

        # 2. Append to logs/kpi_logs.csv
        try:
            with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([timestamp_iso, client_id, task_msg, status])
            logger.info("Đã ghi nhận KPI vào CSV: [%s] | Máy: %s | Trạng thái: %s", timestamp_iso, client_id, status)
        except Exception as exc:
            logger.error("Lỗi ghi kpi_logs.csv: %s", exc)

        # 3. Update in SQLite Database
        try:
            db_manager.add_or_update_task({
                "task_id": task_id,
                "timestamp": timestamp_iso,
                "client_id": client_id,
                "task_message": task_msg,
                "status": status,
            })
            logger.info("Đã ghi nhận KPI vào SQLite: [%s] | Task: %s", timestamp_iso, task_id)
        except Exception as dbe:
            logger.warning("Lỗi cập nhật task trong SQLite: %s", dbe)

        # 4. Trigger Voice TTS notification
        announcement = ""
        if status == "completed":
            announcement = f"Báo cáo Sếp, máy {client_id} vừa báo cáo đã hoàn thành công việc: {task_msg}."
        elif status in ("issue", "error"):
            announcement = f"Cảnh báo, máy {client_id} báo cáo đang gặp vướng mắc với công việc: {task_msg}."
        else:
            announcement = f"Máy {client_id} vừa phản hồi công việc với trạng thái: {status}."

        if self._tts_notifier and announcement:
            try:
                self._tts_notifier(announcement)
            except Exception as exc:
                logger.warning("Không thể phát TTS thông báo KPI: %s", exc)

        return {
            "status": "success",
            "task_id": task_id,
            "client_id": client_id,
            "kpi_status": status,
            "timestamp": timestamp_iso,
            "announcement": announcement,
        }

    # ------------------------------------------------------------------
    # KPI Analytics & Summaries
    # ------------------------------------------------------------------

    def get_kpi_logs(
        self,
        limit: int = 100,
        client_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Đọc nhật ký công việc thực tế từ SQLite database (hoặc kpi_logs.csv) và tính toán KPI metrics.
        """
        try:
            # 1. Thử lấy từ SQLite database
            db_tasks = db_manager.get_tasks(limit=limit, client_id=client_id, status=status)
            if db_tasks:
                counts = db_manager.count_tasks()
                total = counts.get("total", len(db_tasks))
                completed = counts.get("completed", 0)
                issues = counts.get("issues", 0)
                rate = round((completed / total * 100), 1) if total > 0 else 0.0
                return {
                    "status": "success",
                    "source": "sqlite",
                    "total": total,
                    "completed": completed,
                    "issue": issues,
                    "completion_rate": rate,
                    "logs": db_tasks,
                }
        except Exception as dbe:
            logger.warning("Không thể đọc tasks từ SQLite, chuyển sang đọc CSV: %s", dbe)

        # 2. Fallback sang đọc file logs/kpi_logs.csv
        logs: List[Dict[str, str]] = []
        total_tasks = 0
        completed_count = 0
        issue_count = 0

        if not self.csv_path.exists():
            return {
                "status": "success",
                "source": "empty",
                "total": 0,
                "completed": 0,
                "issue": 0,
                "completion_rate": 0.0,
                "logs": [],
            }

        try:
            with open(self.csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    r_cid = row.get("Client_ID", "").strip()
                    r_status = row.get("Status", "").strip().lower()

                    if client_id and client_id.lower() != r_cid.lower():
                        continue
                    if status and status.lower() != r_status:
                        continue

                    total_tasks += 1
                    if r_status == "completed":
                        completed_count += 1
                    elif r_status in ("issue", "error"):
                        issue_count += 1

                    item = {
                        "timestamp": row.get("Timestamp", ""),
                        "client_id": r_cid,
                        "task_message": row.get("Task_Message", ""),
                        "status": r_status,
                    }
                    logs.append(item)
                    # Sync to SQLite
                    try:
                        db_manager.add_or_update_task(item)
                    except Exception:
                        pass

            logs.reverse()
            logs = logs[:limit]

            rate = round((completed_count / total_tasks * 100), 1) if total_tasks > 0 else 0.0

            return {
                "status": "success",
                "source": "csv",
                "total": total_tasks,
                "completed": completed_count,
                "issue": issue_count,
                "completion_rate": rate,
                "logs": logs,
            }
        except Exception as exc:
            logger.error("Lỗi đọc file kpi_logs.csv: %s", exc)
            return {
                "status": "error",
                "error": str(exc),
                "logs": [],
            }

    def summarize_monthly(
        self,
        client_id: Optional[str] = None,
        month: Optional[str] = None,
    ) -> str:
        """
        Calculate KPI completion counts for a specific month.
        Format of month: 'YYYY-MM' or 'MM' or string (default: current month).
        Returns a friendly Vietnamese text summary for the AI assistant.
        """
        now = datetime.now()
        target_month_prefix = ""

        if month:
            clean_m = "".join(c for c in month if c.isdigit())
            if len(clean_m) == 1:
                clean_m = f"0{clean_m}"
            if len(clean_m) == 2:
                target_month_prefix = f"{now.year}-{clean_m}"
            elif len(clean_m) >= 6:
                target_month_prefix = f"{clean_m[:4]}-{clean_m[4:6]}"
            else:
                target_month_prefix = month.strip()
        else:
            target_month_prefix = now.strftime("%Y-%m")

        if not self.csv_path.exists():
            return f"Chưa có dữ liệu nhật ký công việc trong tháng {target_month_prefix}."

        completed_by_client: Dict[str, int] = {}
        issue_by_client: Dict[str, int] = {}
        total_completed = 0
        total_issues = 0

        try:
            with open(self.csv_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get("Timestamp", "")
                    if not ts.startswith(target_month_prefix):
                        continue

                    cid = row.get("Client_ID", "").strip()
                    st = row.get("Status", "").strip().lower()

                    if client_id and client_id.lower() != cid.lower():
                        continue

                    if st == "completed":
                        completed_by_client[cid] = completed_by_client.get(cid, 0) + 1
                        total_completed += 1
                    elif st in ("issue", "error"):
                        issue_by_client[cid] = issue_by_client.get(cid, 0) + 1
                        total_issues += 1

            if client_id:
                cnt = completed_by_client.get(client_id, 0)
                iss = issue_by_client.get(client_id, 0)
                return (
                    f"Trong tháng {target_month_prefix}, máy trạm {client_id} đã hoàn thành "
                    f"{cnt} đầu việc (vướng mắc {iss} việc)."
                )

            if total_completed == 0 and total_issues == 0:
                return f"Hệ thống chưa ghi nhận đầu việc nào hoàn thành trong tháng {target_month_prefix}."

            lines = [f"Tổng hợp KPI tháng {target_month_prefix}: toàn hệ thống đã hoàn thành {total_completed} đầu việc ({total_issues} vướng mắc)."]
            for cid, c_cnt in completed_by_client.items():
                i_cnt = issue_by_client.get(cid, 0)
                lines.append(f"- Máy {cid}: hoàn thành {c_cnt} việc (vướng mắc: {i_cnt}).")

            return "\n".join(lines)

        except Exception as exc:
            logger.error("Lỗi khi tổng hợp KPI tháng: %s", exc)
            return f"Không thể đọc tệp nhật ký KPI: {exc}"


# Module-level singleton
task_manager = TaskManager()
