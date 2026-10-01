"""
src/mateai/application/voice/sentence_buffer.py
================================================
Bộ đệm phân đoạn câu cho luồng LLM sang TTS (Sentence Segmentation Buffer).

Nhiệm vụ:
- Tiếp nhận từng token văn bản từ luồng LLM Streaming.
- Làm sạch văn bản (Markdown, bullet points, code blocks) phù hợp cho đọc giọng nói.
- Phân đoạn câu theo ranh giới cứng (., !, ?, \n\n) và mềm (dấu phẩy khi câu đủ dài).
- Đẩy câu hoàn chỉnh ra hàng đợi của TTS Worker mà không gây trễ đàm thoại (< 600ms TTFA).
"""

from __future__ import annotations

import re
from typing import AsyncGenerator, List, Optional

# Ký tự phân cách CỨNG (ngắt câu tức thì)
_HARD_BOUNDARY_RE = re.compile(r'([.!?;…]|\?!|!\.{2,}|\.{3,}|\n{2,})', re.UNICODE)

# Ký tự phân cách MỀM (chỉ ngắt khi buffer đã tích lũy đủ số từ tối thiểu)
_SOFT_BOUNDARY_RE = re.compile(r'[,،،]', re.UNICODE)

MIN_SOFT_WORDS: int = 10
MIN_SENTENCE_CHARS: int = 6


def sanitize_text_for_speech(text: str) -> str:
    """Loại bỏ ký tự Markdown, code block, URL để TTS đọc tự nhiên."""
    if not text:
        return ""
    # Bỏ comment ẩn
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    # Bỏ code blocks
    text = re.sub(r'```[\s\S]*?```', 'em đã thực thi xong.', text)
    text = re.sub(r'`[^`]+`', '', text)
    # Bỏ Markdown headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bỏ bullet markers
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Bỏ bold / italic
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    # Bỏ URL
    text = re.sub(r'https?://\S+', 'đường dẫn liên kết', text)
    # Chuẩn hóa khoảng trắng
    text = re.sub(r'\s+', ' ', text).strip()
    return text


class SentenceBuffer:
    """Bộ đệm thu thập token và tách thành các câu hoàn chỉnh cho TTS."""

    def __init__(self, min_soft_words: int = MIN_SOFT_WORDS, min_chars: int = MIN_SENTENCE_CHARS):
        self.min_soft_words = min_soft_words
        self.min_chars = min_chars
        self._buffer: str = ""
        self._sentence_index: int = 0

    def feed(self, token: str) -> List[str]:
        """Tiếp nhận một token mới từ LLM và trả về danh sách các câu đã hoàn thiện."""
        self._buffer += token
        ready_sentences: List[str] = []

        while True:
            hard_match = _HARD_BOUNDARY_RE.search(self._buffer)
            if hard_match:
                end_pos = hard_match.end()
                raw_sentence = self._buffer[:end_pos]
                cleaned = sanitize_text_for_speech(raw_sentence)
                if len(cleaned) >= self.min_chars:
                    ready_sentences.append(cleaned)
                    self._sentence_index += 1
                self._buffer = self._buffer[end_pos:].lstrip()
                continue

            # Kiểm tra ngắt mềm (dấu phẩy) nếu buffer đã tích lũy đủ số từ
            words = self._buffer.split()
            if len(words) >= self.min_soft_words:
                soft_match = _SOFT_BOUNDARY_RE.search(self._buffer)
                if soft_match:
                    end_pos = soft_match.end()
                    raw_sentence = self._buffer[:end_pos]
                    cleaned = sanitize_text_for_speech(raw_sentence)
                    if len(cleaned) >= self.min_chars:
                        ready_sentences.append(cleaned)
                        self._sentence_index += 1
                    self._buffer = self._buffer[end_pos:].lstrip()
                    continue

            break

        return ready_sentences

    def flush(self) -> Optional[str]:
        """Xả toàn bộ phần văn bản còn lại trong buffer khi LLM kết thúc stream."""
        remaining = sanitize_text_for_speech(self._buffer)
        self._buffer = ""
        if remaining and len(remaining) >= 2:
            self._sentence_index += 1
            return remaining
        return None

    def reset(self) -> None:
        """Làm sạch bộ đệm khi bắt đầu lượt mới hoặc khi bị ngắt lời."""
        self._buffer = ""
        self._sentence_index = 0
