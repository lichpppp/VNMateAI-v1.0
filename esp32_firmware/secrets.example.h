/**
 * esp32_firmware/secrets.example.h
 * =================================
 * Template cho thông tin bí mật của firmware ESP32.
 *
 * Zero-Trust: thông tin đăng nhập WiFi KHÔNG được commit vào git.
 * (Trước đây SSID/PASS thật nằm cứng trong src/config.h và đã bị commit —
 *  bất kỳ ai đọc repo cũng nối được vào mạng của bạn.)
 *
 * ── Cách dùng ───────────────────────────────────────────────────────────────
 *   1. Copy file này thành secrets.h:
 *        cp esp32_firmware/secrets.example.h esp32_firmware/src/secrets.h
 *   2. Điền SSID/PASS thật của bạn.
 *   3. Build. File src/secrets.h đã được .git-ignore nên không lọt lên remote.
 *
 * ── Lưu ý ──────────────────────────────────────────────────────────────────
 * - Nên cấu hình WiFi qua NVS/SoftAP thay vì nhúng vào firmware, nhưng nếu dùng
 *   cách này thì TUYỆT ĐỐI không commit secrets.h.
 * - File này chỉ chứa #define, không có logic.
 */

#pragma once

// ─── WiFi Credentials (KHÔNG commit file chứa giá trị thật) ──────────────────
#define DEFAULT_WIFI_SSID "TEN_WIFI_CUA_BAN"
#define DEFAULT_WIFI_PASS "MAT_KHAU_WIFI_CUA_BAN"

// ─── Device Enrollment Token ────────────────────────────────────────────────
// Zero-Trust: master server từ chối kết nối /api/v1/xiaozhi/ws nếu thiếu token
// này (WebSocket close code 1008). Lấy giá trị từ file `certs/device_secret.key`
// trên máy chủ VN-MateAI — file đó tự sinh ở lần chạy đầu tiên.
//
// Khởi động server một lần, rồi:  cat certs/device_secret.key
#define DEFAULT_DEVICE_TOKEN ""

