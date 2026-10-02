"""
core/audio/sentence_streamer.py
================================
Chuẩn hoá văn bản cho lời nói — DUY NHẤT cho mọi kênh voice (Phase 2).

  - sanitise_for_tts(text):   bỏ phần không đọc được (Markdown, code, JSON, URL,
                              bảng, emoji…). Kết quả vẫn dùng để hiển thị.
  - shorten_for_speech(text): rút lời nói dài về 1–2 câu đầu khi đọc nguyên đoạn.

Trước Phase 2 có ba hàm làm sạch khác nhau (`llm_engine._sanitise_for_tts`,
`audio_processor.clean_text_for_tts` và hàm này), nên cùng câu trả lời được đọc
khác nhau tuỳ kênh. Gợi ý phát âm (A P I, C P U…) nằm ở
`tts_stream_engine.apply_pronunciation`, không ở đây.

Tách câu từ luồng token: `core/audio/sentence_buffer.py` (lớp `SentenceStreamer`
cũ chỉ bọc lại SentenceBuffer và đã được gỡ).
"""

from __future__ import annotations

import re

#: Emoji / ký hiệu hình — TTS đọc vấp hoặc đọc tên ký hiệu.
_EMOJI_RE = re.compile('[\U0001F300-\U0001FAFF☀-➿️‍]')


def sanitise_for_tts(text: str) -> str:
    """
    Chuẩn hoá văn bản trước khi đưa vào TTS — hàm DUY NHẤT cho mọi kênh voice.

    Loại bỏ: Markdown, khối code, khối JSON, bullet, URL, bảng, emoji, ký tự đặc
    biệt. Giữ nguyên số thập phân, IP, và gạch nối trong từ (Wi-Fi, COVID-19).
    Kết quả vẫn dùng để HIỂN THỊ (HUD, ESP32), nên gợi ý phát âm không đặt ở
    đây mà ở `tts_stream_engine.apply_pronunciation`.
    """
    if not text:
        return ""

    # Bỏ comment giọng nói ẩn <!--VOICE:...-->
    text = re.sub(r'<!--VOICE:.*?-->', '', text, flags=re.DOTALL)

    # Thay code block bằng câu nói tắt
    text = re.sub(r'```[\s\S]*?```', 'em đã thực thi xong.', text)
    text = re.sub(r'`[^`]+`', '', text)

    # Bỏ nguyên khối JSON (kết quả tool lọt vào câu trả lời)
    text = re.sub(r'\{[^{}]{0,200}\}', '', text)

    # Bỏ tiêu đề Markdown
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Bỏ định dạng bold/italic. Gạch dưới chỉ là in nghiêng khi đứng ngoài từ —
    # `get_current_time` là tên, không phải in nghiêng.
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'(?<!\w)_{1,3}([^_\n]+?)_{1,3}(?!\w)', r'\1', text)

    # Bỏ bullet và list đánh số
    text = re.sub(r'^\s*[-*+•]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)

    # Bỏ link Markdown (giữ nhãn) và URL
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)

    # Bỏ bảng và phân cách
    text = re.sub(r'\|[^\n]*', '', text)
    text = re.sub(r'-{3,}', '', text)
    text = re.sub(r'={3,}', '', text)

    text = _EMOJI_RE.sub(' ', text)

    # snake_case đọc thành từng từ
    text = re.sub(r'(?<=\w)_(?=\w)', ' ', text)
    # Gạch ngang giữa hai vế câu -> ngắt nghỉ; gạch nối trong từ giữ nguyên
    text = re.sub(r'\s+[-–—]\s+', ', ', text)

    # Bỏ ký tự đặc biệt không phát âm được
    text = re.sub(r'[#@&^~\\<>{}[\]()]', '', text)

    # Chuẩn hoá xuống dòng
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)

    # Bỏ khoảng trắng thừa
    text = re.sub(r'\s{2,}', ' ', text)

    return text.strip()


def shorten_for_speech(text: str, max_chars: int = 200) -> str:
    """
    Rút lời nói dài về 1–2 câu đầu (giữ trọn câu) + báo chi tiết trên màn hình.

    Dùng khi đọc NGUYÊN câu trả lời một lần (ESP32, mic máy chủ, REST); đường
    stream từng câu không cần. Trước Phase 2 nằm trong `clean_text_for_tts`.
    """
    if len(text) <= max_chars:
        return text
    sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 3]
    short_parts: list[str] = []
    cur_len = 0
    for s in sentences:
        if cur_len + len(s) < max_chars - 30:
            short_parts.append(s)
            cur_len += len(s)
        else:
            break
    if short_parts:
        out = " ".join(short_parts)
        if not out.endswith(('.', '!', '?')):
            out += "."
        return out + " Chi tiết cụ thể đã hiển thị trên màn hình."
    return text[:max_chars - 40] + "... Chi tiết đã hiển thị trên màn hình."
