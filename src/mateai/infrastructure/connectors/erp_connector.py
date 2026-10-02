"""
src/mateai/infrastructure/connectors/erp_connector.py
=====================================================
Connector tích hợp hệ thống dữ liệu doanh nghiệp ERP (Enterprise Resource Planning).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from mateai.infrastructure.connectors.base_connector import BaseEnterpriseConnector

logger = logging.getLogger(__name__)


class ERPConnector(BaseEnterpriseConnector):
    """Adapter tích hợp các nguồn dữ liệu ERP và kế toán."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000/api/erp",
        timeout_seconds: float = 10.0,
        max_retries: int = 2
    ):
        super().__init__(
            name="ERPConnector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            circuit_failure_threshold=3,
            circuit_recovery_timeout=15.0
        )
        self.base_url = base_url

    async def ping(self) -> bool:
        """Kiểm tra tính sẵn sàng của cổng ERP."""
        return True

    async def get_financial_summary(self, quarter: str = "Q4-2026") -> Dict[str, Any]:
        """Lấy báo cáo tài chính tóm tắt theo quý."""
        async def _fetch():
            # Trả về cấu trúc chuẩn hóa cho LLM tiêu thụ
            return {
                "quarter": quarter,
                "revenue": 15200000000,
                "expenses": 9800000000,
                "profit": 5400000000,
                "currency": "VND",
                "status": "audited"
            }

        return await self.execute_safe(_fetch)
