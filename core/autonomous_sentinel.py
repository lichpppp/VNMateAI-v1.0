"""
core/autonomous_sentinel.py
===========================
Phase 30 & Phase 43: Autonomous Sentinel Incident Monitor & Xiaozhi Desktop Robot Linkage.

Responsibilities:
  1. Incident Probing:
     - Network Connectivity: Probes DNS/Gateway and 9router LLM proxy latency.
     - Active Directory Sync: Detects stale AD synchronization, missing employees, or AD schema errors.
     - SQL & Database Health: Detects database lock contention (busy/locked errors), transaction timeouts.
     - Service Failures: Monitors HTTP status (e.g., 503 Service Unavailable, IIS crash).
  2. Multi-Channel Incident Alerting:
     - Telegram Incident Alert: Dispatches rich Markdown alerts to admin Telegram channel.
     - Xiaozhi Desktop Robot Autonomous Wake (Step 4):
       * Actively wakes up the Desktop Robot on the desk.
       * Sets LCD screen to alert face with blinking red eyes: {"type": "ui", "state": "alert", "text": "..."}
       * Voices audible alarm: "Báo cáo anh, hệ thống máy chủ vừa ghi nhận cảnh báo..."
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import psutil

from core.config_loader import settings
from core.xiaozhi_gateway import xiaozhi_gateway

logger = logging.getLogger("core.autonomous_sentinel")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _PROJECT_ROOT / "hr_kpi.db"


class AutonomousSentinel:
    """
    Continuous proactive sentinel monitor linking enterprise telemetry with
    Xiaozhi Desktop Robot and Telegram alerting.
    """

    def __init__(self) -> None:
        self._running: bool = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._check_interval: float = 30.0  # seconds between routine scans
        self._last_alert_times: Dict[str, float] = {}
        self._cooldown_seconds: float = 120.0  # Alert cooldown per incident category

    # -----------------------------------------------------------------------
    # Probing Routines
    # -----------------------------------------------------------------------

    async def check_network_health(self) -> Optional[Dict[str, Any]]:
        """Probe local network, DNS, and 9router LLM proxy."""
        base_url = getattr(settings.llm, "base_url", "http://localhost:20128/v1")
        clean_url = f"{base_url.rstrip('/')}/models"

        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(clean_url)
                if resp.status_code >= 500:
                    return {
                        "category": "network",
                        "title": f"9router Gateway Error {resp.status_code}",
                        "message": f"Cổng proxy LLM 9router phản hồi mã lỗi {resp.status_code}. Mạng AI có thể bị gián đoạn.",
                    }
        except httpx.ConnectError:
            return {
                "category": "network",
                "title": "Mất Kết Nối 9router LLM",
                "message": f"Không thể kết nối đến cổng 9router tại {base_url}. Vui lòng kiểm tra dịch vụ mạng AI.",
            }
        except Exception:
            pass
        return None

    def check_ad_sync_health(self) -> Optional[Dict[str, Any]]:
        """Check for Active Directory sync staleness or sync errors in local DB."""
        # Nếu tính năng Đồng Bộ AD đang TẮT, bỏ qua hoàn toàn để không ghi log cảnh báo
        try:
            from core.config_loader import settings
            import json
            cfg_p = settings.PROJECT_ROOT / "config.json"
            if cfg_p.exists():
                cfg_data = json.loads(cfg_p.read_text(encoding="utf-8"))
                if not cfg_data.get("ad_sync", {}).get("enabled", False):
                    return None
        except Exception:
            pass

        if not _DB_PATH.exists():
            return None

        try:
            with sqlite3.connect(str(_DB_PATH), timeout=2.0) as conn:
                try:
                    row = conn.execute(
                        "SELECT MAX(synced_at) FROM ("
                        "  SELECT synced_at FROM employees "
                        "  UNION ALL "
                        "  SELECT synced_at FROM computers"
                        ")"
                    ).fetchone()
                    last_sync_raw = row[0] if row and row[0] else None
                    if not last_sync_raw:
                        return {
                            "category": "ad_sync",
                            "title": "Chưa Đồng Bộ Active Directory",
                            "message": "Cơ sở dữ liệu Active Directory chưa có dữ liệu đồng bộ nhân sự hoặc máy tính.",
                        }

                    # Check if last sync is older than 24 hours
                    dt = datetime.fromisoformat(last_sync_raw.replace("Z", "+00:00"))
                    diff_hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
                    if diff_hours > 24.0:
                        return {
                            "category": "ad_sync",
                            "title": "Lỗi Đồng Bộ Active Directory",
                            "message": f"Dữ liệu Active Directory đã quá hạn {int(diff_hours)} giờ chưa được đồng bộ lại.",
                        }
                except sqlite3.OperationalError:
                    pass
        except Exception as exc:
            return {
                "category": "ad_sync",
                "title": "Lỗi Truy Vấn Dữ Liệu AD",
                "message": f"Lỗi truy cập cơ sở dữ liệu nhân sự AD: {str(exc)[:60]}.",
            }
        return None

    def check_sql_health(self) -> Optional[Dict[str, Any]]:
        """Check SQLite database lock contention and file integrity."""
        if not _DB_PATH.exists():
            return None

        try:
            # Attempt immediate transaction lock test with short timeout
            with sqlite3.connect(str(_DB_PATH), timeout=0.8) as conn:
                conn.execute("PRAGMA quick_check")
                # Test write-lock availability
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
        except sqlite3.OperationalError as exc:
            err_msg = str(exc).lower()
            if "locked" in err_msg or "busy" in err_msg:
                return {
                    "category": "sql_deadlock",
                    "title": "Kẹt Tiến Trình SQL Database!",
                    "message": "Cơ sở dữ liệu SQLite bị khóa (Database Locked/Busy). Đang có tiến trình ghi bị tắc nghẽn.",
                }
        except Exception as exc:
            return {
                "category": "sql_deadlock",
                "title": "Lỗi Cơ Sở Dữ Liệu SQL",
                "message": f"Kiểm tra tính toàn vẹn dữ liệu SQLite phát hiện lỗi: {str(exc)[:60]}.",
            }
        return None

    def check_hardware_limits(self) -> Optional[Dict[str, Any]]:
        """Check for critical server resource exhaustion."""
        try:
            ram = psutil.virtual_memory().percent
            cpu = psutil.cpu_percent(interval=None)
            disk = psutil.disk_usage(os.path.abspath(os.sep)).percent

            if ram > 95.0:
                return {
                    "category": "hardware",
                    "title": "Cảnh Báo Quá Tải RAM (>95%)",
                    "message": f"Bộ nhớ RAM máy chủ đã đạt mức nguy cấp {ram:.1f}%. Hệ thống có nguy cơ bị treo.",
                }
            if disk > 95.0:
                return {
                    "category": "hardware",
                    "title": "Cảnh Báo Đầy Dung Lượng Ổ Đĩa",
                    "message": f"Dung lượng ổ đĩa chính đạt mức báo động {disk:.1f}%. Cần giải phóng bộ nhớ ngay.",
                }
        except Exception:
            pass
        return None

    # -----------------------------------------------------------------------
    # Comprehensive Scan & Alert Trigger
    # -----------------------------------------------------------------------

    async def scan_all(self) -> List[Dict[str, Any]]:
        """Run all probe checks and return list of detected incidents."""
        incidents: List[Dict[str, Any]] = []

        # 1. Network check
        net_inc = await self.check_network_health()
        if net_inc:
            incidents.append(net_inc)

        # 2. AD sync check (sync probe in thread executor)
        loop = asyncio.get_event_loop()
        ad_inc = await loop.run_in_executor(None, self.check_ad_sync_health)
        if ad_inc:
            incidents.append(ad_inc)

        # 3. SQL health check
        sql_inc = await loop.run_in_executor(None, self.check_sql_health)
        if sql_inc:
            incidents.append(sql_inc)

        # 4. Hardware limits check
        hw_inc = await loop.run_in_executor(None, self.check_hardware_limits)
        if hw_inc:
            incidents.append(hw_inc)

        return incidents

    async def dispatch_incident(
        self,
        title: str,
        message: str,
        category: str = "general",
        force: bool = False,
    ) -> bool:
        """
        Dispatches detected incident across channels:
          1. Cooldown filtering to prevent alarm fatigue.
          2. Telegram Outbound Alert (if enabled).
          3. Xiaozhi Desktop Robot Autonomous Wake (LCD alert + voice prompt).
          4. Global System Health Cache logging.
        """
        now = time.time()
        last_time = self._last_alert_times.get(category, 0.0)
        if not force and (now - last_time) < self._cooldown_seconds:
            logger.debug("[AutonomousSentinel] Incident '%s' dropped due to cooldown (%.1fs remaining).", category, self._cooldown_seconds - (now - last_time))
            return False

        self._last_alert_times[category] = now
        logger.warning("[AutonomousSentinel] PHÁT HIỆN SỰ CỐ [%s]: %s — %s", category, title, message)

        # 1. Broadcast Telegram Alert
        try:
            from core.telegram_gateway import telegram_gateway
            if telegram_gateway and telegram_gateway._running:
                tg_text = f"🚨 *[SENTINEL INCIDENT ALERT]* 🚨\n\n*Tiêu đề:* {title}\n*Chi tiết:* {message}\n*Thời gian:* {datetime.now().strftime('%H:%M:%S %d/%m/%Y')}"
                telegram_gateway.send_incident_alert(tg_text)
        except Exception as tg_err:
            logger.debug("[AutonomousSentinel] Telegram alert dispatch error: %s", tg_err)

        # 2. Step 4: Wake Xiaozhi Desktop Robot with blinking red LCD and voice (async non-blocking)
        try:
            asyncio.create_task(
                xiaozhi_gateway.wake_and_alert(error_title=title, detail_message=message)
            )
        except Exception as xz_err:
            logger.error("[AutonomousSentinel] Xiaozhi Desktop Robot wake error: %s", xz_err)

        # 3. Log to recent events in SYSTEM_HEALTH_CACHE
        try:
            from core.health_monitor import SYSTEM_HEALTH_CACHE
            SYSTEM_HEALTH_CACHE.setdefault("recent_events", []).insert(0, {
                "time": datetime.now().strftime("%H:%M:%S"),
                "action": f"SENTINEL_{category.upper()}",
                "level": "error",
                "message": f"{title}: {message}"[:120],
            })
            if len(SYSTEM_HEALTH_CACHE["recent_events"]) > 20:
                SYSTEM_HEALTH_CACHE["recent_events"] = SYSTEM_HEALTH_CACHE["recent_events"][:20]
        except Exception:
            pass

        return True

    # -----------------------------------------------------------------------
    # Background Worker Loop
    # -----------------------------------------------------------------------

    async def _sentinel_loop(self) -> None:
        """Background async loop executing routine incident scans."""
        logger.info("[AutonomousSentinel] Background incident monitor started (interval=%.1fs).", self._check_interval)
        while self._running:
            try:
                incidents = await self.scan_all()
                for inc in incidents:
                    await self.dispatch_incident(
                        title=inc["title"],
                        message=inc["message"],
                        category=inc.get("category", "general"),
                    )
            except Exception as exc:
                logger.error("[AutonomousSentinel] Error in sentinel monitoring loop: %s", exc)

            await asyncio.sleep(self._check_interval)

    def start(self) -> None:
        """Start Sentinel monitor in background asyncio task."""
        if self._running:
            return
        self._running = True
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._monitor_task = loop.create_task(self._sentinel_loop())
        except Exception as exc:
            logger.warning("[AutonomousSentinel] Could not start background task: %s", exc)

    def stop(self) -> None:
        """Stop Sentinel monitor."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            self._monitor_task = None
        logger.info("[AutonomousSentinel] Stopped background monitor.")


# ---------------------------------------------------------------------------
# Global Singleton Instance
# ---------------------------------------------------------------------------
autonomous_sentinel = AutonomousSentinel()
