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
import re
from typing import Any, Dict, Optional

from core.plugin_manager import export_skill

logger = logging.getLogger(__name__)


@export_skill(
    name="create_new_skill",
    description="Tự động thiết kế, sinh mã nguồn Python, kiểm tra an toàn AST và nạp nóng kỹ năng mới vào hệ thống VN-MateAI.",
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
    Sinh mã kỹ năng tự động bằng MetaArchitect, kiểm duyệt an toàn và nạp nóng vào hệ thống.
    """
    from core.meta_architect import meta_architect
    from core.plugin_manager import plugin_manager

    desc = intent_description.strip()
    if not desc:
        return {"status": "error", "message": "Mô tả kỹ năng không được để trống."}

    # Sinh tên file an toàn nếu không truyền
    if skill_name:
        safe_name = re.sub(r"[^\w]", "_", skill_name.strip()).strip("_")
    else:
        # Tự động rút trích slug ngắn
        raw_slug = re.sub(r"[^\w\s]", "", desc.lower())[:30].strip().replace(" ", "_")
        safe_name = f"auto_{raw_slug}" if raw_slug else "auto_skill"

    logger.info("Yêu cầu tự tạo kỹ năng mới: '%s' (mô tả: %s)", safe_name, desc[:80])

    try:
        # Bước 1: Sinh mã nguồn Python qua mô hình LLM chuyên trách
        code_str = meta_architect.synthesize_skill(
            intent_description=desc,
            failed_context={"target_name": safe_name},
        )

        # Bước 2: Kiểm tra AST, kiểm duyệt an ninh Zero-Trust, ghi ra đĩa và nạp nóng
        success = meta_architect.verify_and_install(code_str, safe_name)
        if not success:
            return {
                "status": "error",
                "message": f"Không thể cài đặt kỹ năng '{safe_name}' do không vượt qua kiểm định an toàn AST hoặc phê duyệt.",
            }

        # Lấy tên kỹ năng vừa đăng ký trong registry
        all_names = plugin_manager.get_skill_names()
        registered_name = safe_name if safe_name in all_names else (all_names[-1] if all_names else safe_name)

        return {
            "status": "success",
            "message": f"Kỹ năng mới '{registered_name}' đã được AI tự động tạo, kiểm tra an toàn và nạp nóng thành công vào hệ thống!",
            "skill_name": registered_name,
            "total_skills": len(all_names),
        }

    except Exception as exc:
        logger.error("Lỗi khi tự tạo kỹ năng '%s': %s", safe_name, exc)
        return {
            "status": "error",
            "message": f"Quá trình tự tạo kỹ năng thất bại: {str(exc)}",
        }


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
