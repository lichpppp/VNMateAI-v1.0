// Kiểm thử hồi quy cho Phase 71 — bảng điều khiển vận hành của Trung Tâm Chỉ Huy.
//
// Cùng cách làm với test_command_center_phase5960.mjs: cắt riêng khối hàm
// cần kiểm ra khỏi web/app.js rồi import động, vì app.js kéo theo
// WebSocket, Chart.js và toàn bộ DOM — không chạy được trong Node.
//
// Ở đây trọng tâm là những thứ dễ sai mà mắt thường không thấy:
//   1. Ngưỡng màu — đỏ ở 80% RAM sẽ kêu suốt trên máy 8GB, mất hết tác dụng.
//   2. Phân loại "sự cố đang mở" — gộp "completed" vào số việc cần xử là báo
//      hỏng dữ liệu, và đây là chỗ dễ sai nhất.
//   3. Lọc nhiễu log — đo thật thì 57% dòng là heartbeat.
//   4. Phân mức rủi ro — tool risk>=3 mới phải duyệt, đảo ngược là cho AI
//      tự chạy việc cần người ký.
//   5. Endpoint lỗi phải hiện lỗi, không được để trắng rồi tưởng là bình thường.

import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const APP = join(HERE, '..', 'web', 'app.js');
const HTML = join(HERE, '..', 'web', 'index.html');
const src = readFileSync(APP, 'utf-8');
const html = readFileSync(HTML, 'utf-8');

let pass = 0, fail = 0;
const results = [];
function check(name, cond, extra = '') {
  if (cond) { pass++; results.push(`  ✅ ${name}`); }
  else { fail++; results.push(`  ❌ ${name}${extra ? ' — ' + extra : ''}`); }
}

// ── Cắt khối cần test ──────────────────────────────────────────────────────
function cut(startMark, endMark) {
  const a = src.indexOf(startMark);
  if (a < 0) throw new Error(`không tìm thấy: ${startMark}`);
  const b = src.indexOf(endMark, a);
  if (b < 0) throw new Error(`không tìm thấy end: ${endMark}`);
  return src.slice(a, b + endMark.length);
}

const opsBlock = cut(
  '  // ── Phase 71: sức khoẻ vận hành ───────────────────────────────────────',
  '  // ── Smart Tool Router ───────────────────────────────────────────────────',
);

// Khung DOM giả: các hàm render chỉ cần `getElementById` trả về phần tử
// có `textContent`/`className`/`innerHTML`/`checked`. Không cần DOM thật — và
// đây cũng là lý do test bắt được lỗi "render vào id không tồn tại" (hàm đó
// im lặng trả về, nên nếu không khẳng định id thì test sẽ tự xanh).
const harness = `
  const window = globalThis;
  const API_BASE = '';
  const CC_CONNECTORS = ['aws'];
  const _ccExtensions = [];
  const CC_CON = { aws: 'AWS' };
  const CC_TITLES = { aws: 'AWS' };
  const _toasts = [];
  function showToast(msg, type) { _toasts.push({ msg: String(msg), type: type || 'info' }); }
  function getAuthToken() { return 'test-token'; }
  const _els = {};
  function makeEl() {
    return {
      textContent: '', innerHTML: '', className: '', checked: false, style: {},
      classList: { toggle() {}, contains() { return false; }, add() {}, remove() {} },
    };
  }
  function $(id) {
    if (!(id in _els)) _els[id] = makeEl();
    return _els[id];
  }
  function _esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }
  let _fetchQueue = [];
  let _fetchCalls = [];
  function _queue(payload) { _fetchQueue.push(payload); }
  function apiFetch(url, opts) {
    _fetchCalls.push({ url, opts });
    const next = _fetchQueue.shift();
    if (!next) return Promise.resolve({ ok: false, status: 599, json: async () => ({}) });
    if (next instanceof Error) return Promise.reject(next);
    return Promise.resolve({
      ok: next.status == null ? true : (next.status >= 200 && next.status < 300),
      status: next.status == null ? 200 : next.status,
      json: async () => next.body || {},
    });
  }
  ${opsBlock}
  const _api = { $: $, makeEl: makeEl, _els: _els, _queue: _queue, _fetchCalls: () => _fetchCalls, _toasts: () => _toasts };
`;

