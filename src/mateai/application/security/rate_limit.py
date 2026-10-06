"""
mateai/application/security/rate_limit.py
=========================================
Giới hạn tần suất dùng chung (prompt cuối §97) cho voice, lượt agent / LLM và phiên
WebSocket thoại. Ngưỡng: `settings.security.rate_limits` (0 = tắt).

Đăng nhập giữ bộ đếm riêng ở `routers/auth.py` vì khác nghĩa: đếm lần SAI rồi khoá, không
đếm mọi lần gọi. Bộ đếm này ở RAM một tiến trình — khi chạy nhiều tiến trình cần chuyển sang
Redis (xem `docs/architecture/current-system-map.md`).
"""
from __future__ import annotations

import collections
import threading
import time
from typing import Deque, Dict

_LOCK = threading.Lock()
_HITS: Dict[str, Deque[float]] = {}
_SESSIONS: Dict[str, int] = collections.Counter()


def limit(name: str, default: int) -> int:
    try:
        from mateai.config.loader import settings
        return int((settings.security.rate_limits or {}).get(name, default))
    except Exception:  # noqa: BLE001
        return default


def hit(key: str, max_hits: int, window_s: float) -> float:
    """Ghi một lần gọi. 0.0 = được phép; > 0 = số giây phải chờ (lần gọi này KHÔNG được tính)."""
    if max_hits <= 0:
        return 0.0
    now = time.monotonic()
    with _LOCK:
        q = _HITS.setdefault(key, collections.deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= max_hits:
            return max(0.1, window_s - (now - q[0]))
        q.append(now)
        return 0.0


def open_session(kind: str, who: str, max_sessions: int) -> bool:
    if max_sessions <= 0:
        return True
    key = f"{kind}:{who}"
    with _LOCK:
        if _SESSIONS[key] >= max_sessions:
            return False
        _SESSIONS[key] += 1
        return True


def close_session(kind: str, who: str) -> None:
    key = f"{kind}:{who}"
    with _LOCK:
        _SESSIONS[key] = max(0, _SESSIONS[key] - 1)


def reset() -> None:
    with _LOCK:
        _HITS.clear()
        _SESSIONS.clear()


def busy_message(wait_s: float) -> str:
    return f"Dạ, anh/chị đang gửi quá nhiều yêu cầu. Vui lòng thử lại sau khoảng {int(wait_s) + 1} giây."
