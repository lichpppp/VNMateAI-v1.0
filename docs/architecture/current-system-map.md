# Bản đồ hệ thống hiện tại — VN-MateAI

> Đo ngày **2026-10-06** trên nhánh `refactor/phase-0-1-safety-net`, đối chiếu với `promptfinalvnmateai.txt` (Enterprise Autonomous AI Supervisor — 194 mục).
> Mọi con số dưới đây lấy từ mã nguồn, `pytest`, quét AST hoặc máy chủ đang chạy — không ước lượng. Nguyên tắc của prompt (§4): **mã nguồn thắng tài liệu, hành vi chạy thật thắng mã nguồn**.
> Tài liệu chi tiết hơn từng phần: `canonical-components.md`, `dependency-rules.md`, `docs/realtime/*`, `docs/autonomy/*`, `docs/production/readiness-score.md`, `docs/migration/final-audit.md`.

---

## 1. Kết luận ngắn

| Câu hỏi | Trả lời |
|---|---|
| Kiến trúc lõi đã đúng hướng prompt chưa? | **Phần lớn rồi.** Mỗi chức năng lõi có MỘT bản chuẩn (thoại, LLM, TTS, fast router, policy, phân quyền, audit, vòng đời tác vụ); 10 luật kiến trúc tự động đều = 0 vi phạm; 770 pytest + 14 test Node đạt. |
| Đã "Production Ready" theo §150 chưa? | **Chưa.** Thiếu hạ tầng để chạy nhiều tiến trình: PostgreSQL, Redis, object storage, OpenTelemetry; chưa có ABAC theo phòng ban, phân loại dữ liệu, model registry; mới chạy một tiến trình trên Windows. |
| Còn trùng lặp không lý do? | Không còn bản chạy song song cho cùng một việc. Còn 3 chỗ "hai thứ" có lý do ghi rõ (§6). |
| Còn đường cũ chạy được không? | Một: alias `/ws/audio-stream` cho firmware cũ (cùng handler, có log `[DEPRECATED]` để thu bằng chứng trước khi gỡ). |
| Rủi ro lớn nhất còn lại | Toàn bộ trạng thái phiên / hàng đợi / bộ đếm nằm trong RAM của **một** tiến trình → không mở rộng ngang, mất khi khởi động lại; và tài khoản `admin/admin123` mặc định chưa đổi. |

---

## 2. Cây thư mục (mã nguồn thật, bỏ `__pycache__`, `node_modules`, bản build)

```text
VNMateaiv1/
├── main.py                     điểm vào: 2 listener uvicorn trên cùng app (HTTPS settings.PORT=443, IoT settings.IOT_PORT=8000)
├── src/mateai/                 MÃ CHÍNH — 181 tệp Python, 47 002 dòng
│   ├── config/        3 tệp    868 dòng   cấu hình chuẩn duy nhất (loader + secret_box mã hoá khoá)
│   ├── application/  69 tệp 18 176 dòng   nghiệp vụ / use case (không SQL, không SDK — RULE-011/012/024)
│   ├── infrastructure/42 tệp 11 550 dòng  adapter: DB, LLM, TTS, audio/STT, connector, cache, thông báo
│   └── interfaces/   66 tệp 16 394 dòng   giao tiếp: HTTP, WebSocket, Telegram, Email, desktop, IoT
├── core/plugin_manager.py      498 dòng   API skill công khai `@export_skill` (giữ vị trí cũ vì skill ngoài repo import nó)
├── skills/           33 tệp  5 256 dòng   bề mặt đăng ký skill (nạp động); code thật ở application/skills/builtin
├── client_agent/     14 tệp  4 415 dòng   agent máy trạm (chạy độc lập, không import `mateai`)
├── workers/           5 tệp  1 246 dòng   worker từ xa (heartbeat, enrollment)
├── web/               8 tệp 28 191 dòng   portal + HUD (HTML/JS/CSS)
├── admin/            41 tệp  5 435 dòng   app Next.js cũ — đã gỡ khỏi định tuyến (Phase 81), chỉ còn mount tài nguyên build nếu có
├── esp32_firmware/  130 tệp 10 414 dòng   firmware robot Xiaozhi
├── scripts/           6 tệp    964 dòng   backup/restore, build CSS, bench
├── tests/           140 tệp 23 277 dòng   770 pytest + 14 Node; tests/architecture = luật phụ thuộc
└── docs/             52 tệp               kiến trúc, realtime, tự trị, bảo mật, production, migration, đánh giá
```

