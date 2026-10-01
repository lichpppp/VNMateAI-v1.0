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

## III. CƠ CHẾ KIỂM TRA TỰ ĐỘNG BẰNG ARCHITECTURE TESTS

Trong các phase tiếp theo, một bộ kiểm tra tĩnh (Architecture Tests) sẽ được đưa vào thư mục `tests/architecture/` để quét AST mã nguồn:
1. Quét toàn bộ lệnh `import` trong `src/mateai/domain/`: Báo lỗi nếu phát hiện bất kỳ package ngoài Python standard library (ngoại trừ Pydantic).
2. Quét các file trong `src/mateai/application/`: Báo lỗi nếu phát hiện `fastapi`, `starlette` hoặc module `sqlite3`/`asyncpg`.
3. Quét các file trong `src/mateai/interfaces/http/`: Báo lỗi nếu phát hiện câu lệnh SQL thô (`SELECT`, `INSERT`).
