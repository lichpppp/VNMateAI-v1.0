"""
tests/unit/test_connectors.py
=============================
Unit Test Suite cho Bounded Context External Connectors (Phase 9).
Kiểm tra Circuit Breaker, Exponential Retry, và Failure Isolation.
"""

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from mateai.infrastructure.connectors.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    CircuitState,
)
from mateai.infrastructure.connectors.base_connector import (
    BaseEnterpriseConnector,
    ConnectorStatus,
)
from mateai.infrastructure.connectors.telegram_connector import TelegramConnector
from mateai.infrastructure.connectors.erp_connector import ERPConnector


async def test_circuit_breaker_trip_and_recover():
    print("\n▸ 1. Kiểm thử Circuit Breaker (Ngắt mạch tự động & Hồi phục)")
    cb = CircuitBreaker("TestService", failure_threshold=3, recovery_timeout=0.5)  # đủ rộng cho máy đang tải (Windows tick ~15 ms)
    assert cb.state == CircuitState.CLOSED

    # Giả lập hàm luôn ném ngoại lệ
    async def fail_func():
        raise ConnectionError("Máy chủ ngoài không phản hồi")

    # 3 lần lỗi liên tiếp -> Kích hoạt OPEN
    for i in range(3):
        try:
            await cb.call(fail_func)
        except ConnectionError:
            pass

    assert cb.state == CircuitState.OPEN
    print("  ✅ Circuit Breaker đã chuyển sang trạng thái OPEN sau 3 lần lỗi liên tiếp.")

    # Khi OPEN, gọi hàm lập tức bị chặn mà không thực thi hàm gốc (Fail-Fast)
    try:
        await cb.call(fail_func)
        assert False, "Kỳ vọng ném CircuitBreakerOpenError"
    except CircuitBreakerOpenError as e:
        print(f"  ✅ Fail-Fast thành công khi OPEN: {e}")

    # Chờ 0.6s để hết recovery_timeout -> chuyển sang HALF_OPEN
    await asyncio.sleep(0.6)
    assert cb.state == CircuitState.HALF_OPEN
    print("  ✅ Hết thời gian chờ, Circuit Breaker tự động chuyển sang HALF_OPEN.")

    # Cho 2 lần thành công để đóng mạch trở lại CLOSED
    async def success_func():
        return "OK"

    await cb.call(success_func)
    await cb.call(success_func)
    assert cb.state == CircuitState.CLOSED
    print("  ✅ Thử nghiệm thành công, Circuit Breaker đã đóng mạch lại CLOSED.")


async def test_connector_health_and_retry():
    print("\n▸ 2. Kiểm thử BaseEnterpriseConnector (Health Check & Retry)")
    
    attempts = 0
    class FlakyConnector(BaseEnterpriseConnector):
        def __init__(self):
            super().__init__(name="FlakyAPI", timeout_seconds=1.0, max_retries=3)

        async def ping(self):
            return True

        async def flaky_fetch(self):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError(f"Mất kết nối tạm thời lần {attempts}")
            return {"data": "final_success"}

    conn = FlakyConnector()
    health = await conn.check_health()
    assert health.status == ConnectorStatus.HEALTHY
    print(f"  ✅ Health check đạt: {health.connector_name} - {health.status.value} ({health.latency_ms:.2f}ms)")

    # Test retry: Lần 1 và 2 lỗi, lần 3 thành công
    res = await conn.execute_safe(conn.flaky_fetch)
    assert res["data"] == "final_success"
    assert attempts == 3
    print(f"  ✅ Exponential backoff retry thành công sau {attempts} lần thử.")


async def test_concrete_connectors():
    print("\n▸ 3. Kiểm thử Concrete Connectors (Telegram & ERP)")
    
    # Telegram
    telegram = TelegramConnector()
    msg_res = await telegram.send_message(chat_id="12345", text="Thông báo từ VN-MateAI")
    assert msg_res["ok"] is True
    print(f"  ✅ TelegramConnector gửi tin nhắn an toàn: message_id={msg_res['message_id']}")

    # ERP
    erp = ERPConnector()
    erp_res = await erp.get_financial_summary("Q4-2026")
    assert erp_res["profit"] > 0
    print(f"  ✅ ERPConnector truy vấn tài chính thành công: Doanh thu={erp_res['revenue']:,} VND")


if __name__ == "__main__":
    print("=" * 65)
    print("BẮT ĐẦU KIỂM THỬ BOUNDED CONTEXT CONNECTORS (PHASE 9)")
    print("=" * 65)
    asyncio.run(test_circuit_breaker_trip_and_recover())
    asyncio.run(test_connector_health_and_retry())
    asyncio.run(test_concrete_connectors())
    print("\n" + "=" * 65)
    print("🎉 TẤT CẢ 3/3 BÀI KIỂM THỬ CONNECTORS ĐẠT 100% THÀNH CÔNG!")
    print("=" * 65)
