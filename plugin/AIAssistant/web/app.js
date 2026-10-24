(() => {
  'use strict';

  const TOKEN_KEY = 'ste_assistant_token';
  const API_ROOT = '/api';
  const MAX_FILE_BYTES = 24 * 1024 * 1024;
  const MAX_MODEL_BYTES = 64 * 1024 * 1024;
  const MAX_ATTACHMENTS = 8;
  const MAX_EVENTS = 1000;
  const POLL_INTERVAL_MS = 1000;
  const ALLOWED_EXTENSIONS = new Set(['docx', 'txt', 'png', 'jpg', 'jpeg', 'stl', 'obj', '3mf']);

  const byId = (id) => document.getElementById(id);
  const dom = {
    body: document.body,
    connectionPill: byId('connection-pill'),
    connectionLabel: byId('connection-label'),
    modePill: byId('mode-pill'),
    openSettings: byId('open-settings'),
    closeSettings: byId('close-settings'),
    settingsDialog: byId('settings-dialog'),
    settingsForm: byId('settings-form'),
    baseUrl: byId('base-url'),
    modelName: byId('model-name'),
    apiKey: byId('api-key'),
    clearKey: byId('clear-key'),
    keyStatus: byId('key-status'),
    settingsMode: byId('settings-mode'),
    compatibilityNotice: byId('compatibility-notice'),
    compatibilityText: byId('compatibility-text'),
    tabs: Array.from(document.querySelectorAll('[data-tab]')),
    tabPanels: Array.from(document.querySelectorAll('[data-panel]')),
    conversationList: byId('conversation-list'),
    newConversation: byId('new-conversation'),
    sessionStatus: byId('session-status'),
    sessionNote: byId('session-note'),
    modelSteps: byId('model-steps'),
    messages: byId('messages'),
    welcomeCard: byId('welcome-card'),
    starterGrid: byId('starter-grid'),
    typingIndicator: byId('typing-indicator'),
    eventLog: byId('event-log'),
    eventLogCount: byId('event-log-count'),
    busyLabel: byId('busy-label'),
    cancelOperation: byId('cancel-operation'),
    composerForm: byId('composer-form'),
    messageInput: byId('message-input'),
    sendButton: byId('send-button'),
    attachTrigger: byId('attach-trigger'),
    fileInput: byId('file-input'),
    composerAttachments: byId('composer-attachments'),
    continueCard: byId('continue-card'),
    continueReason: byId('continue-reason'),
    continueOperation: byId('continue-operation'),
    uploadStatus: byId('upload-status'),
    toastRegion: byId('toast-region'),
    sceneMeta: byId('scene-meta'),
    sceneObjectSelect: byId('scene-object-select'),
    sceneDetails: byId('scene-details'),
    materialId: byId('material-id'),
    preserveDimensions: byId('preserve-dimensions'),
    noSupports: byId('no-supports'),
    pinnedSettings: byId('pinned-settings'),
    addSetting: byId('add-setting'),
    requirementsNotes: byId('requirements-notes'),
    saveRequirements: byId('save-requirements'),
    openRequirements: byId('open-requirements'),
    closeRequirements: byId('close-requirements'),
    requirementsDialog: byId('requirements-dialog'),
    requirementsForm: byId('requirements-form'),
    requirementsChips: byId('requirements-chips'),
    requirementsCount: byId('requirements-count'),
    sliceState: byId('slice-state'),
    sliceProgress: byId('slice-progress'),
    sliceProgressText: byId('slice-progress-text'),
    slicePercent: byId('slice-percent'),
    sliceMetrics: byId('slice-metrics'),
    variantsCount: byId('variants-count'),
    variantsList: byId('variants-list'),
    outputsCount: byId('outputs-count'),
    outputsList: byId('outputs-list'),
    settingRowTemplate: byId('setting-row-template')
    ,developerModelSteps: byId('developer-model-steps')
    ,eventFilter: byId('event-filter')
    ,copyEventsJson: byId('copy-events-json')
  };

  const app = {
    state: {
      mode: 'demo',
      busy: false,
      status: 'idle',
      can_continue: false,
      pause_reason: '',
      model_steps: 0,
      configured: false,
      settings: { base_url: 'https://api.timeweb.ai/v1', model: 'dashscope/qwen3.8-flash', has_key: false },
      messages: [],
      events: [],
      cursor: 0,
      scene: { objects: [] },
      slice: { state: 'unavailable', progress: 0, result: { available: false } },
      requirements: { settings: [], material_id: '', preserve_dimensions: false, notes: '' },
      variants: [],
      outputs: [],
      last_error: ''
      ,api_version: 0
      ,conversation_id: ''
      ,conversations: []
      ,history_error: ''
    },
    eventsById: new Map(),
    pendingAttachments: [],
    uploadInFlight: 0,
    sendInFlight: false,
    continueInFlight: false,
    mutationInFlight: 0,
    stateGeneration: 0,
    pollInFlight: false,
    pollPromise: null,
    pollTimer: null,
    requirementsDirty: false,
    lastPollError: '',
    lastServerError: '',
    renderedMessagesSignature: '',
    renderedEventsSignature: '',
    compatibility: 'unknown',
    activeTab: 'chat',
    selectedSceneObject: '',
    eventFilter: '',
    lastHistoryError: '',
    toastTimer: null
  };

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.name = 'ApiError';
      this.status = status;
    }
  }

  function token() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || '';
    } catch (error) {
      return '';
    }
  }

  function captureAuthFragment() {
    if (!window.location.hash) return;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const fragmentToken = params.get('token');
    if (!fragmentToken) return;
    try {
      window.sessionStorage.setItem(TOKEN_KEY, fragmentToken);
    } catch (error) {
      showToast('Не удалось сохранить токен текущей сессии', 'error');
    }
    window.history.replaceState(null, document.title, window.location.pathname + window.location.search);
  }

  function apiHeaders(extra) {
    const headers = new Headers(extra || {});
    headers.set('X-AIAssistant-Token', token());
    return headers;
  }

  async function apiRequest(path, options) {
    const request = Object.assign({}, options || {});
    request.headers = apiHeaders(request.headers);
    if (request.body !== undefined && request.body !== null && !request.headers.has('Content-Type')) {
      request.headers.set('Content-Type', 'application/json');
    }
    let response;
    try {
      response = await window.fetch(API_ROOT + path, request);
    } catch (error) {
      throw new ApiError('Сервер плагина недоступен', 0);
    }
    const raw = await response.text();
    let payload = null;
    if (raw) {
      try {
        payload = JSON.parse(raw);
      } catch (error) {
        payload = { message: raw };
      }
    }
    if (!response.ok) {
      const message = payload && (payload.error || payload.message) ? (payload.error || payload.message) : `HTTP ${response.status}`;
      throw new ApiError(String(message), response.status);
    }
    return payload || {};
  }

  function showToast(message, kind) {
    if (!message) return;
    const toast = document.createElement('div');
    toast.className = `toast ${kind || ''}`.trim();
    toast.textContent = String(message);
    dom.toastRegion.appendChild(toast);
    window.setTimeout(() => {
      toast.remove();
    }, 4300);
  }

  function safeString(value, fallback) {
    if (value === null || value === undefined) return fallback || '';
    return String(value);
  }

  function formatNumber(value, digits) {
    const number = Number(value);
    if (!Number.isFinite(number)) return safeString(value, '—');
    return number.toLocaleString('ru-RU', { maximumFractionDigits: digits === undefined ? 2 : digits });
  }

  function formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value < 0) return '';
    if (value < 1024) return `${value} Б`;
    if (value < 1024 * 1024) return `${formatNumber(value / 1024, 1)} КБ`;
    return `${formatNumber(value / (1024 * 1024), 1)} МБ`;
  }

  function formatDuration(seconds) {
    const value = Number(seconds);
    if (!Number.isFinite(value) || value < 0) return '';
    const rounded = Math.round(value);
    const minutes = Math.floor(rounded / 60);
    const rest = rounded % 60;
    return minutes ? `${minutes} мин ${String(rest).padStart(2, '0')} с` : `${rest} с`;
  }

  function actualMetric(value, unit, formatter) {
    const suffix = unit ? ` ${unit}` : '';
    if (Array.isArray(value)) {
      if (!value.length) return null;
      const entries = value.map((entry, index) => {
        const number = Number(entry);
        return { index, number, valid: Number.isFinite(number) };
      });
      const detail = entries.map((entry) => `E${entry.index}: ${entry.valid ? formatter(entry.number) + suffix : safeString(value[entry.index], 'нет данных')}`).join(' · ');
      if (entries.every((entry) => entry.valid)) {
        const total = entries.reduce((sum, entry) => sum + entry.number, 0);
        return { value: `${formatter(total)}${suffix}`, detail: `По экструдерам: ${detail}` };
      }
      return { value: 'По экструдерам', detail };
    }
    if (value === null || value === undefined || value === '') return null;
    const formatted = formatter(value);
    return formatted ? { value: `${formatted}${suffix}`, detail: '' } : null;
  }

  function variantMetricText(value, unit, formatter) {
    const metric = actualMetric(value, unit, formatter);
    if (!metric) return '';
    return metric.detail ? `${metric.value} (${metric.detail})` : metric.value;
  }

  function appendText(parent, text, className) {
    const node = document.createElement('span');
    if (className) node.className = className;
    node.textContent = safeString(text);
    parent.appendChild(node);
    return node;
  }

  function jsonText(value) {
    if (value === undefined) return '';
    try {
      return JSON.stringify(value, null, 2);
    } catch (error) {
      return safeString(value);
    }
  }

  function setConnection(status, label) {
    dom.connectionPill.classList.remove('connected', 'error');
    if (status === 'connected') dom.connectionPill.classList.add('connected');
    if (status === 'error') dom.connectionPill.classList.add('error');
    dom.connectionLabel.textContent = label;
  }

  function stateStatus() {
    const raw = safeString(app.state.status).toLowerCase();
    if (app.state.busy) return 'running';
    if (raw) return raw;
    if (app.state.can_continue) return 'paused';
    return 'idle';
  }

  function operationLocked() {
    return Boolean(app.state.busy || app.uploadInFlight > 0 || app.sendInFlight || app.continueInFlight || app.mutationInFlight > 0);
  }

  function canUseNewRoutes() {
    return Number(app.state.api_version) >= 3 && app.compatibility !== 'legacy';
  }

  function requireApiV3(action) {
    if (canUseNewRoutes()) return true;
    showToast(`${action} временно недоступно. Сохраните проект и перезапустите STE Slicer, затем откройте помощника из меню.`, 'error');
    return false;
  }

  function renderCompatibility() {
    const legacy = app.compatibility === 'legacy';
    const unknown = app.compatibility === 'unknown';
    const historyError = safeString(app.state.history_error).trim();
    if (dom.compatibilityNotice) dom.compatibilityNotice.hidden = !legacy && !historyError;
    if (historyError && dom.compatibilityText) dom.compatibilityText.textContent = `История диалогов: ${historyError}`;
    else if (legacy && dom.compatibilityText) dom.compatibilityText.textContent = 'Обновление установлено. Сохраните проект и перезапустите STE Slicer, затем откройте помощника из меню.';
    if (historyError && historyError !== app.lastHistoryError) { app.lastHistoryError = historyError; showToast(`История диалогов: ${historyError}`, 'error'); }
    if (!historyError) app.lastHistoryError = '';
    if (dom.compatibilityNotice) {
      const reserve = dom.compatibilityNotice.hidden ? 0 : dom.compatibilityNotice.offsetHeight + 8;
      document.documentElement.style.setProperty('--compatibility-reserve', `${reserve}px`);
    }
    if (dom.newConversation) dom.newConversation.title = legacy ? 'Станет доступно после перезапуска STE Slicer' : '';
    if (unknown && dom.compatibilityNotice) dom.compatibilityNotice.hidden = true;
  }

  function beginMutation() {
    app.stateGeneration += 1;
    app.mutationInFlight += 1;
  }

  function endMutation() {
    app.mutationInFlight = Math.max(0, app.mutationInFlight - 1);
  }

  function setSessionStatus(message, kind) {
    if (!dom.sessionStatus) return;
    dom.sessionStatus.textContent = safeString(message);
    dom.sessionStatus.className = `session-status ${kind || ''}`.trim();
  }

  function modelStepTotal() {
    const raw = app.state.model_steps;
    if (raw && typeof raw === 'object') {
      const total = raw.total !== undefined ? raw.total : raw.count;
      if (Number.isFinite(Number(total))) return Number(total);
    }
    return Number.isFinite(Number(raw)) ? Number(raw) : 0;
  }

  function renderSessionState() {
    const status = stateStatus();
    const labels = {
      idle: 'Готов к задаче',
      running: 'Помощник работает',
      paused: 'Пауза: можно продолжить',
      failed: 'Завершено с ошибкой',
      cancelled: 'Остановлено',
      done: 'Готово',
      interrupted: 'Запуск прерван перезапуском'
    };
    const kind = status === 'running' ? 'active' : status === 'failed' ? 'error' : (status === 'paused' || status === 'interrupted') ? 'paused' : '';
    setSessionStatus(labels[status] || 'Готов к задаче', kind);
    if (dom.sessionNote) {
      const interrupted = status === 'interrupted';
      dom.sessionNote.hidden = !interrupted;
      dom.sessionNote.textContent = interrupted ? 'Отправьте новое сообщение, чтобы начать новый запуск.' : '';
    }
    if (dom.modelSteps) dom.modelSteps.textContent = `Шаги: ${formatNumber(modelStepTotal(), 0)}`;

    const canContinue = status === 'paused' && Boolean(app.state.can_continue);
    dom.continueCard.hidden = !canContinue;
    if (canContinue) {
      const reason = safeString(app.state.pause_reason).toLowerCase();
      const reasonText = reason === 'time_limit'
        ? 'Время текущего запуска закончилось. Продолжение начнёт следующий отрезок с сохранённого шага.'
        : 'Достигнут лимит шагов модели. Продолжение начнёт следующий отрезок с сохранённого шага.';
      dom.continueReason.textContent = reasonText;
    }
  }

  function renderHeader() {
    const mode = safeString(app.state.mode, 'demo').toLowerCase();
    const isSte = mode === 'ste' || mode === 'steslicer' || mode === 'ste_slicer';
    dom.modePill.textContent = isSte ? 'STE' : 'DEMO';
    dom.modePill.classList.toggle('ste', isSte);
    dom.settingsMode.textContent = isSte ? 'STE Slicer' : 'DEMO';
    const settings = app.state.settings || {};
    dom.keyStatus.textContent = settings.has_key ? 'ключ сохранён' : 'не задан';
    dom.keyStatus.style.color = settings.has_key ? 'var(--cyan)' : '';
  }

  function renderBusy() {
    const busy = Boolean(app.state.busy);
    const locked = operationLocked();
    dom.busyLabel.hidden = !busy;
    dom.cancelOperation.hidden = !busy;
    dom.typingIndicator.hidden = !busy;
    dom.sendButton.disabled = locked;
    dom.attachTrigger.disabled = locked;
    dom.continueOperation.disabled = locked;
    if (dom.saveRequirements) dom.saveRequirements.disabled = busy;
    if (dom.addSetting) dom.addSetting.disabled = busy;
    if (dom.openRequirements) dom.openRequirements.disabled = busy;
    if (dom.newConversation) dom.newConversation.disabled = locked || !canUseNewRoutes();
    document.querySelectorAll('[data-tool]').forEach((button) => {
      button.disabled = locked;
    });
    document.querySelectorAll('.variant-card .outline-button').forEach((button) => {
      button.disabled = locked;
    });
    document.querySelectorAll('[data-starter]').forEach((button) => {
      button.disabled = locked;
    });
    renderSessionState();
  }

  function appendInlineMarkdown(parent, source) {
    const text = safeString(source);
    const tokenPattern = /`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|\[([^\]\n]+)\]\(([^)\n]+)\)/g;
    let cursor = 0;
    let match;
    while ((match = tokenPattern.exec(text))) {
      if (match.index > cursor) appendText(parent, text.slice(cursor, match.index));
      const token = match[0];
      if (token[0] === '`') {
        const code = document.createElement('code');
        code.textContent = token.slice(1, -1);
        parent.appendChild(code);
      } else if (token[0] === '[' && match[1] !== undefined && match[2] !== undefined) {
        const href = normalizeMarkdownLink(match[2]);
        if (!href) appendText(parent, token);
        else {
          const link = document.createElement('a');
          link.textContent = match[1];
          link.href = href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          parent.appendChild(link);
        }
      } else {
        const strong = token.startsWith('**') || token.startsWith('__');
        const mark = document.createElement(strong ? 'strong' : 'em');
        mark.textContent = token.slice(strong ? 2 : 1, strong ? -2 : -1);
        parent.appendChild(mark);
      }
      cursor = tokenPattern.lastIndex;
    }
    if (cursor < text.length) appendText(parent, text.slice(cursor));
  }

  function normalizeMarkdownLink(raw) {
    const value = safeString(raw).trim();
    if (!/^https?:\/\//i.test(value) || /[\u0000-\u0020<>"'`]/.test(value)) return '';
    try {
      const url = new URL(value);
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) return '';
      return url.href;
    } catch (error) {
      return '';
    }
  }

  function appendMarkdownBlock(parent, text) {
    const paragraph = document.createElement('p');
    appendInlineMarkdown(paragraph, text);
    parent.appendChild(paragraph);
  }

  function renderMarkdown(parent, source) {
    const lines = safeString(source).replace(/\r\n?/g, '\n').split('\n');
    let paragraph = [];
    let list = null;
    let quote = null;
    let code = null;
    let table = null;
    const flushParagraph = () => { if (paragraph.length) { appendMarkdownBlock(parent, paragraph.join(' ')); paragraph = []; } };
    const closeList = () => { if (list) { parent.appendChild(list.node); list = null; } };
    const closeQuote = () => { if (quote) { parent.appendChild(quote); quote = null; } };
    const closeTable = () => { if (table) { parent.appendChild(table.node); table = null; } };
    lines.forEach((line) => {
      if (code) {
        if (/^\s*```/.test(line)) { parent.appendChild(code.node); code = null; }
        else appendText(code.node, line + '\n');
        return;
      }
      const fence = line.match(/^\s*```(?:[\w-]+)?\s*$/);
      if (fence) { flushParagraph(); closeList(); closeQuote(); closeTable(); code = { node: document.createElement('pre') }; return; }
      if (!line.trim()) { flushParagraph(); closeList(); closeQuote(); closeTable(); return; }
      const heading = line.match(/^\s*(#{1,4})\s+(.+?)\s*#*\s*$/);
      if (heading) { flushParagraph(); closeList(); closeQuote(); closeTable(); const h = document.createElement(`h${heading[1].length}`); appendInlineMarkdown(h, heading[2]); parent.appendChild(h); return; }
      const quoteLine = line.match(/^\s*>\s?(.*)$/);
      if (quoteLine) { flushParagraph(); closeList(); closeTable(); if (!quote) quote = document.createElement('blockquote'); appendInlineMarkdown(quote, quoteLine[1]); quote.appendChild(document.createElement('br')); return; }
      const item = line.match(/^\s*(?:[-*]|(\d+)\.)\s+(.+)$/);
      if (item) { flushParagraph(); closeQuote(); closeTable(); const ordered = Boolean(item[1]); if (!list || list.ordered !== ordered) { closeList(); list = { ordered, node: document.createElement(ordered ? 'ol' : 'ul') }; } const li = document.createElement('li'); appendInlineMarkdown(li, item[2]); list.node.appendChild(li); return; }
      const cells = line.trim().startsWith('|') && line.trim().endsWith('|') ? line.trim().slice(1, -1).split('|').map((cell) => cell.trim()) : null;
      if (cells) { flushParagraph(); closeList(); closeQuote(); if (cells.every((cell) => /^:?-{3,}:?$/.test(cell))) { if (table) table.delimiter = true; return; } if (!table) { table = { node: document.createElement('table'), rows: 0, delimiter: false }; } const tr = document.createElement('tr'); cells.forEach((cell) => { const cellNode = document.createElement(table.rows === 0 ? 'th' : 'td'); appendInlineMarkdown(cellNode, cell); tr.appendChild(cellNode); }); table.node.appendChild(tr); table.rows += 1; return; }
      closeList(); closeQuote(); closeTable(); paragraph.push(line.trim());
    });
    if (code) parent.appendChild(code.node);
    flushParagraph(); closeList(); closeQuote(); closeTable();
  }

  function renderMessages(messages) {
    dom.messages.replaceChildren();
    const list = Array.isArray(messages) ? messages : [];
    dom.welcomeCard.hidden = list.length > 0;
    dom.starterGrid.hidden = list.length > 0;
    for (const message of list) {
      const row = document.createElement('article');
      row.className = `message-row ${message && message.role === 'user' ? 'user' : 'assistant'}`;
      const avatar = document.createElement('span');
      avatar.className = `avatar ${message && message.role === 'user' ? 'user-avatar' : 'assistant-avatar'}`;
      avatar.textContent = message && message.role === 'user' ? 'Вы' : 'AI';
      const stack = document.createElement('div');
      stack.className = 'message-stack';
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      renderMarkdown(bubble, message && message.content);
      stack.appendChild(bubble);
      const attachments = message && Array.isArray(message.attachments) ? message.attachments : [];
      if (attachments.length) {
        const files = document.createElement('div');
        files.className = 'message-files';
        attachments.forEach((attachment) => {
          const chip = document.createElement('span');
          chip.className = 'file-chip';
          chip.textContent = `▧ ${safeString(attachment && attachment.name, 'файл')}`;
          files.appendChild(chip);
        });
        stack.appendChild(files);
      }
      const time = document.createElement('div');
      time.className = 'message-time';
      time.textContent = message && message.role === 'user' ? 'Отправлено' : 'STE Assistant';
      stack.appendChild(time);
      row.append(avatar, stack);
      dom.messages.appendChild(row);
    }
  }

  function eventKey(event, index) {
    if (event && event.id !== undefined && event.id !== null) return String(event.id);
    return `event-${index}-${event && event.type ? event.type : 'unknown'}-${event && event.name ? event.name : ''}`;
  }

  function ingestEvents(events) {
    if (!Array.isArray(events)) return;
    events.forEach((event, index) => {
      if (!event || typeof event !== 'object') return;
      const key = eventKey(event, index);
      if (!app.eventsById.has(key)) app.eventsById.set(key, event);
    });
    if (app.eventsById.size <= MAX_EVENTS) return;
    const entries = Array.from(app.eventsById.entries());
    entries.sort((left, right) => {
      const a = Number(left[1] && left[1].id);
      const b = Number(right[1] && right[1].id);
      if (Number.isFinite(a) && Number.isFinite(b)) return a - b;
      return 0;
    });
    entries.slice(0, Math.max(0, entries.length - MAX_EVENTS)).forEach(([key]) => app.eventsById.delete(key));
  }

  function renderEvents() {
    const filter = safeString(app.eventFilter).trim().toLowerCase();
    const events = Array.from(app.eventsById.values()).filter((event) => {
      if (!filter) return true;
      return safeString(JSON.stringify(event)).toLowerCase().includes(filter);
    });
    const openKeys = new Set(Array.from(dom.eventLog.querySelectorAll('details[open]')).map((details) => details.dataset.eventKey));
    const oldScrollTop = dom.eventLog.scrollTop;
    dom.eventLog.replaceChildren();
    events.sort((left, right) => {
      const a = Number(left && left.id);
      const b = Number(right && right.id);
      if (Number.isFinite(a) && Number.isFinite(b)) return a - b;
      return 0;
    });
    events.forEach((event, index) => {
      const details = document.createElement('details');
      details.className = `tool-event ${event.type === 'error' ? 'tool-error' : ''}`;
      details.dataset.eventKey = eventKey(event, index);
      details.open = openKeys.has(details.dataset.eventKey);
      const summary = document.createElement('summary');
      let title = 'Событие помощника';
      if (event.type === 'tool_start') title = `Запуск инструмента: ${safeString(event.name, 'без имени')}`;
      if (event.type === 'tool_result') title = `Результат инструмента: ${safeString(event.name, 'без имени')}`;
      if (event.type === 'error') title = `Ошибка: ${safeString(event.message, 'операция не выполнена')}`;
      if (event.type === 'info') title = safeString(event.message, 'Информация');
      summary.textContent = title;
      details.appendChild(summary);
      const payload = document.createElement('pre');
      if (event.type === 'tool_start') {
        payload.textContent = jsonText(event.arguments);
      } else if (event.type === 'tool_result') {
        payload.textContent = jsonText(event.result);
      } else if (event.message) {
        payload.textContent = safeString(event.message);
      } else {
        payload.textContent = jsonText(event);
      }
      details.appendChild(payload);
      dom.eventLog.appendChild(details);
    });
    dom.eventLog.scrollTop = oldScrollTop;
    dom.eventLogCount.textContent = String(events.length);
    if (dom.developerModelSteps) dom.developerModelSteps.textContent = `Шаги модели: ${formatNumber(modelStepTotal(), 0)}`;
  }

  function renderConversation() {
    const messages = Array.isArray(app.state.messages) ? app.state.messages : [];
    const messagesSignature = JSON.stringify(messages);
    const events = Array.from(app.eventsById.values());
    const eventsSignature = JSON.stringify(events);
    const messagesChanged = messagesSignature !== app.renderedMessagesSignature;
    const eventsChanged = eventsSignature !== app.renderedEventsSignature;
    if (!messagesChanged && !eventsChanged) return;

    const chatBody = document.getElementById('chat-body');
    const oldScrollTop = chatBody ? chatBody.scrollTop : 0;
    const distanceFromBottom = chatBody ? chatBody.scrollHeight - chatBody.scrollTop - chatBody.clientHeight : 0;
    const wasNearBottom = distanceFromBottom < 36;

    if (messagesChanged) {
      renderMessages(messages);
      app.renderedMessagesSignature = messagesSignature;
    }
    if (eventsChanged) {
      renderEvents();
      app.renderedEventsSignature = eventsSignature;
    }

    if (messagesChanged) {
      window.requestAnimationFrame(() => {
        if (!chatBody) return;
        chatBody.scrollTop = wasNearBottom ? chatBody.scrollHeight : oldScrollTop;
      });
    }
  }

  function renderScene() {
    const scene = app.state.scene || {};
    const objects = Array.isArray(scene.objects) ? scene.objects : [];
    if (dom.sceneObjectSelect) {
      const selected = app.selectedSceneObject || dom.sceneObjectSelect.value;
      dom.sceneObjectSelect.replaceChildren();
      if (!objects.length) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'Сцена ещё не загружена';
        dom.sceneObjectSelect.appendChild(option);
        app.selectedSceneObject = '';
      } else {
        objects.forEach((object, index) => {
          const option = document.createElement('option');
          option.value = safeString(object && object.id, String(index));
          option.textContent = safeString(object && object.name, `Объект ${index + 1}`);
          dom.sceneObjectSelect.appendChild(option);
        });
        const valid = objects.some((object, index) => safeString(object && object.id, String(index)) === selected);
        app.selectedSceneObject = valid ? selected : safeString(objects[0] && objects[0].id, '0');
        dom.sceneObjectSelect.value = app.selectedSceneObject;
      }
    }
    const selectedObject = objects.find((object, index) => safeString(object && object.id, String(index)) === app.selectedSceneObject) || objects[0];
    if (dom.sceneDetails) {
      dom.sceneDetails.replaceChildren();
      if (!selectedObject) {
        const empty = document.createElement('div'); empty.className = 'empty-state compact-empty'; appendText(empty, 'Выберите объект, чтобы увидеть размеры и идентификатор.'); dom.sceneDetails.appendChild(empty);
      } else {
        const bounds = selectedObject.bounds_mm || {};
        const card = document.createElement('div'); card.className = 'scene-object-detail';
        const name = document.createElement('h2'); appendText(name, safeString(selectedObject.name, 'Без имени')); card.appendChild(name);
        const detailGrid = document.createElement('div'); detailGrid.className = 'scene-detail-grid';
        [['ID объекта', safeString(selectedObject.id, '—')], ['Размеры', `${formatNumber(bounds.x, 1)} × ${formatNumber(bounds.y, 1)} × ${formatNumber(bounds.z, 1)} мм`], ['Количество', safeString(selectedObject.count, '1')]].forEach(([label, value]) => { const item = document.createElement('div'); appendText(item, label, 'detail-label'); appendText(item, value, 'detail-value'); detailGrid.appendChild(item); });
        card.appendChild(detailGrid); dom.sceneDetails.appendChild(card);
      }
    }
    if (!dom.sceneMeta) return;
    dom.sceneMeta.replaceChildren();
    const mode = scene.mode;
    const material = scene.material_id;
    if (mode) {
      const chip = document.createElement('span');
      chip.className = 'meta-chip';
      chip.textContent = `Режим: ${mode}`;
      dom.sceneMeta.appendChild(chip);
    }
    if (material) {
      const chip = document.createElement('span');
      chip.className = 'meta-chip';
      chip.textContent = `Материал: ${material}`;
      dom.sceneMeta.appendChild(chip);
    }
  }

  function renderSlice() {
    const slice = app.state.slice || {};
    const rawProgress = Number(slice.progress);
    const progress = Number.isFinite(rawProgress) ? Math.max(0, Math.min(1, rawProgress)) : 0;
    const currentState = safeString(slice.state, 'unavailable').toLowerCase();
    const labels = { not_started: 'Не запущен', running: 'Слайсинг выполняется', done: 'Готово', error: 'Ошибка слайсинга', unavailable: 'Недоступен', stale: 'Требуется новый слайсинг' };
    dom.sliceState.textContent = currentState.toUpperCase();
    dom.sliceState.className = `status-badge ${currentState === 'running' ? 'running' : currentState === 'done' ? 'done' : currentState === 'error' ? 'error' : ''}`;
    dom.sliceProgress.style.width = `${Math.round(progress * 100)}%`;
    dom.slicePercent.textContent = currentState === 'running' || progress > 0 ? `${Math.round(progress * 100)}%` : '—';
    dom.sliceProgressText.textContent = labels[currentState] || 'Ожидание запуска';
    dom.sliceMetrics.replaceChildren();
    const result = slice.result && typeof slice.result === 'object' ? slice.result : {};
    if (result.available) {
      const metrics = [];
      if (result.time_seconds !== undefined) metrics.push(['Время', actualMetric(result.time_seconds, '', formatDuration)]);
      if (result.material_mm !== undefined) metrics.push(['Материал', actualMetric(result.material_mm, 'мм', (value) => formatNumber(value, 1))]);
      if (result.material_g !== undefined) metrics.push(['Вес', actualMetric(result.material_g, 'г', (value) => formatNumber(value, 2))]);
      metrics.forEach(([label, metric]) => {
        if (!metric) return;
        const card = document.createElement('div');
        card.className = 'metric';
        appendText(card, label, 'metric-label');
        appendText(card, metric.value, 'metric-value');
        if (metric.detail) appendText(card, metric.detail, 'metric-detail');
        dom.sliceMetrics.appendChild(card);
      });
    }
  }

  function renderVariants() {
    const variants = Array.isArray(app.state.variants) ? app.state.variants : [];
    dom.variantsCount.textContent = String(variants.length);
    dom.variantsList.replaceChildren();
    if (!variants.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state compact-empty';
      appendText(empty, '⇄', 'empty-icon');
      appendText(empty, 'Попросите сравнить 2–3 режима');
      dom.variantsList.appendChild(empty);
      return;
    }
    variants.forEach((variant) => {
      const card = document.createElement('article');
      card.className = 'variant-card';
      const top = document.createElement('div');
      top.className = 'variant-top';
      appendText(top, variant && variant.label ? variant.label : 'Вариант печати', 'variant-label');
      const result = variant && variant.result && typeof variant.result === 'object' ? variant.result : {};
      const status = result.state || (result.available ? 'готов' : 'ожидает');
      appendText(top, status, 'status-badge');
      card.appendChild(top);
      const resultLine = document.createElement('div');
      resultLine.className = 'variant-result';
      const resultParts = [];
      if (result.time_seconds !== undefined) {
        const value = variantMetricText(result.time_seconds, '', formatDuration);
        if (value) resultParts.push(`Время: ${value}`);
      }
      if (result.material_g !== undefined) {
        const value = variantMetricText(result.material_g, 'г', (entry) => formatNumber(entry, 2));
        if (value) resultParts.push(`Материал: ${value}`);
      }
      if (result.material_mm !== undefined) {
        const value = variantMetricText(result.material_mm, 'мм', (entry) => formatNumber(entry, 1));
        if (value) resultParts.push(`Длина: ${value}`);
      }
      resultLine.textContent = resultParts.length ? resultParts.join(' · ') : 'Метрики появятся после фактического слайсинга';
      card.appendChild(resultLine);
      const settings = variant && variant.settings && typeof variant.settings === 'object' ? variant.settings : {};
      const settingKeys = Object.keys(settings);
      if (settingKeys.length) {
        const settingList = document.createElement('div');
        settingList.className = 'variant-settings';
        settingKeys.forEach((key) => {
          const chip = document.createElement('span');
          chip.className = 'variant-setting';
          chip.textContent = `${key}: ${safeString(settings[key])}`;
          settingList.appendChild(chip);
        });
        card.appendChild(settingList);
      }
      if (variant && variant.id) {
        const apply = document.createElement('button');
        apply.className = 'outline-button';
        apply.type = 'button';
        apply.textContent = 'Применить вариант';
        apply.dataset.variantId = String(variant.id);
        apply.addEventListener('click', () => callTool('apply_variant', { variant_id: variant.id }));
        card.appendChild(apply);
      }
      dom.variantsList.appendChild(card);
    });
  }

  function renderOutputs() {
    const outputs = Array.isArray(app.state.outputs) ? app.state.outputs : [];
    dom.outputsCount.textContent = String(outputs.length);
    dom.outputsList.replaceChildren();
    if (!outputs.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state compact-empty';
      appendText(empty, '⇩', 'empty-icon');
      appendText(empty, 'Готовые файлы появятся здесь');
      dom.outputsList.appendChild(empty);
      return;
    }
    outputs.forEach((output) => {
      const card = document.createElement('article');
      card.className = 'output-card';
      const available = Boolean(output && output.available !== false);
      appendText(card, '⇩', 'output-icon');
      const info = document.createElement('div');
      info.className = 'output-info';
      appendText(info, output && output.filename ? output.filename : 'Файл', 'output-name');
      appendText(info, available ? formatBytes(output && output.size_bytes) : 'Файл недоступен', available ? 'output-size' : 'output-size output-unavailable');
      card.appendChild(info);
      if (available && outputDownloadPath(output)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'download-button';
        button.textContent = '↓';
        button.title = 'Скачать файл';
        button.addEventListener('click', () => downloadOutput(output));
        card.appendChild(button);
      }
      dom.outputsList.appendChild(card);
    });
  }

  function parseSettingValue(raw) {
    const value = safeString(raw).trim();
    if (value === 'true') return true;
    if (value === 'false') return false;
    if (value !== '' && Number.isFinite(Number(value))) return Number(value);
    return value;
  }

  function addSettingRow(setting, options) {
    const source = setting || {};
    const fragment = dom.settingRowTemplate.content.cloneNode(true);
    const row = fragment.querySelector('.setting-row');
    const key = fragment.querySelector('.setting-key');
    const value = fragment.querySelector('.setting-value');
    const scope = fragment.querySelector('.setting-scope');
    const extruder = fragment.querySelector('.setting-extruder');
    key.value = safeString(source.key);
    value.value = source.value === undefined ? '' : safeString(source.value);
    scope.value = source.scope === 'extruder' ? 'extruder' : 'global';
    extruder.value = source.extruder === undefined ? '0' : safeString(source.extruder);
    if (options && options.autoSupport) row.dataset.autoSupport = 'true';
    const syncExtruder = () => {
      extruder.hidden = scope.value !== 'extruder';
      row.style.gridTemplateColumns = scope.value === 'extruder' ? 'minmax(0,1fr) minmax(0,1fr) 55px 20px' : '';
    };
    scope.addEventListener('change', () => {
      app.requirementsDirty = true;
      syncExtruder();
    });
    [key, value, extruder].forEach((field) => field.addEventListener('input', () => { app.requirementsDirty = true; }));
    fragment.querySelector('.remove-setting').addEventListener('click', () => {
      row.remove();
      app.requirementsDirty = true;
      syncNoSupportCheckboxFromRows();
    });
    dom.pinnedSettings.appendChild(fragment);
    syncExtruder();
    return row;
  }

  function settingRows() {
    return Array.from(dom.pinnedSettings.querySelectorAll('.setting-row'));
  }

  function syncNoSupportCheckboxFromRows() {
    const support = settingRows().find((row) => row.querySelector('.setting-key').value.trim() === 'support_enable');
    dom.noSupports.checked = Boolean(support && parseSettingValue(support.querySelector('.setting-value').value) === false);
  }

  function syncRequirementsFromState() {
    if (app.requirementsDirty) return;
    const requirements = app.state.requirements || {};
    dom.materialId.value = safeString(requirements.material_id);
    dom.preserveDimensions.checked = Boolean(requirements.preserve_dimensions);
    dom.requirementsNotes.value = safeString(requirements.notes);
    dom.pinnedSettings.replaceChildren();
    const settings = Array.isArray(requirements.settings) ? requirements.settings : [];
    settings.forEach((setting) => addSettingRow(setting));
    dom.requirementsCount.textContent = String(settings.length + (dom.preserveDimensions.checked ? 1 : 0));
    syncNoSupportCheckboxFromRows();
    renderRequirementChips(requirements);
  }

  function renderRequirementChips(requirements) {
    if (!dom.requirementsChips) return;
    dom.requirementsChips.replaceChildren();
    const pins = [];
    if (requirements && requirements.material_id) pins.push(`Материал: ${requirements.material_id}`);
    if (requirements && requirements.preserve_dimensions) pins.push('Сохранить размеры');
    const settings = requirements && Array.isArray(requirements.settings) ? requirements.settings : [];
    settings.forEach((setting) => {
      if (!setting || !setting.key) return;
      if (setting.key === 'support_enable' && setting.value === false) pins.push('Без поддержек');
      else pins.push(`${setting.key}: ${safeString(setting.value)}`);
    });
    if (requirements && safeString(requirements.notes).trim()) pins.push('Пожелания добавлены');
    if (!pins.length) { appendText(dom.requirementsChips, 'Условия не закреплены', 'requirements-empty'); return; }
    pins.slice(0, 4).forEach((pin) => appendText(dom.requirementsChips, pin, 'requirement-chip'));
    if (pins.length > 4) appendText(dom.requirementsChips, `+${pins.length - 4}`, 'requirement-chip more');
  }

  function collectRequirements() {
    const settings = [];
    settingRows().forEach((row) => {
      const key = row.querySelector('.setting-key').value.trim();
      if (!key) return;
      const scope = row.querySelector('.setting-scope').value === 'extruder' ? 'extruder' : 'global';
      settings.push({
        key,
        value: parseSettingValue(row.querySelector('.setting-value').value),
        scope,
        extruder: Number(row.querySelector('.setting-extruder').value) || 0
      });
    });
    return {
      settings,
      material_id: dom.materialId.value.trim(),
      preserve_dimensions: Boolean(dom.preserveDimensions.checked),
      notes: dom.requirementsNotes.value.trim()
    };
  }

  function ensureSupportSetting() {
    const existing = settingRows().find((row) => row.querySelector('.setting-key').value.trim() === 'support_enable');
    if (existing) {
      existing.querySelector('.setting-value').value = 'false';
      return;
    }
    addSettingRow({ key: 'support_enable', value: false, scope: 'global' }, { autoSupport: true });
  }

  function removeAutoSupportSetting() {
    settingRows().forEach((row) => {
      const key = row.querySelector('.setting-key').value.trim();
      const value = parseSettingValue(row.querySelector('.setting-value').value);
      if (row.dataset.autoSupport === 'true' || (key === 'support_enable' && value === false)) row.remove();
    });
  }

  function renderConversations() {
    if (!dom.conversationList) return;
    const records = Array.isArray(app.state.conversations) ? app.state.conversations : [];
    dom.conversationList.replaceChildren();
    if (!records.length) {
      const empty = document.createElement('div'); empty.className = 'empty-state compact-empty'; appendText(empty, canUseNewRoutes() ? 'Новых диалогов пока нет' : 'Сохранённые диалоги появятся после перезапуска STE Slicer'); dom.conversationList.appendChild(empty); return;
    }
    const orderedRecords = records.map((record, index) => ({ record, index })).sort((left, right) => {
      const leftTime = Date.parse(safeString(left.record && (left.record.updated_at || left.record.created_at)));
      const rightTime = Date.parse(safeString(right.record && (right.record.updated_at || right.record.created_at)));
      if (Number.isFinite(leftTime) && Number.isFinite(rightTime) && leftTime !== rightTime) return rightTime - leftTime;
      if (Number.isFinite(leftTime) !== Number.isFinite(rightTime)) return Number.isFinite(leftTime) ? -1 : 1;
      return right.index - left.index;
    }).map((entry) => entry.record);
    orderedRecords.forEach((record) => {
      if (!record || !record.id) return;
      const button = document.createElement('button');
      button.type = 'button'; button.className = `conversation-item ${String(record.id) === String(app.state.conversation_id) ? 'active' : ''}`;
      button.disabled = operationLocked() || !canUseNewRoutes(); button.dataset.conversationId = String(record.id);
      const title = document.createElement('strong'); title.textContent = safeString(record.title, 'Новый диалог');
      const date = document.createElement('span'); date.textContent = formatConversationDate(record.updated_at || record.created_at);
      button.append(title, date); button.addEventListener('click', () => selectConversation(record.id)); dom.conversationList.appendChild(button);
    });
  }

  function formatConversationDate(value) {
    if (!value) return 'Без сообщений';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return safeString(value);
    return date.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  }

  function applyState(next) {
    if (!next || typeof next !== 'object') return;
    app.state = Object.assign({}, app.state, next);
    if (next.settings) app.state.settings = Object.assign({}, app.state.settings, next.settings);
    ingestEvents(next.events);
    if (next.cursor !== undefined && next.cursor !== null) app.state.cursor = next.cursor;
    renderHeader();
    if (next.api_version !== undefined) {
      app.compatibility = Number(next.api_version) >= 3 ? 'ready' : 'legacy';
    } else if (app.compatibility === 'unknown') {
      app.compatibility = 'legacy';
    }
    renderCompatibility();
    renderBusy();
    renderConversation();
    renderConversations();
    renderScene();
    renderSlice();
    renderVariants();
    renderOutputs();
    syncRequirementsFromState();
    if (app.state.last_error) showServerError(app.state.last_error);
    else app.lastServerError = '';
  }

  function showServerError(message) {
    const text = safeString(message).trim();
    if (!text || text === app.lastServerError) return;
    app.lastServerError = text;
    showToast(text, 'error');
  }

  function schedulePoll() {
    if (app.pollTimer !== null || app.pollPromise) return;
    app.pollTimer = window.setTimeout(() => {
      app.pollTimer = null;
      pollState();
    }, POLL_INTERVAL_MS);
  }

  function pollState(options) {
    const force = Boolean(options && options.force);
    if (app.pollPromise) {
      if (!force) return app.pollPromise;
      return app.pollPromise.then(() => pollState({ force: true }));
    }
    if (app.pollTimer !== null) {
      window.clearTimeout(app.pollTimer);
      app.pollTimer = null;
    }

    const requestGeneration = app.stateGeneration;
    const request = (async () => {
      app.pollInFlight = true;
      try {
        const after = encodeURIComponent(safeString(app.state.cursor, '0'));
        const next = await apiRequest(`/state?after=${after}`, { method: 'GET' });
        if (requestGeneration !== app.stateGeneration || app.mutationInFlight > 0) return;
        app.lastPollError = '';
        setConnection('connected', 'В СЕТИ');
        applyState(next);
      } catch (error) {
        if (requestGeneration !== app.stateGeneration || app.mutationInFlight > 0) return;
        setConnection('error', 'НЕТ СВЯЗИ');
        setSessionStatus('Нет связи с помощником', 'error');
        if (error && error.message && error.message !== app.lastPollError) {
          app.lastPollError = error.message;
          showToast(error.message, 'error');
        }
      } finally {
        app.pollInFlight = false;
      }
    })();
    app.pollPromise = request;
    const finishPoll = () => {
      if (app.pollPromise === request) {
        app.pollPromise = null;
        schedulePoll();
      }
    };
    request.then(finishPoll, finishPoll);
    return request;
  }

  function readFileAsBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        const result = safeString(reader.result);
        resolve(result.includes(',') ? result.slice(result.indexOf(',') + 1) : result);
      };
      reader.onerror = () => reject(new Error('Не удалось прочитать файл'));
      reader.readAsDataURL(file);
    });
  }

  function fileExtension(filename) {
    const parts = safeString(filename).toLowerCase().split('.');
    return parts.length > 1 ? parts.pop() : '';
  }

  function renderPendingAttachments() {
    dom.composerAttachments.replaceChildren();
    dom.composerAttachments.hidden = app.pendingAttachments.length === 0;
    app.pendingAttachments.forEach((attachment, index) => {
      const chip = document.createElement('span');
      const kind = safeString(attachment.kind, 'model').toLowerCase();
      chip.className = `pending-file pending-file-${kind}`;
      const kindLabels = { document: 'ТЗ DOCX', text: 'Текст TXT', model: 'Модель', image: 'Изображение' };
      appendText(chip, kindLabels[kind] || 'Файл', 'pending-file-kind');
      const label = document.createElement('span');
      label.className = 'pending-file-name';
      label.title = safeString(attachment.name, 'файл');
      label.textContent = safeString(attachment.name, 'файл');
      chip.appendChild(label);
      if (attachment.text_chars !== undefined && attachment.text_chars !== null) {
        appendText(chip, `${formatNumber(attachment.text_chars, 0)} симв.`, 'pending-file-meta');
      }
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.title = 'Удалить вложение';
      remove.disabled = operationLocked();
      remove.addEventListener('click', () => {
        if (operationLocked()) return;
        app.pendingAttachments.splice(index, 1);
        renderPendingAttachments();
        if (app.pendingAttachments.length) setUploadStatus(`Готово вложений: ${app.pendingAttachments.length}/${MAX_ATTACHMENTS}`, true);
        else setUploadStatus('', false);
      });
      chip.appendChild(remove);
      dom.composerAttachments.appendChild(chip);
    });
  }

  function setUploadStatus(message, visible) {
    const text = safeString(message).trim();
    dom.uploadStatus.textContent = text;
    dom.uploadStatus.hidden = !visible || !text;
  }

  function attachmentKind(file, response) {
    const extension = fileExtension(file.name);
    if (extension === 'docx') return 'document';
    if (extension === 'txt') return 'text';
    if (['png', 'jpg', 'jpeg'].includes(extension)) return 'image';
    if (response && response.kind) return safeString(response.kind).toLowerCase();
    return 'model';
  }

  async function uploadFile(file) {
    if (!file || !file.name || file.size <= 0) {
      showToast(`Файл «${safeString(file && file.name, 'без имени')}» пуст`, 'error');
      return;
    }
    const extension = fileExtension(file.name);
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      showToast(`Формат .${extension || 'без расширения'} не поддерживается`, 'error');
      return;
    }
    const limit = ['stl', 'obj', '3mf'].includes(extension) ? MAX_MODEL_BYTES : MAX_FILE_BYTES;
    if (file.size > limit) {
      showToast(`Файл «${file.name}» больше ${limit / (1024 * 1024)} MiB`, 'error');
      return;
    }
    if (app.pendingAttachments.length + app.uploadInFlight >= MAX_ATTACHMENTS) {
      showToast(`Можно прикрепить не больше ${MAX_ATTACHMENTS} файлов за сообщение`, 'error');
      return;
    }
    app.uploadInFlight += 1;
    renderBusy();
    setUploadStatus(`Обрабатываем «${file.name}»…`, true);
    try {
      const dataBase64 = await readFileAsBase64(file);
      const response = await apiRequest('/upload', {
        method: 'POST',
        body: JSON.stringify({ filename: file.name, data_base64: dataBase64 })
      });
      if (!response || !response.id) throw new Error('Сервер не вернул идентификатор файла');
      app.pendingAttachments.push({
        id: response.id,
        name: response.name || file.name,
        kind: attachmentKind(file, response),
        mime_type: response.mime_type || file.type || '',
        text_chars: response.text_chars
      });
      renderPendingAttachments();
      setUploadStatus(`Файл «${file.name}» готов к отправке · ${app.pendingAttachments.length}/${MAX_ATTACHMENTS}`, true);
    } catch (error) {
      showToast(`Не удалось добавить «${file.name}»: ${error.message}`, 'error');
      setUploadStatus(`Не удалось добавить «${file.name}»`, true);
    } finally {
      app.uploadInFlight = Math.max(0, app.uploadInFlight - 1);
      renderPendingAttachments();
      renderBusy();
      if (app.uploadInFlight === 0 && !app.pendingAttachments.length) setUploadStatus('', false);
    }
  }

  async function sendMessage(textOverride) {
    if (operationLocked()) return;
    const draftSnapshot = dom.messageInput.value;
    const text = textOverride === undefined ? draftSnapshot.trim() : safeString(textOverride).trim();
    const attachmentSnapshot = app.pendingAttachments.slice();
    const attachments = attachmentSnapshot.map((item) => item.id);
    if (!text && attachments.length === 0) return;
    app.sendInFlight = true;
    beginMutation();
    renderBusy();
    setSessionStatus('Отправляем запрос…', 'active');
    try {
      const response = await apiRequest('/message', {
        method: 'POST',
        body: JSON.stringify({ text, attachments })
      });
      if (response && response.accepted) {
        app.state = Object.assign({}, app.state, { busy: true });
        if (dom.messageInput.value === draftSnapshot) {
          dom.messageInput.value = '';
          autoResizeComposer();
        }
        const stillSameAttachments = app.pendingAttachments.length === attachmentSnapshot.length
          && app.pendingAttachments.every((item, index) => item.id === attachmentSnapshot[index].id);
        if (stillSameAttachments) app.pendingAttachments = [];
        renderPendingAttachments();
        if (!app.pendingAttachments.length) setUploadStatus('', false);
        renderBusy();
        setSessionStatus('Запрос принят, помощник работает…', 'active');
      } else {
        showToast('Сервер не подтвердил отправку запроса', 'error');
        setSessionStatus('Запрос не принят', 'error');
      }
    } catch (error) {
      showToast(`Не удалось отправить сообщение: ${error.message}`, 'error');
      setSessionStatus('Не удалось отправить запрос', 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
      app.sendInFlight = false;
      renderBusy();
    }
  }

  async function continueOperation() {
    if (operationLocked() || stateStatus() !== 'paused' || !app.state.can_continue) return;
    app.continueInFlight = true;
    beginMutation();
    renderBusy();
    setSessionStatus('Продолжаем с сохранённого шага…', 'active');
    try {
      const response = await apiRequest('/continue', { method: 'POST', body: JSON.stringify({}) });
      if (response && response.accepted) {
        app.state = Object.assign({}, app.state, { busy: true, status: 'running' });
        renderBusy();
        setSessionStatus('Продолжение запущено…', 'active');
      } else {
        showToast('Сервер не подтвердил продолжение', 'error');
        setSessionStatus('Продолжение не запущено', 'error');
      }
    } catch (error) {
      showToast(`Не удалось продолжить задачу: ${error.message}`, 'error');
      setSessionStatus('Продолжение недоступно', 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
      app.continueInFlight = false;
      renderBusy();
    }
  }

  async function saveRequirements() {
    if (operationLocked()) return;
    const payload = collectRequirements();
    beginMutation();
    try {
      await apiRequest('/requirements', { method: 'POST', body: JSON.stringify(payload) });
      app.state.requirements = payload;
      app.requirementsDirty = false;
      syncRequirementsFromState();
      closeRequirements();
      setSessionStatus('Требования сохранены', '');
    } catch (error) {
      showToast(`Требования не сохранены: ${error.message}`, 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
    }
  }

  async function callTool(name, argumentsObject) {
    if (operationLocked()) return;
    const args = argumentsObject || {};
    beginMutation();
    try {
      app.state = Object.assign({}, app.state, { busy: true });
      renderBusy();
      const response = await apiRequest('/tool', { method: 'POST', body: JSON.stringify({ name, arguments: args }) });
      const immediateResult = response && response.result;
      if (immediateResult && immediateResult.ok === false) {
        app.state = Object.assign({}, app.state, { busy: false });
        renderBusy();
        showToast(`Инструмент «${name}»: ${safeString(immediateResult.error, 'операция отклонена')}`, 'error');
      } else {
        setSessionStatus(`Инструмент «${name}» запущен…`, 'active');
      }
    } catch (error) {
      app.state = Object.assign({}, app.state, { busy: false });
      renderBusy();
      showToast(`Инструмент «${name}» не выполнен: ${error.message}`, 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
    }
  }

  async function cancelOperation() {
    if (!app.state.busy) return;
    beginMutation();
    try {
      await apiRequest('/cancel', { method: 'POST', body: JSON.stringify({}) });
      setSessionStatus('Остановка запрошена…', 'active');
    } catch (error) {
      showToast(`Не удалось остановить операцию: ${error.message}`, 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
    }
  }

  async function selectConversation(id) {
    if (!requireApiV3('Переключение диалога') || operationLocked() || !id || String(id) === String(app.state.conversation_id)) return;
    if (composerHasUnsentWork()) return;
    beginMutation();
    app.eventsById.clear();
    app.renderedMessagesSignature = '';
    app.renderedEventsSignature = '';
    app.state = Object.assign({}, app.state, { conversation_id: String(id), cursor: 0, messages: [], events: [], busy: false, status: 'idle', can_continue: false, pause_reason: '' });
    renderConversation(); renderEvents(); renderBusy();
    try {
      const response = await apiRequest('/conversations/select', { method: 'POST', body: JSON.stringify({ id }) });
      applyState(Object.assign({}, response || {}, { conversation_id: id }));
      setSessionStatus('Диалог выбран', '');
    } catch (error) {
      showToast(`Диалог не выбран: ${error.message}`, 'error');
    } finally {
      endMutation(); renderBusy(); await pollState({ force: true });
    }
  }

  async function newConversation() {
    if (!requireApiV3('Новый диалог') || operationLocked()) return;
    if (composerHasUnsentWork()) return;
    beginMutation();
    app.eventsById.clear();
    app.renderedMessagesSignature = '';
    app.renderedEventsSignature = '';
    app.state = Object.assign({}, app.state, { conversation_id: '', cursor: 0, messages: [], events: [], busy: false, status: 'idle', can_continue: false, pause_reason: '', model_steps: 0 });
    renderConversation(); renderEvents(); renderBusy();
    try {
      const response = await apiRequest('/conversations', { method: 'POST', body: JSON.stringify({}) });
      applyState(response || {}); setSessionStatus('Создан новый диалог', '');
    } catch (error) {
      showToast(`Новый диалог не создан: ${error.message}`, 'error');
    } finally {
      endMutation(); renderBusy(); await pollState({ force: true });
    }
  }

  function composerHasUnsentWork() {
    const hasAttachments = app.pendingAttachments.length > 0 || app.uploadInFlight > 0;
    const hasDraft = Boolean(dom.messageInput && dom.messageInput.value.trim());
    if (!hasAttachments && !hasDraft && !app.requirementsDirty) return false;
    if (app.requirementsDirty) {
      showToast('Сначала добавьте условия в текущий диалог или отмените их изменения.', 'error');
      return true;
    }
    showToast(hasAttachments ? 'Сначала отправьте или удалите вложения.' : 'Сначала отправьте или очистьте черновик.', 'error');
    return true;
  }

  async function resetChat() {
    if (operationLocked()) {
      showToast('Дождитесь завершения операции или остановите её', 'error');
      return;
    }
    beginMutation();
    try {
      await apiRequest('/reset', { method: 'POST', body: JSON.stringify({}) });
      app.eventsById.clear();
      app.state = Object.assign({}, app.state, { cursor: 0, messages: [], events: [], last_error: '', busy: false, status: 'idle', can_continue: false, pause_reason: '', model_steps: 0 });
      app.requirementsDirty = false;
      app.pendingAttachments = [];
      renderPendingAttachments();
      setUploadStatus('', false);
      applyState(app.state);
      setSessionStatus('Диалог очищен', '');
    } catch (error) {
      showToast(`Диалог не очищен: ${error.message}`, 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
    }
  }

  function outputDownloadPath(output) {
    if (!output || output.available === false) return '';
    if (output.id !== undefined && output.id !== null && safeString(output.id) !== '') {
      return `${API_ROOT}/download/${encodeURIComponent(safeString(output.id))}`;
    }
    const raw = safeString(output.url).trim();
    if (!raw) return '';
    try {
      const parsed = new URL(raw, window.location.origin);
      if (parsed.origin !== window.location.origin || parsed.search || parsed.hash || !/^\/api\/download\/[^/]+$/.test(parsed.pathname)) return '';
      return parsed.pathname;
    } catch (error) {
      return '';
    }
  }

  async function downloadOutput(output) {
    const url = outputDownloadPath(output);
    if (!url) return;
    try {
      const response = await window.fetch(url, { method: 'GET', headers: apiHeaders() });
      if (!response.ok) throw new ApiError(`HTTP ${response.status}`, response.status);
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = output.filename || 'ste-output';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (error) {
      showToast(`Не удалось скачать файл: ${error.message}`, 'error');
    }
  }

  function autoResizeComposer() {
    dom.messageInput.style.height = 'auto';
    dom.messageInput.style.height = `${Math.min(dom.messageInput.scrollHeight, 130)}px`;
  }

  function openSettings() {
    const settings = app.state.settings || {};
    dom.baseUrl.value = settings.base_url || 'https://api.timeweb.ai/v1';
    dom.modelName.value = settings.model || 'dashscope/qwen3.8-flash';
    dom.apiKey.value = '';
    dom.clearKey.checked = false;
    renderHeader();
    if (typeof dom.settingsDialog.showModal === 'function') dom.settingsDialog.showModal();
    else dom.settingsDialog.setAttribute('open', '');
  }

  function closeSettings() {
    if (typeof dom.settingsDialog.close === 'function') dom.settingsDialog.close();
    else dom.settingsDialog.removeAttribute('open');
  }

  function switchTab(name) {
    const tabName = ['chat', 'scene', 'developers'].includes(name) ? name : 'chat';
    app.activeTab = tabName;
    dom.tabs.forEach((tab) => tab.classList.toggle('active', tab.dataset.tab === tabName));
    dom.tabPanels.forEach((panel) => { const active = panel.dataset.panel === tabName; panel.classList.toggle('active', active); panel.hidden = !active; });
    if (tabName === 'developers') renderEvents();
  }

  function openRequirements() {
    syncRequirementsFromState();
    if (typeof dom.requirementsDialog.showModal === 'function') dom.requirementsDialog.showModal();
    else dom.requirementsDialog.setAttribute('open', '');
  }

  function closeRequirements() {
    if (typeof dom.requirementsDialog.close === 'function') dom.requirementsDialog.close();
    else dom.requirementsDialog.removeAttribute('open');
  }

  async function copyEventsJson() {
    const payload = jsonText(Array.from(app.eventsById.values()));
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) await navigator.clipboard.writeText(payload);
      else {
        const input = document.createElement('textarea'); input.value = payload; input.style.position = 'fixed'; input.style.opacity = '0'; document.body.appendChild(input); input.select(); document.execCommand('copy'); input.remove();
      }
      showToast('Журнал скопирован в JSON', 'success');
    } catch (error) { showToast('Не удалось скопировать журнал', 'error'); }
  }

  async function saveSettings(event) {
    event.preventDefault();
    const apiKey = dom.apiKey.value;
    const payload = {
      base_url: dom.baseUrl.value.trim() || 'https://api.timeweb.ai/v1',
      model: dom.modelName.value.trim() || 'dashscope/qwen3.8-flash',
      api_key: apiKey,
      clear_key: Boolean(dom.clearKey.checked)
    };
    beginMutation();
    try {
      const response = await apiRequest('/settings', { method: 'POST', body: JSON.stringify(payload) });
      const returnedSettings = response && response.settings ? response.settings : response;
      if (returnedSettings && typeof returnedSettings === 'object') {
        const safeSettings = {};
        ['base_url', 'model', 'has_key'].forEach((key) => {
          if (returnedSettings[key] !== undefined) safeSettings[key] = returnedSettings[key];
        });
        app.state.settings = Object.assign({}, app.state.settings, safeSettings);
      }
      dom.apiKey.value = '';
      dom.clearKey.checked = false;
      renderHeader();
      closeSettings();
      setSessionStatus('Настройки подключения сохранены', '');
    } catch (error) {
      dom.apiKey.value = '';
      showToast(`Настройки не сохранены: ${error.message}`, 'error');
    } finally {
      endMutation();
      await pollState({ force: true });
    }
  }

  function bindEvents() {
    dom.tabs.forEach((tab) => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));
    if (dom.sceneObjectSelect) dom.sceneObjectSelect.addEventListener('change', () => { app.selectedSceneObject = dom.sceneObjectSelect.value; renderScene(); });
    if (dom.eventFilter) dom.eventFilter.addEventListener('input', () => { app.eventFilter = dom.eventFilter.value; renderEvents(); });
    if (dom.copyEventsJson) dom.copyEventsJson.addEventListener('click', copyEventsJson);
    if (dom.newConversation) dom.newConversation.addEventListener('click', newConversation);
    dom.openSettings.addEventListener('click', openSettings);
    dom.closeSettings.addEventListener('click', closeSettings);
    dom.settingsForm.addEventListener('submit', saveSettings);
    dom.settingsDialog.addEventListener('click', (event) => {
      if (event.target === dom.settingsDialog) closeSettings();
    });
    if (dom.openRequirements) dom.openRequirements.addEventListener('click', openRequirements);
    if (dom.closeRequirements) dom.closeRequirements.addEventListener('click', closeRequirements);
    if (dom.requirementsDialog) dom.requirementsDialog.addEventListener('click', (event) => { if (event.target === dom.requirementsDialog) closeRequirements(); });
    if (dom.requirementsForm) dom.requirementsForm.addEventListener('submit', (event) => { event.preventDefault(); saveRequirements(); });
    dom.composerForm.addEventListener('submit', (event) => {
      event.preventDefault();
      sendMessage();
    });
    dom.messageInput.addEventListener('input', () => {
      app.requirementsDirty = app.requirementsDirty;
      autoResizeComposer();
    });
    dom.messageInput.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        dom.composerForm.requestSubmit();
      }
    });
    dom.attachTrigger.addEventListener('click', () => dom.fileInput.click());
    dom.fileInput.addEventListener('change', async () => {
      const files = Array.from(dom.fileInput.files || []);
      dom.fileInput.value = '';
      for (const file of files) {
        if (app.pendingAttachments.length + app.uploadInFlight >= MAX_ATTACHMENTS) {
          showToast(`Можно прикрепить не больше ${MAX_ATTACHMENTS} файлов за сообщение`, 'error');
          break;
        }
        await uploadFile(file);
      }
    });
    dom.continueOperation.addEventListener('click', continueOperation);
    dom.cancelOperation.addEventListener('click', cancelOperation);
    dom.addSetting.addEventListener('click', () => {
      addSettingRow();
      app.requirementsDirty = true;
    });
    dom.noSupports.addEventListener('change', () => {
      app.requirementsDirty = true;
      if (dom.noSupports.checked) ensureSupportSetting();
      else removeAutoSupportSetting();
    });
    [dom.materialId, dom.preserveDimensions, dom.requirementsNotes].forEach((field) => {
      field.addEventListener('input', () => { app.requirementsDirty = true; });
      field.addEventListener('change', () => { app.requirementsDirty = true; });
    });
    document.querySelectorAll('[data-tool]').forEach((button) => {
      button.addEventListener('click', () => {
        const name = button.dataset.tool;
        if (name === 'save_project') return callTool(name, { filename: 'project.3mf' });
        if (name === 'export_gcode') return callTool(name, { filename: 'result.gcode' });
        return callTool(name, {});
      });
    });
    document.querySelectorAll('[data-starter]').forEach((button) => {
      button.addEventListener('click', () => sendMessage(button.dataset.starter));
    });
  }

  function start() {
    captureAuthFragment();
    bindEvents();
    renderHeader();
    renderBusy();
    renderScene();
    renderSlice();
    renderVariants();
    renderOutputs();
    syncRequirementsFromState();
    autoResizeComposer();
    pollState();
  }

  start();
})();
