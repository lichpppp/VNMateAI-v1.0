"""
src/mateai/infrastructure/websocket/client_agent_protocol.py
============================================================
Giao thức truyền thông máy trạm (Client Agent Protocol — Version 2.0).

Nhiệm vụ:
- Định nghĩa chuẩn trao đổi thông điệp qua WebSocket giữa Máy Chủ Trung Tâm (Master) và Client Agent trên máy trạm nhân viên.
- Đóng gói và xác thực các gói tin: Đăng ký danh tính (REGISTER), Nhịp tim (HEARTBEAT), Giao việc (TASK_DISPATCH), Xác nhận nhận việc (TASK_ACK), Báo cáo kết quả (TASK_RESULT), và Huỷ bỏ (TASK_CANCEL).
- Tách biệt hoàn toàn Client Agent khỏi mã nguồn nội bộ backend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class ClientAgentMessageType(str, Enum):
    REGISTER = "register"                # Agent đăng ký danh tính với Master
    REGISTER_ACK = "register_ack"        # Master xác nhận đăng ký thành công
    HEARTBEAT = "heartbeat"              # Agent gửi báo cáo định kỳ
    TASK_DISPATCH = "task_dispatch"      # Master giao việc xuống Agent
    TASK_ACK = "task_ack"                # Agent xác nhận đã nhận việc
    TASK_RESULT = "task_result"          # Agent báo cáo kết quả hoàn thành
    TASK_CANCEL = "task_cancel"          # Master yêu cầu hủy tác vụ đang chạy


class ClientAgentProtocol:
    """Bộ mã hoá và thẩm định thông điệp giao thức Client Agent."""

    VERSION = "2.0-enterprise"

    @staticmethod
    def create_register_ack(client_id: str, is_accepted: bool = True, message: str = "Connected") -> Dict[str, Any]:
        return {
            "type": ClientAgentMessageType.REGISTER_ACK.value,
            "version": ClientAgentProtocol.VERSION,
            "client_id": client_id,
            "is_accepted": is_accepted,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @staticmethod
    def create_task_dispatch(
        task_id: str,
        skill_name: str,
        parameters: Dict[str, Any],
        timeout_seconds: float = 30.0
    ) -> Dict[str, Any]:
        return {
            "type": ClientAgentMessageType.TASK_DISPATCH.value,
            "version": ClientAgentProtocol.VERSION,
            "task_id": task_id,
            "skill_name": skill_name,
            "parameters": parameters,
            "timeout_seconds": timeout_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @staticmethod
    def create_task_cancel(task_id: str, reason: str = "master_abort") -> Dict[str, Any]:
        return {
            "type": ClientAgentMessageType.TASK_CANCEL.value,
            "version": ClientAgentProtocol.VERSION,
            "task_id": task_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

    @staticmethod
    def parse_message(raw_json: str) -> Dict[str, Any]:
        """Phân tích và kiểm tra cú pháp của gói tin nhận được."""
        try:
            data = json.loads(raw_json)
            if not isinstance(data, dict) or "type" not in data:
                raise ValueError("Gói tin không đúng định dạng (thiếu trường 'type')")
            return data
        except Exception as e:
            raise ValueError(f"Không thể phân tích gói tin: {e}")
