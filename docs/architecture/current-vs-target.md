# VN-MateAI — Báo Cáo Hiện Trạng & So Sánh Kiến Trúc Mục Tiêu (Current vs Target)

## I. TỔNG QUAN AUDIT BASELINE (SỐ LIỆU ĐO ĐẠC THỰC TẾ 100%)

Toàn bộ số liệu dưới đây được trích xuất trực tiếp bằng công cụ phân tích tĩnh trên repository VN-MateAI (không ước lượng, không giả định):

### 1. Phân bổ mã nguồn và tệp tin (Kích thước & LOC)
- **Tổng số tệp tin dự án**: 1,776 tệp (loại trừ `.git`, `node_modules`, `.venv`).
- **Tổng số dòng mã (Total LOC)**: 2,251,654 dòng (bao gồm models, JSON schema, test fixtures, audio storage).
- **Phân bổ theo ngôn ngữ chính**:
  - **Python (`.py`)**: 195 tệp | **67,723 dòng code** (chiếm lõi backend)
  - **JavaScript (`.js`)**: 68 tệp | **18,398 dòng code** (frontend web client & scripts)
  - **JSON (`.json`)**: 41 tệp | **15,584 dòng code** (config, OpenAPI schemas, skill manifests)
  - **HTML (`.html`)**: 13 tệp | **9,358 dòng code** (UI Portal, Standby HUD, Admin static templates)
  - **TypeScript React (`.tsx`)**: 36 tệp | **6,549 dòng code** (Next.js Enterprise Admin dashboard)
  - **Markdown (`.md`)**: 42 tệp | **3,229 dòng code** (Tài liệu kỹ thuật, hướng dẫn vận hành)
  - **TypeScript (`.ts`)**: 12 tệp | **1,150 dòng code** (Admin client libs, types)
  - **CSS (`.css`)**: 5 tệp | **313 dòng code** (Vanilla styling, HUD visualizer)
  - **Binary Audio Cache**: 645 tệp MP3 lưu trữ trong `storage/audio_cache/`.

### 2. Trọng tâm Backend `core/`
- Thư mục `core/` gồm **87 tệp Python** với **42,802 dòng code** (chiếm 63.2% toàn bộ logic backend).
- **Danh sách 6 "God Files" cần giải phóng trách nhiệm**:
  1. `core/server.py`: **8,898 LOC** — Chứa 150 HTTP routes, background telemetry, WebSocket listeners, race conditions.
  2. `core/llm_engine.py`: **2,261 LOC** — Gộp Tri-Brain orchestration, streaming fallback, direct provider adapters.
  3. `core/database.py`: **1,641 LOC** — Schema SQLite, direct raw SQL queries cho tất cả các bảng.
  4. `core/xiaozhi_gateway.py`: **1,122 LOC** — ESP32 XiaoZhi IoT protocol, Opus decoding, WebSocket streaming.
  5. `core/voice_controller.py`: **1,045 LOC** — PyAudio native microphone loop, wake-word, local TTS playback.
  6. `core/telegram_gateway.py`: **1,001 LOC** — Telegram long-polling, command routing, permission checking.

### 3. Giao diện mạng (Network Interfaces)
- **169 HTTP Endpoints**:
  - `150 endpoints` gắn tiền tố `/api/v1/*` (bao gồm Auth, Voice, System Metrics, Cloud Connectors, Files, Agent Control).
  - `5 endpoints` gắn tiền tố `/api/erp/*` (Quản lý Nhân sự, KPI, Máy trạm).
  - `14 endpoints` phục vụ Web UI, Swagger/ReDoc, Health checks, Static Mounts.
