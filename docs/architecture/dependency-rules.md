# VN-MateAI — Bộ Quy Tắc Phụ Thuộc & Ranh Giới Kiến Trúc (Dependency & Architecture Rules)

## I. MƯỜI ĐIỀU LUẬT BẮT BUỘC (ARCHITECTURE RULES)

Mọi module và thay đổi code trong quá trình tái cấu trúc VN-MateAI phải tuân thủ nghiêm ngặt 10 quy tắc kiến trúc sau:

### RULE-001: Domain Layer không được import Infrastructure
- **Mô tả**: Tầng nghiệp vụ cốt lõi (`domain/`) hoàn toàn độc lập với công nghệ kỹ thuật.
- **Cấm**: Tuyệt đối không import `httpx`, `aiohttp`, `sqlite3`, `asyncpg`, `redis`, `edge_tts`, `boto3`, hay bất kỳ thư viện SDK bên thứ ba nào trong `domain/`.
- **Được phép**: Chỉ sử dụng kiểu dữ liệu Python thuần túy (`dataclasses`, `typing`, `enum`, `datetime`, `uuid`, `pydantic`).

### RULE-002: Domain Layer không được import Web Framework
- **Mô tả**: Nghiệp vụ hệ thống không gắn liền với cách thức phân phối qua web.
- **Cấm**: Tuyệt đối không import `fastapi`, `starlette`, `uvicorn`, `websockets` trong `domain/` và `application/`.

### RULE-003: Application Layer không gọi Database thô (No Raw SQL)
- **Mô tả**: Các Use Case trong `application/` chỉ tương tác với dữ liệu thông qua các giao diện trừu tượng (`Repository Interfaces`).
- **Cấm**: Không viết câu lệnh SQL thô (`SELECT`, `INSERT`, `UPDATE`), không quản lý database connections hoặc cursors trong `application/`.

### RULE-004: Tầng Giao Diện (Interfaces Layer) không chứa Business Logic
- **Mô tả**: HTTP Router và WebSocket Handler chỉ đóng vai trò bộ chuyển đổi giao thức (Transport Adapters).
- **Trách nhiệm**: Validate request payload, giải mã JWT token (nếu có), ủy quyền thực thi cho Application Use Case, và format dữ liệu trả về theo chuẩn API response.

### RULE-005: WebSocket Handler không gọi trực tiếp AI SDK hoặc Database
- **Mô tả**: WebSocket endpoint không được tự ý kết nối DeepSeek, Groq, hay database để thực thi logic.
- **Luồng chuẩn**: WebSocket Handler phải gọi Use Case thuộc `application/voice/` hoặc `application/agent/`.

### RULE-006: LLM Providers không được truy xuất trực tiếp Enterprise Database
- **Mô tả**: Các adapter LLM (`infrastructure/llm/`) chỉ thực hiện nhiệm vụ suy luận ngôn ngữ, nhận prompt và trả về completion/stream.
- **Quyền hạn**: Không có quyền truy xuất trực tiếp database doanh nghiệp để lấy dữ liệu ngoài context được cung cấp.

### RULE-007: Mọi công cụ đặc quyền (Privileged Tools) phải kiểm tra quyền (RBAC & Clearance)
- **Mô tả**: Tất cả các tool can thiệp vào hệ thống máy chủ, mạng, cơ sở dữ liệu hoặc thông tin nhân sự/tài chính phải đi qua bộ kiểm tra quyền hạn (`SecurityGuard` / `ZeroTrustValidator`).

### RULE-008: Mọi lời gọi dịch vụ ngoài (External Calls) phải có Timeout & Circuit Breaker
- **Mô tả**: Tuyệt đối không gọi HTTP client ngoài hoặc connector bên thứ ba mà không thiết lập timeout rõ ràng (mặc định 5s - 10s).
- **Bảo vệ**: Phải có cơ chế ngắt mạch (Circuit Breaker) để tránh tình trạng dịch vụ ngoài bị treo làm nghẽn toàn bộ event loop của backend.

### RULE-009: Mọi phiên thoại Realtime phải có vòng đời Cancellation (Barge-In Lifecycle)
- **Mô tả**: Mỗi lượt tương tác thoại gắn liền với một `session_id`, `turn_id` và một `asyncio.Event` / Cancellation Token.
- **Ngắt lời**: Khi người dùng ngắt lời (Barge-In), toàn bộ tác vụ LLM stream, TTS synthesis worker, và audio queue của turn cũ phải bị hủy lập tức trong vòng dưới 10ms.

### RULE-010: Mọi tiến trình Production phải cung cấp Health Endpoints tiêu chuẩn
- **Mô tả**: Các tiến trình backend phải cung cấp đầy đủ 3 endpoint kiểm tra sức khỏe độc lập:
  - `/livez`: Liveness probe (kiểm tra tiến trình còn sống).
  - `/readyz`: Readiness probe (kiểm tra khả năng nhận traffic, kết nối DB/Redis sẵn sàng).
  - `/startupz`: Startup probe (kiểm tra tiến trình đã nạp xong model, config và cache khởi đầu).

---

## II. MA TRẬN PHỤ THUỘC GIỮA CÁC TẦNG (IMPORT BOUNDARY MATRIX)

