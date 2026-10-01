# VN-MateAI — Hiện trạng và mục tiêu (Current vs Target)

> Phase 0 · audit chỉ đọc · 2026-10-01 · commit `4f6464a` (+ một sửa lỗi chưa commit ở `core/config_loader.py`)
> Bản này **thay thế** số liệu của bản trước. Bản trước đếm 1.776 file / 2,25 triệu dòng (lẫn `node_modules`, `.venv`, file âm thanh), 169 HTTP route, 13 WebSocket — các con số đó không khớp với code.
> Chi tiết trùng lặp: `duplication-matrix.md` · ứng viên xóa: `legacy-candidates.md` · bản chính: `canonical-components.md`.

## 1. Cách đo

| Số liệu | Cách đo |
|---|---|
| File, dòng | `git ls-files` (chỉ file được theo dõi) + `wc -l` |
| Phụ thuộc | script AST: import top-level, import trong hàm, `importlib`, chuỗi tên module first-party |
| Route | khởi tạo `core.server.app` rồi đọc `app.routes` |
| Test | chạy **từng file test riêng** (pytest cho file có `def test_`, `python` cho file dạng script) |
| Tài nguyên | `Get-Process` trên tiến trình server đang chạy, 10 giây khi rảnh |

## 2. Quy mô thực tế

| Hạng mục | Giá trị |
|---|---|
| File được git theo dõi | 382 |
| Python | 269 file · 71.359 dòng |
| JavaScript (`web/`) | 4 file · 17.413 dòng (`web/app.js` 14.365 dòng) |
| TSX / TS (`admin/`) | 36 + 7 file · 6.531 + 824 dòng |
| HTML | 3 file · 9.347 dòng (`web/index.html` 7.653 dòng) |
| C++/Arduino (`esp32_firmware/`) | 3 `.cpp` + 5 `.h` + 1 `.ino` |

| Thư mục | File | Dòng Python |
|---|---|---|
| `core/` | 87 | 42.791 |
| `tests/` | 59 | 10.524 |
| `skills/` | 35 | 4.232 |
| `client_agent/` | 14 | 3.966 |
| `client_template/` | 16 | 3.947 |
| `src/mateai/` | 69 | 3.679 |
| `workers/` | 5 | 1.237 |

File lớn nhất: `core/server.py` 8.898 · `core/llm_engine.py` 2.261 · `core/database.py` 1.641 · `core/xiaozhi_gateway.py` 1.122 · `core/voice_controller.py` 1.045 · `core/telegram_gateway.py` 1.001.

## 3. Tiến trình và giao diện mạng lúc chạy

- **Một tiến trình Python** (`main.py`): khay hệ thống `pystray` ở main thread, Uvicorn chạy trong thread daemon với **hai listener trên cùng một event loop**: HTTPS `:443` (TLS tự ký) và HTTP/WS `:8000` (cho IoT). Cùng tiến trình còn có wake-word engine, voice controller (mic máy chủ), các vòng lặp nền.
- Tiến trình con: `client_agent/agent.py` (worker cục bộ `MASTER_LOCAL_WORKER`, bật từ portal), `core/voice_widget.py` (widget nổi), `overlay_ui.py`/`popup_ui.py`.
- Dịch vụ ngoài: 9Router (`localhost:20128`) cho LLM và TTS dự phòng, Edge-TTS, Groq (STT dự phòng), Telegram, các connector AWS/OCI/M365/Paperless/eInvoice.
- **HTTP: 165 cặp method+path, 0 trùng** · 139 dưới `/api/v1`, 5 dưới `/api/erp`, còn lại là trang (`/`, `/hud`, `/roi`, `/admin/*`) và `/health`.
- **WebSocket: 10 decorator, 8 chức năng** (xem `duplication-matrix.md` §12).

Tài nguyên khi rảnh (đo thật, Windows 10, mô hình Whisper `tiny` đã nạp): working set **405 MB**, private **748 MB**, **65 thread**, CPU **6,4 %** một core. Bản cũ ghi "~90 MB, 0 % CPU" — không khớp cấu hình đang chạy.

## 4. Bounded context đang tồn tại (trong `core/`)

