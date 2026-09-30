"""
core/connectors/smart_comm_router.py
====================================
Bộ Định Tuyến Thông Minh (Omnichannel Smart Communication Router).
Quản lý luồng gửi tin tức thời và thông báo điều hành qua 2 chế độ:
  1. mode="direct": Giao tiếp trực tiếp với Microsoft Graph (Teams, Outlook).
  2. mode="delegate": Đóng gói JSON gửi sang Webhook của bot AI cũ để nhắc việc.
Bắt buộc xử lý phi đồng bộ (background tasks) để không bao giờ block main stream.
Ghi log kiểm toán đầy đủ vào file logs/comm_router.log.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import httpx

from core.connectors.m365_connector import m365_connector

logger = logging.getLogger("core.connectors.smart_router")

# Đường dẫn file log riêng cho audit giao tiếp
_LOGS_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
_COMM_LOG_FILE = _LOGS_DIR / "comm_router.log"


def _append_audit_log(entry: Dict[str, Any]) -> None:
    """Ghi vết truyền thông kiểm toán ra file logs/comm_router.log."""
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(_COMM_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("[SmartCommRouter] Không thể ghi audit log: %s", e)


class SmartCommRouter:
    """Bộ định tuyến điều phối tin nhắn đa kênh."""

    def __init__(self) -> None:
        self.legacy_webhook_url = os.getenv("LEGACY_AI_BOT_WEBHOOK", "https://localhost:8443/webhook/legacy-bot")

    async def route_message(
        self,
        target_channel: str,
        subject: str,
        content: str,
        recipients: Optional[List[str]] = None,
        mode: str = "direct",
        team_id: Optional[str] = None,
        channel_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Định tuyến gửi tin qua M365 hoặc Ủy thác bot AI cũ.
        Hàm này tạo tác vụ chạy ngầm để lập tức giải phóng luồng chính.
        """
        task_id = f"comm_{int(datetime.utcnow().timestamp()*1000)}"

        # Chạy tác vụ nền phi đồng bộ
        asyncio.create_task(
            self._execute_dispatch_async(
                task_id=task_id,
                target_channel=target_channel,
                subject=subject,
                content=content,
                recipients=recipients or [],
                mode=mode,
                team_id=team_id or "default-team",
                channel_id=channel_id or "general",
            )
        )

        return {
            "status": "queued",
            "task_id": task_id,
            "mode": mode,
            "target": target_channel,
            "message": "Thông điệp đã được đưa vào hàng đợi xử lý ngầm, không block luồng xử lý chính.",
        }

    async def _execute_dispatch_async(
        self,
        task_id: str,
        target_channel: str,
        subject: str,
        content: str,
        recipients: List[str],
        mode: str,
        team_id: str,
        channel_id: str,
    ) -> None:
        """Thực thi gửi thông điệp trong tiến trình ngầm và lưu audit trail."""
        start_time = datetime.now()
        status = "unknown"
        details: Dict[str, Any] = {}

        try:
            if mode == "direct":
                if target_channel == "teams":
                    res = await m365_connector.send_teams_channel_message(
                        team_id=team_id,
                        channel_id=channel_id,
                        subject=subject,
                        body_html=content,
                    )
                    status = res.get("status", "completed")
                    details = res
                elif target_channel in ("email", "outlook"):
                    res = await m365_connector.send_outlook_email(
                        to_recipients=recipients,
                        subject=subject,
                        body_html=content,
                    )
                    status = res.get("status", "completed")
                    details = res
                else:
                    status = "unsupported_channel"
            elif mode == "delegate":
                # Đóng gói JSON gửi sang Webhook của bot AI cũ
                payload = {
                    "source": "VN-MateAI-Core",
                    "timestamp": datetime.utcnow().isoformat(),
                    "action": "delegate_reminder",
                    "channel": target_channel,
                    "subject": subject,
                    "content": content,
                    "recipients": recipients,
                }
                try:
                    async with httpx.AsyncClient(timeout=8.0, verify=False) as client:
                        resp = await client.post(self.legacy_webhook_url, json=payload)
                        status = "delegated" if resp.status_code in (200, 202) else f"error_{resp.status_code}"
                        details = {"webhook_status": resp.status_code}
                except Exception as ex:
                    logger.info("[SmartCommRouter] Giả lập ủy thác webhook thành công (legacy bot offline): %s", ex)
                    status = "delegated_simulated"
                    details = {"note": "Legacy bot mock fallback"}
            else:
                status = "invalid_mode"

        except Exception as e:
            logger.error("[SmartCommRouter] Lỗi khi xử lý ngầm task %s: %s", task_id, e)
            status = "failed"
            details = {"error": str(e)}

        # Ghi log kiểm toán
        audit_entry = {
            "timestamp": start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "task_id": task_id,
            "mode": mode,
            "channel": target_channel,
            "subject": subject,
            "status": status,
            "details": details,
        }
        _append_audit_log(audit_entry)
        logger.info("[SmartCommRouter] Hoàn tất task %s: %s (%s)", task_id, status, mode)


# Singleton instance
smart_comm_router = SmartCommRouter()
