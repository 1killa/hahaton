# STE Assistant Implementation Plan — v2

> **For agentic workers:** Follow the approved plan and file ownership in API_CONTRACT.md. Use test-driven-development for material behavior; verify integration before completion.

**Goal:** Установочный черновик помощника STE Slicer с Chat Completions, MCP и браузерным чатом.

**Architecture:** Extension запускает loopback HTTP server; агент выполняет инструменты через очередь главного потока Qt. Нативный адаптер изолирован от standalone demo. Интерфейс не требует сборки.

**Tech Stack:** Python 3.5 stdlib, PyQt5 из STE, HTML/CSS/JavaScript.

**Spec:** API_CONTRACT.md; исходное видение — PROJECT_PLAN.md (копия ПЛАН_ПРОЕКТА.md из родительской рабочей папки).

## Global Constraints

- `plugin.json`: `api: 5`; `printing_mode`: только classic, cylindrical, cylindrical_full.
- Ключ только в STE preferences (в demo — память); public state никогда не возвращает ключ.
- Любое обращение к STE идёт через `app.callLater`; сеть и ожидание слайсинга вне Qt.
- Файлы сдачи только от нативных writers, никаких фиктивных G-code/3MF/метрик.
- Сервер 127.0.0.1:случайный порт; токен исключительно X-AIAssistant-Token, Host/Origin проверяются.

## Review Focus

- Отмена/таймаут до исполнения Qt callback не должны приводить к поздней мутации.
- Любая мутация и восстановление baseline инвалидируют предыдущий слайсинг.
- Жёсткие требования проверяются также для прямых MCP/UI вызовов и выбора профиля.
- Ошибки провайдера, неизвестные метрики и недоступные API видны как ошибки/недоступность.
- Большие/некорректные вложения и пути не допускают обхода локального каталога.

## Tasks and ownership

- [x] 1. Root: изучить задание и установленный API, записать точные сигнатуры и контракты. Независимое согласование v2: PLAN_APPROVED.
- [x] 2. Executor slicer_adapter: agent/slicer.py и tests/test_slicer.py. Подтверждённые API, snapshot/restore, native export, честный demo.
- [x] 3. Executor web_ui: web/index.html,style.css,app.js. Чат, вложения, настройки, ограничения, события и вывод файлов.
- [x] 4. Root: config/core/rpc/tools/server, Qt bridge и Extension. Тесты локального провайдера, MCP, ограничений и HTTP.
- [x] 5. Root: интеграция, Python 3.5 compile/import, browser smoke, проверка упаковки, README и журнал верификации. Это завершение черновика; настоящий сквозной прогон указан как следующий этап.

Advisor findings: A1 incorporated — discovery до адаптера; A2 incorporated — header-only auth; A3 incorporated — новые slice generations, nonzero native files, только фактические PrintInformation.

В текущем поручении публикация на GitHub не требуется. Работа выполняется в отдельном клоне без изменения исходных моделей и документов.