Không có tệp mang tên `*_old / *_new / *_v2 / *_v3 / *_legacy / *_backup / *_copy / *_tmp / *_experimental` (§14, quét toàn repo).

### 2.1 `application/` — nghiệp vụ

| Gói | Tệp / dòng | Trách nhiệm (bản chuẩn) |
|---|---|---|
| `voice` | 6 / 1 453 | `voice_turn.process_voice_turn` — MỘT lượt thoại cho 5 kênh; `sentence_buffer`; `speech_text` (làm sạch lời đọc) |
| `commands` | 2 / 400 | `fast_command_router` — lệnh nhanh không gọi LLM, vẫn qua cổng tool |
| `agent` | 5 / 2 911 | `llm_engine` (vòng agent + ngân sách lượt), `tool_gate` (cổng tool DUY NHẤT), `agent_orchestrator`, `state_manager` |
| `security` | 8 / 2 387 | `policy_engine.authorize` (một hàm quyết định), `risk_engine`, `security_guard` (RBAC), `zero_trust` (hàng đợi duyệt HITL), `auth_manager` (JWT), `approval_decisions` (duyệt / huỷ) |
| `tasks` | 3 / 363 | `ledger` — sổ tác vụ AI có máy trạng thái NEW…ESCALATED; `verification` — kiểm chứng sau hành động |
| `administration` | 4 / 512 | `config_governance.save_config` (một đường ghi cấu hình: nguyên tử + lịch sử + audit), `config_service`, `autonomy_settings` |
| `operations` | 7 / 1 760 | `health_monitor`, `autonomous_sentinel`, `alert_dispatcher` (cảnh báo đa kênh), `background_workers`, `topology_events`, `topology_layout` |
| `enterprise` | 5 / 813 | `integrations` (connector / data source), `operations` (sổ quỹ, onboarding, đa tác nhân), `erp_import` |
| `skills` | 15 / 4 451 | `plugin_registry` (thực thi tool connector có timeout + circuit breaker), `skill_router` (chọn tool theo câu hỏi), skill dựng sẵn |
| `knowledge` | 4 / 1 303 | `rag_engine` (ChromaDB), `graph_rag` |
| `devices` | 4 / 867 | `task_manager` (giao việc máy trạm), `worker_enrollment` (khoá thiết bị riêng) |
| `conversation`, `analytics` | 5 / 951 | trí nhớ hội thoại; truy vấn SQL chỉ đọc có kiểm tra |

### 2.2 `infrastructure/` — adapter

| Gói | Tệp / dòng | Nội dung |
|---|---|---|
| `database` | 3 / 2 969 | `erp_database` (mở SQLite DUY NHẤT — RULE-014), `db_manager` (repository) |
| `llm` | 2 / 711 | `llm_provider.BaseLLMProvider` — Direct / NineRouter / TriBrain; stream, timeout, fallback |
| `tts` | 6 / 1 180 | `tts_stream_engine` (provider), `tts_queue_pipeline` (hàng đợi có giới hạn), `acoustic_ack` + `audio_cache` (câu ACK dựng sẵn) |
| `audio` | 3 / 1 199 | `audio_processor` — STT (Whisper / Google), VAD, wake word |
| `connectors` | 10 / 3 175 | AWS, OCI, Paperless, eInvoice, M365, generic; `base_connector` (timeout, retry, rate limit), `tool_bridge` |
| `websocket` | 2 / 174 | `binary_transport` — khung audio nhị phân |
| `notifications`, `cache`, `memory`, `directory`, `files`, `http`, `security` | 15 / 2 140 | kênh cảnh báo, cache RAM tự huỷ, vector memory, AD sync, xuất tệp, pool HTTP, TLS |

