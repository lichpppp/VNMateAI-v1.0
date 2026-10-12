# Cổng Agent: nối MCP server / agent có sẵn của doanh nghiệp

Dành cho quản trị viên. Doanh nghiệp đã có hệ thống và agent riêng (ERP, CRM, ITSM, agent nội bộ…) nói giao thức **MCP (Model Context
Protocol)**: khai báo một lần ở VN-MateAI, mọi **công cụ** của server đó thành công cụ của AI Ly Ly — dùng được qua portal, giọng nói,
robot, Telegram — và đi qua **cùng chính sách** như công cụ có sẵn.

> **Trạng thái trung thực.** Giao thức đã kiểm thử với MCP server giả (HTTP JSON + SSE, phân trang, phiên hết hạn, stdio) —
> `tests/test_agent_gateway_mcp.py`, `tests/test_agent_gateway_ui.mjs`. **Chưa** thử với MCP server thật của hãng nào. Sau khi lưu, luôn
> bấm **Thử kết nối** và **Đồng bộ công cụ** trên hệ thống thật.

## Nối một server

Tab **Cổng Agent** → **+ Thêm MCP server**:

1. **Mã** (`erp`, `crm-noi-bo`…) và **tên**.
2. **Kiểu kết nối**
   - **HTTP** (Streamable HTTP): địa chỉ MCP (vd `https://erp.congty.vn/mcp`), header + giá trị xác thực (vd `Authorization: Bearer …`).
     Giá trị được **mã hoá** khi lưu và không bao giờ hiện lại (gửi lại mặt nạ `••••••••` = giữ khoá cũ).
   - **stdio**: máy chủ VN-MateAI **chạy một chương trình** cục bộ (vd `npx @hãng/mcp-server`). **Tắt mặc định**; bật bằng
     `"agent_gateway": {"allow_stdio": true}` trong `config.json` khi thật sự cần — chương trình chạy với quyền của máy chủ.
3. **Thời gian chờ**, **rủi ro mặc định** (L3 = cần duyệt), **mức dữ liệu** (PUBLIC … RESTRICTED, áp ABAC như công cụ khác).
4. **Thử kết nối** (không lưu, không đăng ký) → **Lưu** → **Đồng bộ công cụ**.

Khai báo nằm ở `config/mcp_servers.json` (quyền 600, không lên Git).

## Công cụ trở thành gì

- Tên: `mcp_<mã server>_<tên công cụ>` (chỉ a-z 0-9 _, tối đa 64 ký tự). Mô tả có tiền tố *[Công cụ của hệ thống ngoài «…» qua MCP]*.
- **Mức rủi ro**: admin đặt riêng > gợi ý của server (`readOnlyHint` → L1, `destructiveHint` → L4) > rủi ro mặc định của server.
  Gợi ý do server tự khai, có thể sai — xem lại bảng công cụ và đặt riêng. Hạ xuống dưới L3 (không cần duyệt) phải xác nhận.
- Bật / tắt từng công cụ. Tắt = AI không thấy.
- Lúc khởi động, công cụ đã lưu được đăng ký ngay (AI thấy từ lượt đầu), rồi máy chủ kết nối lại và đồng bộ danh sách ở nền.

## Chính sách (không có đường vòng)

- Mỗi lời gọi đi qua `tool_gate`: kill switch, từ khoá cấm, **RBAC — mặc định chỉ admin** (vai trò khác chưa có trong danh sách cho phép),
  rủi ro ≥ L3 **phải duyệt** (nhớ duyệt theo công cụ như mọi tool), audit, sổ tác vụ, kiểm chứng.
- Thực thi qua Plugin Registry: timeout, **circuit breaker** (server hỏng liên tục thì tự ngắt), số liệu gọi.
- Kết quả trả về mang `untrusted: true` kèm ghi chú: nội dung từ hệ thống ngoài có thể chứa chỉ dẫn độc hại (prompt injection) — AI
  chỉ dùng làm thông tin. Chữ dài quá 8 000 ký tự bị cắt.
- Lỗi của công cụ (`isError`) và lỗi kết nối được báo là **thất bại**, không phải thành công.

## API (`/api/v1/agent-gateway/mcp/...`)

| Phương thức | Đường dẫn | Quyền |
|---|---|---|
| GET | `servers`, `servers/{id}` | manager, admin |
| PUT / DELETE | `servers/{id}` | admin |
| POST | `test` (thử khai báo chưa lưu) | admin |
| POST | `servers/{id}/discover` | admin |
| PATCH | `servers/{id}/tools/{tool}` — `{"enabled": bool}` / `{"risk": 1–5}` | admin |

## Giới hạn hiện tại

- Chỉ dùng **công cụ** (tools). Chưa dùng resources, prompts, sampling; chưa có OAuth 2.1 tự động (dùng token đặt sẵn trong header).
- Chưa hỗ trợ giao thức HTTP+SSE kiểu cũ (2024-11-05) — chỉ Streamable HTTP.
- Tối đa `agent_gateway.max_tools_per_server` (mặc định 64) công cụ mỗi server.
- Chưa có giao thức agent-với-agent (A2A) — đang nằm trong kế hoạch tiếp theo cùng **Danh bạ Agent**.
