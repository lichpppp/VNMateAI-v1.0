# Bảo mật VN-MateAI

Dành cho: quản trị viên IT và người đánh giá an ninh. Mọi điểm dưới đây được giữ bằng test tự động (tên test trong ngoặc).

## 1. Xác thực theo kênh

| Kênh | Danh tính | Không hợp lệ thì |
|---|---|---|
| REST `/api/v1/*`, `/api/erp/*` | JWT người dùng (Bearer) | 401. Chỉ `login`, `config/assistant-name`, `health-dashboard` là công khai (`test_public_endpoints_locked`) |
| `POST /api/v1/worknodes/heartbeat` | Enrollment secret của worker, hoặc JWT admin/manager — JWT người dùng thường KHÔNG đủ | 401 |
| `/ws/portal-ui`, `/ws/voice`, `/ws/v1/voice-stream`, `/ws/topology` | JWT (`?token=`) | đóng 1008 / HTTP 403 (`test_websockets_require_login`) |
| `/ws/hud` | JWT; không có thì chỉ xem telemetry, **không** nhận lệnh thoại | `auth_required` (`test_hud_requires_login`) |
| `/ws/client` (client agent) | Enrollment secret (gói tải agent) hoặc JWT admin/manager | đóng 1008 |
| `/api/v1/xiaozhi/ws/<id>`, `/ws/audio-stream/<id>` (ESP32) | Token riêng của đúng `<id>` (khuyến nghị), hoặc token chung (tắt được bằng `security.require_per_device_token`), hoặc JWT admin/manager. **Không** có ngoại lệ theo IP LAN | HTTP 403 (`test_device_auth_requires_token`, `test_per_device_tokens`) |
| Telegram | `chat_id` phải nằm trong `admin_chat_ids`; danh sách trống = không ai | tin nhắn bị bỏ qua, nút duyệt bị từ chối (`test_role_resolution_order`) |
| Cổng 8000 (không TLS) | chỉ đường thiết bị + health probe | 404 / đóng 1008 (`test_iot_port_filter`) |

Tài khoản: duy nhất bảng `users` trong `vnmateai.db` (bcrypt). Xoá tài khoản có hiệu lực ngay, không sống lại khi khởi động lại (`test_single_user_store`).

## 2. Phân quyền tool (RBAC)

- Cổng duy nhất: `mateai.application.agent.tool_gate.run_tool_with_policy` → `security_guard.check_permission` (mọi kênh: chat, voice, REST, Telegram).
- Danh tính dùng để xét quyền = **người đã đăng nhập** (không phải `source_device` do client gửi).
- Thứ tự xác định role: (1) tài khoản/nhân viên trong CSDL → role trong CSDL; (2) service principal khai báo tường minh; (3) id do server gán có tiền tố `esp32`, `xiaozhi`, `telegram`, `hud`, `robot` → **admin** (quyết định của chủ dự án, f389bbe); (4) còn lại / lỗi tra cứu → `viewer` (fail-closed).
- Role portal → role RBAC: `admin`→`admin`, `manager`→`it_support`, `viewer`→`operator`.
- Tool cấm vĩnh viễn kể cả admin: `format_drive`, `wipe_all_data`.

## 3. Phê duyệt (HITL)

- Một hàng đợi duy nhất: `mateai.application.security.zero_trust.hitl_manager` — cả tác vụ từ cổng tool (hội thoại, portal, `fs/*`, máy trạm; `kind="tool"`) lẫn `/skills/execute`, Plugin Registry, computer-use. Duyệt ở portal, HUD, panel HITL, nút Telegram hay câu "đồng ý" đều đi qua `approve_async`. Tool mức rủi ro ≥ 3 (hoặc `NEED_CONFIRM`) cần duyệt; yêu cầu hết hạn sau 15 phút; yêu cầu `kind="tool"` còn hạn được khôi phục từ audit khi khởi động lại (`test_pending_action_lookup`).
- Tác vụ chỉ chạy **sau khi** được duyệt (callback), kể cả computer-use mức 4 (`test_phase90_computer_use`). Không tạo được yêu cầu duyệt → không chạy.
- Duyệt qua: portal/HUD (`POST /api/v1/security/confirm-action`, `/api/v1/enterprise/hitl/approve`, lệnh `confirm_action` trên `/ws/hud` — **chỉ admin**), Telegram (chat trong `admin_chat_ids`), lệnh "đồng ý"/"huỷ" trong hội thoại: tác vụ của chính người nói, hoặc của người khác nếu người nói có role admin.
- Duyệt chỉ chạy **đúng** tác vụ trong hàng đợi (tên tool, tham số, máy đích lấy từ hàng đợi; body không thay được), qua cổng tool chung với `approved=True`. Cờ `confirmed` trong tham số tool/body bị bỏ qua — LLM và client tự đặt được nó (`test_confirm_action_endpoint`, `test_tool_policy_gate`).
- "Đồng ý"/"huỷ" nhận theo ranh giới từ, ý phủ định thắng, chỉ câu ngắn ("hủy" từng bị hiểu là đồng ý — `test_approval_reply_classifier`).
- `/api/v1/fs/*` chỉ admin; tool `read_file` từ chối tệp chứa bí mật (`config.json`, `.env*`, khoá, chứng chỉ, CSDL) cho mọi kênh (`test_fs_routes_policy`).

