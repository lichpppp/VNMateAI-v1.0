# Kiểm toán cuối — refactor production (Phase 11)

Ngày: 2026-10-03 · Nhánh `refactor/phase-0-1-safety-net` · Mốc so sánh: commit `4f6464a` (trước refactor).
Mọi số dưới đây đo bằng `git` / `pytest` / quét AST trên repo — không ước lượng.

## 1. Tổng quan thay đổi

| Chỉ số | Giá trị |
|---|---|
| Commit kể từ mốc | 58 (tới `f77cca4`) |
| Tệp xoá / thêm / đổi chỗ | 71 / 125 / 66 |
| Dòng thêm / xoá | +21.194 / −24.321 (giảm ròng ~3.100 dòng) |
| `core/` | còn **1 module**: `core/plugin_manager.py` (API plugin công khai) |
| `src/mateai` | 171 tệp Python, mọi code chạy thật |
| `server.py` | 8.898 (mốc) → **1.021 dòng** (chỉ dựng app); 30 router trong `interfaces/http/routers/` |
| Route HTTP/WS | 183 route + 3 mount tĩnh (bảng route so với HEAD sau mỗi bước tách; `/api/v1/audit-logs` gỡ có chủ đích ở §52) |
| Test | 96 tệp; **371 pass**, 1 bỏ qua (`network`, gọi model thật) |
| Bản checkout sạch + `config.example.json` | 371 pass (môi trường Python có sẵn) |

## 2. Một chức năng — một implementation

| Chức năng | Trước | Nay (bản duy nhất) | Test canh giữ |
|---|---|---|---|
| Lượt thoại | 5 đường (portal WS, HUD, Xiaozhi, mic máy chủ, REST) mỗi đường một vòng LLM/TTS | `application/voice/voice_turn.process_voice_turn` (transport giữ riêng) | test voice pipeline, phase67 |
| Tổng hợp giọng | `AudioEngine` + `TTSStreamEngine` + race gTTS trong server | `infrastructure/tts/tts_stream_engine` | RULE-012 |
| Làm sạch text TTS | 3 hàm | `application/voice/speech_text` | |
| Gọi LLM | client OpenAI tự tạo ở 5 module, 2 vòng fallback | `infrastructure/llm/llm_provider` | RULE-011 (3 chỗ có lý do: nút thử kết nối) |
| Thực thi tool | vòng agent + vòng voice riêng (vòng voice không qua kiểm tra nào) | `application/agent/tool_gate.run_tool_with_policy` | `test_tool_policy_gate` |
| Hàng đợi duyệt (HITL) | `StateManager` + `hitl_manager` | `zero_trust.hitl_manager` (executor theo `kind`) | `test_pending_action_lookup` |
| Kho audit | `audit_logs` + `logs/security_audit.log` (xoá được) | bảng `audit_logs` (chỉ INSERT), một endpoint đọc | `test_audit_single_store` |
| Kho tài khoản | `users.json` + bảng `users` | bảng `users` | |
| Đọc/ghi `config.json` | tự mở ở nhiều module | `config/loader` (`read_raw_config`, `write_raw_config` nguyên tử) | RULE-013 (cả `CONFIG_PATH.read_text/write_text/open`) |
| Mở SQLite | `sqlite3.connect` rải rác | `erp_database.open_sqlite` | RULE-014 |
| Thư mục gốc dự án | suy từ `__file__` ở nhiều nơi | `loader._resolve_project_root` | `test_project_root_single_source` |
| Tên trợ lý | 4 cách đọc (server/lời chào luôn "Ly Ly") | `loader.get_assistant_name` | `test_assistant_name_single_source` |
| Che bí mật cấu hình | trong server | `interfaces/http/secret_masking` | phase80 |
| Secret ghi danh worker/thiết bị | 2 hàm gần giống | `interfaces/http/enrollment` | |
| Xác thực WebSocket | trong server | `interfaces/http/ws_auth` | `test_websockets_require_login` |
| Đường dẫn `hr_kpi.db` | tính ở 3 module | `domain_sync.DEFAULT_DB_PATH` | `test_hr_db_path_has_one_owner` |

