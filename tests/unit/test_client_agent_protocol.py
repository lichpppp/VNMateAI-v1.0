"""
tests/unit/test_client_agent_protocol.py
========================================
Unit Test Suite cho Bounded Context Client Agent Protocol (Phase 11).
Kiểm tra Giao thức Client Agent Protocol và ClientAgentService.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mateai.infrastructure.websocket.client_agent_protocol import (
    ClientAgentProtocol,
    ClientAgentMessageType,
)
from src.mateai.application.devices.client_agent_service import (
    ClientAgentService,
    client_agent_service,
)


def test_client_agent_protocol():
    print("\n▸ 1. Kiểm thử Giao Thức Thông Điệp Máy Trạm (ClientAgentProtocol)")
    
    # 1. Register ACK
    ack = ClientAgentProtocol.create_register_ack("ws_001", is_accepted=True)
    assert ack["type"] == ClientAgentMessageType.REGISTER_ACK.value
    assert ack["client_id"] == "ws_001"
    assert ack["is_accepted"] is True
    print(f"  ✅ Register ACK: type={ack['type']}, client_id={ack['client_id']}")

    # 2. Task Dispatch
    dispatch = ClientAgentProtocol.create_task_dispatch("task_99", "capture_screen", {"quality": 80})
    assert dispatch["type"] == ClientAgentMessageType.TASK_DISPATCH.value
    assert dispatch["task_id"] == "task_99"
    assert dispatch["skill_name"] == "capture_screen"
    print(f"  ✅ Task Dispatch: task_id={dispatch['task_id']}, skill={dispatch['skill_name']}")

    # 3. Parse JSON
    parsed = ClientAgentProtocol.parse_message('{"type": "heartbeat", "cpu": 15.0}')
    assert parsed["type"] == "heartbeat"
    assert parsed["cpu"] == 15.0
    print("  ✅ Parse JSON gói tin thành công.")


async def test_client_agent_service_lifecycle():
    print("\n▸ 2. Kiểm thử Vòng Đời Điều Phối Máy Trạm (ClientAgentService)")
    service = ClientAgentService()
    client_id = "client_nv_01"

    # 1. Đăng ký máy trạm
    reg_ack = service.register_client(
        client_id=client_id,
        hostname="DESKTOP-LAN01",
        platform_name="Windows 11",
        ip_address="192.168.1.150",
        skills=["open_app", "capture_screen"]
    )
    assert reg_ack["is_accepted"] is True
    assert len(service.list_online_agents()) == 1
    print(f"  ✅ Đăng ký máy trạm thành công: {service.list_online_agents()[0].hostname}")

    # 2. Ghi nhận nhịp tim
    hb_ok = service.record_heartbeat(client_id, cpu_usage=25.5, ram_usage=60.0)
    assert hb_ok is True
    agent = service.list_online_agents()[0]
    assert agent.cpu_percent == 25.5
    print(f"  ✅ Nhịp tim đã cập nhật: CPU={agent.cpu_percent}%, RAM={agent.ram_percent}%")

    # 3. Giao task và nhận kết quả hoàn thành
    task_id, dispatch_payload = service.create_task_dispatch_payload(
        client_id=client_id,
        skill_name="capture_screen",
        parameters={"format": "png"}
    )
    assert task_id in service._pending_tasks

    # Giả lập máy trạm gửi kết quả trả về
    service.handle_task_result(task_id, success=True, result_data={"image_size": 102400})
    fut = service._pending_tasks.get(task_id)
    assert fut is None  # Đã hoàn thành và giải phóng
    print(f"  ✅ Giao task '{task_id[:8]}' và hoàn thành tác vụ bất đồng bộ thành công.")

    # 4. Ngắt kết nối
    service.disconnect_client(client_id)
    assert len(service.list_online_agents()) == 0
    print("  ✅ Ngắt kết nối máy trạm an toàn, danh sách trực tuyến cập nhật = 0.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT CLIENT AGENT PROTOCOL (PHASE 11)")
    print("=" * 65)
    test_client_agent_protocol()
    asyncio.run(test_client_agent_service_lifecycle())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 2/2 BÀI KIỂM THỬ CLIENT AGENT PROTOCOL ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
