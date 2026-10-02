# VN-MateAI — Kế hoạch refactor production

> Cập nhật Phase 0 · 2026-10-01. Thay thế bản trước (bản trước ghi Phase 2–11 đã xong, nhưng phần code của các phase đó nằm trong `src/mateai/` và chưa được nối vào runtime).
> Số liệu hiện trạng: `docs/architecture/current-vs-target.md`. Trùng lặp: `docs/architecture/duplication-matrix.md`.

## 1. Nguyên tắc

1. **Một tính năng = một implementation.** Không thêm bản mới bên cạnh bản cũ. Trình tự mỗi bước: chứng minh bản thay thế → chuyển caller → xóa bản cũ.
2. **Gộp trước, di chuyển sau.** Bản trùng được gộp vào bản canonical ngay trong `core/`. Chỉ khi một context còn đúng một implementation mới di chuyển nó sang tầng đích. Không lúc nào có hai bản chạy song song ở hai cây thư mục.
3. **Từng bounded context một**, theo thứ tự: Voice → LLM/Agent → Skills/Tools → Security → Data → Connectors → Deployment.
4. **Không xóa** khi chưa đủ: thay thế có sẵn, caller đã chuyển, test pass, đã chạy thử runtime. Phase 0–3 không xóa, không di chuyển hàng loạt, không đổi tên hàng loạt.
5. **Không lùi hiệu năng realtime.** Mỗi bước Voice phải đo lại TTFT/TTFA/fast path bằng cùng một công cụ đo (xây ở Phase 1).
6. **Không số liệu giả.** Số nào chưa đo thì ghi "chưa đo".

## 2. Trạng thái thật của các phase cũ (commit `4f6464a`)

| Phase cũ | Ghi trong bản trước | Thực tế |
|---|---|---|
| 0 Audit | đã xong | số liệu sai (đếm lẫn `node_modules`/`.venv`) — làm lại ở Phase 0 này |
| 1 Dependency graph + test baseline | đã xong | "24 vòng import" không khớp (thực đo: 1 khối 37 module + 1 vòng 2 module); không có runner test chung |
| 2 Skeleton `src/mateai` | đã xong | có thư mục; không được import |
| 3–11 Domain, voice, LLM, fast router, skills, repository, connectors, IoT, client agent | đã xong | mỗi phase viết một bản mới trong `src/mateai/`; runtime vẫn dùng `core/`; unit test kiểm tra bản mới |
| Architecture tests | 100 % pass | chỉ quét `src/mateai/`, 3/10 quy tắc |

## 3. Quyết định cần chủ dự án chốt

| Mã | Câu hỏi | Khuyến nghị | Ảnh hưởng nếu chưa chốt |
|---|---|---|---|
| **D1** | Làm gì với `src/mateai/` (69 file viết lại, chưa nối)? | Giữ `src/mateai/` làm **thư mục đích**, nhưng nội dung đích là **code canonical di chuyển từ `core/`**. Bản viết lại nào trùng với code đang chạy thì xóa khi context đó được di chuyển; entity domain thuần Python giữ lại nếu được dùng thật. Sửa import `src.mateai.*` → `mateai.*` (cài package editable). | Phase 4 trở đi bị chặn |
| D2 | HUD có chuyển sang giao thức `/ws/v1/voice-stream` không? | Có. `/ws/hud` giữ phần telemetry, bỏ phần voice | P2 phải giữ song song |
| D3 | Gộp hai mô hình vai trò (`admin/manager/viewer` và `admin/it_support/operator/viewer`) thế nào? | Bảng ánh xạ do chủ dự án duyệt | Phase Security bị chặn |
| D4 | Máy chủ có được chạy skill điều khiển PC (`skills/pc_control_skills.py`…) trên chính nó không? | Hỏi chủ dự án | E3 trong `legacy-candidates.md` giữ nguyên |
| D5 | Khi nào chuyển PostgreSQL làm system of record? | Sau khi gộp DAO (bảng `tasks`, user) — không chuyển DB khi dữ liệu còn hai chủ | Phase Data chỉ làm phần gộp |

## 4. Lộ trình

Mỗi phase kết thúc bằng báo cáo: PHASE / STATUS / FILES CHANGED / ARCHITECTURAL IMPACT / RISKS / TESTS / PERFORMANCE / NEXT STEP.

### Phase 0 — Audit (xong)
Tài liệu: `current-vs-target.md`, `target-architecture.md`, `dependency-rules.md`, `duplication-matrix.md`, `legacy-candidates.md`, `canonical-components.md`, file này. Không đổi code.

### Phase 1 — Lưới an toàn (xong 2026-10-01, xem báo cáo ở mục 6)
- Khai báo phụ thuộc test (`pytest`, `pytest-asyncio`, `pytest-timeout`). (`gTTS` **không** thêm: xung đột `click` với `huggingface-hub` — nhánh gTTS chuyển thành ứng viên xoá, xem `legacy-candidates.md` A7.)
- Một lệnh chạy toàn bộ test: chuyển các file dạng script sang hàm `test_*` hoặc runner tách tiến trình; cô lập test khỏi `config.json`/DB thật (thư mục tạm + biến môi trường).
- Sửa test phụ thuộc môi trường (ngưỡng thời gian, số dòng audit của máy dev, quyền file trên Windows, gọi mạng thật → đánh dấu `network`).
- Sửa architecture test: quét `core/` + baseline ngoại lệ; thêm RULE-011…016.
- **Công cụ đo latency voice** chạy được lặp lại: fast path, TTFT, TTFA, first audio qua WS, RAM/CPU — ghi baseline thật trước khi đụng Voice.
- Kiểm chứng: tất cả test pass hoặc được đánh dấu rõ lý do skip.

