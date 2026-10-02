"""
src/mateai/infrastructure/connectors/base_connector.py
======================================================
Lớp nền tảng cho các Connector Doanh Nghiệp (Base Enterprise Connector).

Mục tiêu:
- Tích hợp sẵn Circuit Breaker, Strict Timeout và Exponential Backoff Retry.
- Cách ly lỗi (Failure Isolation): Ngăn chặn một dịch vụ ngoài bị lỗi làm ảnh hưởng đến luồng chính của trợ lý.
- Chuẩn hóa đầu ra dạng JSON Dict để Agent/LLM dễ dàng tiêu thụ.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, TypeVar

from mateai.infrastructure.connectors.circuit_breaker import CircuitBreaker, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class ConnectorStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class ConnectorHealth:
    """Báo cáo tình trạng sức khỏe của Connector."""
    connector_name: str
    status: ConnectorStatus
    latency_ms: float = 0.0
    message: str = "OK"
    circuit_state: str = "CLOSED"


class BaseEnterpriseConnector(abc.ABC):
    """Lớp trừu tượng định hình tiêu chuẩn cho tất cả các Connector tích hợp."""

    def __init__(
        self,
        name: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 30.0
    ):
        self.name = name
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.circuit_breaker = CircuitBreaker(
            name=name,
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout
        )

    @abc.abstractmethod
    async def ping(self) -> bool:
        """Kiểm tra kết nối nhanh đến dịch vụ đích."""
        pass

    async def check_health(self) -> ConnectorHealth:
        """Thực hiện kiểm tra sức khỏe và đo độ trễ kết nối."""
        start_time = time.monotonic()
        try:
            is_alive = await self.execute_safe(self.ping)
            latency = (time.monotonic() - start_time) * 1000.0
            status = ConnectorStatus.HEALTHY if is_alive else ConnectorStatus.DEGRADED
            return ConnectorHealth(
                connector_name=self.name,
                status=status,
                latency_ms=latency,
                message="Kết nối bình thường" if is_alive else "Dịch vụ phản hồi bất thường",
                circuit_state=self.circuit_breaker.state.value
            )
        except CircuitBreakerOpenError as cb_err:
            return ConnectorHealth(
                connector_name=self.name,
                status=ConnectorStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                message=str(cb_err),
                circuit_state="OPEN"
            )
        except Exception as exc:
            return ConnectorHealth(
                connector_name=self.name,
                status=ConnectorStatus.UNHEALTHY,
                latency_ms=(time.monotonic() - start_time) * 1000.0,
                message=f"Lỗi kết nối: {exc}",
                circuit_state=self.circuit_breaker.state.value
            )

    async def execute_safe(
        self,
        action: Callable[..., Coroutine[Any, Any, T]],
        *args,
        retryable_exceptions: tuple = (asyncio.TimeoutError, ConnectionError, OSError),
        **kwargs
    ) -> T:
        """
        Thực thi hành động với Circuit Breaker, Timeout, và Exponential Backoff Retry.
        """
        async def _call_with_retry() -> T:
            last_exception: Optional[Exception] = None
            delay = 0.5

            for attempt in range(1, self.max_retries + 1):
                try:
                    return await asyncio.wait_for(
                        action(*args, **kwargs),
                        timeout=self.timeout_seconds
                    )
                except retryable_exceptions as exc:
                    last_exception = exc
                    if attempt < self.max_retries:
                        logger.warning(
                            "[Connector:%s] Thử lại lần %d/%d sau %.2fs do lỗi mạng: %s",
                            self.name, attempt, self.max_retries, delay, exc
                        )
                        await asyncio.sleep(delay)
                        delay *= 2
                    else:
                        logger.error("[Connector:%s] Đã thử %d lần nhưng thất bại.", self.name, self.max_retries)
                        raise
                except Exception as fatal_exc:
                    # Lỗi nghiệp vụ không nên retry (ví dụ 401 Unauthorized, 400 Bad Request)
                    logger.error("[Connector:%s] Gặp lỗi nghiêm trọng không retry: %s", self.name, fatal_exc)
                    raise fatal_exc

            if last_exception:
                raise last_exception
            raise RuntimeError(f"Hành động thất bại trên connector {self.name}")

        return await self.circuit_breaker.call(_call_with_retry)
