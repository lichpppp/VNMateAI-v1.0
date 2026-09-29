// Kiểm thử hồi quy cho tab "Tích Hợp Hệ Thống Báo Cáo".
//
// Phase 59/60 được tách khỏi tab "Trung Tâm Chỉ Huy" sang một tab cấp cao
// riêng. Tách tab là thay đổi cấu trúc, nên rủi ro hồi quy nằm ở chỗ:
//   1. HTML: khối tích hợp nằm nửa trong tab này nửa kia, hoặc quên nút nav.
//   2. JS:   `switchTab` không nạp dữ liệu, hoặc `CommandCenter.onEnter()`
//             vẫn gọi 4 hàm tải của tích hợp (tốn API vô ích).
//   3. KPI:  `syncCommandCenterKpi` còn đụng vào ô của tab kia → số hiển thị
//             ở Trung Tâm Chỉ Huy là số chết, không bao giờ cập nhật.
//
// Cả ba đều là loại lỗi "vẫn chạy, không báo lỗi, chỉ sai âm thầm" — nên
// phải có test chặn thay vì tin vào việc click tay.

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

// ── Tiện ích: cắt ra đúng phạm vi một section trong index.html ─────────────
// Bằng cách đếm thẻ mở/đóng thay vì so regex, vì các thẻ lồng nhau cùng
// cấp không cho biết đâu là thẻ đóng tương ứng.
function sectionRange(id) {
  const open = new RegExp(`<section[^>]*\\bid="${id}"[^>]*>`, 'g');
  const m = open.exec(html);
  if (!m) return null;
  let depth = 0, i = m.index;
  const re = /<section\b|<\/section>/g;
  re.lastIndex = i;
  let t;
  while ((t = re.exec(html))) {
    depth += t[0] === '</section>' ? -1 : 1;
    if (depth === 0) return [m.index, t.index + t[0].length];
  }
  return null;
}

const cc = sectionRange('tab-command-center');
const si = sectionRange('tab-system-integration');

results.push('▸ Cấu trúc HTML');
check('có section #tab-command-center', cc !== null);
check('có section #tab-system-integration', si !== null);