### Phase 2 — Voice: gộp TTS và làm sạch text (Phase B/C trong `core/`)
- Bỏ chặn event loop: `fast_command_router` gọi `psutil.cpu_percent(interval=0.05)` trong handler async → khoá loop 50 ms cho **mọi** phiên realtime mỗi lần hỏi CPU (đo: p50 50,5 ms).
- Edge-TTS trả 403 → mọi câu rơi xuống 9Router (TTFA engine p50 2,3 s). Kiểm tra nâng `edge-tts` (đang ghim 6.1.12).
- A7: xoá nhánh gTTS chết (3 nơi).
- C3: một hàm `sanitise_for_tts` (test so sánh đầu ra ba hàm cũ trước).
- C1, C2: mọi tổng hợp giọng qua `TTSStreamEngine`; `AudioEngine` chỉ còn STT/VAD; bỏ race gTTS trong `server.py`.
- A1, B1, B2: xóa facade chết sau khi sửa test.
- Kiểm chứng: test voice, chạy thử portal + HUD + ESP32 (nếu có thiết bị) + mic máy chủ, đo lại TTFA.

### Phase 3 — Voice: một use case xử lý lượt nói
- Tách phần xử lý lượt nói của P1 thành một hàm/lớp dùng chung (không có WebSocket bên trong).
- Chuyển P2 (HUD, sau D2), P3 (XiaoZhi), P4 (mic), P5 (REST) sang use case đó; mỗi kênh chỉ còn transport + audio sink.
- C4, C7, C8: bỏ `stream_voice_response`, pipeline HUD trong `server.py`, `VoiceSessionStore`, `_history` của `voice_controller`.
- A5: bỏ alias `/ws/voice`. E2 `/ws/audio-stream`: chỉ bỏ khi log truy cập xác nhận không còn thiết bị dùng.
- Kiểm chứng: như Phase 2 + test barge-in trên mọi kênh.

### Phase 4 — Voice: di chuyển sang tầng đích (cần D1)
- Di chuyển canonical Voice sang `src/mateai/{application,infrastructure,interfaces}` theo bảng ánh xạ ở `target-architecture.md` §V; xóa bản viết lại trùng trong `src/mateai/`; trỏ test về code đã di chuyển.

### Phase 5 — LLM / Agent
- Failover phải nhớ model hỏng: hiện `NineRouterLLMProvider` thử lại toàn bộ danh sách mỗi lượt, mỗi model hỏng tốn tới 5 s → đo được TTFT p50 22,6 s (6 model hỏng/chậm trước model chạy được). Cần breaker theo từng model + bỏ giá trị mẫu `YOUR_MODEL_NAME_HERE` khỏi danh sách dự phòng.
- C5, C6: mọi lời gọi LLM qua `LLMProvider` (`complete()` cho đường không stream); bỏ hai vòng fallback trong `llm_engine`; 5 module tự tạo client chuyển sang provider.
- Sau đó di chuyển `llm_provider` → `infrastructure/llm`, phần agent/prompt của `llm_engine` → `application/agent`.

### Phase 6 — Skills / Tools
- D7: một registry (giữ circuit breaker + HITL gate của `plugin_registry`); A2, A3, A4.
- D4 quyết định skill máy trạm phía server; C9 sinh gói tải về từ `client_agent/`.

### Phase 7 — Security (cẩn thận, không xóa vì "trông giống")
- D1 (legacy) HITL: gộp trạng thái chờ duyệt rồi mới chuyển caller. D2 audit: một sink, giữ INSERT-only. D3 rủi ro. D4 vai trò (cần quyết định D3 của mục 3).
- Health `/livez` `/readyz` `/startupz`.

### Phase 8 — Data
- D6: một repository cho `tasks`; D5: một kho user; C11: bỏ `sqlite3.connect` rải rác; E5 `hr_kpi.db`.
- Sau khi dữ liệu chỉ còn một chủ: kế hoạch SQLite → PostgreSQL (inventory schema, script, đếm dòng, checksum, cutover, rollback).

### Phase 9 — Connectors, cấu hình
- C10: một loader cấu hình; gộp khóa trùng; tách secret khỏi `config.json`.
- 17 `httpx` client: timeout/retry/breaker thống nhất.

### Phase 10 — State, worker, triển khai
- Đưa state chia sẻ (session, HITL chờ duyệt, lịch sử ngắn hạn) ra Redis; tiến trình api / realtime / worker tách được; container; CI.

### Phase 11 — Kiểm toán cuối
- Trả lời đầy đủ các câu hỏi "final check" (số implementation cũ, đã xóa, wrapper còn lại và lý do, có bản cũ nào còn gọi được lúc chạy không).

## 5. Rủi ro chính

| Rủi ro | Giảm thiểu |
|---|---|
| Gộp voice làm lệch giọng đọc / ngắt câu ở một kênh | test so sánh đầu ra sanitize + chạy thử từng kênh |
| Thiết bị ESP32 ngoài thực địa dùng đường WS cũ | không bỏ `/ws/audio-stream` khi chưa có log truy cập |
| Gộp HITL làm mất yêu cầu đang chờ duyệt | gộp kho trạng thái trước, chuyển caller sau, có test đường Telegram |
| Test hiện tại ghi vào dữ liệu thật | cô lập test ở Phase 1 trước mọi thay đổi khác |
| Hiệu năng lùi mà không ai biết | công cụ đo Phase 1, đo lại sau mỗi phase Voice |

## 6. Báo cáo Phase 1 (2026-10-01)

**STATUS:** xong. Chạy toàn bộ test bằng một lệnh: `python -m pytest` (cài trước `pip install -r requirements-dev.txt`).

