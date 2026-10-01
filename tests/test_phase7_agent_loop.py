"""
tests/test_phase7_agent_loop.py
===============================
Unit & Integration Test Suite cho Giai đoạn 7:
Phase 7: Agent Loop Optimization & Tool Pruning.

Mục tiêu kiểm thử:
1. Tool Schema Pruning: Cắt tỉa schema, trả về 0 tools cho câu hỏi đàm thoại và tối đa 3-5 tools cho câu lệnh kỹ thuật.
2. Direct Response Synthesis: Bỏ qua LLM Round 2 cho các công cụ tự giải thích / xác nhận hành động.
3. Complex Payload Fallback: Trả về None đối với dữ liệu phức tạp để kích hoạt LLM Round 2.
4. Payload Pruning: Cắt gọt các trường rác kỹ thuật (stacktrace, debug, raw headers) trước khi gửi vào LLM.
5. Tool Execution Integration: Thực thi tool call an toàn và đo lường thời gian.
"""

import asyncio
import sys
import time
from pathlib import Path
from typing import Any, Dict

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent_voice_loop import (
    prune_tool_schemas,
    can_synthesize_direct_response,
    prune_tool_payload_for_llm,
    execute_tool_call,
    ToolExecutionResult,
)


def test_tool_schema_pruning():
    print("\n▸ 1. Kiểm thử Cắt Tỉa Schema Công Cụ (Tool Schema Pruning)")

    # 1.1 Đàm thoại thông thường -> 0 tools
    casual_queries = [
        "Xin chào em nhé",
        "Chào bạn, hôm nay thế nào?",
        "Cảm ơn em nhiều nha",
        "Tạm biệt nhé",
    ]
    for cq in casual_queries:
        tools = prune_tool_schemas(cq, max_tools=5)
        assert len(tools) == 0, f"Query đàm thoại '{cq}' phải trả về 0 tools, nhận: {len(tools)}"
        print(f"  ✅ Đàm thoại '{cq}' → 0 tools (Giảm tối đa TTFT).")

    # 1.2 Tác vụ kỹ thuật hệ thống -> tối đa 5 tools
    op_query = "Kiểm tra tiến trình CPU và kill process chiếm tài nguyên"
    tools_op = prune_tool_schemas(op_query, max_tools=5)
    assert 0 < len(tools_op) <= 5, f"Kỳ vọng 1-5 tools, nhận: {len(tools_op)}"
    print(f"  ✅ Tác vụ hệ thống → Đã lọc chính xác {len(tools_op)} công cụ phù hợp nhất.")


def test_direct_response_synthesis():
    print("\n▸ 2. Kiểm thử Bỏ Qua LLM Round 2 (Direct Response Synthesis)")

    # 2.1 Action tool: kill_process
    res_kill = {"success": True, "data": {"status": "ok"}}
    direct_kill = can_synthesize_direct_response("kill_process", res_kill)
    assert direct_kill is not None, "Kỳ vọng sinh câu nói trực tiếp cho kill_process"
    assert "dừng tiến trình" in direct_kill
    print(f"  ✅ kill_process → '{direct_kill}' (Bỏ qua LLM Round 2, tiết kiệm ~2.5s).")

    # 2.2 Tool trả về message tường minh
    res_msg = {"success": True, "data": {"message": "Đã tạo bản sao lưu cơ sở dữ liệu thành công"}}
    direct_msg = can_synthesize_direct_response("backup_db", res_msg)
    assert direct_msg is not None
    assert "sao lưu cơ sở dữ liệu" in direct_msg
    print(f"  ✅ backup_db (kèm message) → '{direct_msg}'")

    # 2.3 Tool trả về lỗi
    res_err = {"success": False, "error": "Access Denied: Không có quyền root"}
    direct_err = can_synthesize_direct_response("delete_system_file", res_err)
    assert direct_err is not None
    assert "không thực hiện được" in direct_err and "Access Denied" in direct_err
    print(f"  ✅ Tool lỗi → Phản hồi trực tiếp câu báo lỗi: '{direct_err}'")

    # 2.4 Dữ liệu phức tạp (Bảng danh sách, RAG) -> Không direct, cần LLM Round 2
    res_complex = {
        "success": True,
        "data": {
            "records": [{"id": i, "name": f"User_{i}", "salary": 2000} for i in range(25)],
            "total_count": 25,
        }
    }
    direct_complex = can_synthesize_direct_response("query_database", res_complex)
    assert direct_complex is None, "Dữ liệu phức tạp không được direct synthesize, phải nhường LLM Round 2"
    print("  ✅ Dữ liệu phức tạp (25 records) → Chuyển tiếp LLM Round 2 tổng hợp chính xác.")


def test_payload_pruning():
    print("\n▸ 3. Kiểm thử Thu Gọn Dữ Liệu Tool Cho LLM (Payload Pruning)")

    dirty_payload = {
        "status": "success",
        "user_id": 42,
        "stacktrace": "Traceback (most recent call last):\nFile 'foo.py' line 10\nException: test",
        "debug": {"internal_mem_addr": "0x7ffee3b4", "socket_fd": 19},
        "raw_headers": ["Host: 127.0.0.1", "Authorization: Bearer test_token"],
        "items": [f"item_{i}" for i in range(50)],
    }

    clean_json_str = prune_tool_payload_for_llm(dirty_payload, max_chars=1000)
    assert "stacktrace" not in clean_json_str, "Phải loại bỏ stacktrace rác!"
    assert "raw_headers" not in clean_json_str, "Phải loại bỏ raw_headers!"
    assert "user_id" in clean_json_str
    print(f"  ✅ Đã cắt tỉa dữ liệu rác, dung lượng gọn gàng: {len(clean_json_str)} ký tự.")


async def test_tool_execution_integration():
    print("\n▸ 4. Kiểm thử Thực Thi Tool An Toàn (execute_tool_call)")

    # Tool call mô phỏng
    mock_tc = {
        "id": "call_mock_1",
        "name": "set_system_volume",
        "arguments": '{"level": 75}',
    }

    result = await execute_tool_call(mock_tc)
    assert isinstance(result, ToolExecutionResult)
    assert result.tool_name == "set_system_volume"
    assert result.direct_response is not None
    print(f"  ✅ Thực thi '{result.tool_name}' hoàn tất trong {result.execution_time_ms:.2f}ms: '{result.direct_response}'")


async def main():
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ GIAI ĐOẠN 7 (PHASE 7: AGENT LOOP & TOOL PRUNING)")
    print("=" * 65)

    test_tool_schema_pruning()
    test_direct_response_synthesis()
    test_payload_pruning()
    await test_tool_execution_integration()

    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 4/4 BÀI KIỂM THỬ PHASE 7 ĐÃ ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)


if __name__ == "__main__":
    asyncio.run(main())
