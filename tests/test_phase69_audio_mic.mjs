/**
 * tests/test_phase69_audio_mic.mjs
 * =================================
 * Kiểm thử Phase 69 — giọng đọc không ngắt quãng, mic không "mất quyền" im lặng.
 *
 * Hai lỗi admin phản ánh:
 *   1. "giọng đọc bị ngắt quãng"
 *   2. "mic đang bị mất quyền từ hub"
 *
 * Cả hai đều là lỗi ở web/hud.js, và cả hai đều không thấy được bằng mắt khi
 * đọc code: phải soi thứ tự thao tác trên một phần tử audio và vòng đời của
 * một đối tượng SpeechRecognition.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const src = readFileSync(join(ROOT, 'web', 'hud.js'), 'utf8');

let passed = 0;
let failed = 0;
const failures = [];

function check(name, cond, extra = '') {
  if (cond) { passed++; }
  else { failed++; failures.push(`  ✗ ${name}` + (extra ? ` — ${extra}` : '')); }
}
const section = (t) => console.log(`\n▸ ${t}`);

const has = (needle) => src.includes(needle);

// ══ 1. Ngắt quãng: nguyên nhân là ghi đè audio đang phát ═════════════════
section('Không còn cắt ngang câu đang phát');

// Đây là nguyên nhân gốc. Server gửi TTS từng câu một; nếu câu sau ghi đè
// src của phần tử đang phát thì câu trước bị cắt giữa chừng.
const overwrite = /audioStreamEl\.pause\(\);\s*\n\s*audioStreamEl\.src\s*=/;
check('KHÔNG còn mẫu pause() rồi ghi đè src', !overwrite.test(src),
  'vẫn còn pause() + ghi đè src — câu đang nói bị cắt');

// Chỉ soi phần PHÁT trong handleSpeakingEvent. `audioStreamEl` vẫn xuất hiện
// ở initWebAudio, nơi gắn phần tử audio vào AudioContext cho hiệu ứng — đó là
// đúng và phải giữ.
const speakBody = src.slice(
  src.indexOf('function handleSpeakingEvent'),
  src.indexOf('function drainSpeechQueue')
);
check('phần phát không còn dùng audioStreamEl để cắt ngang',
  !speakBody.includes('audioStreamEl'),
  'vẫn thao tác trực tiếp phần tử audio trong lúc phát');
check('giữ nguyên phần tử audio cho hiệu ứng (Web Audio)',
  src.includes('createMediaElementSource(audioStreamEl)'),
  'hiệu ứng hình ảnh phụ thuộc phần tử này');

section('Có hàng đợi phát tuần tự');
check('khai báo hàng đợi câu', has('let hudSpeechQueue = []'));
check('khai báo cờ đang phát', has('let hudSpeechDraining = false'));
check('có hàm rút hàng đợi', src.includes('function drainSpeechQueue()'));
check('câu được XẾP HÀNG chứ không phát ngay', has('hudSpeechQueue.push('));
check('hàng đợi được rút tuần tự', has('drainSpeechQueue();'));
check('phát câu kế tiếp khi câu trước HẾT', has('player.onended = next'));
check('lỗi phát cũng mở câu kế tiếp, không kẹt hàng đợi', has('player.onerror'));
check('hết hàng thì mới coi là nói xong', has('hudSpeechQueue.shift()'));

section('Tắt tiếng không làm treo hàng đợi');
// Nếu chỉ pause() mà để hudSpeechDraining = true, hàng đợi kẹt vĩnh viễn và
// mọi câu sau đó im luôn — kể cả sau khi bật tiếng lại.
const muteBlock = src.slice(
  src.indexOf('function toggleHudAudio()'),
  src.indexOf('window.toggleHudAudio')
);
check('tắt tiếng có xoá hàng đợi', muteBlock.includes('hudSpeechQueue = []'));
check('tắt tiếng có mở lại cờ đang phát',
  muteBlock.includes('hudSpeechDraining = false'),
  'để true thì hàng đợi kẹt vĩnh viễn');

// ══ 2. Mất quyền mic ═════════════════════════════════════════════════════
section('Mic mất quyền được báo rõ, không im lặng');
check('có cờ nhớ mic đang bị chặn', has('let hudMicBlocked = false'));
check('có hàm đánh dấu bị thu hồi quyền', src.includes('function hudMarkMicBlocked('));
check('hàm đó đổi nút MIC thành trạng thái cấp lại quyền',
  src.includes('[🔒 CẤP LẠI QUYỀN]'));
check('hàm đó chỉ đường cho người dùng',
  src.includes('Nhấn nút MIC để cấp lại'));
check('ghi log mất quyền', src.includes('Mất quyền micro'));

section('Phân biệt thu hồi quyền với lỗi khác');
// Trước đây not-allowed / service-not-allowed / audio-capture bị gộp làm
// một, người dùng không phân biệt được và tưởng hệ thống hỏng.
check('bắt not-allowed', src.includes("'not-allowed'"));
check('bắt service-not-allowed', src.includes("'service-not-allowed'"));
check('bắt audio-capture (không có mic nào)', src.includes("'audio-capture'"));
check('cả ba dẫn tới cùng cách xử lý',
  src.includes("|| event.error === 'audio-capture'"));

section('Instance chết được vứt, dựng cái mới');
// Instance SpeechRecognition đã bị thu hồi quyền không hồi sinh được —
// Chrome giữ nguyên trạng thái lỗi trên đó. Dùng lại thì bấm MIC vô ích.
check('vứt instance khi bị thu hồi quyền',
  src.includes("hudSpeechRecognition.abort()"));
check('xoá tham chiếu instance sau khi vứt',
  src.includes('hudSpeechRecognition = null'));
check('có hàm dựng instance mới',
  src.includes('function hudBuildRecognition()'));
check('dựng lại khi instance đã chết',
  src.includes('if (hudMicBlocked || !hudSpeechRecognition)'));
check('bấm MIC xoá cờ chặn', src.includes('hudMicBlocked = false;'));
check('onstart cũng xoá cờ chặn (quyền đã được cấp lại)',
  src.includes('hudMicBlocked = false;'));

section('Không giật mic');
// Điều kiện cũ gộp "có ý định chờ" với "mic đang mở", nên đúng lúc cần mở
// gấp nhất (sau khi Chrome vừa đóng phiên) lại bị bỏ qua.
check('kiểm tra isHudListening, không gộp với ý định',
  src.includes('if (isHudListening) return;'));
check('không còn điều kiện gộp sai', !has('if (hudAwaitingReply && isHudListening) return;'));
check('không giật mic khi đã bị thu hồi quyền', src.includes('if (hudMicBlocked) return;'));
check('tự dựng instance nếu chưa có khi mở nối tiếp',
  src.includes('hudAttachRecognitionHandlers(hudSpeechRecognition);'));

section('Handler tách riêng để dùng lại được');
// Handler trước nằm trong toggleHudMic, nên chỉ gắn được khi bấm nút — vòng
// lặp hội thoại không có cách nào mở lại mic mà không đi qua nút bấm.
check('có hàm gắn handler riêng',
  src.includes('function hudAttachRecognitionHandlers('));
const handlerBody = src.slice(
  src.indexOf('function hudAttachRecognitionHandlers('),
  src.indexOf('function hudMarkMicBlocked(')
);
for (const h of ['rec.onstart', 'rec.onresult', 'rec.onerror', 'rec.onend']) {
  check(`handler ${h} nằm trong hàm dùng chung`, handlerBody.includes(h));
}
check('toggleHudMic gọi hàm gắn handler',
  src.includes('hudAttachRecognitionHandlers(hudSpeechRecognition);'));

// ══ 3. Sự toàn vẹn của cấu trúc ═════════════════════════════════════════
section('Cấu trúc còn nguyên');
check('cú pháp cân bằng ngoặc', (src.match(/{/g) || []).length === (src.match(/}/g) || []).length,
  `${(src.match(/{/g) || []).length} { vs ${(src.match(/}/g) || []).length} }`);
check('vẫn còn các hàm cũ (không xoá nhầm)',
  has('function hudStopListeningForTurn') && has('function hudExpectReply'));
check('vẫn còn handleSpeakingEvent', src.includes('function handleSpeakingEvent'));
check('vẫn còn setHudState dùng để báo trạng thái', src.includes('setHudState('));
check('không còn nhánh fallback TTS giả chỉ bằng timer im lặng',
  !/Không có TTS: coi như đã nói xong/.test(src) || has('speakHudText'),
  'đường hỏi lại không phát được tiếng, chỉ đợi bằng timer');

section('Cảnh báo mất quyền không bị chính nó xoá mất');

// Lỗi tìm ra khi thử trên trình duyệt thật, không phải khi đọc code:
// Chrome bắn `onerror` rồi bắn `onend` ngay sau đó. Nếu onend reset nút về
// trạng thái MIC bình thường, cảnh báo "mất quyền" sống chỉ ~1ms rồi biến
// mất — HUD trông sẵn sàng trong khi mic đã chết. Đúng triệu chứng admin
// phản ánh.
const onendAt = src.indexOf('rec.onend = () => {');
const onendBody = src.slice(onendAt, onendAt + 900);
check('onend không reset nút khi mic đang bị chặn',
  onendBody.includes('if (micBtn && !hudMicBlocked)'),
  'onend vẫn xoá cảnh báo mất quyền ngay sau khi onerror báo');
const stopBody = src.slice(
  src.indexOf('function hudStopListeningForTurn()'),
  src.indexOf('function hudOpenMicForFollowup()')
);
check('hudStopListeningForTurn cũng giữ cảnh báo',
  stopBody.includes('!hudMicBlocked'),
  'tắt lượt nghe sẽ xoá cảnh báo mất quyền');

section('Bản nạp vào trình duyệt phải là bản mới');
// Hai lỗi trước đều do trình duyệt chạy bản cache cũ mà không ai biết:
// StaticFiles không gửi Cache-Control, và thẻ script ghi số phiên bản cứng.
const srv = readFileSync(join(ROOT, 'core', 'server.py'), 'utf8');
check('static buộc kiểm tra lại bản mới',
  srv.includes('no-cache, must-revalidate'));
check('có lớp static tuỳ chỉnh cache',
  srv.includes('class _NoStaleStatic'));
check('phiên bản script lấy từ mtime file',
  srv.includes('st_mtime_ns'),
  'số phiên bản cứng không đổi sau khi sửa JS');
check('tiêm phiên bản vào cả hai trang HTML',
  (srv.match(/_inject_asset_versions\(/g) || []).length >= 3,
  'phải dùng ở cả index.html và hud.html');
check('tiêm phiên bản giữ nguyên thuộc tính src',
  srv.includes('src="{url.rsplit') || srv.includes("return f'src="),
  'bỏ mất src= thì thẻ script hỏng, trình duyệt báo lỗi im lặng');

console.log('\n' + '─'.repeat(62));
if (failures.length) {
  console.log('Các assertion FAIL:');
  failures.forEach((f) => console.log(f));
}
console.log(`\nTổng: ${passed + failed} | Pass: ${passed} | Fail: ${failed}`);
process.exit(failed ? 1 : 0);