**Kết quả test:** 158 pass / 0 fail (trước Phase 1: 38/47 file Python pass, 5/12 file `.mjs` pass, không có lệnh chạy chung). 1 test `network` bỏ qua mặc định (`pytest -m network` để chạy; đang fail vì model trên 9Router chết — dữ liệu ngoài, không phải lỗi code).

**Lỗi thật tìm ra và đã sửa** (mỗi lỗi có test fail trên `HEAD`, pass sau khi sửa):

| Lỗi | Nơi | Hệ quả trước khi sửa | Test |
|---|---|---|---|
| `NameError: sanitized_query` | `core/realtime_voice_ws.py` | **mọi câu hỏi qua LLM ở portal đều lỗi**; chỉ lệnh nhanh chạy được | `tests/test_realtime_voice_llm_turn.py` |
| HUD mất hàng đợi TTS song song, lời đệm, timeout TTS (commit 4f6464a) | `core/server.py` `_process_hud_voice_command_body` | mỗi câu cộng nguyên thời gian TTS; TTS treo thì treo cả lượt | `tests/test_hud_voice_pipeline_behavior.py` (+ 2 test cũ) |
| Khoá giả `sk-dummy` quay lại UI + ghi vào `config.json` | `web/app.js`, `core/server.py` | ghi khoá giả vào config | `test_phase74_no_fake_ui.mjs` |
| Nút thử kết nối "direct" gửi **khoá 9Router sang URL direct** khi ô khoá trống | `core/server.py` `/api/v1/llm/test` | lộ khoá 9Router cho máy chủ khác | (kiểm tra tay) |
| Pane Phòng Ban / Elastic Grid không được bảo vệ | `web/app.js` `CC_HANDWRITTEN_SUBTABS` | lần đồng bộ nguồn dữ liệu kế tiếp xoá trắng nội dung pane | `test_phase85_may_tram_that_muc.py` |
| `with sqlite3.Connection` không đóng kết nối | `core/database.py`, `db_manager.py`, `domain_sync.py` | file DB bị giữ tới khi GC; Windows lỗi `WinError 32` | `test_phase73`, `test_phase75` |
| Đồng hồ đo latency phân giải 15,6 ms trên Windows | `core/realtime_voice_ws.py` `VoiceRequestTrace` | TTFD/TTFT/TTFA nhảy bậc | `test_phase1_realtime_ws.py` |

**Cô lập test:** `VNMATEAI_DB_PATH` / `VNMATEAI_HR_DB_PATH` (mặc định giữ đường dẫn cũ) — `tests/conftest.py` trỏ chúng vào thư mục tạm; sao lưu và trả lại `config.json`, `users.json`, `skills/registry.json`, `config/data_sources.json` quanh phiên test. Trước đó test đã ghi 54 sự kiện audit giả vào `audit_logs` thật và 3 task giả vào `tasks` thật (3 task đã xoá; 54 dòng audit chưa xoá vì bảng là chỉ-ghi — chờ chủ dự án quyết).

**Test sửa vì phụ thuộc môi trường** (không nới điều kiện kiểm tra): đọc file `.mjs` chuẩn hoá CRLF (6 file fail chỉ vì `core.autocrlf=true`); `test_repositories` dùng DB tạm thay vì đòi ≥ 2459 dòng audit của máy dev; `test_phase62` bỏ qua kiểm tra chmod 600 trên Windows kèm cảnh báo (rủi ro thật, xem `legacy-candidates.md` E7); `test_phase85` đo đúng phạm vi khối Máy Trạm; `test_phase67` chấp nhận `_tts_bytes` (bọc timeout, trả bytes cho binary frame).

**Architecture test:** `tests/architecture/test_core_rules.py` quét `core/`, `skills/`, `workers/`, `main.py` theo RULE-011…015, baseline `core_rules_baseline.json` (bánh cóc: vi phạm mới → fail; giảm → phải hạ baseline). Test cũ chỉ quét `src/mateai` nay được pytest chạy (trước đó không có hàm `test_*`).

**Baseline hiệu năng** (`scripts/bench_voice.py`, Windows 10, `perf_counter`, đo thật):

| Chỉ số | p50 | p95 / max | Ghi chú |
|---|---|---|---|
| Fast path — 4/5 lệnh | 0,006–1,3 ms | ≤ 2 ms | `mấy giờ rồi`, `xin chào`, `ping`, `xem ram` |
| Fast path — `kiểm tra cpu` | 50,5 ms | 51,7 ms | chặn event loop (Phase 2) |
| SentenceBuffer, 58 token → 5 câu | 0,48 ms | 1,37 ms | |
| TTS chunk đầu (engine, không cache) | 2.297 ms | 3.625 ms | Edge-TTS 403 → 9Router |
| WS fast path: sự kiện đầu / chữ đầu | 2,8 ms / 252 ms | 2,8 / 321 ms | 3 lượt |
| WS fast path: audio đầu / hết lượt | 1.612 ms / 1.830 ms | 3.221 ms | |
| WS LLM: chữ đầu (TTFT) | 22.623 ms | 26.552 ms | 6 model hỏng trước model chạy được (Phase 5) |
| WS LLM: audio đầu / hết lượt | 26.402 ms / 26.408 ms | 26.558 ms | |
| Server rảnh | 405 MB WS, 6,4 % CPU | | đo Phase 0 |

Số liệu chỉ có 3 lượt WS — dùng làm mốc so sánh, không phải p95 có ý nghĩa thống kê. Tăng `--ws-rounds` khi cần.

## 7. Báo cáo Phase 2 — Voice: gộp TTS và làm sạch text (2026-10-01)

**STATUS:** xong, trừ một mục bị chặn (xem cuối mục).

**Một implementation cho mỗi việc (đã chuyển caller rồi xoá bản cũ):**

