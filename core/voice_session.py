"""
core/voice_session.py
=====================
Phase 65 — Phiên hội thoại liên tục cho HUD.

Vấn đề
------
HUD cũ gọi `stream_voice_response()` mà KHÔNG truyền `history`. Mỗi lượt nói
đều là một cuộc trò chuyện mới, nên Ly Ly không nhớ mình vừa hỏi gì, không
nhớ admin đã trả lời gì. Hậu quả đúng như admin phản ánh: câu lệnh bị lặp
lại, báo cáo ra sai.

Tách khỏi `state_manager`
-------------------------
`state_manager` giữ *pending action* — một tác vụ đang chờ duyệt, lấy ra thì
xoá. Hội thoại HUD thì khác: cần giữ N lượt, có thời hạn, và phải sống lâu hơn
một tác vụ. Nhét vào chung sẽ làm hai thứ cùng đặt tên "chờ" nhưng khác nghĩa,
rồi sửa cái này là vỡ cái kia.

Ba trạng thái của một lượt nói
-----------------------------
    AI hỏi  ->  chờ admin  ->  admin đáp / không đáp

`expecting_reply` là phần cốt lõi: nó cho biết Ly Ly vừa hỏi một câu và đang
chờ. HUD dùng cờ này để mở lại mic, và đếm số lần không có phản hồi để biết
khi nào hỏi lại rồi bỏ cuộc.

Không tự đoán "AI có đang hỏi không"
-----------------------------------
Đoán bằng dấu chấm câu là cách dễ sai: "Đã xuất xong." không phải câu hỏi,
còn "Anh muốn báo cáo nào?" thì là. Nên `mark_expecting_reply()` do phía gọi
bật tường minh, kèm lý do — thứ gì đó không tự bịa ra "chờ phản hồi" rồi
treo mic vô ích.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: Số lượt giữ lại cho mỗi phiên. Đủ để Ly Ly nhớ cả cuộc trao đổi về một
#: báo cáo, mà không phình lên làm chậm mỗi lượt gọi LLM.
MAX_TURNS = 12

#: Phiên không dùng trong 10 phút thì bị bỏ. HUD có thể để cả đêm không ai
#: nói chuyện; giữ lại lịch sử cũ chỉ tốn bộ nhớ và làm nhiễu ngữ cảnh lượt sau.
SESSION_TTL = 600

#: Độ dài tối đa của một lượt, để một câu trả lời dài không làm phình lịch sử.
MAX_TURN_CHARS = 1200


def _clip(text: str, limit: int = MAX_TURN_CHARS) -> str:
    """
    Cắt bớt lượt quá dài, cắt ở ranh giới từ để không băm vỡ câu.

    Dấu "…" báo hiệu đã cắt cũng nằm trong `limit` — tính ngoài thì lượt dài
    đúng hạn vẫn vượt, và giới hạn lịch sử trở nên không đáng tin.
    """
    s = str(text or "").strip()
    if len(s) <= limit:
        return s
    budget = max(1, limit - 1)  # dành 1 ký tự cho dấu "…"
    cut = s[:budget]
    sp = cut.rfind(" ")
    body = cut[:sp] if sp > budget * 0.6 else cut
    return body.rstrip() + "…"


class VoiceSession:
    """Lịch sử hội thoại + trạng thái chờ phản hồi cho một phiên HUD."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.turns: List[Dict[str, str]] = []
        self.updated_at = time.time()

        # Đang chờ admin trả lời sau câu hỏi của AI.
        self.expecting_reply = False
        self.pending_question = ""
        # Số lần đã hỏi lại vì im lặng. Tới ngưỡng thì HUD đóng lắng nghe.
        self.reask_count = 0
        # Mốc để HUD biết đã chờ bao lâu mà không có phản hồi.
        self.asked_at = 0.0

    # ── Lịch sử ───────────────────────────────────────────────────────

    def add_turn(self, role: str, content: str) -> None:
        """role: 'user' (admin nói) hoặc 'assistant' (Ly Ly đáp)."""
        text = _clip(content)
        if not text:
            return
        self.turns.append({"role": role, "content": text})
        # Cắt từ đầu, giữ N lượt gần nhất — lượt cũ nhất ít liên quan nhất.
        if len(self.turns) > MAX_TURNS * 2:
            self.turns = self.turns[-MAX_TURNS * 2:]
        self.updated_at = time.time()

    def history(self, max_turns: int = MAX_TURNS) -> List[Dict[str, str]]:
        """Lịch sử dạng messages, bỏ lượt cũ ngoài ngưỡng."""
        return [dict(t) for t in self.turns[-max_turns:]]

    def clear(self) -> None:
        self.turns.clear()
        self.expecting_reply = False
        self.pending_question = ""
        self.reask_count = 0
        self.asked_at = 0.0
        self.updated_at = time.time()

    # ── Trạng thái chờ phản hồi ─────────────────────────────────────────

    def mark_expecting_reply(self, question: str) -> None:
        """Bật chờ phản hồi, ghi lại câu hỏi và đặt lại bộ đếm hỏi lại."""
        self.expecting_reply = True
        self.pending_question = _clip(question)
        self.reask_count = 0
        self.asked_at = time.time()
        self.updated_at = self.asked_at

    def clear_expecting_reply(self) -> None:
        """Admin đã đáp — không còn chờ, không cần hỏi lại nữa."""
        self.expecting_reply = False
        self.pending_question = ""
        self.reask_count = 0
        self.asked_at = 0.0
        self.updated_at = time.time()

    def bump_reask(self) -> int:
        """Tăng bộ đếm hỏi lại. Trả số lần sau khi tăng."""
        self.reask_count += 1
        self.asked_at = time.time()
        self.updated_at = self.asked_at
        return self.reask_count

    def waiting_seconds(self) -> float:
        return max(0.0, time.time() - self.asked_at) if self.asked_at else 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now or time.time()) - self.updated_at > SESSION_TTL

    def to_client(self) -> Dict[str, Any]:
        """Trạng thái gửi HUD. Không kèm lịch sử — HUD không cần đọc lại."""
        return {
            "session_id": self.session_id,
            "turns": len(self.turns),
            "expecting_reply": self.expecting_reply,
            "pending_question": self.pending_question,
            "reask_count": self.reask_count,
            "waiting_seconds": round(self.waiting_seconds(), 1),
        }