const { readFileSync: rf, writeFileSync: wf } = await import('node:fs');
const { tmpdir } = await import('node:os');
const { pathToFileURL } = await import('node:url');
const file = join(tmpdir(), `cc71-${process.pid}-${Date.now()}.mjs`);
wf(file, `${harness}\nexport { _api }; export { renderOpsHealth, renderResourceBars, renderInfraList, renderRiskTiers, _isOpsNoise, _fmtDuration, runCommandCenterAudit };`);
const M = await import(pathToFileURL(file).href);
const $ = M._api.$;

// ═══ 1. Ngưỡng màu tài nguyên ════════════════════════════════════════════
console.log('\n── 1. Ngưỡng màu tài nguyên ──');
{
  // Máy thật của người dùng: 8GB RAM. Ở 80% thì phải im, không đỏ.
  const cases = [
    { cpu: 10, ram: 45, want: 'emerald', why: '45% RAM là bình thường, tuyệt đối không đỏ' },
    { cpu: 10, ram: 80, want: 'emerald', why: '80% RAM trên máy 8GB vẫn là bình thường' },
    { cpu: 10, ram: 85, want: 'amber', why: '85% thì cảnh báo' },
    { cpu: 10, ram: 95, want: 'rose', why: '95% thì báo động' },
  ];
  for (const c of cases) {
    M.renderOpsHealth({ stats: { hardware: { cpu_percent: c.cpu, ram_percent: c.ram, ram_used_gb: 3.2, ram_total_gb: 8, disk_percent: 24, uptime_seconds: 3600 } } });
    const el = $('cc-kpi-resource');
    check(`RAM ${c.ram}% → ${c.want}`, el.className.includes(c.want), `thực tế: ${el.className} (${c.why})`);
  }

  // Ngưỡng phải nhìn vào GIÁ TRỊ LỚN NHẤT, không phải riêng CPU.
  M.renderOpsHealth({ stats: { hardware: { cpu_percent: 5, ram_percent: 96, ram_used_gb: 7.7, ram_total_gb: 8, disk_percent: 10, uptime_seconds: 60 } } });
  check('CPU thấp nhưng RAM 96% vẫn phải đỏ', $('cc-kpi-resource').className.includes('rose'),
    `thực tế: ${$('cc-kpi-resource').className}`);

  // Thanh tiến trình phải bị kẹp trong 0..100 — RAM 140% (đọc sai số liệu)
  // không được vẽ thanh tràn ra ngoài khung. So số thực, không so chuỗi:
  // "width:100%" là hợp lệ, "width:150%" thì không.
  M.renderResourceBars({ cpu_percent: 150, ram_percent: -5, disk_percent: 24 });
  const widths = [...$('cc-res-bars').innerHTML.matchAll(/width:(\d+)%/g)].map((m) => Number(m[1]));
  check('thanh kẹp trong 0..100 khi số liệu vô lý',
    widths.length === 3 && widths.every((w) => w >= 0 && w <= 100),
    `thực tế: ${JSON.stringify(widths)}`);
}

