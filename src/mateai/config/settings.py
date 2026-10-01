"""
src/mateai/config/settings.py
==============================
Hệ thống cấu hình trung tâm (Centralized Enterprise Settings) cho VN-MateAI.

Hỗ trợ:
- Các môi trường: development, testing, staging, production.
- Đọc biến môi trường (Environment Variables) ghi đè tệp config.json.
- Tự động fallback về cấu hình mặc định an toàn.
- Phân tách cấu hình hạ tầng (Database, Redis, Storage) và dịch vụ AI (LLM, TTS, STT).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Xác định đường dẫn gốc của dự án
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_JSON_PATH = PROJECT_ROOT / "config.json"


class LLMConfig(BaseModel):
    base_url: str = Field(default="http://localhost:20128/v1", description="URL endpoint của LLM gateway hoặc provider")
    model_name: str = Field(default="deepseek-chat", description="Tên model mặc định")
    api_key: str = Field(default="sk-dummy", description="API Key xác thực")
    routing_mode: str = Field(default="router", description="Chế độ routing: router | direct | auto")
    direct_url: str = Field(default="", description="URL direct provider nếu dùng direct mode")
    direct_model: str = Field(default="", description="Tên direct model")
    direct_api_key: str = Field(default="", description="API key cho direct mode")
    timeout_seconds: float = Field(default=30.0, description="Timeout tối đa cho mỗi request LLM")


class VoicePipelineConfig(BaseModel):
    tts_voice: str = Field(default="vi-VN-HoaiMyNeural", description="Giọng đọc tiếng Việt chuẩn")
    tts_rate: str = Field(default="+50%", description="Tốc độ đọc giọng nói")
    tts_volume: str = Field(default="+0%", description="Âm lượng giọng đọc")
    sample_rate: int = Field(default=24000, description="Tần số lấy mẫu âm thanh (Hz)")
    binary_chunk_size: int = Field(default=16384, description="Kích thước tối đa mỗi frame nhị phân WebSocket")
    max_sentence_buffer_chars: int = Field(default=250, description="Ngưỡng ký tự tối đa trước khi ép ngắt câu")


class DatabaseConfig(BaseModel):
    system_of_record: str = Field(default="sqlite", description="Hệ thống lưu trữ chính: sqlite | postgresql")
    sqlite_db_path: str = Field(default=str(PROJECT_ROOT / "vnmateai.db"), description="Đường dẫn file SQLite chính")
    sqlite_hr_path: str = Field(default=str(PROJECT_ROOT / "hr_kpi.db"), description="Đường dẫn file SQLite HR")
    postgres_dsn: Optional[str] = Field(default=None, description="PostgreSQL Connection URI (postgresql+asyncpg://...)")
    pool_size: int = Field(default=10, description="Kích thước pool kết nối cơ sở dữ liệu")
    max_overflow: int = Field(default=20, description="Số kết nối tối đa vượt ngưỡng pool")


class RedisConfig(BaseModel):
    enabled: bool = Field(default=False, description="Kích hoạt Redis distributed cache & state")
    url: str = Field(default="redis://localhost:6379/0", description="Redis connection URL")
    session_ttl_seconds: int = Field(default=86400, description="Thời gian sống của session thoại")


class SecurityConfig(BaseModel):
    jwt_secret_key: str = Field(default="change-me-in-production-vn-mateai-secret-key-32bytes", description="Khoá bí mật JWT")
    jwt_algorithm: str = Field(default="HS256", description="Thuật toán mã hóa JWT")
    token_expire_minutes: int = Field(default=1440, description="Thời hạn token đăng nhập (phút)")
    cors_allowed_origins: List[str] = Field(default=["*"], description="Danh sách domain được phép CORS")
    rate_limit_per_minute: int = Field(default=120, description="Số request tối đa mỗi phút")


class AppSettings(BaseSettings):
    """Cấu hình toàn diện cho ứng dụng VN-MateAI Enterprise."""
    environment: str = Field(default="development", description="Môi trường runtime: development | staging | production")
    host: str = Field(default="0.0.0.0", description="Địa chỉ IP lắng nghe")
    port: int = Field(default=8000, description="Cổng dịch vụ HTTP/WebSocket")
    log_level: str = Field(default="INFO", description="Mức độ ghi log: DEBUG | INFO | WARNING | ERROR")
    
    # Sub-configurations
    llm: LLMConfig = Field(default_factory=LLMConfig)
    voice: VoicePipelineConfig = Field(default_factory=VoicePipelineConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    model_config = SettingsConfigDict(
        env_prefix="MATEAI_",
        env_nested_delimiter="__",
        extra="ignore"
    )

    @classmethod
    def load_from_json(cls, config_path: Path = CONFIG_JSON_PATH) -> AppSettings:
        """Đọc và kết hợp cấu hình từ file config.json hiện có."""
        data: Dict[str, Any] = {}
        if config_path.exists():
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"[Settings] Cảnh báo: Không thể đọc {config_path}: {e}")

        # Map legacy keys if present
        llm_data = data.get("llm", {})
        if "llm" in data:
            del data["llm"]
        
        settings = cls(llm=LLMConfig(**llm_data)) if llm_data else cls()
        return settings


# Singleton instance
settings = AppSettings.load_from_json()