| Việc | Trước | Sau |
|---|---|---|
| Tổng hợp giọng | `TTSStreamEngine` + `AudioEngine.text_to_speech_stream/_bytes/_tts_9router/_tts_gtts` + race gTTS trong `server._tts_bytes` | chỉ `core/audio/tts_stream_engine.py` (`AudioEngine` còn STT/VAD) |
| Làm sạch text cho TTS | 3 hàm: `sanitise_for_tts`, `llm_engine._sanitise_for_tts`, `audio_processor.clean_text_for_tts` | `sentence_streamer.sanitise_for_tts` + `shorten_for_speech` (rút gọn) + `tts_stream_engine.apply_pronunciation` (chỉ áp lên chữ gửi đi tổng hợp) |
| Tách câu từ luồng token | `SentenceBuffer` + `SentenceStreamer` (bọc lại) + `split_into_sentences` | chỉ `SentenceBuffer` (hết vòng import `sentence_buffer ↔ sentence_streamer`) |
| Làm nóng cache TTS | `prewarm_tts_cache` (thread + event loop riêng) + `warmup_acoustic_ack_cache` | chỉ `warmup_acoustic_ack_cache` (loop chính) |
| Hàm bọc TTS của server | `_tts_bytes(audio_engine, …)` + `_safe_tts` (base64) | `_tts_bytes(text)` |

**Đã xoá:** `SentenceBoundaryStreamer`, `edge_tts_stream_audio`, `get_acoustic_ack_for_query`, `race_synthesise`, `SentenceStreamer`, `split_into_sentences`, `clean_text_for_tts`, `prewarm_tts_cache`, `_safe_tts`, 4 phương thức TTS của `AudioEngine`, mọi nhánh gTTS (3 nơi), cache RAM riêng `_TTS_CACHE`.

**Hành vi thay đổi có chủ đích:**
- Thứ tự nguồn TTS: 9Router → Edge (trước: Edge → 9Router → gTTS). Lý do đo được: 9Router p50 1,2 s; Edge trực tiếp p50 3,8 s.
- `edge-tts` 6.1.12 → 7.2.8 (6.1.12 bị dịch vụ trả 403; API dùng trong code tương thích).
- Mọi kênh đọc cùng một cách: hết đọc to URL/bảng/khối code ở HUD; hết "Wi, Fi" / "COVID, 19" ở ESP32 + mic; gợi ý phát âm (A P I, C P U) áp cho mọi kênh nhưng không lên chữ hiển thị.
- Pool HTTP theo từng event loop (`core/connection_pool.py`): luồng tự chạy loop riêng không còn dùng nhầm client của loop chính (log khởi động trước đây: `Event loop is closed`).

**Test:** 176 pass / 0 fail (Phase 1: 158). Test mới: `test_tts_text_sanitiser.py`, `test_tts_engine_sources.py`, `test_connection_pool_event_loops.py` (fail trên code cũ). RULE-012 (TTS ngoài engine) giảm từ 5 vi phạm / 3 file xuống 1 (`skills/ninerouter_skills.py` — tool tạo file giọng nói cho người dùng, REVIEW).

**Hiệu năng (cùng công cụ `scripts/bench_voice.py`):**

| Chỉ số | Phase 1 | Phase 2 |
|---|---|---|
| TTS chunk đầu (engine, câu mới) | p50 2.297 ms | **p50 1.213 ms** (max 2.298) |
| WS fast path: audio đầu | p50 1.612 ms | **p50 1.172 ms** |
| WS fast path: hết lượt | p50 1.830 ms | **p50 1.173 ms** |
| WS LLM: từ chữ đầu tới audio đầu | ~3.780 ms | **~560 ms** |
| WS LLM: chữ đầu (TTFT) | p50 22.623 ms | p50 22.405 ms (không đổi — Phase 5) |
| Khởi động: câu đệm sẵn sàng | 21/23, có lỗi TTS | 28/28, 0 lỗi |

**Bị chặn, chưa làm:** sửa `psutil.cpu_percent(interval=0.05)` chặn event loop trong `core/fast_command_router.py` (dòng ~257 và ~281). Thao tác đọc đoạn code đó bị bộ phân loại an toàn của Claude Code từ chối; chờ chủ dự án cho phép hoặc tự sửa (đề xuất: `await asyncio.to_thread(psutil.cpu_percent, 0.05)`).

**Phát hiện mới (chưa sửa):** `tts_stream_engine.reset_tts_engine()` không được gọi ở đâu → đổi giọng/tốc độ trong cấu hình chỉ có hiệu lực sau khi khởi động lại server.

## 8. Báo cáo Phase 3 — Voice: một use case cho mọi kênh (2026-10-02)

**Quyết định của chủ dự án:** "stream + vòng agent" — câu trò chuyện stream từng câu; câu cần tool chạy vòng agent đầy đủ (`ask_async`: nhiều bước, "Đồng ý" tiếp tục tác vụ chờ duyệt, gửi lệnh tới máy trạm). Giữ nguyên mọi giao thức client (không đổi `/ws/hud`, firmware, REST — D2 chưa áp dụng).

