# Điểm sẵn sàng production

> Phase 0 — 2026-10-05, commit `128c87d`. Thang §185: **PASS** (có bằng chứng) · **PARTIAL** · **FAIL** · **NOT IMPLEMENTED**. Không chấm PASS khi chưa có bằng chứng.
> Đánh giá riêng đường thoại (2026-10-03): `production-readiness.md`. Bằng chứng chi tiết: các tài liệu `docs/architecture`, `docs/autonomy`, `docs/security`, `docs/realtime`.

**Kết luận:** dùng được cho **một văn phòng, một tiến trình máy chủ, người vận hành tin cậy**. **Chưa** sẵn sàng làm "AI Supervisor tự trị" cho doanh nghiệp: lớp kiểm soát tự trị chưa có, và có 4 khoảng trống bảo mật mức cao (S1, S2, S4, S6) cùng một việc vận hành cần làm ngay (S7: đổi mật khẩu mặc định).

| Hạng mục | Điểm | Bằng chứng / lý do |
|---|---|---|
| Kiến trúc | **PARTIAL** | Một đường thoại, một provider LLM, một engine TTS, một kho audit (PASS từng phần); nhưng 2 danh mục tool, 3 đường phân quyền, 4 nguồn luật chính sách; `domain/` không được dùng (`canonical-components.md` §Đếm trùng lặp) |
| Bảo mật | **PARTIAL** | JWT mọi kênh, RBAC fail-closed, HITL bền, audit bất biến, bí mật mã hoá, timeout HTTP (có test). Hở: luật cấu hình không áp dụng (S1), không DENY / admin bỏ qua mức 5 (S2), chỉ thị bền ghi được (S4), không giới hạn đăng nhập (S6), mật khẩu mặc định còn hiệu lực (S7) — `security-architecture.md` §3 |
| Tự trị | **NOT IMPLEMENTED** | Không Policy Engine chuẩn, không L0–L5, không kill switch, không danh tính tác nhân, không sổ tác vụ / bằng chứng / kiểm chứng (`autonomy-model.md`) |
| Realtime | **PARTIAL** | Stream LLM, TTS theo câu, audio nhị phân, ngắt lời, hàng đợi có giới hạn, lệnh nhanh, câu đệm — PASS (test + bench). Chưa đạt: TTFA câu cần LLM p50 4,4 s; lệnh vận hành p50 12,8 s / p95 40,7 s; "kiểm tra CPU" chặn loop 50 ms; p99 và 50/100 phiên chưa đo (`voice-architecture.md` §4–§5) |
| Độ tin cậy | **PARTIAL** | Circuit breaker theo tool, nhớ model LLM hỏng, timeout mọi lời gọi ngoài (có test). Chưa: giới hạn tổng thời gian thử model (đo được treo 40 s), idempotency cho hành động gửi tin / giao việc |
| Quan sát | **PARTIAL** | Trace thoại đủ mốc (TTFD/TTFT/TTFA/TTL), trang topology realtime, audit. Chưa: trace bền (mất khi khởi động lại — đã gặp 2026-10-05), OpenTelemetry, đếm token / chi phí, chỉ số AI (§73) |
| Dữ liệu | **PARTIAL** | SQLite một chủ schema, một đường mở DB (RULE-014 = 0). Chưa: SQL ra khỏi application (3 module), phân loại dữ liệu, phạm vi phòng ban, PostgreSQL / Redis / object storage |
| Khả năng mở rộng | **FAIL** | Phiên, hàng đợi TTS, WS, trace nằm trong RAM một tiến trình — không chạy được nhiều bản |
| Quản trị AI (governance) | **NOT IMPLEMENTED** | Không danh mục hệ thống AI, không phiên bản chính sách / cấu hình tác nhân, không quy trình sự cố AI (§125–§129) |
| Kiểm thử | **PARTIAL** | 664 pytest + 14 test Node pass (2026-10-05); test kiến trúc RULE-011…015 có baseline. Chưa: test đối kháng (prompt injection, leo thang), bộ đánh giá agent, test hỗn loạn, tải 50/100 phiên |
| Triển khai | **PARTIAL** | CI chạy pytest (`.github/workflows/tests.yml`); `/livez` `/readyz` `/startupz`; gói Agent máy trạm có tự cập nhật. Chưa: lint / type-check / scan trong CI, container, kế hoạch rollback |
| Khôi phục | **NOT IMPLEMENTED** | Không có quy trình sao lưu / khôi phục có kiểm (§116); tắt máy không đóng pool HTTP, email gateway, luồng proactive, WS máy trạm (`server.py:1023–1052`) |

## Việc làm ngay (không cần đổi kiến trúc)

1. Đổi mật khẩu tài khoản `admin` trên máy chủ đang chạy (S7).
2. Không bật email gateway tự trả lời cho tới khi có chính sách giao tiếp ngoài (S8).
3. Không coi danh sách "từ khoá cấm" trên trang Bảo mật là đang chặn (S1) cho tới P2.
