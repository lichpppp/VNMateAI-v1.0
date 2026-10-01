# VN-MateAI — Kế Hoạch Chuyển Đổi Kiến Trúc Doanh Nghiệp (Production Refactor Plan)

## I. NGUYÊN TẮC THI CÔNG BẤT DI BẤT DỊCH (NON-DESTRUCTIVE REFACTOR)

1. **Tuyệt đối không xóa code cũ mù quáng**: Trong suốt quá trình chuyển đổi, code hiện hành tiếp tục phục vụ như một baseline hoạt động. Chỉ loại bỏ code cũ khi implementation mới đã được xác minh toàn diện bằng test suite.
2. **Áp dụng mô hình Strangler Fig Pattern**: Tạo kiến trúc mới song song (`src/mateai/`), điều hướng từng phần lưu lượng (traffic) qua Adapter, xác minh tính toàn vẹn rồi mới ngắt kết nối implementation cũ.
3. **Hiệu năng Realtime không được thoái lui**: Sau mỗi bước chuyển đổi, các chỉ số cốt lõi (Fast Command < 0.05ms, Barge-in < 0.01ms, TTFA < 600ms, RAM idle < 100MB) phải được benchmark lại để đảm bảo không bị suy giảm.

---

## II. LỘ TRÌNH 18 GIAI ĐOẠN CHI TIẾT (PHASE 0 ➔ PHASE 17)

### Phase 0: Thẩm định & Lập bản đồ kiến trúc (Current Audit & Boundary Mapping)
- **Trạng thái**: **ĐANG HOÀN TẤT**
- **Nội dung**:
  - Đo đạc chính xác 100% LOC, số tệp tin, số bảng database, số route HTTP (169) và WebSocket (13).
  - Xác định các "God Files" (`server.py`, `llm_engine.py`, `database.py`, `xiaozhi_gateway.py`).
  - Xuất bản 4 tài liệu cốt lõi: `current-vs-target.md`, `target-architecture.md`, `dependency-rules.md`, `production-refactor-plan.md`.
  - **Quy tắc**: Không xóa file, không di chuyển hàng loạt.

### Phase 1: Bản đồ phụ thuộc chi tiết & Test Baseline
- **Nội dung**:
  - Chạy toàn bộ test suite hiện có để lưu trữ baseline kết quả kiểm thử.
  - Đo đạc latency thực tế của Fast Command, LLM streaming và TTS synthesis.
  - Khởi tạo thư mục kiểm thử kiến trúc `tests/architecture/`.

### Phase 2: Khởi tạo khung cấu trúc đích (Target Skeleton Setup)
- **Nội dung**:
  - Tạo cấu trúc thư mục `src/mateai/` chuẩn:
    - `src/mateai/domain/`
    - `src/mateai/application/`
    - `src/mateai/infrastructure/`
    - `src/mateai/interfaces/`
    - `src/mateai/config/`
  - Thêm các tệp `__init__.py` và cấu hình package resolution.

### Phase 3: Bounded Context 1 — Domain Entities & Value Objects
- **Nội dung**:
  - Di chuyển và chuẩn hóa các Entity thuần túy: `VoiceSession`, `AudioFrame`, `ConversationContext`, `AgentTask`, `UserIdentity`.
  - Đảm bảo 100% tệp tin trong `domain/` không import framework ngoài.

### Phase 4: Bounded Context 2 — Voice Pipeline Refactor
- **Nội dung**:
  - Trích xuất `ProcessVoiceTurnUseCase`, `SentenceBuffer`, `BargeInController` vào `application/voice/`.
  - Trích xuất `StreamingTTSWorker` và `WhisperSTTAdapter` vào `infrastructure/tts/` và `infrastructure/stt/`.
  - Tạo `interfaces/websocket/realtime_voice_endpoint.py`.
  - **Kiểm thử**: Đảm bảo pipeline voice binary đạt độ trễ TTFA < 600ms và không bị lặp giọng nói.

### Phase 5: Bounded Context 3 — LLM Provider Abstraction
- **Nội dung**:
  - Xây dựng giao diện trừu tượng `LLMProvider` với các phương thức `generate()` và `stream()`.
  - Tách các adapter cụ thể: `DeepSeekAdapter`, `GroqAdapter`, `OpenAIAdapter`, `NineRouterAdapter`.
  - Tách logic prompt template và context window khỏi engine chính.

