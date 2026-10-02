# Việc chủ dự án cần làm (điền sau)

Dành cho: chủ dự án / quản trị viên. Những việc dưới đây cần thông tin hoặc thiết bị thật mà máy chủ không tự làm được. Đánh dấu `[x]` khi xong.

## Bắt buộc trước khi dùng thật

- [ ] **Đổi mật khẩu 3 tài khoản mặc định** (`admin/admin123`, `manager/manager123`, `viewer/viewer123`) trong trang quản trị → Người dùng.
- [ ] **LLM model** — trong trang Cấu hình LLM (`llm.model_name`, `router_models`, `specialist_models`):
  - Bỏ các model **đã ngừng** (máy chủ nhận câu "Gemini 3.5 Flash is no longer available"): `ag/gemini-3-flash-agent`, `ag/gemini-3.5-flash-extra-low`, `ag/gemini-3.5-flash-low`.
  - `ag/claude-opus-4-6-thinking`, `ag/claude-sonnet-4-6` **hết quota tới 2026-10-06 09:22 UTC** — giữ cuối danh sách hoặc bỏ.
  - Bỏ giá trị mẫu `YOUR_MODEL_NAME_HERE` trong danh sách model chuyên gia.
  - Đặt model đang chạy lên đầu (đã chạy được khi kiểm tra: `ag/gemini-3-flash`).
  - Kiểm: hỏi một câu trên portal; log không còn dòng `Tạm xếp cuối model …`.

## Khi bật Telegram

- [ ] `telegram.bot_token`: token thật từ @BotFather (dạng `123456789:AA…`). Hiện là giá trị mẫu.
- [ ] `telegram.admin_chat_ids`: chat id thật của người được ra lệnh/duyệt (hiện là `YOUR_TELEGRAM_ADMIN_CHAT_ID_HERE`). Nhắn bot một tin rồi dùng nút "Dò chat" trong trang Telegram để lấy id.
- [ ] Bật `telegram.enabled`, bấm "Kiểm tra kết nối".

## Robot ESP32 / Xiaozhi (mỗi robot)

Máy chủ đã hỗ trợ token riêng cho từng robot. Mã firmware đã được sửa để đưa id robot vào đường dẫn kết nối — **chưa build/nạp thử được trên chip thật**.

- [ ] Chọn id riêng cho robot (vd. `robot_phong_hop`; chữ, số, `_ - .`, ≤ 64 ký tự).
- [ ] Cấp token (tài khoản admin): `POST /api/v1/security/devices` với `{"device_id": "<id>"}` — token chỉ hiện một lần.
- [ ] `esp32_firmware/src/secrets.h`: đặt `DEFAULT_DEVICE_ID` và `DEFAULT_DEVICE_TOKEN`; build và nạp.
- [ ] Kiểm: robot kết nối được; `GET /api/v1/security/devices` có `last_seen_at`.
- [ ] Khi **mọi** robot đã có token riêng: thêm `"security": {"require_per_device_token": true}` vào `config.json` → token chung cũ (`certs/device_secret.key`) bị từ chối.

Robot nạp firmware cũ (token rỗng) hiện bị từ chối (HTTP 403) — nạp lại theo các bước trên.

## Hạ tầng

- [ ] Thay chứng chỉ tự ký `certs/server.crt` / `certs/server.key` bằng chứng chỉ do CA cấp (giữ tên file).
- [ ] Tường lửa: chỉ cho VLAN thiết bị vào cổng 8000; người dùng vào cổng 443.
- [ ] Đặt `VNMATEAI_JWT_SECRET` nếu sẽ chạy lại máy chủ ở máy khác (để phiên đăng nhập không mất).
- [ ] Worker daemon (nếu dùng): đặt `VNMATE_ENROLLMENT_TOKEN` (giá trị `enrollment_token` trong gói tải agent) và `MASTER_API_URL`.
- [ ] Lịch sao lưu `vnmateai.db`, `hr_kpi.db`, `certs/`, `config.json` (xem operations.md §1).

## Connector (khi dùng)

- [ ] M365 / eInvoice / Paperless / OCI chưa được chạy thật (không có tài khoản thử). Bật từng cái trong môi trường thử; webhook cần `VNMATE_WEBHOOK_<NGUỒN>_SECRET`.
