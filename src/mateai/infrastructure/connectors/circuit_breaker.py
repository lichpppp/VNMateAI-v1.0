"""
src/mateai/infrastructure/connectors/circuit_breaker.py
======================================================
Mô hình Ngắt Mạch Bảo Vệ Hệ Thống (Circuit Breaker Pattern).

Quy tắc:
- Tuân thủ RULE-008: Ngăn chặn hiện tượng lỗi dây chuyền (Cascading Failure) khi dịch vụ bên thứ ba bị treo hoặc sập.
- Trạng thái:
  + CLOSED: Bình thường, mọi cuộc gọi được thông qua.
  + OPEN: Ngắt mạch khi số lỗi liên tiếp vượt ngưỡng (failure_threshold), từ chối ngay lập tức (Fail-Fast) để không làm nghẽn tiến trình.
  + HALF_OPEN: Sau thời gian chờ hồi phục (recovery_timeout), cho phép một số cuộc gọi thử nghiệm để kiểm tra dịch vụ đã sống lại chưa.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(Exception):
    """Ngoại lệ phát sinh khi Circuit Breaker đang ở trạng thái OPEN."""
    pass


class CircuitBreaker:
    """Bộ ngắt mạch bảo vệ các kết nối dịch vụ ngoài."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 2
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_state_change = time.monotonic()
        self._half_open_successes = 0

    @property
    def state(self) -> CircuitState:
        # Tự động chuyển từ OPEN sang HALF_OPEN nếu đã hết thời gian recovery_timeout
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_state_change >= self.recovery_timeout:
                logger.info("[CircuitBreaker:%s] Hết thời gian chờ, chuyển sang HALF_OPEN để thăm dò.", self.name)
                self._state = CircuitState.HALF_OPEN
                self._last_state_change = time.monotonic()
                self._half_open_successes = 0
        return self._state

    async def call(self, coro_func: Callable[..., Coroutine[Any, Any, T]], *args, **kwargs) -> T:
        """Bọc một hàm async trong cơ chế ngắt mạch."""
        current_state = self.state

        if current_state == CircuitState.OPEN:
            raise CircuitBreakerOpenError(
                f"Dịch vụ ngoài '{self.name}' đang bị ngắt mạch (OPEN) do gặp lỗi liên tiếp. Vui lòng thử lại sau."
            )

        try:
            result = await coro_func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    def _on_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            if self._half_open_successes >= self.half_open_max_calls:
                logger.info("[CircuitBreaker:%s] Thử nghiệm thành công, đóng mạch trở lại (CLOSED).", self.name)
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._last_state_change = time.monotonic()
        else:
            self._failure_count = 0

    def _on_failure(self, exc: Exception) -> None:
        self._failure_count += 1
        logger.warning(
            "[CircuitBreaker:%s] Ghi nhận lỗi lần %d/%d: %s",
            self.name, self._failure_count, self.failure_threshold, exc
        )
        if self._failure_count >= self.failure_threshold or self._state == CircuitState.HALF_OPEN:
            logger.error(
                "[CircuitBreaker:%s] Vượt ngưỡng lỗi cho phép, kích hoạt NGẮT MẠCH (OPEN) trong %.1fs!",
                self.name, self.recovery_timeout
            )
            self._state = CircuitState.OPEN
            self._last_state_change = time.monotonic()