Đã xoá trong đợt này: 4 gói rỗng chỉ có `__init__.py` (`infrastructure/messaging`, `infrastructure/observability`, `infrastructure/stt`, `interfaces/cli`) — không có caller, docstring hứa "Prometheus / OpenTelemetry" mà không có mã nào (§162, §179).

### 2.3 `interfaces/` — giao tiếp

| Gói | Nội dung |
|---|---|
| `http/server.py` (≈260 dòng) | CHỈ dựng app: CORS, middleware xác thực, middleware `X-Request-ID`, mô hình lỗi chuẩn, listener IoT, móc khởi động / tắt |
| `http/routes.py` | gắn 34 router theo thứ tự cố định (bảng route có test chụp) |
| `http/lifecycle.py` | 20 bước khởi động có tên + báo cáo `GET /api/v1/health/startup`; tắt máy có thứ tự, huỷ task nền |
| `http/routers/` (32 router + `__init__`) | endpoint mỏng — nghiệp vụ ở `application` (RULE-027 = 0) |
| `websocket/` | `realtime_voice_ws` (thoại portal), `xiaozhi_gateway` (robot), `client_orchestrator` (máy trạm), `realtime_hub` (phát sự kiện), `audio_announce` |
| `telegram/`, `email/`, `desktop/`, `iot/` | gateway Telegram, IMAP/SMTP, mic + phím tắt máy chủ, beacon UDP tìm máy chủ |

---

## 3. Runtime (§10)

**Tiến trình:** một tiến trình Python (`main.py`), một event loop chạy 2 server uvicorn trên cùng `app`: HTTPS `:443` (mọi thứ) và `:8000` không TLS (chỉ đường thiết bị + probe — lọc ở `iot_listener_app`).

**20 bước khởi động** (`lifecycle.default_steps()`, đo trên máy thật hôm nay: 19 ok, 1 bỏ qua đúng):

`core` (nạp 86 skill, khôi phục hàng duyệt) → `ephemeral_sweeper` → `telegram` → `observability_workers` → `hud_telemetry` → `encrypt_secrets` → `alert_dispatcher` → `topology_loop` → `autonomous_sentinel` → `udp_beacon` (UDP 8888) → `proactive_manager` → `rag_warmup` → `multi_agent_warmup` → `webhook_routes` → `connectors` → `acoustic_ack_warmup` → `connector_tools` (11 tool) → `computer_use_tool` → `background_workers` → `email_gateway` (bỏ qua: chưa cấu hình).

**Luồng / task nền** — chủ sở hữu là `lifecycle` (task nền được giữ tham chiếu, huỷ khi tắt máy): dọn cache RAM, thu thập sức khoẻ, telemetry HUD, vòng topology 2 s, Sentinel, beacon UDP, đôn đốc 08:00/16:00, worker nền, gateway Telegram (thread), email (thread khi bật).

**Endpoint:** 209 route HTTP + 9 WebSocket:

| WebSocket | Mục đích | Xác thực |
|---|---|---|
| `/ws/v1/voice-stream` | thoại portal (giao thức chuẩn `docs/realtime/protocol.md`, audio nhị phân) | JWT |
| `/ws/hud` | HUD: telemetry + lệnh thoại + duyệt (duyệt chỉ admin) | JWT (không token = chỉ xem) |
| `/api/v1/xiaozhi/ws[/{id}]` | robot ESP32 | token thiết bị |
| `/ws/audio-stream[/{id}]` | alias firmware cũ — cùng handler, log `[DEPRECATED]` | token thiết bị |
| `/ws/portal-ui`, `/ws/topology` | đồng bộ portal, sơ đồ giám sát | JWT (phát lên màn hình người khác: chỉ admin) |
| `/ws/client` | agent máy trạm | khoá thiết bị / enrollment |

**Probe:** `/livez`, `/startupz`, `/readyz` (DB + skill + startup).

---

## 4. Hai đường xử lý chuẩn

**Thoại (§18–§30):**

