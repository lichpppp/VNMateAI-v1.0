# Điểm sẵn sàng production

> Cập nhật 2026-10-05 sau Supervisor P1–P14 (commit `5f03aca`). Bản Phase 0 cùng ngày: lịch sử git của tệp này. Thang §185: **PASS** (có bằng chứng) · **PARTIAL** · **FAIL** · **NOT IMPLEMENTED**. Không chấm PASS khi chưa có bằng chứng.
> Đánh giá riêng đường thoại: `production-readiness.md`. Trả lời 30 câu hỏi §213: `docs/migration/final-audit.md` §0.

**Kết luận:** dùng được cho **một văn phòng, một tiến trình máy chủ**, với AI **tự trị có giới hạn**: mọi hành động đi qua một cổng chính sách ngoài LLM, có dừng khẩn cấp, có sổ tác vụ, kiểm chứng và audit. **Chưa** sẵn sàng cho nhiều tiến trình / nhiều chi nhánh, phân vùng dữ liệu theo phòng ban, hay đánh giá chất lượng model trên LLM thật. **Việc vận hành phải làm ngay:** đổi mật khẩu mặc định `admin`.

| Hạng mục | Phase 0 | Nay | Bằng chứng / lý do |
|---|---|---|---|
| Kiến trúc | PARTIAL | **PARTIAL** | Một đường thoại, một provider LLM, một TTS (RULE-012 = 0), một cổng chính sách (RULE-017 = 0), application không SQL (RULE-024 = 0), lớp domain chết đã xoá. Còn: 2 danh mục tool (L5), schema sự kiện HUD riêng (L10), router lớn còn nghiệp vụ (`routers/enterprise.py` 1 436 dòng) |
| Bảo mật | PARTIAL | **PARTIAL** | Đóng 11 lỗ hổng (`docs/evaluation/security-evaluation.md` §3); 8 kịch bản đối kháng ĐẠT. Còn: mật khẩu mặc định (S7), không ABAC phòng ban (S10), bản cấu hình chưa mã hoá còn trên đĩa |
| Tự trị | NOT IMPLEMENTED | **PASS** (phạm vi một tiến trình) | Policy Engine + Risk Engine, L0–L5, kill switch toàn cục / tác nhân / tool, danh tính tác nhân, uỷ quyền có hạn, ngân sách lượt, sổ tác vụ có máy trạng thái, kiểm chứng, bằng chứng, leo thang — `test_policy_engine`, `test_task_ledger`, `test_autonomy_controls`, kịch bản vàng; kiểm thật: kill switch chặn ghi tệp |
| Realtime | PARTIAL | **PARTIAL** | Stream, TTS theo câu, audio nhị phân, ngắt lời, lệnh nhanh, câu đệm: PASS. Câu cần LLM p50 4,9 s / lệnh vận hành phụ thuộc nhà cung cấp (`performance-before-after.md`); p99, 50/100 phiên chưa đo |
| Độ tin cậy | PARTIAL | **PARTIAL** | Thêm: ngân sách tổng thử model (chờ lỗi ≤ 12,7 s thay vì tới 40,7 s — đo thật), idempotency giao việc, tắt máy đóng pool / luồng nền. Còn: idempotency cho các tác dụng phụ khác (gửi tin), circuit breaker cho TTS |
| Quan sát | PARTIAL | **PARTIAL** | Trace thoại bền 30 ngày, token / tác vụ, bảng AI Supervisor, audit có `agent_id` + `policy_version`. Chưa: OpenTelemetry, chi phí tiền (chưa có bảng giá), p99 có đủ mẫu |
| Dữ liệu | PARTIAL | **PARTIAL** | Một kho SQLite, truy vấn ở tầng repository. Chưa: phân loại dữ liệu, phạm vi phòng ban, PostgreSQL / Redis / object storage |
| Khả năng mở rộng | FAIL | **FAIL** | Phiên WS, hàng đợi TTS, giới hạn đăng nhập, idempotency nằm trong RAM một tiến trình |
| Quản trị AI | NOT IMPLEMENTED | **PARTIAL** | `docs/governance/ai-governance.md`: danh mục hệ thống AI, kiểm soát, đo lường, thay đổi có audit + phiên bản chính sách. Chưa: phiên bản tác nhân tách riêng, quy trình phê duyệt thay đổi model, đánh giá tác động định kỳ |
| Kiểm thử | PARTIAL | **PARTIAL** | 714 pytest + 14 Node, gồm test kiến trúc (RULE-011…026), đối kháng, kịch bản vàng, sao lưu. Chưa: đánh giá model trên LLM thật, tải 50/100 phiên, test hỗn loạn có hệ thống |
| Triển khai | PARTIAL | **PARTIAL** | CI: pytest + Node + cú pháp + chặn bí mật trong git. Chưa: linter, kiểm kiểu, quét phụ thuộc, container, staging |
| Khôi phục | NOT IMPLEMENTED | **PARTIAL** | `scripts/backup.py` tạo / kiểm chứng / khôi phục (có bản an toàn trước khi ghi đè) — chạy thật 0,09 s, ĐẠT; runbook. Chưa: lịch sao lưu tự động, bản sao ngoài máy, RPO / RTO cam kết |

## Việc chủ hệ thống cần làm

1. Đổi mật khẩu `admin` (máy chủ vẫn nhận `admin123`).
2. Xoá `certs/config.json.pre-encrypt.bak` sau khi đã kiểm cấu hình mã hoá chạy đúng.
3. Đặt lịch `python scripts/backup.py create` (Task Scheduler) và chép `backups/` ra nơi lưu trữ được bảo vệ.
4. Nạp firmware 54 cho robot khi cắm (`pio run -e esp32s3 -t upload --upload-port COM7`).
