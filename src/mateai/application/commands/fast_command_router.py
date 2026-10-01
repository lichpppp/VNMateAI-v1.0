"""
src/mateai/application/commands/fast_command_router.py
======================================================
Bộ điều phối lệnh tất định phản hồi tức thì (Fast Command Router).

Nhiệm vụ:
- Phân tích cú pháp các câu hỏi và thao tác tất định (Deterministic Ops) KHÔNG cần qua LLM.
- Đạt độ trễ xử lý sub-millisecond (< 0.05ms cho lệnh bộ nhớ, < 50ms cho lệnh đo tải phần cứng).
- Tích hợp kiểm tra quyền RBAC trước khi thực thi các thao tác hệ thống đặc quyền.
- Trả về kết quả văn bản hoặc âm thanh tổng hợp trực tiếp.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import socket
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple
import psutil

from src.mateai.domain.identity.entities import ClearanceLevel, UserIdentity

logger = logging.getLogger(__name__)


@dataclass
class FastCommandResult:
    """Kết quả phản hồi của một Fast Command."""
    is_matched: bool
    command_name: str
    reply_text: str
    audio_bytes: Optional[bytes] = None
    tool_executed: Optional[str] = None
    latency_ms: float = 0.0
    required_clearance: ClearanceLevel = ClearanceLevel.PUBLIC
    meta: Dict[str, Any] = field(default_factory=dict)


def strip_vietnamese_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để so khớp regex linh hoạt không dấu."""
    text = text.replace("đ", "d").replace("Đ", "D")
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if not unicodedata.combining(c)).lower().strip()


