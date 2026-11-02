import { api } from './api/index.js';

const $ = id => document.getElementById(id);
const state = { session: null, operation: null, health: null, assistant: null,
  tools: new Map(), citations: new Set(), charts: new Set(), resetPending: false, progress: null };

function renderProgress() {
  if (!state.progress) return;
  const seconds = Math.floor((performance.now() - state.progress.started) / 1000);
  $('run-status').textContent = `${state.progress.stage} · 已等待 ${seconds} 秒 · 可停止（计时不代表模型进度）`;
}
function stopProgress() {
  if (state.progress) clearInterval(state.progress.timer);
  state.progress = null;
}
function startProgress(signal) {
  stopProgress();
  state.progress = { started: performance.now(), stage: '准备请求', timer: null };
  state.progress.timer = setInterval(renderProgress, 1000);
  signal.addEventListener('abort', stopProgress, { once: true });
  renderProgress();
}
function progressStage(stage) {
  if (!state.progress) return;
  state.progress.stage = stage;
  renderProgress();
}

function node(tag, text, className) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  if (className) element.className = className;
  return element;
}
function printable(value) {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  return JSON.stringify(value);
}
function showError(error) {
  $('error-code').textContent = error.code ?? 'REQUEST_FAILED';
  $('error-message').textContent = error.message ?? '请求失败，请检查本地服务。';
  $('error-panel').hidden = false;
}
function updateControls() {
  const busy = state.operation !== null;
  $('send').disabled = busy || state.resetPending;
  $('question').disabled = busy || state.resetPending;
  $('stop').disabled = !busy || state.operation.signal.aborted;
  $('new-session').disabled = state.resetPending;
  document.querySelectorAll('.prompt').forEach(button => { button.disabled = busy || state.resetPending; });
}
function setReference(date) {
  $('reference-date').textContent = typeof date === 'string' && date ? `${date} · UTC` : '待数据核验';
}
async function checkHealth() {
  $('check-health').disabled = true;
  $('check-health').setAttribute('aria-busy', 'true');
  try {
    const health = await api.health();
    state.health = health;
    $('model-status').textContent = health.model_configured !== true ? '模型待配置 · 未验证'
      : health.model_verified === true ? '模型已配置 · 后端已真实验证' : '模型已配置 · 未验证';
    $('model-note').textContent = health.model_configured !== true
      ? '请检查后端 .env：endpoint / deployment / model；Responses URI 可携带 api-version，否则显式填写版本，修改后重启。待配置不等于连接失败。'
      : '健康检查不触发推理；模型验证状态来自后端，实际请求仍可能失败。';
    $('data-status').textContent = health.data_sources_ready === true ? '后端报告资料已就绪 · Synthetic' : '待核验 · 尚不可作为既定事实';
    setReference(state.session?.reference_date ?? health.reference_date);
  } catch (error) {
    // Unknown health is not proof of missing config or failed model connectivity.
    state.health = null;
    $('model-status').textContent = '服务状态未知 · 模型未验证';
    $('model-note').textContent = '未能读取健康状态；未执行模型探测。';
    showError(error);
  } finally {
    $('check-health').disabled = false;
    $('check-health').removeAttribute('aria-busy');
  }
}
function message(role, text) {
  $('welcome').hidden = true;
  const article = node('article', undefined, `message ${role}`);
  const label = node('small', role === 'user' ? '你 · 调查问题' : '助手 · 生成中（尚未完成验证）');
  const body = node('p', text, 'message-body');
  article.append(label, body);
  $('messages').append(article);
  return { article, label, body };
}
function finishAnswer(status, description) {
  stopProgress();
  if (state.assistant) {
    state.assistant.article.dataset.state = status;
    state.assistant.label.textContent = `助手 · ${description}`;
    if (!state.assistant.body.textContent) state.assistant.body.textContent = '本次未产生回答文本。';
  }
  $('run-status').textContent = description;
}
function toolEvent(event, payload) {
  if (payload.result && typeof payload.result === 'object' && !Array.isArray(payload.result)) {
    payload = { ...payload.result, ...payload };
  }
  const id = payload.tool_call_id;
  if (typeof id !== 'string' || !id) throw new Error('工具事件缺少 tool_call_id，无法可靠关联。');
  let record = state.tools.get(id);
  if (!record) {
    const details = node('details', undefined, 'tool-event');
    const summary = node('summary');
    const body = node('div');
    details.append(summary, body);
    if (!state.tools.size) $('tools').replaceChildren();
    $('tools').append(details);
    record = { details, summary, body, name: String(payload.tool_name ?? payload.name ?? '工具') };
    state.tools.set(id, record);
  }
  const labels = { tool_started: '执行中', tool_completed: '已完成', tool_error: '错误', retry: '重试' };
  record.summary.textContent = `${record.name} · ${labels[event.type]}${payload.duration_ms !== undefined ? ` · ${payload.duration_ms} ms` : ''}`;
  // Only public, explicitly selected summary fields; never display raw tool results/arguments/reasoning.
  const summary = payload.summary ?? payload.result_summary ?? payload.message ?? payload.error?.message;
  const detail = node('p', `${event.time} · #${event.seq}\n${printable(summary) || labels[event.type]}`);
  record.body.append(detail);
  $('tool-count').textContent = String(state.tools.size);
}
function citation(payload) {
  const item = payload.result && typeof payload.result === 'object' ? payload.result : payload;
  if (typeof item.citation_id !== 'string' || !item.citation_id || typeof item.source_id !== 'string' ||
      typeof item.title !== 'string' || typeof item.excerpt !== 'string' ||
      !(item.page === null || (Number.isSafeInteger(item.page) && item.page > 0)) ||
      !(item.section === null || typeof item.section === 'string')) throw new Error('引用事件缺少有效的来源、页码/章节或原文契约。');
  if (state.citations.has(item.citation_id)) return;
  const href = api.documentUrl(item.source_id, item.page);
  const article = node('article', undefined, 'evidence-card');
  article.append(node('h3', item.title), node('small', `引用 ${item.citation_id} · ${item.section || '章节未注明'}`), node('blockquote', item.excerpt));
  const link = node('a', item.page ? `查看原文 · PDF 第 ${item.page} 页` : '查看原文 · 页码未注明');
  link.href = href;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  article.append(link);
  if (!state.citations.size) $('citations').replaceChildren();
  state.citations.add(item.citation_id);
  $('citations').append(article);
  $('citation-count').textContent = String(state.citations.size);
}
function chart(payload) {
  const item = payload.result && typeof payload.result === 'object' ? payload.result : payload;
  if (typeof item.chart_id !== 'string' || typeof item.title !== 'string') throw new Error('图表事件契约无效。');
  if (state.charts.has(item.chart_id)) return;
  const src = api.chartUrl(state.session.session_id, item.chart_id);
  const article = node('article', undefined, 'chart-card');
  const figure = node('figure');
  const image = node('img');
  image.src = src;
  image.alt = item.title;
  image.loading = 'lazy';
  image.addEventListener('error', () => {
    image.hidden = true;
    figure.append(node('p', '图表加载失败或已过期；未生成替代示意图。', 'quiet'));
  }, { once: true });
  figure.append(image, node('figcaption', 'Synthetic · 服务器受控计算产物；筛选规则与覆盖范围以本轮证据为准。'));
  article.append(node('h3', item.title), figure);
  if (!state.charts.size) $('charts').replaceChildren();
  state.charts.add(item.chart_id);
  $('charts').append(article);
}
function onEvent(event) {
  const payload = event.payload;
  switch (event.type) {
    case 'run_started':
      if (state.assistant) throw new Error('同一运行收到重复开始事件。');
      state.assistant = message('assistant', '');
      state.assistant.article.dataset.state = 'generating';
      progressStage('等待模型输出 · 尚未完成验证');
      break;
    case 'text_delta': {
      const delta = payload.text ?? payload.delta;
      if (typeof delta !== 'string') throw new Error('文本增量契约无效。');
      progressStage('接收回答文本 · 尚未完成验证');
      state.assistant.body.append(document.createTextNode(delta));
      break;
    }
    case 'tool_started': case 'tool_completed': case 'tool_error':
      toolEvent(event, payload);
      progressStage(event.type === 'tool_started' ? '只读工具执行中' : '工具已返回 · 等待后续模型输出');
      break;
    case 'retry':
      if (payload.tool_call_id) toolEvent(event, payload);
      progressStage('有限重试 · 等待后端恢复');
      break;
    case 'citation': citation(payload); progressStage('已收到引用 · 最终回答尚未完成'); break;
    case 'chart': chart(payload); break;
    case 'run_completed': finishAnswer('completed', '已完成 · 后端结束验证通过'); break;
    case 'run_error': {
      const error = payload.error ?? payload;
      finishAnswer('error', error.code === 'TIMEOUT' || error.code === 'RUN_TIMEOUT' ? '超时 · 回答未完成' : '错误 · 回答未完成');
      showError({ code: error.code ?? 'RUN_ERROR', message: error.message ?? '生成失败；保留的文本未构成完整回答。' });
      break;
    }
  }
}
async function send(event) {
  event.preventDefault();
  if (state.operation || state.resetPending) return;
  const text = $('question').value.trim();
  const invalid = !text || text.length > 8000;
  $('validation-message').hidden = !invalid;
  $('question').setAttribute('aria-invalid', String(invalid));
  if (invalid) { $('question').focus(); return; }
  $('error-panel').hidden = true;
  const operation = new AbortController();
  state.operation = operation;
  state.assistant = null;
  updateControls();
  message('user', text);
  $('question').value = '';
  startProgress(operation.signal);
  try {
    if (!state.session) {
      const session = await api.createSession();
      // Retain returned session even if stopped during creation, so reset can delete it.
      state.session = session;
      setReference(session.reference_date);
      $('session-label').textContent = '临时会话 · 仅进程内保留';
    }
    operation.signal.throwIfAborted();
    await api.chat(state.session.session_id, text, { signal: operation.signal, onEvent });
  } catch (error) {
    if (operation.signal.aborted) finishAnswer('cancelled', '已停止 · 回答未完成');
    else {
      finishAnswer('error', '错误 · 回答未完成');
      showError(error);
      if (error.code === 'MODEL_NOT_CONFIGURED') $('model-status').textContent = '模型待配置 · 未验证';
    }
  } finally {
    stopProgress();
    state.operation = null;
    updateControls();
    if (!state.resetPending) $('question').focus();
  }
}
async function reset() {
  if (state.resetPending) return;
  state.resetPending = true;
  $('confirm-reset').disabled = true;
  $('reset-dialog').close();
  state.operation?.abort();
  updateControls();
  try {
    // Do not race a session creation or active stream with deletion/new requests.
    if (currentSend) await currentSend;
    if (state.session) await api.deleteSession(state.session.session_id);
    state.session = null;
    state.assistant = null;
    state.tools.clear(); state.citations.clear(); state.charts.clear();
    $('messages').replaceChildren();
    $('citations').replaceChildren(node('p', '尚无已核验引用。实际检索后才显示页码和原文。', 'quiet empty-note'));
    $('tools').replaceChildren(node('p', '尚无工具调用记录。', 'quiet empty-note'));
    $('charts').replaceChildren(initialCharts.cloneNode(true));
    $('tool-count').textContent = '0'; $('citation-count').textContent = '0';
    $('tool-panel').open = false;
    $('welcome').hidden = false;
    $('run-status').textContent = '尚未开始';
    $('error-panel').hidden = true;
    $('question').value = '';
    $('question').removeAttribute('aria-invalid');
    $('validation-message').hidden = true;
    $('session-label').textContent = '临时会话 · 刷新不保留浏览器记录';
    setReference(state.health?.reference_date);
  } catch (error) { showError(error); }
  finally {
    state.resetPending = false;
    $('confirm-reset').disabled = false;
    updateControls();
    $('question').focus();
  }
}

