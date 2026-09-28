"""
core/audio_cache.py
===================
Phase 36: Dynamic Audio Cache & Reflex Vocabulary Store.

Quản lý bộ nhớ đệm âm thanh cục bộ sử dụng thuật toán băm MD5:
  - Băm nội dung câu thoại (chuẩn hóa khoảng trắng, chữ thường).
  - Lưu và truy xuất file .mp3 từ storage/audio_cache/[hash].mp3.
  - Phục vụ tức thì với độ trễ 0ms cho các câu phản xạ, từ đệm, thông báo hệ thống.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Base directory for cached audio files
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "storage" / "audio_cache"
INDEX_FILE = CACHE_DIR / "cache_index.json"

_cache_lock = threading.Lock()


def _ensure_cache_dir() -> Path:
    """Ensure storage/audio_cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def normalize_text_for_hash(text: str) -> str:
    """
    Chuẩn hóa văn bản trước khi tạo mã băm:
    - Loại bỏ dấu cách thừa, tab, xuống dòng.
    - Chuyển thành chữ thường.
    - Loại bỏ dấu câu cơ bản ở cuối để tối ưu hóa tái sử dụng.
    """
    if not text:
        return ""
    # Chuyển thường và gộp khoảng trắng
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    # Bỏ dấu câu cuối câu (. ! ? : ;) để "chờ em một chút ạ." khớp với "chờ em một chút ạ"
    cleaned = re.sub(r"[\.!\?:;…]+$", "", cleaned).strip()
    return cleaned


def get_audio_hash(text: str) -> str:
    """
    Trả về mã MD5 của chuỗi text (đã chuyển lowercase và xóa khoảng trắng thừa).
    """
    norm = normalize_text_for_hash(text)
    if not norm:
        return ""
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def check_cached_audio(text: str) -> Optional[str]:
    """
    Kiểm tra nếu file [hash].mp3 tồn tại trong storage/audio_cache/,
    trả về đường dẫn tệp (str). Nếu không tồn tại hoặc rỗng, trả về None.
    """
    h = get_audio_hash(text)
    if not h:
        return None

    file_path = CACHE_DIR / f"{h}.mp3"
    if file_path.is_file() and file_path.stat().st_size > 100:
        return str(file_path)
    return None


def get_cached_audio_bytes(text: str) -> Optional[bytes]:
    """
    Lấy trực tiếp nội dung bytes âm thanh từ file cache nếu tồn tại.
    """
    path_str = check_cached_audio(text)
    if not path_str:
        return None
    try:
        with open(path_str, "rb") as f:
            data = f.read()
            if len(data) > 100:
                return data
    except Exception as exc:
        logger.warning("[AudioCache] Lỗi đọc file cache %s: %s", path_str, exc)
    return None


def save_to_cache(text: str, audio_bytes: bytes, voice: str = "vi-VN-HoaiMyNeural") -> Optional[str]:
    """
    Lưu luồng bytes âm thanh sinh ra từ TTS thành file [hash].mp3.
    Cập nhật cache_index.json để dễ theo dõi.
    """
    if not audio_bytes or len(audio_bytes) < 100:
        return None

    h = get_audio_hash(text)
    if not h:
        return None

    _ensure_cache_dir()
    file_path = CACHE_DIR / f"{h}.mp3"

    with _cache_lock:
        try:
            # Ghi file nhị phân mp3
            with open(file_path, "wb") as f:
                f.write(audio_bytes)

            # Cập nhật metadata index
            _update_index(h, text, voice, len(audio_bytes))
            logger.info("[AudioCache] Đã lưu cache âm thanh: hash=%s text='%s' (%d bytes)", h, text[:50], len(audio_bytes))
            return str(file_path)
        except Exception as exc:
            logger.error("[AudioCache] Lỗi lưu cache âm thanh: %s", exc)
            return None


def _update_index(h: str, text: str, voice: str, size: int) -> None:
    """Cập nhật tệp chỉ mục cache_index.json."""
    index_data: Dict[str, dict] = {}
    if INDEX_FILE.is_file():
        try:
            with open(INDEX_FILE, "r", encoding="utf-8") as f:
                index_data = json.load(f)
        except Exception:
            index_data = {}

    index_data[h] = {
        "text": text,
        "norm_text": normalize_text_for_hash(text),
        "voice": voice,
        "size_bytes": size,
        "file_name": f"{h}.mp3",
    }

    try:
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("[AudioCache] Không thể cập nhật cache_index.json: %s", exc)


def get_cache_stats() -> dict:
    """Trả về thống kê số lượng file và dung lượng bộ nhớ đệm."""
    _ensure_cache_dir()
    mp3_files = list(CACHE_DIR.glob("*.mp3"))
    total_size = sum(f.stat().st_size for f in mp3_files)
    return {
        "total_files": len(mp3_files),
        "total_size_bytes": total_size,
        "total_size_mb": round(total_size / (1024 * 1024), 2),
        "cache_dir": str(CACHE_DIR),
    }