- **13 WebSocket Endpoints**:
  - `/ws/v1/voice-stream`: WebSocket Voice Realtime chính thức (Binary audio frames, Sentence-level streaming, Barge-in interrupt).
  - `/ws/hud`: Standby HUD Visualizer & binary TTS playback.
  - `/ws/audio-stream`, `/ws/audio-stream/{device_id}`: Kênh audio relay giữa server và client.
  - `/api/v1/xiaozhi/ws`, `/api/v1/xiaozhi/ws/{device_id}`: Kênh giao tiếp thiết bị IoT ESP32 XiaoZhi.
  - `/ws/portal-ui`: WebSocket đồng bộ trạng thái Web Portal.
  - `/ws/client`: WebSocket giao tiếp Client Agent (máy trạm nhân viên).
  - `/ws/topology`: Live stream sơ đồ mạng và trạng thái node.
  - `/ws/voice`: Endpoint Voice legacy.

### 4. Cơ sở dữ liệu hiện hành (Persistence)
- Hệ cơ sở dữ liệu: **SQLite WAL mode**.
- Database chính `vnmateai.db`: **13 bảng** với **2,464 bản ghi** (Bảng `audit_logs`: 2459 bản ghi, `users`: 3 tài khoản, `tasks`: 2 bản ghi, các bảng ERP/department: 0).
- Database phụ `hr_kpi.db`: **2 bảng** (`employees`: 0, `computers`: 0).

---

## II. MA TRẬN SO SÁNH HIỆN TRẠNG (CURRENT) VS MỤC TIÊU (TARGET)

| Thành phần | Hiện trạng (Current Architecture) | Kiến trúc mục tiêu (Target Modular Monolith) | Rủi ro / Điểm nghẽn cần xử lý |
| :--- | :--- | :--- | :--- |
| **Cấu trúc thư mục** | Monolithic `core/` chứa lẫn lộn domain, delivery, infrastructure, database. | Clean Architecture: `src/mateai/domain`, `application`, `infrastructure`, `interfaces`. | Import vòng (circular import), spaghetti coupling. |
| **HTTP Routing** | `core/server.py` dài 8,898 dòng, khai báo toàn bộ 169 endpoints. | Tách thành các router theo Bounded Context trong `interfaces/http/` (auth, admin, voice, erp...). | Đứt gãy routing table nếu không kiểm tra decorator và path prefix. |
| **WebSocket** | 13 endpoints xử lý trực tiếp database, LLM, TTS, logging ngay trong handler. | `interfaces/websocket/` mỏng, chỉ nhận/gửi message và ủy quyền cho Application Use Cases. | Race condition, latency đơ luồng asyncio nếu dính sync I/O. |
| **Voice Pipeline** | Có 2 luồng: `/ws/v1/voice-stream` (chuẩn) và `/ws/voice` + native `voice_controller.py`. | **1 Canonical Pipeline duy nhất**: STT ➔ Fast Router ➔ LLM Stream ➔ SentenceBuffer ➔ StreamingTTSWorker ➔ Binary WS. | Trùng giọng nói, giật audio khi nhiều worker cùng synthesize một lúc. |
| **LLM Abstraction** | `core/llm_engine.py` (2,261 LOC) ôm cả prompt engineering, fallback logic, trực tiếp gọi DeepSeek/Groq. | Interface `LLMProvider` trong `infrastructure/llm/` với các Adapter riêng biệt (DeepSeek, Groq, OpenAI, 9Router). | Quản lý token, streaming buffer và circuit breaker khi timeout. |
| **Fast Path Router** | `core/fast_command_router.py` chạy regex song song với heuristic router trong `server.py`. | Tích hợp vào `application/commands/fast_path_router.py` với RBAC check và latency < 0.05ms. | Bỏ lọt lệnh hoặc bypass quyền truy cập nhạy cảm. |
| **Cơ sở dữ liệu** | Gọi raw SQL `sqlite3.connect()` rải rác trong `database.py` và `server.py`. | Repository Pattern (`UserRepository`, `AuditRepository`, ...) với Adapter SQLite (dev/edge) & PostgreSQL (production). | Schema divergence, thiếu transaction isolation, lock contention. |
| **Quản lý State** | In-memory Python dictionaries (`_sessions`, `_hud_voice_tasks`, memory cache). | Externalized State qua Redis (`infrastructure/cache/redis_adapter.py`) cho distributed session & lock. | Không thể scale ngang nhiều replica nếu state vẫn nằm ở RAM process. |
| **Worker / Queue** | Chạy background tasks bằng `asyncio.create_task()` không kiểm soát tải. | Background Worker Architecture với Queue bounded (`asyncio.Queue` / Redis Streams) cho long tasks. | OOM do background task tích tụ khi tải cao hoặc worker bị treo. |
| **Audio Transport** | Có lẫn Base64 qua JSON và Raw Binary frames trong các endpoint khác nhau. | 100% Binary frames cho audio payload; JSON chỉ dùng cho metadata/control events. | Client parsing overhead, tăng 33% băng thông vô nghĩa nếu dùng Base64. |

