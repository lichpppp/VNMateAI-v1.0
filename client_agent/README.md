# VN-MateAI Client Agent (máy trạm)

Agent chạy nền trên máy trạm trong mạng nội bộ. Nó giữ một kết nối WebSocket mã hoá (WSS, ghim chứng chỉ máy chủ) về máy chủ VN-MateAI để:
- nhận lệnh tự động hoá và trả kết quả;
- gửi số đo sức khoẻ máy.

Phiên bản hiện tại: xem `AGENT_VERSION` trong `agent.py`. Máy chủ so phiên bản này với gói đang phát hành và báo trên trang `/admin/topology` máy trạm nào cần tải lại Agent.

## Cài đặt (Windows 10/11)

1. Trên Portal, bấm **Tải Agent**. File `VN-Mate_Agent.zip` đã chứa sẵn:
   - `config.json`: địa chỉ máy chủ và mã đăng ký (enrollment token);
   - `server_cert.pem`: chứng chỉ máy chủ để ghim.
2. Giải nén vào một thư mục cố định, ví dụ `C:\VNMateAgent`.
3. Cài Python 3.10 trở lên từ python.org. Khi cài, nhớ tick **Add python.exe to PATH**.
4. Bấm đúp **`install_agent.bat`**. Script sẽ:
   - tạo môi trường ảo `.venv` và cài thư viện trong `requirements.txt`;
   - đăng ký tác vụ **"VN-MateAI Agent"** trong Task Scheduler: chạy ngầm mỗi khi người dùng đăng nhập, tự khởi động lại nếu dừng bất thường;
   - khởi động Agent ngay.
5. Kiểm tra: máy trạm hiện trên trang **/admin/topology** kèm CPU, RAM, ổ đĩa và phiên bản Agent.

Agent chạy trong **phiên người dùng** (không phải dịch vụ SYSTEM), vì cần hiện popup nhắc việc / overlay và chụp màn hình phiên đang làm việc.

| Việc | Cách làm |
|---|---|
| Xem log | `logs\agent.log` (xoay vòng 3 × 2 MB) |
| Gỡ cài đặt | `powershell -ExecutionPolicy Bypass -File uninstall_agent.ps1` (thêm `-RemoveVenv` để xoá thư viện) |
| Cập nhật Agent | Tải gói mới, giải nén **đè** vào cùng thư mục, chạy lại `install_agent.bat` |
| Chạy tay để dò lỗi | `.venv\Scripts\python.exe agent.py` |

Tuỳ chọn khi chạy tay:
- `--server wss://<ip>:443/ws/client` hoặc biến môi trường `VNMATE_MASTER_URL`;
- `--id TEN_MAY` hoặc biến môi trường `VNMATE_CLIENT_ID` (mặc định là tên máy);
- `VNMATE_ENROLLMENT_TOKEN`: mã đăng ký, nếu không muốn để trong `config.json`;
- `VNMATE_AGENT_LOCK_PORT`: cổng khoá một-bản, mặc định 58431.

## Bảo mật

- **Đăng ký:** máy chủ chỉ nhận Agent có enrollment token (`/ws/client`). Token chỉ phát cho tài khoản admin/manager khi tải gói. Log của Agent che token.
- **Mã hoá:** WSS, kiểm tra chứng chỉ bắt buộc (`CERT_REQUIRED`) theo `server_cert.pem` đi kèm.
- **Lệnh nguy hiểm:** tắt tiến trình, PowerShell, dịch vụ Windows, xoá file… đều đi qua **cổng phê duyệt (HITL)** trên máy chủ trước khi xuống máy trạm, và được ghi `audit_logs`. Mỗi lần **chụp màn hình** máy trạm cũng được ghi audit.
- **Cài skill từ xa:** chỉ nhận tên file dạng `ten_skill.py` (chữ, số, `_`). Tên chứa thư mục như `../` bị từ chối.
- **Tiến trình lõi:** không tắt được tiến trình hệ thống có PID ≤ 4.
- **Một bản mỗi máy:** chạy bản thứ hai sẽ tự thoát với mã 2.

## Giao thức (Agent ↔ máy chủ)