**Lỗ hổng bảo mật đã vá (commit `b792374`):** đường voice của portal chạy MỌI tool không qua Zero-Trust / RBAC / audit (import `zero_trust.evaluate_risk` — không tồn tại — rồi nuốt lỗi). Nay chỉ còn một cổng: `core/agent_voice_loop.run_tool_with_policy`, dùng bởi vòng agent; portal truyền username đã đăng nhập làm danh tính RBAC (`caller`), các kênh khác giữ nguyên hành vi (gồm chính sách bỏ qua xác nhận cho kênh quản trị từ commit f389bbe).

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Xử lý lượt nói | 4 đường: `realtime_voice_ws._execute_voice_turn` (vòng tool 1 bước riêng), `server._process_hud_voice_command_body`, `xiaozhi_gateway._execute_pipeline`, `voice_controller._stream_response_and_play` | `core/voice_turn.process_voice_turn`; mỗi kênh còn transport + `VoiceSink` (đầu ra) |
| Thực thi tool | `ask_async` (có kiểm soát) + `agent_voice_loop.execute_tool_call` (không kiểm soát) | `run_tool_with_policy` |
| Lịch sử hội thoại | `memory_manager` + `VoiceSessionStore` (HUD) + `voice_controller._session_history` | `memory_manager` (khoá = session_id); `VoiceSession` chỉ giữ trạng thái "đang chờ admin trả lời" |
| Nhận diện câu hỏi | `voice_session.looks_like_question` + bản riêng trong `xiaozhi_gateway` | `looks_like_question` |
| Hàng đợi TTS | HUD tự làm (`_tts_pending`), ESP32/mic tuần tự | `StreamingTTSWorkerPipeline` cho mọi kênh, thêm timeout 14 s mỗi câu |

Đã xoá: vòng tool 1 bước của portal + `execute_tool_call`, `can_synthesize_direct_response`, `prune_tool_schemas`, `prune_tool_payload_for_llm`, `ToolExecutionResult`, `AgentTurnMetrics`; lịch sử trong `VoiceSession`; `tests/test_phase7_agent_loop.py` (chỉ kiểm tra code đã xoá; hành vi chọn tool vẫn được `test_phase8` kiểm tra).

**Lỗi có sẵn đã sửa:**
- `stream_voice_response` lưu lịch sử / chữ hiển thị từ `text_buffer` — chỉ còn phần dư sau câu cuối → câu trả lời kết thúc bằng dấu câu KHÔNG được lưu lịch sử, chữ hiển thị là của lượt trước (gốc rễ của "HUD không nhớ" ở Phase 65).
- Kết quả lượt (chữ hiển thị, suy nghĩ) ghi trên singleton `llm_engine` → nhiều phiên song song ghi đè nhau; nay trả theo từng lượt (`turn`).
- `can_synthesize_direct_response` coi mọi kết quả tool thành công là lỗi.
- Hàng đợi TTS không có timeout mỗi câu → một câu treo giữ mọi câu sau.
- Mảnh chỉ có dấu câu ("!", "--") bị đưa đi tổng hợp giọng → TTS lỗi.

**Kênh được thêm năng lực (hành vi mới có chủ đích):** HUD, ESP32, mic có lệnh nhanh và TTS gối đầu; câu dài được rút gọn khi ĐỌC ở mọi kênh (chữ hiển thị đầy đủ); portal hiển thị chữ gốc (Markdown) và đọc bản đã làm sạch.

**Test:** 185 pass / 0 fail. Mới: `test_voice_turn.py`, `test_tool_policy_gate.py`; viết lại `test_realtime_voice_llm_turn.py` (giả lập ở client OpenAI, đi qua code thật của `stream_voice_response`).

**Chạy thử thật (server đang chạy):** HUD qua `/ws/hud`: lệnh nhanh "mấy giờ rồi" trả lời đúng giờ có tiếng; câu hỏi LLM có lời đệm sau ~1 s, đọc đủ các câu, `voice_state` cuối lượt đúng. Portal qua `/ws/v1/voice-stream`:

| Chỉ số (3 lượt) | Phase 2 | Phase 3 |
|---|---|---|
| Fast path: chữ đầu | p50 53 ms | p50 11 ms |
| Fast path: tiếng đầu | p50 1.172 ms | p50 1.243 ms |
| LLM: chữ đầu | p50 22.405 ms | p50 2.484 ms — **không phải TTFT thật**, xem dưới |

**Phát hiện cho Phase 5 (chưa sửa):**
1. Model `ag/gemini-3.6-flash-low` trả về thông báo của nhà cung cấp *"Gemini 3.5 Flash is no longer available…"* như một câu trả lời bình thường — hệ thống đọc to nó. Cần cập nhật model trong cấu hình và phát hiện kiểu phản hồi này.
2. Sau Phase 3, `core/llm_provider.py` + `LLMEngine.stream/stream_tokens/get_provider` KHÔNG còn caller production (chỉ test). Mọi lời gọi LLM thật dùng client OpenAI thô trong `llm_engine` với vòng fallback riêng. Phase 5: chuyển `stream_voice_response` và `_call_llm*` lên provider (không xoá provider — kiến trúc đích cần nó).
3. Bộ tách câu thứ ba `LLMEngine._extract_sentences` (Phase 0 bỏ sót) cắt sai số thập phân ("3.5" → "3. 5"); gộp vào `SentenceBuffer`.

## 9. Báo cáo Phase 5 — LLM: một provider, nhớ model hỏng (2026-10-02)

(Làm trước Phase 4 theo đề xuất: model hỏng và provider không ai dùng ảnh hưởng trực tiếp tới chất lượng trả lời.)

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Gọi LLM không stream (vòng agent) | `_call_llm` → `_call_llm_direct` / `_call_llm_router` (vòng thử model riêng) | `_call_llm` → `provider.complete()` |
| Stream cho voice | `stream_voice_response` tự mở stream + vòng thử model riêng (client OpenAI thô) | `provider.stream()` |
| Ủy quyền chuyên gia | `ai_delegation`: vòng thử model async + vòng thứ hai bằng client đồng bộ | `NineRouterLLMProvider.complete(timeout=180)`; bỏ vòng đồng bộ |
| Tách câu cho giọng đọc | `SentenceBuffer` + `LLMEngine._extract_sentences` (regex thô, cắt "3.5" thành "3. 5") | `SentenceBuffer(min_words=8, max_words=30)` — ranh giới an toàn + chính sách nghe tự nhiên chuyển từ `_extract_sentences` |
| Model đã trả lời (route_info) | `self._last_successful_model` (trạng thái trên singleton) | `response.model` |

