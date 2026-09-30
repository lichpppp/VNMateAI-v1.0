"""
tests/test_phase90_computer_use.py
==================================
Unit & Integration Tests for Phase 90: Computer-Use & Self-Healing Worker Engine.
Sử dụng thư viện unittest chuẩn của Python.
"""

import asyncio
import base64
import json
import pathlib
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from core.schemas.computer_use_schema import (
    ActionType,
    GUIActionPayload,
    ActionResult,
    GUITaskRequest,
)
from workers.browser_session_vault import (
    BrowserSessionVault,
    STEALTH_INJECTION_SCRIPT,
)
from workers.self_healing_engine import (
    SelfHealingUIEngine,
    is_dynamic_class,
)
from workers.native_os_driver import NativeOSDriver
from core.plugins.computer_use_plugin import (
    tool_execute_gui_task,
    evaluate_task_risk,
    register_computer_use_tool,
)
from core.plugin_registry import plugin_registry


class TestPhase90ComputerUse(unittest.TestCase):

    # ================================================================
    # BƯỚC 1: SCHEMA GIAO THỨC COMPUTER-USE
    # ================================================================

    def test_action_types_enum(self):
        """Kiểm tra ActionType chứa đủ 8 hành vi theo đặc tả."""
        expected_actions = {
            "CLICK", "DOUBLE_CLICK", "MOVE", "TYPE_TEXT",
            "PRESS_KEY", "SCROLL", "DRAG_DROP", "CAPTURE_SCREEN"
        }
        actual_actions = {a.value for a in ActionType}
        self.assertTrue(expected_actions.issubset(actual_actions))

    def test_gui_action_payload_validation(self):
        """Kiểm tra khởi tạo và serialize GUIActionPayload với tọa độ chuẩn hóa."""
        payload = GUIActionPayload(
            action=ActionType.CLICK,
            coordinate=(500, 320),
            target_query="Nút Đăng nhập màu xanh",
            session_id="session_mac_01",
            timeout_ms=5000,
        )
        self.assertEqual(payload.action, "CLICK")
        self.assertEqual(payload.coordinate, (500, 320))
        self.assertEqual(payload.target_query, "Nút Đăng nhập màu xanh")
        self.assertEqual(payload.session_id, "session_mac_01")
        self.assertEqual(payload.timeout_ms, 5000)

        data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        self.assertEqual(data["action"], "CLICK")
        self.assertEqual(data["coordinate"], (500, 320))

    # ================================================================
    # BƯỚC 2: SESSION VAULT & STEALTH PROFILES
    # ================================================================

    def test_stealth_script_content(self):
        """Kiểm tra script stealth chứa đầy đủ cờ bypass anti-bot."""
        self.assertIn("navigator.webdriver", STEALTH_INJECTION_SCRIPT)
        self.assertIn("Apple Inc.", STEALTH_INJECTION_SCRIPT)
        self.assertIn("Apple M-series", STEALTH_INJECTION_SCRIPT)
        self.assertIn("MacIntel", STEALTH_INJECTION_SCRIPT)
        self.assertIn("AudioContext", STEALTH_INJECTION_SCRIPT)

    def test_session_state_export_import(self):
        """Kiểm tra export và import session state để đồng bộ giữa các worker node."""
        async def _run():
            with tempfile.TemporaryDirectory() as td:
                vault = BrowserSessionVault(base_profile_dir=td)
                session_id = "test_bank_session"

                sample_state = {
                    "cookies": [
                        {"name": "session_token", "value": "xyz123_secure", "domain": "bank.vn", "path": "/"}
                    ],
                    "origins": []
                }

                # Import state
                success = await vault.import_session_state(session_id, sample_state)
                self.assertTrue(success)

                # State file phải được tạo
                state_file = vault.get_state_file_path(session_id)
                self.assertTrue(state_file.exists())

                # Export state
                exported = await vault.export_session_state(session_id)
                self.assertEqual(exported["session_id"], session_id)
                self.assertEqual(exported["storage_state"]["cookies"][0]["value"], "xyz123_secure")

        asyncio.run(_run())

    # ================================================================
    # BƯỚC 3: DUAL-LAYER SELF-HEALING UI ENGINE
    # ================================================================

    def test_dynamic_css_detection(self):
        """Kiểm tra bộ lọc class động (Tailwind / CSS Modules / hash)."""
        self.assertTrue(is_dynamic_class("css-1a2b3c"))
        self.assertTrue(is_dynamic_class("tw-x98yz"))
        self.assertTrue(is_dynamic_class("styled-button-123"))
        self.assertFalse(is_dynamic_class("btn-primary"))
        self.assertFalse(is_dynamic_class("login-submit"))

    def test_self_healing_vision_fallback_center_calculation(self):
        """Kiểm tra Tầng 2 Vision Fallback tính toán đúng tâm điểm."""
        async def _run():
            engine = SelfHealingUIEngine(redis_client=None)

            from PIL import Image
            import io
            img = Image.new("RGB", (800, 600), color=(255, 255, 255))
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            screenshot_bytes = buf.getvalue()

            coord = await engine.find_by_vision(screenshot_bytes, "Nút Xác Nhận")
            self.assertIsNotNone(coord)
            x, y = coord
            self.assertTrue(0 <= x <= 800)
            self.assertTrue(0 <= y <= 600)

        asyncio.run(_run())

    def test_self_healing_cache_flow(self):
        """Kiểm tra ghi và đọc cache Self-Healing."""
        async def _run():
            engine = SelfHealingUIEngine(redis_client=None)
            system = "VCB_Portal"
            query = "Nút Đăng Nhập"
            target_coord = (450, 280)

            # Lưu cache
            await engine.save_healed_cache(system, query, target_coord, source="vision_fallback")

            # Đọc lại cache
            cached = await engine.get_cached_healing(system, query)
            self.assertIsNotNone(cached)
            self.assertEqual(tuple(cached["coordinate"]), target_coord)
            self.assertEqual(cached["source"], "vision_fallback")

        asyncio.run(_run())

    # ================================================================
    # BƯỚC 4: NATIVE OS DRIVER
    # ================================================================

    def test_native_os_driver_methods(self):
        """Kiểm tra các method của NativeOSDriver hoạt động an toàn."""
        driver = NativeOSDriver()

        # Click
        click_res = driver.mouse_click(100, 200, button="left")
        self.assertTrue(click_res)

        # Type UTF-8 tiếng Việt có dấu
        type_res = driver.type_utf8("Công ty Cổ phần Công nghệ VN-MateAI")
        self.assertTrue(type_res)

        # Screenshot base64
        b64 = driver.capture_active_window()
        self.assertIsInstance(b64, str)
        self.assertGreater(len(b64), 0)
        decoded = base64.b64decode(b64)
        self.assertGreater(len(decoded), 0)

    # ================================================================
    # BƯỚC 5: COMPUTER-USE PLUGIN & HITL GATE
    # ================================================================

    def test_risk_evaluation(self):
        """Kiểm tra tự động nâng risk_level=4 khi có từ khóa tài chính/chuyển tiền/duyệt lệnh."""
        self.assertEqual(evaluate_task_risk("Kiểm tra trạng thái máy chủ"), 2)
        self.assertEqual(evaluate_task_risk("Chuyển tiền 10 triệu đồng cho đối tác"), 4)
        self.assertEqual(evaluate_task_risk("Duyệt lệnh thanh toán hóa đơn VAT"), 4)
        self.assertEqual(evaluate_task_risk("Approve financial payout"), 4)

    def test_tool_execute_gui_task_voice_reply_and_queue(self):
        """Kiểm tra phản hồi voice và đóng gói task vào queue."""
        async def _run():
            res = await tool_execute_gui_task(
                task_goal="Mở trang chủ và xem danh mục",
                system_target="Web Portal",
                session_id="sess_001",
            )
            self.assertTrue(res["success"])
            self.assertEqual(res["risk_level"], 2)
            self.assertEqual(res["voice_reply"], "Em đã giao lệnh tự động hóa giao diện cho worker xử lý trong phiên làm việc an toàn.")

        asyncio.run(_run())

    def test_tool_execute_gui_task_high_risk_hitl(self):
        """Kiểm tra task tài chính kích hoạt risk_level=4 và Telegram HITL."""
        async def _run():
            with patch("core.zero_trust.hitl_manager.request_approval", new_callable=AsyncMock) as mock_hitl:
                mock_hitl.return_value = {"approval_id": "appr_test_123", "status": "pending"}

                res = await tool_execute_gui_task(
                    task_goal="Chuyển tiền lương tháng cho nhân viên",
                    system_target="VCB Digibank",
                    session_id="sess_finance_01",
                )

                self.assertTrue(res["success"])
                self.assertEqual(res["risk_level"], 4)
                self.assertEqual(res["status"], "awaiting_approval")
                self.assertEqual(res["voice_reply"], "Em đã giao lệnh tự động hóa giao diện cho worker xử lý trong phiên làm việc an toàn.")
                mock_hitl.assert_called_once()

        asyncio.run(_run())

    def test_plugin_registration(self):
        """Kiểm tra tool_execute_gui_task đã được đăng ký vào PluginRegistry."""
        stats = register_computer_use_tool()
        self.assertTrue(stats.get("registered") == 1 or stats.get("tool_name") == "tool_execute_gui_task")
        self.assertIn("tool_execute_gui_task", plugin_registry._tools)


if __name__ == "__main__":
    unittest.main()
