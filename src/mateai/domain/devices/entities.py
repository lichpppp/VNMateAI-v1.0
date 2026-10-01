"""
src/mateai/domain/devices/entities.py
=====================================
Tầng Nghiệp vụ Quản lý Thiết bị (Devices & IoT Domain Entities).

Quy tắc:
- Tuân thủ RULE-001 & RULE-002: Pure Python, không phụ thuộc ESP32 hay WebSocket transport.
- Định nghĩa thiết bị kết nối (ESP32-S3 XiaoZhi, Client Agent, Browser HUD, Mobile).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional
import uuid


class DeviceType(str, Enum):
    ESP32_XIAOZHI = "esp32_xiaozhi"   # Thiết bị phần cứng IoT thông minh
    CLIENT_AGENT = "client_agent"     # Máy trạm Windows/macOS/Linux
    BROWSER_HUD = "browser_hud"       # Web Standby HUD
    MOBILE_CLIENT = "mobile_client"   # Ứng dụng điện thoại


class DeviceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    MAINTENANCE = "maintenance"


@dataclass
class Device:
    """Đại diện cho một thiết bị kết nối vào hệ sinh thái VN-MateAI."""
    device_id: str
    device_name: str
    device_type: DeviceType
    ip_address: Optional[str] = None
    mac_address: Optional[str] = None
    firmware_version: Optional[str] = None
    status: DeviceStatus = DeviceStatus.OFFLINE
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)