```text
mic / robot / portal / HUD / REST
  → STT (audio_processor) — hoặc STT phía trình duyệt
  → voice_turn.process_voice_turn  (một hàm cho 5 kênh, có VoiceTurnTrace + request_id)
      ├─ fast_command_router  → tool_gate → trả lời cố định (không LLM)
      ├─ ACK dựng sẵn (acoustic_ack, không gọi LLM, không nói "đã xong")
      └─ llm_engine.stream_voice_response → SentenceBuffer → speech_text → tts_queue_pipeline (bounded)
           → tts_stream_engine → binary_transport → client
  Barge-in: lệnh mới huỷ lượt cũ (LLM + TTS + hàng đợi); WS đóng → huỷ lượt.
```

**Hành động / tool (§31–§55):**

```text
LLM / fast router / Portal / agent_orchestrator / plugin_registry (gọi thẳng)
  → tool_gate.run_tool_with_policy                         (cổng DUY NHẤT — RULE-017 = 0)
      → policy_engine.authorize: kill switch → L5 / tool cấm / từ khoá cấm → RBAC
                                 → rủi ro (risk_engine) → L0/L2 tự chạy | L3 chờ duyệt | L4 uỷ quyền có hạn
                                 → chế độ khẩn cấp (§54, mới)
      → DENY: không chạy + audit  |  REQUIRE_APPROVAL: hàng đợi duyệt duy nhất (zero_trust)
      → thực thi (plugin_manager / plugin_registry có breaker / máy trạm qua WS)
      → verification → ledger (bước + bằng chứng) → audit_logs (chỉ INSERT)
  Vòng agent: ≤ 6 vòng, ≤ max_tool_calls_per_turn, ≤ max_agent_seconds, tool lỗi lặp ≥ max_tool_failures_per_turn → dừng + leo thang (mới).
```

---

## 5. Kho dữ liệu (§65–§68, §135)

| Kho | Vai trò hiện tại | Prompt muốn |
|---|---|---|
| `vnmateai.db` (SQLite, 0,9 MB) | nguồn sự thật: tài khoản, tác vụ, sổ tác vụ AI, audit (INSERT-only), lịch sử cấu hình, trace thoại, uỷ quyền | PostgreSQL (kế hoạch: `docs/migration/sqlite-to-postgresql-plan.md`) |
| `hr_kpi.db` (SQLite) | nhân sự đồng bộ AD | như trên |
| `config.json` (khoá mã hoá bằng `secret_box`) | cấu hình + chính sách; ghi qua MỘT đường có lịch sử phiên bản + audit | giữ, tách theo môi trường |
| `storage/vector_db`, `storage/chroma_db` | trí nhớ dài hạn + RAG | vector store — giữ |
| `storage/audio_cache`, `knowledge_docs`, `backups` | tệp nhị phân trên đĩa cục bộ | object storage (S3/MinIO) — **chưa có** |
| RAM tiến trình | phiên WS, hội thoại ngắn, hàng đợi TTS, bộ đếm đăng nhập, idempotency, bộ đếm khẩn cấp | Redis — **chưa có** |

Không commit bí mật: `config.json`, `users.json`, `*.db`, `certs/` đều bị `.gitignore` (kiểm `git ls-files` hôm nay).

---

## 6. "Một chức năng = một bản" (§1, §160)

| Chức năng | Bản chuẩn | Số bản | Ghi chú |
|---|---|---|---|
| Voice pipeline | `voice_turn.process_voice_turn` | 1 | |
| LLM abstraction | `llm_provider.BaseLLMProvider` | 1 | client Whisper (STT) dựng riêng — ngoại lệ có ghi trong RULE-011 |
| TTS pipeline | `tts_stream_engine` + `tts_queue_pipeline` | 1 | provider + hàng đợi, không chồng chéo |
| WS thoại | `/ws/v1/voice-stream` | 1 + HUD + robot | HUD dùng schema sự kiện riêng (L10, chưa gộp) |
| Fast router | `fast_command_router` | 1 | |
| Tool registry | danh mục LLM thấy: `plugin_manager` | 1 danh mục, 2 bộ thực thi | `plugin_registry` chỉ thực thi tool connector (cần timeout + circuit breaker). Đợt này: nhánh duyệt riêng của nó đã chuyển qua cổng chuẩn |
| Skill registry | `core/plugin_manager` | 1 | |
| Policy engine | `policy_engine.authorize` | 1 | |
| Authorization path | `tool_gate` → `authorize()` | 1 | RULE-017 = 0 |
| Task lifecycle | `application/tasks/ledger` | 1 sổ AI | việc máy trạm / việc ERP là dữ liệu nghiệp vụ khác, không phải engine trùng |
| Audit | `erp_db.write_audit_log` → `audit_logs` | 1 kho, 1 hàm ghi | 4 lớp chuyển tham số (L4) |
| Ghi cấu hình | `config_governance.save_config` | 1 | RULE-027 = 0 |
| Khởi động / tắt | `interfaces/http/lifecycle` | 1 | |