class VoiceSessionStore:
    """Kho phiên HUD, an toàn khi gọi từ nhiều luồng."""

    def __init__(self, ttl_seconds: int = SESSION_TTL) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[str, VoiceSession] = {}
        self._ttl = ttl_seconds

    def _purge(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        dead = [sid for sid, s in self._sessions.items() if (now - s.updated_at) > self._ttl]
        for sid in dead:
            self._sessions.pop(sid, None)
        if dead:
            logger.info("[VoiceSession] Đã dọn %d phiên hết hạn", len(dead))

    def get(self, session_id: str) -> VoiceSession:
        with self._lock:
            self._purge()
            sess = self._sessions.get(session_id)
            if sess is None:
                sess = VoiceSession(session_id)
                self._sessions[session_id] = sess
            return sess

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def clear_all(self) -> int:
        with self._lock:
            n = len(self._sessions)
            self._sessions.clear()
            return n

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._sessions)


#: Phiên dùng chung cho HUD. HUD chỉ có một người dùng quản trị nên một phiên
#: `default` là đủ; vẫn để khoá theo id để sau này thêm nhiều người không phải
#: sửa cấu trúc.
voice_sessions = VoiceSessionStore()


# ── Nhận diện câu hỏi cần chờ trả lời ────────────────────────────────────

#: Dấu hiệu Ly Ly đang hỏi admin, cần chờ đáp.
_QUESTION_MARKS = ("?", "？")

#: Câu kết bằng dấu chấm hỏi nhưng thực chất là mệnh lệnh/ tuyên bố.
#: "Đã xuất xong?" hiếm gặp, nhưng "Anh có cần em làm gì tiếp không ạ?" thì
#: không phải — nên tách riêng để không mở mic vô ích.
#: Cửa sổ 80 ký tự thay vì 40: "Em đã kiểm tra xong 4 connector, tất cả đều
#: hoạt động bình thường?" dài hơn 40 và là thông báo, không phải câu hỏi.
#: Hẹp quá thì HUD mở mic và chờ một câu không bao giờ được đáp.
_STATEMENT_WITH_Q = re.compile(
    r"(đã\s+(xuất|kiểm tra|lấy|gửi|tạo|xong|thành công)|hoàn tất|xong rồi)\b[^?]{0,80}\?$",
    re.IGNORECASE,
)

#: Lệnh từ chối / kết thúc — admin muốn dừng, không hỏi lại nữa.
_STOPWORDS = (
    "dừng", "thôi", "huỷ", "hủy", "bỏ qua", "không cần", "tạm dừng",
    "stop", "cancel", "never mind", "thôi vậy",
)


def looks_like_question(text: str) -> bool:
    """
    Có nên chờ admin trả lời sau câu này không.

    Chỉ là gợi ý — phía gọi vẫn có quyền quyết định. Sai theo hướng này (hỏi
    lại một câu không cần) thì phiền; sai theo hướng kia (không mở mic khi
    thật sự đang hỏi) thì hội thoại đứt.
    """
    s = str(text or "").strip()
    if not s:
        return False
    if _STATEMENT_WITH_Q.search(s):
        return False
    return any(m in s for m in _QUESTION_MARKS)


def is_stop_reply(text: str) -> bool:
    """Admin muốn dừng hội thoại."""
    s = str(text or "").strip().lower()
    return any(w in s for w in _STOPWORDS)
