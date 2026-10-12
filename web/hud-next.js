// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 Dương Thanh Lịch — VN-MateAI. See LICENSE and NOTICE.
/**
 * web/hud-next.js — BẢN THỬ giao diện HUD "buồng lái AI" (2026-10-12). HUD đang dùng vẫn ở /hud (web/hud.js).
 *
 * - Lõi AI là nhân vật chính, đổi hình theo trạng thái: chờ (thở chậm, cyan) · đang nghe (hạt hút vào tâm, xanh lá) · suy nghĩ (quỹ đạo
 *   quay nhanh, mảnh suy nghĩ bay ra, tím) · đang nói (lõi đập theo nhịp, sóng âm tròn quanh lõi, hổ phách) · sự cố (đỏ, thẻ sự cố).
 * - "Chòm sao agent": MCP server, agent A2A / gọi vào, Dev Fleet, máy trạm (Danh bạ Agent) + máy chủ, mô hình AI, phê duyệt, robot quay quanh
 *   lõi; mỗi lần AI gọi công cụ (sự kiện topology thật) bắn một tia từ lõi tới đúng nút: tím = đang chạy, xanh = xong, vàng = chờ duyệt, đỏ = lỗi.
 * - Dữ liệu THẬT khi đã đăng nhập portal (cùng trình duyệt): /api/v1/ops/overview, /api/v1/health-dashboard, /api/v1/system/topology/events,
 *   /api/v1/agent-gateway/agents, /api/v1/llmops/budget, WebSocket /ws/hud (trạng thái thoại, suy nghĩ, câu trả lời, phiếu duyệt).
 *   Chưa đăng nhập → chế độ DEMO, số liệu mô phỏng và ghi rõ là mô phỏng. Bản thử KHÔNG phát âm thanh, KHÔNG gửi lệnh, KHÔNG duyệt phiếu.
 */
