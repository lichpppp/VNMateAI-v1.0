# VN-MateAI — Tài liệu Production

Dành cho: quản trị viên IT triển khai và vận hành VN-MateAI trong doanh nghiệp.

| Tài liệu | Nội dung |
|---|---|
| [deployment.md](deployment.md) | Yêu cầu, cài đặt, cấu hình bắt buộc, biến môi trường, cổng mạng, chứng chỉ, khởi động, health probe, thiết bị & worker |
| [security.md](security.md) | Mô hình xác thực/phân quyền theo kênh, HITL, audit, bí mật, rủi ro còn lại đã biết |
| [operations.md](operations.md) | Sao lưu/khôi phục, log, giám sát, xử lý sự cố thường gặp (LLM, Telegram, thiết bị) |
| [../architecture/dependency-rules.md](../architecture/dependency-rules.md) | Quy tắc kiến trúc và trạng thái tuân thủ (được test giữ) |
| [../migration/production-refactor-plan.md](../migration/production-refactor-plan.md) | Nhật ký từng phase refactor: lỗi tìm thấy, cách sửa, số đo thật |

## Trạng thái sẵn sàng (2026-10-02)

Đánh giá thẳng, dựa trên những gì đã kiểm chứng — **chưa** phải chứng nhận production:

**Đã kiểm chứng (test + chạy thật trên máy chủ):**
- Mọi API `/api/v1/*` và WebSocket người dùng/thiết bị/worker đều bắt buộc xác thực; cổng 8000 không TLS chỉ phục vụ WebSocket thiết bị + health probe.
- Một kho tài khoản (SQLite), một kho audit bất biến, một cổng HITL; tác vụ rủi ro cao chỉ chạy sau khi được duyệt.
- Skill đồng bộ chạy ngoài event loop (server không đứng khi tool chậm).
- `config.json` ghi nguyên tử; health probe `/livez`, `/readyz`, `/startupz`.
- 270 test tự động pass.

**Chưa có / chưa kiểm chứng:**
- Chạy nhiều tiến trình / nhiều máy (state nằm trong bộ nhớ tiến trình: phiên thoại, hàng đợi HITL, trí nhớ model hỏng). **Chỉ chạy MỘT tiến trình.**
- PostgreSQL (hiện SQLite; đã gom về một đường mở kết nối để chuẩn bị).
- Đo tải / benchmark đồng thời nhiều người dùng.
- Connector M365 / eInvoice / Paperless / OCI chưa được chạy thật (không có tài khoản thử).
- Chứng chỉ TLS mặc định là tự ký.
