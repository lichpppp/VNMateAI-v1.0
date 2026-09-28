# VN-MateAI Client Agent

## Giới Thiệu
Client Agent là ứng dụng chạy nền trên các máy trạm (Worker Node) trong mạng nội bộ doanh nghiệp.
Kết nối về Master Server (VN-MateAI) thông qua WebSocket để nhận và thực thi lệnh tự động hóa từ xa.

## Cài Đặt

### Yêu Cầu
- Python 3.10+
- Hệ điều hành: Windows 10/11 (khuyên dùng), macOS, Linux

### Bước 1: Cài đặt thư viện
```bash
pip install -r requirements.txt
```

### Bước 2: Cấu hình kết nối & Mã hóa SSL/TLS
Mở file `config.json` — file này đã được điền sẵn IP máy chủ Master và URL mã hóa (HTTPS/WSS) từ lúc tải về:
```json
{
    "server_url": "https://<IP_MASTER>:443",
    "ws_url": "wss://<IP_MASTER>:443/ws/client",
    "client_id": "auto_generate_on_first_run"
}
```
> **Bảo mật End-to-End SSL/TLS (Phase 29):**
> File chứng chỉ công khai `server_cert.pem` được đóng gói tự động đi kèm. Agent tự động nạp chứng chỉ này để ghim khóa (Certificate Pinning), ngăn chặn hoàn toàn tấn công Man-in-the-Middle (MITM).

### Bước 3: Khởi chạy Agent
```bash
python agent.py
```

Hoặc với tùy chọn thủ công:
```bash
python agent.py --server wss://<IP_MASTER>:443/ws/client --id "TEN_MAY_TRAM"
```

## Tính Năng
- Kết nối WebSocket tự động với khả năng tự động kết nối lại (Exponential Backoff)
- Thực thi lệnh tự động hóa RPA từ xa: chụp màn hình, giám sát CPU/RAM, điều khiển ứng dụng
- Hỗ trợ cài đặt Skills (kỹ năng) mới từ xa không cần khởi động lại
- Zero-Trust: Mọi lệnh được kiểm tra trước khi thực thi

## Cấu Trúc Thư Mục
```
client_agent/
├── agent.py              # File chính – Khởi chạy ở đây
├── config.json           # Cấu hình kết nối (tự động điền IP Master)
├── requirements.txt      # Thư viện cần cài đặt
├── popup_ui.py           # Giao diện thông báo popup trên máy trạm
├── core/
│   ├── __init__.py
│   └── plugin_manager.py # Quản lý Skills
└── skills/               # Thư viện kỹ năng RPA
    ├── monitoring_skills.py  # Giám sát hệ thống
    ├── pc_control_skills.py  # Điều khiển máy tính
    ├── sysadmin_skills.py    # Quản trị hệ thống
    ├── excel_records_skill.py # Thao tác file Excel
    └── custom_skills.py      # Kỹ năng tùy chỉnh
```
