"""
tests/test_phase65_voice_session.py
===================================
Kiểm thử Phase 65 — phiên hội thoại liên tục cho HUD.

Ba lỗi admin phản ánh, mỗi lỗi có phần test riêng:
  1. Lệnh lặp lại    → lỗi này ở JS, nhưng server phải truyền history
  2. Phản hồi chậm   → server phải báo trạng thái "đang chờ đáp"
  3. Không hỏi lại    → phải đếm được số lần im lặng, và bỏ cuộc có giới hạn
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


from core.voice_session import (  # noqa: E402
    MAX_TURNS,
    SESSION_TTL,
    VoiceSession,
    VoiceSessionStore,
    is_stop_reply,
    looks_like_question,
)


# ══ 1. Lịch sử hội thoại ═════════════════════════════════════════════════
section("Lịch sử hội thoại (chống lặp lệnh)")
s = VoiceSession("hud")
check("phiên mới rỗng", s.turns == [] and s.history() == [])

s.add_turn("user", "Báo cáo tồn kho ERP")
s.add_turn("assistant", "Anh muốn báo cáo tháng nào ạ?")
s.add_turn("user", "tháng 9")
h = s.history()
check("lưu đủ 3 lượt", len(h) == 3, str(len(h)))
check("đúng vai trò từng lượt",
      [t["role"] for t in h] == ["user", "assistant", "user"], str([t["role"] for t in h]))
check("nội dung lượt giữ nguyên", h[0]["content"] == "Báo cáo tồn kho ERP")

# messages đúng định dạng API chat
check("history() trả dict role/content",
      all(set(t) == {"role", "content"} for t in h), str(h[0]))

# Lượt rỗng -> bỏ, không gửi lên LLM
s2 = VoiceSession("hud")
s2.add_turn("user", "")
s2.add_turn("user", "   ")
check("lượt rỗng bị bỏ qua", s2.turns == [], str(s2.turns))

# Lượt quá dài -> cắt, không phình ngữ cảnh
s3 = VoiceSession("hud")
s3.add_turn("user", "x" * 5000)
check("lượt quá dài được cắt", len(s3.turns[0]["content"]) <= 1200, str(len(s3.turns[0]["content"])))

# Giới hạn số lượt
s4 = VoiceSession("hud")
for i in range(60):
    s4.add_turn("user" if i % 2 == 0 else "assistant", f"lượt {i}")
check("giới hạn số lượt lưu", len(s4.turns) <= MAX_TURNS * 2, str(len(s4.turns)))
check("giữ lượt MỚI NHẤT", "lượt 59" in s4.turns[-1]["content"], s4.turns[-1]["content"])
check("bỏ lượt CŨ NHẤT", "lượt 0" not in "".join(t["content"] for t in s4.turns))


# ══ 2. Nhận diện câu hỏi ══════════════════════════════════════════════════
section("Nhận diện câu cần chờ trả lời")
check("câu hỏi có dấu ?", looks_like_question("Anh muốn báo cáo tháng nào ạ?"))
check("câu hỏi tiếng Việt dấu ?", looks_like_question("Báo cáo nào ạ?"))
check("câu kết bằng dấu chấm -> không hỏi", not looks_like_question("Đã xuất xong."))
check("câu tuyên bố KHÔNG hỏi", not looks_like_question("Tổng giá trị tồn kho là 104 triệu."))

# Câu có dấu ? nhưng thực chất là thông báo -> không mở mic vô ích
check("thông báo có dấu ? vẫn không hỏi",
      not looks_like_question("Đã xuất xong?"),
      "mở mic vô ích sau khi đã xong việc")
check("thông báo dài có dấu ? vẫn không hỏi",
      not looks_like_question("Em đã kiểm tra xong 4 connector, tất cả đều hoạt động bình thường?"))
check("câu hỏi thật vẫn bắt được dù có chữ 'xong'",
      looks_like_question("Anh muốn xử lý tiếp hay để em xử lý xong phần còn lại ạ?"))

check("chuỗi rỗng -> không hỏi", not looks_like_question(""))
check("None -> không hỏi", not looks_like_question(None))

section("Lệnh dừng của admin")
for w in ["dừng", "thôi", "hủy", "huỷ", "bỏ qua", "không cần", "stop", "cancel"]:
    check(f"'{w}' -> dừng", is_stop_reply(w), w)
check("câu hỏi bình thường không phải lệnh dừng", not is_stop_reply("Anh muốn báo cáo tháng nào?"))


# ══ 3. Đếm lượt hỏi lại ══════════════════════════════════════════════════
section("Đếm lượt hỏi lại và đóng lắng nghe")
s5 = VoiceSession("hud")
check("chưa hỏi -> không chờ", s5.expecting_reply is False)
check("chưa hỏi -> số lần hỏi lại = 0", s5.reask_count == 0)

s5.mark_expecting_reply("Anh muốn báo cáo tháng nào ạ?")
check("hỏi -> bật chờ", s5.expecting_reply is True)
check("lưu câu hỏi", s5.pending_question.startswith("Anh muốn báo cáo"))
check("đặt lại bộ đếm về 0", s5.reask_count == 0)

check("bump lần 1 -> 1", s5.bump_reask() == 1)
check("bump lần 2 -> 2", s5.bump_reask() == 2)
check("sau 2 lần -> HUD sẽ đóng lắng nghe", s5.reask_count >= 2)

s5.clear_expecting_reply()
check("admin đáp -> tắt chờ", s5.expecting_reply is False)
check("admin đáp -> xoá câu hỏi", s5.pending_question == "")
check("admin đáp -> reset bộ đếm", s5.reask_count == 0)

# Hỏi mới thì phải reset bộ đếm, không cộng dồn từ câu hỏi cũ
s6 = VoiceSession("hud")
s6.mark_expecting_reply("Câu hỏi một?")
s6.bump_reask()
s6.bump_reask()
check("đã hỏi lại 2 lần", s6.reask_count == 2)
s6.mark_expecting_reply("Câu hỏi hai?")
check("hỏi mới -> reset bộ đếm", s6.reask_count == 0, str(s6.reask_count))
check("hỏi mới -> lưu câu mới", s6.pending_question == "Câu hỏi hai?")

section("Đo thời gian chờ")
s7 = VoiceSession("hud")
s7.mark_expecting_reply("Chờ tôi?")
check("vừa hỏi -> chờ ~0s", s7.waiting_seconds() < 0.5, str(s7.waiting_seconds()))
time.sleep(0.3)
check("sau 0.3s -> chờ >0.2s", s7.waiting_seconds() > 0.2, str(s7.waiting_seconds()))


# ══ 4. Kho phiên ═════════════════════════════════════════════════════════
section("Kho phiên")
store = VoiceSessionStore(ttl_seconds=2)
a = store.get("hud")
check("cùng id -> cùng phiên", store.get("hud") is a)
check("id khác -> phiên khác", store.get("khac") is not a)

a.add_turn("user", "xin chào")
check("phiên lưu lượt", len(store.get("hud").turns) == 1)
check("drop phiên tồn tại", store.drop("hud") is True)
check("drop phiên đã xoá -> False", store.drop("hud") is False)

# Hết hạn
s8 = store.get("hud2")
check("phiên mới chưa hết hạn", not s8.is_expired())
time.sleep(2.1)
# KHÔNG gọi store.get("hud2") ở đây: get() tạo phiên mới nếu chưa có, sẽ
# làm mất ý nghĩa của phép kiểm tra. Nhìn thẳng vào kho.
check("hết hạn -> còn nằm trong kho cho tới lúc purge", "hud2" in store._sessions)
store._purge()
check("purge dọn phiên hết hạn", "hud2" not in store._sessions)

section("Thread safety")
import threading  # noqa: E402

store2 = VoiceSessionStore(ttl_seconds=600)
errs: list = []


def _w(i: int) -> None:
    try:
        sess = store2.get(f"s{i % 5}")
        sess.add_turn("user", f"lượt {i}")
        sess.bump_reask()
    except Exception as exc:  # pragma: no cover
        errs.append(str(exc))


ts = [threading.Thread(target=_w, args=(i,)) for i in range(40)]
for t in ts:
    t.start()
for t in ts:
    t.join()
check("40 luồng ghi song song không lỗi", not errs, str(errs[:2]))

section("Phiên bị chia tay")
s9 = VoiceSession("hud")
s9.add_turn("user", "lệnh một")
s9.mark_expecting_reply("hỏi một?")
s9.bump_reask()
s9.clear_expecting_reply()
check("đóng phiên -> vẫn giữ lịch sử", len(s9.turns) >= 1, str(len(s9.turns)))
check("đóng phiên -> không còn chờ", s9.expecting_reply is False)
check("đóng phiên -> bộ đếm về 0", s9.reask_count == 0)

section("to_client")
s10 = VoiceSession("hud")
s10.add_turn("user", "xin chào")
s10.mark_expecting_reply("Anh cần gì ạ?")
c = s10.to_client()
check("to_client đủ trường HUD cần",
      all(k in c for k in ("expecting_reply", "question" if False else "pending_question",
                           "reask_count", "waiting_seconds")),
      str(list(c)))
check("to_client KHÔNG kèm lịch sử đầy đủ", "turns" not in c or c["turns"] <= 2, str(c.get("turns")))


# ── Tổng kết ─────────────────────────────────────────────────────────────
print("\n" + "─" * 60)
if FAILURES:
    print("Các assertion FAIL:")
    for f in FAILURES:
        print(f)
print(f"\nTổng: {PASSED + FAILED} | Pass: {PASSED} | Fail: {FAILED}")
sys.exit(1 if FAILED else 0)
