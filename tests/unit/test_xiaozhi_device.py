"""
tests/unit/test_xiaozhi_device.py
=================================
Unit Test Suite cho Bounded Context IoT Device Transport (Phase 10).
Kiểm tra Giao thức XiaoZhi IoT Protocol và XiaoZhiDeviceService.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mateai.domain.voice.entities import VoiceState
from mateai.infrastructure.websocket.xiaozhi_protocol import (
    XiaoZhiProtocol,
    XiaoZhiState,
    XiaoZhiEmotion,
)
from mateai.application.devices.xiaozhi_service import (
    XiaoZhiDeviceService,
    xiaozhi_device_service,
)
from mateai.application.voice.barge_in_controller import barge_in_controller


def test_xiaozhi_protocol_serialization():
    print("\n▸ 1. Kiểm thử Giao Thức Khung Tin XiaoZhi (XiaoZhiProtocol)")
    
    # 1. UI Frame
    ui_frame = XiaoZhiProtocol.create_ui_frame(
        state=XiaoZhiState.PROCESSING,
        emotion=XiaoZhiEmotion.THINKING,
        text="Đang phân tích..."
    )
    assert ui_frame["type"] == "ui"
    assert ui_frame["state"] == "processing"
    assert ui_frame["emotion"] == "thinking"
    assert ui_frame["text"] == "Đang phân tích..."
    print(f"  ✅ UI Frame chuẩn hóa: state={ui_frame['state']}, emotion={ui_frame['emotion']}")

    # 2. Hello ACK
    ack_frame = XiaoZhiProtocol.create_hello_ack("session_123", sample_rate=16000)
    assert ack_frame["type"] == "hello_ack"
    assert ack_frame["session_id"] == "session_123"
    assert ack_frame["sample_rate"] == 16000
    print(f"  ✅ Hello ACK Frame: session={ack_frame['session_id']}, rate={ack_frame['sample_rate']}")

    # 3. Parse Client Frame
    frame_type, data = XiaoZhiProtocol.parse_client_frame('{"type": "listen", "state": "detect"}')
    assert frame_type == "listen"
    assert data["state"] == "detect"
    print(f"  ✅ Parse Client Frame thành công: type={frame_type}")


def test_xiaozhi_device_service_lifecycle():
    print("\n▸ 2. Kiểm thử Vòng Đời Phiên Thiết Bị XiaoZhi (XiaoZhiDeviceService)")
    service = XiaoZhiDeviceService()
    device_id = "esp32_s3_device_001"
    pairing_code = "889900"

    # 1. Handshake Hello
    ack, ui = service.handle_hello(device_id, pairing_code=pairing_code)
    assert ack["type"] == "hello_ack"
    assert ui["emotion"] == "sleeping"
    assert service.get_device_by_pairing_code("889900") == device_id
    print(f"  ✅ Ghép nối thiết bị thành công qua mã '{pairing_code}' → device_id='{device_id}'")

    # 2. Bắt đầu lắng nghe
    ui_listen = service.handle_listen(device_id)
    assert ui_listen["state"] == "listening"
    assert ui_listen["emotion"] == "focused"
    print("  ✅ Chuyển trạng thái sang LISTENING / FOCUSED thành công.")

    # 3. Ngắt lời phần cứng (Hardware Barge-In)
    ui_barge_in = service.handle_hardware_barge_in(device_id)
    assert ui_barge_in["emotion"] == "focused"
    assert "Dạ, em nghe đây" in ui_barge_in["text"]
    
    session = service._device_sessions[device_id]
    assert session.state == VoiceState.INTERRUPTED
    print("  ✅ Ngắt lời phần cứng kích hoạt Barge-In thành công, phiên chuyển sang INTERRUPTED.")

    # 4. Ngắt kết nối
    service.disconnect_device(device_id)
    assert device_id not in service._device_sessions
    print("  ✅ Dọn dẹp session khi ngắt kết nối an toàn.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT XIAOZHI DEVICE (PHASE 10)")
    print("=" * 65)
    test_xiaozhi_protocol_serialization()
    test_xiaozhi_device_service_lifecycle()
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 2/2 BÀI KIỂM THỬ XIAOZHI DEVICE ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
