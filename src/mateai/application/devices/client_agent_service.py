"""
src/mateai/application/devices/client_agent_service.py
======================================================
Dịch vụ quản trị máy trạm nhân viên (Client Agent Service).

Mục tiêu:
- Theo dõi các máy trạm trực tuyến (Online Workstations).
- Điều phối giao việc tự động hóa (RPA Task Dispatching) xuống Client Agent.
- Quản lý xác nhận nhận việc (Task ACK) và thu thập kết quả thực thi an toàn.
- Tuân thủ RULE-003: Không gọi database thô trực tiếp.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
import uuid

from src.mateai.infrastructure.websocket.client_agent_protocol import (
    ClientAgentProtocol,
    ClientAgentMessageType,
)

logger = logging.getLogger(__name__)


@dataclass
class ConnectedClientAgent:
    """Thông tin máy trạm nhân viên đang kết nối."""
    client_id: str
    hostname: str
    platform: str
    ip_address: str
    skills: List[str] = field(default_factory=list)
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    is_online: bool = True
    cpu_percent: float = 0.0
    ram_percent: float = 0.0


class ClientAgentService:
    """Dịch vụ điều phối và quản lý máy trạm."""

    def __init__(self):
        # client_id -> ConnectedClientAgent
        self._connected_agents: Dict[str, ConnectedClientAgent] = {}
        # task_id -> asyncio.Future
        self._pending_tasks: Dict[str, asyncio.Future] = {}

    def register_client(
        self,
        client_id: str,
        hostname: str,
        platform_name: str,
        ip_address: str,
        skills: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """Đăng ký máy trạm nhân viên mới kết nối vào hệ thống."""
        agent = ConnectedClientAgent(
            client_id=client_id,
            hostname=hostname,
            platform=platform_name,
            ip_address=ip_address,
            skills=skills or [],
            last_heartbeat=datetime.now(timezone.utc),
            is_online=True
        )
        self._connected_agents[client_id] = agent
        logger.info("[ClientAgentService] Đã đăng ký máy trạm '%s' (%s @ %s)", hostname, platform_name, ip_address)
        return ClientAgentProtocol.create_register_ack(client_id=client_id, is_accepted=True)

    def record_heartbeat(
        self,
        client_id: str,
        cpu_usage: float = 0.0,
        ram_usage: float = 0.0
    ) -> bool:
        """Ghi nhận nhịp tim và tình trạng tài nguyên định kỳ từ máy trạm."""
        agent = self._connected_agents.get(client_id)
        if not agent:
            return False
        agent.last_heartbeat = datetime.now(timezone.utc)
        agent.cpu_percent = cpu_usage
        agent.ram_percent = ram_usage
        agent.is_online = True
        return True

    def create_task_dispatch_payload(
        self,
        client_id: str,
        skill_name: str,
        parameters: Dict[str, Any],
        timeout_seconds: float = 30.0
    ) -> Tuple[str, Dict[str, Any]]:
        """Tạo gói tin giao việc và đăng ký future chờ kết quả."""
        task_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending_tasks[task_id] = future

        payload = ClientAgentProtocol.create_task_dispatch(
            task_id=task_id,
            skill_name=skill_name,
            parameters=parameters,
            timeout_seconds=timeout_seconds
        )
        return task_id, payload

    def handle_task_ack(self, task_id: str, client_id: str) -> None:
        """Xử lý khi máy trạm xác nhận đã nhận việc."""
        logger.debug("[ClientAgentService] Máy trạm '%s' đã xác nhận nhận task '%s'", client_id, task_id[:8])

    def handle_task_result(
        self,
        task_id: str,
        success: bool,
        result_data: Any,
        error_message: Optional[str] = None
    ) -> None:
        """Xử lý khi máy trạm gửi báo cáo kết quả hoàn thành task."""
        future = self._pending_tasks.pop(task_id, None)
        if future and not future.done():
            if success:
                future.set_result(result_data)
            else:
                future.set_exception(RuntimeError(error_message or "Task failed on client agent"))

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Tạo gói tin hủy tác vụ gửi xuống máy trạm."""
        future = self._pending_tasks.pop(task_id, None)
        if future and not future.done():
            future.cancel()
        return ClientAgentProtocol.create_task_cancel(task_id=task_id)

    def disconnect_client(self, client_id: str) -> None:
        """Đánh dấu máy trạm ngắt kết nối."""
        agent = self._connected_agents.get(client_id)
        if agent:
            agent.is_online = False
            logger.info("[ClientAgentService] Máy trạm '%s' đã ngắt kết nối", agent.hostname)

    def list_online_agents(self) -> List[ConnectedClientAgent]:
        """Lấy danh sách các máy trạm đang trực tuyến."""
        return [a for a in self._connected_agents.values() if a.is_online]


# Singleton service
client_agent_service = ClientAgentService()