## 3. Lớp bọc / bản sao CÒN LẠI và lý do

| Thành phần | Loại | Lý do giữ |
|---|---|---|
| `core/plugin_manager.py` | module ở vị trí cũ | API công khai `from core.plugin_manager import export_skill` của skill người dùng và skill do AI sinh trên các bản cài khác — đổi sẽ làm hỏng skill ngoài repo |
| `skills/*.py` dạng re-export (13 tệp: `agent_orchestrator`, `ai_delegation`, `file_system`, …) | lớp re-export | `plugin_manager` nạp skill bằng cách quét `skills/*.py`; đây là bề mặt đăng ký, code thật ở `application/skills/builtin` |
| `client_agent/` (core, skills) | bản riêng | chạy trên máy trạm KHÔNG có `mateai`; gói "Tải Agent" đóng từ thư mục này |
| `skills/{custom_skills, pc_control_skills, sysadmin_skills}.py` | **giống hệt** `client_agent/skills/` | chờ quyết định sản phẩm (owner-todo): máy chủ có tự điều khiển chính nó không |
| `skills/{file_system, monitoring_skills, excel_records_skill, visual_skills}.py` | **đã lệch** so với `client_agent/skills/` | như trên — hai bên đã sửa khác nhau, gộp cần quyết định trên |
| Hai bảng `employees` (ERP ở `vnmateai.db`, AD ở `hr_kpi.db`) | hai mô hình dữ liệu | ý nghĩa khác nhau; kế hoạch PG để hai schema |
| Ánh xạ role portal → RBAC (`admin→admin`, `manager→it_support`, `viewer→operator`) | hai mô hình role | gộp cần đổi dữ liệu role trong CSDL |

Không còn module chỉ re-export trong `src/mateai`, `core/`, `workers/` (quét AST).

## 4. Bản cũ còn gọi được lúc chạy không?

- `core/` chỉ còn `plugin_manager.py`; test RULE-015 chặn import ngược `mateai.interfaces.http.server` từ module khác.
- Các lớp tương thích chết đã xoá (`SentenceBoundaryStreamer`, `edge_tts_stream_audio`, `get_acoustic_ack_for_query`, `select_relevant_skills`, `SentenceStreamer`, `_sanitise_for_tts`, `clean_text_for_tts`, `text_to_speech_stream`, `_call_llm_router`) — chỉ còn nhắc trong docstring.
- `apps/{api,realtime,worker}` (gói rỗng từ cây song song) đã xoá.
- Endpoint cũ: `/api/v1/audit-logs` gỡ; `DELETE /api/v1/security/audit-logs` → 405.

## 5. Bảo mật đã sửa trong lúc refactor (đều tái hiện trên máy chủ thật trước khi sửa)

Chi tiết ở plan §44–52. Tóm tắt: viewer đọc được `config.json` qua `fs/read`; cờ `confirmed` trong body/tham số tool bỏ qua được duyệt; ai đăng nhập cũng duyệt/chạy skill tuỳ ý qua `confirm-action`; "đồng ý" duyệt nhầm tác vụ của người khác; **"hủy" bị hiểu là đồng ý**; manager duyệt qua HUD; danh sách chờ duyệt, audit và log lộ tham số tác vụ cho viewer; ký ức tác vụ đã chạy trả kết quả của người khác; config ghi không nguyên tử; enrollment token ghi ra log của agent; Telegram có thể nhận tin từ bộ test.

## 6. Chưa làm (cần thông tin/hạ tầng/quyết định — xem `docs/production/owner-todo.md`)

- PostgreSQL (kế hoạch có số liệu: `sqlite-to-postgresql-plan.md`).
- Tách tiến trình + Redis cho state chia sẻ.
- Container (ứng dụng phụ thuộc Windows: COM, micro, điều khiển màn hình).
- CI: workflow đã thêm, chưa xác nhận chạy trên GitHub.
- Firmware ESP32 với token riêng: đã sửa mã, chưa nạp thử trên chip.
- Connector M365/eInvoice/Paperless/OCI: chưa chạy với tài khoản thật.
- Gộp skill máy chủ ↔ agent máy trạm: chờ quyết định sản phẩm.
