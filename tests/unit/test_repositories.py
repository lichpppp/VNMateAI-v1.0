"""
tests/unit/test_repositories.py
===============================
Unit Test Suite cho Bounded Context Data Access & Repositories (Phase 8).
Kiểm tra tính toàn vẹn khi thao tác dữ liệu thực tế qua Repository Pattern.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mateai.domain.identity.entities import UserIdentity, UserRole, ClearanceLevel
from src.mateai.domain.audit.entities import AuditEvent, AuditAction, AuditRiskLevel
from src.mateai.domain.tasks.entities import BackgroundTask, TaskStatus
from src.mateai.infrastructure.database.factory import get_repository_registry


async def test_user_repository():
    print("\n▸ 1. Kiểm thử UserRepository (Đọc dữ liệu tài khoản thực tế)")
    repo = get_repository_registry()
    users = await repo.users.list_users()
    assert len(users) >= 3, f"Kỳ vọng ít nhất 3 users trong DB, thực tế: {len(users)}"
    print(f"  ✅ Đã tải thành công {len(users)} người dùng từ vnmateai.db:")
    for u in users:
        print(f"     • Username: {u.username:15s} | Role: {u.role.value:10s} | Clearance: {u.clearance.value}")

    # Tìm kiếm theo username
    first_u = users[0]
    found = await repo.users.get_by_username(first_u.username)
    assert found is not None
    assert found.user_id == first_u.user_id
    print(f"  ✅ get_by_username('{first_u.username}') trả về đúng UserIdentity entity.")


async def test_audit_repository():
    print("\n▸ 2. Kiểm thử AuditRepository (Nhật ký kiểm toán an ninh)")
    repo = get_repository_registry()
    count_before = await repo.audits.count_events()
    assert count_before >= 2459, f"Kỳ vọng ít nhất 2459 audit events, thực tế: {count_before}"
    print(f"  ✅ Đếm được {count_before} bản ghi audit logs trong cơ sở dữ liệu.")

    # Ghi nhận sự kiện kiểm toán mới
    test_event = AuditEvent(
        actor_id="test_admin",
        action=AuditAction.TOOL_EXECUTE,
        target="ping_host",
        risk_level=AuditRiskLevel.INFO,
        details={"ping_ms": 1.2},
        session_id="session_test_phase8"
    )
    ok = await repo.audits.record_event(test_event)
    assert ok is True

    count_after = await repo.audits.count_events()
    assert count_after == count_before + 1
    print(f"  ✅ record_event thành công (Số lượng mới: {count_after}).")

    # Lấy danh sách 5 sự kiện gần nhất
    latest_events = await repo.audits.list_events(limit=5)
    assert len(latest_events) == 5
    assert latest_events[0]["action_type"] == "tool_execute"
    print(f"  ✅ list_events(limit=5) trả về bản ghi vừa tạo thành công.")


async def test_task_repository():
    print("\n▸ 3. Kiểm thử TaskRepository (Quản lý tác vụ nền)")
    repo = get_repository_registry()
    
    # Tạo task mới
    new_task = BackgroundTask(
        name="Đồng bộ hóa dữ liệu ERP Phase 8",
        payload={"department": "IT", "action": "health_check"},
        status=TaskStatus.PENDING
    )
    ok = await repo.tasks.create_task(new_task)
    assert ok is True

    # Đọc task
    fetched = await repo.tasks.get_task(new_task.task_id)
    assert fetched is not None
    assert fetched.name == "Đồng bộ hóa dữ liệu ERP Phase 8"
    assert fetched.status == TaskStatus.PENDING
    print(f"  ✅ Tạo và truy vấn BackgroundTask thành công: id={fetched.task_id[:8]} status={fetched.status.value}")

    # Cập nhật trạng thái
    up_ok = await repo.tasks.update_status(new_task.task_id, "completed")
    assert up_ok is True
    updated = await repo.tasks.get_task(new_task.task_id)
    assert updated.status == TaskStatus.COMPLETED
    print(f"  ✅ update_status('{fetched.task_id[:8]}', 'completed') thành công.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT DATA ACCESS & REPOSITORY (PHASE 8)")
    print("=" * 65)
    asyncio.run(test_user_repository())
    asyncio.run(test_audit_repository())
    asyncio.run(test_task_repository())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 3/3 BÀI KIỂM THỬ DATA ACCESS & REPOSITORY ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