Đã xoá: `_call_llm_direct`, `_call_llm_router`, `_extract_sentences`, vòng thử model đồng bộ trong `delegate_to_specialist`.

**Mới trong provider (`core/llm_provider.py`):** model lỗi / quá hạn bị xếp cuối danh sách 120 s (dùng chung mọi phiên); model chạy được thì gỡ khỏi danh sách hỏng; giá trị mẫu `YOUR_*_HERE` bị bỏ qua; mọi model đều "đang hỏng" thì vẫn thử lại hết (không khoá cứng). `complete()` nhận `timeout` và `extra_body` (mặc định giữ 8 s, tắt thinking).

**Đo thật (portal, 9Router, 3 lượt liên tiếp ngay sau khởi động):** chữ đầu tiên 20,5 s → 5,5 s → 3,6 s (trước Phase 5: ~22 s mọi lượt). Lượt 1 phát hiện 4 model timeout; lượt 3 đi thẳng tới model chạy được. Dịch vụ 9Router chập chờn (một model chạy ở lượt 1 rồi timeout ở lượt 2), nên lượt đầu sau khởi động / sau 120 s vẫn có thể chậm.

**Test:** 191 pass / 0 fail. Mới: `test_llm_provider_health.py`, `test_ai_delegation_provider.py`. RULE-011 (client LLM ngoài provider) 19 → 12.

**Còn lại (chưa làm, có lý do):**
- `meta_architect` (3) và `analytics_engine` (2) tự tạo client OpenAI **đồng bộ** trong hàm đồng bộ được gọi ngay trên event loop — chuyển sang provider async cần quyết định chạy ở thread nào; làm cùng đợt Skills/Tools.
- `server.py` (3): nút "thử kết nối" kiểm tra endpoint/model/khoá DO NGƯỜI DÙNG NHẬP — cố ý không qua provider đã cấu hình; giữ.
- `llm_engine` (3): nơi tạo client cho chính provider; đúng vai trò.
- Chưa phát hiện được phản hồi kiểu "model X is no longer available" mà nhà cung cấp trả như câu trả lời bình thường — cần cập nhật model trong cấu hình.

## 10. Báo cáo Phase 6 — Skills/Tools: một danh mục tool, một client agent (2026-10-02)

**PHASE:** 6 — Skills/Tools
**STATUS:** XONG (đã kiểm tra runtime)

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| Danh mục tool LLM thấy | `plugin_manager` + 4 danh sách viết tay trong `llm_engine` (6/7 tool trùng) + schema nạp từ `plugin_registry` | chỉ `plugin_manager` (@export_skill / `skills/registry.json`) |
| `plugin_registry` | danh mục thứ hai + `select_relevant_skills` (không caller) | chỉ chính sách thực thi (timeout, circuit breaker, risk_level → HITL) qua `run_tool_with_policy` |
| Tool không tìm thấy | cổng gọi thẳng hàm Python bằng tên (vượt danh mục) | bị từ chối như tool lạ |
| Phê duyệt "Đồng ý" | `asyncio.to_thread(execute_skill)` trên hàm async → tác vụ đã duyệt **không bao giờ chạy** | `run_tool_with_policy(..., confirmed=True)` — có RBAC + audit |
| Client agent | `client_agent/` + bản fork `client_template/` (zip download lấy từ fork) | chỉ `client_agent/`; zip bỏ `__pycache__`, log, `config.json`, cert cũ |

**Đã xoá:** `client_template/` (13 file), `core/connectors/smart_comm_router.py` (không tham chiếu), `PluginRegistry.select_relevant_skills`, đăng ký hỏng trong `core/agents/agent_orchestrator.py`, đăng ký lúc import trong `computer_use_plugin`, nhánh native/registry trong `dynamic_skill_router`.
**Đã đăng ký (thay vì xoá):** `query_organization_data` (A4), `get_enterprise_executive_summary`.

**Test:** 192 pass / 0 fail. Mới: `test_confirm_pending_action.py` (fail trên code cũ). Cập nhật test phase 3/4/6/65/67/85/87/92, `test_tool_policy_gate`, `test_hud_*` theo danh mục mới.
**Runtime:** tải `/api/v1/download-agent` (200, 18 file, có README/requirements, không pycache) → giải nén → chạy `agent.py` → kết nối WSS và xuất hiện trong `/api/v1/clients` (DESKTOP-M1875L9), nạp 25 kỹ năng.
**Hiệu năng:** không đo — thay đổi không nằm trên đường nóng voice ngoài việc bớt dựng danh sách tool mỗi lượt.

**Rủi ro:** tool trước đây chỉ có trong danh sách viết tay mà không có skill tương ứng sẽ biến mất khỏi LLM — đã đối chiếu 7/7 tool (6 trùng, 1 đăng ký mới). Connector Phase 59 phải tự `@export_skill` để LLM thấy.

**Còn lại:** D4 (bản server của kỹ năng máy con), C6 `meta_architect`/`analytics_engine`, E2 alias `/ws/audio-stream`.

## 11. Báo cáo Phase Security — một HITL, một kho audit, đóng các lối vào không xác thực (2026-10-02)

**PHASE:** Security (sau Skills/Tools theo thứ tự Voice → LLM → Skills → Security)
**STATUS:** XONG phần dưới; còn lại ghi ở cuối mục

**Một implementation:**

