# Điểm sẵn sàng production

> Cập nhật 2026-10-05 sau Supervisor P1–P14 (commit `5f03aca`). Bản Phase 0 cùng ngày: lịch sử git của tệp này. Thang §185: **PASS** (có bằng chứng) · **PARTIAL** · **FAIL** · **NOT IMPLEMENTED**. Không chấm PASS khi chưa có bằng chứng.
> Đánh giá riêng đường thoại: `production-readiness.md`. Trả lời 30 câu hỏi §213: `docs/migration/final-audit.md` §0.

**Kết luận:** dùng được cho **một văn phòng, một tiến trình máy chủ**, với AI **tự trị có giới hạn**: mọi hành động đi qua một cổng chính sách ngoài LLM, có dừng khẩn cấp, có sổ tác vụ, kiểm chứng và audit. **Chưa** sẵn sàng cho nhiều tiến trình / nhiều chi nhánh, phân vùng dữ liệu theo phòng ban, hay đánh giá chất lượng model trên LLM thật. **Việc vận hành phải làm ngay:** đổi mật khẩu mặc định `admin`.

| Hạng mục | Phase 0 | Nay | Bằng chứng / lý do |
|---|---|---|---|
| Kiến trúc | PARTIAL | **PASS** | Một bản chuẩn mỗi chức năng; 10 luật kiến trúc = 0; router mỏng (RULE-027 = 0); `server.py` 264 dòng; bảng route cố định bằng test. Danh mục tool một, bộ thực thi connector có lý do (breaker); HUD là kênh phát hiển thị, audio nhị phân như voice (`current-system-map.md` §6) |
| Bảo mật | PARTIAL | **PARTIAL** | ABAC phòng ban + cấp bảo mật, phân loại dữ liệu trong tool contract, audit chuỗi băm + trigger, rate limit voice / agent / WS / đăng nhập, quét lỗ hổng phụ thuộc (đã nâng 5 gói; chromadb không áp dụng — chế độ nhúng), skill có lệnh cấp module bị từ chối nạp; 15 kịch bản vàng gồm injection / chéo phòng ban / đa tác nhân. Còn: mật khẩu admin mặc định (S7), bản cấu hình chưa mã hoá còn trên đĩa |
| Tự trị | NOT IMPLEMENTED | **PASS** (phạm vi một tiến trình) | Policy Engine + Risk Engine, L0–L5, kill switch toàn cục / tác nhân / tool, chế độ khẩn cấp tự chuyển chỉ-đọc khi AI hành động dồn dập (§54, 2026-10-06), dừng tool lỗi lặp, danh tính tác nhân, uỷ quyền có hạn, ngân sách lượt, sổ tác vụ có máy trạng thái, kiểm chứng, bằng chứng, leo thang — `test_policy_engine`, `test_task_ledger`, `test_autonomy_controls`, kịch bản vàng; kiểm thật: kill switch chặn ghi tệp |
| Realtime | PARTIAL | **PARTIAL** | Stream, TTS theo câu, audio nhị phân (cả HUD phê duyệt), ngắt lời, lệnh nhanh, jitter buffer 50–250 ms, đánh giá codec có số đo (`codec-evaluation.md`). Còn: STT máy chủ có kết quả tạm, Opus cho micro robot (firmware), p99 / 50–100 phiên chưa đo |
| Độ tin cậy | PARTIAL | **PARTIAL** | Ngân sách thử model, idempotency giao việc, tắt máy đóng pool, Redis lỗi → lùi về RAM, dừng tool lỗi lặp, chế độ khẩn cấp. Còn: idempotency cho gửi tin, circuit breaker cho TTS |
| Quan sát | PARTIAL | **PASS** | OpenTelemetry (http / voice + giai đoạn / tool + quyết định chính sách / llm), log JSON có request_id + che bí mật, trace thoại bền 30 ngày, báo cáo khởi động từng bước, token / tác vụ. Chi phí tiền chưa có (router không trả giá) |
| Dữ liệu | PARTIAL | **PARTIAL** | Repository một tầng; phân loại dữ liệu + phạm vi phòng ban; Redis (`shared_state`) và S3 (`object_storage`) kiểm trên server thật; di trú PostgreSQL có kiểm chứng — bản sao dữ liệu thật 23 bảng / 1 195 dòng khớp. Còn: cutover sang PostgreSQL |
| Khả năng mở rộng | FAIL | **PARTIAL** | Giới hạn đăng nhập, rate limit, bộ đếm khẩn cấp dùng chung qua Redis (kiểm trên máy chủ thật); tệp nhị phân ra object storage. Còn: SQLite (chờ cutover PostgreSQL), phiên WS + hàng đợi TTS theo tiến trình (cần sticky session), idempotency giao việc trong RAM |
| Quản trị AI | NOT IMPLEMENTED | **PARTIAL** | `docs/governance/ai-governance.md`: danh mục hệ thống AI, kiểm soát, đo lường, thay đổi có audit + phiên bản chính sách. Chưa: phiên bản tác nhân tách riêng, quy trình phê duyệt thay đổi model, đánh giá tác động định kỳ |
| Kiểm thử | PARTIAL | **PASS** | 827 pytest + 14 Node: kiến trúc, đối kháng, kịch bản vàng 15/15, hạ tầng thật (Redis / S3 / PostgreSQL), sao lưu; `check()` cũ không còn trượt im lặng. Còn: đánh giá trên LLM thật, tải 50/100 phiên |
| Triển khai | PARTIAL | **PARTIAL** | CI: lint lỗi chạy thật + pip-audit + pytest + Node; `deploy/docker-compose.infra.yml` (PostgreSQL, Redis, S3, OTel collector); `scripts/dev_infra.py` khi không có Docker. Còn: CI chưa xác nhận chạy trên GitHub, Docker trên máy này cần `wsl --update`, ứng dụng chạy trên host Windows |
| Khôi phục | NOT IMPLEMENTED | **PARTIAL** | `scripts/backup.py` tạo / kiểm chứng / khôi phục (có bản an toàn trước khi ghi đè) — chạy thật 0,09 s, ĐẠT; runbook. Chưa: lịch sao lưu tự động, bản sao ngoài máy, RPO / RTO cam kết |

## Việc chủ hệ thống cần làm

1. Đổi mật khẩu `admin` (máy chủ vẫn nhận `admin123`).
2. Xoá `certs/config.json.pre-encrypt.bak` sau khi đã kiểm cấu hình mã hoá chạy đúng.
3. Đặt lịch `python scripts/backup.py create` (Task Scheduler) và chép `backups/` ra nơi lưu trữ được bảo vệ.
4. Nạp firmware 54 cho robot khi cắm (`pio run -e esp32s3 -t upload --upload-port COM7`).
