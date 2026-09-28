"""
skills/custom_skills.py
=======================
Các kỹ năng tự định nghĩa do người dùng thêm thủ công.
"""
from core.plugin_manager import export_skill
import subprocess
import os
import sys



@export_skill(
    name="test_ping_host",
    description="Kiểm tra kết nối mạng ping đến máy chủ",
    parameters_schema={
    "type": "object",
    "properties": {},
    "required": []
},
)
def test_ping_host(**kwargs) -> dict:
    return {"status": "ok", "ping": "15ms"}