## 4. Audit

- Một kho: bảng `audit_logs` (chỉ INSERT; không có API sửa/xoá — `DELETE /api/v1/security/audit-logs` trả 405). Ghi cả quyết định RBAC lẫn sự kiện Zero-Trust/HITL (`test_audit_single_store`).
- Xem: portal → Bảo mật, hoặc `GET /api/v1/security/audit-logs` / `GET /api/v1/audit-logs` — **chỉ admin** (payload chứa tham số tác vụ, vd nội dung tệp ghi qua `fs/write`). Danh sách tác vụ chờ duyệt (`GET /api/v1/security/pending-action`) cũng chỉ admin.

## 5. Bí mật

| Bí mật | Vị trí | Ghi chú |
|---|---|---|
| Khoá ký JWT | `VNMATEAI_JWT_SECRET` hoặc `certs/jwt_secret.key` | đổi = mọi phiên đăng nhập hết hiệu lực |
| Secret worker / thiết bị | `certs/worker_secret.key`, `certs/device_secret.key` | đổi = phải phát lại gói agent / nạp lại firmware |
| Khoá LLM, token Telegram | biến môi trường hoặc `config.json` | API cấu hình chỉ trả ký hiệu che, không trả giá trị thật |
| Chứng chỉ TLS | `certs/server.crt`, `certs/server.key` | |

`config.json`, `certs/`, `*.key`, `*.pem`, `secrets.h` bị `.gitignore`. Log được lọc: mọi giá trị sau `api.telegram.org/bot`, khoá dạng `sk-…`, và tham số `token`/`api_key`/`password` trong query bị thay bằng ký hiệu (`test_telegram_outbound_guard`, `test_phase80_secret_masking` — quét mọi route GET bằng 3 role trên máy chủ thật).

## 6. Rủi ro còn lại đã biết (chưa xử lý)

| Rủi ro | Mức | Ghi chú / giảm thiểu |
|---|---|---|
| Id kênh do server gán có tiền tố `esp32/xiaozhi/telegram/hud/robot` nhận admin | Trung bình | An toàn khi mọi kênh đều xác thực (đã làm). Kênh mới nào truyền id do client tự đặt sẽ thành admin — rà khi thêm kênh |
| Token CHUNG cho thiết bị vẫn được nhận (tương thích firmware cũ) | Trung bình → Thấp khi tắt | Đã có token riêng theo từng thiết bị. Sau khi nạp token riêng cho mọi robot, bật `security.require_per_device_token` (xem owner-todo.md) |
| Cổng 8000 không TLS | Thấp–TB | Chỉ còn đường thiết bị; giới hạn bằng VLAN/tường lửa |
| Hai mô hình role (portal ↔ RBAC) | Thấp | Ánh xạ cố định ở trên; gộp cần đổi role trong CSDL |
| State trong bộ nhớ (HITL, phiên thoại, trí nhớ model) | Vận hành | Khởi động lại: yêu cầu từ cổng tool được khôi phục từ audit; yêu cầu dùng closure (`/skills/execute`, Plugin Registry, computer-use) bị mất — phải gửi lại |
| Connector M365/eInvoice/Paperless/OCI chưa chạy thật | Chưa kiểm chứng | Bật từng connector trong môi trường thử trước |
| Chứng chỉ tự ký | Thấp | Thay bằng chứng chỉ CA |
| `POST /api/v1/clients/{id}/kill-process` và `/deploy-skill` (admin) chạy thẳng trên máy trạm, không qua HITL | Thấp–TB | Chỉ admin, nay có audit (deploy ghi tên tệp + SHA-256 mã). Đưa qua HITL nếu cần duyệt hai người |
| Hai endpoint đọc cùng bảng audit (`/api/v1/audit-logs` và `/api/v1/security/audit-logs`), UI gọi cả hai | Thấp | Cả hai chỉ admin (§4); gộp làm một ở phase Security |