---

## III. BẢN ĐỒ ÁNH XẠ THƯ MỤC CHI TIẾT (CURRENT ➔ TARGET MAPPING)

```text
HIỆN TRẠNG (CURRENT)                              MỤC TIÊU (TARGET)
------------------------------------------------  --------------------------------------------------
core/schemas/                                  ➔  src/mateai/domain/ (VoiceSession, Conversation, Task, Device)
core/security/ (RBAC, security_guard)          ➔  src/mateai/domain/identity/ + infrastructure/security/
core/dynamic_skill_router.py, skills/          ➔  src/mateai/domain/skills/ + application/skills/
core/agent_voice_loop.py, realtime_voice_ws.py ➔  src/mateai/application/voice/ + interfaces/websocket/
core/fast_command_router.py                    ➔  src/mateai/application/commands/
core/autonomous_sentinel.py, agents/           ➔  src/mateai/application/agent/
core/llm_engine.py, llm_provider.py            ➔  src/mateai/infrastructure/llm/
core/audio/ (tts_stream_engine, audio_processor)➔ src/mateai/infrastructure/tts/ & stt/
core/database.py, db_manager.py, connection_pool➔ src/mateai/infrastructure/database/ (Repositories)
core/connectors/ (aws, oci, telegram, erp, etc)➔  src/mateai/infrastructure/connectors/
core/server.py (169 routes, 13 WS)             ➔  src/mateai/interfaces/http/ & interfaces/websocket/
client_agent/                                  ➔  client_agent/ (Boundary riêng biệt, không import backend core)
admin/ (Next.js Dashboard)                     ➔  admin/ (Giao tiếp qua HTTP REST /api/v1/ & WebSocket)
web/ (Standby HUD & Portal)                    ➔  web/ (Sử dụng Web Audio API binary player)
```

---

## IV. CÁC VI PHẠM KIẾN TRÚC CẦN KHẮC PHỤC (ARCHITECTURAL VIOLATIONS)

1. **God Module `core/server.py`**:
   - Vừa đảm nhận khởi tạo FastAPI, cấu hình CORS, khởi tạo kết nối DB, định nghĩa 169 HTTP routes, quản lý 13 WebSocket connections, vừa xử lý logic phát giọng nói, telemetry loop và dọn dẹp bộ nhớ.
   - **Khắc phục**: Chuyển thành `create_app()` factory gọn nhẹ trong `apps/api/`, mount các router tách biệt từ `interfaces/http/`.

2. **Truy cập Database trực tiếp (No Persistence Abstraction)**:
   - Các hàm API trong `server.py` tự tạo câu lệnh SQL `SELECT * FROM audit_logs WHERE...` và execute trực tiếp trên SQLite cursor.
   - **Khắc phục**: Bắt buộc mọi truy xuất dữ liệu phải thông qua Repository interfaces.

3. **Domain Layer phụ thuộc Transport & Third-party SDKs**:
   - `core/llm_engine.py` và `core/agent_voice_loop.py` import trực tiếp `fastapi.WebSocket`, `httpx`, `edge_tts`.
   - **Khắc phục**: Tách biệt Domain entity/value objects thuần túy Python, không import bất kỳ framework web hay SDK mạng nào.