| Việc | Trước | Sau |
|---|---|---|
| HITL | `zero_trust.HumanInTheLoopManager` + `security/hitl_manager.HITLManager` (Phase 60) | chỉ `zero_trust.hitl_manager`. `HITLManager` **không có nơi nào tạo request** ngoài test → hàng đợi luôn rỗng; đã xoá cùng vòng dọn dẹp, nhánh Telegram, counter `p60_pending` trên dashboard |
| Kho audit | bảng `audit_logs` (bất biến) + file `logs/security_audit.log` (API `DELETE` xoá sạch được) | chỉ `audit_logs`. `security_engine.log_audit` ghi DB (sự kiện gốc giữ trong payload), portal + `StateManager` đọc từ DB. `DELETE /api/v1/security/audit-logs` và nút "Làm Sạch Log" đã gỡ (→ 405). File cũ để nguyên, không xoá, không còn ghi |

**Lỗi bảo mật đã sửa (đều có test fail trên code cũ):**
1. `POST /api/v1/voice-command`: RBAC theo `source_device` do client tự khai — manager gửi `"hud"` là thành admin. Nay theo user đã đăng nhập.
2. `/ws/hud` chưa đăng nhập vẫn gửi `voice_command`, chạy dưới danh tính `"hud"` = admin. Nay bị từ chối (`auth_required`); HUD đã đăng nhập chạy theo username (quyết định của chủ dự án).
3. 13 endpoint nằm trong danh sách public của middleware, gồm `computer-use/dispatch` (điều khiển GUI máy chủ), `computer-use/screenshot`, topology save/reset/trigger, `departments/save`, `cross-report`, `ephemeral-cache/flush`. Nay chỉ còn public: login, assistant-name, health-dashboard, worknodes/heartbeat. Portal (`apiFetch`) và admin (`authFetch` mới trong `admin/lib/api.ts`) gửi JWT.
4. `ask_async` tra pending action theo `source_device` trong khi cổng tool lưu theo `caller` → trên portal, "Đồng ý" không bao giờ tìm thấy tác vụ. Nay dùng cùng khoá.

**Test:** 212 pass / 0 fail. Mới: `test_hud_requires_login.py`, `test_audit_single_store.py`, `test_public_endpoints_locked.py`, 2 test thêm trong `test_confirm_pending_action.py`. Sửa: phase60 (bỏ test của manager đã xoá), phase61 mjs (bỏ counter P60), phase88 (gửi token).
**Runtime (server thật):** endpoint khoá: không token 401 / có token 200; `DELETE audit-logs` 405; HUD không token bị từ chối, có token chạy đủ lượt (listening → speaking); `/admin/topology` 200 và chunk topology gửi Bearer.
**Hiệu năng:** không đo. `log_audit` đổi từ append file sang INSERT SQLite (đồng bộ, như trước).

**Rủi ro / còn lại:**
- `POST /api/v1/worknodes/heartbeat` vẫn public, và phản hồi giao task cho node bất kỳ — ai giả làm worker là nhận được task. Cần danh tính cho worker daemon.
- `security_guard._resolve_role` vẫn cấp admin theo **tiền tố** id (`esp32*`, `xiaozhi*`, `telegram*`, `hud*`, `robot*`; f389bbe). Sau bản sửa này, các đường đã biết đều đi theo user hoặc thiết bị đã xác thực; nhưng kênh mới nào truyền id do client tự đặt thì sẽ lại thành admin.
- Hai mô hình role (portal admin/manager/viewer ↔ RBAC admin/it_support/operator/viewer qua `PORTAL_ROLE_MAP`) chưa gộp: cần đổi role trong DB, là quyết định sản phẩm.
- `/ws/topology` và các WebSocket khác chưa được rà soát trong đợt này.
- Health endpoints `/livez` `/readyz` `/startupz` chưa làm.

## 12. Báo cáo Security (tiếp) — danh tính worker, health probes (2026-10-02)

**STATUS:** XONG

- **Heartbeat worker:** `POST /api/v1/worknodes/heartbeat` không còn public. Chỉ nhận enrollment secret của worker hoặc JWT admin/manager — cùng hàm `_is_valid_worker_token` với `/ws/client` (tách từ `_authenticate_worker`, không có cơ chế thứ hai). JWT người dùng thường KHÔNG giả được worker. `workers/remote_worker_daemon.py` gửi `VNMATE_ENROLLMENT_TOKEN` (giá trị `enrollment_token` trong gói tải agent).
- **Probes:** `/livez` (tiến trình sống), `/startupz` (lifecycle startup chạy xong — cờ `_STARTUP_COMPLETE` đặt ở cuối `_on_startup`), `/readyz` (startup + `SELECT 1` DB có timeout 3 s + đã nạp skill; 503 kèm check hỏng). Nằm ngoài `/api/v1/`, không lộ cấu hình.

**Test:** 216 pass / 0 fail. Mới: `test_health_probes.py` (3), heartbeat trong `test_public_endpoints_locked.py` (không token / sai secret / JWT viewer → 401; secret → 200).
**Runtime:** 3 probe trả 200 trên server thật; chạy `remote_worker_daemon.py` thật: không token → 401 và node không lên grid; có token → node có trong `/api/v1/worknodes/status`.

**Còn lại (Security):** cấp admin theo tiền tố id trong `_resolve_role`; gộp hai mô hình role; rà các WebSocket còn lại (`/ws/topology`, `/ws/portal-ui`, `/ws/voice`, `/ws/audio-stream`).

## 13. Báo cáo Security (tiếp) — rà soát WebSocket (2026-10-02)

**STATUS:** XONG

| Socket | Trước | Sau |
|---|---|---|
| `/ws/portal-ui`, `/ws/client`, `/ws/audio-stream` (+ alias xiaozhi) | đã bắt buộc JWT / enrollment secret / device secret | giữ nguyên |
| `/ws/hud` | xem §11 | chỉ xem telemetry khi chưa đăng nhập |
| `/ws/voice`, `/ws/v1/voice-stream` | ẩn danh chạy dưới tên `web_user` (viewer: vẫn đọc dữ liệu tổ chức qua tool, tốn chi phí LLM) | bắt buộc JWT, đóng 1008 |
| `/ws/topology` | không xác thực: ai cũng xem luồng tool và bơm sự kiện `trigger` giả | bắt buộc JWT; admin `SystemCanvas` gửi `?token=` (`sessionToken()` trong `admin/lib/api.ts`) |