| Tầng gọi tới ➔ Gọi vào Tầng | Domain | Application | Infrastructure | Interfaces | Frameworks (FastAPI) | External SDKs |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Domain** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Application** | ✅ | ✅ | ❌ (Chỉ qua Ports) | ❌ | ❌ | ❌ |
| **Infrastructure** | ✅ | ✅ (Thực thi Ports) | ✅ | ❌ | ❌ | ✅ |
| **Interfaces** | ✅ | ✅ | ❌ (DI container nạp) | ✅ | ✅ | ❌ |

---

## III. CƠ CHẾ KIỂM TRA TỰ ĐỘNG — TRẠNG THÁI THẬT (Phase 0, 2026-10-01)

`tests/architecture/test_architecture_boundaries.py` hiện chỉ kiểm tra **3/10 quy tắc** (RULE-001/002, RULE-003, RULE-004) và chỉ quét `src/mateai/` — cây code **không chạy** trong runtime. Thư mục `src/mateai/interfaces/` rỗng nên RULE-004 luôn đạt. Kết quả "đạt 100 %" vì thế không nói gì về `core/`.

**Phase 1 đã làm:** `tests/architecture/test_core_rules.py` quét code đang chạy (`core/`, `skills/`, `workers/`, `main.py`) theo RULE-011…015 với baseline `tests/architecture/core_rules_baseline.json` — vi phạm mới làm test fail, số vi phạm giảm thì phải hạ baseline (bánh cóc). Test cũ cho `src/mateai/` nay được pytest chạy.

Yêu cầu ban đầu (giữ để đối chiếu):
1. Quét **code thật đang chạy**: `core/` hôm nay, và các tầng đích khi code được di chuyển sang.
2. Cho phép ghi nhận vi phạm hiện có vào một danh sách ngoại lệ có hạn chót (baseline), và **fail khi xuất hiện vi phạm mới** — để cấm quay lui mà không chặn công việc.
3. Kiểm tra thêm các quy tắc chống trùng lặp ở mục IV.

## IV. QUY TẮC CHỐNG TRÙNG LẶP (bổ sung từ audit trùng lặp)

| Mã | Quy tắc | Kiểm tra tự động |
|---|---|---|
| RULE-011 | Chỉ `infrastructure/llm` (hôm nay: `core/llm_provider.py`) được tạo client `OpenAI`/`AsyncOpenAI` hoặc gọi `chat.completions.create` | grep AST `OpenAI(`, `AsyncOpenAI(`, `.chat.completions.create` |
| RULE-012 | Chỉ `infrastructure/tts` (hôm nay: `core/audio/tts_stream_engine.py`) được gọi `edge_tts.Communicate`, `gTTS`, `/audio/speech` | grep AST |
| RULE-013 | Chỉ loader cấu hình được mở `config.json` | grep chuỗi `"config.json"` kèm `open`/`read_text`/`json.load` |
| RULE-014 | Chỉ tầng persistence được gọi `sqlite3.connect` | grep AST |
| RULE-015 | Không module nào trong lõi import `core.server` (hoặc `interfaces/*`) | đồ thị import |
| RULE-016 | Mỗi tên tool chỉ được đăng ký ở một registry | so khớp tên từ `plugin_manager` và `plugin_registry` lúc khởi động |

## V. TRẠNG THÁI TUÂN THỦ CỦA CODE ĐANG CHẠY (`core/`) — cập nhật 2026-10-02

Số vi phạm RULE-011…015 do `tests/architecture/test_core_rules.py` đo trên mã thật; baseline ở `tests/architecture/core_rules_baseline.json` chỉ được giảm (vi phạm mới = test fail).

| Quy tắc | Trạng thái | Bằng chứng |
|---|---|---|
| RULE-001/002 | Không áp dụng được — `core/` chưa tách tầng (Phase 4 chưa làm) | — |
| RULE-005 | Một phần | mọi kênh thoại dùng chung `core.voice_turn.process_voice_turn`; handler WS vẫn nằm trong `server.py` |
| RULE-007 | Đạt (một cổng) | mọi tool qua `agent_voice_loop.run_tool_with_policy` → `security_guard`; một HITL (`zero_trust`). Còn hai mô hình role (portal ↔ RBAC, ánh xạ cố định) |
| RULE-008 | Một phần | `plugin_registry`: timeout + circuit breaker; client httpx riêng còn lại đã phân loại có lý do (plan §19) |
| RULE-009 | Chỉ kênh portal/HUD | huỷ lượt khi có lệnh mới (barge-in) |
| RULE-010 | Đạt | `/livez`, `/startupz`, `/readyz` (`test_health_probes`) |
| RULE-011 | 7 chỗ, đều có lý do | `llm_engine` (3: tạo client cho chính provider), `server` (3: nút "thử kết nối" dùng URL/khoá người dùng nhập), `ai_delegation` (1: client async cho provider chuyên gia). `audio_processor` (Whisper) được miễn |
| RULE-012 | 1 | `skills/ninerouter_skills.py` |
| RULE-013 | **0** | `config_loader` là cổng duy nhất (plan §18) |
| RULE-014 | **0** | `core.database.open_sqlite` là đường mở duy nhất (plan §23) |
| RULE-015 | **0** | trạng thái kết nối + phát sóng ở `core/realtime_hub.py`, helper xuất file ở `core/file_export.py` (plan §27) |
| RULE-016 | Đạt | danh mục tool duy nhất là `plugin_manager`; `plugin_registry` chỉ là chính sách thực thi (plan §10); log khởi động không còn cảnh báo đăng ký trùng |