---

## 7. Đối chiếu prompt — trạng thái theo nhóm mục

PASS = có mã + test + chạy thật · PARTIAL = có nhưng thiếu phần · FAIL / NOT IMPLEMENTED = chưa có.

| Nhóm (mục prompt) | Trạng thái | Bằng chứng / còn thiếu |
|---|---|---|
| Một bản chuẩn, gỡ legacy (§1, §11–§17, §122, §160–§163) | **PASS** | §6; không tệp tên legacy; 10 luật kiến trúc = 0 |
| Luật phụ thuộc (§124, §176–§177) | **PASS** | `tests/architecture/test_core_rules.py` — RULE-011…027 đều 0; đợt này RULE-026 bắt thêm `urlopen` |
| Realtime: streaming, sentence buffer, TTS theo câu, audio nhị phân, barge-in, huỷ, bounded queue (§22–§30) | **PASS** | test phase3/4/11, barge-in, backpressure; không còn `Queue()` không giới hạn |
| Fast path qua cổng (§20–§21) | **PASS** | fast router gọi `tool_gate` |
| ACK cache (§85) | **PASS** | `acoustic_ack` dựng sẵn lúc khởi động |
| Codec Opus (§27) | NOT IMPLEMENTED | chưa đo; firmware dùng PCM/MP3 |
| Jitter buffer phía client (§26) | PARTIAL | hàng đợi phát tuần tự ở `hud.js`/`app.js`, chưa có jitter buffer đo được |
| STT provider streaming (§82) | PARTIAL | STT theo đoạn, chưa có partial transcript chuẩn |
| Policy / Risk / Autonomy L0–L5 / Approval / Kill switch (§31–§38, §51, §53) | **PASS** | `policy_engine`, `risk_engine`, `zero_trust`; kill switch toàn cục / tác nhân / tool |
| Emergency mode (§54) | **PASS** (mới) | quá `emergency_max_actions_per_minute` → tự bật kill switch (bền, audit, cảnh báo critical) — `test_emergency_and_loop_guards.py` |
| Loop guards (§55, §158) | **PASS** (mới phần lỗi lặp) | vòng, số tool, thời gian, lỗi lặp → dừng + leo thang |
| Tool contract đầy đủ (§39) | PARTIAL | có tên, schema, risk, timeout (registry); thiếu `output_schema`, `reversible`, `data_scope` cho skill |
| Task ledger + verification + evidence (§41, §47, §49) | **PASS** | `ledger`, `verification`; COMPLETED cần kiểm chứng đạt |
| Goal hierarchy / Priority engine (§42–§43) | NOT IMPLEMENTED | |
| Incident management (§88) | PARTIAL | Sentinel phát hiện + cảnh báo; chưa có vòng đời incident đầy đủ |
| Identity tách người / tác nhân / thiết bị (§33) | **PASS** | `agent_id` do máy chủ gán; khoá thiết bị riêng; audit có `agent_id`, `op_task_id` |
| RBAC (§34) | **PASS** | `security_guard` + `require_roles` |
| ABAC / tenant / phòng ban (§35, §64, §155) | NOT IMPLEMENTED | chưa có `department_id` trong quyết định |
| Data classification / exfiltration (§62–§63) | PARTIAL | che dữ liệu nhạy cảm trước khi gửi LLM (`mask_sensitive_data`); chưa có nhãn PUBLIC…RESTRICTED |
| Prompt injection / memory trust (§59–§61) | PARTIAL | ranh giới tin cậy trong chỉ thị; kết quả tool là dữ liệu; chưa có trường tin cậy cho bản ghi trí nhớ |
| Audit bất biến (§73) | **PASS** | chỉ INSERT, không endpoint xoá; chưa có chữ ký toàn vẹn |
| API mỏng (§74), lỗi chuẩn (§143) | **PASS** (mới phần lỗi) | RULE-027 = 0; lỗi không bắt → `{"error":{code,message,request_id}}`, không lộ stack trace |
| Request ID / log có cấu trúc (§95) | PARTIAL (mới) | `X-Request-ID` mọi phản hồi; log chưa phải JSON |
| Rate limit (§97) | PARTIAL | đăng nhập, enroll, connector; chưa cho voice / LLM |
| Timeout (§99), retry (§100), circuit breaker (§101) | **PASS** | `test_http_timeouts.py`; breaker ở connector |
| Health + graceful shutdown (§91–§92) | **PASS** | probe; `lifecycle.run_shutdown` |
| OpenTelemetry / metrics backend (§93–§94) | NOT IMPLEMENTED | có số đo trong ứng dụng (`/api/v1/ops/overview`, trace thoại), chưa xuất OTel |
| Skill loader không chạy code tuỳ ý (§106, §118) | **PASS** (mới) | tệp có lệnh cấp module bị từ chối nạp |
| PostgreSQL / Redis / object storage / worker queue (§65–§69) | NOT IMPLEMENTED | kế hoạch PG có; hạ tầng chưa có |
| Mở rộng ngang (§145, §164) | FAIL | trạng thái trong RAM một tiến trình |
| Model registry (§79) | NOT IMPLEMENTED | danh sách model lấy từ router lúc chạy |
| Backup / restore (§136) | **PASS** | `scripts/backup.py` có kiểm chứng; chưa lên lịch tự động |
| CI/CD (§139) | PARTIAL | workflow có, chưa xác nhận chạy; chưa quét container |
| Governance NIST / ISO 42001 (§108–§109) | PARTIAL | `docs/governance/ai-governance.md`; chưa có risk register / model governance riêng |
| Golden scenarios (§152) | PARTIAL | 10 kịch bản vàng (`docs/evaluation/agent-evaluation.md`); prompt cần 15 |

