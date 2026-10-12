#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
#
# Cài VN-MateAI trên máy chủ Linux có Docker. Chạy trong thư mục gói:   ./install.sh
#   - nạp image dựng sẵn (images/*.tar) nếu có → không cần Internet để cài;
#   - tạo .env, sinh mật khẩu ngẫu nhiên cho admin / CSDL / Redis;
#   - hỏi gateway mô hình AI (Enter để bỏ qua, nhập sau trên Portal);
#   - khởi động và chờ hệ thống sẵn sàng.
set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\033[1;36m[VN-MateAI]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[VN-MateAI] %s\033[0m\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "Chưa có Docker. Cài: https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || die "Chưa có Docker Compose v2 (lệnh 'docker compose')."
docker info >/dev/null 2>&1 || die "Không nói chuyện được với Docker (chạy bằng sudo hoặc thêm user vào nhóm docker)."

# 1. Image dựng sẵn
if ls images/*.tar >/dev/null 2>&1; then
  for t in images/*.tar; do say "Nạp image $t …"; docker load -i "$t" >/dev/null; done
fi

# 2. .env
rnd() { tr -dc 'A-Za-z0-9' </dev/urandom | head -c "${1:-24}"; }
setv() { # setv KEY VALUE — ghi / thay trong .env
  if grep -q "^$1=" .env; then sed -i "s|^$1=.*|$1=$2|" .env; else echo "$1=$2" >> .env; fi
}
getv() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*$//'; }
[ -f .env ] || { cp env.example .env; chmod 600 .env; say "Đã tạo .env"; }
for k in VNMATE_ADMIN_PASSWORD VNMATE_MANAGER_PASSWORD VNMATE_VIEWER_PASSWORD VNMATE_PG_PASSWORD VNMATE_REDIS_PASSWORD; do
  [ -n "$(getv $k)" ] || setv "$k" "$(rnd 24)"
done

if [ -z "$(getv VNMATE_LLM_BASE_URL)" ] && [ -t 0 ]; then
  say "Gateway mô hình AI (OpenAI-compatible). Enter để bỏ qua — nhập sau ở Portal → Quản Lý Trợ Lý AI."
  read -rp "  URL (vd http://10.0.0.5:20128/v1): " v; [ -z "$v" ] || setv VNMATE_LLM_BASE_URL "$v"
  if [ -n "$v" ]; then
    read -rsp "  API key: " k; echo; [ -z "$k" ] || setv VNMATE_LLM_API_KEY "$k"
    read -rp "  Model chính (vd gpt-4o-mini): " m; [ -z "$m" ] || setv VNMATE_LLM_MODEL "$m"
  fi
fi

# 3. Khởi động (có image thì không build; không có thì build từ app/ — cần Internet)
if docker image inspect "vnmateai/app:$(getv VNMATE_VERSION)" >/dev/null 2>&1; then
  docker compose up -d
else
  say "Không thấy image dựng sẵn — build từ mã nguồn (cần Internet, 10–20 phút)…"
  docker compose up -d --build
fi

# 4. Chờ sẵn sàng
port="$(getv VNMATE_HTTPS_PORT)"; port="${port:-443}"
say "Đang khởi động (lần đầu ~1–2 phút: tạo CSDL, sinh khoá, nạp model)…"
for i in $(seq 1 90); do
  if curl -fsk "https://127.0.0.1:${port}/livez" >/dev/null 2>&1; then ok=1; break; fi
  sleep 4
done
[ "${ok:-0}" = 1 ] || { docker compose logs --tail=60 app; die "Chưa thấy ứng dụng trả lời — xem nhật ký ở trên."; }

ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
say "XONG. Mở Portal: https://${ip:-<địa chỉ máy chủ>}$( [ "$port" = 443 ] || echo ":$port" )/   ·   HUD: …/hud"
say "Đăng nhập: admin / $(getv VNMATE_ADMIN_PASSWORD)    (mật khẩu nằm trong .env — đổi ngay sau lần đăng nhập đầu)"
say "Trình duyệt sẽ cảnh báo chứng chỉ tự ký: chọn 'Vẫn tiếp tục' hoặc thay chứng chỉ thật (xem README-CAI-DAT.md)."