const initialCharts = $('charts').firstElementChild.cloneNode(true);
let currentSend = null;
$('chat-form').addEventListener('submit', event => { currentSend = send(event); });
$('question').addEventListener('keydown', event => {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter' && !event.isComposing) {
    event.preventDefault(); $('chat-form').requestSubmit();
  }
});
$('question').addEventListener('input', () => {
  $('question').removeAttribute('aria-invalid'); $('validation-message').hidden = true;
});
$('stop').addEventListener('click', () => { state.operation?.abort(); updateControls(); });
$('new-session').addEventListener('click', () => $('reset-dialog').showModal());
$('cancel-reset').addEventListener('click', () => $('reset-dialog').close());
$('confirm-reset').addEventListener('click', reset);
$('check-health').addEventListener('click', checkHealth);
$('dismiss-error').addEventListener('click', () => { $('error-panel').hidden = true; });
document.querySelectorAll('[data-prompt]').forEach(button => button.addEventListener('click', () => {
  $('question').value = button.dataset.prompt; $('question').focus();
}));

// Theme is deliberately memory-only: no cookies, browser storage or persistence.
let manualTheme = false;
const themeQuery = matchMedia('(prefers-color-scheme: dark)');
function applyTheme(dark) {
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  $('theme-toggle').setAttribute('aria-label', dark ? '切换浅色主题' : '切换深色主题');
  $('theme-toggle').firstElementChild.src = `/static/vendor/icons/${dark ? 'sun' : 'moon'}.svg`;
}
applyTheme(themeQuery.matches);
themeQuery.addEventListener('change', event => { if (!manualTheme) applyTheme(event.matches); });
$('theme-toggle').addEventListener('click', () => { manualTheme = true; applyTheme(document.documentElement.dataset.theme !== 'dark'); });

void checkHealth();