# TẦM NHÌN — AI Ly Ly

> Tài liệu này là **mục tiêu sản phẩm**, không phải ghi chú kỹ thuật.
> Mọi thay đổi code phải soi chiều về đây. Khi nào một thay đổi **lệch** so
> với tầm nhìn này thì phải nói ra, không được làm lặng lẽ.

## 1. Ly Ly là gì

Ly Ly **không phải** một con chatbot, không phải một dashboard để ngắm.
Ly Ly là **người quản lý công ty bằng AI** — chạy liên tục, tự quyết, có quyền.

Người dùng **ra lệnh bằng lời nói** (web / HUD / robot tích hợp), Ly Ly
nghe, hiểu, quyết định, rồi **tự thi hành** và báo lại kết quả.

## 2. Năng lực cốt lõi

| Nhóm | Năng lực | Nghĩa là gì cụ thể |
|---|---|---|
| **Quản lý** | Ly Ly điều hành toàn bộ công ty | task, nhân sự, tài chính, vận hành — không phải người dùng phải tự bấm |
| **Truy xuất** | hỏi → có đáp án, có số | tra cứu dữ liệu nội bộ lẫn ngoài doanh nghiệp |
| **Báo cáo** | sinh báo cáo khi được hỏi | số liệu thật, không bịa, nói rõ mức độ tin cậy |
| **Chỉnh sửa** | sửa/thay thế dữ liệu, cấu hình, code | có đường thẳng từ ý định người dùng tới hành động |
| **Quản lý client** | điều khiển client qua **agent** | robot, thiết bị đầu cuối, node từ xa |
| **Tích hợp ngoại vi** | gắn module bên ngoài để lấy dữ liệu | OCI, AWS, Paperless, eInvoice… và bất cứ gì thêm sau này |
| **Tự sinh skill** | tự tạo skill mới | không cần dev thêm tay cho mọi tác vụ lặp lại |
| **Tự trị** | **cao nhất** — tự hành động khi an toàn | chỉ hỏi khi thật sự rủi ro; phần còn tự lo |

## 3. Bốn yêu cầu cấp sản phẩm

### 3.1 Dễ nhân bản trên nhiều công ty / doanh nghiệp
Một bản cài đặt phải chạy được ở bất kỳ doanh nghiệp nào. Suy ra:
- Cấu hình **không hardcode** (khoá API, địa chỉ, tenant).
- Cấu hình qua biến môi trường / file secrets, có kiểm tra lúc khởi động.
- Không phụ thuộc đường dẫn máy, không phụ thuộc tên domain riêng.
- Bật/tắt tính năng theo doanh nghiệp, không phải sửa code.

### 3.2 Ra lệnh bằng web / HUD / robot tích hợp
Cùng một Ly Ly, nhiều đường vào. Suy ra:
- Logic xử lý lệnh **không được gắn cứng vào một giao diện**.
- Web và HUD là hai *client* của cùng một API, không phải hai hệ thống.

### 3.3 Phản hồi tự nhiên, không độ trễ
Đây là yêu cầu nghiêm ngặt nhất. Suy ra:
- **Mọi vòng poll phải kiểm soát được** — không nhân bản, không poll tab ẩn.
- Đường nhận lệnh không được xếp hàng sau việc vẽ giao diện.
- Mọi thao tác chậm phải bất đồng bộ hoặc có phản hồi tức thì ("đang xử lý…").
- Sửa xong phải **đo trên trình duyệt thật**, không chỉ tin test.

### 3.4 Thay đổi cấu trúc phải cẩn thận, không làm vỡ cấu trúc
Suy ra:
- Sửa **bổ sung** trước, **không xoá** ngữ nghĩa cũ trừ khi có lý do ghi rõ.
- Mỗi lần đổi cấu trúc phải kèm: kiểm tra cú pháp → chạy test → mở trình duyệt thật.
- Test phải bắt được hành vi, không chỉ bắt được hình dạng.

## 4. Nguyên tắc bất di bất dịch

1. **Trung thực về dữ liệu.** Không bịa số. Panel rỗng phải nói *tại sao* rỗng, không
   vẽ khung rỗng. Đo được gì hiện đó.
2. **Đánh dấu việc chưa làm.** Kết quả phải phân biệt rõ: đã kiểm chứng trên
   trình duyệt thật / mới chỉ qua test / **chưa thực hiện**. Không báo thành công giả.
3. **Bí mật không lên git.** Repo đích là **PUBLIC**. Khoá API, mật khẩu, token
   không bao giờ vào lịch sử commit — kể cả lịch sử của nhánh cũ.
4. **Không tự tạo DoS.** Mỗi vòng poll phải có nhịp, phải dừng khi không cần,
   phải chịu được việc hai nơi gọi cùng lúc.
5. **Sửa nhỏ, đo thật.** Ưu tiên sửa lỗi có bằng chứng đo được thay vì sửa cho
   cảm giác đã đẹp hơn.

## 5. Bảng trạng thái các tab

Cập nhật mỗi khi có thay đổi. `chưa kiểm chứng` = chưa mở trên trình duyệt thật.

| Tab | Trạng thái | Ghi chú |
|---|---|---|
| Bảng Điều Khiển | 6 monitor mới chạy thật | Phase 61 — 6/6 panel, 0 lỗi console, 399/399 test |
| Trung Tâm Chỉ Huy | đã có | bố cục `xl:grid-cols-3` **chưa kiểm chứng** (viewport 1002px) |
| Tích Hợp Hệ Thống | đã có | |
| Quản Lý AI | đã có | phụ thuộc credentials 9Router — **đang chết** |
| Kỹ Năng | đã có | |
| Thiết Bị | đã có | |
| Giọng Nói | đã có | |
| Cấu Hình | đã có | |
| Bảo Mật | đã có | |
| Tác Vụ | đã có | |
| Người Dùng | đã có | |
| Nhật Ký | đã có | |
