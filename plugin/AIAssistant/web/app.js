(() => {
  'use strict';

  const TOKEN_KEY = 'ste_assistant_token';
  const API_ROOT = '/api';
  const MAX_FILE_BYTES = 24 * 1024 * 1024;
  const POLL_INTERVAL_MS = 1000;
  const ALLOWED_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'stl', 'obj', '3mf']);

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
    messages: byId('messages'),
    welcomeCard: byId('welcome-card'),
    starterGrid: byId('starter-grid'),
    typingIndicator: byId('typing-indicator'),
    eventLog: byId('event-log'),
    busyLabel: byId('busy-label'),
    cancelOperation: byId('cancel-operation'),
    resetChat: byId('reset-chat'),
    composerForm: byId('composer-form'),
    messageInput: byId('message-input'),
    sendButton: byId('send-button'),
    attachTrigger: byId('attach-trigger'),
    fileInput: byId('file-input'),
    composerAttachments: byId('composer-attachments'),
    toastRegion: byId('toast-region'),
    sceneSummary: byId('scene-summary'),
    sceneMeta: byId('scene-meta'),
    materialId: byId('material-id'),
    preserveDimensions: byId('preserve-dimensions'),
    noSupports: byId('no-supports'),
    pinnedSettings: byId('pinned-settings'),
    addSetting: byId('add-setting'),
    requirementsNotes: byId('requirements-notes'),
    saveRequirements: byId('save-requirements'),
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
  };

  const app = {
    state: {
      mode: 'demo',
      busy: false,
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
    },
    eventsById: new Map(),
    pendingAttachments: [],
    pollInFlight: false,
    pollTimer: null,
    requirementsDirty: false,
    lastPollError: '',
    lastServerError: '',
    renderedMessagesSignature: '',
    renderedEventsSignature: '',
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
    dom.busyLabel.hidden = !busy;
    dom.cancelOperation.hidden = !busy;
    dom.typingIndicator.hidden = !busy;
    dom.sendButton.disabled = busy;
    dom.attachTrigger.disabled = busy;
    dom.saveRequirements.disabled = busy;
    dom.addSetting.disabled = busy;
    document.querySelectorAll('[data-tool]').forEach((button) => {
      button.disabled = busy;
    });
    document.querySelectorAll('.variant-card .outline-button').forEach((button) => {
      button.disabled = busy;
    });
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
      avatar.textContent = message && message.role === 'user' ? 'Вы' : 'S';
      const stack = document.createElement('div');
      stack.className = 'message-stack';
      const bubble = document.createElement('div');
      bubble.className = 'message-bubble';
      bubble.textContent = safeString(message && message.content);
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
  }

  function renderEvents() {
    const events = Array.from(app.eventsById.values());
    const openKeys = new Set(Array.from(dom.eventLog.querySelectorAll('details[open]')).map((details) => details.dataset.eventKey));
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

    window.requestAnimationFrame(() => {
      if (!chatBody) return;
      chatBody.scrollTop = wasNearBottom ? chatBody.scrollHeight : oldScrollTop;
    });
  }

  function renderScene() {
    const scene = app.state.scene || {};
    const objects = Array.isArray(scene.objects) ? scene.objects : [];
    dom.sceneSummary.replaceChildren();
    if (!objects.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state compact-empty';
      appendText(empty, '◇', 'empty-icon');
      appendText(empty, 'Сцена ещё не загружена');
      dom.sceneSummary.appendChild(empty);
    } else {
      const list = document.createElement('div');
      list.className = 'object-list';
      objects.forEach((object) => {
        const card = document.createElement('div');
        card.className = 'object-card';
        const info = document.createElement('div');
        info.style.minWidth = '0';
        appendText(info, object && object.name ? object.name : 'Без имени', 'object-name');
        appendText(info, object && object.id ? object.id : '', 'object-id');
        const bounds = object && object.bounds_mm ? object.bounds_mm : {};
        const size = document.createElement('span');
        size.className = 'object-size';
        size.textContent = `${formatNumber(bounds.x, 1)} × ${formatNumber(bounds.y, 1)} × ${formatNumber(bounds.z, 1)} мм`;
        card.append(info, size);
        list.appendChild(card);
      });
      dom.sceneSummary.appendChild(list);
    }
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
      appendText(card, '⇩', 'output-icon');
      const info = document.createElement('div');
      info.className = 'output-info';
      appendText(info, output && output.filename ? output.filename : 'Файл', 'output-name');
      appendText(info, formatBytes(output && output.size_bytes), 'output-size');
      card.appendChild(info);
      if (output && (output.url || output.id)) {
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

  function applyState(next) {
    if (!next || typeof next !== 'object') return;
    app.state = Object.assign({}, app.state, next);
    if (next.settings) app.state.settings = Object.assign({}, app.state.settings, next.settings);
    ingestEvents(next.events);
    if (next.cursor !== undefined && next.cursor !== null) app.state.cursor = next.cursor;
    renderHeader();
    renderBusy();
    renderConversation();
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

  async function pollState() {
    if (app.pollInFlight) return;
    app.pollInFlight = true;
    try {
      const after = encodeURIComponent(safeString(app.state.cursor, '0'));
      const next = await apiRequest(`/state?after=${after}`, { method: 'GET' });
      app.lastPollError = '';
      setConnection('connected', 'ONLINE');
      applyState(next);
    } catch (error) {
      setConnection('error', 'OFFLINE');
      if (error && error.message && error.message !== app.lastPollError) {
        app.lastPollError = error.message;
        showToast(error.message, 'error');
      }
    } finally {
      app.pollInFlight = false;
      app.pollTimer = window.setTimeout(pollState, POLL_INTERVAL_MS);
    }
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
      chip.className = 'pending-file';
      appendText(chip, `▧ ${attachment.name}`);
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '×';
      remove.title = 'Удалить вложение';
      remove.addEventListener('click', () => {
        app.pendingAttachments.splice(index, 1);
        renderPendingAttachments();
      });
      chip.appendChild(remove);
      dom.composerAttachments.appendChild(chip);
    });
  }

  async function uploadFile(file) {
    const extension = fileExtension(file.name);
    if (!ALLOWED_EXTENSIONS.has(extension)) {
      showToast(`Формат .${extension || 'без расширения'} не поддерживается`, 'error');
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      showToast(`Файл «${file.name}» больше 24 МБ`, 'error');
      return;
    }
    try {
      dom.attachTrigger.disabled = true;
      const dataBase64 = await readFileAsBase64(file);
      const response = await apiRequest('/upload', {
        method: 'POST',
        body: JSON.stringify({ filename: file.name, data_base64: dataBase64 })
      });
      if (!response || !response.id) throw new Error('Сервер не вернул идентификатор файла');
      app.pendingAttachments.push({ id: response.id, name: response.name || file.name, kind: response.kind || 'model' });
      renderPendingAttachments();
      showToast(`Файл «${file.name}» добавлен`, 'success');
    } catch (error) {
      showToast(`Не удалось добавить «${file.name}»: ${error.message}`, 'error');
    } finally {
      dom.attachTrigger.disabled = Boolean(app.state.busy);
    }
  }

  async function sendMessage(textOverride) {
    if (app.state.busy) return;
    const text = textOverride === undefined ? dom.messageInput.value.trim() : safeString(textOverride).trim();
    const attachments = app.pendingAttachments.map((item) => item.id);
    if (!text && attachments.length === 0) return;
    try {
      const response = await apiRequest('/message', {
        method: 'POST',
        body: JSON.stringify({ text, attachments })
      });
      dom.messageInput.value = '';
      autoResizeComposer();
      app.pendingAttachments = [];
      renderPendingAttachments();
      if (response && response.accepted) {
        app.state = Object.assign({}, app.state, { busy: true });
        renderBusy();
        showToast('Запрос принят', 'success');
      }
      await pollState();
    } catch (error) {
      showToast(`Не удалось отправить сообщение: ${error.message}`, 'error');
    }
  }

  async function saveRequirements() {
    if (app.state.busy) return;
    const payload = collectRequirements();
    try {
      await apiRequest('/requirements', { method: 'POST', body: JSON.stringify(payload) });
      app.state.requirements = payload;
      app.requirementsDirty = false;
      syncRequirementsFromState();
      showToast('Требования закреплены', 'success');
      await pollState();
    } catch (error) {
      showToast(`Требования не сохранены: ${error.message}`, 'error');
    }
  }

  async function callTool(name, argumentsObject) {
    if (app.state.busy) return;
    const args = argumentsObject || {};
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
        showToast(`Инструмент «${name}» запущен`, 'success');
      }
      await pollState();
    } catch (error) {
      app.state = Object.assign({}, app.state, { busy: false });
      renderBusy();
      showToast(`Инструмент «${name}» не выполнен: ${error.message}`, 'error');
    }
  }

  async function cancelOperation() {
    if (!app.state.busy) return;
    try {
      await apiRequest('/cancel', { method: 'POST', body: JSON.stringify({}) });
      showToast('Запрос на остановку отправлен', 'success');
      await pollState();
    } catch (error) {
      showToast(`Не удалось остановить операцию: ${error.message}`, 'error');
    }
  }

  async function resetChat() {
    if (app.state.busy) {
      showToast('Дождитесь завершения операции или остановите её', 'error');
      return;
    }
    try {
      await apiRequest('/reset', { method: 'POST', body: JSON.stringify({}) });
      app.eventsById.clear();
      app.state = Object.assign({}, app.state, { cursor: 0, messages: [], events: [], last_error: '', busy: false });
      app.requirementsDirty = false;
      app.pendingAttachments = [];
      renderPendingAttachments();
      applyState(app.state);
      showToast('Диалог очищен', 'success');
      await pollState();
    } catch (error) {
      showToast(`Диалог не очищен: ${error.message}`, 'error');
    }
  }

  async function downloadOutput(output) {
    const url = output.url || (output.id ? `${API_ROOT}/download/${encodeURIComponent(output.id)}` : '');
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

  async function saveSettings(event) {
    event.preventDefault();
    const apiKey = dom.apiKey.value;
    const payload = {
      base_url: dom.baseUrl.value.trim() || 'https://api.timeweb.ai/v1',
      model: dom.modelName.value.trim() || 'dashscope/qwen3.8-flash',
      api_key: apiKey,
      clear_key: Boolean(dom.clearKey.checked)
    };
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
      showToast('Настройки подключения сохранены', 'success');
      await pollState();
    } catch (error) {
      dom.apiKey.value = '';
      showToast(`Настройки не сохранены: ${error.message}`, 'error');
    }
  }

  function bindEvents() {
    dom.openSettings.addEventListener('click', openSettings);
    dom.closeSettings.addEventListener('click', closeSettings);
    dom.settingsForm.addEventListener('submit', saveSettings);
    dom.settingsDialog.addEventListener('click', (event) => {
      if (event.target === dom.settingsDialog) closeSettings();
    });
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
      for (const file of files) await uploadFile(file);
    });
    dom.cancelOperation.addEventListener('click', cancelOperation);
    dom.resetChat.addEventListener('click', resetChat);
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
    dom.saveRequirements.addEventListener('click', saveRequirements);
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
