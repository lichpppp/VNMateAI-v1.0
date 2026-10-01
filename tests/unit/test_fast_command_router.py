"""
tests/unit/test_fast_command_router.py
======================================
Unit Test Suite cho Bounded Context Fast Command Router (Phase 6).
Kiểm tra độ chính xác khớp lệnh, phân quyền RBAC, độ trễ và pass-through.
"""

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.mateai.domain.identity.entities import ClearanceLevel, UserIdentity, UserRole
from src.mateai.application.commands.fast_command_router import (
    FastCommandRouter,
    fast_command_router,
    strip_vietnamese_accents,
)


def test_vietnamese_accents_stripping():
    print("\n▸ 1. Kiểm thử Chuẩn Hóa Tiếng Việt (Accent Normalization)")
    raw = "Mấy giờ rồi hả em ơi?"
    stripped = strip_vietnamese_accents(raw)
    assert "may gio roi ha em oi" in stripped
    print(f"  ✅ '{raw}' → '{stripped}'")


async def test_deterministic_dispatch():
    print("\n▸ 2. Kiểm thử Khớp Lệnh Tất Định (Deterministic Dispatch)")

    # 1. Giờ
    r_time = await fast_command_router.dispatch("mấy giờ rồi")
    assert r_time is not None
    assert r_time.command_name == "get_current_time"
    assert "giờ" in r_time.reply_text
    print(f"  ✅ 'mấy giờ rồi' → '{r_time.reply_text}' (Latency: {r_time.latency_ms:.2f}ms)")

    # 2. Ngày
    r_date = await fast_command_router.dispatch("hôm nay ngày mấy")
    assert r_date is not None
    assert r_date.command_name == "get_current_date"
    assert "ngày" in r_date.reply_text
    print(f"  ✅ 'hôm nay ngày mấy' → '{r_date.reply_text}' (Latency: {r_date.latency_ms:.2f}ms)")

    # 3. RAM
    r_ram = await fast_command_router.dispatch("kiểm tra ram")
    assert r_ram is not None
    assert r_ram.command_name == "get_ram_status"
    assert "RAM" in r_ram.reply_text
    print(f"  ✅ 'kiểm tra ram' → '{r_ram.reply_text}' (Latency: {r_ram.latency_ms:.2f}ms)")

    # 4. Chào hỏi
    r_hello = await fast_command_router.dispatch("xin chào")
    assert r_hello is not None
    assert r_hello.command_name == "quick_greeting"
    print(f"  ✅ 'xin chào' → '{r_hello.reply_text}' (Latency: {r_hello.latency_ms:.2f}ms)")

    # 5. Ping
    r_ping = await fast_command_router.dispatch("ping")
    assert r_ping is not None
    assert r_ping.command_name == "system_ping"
    assert "Pong" in r_ping.reply_text
    print(f"  ✅ 'ping' → '{r_ping.reply_text}' (Latency: {r_ping.latency_ms:.2f}ms)")


async def test_rbac_clearance_enforcement():
    print("\n▸ 3. Kiểm thử Ràng Buộc Phân Quyền Bảo Mật (RBAC Enforcement)")
    
    # User chỉ có quyền PUBLIC (Guest/Khách ngoài)
    guest_user = UserIdentity(
        username="guest_01",
        role=UserRole.GUEST,
        clearance=ClearanceLevel.PUBLIC
    )
    # Lệnh xem giờ (PUBLIC) -> Cho phép
    r_pub = await fast_command_router.dispatch("mấy giờ rồi", user=guest_user)
    assert r_pub is not None
    assert "chưa được cấp quyền" not in r_pub.reply_text
    print("  ✅ Lệnh PUBLIC được thực thi bình thường cho Guest.")

    # Lệnh xem CPU (INTERNAL) -> Bị từ chối
    r_priv = await fast_command_router.dispatch("kiểm tra cpu", user=guest_user)
    assert r_priv is not None
    assert "chưa được cấp quyền" in r_priv.reply_text
    print("  ✅ Lệnh INTERNAL bị chặn an toàn khi Guest cố tình truy cập.")

    # User nội bộ (INTERNAL) -> Được phép
    staff_user = UserIdentity(
        username="staff_01",
        role=UserRole.EMPLOYEE,
        clearance=ClearanceLevel.INTERNAL
    )
    r_staff = await fast_command_router.dispatch("kiểm tra cpu", user=staff_user)
    assert r_staff is not None
    assert "mức tải CPU" in r_staff.reply_text
    print("  ✅ Lệnh INTERNAL được cấp phép đúng đối tượng cho nhân viên nội bộ.")


async def test_complex_query_pass_through():
    print("\n▸ 4. Kiểm thử Chuyển Tiếp Cho Lệnh Phức Tạp (Pass-Through to LLM)")
    queries = [
        "Hãy viết cho tôi bài văn phân tích thị trường chứng khoán",
        "Có bao nhiêu nhân viên đi trễ trong tháng này?",
        "Giải thích cơ chế ngắt lời Barge-In trong VN-MateAI"
    ]
    for q in queries:
        r = await fast_command_router.dispatch(q)
        assert r is None, f"Lệnh '{q}' bị bắt nhầm vào Fast Router!"
        print(f"  ✅ '{q[:40]}...' → Pass-Through (None) thành công.")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT FAST COMMAND ROUTER (PHASE 6)")
    print("=" * 65)
    test_vietnamese_accents_stripping()
    asyncio.run(test_deterministic_dispatch())
    asyncio.run(test_rbac_clearance_enforcement())
    asyncio.run(test_complex_query_pass_through())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ FAST COMMAND ROUTER ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