---

## 8. Đã sửa trong đợt rà soát này (2026-10-06)

Mỗi mục có test viết trước rồi mới sửa, và đã kiểm trên máy chủ thật.

1. **Chế độ khẩn cấp (§54).** AI tự chạy hành động có tác dụng phụ quá ngưỡng trong 60 s → tự bật kill switch qua đường đổi giới hạn tự trị chuẩn (lưu bền, lịch sử, audit `system:emergency-guard`, cảnh báo critical). Hành động người đã duyệt và tác vụ chỉ đọc không bị tính; chỉ người tắt lại được. Ngưỡng `autonomy.emergency_max_actions_per_minute` (mặc định 30, 0 = tắt), admin chỉnh qua `PUT /api/v1/security/autonomy`.
2. **Tool lỗi lặp (§55, §158).** Một tool lỗi `max_tool_failures_per_turn` lần (mặc định 3) trong cùng lượt → không gọi lại, trả `repeated_failure` ("CHƯA được thực hiện"), leo thang một lần.
3. **Một đường phân quyền (§1, §40, §182).** `plugin_registry.execute_tool` gọi thẳng (không cờ `authorized`) từng có đường duyệt HITL riêng — nay đi qua `tool_gate` (policy + hàng đợi duyệt chuẩn + RBAC theo danh tính gọi).
4. **Mô hình lỗi API + request id (§95, §143).** Mọi phản hồi có `X-Request-ID` (kể cả 401); lỗi không bắt trả JSON chuẩn, không lộ thông điệp ngoại lệ.
5. **Nạp skill không chạy code tuỳ ý (§106, §118).** `skills/generate_pdf.py` (tệp chưa commit) là script ghi đè `Desktop\Palo_Alto_Networks_Overview.html` mỗi lần máy chủ khởi động / chạy test. Bộ nạp nay từ chối mọi tệp có lệnh ở cấp module — không sửa tệp của chủ dự án, chỉ không import nó.
6. **Test giả (prompt: "không fake tests").** `test_phase60_sot_bao_mat.py` có 11 kiểm tra **trượt mà pytest vẫn xanh** (hàm `check()` chỉ ghi lại, không báo đỏ): 6 kiểm tra chuỗi nguồn trỏ vào `server.py` cũ, 5 kiểm tra định dạng kết quả bị RBAC chặn. Đã sửa cả 11 và cho `check()` báo đỏ dưới pytest; `test_phase87` cùng bệnh, cũng đã sửa.
7. **Gói rỗng** — xoá 4 gói chỉ có `__init__.py` (§2.2).