### Phase 6: Bounded Context 4 — Fast Command Router
- **Nội dung**:
  - Đưa `FastCommandRouter` vào `application/commands/fast_command_router.py`.
  - Chuẩn hóa các câu lệnh điều khiển hệ thống nội bộ (mở ứng dụng, chụp màn hình, chỉnh âm lượng) với cơ chế phân quyền RBAC.
  - Đảm bảo thời gian phản hồi đạt mức sub-millisecond (< 0.05ms).

### Phase 7: Bounded Context 5 — Skills & Tool Taxonomy
- **Nội dung**:
  - Hợp nhất đăng ký Tool từ `core/skills/` và `skills/` vào một `ToolRegistry` duy nhất.
  - Phân loại rõ ràng 10 miền chức năng (`SYSTEM_OPS`, `NETWORK_SECURITY`, `FILE_STORAGE`, `DATABASE_ERP`, v.v.).
  - Triển khai `SkillResolver` nạp động công cụ theo ngữ cảnh thay vì nạp toàn bộ 79+ tools.

### Phase 8: Bounded Context 6 — Data Access & Repository Pattern
- **Nội dung**:
  - Định nghĩa Repository Interfaces trong `domain/`: `UserRepository`, `AuditRepository`, `TaskRepository`, `DeviceRepository`.
  - Xây dựng triển khai `SQLiteRepository` để duy trì tương thích 100% với dữ liệu hiện hành.
  - Chuẩn bị sẵn `PostgresRepository` phục vụ triển khai mở rộng doanh nghiệp.

### Phase 9: Bounded Context 7 — External Connectors Isolation
- **Nội dung**:
  - Tách các connector trong `core/connectors/` (AWS, OCI, Telegram, ERP, Paperless) vào `infrastructure/connectors/`.
  - Bổ sung circuit breaker, timeout và retry policy cho từng connector để cách ly lỗi.

### Phase 10: Bounded Context 8 — IoT Device Transport (ESP32 XiaoZhi)
- **Nội dung**:
  - Tách logic giao thức XiaoZhi từ `core/xiaozhi_gateway.py` thành `interfaces/websocket/xiaozhi_iot_endpoint.py`.
  - Chuyển logic giải mã Opus và quản lý trạng thái thiết bị thành adapter riêng.

### Phase 11: Bounded Context 9 — Client Agent Separation
- **Nội dung**:
  - Định nghĩa giao thức truyền tin chuẩn giữa Server và `client_agent/`.
  - Đảm bảo Client Agent hoạt động độc lập, không import mã nội bộ backend.

### Phase 12: Phân rã God Module `server.py` (HTTP Routers Refactoring)
- **Nội dung**:
  - Chia tách 150 HTTP routes sang các controller tương ứng trong `interfaces/http/`:
    - `auth_routes.py`
    - `admin_routes.py`
    - `voice_routes.py`
    - `erp_routes.py`
    - `system_routes.py`
  - Chuyển `server.py` thành hàm factory `create_app()` gọn gàng trong `apps/api/main.py`.

### Phase 13: Bảo mật & Phân quyền (Security & Zero-Trust Boundary)
- **Nội dung**:
  - Thống nhất các cơ chế bảo vệ từ `core/security/` vào `infrastructure/security/`.
  - Enforce RBAC và phân loại dữ liệu (Public, Internal, Confidential, Restricted) tại Application layer.

### Phase 14: Giám sát & Đo lường (Observability & Telemetry)
- **Nội dung**:
  - Chuẩn hóa structured logging (JSON format) với `trace_id`, `request_id`, `session_id`.
  - Tích hợp Prometheus metrics cho voice latency (TTFT, TTFA, queue depth).
  - Bổ sung endpoint kiểm tra sức khỏe `/livez`, `/readyz`, `/startupz`.

### Phase 15: Externalized State & Distributed Scaling
- **Nội dung**:
  - Tách state trong bộ nhớ Python dictionary sang Redis adapter (`infrastructure/cache/`).
  - Hỗ trợ chạy nhiều instance song song của API và Realtime Gateway.

### Phase 16: Dọn dẹp Legacy Code & Hoàn thiện Tests
- **Nội dung**:
  - Chạy toàn bộ regression test suite (Unit, Integration, Realtime, E2E).
  - Sau khi xác nhận 100% chức năng hoạt động hoàn hảo trên kiến trúc mới, chuyển các file cũ không còn dùng trong `core/` vào `_archive/`.

### Phase 17: Báo cáo nghiệm thu & Sẵn sàng vận hành (Final Enterprise Delivery)
- **Nội dung**:
  - Xuất bản tài liệu vận hành: `runbook.md`, `incident-response.md`, `backup-restore.md`.
  - Kiểm tra Production Readiness Checklist.
  - Nghiệm thu kết quả cùng người dùng.
