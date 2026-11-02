/* Pure Node verification. No server, npm dependencies, browser or model requests. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { pathToFileURL } = require('node:url');
const vm = require('node:vm');
const root = path.resolve(__dirname, '../src/asset_assistant/static');
const files = fs.readdirSync(root, { recursive: true }).filter(file => fs.statSync(path.join(root, file)).isFile());
const js = files.filter(file => file.endsWith('.js'));
const read = file => fs.readFileSync(path.join(root, file), 'utf8');
const exists = file => assert.ok(fs.existsSync(file), `Missing resource: ${file}`);
function localResource(value, file) {
  if (!value || value.startsWith('#') || value.startsWith('data:')) return;
  assert.ok(!/^(?:https?:|\/\/|javascript:)/i.test(value), `Non-local resource in ${file}: ${value}`);
  const clean = value.split(/[?#]/)[0];
  exists(value.startsWith('/static/') ? path.join(root, clean.slice(8)) : path.resolve(path.dirname(path.join(root, file)), clean));
}
async function main() {
  for (const file of js) {
    const result = spawnSync(process.execPath, ['--check', path.join(root, file)], { encoding: 'utf8' });
    assert.equal(result.status, 0, `${file}: ${result.stderr}`);
    const source = read(file);
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|localStorage|sessionStorage|document\.cookie|indexedDB|\beval\s*\(/.test(source), `Unsafe rendering/storage in ${file}`);
    for (const match of source.matchAll(/(?:import|export)\s+(?:[^;'"\n]*?\s+from\s*)?['"]([^'"]+)['"]/g)) localResource(match[1], file);
    if (!file.startsWith(`api${path.sep}`) && !file.startsWith('api/')) {
      assert.ok(!/from\s*['"][^'"]*(?:mockClient|previewState)/.test(source), 'UI bypasses API seam');
    }
  }
  console.log(`PASS: node --check individually: ${js.length} JavaScript modules; imports resolve; no unsafe sinks/storage`);

  const html = read('index.html');
  const voidTags = new Set(['meta', 'link', 'img', 'input', 'br', 'hr', 'source', 'wbr', 'area', 'base', 'embed', 'param', 'track', 'col']);
  const stack = [], ids = new Set();
  for (const match of html.matchAll(/<\/?([a-z][a-z0-9-]*)\b[^>]*>/gi)) {
    const [tag, name] = match;
    if (tag.startsWith('</')) assert.equal(stack.pop(), name, `HTML nesting mismatch at ${tag}`);
    else {
      if (!voidTags.has(name)) stack.push(name);
      for (const id of tag.matchAll(/\bid="([^"]+)"/g)) { assert.ok(!ids.has(id[1]), `Duplicate id ${id[1]}`); ids.add(id[1]); }
      for (const ref of tag.matchAll(/\b(?:src|href)="([^"]+)"/g)) {
        if (!ref[1].startsWith('?')) localResource(ref[1], 'index.html');
      }
      assert.ok(!/\son\w+\s*=/.test(tag), `Inline handler ${tag}`);
    }
  }
  assert.equal(stack.length, 0, 'Unclosed HTML elements');
  for (const match of read('app.js').matchAll(/\$\('([^']+)'\)/g)) assert.ok(ids.has(match[1]), `UI references missing id ${match[1]}`);
  for (const match of html.matchAll(/(?:for|aria-labelledby|aria-describedby)="([^"]+)"/g)) {
    for (const id of match[1].split(' ')) assert.ok(ids.has(id), `Missing accessibility target: ${id}`);
  }
  for (const file of files.filter(file => file.endsWith('.css'))) {
    for (const match of read(file).matchAll(/url\(\s*['"]?([^)'"\s]+)['"]?\s*\)/g)) localResource(match[1], file);
  }
  assert.ok(/Pico CSS[^\n]*v2\.1\.1/.test(read('vendor/pico-2.1.1.min.css')), 'Unexpected Pico distribution/version');
  assert.ok(read('vendor/PICO-LICENSE.md').includes('MIT'), 'Missing Pico license');
  assert.ok(read('vendor/LUCIDE-LICENSE').includes('ISC'), 'Missing Lucide license');
  console.log('PASS: HTML balanced structure, unique ids, ARIA targets, HTML/CSS local assets, pinned Pico + licenses');

  const load = file => import(pathToFileURL(path.join(root, file)).href);
  const { consumeSSE } = await load('api/sse.js');
  const { liveClient } = await load('api/liveClient.js');
  const event = (type, seq, payload = {}) => ({ run_id: 'test_run', seq, type, time: '2026-10-28T00:00:00Z', payload });
  const frame = value => `event: ${value.type}\r\ndata: ${JSON.stringify(value)}\r\n\r\n`;
  function stream(text, width = 1) {
    const bytes = new TextEncoder().encode(text);
    return new ReadableStream({ start(controller) { for (let i = 0; i < bytes.length; i += width) controller.enqueue(bytes.slice(i, i + width)); controller.close(); } });
  }
  const list = ['run_started', 'text_delta', 'tool_started', 'tool_completed', 'tool_error', 'retry', 'citation', 'chart', 'run_completed'];
  const content = ': heartbeat\r\n\r\n' + list.map((type, seq) => frame(event(type, seq, { text: '中文证据', tool_call_id: 'call_1' }))).join('');
  for (const width of [1, 2, 7, 64, 4096]) {
    const received = [];
    await consumeSSE(stream(content, width), { signal: new AbortController().signal, onEvent: value => received.push(value) });
    assert.deepEqual(received.map(value => value.type), list);
    assert.equal(received[1].payload.text, '中文证据');
  }
  await assert.rejects(consumeSSE(stream(frame(event('run_started', 0))), { signal: new AbortController().signal, onEvent() {} }), { code: 'STREAM_DISCONNECTED' });
  await assert.rejects(consumeSSE(stream(frame(event('run_started', 0)) + frame(event('text_delta', 0))), { signal: new AbortController().signal, onEvent() {} }), { code: 'INVALID_STREAM' });
  await assert.rejects(consumeSSE(stream(frame(event('text_delta', 0))), { signal: new AbortController().signal, onEvent() {} }), { code: 'INVALID_STREAM' });
  await assert.rejects(consumeSSE(stream(frame(event('run_started', 0)) + frame({ ...event('run_completed', 1), run_id: 'other' })), { signal: new AbortController().signal, onEvent() {} }), { code: 'INVALID_STREAM' });
  const multiline = 'data: {"run_id":"test_run","seq":0,\n' + 'data: "type":"run_started","time":"2026-10-28T00:00:00Z","payload":{}}\n\n';
  const received = [];
  await consumeSSE(stream(multiline + frame(event('run_error', 1, { code: 'MODEL_NOT_CONFIGURED' }))), { signal: new AbortController().signal, onEvent: value => received.push(value) });
  assert.equal(received.at(-1).type, 'run_error');
  const abort = new AbortController();
  const hanging = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(frame(event('run_started', 0)))); } });
  await assert.rejects(consumeSSE(hanging, { signal: abort.signal, onEvent() { abort.abort(); } }), { name: 'AbortError' });
  assert.equal(liveClient.documentUrl('source_1', 3), '/api/documents/source_1#page=3');
  assert.equal(liveClient.chartUrl('session_1', 'chart_1'), '/api/sessions/session_1/charts/chart_1');
  for (const id of ['../x', 'https://example.test', 'a/b', '%2e', '', 'x.pdf']) assert.throws(() => liveClient.documentUrl(id, 1), { code: 'INVALID_RESOURCE_ID' });
  console.log('PASS: SSE UTF-8/CRLF chunk splits, multiline data, all 10 event types, abort, disconnect, invalid order/run, controlled IDs');

  const originalFetch = globalThis.fetch;
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    if (url === '/api/chat') return new Response(content, { headers: { 'Content-Type': 'text/event-stream' } });
    if (options.method === 'DELETE') return new Response(null, { status: 204 });
    return new Response(JSON.stringify(url === '/api/health'
      ? { status: 'ok', model_configured: false, model_verified: false, data_sources_ready: false, reference_date: null }
      : { session_id: 'test_session', reference_date: null, data_labels: ['Synthetic'] }));
  };
  try {
    await liveClient.health(); const session = await liveClient.createSession();
    await liveClient.chat(session.session_id, '证据问题', { signal: new AbortController().signal, onEvent() {} });
    await liveClient.deleteSession(session.session_id);
    assert.deepEqual(calls.map(call => call.url), ['/api/health', '/api/sessions', '/api/chat', '/api/sessions/test_session']);
    assert.equal(calls[2].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[2].options.body), { session_id: 'test_session', message: '证据问题' });
    assert.ok(calls.every(call => call.options.redirect === 'error'));
    globalThis.fetch = async () => new Response(JSON.stringify({ error: { code: 'MODEL_NOT_CONFIGURED', message: '模型待配置', details: null } }), { status: 503 });
    await assert.rejects(liveClient.chat('test_session', '问题', { signal: new AbortController().signal, onEvent() {} }), { code: 'MODEL_NOT_CONFIGURED' });
  } finally { globalThis.fetch = originalFetch; }
  const { api } = await load('api/index.js');
  assert.equal(api, liveClient);
  for (const removed of ['api/mockClient.js', 'api/previewState.js']) {
    assert.ok(!fs.existsSync(path.join(root, removed)), `Removed layer still exists: ${removed}`);
  }
  for (const file of [...js, 'index.html']) {
    assert.ok(!/mockClient|previewState|api\.preview|preview-banner|preview-clues|loading-preview/.test(read(file)), `Preview layer remains in ${file}`);
  }
  assert.ok(html.includes('maxlength="8000"') && read('app.js').includes('text.length > 8000'), 'Chat length must match backend contract');
  console.log('PASS: live route/request/error contract with stubbed fetch; live seam; removed mock/state switcher; chat length matches backend');
  // Run the actual UI timer helpers with an offline clock and no browser/network.
  const source = read('app.js');
  const helpers = source.slice(source.indexOf('function renderProgress()'), source.indexOf('function node('));
  let now = 1000, tick = null;
  const status = { textContent: '' }, progressState = { progress: null };
  const context = vm.createContext({ state: progressState, $: () => status,
    performance: { now: () => now },
    setInterval(callback, delay) { assert.equal(delay, 1000); assert.equal(tick, null); tick = callback; return 42; },
    clearInterval(id) { assert.equal(id, 42); tick = null; } });
  vm.runInContext(helpers, context);
  const controller = new AbortController();
  context.startProgress(controller.signal);
  assert.ok(status.textContent.includes('已等待 0 秒'));
  now += 65000; tick();
  assert.ok(status.textContent.includes('已等待 65 秒'));
  context.progressStage('已收到引用 · 最终回答尚未完成');
  assert.ok(status.textContent.includes('最终回答尚未完成') && status.textContent.includes('不代表模型进度'));
  controller.abort();
  assert.equal(tick, null); assert.equal(progressState.progress, null);
  const terminalStatus = status.textContent;
  context.progressStage('stale event');
  assert.equal(status.textContent, terminalStatus);
  for (const reason of ['success', 'run_error', 'http_error', 'disconnect', 'reset']) {
    context.startProgress(new AbortController().signal);
    context.stopProgress(); context.stopProgress();
    assert.equal(tick, null, reason); assert.equal(progressState.progress, null, reason);
  }
  assert.match(source, /function finishAnswer\(status, description\)\s*\{\s*stopProgress\(\);/);
  assert.match(source, /finally\s*\{\s*stopProgress\(\);\s*state\.operation = null;/);
  assert.match(source, /state\.operation\?\.abort\(\);/);
  console.log('PASS: UI elapsed clock, public phase label, abort/repeated cleanup, terminal and finally cleanup hooks (offline)');
  console.log(`ALL PASS: ${files.length} local files. Static/Node verification only; no server or real model/backend exercised.`);
}
main().catch(error => { console.error(error); process.exitCode = 1; });