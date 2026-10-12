// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/hud-next.js — BẢN THỬ giao diện HUD "buồng lái AI" (2026-10-12). HUD đang dùng vẫn ở /hud (web/hud.js).
 *
 * - Lõi AI là nhân vật chính, đổi hình theo trạng thái: chờ (thở chậm, cyan) · đang nghe (hạt hút vào tâm, xanh lá) · suy nghĩ (quỹ đạo
 *   quay nhanh, mạng nơ-ron loé tín hiệu, mảnh suy nghĩ bay ra, tím) · đang nói (lõi đập theo nhịp, sóng âm tròn, hổ phách) · sự cố (đỏ).
 * - Lớp hiệu ứng: sao bay sâu, mạng nơ-ron giữa các hạt, thước độ + khung ngắm quay, sóng xung kích khi đổi trạng thái, vệ tinh trên quỹ đạo,
 *   màn khởi động. Tất cả vẽ trên MỘT canvas 2D; chuyển màu theo thời gian thật (không phụ thuộc số khung hình).
 * - "Chòm sao agent": MCP server, agent A2A / gọi vào, Dev Fleet, máy trạm (Danh bạ Agent) + máy chủ, mô hình AI, phê duyệt, robot; mỗi lần AI
 *   gọi công cụ (sự kiện topology thật) bắn một tia có đuôi lấp lánh tới đúng nút, nút gợn sóng khi nhận: tím = đang chạy, xanh = xong,
 *   vàng = chờ duyệt, đỏ = lỗi.
 * - Dữ liệu THẬT khi đã đăng nhập portal (cùng trình duyệt): /api/v1/ops/overview, /api/v1/health-dashboard, /api/v1/system/topology/events,
 *   /api/v1/agent-gateway/agents, /api/v1/llmops/budget, WebSocket /ws/hud (trạng thái thoại, suy nghĩ, câu trả lời, phiếu duyệt).
 *   Chưa đăng nhập → chế độ DEMO, số liệu mô phỏng và ghi rõ là mô phỏng. Bản thử KHÔNG phát âm thanh, KHÔNG gửi lệnh, KHÔNG duyệt phiếu.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const lerp = (a, b, t) => a + (b - a) * t;
  const clamp01 = (x) => Math.max(0, Math.min(1, x));
  const easeOutBack = (x) => { const c1 = 1.70158, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
  const TOKEN = (() => { try { return localStorage.getItem('vnmateai_token') || ''; } catch (_) { return ''; } })();

  // ── trạng thái ────────────────────────────────────────────────────────────
  const STATES = {
    idle:      { hue: 190, label: 'STANDBY',        spin: 0.12, ring: 0.25, pull: 0,    noise: 0.02, pulse: 0.03, lattice: 0.35 },
    listening: { hue: 152, label: 'ĐANG NGHE',      spin: 0.22, ring: 0.5,  pull: 0.35, noise: 0.05, pulse: 0.0,  lattice: 0.5 },
    thinking:  { hue: 272, label: 'ĐANG SUY NGHĨ',  spin: 1.1,  ring: 1.6,  pull: 0.1,  noise: 0.16, pulse: 0.02, lattice: 1.0 },
    speaking:  { hue: 36,  label: 'ĐANG TRẢ LỜI',   spin: 0.35, ring: 0.6,  pull: 0,    noise: 0.04, pulse: 0.0,  lattice: 0.55 },
    alert:     { hue: 352, label: 'SỰ CỐ',          spin: 0.6,  ring: 1.0,  pull: 0,    noise: 0.09, pulse: 0.08, lattice: 0.8 },
  };
  let state = 'idle';
  const cur = { ...STATES.idle };
  let level = 0;
  let speakUntil = 0, wordTimer = null;
  let lastManual = 0;
  const shocks = [];                       // sóng xung kích khi đổi trạng thái
  // MỘT đồng hồ cho mọi hiệu ứng: dấu thời gian của requestAnimationFrame. Trộn với performance.now() làm tiến độ âm
  // (hai đồng hồ lệch nhau) → bán kính âm → canvas ném lỗi và mọi lớp vẽ sau đó trong khung bị mất.
  let clockNow = performance.now();

  function setState(s, opts = {}) {
    if (!STATES[s]) return;
    if (s !== state) shocks.push({ t0: clockNow, hue: STATES[s].hue });
    state = s;
    document.documentElement.style.setProperty('--c', STATES[s].hue);
    if ($('state-pill').dataset.t !== STATES[s].label) { scramble($('state-pill'), STATES[s].label); glitch(); }
    document.body.classList.toggle('alert', s === 'alert');
    if (opts.query !== undefined) $('query').textContent = opts.query ? `“${opts.query}”` : '';
    if (s === 'speaking' && opts.text) speak(opts.text);
    if (s === 'listening') subtitle(opts.text || 'Em đang lắng nghe…', true);
    if (s === 'thinking') { subtitle('Em đang suy nghĩ…', true); if (opts.text) thoughts(opts.text); }
    if (s === 'alert' && opts.text) subtitle(opts.text, true);
    if (s === 'idle' && opts.text !== undefined) subtitle(opts.text || 'Sẵn sàng nhận lệnh.', true);
  }

  // chữ chạy ký tự ngẫu nhiên rồi "giải mã" dần từ trái sang phải
  const GLYPHS = '01ABCDEF#%&*<>/\\|=+ΞΣΔ';
  function scramble(el, text, ms = 520) {
    el.dataset.t = text;
    const start = performance.now();
    const step = () => {
      if (el.dataset.t !== text) return;
      // đo bằng performance.now() (cùng đồng hồ với `start`) — không dùng dấu thời gian của requestAnimationFrame
      const p = Math.max(0, Math.min(1, (performance.now() - start) / ms)), k = Math.floor(p * text.length);
      el.textContent = text.slice(0, k) + [...text.slice(k)].map((c) => (c === ' ' ? ' ' : GLYPHS[(Math.random() * GLYPHS.length) | 0])).join('');
      if (p < 1) requestAnimationFrame(step); else el.textContent = text;
    };
    requestAnimationFrame(step);
    setTimeout(() => { if (el.dataset.t === text) el.textContent = text; }, ms + 80);   // chắc chắn hiện chữ đúng khi tab bị giảm khung hình
  }
  let glitchTimer = null;
  function glitch() {
    document.body.classList.remove('glitch'); void document.body.offsetWidth; document.body.classList.add('glitch');
    clearTimeout(glitchTimer); glitchTimer = setTimeout(() => document.body.classList.remove('glitch'), 420);
  }

  // ── phụ đề kiểu điện ảnh ─────────────────────────────────────────────────
  function subtitle(text, allOn) {
    clearInterval(wordTimer);
    const words = String(text || '').split(/\s+/).filter(Boolean);
    $('subtitle').innerHTML = '<span class="who on">LY LY</span>' + words.map((w) => `<span${allOn ? ' class="on"' : ''}>${esc(w)} </span>`).join('');
  }
  function speak(text) {
    subtitle(text, false);
    const spans = [...$('subtitle').querySelectorAll('span:not(.who)')];
    let i = 0;
    const per = Math.max(140, Math.min(320, 5200 / Math.max(spans.length, 1)));
    speakUntil = clockNow + spans.length * per + 600;
    wordTimer = setInterval(() => { if (i < spans.length) spans[i++].classList.add('on'); else clearInterval(wordTimer); }, per);
  }

  // ── canvas ────────────────────────────────────────────────────────────────
  const cv = $('core'), ctx = cv.getContext('2d');
  let W = 0, H = 0, DPR = 1, CX = 0, CY = 0, R = 0;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    CX = W / 2; CY = H * 0.44; R = Math.min(W, H) * 0.185;
    buildHex();
    const c = $('center') && $('center').getBoundingClientRect();
    CENTER_HALF = c && c.width ? c.width / 2 : W * 0.25;
  }
  let CENTER_HALF = 0;
  window.addEventListener('resize', resize);

  // hạt trên mặt cầu (Fibonacci) + mạng nơ-ron giữa các hạt gần nhau
  const N = 1700, pts = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, s: Math.random() });
  }
  const PX = new Float32Array(N), PY = new Float32Array(N), PD = new Float32Array(N);
  const links = [];
  for (let k = 0; k < 720; k++) {
    const i = Math.floor(Math.random() * N); let best = -1, bd = 1e9;
    for (let j = 0; j < N; j += 1) {
      if (j === i) continue;
      const d = (pts[i].x - pts[j].x) ** 2 + (pts[i].y - pts[j].y) ** 2 + (pts[i].z - pts[j].z) ** 2;
      if (d < bd && d > 0.004) { bd = d; best = j; }
    }
    if (best >= 0) links.push([i, best, Math.random()]);
  }

  const adj = Array.from({ length: N }, () => []);
  links.forEach(([a, b], li) => { adj[a].push(li); adj[b].push(li); });

  // xung synapse (chạy dọc một nhánh), nơ-ron loé sáng, xung trên sợi thần kinh tới agent
  const synapses = [], fires = [], nerves = [];
  function fireNeuron(i, strength = 1) {
    fires.push({ i, t0: clockNow, s: strength });
    for (const li of adj[i].slice(0, 4)) synapses.push({ li, from: i, t0: clockNow + Math.random() * 120, dur: 380 + Math.random() * 260 });
  }

  // chuột: nơ-ron quanh con trỏ bừng sáng; rê lên agent → sợi sáng + thẻ thông tin; bấm lõi / agent → phát xung
  const mouse = { x: -1e4, y: -1e4, on: false };
  let hovered = null;

  // lưới tổ ong năng lượng quanh lõi (dựng lại khi đổi kích thước)
  let hexCells = [];
  function buildHex() {
    hexCells = [];
    const sz = R * 0.15, w = sz * Math.sqrt(3), h = sz * 1.5, lim = R * 3.3;
    for (let row = -30; row <= 30; row++) {
      for (let col = -40; col <= 40; col++) {
        const x = CX + col * w + (row & 1 ? w / 2 : 0), y = CY + row * h, d = Math.hypot(x - CX, (y - CY) * 1.35);
        if (d > R * 1.45 && d < lim) hexCells.push({ x, y, d, sz: sz * 0.92 });
      }
    }
  }
  let ringText = 'VN-MATEAI ◆ NEURAL CORE ◆ ';                 // vòng chữ quay quanh lõi — cập nhật từ số liệu thật
  const bolts = [];                                         // tia điện (khi nói / sự cố)

  // sao bay sâu phía sau
  const stars = Array.from({ length: 240 }, () => ({ x: (Math.random() - 0.5) * 2, y: (Math.random() - 0.5) * 2, z: Math.random() }));

  // chòm sao agent
  const FIXED = [
    { id: 'core-server', label: 'Máy chủ', kind: 'core', icon: 'S', info: ['Công cụ chạy ngay trên máy chủ VN-MateAI'] },
    { id: 'llm', label: 'Mô hình AI', kind: 'core', icon: 'AI', info: ['Bộ não ngôn ngữ — mỗi lượt hội thoại đi qua đây'] },
    { id: 'hitl', label: 'Phê duyệt', kind: 'core', icon: '✓', info: ['Hàng đợi duyệt: tác vụ rủi ro cao chờ người có thẩm quyền'] },
    { id: 'robot', label: 'Robot', kind: 'core', icon: 'R', info: ['Robot ESP32 / Xiaozhi nói chuyện trực tiếp với Ly Ly'] },
  ];
  const KIND_ICON = { mcp: 'M', a2a: 'A', inbound: 'I', devfleet: 'D', workstations: 'W' };
  const KIND_HUE = { core: 190, mcp: 200, a2a: 150, inbound: 215, devfleet: 38, workstations: 170 };
  const KIND_NAME = { core: 'Thành phần lõi', mcp: 'MCP server', a2a: 'Agent A2A (được giao việc)', inbound: 'Agent gọi vào', devfleet: 'Dev Fleet', workstations: 'Máy trạm' };
  let nodes = [];
  function setNodes(list) {
    const all = [...FIXED, ...list.map((n) => ({ ...n, icon: KIND_ICON[n.kind] || '•' }))];
    const keep = new Map(nodes.map((n) => [n.id, n]));
    nodes = all.map((n, i) => ({ ...n, a0: (i / all.length) * Math.PI * 2, glow: (keep.get(n.id) || {}).glow || 0,
      ph: Math.random() * 6.28, nextNerve: 0 }));
  }

  const beams = [], frags = [], ripples = [];
  // Sợi thần kinh lõi → agent: bezier bậc ba, hai điểm điều khiển lắc nhẹ theo thời gian (trông như sợi sống).
  function nerveCurve(n, t) {
    const a = Math.atan2(n.y - CY, n.x - CX), sx = CX + Math.cos(a) * R * 0.92, sy = CY + Math.sin(a) * R * 0.85;
    const dx = n.x - sx, dy = n.y - sy, len = Math.hypot(dx, dy) || 1, px = -dy / len, py = dx / len;
    const w1 = Math.sin(t * 0.9 + n.ph) * R * 0.32, w2 = Math.cos(t * 0.7 + n.ph * 1.3) * R * 0.24;
    return [sx, sy, sx + dx * 0.33 + px * w1, sy + dy * 0.33 + py * w1, sx + dx * 0.68 + px * w2, sy + dy * 0.68 + py * w2, n.x, n.y];
  }
  function bez(c, u) {
    const v = 1 - u;
    return [v * v * v * c[0] + 3 * v * v * u * c[2] + 3 * v * u * u * c[4] + u * u * u * c[6],
      v * v * v * c[1] + 3 * v * v * u * c[3] + 3 * v * u * u * c[5] + u * u * u * c[7]];
  }
  function beam(nodeId, status) {
    const n = nodes.find((x) => x.id === nodeId) || nodes[0];
    if (!n) return;
    const color = { ok: '#34f5a4', error: '#ff4d6d', waiting: '#ffb547', running: '#c39bff' }[status] || '#7fe9ff';
    beams.push({ n, color, t0: clockNow, dur: 1300, arrived: false });
  }
  function thoughts(text) {
    const parts = String(text).split(/[.;:\n]|, /).map((s) => s.trim()).filter((s) => s.length > 3).slice(0, 8);
    const start = Math.random() * Math.PI * 2;
    parts.forEach((p, i) => setTimeout(() => {
      frags.push({ text: p.length > 42 ? p.slice(0, 41) + '…' : p, a: start + (i / parts.length) * Math.PI * 2, d: R * 1.15, t0: clockNow, dur: 5600 });
    }, i * 600));
  }

  const NOINTRO = new URLSearchParams(location.search).has('nointro');
  let BOOT = null, t0 = null;              // lấy từ khung đầu tiên (cùng đồng hồ với `now`)

  // Một lỗi vẽ không được làm đứng cả HUD: ghi lại (data-render-error) và vẫn chạy khung sau.
  function frame(now) {
    try { draw(now); } catch (err) {
      document.body.dataset.renderError = String(err && err.message || err).slice(0, 200);
      if (!frame.warned) { frame.warned = true; console.error('[hud-next] lỗi vẽ:', err); }
    }
    // ?freeze: dừng trên một khung hoàn chỉnh sau 3 giây (chụp ảnh xem trước)
    if (!(FREEZE && BOOT !== null && now - (NOINTRO ? BOOT + 1e6 : BOOT) > 3000)) requestAnimationFrame(frame);
    else if (!frame.snap) {                            // chụp xem trước: thay canvas bằng ảnh của chính nó (ảnh chụp headless bỏ sót lớp vẽ sau)
      frame.snap = true;
      const img = new Image(); img.src = cv.toDataURL('image/png');
      img.style.cssText = 'position:fixed;inset:0;width:100%;height:100%;z-index:1'; cv.replaceWith(img);
    }
  }
  const FREEZE = new URLSearchParams(location.search).has('freeze');

  function draw(now) {
    clockNow = now;
    if (BOOT === null) { BOOT = NOINTRO ? now - 1e6 : now; t0 = now; if (!NOINTRO) shocks.push({ t0: now + 300, hue: 190 }); }
    const realDt = Math.max(0, Math.min(1, (now - t0) / 1000)), dt = Math.min(0.05, realDt); t0 = now;
    const tgt = STATES[state];
    const ease = 1 - Math.exp(-realDt * 9);
    const dh = ((tgt.hue - cur.hue + 540) % 360) - 180; cur.hue = (cur.hue + dh * ease + 360) % 360;
    for (const k of ['spin', 'ring', 'pull', 'noise', 'pulse', 'lattice']) cur[k] = lerp(cur[k], tgt[k], 1 - Math.exp(-realDt * 4));
    const speakingNow = state === 'speaking' && now < speakUntil;
    const want = speakingNow ? 0.45 + 0.55 * Math.abs(Math.sin(now / 95) * Math.sin(now / 233)) :
      state === 'listening' ? 0.25 + 0.4 * Math.abs(Math.sin(now / 170) * Math.cos(now / 410)) : 0;
    level = lerp(level, want, 1 - Math.exp(-realDt * 11));
    if (state === 'speaking' && now > speakUntil + 900 && Date.now() - lastManual > 400) setState('idle');

    const boot = clamp01((now - BOOT) / 1500), bootS = easeOutBack(boot);
    const hue = cur.hue, t = now / 1000;
    ctx.clearRect(0, 0, W, H);

    // 1. sao bay sâu
    ctx.fillStyle = `hsla(${hue},80%,80%,1)`;
    for (const s of stars) {
      s.z -= dt * (0.035 + cur.spin * 0.03); if (s.z <= 0.02) { s.z = 1; s.x = (Math.random() - 0.5) * 2; s.y = (Math.random() - 0.5) * 2; }
      const k = 0.5 / s.z, x = CX + s.x * k * W * 0.5, y = CY + s.y * k * H * 0.5;
      if (x < 0 || x > W || y < 0 || y > H) continue;
      ctx.globalAlpha = (1 - s.z) * 0.7; const sz = (1 - s.z) * 2.2; ctx.fillRect(x, y, sz, sz);
    }
    ctx.globalAlpha = 1;

    // 1b. lưới tổ ong: sóng năng lượng lan từ lõi; sóng xung kích làm từng ô loé lên
    const shockR = shocks.map((sk) => ((now - sk.t0) / 1100)).filter((p) => p >= 0 && p <= 1).map((p) => R * (0.9 + p * 2.4));
    ctx.lineWidth = 1;
    for (const c of hexCells) {
      const wave = Math.max(0, Math.sin(c.d / (R * 0.32) - t * 1.8));
      let al = 0.025 + Math.pow(wave, 6) * 0.11 * (0.6 + cur.lattice);
      for (const sr of shockR) { const dd = Math.abs(c.d - sr); if (dd < R * 0.25) al += (1 - dd / (R * 0.25)) * 0.35; }
      if (al < 0.03) continue;
      ctx.strokeStyle = `hsla(${hue},100%,65%,${al})`;
      ctx.beginPath();
      for (let k = 0; k < 6; k++) { const a = Math.PI / 3 * k; ctx.lineTo(c.x + Math.cos(a) * c.sz, c.y + Math.sin(a) * c.sz); }
      ctx.closePath(); ctx.stroke();
    }

    // 2. quầng sáng nền (bloom)
    const breathe = (1 + Math.sin(t * 1.6) * cur.pulse + level * 0.18) * bootS;
    let g = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 3.2 * breathe + 1);
    g.addColorStop(0, `hsla(${hue},95%,62%,0.30)`); g.addColorStop(0.3, `hsla(${hue},95%,50%,0.10)`); g.addColorStop(1, 'transparent');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    // 2b. bệ phát ba chiều dưới lõi: đĩa sáng, vòng quay, chùm sáng hắt lên và các lát quét chạy lên
    const PY0 = CY + R * 1.42, PRX = R * 1.05 * bootS, PRY = R * 0.2 * bootS;
    ctx.globalCompositeOperation = 'lighter';
    const cone = ctx.createLinearGradient(0, PY0, 0, CY);
    cone.addColorStop(0, `hsla(${hue},100%,65%,0.20)`); cone.addColorStop(1, `hsla(${hue},100%,65%,0)`);
    ctx.fillStyle = cone; ctx.beginPath();
    ctx.moveTo(CX - PRX, PY0); ctx.lineTo(CX - R * 0.55, CY + R * 0.2); ctx.lineTo(CX + R * 0.55, CY + R * 0.2); ctx.lineTo(CX + PRX, PY0);
    ctx.closePath(); ctx.fill();
    for (let k = 0; k < 4; k++) {                                   // lát quét chạy lên
      const q = ((t * 0.55 + k / 4) % 1), y = lerp(PY0, CY + R * 0.25, q), rx = lerp(PRX, R * 0.55, q);
      ctx.strokeStyle = `hsla(${hue},100%,75%,${(1 - q) * 0.35})`; ctx.lineWidth = 1.2;
      ctx.beginPath(); ctx.ellipse(CX, y, rx + 0.1, rx * 0.19 + 0.1, 0, 0, Math.PI * 2); ctx.stroke();
    }
    const disc = ctx.createRadialGradient(CX, PY0, 0, CX, PY0, PRX + 0.1);
    disc.addColorStop(0, `hsla(${hue},100%,80%,0.55)`); disc.addColorStop(0.5, `hsla(${hue},100%,60%,0.16)`); disc.addColorStop(1, 'transparent');
    ctx.fillStyle = disc; ctx.beginPath(); ctx.ellipse(CX, PY0, PRX + 0.1, PRY + 0.1, 0, 0, Math.PI * 2); ctx.fill();
    for (let k = 0; k < 3; k++) {
      ctx.save(); ctx.translate(CX, PY0); ctx.scale(1, PRY / Math.max(PRX, 0.1)); ctx.rotate(t * (k % 2 ? -0.6 : 0.4) * (1 + k * 0.3));
      ctx.strokeStyle = `hsla(${hue},100%,${70 + k * 8}%,${0.65 - k * 0.15})`; ctx.lineWidth = 2.4 - k * 0.6;
      ctx.setLineDash([PRX * 0.25, PRX * 0.08 + k * 6]);
      ctx.beginPath(); ctx.arc(0, 0, PRX * (1 - k * 0.22) + 0.1, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
    ctx.setLineDash([]);
    ctx.globalCompositeOperation = 'source-over';

    // 3. chòm sao (nửa sau)
    const orbA = Math.min(R * 2.4, CENTER_HALF - 40), orbB = Math.min(R * 1.0, orbA * 0.42);
    nodes.forEach((n) => {
      const a = n.a0 + t * 0.06;
      n.x = CX + Math.cos(a) * orbA * bootS; n.y = CY + Math.sin(a) * orbB * bootS; n.z = Math.sin(a); n.glow = Math.max(0, (n.glow || 0) - dt * 0.7);
    });
    ctx.strokeStyle = `hsla(${hue},90%,60%,0.12)`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.ellipse(CX, CY, orbA * bootS + 0.1, orbB * bootS + 0.1, 0, 0, Math.PI * 2); ctx.stroke();
    nodes.filter((n) => n.z < 0).forEach(drawNode);

    // 3b. sợi thần kinh lõi → từng agent (+ nối các agent kề nhau); xung tín hiệu chạy dọc sợi
    ctx.globalCompositeOperation = 'lighter';
    nodes.forEach((n, i) => {
      const m = nodes[(i + 1) % nodes.length];
      if (nodes.length > 2) {
        ctx.strokeStyle = `hsla(${hue},80%,65%,0.07)`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.quadraticCurveTo((n.x + m.x) / 2, (n.y + m.y) / 2 + (n.y + m.y > 2 * CY ? 18 : -18), m.x, m.y); ctx.stroke();
      }
      const c = nerveCurve(n, t), hot = n === hovered ? 1 : n.glow;
      const nh = KIND_HUE[n.kind] ?? 190;
      // hai lớp: quầng rộng mờ + lõi mảnh sáng — đủ thấy trên nền tối mà vẫn thanh
      const path = () => { ctx.beginPath(); ctx.moveTo(c[0], c[1]); ctx.bezierCurveTo(c[2], c[3], c[4], c[5], c[6], c[7]); };
      const glow = ctx.createLinearGradient(c[0], c[1], c[6], c[7]);
      glow.addColorStop(0, `hsla(${hue},100%,60%,${0.16 + hot * 0.3})`); glow.addColorStop(1, `hsla(${nh},100%,60%,${0.10 + hot * 0.3})`);
      ctx.strokeStyle = glow; ctx.lineWidth = 6 + hot * 6; path(); ctx.stroke();
      const core = ctx.createLinearGradient(c[0], c[1], c[6], c[7]);
      core.addColorStop(0, `hsla(${hue},100%,80%,${0.75 + hot * 0.25})`); core.addColorStop(1, `hsla(${nh},100%,72%,${0.45 + hot * 0.55})`);
      ctx.strokeStyle = core; ctx.lineWidth = 1.3 + hot * 1.4; path(); ctx.stroke();
      if (now > n.nextNerve) {                         // xung đều đặn: lõi "hỏi thăm" agent và agent "trả lời"
        nerves.push({ n, t0: now, dur: 1500 + Math.random() * 900, back: Math.random() < 0.45 });
        n.nextNerve = now + 1800 + Math.random() * (state === 'thinking' ? 1200 : 4200);
      }
    });
    for (let i = nerves.length - 1; i >= 0; i--) {
      const p = nerves[i], q = (now - p.t0) / p.dur;
      if (q > 1) { nerves.splice(i, 1); continue; }
      if (q < 0) continue;
      const c = nerveCurve(p.n, t);
      for (let k = 0; k < 6; k++) {
        const u = p.back ? 1 - (q - k * 0.022) : q - k * 0.022; if (u < 0 || u > 1) continue;
        const [x, y] = bez(c, u); ctx.fillStyle = `hsla(${hue},100%,${80 - k * 6}%,${(1 - k / 6) * 0.9})`;
        ctx.beginPath(); ctx.arc(x, y, Math.max(0.4, 2.6 - k * 0.35), 0, Math.PI * 2); ctx.fill();
      }
    }
    ctx.globalCompositeOperation = 'source-over';

    // 4. thước độ quay + khung ngắm
    const RS = R * 1.86 * bootS;
    ctx.save(); ctx.translate(CX, CY); ctx.rotate(-t * 0.05);
    ctx.strokeStyle = `hsla(${hue},90%,70%,0.35)`; ctx.fillStyle = `hsla(${hue},90%,75%,0.55)`;
    ctx.font = `${Math.max(9, R * 0.06)}px 'Share Tech Mono', monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    for (let d = 0; d < 360; d += 5) {
      const a = d * Math.PI / 180, long = d % 30 === 0, r1 = RS, r2 = RS + (long ? 9 : 4);
      ctx.lineWidth = long ? 1.4 : 0.8; ctx.beginPath(); ctx.moveTo(Math.cos(a) * r1, Math.sin(a) * r1 * 0.98); ctx.lineTo(Math.cos(a) * r2, Math.sin(a) * r2 * 0.98); ctx.stroke();
      if (long && d % 90 === 0) ctx.fillText(String(d).padStart(3, '0') + '°', Math.cos(a) * (RS + 22), Math.sin(a) * (RS + 22));
    }
    ctx.restore();
    ctx.save(); ctx.translate(CX, CY); ctx.rotate(t * 0.22 * (0.5 + cur.ring));
    ctx.strokeStyle = `hsla(${hue},100%,70%,0.85)`; ctx.lineWidth = 2.4; ctx.shadowColor = `hsl(${hue},100%,60%)`; ctx.shadowBlur = 12;
    for (let q = 0; q < 4; q++) {
      const a = q * Math.PI / 2 + Math.PI / 4;
      ctx.beginPath(); ctx.arc(0, 0, RS * 1.09, a - 0.17, a + 0.17); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(Math.cos(a) * RS * 1.14, Math.sin(a) * RS * 1.14); ctx.lineTo(Math.cos(a) * RS * 1.2, Math.sin(a) * RS * 1.2); ctx.stroke();
    }
    ctx.restore(); ctx.shadowBlur = 0;

    // 4b. vòng chữ dữ liệu (số liệu thật) quay chậm quanh lõi
    {
      const rr = R * 1.64 * bootS, txt = ringText.repeat(Math.max(1, Math.ceil((2 * Math.PI * rr) / (ringText.length * 7.4))));
      const n = txt.length, a0 = -t * 0.08;
      ctx.font = `${Math.max(10, R * 0.062)}px 'Share Tech Mono', monospace`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      for (let i = 0; i < n; i++) {
        const a = a0 + (i / n) * Math.PI * 2, x = CX + Math.cos(a) * rr, y = CY + Math.sin(a) * rr * 0.98;
        const front = 0.35 + 0.65 * (0.5 + 0.5 * Math.sin(a + Math.PI / 2));
        ctx.save(); ctx.translate(x, y); ctx.rotate(a + Math.PI / 2);
        ctx.fillStyle = `hsla(${hue},100%,78%,${0.22 + front * 0.5})`; ctx.fillText(txt[i], 0, 0); ctx.restore();
      }
      ctx.textBaseline = 'alphabetic';
    }

    // 5. vòng quỹ đạo + vệ tinh
    for (let i = 0; i < 3; i++) {
      const rr = R * (1.32 + i * 0.2) * bootS, sp = (i % 2 ? -1 : 1) * t * cur.ring * (0.6 + i * 0.25), ry = rr * 0.86;
      ctx.save(); ctx.translate(CX, CY); ctx.rotate(sp);
      ctx.strokeStyle = `hsla(${hue},95%,${60 + i * 6}%,${0.55 - i * 0.13})`; ctx.lineWidth = i === 0 ? 2 : 1.2;
      ctx.setLineDash(i === 1 ? [3, 9] : [R * 0.45, R * 0.18]);
      ctx.beginPath(); ctx.ellipse(0, 0, rr + 0.1, ry + 0.1, 0, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      const sa = t * (1.2 + i * 0.5) * (i % 2 ? -1 : 1);
      ctx.fillStyle = '#fff'; ctx.shadowColor = `hsl(${hue},100%,65%)`; ctx.shadowBlur = 14;
      ctx.beginPath(); ctx.arc(Math.cos(sa) * rr, Math.sin(sa) * ry, 2.6, 0, Math.PI * 2); ctx.fill(); ctx.shadowBlur = 0;
      ctx.restore();
    }

    // 6. sóng âm tròn
    const bars = 120, base = R * 1.12;
    for (let i = 0; i < bars; i++) {
      const a = (i / bars) * Math.PI * 2;
      const amp = level * R * 0.42 * (0.35 + 0.65 * Math.abs(Math.sin(i * 0.37 + t * 7) * Math.cos(i * 0.11 - t * 3)));
      if (amp < 0.6) continue;
      const x1 = CX + Math.cos(a) * base, y1 = CY + Math.sin(a) * base;
      ctx.strokeStyle = `hsla(${hue + Math.sin(i) * 12},100%,${62 + amp / R * 30}%,0.85)`; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 + Math.cos(a) * amp, y1 + Math.sin(a) * amp); ctx.stroke();
    }

    // 7. quả cầu hạt (tính toạ độ chiếu, dùng lại cho mạng nơ-ron)
    const rot = t * cur.spin, tilt = 0.42, cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
    const rad = R * (1 - cur.pull * 0.25 * (0.6 + level)) * (1 + level * 0.1 + Math.sin(t * 1.6) * cur.pulse) * bootS;
    for (let i = 0; i < N; i++) {
      const p = pts[i];
      const wob = 1 + cur.noise * Math.sin(p.s * 40 + t * 3 + p.y * 6) + (state === 'listening' ? -level * 0.12 * p.s : 0);
      let x = p.x * cr - p.z * sr, z = p.x * sr + p.z * cr, y = p.y;
      const y2 = y * ct - z * st; z = y * st + z * ct; y = y2;
      const persp = 1 / (1.9 - z * 0.6);
      PX[i] = CX + x * rad * wob * persp * 1.3; PY[i] = CY + y * rad * wob * persp * 1.3; PD[i] = (z + 1) / 2;
    }
    ctx.globalCompositeOperation = 'lighter';
    // mạng nơ-ron: nhánh mặt trước sáng theo độ sâu; gần con trỏ thì bừng sáng
    const mNear = mouse.on && Math.hypot(mouse.x - CX, mouse.y - CY) < R * 1.6;
    ctx.lineWidth = 0.9;
    for (const [a, b, ph] of links) {
      const d = Math.min(PD[a], PD[b]); if (d < 0.42) continue;
      const pulse = 0.5 + 0.5 * Math.sin(t * 3 + ph * 20);
      let al = (d - 0.42) * (0.25 + cur.lattice * 0.9) * (0.45 + pulse * 0.55);
      if (mNear) { const md = Math.hypot((PX[a] + PX[b]) / 2 - mouse.x, (PY[a] + PY[b]) / 2 - mouse.y); if (md < 90) al += (1 - md / 90) * 0.9; }
      ctx.strokeStyle = `hsla(${hue + ph * 30},100%,74%,${Math.min(1, al)})`;
      ctx.beginPath(); ctx.moveTo(PX[a], PY[a]); ctx.lineTo(PX[b], PY[b]); ctx.stroke();
    }
    // nơ-ron bắn ngẫu nhiên (khi suy nghĩ dày đặc) + gần con trỏ
    const rate = { idle: 2, listening: 4, thinking: 16, speaking: 6, alert: 9 }[state] || 2;
    for (let k = 0; k < rate * dt * 3; k++) { const i = (Math.random() * N) | 0; if (PD[i] > 0.55) fireNeuron(i, 0.8); }
    if (mNear && Math.random() < 0.6) {
      let best = -1, bd = 70 * 70;
      for (let k = 0; k < 120; k++) { const i = (Math.random() * N) | 0; const dd = (PX[i] - mouse.x) ** 2 + (PY[i] - mouse.y) ** 2; if (dd < bd && PD[i] > 0.5) { bd = dd; best = i; } }
      if (best >= 0) fireNeuron(best, 1.2);
    }
    for (let i = synapses.length - 1; i >= 0; i--) {
      const sp = synapses[i], q = (now - sp.t0) / sp.dur;
      if (q > 1) { synapses.splice(i, 1); continue; }
      if (q < 0) continue;
      const [a, b] = links[sp.li], from = sp.from === a ? a : b, to = from === a ? b : a;
      const x = lerp(PX[from], PX[to], q), y = lerp(PY[from], PY[to], q);
      ctx.strokeStyle = `hsla(${hue},100%,85%,${(1 - q) * 0.9})`; ctx.lineWidth = 1.6;
      ctx.beginPath(); ctx.moveTo(PX[from], PY[from]); ctx.lineTo(x, y); ctx.stroke();
      ctx.fillStyle = '#fff'; ctx.fillRect(x - 1.3, y - 1.3, 2.6, 2.6);
      if (q > 0.96 && !sp.chained && Math.random() < 0.35 && synapses.length < 400) { sp.chained = true; fireNeuron(to, 0.6); }   // lan truyền
    }
    for (let i = fires.length - 1; i >= 0; i--) {
      const f = fires[i], q = (now - f.t0) / 520;
      if (q > 1) { fires.splice(i, 1); continue; }
      if (q < 0) continue;
      const r = 3 + q * 9 * f.s, gx = ctx.createRadialGradient(PX[f.i], PY[f.i], 0, PX[f.i], PY[f.i], r);
      gx.addColorStop(0, `hsla(${hue},100%,92%,${(1 - q) * 0.95})`); gx.addColorStop(1, 'transparent');
      ctx.fillStyle = gx; ctx.beginPath(); ctx.arc(PX[f.i], PY[f.i], r, 0, Math.PI * 2); ctx.fill();
    }
    // tia plasma từ con trỏ tới các nơ-ron gần nhất
    if (mNear) {
      const near = [];
      for (let k = 0; k < 260; k++) { const i = (Math.random() * N) | 0; const dd = Math.hypot(PX[i] - mouse.x, PY[i] - mouse.y); if (dd < 110 && PD[i] > 0.5) near.push([dd, i]); }
      near.sort((x, y) => x[0] - y[0]).slice(0, 4).forEach(([dd, i]) => {
        ctx.strokeStyle = `hsla(${hue},100%,80%,${(1 - dd / 110) * 0.8})`; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(mouse.x, mouse.y);
        ctx.quadraticCurveTo((mouse.x + PX[i]) / 2 + (Math.random() - 0.5) * 14, (mouse.y + PY[i]) / 2 + (Math.random() - 0.5) * 14, PX[i], PY[i]); ctx.stroke();
      });
    }
    for (let i = 0; i < N; i++) {
      const depth = PD[i], p = pts[i];
      ctx.fillStyle = `hsla(${hue + p.s * 30 - 15},100%,${58 + depth * 30}%,${0.18 + depth * 0.8})`;
      const sz = 0.8 + depth * 2.0;
      ctx.fillRect(PX[i] - sz / 2, PY[i] - sz / 2, sz, sz);
    }
    const coreR = R * 0.5 * breathe + 0.1;
    const k = ctx.createRadialGradient(CX, CY, 0, CX, CY, coreR);
    k.addColorStop(0, 'rgba(255,255,255,0.95)'); k.addColorStop(0.25, `hsla(${hue},100%,70%,0.8)`); k.addColorStop(1, 'transparent');
    ctx.fillStyle = k; ctx.beginPath(); ctx.arc(CX, CY, coreR, 0, Math.PI * 2); ctx.fill();
    // tia loé chéo (lens flare)
    ctx.strokeStyle = `hsla(${hue},100%,85%,${0.25 + level * 0.3})`; ctx.lineWidth = 1;
    for (const a of [0.2, 1.77]) {
      const L = R * (1.4 + level * 0.6);
      ctx.beginPath(); ctx.moveTo(CX - Math.cos(a) * L, CY - Math.sin(a) * L * 0.2); ctx.lineTo(CX + Math.cos(a) * L, CY + Math.sin(a) * L * 0.2); ctx.stroke();
    }
    ctx.globalCompositeOperation = 'source-over';

    // 7b. tia điện phóng ra từ lõi (đang nói / sự cố)
    if ((state === 'speaking' && level > 0.5) || state === 'alert') {
      if (Math.random() < (state === 'alert' ? 0.22 : 0.14)) {
        const a = Math.random() * Math.PI * 2, pts2 = [], seg = 9;
        for (let k = 0; k <= seg; k++) {
          const r = R * (0.95 + (k / seg) * 0.55), jit = k && k < seg ? (Math.random() - 0.5) * R * 0.12 : 0;
          pts2.push([CX + Math.cos(a) * r - Math.sin(a) * jit, CY + Math.sin(a) * r + Math.cos(a) * jit]);
        }
        bolts.push({ pts: pts2, t0: now });
      }
    }
    ctx.globalCompositeOperation = 'lighter';
    for (let i = bolts.length - 1; i >= 0; i--) {
      const b = bolts[i], q = (now - b.t0) / 180;
      if (q > 1) { bolts.splice(i, 1); continue; }
      if (q < 0) continue;
      ctx.strokeStyle = `hsla(${hue},100%,88%,${1 - q})`; ctx.lineWidth = 1.6; ctx.shadowColor = `hsl(${hue},100%,65%)`; ctx.shadowBlur = 14;
      ctx.beginPath(); b.pts.forEach(([x, y], k) => (k ? ctx.lineTo(x, y) : ctx.moveTo(x, y))); ctx.stroke();
    }
    ctx.shadowBlur = 0; ctx.globalCompositeOperation = 'source-over';

    // 8. sóng xung kích
    for (let i = shocks.length - 1; i >= 0; i--) {
      const s = shocks[i], p = (now - s.t0) / 1100;
      if (p > 1) { shocks.splice(i, 1); continue; }
      if (p < 0) continue;
      ctx.strokeStyle = `hsla(${s.hue},100%,70%,${(1 - p) * 0.8})`; ctx.lineWidth = 3 * (1 - p) + 0.5;
      ctx.beginPath(); ctx.arc(CX, CY, R * (0.9 + p * 2.4), 0, Math.PI * 2); ctx.stroke();
    }

    // 9. tia gọi công cụ: đuôi lấp lánh + đầu phát sáng; tới nơi thì nút gợn sóng
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i], p = Math.max(0, (now - b.t0) / b.dur);
      if (p > 1.5) { beams.splice(i, 1); continue; }
      const nc = nerveCurve(b.n, t), at = (q) => bez(nc, q);
      const fade = Math.max(0, 1 - Math.max(0, p - 0.7) * 1.4);
      ctx.strokeStyle = b.color; ctx.globalAlpha = fade * 0.75; ctx.lineWidth = 2.2; ctx.shadowColor = b.color; ctx.shadowBlur = 16;
      ctx.beginPath(); ctx.moveTo(nc[0], nc[1]); ctx.bezierCurveTo(nc[2], nc[3], nc[4], nc[5], nc[6], nc[7]); ctx.stroke();
      const q = Math.min(1, p);
      for (let s = 0; s < 10; s++) {
        const qq = q - s * 0.025; if (qq < 0) break;
        const [sx, sy] = at(qq); ctx.globalAlpha = fade * (1 - s / 10); ctx.fillStyle = b.color;
        ctx.beginPath(); ctx.arc(sx + (Math.random() - 0.5) * 2, sy + (Math.random() - 0.5) * 2, 2.6 - s * 0.2, 0, Math.PI * 2); ctx.fill();
      }
      const [hx, hy] = at(q); ctx.globalAlpha = fade; ctx.fillStyle = '#fff'; ctx.shadowBlur = 20;
      ctx.beginPath(); ctx.arc(hx, hy, 3.6, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
      if (p >= 1 && !b.arrived) { b.arrived = true; b.n.glow = 1; b.n.hit = b.color; ripples.push({ n: b.n, color: b.color, t0: now }); }
    }
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i], p = Math.max(0, (now - r.t0) / 900);
      if (p > 1) { ripples.splice(i, 1); continue; }
      ctx.strokeStyle = r.color; ctx.globalAlpha = 1 - p; ctx.lineWidth = 2;
      hexPath(r.n.x, r.n.y, 10 + p * 26); ctx.stroke(); ctx.globalAlpha = 1;
    }

    // 10. chòm sao (nửa trước)
    nodes.filter((n) => n.z >= 0).forEach(drawNode);

    // 11. mảnh suy nghĩ
    ctx.font = `500 ${Math.max(12, R * 0.085)}px 'Be Vietnam Pro', sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    for (let i = frags.length - 1; i >= 0; i--) {
      const f = frags[i], p = (now - f.t0) / f.dur;
      if (p > 1) { frags.splice(i, 1); continue; }
      if (p < 0) continue;
      const d = f.d + p * R * 1.25, x = CX + Math.cos(f.a) * d * 1.35, y = CY + Math.sin(f.a) * d * 0.75;
      ctx.globalAlpha = Math.sin(p * Math.PI) * 0.9; ctx.fillStyle = 'hsl(272,100%,86%)'; ctx.shadowColor = 'hsl(272,100%,60%)'; ctx.shadowBlur = 10;
      ctx.fillText(f.text, x, y); ctx.shadowBlur = 0;
    }
    ctx.globalAlpha = 1;
  }

  function hexPath(x, y, r) {
    ctx.beginPath();
    for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r); }
    ctx.closePath();
  }
  function pill(x, y, w, h) {
    const r = h / 2; ctx.beginPath(); ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arc(x + w - r, y + r, r, -Math.PI / 2, Math.PI / 2);
    ctx.lineTo(x + r, y + h); ctx.arc(x + r, y + r, r, Math.PI / 2, Math.PI * 1.5); ctx.closePath();
  }
  function drawNode(n) {
    const hue = KIND_HUE[n.kind] ?? 190, front = (n.z + 1) / 2, sz = 10 + front * 6 + n.glow * 5 + (n === hovered ? 6 : 0);
    const col = n.glow > 0.05 ? n.hit : n.down ? '#ff4d6d' : `hsl(${hue},100%,66%)`;
    ctx.globalAlpha = 0.4 + front * 0.6;
    ctx.shadowColor = col; ctx.shadowBlur = 10 + n.glow * 26 + (n === hovered ? 24 : 0);
    hexPath(n.x, n.y, sz); ctx.fillStyle = n.down ? 'rgba(255,77,109,.35)' : `hsla(${hue},100%,55%,0.18)`; ctx.fill();
    ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke(); ctx.shadowBlur = 0;
    ctx.fillStyle = '#eaffff'; ctx.font = `700 ${n.icon.length > 1 ? 8 + front * 2 : 9 + front * 3}px Orbitron, sans-serif`;
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(n.icon, n.x, n.y + 0.5);
    const label = n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label;
    ctx.font = `500 ${11 + front * 1.5}px 'Be Vietnam Pro', sans-serif`;
    const w = ctx.measureText(label).width + 16, ly = n.y + sz + 7;
    pill(n.x - w / 2, ly, w, 19); ctx.fillStyle = 'rgba(2,12,24,.72)'; ctx.fill();
    ctx.strokeStyle = `hsla(${hue},90%,60%,${0.25 + n.glow * 0.6})`; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = `rgba(222,246,255,${0.55 + front * 0.45})`; ctx.fillText(label, n.x, ly + 10);
    ctx.globalAlpha = 1; ctx.textBaseline = 'alphabetic';
  }

  // ── bảng số liệu ─────────────────────────────────────────────────────────
  function countTo(el, target, suffix = '') {
    const from = Number(el.dataset.v || 0), to = Number(target), t0 = performance.now();
    el.dataset.v = to;
    if (!Number.isFinite(to)) { el.textContent = target; return; }
    const step = (now) => {
      const p = clamp01((now - t0) / 700), v = from + (to - from) * (1 - Math.pow(1 - p, 3));
      el.textContent = (Number.isInteger(to) ? Math.round(v).toLocaleString('vi-VN') : v.toFixed(1)) + suffix;
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  function tile(id, value, cls, suffix = '') {
    const el = $(id), v = el.querySelector('.v');
    el.className = `tile ${value === 0 ? 'zero' : cls}`;
    if (v.dataset.v === String(value)) return;
    if (value === '–' || value == null) { v.textContent = '–'; v.dataset.v = ''; return; }
    countTo(v, value, suffix);
    const panel = el.closest('.glass'); panel.classList.remove('flash'); void panel.offsetWidth; panel.classList.add('flash');
  }
  function gauge(id, pct, label) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0)), r = 30, c = 2 * Math.PI * r;
    const col = p > 85 ? 'var(--bad)' : p > 70 ? 'var(--warn)' : 'var(--accent)';
    const ticks = Array.from({ length: 24 }, (_, i) => { const a = (i / 24) * Math.PI * 2 - Math.PI / 2;
      return `<line x1="${40 + Math.cos(a) * 36}" y1="${40 + Math.sin(a) * 36}" x2="${40 + Math.cos(a) * 38.5}" y2="${40 + Math.sin(a) * 38.5}" stroke="rgba(255,255,255,${i / 24 <= p / 100 ? '.55' : '.12'})" stroke-width="1"/>`; }).join('');
    $(id).innerHTML = `<svg viewBox="0 0 80 80">${ticks}<circle cx="40" cy="40" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="6"/>
      <circle cx="40" cy="40" r="${r}" fill="none" stroke="${col}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${c * p / 100} ${c}"
        transform="rotate(-90 40 40)" style="filter:drop-shadow(0 0 6px ${col});transition:stroke-dasharray .8s"/>
      <text x="40" y="45" text-anchor="middle" fill="#fff" font-family="Orbitron" font-size="15" font-weight="700">${Math.round(p)}%</text></svg><div class="k">${esc(label)}</div>`;
  }
  function spark(series) {
    const c = $('spark'), x = c.getContext('2d'), w = c.width = c.clientWidth * DPR, h = c.height = c.clientHeight * DPR;
    if (!series || series.length < 2) return;
    const max = Math.max(20, ...series);
    const accent = `hsl(${Math.round(cur.hue)},95%,62%)`;
    const grd = x.createLinearGradient(0, 0, 0, h); grd.addColorStop(0, `hsla(${Math.round(cur.hue)},95%,62%,.35)`); grd.addColorStop(1, 'transparent');
    const ptsS = series.map((v, i) => [(i / (series.length - 1)) * w, h - (v / max) * (h - 6 * DPR) - 3 * DPR]);
    x.beginPath(); ptsS.forEach(([px, py], i) => (i ? x.lineTo(px, py) : x.moveTo(px, py)));
    x.lineTo(w, h); x.lineTo(0, h); x.closePath(); x.fillStyle = grd; x.fill();
    x.beginPath(); ptsS.forEach(([px, py], i) => (i ? x.lineTo(px, py) : x.moveTo(px, py)));
    x.strokeStyle = accent; x.lineWidth = 2 * DPR; x.shadowColor = accent; x.shadowBlur = 8 * DPR; x.stroke();
    const [lx, ly] = ptsS[ptsS.length - 1]; x.fillStyle = '#fff'; x.beginPath(); x.arc(lx - 2 * DPR, ly, 3 * DPR, 0, Math.PI * 2); x.fill();
  }

  const fmtN = (n) => (n == null ? '–' : Number(n).toLocaleString('vi-VN'));
  const fmtMs = (v) => (v == null ? '–' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

  function applyOverview(d) {
    tile('t-incidents', d.incidents_open ?? 0, 'bad');
    tile('t-approvals', d.approvals_pending ?? 0, 'warn');
    tile('t-active', (d.tasks || {}).active ?? 0, 'info');
    const sr = (d.tasks || {}).success_rate; tile('t-success', sr == null ? '–' : Math.round(sr), 'ok', '%');
    const cost = d.cost || {};
    $('ai-tokens').textContent = fmtN(cost.total_tokens); $('ai-calls').textContent = fmtN(cost.llm_calls);
    ringData.tokens = cost.total_tokens; ringData.calls = cost.llm_calls; ringData.incidents = d.incidents_open; updateRing();
    const groups = (d.voice && d.voice.by_outcome) || {};
    let p50 = null;
    for (const name of ['llm', 'agent', ...Object.keys(groups)]) { const m = ((groups[name] || {}).metrics || {}).ttfa_answer_ms; if (m) { p50 = m.p50; break; } }
    $('ai-voice').textContent = fmtMs(p50);
    const att = (d.tasks || {}).attention || [];
    $('att-count').textContent = (d.tasks || {}).attention_total || att.length;
    $('attention').innerHTML = att.length ? att.slice(0, 6).map((a) => {
      const st = a.status === 'FAILED' ? 'error' : a.status === 'WAITING_AUTHORIZATION' ? 'waiting' : 'running';
      const lbl = { error: 'thất bại', waiting: 'chờ duyệt', running: 'cần xử lý' }[st];
      return `<li class="${st}"><time>${esc(String(a.updated_at || a.created_at || '').slice(11, 16))}</time><span class="t">${esc(a.title)}</span><span class="tag ${st}">${lbl}</span></li>`;
    }).join('') : '<li class="empty">Không có việc nào cần chú ý.</li>';
  }
  const SERVICE_NAME = { llm_9router: 'Mô hình AI (9Router)', telegram_gateway: 'Telegram', database_sqlite: 'Cơ sở dữ liệu', active_directory: 'Danh bạ nhân sự (AD)' };
  const ringData = {};
  function updateRing() {
    const parts = ['VN-MATEAI', 'NEURAL CORE', `AGENTS ${nodes.length}`];
    if (ringData.tokens != null) parts.push(`TOKENS ${Number(ringData.tokens).toLocaleString('en-US')}`);
    if (ringData.calls != null) parts.push(`LLM CALLS ${ringData.calls}`);
    if (ringData.latency != null) parts.push(`LATENCY ${Math.round(ringData.latency)} MS`);
    if (ringData.cpu != null) parts.push(`CPU ${Math.round(ringData.cpu)}%`);
    if (ringData.incidents) parts.push(`INCIDENTS ${ringData.incidents}`);
    ringText = parts.join(' ◆ ') + ' ◆ ';
  }
  function applyHealth(h) {
    const hw = h.hardware || {};
    ringData.cpu = hw.cpu_percent; ringData.latency = ((h.services || {}).llm_9router || {}).latency_ms; updateRing();
    gauge('g-cpu', hw.cpu_percent, 'CPU'); gauge('g-ram', hw.ram_percent, 'RAM'); gauge('g-disk', hw.disk_percent, 'Ổ đĩa');
    $('host-net').textContent = `↑ ${(hw.net_sent_mbps ?? 0).toFixed(2)} · ↓ ${(hw.net_recv_mbps ?? 0).toFixed(2)} MB/s`;
    spark(hw.cpu_history);
    const sv = h.services || {};
    const llm = sv.llm_9router; if (llm) $('ai-model').textContent = llm.model || '9Router';
    const rows = Object.entries(sv).map(([k, v]) => {
      const ok = String(v.status || '').toUpperCase() === 'OK';
      const extra = v.latency_ms != null ? fmtMs(v.latency_ms) : v.backend ? v.backend : ok ? 'trực tuyến' : (v.status || '').toLowerCase();
      return `<div class="svc ${ok ? 'ok' : 'bad'}"><i></i><span>${esc(SERVICE_NAME[k] || k)}</span><b>${esc(extra)}</b></div>`;
    });
    $('services').innerHTML = rows.join('') || '<div class="empty">Chưa có số liệu dịch vụ.</div>';
  }
  function applyBudget(b) {
    const daily = (b.checks || []).find((c) => c.key === 'daily_tokens') || (b.checks || [])[0];
    if (!daily) { $('ai-budget').style.width = '0'; $('ai-budget-text').textContent = `Ngân sách: chưa đặt hạn mức (chế độ ${b.mode || '—'})`; return; }
    $('ai-budget').style.width = `${Math.min(100, daily.ratio * 100)}%`;
    $('ai-budget-text').textContent = `${daily.label}: ${fmtN(Math.round(daily.used))} / ${fmtN(daily.limit)} (${Math.round(daily.ratio * 100)}%)`;
  }
  function addFeed(ev, quiet) {
    const ul = $('feed'); const empty = ul.querySelector('.empty'); if (empty) empty.remove();
    const st = ev.status === 'ok' ? 'ok' : ev.status === 'error' ? 'error' : ev.status === 'waiting' ? 'waiting' : 'running';
    const lbl = { ok: 'xong', error: 'lỗi', waiting: 'chờ duyệt', running: 'đang chạy' }[st];
    const li = document.createElement('li'); li.className = st + (quiet ? ' quiet' : '');
    li.innerHTML = `<time>${esc(String(ev.ts || '').slice(11, 19))}</time><span class="t">${esc(ev.tool)}${ev.where ? ` <small>@ ${esc(ev.where)}</small>` : ''}</span><span class="tag ${st}">${lbl}${ev.ms ? ` · ${fmtMs(ev.ms)}` : ''}</span>`;
    ul.prepend(li);
    while (ul.children.length > 14) ul.lastChild.remove();
    if (!quiet) { const panel = ul.closest('.glass'); panel.classList.remove('flash'); void panel.offsetWidth; panel.classList.add('flash'); }
  }

  function nodeFor(tool, target) {
    const t = String(tool || '');
    if (t.startsWith('mcp_')) { const n = nodes.find((x) => x.kind === 'mcp' && t.startsWith(`mcp_${x.id.replace(/-/g, '_')}_`)); if (n) return n.id; }
    if (t.startsWith('a2a_')) { const n = nodes.find((x) => x.kind === 'a2a' && `a2a_${x.id.replace(/-/g, '_')}` === t); if (n) return n.id; }
    if (String(target || '').startsWith('worker:')) return (nodes.find((x) => x.kind === 'workstations') || {}).id || 'core-server';
    return 'core-server';
  }
  function onTopology(ev, quiet) {
    if (ev.kind === 'tool') {
      const [tool, where] = String(ev.detail || '').split(/ @ |: /);
      const target = ev.stage === 'start' ? ev.target : ev.source;
      if (!quiet) beam(nodeFor(tool, target), ev.stage === 'start' ? 'running' : ev.status === 'ok' ? 'ok' : 'error');
      if (ev.stage === 'end') addFeed({ ts: ev.ts, tool, where: where && !where.includes(' ') ? where : '', status: ev.status === 'ok' ? 'ok' : 'error', ms: ev.ms }, quiet);
    } else if (ev.kind === 'approval') {
      if (!quiet) beam('hitl', ev.stage === 'request' ? 'waiting' : 'ok');
      if (ev.stage === 'request') addFeed({ ts: ev.ts, tool: String(ev.detail || 'Yêu cầu phê duyệt').split(' chờ duyệt')[0], status: 'waiting' }, quiet);
    } else if (!quiet && ev.kind === 'turn') {
      beam('llm', ev.status === 'error' ? 'error' : 'running');
    } else if (!quiet && ev.kind === 'robot') {
      beam('robot', ev.status === 'down' ? 'error' : 'ok');
    }
  }

  // ── dữ liệu thật ─────────────────────────────────────────────────────────
  async function api(path) {
    const r = await fetch(path, { headers: TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {} });
    if (r.status === 401 || r.status === 403) throw Object.assign(new Error('auth'), { auth: true });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.json();
  }
  let seq = -1, live = false;
  async function pollFast() {
    try { applyHealth(await fetch('/api/v1/health-dashboard').then((r) => r.json())); } catch (_) { /* bỏ qua */ }
    if (!live) return;
    try {
      const ev = await api(`/api/v1/system/topology/events?since=${Math.max(seq, 0)}&limit=${seq < 0 ? 60 : 100}`);
      const first = seq < 0;
      (ev.events || []).forEach((e) => { if (!e.simulated) onTopology(e, first); seq = Math.max(seq, e.seq); });  // lần đầu: điền luồng, không bắn tia
      if (first && seq < 0) seq = 0;
    } catch (e) { if (e.auth) toDemo('Phiên đăng nhập hết hạn — đăng nhập lại portal để xem dữ liệu thật.'); }
  }
  async function pollSlow() {
    if (!live) return;
    try { applyOverview(await api('/api/v1/ops/overview?hours=24')); } catch (e) { if (e.auth) return toDemo('Tài khoản không đủ quyền xem số liệu (cần manager / admin).'); }
    try { applyBudget(await api('/api/v1/llmops/budget')); } catch (_) { /* tuỳ chọn */ }
    try {
      const d = await api('/api/v1/agent-gateway/agents');
      setNodes((d.agents || []).map((a) => ({ id: a.agent_id, label: a.name, kind: a.kind, down: a.status !== 'active' || !!a.last_error,
        info: [a.status === 'active' ? 'Đang hoạt động' : 'TẠM DỪNG', a.calls ? `${a.calls.total} lời gọi · ${a.calls.failed} lỗi` : '',
          a.role ? `Quyền: ${a.role}` : '', a.tools != null ? `${a.registered}/${a.tools} công cụ` : '', a.last_seen_at ? `Lần cuối: ${a.last_seen_at}` : '',
          a.last_error ? `Lỗi: ${a.last_error}` : ''].filter(Boolean) })));
    } catch (_) { /* tuỳ chọn */ }
  }
  function connectWs() {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/hud?token=${encodeURIComponent(TOKEN)}`);
    ws.onmessage = (m) => {
      let p; try { p = JSON.parse(m.data); } catch (_) { return; }
      if (Date.now() - lastManual < 8000) return;
      if (p.type === 'voice_active') {
        const s = p.status || 'speaking';
        if (s === 'listening') setState('listening', { text: p.text });
        else if (s === 'processing' || s === 'thinking') setState('thinking', { query: p.query || p.text || '' });
        else if (s === 'speaking') setState('speaking', { text: p.text || '' });
        else if (s === 'idle') setState('idle');
      } else if (p.type === 'thinking') {
        if (p.status === 'thinking') setState('thinking');
        else if (p.status === 'done' && p.text) thoughts(p.text);
      } else if (p.type === 'display_result' && p.display_text) {
        setState('speaking', { text: String(p.display_text).replace(/[#*`>|_-]+/g, ' ').slice(0, 400), query: p.query || '' });
      } else if (p.type === 'security_approval_required') {
        showIncident('Cần phê duyệt tác vụ rủi ro cao', p.description || p.action_name || '');
      } else if (p.type === 'security_approval_resolved' || p.type === 'security_approval_rejected') {
        if (state === 'alert') setState('idle');
      }
    };
    ws.onclose = () => { if (live) setTimeout(connectWs, 4000); };
  }

  // ── DEMO ─────────────────────────────────────────────────────────────────
  const DEMO_AGENTS = [
    { id: 'erp', label: 'ERP nội bộ', kind: 'mcp', info: ['Đang hoạt động', '128 lời gọi · 2 lỗi', '14/14 công cụ'] },
    { id: 'crm-bot', label: 'Bot CRM', kind: 'inbound', info: ['Đang hoạt động', 'Quyền: operator', '57 lời gọi'] },
    { id: 'ke-toan', label: 'Agent Kế toán', kind: 'a2a', info: ['Đang hoạt động', '23 việc được giao · 1 lỗi'] },
    { id: 'dev-fleet', label: 'Dev Fleet', kind: 'devfleet', info: ['Chế độ: read_only', '4 tác vụ'] },
    { id: 'may-tram', label: 'Máy trạm (6)', kind: 'workstations', info: ['6 máy online'] },
    { id: 'itsm', label: 'ITSM GLPI', kind: 'mcp', info: ['Đang hoạt động', '41 lời gọi', '9/9 công cụ'] },
  ];
  const DEMO_TOOLS = [['mcp_erp_get_order', 'erp'], ['get_infra_status', 'core-server'], ['a2a_ke_toan', 'ke-toan'], ['create_system_ticket', 'core-server'],
    ['mcp_itsm_list_tickets', 'itsm'], ['get_system_info', 'may-tram'], ['run_playbook', 'hitl'], ['crm_lookup_customer', 'crm-bot']];
  const DEMO_TALK = [
    { q: 'Tình hình hệ thống sáng nay thế nào?', think: 'Cần xem cảnh báo hạ tầng; đối chiếu ticket đang mở; tổng hợp việc chờ duyệt',
      a: 'Dạ, hệ thống ổn định. Có một cảnh báo ổ đĩa ở máy chủ cơ sở dữ liệu, hai ticket đang mở và một kịch bản dọn ổ đĩa đang chờ anh duyệt.' },
    { q: 'Đơn hàng DH-1024 giao chưa?', think: 'Tra ERP qua MCP; kiểm tra trạng thái giao vận',
      a: 'Đơn DH-1024 đã giao lúc chín giờ mười lăm sáng nay, khách đã ký nhận.' },
  ];
  let demoTimers = [];
  const hhmmss = (d) => d.toTimeString().slice(0, 8).padStart(19, ' ');
  function demoEvent(quiet, when) {
    const [tool, node] = DEMO_TOOLS[Math.floor(Math.random() * DEMO_TOOLS.length)];
    const st = node === 'hitl' ? 'waiting' : Math.random() < 0.12 ? 'error' : 'ok';
    if (!quiet) beam(node, st);
    addFeed({ ts: hhmmss(when || new Date()), tool, status: st, ms: 200 + Math.random() * 1800 }, quiet);
  }
  function toDemo(reason) {
    live = false;
    $('mode-chip').className = 'chip demo'; $('mode-chip').textContent = 'DEMO · số liệu mô phỏng';
    $('note').textContent = reason || 'Chưa đăng nhập portal trên trình duyệt này — đang hiển thị dữ liệu MÔ PHỎNG. Đăng nhập portal rồi mở lại trang để xem dữ liệu thật.';
    setNodes(DEMO_AGENTS);
    applyOverview({ incidents_open: 1, approvals_pending: 2, tasks: { active: 5, success_rate: 96.4, attention_total: 3, attention: [
      { title: 'Ổ đĩa srv-db sắp đầy (92%)', status: 'ESCALATED', updated_at: '2026-10-12 08:12:00' },
      { title: 'Kịch bản dọn ổ đĩa srv-db', status: 'WAITING_AUTHORIZATION', updated_at: '2026-10-12 08:14:00' },
      { title: 'Đồng bộ hoá đơn điện tử', status: 'FAILED', updated_at: '2026-10-12 07:40:00' }] },
      cost: { total_tokens: 184230, llm_calls: 412 }, voice: { by_outcome: { llm: { metrics: { ttfa_answer_ms: { p50: 1850 } } } } } });
    applyBudget({ mode: 'warn', checks: [{ key: 'daily_tokens', label: 'Token hôm nay', used: 184230, limit: 500000, ratio: 0.37 }] });
    for (let i = 8; i > 0; i--) demoEvent(true, new Date(Date.now() - i * 47000));
    demoTimers.forEach(clearInterval); demoTimers = [];
    demoTimers.push(setInterval(() => demoEvent(false), 3800));
    let k = 0;
    const talk = () => {
      if (Date.now() - lastManual < 20000) return;
      const c = DEMO_TALK[k++ % DEMO_TALK.length];
      setState('listening', { query: '' });
      setTimeout(() => setState('thinking', { query: c.q, text: c.think }), 2600);
      setTimeout(() => setState('speaking', { text: c.a }), 7200);
    };
    demoTimers.push(setInterval(talk, 26000)); setTimeout(talk, 3000);
  }

  function showIncident(title, desc) {
    $('inc-title').textContent = title; $('inc-desc').textContent = desc;
    $('inc-yes').title = $('inc-no').title = 'Bản thử chỉ hiển thị — duyệt ở HUD hiện tại hoặc portal.';
    setState('alert', { text: `Phát hiện sự cố: ${title}. ${desc}`.slice(0, 220) });
  }

  // ── xem thử ───────────────────────────────────────────────────────────────
  const PREVIEW = {
    idle: () => setState('idle', { text: 'Sẵn sàng nhận lệnh.', query: '' }),
    listening: () => setState('listening', { text: 'Em đang lắng nghe anh…', query: '' }),
    thinking: () => setState('thinking', { query: 'Tổng hợp tình hình hạ tầng và việc chờ duyệt', text: 'Đọc cảnh báo Prometheus; đối chiếu ticket GLPI; kiểm tra phiếu chờ duyệt; ước lượng mức ảnh hưởng' }),
    speaking: () => setState('speaking', { text: 'Dạ, em đã kiểm tra xong. Hạ tầng ổn định, có một cảnh báo ổ đĩa ở máy chủ cơ sở dữ liệu và một kịch bản dọn dẹp đang chờ anh duyệt.' }),
    alert: () => showIncident('Ổ đĩa srv-db đầy 97%', 'Prometheus báo ổ đĩa máy chủ cơ sở dữ liệu vượt ngưỡng nghiêm trọng. Kịch bản "Dọn ổ đĩa" đã sẵn sàng, cần duyệt để chạy.'),
    tool: () => { const pick = nodes[Math.floor(Math.random() * nodes.length)]; ['running', 'ok'].forEach((s, i) => setTimeout(() => beam(pick.id, s), i * 900)); },
  };
  document.querySelectorAll('#dock button').forEach((b) => b.addEventListener('click', () => { lastManual = Date.now(); PREVIEW[b.dataset.s](); }));
  window.addEventListener('keydown', (e) => {
    const k = ['idle', 'listening', 'thinking', 'speaking', 'alert', 'tool'][Number(e.key) - 1];
    if (k && !e.target.closest('input,textarea')) { lastManual = Date.now(); PREVIEW[k](); }
  });

  const par = { x: 0, y: 0, tx: 0, ty: 0 };
  window.addEventListener('mousemove', (e) => { par.tx = (e.clientX / W) * 2 - 1; par.ty = (e.clientY / H) * 2 - 1; });
  (function parallax() {
    par.x = lerp(par.x, par.tx, 0.06); par.y = lerp(par.y, par.ty, 0.06);
    document.documentElement.style.setProperty('--px', par.x.toFixed(3)); document.documentElement.style.setProperty('--py', par.y.toFixed(3));
    requestAnimationFrame(parallax);
  })();

  function hitNode(x, y) {
    let best = null, bd = 30;
    for (const n of nodes) { const d = Math.hypot(n.x - x, n.y - y); if (d < bd) { bd = d; best = n; } }
    return best;
  }
  function showTip(n) {
    const tip = $('tip');
    if (!n) { tip.classList.remove('on'); return; }
    tip.innerHTML = `<b>${esc(n.label)}</b><small>${esc(KIND_NAME[n.kind] || n.kind)}</small>${(n.info || []).map((l) => `<div>${esc(l)}</div>`).join('')}`
      + '<em>Bấm để gửi xung tới agent này</em>';
    // đặt thẻ phía NGOÀI agent (xa lõi) để không che sợi thần kinh
    const left = n.x < CX ? n.x - 300 : n.x + 30;
    tip.style.left = `${Math.max(10, Math.min(W - 290, left))}px`; tip.style.top = `${Math.max(70, n.y - 40)}px`; tip.classList.add('on');
  }
  const toCanvas = (e) => [e.clientX + par.x * 10, e.clientY + par.y * 8];      // bù độ dịch parallax của canvas
  cv.addEventListener('mousemove', (e) => {
    const [x, y] = toCanvas(e);
    mouse.x = x; mouse.y = y; mouse.on = true;
    const n = hitNode(x, y);
    if (n !== hovered) { hovered = n; showTip(n); }
    cv.style.cursor = n || Math.hypot(x - CX, y - CY) < R ? 'pointer' : 'default';
  });
  cv.addEventListener('mouseleave', () => { mouse.on = false; hovered = null; showTip(null); });
  cv.addEventListener('click', (e) => {
    const [x, y] = toCanvas(e), n = hitNode(x, y);
    if (n) { beam(n.id, 'running'); setTimeout(() => beam(n.id, 'ok'), 700); return; }
    if (Math.hypot(x - CX, y - CY) < R * 1.1) {          // bấm lõi: xung lan toả + một loạt nơ-ron bắn
      shocks.push({ t0: clockNow, hue: Math.round(cur.hue) });
      for (let k = 0; k < 40; k++) { const i = (Math.random() * N) | 0; if (PD[i] > 0.45) fireNeuron(i, 1.4); }
      nodes.forEach((m, i) => nerves.push({ n: m, t0: clockNow + i * 60, dur: 1100, back: false }));
    }
  });

  function clock() {
    const d = new Date();
    $('clock').innerHTML = `${d.toLocaleTimeString('vi-VN', { hour12: false })}<small>${d.toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' })}</small>`;
  }

  // ── khởi động ─────────────────────────────────────────────────────────────
  resize(); setNodes([]); subtitle('Sẵn sàng nhận lệnh.', true); clock(); scramble($('state-pill'), STATES.idle.label, 900);
  setInterval(clock, 1000);
  gauge('g-cpu', 0, 'CPU'); gauge('g-ram', 0, 'RAM'); gauge('g-disk', 0, 'Ổ đĩa');
  requestAnimationFrame(frame);
  // ?nointro hoặc hệ điều hành bật "giảm chuyển động" → bỏ màn khởi động
  const noIntro = new URLSearchParams(location.search).has('nointro') || (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  if (noIntro) { document.body.classList.add('no-intro'); document.body.classList.remove('booting'); document.body.classList.add('booted'); }
  else { setTimeout(() => document.body.classList.remove('booting'), 60); setTimeout(() => document.body.classList.add('booted'), 2200); }
  if (TOKEN) {
    live = true;
    $('mode-chip').className = 'chip live'; $('mode-chip').textContent = '● LIVE · dữ liệu thật';
    $('note').textContent = 'Bản thử giao diện: chỉ hiển thị (không phát tiếng, không gửi lệnh, không duyệt). Phím 1–6 để xem thử từng trạng thái.';
    pollSlow().then(() => { if (live) connectWs(); });
    setInterval(pollSlow, 10000);
  } else {
    toDemo();
  }
  pollFast(); setInterval(pollFast, 2000);
  const qs = new URLSearchParams(location.search).get('state');
  if (qs && PREVIEW[qs]) setTimeout(() => { lastManual = Date.now() + 60000; PREVIEW[qs](); }, 1700);
  // ?hover: giả lập chuột (xem trước tương tác) — con trỏ trên lõi + rê lên một agent
  if (new URLSearchParams(location.search).has('hover')) setTimeout(() => {
    mouse.on = true; mouse.x = CX + R * 0.35; mouse.y = CY - R * 0.25;
    hovered = nodes.find((n) => n.kind !== 'core') || nodes[0]; showTip(hovered);
  }, 1900);
})();
