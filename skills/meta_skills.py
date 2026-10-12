# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
skills/meta_skills.py
=====================
Bộ kỹ năng Tự Mở Rộng Hệ Thống (Meta Skills & Dynamic Runtime Expansion).
Cho phép Trợ lý AI và người dùng:
  - Tự động thiết kế, sinh mã nguồn Python cho kỹ năng mới.
  - Kiểm tra cú pháp AST, thẩm định an toàn bảo mật (Zero-Trust).
  - Nạp nóng (Hot-reload) kỹ năng vào bộ nhớ tức thời không cần khởi động lại server.
  - Quản lý và làm mới danh mục kỹ năng của hệ thống.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)

#: Mỗi danh tính tạo tối đa CREATE_LIMIT kỹ năng trong CREATE_WINDOW_S giây. Sự cố 2026-10-08: cửa sổ không hiện (máy chủ ở phiên
#: nền), AI tưởng thiếu kỹ năng và gọi create_new_skill 39 lần trong 30 phút, sinh nhiều kỹ năng gần giống nhau.
CREATE_LIMIT = 3
CREATE_WINDOW_S = 600
_recent: Dict[str, Deque[float]] = {}
_recent_lock = threading.Lock()


def _creation_slot(principal: str) -> Optional[str]:
    """None = được tạo (đã ghi nhận lượt); chuỗi = lý do từ chối."""
    now = time.monotonic()
    with _recent_lock:
        q = _recent.setdefault(principal, deque())
        while q and now - q[0] > CREATE_WINDOW_S:
            q.popleft()
        if len(q) >= CREATE_LIMIT:
            wait = int(CREATE_WINDOW_S - (now - q[0])) + 1
            return (f"Đã tạo {len(q)} kỹ năng trong {CREATE_WINDOW_S // 60} phút — dừng tạo thêm (thử lại sau {wait} s). "
                    "Nếu kỹ năng có sẵn đã chạy mà kết quả không như mong muốn, hãy báo người dùng lý do cụ thể "
                    "(lỗi trả về, máy chủ không có màn hình…) thay vì tạo kỹ năng mới gần giống.")
        q.append(now)
    return None


@export_skill(
    name="create_new_skill",
    description=(
        "Tạo KỸ NĂNG MỚI khi người dùng yêu cầu một VIỆC mà hiện chưa có công cụ nào làm được "
        "(vd mở bài hát/phát nhạc, tra cứu một trang web cụ thể, tự động hoá một thao tác), "
        "kể cả khi cần DỮ LIỆU TRỰC TUYẾN / THỜI GIAN THỰC chưa có công cụ lấy (giá vàng, tỷ giá, "
        "thời tiết, tin tức). "
        "Sinh mã Python, kiểm tra an toàn và nạp ngay; sau đó gọi kỹ năng mới để làm tiếp yêu cầu. "
        "KHÔNG dùng cho câu hỏi trò chuyện hay câu tự trả lời được bằng kiến thức. "
        "intent_description: mô tả đầy đủ việc cần làm, dùng chính lời người dùng."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "intent_description": {
                "type": "string",
                "description": "Mô tả chi tiết chức năng kỹ năng cần tạo (đầu vào, quy tắc xử lý, dữ liệu trả về).",
            },
            "skill_name": {
                "type": "string",
                "description": "Tên hàm tiếng Anh dạng snake_case (tùy chọn), ví dụ: calculate_tax, scan_lan_devices.",
            },
        },
        "required": ["intent_description"],
    },
)
def create_new_skill(intent_description: str, skill_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Sinh mã kỹ năng bằng MetaArchitect, kiểm tra an toàn, ghi tệp mới và nạp nóng.
    Toàn bộ quy trình ở `meta_architect.create_skill` (một nơi duy nhất).
    `skill_name` trả về là TÊN CÔNG CỤ đã đăng ký (lấy từ mã đã nạp).
    """
    from mateai.application.security.security_guard import CURRENT_PRINCIPAL
    refused = _creation_slot(str(CURRENT_PRINCIPAL.get() or "unknown"))
    if refused:
        logger.warning("[MetaSkills] Từ chối create_new_skill: %s", refused)
        return {"status": "error", "code": "SKILL_CREATION_RATE_LIMITED", "message": refused}
    from mateai.application.skills.meta_architect import meta_architect
    return meta_architect.create_skill(intent_description, skill_name=skill_name)


@export_skill(
    name="reload_all_skills",
    description="Nạp lại toàn bộ kho kỹ năng từ đĩa, phát hiện các module mới và cập nhật registry trong bộ nhớ.",
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
def reload_all_skills() -> Dict[str, Any]:
    """
    Quét lại thư mục skills/ và nạp nóng lại toàn bộ modules vào runtime.
    """
    from core.plugin_manager import plugin_manager

    try:
        count = plugin_manager.load_plugins()
        names = plugin_manager.get_skill_names()
        return {
            "status": "success",
            "message": f"Đã nạp lại thành công {count} kỹ năng vào bộ nhớ hệ thống.",
            "total_skills": count,
            "skills": names,
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Lỗi khi nạp lại kho kỹ năng: {str(exc)}",
        }


@export_skill(
    name="list_available_skills",
    description="Liệt kê danh sách tất cả các kỹ năng đang hoạt động trong hệ thống kèm trạng thái.",
    parameters_schema={
        "type": "object",
        "properties": {
            "filter_keyword": {
                "type": "string",
                "description": "Từ khóa lọc kỹ năng theo tên hoặc mô tả (tùy chọn).",
            }
        },
        "required": [],
    },
)
def list_available_skills(filter_keyword: Optional[str] = None) -> Dict[str, Any]:
    """
    Trả về danh sách kỹ năng hiện có trong registry kèm mô tả tóm tắt.
    """
    from core.plugin_manager import plugin_manager

    kw = (filter_keyword or "").lower().strip()
    with plugin_manager._lock:
        items = []
        for name, entry in plugin_manager._registry.items():
            meta = entry.get("meta", {})
            desc = meta.get("description", "")
            enabled = entry.get("enabled", True)
            if kw and (kw not in name.lower() and kw not in desc.lower()):
                continue
            items.append({
                "name": name,
                "description": desc,
                "module": entry.get("module", ""),
                "enabled": enabled,
            })

    return {
        "status": "success",
        "total": len(items),
        "skills": items,
    }