| Context | Module chính | Ghi chú |
|---|---|---|
| Voice | `realtime_voice_ws`, `agent_voice_loop`, `audio/*`, `audio_processor`, `audio_cache`, `voice_controller`, `voice_session`, `wake_word_engine`, `voice_widget`, phần HUD trong `server.py` | **5 pipeline song song** (P1–P5) |
| Device (ESP32) | `xiaozhi_gateway` | trộn transport thiết bị với logic hội thoại |
| LLM / Agent | `llm_engine`, `llm_provider`, `connection_pool`, `agents/agent_orchestrator`, `orchestrator`, `meta_architect`, `analytics_engine` | 3 đường gọi LLM trong `llm_engine` + 5 module tự tạo client |
| Skills / Tools | `plugin_manager`, `plugin_registry`, `dynamic_skill_router`, `fast_command_router`, `core/skills/*`, `skills/*`, `connectors/tool_bridge`, `plugins/*` | 2 registry |
| Identity / Security | `auth_manager`, `security_guard`, `safety_guard`, `zero_trust`, `security/hitl_manager`, `security_tls` | 2 HITL, 3 audit sink, 2 mô hình vai trò |
| Data | `database`, `db_manager`, `domain_sync`, `department_engine`, `ephemeral_cache`, `cognitive_memory`, `rag_engine`, `knowledge/*` | bảng `tasks` hai chủ, user ở hai nơi |
| Conversation | `memory_manager`, `history_pruner`, `voice_session`, `state_manager` | 3 kho lịch sử |
| Connectors | `connectors/*`, `telegram_gateway`, `email_gateway`, `webhook_gateway` | 17 `httpx` client tự tạo |
| Client agent | `client_agent/`, `client_template/`, `workers/*`, `worknodes/*` | 2 bản fork của cùng một runtime |
| Operations | `health_monitor`, `autonomous_sentinel`, `background_workers`, `task_manager`, `download_queue` | đo CPU/RAM ở 5 nơi |

## 5. Dữ liệu và trạng thái

| Dữ liệu | Nơi lưu | Chủ sở hữu (code) | Vấn đề |
|---|---|---|---|
| ERP (phòng ban, nhân sự, thiết bị, hồ sơ, tài chính, chấm công), audit | `vnmateai.db` (SQLite WAL) | `ERPDatabase` | — |
| `tasks` | `vnmateai.db` | **`db_manager` và `ERPDatabase`** | schema phụ thuộc lớp nào tạo bảng trước; trên máy này `client_id`, `task_message` là `NOT NULL` |
| User | `users.json` **và** bảng `users` | `auth_manager` (đăng nhập) / `db_manager` (copy một chiều) | hai nguồn sự thật |
| HR/AD | `hr_kpi.db` (`employees`, `computers`) | `domain_sync` | bảng `employees` thứ hai, khác schema |
| Vector memory | `storage/chroma_db`, `storage/vector_db` | `cognitive_memory`, `rag_engine` | — |
| Cache âm thanh | `storage/audio_cache/*.mp3` | `audio_cache` | không có chính sách dọn |
| Cấu hình | `config.json` | `config_loader` + 14 chỗ đọc trực tiếp | khóa trùng 2–6 lần |
| Data source tùy chỉnh | `config/data_sources.json` | `connectors/custom_registry` | — |
| Secret | `config.json`, biến môi trường, `certs/` | nhiều module | không có secret thật nào bị commit (đã quét) |

Trạng thái trong RAM tiến trình: **54 singleton cấp module** (`memory_manager`, `voice_sessions`, `state_manager`, hai `hitl_manager`, `ephemeral_cache`, `task_manager`, …) và 5 tập hợp WebSocket/task toàn cục trong `server.py` (`active_hud_websockets`, `active_portal_websockets`, `active_topology_websockets`, `active_audio_nodes`, `_hud_voice_tasks`). Hệ quả: **không thể chạy hơn một instance** mà không mất session, HITL đang chờ, lịch sử hội thoại.

## 6. Phụ thuộc

- Không có vòng import ở top-level (đã được gỡ bằng import trong hàm).
- Tính cả import trong hàm: **một khối liên thông mạnh gồm 37 module** (gần như toàn bộ lõi) + một vòng nhỏ `sentence_buffer ↔ sentence_streamer`.
- **10 module lõi import ngược `core.server` (không tính `main.py`)** (lớp lõi phụ thuộc lớp giao tiếp).
- Module được import nhiều nhất: `plugin_manager` (55), `config_loader` (23), `database` (15), `server` (13), `zero_trust` (11), `telegram_gateway` (11).

## 7. Baseline test (chạy thật ngày 2026-10-01)

Repo **không khai báo** `pytest`/`pytest-asyncio` (đã cài tạm vào `.venv` để chạy). Không chạy được bằng một lệnh `pytest tests`: các file dạng script gọi `sys.exit()` khi import làm hỏng collection.

**47 file Python: 38 pass, 9 fail.** (11 file `.mjs` chưa chạy trong Phase 0.)

