"""
src/mateai/infrastructure/connectors/telegram_connector.py
==========================================================
Connector tích hợp Telegram Bot API có bảo vệ ngắt mạch và timeout.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional
import httpx

from mateai.infrastructure.connectors.base_connector import BaseEnterpriseConnector

logger = logging.getLogger(__name__)


class TelegramConnector(BaseEnterpriseConnector):
    """Adapter gửi và nhận tin nhắn từ Telegram Bot API an toàn."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 3
    ):
        super().__init__(
            name="TelegramConnector",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            circuit_failure_threshold=4,
            circuit_recovery_timeout=20.0
        )
        self.bot_token = bot_token or "dummy_telegram_token"
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    async def ping(self) -> bool:
        """Kiểm tra API Telegram còn hoạt động."""
        if self.bot_token == "dummy_telegram_token":
            return True
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{self.base_url}/getMe")
            return resp.status_code == 200

    async def send_message(self, chat_id: str, text: str) -> Dict[str, Any]:
        """Gửi thông báo qua Telegram với cơ chế bảo vệ ngắt mạch."""
        async def _send():
            if self.bot_token == "dummy_telegram_token":
                # Mock thành công trong môi trường dev/test
                return {"ok": True, "chat_id": chat_id, "text": text, "message_id": 9999}

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(
                    f"{self.base_url}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
                )
                resp.raise_for_status()
                return resp.json()

        return await self.execute_safe(_send)
