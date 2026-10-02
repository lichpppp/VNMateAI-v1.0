"""
tests/unit/test_domain_entities.py
==================================
Unit Test Suite cho Tầng Domain Entities (Phase 3).
Kiểm tra tính toàn vẹn của các thực thể nghiệp vụ cốt lõi không có dependency bên ngoài.
"""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mateai.domain import (
    VoiceSession,
    VoiceState,
    AudioFrame,
    AudioEncoding,
    Message,
    MessageRole,
    ConversationContext,
    BrainType,
    AgentTask,
    AgentState,
    SkillDomain,
    ToolRiskLevel,
    ToolDefinition,
    UserIdentity,
    UserRole,
    ClearanceLevel,
    Device,
    DeviceType,
    DeviceStatus,
    BackgroundTask,
    TaskStatus,
    AuditEvent,
    AuditAction,
)


def test_voice_session_lifecycle():
    print("\n▸ 1. Kiểm thử Vòng Đời VoiceSession & Barge-In")
    session = VoiceSession(client_id="hud_test")
    assert session.state == VoiceState.IDLE
    
    # Start turn
    turn_id = session.start_new_turn()
    assert session.state == VoiceState.PROCESSING
    assert session.current_turn_id == turn_id
    
    # Interruption (Barge-in)
    interruption = session.interrupt()
    assert session.state == VoiceState.INTERRUPTED
    assert interruption.session_id == session.session_id
    assert interruption.target_turn_id == turn_id
    print(f"  ✅ VoiceSession chuyển trạng thái chính xác: {session.state.value}, turn={turn_id[:8]}")

    # AudioFrame test
    frame = AudioFrame(payload=b"\x00\x01\x02", sequence_number=1, encoding=AudioEncoding.MP3)
    assert frame.byte_length == 3
    print(f"  ✅ AudioFrame hoạt động chuẩn xác: {frame.byte_length} bytes, seq={frame.sequence_number}")


def test_conversation_context():
    print("\n▸ 2. Kiểm thử Ngữ Cảnh Hội Thoại (ConversationContext)")
    ctx = ConversationContext(system_prompt="Bạn là Ly Ly trợ lý AI.")
    ctx.add_user_message("Xin chào")
    ctx.add_assistant_message("Dạ chào bạn!")
    
    llm_payload = ctx.get_messages_for_llm()
    assert len(llm_payload) == 3
    assert llm_payload[0]["role"] == "system"
    assert llm_payload[1]["role"] == "user"
    assert llm_payload[2]["role"] == "assistant"
    print(f"  ✅ ConversationContext tạo payload LLM chuẩn ({len(llm_payload)} messages)")


def test_tool_definition_schema():
    print("\n▸ 3. Kiểm thử Định Nghĩa Công Cụ (ToolDefinition)")
    tool = ToolDefinition(
        name="get_current_time",
        description="Lấy thời gian thực",
        domain=SkillDomain.SYSTEM_OPS,
        risk_level=ToolRiskLevel.LEVEL_0_READ_ONLY
    )
    schema = tool.to_openai_tool_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "get_current_time"
    print(f"  ✅ ToolDefinition sinh schema Function Call tiêu chuẩn: {schema['function']['name']}")


def test_user_identity_and_clearance():
    print("\n▸ 4. Kiểm thử Định Danh & Phân Quyền Bảo Mật (UserIdentity)")
    user = UserIdentity(
        username="admin_tech",
        role=UserRole.ADMIN,
        clearance=ClearanceLevel.RESTRICTED
    )
    assert user.is_admin() is True
    assert user.has_permission_for_clearance(ClearanceLevel.PUBLIC) is True
    assert user.has_permission_for_clearance(ClearanceLevel.INTERNAL) is True
    assert user.has_permission_for_clearance(ClearanceLevel.CONFIDENTIAL) is True
    assert user.has_permission_for_clearance(ClearanceLevel.RESTRICTED) is True

    employee = UserIdentity(username="nv01", role=UserRole.EMPLOYEE, clearance=ClearanceLevel.INTERNAL)
    assert employee.has_permission_for_clearance(ClearanceLevel.CONFIDENTIAL) is False
    print(f"  ✅ UserIdentity kiểm tra Clearance hierarchy chính xác (Admin: ALL, Employee: INTERNAL only)")


def test_devices_tasks_audit():
    print("\n▸ 5. Kiểm thử Thiết Bị, Tác Vụ Nền & Nhật Ký Kiểm Toán")
    dev = Device(device_id="esp32-01", device_name="XiaoZhi S3", device_type=DeviceType.ESP32_XIAOZHI)
    assert dev.status == DeviceStatus.OFFLINE

    task = BackgroundTask(name="sync_erp_kpi")
    assert task.status == TaskStatus.PENDING

    audit = AuditEvent(actor_id="user_123", action=AuditAction.TOOL_EXECUTE, target="get_current_time")
    assert audit.is_success is True
    print("  ✅ Device, BackgroundTask, và AuditEvent khởi tạo thành công.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ UNIT TẦNG DOMAIN (PHASE 3: DOMAIN ENTITIES)")
    print("=" * 65)
    test_voice_session_lifecycle()
    test_conversation_context()
    test_tool_definition_schema()
    test_user_identity_and_clearance()
    test_devices_tasks_audit()
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 5/5 BÀI KIỂM THỬ TẦNG DOMAIN ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