| File fail | Nguyên nhân | Loại |
|---|---|---|
| `test_hud_barge_in_and_tts_flow.py` (4/38 check) | đòi hàng đợi task TTS trong đường HUD — code HUD đã đổi sang `TTSStreamEngine` | test lệch code |
| `test_phase67_voice_latency.py` (10/41) | đòi hàm bọc TTS có timeout cho HUD — như trên | test lệch code |
| `test_phase85_may_tram_that_muc.py` (2/64) | kiểm tra cấu trúc HTML `web/index.html` | test lệch UI |
| `test_phase1_realtime_ws.py::test_metric_trace_tracker` | `sleep(0.02)` trên Windows đo được 15 ms < ngưỡng 20 ms | test phụ thuộc đồng hồ |
| `unit/test_repositories.py::test_audit_repository` | đòi DB của máy dev có ≥ 2.459 dòng audit (máy mới có 26) | test phụ thuộc dữ liệu máy |
| `test_phase62_data_source.py` (1 check) | đòi quyền file `0600` — Windows trả `0666` | phụ thuộc OS |
| `test_phase73_no_fake_data.py`, `test_phase75_roi_real_data.py` | `WinError 32`: file SQLite tạm còn bị giữ khi dọn → kết nối chưa được đóng | rò kết nối (lộ ra trên Windows) |
| `test_phase68_no_hardcoded_models.py` (2/68) | gọi model thật qua 9Router, một model trả HTTP 400 | phụ thuộc mạng |

Một số test ghi vào `config.json`, `vnmateai.db` và kho data source **thật** (có khôi phục lại). Phase 0 đã sao lưu trước khi chạy; dữ liệu sau khi chạy giống bản sao lưu.

Commit trước ghi "100 % pass": không đúng (`tests/unit/test_repositories.py` fail ngay trên máy mới). Ngoài ra **9 file `tests/unit/*` chỉ kiểm tra `src/mateai/`** — cây code không chạy trong runtime — nên không bảo vệ `core/`.

## 8. Vi phạm kiến trúc (đã kiểm chứng)

| # | Vi phạm | Bằng chứng |
|---|---|---|
| V1 | Một tính năng, nhiều implementation | 5 voice pipeline, 2 TTS engine, 3 hàm làm sạch text, 3 vòng fallback LLM, 2 HITL, 3 audit sink, 2 registry, 3 kho lịch sử, 2 bản client agent |
| V2 | Kiến trúc đích dựng song song nhưng không nối | `src/mateai/` 0 importer production |
| V3 | Lõi phụ thuộc lớp giao tiếp | 10 module lõi import `core.server`; khối SCC 37 module |
| V4 | God module | `server.py` 8.898 dòng: 165 route + 10 WS + pipeline voice HUD + race TTS + vòng telemetry + quản lý worker |
| V5 | Bỏ qua lớp trừu tượng | 5 module tự tạo client OpenAI; 14 chỗ tự đọc `config.json`; 3 module tự mở `sqlite3` |
| V6 | Dữ liệu hai chủ | bảng `tasks`; user (`users.json` + bảng `users`); `employees` ở hai DB |
| V7 | Trạng thái chỉ trong RAM | 54 singleton + 5 tập hợp toàn cục trong `server.py` |
| V8 | Phụ thuộc thiếu khai báo | `gTTS` không có trong `requirements.txt` → nhánh dự phòng cuối của TTS luôn lỗi im lặng; `pytest` không được khai báo |
| V9 | Kiểm tra kiến trúc giả xanh | `tests/architecture/` chỉ quét `src/mateai/` và chỉ 3/10 quy tắc; không quét `core/` |
| V10 | Test không cô lập | ghi vào `config.json`/DB thật; phụ thuộc dữ liệu máy dev, mạng, đồng hồ |

## 9. So sánh với mục tiêu

| Năng lực | Hiện trạng | Mục tiêu | Khoảng cách |
|---|---|---|---|
| Voice pipeline | 5 pipeline | 1 (P1), các kênh khác chỉ là transport | lớn |
| Giao thức voice | `/ws/v1/voice-stream`, `/ws/hud`, XiaoZhi, REST | 1 giao thức voice + transport thiết bị riêng | trung bình |
| LLM | provider abstraction có, nhưng 3 vòng fallback + 5 client riêng | mọi lời gọi qua `LLMProvider` | trung bình |
| TTS | 2 engine + race riêng | `TTSStreamEngine` duy nhất | trung bình |
| Tool registry | 2 | 1 | trung bình |
| Security policy | 2 HITL, 3 audit, 2 role model | 1 đường policy | lớn, rủi ro cao |
| Persistence | SQLite, DAO dùng chung bảng | repository + PostgreSQL làm system of record | lớn |
| State | RAM tiến trình | Redis cho state chia sẻ | lớn |
| Cấu hình | khóa trùng + đọc trực tiếp | 1 loader, secret tách khỏi config | nhỏ |
| Health | `/health` | `/livez`, `/readyz`, `/startupz` | nhỏ |
| Observability | log văn bản, trace riêng cho P1 | log có cấu trúc + metric + trace | lớn |
| Triển khai | 1 tiến trình Windows có khay hệ thống | tiến trình api/realtime/worker tách được, container | lớn |
| Test | 38/47 pass, không có runner chung | 1 lệnh chạy, cô lập, có CI | trung bình |
