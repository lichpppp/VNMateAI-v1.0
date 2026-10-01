"""
tests/test_tts_text_sanitiser.py
================================
Một hàm làm sạch văn bản cho TTS dùng chung cho mọi kênh voice
(`core.audio.sentence_streamer.sanitise_for_tts`) + một hàm rút gọn lời nói
(`shorten_for_speech`).

Trước Phase 2 có ba hàm khác nhau (portal / HUD / ESP32 + mic), nên cùng một câu
trả lời được đọc khác nhau tuỳ kênh, và mỗi hàm có lỗi riêng: đọc to URL / bảng
/ khối code, "Wi-Fi" thành "Wi, Fi", "get_current_time" thành "getcurrenttime".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.audio.sentence_streamer import sanitise_for_tts, shorten_for_speech  # noqa: E402
from core.audio.tts_stream_engine import apply_pronunciation  # noqa: E402


@pytest.mark.parametrize("raw, expected", [
    # Markdown đậm bỏ; số thập phân giữ nguyên; chữ viết tắt GIỮ (vẫn để hiển thị)
    ("**CPU** đang ở mức 32,5%, RAM còn 4.2 GB.", "CPU đang ở mức 32,5%, RAM còn 4.2 GB."),
    # Gạch nối trong từ / số giữ nguyên; gạch có khoảng trắng hai bên -> dấu phẩy
    ("Máy 192.168.1.27 chạy Wi-Fi, lỗi COVID-19 - cần kiểm tra.",
     "Máy 192.168.1.27 chạy Wi-Fi, lỗi COVID-19, cần kiểm tra."),
    # Bullet + khối code
    ("Kết quả:\n- Dòng 1\n- Dòng 2\n```bash\nls -la\n```", "Kết quả: Dòng 1 Dòng 2 em đã thực thi xong."),
    # Khối JSON bỏ cả khối, emoji bỏ
    ('Tool trả về {"status": "ok", "count": 3} rồi ạ ✅', "Tool trả về rồi ạ"),
    # Link Markdown giữ nhãn, URL trần bỏ
    ("Xem tại [tài liệu](https://example.com/doc) hoặc https://x.vn", "Xem tại tài liệu hoặc"),
    # Chú thích giọng nói ẩn và bảng không bị đọc
    ("<!--VOICE:Đây là bản đọc--> Bảng:\n| a | b |\n|---|---|", "Bảng:."),
    # snake_case đọc thành từng từ
    ("Dạ, API của VN-MateAI dùng get_current_time() để lấy giờ.",
     "Dạ, API của VN-MateAI dùng get current time để lấy giờ."),
    # Bullet tròn
    ("• Ổ C còn 120 GB", "Ổ C còn 120 GB"),
    # In nghiêng bằng gạch dưới vẫn được bỏ dấu
    ("Đây là _quan trọng_ nhé.", "Đây là quan trọng nhé."),
])
def test_sanitise_for_tts(raw, expected):
    assert sanitise_for_tts(raw) == expected


def test_sanitise_empty():
    assert sanitise_for_tts("") == ""
    assert sanitise_for_tts("   ") == ""


def test_shorten_keeps_short_text():
    t = "Dạ, em đã kiểm tra xong."
    assert shorten_for_speech(t) == t


def test_shorten_long_text_keeps_whole_sentences():
    long = " ".join(f"Đây là câu thông tin số {i} trong báo cáo dài." for i in range(1, 15))
    out = shorten_for_speech(long)
    assert len(out) < len(long)
    assert out.startswith("Đây là câu thông tin số 1 trong báo cáo dài.")
    assert out.endswith("Chi tiết cụ thể đã hiển thị trên màn hình.")


def test_pronunciation_only_at_tts_boundary():
    """Gợi ý phát âm áp ở engine TTS, chữ hiển thị không bị đổi thành 'C P U'."""
    assert apply_pronunciation("API của VN-MateAI, CPU và RAM, máy PC-01") == (
        "A P I của VN Mate AI, C P U và Ram, máy PC 01"
    )
    assert "C P U" not in sanitise_for_tts("CPU là 32%.")