if (cc && si) {
  const ccHtml = html.slice(cc[0], cc[1]);
  const siHtml = html.slice(si[0], si[1]);

  check('tab mới nằm NGAY SAU tab Trung Tâm Chỉ Huy (không chen tab khác)',
    si[0] > cc[1] && html.slice(cc[1], si[0]).indexOf('<section') === -1);
  check('tab mới không lồng bên trong tab Trung Tâm Chỉ Huy',
    si[0] > cc[1]);

  // 5 sub-pane phải nằm trong tab mới, không sót lại tab cũ.
  const SUBPANES = ['cc-int-conn', 'cc-int-config', 'cc-int-webhook', 'cc-int-tools', 'cc-int-sys'];
  const missingInSi = SUBPANES.filter(id => !siHtml.includes(`id="${id}"`));
  const leftInCc = SUBPANES.filter(id => ccHtml.includes(`id="${id}"`));
  check('đủ 5 sub-pane trong tab mới', missingInSi.length === 0, missingInSi.join(','));
  check('không sub-pane nào còn sót trong Trung Tâm Chỉ Huy', leftInCc.length === 0, leftInCc.join(','));

  // Thanh sub-tab đi kèm phải ở tab mới.
  const subtabBtns = (html.match(/switchCcSubTab\('/g) || []).length;
  check('đủ 5 nút sub-tab trong tab mới',
    (siHtml.match(/switchCcSubTab\('/g) || []).length === 5,
    `thấy ${(siHtml.match(/switchCcSubTab\('/g) || []).length}/${subtabBtns} toàn trang`);

  // Các ô KPI của tích hợp phải theo khối sang tab mới.
  const movedKpi = ['cc-kpi-connectors', 'cc-kpi-plugins'];
  check('2 ô KPI cũ (Kết nối ngoại vi, Công cụ) đã sang tab mới',
    movedKpi.every(id => siHtml.includes(`id="${id}"`))
    && !movedKpi.some(id => ccHtml.includes(`id="${id}"`)));

  // Dải KPI của Trung Tâm Chỉ Huy.
  //
  // Phase 71 đổi dải này: ô "Sức khoẻ quỹ" (báo cáo dòng tiền) bị gỡ theo
  // yêu cầu admin, ô "Cảnh báo" gộp vào chỉ số "Sự cố mở", và thêm 4 ô vận
  // hành. Test đi theo bộ mới, không phải bỏ hẳn — dải KPI rỗng thì tab mất
  // mất khả năng trả lời nhanh "tình hình thế nào", đúng thứ admin cần.
  for (const id of ['cc-kpi-resource', 'cc-kpi-infra', 'cc-kpi-bg',
                    'cc-kpi-pending', 'cc-kpi-incident']) {
    check(`Trung Tâm Chỉ Huy có ô KPI "${id}"`, ccHtml.includes(`id="${id}"`));
  }
  check('ô KPI dòng tiền đã gỡ khỏi Trung Tâm Chỉ Huy',
    !ccHtml.includes('cc-kpi-cashflow'));

  // Ô KPI mới của tab tích hợp.
  for (const id of ['cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']) {
    check(`tab mới có ô KPI "${id}"`, siHtml.includes(`id="${id}"`));
  }

  // Nút điều hướng.
  check('có nút nav #nav-system-integration gọi switchTab(\'system-integration\')',
    /id="nav-system-integration"[^>]*onclick="switchTab\('system-integration'\)"/.test(html));
  // Vị trí trong thanh điều hướng: phải kề nhau. So sánh theo thứ tự các
  // thẻ nav-btn chứ không theo byte offset — offset dễ bị lệch chỉ vì
  // `class=` đứng trước `id=` trong cùng một thẻ.
  const navOrder = [...html.matchAll(/<div class="nav-btn" id="nav-([a-z-]+)"/g)].map(m => m[1]);
  const iCc = navOrder.indexOf('command-center');
  const iSi = navOrder.indexOf('system-integration');
  check('nút nav tab mới nằm ngay sau nút Trung Tâm Chỉ Huy',
    iCc >= 0 && iSi === iCc + 1, navOrder.slice(Math.max(0, iCc - 1), iSi + 2).join(' > '));
}

// Không được để lại id trùng sau khi di chuyển — trùng id làm phần tử thứ hai
// bị bỏ qua, biểu hiện là "ô này cập nhật, ô kia treo" rất khó tìm.
{
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
  const dup = [...new Set(ids.filter((v, i) => ids.indexOf(v) !== i))];
  check('không có id nào bị trùng trong index.html', dup.length === 0, dup.join(','));
}

results.push('▸ Đăng ký tab trong app.js');
check("VALID_TABS có 'system-integration'",
  /const VALID_TABS = \[[^\]]*'system-integration'[^\]]*\]/.test(src));
check("TAB_TITLES['system-integration'] = 'Tích Hợp Hệ Thống Báo Cáo'",
  src.includes("'system-integration': 'Tích Hợp Hệ Thống Báo Cáo',"));
check('tab mới được nạp dữ liệu khi mở',
  src.includes("if (tabId === 'system-integration') loadSystemIntegration();"));
check('loadSystemIntegration() nạp đủ 4 nhóm dữ liệu Phase 59/60',
  (() => {
    const m = src.match(/function loadSystemIntegration\(\) \{([\s\S]*?)\n\}/);
    if (!m) return false;
    return ['loadPluginRegistryStats', 'loadBackgroundTasks', 'loadWebhookAlerts', 'loadConnectorConfigAll']
      .every(f => m[1].includes(f + '()'));
  })());

results.push('▸ Không còn tải tích hợp từ Trung Tâm Chỉ Huy');
{
  const m = src.match(/(?:async )?function onEnter\(\) \{([\s\S]*?)\n  \}\n\n  function onLeave\(\)/);
  check('tìm thấy onEnter() của CommandCenter', m !== null);
  if (m) {
    const leaked = ['loadPluginRegistryStats', 'loadBackgroundTasks', 'loadWebhookAlerts', 'loadConnectorConfigAll']
      .filter(f => m[1].includes(f));
    check('CommandCenter.onEnter() KHÔNG gọi hàm tải của Phase 59/60',
      leaked.length === 0, leaked.join(','));
    check('CommandCenter.onEnter() vẫn cập nhật KPI của riêng nó', m[1].includes('syncCommandCenterKpi()'));
  }
}

results.push('▸ Tách hai dải KPI');
{
  const ccKpi = src.match(/function syncCommandCenterKpi\(\) \{([\s\S]*?)\n\}/);
  check('tìm thấy syncCommandCenterKpi()', ccKpi !== null);
  if (ccKpi) {
    const foreign = ['cc-kpi-connectors', 'cc-kpi-plugins', 'cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']
      .filter(id => ccKpi[1].includes(id));
    check('syncCommandCenterKpi() KHÔNG đụng vào ô KPI của tab tích hợp',
      foreign.length === 0, foreign.join(','));
    // Phase 71: `syncCommandCenterKpi()` nay chỉ chép ô chờ duyệt. Cảnh báo
    // an ninh thì `loadSecurity()` tự ghi thẳng vào `cc-security-count`, còn
    // dòng tiền đã bị gỡ. Chép lại hai thứ đó ở đây là chép một phần tử đã
    // bị xoá — và sẽ hỏng nếu ai đó tái sử dụng id đó cho mục đích khác.
    check('syncCommandCenterKpi() chép ô chờ duyệt', ccKpi[1].includes('cc-kpi-pending'));
    check('syncCommandCenterKpi() KHÔNG chép ô dòng tiền đã gỡ',
      !ccKpi[1].includes('cc-kpi-cashflow'));
  }

  const siKpi = src.match(/function syncIntegrationKpi\(\) \{([\s\S]*?)\n\}/);
  check('tìm thấy syncIntegrationKpi()', siKpi !== null);
  if (siKpi) {
    for (const id of ['cc-kpi-connectors', 'cc-kpi-plugins', 'cc-kpi-bg-tasks', 'cc-kpi-webhook-alerts']) {
      check(`syncIntegrationKpi() cập nhật "${id}"`, siKpi[1].includes(id));
    }
    check('syncIntegrationKpi() đếm connector theo chấm sức khoẻ thật (đã kiểm tra OK)',
      siKpi[1].includes('bg-emerald-500'));
  }

  // Mọi nơi ghi vào ô nguồn phải gọi lại hàm KPI, nếu không số sẽ kẹt.
  for (const [fn, name] of [
    [/async function runConnectorHealth\(connectorName\) \{([\s\S]*?)\n\}/, 'runConnectorHealth'],
    [/async function loadPluginRegistryStats\(\) \{([\s\S]*?)\n\}/, 'loadPluginRegistryStats'],
    [/async function loadBackgroundTasks\(\) \{([\s\S]*?)\n\}/, 'loadBackgroundTasks'],
    [/async function loadWebhookAlerts\(\) \{([\s\S]*?)\n\}/, 'loadWebhookAlerts'],
    [/function onWebhookAlert\(alert\) \{([\s\S]*?)\n\}/, 'onWebhookAlert'],
  ]) {
    const m = src.match(fn);
    check(`tìm thấy ${name}()`, m !== null);
    if (m) check(`${name}() gọi syncIntegrationKpi()`, m[1].includes('syncIntegrationKpi()'));
  }
}

console.log('=== Tab "Tích Hợp Hệ Thống Báo Cáo" — Test ===');
console.log(results.join('\n'));
console.log(`\nTổng: ${pass + fail} | Pass: ${pass} | Fail: ${fail}`);
if (fail > 0) process.exit(1);
console.log('\n✅ TẤT CẢ PASS');