| Chiều | `action` | Nội dung |
|---|---|---|
| Agent → máy chủ | `register` | `client_id`, `hostname`, `platform`, `ip`, `skills`, `agent_version` |
| Agent → máy chủ | `heartbeat` (mỗi 30 s) | `cpu_percent`, `ram_percent`, `disk_percent`, `uptime_s`, `skills_count`, `agent_version` |
| Máy chủ → Agent | `execute` | chạy skill → trả `result` |
| Máy chủ → Agent | `install_skill` | cài và nạp nóng skill → trả `install_result` |
| Máy chủ → Agent | `monitor` | `screen` / `processes` / `network` / `peripherals` / `security` → trả `monitor_result` |
| Máy chủ → Agent | `kill_process` | tắt tiến trình theo PID → trả `kill_result` |
| Máy chủ → Agent | `task_popup`, `show_visual` | popup nhắc việc / overlay → trả `task_response` / `visual_result` |
| Máy chủ → Agent | `ping` | trả `pong` |

Trên máy chủ:
- Mỗi task gắn với **máy trạm sở hữu**. Máy trạm khác không trả kết quả thay được.
- Máy trạm ngắt kết nối thì chỉ task của chính nó bị huỷ.
- Mất nhịp tim quá 90 s: máy trạm hiện **Lỗi** trên sơ đồ và kích hoạt cảnh báo (xem `docs/integrations/alert-channels.md`).

## Kỹ năng có sẵn (`skills/`)

| File | Kỹ năng |
|---|---|
| `pc_control_skills.py` | `get_system_info`, `list_processes`, `kill_process`, `open_application`, `search_files`, `get_clipboard`, `set_clipboard`, `get_network_info`, `set_system_volume`, `run_powershell_command` |
| `monitoring_skills.py` | `capture_screen_base64`, `get_active_processes`, `get_network_connections`, `check_peripherals`, `security_audit` |
| `sysadmin_skills.py` | `manage_windows_service`, `run_local_sql_check` |
| `file_system.py` | `list_directory`, `read_file`, `write_file`, `delete_item` |
| `visual_skills.py` | `display_visual_data` (overlay biểu đồ) |
| `excel_records_skill.py` | `append_genealogy_record` |
| `worker_health_check.py` | `worker_health_check`: CPU / RAM / ổ đĩa đo thật |
| `custom_skills.py` | `test_ping_host`: ping thật, mặc định tới máy chủ |

Thêm skill: tạo file `.py` trong `skills/`, đánh dấu hàm bằng `@export_skill(name=..., description=..., parameters_schema=...)` (từ `core.plugin_manager`). Cách khác: đẩy từ máy chủ qua `POST /api/v1/clients/{id}/deploy-skill` (chỉ admin, có audit).

## Cấu trúc

```
client_agent/
├── agent.py               # Điểm vào: kết nối, nhịp tim, điều phối lệnh
├── config.json            # (trong gói tải về) địa chỉ máy chủ + enrollment token
├── server_cert.pem        # (trong gói tải về) chứng chỉ máy chủ để ghim
├── install_agent.bat/.ps1 # Cài đặt + tự chạy khi đăng nhập
├── uninstall_agent.ps1    # Gỡ
├── requirements.txt
├── popup_ui.py            # Popup nhắc việc
├── overlay_ui.py          # Overlay biểu đồ
├── core/plugin_manager.py # Nạp / chạy skill
├── skills/                # Kỹ năng
└── logs/agent.log         # (tạo khi chạy)
```

## Chưa có (ghi nhận)

- **Bộ cài .exe / MSI không cần Python:** hiện vẫn cần Python 3.10+ trên máy trạm. Đóng gói PyInstaller và ký số cần chứng chỉ code-signing của doanh nghiệp.
- **Tự cập nhật:** Agent báo phiên bản cũ trên sơ đồ, nhưng chưa tự tải bản mới.
- **Mã đăng ký riêng từng máy:** hiện mọi máy dùng chung một enrollment token. Thu hồi một máy đồng nghĩa phải đổi token cho tất cả (xoá `certs/worker_secret.key` trên máy chủ rồi phát lại gói).
- **macOS / Linux:** Agent chạy được bằng tay, nhưng chưa có script cài tự chạy (systemd / launchd).
