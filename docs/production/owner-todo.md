# Việc chủ dự án cần làm (điền sau)

Dành cho: chủ dự án / quản trị viên. Những việc dưới đây cần thông tin hoặc thiết bị thật mà máy chủ không tự làm được. Đánh dấu `[x]` khi xong.

## Bắt buộc trước khi dùng thật

- [ ] **Đổi mật khẩu 3 tài khoản mặc định** (`admin/admin123`, `manager/manager123`, `viewer/viewer123`) trong trang quản trị → Người dùng.
- [x] **LLM model** (2026-10-02, đo thật từng model qua 9Router — 3 lượt cho model chạy được):
  - Chính: `ag/gemini-3.8-flash-low` (trung vị 2,7 s, 3/3).
  - Dự phòng theo độ trễ: `ag/claude-sonnet-4-6`, `ag/claude-opus-4-6-thinking`, `ag/gemini-3.7-flash-low`, `ag/gemini-3.7-flash-medium`, `ag/gemini-3.8-flash-medium`, `ag/gemini-3.7-flash-high`, `ag/gemini-3.6-flash-low`, `ag/gemini-3.8-flash`, `ag/gemini-3.6-flash-high`.
  - Chuyên gia: `ag/claude-sonnet-4-6`, `ag/claude-opus-4-6-thinking`, `ag/gemini-3.8-flash-medium`.
  - Đã bỏ (thêm lại trong trang Cấu hình LLM nếu 9Router sửa): **đã ngừng** `ag/gemini-3-flash-agent`, `ag/gemini-3.5-flash-low`, `ag/gemini-3.5-flash-extra-low`; **trả rỗng** `ag/gpt-oss-120b-medium`, `ag/gemini-3.1-pro-low`, `ag/gemini-pro-agent`, `ag/gemini-3-flash`; **lỗi 400/503** `oc/ling-3.0-flash-fin-free`, `ag/gemini-3.5-flash-high`, `openrouter/typesafe/jev-1.13`; **timeout 15 s** `ag/gemini-3.6-flash-medium`, `ag/gemini-3.8-flash-high`; giá trị mẫu `YOUR_MODEL_NAME_HERE`.
  - Kết quả: lượt thoại qua LLM chữ đầu 8,5 s (lượt đầu) / 2,8 s (lượt sau) — trước đó 88,9 s.
  - Còn lại: khối cũ `router.primary.provider_model` = `oc/ling-3.0-flash-fin-free` (lỗi 400) — KHÔNG được dùng khi đã có khối `llm`; có thể xoá khỏi config.json cho gọn.
- [x] **9Router** đã hoạt động lại (chủ dự án sửa 2026-10-02).

## Telegram

- [x] Token thật đã thêm. Lưu ý: token từng được lưu thành `••••••••<token>` (dán sau ký hiệu che trên giao diện) → đã sửa dữ liệu và sửa máy chủ để tự bỏ ký hiệu che khi lưu.
- [x] `admin_chat_ids` (1 id) và `incident_group_id` đã điền; `enabled` = true; bot `@VNMateai_bot` polling chạy; tin thử gửi tới nhóm `-1003922961701` thành công.
- [x] Id nhóm `-1003922961701` đã thêm vào `admin_chat_ids` (2026-10-02): mọi thành viên nhóm ra lệnh / bấm duyệt được (quyền admin của kênh Telegram) — chỉ giữ người được phép trong nhóm.
- [ ] Kiểm tra: nhắn bot một câu trong nhóm (vd. "mấy giờ rồi") và thử bấm nút duyệt của một yêu cầu HITL.

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
- [ ] **Webhook giờ bị TỪ CHỐI nếu chưa có chữ ký** (2026-10-02). Khi tích hợp hệ thống gửi webhook, đặt `VNMATE_WEBHOOK_<NGUỒN>_SECRET` (`PAPERLESS`, `EINVOICE`, `CUSTOM`, `OCI`; AWS SNS dùng chứng chỉ ký). Chỉ khi cài đặt thử mới tạm bật `"security": {"allow_unsigned_webhooks": true}`.
- [ ] Xoá trong nhóm Telegram tin cảnh báo thử "smoke" (2026-10-02 19:44) — do kiểm tra webhook trước khi chặn.

## Thay đổi cần biết (2026-10-03)

- **Chỉ admin duyệt được tác vụ rủi ro cao** (portal, HUD, panel HITL, Telegram, câu "đồng ý"). Manager không còn duyệt được. Danh sách chờ duyệt và nhật ký audit cũng chỉ admin xem.
- **Mọi yêu cầu duyệt giờ đi một hàng đợi** và tác vụ cần duyệt từ hội thoại/portal/`fs`/máy trạm cũng **gửi thẻ duyệt có nút bấm tới Telegram** (trước chỉ một số loại). Nếu thấy nhiều tin hơn trước, đó là lý do.
- `/api/v1/fs/*` chỉ admin; công cụ đọc tệp từ chối tệp chứa bí mật (`config.json`, `.env`, khoá, chứng chỉ, CSDL) ở mọi kênh.
- Endpoint cũ `/api/v1/audit-logs` đã gỡ — dùng `/api/v1/security/audit-logs` (giao diện đã chuyển). Ai có script riêng gọi đường cũ cần đổi.

## Kiểm tra / quyết định còn mở

- [ ] **CI GitHub Actions** (`.github/workflows/tests.yml`) đã thêm nhưng **chưa thấy chạy trên GitHub** (máy này không có `gh`). Mở tab Actions của repo xem lần chạy đầu; nếu bước `pip install` lỗi (torch, pyaudio trên runner) báo lại để chỉnh. Bộ test đã qua 371/371 trên bản checkout sạch với `config.example.json`.
- [ ] **Skill trùng giữa máy chủ và gói agent máy trạm**: `skills/{custom_skills, pc_control_skills, sysadmin_skills}.py` giống hệt `client_agent/skills/`; `file_system`, `monitoring_skills`, `excel_records_skill`, `visual_skills` đã lệch nhau. Quyết định: máy chủ có cần tự điều khiển chính nó (chuột/bàn phím/cửa sổ) không? Nếu không → bỏ bản ở `skills/`, chỉ giữ ở agent; nếu có → giữ một nguồn và đóng gói agent từ nguồn đó.
- [ ] **PostgreSQL** (kế hoạch: `docs/migration/sqlite-to-postgresql-plan.md`): cần máy chủ PG và chọn thời điểm cutover. Chưa làm.
- [ ] **Tách tiến trình api / realtime / worker + Redis** (state chia sẻ: hàng đợi duyệt, phiên thoại, WebSocket): chưa làm — cần Redis và quyết định có chạy nhiều tiến trình không. Hiện một tiến trình là đủ cho một văn phòng; khôi phục hàng đợi duyệt sau khởi động lại đã có.
- [ ] **Container**: chưa làm — ứng dụng dùng COM/pywin32, micro, điều khiển màn hình Windows nên không chạy được trong container Linux; nếu cần, chỉ tách phần API thuần.
- [ ] `skills/registry.json` và hai skill do AI tạo (`skills/auto_play_music.py`, `skills/thong_ke_lo_xsmb.py`) cùng `get_lotto.py`, `generate_pdf.py` ở gốc repo là tệp sinh trong lúc chạy / của bạn — **chưa commit, không đụng tới**. Xem lại và tự quyết có đưa vào git không.
