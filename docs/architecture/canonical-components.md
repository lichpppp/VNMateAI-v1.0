# Thành phần canonical (Canonical Components)

> Phase 0. "Canonical" = bản mà mọi caller khác sẽ được chuyển về. Chọn theo: đang chạy trong runtime thật, có cancellation/backpressure/test tốt nhất, ít phụ thuộc ngược nhất.
> Mọi đường dẫn đều là code **đang chạy trong `core/`**. Cây `src/mateai/` không được chọn làm canonical vì chưa được nối vào runtime (xem quyết định D1).

| Năng lực | Canonical | Lý do chọn | Đối thủ sẽ gộp vào |
|---|---|---|---|
| Voice pipeline | `core/realtime_voice_ws.py` + `core/agent_voice_loop.py` | provider abstraction, barge-in, backpressure, binary transport, fast path, có test riêng | P2 HUD, P3 XiaoZhi (phần ứng dụng), P4 mic cục bộ, P5 REST |
| Giao thức voice | `/ws/v1/voice-stream` + `core/audio/binary_transport.py` | binary frame, có version trong path | `/ws/hud` (phần voice), `/ws/voice` |
| Giao thức thiết bị | `/api/v1/xiaozhi/ws` + `core/xiaozhi_gateway.py` (phần transport) | firmware trong repo dùng đường này | `/ws/audio-stream` |
| Giao thức máy trạm | `/ws/client` | duy nhất | — |
| Tách câu | `core/audio/sentence_buffer.py::SentenceBuffer` | chống tách sai số/IP/URL/viết tắt | `SentenceStreamer` (thành lớp bọc mỏng hoặc bỏ) |
| Làm sạch text TTS | `core/audio/sentence_streamer.py::sanitise_for_tts` | đang dùng bởi `SentenceBuffer` | `llm_engine._sanitise_for_tts`, `audio_processor.clean_text_for_tts` |
| TTS | `core/audio/tts_stream_engine.py::TTSStreamEngine` (+ `get_tts_engine()`) | stream + cache + chuỗi fallback | `AudioEngine` phần TTS, race gTTS trong `server.py` |
| Hàng đợi TTS | `core/audio/tts_queue_pipeline.py::StreamingTTSWorkerPipeline` | đúng thứ tự, có maxsize, hủy được | — |
| Âm đệm (ACK) | `core/audio/acoustic_ack_catalog.py` + `streaming_tts_pipeline.warmup_acoustic_ack_cache/get_acoustic_ack_audio` | — | `get_acoustic_ack_for_query` |
| Cache âm thanh | `core/audio_cache.py` | 10 caller | — |
| STT / VAD | `core/audio_processor.py::AudioEngine.transcribe_audio` + `SileroVADDetector` | duy nhất | — |
| LLM provider | `core/llm_provider.py` | interface + Direct/9Router/TriBrain | `_call_llm*`, `stream_voice_response`, 5 module tự tạo client OpenAI |
| Điều phối LLM/agent | `core/llm_engine.py::LLMEngine.stream` / `ask_async` | điểm vào chung cho các kênh | — (tách dần, xem kế hoạch) |
| HTTP pool | `core/connection_pool.py` | keep-alive, đóng khi shutdown | client tự tạo cho LLM |
| Lệnh nhanh | `core/fast_command_router.py` | đã đo latency, có test | áp dụng cho mọi kênh voice khi gộp |
| Chọn tool | `core/dynamic_skill_router.py` | `plugin_manager` đã chuyển tiếp sang đây | `plugin_registry.select_relevant_skills` (dead) |
| Nạp skill | `core/plugin_manager.py` + `skills/*.py` (`@export_skill`) | 55 importer | — |
| Thực thi tool + circuit breaker | `core/plugin_registry.py` | timeout + breaker + HITL gate | hợp nhất registry với `plugin_manager` |
| Xác thực | `core/auth_manager.py` | JWT + `require_roles` | — |
| Đánh giá rủi ro + HITL | `core/zero_trust.py` | nhiều caller nhất (11) | `safety_guard.evaluate_action_risk`, `security/hitl_manager` (cần gộp hai chiều, xem D1 legacy) |
| Che dữ liệu / kiểm tra code sinh | `core/safety_guard.py` (`mask_sensitive_data`, `inspect_generated_code`) | duy nhất | — |
| Persistence ERP + audit | `core/database.py::ERPDatabase` | 15 importer, audit INSERT-only | `db_manager` (users/tasks), sqlite riêng trong sentinel/health |
| Lịch sử hội thoại | `core/memory_manager.py` + `core/history_pruner.py` | dùng bởi P1 và `llm_engine` | `voice_sessions`, `voice_controller._history` |
| Trí nhớ dài hạn | `core/cognitive_memory.py` | duy nhất | — |
| Cấu hình | `core/config_loader.py::settings` | đã chuẩn hóa khóa trùng | 14 chỗ đọc `config.json` trực tiếp, `src/mateai/config/settings.py` |
| Máy trạm | `client_agent/` | server tự chạy bản này | `client_template/` |
| Frontend voice | `web/app.js` | dùng giao thức canonical | `web/hud.js` (phần voice) |
| Admin API client | `admin/lib/api.ts` | — | fetch trực tiếp trong 2 component |