(() => {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const lerp = (a, b, t) => a + (b - a) * t;
  const TOKEN = (() => { try { return localStorage.getItem('vnmateai_token') || ''; } catch (_) { return ''; } })();

  // ── trạng thái ────────────────────────────────────────────────────────────
  const STATES = {
    idle:      { hue: 190, label: 'STANDBY',        spin: 0.12, ring: 0.25, pull: 0,    noise: 0.02, pulse: 0.03 },
    listening: { hue: 152, label: 'ĐANG NGHE',      spin: 0.22, ring: 0.5,  pull: 0.35, noise: 0.05, pulse: 0.0 },
    thinking:  { hue: 272, label: 'ĐANG SUY NGHĨ',  spin: 1.1,  ring: 1.6,  pull: 0.1,  noise: 0.16, pulse: 0.02 },
    speaking:  { hue: 36,  label: 'ĐANG TRẢ LỜI',   spin: 0.35, ring: 0.6,  pull: 0,    noise: 0.04, pulse: 0.0 },
    alert:     { hue: 352, label: 'SỰ CỐ',          spin: 0.6,  ring: 1.0,  pull: 0,    noise: 0.09, pulse: 0.08 },
  };
  let state = 'idle';
  const cur = { ...STATES.idle };        // tham số đang hiển thị (nội suy dần tới trạng thái đích)
  let level = 0;                          // "âm lượng" 0..1 (nói / nghe)
  let speakUntil = 0, words = [], wordIdx = 0, wordTimer = null;
  let lastManual = 0;

  function setState(s, opts = {}) {
    if (!STATES[s]) return;
    state = s;
    document.documentElement.style.setProperty('--c', STATES[s].hue);
    $('state-pill').textContent = STATES[s].label;
    document.body.classList.toggle('alert', s === 'alert');
    if (opts.query !== undefined) $('query').textContent = opts.query ? `“${opts.query}”` : '';
    if (s === 'speaking' && opts.text) speak(opts.text);
    if (s === 'listening') subtitle(opts.text || 'Em đang lắng nghe…', true);
    if (s === 'thinking') { subtitle('Em đang suy nghĩ…', true); if (opts.text) thoughts(opts.text); }
    if (s === 'alert' && opts.text) subtitle(opts.text, true);
    if (s === 'idle' && opts.text !== undefined) subtitle(opts.text || 'Sẵn sàng nhận lệnh.', true);
  }

  // ── phụ đề kiểu điện ảnh: chữ sáng dần theo nhịp nói ─────────────────────
  function subtitle(text, allOn) {
    clearInterval(wordTimer);
    words = String(text || '').split(/\s+/).filter(Boolean);
    $('subtitle').innerHTML = '<span class="who on">LY LY</span>' + words.map((w) => `<span${allOn ? ' class="on"' : ''}>${esc(w)} </span>`).join('');
  }
  function speak(text) {
    subtitle(text, false);
    const spans = [...$('subtitle').querySelectorAll('span:not(.who)')];
    wordIdx = 0;
    const per = Math.max(140, Math.min(320, 5200 / Math.max(spans.length, 1)));
    speakUntil = performance.now() + spans.length * per + 600;
    wordTimer = setInterval(() => {
      if (wordIdx < spans.length) spans[wordIdx++].classList.add('on');
      else { clearInterval(wordTimer); }
    }, per);
  }

  // ── canvas ────────────────────────────────────────────────────────────────
  const cv = $('core'), ctx = cv.getContext('2d');
  let W = 0, H = 0, DPR = 1, CX = 0, CY = 0, R = 0;
  function resize() {
    DPR = Math.min(window.devicePixelRatio || 1, 1.5);
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * DPR; cv.height = H * DPR; cv.style.width = W + 'px'; cv.style.height = H + 'px';
    ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    CX = W / 2; CY = H * 0.47; R = Math.min(W, H) * 0.155;
  }
  window.addEventListener('resize', resize);

  // hạt trên mặt cầu (phân bố Fibonacci)
  const N = 1700, pts = [];
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), th = i * 2.399963;
    pts.push({ x: Math.cos(th) * r, y, z: Math.sin(th) * r, s: Math.random() });
  }

  // chòm sao agent
  const FIXED = [
    { id: 'core-server', label: 'Máy chủ', kind: 'core' },
    { id: 'llm', label: 'Mô hình AI', kind: 'core' },
    { id: 'hitl', label: 'Phê duyệt', kind: 'core' },
    { id: 'robot', label: 'Robot', kind: 'core' },
  ];
  let nodes = [];
  function setNodes(list) {
    const all = [...FIXED, ...list];
    const keep = new Map(nodes.map((n) => [n.id, n]));
    nodes = all.map((n, i) => ({ ...n, a0: (i / all.length) * Math.PI * 2, glow: (keep.get(n.id) || {}).glow || 0 }));
  }
  const KIND_HUE = { core: 190, mcp: 200, a2a: 150, inbound: 210, devfleet: 38, workstations: 170 };

  const beams = [];       // {node, color, t0, dur}
  const frags = [];       // mảnh suy nghĩ bay ra
  function beam(nodeId, status) {
    const n = nodes.find((x) => x.id === nodeId) || nodes[0];
    if (!n) return;
    const color = { ok: '#34f5a4', error: '#ff4d6d', waiting: '#ffb547', running: '#b18cff' }[status] || '#7fe9ff';
    beams.push({ n, color, t0: performance.now(), dur: 1400 });
    n.glow = 1; n.hit = color;
  }
  function thoughts(text) {
    const parts = String(text).split(/[.;:\n]|, /).map((s) => s.trim()).filter((s) => s.length > 3).slice(0, 8);
    const start = Math.random() * Math.PI * 2;
    parts.forEach((p, i) => setTimeout(() => {
      const a = start + (i / parts.length) * Math.PI * 2;          // rải đều quanh lõi, không chồng nhau
      frags.push({ text: p.length > 42 ? p.slice(0, 41) + '…' : p, a, d: R * 1.15, t0: performance.now(), dur: 5600 });
    }, i * 600));
  }

  let t0 = performance.now(), fps = 60;
  function frame(now) {
    const realDt = Math.min(1, (now - t0) / 1000), dt = Math.min(0.05, realDt); t0 = now;
    fps = lerp(fps, 1 / Math.max(dt, 0.001), 0.05);
    const tgt = STATES[state];
    // màu đi đường NGẮN quanh vòng màu và đổi nhanh — người nhìn nhận ra trạng thái mới trong ~0,5 giây
    // theo THỜI GIAN thật (không theo số khung hình): ~0,4 s dù máy chậm hay tab bị giảm khung hình
    const ease = 1 - Math.exp(-realDt * 9);
    const dh = ((tgt.hue - cur.hue + 540) % 360) - 180; cur.hue = (cur.hue + dh * ease + 360) % 360;
    for (const k of ['spin', 'ring', 'pull', 'noise', 'pulse']) cur[k] = lerp(cur[k], tgt[k], 1 - Math.exp(-realDt * 4));
    // âm lượng: nói → theo nhịp chữ; nghe → dao động như giọng người; khác → tắt dần
    const speakingNow = state === 'speaking' && now < speakUntil;
    const want = speakingNow ? 0.45 + 0.55 * Math.abs(Math.sin(now / 95) * Math.sin(now / 233)) :
      state === 'listening' ? 0.25 + 0.4 * Math.abs(Math.sin(now / 170) * Math.cos(now / 410)) : 0;
    level = lerp(level, want, 0.18);
    if (state === 'speaking' && now > speakUntil + 900 && Date.now() - lastManual > 400) setState('idle');

    ctx.clearRect(0, 0, W, H);
    const hue = cur.hue, t = now / 1000;
    const rot = t * cur.spin, tilt = 0.42;

    // quầng sáng nền
    const breathe = 1 + Math.sin(t * 1.6) * cur.pulse + level * 0.18;
    const g = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 2.6 * breathe);
    g.addColorStop(0, `hsla(${hue},95%,62%,0.32)`); g.addColorStop(0.35, `hsla(${hue},95%,50%,0.10)`); g.addColorStop(1, 'transparent');
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);

    // chòm sao: tính vị trí, vẽ phần phía sau lõi trước
    // quỹ đạo vừa cột giữa (không chui dưới hai cánh panel)
    const orbA = Math.min(R * 2.55, W * 0.245 - 70), orbB = Math.min(R * 0.95, orbA * 0.4);
    nodes.forEach((n) => {
      const a = n.a0 + t * 0.06;
      n.x = CX + Math.cos(a) * orbA; n.y = CY + Math.sin(a) * orbB; n.z = Math.sin(a); n.glow = Math.max(0, (n.glow || 0) - dt * 0.6);
    });
    ctx.strokeStyle = `hsla(${hue},90%,60%,0.10)`; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.ellipse(CX, CY, orbA, orbB, 0, 0, Math.PI * 2); ctx.stroke();
    nodes.filter((n) => n.z < 0).forEach(drawNode);

    // vòng quỹ đạo + vạch chia
    for (let i = 0; i < 3; i++) {
      const rr = R * (1.32 + i * 0.22), sp = (i % 2 ? -1 : 1) * t * cur.ring * (0.6 + i * 0.25);
      ctx.save(); ctx.translate(CX, CY); ctx.rotate(sp);
      ctx.strokeStyle = `hsla(${hue},95%,${60 + i * 6}%,${0.55 - i * 0.12})`; ctx.lineWidth = i === 0 ? 2 : 1.2;
      ctx.setLineDash(i === 1 ? [3, 9] : [R * 0.45, R * 0.18]);
      ctx.beginPath(); ctx.ellipse(0, 0, rr, rr * (1 - tilt * 0.35), 0, 0, Math.PI * 2); ctx.stroke();
      ctx.restore();
    }
    ctx.setLineDash([]);

    // sóng âm tròn quanh lõi
    const bars = 120, base = R * 1.12;
    for (let i = 0; i < bars; i++) {
      const a = (i / bars) * Math.PI * 2;
      const amp = level * R * 0.42 * (0.35 + 0.65 * Math.abs(Math.sin(i * 0.37 + t * 7) * Math.cos(i * 0.11 - t * 3)));
      if (amp < 0.6) continue;
      const x1 = CX + Math.cos(a) * base, y1 = CY + Math.sin(a) * base;
      ctx.strokeStyle = `hsla(${hue + Math.sin(i) * 12},100%,${62 + amp / R * 30}%,0.85)`; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x1 + Math.cos(a) * amp, y1 + Math.sin(a) * amp); ctx.stroke();
    }

    // quả cầu hạt
    const cr = Math.cos(rot), sr = Math.sin(rot), ct = Math.cos(tilt), st = Math.sin(tilt);
    const rad = R * (1 - cur.pull * 0.25 * (0.6 + level)) * (1 + level * 0.1 + Math.sin(t * 1.6) * cur.pulse);
    ctx.globalCompositeOperation = 'lighter';
    for (const p of pts) {
      const wob = 1 + cur.noise * Math.sin(p.s * 40 + t * 3 + p.y * 6) + (state === 'listening' ? -level * 0.12 * p.s : 0);
      let x = p.x * cr - p.z * sr, z = p.x * sr + p.z * cr, y = p.y;
      const y2 = y * ct - z * st; z = y * st + z * ct; y = y2;
      const persp = 1 / (1.9 - z * 0.6);
      const px = CX + x * rad * wob * persp * 1.3, py = CY + y * rad * wob * persp * 1.3;
      const depth = (z + 1) / 2;
      ctx.fillStyle = `hsla(${hue + p.s * 30 - 15},100%,${58 + depth * 30}%,${0.18 + depth * 0.8})`;
      const sz = 0.8 + depth * 2.0;
      ctx.fillRect(px - sz / 2, py - sz / 2, sz, sz);
    }
    // nhân sáng
    const k = ctx.createRadialGradient(CX, CY, 0, CX, CY, R * 0.5 * breathe);
    k.addColorStop(0, 'rgba(255,255,255,0.95)'); k.addColorStop(0.25, `hsla(${hue},100%,70%,0.8)`); k.addColorStop(1, 'transparent');
    ctx.fillStyle = k; ctx.beginPath(); ctx.arc(CX, CY, R * 0.5 * breathe, 0, Math.PI * 2); ctx.fill();
    ctx.globalCompositeOperation = 'source-over';

    // tia gọi công cụ: cong từ lõi tới nút, chấm sáng chạy dọc tia
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i], p = (now - b.t0) / b.dur;
      if (p > 1.4) { beams.splice(i, 1); continue; }
      const mx = (CX + b.n.x) / 2, my = Math.min(CY, b.n.y) - R * 0.9;
      ctx.strokeStyle = b.color; ctx.globalAlpha = Math.max(0, 1 - Math.max(0, p - 0.6) * 1.6) * 0.7; ctx.lineWidth = 1.6;
      ctx.shadowColor = b.color; ctx.shadowBlur = 14;
      ctx.beginPath(); ctx.moveTo(CX, CY); ctx.quadraticCurveTo(mx, my, b.n.x, b.n.y); ctx.stroke();
      const q = Math.min(1, p), ix = (1 - q) * (1 - q) * CX + 2 * (1 - q) * q * mx + q * q * b.n.x, iy = (1 - q) * (1 - q) * CY + 2 * (1 - q) * q * my + q * q * b.n.y;
      ctx.globalAlpha = 1; ctx.fillStyle = '#fff'; ctx.beginPath(); ctx.arc(ix, iy, 3.2, 0, Math.PI * 2); ctx.fill();
      ctx.shadowBlur = 0; ctx.globalAlpha = 1;
    }

    nodes.filter((n) => n.z >= 0).forEach(drawNode);

    // mảnh suy nghĩ
    ctx.font = `500 ${Math.max(12, R * 0.085)}px 'Be Vietnam Pro', sans-serif`; ctx.textAlign = 'center';
    for (let i = frags.length - 1; i >= 0; i--) {
      const f = frags[i], p = (now - f.t0) / f.dur;
      if (p > 1) { frags.splice(i, 1); continue; }
      const d = f.d + p * R * 1.25, x = CX + Math.cos(f.a) * d * 1.35, y = CY + Math.sin(f.a) * d * 0.75;
      ctx.globalAlpha = Math.sin(p * Math.PI) * 0.85; ctx.fillStyle = 'hsl(272,100%,84%)';
      ctx.fillText(f.text, x, y);
    }
    ctx.globalAlpha = 1;
    requestAnimationFrame(frame);
  }

  function drawNode(n) {
    const hue = KIND_HUE[n.kind] ?? 190, front = (n.z + 1) / 2, sz = 5 + front * 5 + n.glow * 6;
    ctx.globalAlpha = 0.35 + front * 0.65;
    ctx.save(); ctx.translate(n.x, n.y);
    ctx.shadowColor = n.glow > 0.05 ? n.hit : `hsl(${hue},100%,60%)`; ctx.shadowBlur = 10 + n.glow * 24;
    ctx.strokeStyle = n.glow > 0.05 ? n.hit : `hsl(${hue},100%,${n.down ? 35 : 65}%)`; ctx.lineWidth = 1.6;
    ctx.beginPath();
    for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i + Math.PI / 6; ctx.lineTo(Math.cos(a) * sz, Math.sin(a) * sz); }
    ctx.closePath(); ctx.stroke();
    ctx.fillStyle = n.down ? 'rgba(255,77,109,.6)' : `hsla(${hue},100%,60%,0.25)`; ctx.fill();
    ctx.restore(); ctx.shadowBlur = 0;
    ctx.font = `500 ${11 + front * 2}px 'Be Vietnam Pro', sans-serif`; ctx.textAlign = 'center';
    ctx.fillStyle = `rgba(214,244,255,${0.45 + front * 0.5})`;
    ctx.fillText(n.label.length > 22 ? n.label.slice(0, 21) + '…' : n.label, n.x, n.y + sz + 15);
    ctx.globalAlpha = 1;
  }

  // ── bảng số liệu ─────────────────────────────────────────────────────────
  function tile(id, value, cls) {
    const el = $(id); el.className = `tile ${value === 0 || value === '0' ? 'zero' : cls}`;
    const v = el.querySelector('.v'); if (v.textContent !== String(value)) { v.textContent = value; el.closest('.glass').classList.remove('flash'); void el.offsetWidth; el.closest('.glass').classList.add('flash'); }
  }
  function gauge(id, pct, label) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0)), r = 30, c = 2 * Math.PI * r;
    const col = p > 85 ? 'var(--bad)' : p > 70 ? 'var(--warn)' : 'var(--accent)';
    $(id).innerHTML = `<svg viewBox="0 0 80 80"><circle cx="40" cy="40" r="${r}" fill="none" stroke="rgba(255,255,255,.07)" stroke-width="6"/>
      <circle cx="40" cy="40" r="${r}" fill="none" stroke="${col}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${c * p / 100} ${c}"
        transform="rotate(-90 40 40)" style="filter:drop-shadow(0 0 6px ${col});transition:stroke-dasharray .8s"/>
      <text x="40" y="45" text-anchor="middle" fill="#fff" font-family="Orbitron" font-size="15" font-weight="700">${Math.round(p)}%</text></svg><div class="k">${esc(label)}</div>`;
  }
  function spark(series) {
    const c = $('spark'), x = c.getContext('2d'), w = c.width = c.clientWidth * DPR, h = c.height = c.clientHeight * DPR;
    if (!series || series.length < 2) return;
    const max = Math.max(20, ...series);
    x.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--accent') || '#5ff'; x.lineWidth = 2 * DPR;
    const grd = x.createLinearGradient(0, 0, 0, h); grd.addColorStop(0, 'rgba(95,233,255,.35)'); grd.addColorStop(1, 'transparent');
    x.beginPath();
    series.forEach((v, i) => { const px = (i / (series.length - 1)) * w, py = h - (v / max) * (h - 4 * DPR) - 2 * DPR; i ? x.lineTo(px, py) : x.moveTo(px, py); });
    x.stroke(); x.lineTo(w, h); x.lineTo(0, h); x.closePath(); x.fillStyle = grd; x.fill();
  }

  const fmtN = (n) => (n == null ? '–' : Number(n).toLocaleString('vi-VN'));
  const fmtMs = (v) => (v == null ? '–' : v >= 1000 ? `${(v / 1000).toFixed(1)} s` : `${Math.round(v)} ms`);

  function applyOverview(d) {
    tile('t-incidents', d.incidents_open ?? 0, 'bad');
    tile('t-approvals', d.approvals_pending ?? 0, 'warn');
    tile('t-active', (d.tasks || {}).active ?? 0, 'info');
    const sr = (d.tasks || {}).success_rate; tile('t-success', sr == null ? '–' : `${Math.round(sr)}%`, 'ok');
    const cost = d.cost || {};
    $('ai-tokens').textContent = fmtN(cost.total_tokens); $('ai-calls').textContent = fmtN(cost.llm_calls);
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
    }).join('') : '<li class="empty" style="border:0;background:none">Không có việc nào cần chú ý.</li>';
  }
  function applyHealth(h) {
    const hw = h.hardware || {};
    gauge('g-cpu', hw.cpu_percent, 'CPU'); gauge('g-ram', hw.ram_percent, 'RAM'); gauge('g-disk', hw.disk_percent, 'Ổ đĩa');
    $('host-net').textContent = `↑ ${(hw.net_sent_mbps ?? 0).toFixed(2)} · ↓ ${(hw.net_recv_mbps ?? 0).toFixed(2)} MB/s`;
    spark(hw.cpu_history);
    const llm = (h.services || {}).llm_9router; if (llm) $('ai-model').textContent = `9Router ${llm.status || ''}`;
  }
  function applyBudget(b) {
    const daily = (b.checks || []).find((c) => c.key === 'daily_tokens') || (b.checks || [])[0];
    if (!daily) { $('ai-budget').style.width = '0'; $('ai-budget-text').textContent = `Ngân sách: chưa đặt hạn mức (chế độ ${b.mode || '—'})`; return; }
    $('ai-budget').style.width = `${Math.min(100, daily.ratio * 100)}%`;
    $('ai-budget-text').textContent = `${daily.label}: ${fmtN(Math.round(daily.used))} / ${fmtN(daily.limit)} (${Math.round(daily.ratio * 100)}%)`;
  }
  function addFeed(ev) {
    const ul = $('feed'); const empty = ul.querySelector('.empty'); if (empty) empty.remove();
    const st = ev.status === 'ok' ? 'ok' : ev.status === 'error' ? 'error' : ev.status === 'waiting' ? 'waiting' : 'running';
    const lbl = { ok: 'xong', error: 'lỗi', waiting: 'chờ duyệt', running: 'đang chạy' }[st];
    const li = document.createElement('li'); li.className = st;
    li.innerHTML = `<time>${esc(String(ev.ts || '').slice(11, 19))}</time><span class="t">${esc(ev.tool)}${ev.where ? ` <small>@ ${esc(ev.where)}</small>` : ''}</span><span class="tag ${st}">${lbl}${ev.ms ? ` · ${fmtMs(ev.ms)}` : ''}</span>`;
    ul.prepend(li);
    while (ul.children.length > 12) ul.lastChild.remove();
  }

  // sự kiện topology → tia + dòng hành động
  function nodeFor(tool, target) {
    const t = String(tool || '');
    let m = t.match(/^mcp_([a-z0-9_]+?)_/); if (m) { const n = nodes.find((x) => x.kind === 'mcp' && t.startsWith(`mcp_${x.id.replace(/-/g, '_')}_`)); if (n) return n.id; }
    m = t.match(/^a2a_(.+)$/); if (m) { const n = nodes.find((x) => x.kind === 'a2a' && x.id.replace(/-/g, '_') === m[1]); if (n) return n.id; }
    if (String(target || '').startsWith('worker:')) return (nodes.find((x) => x.kind === 'workstations') || {}).id || 'core-server';
    return 'core-server';
  }
  function onTopology(ev) {
    if (ev.kind === 'tool') {
      const [tool, where] = String(ev.detail || '').split(/ @ |: /);
      const target = ev.stage === 'start' ? ev.target : ev.source;
      const id = nodeFor(tool, target);
      beam(id, ev.stage === 'start' ? 'running' : ev.status === 'ok' ? 'ok' : 'error');
      if (ev.stage === 'end') addFeed({ ts: ev.ts, tool, where: where && !where.includes(' ') ? where : '', status: ev.status === 'ok' ? 'ok' : 'error', ms: ev.ms });
    } else if (ev.kind === 'approval') {
      beam('hitl', ev.stage === 'request' ? 'waiting' : 'ok');
      if (ev.stage === 'request') addFeed({ ts: ev.ts, tool: String(ev.detail || 'Yêu cầu phê duyệt').split(' chờ duyệt')[0], status: 'waiting' });
    } else if (ev.kind === 'turn') {
      beam('llm', ev.status === 'error' ? 'error' : 'running');
    } else if (ev.kind === 'robot') {
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
  let seq = 0, live = false;
  async function pollFast() {
    try {
      const h = await fetch('/api/v1/health-dashboard').then((r) => r.json()); applyHealth(h);
    } catch (_) { /* bỏ qua */ }
    if (!live) return;
    try {
      const ev = await api(`/api/v1/system/topology/events?since=${seq}&limit=100`);
      (ev.events || []).forEach((e) => { if (seq && !e.simulated) onTopology(e); seq = Math.max(seq, e.seq); });
    } catch (e) { if (e.auth) toDemo('Phiên đăng nhập hết hạn — đăng nhập lại portal để xem dữ liệu thật.'); }
  }
  async function pollSlow() {
    if (!live) return;
    try { applyOverview(await api('/api/v1/ops/overview?hours=24')); } catch (e) { if (e.auth) return toDemo('Tài khoản không đủ quyền xem số liệu (cần manager / admin).'); }
    try { applyBudget(await api('/api/v1/llmops/budget')); } catch (_) { /* tuỳ chọn */ }
    try {
      const d = await api('/api/v1/agent-gateway/agents');
      setNodes((d.agents || []).map((a) => ({ id: a.agent_id, label: a.name, kind: a.kind, down: a.status !== 'active' || !!a.last_error })));
    } catch (_) { /* tuỳ chọn */ }
  }
  function connectWs() {
    const ws = new WebSocket(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/ws/hud?token=${encodeURIComponent(TOKEN)}`);
    ws.onmessage = (m) => {
      let p; try { p = JSON.parse(m.data); } catch (_) { return; }
      if (Date.now() - lastManual < 8000) return;            // đang xem thử bằng tay
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
        showIncident('Cần phê duyệt tác vụ rủi ro cao', p.description || p.action_name || '', false);
      } else if (p.type === 'security_approval_resolved' || p.type === 'security_approval_rejected') {
        if (state === 'alert') setState('idle');
      }
    };
    ws.onclose = () => { if (live) setTimeout(connectWs, 4000); };
  }

  // ── DEMO (chưa đăng nhập) ────────────────────────────────────────────────
  const DEMO_AGENTS = [
    { id: 'erp', label: 'ERP nội bộ', kind: 'mcp' }, { id: 'crm-bot', label: 'Bot CRM', kind: 'inbound' },
    { id: 'ke-toan', label: 'Agent Kế toán', kind: 'a2a' }, { id: 'dev-fleet', label: 'Dev Fleet', kind: 'devfleet' },
    { id: 'may-tram', label: 'Máy trạm (6)', kind: 'workstations' }, { id: 'itsm', label: 'ITSM GLPI', kind: 'mcp' },
  ];
  const DEMO_TOOLS = [['mcp_erp_get_order', 'erp'], ['get_infra_status', 'core-server'], ['a2a_ke_toan', 'ke-toan'], ['create_system_ticket', 'core-server'],
    ['mcp_itsm_list_tickets', 'itsm'], ['get_system_info', 'may-tram'], ['run_playbook', 'hitl']];
  const DEMO_TALK = [
    { q: 'Tình hình hệ thống sáng nay thế nào?', think: 'Cần xem cảnh báo hạ tầng; đối chiếu ticket đang mở; tổng hợp việc chờ duyệt',
      a: 'Dạ, hệ thống ổn định. Có một cảnh báo ổ đĩa ở máy chủ cơ sở dữ liệu, hai ticket đang mở và một kịch bản dọn ổ đĩa đang chờ anh duyệt.' },
    { q: 'Đơn hàng DH-1024 giao chưa?', think: 'Tra ERP qua MCP; kiểm tra trạng thái giao vận',
      a: 'Đơn DH-1024 đã giao lúc chín giờ mười lăm sáng nay, khách đã ký nhận.' },
  ];
  let demoTimers = [];
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
    demoTimers.forEach(clearInterval); demoTimers = [];
    demoTimers.push(setInterval(() => {
      const [tool, node] = DEMO_TOOLS[Math.floor(Math.random() * DEMO_TOOLS.length)];
      const st = node === 'hitl' ? 'waiting' : Math.random() < 0.12 ? 'error' : 'ok';
      beam(node, st); addFeed({ ts: new Date().toTimeString().slice(0, 8).padStart(19, ' '), tool, status: st, ms: 200 + Math.random() * 1800 });
    }, 3800));
    let k = 0;
    const talk = () => {
      if (Date.now() - lastManual < 20000) return;
      const c = DEMO_TALK[k++ % DEMO_TALK.length];
      setState('listening', { query: '' });
      setTimeout(() => setState('thinking', { query: c.q, text: c.think }), 2600);
      setTimeout(() => setState('speaking', { text: c.a }), 7200);
    };
    demoTimers.push(setInterval(talk, 26000)); setTimeout(talk, 1500);
  }

  // ── thẻ sự cố ─────────────────────────────────────────────────────────────
  function showIncident(title, desc) {
    $('inc-title').textContent = title; $('inc-desc').textContent = desc;
    $('inc-yes').title = $('inc-no').title = 'Bản thử chỉ hiển thị — duyệt ở HUD hiện tại hoặc portal.';
    setState('alert', { text: `Phát hiện sự cố: ${title}. ${desc}`.slice(0, 220) });
  }

  // ── nút xem thử + phím 1–6 ────────────────────────────────────────────────
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

  // ── đồng hồ ───────────────────────────────────────────────────────────────
  function clock() {
    const d = new Date();
    $('clock').innerHTML = `${d.toLocaleTimeString('vi-VN', { hour12: false })}<small>${d.toLocaleDateString('vi-VN', { weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric' })}</small>`;
  }

  // ── khởi động ─────────────────────────────────────────────────────────────
  resize(); setNodes([]); subtitle('Sẵn sàng nhận lệnh.', true); clock();
  setInterval(clock, 1000);
  gauge('g-cpu', 0, 'CPU'); gauge('g-ram', 0, 'RAM'); gauge('g-disk', 0, 'Ổ đĩa');
  requestAnimationFrame(frame);
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
  // ?state=thinking … mở thẳng một trạng thái để xem thử / chụp màn hình
  const qs = new URLSearchParams(location.search).get('state');
  if (qs && PREVIEW[qs]) setTimeout(() => { lastManual = Date.now() + 60000; PREVIEW[qs](); }, 400);
})();
