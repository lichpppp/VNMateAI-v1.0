"""
core/audio/sentence_streamer.py
================================
Bộ đệm ngắt câu gối đầu LLM→TTS (< 600ms TTFA).

Kiến trúc:
  LLM token stream → SentenceStreamer → yield câu hoàn chỉnh → TTS engine

Ngắt câu:
  - Ký tự cứng: . ! ? ; \n\n
  - Ký tự mềm (dấu ,) nếu buffer > MIN_SOFT_WORDS từ (câu đầu phát siêu nhanh)
  - Fallback flush sau FLUSH_TIMEOUT giây nếu không gặp điểm ngắt
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import AsyncGenerator, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex nhận diện ranh giới câu
# ---------------------------------------------------------------------------

# Ranh giới CỨNG — ngắt ngay lập tức
_HARD_BOUNDARY_RE = re.compile(
    r'([.!?;…]|\?!|!\.{2,}|\.{3,}|\n{2,})',
    re.UNICODE,
)

# Ranh giới MỀM — chỉ ngắt khi buffer đã đủ MIN_SOFT_WORDS từ
_SOFT_BOUNDARY_RE = re.compile(r'[,،،]', re.UNICODE)

# Số từ tối thiểu để ngắt tại dấu phẩy
MIN_SOFT_WORDS: int = 10

# Độ dài ký tự tối thiểu của câu để đưa vào TTS (tránh âm thanh ngắn vô nghĩa)
MIN_SENTENCE_CHARS: int = 8

# Thời gian chờ tối đa trước khi flush buffer còn lại (giây)
FLUSH_TIMEOUT: float = 2.5


# ---------------------------------------------------------------------------
# Text sanitizer
# ---------------------------------------------------------------------------

def sanitise_for_tts(text: str) -> str:
    """
    Chuẩn hoá văn bản trước khi đưa vào TTS.
    Loại bỏ: Markdown, code blocks, bullet, URL, bảng, ký tự đặc biệt.
    Giữ lại: Nội dung ngôn ngữ tự nhiên thuần tuý.
    """
    if not text:
        return ""

    # Bỏ comment giọng nói ẩn <!--VOICE:...-->
    text = re.sub(r'<!--VOICE:.*?-->', '', text, flags=re.DOTALL)

    # Thay code block bằng câu nói tắt
    text = re.sub(r'```[\s\S]*?```', 'em đã thực thi xong.', text)
    text = re.sub(r'`[^`]+`', '', text)

    # Bỏ tiêu đề Markdown
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)

    # Bỏ định dạng bold/italic
    text = re.sub(r'\*{1,3}(.*?)\*{1,3}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_{1,3}(.*?)_{1,3}', r'\1', text, flags=re.DOTALL)

    # Bỏ bullet và list đánh số
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+[.)]\s+', '', text, flags=re.MULTILINE)

    # Bỏ link Markdown và URL
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)

    # Bỏ bảng và phân cách
    text = re.sub(r'\|[^\n]*', '', text)
    text = re.sub(r'-{3,}', '', text)
    text = re.sub(r'={3,}', '', text)

    # Bỏ ký tự đặc biệt không phát âm được
    text = re.sub(r'[#@&^~\\<>{}[\]()]', '', text)

    # Chuẩn hoá xuống dòng
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)

    # Bỏ khoảng trắng thừa
    text = re.sub(r'\s{2,}', ' ', text)

    return text.strip()


def _word_count(text: str) -> int:
    """Đếm số từ trong text."""
    return len(text.split())


# ---------------------------------------------------------------------------
# SentenceStreamer
# ---------------------------------------------------------------------------

class SentenceStreamer:
    """
    Gom token từ LLM stream, ngắt tại ranh giới câu tự nhiên tiếng Việt,
    yield câu đã sanitize ngay cho TTS — không chờ LLM hoàn thành.

    Sử dụng:
        streamer = SentenceStreamer()
        async for sentence in streamer.stream(token_gen):
            audio = await tts_engine.synthesise(sentence)
            await ws.send_bytes(audio)
    """

    def __init__(self) -> None:
        from core.audio.sentence_buffer import SentenceBuffer
        self._sentence_buffer = SentenceBuffer(min_chars=MIN_SENTENCE_CHARS)
        self._sentences_yielded: int = 0
        self._start_time: float = time.monotonic()
        self._first_yield_time: Optional[float] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def stream(
        self,
        token_generator: AsyncGenerator[str, None],
    ) -> AsyncGenerator[str, None]:
        """
        Nhận async generator token từ LLM, yield câu hoàn chỉnh đã sanitize.
        Sử dụng SentenceBuffer với bộ lọc chống ngắt sai số thập phân/IP/domain.

        Yields:
            str: Câu văn sạch, sẵn sàng đưa vào TTS.
        """
        async for token in token_generator:
            ready_sentences = self._sentence_buffer.add_token(token)
            for sentence in ready_sentences:
                self._record_yield(sentence)
                yield sentence

        # Flush phần còn lại sau khi token generator kết thúc
        for remaining in self._sentence_buffer.flush():
            self._record_yield(remaining)
            yield remaining

        elapsed = (time.monotonic() - self._start_time) * 1000
        logger.info(
            "[SentenceStreamer] Hoàn thành: %d câu đã yield / %.0fms tổng",
            self._sentences_yielded, elapsed,
        )

    def get_ttfa_ms(self) -> Optional[float]:
        """Time-To-First-Audio = thời gian từ init đến câu đầu tiên được yield."""
        if self._first_yield_time is None:
            return None
        return (self._first_yield_time - self._start_time) * 1000

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_yield(self, sentence: str = "") -> None:
        self._sentences_yielded += 1
        if self._first_yield_time is None:
            self._first_yield_time = time.monotonic()
            ttfa = (self._first_yield_time - self._start_time) * 1000
            logger.info(
                "[SentenceStreamer] TTFA = %.0fms (câu #1: '%s')",
                ttfa, sentence[:40]
            )


# ---------------------------------------------------------------------------
# Convenience: flush single text block (không streaming)
# ---------------------------------------------------------------------------

def split_into_sentences(text: str) -> list[str]:
    """
    Tách văn bản thành danh sách câu hoàn chỉnh an toàn (không ngắt sai số/IP).
    """
    from core.audio.sentence_buffer import SentenceBuffer
    buf = SentenceBuffer(min_chars=MIN_SENTENCE_CHARS)
    return buf.add_token(text) + buf.flush()
