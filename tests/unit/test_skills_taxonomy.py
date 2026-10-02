"""
tests/unit/test_skills_taxonomy.py
==================================
Unit Test Suite cho Bounded Context Skills & Tool Taxonomy (Phase 7).
Kiểm tra ToolRegistry, SkillResolver, và Secure ToolExecutor.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mateai.domain.identity.entities import ClearanceLevel, UserIdentity, UserRole
from mateai.domain.skills.entities import SkillDomain, ToolDefinition, ToolRiskLevel
from mateai.domain.skills.registry import ToolRegistry
from mateai.application.skills.skill_resolver import SkillResolver
from mateai.application.skills.tool_executor import (
    ToolExecutor,
    ToolPermissionDenied,
    ToolExecutionError,
)


def test_tool_registry():
    print("\n▸ 1. Kiểm thử Sổ Đăng Ký Công Cụ (ToolRegistry)")
    registry = ToolRegistry()
    
    t1 = ToolDefinition(
        name="check_cpu",
        description="Đo mức tải CPU",
        domain=SkillDomain.SYSTEM_OPS,
        risk_level=ToolRiskLevel.LEVEL_0_READ_ONLY
    )
    t2 = ToolDefinition(
        name="scan_network_port",
        description="Quét cổng mạng",
        domain=SkillDomain.NETWORK_SECURITY,
        risk_level=ToolRiskLevel.LEVEL_1_LOW
    )
    t3 = ToolDefinition(
        name="query_sql_financials",
        description="Truy vấn báo cáo tài chính",
        domain=SkillDomain.DATABASE_ERP,
        risk_level=ToolRiskLevel.LEVEL_3_HIGH,
        required_clearance="CONFIDENTIAL"
    )

    registry.register(t1)
    registry.register(t2)
    registry.register(t3)

    assert len(registry.get_all()) == 3
    assert len(registry.get_by_domain(SkillDomain.SYSTEM_OPS)) == 1
    assert registry.get("check_cpu") == t1

    schemas = registry.to_openai_tools()
    assert len(schemas) == 3
    assert schemas[0]["type"] == "function"
    print(f"  ✅ Đăng ký 3 công cụ thành công, sinh schema Function Calling chuẩn ({len(schemas)} tools)")


def test_skill_resolver_dynamic_loading():
    print("\n▸ 2. Kiểm thử Phân Giải Miền Nghiệp Vụ (SkillResolver)")
    registry = ToolRegistry()
    
    # Nạp mẫu tools cho 3 miền
    for i in range(3):
        registry.register(ToolDefinition(
            name=f"sys_tool_{i}",
            description="Lệnh hệ thống",
            domain=SkillDomain.SYSTEM_OPS
        ))
        registry.register(ToolDefinition(
            name=f"net_tool_{i}",
            description="Lệnh an ninh mạng",
            domain=SkillDomain.NETWORK_SECURITY
        ))
        registry.register(ToolDefinition(
            name=f"erp_tool_{i}",
            description="Lệnh cơ sở dữ liệu",
            domain=SkillDomain.DATABASE_ERP
        ))

    resolver = SkillResolver(registry)

    # 1. Casual Chat Short-Circuit
    assert resolver.resolve_tools("Xin chào em nhé") == []
    assert resolver.resolve_tools("Cảm ơn bạn nhiều nha") == []
    assert resolver.resolve_tools("Tạm biệt nhé") == []
    print("  ✅ Casual Chat Short-Circuit: Trả về 0 tools (0 token overhead) thành công.")

    # 2. System Ops Query
    sys_tools = resolver.resolve_tools("Kiểm tra cpu và tiến trình bộ nhớ ram", max_tools=2)
    assert len(sys_tools) == 2
    assert all(t.domain == SkillDomain.SYSTEM_OPS for t in sys_tools)
    print(f"  ✅ Phân giải 'Kiểm tra cpu...' → Chọn được {len(sys_tools)} tools SYSTEM_OPS.")

    # 3. Network Query
    net_tools = resolver.resolve_tools("Quét cổng mạng ip 192.168.1.1 kiểm tra an ninh", max_tools=3)
    assert len(net_tools) == 3
    assert all(t.domain == SkillDomain.NETWORK_SECURITY for t in net_tools)
    print(f"  ✅ Phân giải 'Quét cổng mạng...' → Chọn được {len(net_tools)} tools NETWORK_SECURITY.")


async def test_tool_executor_security():
    print("\n▸ 3. Kiểm thử Thực Thi Công Cụ Có Bảo Mật (Secure ToolExecutor)")
    executor = ToolExecutor()

    # Định nghĩa handler giả lập
    async def mock_sys_handler():
        return {"cpu_percent": 12.5}

    async def mock_sql_handler():
        return {"revenue": 1000000}

    executor.register_handler("check_cpu", mock_sys_handler)
    executor.register_handler("query_sql", mock_sql_handler)

    tool_normal = ToolDefinition(
        name="check_cpu",
        description="Xem cpu",
        domain=SkillDomain.SYSTEM_OPS,
        required_clearance="INTERNAL"
    )
    tool_confidential = ToolDefinition(
        name="query_sql",
        description="Xem tài chính",
        domain=SkillDomain.DATABASE_ERP,
        required_clearance="CONFIDENTIAL"
    )

    staff_user = UserIdentity(username="nhanvien_01", role=UserRole.EMPLOYEE, clearance=ClearanceLevel.INTERNAL)

    # 1. Thực thi bình thường thành công
    res = await executor.execute(tool_normal, {}, user=staff_user)
    assert res["success"] is True
    assert res["result"]["cpu_percent"] == 12.5
    assert res["audit_event"].is_success is True
    print(f"  ✅ Thực thi check_cpu thành công: {res['result']} (Audit: PASS)")

    # 2. Từ chối khi thiếu Clearance
    try:
        await executor.execute(tool_confidential, {}, user=staff_user)
        assert False, "Kỳ vọng ném ToolPermissionDenied"
    except ToolPermissionDenied as e:
        print(f"  ✅ Chặn truy cập CONFIDENTIAL thành công đối với nhân viên INTERNAL: {e}")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT SKILLS & TOOLS (PHASE 7)")
    print("=" * 65)
    test_tool_registry()
    test_skill_resolver_dynamic_loading()
    asyncio.run(test_tool_executor_security())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 3/3 BÀI KIỂM THỬ SKILLS & TOOLS ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
