/**
 * web/hud.js
 * ==========
 * VN-MateAI 3D Cybernetic HUD & Visualizer Engine.
 * 
 * Features:
 * - Ultra-crisp 60 FPS HTML5 Canvas engine with Retina / 4K HiDPI scaling.
 * - Multi-tier holographic Arc Reactor rings with rotating degree notches.
 * - 3D Quantum Fibonacci Sphere with dynamic crystalline Plexus connections.
 * - Audio-reactive shockwave ripples & particle burst physics.
 * - Decorative radar mini-sweep canvas (hiệu ứng trang trí, không phải dữ liệu).
 * - Circular SVG dual arc gauges for CPU & RAM telemetry.
 * - Multi-band stereo equalizer oscilloscope with floating peak caps.
 * - WebSocket telemetry link; mọi số đo thiếu đều hiện "chờ kết nối".
 *
 * Phase 76: HUD chỉ hiện dữ liệu thật. Xem `applyMetrics()` và
 * `renderAuthStatus()` — đó là hai nơi quyết định mọi con số và vai trò
 * hiển thị trên màn hình.
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // 0. QUY ƯỚC "CHỜ KẾT NỐI" — chép NGUYÊN VĂN từ web/app.js
  //
  //   Phải khớp từng ký tự với app.js và roi_dashboard.html, nếu không mỗi
  //   nơi sẽ hiển thị "chờ kết nối" theo một kiểu khác nhau. Test
  //   tests/test_phase76_no_fake_hud.mjs so khớp tự động nên sửa một bên mà
  //   quên bên kia là test đỏ.
  // ---------------------------------------------------------------------------
  const WAIT_TXT = 'chờ kết nối';
  
  /** Giá trị này có phải số liệu thật không (không phải thiếu/rỗng/NaN). */
  function _isLive(v) {
    if (v === null || v === undefined || v === '') return false;
    if (typeof v === 'number' && !Number.isFinite(v)) return false;
    return true;
  }
  
  /**
   * Trả về `v` nếu là dữ liệu thật, ngược lại trả `fallback` (mặc định WAIT_TXT).
   * Dùng cho mọi ô đang chờ dữ liệu thay vì `|| 0` hay `?? 100`.
   */
  function _live(v, fallback = WAIT_TXT) {
    return _isLive(v) ? v : fallback;
  }
  
  /**
   * Định dạng số đo kèm đơn vị, hoặc "chờ kết nối" nếu chưa có dữ liệu.
   * @param {*} v        giá trị thô từ API
   * @param {object} opt {digits: số lẻ thập phân, unit: đơn vị, suffix}
   */
  function _liveNum(v, { digits = null, unit = '', suffix = '' } = {}) {
    if (!_isLive(v)) return WAIT_TXT;
    const n = Number(v);
    if (!Number.isFinite(n)) return WAIT_TXT;
    const shown = digits === null ? String(n) : n.toFixed(digits);
    return `${shown}${unit}${suffix}`;
  }
  
  /**
   * Ghi giá trị ra phần tử, tự gắn/bỏ class `is-waiting` theo tình trạng dữ liệu.
   * `el` nhận selector hoặc element; không tồn tại thì bỏ qua (không ném lỗi).
   */
  function _setLiveText(el, value, { waiting = WAIT_TXT } = {}) {
    const node = typeof el === 'string' ? document.querySelector(el) : el;
    if (!node) return;
    const isWait = !_isLive(value) || value === waiting;
    node.textContent = isWait ? waiting : String(value);
    node.classList.toggle('is-waiting', isWait);
  }


  // ---------------------------------------------------------------------------
  // 1. STATE & COLOR PALETTES
  // ---------------------------------------------------------------------------
  let currentAiName = 'Ly Ly';

  const STATES = {
    IDLE: {
      name: 'IDLE // STANDBY',
      badge: '● AI CORE: STANDBY // IDLE',
      tag: 'Ly Ly:',
      r: 0, g: 242, b: 254, // Luminous Cyan
      secondaryR: 2, secondaryG: 132, secondaryB: 199, // Deep Sky Blue
      spinSpeed: 0.009,
      pulseAmp: 1.0,
      badgeClass: 'border-cyan-400/60 text-cyan-300 glow-cyan shadow-[0_0_20px_rgba(0,242,254,0.3)]'
    },
    LISTENING: {
      name: 'VOICE INGEST // LISTENING',
      badge: '● AI CORE: LISTENING // MIC ACTIVE',
      tag: 'VOICE INPUT:',
      r: 0, g: 195, b: 255, // Electric Blue
      secondaryR: 37, secondaryG: 99, secondaryB: 235, // Cobalt Blue
      spinSpeed: 0.018,
      pulseAmp: 2.2,
      badgeClass: 'border-blue-400/80 text-blue-300 glow-cyan shadow-[0_0_25px_rgba(0,195,255,0.4)]'
    },
    PROCESSING: {
      name: 'NEURAL INFERENCE // PROCESSING',
      badge: '● AI CORE: PROCESSING // REASONING',
      tag: 'INFERENCE:',
      r: 245, g: 158, b: 11, // Radiant Amber/Gold
      secondaryR: 217, secondaryG: 119, secondaryB: 6,
      spinSpeed: 0.038,
      pulseAmp: 1.9,
      badgeClass: 'border-amber-400/80 text-amber-300 glow-amber shadow-[0_0_25px_rgba(245,158,11,0.4)]'
    },
    SPEAKING: {
      name: 'AUDIO SYNTHESIS // TRANSMITTING',
      badge: '● AI CORE: TRANSMITTING // SPEAKING',
      tag: 'Ly Ly:',
      r: 16, g: 185, b: 129, // Matrix Emerald Cyan
      secondaryR: 20, secondaryG: 184, secondaryB: 166,
      spinSpeed: 0.024,
      pulseAmp: 3.2,
      badgeClass: 'border-emerald-400/80 text-emerald-300 glow-emerald shadow-[0_0_25px_rgba(16,185,129,0.4)]'
    }
  };

  let currentState = STATES.IDLE;
  let currentColor = { r: 0, g: 242, b: 254 };
  let targetColor = { r: 0, g: 242, b: 254 };

  // ---------------------------------------------------------------------------
  // 2. DOM ELEMENT REFERENCES
  // ---------------------------------------------------------------------------
  const coreCanvas = document.getElementById('core-canvas');
  const coreCtx = coreCanvas ? coreCanvas.getContext('2d') : null;
  const waveCanvas = document.getElementById('waveform-canvas');
  const waveCtx = waveCanvas ? waveCanvas.getContext('2d') : null;
  const radarCanvas = document.getElementById('radar-canvas');
  const radarCtx = radarCanvas ? radarCanvas.getContext('2d') : null;

  const clockLocalEl = document.getElementById('hud-clock-local');
  const uptimeEl = document.getElementById('hud-uptime');
  const fpsEl = document.getElementById('hud-fps');
  const connDotEl = document.getElementById('hud-conn-dot');
  const connTextEl = document.getElementById('hud-conn-text');
  const linkBadgeEl = document.getElementById('hud-link-badge');
  const secStatusEl = document.getElementById('hud-security-status');
  const statusBadgeEl = document.getElementById('core-status-badge');

  const cpuTextEl = document.getElementById('metric-cpu-text');
  const cpuCircleEl = document.getElementById('metric-cpu-circle');
  const cpuBarEl = document.getElementById('metric-cpu-bar');
  const coresTextEl = document.getElementById('metric-cores-text');
  const procsTextEl = document.getElementById('metric-procs-text');
  
  const ramTextEl = document.getElementById('metric-ram-text');
  const ramCircleEl = document.getElementById('metric-ram-circle');
  const ramBarEl = document.getElementById('metric-ram-bar');
  const ramGbEl = document.getElementById('metric-ram-gb');
  const ramTotalEl = document.getElementById('metric-ram-total');

  const diskTextEl = document.getElementById('metric-disk-text');
  const diskBarEl = document.getElementById('metric-disk-bar');
  const diskFreeEl = document.getElementById('metric-disk-free');
  const clientsCountEl = document.getElementById('metric-clients-count');
  const audioNodesEl = document.getElementById('metric-audio-nodes');
  const skillsCountEl = document.getElementById('metric-skills-count');
  const netIoEl = document.getElementById('metric-net-io');
  const permBadgeEl = document.getElementById('hud-permission-badge');

  const voiceDotEl = document.getElementById('voice-indicator-dot');
  const voiceTagEl = document.getElementById('voice-indicator-tag');
  const typewriterTextEl = document.getElementById('typewriter-text');
  const logStreamEl = document.getElementById('hud-log-stream');
  const logPlaceholderEl = document.getElementById('hud-log-placeholder');
  const voiceStreamEl = document.getElementById('hud-voice-stream');

  // Phase 87: khung "QUÁ TRÌNH SUY NGHĨ" — hiện phần model tự suy luận trước
  // khi trả lời. Gập lại mặc định, bấm vào dòng tiêu đề để mở xem.
  const thinkingEl = document.getElementById('hud-thinking');
  const thinkingHeadEl = document.getElementById('hud-thinking-head');
  const thinkingDotEl = document.getElementById('hud-thinking-dot');
  const thinkingPeekEl = document.getElementById('hud-thinking-peek');
  const thinkingCaretEl = document.getElementById('hud-thinking-caret');
  const thinkingBodyEl = document.getElementById('hud-thinking-body');
  const thinkingTextEl = document.getElementById('hud-thinking-text');
  // Đếm giây không nhận được gói "done": nếu máy chủ đứt giữa chừng, vòng
  // xoay phải tự tắt thay vì quay mãi.
  let thinkingStuckTimer = null;

  // ---------------------------------------------------------------------------
  // 3. ZERO-ALLOCATION PRE-ALLOCATED POOLS FOR 3D QUANTUM SPHERE & PARTICLES
  // ---------------------------------------------------------------------------
  const NUM_PARTICLES = 70;
  const NUM_SPHERE_POINTS = 180;
  const CIRCLE_CIRCUMFERENCE = 238.76; // 2 * Math.PI * 38
  
  // Static Particle Pool [x, y, z, angle, speed, radius]
  const particles = new Float32Array(NUM_PARTICLES * 6);
  for (let i = 0; i < NUM_PARTICLES; i++) {
    const idx = i * 6;
    const theta = Math.random() * Math.PI * 2;
    const rad = 95 + Math.random() * 140;
    particles[idx] = Math.cos(theta) * rad;      // x
    particles[idx + 1] = (Math.random() - 0.5) * 80; // y
    particles[idx + 2] = Math.sin(theta) * rad;  // z
    particles[idx + 3] = theta;                  // angle
    particles[idx + 4] = 0.006 + Math.random() * 0.016; // speed
    particles[idx + 5] = 1.0 + Math.random() * 2.0;     // size
  }

  // Static 3D Sphere Vertices Pool (Fibonacci Sphere with Golden Spiral)
  const sphereVertices = new Float32Array(NUM_SPHERE_POINTS * 3);
  const phi = Math.PI * (3 - Math.sqrt(5)); // Golden angle ~2.3999 rad
  for (let i = 0; i < NUM_SPHERE_POINTS; i++) {
    const y = 1 - (i / (NUM_SPHERE_POINTS - 1)) * 2; // -1 to 1
    const radius = Math.sqrt(1 - y * y);
    const theta = phi * i;
    const x = Math.cos(theta) * radius;
    const z = Math.sin(theta) * radius;
    const baseRadius = 90;

    sphereVertices[i * 3] = x * baseRadius;
    sphereVertices[i * 3 + 1] = y * baseRadius;
    sphereVertices[i * 3 + 2] = z * baseRadius;
  }

  // Projected 2D points pool for crystal Plexus lines
  const projectedPoints = new Float32Array(NUM_SPHERE_POINTS * 3); // [px, py, scale]

  // Expanding shockwave ripple rings
  const shockwaves = [
    { radius: 0, alpha: 0, active: false },
    { radius: 0, alpha: 0, active: false },
    { radius: 0, alpha: 0, active: false }
  ];

  // ---------------------------------------------------------------------------
  // 4. ANIMATION ANGLES & TIMING
  // ---------------------------------------------------------------------------
  let rotX = 0.25;
  let rotY = 0;
  let rotZ = 0;
  let arcReactorAngle1 = 0;
  let arcReactorAngle2 = 0;
  let arcReactorAngle3 = 0;
  let arcReactorAngle4 = 0;
  let radarAngle = 0;
  let wavePhase = 0;

  let startTime = Date.now();
  let frameCount = 0;
  let lastFpsUpdate = performance.now();

  // Equalizer bar heights & peak caps
  const NUM_BARS = 40;
  const barHeights = new Float32Array(NUM_BARS);
  const barPeaks = new Float32Array(NUM_BARS);

  // ---------------------------------------------------------------------------
  // 4.1. HARDWARE WEB AUDIO API FFT SPECTRUM & PROCEDURAL SFX SYNTHESIZER
  // ---------------------------------------------------------------------------
  let audioCtx = null;
  let analyserNode = null;
  let audioSourceNode = null;
  let freqData = null;
  let isAudioPlaying = false;
  let isTabActive = true;
  let hudAudioEnabled = true;

  // ---------------------------------------------------------------------------
  // Phát MP3 câu trả lời (khung nhị phân từ broadcast_hud_binary) — bộ phát dùng
  // chung với portal: web/voice-audio-queue.js (realtime P5). Bản riêng trước
  // đây (HudAudioQueue) giải mã các đoạn song song nên câu có thể bị đảo.
  // Âm thanh đi qua AnalyserNode để Arc Reactor / FFT dao động theo giọng nói.
  // ---------------------------------------------------------------------------
  function _hudAudioContext() {
    if (typeof initWebAudio === 'function') initWebAudio();
    if (!audioCtx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return null;
      try { audioCtx = new AC(); } catch (e) { console.warn('[HudAQ] AudioContext failed:', e); return null; }
    }
    return audioCtx;
  }

  const _hudAudioQueue = new VoiceAudioQueue({
    getContext: _hudAudioContext,
    getDestination: (ctx) => analyserNode || ctx.destination,
    canPlay: () => hudAudioEnabled,
    autoEnd: true,          // HUD không có tín hiệu hết lượt: phát hết là xong
    notifyOnStop: false,    // ngắt lời không phát chuông "sẵn sàng"
    keepChunks: false,      // HUD chạy cả ngày, không giữ MP3 đã phát
    onBeforeChunk: () => {
      // Triệt tiêu player HTMLAudioElement / hàng đợi base64 cũ đang phát dở.
      if (currentVoiceAudio) {
        try { currentVoiceAudio.pause(); currentVoiceAudio.currentTime = 0; } catch (e) {}
        currentVoiceAudio = null;
      }
      if (hudSpeechQueue && hudSpeechQueue.length) {
        hudSpeechQueue = [];
        hudSpeechDraining = false;
      }
    },
    onStart: (durationSec) => {
      isAudioPlaying = true;
      setHudState('speaking', lastSpokenText || '', durationSec * 1000);
    },
    onPlaybackEnd: () => {
      isAudioPlaying = false;
      playCyberChime('ready');
      setHudState('idle', 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...');
    },
  });


  function initWebAudio() {
    if (audioCtx) return;
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;

    try {
      audioCtx = new AudioContextClass();
      analyserNode = audioCtx.createAnalyser();
      analyserNode.fftSize = 128; // 64 frequency bands
      analyserNode.smoothingTimeConstant = 0.82;
      freqData = new Uint8Array(analyserNode.frequencyBinCount);

      const audioStreamEl = document.getElementById('hud-audio-stream');
      if (audioStreamEl) {
        audioSourceNode = audioCtx.createMediaElementSource(audioStreamEl);
        audioSourceNode.connect(analyserNode);
        analyserNode.connect(audioCtx.destination);
      }
    } catch (err) {
      console.warn('[Web Audio] Init note:', err);
    }
  }

  function playCyberChime(type) {
    if (!hudAudioEnabled) return;
    initWebAudio();
    if (!audioCtx) return;
    if (audioCtx.state === 'suspended') {
      audioCtx.resume().catch(() => {});
    }

    try {
      const now = audioCtx.currentTime;
      const osc = audioCtx.createOscillator();
      const gain = audioCtx.createGain();
      osc.connect(gain);
      gain.connect(audioCtx.destination);

      if (type === 'wake') {
        // High-tech rising chirp when Mic activates
        osc.type = 'sine';
        osc.frequency.setValueAtTime(520, now);
        osc.frequency.exponentialRampToValueAtTime(1040, now + 0.08);
        gain.gain.setValueAtTime(0.06, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.14);
        osc.start(now);
        osc.stop(now + 0.15);
      } else if (type === 'process') {
        // Subtle cyber acoustic pulse on command submission
        osc.type = 'sine';
        osc.frequency.setValueAtTime(320, now);
        osc.frequency.linearRampToValueAtTime(640, now + 0.06);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.12);
        osc.start(now);
        osc.stop(now + 0.13);
      } else if (type === 'ready') {
        // Crystal confirmation chime when AI speech completes
        osc.type = 'triangle';
        osc.frequency.setValueAtTime(880, now);
        osc.frequency.exponentialRampToValueAtTime(1318.5, now + 0.08);
        gain.gain.setValueAtTime(0.05, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
        osc.start(now);
        osc.stop(now + 0.21);
      } else if (type === 'click') {
        // Sub-millisecond mechanical click on UI touch
        osc.type = 'sine';
        osc.frequency.setValueAtTime(1200, now);
        gain.gain.setValueAtTime(0.03, now);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.03);
        osc.start(now);
        osc.stop(now + 0.035);
      }
    } catch (_) {}
  }
  window.playCyberChime = playCyberChime;

  document.addEventListener('visibilitychange', () => {
    isTabActive = !document.hidden;
  });

  // ---------------------------------------------------------------------------
  // 5. CANVAS RESIZING (HiDPI / Retina Crisp Rendering)
  // ---------------------------------------------------------------------------
  function resizeCanvases() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    if (coreCanvas) {
      const rect = coreCanvas.getBoundingClientRect();
      const w = rect.width || 440;
      const h = rect.height || 440;
      coreCanvas.width = w * dpr;
      coreCanvas.height = h * dpr;
      if (coreCtx) coreCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    if (waveCanvas) {
      const rect = waveCanvas.getBoundingClientRect();
      const w = rect.width || 600;
      const h = rect.height || 50;
      waveCanvas.width = w * dpr;
      waveCanvas.height = h * dpr;
      if (waveCtx) waveCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    if (radarCanvas) {
      const rect = radarCanvas.getBoundingClientRect();
      const w = rect.width || 240;
      const h = rect.height || 110;
      radarCanvas.width = w * dpr;
      radarCanvas.height = h * dpr;
      if (radarCtx) radarCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
  }

  window.addEventListener('resize', resizeCanvases);

  // ---------------------------------------------------------------------------
  // 6. RENDER LOOP: 3D CYBERNETIC ARC REACTOR & QUANTUM PLEXUS SPHERE
  // ---------------------------------------------------------------------------
  function renderCore(timestamp) {
    if (!coreCtx || !coreCanvas) return;

    const w = coreCanvas.getBoundingClientRect().width;
    const h = coreCanvas.getBoundingClientRect().height;
    const cx = w / 2;
    const cy = h / 2;

    coreCtx.clearRect(0, 0, w, h);

    // Smooth Color Interpolation
    currentColor.r += (targetColor.r - currentColor.r) * 0.08;
    currentColor.g += (targetColor.g - currentColor.g) * 0.08;
    currentColor.b += (targetColor.b - currentColor.b) * 0.08;

    const cr = Math.round(currentColor.r);
    const cg = Math.round(currentColor.g);
    const cb = Math.round(currentColor.b);

    // ── Real-Time Voice Audio FFT Frequency Coupling ──────────────────
    let voiceEnergy = 0;
    if (isAudioPlaying && freqData) {
      let sum = 0;
      const count = Math.min(32, freqData.length);
      for (let k = 0; k < count; k++) sum += freqData[k];
      voiceEnergy = sum / count; // 0 - 255
    }
    const voiceEnergyRatio = voiceEnergy / 255;

    // Rotational velocities accelerated by real-time voice energy
    rotY += currentState.spinSpeed * (1 + voiceEnergyRatio * 1.5);
    rotX = 0.28 + Math.sin(timestamp * 0.0012) * 0.12;
    arcReactorAngle1 += currentState.spinSpeed * (1.2 + voiceEnergyRatio * 0.8);
    arcReactorAngle2 -= currentState.spinSpeed * (0.9 + voiceEnergyRatio * 0.6);
    arcReactorAngle3 += currentState.spinSpeed * (1.6 + voiceEnergyRatio * 1.2);
    arcReactorAngle4 -= currentState.spinSpeed * 0.5;

    // Pulse scaled dynamically with vocal syllable energy
    const pulse = (1 + Math.sin(timestamp * 0.004) * 0.04 * currentState.pulseAmp) * (1 + voiceEnergyRatio * 0.24);

    // ── Layer 0: Radial Ambient Plasma Glow (Additive Blend) ──────────
    coreCtx.save();
    coreCtx.globalCompositeOperation = 'lighter';
    const bgGlow = coreCtx.createRadialGradient(cx, cy, 5, cx, cy, 180 * pulse);
    bgGlow.addColorStop(0, `rgba(${cr},${cg},${cb}, ${0.28 + voiceEnergyRatio * 0.2})`);
    bgGlow.addColorStop(0.4, `rgba(${cr},${cg},${cb}, 0.08)`);
    bgGlow.addColorStop(1, 'rgba(0,0,0,0)');
    coreCtx.fillStyle = bgGlow;
    coreCtx.beginPath();
    coreCtx.arc(cx, cy, 180 * pulse, 0, Math.PI * 2);
    coreCtx.fill();
    coreCtx.restore();

    // ── Layer 1: Holographic Arc Reactor Rings ────────────────────────
    coreCtx.save();
    coreCtx.translate(cx, cy);

    // Shockwave ripple update & render (real-time voice transient trigger)
    const shouldTriggerShockwave = (isAudioPlaying && voiceEnergy > 90 && Math.random() < 0.12) ||
      ((currentState === STATES.SPEAKING || currentState === STATES.LISTENING) && Math.random() < 0.04);

    if (shouldTriggerShockwave) {
      for (const sw of shockwaves) {
        if (!sw.active) {
          sw.active = true;
          sw.radius = 40;
          sw.alpha = 0.95;
          break;
        }
      }
    }
    for (const sw of shockwaves) {
      if (sw.active) {
        sw.radius += 2.5;
        sw.alpha -= 0.015;
        if (sw.alpha <= 0 || sw.radius > 200) {
          sw.active = false;
        } else {
          coreCtx.save();
          coreCtx.strokeStyle = `rgba(${cr},${cg},${cb}, ${sw.alpha * 0.6})`;
          coreCtx.lineWidth = 1.8;
          coreCtx.beginPath();
          coreCtx.arc(0, 0, sw.radius, 0, Math.PI * 2);
          coreCtx.stroke();
          coreCtx.restore();
        }
      }
    }

    // Outer Segmented Tech Ring
    coreCtx.save();
    coreCtx.rotate(arcReactorAngle1);
    coreCtx.strokeStyle = `rgba(${cr},${cg},${cb}, 0.5)`;
    coreCtx.lineWidth = 1.6;
    coreCtx.setLineDash([22, 10, 6, 10]);
    coreCtx.beginPath();
    coreCtx.arc(0, 0, 168 * pulse, 0, Math.PI * 2);
    coreCtx.stroke();

    // 8 Outer Cardinal Chevrons
    for (let i = 0; i < 8; i++) {
      const a = (i / 8) * Math.PI * 2;
      coreCtx.save();
      coreCtx.rotate(a);
      coreCtx.fillStyle = `rgba(${cr},${cg},${cb}, 0.8)`;
      coreCtx.beginPath();
      coreCtx.moveTo(176 * pulse, 0);
      coreCtx.lineTo(182 * pulse, -3.5);
      coreCtx.lineTo(185 * pulse, 0);
      coreCtx.lineTo(182 * pulse, 3.5);
      coreCtx.closePath();
      coreCtx.fill();
      coreCtx.restore();
    }
    coreCtx.restore();

    // Middle Geared Ring with 24 Radial Ticks
    coreCtx.save();
    coreCtx.rotate(arcReactorAngle2);
    coreCtx.strokeStyle = `rgba(${cr},${cg},${cb}, 0.6)`;
    coreCtx.lineWidth = 1.2;
    coreCtx.setLineDash([40, 12, 12, 12]);
    coreCtx.beginPath();
    coreCtx.arc(0, 0, 138 * pulse, 0, Math.PI * 2);
    coreCtx.stroke();

    for (let i = 0; i < 24; i++) {
      const a = (i / 24) * Math.PI * 2;
      const tickLen = (i % 6 === 0) ? 9 : 5;
      coreCtx.strokeStyle = (i % 6 === 0) ? `rgba(${cr},${cg},${cb}, 0.9)` : `rgba(${cr},${cg},${cb}, 0.4)`;
      coreCtx.lineWidth = (i % 6 === 0) ? 1.8 : 1.0;
      coreCtx.beginPath();
      coreCtx.moveTo(Math.cos(a) * (138 - tickLen) * pulse, Math.sin(a) * (138 - tickLen) * pulse);
      coreCtx.lineTo(Math.cos(a) * (138 + tickLen) * pulse, Math.sin(a) * (138 + tickLen) * pulse);
      coreCtx.stroke();
    }
    coreCtx.restore();

    // Inner Gyroscope Gimbal Ring (Fast Spin)
    coreCtx.save();
    coreCtx.rotate(arcReactorAngle3);
    coreCtx.strokeStyle = `rgba(${cr},${cg},${cb}, 0.75)`;
    coreCtx.lineWidth = 1.8;
    coreCtx.setLineDash([12, 8, 4, 8]);
    coreCtx.beginPath();
    coreCtx.arc(0, 0, 108 * pulse, 0, Math.PI * 2);
    coreCtx.stroke();
    coreCtx.restore();

    coreCtx.restore(); // Return translation

    // ── Layer 2: 3D Holographic Fibonacci Sphere & Plexus Lines ──────
    const cosY = Math.cos(rotY);
    const sinY = Math.sin(rotY);
    const cosX = Math.cos(rotX);
    const sinX = Math.sin(rotX);
    const fov = 320;

    // 1. Calculate 3D perspective projections for all sphere vertices
    for (let i = 0; i < NUM_SPHERE_POINTS; i++) {
      const idx = i * 3;
      let x0 = sphereVertices[idx] * pulse;
      let y0 = sphereVertices[idx + 1] * pulse;
      let z0 = sphereVertices[idx + 2] * pulse;

      // Rotate Y
      const x1 = x0 * cosY - z0 * sinY;
      const z1 = z0 * cosY + x0 * sinY;

      // Rotate X
      const y2 = y0 * cosX - z1 * sinX;
      const z2 = z1 * cosX + y0 * sinX;

      const scale = fov / (fov + z2 + 110);
      const px = cx + x1 * scale;
      const py = cy + y2 * scale;

      projectedPoints[idx] = px;
      projectedPoints[idx + 1] = py;
      projectedPoints[idx + 2] = scale;
    }

    // 2. Draw Crystal Plexus Lines between nearby 3D vertices
    coreCtx.save();
    coreCtx.lineWidth = 0.65;
    for (let i = 0; i < NUM_SPHERE_POINTS; i += 2) {
      const p1x = projectedPoints[i * 3];
      const p1y = projectedPoints[i * 3 + 1];
      const scale1 = projectedPoints[i * 3 + 2];

      for (let j = i + 1; j < Math.min(i + 14, NUM_SPHERE_POINTS); j++) {
        const p2x = projectedPoints[j * 3];
        const p2y = projectedPoints[j * 3 + 1];
        const dx = p1x - p2x;
        const dy = p1y - p2y;
        const distSq = dx * dx + dy * dy;

        // Threshold distance for plexus connection (~32px)
        if (distSq < 1050) {
          const alpha = (1 - distSq / 1050) * 0.35 * Math.min(scale1, 1.2);
          coreCtx.strokeStyle = `rgba(${cr},${cg},${cb}, ${alpha})`;
          coreCtx.beginPath();
          coreCtx.moveTo(p1x, p1y);
          coreCtx.lineTo(p2x, p2y);
          coreCtx.stroke();
        }
      }
    }
    coreCtx.restore();

    // 3. Draw Radiant Central Singularity Plasma Nucleus
    coreCtx.save();
    coreCtx.globalCompositeOperation = 'lighter';
    const coreGrad = coreCtx.createRadialGradient(cx, cy, 2, cx, cy, 48 * pulse);
    coreGrad.addColorStop(0, '#ffffff');
    coreGrad.addColorStop(0.2, `rgba(${cr},${cg},${cb}, 0.95)`);
    coreGrad.addColorStop(0.55, `rgba(${cr},${cg},${cb}, 0.3)`);
    coreGrad.addColorStop(1, 'rgba(0,0,0,0)');
    coreCtx.fillStyle = coreGrad;
    coreCtx.beginPath();
    coreCtx.arc(cx, cy, 48 * pulse, 0, Math.PI * 2);
    coreCtx.fill();
    coreCtx.restore();

    // 4. Draw 3D Sphere Vertices (Glowing Nodes)
    for (let i = 0; i < NUM_SPHERE_POINTS; i++) {
      const px = projectedPoints[i * 3];
      const py = projectedPoints[i * 3 + 1];
      const scale = projectedPoints[i * 3 + 2];

      const alpha = Math.max(0.15, Math.min(0.95, (scale - 0.6) * 1.5));
      const ptRadius = Math.max(1.0, 2.2 * scale);

      coreCtx.fillStyle = `rgba(${cr},${cg},${cb}, ${alpha})`;
      coreCtx.beginPath();
      coreCtx.arc(px, py, ptRadius, 0, Math.PI * 2);
      coreCtx.fill();
    }

    // ── Layer 3: Orbiting Quantum Particles with Luminous Tracers ─────
    for (let i = 0; i < NUM_PARTICLES; i++) {
      const idx = i * 6;
      particles[idx + 3] += particles[idx + 4] * (currentState === STATES.PROCESSING ? 3.5 : 1.0);
      const a = particles[idx + 3];
      const rad = 115 + (i % 6) * 16;

      const x = Math.cos(a) * rad;
      const z = Math.sin(a) * rad;
      const y = Math.sin(a * 2.5 + i) * 35;

      const x1 = x * cosY - z * sinY;
      const z1 = z * cosY + x * sinY;
      const y2 = y * cosX - z1 * sinX;
      const z2 = z1 * cosX + y * sinX;

      const scale = fov / (fov + z2 + 110);
      const px = cx + x1 * scale;
      const py = cy + y2 * scale;

      const alpha = Math.max(0.12, Math.min(0.95, (z2 + 120) / 240));
      const pSize = particles[idx + 5] * scale;

      coreCtx.fillStyle = `rgba(${cr},${cg},${cb}, ${alpha})`;
      coreCtx.beginPath();
      coreCtx.arc(px, py, pSize, 0, Math.PI * 2);
      coreCtx.fill();

      // Mini tracer spark
      if (scale > 0.8) {
        coreCtx.fillStyle = `rgba(255, 255, 255, ${alpha * 0.8})`;
        coreCtx.beginPath();
        coreCtx.arc(px, py, pSize * 0.5, 0, Math.PI * 2);
        coreCtx.fill();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // 7. RENDER LOOP: TACTICAL MINI-RADAR SWEEP CANVAS
  // ---------------------------------------------------------------------------
  function renderRadar(timestamp) {
    if (!radarCtx || !radarCanvas) return;

    const w = radarCanvas.getBoundingClientRect().width;
    const h = radarCanvas.getBoundingClientRect().height;
    const cx = w / 2;
    const cy = h / 2;
    const radius = Math.min(w, h) * 0.44;

    radarCtx.clearRect(0, 0, w, h);

    radarAngle += 0.035;

    // Draw Range Rings
    radarCtx.strokeStyle = 'rgba(0, 242, 254, 0.2)';
    radarCtx.lineWidth = 1;

    for (let r = 0.33; r <= 1.0; r += 0.33) {
      radarCtx.beginPath();
      radarCtx.arc(cx, cy, radius * r, 0, Math.PI * 2);
      radarCtx.stroke();
    }

    // Crosshairs
    radarCtx.beginPath();
    radarCtx.moveTo(cx - radius, cy);
    radarCtx.lineTo(cx + radius, cy);
    radarCtx.moveTo(cx, cy - radius);
    radarCtx.lineTo(cx, cy + radius);
    radarCtx.stroke();

    // Rotating Sweep Gradient Beam
    radarCtx.save();
    radarCtx.translate(cx, cy);
    radarCtx.rotate(radarAngle);

    const sweepGrad = radarCtx.createRadialGradient(0, 0, 0, 0, 0, radius);
    sweepGrad.addColorStop(0, 'rgba(0, 242, 254, 0.45)');
    sweepGrad.addColorStop(1, 'rgba(0, 242, 254, 0.0)');

    radarCtx.fillStyle = sweepGrad;
    radarCtx.beginPath();
    radarCtx.moveTo(0, 0);
    radarCtx.arc(0, 0, radius, 0, 0.45);
    radarCtx.closePath();
    radarCtx.fill();

    // Sharp Sweep Front Line
    radarCtx.strokeStyle = '#00ffff';
    radarCtx.lineWidth = 1.5;
    radarCtx.beginPath();
    radarCtx.moveTo(0, 0);
    radarCtx.lineTo(Math.cos(0.45) * radius, Math.sin(0.45) * radius);
    radarCtx.stroke();
    radarCtx.restore();

    // Phase 76: đã gỡ 2 chấm sáng "mục tiêu vệ tinh mô phỏng" vẽ ra.
    // Chúng không đến từ dữ liệu nào — chỉ là hình tròn tĩnh đặt cứng toạ độ,
    // mà bảng "TARGETS: 0 DETECTED" lại nói ngược lại. Giữ lại sẽ hiển thị
    // mục tiêu không có thật. Ô quét radar giờ là hiệu ứng trang trí thuần,
    // có nhãn "MINHỌA — KHÔNG PHẢI DỮ LIỆU" bên cạnh.
  }

  // ---------------------------------------------------------------------------
  // 8. RENDER LOOP: DUAL-BAND HARMONIC AUDIO EQUALIZER
  // ---------------------------------------------------------------------------
  function renderWaveform(timestamp) {
    if (!waveCtx || !waveCanvas) return;

    const w = waveCanvas.getBoundingClientRect().width;
    const h = waveCanvas.getBoundingClientRect().height;
    const cy = h / 2;

    waveCtx.clearRect(0, 0, w, h);

    const cr = Math.round(currentColor.r);
    const cg = Math.round(currentColor.g);
    const cb = Math.round(currentColor.b);

    const isVoiceActive = currentState === STATES.SPEAKING || currentState === STATES.LISTENING;
    wavePhase += (isVoiceActive || isAudioPlaying) ? 0.035 : 0.012;

    // Read real hardware FFT frequency spectrum
    if (analyserNode && freqData && (isAudioPlaying || isVoiceActive)) {
      analyserNode.getByteFrequencyData(freqData);
    }

    // 1. Mirrored Dynamic Stereo Equalizer Bars
    const barWidth = 3.5;
    const gap = 3.5;
    const totalW = NUM_BARS * (barWidth + gap);
    const startX = (w - totalW) / 2;

    for (let i = 0; i < NUM_BARS; i++) {
      const distFromCenter = 1 - Math.abs(i - NUM_BARS / 2) / (NUM_BARS / 2);
      
      let targetH = 3;
      if (isAudioPlaying && freqData) {
        // Map 40 bars across first 48 frequency bins (sub-bass to speech formant range)
        const binIndex = Math.min(freqData.length - 1, Math.floor((i / NUM_BARS) * 48));
        const rawEnergy = freqData[binIndex] / 255;
        targetH = Math.max(3, rawEnergy * 32.0 * (0.45 + distFromCenter * 0.85));
      } else if (isVoiceActive) {
        targetH = Math.max(3, (Math.sin(timestamp * 0.008 + i * 0.45) * 0.5 + 0.5) * 16.0 * (0.4 + distFromCenter * 0.9));
      } else {
        // Organic quantum ambient breathing oscillation
        targetH = Math.max(2.5, (Math.sin(timestamp * 0.003 + i * 0.35) * 0.5 + 0.5) * 5.0 * (0.3 + distFromCenter * 0.8));
      }
      
      // Smooth bar smoothing & peak caps
      barHeights[i] += (targetH - barHeights[i]) * 0.32;
      if (barHeights[i] > barPeaks[i]) {
        barPeaks[i] = barHeights[i];
      } else {
        barPeaks[i] = Math.max(barHeights[i], barPeaks[i] - 0.45); // Gravity fall
      }

      const bx = startX + i * (barWidth + gap);
      const bh = barHeights[i];

      // Gradient fill for each bar
      const barGrad = waveCtx.createLinearGradient(bx, cy - bh, bx, cy + bh);
      barGrad.addColorStop(0, `rgba(${cr},${cg},${cb}, 0.95)`);
      barGrad.addColorStop(0.5, `rgba(0, 255, 255, 0.45)`);
      barGrad.addColorStop(1, `rgba(${cr},${cg},${cb}, 0.95)`);

      waveCtx.fillStyle = barGrad;
      waveCtx.fillRect(bx, cy - bh, barWidth, Math.max(2, bh * 2));

      // Peak cap dot
      const peakY = Math.max(cy - barPeaks[i] - 2, 2);
      waveCtx.fillStyle = '#ffffff';
      waveCtx.fillRect(bx, peakY, barWidth, 1.5);
    }

    // 2. Harmonic Center Bezier Waves
    const waveAmp = (isAudioPlaying || isVoiceActive) ? 14.0 : 3.0;
    for (let channel = 0; channel < 2; channel++) {
      waveCtx.beginPath();
      waveCtx.lineWidth = channel === 0 ? 1.8 : 1.0;
      waveCtx.strokeStyle = channel === 0 ? `rgba(${cr},${cg},${cb}, 0.85)` : `rgba(0, 255, 255, 0.35)`;

      const offset = channel * Math.PI * 0.5;

      for (let x = 0; x < w; x += 4) {
        const normX = (x / w) * Math.PI * 6;
        const envelope = Math.sin((x / w) * Math.PI); // Window function
        const wave1 = Math.sin(normX + wavePhase * 10 + offset);
        const wave2 = Math.cos(normX * 1.8 - wavePhase * 8) * 0.5;
        const y = cy + (wave1 + wave2) * waveAmp * 0.85 * envelope;

        if (x === 0) waveCtx.moveTo(x, y);
        else waveCtx.lineTo(x, y);
      }
      waveCtx.stroke();
    }
  }

  // ---------------------------------------------------------------------------
  // 9. MASTER ANIMATION TICKER & FPS CALCULATION
  // ---------------------------------------------------------------------------
  function tick(timestamp) {
    if (!isTabActive) {
      // Background tab optimization: Throttle to 10 FPS to save 95% GPU & battery
      setTimeout(() => {
        requestAnimationFrame(tick);
      }, 100);
      return;
    }

    frameCount++;
    if (timestamp - lastFpsUpdate >= 1000) {
      const fps = Math.round((frameCount * 1000) / (timestamp - lastFpsUpdate));
      if (fpsEl) fpsEl.textContent = `${fps}.0 FPS`;
      frameCount = 0;
      lastFpsUpdate = timestamp;
    }

    renderCore(timestamp);
    // renderRadar(timestamp); // Removed decorative radar as requested
    renderWaveform(timestamp);

    requestAnimationFrame(tick);
  }

  // ---------------------------------------------------------------------------
  // 10. TYPEWRITER SUBTITLE ENGINE & AUDIO SYNCHRONIZER
  // ---------------------------------------------------------------------------
  let currentTypedText = '';
  let targetTypedText = 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...';
  let typeIndex = 0;
  let typingTimer = null;
  let currentVoiceAudio = null;

  /* HÀNG ĐỢI PHÁT GIỌNG NÓI — Phase 69
   *
   * Trước đây mỗi câu về là ghi đè thẳng lên phần tử audio đang phát:
   *     audioStreamEl.pause();
   *     audioStreamEl.src = 'data:...câu mới';
   * Server gửi TỪNG CÂU một, nên câu sau tới khi câu trước còn đang nói dở
   * là câu đó bị cắt ngang giữa chừng — đúng triệu chứng "ngắt quãng".
   *
   * Nay câu nối được xếp hàng và chỉ phát sau khi câu n-1 hết. Không câu nào
   * cắt câu nào, và không có khoảng lặng giữa các câu.
   */
  let hudSpeechQueue = [];
  let hudSpeechDraining = false;

  // TTS runtime config — updated live via 'audio_config_updated' WS message
  // khi Admin lưu cấu hình ở Voice Studio (Tab AI Manager).
  let hudTtsConfig = {
    voice: 'vi-VN-HoaiMyNeural',
    rate: '+15%',
    speechRateNum: 15,
    volume: 80,        // 0-100 → playback volume 0.0-1.0
  };


  function setTypewriterText(newText, durationMs = null) {
    if (!newText || newText === targetTypedText) return;
    targetTypedText = newText;
    currentTypedText = '';
    typeIndex = 0;

    if (typingTimer) {
      clearInterval(typingTimer);
      typingTimer = null;
    }

    // Dynamic pacing: synchronize typing speed with audio duration!
    let charInterval = 45;
    if (durationMs && durationMs > 0 && newText.length > 0) {
      charInterval = Math.max(18, Math.min(80, Math.floor((durationMs * 0.95) / newText.length)));
    }

    typingTimer = setInterval(() => {
      if (typeIndex < targetTypedText.length) {
        currentTypedText += targetTypedText.charAt(typeIndex);
        typeIndex++;
        if (typewriterTextEl) {
          typewriterTextEl.textContent = currentTypedText;
        }
      } else {
        clearInterval(typingTimer);
        typingTimer = null;
      }
    }, charInterval);
  }

  let lastSpokenText = '';
  let lastSpokenTime = 0;
  let hudDisplayDismissTimer = null;

  // ── Phase 47: Holographic Markdown & Structured Table Renderer ─────────────
  function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function formatInlineText(str) {
    if (!str) return '';
    return str
      .replace(/\*\*(.*?)\*\*/g, '<strong class="text-cyan-300 font-bold">$1</strong>')
      .replace(/\*(.*?)\*/g, '<em class="text-slate-300 italic">$1</em>')
      .replace(/`([^`]+)`/g, '<code class="px-1.5 py-0.5 rounded bg-black/60 border border-cyan-500/30 text-cyan-300 font-mono text-xs">$1</code>');
  }

  function renderHudMarkdown(rawText) {
    if (!rawText) return '';
    let text = rawText.trim();

    // 1. Code blocks: ```...```
    text = text.replace(/```([a-zA-Z0-9]*)\n?([\s\S]*?)```/g, (match, lang, code) => {
      return `<pre class="hud-code-block"><code>${escapeHtml(code.trim())}</code></pre>`;
    });

    // 2. Tables: lines containing |
    const rawLines = text.split('\n');
    let inTable = false;
    let tableHtml = '';
    const outputLines = [];

    for (let i = 0; i < rawLines.length; i++) {
      const line = rawLines[i].trim();
      if (line.startsWith('|') && line.endsWith('|')) {
        if (!inTable) {
          inTable = true;
          tableHtml = '<table class="hud-table"><tbody>';
        }
        // Check if header separator: |--|--|
        if (/^\|(\s*[-:]+[-|\s:]*)\|$/.test(line)) {
          continue; // skip separator row
        }
        const cells = line.split('|').slice(1, -1);
        const isHeader = !tableHtml.includes('<tr');
        const tag = isHeader ? 'th' : 'td';
        tableHtml += '<tr>' + cells.map(c => `<${tag}>${formatInlineText(c.trim())}</${tag}>`).join('') + '</tr>';
      } else {
        if (inTable) {
          tableHtml += '</tbody></table>';
          outputLines.push(tableHtml);
          inTable = false;
          tableHtml = '';
        }
        outputLines.push(line);
      }
    }
    if (inTable) {
      tableHtml += '</tbody></table>';
      outputLines.push(tableHtml);
    }

    text = outputLines.join('\n');

    // 3. Headers: #, ##, ###
    text = text.replace(/^### (.*$)/gim, '<h4 class="text-sm font-orbitron font-bold text-cyan-400 mt-2 mb-1 tracking-wider">▸ $1</h4>');
    text = text.replace(/^## (.*$)/gim, '<h3 class="text-base font-orbitron font-bold text-cyan-300 mt-3 mb-1.5 tracking-wider border-b border-cyan-500/20 pb-1">■ $1</h3>');
    text = text.replace(/^# (.*$)/gim, '<h2 class="text-lg font-orbitron font-black text-cyan-200 mt-4 mb-2 tracking-widest border-b border-cyan-400/40 pb-1">✦ $1</h2>');

    // 4. Bullet lists: - item, * item, • item
    text = text.replace(/^[\*\-•]\s+(.*$)/gim, '<div class="flex items-start gap-2 my-1"><span class="text-cyan-400 font-bold shrink-0">▸</span><span class="text-slate-200">$1</span></div>');

    // 5. Bold & inline elements
    text = formatInlineText(text);

    // 6. Paragraph breaks
    text = text.replace(/\n{2,}/g, '<div class="my-2"></div>');
    text = text.replace(/\n/g, '<br/>');

    return text;
  }

  /** Thời gian giữ popup SAU KHI đọc xong: tối thiểu 10s, dài thì theo độ dài
   *  nội dung (~16 ký tự/giây), tối đa 60s. Trong lúc trợ lý còn đang đọc thì
   *  không đếm — trước đây cố định 10s nên popup tắt khi còn đang đọc dở. */
  const HUD_CARD_MIN_MS = 10000;
  const HUD_CARD_MAX_MS = 60000;
  let hudCardDurationMs = HUD_CARD_MIN_MS;
  let hudCountdownInterval = null;
  let hudCardRemainingSec = 10;
  let hudCardIsPinned = false;
  let hudCardStartTime = 0;
  let hudCardListenersAttached = false;

  function attachHudCardHoverListeners() {
    if (hudCardListenersAttached) return;
    const cardEl = document.getElementById('hud-display-card');
    if (!cardEl) return;
    hudCardListenersAttached = true;

    // Hover to temporarily pause countdown, mouseleave to resume
    cardEl.addEventListener('mouseenter', () => {
      if (!hudCardIsPinned) {
        const badgeEl = document.getElementById('hud-countdown-badge');
        if (badgeEl) {
          badgeEl.textContent = '⏸ Đang giữ (Hover)';
          badgeEl.className = 'px-2.5 py-0.5 rounded text-[10px] font-mono bg-cyan-500/30 border border-cyan-300 text-cyan-200 font-bold';
        }
      }
    });

    cardEl.addEventListener('mouseleave', () => {
      if (!hudCardIsPinned) {
        updateHudCountdownUI(hudCardRemainingSec / 10);
      }
    });
  }

  function showHudDisplayCard(displayText, queryText = '', opts = {}) {
    if (!displayText || typeof displayText !== 'string' || !displayText.trim()) return;
    const cardEl = document.getElementById('hud-display-card');
    const contentEl = document.getElementById('hud-display-content');
    const queryEl = document.getElementById('hud-display-query');
    const pinBtn = document.getElementById('hud-display-pin-btn');
    if (!cardEl || !contentEl) return;

    attachHudCardHoverListeners();

    if (queryEl && queryText) {
      queryEl.textContent = `Lệnh: ${queryText}`;
    }

    if (typeof opts.spokenUpTo === 'number' && opts.spokenUpTo > 0) {
      const done = displayText.slice(0, opts.spokenUpTo);
      const rest = displayText.slice(opts.spokenUpTo);
      contentEl.innerHTML =
        `<span class="text-emerald-300 font-semibold">${renderHudMarkdown(done)}</span>`
        + (rest ? `<span class="opacity-40">${renderHudMarkdown(rest)}</span>` : '');
    } else {
      contentEl.innerHTML = renderHudMarkdown(displayText);
    }

    if (opts.silent) {
      let warn = cardEl.querySelector('.hud-silent-warn');
      if (!warn) {
        warn = document.createElement('p');
        warn.className = 'hud-silent-warn mt-2 text-[10px] text-amber-400/90 font-mono';
        cardEl.appendChild(warn);
      }
      warn.textContent = '⚠ Câu này không có tiếng — máy chủ không sinh được audio (TTS lỗi hoặc quá thời gian chờ).';
    }

    cardEl.classList.remove('hidden');

    hudCardDurationMs = Math.min(HUD_CARD_MAX_MS, Math.max(HUD_CARD_MIN_MS, displayText.length * 60));
    // Reset pin status and restart countdown whenever a new request/response appears
    hudCardIsPinned = false;
    if (pinBtn) pinBtn.textContent = '⏸ TẠM DỪNG';
    startHudCardCountdown();
  }

  function startHudCardCountdown() {
    clearHudCardTimers();
    hudCardRemainingSec = Math.ceil(hudCardDurationMs / 1000);
    hudCardStartTime = Date.now();
    updateHudCountdownUI(1);

    hudCountdownInterval = setInterval(() => {
      const cardEl = document.getElementById('hud-display-card');
      if (cardEl && cardEl.matches(':hover') && !hudCardIsPinned) {
        return; // Paused while hovering
      }
      if (hudCardIsPinned) {
        return; // Paused while pinned
      }
      if (hudIsSpeaking()) {
        // Đang đọc: chưa đếm, bắt đầu đếm từ lúc đọc xong.
        hudCardStartTime = Date.now();
        hudCardRemainingSec = Math.ceil(hudCardDurationMs / 1000);
        updateHudCountdownUI(1, true);
        return;
      }

      const elapsed = Date.now() - hudCardStartTime;
      const leftMs = Math.max(0, hudCardDurationMs - elapsed);
      hudCardRemainingSec = Math.ceil(leftMs / 1000);
      const ratio = leftMs / hudCardDurationMs;

      updateHudCountdownUI(ratio);

      if (leftMs <= 0) {
        clearHudCardTimers();
        hideHudDisplayCard();
      }
    }, 100);
  }

  function updateHudCountdownUI(ratio = 1, speaking = false) {
    const badgeEl = document.getElementById('hud-countdown-badge');
    const progressEl = document.getElementById('hud-display-progress');
    if (badgeEl) {
      if (speaking && !hudCardIsPinned) {
        badgeEl.textContent = '🔊 Đang đọc...';
        badgeEl.className = 'px-2.5 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 border border-emerald-400/60 text-emerald-300 font-bold';
      } else if (hudCardIsPinned) {
        badgeEl.textContent = '📌 ĐÃ GHIM';
        badgeEl.className = 'px-2.5 py-0.5 rounded text-[10px] font-mono bg-amber-500/30 border border-amber-400 text-amber-200 font-bold';
      } else {
        badgeEl.textContent = `⏱ Tự đóng: ${hudCardRemainingSec}s`;
        badgeEl.className = 'px-2.5 py-0.5 rounded text-[10px] font-mono bg-cyan-500/20 border border-cyan-400/60 text-cyan-300 font-bold';
      }
    }
    if (progressEl) {
      progressEl.style.width = hudCardIsPinned ? '100%' : `${Math.max(0, Math.min(100, ratio * 100))}%`;
    }
  }

  function clearHudCardTimers() {
    if (hudDisplayDismissTimer) {
      clearTimeout(hudDisplayDismissTimer);
      hudDisplayDismissTimer = null;
    }
    if (hudCountdownInterval) {
      clearInterval(hudCountdownInterval);
      hudCountdownInterval = null;
    }
  }

  function hideHudDisplayCard() {
    const cardEl = document.getElementById('hud-display-card');
    if (cardEl) {
      cardEl.classList.add('hidden');
    }
    clearHudCardTimers();
  }

  function togglePinHudDisplay() {
    hudCardIsPinned = !hudCardIsPinned;
    const pinBtn = document.getElementById('hud-display-pin-btn');
    if (pinBtn) {
      pinBtn.textContent = hudCardIsPinned ? '▶ TIẾP TỤC' : '⏸ TẠM DỪNG';
    }
    if (!hudCardIsPinned) {
      hudCardStartTime = Date.now() - ((10 - hudCardRemainingSec) * 1000);
    }
    updateHudCountdownUI(hudCardRemainingSec / 10);
  }

  function copyHudDisplayContent() {
    const contentEl = document.getElementById('hud-display-content');
    if (contentEl) {
      navigator.clipboard.writeText(contentEl.innerText || contentEl.textContent || '')
        .then(() => {
          appendSystemLog('Đã sao chép nội dung chi tiết vào Clipboard.', 'SYS');
          hudShowToast('📋 Đã sao chép báo cáo vào Clipboard thành công!', 'success', 10000);
        })
        .catch(() => {
          hudShowToast('❌ Không thể truy cập clipboard.', 'error', 5000);
        });
    }
  }

  function hudShowToast(message, type = 'info', duration = 10000) {
    const container = document.getElementById('hud-toast-container');
    if (!container) return;
    const toast = document.createElement('div');
    const borderCol = type === 'error' ? 'border-rose-500/80 text-rose-300 bg-rose-950/90' :
                      type === 'warning' ? 'border-amber-500/80 text-amber-300 bg-amber-950/90' :
                      type === 'success' ? 'border-emerald-500/80 text-emerald-300 bg-emerald-950/90' :
                      'border-cyan-400/80 text-cyan-200 bg-[#020a16]/95';
    const icon = type === 'error' ? '❌' : type === 'warning' ? '⚠️' : type === 'success' ? '✅' : 'ℹ️';

    toast.className = `hud-toast border ${borderCol}`;
    toast.innerHTML = `
      <span class="text-sm shrink-0">${icon}</span>
      <span class="flex-1 text-xs font-mono break-words leading-tight">${message}</span>
      <button onclick="this.parentElement.remove()" class="text-slate-400 hover:text-white text-xs px-1" title="Đóng">✕</button>
      <div class="hud-toast-progress" style="animation-duration: ${duration}ms"></div>
    `;
    container.appendChild(toast);
    setTimeout(() => {
      if (toast.parentElement) toast.remove();
    }, duration);
  }

  window.showHudDisplayCard = showHudDisplayCard;
  window.hideHudDisplayCard = hideHudDisplayCard;
  window.copyHudDisplayContent = copyHudDisplayContent;
  window.togglePinHudDisplay = togglePinHudDisplay;
  window.hudShowToast = hudShowToast;

  /**
   * Phase 65: nhận trạng thái hội thoại từ server và bật/tắt vòng lặp.
   *
   * Cố ý không dùng `audio_base64` để quyết định "đã nói xong": audio về từng
   * câu, nên lúc câu đầu tiên tới thì Ly Ly còn đang nói. Chờ hết âm thanh rồi
   * mới mở mic, nếu không sẽ thu âm luôn giọng Ly Ly và nhận dạng nhầm.
   */
  function handleVoiceState(packet) {
    if (packet.closed) {
      // Máy chủ đã kết thúc hội thoại (admin nói không còn yêu cầu, hoặc đã
      // chào sau 30s im lặng). Lời chào có thể còn đang phát — để nó phát hết.
      hudResetConversation();
      hudStopListeningForTurn();
      if (!hudIsSpeaking()) setHudState('idle', 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...');
      return;
    }
    if (packet.expecting_reply && packet.question) {
      hudExpectReply(packet.question);
    } else {
      hudResetConversation();
    }
  }

  function handleSpeakingEvent(packet) {
    const text = packet.text || '';
    const audioB64 = packet.audio_base64;
    const sourceDevice = packet.source_device || '';
    const now = Date.now();

    // De-duplication: Prevent playing identical audio if received within 2.5s (e.g. from REST + WS broadcast)
    if (text && text === lastSpokenText && (now - lastSpokenTime < 2500)) {
      return;
    }
    lastSpokenText = text;
    lastSpokenTime = now;
    hudRememberSpoken(text);

    // Phân chia nguồn phát âm thanh rõ ràng:
    // 1. Nếu lệnh xuất phát từ Robot (esp32, xiaozhi, robot), Web dashboard hoặc nguồn khác (không phải hud):
    //    Âm thanh AI PHẢI phát ra từ chính loa của thiết bị đó (Robot phát qua loa robot).
    //    HUD CHỈ hiển thị trạng thái đồ hoạ / phụ đề thị giác, TUYỆT ĐỐI KHÔNG PHÁT TIẾNG trên HUD.
    // 2. Chỉ khi ra lệnh từ HUD (sourceDevice === 'hud' hoặc rỗng):
    //    Âm thanh AI mới phát ra từ loa HUD.
    if (sourceDevice && sourceDevice !== 'hud') {
      setHudState('speaking', text, text.length * 65);
      if (packet.display_text) {
        showHudDisplayCard(packet.display_text, packet.query || packet.text, {
          silent: true,
          spokenUpTo: text ? text.length : 0,
        });
      }
      return;
    }

    if (packet.has_audio && !audioB64) {
      // Tiếng của câu này tới ngay sau bằng khung nhị phân (_hudAudioQueue).
      // Trước đây gói chữ không có `audio_base64` bị coi là "không có tiếng":
      // hiện cảnh báo, nhảy về idle rồi lại "đang nói" khi tiếng tới.
      if (packet.display_text && text) {
        const at = packet.display_text.indexOf(text);
        showHudDisplayCard(packet.display_text, '', { spokenUpTo: at >= 0 ? at + text.length : 0 });
      }
      return;
    }

    if (audioB64 && hudAudioEnabled) {

      // Xếp hàng, KHÔNG phát ngay — xem giải thích ở khai báo hàng đợi.
      //
      // Phase 81: mang theo `spokenUpTo` = vị trí đã đọc tới trong display_text.
      // Lý do: `display_text` là TOÀN BỘ câu trả lời, và LLM stream nhanh hơn
      // TTS nhiều lần. Trước đây card hiện hết câu trả lời ngay khi gói tin
      // đầu tiên tới, còn tiếng còn đang nói câu đầu — nên chữ luôn chạy
      // trước tiếng, và người đọc không biết Ly Ly đang đọc tới đâu.
      // Nay vị trí đọc được đẩy xuống cùng lúc phát audio.
      hudSpeechQueue.push({
        text: text,
        audioB64: audioB64,
        spokenUpTo: packet.display_text ? (packet.display_text.indexOf(text) + text.length) : null,
        displayText: packet.display_text || '',
      });
      drainSpeechQueue();
    } else {
      // KHÔNG có audio. Trước đây vẫn bật trạng thái "đang nói" và chạy chữ
      // chạy với thời lượng ĐOÁN (text.length * 65) — nhìn thì như đang nói
      // trong khi không có tiếng nào. Đó là "hiệu ứng phát thanh hiện trước,
      // lời nói chẳng bao giờ tới".
      // Nay hiện chữ và nói rõ là không có tiếng.
      isAudioPlaying = false;
      if (packet.display_text) {
        showHudDisplayCard(packet.display_text, packet.query || packet.text, {
          silent: true,
          spokenUpTo: text ? text.length : 0,
        });
      }
      setHudState('idle', text ? `⚠ ${text} (không có tiếng — TTS không sinh được audio)` : 'Không có tiếng');
    }
  }

  /**
   * Phát lần lượt các câu đang xếp hàng.
   *
   * Một câu chỉ kết thúc khi thật sự hết, rồi mới lấy câu kế tiếp ra phát.
   * `onerror` cũng phải mở câu kế tiếp — nếu không, một câu hỏng sẽ kẹt cả
   * lượt nói về sau, và người dùng chỉ thấy HUD đứng yên.
   */
  function drainSpeechQueue() {
    if (hudSpeechDraining) return;
    const item = hudSpeechQueue[0];
    if (!item) return;

    hudSpeechDraining = true;
    const text = item.text;
    const audioB64 = item.audioB64;

    const next = () => {
      hudSpeechQueue.shift();
      hudSpeechDraining = false;
      if (hudSpeechQueue.length) {
        // Để một nhịp ngắn cho AudioContext kịp giải phóng, tránh kẹt khi
        // hai câu liền nhau quá ngắn.
        setTimeout(drainSpeechQueue, 40);
      } else {
        isAudioPlaying = false;
        playCyberChime('ready');
        setHudState('idle', 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...');
      }
    };

    try {
      initWebAudio();
      if (audioCtx && audioCtx.state === 'suspended') {
        audioCtx.resume().catch(() => {});
      }

      // Ngắt Web Audio stream nếu đang phát để tránh 2 giọng chồng chéo
      if (_hudAudioQueue && _hudAudioQueue.isPlaying) {
        _hudAudioQueue.stop();
      }

      const el = document.getElementById('hud-audio-stream');
      const player = el || new Audio();

      // KHÔNG pause() ở đây. Đó chính là chỗ cắt ngang câu đang nói.
      player.src = 'data:audio/mp3;base64,' + audioB64;
      // Áp dụng âm lượng từ cấu hình Voice Studio (cập nhật qua audio_config_updated)
      player.volume = Math.max(0, Math.min(1, hudTtsConfig.volume / 100));
      currentVoiceAudio = player;
      // KHÔNG bật `isAudioPlaying` ở đây. Trình duyệt còn phải tải và giải mã
      // mp3 mới ra tiếng; bật sớm thì sóng âm và nhãn "đang nói" chạy trước
      // khi có âm thanh — đúng cảm giác "hiệu ứng trước, tiếng sau".
      // Chỉ bật trong `onplay`, tức khi âm thật sự bắt đầu.

      player.onplay = () => {
        isAudioPlaying = true;
        const dur = player.duration;
        setHudState('speaking', text,
          (dur && !isNaN(dur) && dur > 0) ? dur * 1000 : text.length * 65);
        // Chuyển vị trí đọc trên card sang câu đang phát, để chữ bám theo tiếng.
        if (item.spokenUpTo) {
          showHudDisplayCard(item.displayText, '', { spokenUpTo: item.spokenUpTo });
        }
      };
      player.onended = next;
      player.onerror = () => {
        appendSystemLog('Không phát được một câu — bỏ qua, đọc tiếp.', 'WARNING');
        next();
      };

      const p = player.play();
      if (p !== undefined) {
        p.catch(err => {
          // Trình duyệt chặn phát tự động: HUD chưa từng được tương tác.
          // Giữ câu hiển thị, đừng nuốt lượt nói.
          console.warn('[HUD Audio] Bị chặn phát, cần bấm HUD một lần:', err);
          hudSpeechDraining = false;
          hudSpeechQueue.shift();
          setHudState('speaking', text, text.length * 65);
        });
      }
    } catch (err) {
      console.warn('[HUD Audio] Lỗi phát audio:', err);
      next();
    }
  }

  /**
   * Dừng ngay lời đang nói và xoá sạch hàng đợi.
   *
   * Vì sao cần (người dùng phản ánh: ra lệnh mới nhưng AI phải đọc hết câu cũ
   * rồi mới dừng, mất cảm giác tương tác):
   * Trước đây lệnh mới chỉ được gửi đi, không đụng tới audio đang phát. HUD
   * cứ phát nốt hết hàng đợi của lượt cũ rồi mới tới lượt mới — người dùng
   * nói xong vẫn phải nghe tiếp vài giây, đúng như bị cản.
   *
   * Phải xoá CẢ HÀNG ĐỢI chứ không chỉ `pause()` câu đang phát: các câu sau
   * nó đã được tải sẵn và vẫn nằm trong `hudSpeechQueue`, nên chỉ dừng câu
   * hiện tại thì lượt mới lại phải chờ chúng phát hết.
   *
   * Gọi ở mọi đường vào của lệnh mới: WebSocket, REST dự phòng, và cả nút
   * MIC — không phụ thuộc lệnh đến từ đâu.
   */
  function hudStopSpeaking() {
    // Dừng bộ phát MP3 (_hudAudioQueue, web/voice-audio-queue.js)
    _hudAudioQueue.stop();
    // Bỏ handler trước rồi mới dừng: `onended` giữ lại sẽ gọi `next()` khi
    // audio bị cắt, đẩy câu cũ vào lượt mới đang phát.
    if (currentVoiceAudio) {
      currentVoiceAudio.onended = null;
      currentVoiceAudio.onerror = null;
      try {
        currentVoiceAudio.pause();
        // Xoá `src` để trình duyệt ngắm ngải lượt phát, tránh câu cũ còn
        // kẹt ở đệm phát khi có câu mới chen vào giữa.
        currentVoiceAudio.removeAttribute('src');
        currentVoiceAudio.load();
      } catch (e) { /* phần tử đã bị tháo — không sao */ }
      currentVoiceAudio = null;
    }
    const dropped = hudSpeechQueue.length;
    hudSpeechQueue = [];
    // Cờ draining phải về false, nếu không `drainSpeechQueue()` sẽ trả về ngay
    // ở dòng đầu và HÀNG ĐỢI KẸT VĨNH VIỄN — kể cả các lượt sau.
    hudSpeechDraining = false;
    isAudioPlaying = false;
    if (dropped) appendSystemLog(`Đã dừng lời đang nói, bỏ ${dropped} câu chưa phát.`, 'VOICE');
    return dropped;
  }
  window.hudStopSpeaking = hudStopSpeaking;

  function toggleHudAudio() {
    playCyberChime('click');
    hudAudioEnabled = !hudAudioEnabled;
    const soundBtn = document.getElementById('hud-sound-btn');
    if (soundBtn) {
      if (hudAudioEnabled) {
        soundBtn.textContent = '[🔊 SOUND: BẬT]';
        soundBtn.className = 'hud-btn border-emerald-500/60 bg-emerald-950/40 text-emerald-300 hover:border-emerald-400';
      } else {
        soundBtn.textContent = '[🔇 SOUND: TẮT]';
        soundBtn.className = 'hud-btn border-rose-500/60 bg-rose-950/40 text-rose-300 hover:border-rose-400';
        // Dùng chung hàm dừng với lệnh mới: cùng một chỗ xử lý nên không có
        // chỗ nào dừng được mà chỗ kia không.
        hudStopSpeaking();
      }
    }
    appendSystemLog(`HUD Audio Output: ${hudAudioEnabled ? 'ENABLED' : 'MUTED'}`, 'SYS');
  }
  window.toggleHudAudio = toggleHudAudio;

  // ---------------------------------------------------------------------------
  // 11. STANDBY HUD DIRECT MICROPHONE (WEB SPEECH API)
  // ---------------------------------------------------------------------------
  let hudSpeechRecognition = null;
// ═══════════════════════════════════════════════════════════════════════════
// ── Phase 65: Vòng lặp hội thoại HUD ────────────────────────────────────────
//
// Sửa ba lỗi admin phản ánh:
//   1. Lệnh bị lặp lại  → onend đọc transcript từ phần tử HIỂN THỊ thay vì
//      từ sự kiện nhận dạng. Mọi thứ ghi vào đó (kể cả câu trả lời của AI đang
//      chạy chữ) đều bị gửi lại như lệnh của admin.
//   2. Phản hồi chậm    → không có vòng lặp: sau khi AI hỏi, admin phải bấm
//      MIC lại mỗi vòng.
//   3. Không hỏi lại    → không đo được "admin đã im bao lâu", nên không biết
//      khi nào phải hỏi lại rồi bỏ cuộc.
// ═══════════════════════════════════════════════════════════════════════════

/** Transcript CHÍNH XÁC của lượt nghe hiện tại, lấy từ sự kiện nhận dạng. */
let hudFinalTranscript = '';

/* MẤT QUYỀN MIC — Phase 69
 *
 * Web Speech API của Chrome tự huỷ phiên khi trang im lặng quá lâu, và cũng
 * tự huỷ khi bị bật/tắt liên tục. Khi đó nó trả về `not-allowed` hoặc
 * `service-not-allowed` — tức KHÔNG còn là "chưa cấp quyền" mà là "trình
 * duyệt đã thu hồi".
 *
 * Trước đây mã chỉ ghi một dòng log rồi quay về trạng thái MIC bình thường,
 * nên HUD trông như sẵn sàng trong khi mic đã chết. Người dùng thấy "mất
 * quyền" mà không biết phải làm gì, và bấm MIC cũng vô ích vì vẫn dùng lại
 * đúng instance đã hỏng.
 */
let hudMicBlocked = false;

/**
 * Dựng instance nhận dạng mới.
 *
 * Instance cũ sau khi bị Chrome thu hồi quyền sẽ không hoạt động lại được —
 * Chrome giữ nguyên trạng thái lỗi trên instance đó. Dựng cái mới là cách
 * duy nhất lấy lại phiên, và cũng là cách kích hoạt lại hộp thoại cấp quyền.
 */
function hudBuildRecognition() {
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRec) return false;
  const rec = new SpeechRec();
  rec.lang = 'vi-VN';
  rec.continuous = false;
  rec.interimResults = true;
  hudSpeechRecognition = rec;
  return true;
}

/**
 * Gắn bốn sự kiện vào instance nhận dạng.
 *
 * Tách riêng khỏi toggleHudMic vì instance phải dựng lại được nhiều lần:
 * Chrome thu hồi quyền thì instance cũ không hồi sinh được, và vòng lặp hội
 * thoại cũng cần mở mic lại mà không đi qua nút bấm.
 */
function hudAttachRecognitionHandlers(rec) {
  rec.onstart = () => {
    // Mic mở được tức quyền đã cấp lại — xoá cờ chặn, nếu không lần mở
    // nối tiếp sau sẽ lại bị bỏ qua vì cờ còn dính.
    hudMicBlocked = false;
    isHudListening = true;
    // Xoá transcript của lượt trước. Lượt mới phải bắt đầu từ rỗng, nếu
    // không thì khi lượt này không nhận dạng được gì, onend sẽ gửi lại
    // lệnh cũ — đúng triệu chứng admin phản ánh.
    hudFinalTranscript = '';
    const micBtn = document.getElementById('hud-mic-btn');
    if (micBtn) {
      micBtn.className = 'hud-btn border-rose-500 bg-rose-950/60 text-rose-300 shadow-[0_0_15px_#f43f5e] animate-pulse';
      micBtn.innerHTML = '<span class="w-2 h-2 rounded-full bg-rose-400"></span> [🔴 LẮNG NGHE...]';
    }
    setHudState('listening', 'Microphone active. Đang lắng nghe giọng nói của bạn...');
    appendSystemLog('Microphone input active. Listening for Vietnamese speech...', 'VOICE');
        };

        rec.onresult = (event) => {
    let interimText = '';
    let finalText = '';
    for (let i = event.resultIndex; i < event.results.length; ++i) {
    if (event.results[i].isFinal) {
    finalText += event.results[i][0].transcript;
    } else {
    interimText += event.results[i][0].transcript;
    }
    }
    // Chỉ ghi đè khi có kết quả FINAL. Interim có thể rỗng hoặc sai, đọc
    // nó rồi gửi đi là gửi lệnh sai.
    if (finalText) hudFinalTranscript = finalText.trim();

    const activeTranscript = finalText || interimText;
    if (activeTranscript) {
    if (typewriterTextEl) typewriterTextEl.textContent = `"${activeTranscript}"`;
    }
        };

        rec.onerror = (event) => {
    console.warn('[HUD Mic] Lỗi:', event.error);
    // `audio-capture`: không có micro nào. `not-allowed` và
    // `service-not-allowed`: trình duyệt đã thu hồi quyền sau khi đã cấp
    // — hoặc chưa bao giờ được cấp. Trước đây cả ba bị gộp làm một,
    // nên người dùng không phân biệt được và tưởng hệ thống hỏng.
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed'
    || event.error === 'audio-capture') {
    // Vứt instance đã hỏng. Giữ lại thì mọi lần thử sau đều nhận lại
    // đúng lỗi này mà không bao giờ được cấp quyền lại.
    try { if (hudSpeechRecognition) hudSpeechRecognition.abort(); } catch (_) { /* bỏ qua */ }
    hudSpeechRecognition = null;
    hudMarkMicBlocked(event.error);
    } else if (event.error !== 'no-speech') {
    appendSystemLog(`Microphone warning: ${event.error}`, 'WARNING');
    }
        };

        rec.onend = () => {
    isHudListening = false;
    const micBtn = document.getElementById('hud-mic-btn');
    // KHÔNG đụng tới nút nếu mic đang bị thu hồi quyền. Chrome bắn `onerror`
    // rồi bắn `onend` ngay sau, nên nếu ở đây reset nút về trạng thái MIC
    // bình thường thì cảnh báo "mất quyền" bị xoá sạch trong khoảng 1ms —
    // HUD trông như sẵn sàng trong khi mic đã chết. Đó chính là triệu chứng
    // admin phản ánh.
    if (micBtn && !hudMicBlocked) {
    micBtn.className = 'hud-btn flex items-center gap-1.5 border-cyan-400/60 bg-cyan-950/40 text-cyan-300';
    micBtn.innerHTML = '<span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span> [🎙️ MIC]';
    }

    // Lấy transcript từ SỰ KIỆN nhận dạng, không phải từ phần tử hiển thị.
    // Phần tử đó bị ghi đè bởi nhiều thứ trong lúc chờ: câu trả lời của AI
    // đang chạy chữ, trạng thái, log... đọc từ đó là gửi lại chính lời AI
    // như lệnh của admin, và đó là nguyên nhân lệnh bị lặp.
    const recognizedText = hudFinalTranscript;
    hudFinalTranscript = '';

    if (recognizedText && recognizedText.length >= 2) {
    if (hudIsEcho(recognizedText)) {
    // Mic thu lại chính lời trợ lý vừa đọc — không phải lệnh của admin.
    appendSystemLog(`Bỏ qua tiếng vọng của trợ lý: "${recognizedText.slice(0, 60)}"`, 'VOICE');
    if (hudAwaitingReply) hudKeepListening();
    return;
    }
    if (hudAwaitingReply && hudIsDoneReply(recognizedText)) {
    hudEndConversationByUser(recognizedText);
    return;
    }
    appendSystemLog(`Recognized speech: "${recognizedText}". Routing to AI Engine...`, 'VOICE');
    // Admin đã đáp -> hết lượt chờ; câu trả lời mới sẽ mở lại vòng hội thoại.
    hudResetConversation();
    sendHudVoiceCommand(recognizedText);
    } else {
    // Không nghe thấy gì (Chrome tự dừng sau vài giây im lặng). Đang chờ trả
    // lời thì mở lại mic cho tới hết 30 giây — chưa đóng.
    if (hudAwaitingReply) {
    hudKeepListening();
    } else {
    setHudState('idle', 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...');
    }
    }
        };
}

/* ── ĐỌC QUYỀN MIC THẬT — Phase 70 ──────────────────────────────────────────
 *
 * Phase 69 đã tách ba mã lỗi riêng, nhưng vẫn cho cả ba cùng một lời khuyên:
 * "nhấn nút MIC để cấp lại". Đo trên trình duyệt thật cho thấy lời khuyên đó
 * SAI với phần lớn các ca:
 *
 *   not-allowed + quyền = prompt  → bấm được, Chrome hiện hộp thoại
 *   not-allowed + quyền = denied  → bấm mãi cũng KHÔNG được. Chrome đã chặn
 *                                   vĩnh viễn, không bao giờ hỏi lại lần nữa.
 *                                   Phải vào cài đặt trình duyệt.
 *   service-not-allowed            → KHÔNG liên quan quyền mic. Dịch vụ
 *                                   nhận dạng giọng nói bị tắt.
 *   audio-capture                  → KHÔNG liên quan quyền mic. Máy không
 *                                   có micro nào.
 *
 * Người dùng bấm MIC đi bấm lại, không bao giờ được gì, rồi kết luận hệ
 * thống hỏng — đúng cảm giác "mic mất quyền từ hub".
 *
 * Nay đọc quyền thật từ `navigator.permissions` rồi nói đúng việc cần làm.
 */
let hudMicPermission = 'unknown';
let hudMicPermissionWatch = null;

/**
 * Hỏi trình duyệt quyền mic hiện đang ở thế nào.
 *
 * Trả về 'granted' | 'denied' | 'prompt' | 'unknown'. Không bao giờ ném lỗi:
 * Firefox không hỗ trợ tên quyền 'microphone' và sẽ ném TypeError, mà lỗi ở
 * đây chỉ có nghĩa là "không đọc được" chứ không phải hỏng.
 */
function hudRefreshMicPermission() {
  return new Promise(function (resolve) {
    if (!navigator.permissions || !navigator.permissions.query) {
      hudMicPermission = 'unknown';
      return resolve('unknown');
    }
    navigator.permissions.query({ name: 'microphone' }).then(function (st) {
      hudMicPermission = st.state;

      // Theo dõi thay đổi. Người dùng đi vào cài đặt trình duyệt gỡ chặn rồi
      // quay lại tab này thì HUD tự biết — không bắt họ bấm MIC lần nữa.
      if (!hudMicPermissionWatch) {
        hudMicPermissionWatch = st;
        st.onchange = function () {
          hudMicPermission = st.state;
          if (st.state === 'granted' && hudMicBlocked) {
            hudMicBlocked = false;
            appendSystemLog('Quyền micro đã được cấp lại. Nhấn MIC để bắt đầu nghe.', 'VOICE');
            hudMarkMicIdle();
          }
        };
      }
      resolve(st.state);
    }).catch(function () {
      hudMicPermission = 'unknown';
      resolve('unknown');
    });
  });
}

/** Nút MIC về trạng thái chờ, kèm nhãn dựa trên việc bấm có dùng được không. */
function hudMarkMicIdle() {
  const micBtn = document.getElementById('hud-mic-btn');
  if (!micBtn) return;
  micBtn.className = 'hud-btn flex items-center gap-1.5 border-cyan-400/60 bg-cyan-950/40 text-cyan-300';
  micBtn.innerHTML = '<span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span> [🎙️ MIC]';
}

/**
 * Lời khuyên đúng cho từng nguyên nhân.
 *
 * Nguyên tắc: không được hứa điều mà bấm nút không làm được. Nếu người dùng
 * làm theo mà không khỏi thì họ sẽ nghĩ phần mềm hỏng, chứ không nghĩ mình
 * đang làm sai.
 */
function hudMicAdvice(reason) {
  if (reason === 'audio-capture') {
    return {
      label: '[🎤 KHÔNG CÓ MIC]',
      message: 'Máy không tìm thấy micro nào. Cắm micro vào rồi nhấn MIC lại.',
      log: 'Không tìm thấy micro nào trên máy. Cấp quyền không giúp được — cần cắm micro.',
    };
  }
  if (reason === 'service-not-allowed') {
    return {
      label: '[🔌 DỊCH VỤ TẮT]',
      message: 'Trình duyệt đang tắt dịch vụ nhận dạng giọng nói. Không phải lỗi quyền micro.',
      log: 'Dịch vụ nhận dạng giọng nói bị chặn (service-not-allowed) — không liên quan quyền micro.',
    };
  }
  // not-allowed: cần biết quyền đang ở prompt hay denied mới nói được.
  if (hudMicPermission === 'denied') {
    return {
      label: '[⛔ ĐÃ CHẶN VĨNH VIỄN]',
      message: 'Trình duyệt đã chặn micro vĩnh viễn — bấm nút này sẽ không được. '
        + 'Vào thanh địa chỉ → biểu tượng ổ khóa → Quyền → Microphone → Cho phép, rồi tải lại trang.',
      log: 'Quyền micro đang ở trạng thái "denied" (bị chặn vĩnh viễn). '
        + 'Bấm nút MIC không có tác dụng — cần mở khoá trong cài đặt trình duyệt.',
    };
  }
  return {
    label: '[🔒 CẤP LẠI QUYỀN]',
    message: 'Trình duyệt chưa cấp quyền micro. Nhấn nút MIC để cấp.',
    log: `Mất quyền micro (${reason}). Nhấn nút MIC để cấp lại quyền.`,
  };
}

/** Báo đúng nguyên nhân, kèm đúng việc cần làm — không phải một lời khuyên chung. */
function hudMarkMicBlocked(reason) {
  hudMicBlocked = true;
  isHudListening = false;
  hudRefreshMicPermission().then(function () {
    const advice = hudMicAdvice(reason);
    const micBtn = document.getElementById('hud-mic-btn');
    if (micBtn) {
      micBtn.className = 'hud-btn border-amber-500 bg-amber-950/60 text-amber-300 shadow-[0_0_15px_#f59e0b]';
      micBtn.innerHTML = '<span class="w-2 h-2 rounded-full bg-amber-400"></span> ' + advice.label;
    }
    setHudState('idle', advice.message);
    appendSystemLog(advice.log, 'ERROR');
  });
}

/**
 * Bấm MIC khi đang bị chặn: kiểm tra quyền thật trước, đừng thử lại mù.
 *
 * `start()` với quyền `denied` hỏng ngay lập tức và bắn `not-allowed` lần
 * nữa — mỗi lần bấm lại chỉ khiến Chrome siết phiên thêm, mà không có gì
 * tiến triển. Nói thẳng ra còn hơn bắt thử.
 */
function hudStartMicAfterRegrant() {
  hudRefreshMicPermission().then(function (state) {
    if (state === 'denied') {
      hudMarkMicBlocked('not-allowed');
      appendSystemLog('Vẫn đang bị chặn vĩnh viễn — cần mở khoá trong cài đặt trình duyệt.', 'WARNING');
      return;
    }
    if (!hudBuildRecognition()) return;
    hudAttachRecognitionHandlers(hudSpeechRecognition);
    hudMicBlocked = false;
    try {
      hudSpeechRecognition.start();
    } catch (err) {
      const msg = String(err && err.message);
      if (!/InvalidStateError|already started/i.test(msg)) {
        appendSystemLog(`Không mở được mic: ${msg}`, 'WARNING');
      }
    }
  });
}

/* ── Vòng hội thoại HUD ─────────────────────────────────────────────────────
 *
 * lắng nghe → trả lời → hỏi lại ("Anh còn cần em hỗ trợ gì nữa không ạ?", do
 * máy chủ đọc) → chờ câu tiếp → trả lời tiếp …
 *   - Admin nói "không / thôi / hết rồi / cảm ơn" → đóng lắng nghe NGAY.
 *   - 30 giây không nghe thấy gì → máy chủ chào "Nếu anh không có yêu cầu nào
 *     khác thì tạm biệt…" rồi đóng.
 * Mic chỉ mở khi trợ lý ĐÃ ĐỌC XONG. Trước đây mở ngay khi máy chủ báo xong,
 * lúc loa còn đang đọc: mic thu lại giọng trợ lý và gửi như lệnh mới — trợ lý
 * trả lời chính nó (chào hai lần, "em đã xử lý" hai lần). Lời mic nghe được mà
 * trùng lời trợ lý vừa đọc cũng bị bỏ qua (tiếng vọng).
 */
let hudAwaitingReply = false;
let hudPendingQuestion = '';
let hudReplyDeadline = 0;
let hudReplyTimer = null;
let hudMicOpenTimer = null;

/** Chờ phản hồi bao lâu (ms, tính từ lúc trợ lý hỏi xong) rồi chào và đóng. */
const HUD_REPLY_WAIT_MS = 30000;
const HUD_SESSION_ID = 'hud';

/** Câu "không còn yêu cầu" — cùng quy tắc với `is_stop_reply` phía máy chủ. */
const HUD_DONE_PHRASES = new Set([
  'không', 'ko', 'k', 'không có', 'không có gì', 'không còn', 'không còn gì', 'không cần',
  'không cần đâu', 'hết', 'xong', 'vậy thôi', 'thôi', 'dừng', 'dừng lại', 'hủy', 'huỷ',
  'bỏ qua', 'tạm dừng', 'đủ', 'cảm ơn', 'cám ơn', 'tạm biệt', 'bye', 'stop', 'cancel',
  'never mind', 'no', 'nope', 'không có gì cả', 'không có yêu cầu', 'không có yêu cầu gì',
  'thôi vậy', 'không cần nữa',
]);
const HUD_POLITE = new Set([
  'dạ', 'vâng', 'à', 'ạ', 'a', 'em', 'anh', 'chị', 'nhé', 'nha', 'nhá', 'nhe', 'đâu', 'nữa',
  'rồi', 'được', 'ok', 'oke', 'okay', 'thế', 'vậy', 'là', 'đã', 'cả', 'lắm', 'nhiều',
]);

function hudIsDoneReply(text) {
  const words = String(text || '').toLowerCase().match(/[\p{L}\p{N}]+/gu) || [];
  if (!words.length || words.length > 8) return false;
  const core = words.filter((w) => !HUD_POLITE.has(w)).join(' ');
  return !!core && HUD_DONE_PHRASES.has(core);
}

/** Trợ lý còn đang phát tiếng (khung nhị phân hoặc hàng đợi base64). */
function hudIsSpeaking() {
  return !!(isAudioPlaying
    || (_hudAudioQueue && _hudAudioQueue.isPlaying)
    || (hudSpeechQueue && hudSpeechQueue.length)
    || hudSpeechDraining);
}

/** Lời trợ lý đọc trong 60s gần nhất — để nhận ra tiếng vọng từ loa. */
let hudRecentSpoken = [];
function hudRememberSpoken(text) {
  if (!text) return;
  hudRecentSpoken.push({ text: String(text), at: Date.now() });
  if (hudRecentSpoken.length > 20) hudRecentSpoken.shift();
}
function hudNormalize(s) {
  return String(s || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/đ/g, 'd').replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim();
}
function hudIsEcho(text) {
  const t = hudNormalize(text);
  const words = t.split(' ').filter(Boolean);
  if (words.length < 3) return false;
  const now = Date.now();
  return hudRecentSpoken.some((r) => {
    if (now - r.at > 60000) return false;
    const said = hudNormalize(r.text);
    if (said.includes(t)) return true;
    const saidWords = new Set(said.split(' '));
    return words.filter((w) => saidWords.has(w)).length / words.length >= 0.8;
  });
}

/** Dừng hẳn vòng hội thoại, quay về chờ lệnh mới. */
function hudResetConversation() {
  hudAwaitingReply = false;
  hudPendingQuestion = '';
  hudReplyDeadline = 0;
  if (hudReplyTimer) { clearTimeout(hudReplyTimer); hudReplyTimer = null; }
  if (hudMicOpenTimer) { clearTimeout(hudMicOpenTimer); hudMicOpenTimer = null; }
}

/**
 * Máy chủ báo trợ lý vừa hỏi (`question`) — chờ admin trả lời.
 * `question` rỗng nghĩa là không chờ.
 */
function hudExpectReply(question) {
  hudResetConversation();
  if (!question) {
    hudStopListeningForTurn();
    return;
  }
  hudAwaitingReply = true;
  hudPendingQuestion = question;
  hudOpenMicWhenQuiet();
}

/** Mở mic khi trợ lý đã đọc xong; 30 giây chờ tính từ lúc đó. */
function hudOpenMicWhenQuiet() {
  if (hudMicOpenTimer) { clearTimeout(hudMicOpenTimer); hudMicOpenTimer = null; }
  if (!hudAwaitingReply) return;
  if (hudIsSpeaking()) {
    hudMicOpenTimer = setTimeout(hudOpenMicWhenQuiet, 250);
    return;
  }
  // Một nhịp ngắn cho loa tắt hẳn, kẻo mic thu đuôi câu.
  hudMicOpenTimer = setTimeout(() => {
    hudMicOpenTimer = null;
    if (!hudAwaitingReply) return;
    if (hudIsSpeaking()) { hudOpenMicWhenQuiet(); return; }
    if (!hudReplyDeadline) {
      hudReplyDeadline = Date.now() + HUD_REPLY_WAIT_MS;
      hudReplyTimer = setTimeout(hudOnReplyTimeout, HUD_REPLY_WAIT_MS);
    }
    setHudState('listening', 'Em đang lắng nghe anh/chị...');
    hudOpenMicForFollowup();
  }, 400);
}

/** Mic tự tắt (Chrome dừng sau vài giây im lặng) khi còn trong 30s → mở lại. */
function hudKeepListening() {
  if (!hudAwaitingReply || Date.now() >= hudReplyDeadline) return;
  setHudState('listening', 'Em vẫn đang chờ anh/chị...');
  hudOpenMicWhenQuiet();
}

/** 30 giây không có phản hồi: máy chủ chào rồi đóng. */
function hudOnReplyTimeout() {
  hudReplyTimer = null;
  if (!hudAwaitingReply) return;
  appendSystemLog('Không nghe thấy phản hồi sau 30 giây — chào và đóng lắng nghe.', 'VOICE');
  hudResetConversation();
  hudStopListeningForTurn();
  hudSendControl({ action: 'end_conversation', reason: 'timeout' });
}

/** Admin nói không còn yêu cầu: đóng lắng nghe ngay, không gọi AI. */
function hudEndConversationByUser(text) {
  appendSystemLog(`Không còn yêu cầu ("${text}") — đóng lắng nghe.`, 'VOICE');
  hudResetConversation();
  hudStopListeningForTurn();
  playCyberChime('click');
  setHudState('idle', 'Đang ở trạng thái sẵn sàng lắng nghe chỉ lệnh của bạn...');
  hudSendControl({ action: 'end_conversation', reason: 'user_done' });
}

function hudSendControl(msg) {
  try {
    if (hudSocket && hudSocket.readyState === WebSocket.OPEN) hudSocket.send(JSON.stringify(msg));
  } catch (_) { /* mất kết nối: máy chủ tự hết hạn phiên */ }
}

/** Dừng phiên nghe hiện tại mà không đụng tới vòng hội thoại. */
function hudStopListeningForTurn() {
  try { if (hudSpeechRecognition) hudSpeechRecognition.stop(); } catch (_) { /* đã dừng */ }
  isHudListening = false;
  const micBtn = document.getElementById('hud-mic-btn');
  // Giữ nguyên cảnh báo mất quyền — xem giải thích ở rec.onend.
  if (micBtn && !hudMicBlocked) {
    micBtn.className = 'hud-btn flex items-center gap-1.5 border-cyan-400/60 bg-cyan-950/40 text-cyan-300';
    micBtn.innerHTML = '<span class="w-2 h-2 rounded-full bg-cyan-400 animate-pulse"></span> [🎙️ MIC]';
  }
}

/**
 * Mở mic để nghe câu trả lời nối tiếp, không cần admin bấm.
 *
 * `hudAwaitingReply` là cờ ý định, KHÔNG phải bằng chứng mic đang mở. Kiểm tra
 * riêng `isHudListening`: trước đây điều kiện gộp hai thứ này, nên đúng lúc
 * cần mở gấp nhất — sau khi mic vừa bị Chrome đóng — lại bị bỏ qua.
 */
function hudOpenMicForFollowup() {
  if (isHudListening) return;
  // Mic đã bị thu hồi quyền thì đừng thử lại liên tục: mỗi lần thử lại chỉ
  // khiến Chrome siết phiên thêm. Chờ người dùng bấm nút cấp lại quyền.
  if (hudMicBlocked) return;

  try {
    if (!hudSpeechRecognition) {
      if (!hudBuildRecognition()) return;
      hudAttachRecognitionHandlers(hudSpeechRecognition);
    }
    hudSpeechRecognition.start();
  } catch (err) {
    const msg = String(err && err.message);
    // `start()` khi đã lắng nghe sẽ ném InvalidStateError — vô hại, vì lúc đó
    // mic đã mở sẵn, tức là đã nghe được rồi.
    if (!/InvalidStateError|already started/i.test(msg)) {
      appendSystemLog(`Không mở được mic nối tiếp: ${msg}`, 'WARNING');
    }
  }
}

  let isHudListening = false;

  function toggleHudMic() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      appendSystemLog('Trình duyệt không hỗ trợ Web Speech API cho Microphone.', 'WARNING');
      alert('Trình duyệt không hỗ trợ Web Speech API. Vui lòng dùng Chrome, Edge hoặc Safari.');
      return;
    }

    if (isHudListening) {
      if (hudSpeechRecognition) hudSpeechRecognition.stop();
      isHudListening = false;
      playCyberChime('click');
      return;
    }

    playCyberChime('wake');

    // Dừng phát thanh trước khi mở mic.
    //
    // Nếu không, mic thu luôn giọng Ly Ly đang phát ra loa, nhận dạng nhầm
    // thành lệnh của người dùng, rồi gửi đi — thành vòng lặp lệnh giả. Đây
    // cũng là lý do nhóm tác vụ ở `handleVoiceState` cố tình chờ hết âm
    // thanh mới mở mic.
    //
    // Người dùng bấm MIC là cố ý cất giọng, nên dừng câu đang đọc dở là đúng
    // ý — khác với trường hợp tự mở lại mic sau khi AI hỏi, lúc đó không có
    // gì đang phát nên không mất gì.
    hudStopSpeaking();

    // Đang bị chặn thì phải hỏi lại quyền thật trước. Bấm mù vào `start()`
    // khi quyền đã `denied` chỉ tạo ra thêm một vòng `not-allowed` nữa.
    if (hudMicBlocked) {
      hudStartMicAfterRegrant();
      return;
    }

    try {
      // Instance cũ có thể đã chết sau một phiên dài, hoặc chưa từng có.
      // Dựng mới luôn: instance SpeechRecognition đã từng lỗi thì không tái
      // sử dụng được, Chrome giữ nguyên trạng thái lỗi trên đó.
      if (!hudBuildRecognition()) return;
      hudAttachRecognitionHandlers(hudSpeechRecognition);
      hudMicBlocked = false;

      hudSpeechRecognition.start();
    } catch (err) {
      console.error('[HUD Mic] Failed to start:', err);
      isHudListening = false;
    }
  }
  window.toggleHudMic = toggleHudMic;

  async function sendHudVoiceCommand(query) {
    // Dừng lời đang nói TRƯỚC khi gửi lệnh mới. Đặt ở đầu hàm để mọi đường
    // đi (WebSocket, REST dự phòng) đều được dừng — không phải nhớ dừng ở
    // từng nhánh. Nếu thiếu, người dùng nói lệnh mới vẫn phải nghe hết lượt
    // cũ, đúng triệu chứng "ra lệnh rồi mà AI chưa dừng".
    hudStopSpeaking();
    setHudState('processing', `Đang phân tích câu lệnh: "${query}"...`);

    // 1. Tuyến ưu tiên: Gửi trực tiếp qua kết nối WebSocket bảo mật
    if (hudSocket && hudSocket.readyState === WebSocket.OPEN) {
      try {
        hudSocket.send(JSON.stringify({
          action: 'voice_command',
          query: query,
        }));
        appendSystemLog(`[HUD] Lệnh thoại gửi qua WebSocket Neural Link: "${query}"`, 'VOICE');
        return;
      } catch (wsErr) {
        console.warn('[HUD WS] Lỗi gửi qua socket, chuyển sang REST fallback:', wsErr);
      }
    }

    // 2. Tuyến dự phòng: REST API với JWT Token từ localStorage
    try {
      const token = localStorage.getItem('vnmateai_token') || '';
      const headers = { 'Content-Type': 'application/json' };
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }

      const res = await fetch('/api/v1/voice-command', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({
          query: query,
          source_device: 'hud',
          include_audio: true,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (currentState === STATES.PROCESSING && data.reply) {
        handleSpeakingEvent({
          status: 'speaking',
          text: data.reply,
          audio_base64: data.audio_base64,
        });
      }
    } catch (err) {
      appendSystemLog(`Lỗi gửi câu lệnh thoại: ${err.message}`, 'ERROR');
      setHudState('idle', 'Xin lỗi, không thể kết nối tới máy chủ AI.');
    }
  }

  // ---------------------------------------------------------------------------
  // 11.1. HOLOGRAPHIC QUICK COMMAND BAR & TERMINAL HANDLERS
  // ---------------------------------------------------------------------------
  const cmdBarEl = document.getElementById('hud-cmd-bar');
  const cmdInputEl = document.getElementById('hud-cmd-input');

  function toggleHudCmdBar() {
    playCyberChime('click');
    if (!cmdBarEl) return;
    if (cmdBarEl.classList.contains('hidden')) {
      cmdBarEl.classList.remove('hidden');
      cmdInputEl?.focus();
    } else {
      if (document.activeElement === cmdInputEl) {
        cmdInputEl.blur();
      } else {
        cmdInputEl?.focus();
      }
    }
  }
  window.toggleHudCmdBar = toggleHudCmdBar;

  function submitHudCmd() {
    if (!cmdInputEl) return;
    const query = cmdInputEl.value.trim();
    if (!query) return;
    cmdInputEl.value = '';
    cmdInputEl.blur();
    playCyberChime('process');
    appendSystemLog(`[TERMINAL] Command initiated: "${query}"`, 'VOICE');
    sendHudVoiceCommand(query);
  }
  window.submitHudCmd = submitHudCmd;

  function quickCmd(query) {
    if (!query) return;
    playCyberChime('click');
    if (cmdInputEl) cmdInputEl.value = query;
    appendSystemLog(`[QUICK ACTION] Query triggered: "${query}"`, 'VOICE');
    sendHudVoiceCommand(query);
  }
  window.quickCmd = quickCmd;

  if (cmdInputEl) {
    cmdInputEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        submitHudCmd();
      } else if (e.key === 'Escape') {
        cmdInputEl.blur();
      }
    });
  }

  // ---------------------------------------------------------------------------
  // 12. SYSTEM LOGS MATRIX TAIL
  // ---------------------------------------------------------------------------
  const MAX_LOG_LINES = 80;

  function appendSystemLog(message, level = 'INFO') {
    if (!logStreamEl) return;

    // Phase 76: dòng "chờ kết nối — chưa có sự kiện nào" trong HTML chỉ là
    // trạng thái lúc mới mở trang. Log thật đầu tiên tới là xoá đi, nếu không
    // nó đứng vĩnh viễn ở đầu hàng như một sự kiện đã xảy ra.
    if (logPlaceholderEl && logPlaceholderEl.parentNode) {
      logPlaceholderEl.parentNode.removeChild(logPlaceholderEl);
    }

    const timeStr = new Date().toTimeString().split(' ')[0];
    const item = document.createElement('div');

    let levelBadge = `<span class="text-cyan-400 font-bold">[${level}]</span>`;
    let msgClass = 'text-slate-300';

    if (level === 'ERROR' || level === 'CRITICAL') {
      levelBadge = `<span class="text-red-400 font-bold">[${level}]</span>`;
      msgClass = 'text-red-300 font-semibold';
    } else if (level === 'WARNING') {
      levelBadge = `<span class="text-amber-400 font-bold">[${level}]</span>`;
      msgClass = 'text-amber-200';
    } else if (level === 'SECURITY' || level === 'SEC') {
      levelBadge = `<span class="text-purple-400 font-bold">[SEC]</span>`;
      msgClass = 'text-purple-200';
    } else if (level === 'VOICE') {
      levelBadge = `<span class="text-emerald-400 font-bold">[VOICE]</span>`;
      msgClass = 'text-emerald-200';
    }

    item.className = 'flex items-start gap-1.5 leading-snug';
    item.innerHTML = `<span class="text-slate-500 shrink-0">[${timeStr}]</span> ${levelBadge} <span class="${msgClass} truncate">${message}</span>`;

    logStreamEl.appendChild(item);

    while (logStreamEl.children.length > MAX_LOG_LINES) {
      logStreamEl.removeChild(logStreamEl.firstChild);
    }

    logStreamEl.scrollTop = logStreamEl.scrollHeight;
  }

  // ---------------------------------------------------------------------------
  // 13. STATE SWITCHING
  // ---------------------------------------------------------------------------
  function setHudState(stateKey, customText = '', durationMs = null) {
    const nextState = STATES[stateKey.toUpperCase()] || STATES.IDLE;
    currentState = nextState;
    targetColor = { r: nextState.r, g: nextState.g, b: nextState.b };

    if (statusBadgeEl) {
      statusBadgeEl.textContent = nextState.badge;
      statusBadgeEl.className = `px-5 py-1.5 rounded-full border bg-black/90 font-orbitron font-bold text-xs tracking-[0.2em] shadow-lg transition-all duration-300 ${nextState.badgeClass}`;
    }

    if (voiceDotEl) {
      voiceDotEl.style.backgroundColor = `rgb(${nextState.r}, ${nextState.g}, ${nextState.b})`;
    }

    if (voiceTagEl) {
      voiceTagEl.textContent = nextState.tag;
      voiceTagEl.style.color = `rgb(${nextState.r}, ${nextState.g}, ${nextState.b})`;
    }

    // Phase 76: nhãn này từng ghi cứng "VOICE STREAM ACTIVE" ngay khi mở trang,
    // tức tuyên bố có luồng âm thanh dù mic chưa từng được bật. Nay bám theo
    // trạng thái thật: chỉ "ACTIVE" khi đang nghe hoặc đang nói.
    if (voiceStreamEl) {
      const live = currentState === STATES.LISTENING || currentState === STATES.SPEAKING;
      voiceStreamEl.textContent = live ? 'VOICE STREAM ACTIVE' : 'VOICE STREAM IDLE';
      voiceStreamEl.classList.toggle('is-waiting', false);
    }

    if (customText) {
      setTypewriterText(customText, durationMs);
    }
  }

  function updateAssistantName(name) {
    if (!name || typeof name !== 'string') return;
    currentAiName = name.trim();
    STATES.IDLE.tag = `${currentAiName}:`;
    STATES.SPEAKING.tag = `${currentAiName}:`;

    // Update document title & header
    const docTitle = document.getElementById('hud-doc-title');
    if (docTitle) docTitle.textContent = `VN-MATE AI // TRỢ LÝ AI ${currentAiName.toUpperCase()}`;
    document.title = `VN-MATE AI // TRỢ LÝ AI ${currentAiName.toUpperCase()}`;

    const mainTitle = document.getElementById('hud-main-title');
    if (mainTitle) mainTitle.textContent = `TRỢ LÝ AI ${currentAiName.toUpperCase()}`;

    if (voiceTagEl && (currentState === STATES.IDLE || currentState === STATES.SPEAKING)) {
      voiceTagEl.textContent = `${currentAiName}:`;
    }
  }

  // ---------------------------------------------------------------------------
  // 14. TELEMETRY & CLOCK UPDATERS
  // ---------------------------------------------------------------------------
  function updateClocks() {
    const now = new Date();
    if (clockLocalEl) {
      clockLocalEl.textContent = now.toTimeString().split(' ')[0] + ' ICT';
    }

    if (uptimeEl) {
      const diffSec = Math.floor((Date.now() - startTime) / 1000);
      const hrs = String(Math.floor(diffSec / 3600)).padStart(2, '0');
      const mins = String(Math.floor((diffSec % 3600) / 60)).padStart(2, '0');
      const secs = String(diffSec % 60).padStart(2, '0');
      uptimeEl.textContent = `${hrs}:${mins}:${secs}`;
    }
  }

  setInterval(updateClocks, 1000);

  /**
   * Ghi số đo vào giao diện, hoặc "chờ kết nối" khi chưa có.
   *
   * Phase 76: trước đây mỗi ô có một giá trị bịa riêng — `?? 50` cho số kỹ năng,
   * `?? 0` cho mạch âm thanh, và "NVMe PRIMARY // OPTIMAL" khi không đọc được
   * dung lượng đĩa. Nay không còn ô nào tự chế ra số: số nào không tới thì ô đó
   * nói "chờ kết nối" (và được làm mờ); server lỡ gửi null thì ô đó quay lại
   * trạng thái chờ chứ không giữ số cũ đọng lại như số liệu tối nay.
   */
  function applyMetrics(rawData) {
    if (!rawData) return;
    const data = rawData.hardware ? { ...rawData.hardware, ...rawData } : rawData;

    // ── CPU ────────────────────────────────────────────────────────────────
    if (cpuTextEl) {
      if (_isLive(data.cpu_percent)) {
        const cpu = Math.min(100, Math.max(0, Number(data.cpu_percent)));
        cpuTextEl.textContent = `${cpu.toFixed(1)}%`;
        cpuTextEl.classList.remove('is-waiting');
        if (cpuBarEl) cpuBarEl.style.width = `${cpu}%`;
        // Circumference 238.76
        if (cpuCircleEl) cpuCircleEl.style.strokeDashoffset = CIRCLE_CIRCUMFERENCE * (1 - cpu / 100);
      } else {
        cpuTextEl.textContent = '--';
        cpuTextEl.classList.add('is-waiting');
        if (cpuBarEl) cpuBarEl.style.width = '0%';
        if (cpuCircleEl) cpuCircleEl.style.strokeDashoffset = CIRCLE_CIRCUMFERENCE;
      }
    }

    const procs = data.processes_count ?? data.process_count;
    if (procsTextEl) {
      procsTextEl.textContent = _isLive(procs) ? `PROCS: ${procs}` : 'PROCS: --';
      procsTextEl.classList.toggle('is-waiting', !_isLive(procs));
    }

    const cores = data.cpu_cores ?? data.cores;
    if (coresTextEl) {
      coresTextEl.textContent = _isLive(cores) ? `CORES: ${cores}` : 'CORES: --';
      coresTextEl.classList.toggle('is-waiting', !_isLive(cores));
    }

    // ── RAM ────────────────────────────────────────────────────────────────
    const ramPctVal = data.memory?.percent ?? data.ram_percent;
    if (ramTextEl) {
      if (_isLive(ramPctVal)) {
        const ram = Math.min(100, Math.max(0, Number(ramPctVal)));
        ramTextEl.textContent = `${ram.toFixed(1)}%`;
        ramTextEl.classList.remove('is-waiting');
        if (ramBarEl) ramBarEl.style.width = `${ram}%`;
        if (ramCircleEl) ramCircleEl.style.strokeDashoffset = CIRCLE_CIRCUMFERENCE * (1 - ram / 100);
      } else {
        ramTextEl.textContent = '--';
        ramTextEl.classList.add('is-waiting');
        if (ramBarEl) ramBarEl.style.width = '0%';
        if (ramCircleEl) ramCircleEl.style.strokeDashoffset = CIRCLE_CIRCUMFERENCE;
      }
    }

    const usedGb = data.memory?.used_gb ?? data.ram_used_gb;
    if (ramGbEl) {
      ramGbEl.textContent = _isLive(usedGb) ? `USED: ${usedGb} GB` : 'USED: chờ kết nối';
      ramGbEl.classList.toggle('is-waiting', !_isLive(usedGb));
    }

    const totalGb = data.memory?.total_gb ?? data.ram_total_gb;
    if (ramTotalEl) {
      ramTotalEl.textContent = _isLive(totalGb) ? `TOTAL: ${totalGb} GB` : 'TOTAL: chờ kết nối';
      ramTotalEl.classList.toggle('is-waiting', !_isLive(totalGb));
    }

    // ── Ổ đĩa ──────────────────────────────────────────────────────────────
    const diskPctVal = data.disk?.percent ?? data.disk_percent;
    if (diskTextEl) {
      if (_isLive(diskPctVal)) {
        const disk = Math.min(100, Math.max(0, Number(diskPctVal)));
        diskTextEl.textContent = `${disk.toFixed(1)}%`;
        diskTextEl.classList.remove('is-waiting');
        if (diskBarEl) diskBarEl.style.width = `${disk}%`;
      } else {
        diskTextEl.textContent = 'chờ kết nối';
        diskTextEl.classList.add('is-waiting');
        if (diskBarEl) diskBarEl.style.width = '0%';
      }
    }

    if (diskFreeEl) {
      const freeGb = data.disk_free_gb;
      const diskTotalGb = data.disk_total_gb;
      if (_isLive(freeGb)) {
        diskFreeEl.textContent = `FREE: ${freeGb} GB / ${_isLive(diskTotalGb) ? diskTotalGb : '--'} GB`;
        diskFreeEl.classList.remove('is-waiting');
      } else {
        diskFreeEl.textContent = 'FREE: chờ kết nối';
        diskFreeEl.classList.add('is-waiting');
      }
    }

    // ── Clients / mạch âm thanh / kỹ năng ─────────────────────────────────
    const clients = data.clients_count ?? data.connected_clients ?? data.nodes?.active_web_clients;
    if (clientsCountEl) {
      // 0 là số đo thật (không có client nào đang kết nối) nên vẫn hiện 0.
      clientsCountEl.textContent = _isLive(clients) ? `${clients} ACTIVE` : 'chờ kết nối';
      clientsCountEl.classList.toggle('is-waiting', !_isLive(clients));
    }

    if (audioNodesEl) {
      const aNodes = data.active_audio_hardware ?? data.nodes?.active_audio_hardware;
      audioNodesEl.textContent = _isLive(aNodes) ? `${aNodes} THIẾT BỊ` : 'chờ kết nối';
      audioNodesEl.classList.toggle('is-waiting', !_isLive(aNodes));
    }

    if (skillsCountEl) {
      const sCount = data.skills_count ?? data.nodes?.skills_count;
      skillsCountEl.textContent = _isLive(sCount) ? `${sCount} SKILLS ACTIVE` : 'chờ kết nối';
      skillsCountEl.classList.toggle('is-waiting', !_isLive(sCount));
    }

    // ── Lưu lượng mạng ─────────────────────────────────────────────────────
    if (netIoEl) {
      if (_isLive(data.net_sent_mbps) || _isLive(data.net_recv_mbps)) {
        const up = Number(data.net_sent_mbps || 0).toFixed(1);
        const down = Number(data.net_recv_mbps || 0).toFixed(1);
        netIoEl.innerHTML = '<span class="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-ping"></span> NET: ▲ ' + up + ' MB/s ▼ ' + down + ' MB/s';
        netIoEl.classList.remove('is-waiting');
      } else {
        netIoEl.innerHTML = '<span class="w-1.5 h-1.5 bg-cyan-400 rounded-full animate-ping"></span> NET: chờ kết nối';
        netIoEl.classList.add('is-waiting');
      }
    }

    // Quyền hạn KHÔNG cập nhật ở đây nữa: payload telemetry là bản broadcast
    // chung cho mọi màn hình nên không biết ai đang xem, không thể gán vai trò.
    // Vai trò thật tới từ gói `hud_welcome` riêng của từng kết nối — xem
    // `renderAuthStatus()`.
  }

  /**
   * Vai trò hiển thị trên HUD.
   *
   * Phase 76: trước đây badge ghi cứng "QUYỀN: ADMIN // TOÀN QUYỀN" và dòng
   * phụ ghi cứng "ZERO-TRUST SENTINEL // ONLINE" — ngay cả với khách chưa
   * đăng nhập, và cả khi WebSocket đã rớt. Nay chỉ hiện thứ máy chủ xác thực
   * thật cho phiên này; chưa rõ thì nói "chờ kết nối", không đoán là admin.
   */
  let currentAuth = { known: false, authenticated: false, role: null, username: null };

  function renderAuthStatus() {
    const authed = currentAuth.known && currentAuth.authenticated;

    if (permBadgeEl) {
      if (authed && currentAuth.role) {
        permBadgeEl.textContent = `QUYỀN: ${String(currentAuth.role).toUpperCase()}`;
        permBadgeEl.classList.remove('is-waiting');
      } else if (currentAuth.known) {
        permBadgeEl.textContent = 'QUYỀN: chưa xác thực';
        permBadgeEl.classList.add('is-waiting');
      } else {
        permBadgeEl.textContent = 'QUYỀN: chờ kết nối';
        permBadgeEl.classList.add('is-waiting');
      }
    }

    if (secStatusEl) {
      if (authed) {
        const who = currentAuth.username ? ` (${currentAuth.username})` : '';
        secStatusEl.textContent = `ĐÃ XÁC THỰC${who}`;
        secStatusEl.classList.remove('is-waiting');
      } else if (currentAuth.known) {
        secStatusEl.textContent = 'CHƯA XÁC THỰC · CHỈ XEM';
        secStatusEl.classList.add('is-waiting');
      } else {
        secStatusEl.textContent = WAIT_TXT;
        secStatusEl.classList.add('is-waiting');
      }
    }
  }

  renderAuthStatus();

  // Phase 70: Fallback REST telemetry poller — CHỈ chạy khi WebSocket không kết nối.
  // Khi WS đang OPEN, telemetry được server push qua `metrics_update` packet realtime.
  // Poll REST song song là tốn bandwidth + CPU server không cần thiết.
  // Interval tăng lên 8s (từ 3.5s) để giảm tải khi WS không ổn định.
  async function fetchTelemetryFallback() {
    // Bỏ qua nếu WS đang kết nối tốt — server đã push metrics realtime.
    if (hudSocket && hudSocket.readyState === WebSocket.OPEN) return;
    try {
      const token = localStorage.getItem('vnmateai_token') || '';
      const headers = {};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      const res = await fetch('/api/v1/health-dashboard', { headers });
      if (res.ok) {
        const data = await res.json();
        applyMetrics(data);
      }
    } catch (_) {}
  }

  setInterval(fetchTelemetryFallback, 8000);

  // ---------------------------------------------------------------------------
  // 14.1. ZERO-TRUST SECURITY APPROVAL CONTROLLER & ACTIONS
  // ---------------------------------------------------------------------------
  let currentPendingSecurityAction = null;

  function handleSecurityApprovalRequired(packet) {
    currentPendingSecurityAction = packet;
    const secAlert = document.getElementById('hud-security-alert');
    const secDesc = document.getElementById('hud-security-desc');
    if (secAlert) {
      secAlert.classList.remove('hidden');
      if (secDesc) {
        secDesc.textContent = packet.message || `Tác vụ "${packet.skill || 'hệ thống'}" đang chờ phê duyệt từ Quản Trị Viên...`;
      }
    }
    appendSystemLog(`[SEC] CẢNH BÁO: Tác vụ '${packet.skill || 'hệ thống'}' yêu cầu phê duyệt bảo mật!`, 'SECURITY');
    playCyberChime('process');
  }

  function hideSecurityApprovalModal() {
    currentPendingSecurityAction = null;
    const secAlert = document.getElementById('hud-security-alert');
    if (secAlert) {
      secAlert.classList.add('hidden');
    }
  }

  function handleSecurityApprovalResolved(packet) {
    currentPendingSecurityAction = null;
    const secAlert = document.getElementById('hud-security-alert');
    if (secAlert) {
      secAlert.classList.add('hidden');
    }
    if (packet.status === 'approved') {
      appendSystemLog(`[SEC] ✅ Tác vụ '${packet.skill || 'hệ thống'}' đã được phê duyệt qua Web Portal.`, 'VOICE');
      playCyberChime('ready');
      if (packet.reply) {
        setHudState('speaking', packet.reply, packet.reply.length * 65);
      }
      // Tiếng đọc kết quả tới qua sự kiện voice_active + khung nhị phân (approval_flow), không base64.
    } else {
      appendSystemLog(`[SEC] 🚫 Tác vụ '${packet.skill || 'hệ thống'}' đã bị hủy bỏ bởi người quản trị.`, 'WARNING');
      setHudState('idle', 'Tác vụ đã bị hủy bỏ theo yêu cầu.');
    }
  }

  // ---------------------------------------------------------------------------
  // 12b. PHASE 87 — QUÁ TRÌNH SUY NGHĨ CỦA MODEL
  // ---------------------------------------------------------------------------
  // Model suy luận ở field `reasoning`, tách hẳn khỏi `content` — nên câu
  // trả lời bạn nghe không bị lẫn suy nghĩ. Khung này chỉ hiện phần suy nghĩ
  // đã gọn, gập lại mặc định, bấm vào dòng tiêu đề để mở xem.
  //
  // Dùng `textContent` chứ KHÔNG dùng `innerHTML`: suy nghĩ là văn bản do model
  // sinh ra, không phải HTML. Dùng innerHTML ở đây là mỗi lượt hỏi một lỗ hổng
  // chèn mã — model có thể in ra `<script>` hoặc `<img onerror=...>`.
  const THINKING_STUCK_MS = 25000;

  function setHudThinking(state, text = '') {
    if (!thinkingEl) return;

    if (state === 'empty') {
      // Không có suy nghĩ để hiện (lỗi, hoặc model không suy luận): tắt khung
      // và dừng bộ đếm. Để khung hiện mãi là báo "đang suy nghĩ" mà không bao
      // giờ có kết quả.
      thinkingEl.classList.add('hidden');
      thinkingBodyEl?.classList.add('hidden');
      thinkingPeekEl?.classList.add('hidden');
      thinkingCaretEl?.classList.add('hidden');
      if (thinkingStuckTimer) {
        clearTimeout(thinkingStuckTimer);
        thinkingStuckTimer = null;
      }
      return;
    }

    thinkingEl.classList.remove('hidden');

    if (state === 'thinking') {
      // Model đang suy nghĩ, chưa có nội dung: hiện vòng xoay, ẩn phần xem.
      thinkingDotEl?.classList.remove('hidden');
      thinkingPeekEl?.classList.add('hidden');
      thinkingCaretEl?.classList.add('hidden');
      thinkingBodyEl?.classList.add('hidden');
      thinkingTextEl && (thinkingTextEl.textContent = '');
      if (thinkingStuckTimer) clearTimeout(thinkingStuckTimer);
      // Chốt chặn: nếu máy chủ đứt giữa chừng, tự tắt sau 25s.
      thinkingStuckTimer = setTimeout(() => {
        thinkingStuckTimer = null;
        setHudThinking('empty');
      }, THINKING_STUCK_MS);
      return;
    }

    // state === 'done': suy nghĩ đã xong, có nội dung.
    if (thinkingStuckTimer) {
      clearTimeout(thinkingStuckTimer);
      thinkingStuckTimer = null;
    }
    thinkingDotEl?.classList.add('hidden');
    thinkingPeekEl?.classList.remove('hidden');
    thinkingCaretEl?.classList.remove('hidden');
    thinkingTextEl && (thinkingTextEl.textContent = text);
    // Dòng tóm tắt ở tiêu đề: 120 ký tự đầu, để biết nội dung mà không phải
    // mở khung. Cắt ở ranh giới từ để không dính nửa từ.
    const peek = (text || '').slice(0, 120);
    thinkingPeekEl && (thinkingPeekEl.textContent = peek.length < (text || '').length ? `${peek.trimEnd()}…` : peek);
    // Mặc định gập lại: suy nghĩ dài, mở hết sẽ đẩy hết bố cục HUD.
    thinkingBodyEl?.classList.add('hidden');
    thinkingCaretEl && (thinkingCaretEl.textContent = '▼');
  }

  function toggleHudThinking() {
    if (!thinkingBodyEl || !thinkingCaretEl) return;
    const open = thinkingBodyEl.classList.toggle('hidden');
    thinkingCaretEl.textContent = open ? '▼' : '▲';
  }

  // Gán ra window để gọi được từ bên ngoài IIFE (ví dụ từ console hoặc
  // browser.evaluate khi kiểm thử). showHudDisplayCard cũng làm vậy ở dòng 1056.
  window.toggleHudThinking = toggleHudThinking;
  window.setHudThinking = setHudThinking;

  async function hudConfirmSecurity(approved) {
    playCyberChime('click');
    const secAlert = document.getElementById('hud-security-alert');
    if (secAlert) secAlert.classList.add('hidden');

    const actId = currentPendingSecurityAction?.action_id || 'hud';
    const skill = currentPendingSecurityAction?.skill || '';

    appendSystemLog(`[SEC] Người dùng phản hồi: ${approved ? 'ĐỒNG Ý PHÊ DUYỆT' : 'HỦY BỎ TÁC VỤ'}`, 'SECURITY');

    // 1. Send via WebSocket Neural Link
    if (hudSocket && hudSocket.readyState === WebSocket.OPEN) {
      try {
        hudSocket.send(JSON.stringify({
          action: 'confirm_action',
          approved: approved,
          action_id: actId,
          skill_name: skill,
        }));
      } catch (_) {}
    }

    // 2. Also send via REST with stored JWT token as fallback
    try {
      const token = localStorage.getItem('vnmateai_token') || '';
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;

      await fetch('/api/v1/security/confirm-action', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify({
          approved: approved,
          action_id: actId,
          skill_name: skill,
        }),
      });
    } catch (_) {}
  }
  window.hudConfirmSecurity = hudConfirmSecurity;

  // ---------------------------------------------------------------------------
  // 15. WEBSOCKET REAL-TIME CONNECTION (/ws/hud)
  // ---------------------------------------------------------------------------
  let hudSocket = null;
  let reconnectTimeout = null;

  function connectHudWebSocket() {
    if (hudSocket && (hudSocket.readyState === WebSocket.OPEN || hudSocket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Zero-Trust: gửi JWT kèm khi nối. Browser WebSocket API không cho set header,
    // nên token truyền qua query param. Không có token vẫn xem được telemetry,
    // nhưng các hành động phê duyệt sẽ bị server từ chối.
    const token = localStorage.getItem('vnmateai_token') || '';
    const wsUrl = `${protocol}//${window.location.host}/ws/hud`
      + (token ? `?token=${encodeURIComponent(token)}` : '');

    try {
      hudSocket = new WebSocket(wsUrl);
      hudSocket.binaryType = 'arraybuffer';

      hudSocket.onopen = () => {
        if (connDotEl) {
          connDotEl.className = 'w-2 h-2 rounded-full bg-cyan-400 shadow-[0_0_8px_#00f2fe] animate-pulse';
        }
        if (connTextEl) {
          connTextEl.textContent = 'LINK ACTIVE';
          connTextEl.className = 'text-xs font-bold text-cyan-300 font-orbitron tracking-wider';
          connTextEl.classList.remove('is-waiting');
        }
        if (linkBadgeEl) {
          linkBadgeEl.textContent = 'ACTIVE';
          linkBadgeEl.classList.remove('is-waiting');
        }
        appendSystemLog('WebSocket Neural Link connected to Master Server.', 'SYS');

        hudSocket._pingInterval = setInterval(() => {
          if (hudSocket.readyState === WebSocket.OPEN) {
            hudSocket._lastPingTime = performance.now();
            hudSocket.send(JSON.stringify({ action: 'ping' }));
          }
        }, 10000);
      };

      hudSocket.onmessage = async (event) => {
        // Phase 93: Nhận Binary Frame (raw MP3 chunks) từ broadcast_hud_binary()
        // Không qua base64 — đưa thẳng vào Web Audio API
        if (event.data instanceof ArrayBuffer) {
          if (hudAudioEnabled && event.data.byteLength > 100) {
            await _hudAudioQueue.enqueueChunk(event.data);
          }
          return;
        }
        // Nhận Blob (một số trình duyệt gửi Blob thay vì ArrayBuffer)
        if (event.data instanceof Blob) {
          if (hudAudioEnabled) {
            const ab = await event.data.arrayBuffer();
            if (ab.byteLength > 100) {
              await _hudAudioQueue.enqueueChunk(ab);
            }
          }
          return;
        }
        try {
          if (hudSocket._lastPingTime) {
            const lat = Math.round(performance.now() - hudSocket._lastPingTime);
            const latEl = document.getElementById('hud-net-latency');
            if (latEl && lat >= 0) latEl.textContent = String(Math.min(999, lat));
          }
          const packet = JSON.parse(event.data);
          const type = packet.type;

          if (type === 'hud_welcome') {
            if (packet.assistant_name) {
              updateAssistantName(packet.assistant_name);
            }
            // Vai trò THẬT của phiên này, do máy chủ xác thực JWT gửi kèm.
            currentAuth = {
              known: true,
              authenticated: packet.authenticated === true,
              role: packet.role || null,
              username: packet.username || null,
            };
            renderAuthStatus();
            appendSystemLog(packet.message || `${currentAiName} Cybernetic Core Online.`, 'SYS');
          } else if (type === 'assistant_name_updated') {
            if (packet.assistant_name) {
              updateAssistantName(packet.assistant_name);
              appendSystemLog(`Đã đổi tên trợ lý AI thành: ${packet.assistant_name}`, 'SYS');
            }
          } else if (type === 'audio_config_updated') {
            // Sync Voice Studio settings → HUD TTS runtime config (no restart required)
            if (packet.tts_voice) hudTtsConfig.voice = packet.tts_voice;
            if (packet.tts_rate) hudTtsConfig.rate = packet.tts_rate;
            if (typeof packet.speech_rate_num === 'number') hudTtsConfig.speechRateNum = packet.speech_rate_num;
            if (typeof packet.volume === 'number') {
              hudTtsConfig.volume = packet.volume;
              // Apply to current audio immediately if playing
              if (currentVoiceAudio) {
                currentVoiceAudio.volume = Math.max(0, Math.min(1, packet.volume / 100));
              }
            }
            appendSystemLog(`🔊 Cấu hình giọng đọc đã cập nhật: ${hudTtsConfig.voice} | tốc độ ${hudTtsConfig.rate} | âm lượng ${hudTtsConfig.volume}%`, 'SYS');
          } else if (type === 'metrics_update') {
            applyMetrics(packet.data);
          } else if (type === 'voice_active') {
            const status = packet.status || 'speaking';
            // Máy chủ báo đã huỷ lượt cũ. Dừng ngay ở đây nữa, không chờ HUD
            // tự phát hiện: lệnh mới có thể tới từ nguồn khác (ví dụ điện
            // thoại), lúc đó không đi qua `sendHudVoiceCommand` của HUD.
            if (packet.interrupted) {
              hudStopSpeaking();
              appendSystemLog('Lệnh mới tới — đã cắt lời đang nói.', 'VOICE');
            }
            if (packet.display_text) {
              showHudDisplayCard(packet.display_text, packet.query || packet.text);
            }
            if (status === 'speaking') {
              handleSpeakingEvent(packet);
            } else {
              setHudState(status, packet.text);
            }
            if (packet.text) {
              appendSystemLog(`Voice Event [${status.toUpperCase()}]: ${packet.text.slice(0, 70)}...`, 'VOICE');
            }
          } else if (type === 'robot_status') {
            const rDot = document.getElementById('hud-robot-dot');
            const rText = document.getElementById('hud-robot-status-text');
            const rCode = document.getElementById('hud-robot-code-badge');
            if (packet.status === 'paired' || packet.status === 'online') {
              if (rDot) rDot.className = 'w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px_#10b981] animate-pulse';
              if (rText) {
                rText.textContent = `Online [${packet.device_id || 'Robot'}]`;
                rText.className = 'text-xs font-bold text-emerald-300';
              }
              if (rCode && packet.pairing_code) {
                rCode.textContent = `MÃ: ${packet.pairing_code}`;
                rCode.className = 'px-2 py-0.5 rounded text-[10px] font-mono bg-emerald-500/20 text-emerald-300 border border-emerald-500/40';
              }
              appendSystemLog(`🤖 Robot [${packet.device_id}] đã ghép đôi (Mã: ${packet.pairing_code || 'OK'}).`, 'SYS');
            }
          } else if (type === 'voice_state') {
            // Phase 65: server báo Ly Ly vừa nói xong — có đang chờ admin đáp
            // không. Câu hỏi rỗng nghĩa là đã trả lời xong, đóng vòng lặp.
            handleVoiceState(packet);
          } else if (type === 'display_result') {
            showHudDisplayCard(packet.display_text, packet.query || packet.text || '');
          } else if (type === 'thinking') {
            // Phase 87: máy chủ báo về quá trình suy nghĩ của model.
            //   status "thinking" -> đang suy nghĩ, chưa có nội dung
            //   status "done"     -> suy nghĩ xong, packet.text là nội dung đã gọn
            //   status "empty"    -> không có suy nghĩ để hiện
            setHudThinking(packet.status || 'empty', packet.text || '');
          } else if (type === 'security_approval_required') {
            handleSecurityApprovalRequired(packet);
          } else if (type === 'security_approval_resolved') {
            handleSecurityApprovalResolved(packet);
          } else if (type === 'security_approval_rejected') {
            appendSystemLog(packet.message || 'Yêu cầu phê duyệt bị từ chối.', 'SYS');
            hideSecurityApprovalModal();
          } else if (type === 'auth_required') {
            currentAuth = { known: true, authenticated: false, role: null, username: null };
            renderAuthStatus();
            appendSystemLog(packet.message || 'HUD chưa xác thực.', 'SYS');
          } else if (type === 'system_log') {
            appendSystemLog(packet.message || 'Log received', packet.level || 'INFO');
          }
        } catch (_) {}
      };

      hudSocket.onclose = () => {
        clearInterval(hudSocket?._pingInterval);
        if (connDotEl) {
          connDotEl.className = 'w-2 h-2 rounded-full bg-amber-400 animate-ping';
        }
        if (connTextEl) {
          connTextEl.textContent = 'LINK RECONNECTING';
          connTextEl.className = 'text-xs font-bold text-amber-400 font-orbitron';
          connTextEl.classList.add('is-waiting');
        }
        if (linkBadgeEl) {
          linkBadgeEl.textContent = 'mất kết nối';
          linkBadgeEl.classList.add('is-waiting');
        }
        // Vai trò lấy từ gói `hud_welcome` của chính kết nối này. Kết nối đã
        // rớt thì không còn nguồn để xác nhận, nên về "chờ kết nối" thay vì giữ
        // lại "ADMIN" của lần đăng nhập trước — giữ lại là hiện quyền hạn của
        // một phiên đã không còn.
        currentAuth = { known: false, authenticated: false, role: null, username: null };
        renderAuthStatus();
        scheduleReconnect();
      };

      hudSocket.onerror = () => {
        hudSocket.close();
      };
    } catch (e) {
      scheduleReconnect();
    }
  }

  function scheduleReconnect() {
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    reconnectTimeout = setTimeout(() => {
      connectHudWebSocket();
    }, 2000);
  }

  // ---------------------------------------------------------------------------
  // 16. FULLSCREEN CONTROLLER & INTERACTION SHORTCUTS
  // ---------------------------------------------------------------------------
  function toggleFullscreen() {
    if (!document.fullscreenElement) {
      document.documentElement.requestFullscreen().catch(() => {});
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      }
    }
  }

  window.toggleFullscreen = toggleFullscreen;

  document.addEventListener('keydown', (e) => {
    // Phase 47: Check if Holographic Display Card is open -> ESC closes it
    const displayCard = document.getElementById('hud-display-card');
    const isDisplayCardVisible = displayCard && !displayCard.classList.contains('hidden');
    if (isDisplayCardVisible && e.key === 'Escape') {
      e.preventDefault();
      hideHudDisplayCard();
      return;
    }

    const secAlert = document.getElementById('hud-security-alert');
    const isSecAlertVisible = secAlert && !secAlert.classList.contains('hidden');
    if (isSecAlertVisible) {
      if (e.key === 'Enter') {
        e.preventDefault();
        hudConfirmSecurity(true);
        return;
      } else if (e.key === 'Escape') {
        e.preventDefault();
        hudConfirmSecurity(false);
        return;
      }
    }

    const tag = document.activeElement?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;

    if (e.key === 'f' || e.key === 'F') {
      toggleFullscreen();
    } else if (e.code === 'Space') {
      e.preventDefault();
      toggleHudMic();
    } else if (e.key === '/' || e.key === 'Enter') {
      e.preventDefault();
      if (cmdInputEl) {
        cmdInputEl.focus();
        cmdInputEl.select();
      }
    }
  });

  // Single click on AI Core canvas toggles mic; double click toggles fullscreen
  let coreClickTimeout = null;
  if (coreCanvas) {
    coreCanvas.addEventListener('click', () => {
      if (coreClickTimeout) {
        clearTimeout(coreClickTimeout);
        coreClickTimeout = null;
        toggleFullscreen();
      } else {
        coreClickTimeout = setTimeout(() => {
          coreClickTimeout = null;
          toggleHudMic();
        }, 250);
      }
    });
  }

  // ---------------------------------------------------------------------------
  // 17. INITIALIZATION BOOTSTRAP
  // ---------------------------------------------------------------------------
  function init() {
    resizeCanvases();
    connectHudWebSocket();
    requestAnimationFrame(tick);

    fetch('/api/v1/config/assistant-name')
      .then(res => res.ok ? res.json() : null)
      .then(data => {
        if (data && data.assistant_name) {
          updateAssistantName(data.assistant_name);
        }
      })
      .catch(() => {});

    // Kiểm tra quyền mic NGAY khi mở trang, không đợi tới lúc bấm MIC mới
    // biết. Nếu quyền đã bị chặn vĩnh viễn thì báo trước, để admin không mất
    // thời gian đi tìm micro rồi mới nghe ra không dùng được.
    hudRefreshMicPermission().then(function (state) {
      if (state === 'denied') {
        hudMarkMicBlocked('not-allowed');
      } else if (state === 'granted') {
        appendSystemLog('Quyền micro đã được cấp. Nhấn nút MIC để bắt đầu nghe.', 'VOICE');
      }
    });

    // Live Uptime Session Counter
    const hudSessionStart = Date.now();
    setInterval(() => {
      const el = document.getElementById('hud-uptime-counter');
      if (!el) return;
      const diff = Math.floor((Date.now() - hudSessionStart) / 1000);
      const h = String(Math.floor(diff / 3600)).padStart(2, '0');
      const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0');
      const s = String(diff % 60).padStart(2, '0');
      el.textContent = `${h}:${m}:${s}`;
    }, 1000);

    appendSystemLog(`${currentAiName} Mark-85 3D Cybernetic HUD khởi tạo xong.`, 'SYS');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

})();