Trước đó cùng ngày (Supervisor Phase 10, commit `afb3761`): `server.py` 1 073 → ~260 dòng; khởi động theo bước có báo cáo; nghiệp vụ cấu hình / duyệt / nhập ERP ra khỏi router; 12 lỗi thật (beacon trả IP cứng, `urlopen` chặn loop, HUD chưa đăng nhập phát chữ lên mọi HUD, …).

---

## 9. Trả lời 40 câu §186

| # | Câu hỏi | Trả lời (bằng chứng) |
|---|---|---|
| 1 | Voice pipeline chuẩn | `application/voice/voice_turn.process_voice_turn` |
| 2 | LLM abstraction | `infrastructure/llm/llm_provider.BaseLLMProvider` |
| 3 | TTS pipeline | `tts_stream_engine` (provider) + `tts_queue_pipeline` (hàng đợi bounded) |
| 4 | WS protocol | `/ws/v1/voice-stream`, sự kiện ở `docs/realtime/protocol.md`; HUD còn schema riêng |
| 5 | Fast router | `application/commands/fast_command_router` |
| 6 | Tool registry | danh mục `core/plugin_manager`; thực thi connector qua `plugin_registry` (breaker) — cả hai sau `tool_gate` |
| 7 | Skill registry | `core/plugin_manager` (86 skill) |
| 8 | Policy engine | `application/security/policy_engine.authorize` |
| 9 | Authorization path | `tool_gate.run_tool_with_policy` → `authorize()` (RULE-017 = 0) |
| 10 | Task lifecycle | `application/tasks/ledger` (NEW…ESCALATED, COMPLETED cần kiểm chứng) |
| 11 | Audit path | `erp_db.write_audit_log` → `audit_logs` (INSERT-only) |
| 12 | System of record | SQLite `vnmateai.db` (+ `hr_kpi.db`), `config.json` cho chính sách |
| 13 | Session state | RAM tiến trình (`realtime_hub`, `hud_voice`, `memory_manager`); trace thoại + sổ tác vụ lưu DB |
| 14 | Binary storage | đĩa cục bộ `storage/` |
| 15 | Stateless | router HTTP, `policy_engine`, `risk_engine`, provider LLM/TTS |
| 16 | Stateful | WS session, hàng đợi TTS, hàng duyệt HITL (bền qua audit), bộ đếm đăng nhập / khẩn cấp, cache |
| 17 | Scale ngang | **chưa được** — cần Redis cho state chia sẻ + PostgreSQL (`readiness-score.md`) |
| 18 | Đã xoá | `domain/*`, cây `apps/` rỗng, `sentence_streamer`, `streaming_tts_pipeline`, đường HITL riêng của registry, RBAC riêng router skills, 4 gói rỗng, … (`final-audit.md` §11, §8 ở trên) |
| 19 | Còn lại | alias `/ws/audio-stream`; 4 hàm bọc audit; Base64 ở REST thoại; client Whisper đồng bộ; app `admin/` đã gỡ định tuyến |
| 20 | Vì sao còn | alias: chưa có bằng chứng log hết firmware cũ; hàm bọc: chỉ đổi tham số; Base64: client REST không có WS; Whisper: SDK đồng bộ |
| 21 | Legacy còn chạy được? | chỉ alias WS robot (cùng handler mới); không đường thực thi tool nào ngoài cổng (quét AST) |
| 22 | LLM vượt phân quyền? | không — quyết định từ danh tính máy chủ xác thực + cấu hình, cờ trong tham số bị bỏ |
| 23 | Tool vượt policy? | không — RULE-017 = 0; registry gọi thẳng cũng vào cổng (đợt này) |
| 24 | Agent chạy vô hạn? | không — ≤ 6 vòng, ngân sách tool / thời gian, lỗi lặp, chế độ khẩn cấp |
| 25 | Người dừng được? | có — kill switch toàn cục / tác nhân / tool, huỷ lượt, ngắt lời |
| 26 | Mọi hành động quan trọng có audit? | tool: có (caller, agent_id, quyết định, luật, policy_version, op_task_id) |
| 27 | Mọi hành động kiểm chứng được? | một phần — NONE/BASIC tự động; STANDARD cho `kill_process`, `write_file`; rủi ro cao khác → ESCALATED, không báo xong |
| 28 | LLM lỗi | trả lời an toàn, không báo "đã xong"; fallback model theo router |
| 29 | TTS lỗi | chữ vẫn hiện, Edge TTS dự phòng |
| 30 | Redis lỗi | không dùng Redis |
| 31 | PostgreSQL lỗi | chưa dùng PostgreSQL; SQLite lỗi → `/readyz` 503, ghi thất bại không giả thành công |
| 32 | WS ngắt | huỷ lượt, gỡ khỏi danh sách phát |
| 33 | TTFT / TTFA / TTFD / TTL | trace thoại đo từng lượt; 24 h qua, lượt cần agent (n = 2): ACK tiếng ≈ 1 ms (cache), token LLM đầu p50 2,6 s, câu trả lời có tiếng p50 10,0 s. Mẫu lớn hơn: `docs/realtime/performance-before-after.md` (n = 20: lệnh nhanh p50 53 ms) |
| 34 | p50 / p95 / p99 | như trên — p99 chưa đủ mẫu |
| 35 | Task success rate | 24 h qua: 4/4 tác vụ AI hoàn thành có kiểm chứng (100 %, mẫu quá nhỏ để kết luận) |
| 36 | Policy deny rate | 24 h qua: 0 % (4 quyết định, đều `auto`) |
| 37 | Human override rate | 0 yêu cầu duyệt trong 24 h — chưa có số đo đại diện |
| 38 | Cost / task | 24 h: 2 lần gọi LLM, 17 928 token; chưa quy ra tiền (router không trả giá) |
| 39 | Chưa production-ready | mở rộng ngang, PostgreSQL / Redis / object storage, OpenTelemetry, ABAC phòng ban, phân loại dữ liệu, model registry, goal / priority / incident đầy đủ, codec Opus, CI chạy thật, lịch backup |
| 40 | Rủi ro cao nhất | trạng thái chỉ trong RAM một tiến trình (mất khi khởi động lại, không HA) + mật khẩu admin mặc định |

---

## 10. Việc tiếp theo đề xuất (theo thứ tự rủi ro)

1. **Chủ dự án:** đổi mật khẩu `admin`, xoá `certs/config.json.pre-encrypt.bak`, lên lịch `scripts/backup.py create`, chuyển `skills/generate_pdf.py` ra khỏi `skills/` (giờ đã bị từ chối nạp nhưng vẫn nằm đó).
2. **ABAC theo phòng ban** (§35, §155) — dùng `department` có sẵn trong ERP, đưa vào `authorize()`.
3. **Redis cho state chia sẻ + PostgreSQL** theo kế hoạch có sẵn — điều kiện để chạy nhiều tiến trình.
4. **Gộp schema sự kiện HUD** vào giao thức thoại chuẩn (L10).
5. **OpenTelemetry** cho trace thoại / tool (đã có `request_id` + `VoiceTurnTrace` làm nền).
6. Bổ sung tool contract (`output_schema`, `reversible`, `data_scope`) và nhãn phân loại dữ liệu.