// ═══ 2. Sự cố đang mở — chỗ dễ sai nhất ══════════════════════════════════
console.log('\n── 2. Phân loại sự cố đang mở ──');
{
  const tickets = [
    { id: 'a', status: 'completed' },
    { id: 'b', status: 'closed' },
    { id: 'c', status: 'resolved' },
    { id: 'd', status: 'pending' },
    { id: 'e', status: 'in_progress' },
    { id: 'f', status: 'open' },
    { id: 'g', status: 'PENDING' },      // hoa thường phải bỏ qua phân biệt
  ];
  M.renderOpsHealth({ itsm: { tickets } });
  check('chỉ tính việc CHƯA xong (4, không phải 7)',
    $('cc-kpi-incident').textContent === '4', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Trạng thái lạ chưa từng gặp phải tính là "cần xử" — thừa một dòng còn hơn
  // bỏ sót sự cố.
  M.renderOpsHealth({ itsm: { tickets: [{ id: 'x', status: 'ESCALATED_TO_CEO' }] } });
  check('trạng thái lạ vẫn bị đếm (thừa hơn là bỏ sót)',
    $('cc-kpi-incident').textContent === '1', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Webhook alerts cộng vào cùng số sự cố.
  M.renderOpsHealth({ itsm: { tickets: [] }, wh: { total: 3 } });
  check('cảnh báo webhook cộng vào sự cố',
    $('cc-kpi-incident').textContent === '3', `thực tế: ${$('cc-kpi-incident').textContent}`);

  // Không có gì mở → xanh, tức là hệ thống thực sự yên.
  M.renderOpsHealth({ itsm: { tickets: [{ id: 'a', status: 'completed' }] } });
  check('không có gì mở → xanh', $('cc-kpi-incident').className.includes('emerald'),
    `thực tế: ${$('cc-kpi-incident').className}`);
}

// ═══ 3. Lọc nhiễu log ════════════════════════════════════════════════════
console.log('\n── 3. Lọc nhiễu nhật ký ──');
{
  check('heartbeat httpx bị coi là nhiễu', M._isOpsNoise({ logger: 'httpx', message: 'GET /v1/models HTTP/1.1 200' }) === true);
  check('log khởi động KHÔNG bị coi là nhiễu',
    M._isOpsNoise({ logger: 'core.server', message: 'FastAPI startup: loaded 76 skill(s).' }) === false);
  check('cảnh báo nghiêm trọng KHÔNG bị coi là nhiễu',
    M._isOpsNoise({ logger: 'core.llm_engine', message: 'All models failed' }) === false);
}

// ═══ 4. Phân mức rủi ro ══════════════════════════════════════════════════
console.log('\n── 4. Phân mức rủi ro Zero-Trust ──');
{
  // Số liệu thật lấy từ /api/v1/enterprise/plugin-registry/stats.
  M.renderRiskTiers({
    tools: {
      check_aws_cost: { risk_level: 1, enabled: true },
      check_oci_metrics: { risk_level: 1, enabled: true },
      download_paperless_document: { risk_level: 2, enabled: true },
      restart_service: { risk_level: 3, enabled: true },
      wipe_production: { risk_level: 5, enabled: true },
      old_tool: { risk_level: 1, enabled: false },
    },
  });
  const out = $('cc-risk-tiers').innerHTML;
  check('tool risk>=3 mới phải duyệt → 2/6 cần duyệt', />2<\/p>/.test(out), `thực tế: ${out.match(/>([0-9]+)<\/p>/g)}`);
  check('4 tool tự chạy được liệt kê', />4<\/p>/.test(out));
  check('tool L3 và L5 đều hiện', out.includes('restart_service') && out.includes('wipe_production'));
  check('tool đã tắt vẫn thấy nhưng có đánh dấu', out.includes('(đã tắt)'));
  check('hiện đủ 5 mức L1..L5', [1, 2, 3, 4, 5].every((n) => out.includes(`>L${n}<`)));

  // risk_level thiếu/null phải mặc định về 1 (tự chạy) chứ không phải 0
  // (mà 0 < 3 thì cũng tự chạy — nhưng hiển thị sai mức).
  M.renderRiskTiers({ tools: { x: {}, y: { risk_level: null } } });
  check('tool thiếu risk_level không làm vỡ bảng', $('cc-risk-tiers').innerHTML.length > 0);
}

// ═══ 5. Endpoint hỏng phải hiện lỗi ══════════════════════════════════════
console.log('\n── 5. Endpoint lỗi không được để trắng ──');
{
  M.renderOpsHealth({ stats: { __err: 'HTTP 500' } });
  check('tài nguyên lỗi → KPI ghi "Lỗi" chứ không để trống',
    $('cc-kpi-resource').textContent === 'Lỗi', `thực tế: "${$('cc-kpi-resource').textContent}"`);
  check('tài nguyên lỗi → nói rõ nguyên nhân trong khung',
    $('cc-res-bars').innerHTML.includes('HTTP 500'));

  M.renderInfraList({ __err: 'timeout' });
  check('hạ tầng lỗi → không vẽ ra danh sách rỗng',
    $('cc-infra-list').innerHTML.includes('timeout'), `thực tế: ${$('cc-infra-list').innerHTML.slice(0, 80)}`);

  M.renderRiskTiers({ __err: 'HTTP 401' });
  check('bảng rủi ro lỗi → nói rõ lỗi', $('cc-risk-tiers').innerHTML.includes('HTTP 401'));

  // Lỗi thật phải hiện "Lỗi", KHÔNG được rơi về 0 — "0 sự cố" khi ta không
  // biết là khiến người giám sát yên trí một cách sai.
  M.renderOpsHealth({ itsm: { __err: 'HTTP 503' } });
  check('ITSM hỏng → "Lỗi", không phải "0"',
    $('cc-kpi-incident').textContent === 'Lỗi', `thực tế: "${$('cc-kpi-incident').textContent}"`);

  // Ngược lại: nguồn KHÔNG được hỏi tới (undefined) thì im lặng, không phải
  // báo lỗi. Gộp hai khái niệm này làm cả bảng luôn đỏ mỗi lần render một
  // phần — và mất hết ý nghĩa của việc báo lỗi.
  M.renderOpsHealth({ itsm: { tickets: [] } });
  check('thiếu dữ liệu chỉ thiếu, không phải lỗi',
    $('cc-kpi-incident').textContent === '0', `thực tế: "${$('cc-kpi-incident').textContent}"`);

  M.renderOpsHealth({});
  check('render không có nguồn nào không làm đỏ mọi ô',
    !$('cc-kpi-incident').className.includes('rose'),
    `thực tế: ${$('cc-kpi-incident').className}`);
}

// ═══ 6. Bảng hạ tầng nêu đúng tên trường còn thiếu ════════════════════════
console.log('\n── 6. Bảng kết nối hạ tầng ──');
{
  M.renderInfraList({
    connectors: {
      aws: { configured: false, enabled: true, actions: ['billing_summary', 'instance_status'], missing_fields: ['access_key_id', 'secret_access_key'] },
      oci: { configured: true, enabled: true, actions: ['instance_list'] },
      old: { configured: true, enabled: false, actions: [] },
    },
  });
  const out = $('cc-infra-list').innerHTML;
  check('đếm đúng số nguồn sẵn sàng (1/3)', $('cc-infra-badge').textContent === '1/3 sẵn sàng',
    `thực tế: ${$('cc-infra-badge').textContent}`);
  check('nêu tên trường còn thiếu (access_key_id)', out.includes('access_key_id'));
  check('connector đã tắt không bị báo "thiếu cấu hình"', out.includes('Đã tắt'));
  check('không dán nhãn "thiếu cấu hình" lên connector đã tắt',
    (out.match(/Thiếu cấu hình/g) || []).length === 1, `thực tế: ${(out.match(/Thiếu cấu hình/g) || []).length} lần`);
}

// ═══ 7. Rà soát: lỗi phải báo, không được báo thành công ═══════════════════
console.log('\n── 7. Báo cáo rà soát phải thành thật ──');
{
  const api = M._api;

  // Server trả 200 nhưng bên trong status=error — báo "thành công" ở đây là
  // báo cáo thành công giả, nên phải bắt.
  api._queue({ status: 200, body: { result: { status: 'error', error: 'task store hỏng' } } });
  api._queue({ status: 200, body: { hardware: {} } });           // loadOpsHealth
  api._queue({ status: 200, body: { hardware: {} } });
  api._queue({ status: 200, body: { hardware: {} } });
  api._queue({ status: 200, body: { hardware: {} } });
  api._queue({ status: 200, body: { hardware: {} } });
  await M.runCommandCenterAudit();
  const errToast = api._toasts().find((t) => t.type === 'error');
  check('status=error bên trong HTTP 200 → báo lỗi, không báo xong',
    !!errToast && errToast.msg.includes('task store hỏng'),
    `toast: ${JSON.stringify(api._toasts())}`);

  // Nút phải được bật lại, nếu không thì sau một lỗi nút chết vĩnh viễn.
  check('nút rà soát được bật lại sau lỗi', $('cc-audit-btn').textContent === 'Chạy rà soát tức thì',
    `thực tế: "${$('cc-audit-btn').textContent}"`);
}

// ═══ 8. Dòng tiền đã gỡ khỏi tab ═════════════════════════════════════════
console.log('\n── 8. Dòng tiền không còn trong tab ──');
{
  check('HTML không còn thẻ dòng tiền', !html.includes('cc-cashflow'));
  check('HTML không còn KPI sức khoẻ quỹ', !html.includes('cc-kpi-cashflow'));
  check('JS không còn hàm renderCashflow', !src.includes('renderCashflow'));
  // Tên hàm còn xuất hiện trong chú thích "đã thay X bằng Y" là có chủ đích;
  // thứ cần chặn là phần ĐỊNH NGHĨA và LỜI GỌI còn sót.
  check('JS không còn định nghĩa/gọi loadCashflow',
    !/\bfunction\s+loadCashflow\b/.test(src) && !/[^.\w]loadCashflow\s*\(/.test(src));

  // Nhưng câu hỏi dòng tiền thì vẫn phải trả lời được — bỏ panel không
  // có nghĩa là bỏ khả năng hỏi.
  check('bảng định tuyến vẫn còn nhánh cashflow', src.includes("endpoint: 'cashflow'"));
  check('câu hỏi dòng tiền trả lời bằng chữ, không vẽ vào id đã xoá',
    src.includes('Sức khoẻ dòng tiền') && !src.includes('renderCashflow(d.result'));
}

// ═══ 9. Các id mới đều có trong HTML ══════════════════════════════════════
console.log('\n── 9. HTML có đủ id mà JS tìm ──');
{
  const need = ['cc-kpi-resource', 'cc-kpi-infra', 'cc-kpi-bg', 'cc-kpi-pending', 'cc-kpi-incident',
    'cc-res-bars', 'cc-health-dot', 'cc-health-uptime', 'cc-stat-skills', 'cc-stat-users',
    'cc-stat-tickets', 'cc-infra-list', 'cc-infra-badge', 'cc-ops-log', 'cc-log-verbose',
    'cc-risk-tiers', 'cc-audit-btn'];
  const missing = need.filter((id) => !html.includes(`id="${id}"`));
  check('mọi id Phase 71 đều tồn tại trong HTML', missing.length === 0, `thiếu: ${missing.join(', ')}`);

  // Mỗi id phải có dấu chấm chấm trước để JS lấy được — và mỗi id phải xuất
  // hiện đúng 1 lần (lặp id là HTML không hợp lệ, getElementById chỉ lấy cái
  // đầu nên phần tử thứ hai im lặng không hoạt động).
  const dupes = need.filter((id) => (html.match(new RegExp(`id="${id}"`, 'g')) || []).length > 1);
  check('không có id trùng lặp', dupes.length === 0, `trùng: ${dupes.join(', ')}`);
}

// ═══ 10. Rà soát chỉ đọc — không được tự động duyệt hộ ═════════════════════
console.log('\n── 10. Rà soát là việc chỉ đọc ──');
{
  const i = src.indexOf('async function runCommandCenterAudit()');
  const body = src.slice(i, i + 2200);
  check('gọi đúng endpoint rà soát', body.includes('/api/v1/enterprise/proactive/run-audit'));
  check('không gọi endpoint phê duyệt (đó là việc của người)',
    !body.includes('/hitl/approve') && !body.includes('/hitl/reject'));
  check('nút được khoá lúc đang chạy để không bấm hai lần', body.includes('btn.disabled = true'));
}

// ═══ 11. Gán className lên <svg> — lỗi đã xảy ra thật ═════════════════════
console.log('\n── 11. Không gán .className lên thẻ <svg> ──');
{
  // Lỗi này không nằm trong Phase 71 mà lộ ra khi kiểm tra console trình
  // duyệt: `_applyMicUI` gán `icon.className = ...` cho `mic-status-icon`,
  // nhưng đó là thẻ <svg> — `SVGElement.className` chỉ có getter, gán vào
  // ném TypeError, hàm dừng giữa chừng và `loadMicStatus()` hỏng hoàn toàn.
  // Nó lọt qua vì lỗi nằm sau `catch` chỉ ghi console.
  const svgIds = [...html.matchAll(/<svg\b[^>]*\bid="([^"]+)"/g)].map((m) => m[1]);
  check('HTML có thẻ <svg> có id để kiểm tra', svgIds.length > 0, `tìm thấy ${svgIds.length}`);

  const offenders = [];
  for (const m of src.matchAll(/const\s+(\w+)\s*=\s*document\.getElementById\('([^']+)'\)/g)) {
    const [, varName, id] = m;
    if (!svgIds.includes(id)) continue;
    // `.className` của biến này có xuất hiện sau khi lấy phần tử không?
    const after = src.slice(m.index, m.index + 6000);
    if (new RegExp(`\\b${varName}\\.className\\s*=`).test(after)) {
      offenders.push(`${varName} -> #${id} (dòng ${src.slice(0, m.index).split('\n').length})`);
    }
  }
  check('không chỗ nào gán .className lên <svg>', offenders.length === 0, offenders.join('; '));

  // `setAttribute('class', ...)` thì chạy được trên cả <svg> lẫn <div>.
  check('dùng setAttribute cho class của <svg>',
    /icon\.setAttribute\('class',\s*'text-emerald-400/.test(src)
    && /icon\.setAttribute\('class',\s*'text-slate-400/.test(src));
}

// ── Kết quả ────────────────────────────────────────────────────────────────
console.log('\n' + results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail) { console.log('\n❌ CÓ TEST FAIL'); process.exit(1); }
console.log('✅ TẤT CẢ PASS');