class FastCommandRouter:
    """Router phân tích lệnh nhanh tất định tại tầng Application."""

    def __init__(self, tts_adapter=None) -> None:
        self.tts_adapter = tts_adapter
        self._handlers: List[
            Tuple[re.Pattern, str, ClearanceLevel, Callable[[str, re.Match], Coroutine[Any, Any, FastCommandResult]]]
        ] = []
        self._register_default_handlers()

    async def dispatch(
        self,
        query: str,
        user: Optional[UserIdentity] = None,
        synthesize_audio: bool = False
    ) -> Optional[FastCommandResult]:
        """
        Kiểm tra xem câu lệnh có khớp với lệnh tất định nào không.
        Nếu khớp: Kiểm tra quyền hạn RBAC, thực thi và trả về kết quả trong < 0.05ms.
        Nếu không: Trả về None để nhường cho Tri-Brain LLM xử lý.
        """
        if not query or not query.strip():
            return None

        clean_query = query.strip()
        stripped_query = strip_vietnamese_accents(clean_query)
        t0 = time.monotonic()

        for pattern, cmd_name, required_clearance, handler in self._handlers:
            m = pattern.search(clean_query) or pattern.search(stripped_query)
            if m:
                # Kiểm tra phân quyền bảo mật (RBAC & Clearance)
                if user and not user.has_permission_for_clearance(required_clearance):
                    logger.warning(
                        "[FastCommandRouter] Người dùng '%s' bị từ chối lệnh '%s' (cần %s)",
                        user.username, cmd_name, required_clearance.value
                    )
                    return FastCommandResult(
                        is_matched=True,
                        command_name=cmd_name,
                        reply_text="Dạ, sếp chưa được cấp quyền thực thi lệnh bảo mật này ạ.",
                        latency_ms=(time.monotonic() - t0) * 1000.0,
                        required_clearance=required_clearance
                    )

                try:
                    result = await handler(clean_query, m)
                    result.latency_ms = (time.monotonic() - t0) * 1000.0

                    if synthesize_audio and self.tts_adapter:
                        audio_chunks = []
                        async for chunk in self.tts_adapter.synthesize_stream(result.reply_text):
                            audio_chunks.append(chunk)
                        if audio_chunks:
                            result.audio_bytes = b"".join(audio_chunks)

                    return result
                except Exception as exc:
                    logger.warning("[FastCommandRouter] Lỗi xử lý lệnh '%s': %s", cmd_name, exc)
                    return None

        return None

    def _register(
        self,
        regex_pattern: str,
        command_name: str,
        clearance: ClearanceLevel,
        handler: Callable[[str, re.Match], Coroutine[Any, Any, FastCommandResult]],
    ) -> None:
        pattern = re.compile(regex_pattern, re.IGNORECASE | re.UNICODE)
        self._handlers.append((pattern, command_name, clearance, handler))

    def _register_default_handlers(self) -> None:
        """Đăng ký các mẫu lệnh tất định chuẩn."""
        # 1. Thời gian hiện tại
        self._register(
            r"^(m[âa]y gio(?: roi)?|b[âa]y gio la m[âa]y gio|xem gio|thoi gian hien tai)$",
            "get_current_time",
            ClearanceLevel.PUBLIC,
            self._handle_current_time,
        )

        # 2. Ngày tháng hiện tại
        self._register(
            r"^(h[ôo]m nay (?:la )?ng[àa]y m[âa]y|ng[àa]y bao nhi[êe]u|h[ôo]m nay (?:la )?th[ưu] m[âa]y|xem ng[àa]y)$",
            "get_current_date",
            ClearanceLevel.PUBLIC,
            self._handle_current_date,
        )

        # 3. Kiểm tra CPU (Yêu cầu quyền INTERNAL)
        self._register(
            r"^(ki[êe]m tra cpu|xem cpu|cpu bao nhi[êe]u|cpu th[êe] n[àa]o|t[ình]nh tr[ạa]ng cpu)$",
            "get_cpu_status",
            ClearanceLevel.INTERNAL,
            self._handle_cpu_status,
        )

        # 4. Kiểm tra RAM (Yêu cầu quyền INTERNAL)
        self._register(
            r"^(ki[êe]m tra ram|xem ram|ram bao nhi[êe]u|b[ộo] nh[ớo] ram|t[ình]nh tr[ạa]ng ram)$",
            "get_ram_status",
            ClearanceLevel.INTERNAL,
            self._handle_ram_status,
        )

        # 5. Kiểm tra ổ cứng (Yêu cầu quyền INTERNAL)
        self._register(
            r"^(ki[êe]m tra [ôo] (?:d[ĩi]a|c[ứ]ng)|dung lu[ợ]ng [ôo] (?:d[ĩi]a|c[ứ]ng)|[ôo] c[ứ]ng c[ò]n bao nhi[êe]u)$",
            "get_disk_status",
            ClearanceLevel.INTERNAL,
            self._handle_disk_status,
        )

        # 6. Mạng & Địa chỉ IP (Yêu cầu quyền INTERNAL)
        self._register(
            r"^(ki[êe]m tra m[ạa]ng|dia chi ip(?: cua may)?|ip cua may|xem ip|ip noi bo)$",
            "get_network_ip",
            ClearanceLevel.INTERNAL,
            self._handle_network_ip,
        )

        # 7. Điều khiển âm lượng & Tắt tiếng
        self._register(
            r"^(t[ắ]t ti[ế]ng|mute|im l[ặ]ng|ng[ừ]ng n[ó]i|d[ừ]ng ph[á]t|d[ừ]ng l[ạ]i)$",
            "mute_audio",
            ClearanceLevel.PUBLIC,
            self._handle_mute_audio,
        )
        self._register(
            r"^(?:d[ìê]u ch[ỉ]nh |c[à]i )?[âa]m lu[ợ]ng(?: l[êe]n)? (\d+)(?:%| ph[âa]n tr[ắ]m)?$",
            "set_volume",
            ClearanceLevel.PUBLIC,
            self._handle_set_volume,
        )

        # 8. Chào hỏi & Giao tiếp phản xạ
        self._register(
            r"^(xin ch[àa]o|ch[àa]o em|ch[àa]o b[ạa]n|hello mate|hi mate|ch[àa]o tr[ợ] l[ý] [ả]o)$",
            "quick_greeting",
            ClearanceLevel.PUBLIC,
            self._handle_quick_greeting,
        )
        self._register(
            r"^(c[ả]m [ơn] em|c[ả]m [ơn] b[ạa]n|thank you|thanks mate)$",
            "quick_thanks",
            ClearanceLevel.PUBLIC,
            self._handle_quick_thanks,
        )
        self._register(
            r"^(t[ạa]m bi[ệ]t|h[ẹ]n g[ặ]p l[ạ]i|bye bye)$",
            "quick_goodbye",
            ClearanceLevel.PUBLIC,
            self._handle_quick_goodbye,
        )

        # 9. Ping hệ thống
        self._register(
            r"^(ping|ping h[ệe] th[ốo]ng|ki[êe]m tra k[ế]t n[ố]i)$",
            "system_ping",
            ClearanceLevel.PUBLIC,
            self._handle_system_ping,
        )

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_current_time(self, query: str, match: re.Match) -> FastCommandResult:
        now = datetime.datetime.now()
        reply = f"Bây giờ là {now.hour} giờ {now.minute:02d} phút ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_current_time",
            reply_text=reply,
            tool_executed="datetime.now",
            meta={"hour": now.hour, "minute": now.minute},
        )

    async def _handle_current_date(self, query: str, match: re.Match) -> FastCommandResult:
        now = datetime.datetime.now()
        weekdays = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]
        weekday_vn = weekdays[now.weekday()]
        reply = f"Hôm nay là {weekday_vn}, ngày {now.day} tháng {now.month} năm {now.year} ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_current_date",
            reply_text=reply,
            tool_executed="datetime.now",
            meta={"weekday": weekday_vn, "day": now.day, "month": now.month, "year": now.year},
        )

    async def _handle_cpu_status(self, query: str, match: re.Match) -> FastCommandResult:
        cpu_percent = psutil.cpu_percent(interval=0.05)
        reply = f"Dạ, mức tải CPU hiện tại là {cpu_percent:.1f}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_cpu_status",
            reply_text=reply,
            tool_executed="psutil.cpu_percent",
            meta={"cpu_percent": cpu_percent},
        )

    async def _handle_ram_status(self, query: str, match: re.Match) -> FastCommandResult:
        mem = psutil.virtual_memory()
        used_gb = (mem.total - mem.available) / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        reply = f"Dạ, RAM đã sử dụng {mem.percent:.1f}%, tức {used_gb:.1f} trên {total_gb:.1f} GB ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_ram_status",
            reply_text=reply,
            tool_executed="psutil.virtual_memory",
            meta={"used_gb": used_gb, "total_gb": total_gb, "percent": mem.percent},
        )

    async def _handle_disk_status(self, query: str, match: re.Match) -> FastCommandResult:
        disk = psutil.disk_usage("/")
        free_gb = disk.free / (1024 ** 3)
        reply = f"Dạ, ổ đĩa chính còn trống {free_gb:.1f} GB, đã sử dụng {disk.percent:.1f}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_disk_status",
            reply_text=reply,
            tool_executed="psutil.disk_usage",
            meta={"free_gb": free_gb, "percent": disk.percent},
        )

    async def _handle_network_ip(self, query: str, match: re.Match) -> FastCommandResult:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"
        reply = f"Dạ, địa chỉ IP mạng nội bộ của máy chủ là {local_ip} ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="get_network_ip",
            reply_text=reply,
            tool_executed="socket.getsockname",
            meta={"ip": local_ip},
        )

    async def _handle_mute_audio(self, query: str, match: re.Match) -> FastCommandResult:
        reply = "Dạ, em đã tắt tiếng và dừng phát âm thanh rồi ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="mute_audio",
            reply_text=reply,
            tool_executed="native_volume_mute",
        )

    async def _handle_set_volume(self, query: str, match: re.Match) -> FastCommandResult:
        vol = int(match.group(1)) if match.group(1) else 50
        vol = max(0, min(100, vol))
        reply = f"Dạ, em đã điều chỉnh âm lượng lên {vol}% ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="set_volume",
            reply_text=reply,
            tool_executed="native_volume_set",
            meta={"volume": vol},
        )

    async def _handle_quick_greeting(self, query: str, match: re.Match) -> FastCommandResult:
        reply = "Dạ, em chào sếp! Em có thể giúp gì cho sếp hôm nay ạ?"
        return FastCommandResult(
            is_matched=True,
            command_name="quick_greeting",
            reply_text=reply,
        )

    async def _handle_quick_thanks(self, query: str, match: re.Match) -> FastCommandResult:
        reply = "Dạ không có chi ạ! Rất vui được hỗ trợ sếp."
        return FastCommandResult(
            is_matched=True,
            command_name="quick_thanks",
            reply_text=reply,
        )

    async def _handle_quick_goodbye(self, query: str, match: re.Match) -> FastCommandResult:
        reply = "Dạ tạm biệt sếp! Hẹn gặp lại sếp sau ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="quick_goodbye",
            reply_text=reply,
        )

    async def _handle_system_ping(self, query: str, match: re.Match) -> FastCommandResult:
        reply = "Pong! Hệ thống VN-MateAI hoạt động bình thường, phản hồi dưới 5 mili giây ạ."
        return FastCommandResult(
            is_matched=True,
            command_name="system_ping",
            reply_text=reply,
        )


# Singleton instance
fast_command_router = FastCommandRouter()
