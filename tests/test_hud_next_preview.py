# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
"""
Bản thử HUD "buồng lái AI" (/hud-next): CHỈ HIỂN THỊ — không gửi lệnh, không duyệt phiếu, không phát âm thanh; chưa đăng nhập thì ghi rõ
DEMO / mô phỏng; chữ tiếng Việt dùng phông có dấu (Be Vietnam Pro), không dùng Orbitron cho câu tiếng Việt.
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "web" / "hud-next.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "hud-next.html").read_text(encoding="utf-8")


def test_page_is_served_without_cache():
    from mateai.interfaces.http.server import app
    r = TestClient(app).get("/hud-next")
    assert r.status_code == 200 and "Neural Command" in r.text and "no-store" in r.headers["cache-control"]


def test_preview_is_display_only():
    assert not re.search(r"method:\s*['\"](POST|PUT|PATCH|DELETE)", JS)               # chỉ GET
    assert "new Audio" not in JS and "AudioContext" not in JS and ".play(" not in JS
    assert "ws.send" not in JS                                                        # không gửi gì qua /ws/hud
    assert 'id="inc-yes" disabled' in HTML and 'id="inc-no" disabled' in HTML


def test_demo_mode_is_labelled_and_vietnamese_uses_a_font_with_diacritics():
    assert "DEMO · số liệu mô phỏng" in JS and "MÔ PHỎNG" in JS
    assert "fonts-portal.css" in HTML and "--vn: 'Be Vietnam Pro'" in HTML
    assert "font-family: var(--vn)" in HTML


def test_effects_use_one_clock_and_a_draw_error_never_stops_the_loop():
    # Lỗi 2026-10-12: t0 hiệu ứng lấy từ performance.now() còn vòng vẽ dùng dấu thời gian requestAnimationFrame → tiến độ âm →
    # arc() bán kính âm ném lỗi → mất mọi lớp vẽ sau đó (nút agent phía trước, tia, mảnh suy nghĩ) ở trạng thái nói / suy nghĩ / sự cố.
    for effect in ("shocks.push({ t0: performance.now()", "beams.push({ n, color, t0: performance.now()", "t0: performance.now(), dur"):
        assert effect not in JS, effect
    assert "clockNow = now;" in JS and "try { draw(now); }" in JS and "if (p < 0) continue;" in JS


def test_neural_interactions_exist_and_stay_display_only():
    # sợi thần kinh tới từng agent + tia đi theo sợi, xung synapse, nơ-ron bắn, tương tác chuột (rê / bấm) — vẫn không gửi gì ra ngoài
    for marker in ("function nerveCurve", "function fireNeuron", "cv.addEventListener('mousemove'", "cv.addEventListener('click'", "function showTip"):
        assert marker in JS, marker
    assert 'id="tip"' in HTML and "#frame > main { pointer-events: none; }" in HTML
    click = JS[JS.index("cv.addEventListener('click'"):JS.index("function clock()")]
    assert "fetch(" not in click and "send(" not in click                    # bấm chỉ tạo hiệu ứng


def test_v5_layout_modules_and_performance():
    # Người dùng bỏ vòng chữ dữ liệu + bệ phát ba chiều; thêm quỹ đạo module hệ thống nối với nhau; tối ưu để nhẹ máy.
    for gone in ("2b. bệ phát ba chiều", "4b. vòng chữ dữ liệu", "ringText", "ringData"):
        assert gone not in JS, gone
    for marker in ("MODULE_LINKS", "ring === 'in'", "function adaptQuality", "function setQuality", "BUCKET", "1b. lưới tổ ong", "7b. tia điện", "--px",
                   "function scramble", "function glitch"):
        assert marker in JS, marker
    draw = JS[JS.index("  function draw(now) {"):JS.index("  function hexPath(")]
    assert "createRadialGradient(PX" not in draw                            # không tạo gradient cho từng nơ-ron
    assert draw.count("shadowBlur") == 0                                    # không đổ bóng trong vòng vẽ (chỉ nút đang rê)
    assert "for (let i = 0; i < N; i++) {" in draw                          # toạ độ mọi hạt luôn đúng, chế độ nhẹ chỉ vẽ một nửa
    scr = JS[JS.index("function scramble"):JS.index("function glitch")]
    assert "setTimeout" in scr
    assert 'id="perf-chip"' in HTML