**Test:** 221 pass / 0 fail. Mới: `test_websockets_require_login.py` (5; 3 test từ chối fail trên code cũ).
**Runtime:** 4 socket: không token → HTTP 403, có token → nhận gói chào. Lượt thoại thật qua `/ws/v1/voice-stream` (bench_voice, 1 lượt mỗi loại): fast path chữ đầu 280 ms, audio đầu 3,5 s; lượt LLM chữ đầu 22 s — lượt đầu sau khởi động, khớp hành vi 9Router đã ghi ở §9, không do thay đổi này.

**Còn lại (Security):** cấp admin theo tiền tố id trong `_resolve_role`; gộp hai mô hình role.

## 14. Báo cáo Security (tiếp) — thứ tự xác định role, Telegram fail-closed (2026-10-02)

**STATUS:** XONG

- `security_guard._resolve_role`: danh tính có trong DB (nhân viên ERP / user portal) dùng role trong DB, xét **trước** service principal và tiền tố thiết bị — đúng như chú thích thiết kế gốc ("bước 1 sẽ thắng bước 0"). Trước đây tài khoản viewer tên `hudson` (tiền tố `hud`) hay `console` (service principal) được admin. Lỗi tra cứu DB → `viewer` (fail-closed), kể cả id mang tiền tố thiết bị. Quy tắc admin cho id do server gán (Telegram, ESP32/xiaozhi; f389bbe) giữ nguyên.
- Telegram: `admin_chat_ids` trống trước đây nghĩa là **ai nhắn bot cũng được** (và nhận admin qua tiền tố `telegram`); callback duyệt HITL còn bỏ qua kiểm tra khi thiếu cấu hình. Nay cả tin nhắn lẫn nút duyệt: không có trong danh sách → bỏ qua / từ chối. Chat lạ vẫn được ghi lại để admin chọn chat_id khi cấu hình. Cấu hình hiện tại có 1 chat_id nên hành vi thực tế không đổi.

**Test:** 225 pass / 0 fail. Mới: `test_role_resolution_order.py` (4; 3 fail trên code cũ).
**Runtime:** không kiểm được đường tool qua LLM — 9Router trả lời "Gemini 3.5 Flash is no longer available" (model hết hạn, đã ghi ở §9; cần cập nhật model trong cấu hình). Telegram đang `enabled: false` nên không chạy thật được.

**Sự cố trong lúc làm:** chạy tay `python tests/test_phase60_sot_bao_mat.py` (không qua pytest → không có cô lập DB của conftest) đã ghi 14 dòng `HITL_*` vào `audit_logs` thật (id 55–68). Bảng là bất biến; chưa xoá — chờ chủ dự án quyết định.

**Còn lại (Security):** gộp hai mô hình role (quyết định sản phẩm).

## 15. Báo cáo LLM (tiếp) — nhận ra model đã ngừng (2026-10-02)

**PHASE:** LLM/Agent (bổ sung, ưu tiên vì ảnh hưởng trực tiếp người dùng)
**STATUS:** XONG

**Lỗi:** 9Router báo model đã ngừng bằng một câu trả lời HTTP 200 bình thường ("Gemini 3.5 Flash is no longer available. Please switch to …"). Provider coi là thành công → câu đó được hiển thị/đọc cho người dùng và tool không bao giờ chạy (gặp thật trên server lúc kiểm tra §14).

**Sửa (`core/llm_provider.py`, một chỗ cho mọi kênh):**
- `complete()`: câu trả lời không có tool call mà khớp mẫu thông báo ngừng của nhà cung cấp → coi là model hỏng, thử model kế tiếp.
- `stream()`: đọc trước tối đa 60 ký tự đầu (hoặc tới tool call / hết stream) trên CÙNG iterator, kiểm tra, rồi phát lại nguyên vẹn các chunk đã đọc. SentenceBuffer vốn cần ~8 từ mới phát câu đầu nên không thêm trễ đáng kể (không đo riêng).
- Model đã ngừng xếp cuối 1 giờ (`MODEL_RETIRED_COOLDOWN_S`), lỗi tạm thời giữ 120 s.
- Mẫu chỉ khớp cụm của nhà cung cấp (tiếng Anh: "is no longer available/supported", "has been deprecated", "please switch to") ở 240 ký tự đầu; câu trả lời tiếng Việt bình thường không bị bắt nhầm (có test).

**Test:** 228 pass / 0 fail. Thêm 3 test trong `test_llm_provider_health.py` (fail trên code cũ).
**Runtime:** trước khi sửa: REST trả nguyên câu "Gemini 3.5 Flash is no longer available", không chạy tool. Sau khi sửa: log ghi 3 model `ag/gemini-*` bị bỏ qua vì "đã ngừng", tool `get_system_info` chạy thật và có trong `audit_logs` (người gọi `admin`); lượt đó 88 s do các model khác timeout. Sau khi tăng cooldown: 2 lượt liên tiếp 9,1 s và 6,8 s — nhưng lần chạy này 9Router không trả câu "ngừng" nào, nên không quy được mức cải thiện cho thay đổi.

**Cần làm ở cấu hình (không sửa thay chủ dự án):** danh sách model trong cấu hình còn các model `ag/gemini-3.5-*` / `ag/gemini-3-flash-agent` đã ngừng — cần thay bằng model đang chạy trong trang cấu hình LLM.
