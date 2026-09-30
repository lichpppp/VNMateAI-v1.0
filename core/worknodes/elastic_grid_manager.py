"""
core/worknodes/elastic_grid_manager.py
======================================
Hồ Máy Trạm Ngoại Vi Co Giãn (Elastic Standby Worker Grid Manager).
Nhiệm vụ:
  1. Quản lý linh hoạt cụm N máy trạm (Mac Mini OpenClaw / Windows / Linux) từ N = 0 đến hàng trăm máy.
  2. Heartbeat Ping qua POST /api/v1/worknodes/heartbeat (Timeout 15 giây tự động đánh dấu OFFLINE).
  3. Standby Fallback: Khi 0 Node Online, đưa task vào hàng đợi PENDING_STANDBY và kích hoạt
     câu trả lời thoại an toàn:
     "Hệ thống đã tiếp nhận lệnh. Các trạm thực thi giao diện hiện đang ở chế độ chờ, công việc đã được lưu trữ an toàn trong hàng đợi."
  4. Hỗ trợ phát sóng trạng thái thời gian thực qua WebSocket để cập nhật UI Topology không cần tải lại trang.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger("core.worknodes.grid")

# Ngưỡng timeout heartbeat tính bằng giây (quá 15s -> OFFLINE)
HEARTBEAT_TIMEOUT_SEC = 15.0


class WorknodeState:
    """Trạng thái của một trạm thực thi ngoại vi."""

    def __init__(
        self,
        node_id: str,
        ip: str,
        capabilities: Optional[List[str]] = None,
        status: str = "READY",
        cpu_percent: float = 0.0,
        ram_percent: float = 0.0,
        active_tasks: int = 0,
    ) -> None:
        self.node_id = node_id
        self.ip = ip
        self.capabilities = capabilities or ["GUI_OPENCLAW", "LOCAL_OCR"]
        self.status = status
        self.cpu_percent = cpu_percent
        self.ram_percent = ram_percent
        self.active_tasks = active_tasks
        self.last_heartbeat = time.time()
        self.registered_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def update_heartbeat(
        self,
        ip: Optional[str] = None,
        capabilities: Optional[List[str]] = None,
        status: Optional[str] = None,
        cpu_percent: Optional[float] = None,
        ram_percent: Optional[float] = None,
        active_tasks: Optional[int] = None,
    ) -> None:
        self.last_heartbeat = time.time()
        if ip:
            self.ip = ip
        if capabilities:
            self.capabilities = capabilities
        if status:
            self.status = status
        if cpu_percent is not None:
            self.cpu_percent = cpu_percent
        if ram_percent is not None:
            self.ram_percent = ram_percent
        if active_tasks is not None:
            self.active_tasks = active_tasks

    @property
    def is_online(self) -> bool:
        return (time.time() - self.last_heartbeat) <= HEARTBEAT_TIMEOUT_SEC

    def to_dict(self) -> Dict[str, Any]:
        online = self.is_online
        return {
            "node_id": self.node_id,
            "ip": self.ip,
            "capabilities": self.capabilities,
            "status": self.status if online else "OFFLINE",
            "is_online": online,
            "cpu_percent": self.cpu_percent if online else 0.0,
            "ram_percent": self.ram_percent if online else 0.0,
            "active_tasks": self.active_tasks if online else 0,
            "last_heartbeat_ago_sec": round(time.time() - self.last_heartbeat, 1),
            "registered_at": self.registered_at,
        }


class ElasticGridManager:
    """Quản lý cụm máy trạm co giãn không giới hạn số lượng."""

    def __init__(self) -> None:
        self._nodes: Dict[str, WorknodeState] = {}
        # Hàng đợi tác vụ chờ khi không có node nào online (PENDING_STANDBY)
        self._standby_queue: List[Dict[str, Any]] = []

    def record_heartbeat(
        self,
        node_id: str,
        ip: str = "127.0.0.1",
        capabilities: Optional[List[str]] = None,
        status: str = "READY",
        cpu_percent: float = 0.0,
        ram_percent: float = 0.0,
        active_tasks: int = 0,
    ) -> Dict[str, Any]:
        """Ghi nhận ping định kỳ từ trạm ngoại vi."""
        node_key = node_id.strip()
        is_new = node_key not in self._nodes

        if is_new:
            node = WorknodeState(
                node_id=node_key,
                ip=ip,
                capabilities=capabilities,
                status=status,
                cpu_percent=cpu_percent,
                ram_percent=ram_percent,
                active_tasks=active_tasks,
            )
            self._nodes[node_key] = node
            logger.info("[ElasticGrid] Trạm mới kết nối: %s (%s) — Sẵn sàng phân bổ việc", node_key, ip)
        else:
            node = self._nodes[node_key]
            node.update_heartbeat(
                ip=ip,
                capabilities=capabilities,
                status=status,
                cpu_percent=cpu_percent,
                ram_percent=ram_percent,
                active_tasks=active_tasks,
            )

        # Kiểm tra xem có task PENDING_STANDBY nào cần phân bổ lại không
        dispatched_task = None
        if self._standby_queue and node.status == "READY":
            dispatched_task = self._standby_queue.pop(0)
            logger.info("[ElasticGrid] Đã giải phóng task '%s' từ PENDING_STANDBY sang node %s", dispatched_task.get("task_id"), node_key)

        return {
            "status": "ack",
            "node_id": node_key,
            "grid_online_nodes": self.get_online_count(),
            "assigned_task": dispatched_task,
        }

    def get_online_nodes(self) -> List[Dict[str, Any]]:
        """Lấy danh sách các node hiện đang Online."""
        return [node.to_dict() for node in self._nodes.values() if node.is_online]

    def get_all_nodes(self) -> List[Dict[str, Any]]:
        """Lấy toàn bộ danh sách node trong registry kèm trạng thái."""
        return [node.to_dict() for node in self._nodes.values()]

    def get_online_count(self) -> int:
        """Đếm số trạm đang online thực tế."""
        return sum(1 for node in self._nodes.values() if node.is_online)

    def dispatch_or_standby(self, task_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phân phối tác vụ tới 1 node sẵn sàng.
        Nếu 0 node nào online, tự động chuyển vào PENDING_STANDBY kèm câu trả lời thoại an toàn.
        """
        import uuid
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        online_ready_nodes = [
            n for n in self._nodes.values()
            if n.is_online and n.status == "READY" and task_type in n.capabilities
        ]

        if not online_ready_nodes:
            # Không có node nào -> Fallback PENDING_STANDBY
            task_entry = {
                "task_id": task_id,
                "task_type": task_type,
                "payload": payload,
                "status": "PENDING_STANDBY",
                "queued_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._standby_queue.append(task_entry)
            logger.warning("[ElasticGrid] 0 Node Online — Đã chuyển task %s vào PENDING_STANDBY (Hàng đợi: %d)", task_id, len(self._standby_queue))

            voice_msg = "Hệ thống đã tiếp nhận lệnh. Các trạm thực thi giao diện hiện đang ở chế độ chờ, công việc đã được lưu trữ an toàn trong hàng đợi."
            return {
                "status": "pending_standby",
                "task_id": task_id,
                "voice_response": voice_msg,
                "queued_count": len(self._standby_queue),
            }

        # Chọn node có ít active_tasks nhất
        selected_node = min(online_ready_nodes, key=lambda n: n.active_tasks)
        selected_node.active_tasks += 1

        return {
            "status": "dispatched",
            "task_id": task_id,
            "target_node": selected_node.node_id,
            "target_ip": selected_node.ip,
            "voice_response": f"Lệnh đã được điều phối tới trạm thực thi {selected_node.node_id}.",
        }

    def get_grid_overview(self) -> Dict[str, Any]:
        """Tổng quan cụm Standby Grid phục vụ Web Portal & Topology."""
        all_nodes = self.get_all_nodes()
        online_count = sum(1 for n in all_nodes if n["is_online"])
        total_cpu = sum(n["cpu_percent"] for n in all_nodes if n["is_online"])
        avg_cpu = round(total_cpu / online_count, 1) if online_count > 0 else 0.0

        return {
            "total_registered_nodes": len(all_nodes),
            "online_nodes_count": online_count,
            "standby_queue_length": len(self._standby_queue),
            "average_cpu_load": avg_cpu,
            "nodes": all_nodes,
        }


# Singleton instance
elastic_grid_manager = ElasticGridManager()
