"""
tests/test_phase83_hitl_dedup_telegram.py
==========================================
Phase 83 — HITL không bắn tin Telegram lặp cho cùng một yêu cầu đang chờ.

Vấn đề (người dùng báo: "Telegram sao cứ bắn tin nhắn YÊU CẦU PHÊ DUYỆT C.E.O")
------------------------------------------------------------------------
Mỗi lần `execute_with_hitl` gặp tác vụ rủi ro cao chưa duyệt, nó gọi
`request_approval` — tạo yêu cầu MỚI (id mới) + gửi tin Telegram MỚI — kể cả khi
một yêu cầu y hệt vẫn đang chờ CEO. Hệ quả thấy được:

  - AI agent loop thử lại cùng một tool sau "awaiting_approval"
  - người dùng (hoặc AI) hỏi lại cùng việc
  - hai luồng gọi song song cùng tác vụ rủi ro

mỗi lần là một tin "YÊU CẦU PHÊ DUYỆT C.E.O" mới. Cùng một việc, CEO nhận cả
chồng tin cần duyệt — chưa kể mỗi tin đều đính kèm mã duyệt khác nhau.

Cách sửa
--------
Trong `request_approval`: so dấu vân tay (action + params, cùng chuẩn sha256
`_token_for`) với các yêu cầu đang PENDING trong cửa sổ TTL (15 phút). Trùng thì
tái sử dụng chính yêu cầu đang chờ: cùng approval_id, KHÔNG tạo yêu cầu mới,
KHÔNG gửi tin thứ hai. Sau khi CEO duyệt / từ chối / quá TTL, yêu cầu tiếp theo
lại được tạo bình thường (không kẹt vĩnh viễn).

Test đếm số lần `telegram_gateway.send_hitl_request` thực được gọi để chứng minh
số tin Telegram giảm xuống, thay vì chỉ kiểm tra chuỗi/định danh.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
    else:
        FAILED += 1
        FAILURES.append(f"  ✗ {name}" + (f" — {extra}" if extra else ""))


def section(title: str) -> None:
    print(f"\n▸ {title}")


def main() -> None:
    from core.zero_trust import HITL_APPROVAL_THRESHOLD, hitl_manager

    # ── Gắn "tường lửa gửi Telegram": đếm số tin, không gửi thật ─────────────
    from core.telegram_gateway import telegram_gateway as _tg

    sent: dict[str, int] = {"n": 0}

    def _stub_send(  # noqa: ANN001
        *args, **kwargs
    ) -> bool:
        sent["n"] += 1
        return True  # giả vờ có nút bấm — đi tiếp nhánh "đã gửi"

    _tg.send_hitl_request = _stub_send

    # Dọn hàng đợi cho test lập được (có thể còn yêu cầu cũ trong tiến trình).
    with hitl_manager._lock:
        hitl_manager._pending_approvals.clear()
        hitl_manager._action_callbacks.clear()

    def _exec() -> dict:
        return {"ok": True}

    # 1) Ba lần gọi y hệt (thứ tự params khác nhau) khi chưa duyệt →
    #    MỘT approval_id, MỘT tin Telegram.
    r1 = hitl_manager.request_approval(
        "record_expense", {"amount": 5_000_000, "muc": "x"},
        requested_by="AI", description="d1", action_callback=_exec, risk_level=4,
    )
    r2 = hitl_manager.request_approval(
        "record_expense", {"muc": "x", "amount": 5_000_000},
        requested_by="AI", description="d2", action_callback=_exec, risk_level=4,
    )
    r3 = hitl_manager.request_approval(
        "record_expense", {"amount": 5_000_000, "muc": "x"},
        requested_by="user", description="d3", action_callback=_exec, risk_level=4,
    )
    check("3 lần gọi y hệt cùng 1 approval_id",
          r1["id"] == r2["id"] == r3["id"],
          f"{r1['id']} / {r2['id']} / {r3['id']}")
    check("yêu cầu trùng chỉ gửi ĐÚNG 1 tin Telegram",
          sent["n"] == 1, f"gửi {sent['n']} lần")
    check("các lần trùng đều báo awaiting qua id của lần đầu",
          bool(r1["id"]), r1["id"])

    # 2) Tham số khác → yêu cầu mới + tin mới (không bị chặn oan).
    before = sent["n"]
    r4 = hitl_manager.request_approval(
        "record_expense", {"amount": 99_000_000, "muc": "y"},
        requested_by="AI", description="d4", action_callback=_exec, risk_level=4,
    )
    check("params khác → approval_id khác", r4["id"] != r1["id"], f"{r1['id']} vs {r4['id']}")
    check("params khác → bắn tin mới", sent["n"] == before + 1, f"gửi {sent['n']} lần")

    # 3) Sau khi DUYỆT, gọi lại y hệt → tạo yêu cầu mới (không kẹt bởi cái cũ).
    hitl_manager.approve(r1["id"], approved_by="ceo")
    before = sent["n"]
    r5 = hitl_manager.request_approval(
        "record_expense", {"amount": 5_000_000, "muc": "x"},
        requested_by="AI", description="d5", action_callback=_exec, risk_level=4,
    )
    check("sau khi duyệt, yêu cầu mới được tạo (id khác)",
          r5["id"] != r1["id"], f"{r1['id']} vs {r5['id']}")
    check("sau khi duyệt, gọi lại vẫn bắn tin cho việc làm lại",
          sent["n"] == before + 1, f"gửi {sent['n']} lần")
    hitl_manager.approve(r5["id"], approved_by="ceo")

    # 4) Sau khi TỪ CHỐI, gọi lại → tạo yêu cầu mới (không cấm vĩnh viễn).
    before = sent["n"]
    r6 = hitl_manager.request_approval(
        "record_expense", {"amount": 7_000_000, "muc": "z"},
        requested_by="AI", description="d6", action_callback=_exec, risk_level=4,
    )
    hitl_manager.reject(r6["id"], rejected_by="ceo")
    r7 = hitl_manager.request_approval(
        "record_expense", {"amount": 7_000_000, "muc": "z"},
        requested_by="AI", description="d7", action_callback=_exec, risk_level=4,
    )
    check("sau khi từ chối, gọi lại tạo yêu cầu mới",
          r7["id"] != r6["id"], f"{r6['id']} vs {r7['id']}")
    check("sau khi từ chối, tin mới được gửi lại",
          sent["n"] == before + 2, f"gửi {sent['n']} lần")
    hitl_manager.approve(r7["id"], approved_by="ceo")

    # 5) Sau khi hết TTL, yêu cầu cũ không chặn yêu cầu mới cùng tác vụ.
    from core.zero_trust import _APPROVAL_TTL_SECONDS

    before = sent["n"]
    r8 = hitl_manager.request_approval(
        "record_income", {"so": 1},
        requested_by="AI", description="d8", action_callback=_exec, risk_level=3,
    )
    with hitl_manager._lock:  # già hóa yêu cầu vừa tạo
        hitl_manager._pending_approvals[r8["id"]]["_created_ts"] = (
            time.time() - (_APPROVAL_TTL_SECONDS + 1)
        )
    r9 = hitl_manager.request_approval(
        "record_income", {"so": 1},
        requested_by="AI", description="d9", action_callback=_exec, risk_level=3,
    )
    check("sau TTL, yêu cầu trùng được tạo mới (id khác)",
          r9["id"] != r8["id"], f"{r8['id']} vs {r9['id']}")
    check("sau TTL, tin mới được gửi", sent["n"] == before + 2, f"gửi {sent['n']} lần")
    hitl_manager.approve(r8["id"], approved_by="ceo")
    hitl_manager.approve(r9["id"], approved_by="ceo")

    # 6) Trường nội bộ (dùng cho loại trùng) không lộ qua get_pending_list.
    _ = hitl_manager.request_approval(
        "record_income", {"so": 2},
        requested_by="AI", description="d10", action_callback=_exec, risk_level=3,
    )
    pending = hitl_manager.get_pending_list()
    leaked = [k for it in pending for k in it if k.startswith("_")]
    check("get_pending_list không lộ trường nội bộ (_fp/_created_ts)",
          not leaked, str(leaked))
    with hitl_manager._lock:
        hitl_manager._pending_approvals.clear()
        hitl_manager._action_callbacks.clear()

    check("ngưỡng HITL vẫn là 3 (không bị đổi trong lúc loại trùng)",
          HITL_APPROVAL_THRESHOLD == 3, str(HITL_APPROVAL_THRESHOLD))


if __name__ == "__main__":
    section("HITL loại trùng yêu cầu đang chờ — chống thư rác Telegram")
    main()

    print("─" * 60)
    print(f"Tổng: {PASSED} | Pass: {PASSED} | Fail: {FAILED}")
    if FAILED:
        print("FAILURES:")
        for f_ in FAILURES:
            print(f_)
        sys.exit(1)
    print("✅ TẤT CẢ PASS")