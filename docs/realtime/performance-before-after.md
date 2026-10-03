# Hiệu năng voice — trước / sau từng bước tối ưu

Mỗi bước đo bằng cùng một lệnh với baseline (`scripts/bench_voice.py --ws wss://localhost --ws-rounds 20 --tts-rounds 10 --concurrency 1,5,10`), cùng máy, cùng nhà cung cấp LLM/TTS. Số phía máy chủ từ `VoiceTurnTrace`. Đơn vị ms, dạng p50 / p95, n = 20 mỗi loại lượt qua WebSocket.

**Lưu ý về nhiễu:** độ trễ của nhà cung cấp LLM thay đổi đáng kể giữa hai lần chạy cách nhau vài chục phút. Đối chứng là **lệnh vận hành**, đường mà P2 không đổi (vẫn 11.333 ký tự prompt, 5 tool). Nó vẫn nhanh hơn ~30% ở lần chạy sau. Vì vậy chỉ kết luận ở những chỉ số đổi vượt mức đó, hoặc đổi theo cơ chế đo được trực tiếp (kích thước prompt, số tool, câu xác nhận).

## P2 — Voice Brain gọn + sửa định tuyến nhầm (2026-10-03)

Số gốc: `bench-2026-10-03-phase1.json` → `bench-2026-10-03-p2.json`.

### Câu cần LLM ("Giải thích ngắn gọn RAID 1 là gì trong hai câu.")

| Chỉ số | Trước | Sau |
|---|---|---|
| Kiểu lượt | vận hành (định tuyến nhầm) | trò chuyện |
| Prompt (ký tự, trung vị) | 11.298 | **3.633** (−68%) |
| Tool đưa cho model | 5 | 1 (`create_new_skill` — tài khoản bench là admin) |
| Câu xác nhận "để em kiểm tra" | có (thừa) | không |
| LLM-1st | 2.503 / 8.134 | 2.569 / 4.343 |
| TTFT | 2.948 / 8.238 | 2.634 / 4.343 |
| **TTFA-answer** | **4.531 / 10.451** | **3.938 / 6.081** |
| TTL | 5.551 / 10.454 | 4.807 / 6.633 |

### Đồng thời (trong tiến trình, cùng câu)

| Phiên | TTFA-answer trước | sau | LLM-1st trước | sau | Lỗi |
|---|---|---|---|---|---|
| 1 | 5.879 | 5.365 | 3.866 | 3.284 | 0 |
| 5 | 4.813 / 8.383 | 4.401 / 5.872 | 2.986 / 6.662 | 2.482 / 3.766 | 0 |
| 10 | 5.353 / 6.579 | 3.570 / 5.185 | 2.756 / 4.274 | 2.122 / 3.466 | 0 |

### Đối chứng (P2 không đổi các đường này)

| | Trước | Sau |
|---|---|---|
| Lệnh nhanh TTFA-answer | 53 / 1.462 | 53 / 1.600 |
| Lệnh vận hành TTFA-answer | 15.018 / 48.972 | 10.433 / 16.048 |
| Lệnh vận hành LLM-1st | 4.403 / 39.769 | 3.863 / 8.795 |
| TTS engine đoạn đầu (n=10) | 1.136 / 1.826 | 1.069 / 1.727 |

### Kết luận

- **Chắc chắn (do cơ chế):** câu hỏi kiến thức không còn bị coi là lệnh vận hành. Prompt giảm từ 11,3k xuống 3,6k ký tự, số tool từ 5 xuống 1, và không còn câu xác nhận thừa.
- **TTFA-answer p95 của câu trò chuyện 10,5 s → 6,1 s, p50 4,5 s → 3,9 s.** Một phần cải thiện p50 nằm trong nhiễu của nhà cung cấp, vì đường đối chứng cũng nhanh hơn ở lần chạy này. LLM-1st p50 gần như không đổi (2,5 s): ở p50, thời gian chờ chủ yếu là độ trễ cố định của nhà cung cấp, không phải độ dài prompt.
- **Lệnh vận hành vẫn chậm nhất** (TTFA-answer p50 10–15 s) → P3.
- **Còn lại:** `create_new_skill` vẫn được đưa cho câu hỏi kiến thức của admin, khoảng 1k ký tự schema. Gỡ nó cần quyết định, vì chủ dự án yêu cầu trợ lý chủ động tạo skill khi yêu cầu chưa có công cụ. Ghi lại để xem cùng P3.
