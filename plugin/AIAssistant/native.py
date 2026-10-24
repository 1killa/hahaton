"""Qt controller for the native STE Assistant window.

The controller is intentionally a thin asynchronous view model over the
existing authenticated AssistantServer.  It owns no agent state and never
starts a second server.  Imports are optional so the regular Python test
suite can inspect the module without installing STE's Qt runtime.
"""
import base64
import os
import threading

from .agent.native_client import LoopbackClient, NativeClientError


_MODEL_EXTENSIONS = (".stl", ".obj", ".3mf")
_DOCUMENT_EXTENSIONS = (".docx", ".txt")
_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")
_MODEL_LIMIT = 64 * 1024 * 1024
_OTHER_LIMIT = 24 * 1024 * 1024
_MAX_ATTACHMENTS = 8


def _scene_action_text(name, result):
    if not isinstance(result, dict):
        return ""
    if result.get('ok') is False:
        return "Ошибка: " + str(result.get('error') or 'Действие не выполнено.')
    if name == 'get_scene':
        return "Сцена обновлена. Моделей: %s." % len(result.get('objects') or [])
    if name == 'check_5d':
        warnings = result.get('warnings') or []
        return ("Предварительная проверка 5D: " + "; ".join(str(item) for item in warnings)
                if warnings else "Предварительная проверка 5D завершена, предупреждений нет.")
    if name == 'start_slice':
        return "Нарезка запущена. Статус и прогресс отображаются выше."
    if name in ('save_project', 'export_gcode'):
        filename = (result.get('output') or {}).get('filename')
        return ("Файл %s сохранён и доступен ниже." % filename if filename
                else "Файл сохранён и доступен в результатах ниже.")
    return ""


def _read_bounded(path):
    """Read a selected attachment only after checking its size on disk."""
    if not isinstance(path, str):
        raise ValueError("Некорректный путь вложения.")
    extension = os.path.splitext(path)[1].lower()
    if extension in _MODEL_EXTENSIONS:
        limit = _MODEL_LIMIT
    elif extension in _DOCUMENT_EXTENSIONS + _IMAGE_EXTENSIONS:
        limit = _OTHER_LIMIT
    else:
        raise ValueError("Разрешены STL, OBJ, 3MF, DOCX, TXT, PNG и JPEG.")
    size = os.stat(path).st_size
    if size > limit:
        raise ValueError("Файл %s превышает лимит %s MiB." %
                         (os.path.basename(path), limit // (1024 * 1024)))
    chunks = []
    total = 0
    with open(path, "rb") as stream:
        while True:
            chunk = stream.read(min(1024 * 1024, limit - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValueError("Файл %s превышает лимит." % os.path.basename(path))
            chunks.append(chunk)
    return b"".join(chunks)


try:
    from PyQt5.QtCore import (QObject, QRunnable, QThreadPool, QTimer, pyqtProperty,
                               pyqtSignal, pyqtSlot)
    from PyQt5.QtWidgets import QFileDialog
    _HAS_QT = True
except ImportError:
    _HAS_QT = False


if not _HAS_QT:
    class NativeAssistantController(object):
        """Import-safe placeholder; STE supplies PyQt5 at runtime."""

        def __init__(self, server, parent=None):
            self.server = server
            self.parent = parent
            self.client = LoopbackClient(server.origin, server.token)

        def bind_view(self, view):
            self.view = view

        def show(self):
            if getattr(self, "view", None) is not None and hasattr(self.view, "show"):
                self.view.show()

        def dispose(self):
            self.view = None

else:
    class _WorkerSignals(QObject):
        result = pyqtSignal(int, int, object, str)
        error = pyqtSignal(int, int, object, str)


    class _Worker(QRunnable):
        def __init__(self, signals, operation_id, generation, function, kind):
            QRunnable.__init__(self)
            self.signals = signals
            self.operation_id = operation_id
            self.generation = generation
            self.function = function
            self.kind = kind
            self.setAutoDelete(True)

        def run(self):
            try:
                self.signals.result.emit(self.operation_id, self.generation,
                                         self.function(), self.kind)
            except Exception as error:
                self.signals.error.emit(self.operation_id, self.generation, error, self.kind)


    class NativeAssistantController(QObject):
        stateChanged = pyqtSignal()
        messagesChanged = pyqtSignal()
        eventsChanged = pyqtSignal()
        conversationsChanged = pyqtSignal()
        attachmentsChanged = pyqtSignal()
        outputsChanged = pyqtSignal()
        sceneChanged = pyqtSignal()
        sliceChanged = pyqtSignal()
        uploadsChanged = pyqtSignal()
        uploadErrorChanged = pyqtSignal()
        requirementsChanged = pyqtSignal()
        settingsChanged = pyqtSignal()
        draftChanged = pyqtSignal()
        busyChanged = pyqtSignal()
        errorChanged = pyqtSignal()
        operationChanged = pyqtSignal()
        settingsSaved = pyqtSignal()
        requirementsSaved = pyqtSignal()
        conversationChanged = pyqtSignal()
        actionMessageChanged = pyqtSignal()

        def __init__(self, server, parent=None):
            QObject.__init__(self, parent)
            self.server = server
            self.client = LoopbackClient(server.origin, server.token)
            self.view = None
            self._disposed = False
            self._visible = False
            self._generation = 1
            self._next_operation = 0
            self._poll_inflight = False
            self._poll_operation = 0
            self._poll_generation = 1
            self._command_pending = False
            self._command_operation = 0
            self._ack_waiting_poll = False
            self._cancel_requested = False
            self._conversation_refetch = False
            self._poll_fence = 0
            self._cursor = 0
            self._state = {}
            self._messages = []
            self._events = []
            self._conversations = []
            self._attachments = []
            self._uploads_pending = 0
            self._upload_failures = {}
            self._outputs = []
            self._scene = {}
            self._slice_info = {}
            self._requirements = {"settings": [], "material_id": "",
                                  "preserve_dimensions": False, "notes": ""}
            self._settings = {"base_url": "", "model": "", "has_key": False}
            self._settings_dirty = False
            self._requirements_dirty = False
            self._settings_revision = 0
            self._requirements_revision = 0
            self._submitted_settings_revision = 0
            self._submitted_requirements_revision = 0
            self._draft = ""
            self._draft_revision = 0
            self._attachments_revision = 0
            self._submitted_draft_revision = 0
            self._submitted_attachments_revision = 0
            self._submitted_attachment_ids = []
            self._error = ""
            self._poll_error = ""
            self._upload_error = ""
            self._conversation_id = ""
            self._status = "Подключение к STE Assistant..."
            self._action_message = ""
            self._timer = QTimer(self)
            self._timer.setInterval(700)
            self._timer.timeout.connect(self._poll)
            self._signals = _WorkerSignals()
            self._signals.result.connect(self._worker_result)
            self._signals.error.connect(self._worker_error)
            self._operation_cancels = {}

        def bind_view(self, view):
            self.view = view
            try:
                view.visibleChanged.connect(self._view_visibility_changed)
            except (AttributeError, RuntimeError):
                pass

        @pyqtSlot(bool)
        def windowVisibilityChanged(self, visible):
            self._view_visibility_changed(bool(visible))

        def _view_visibility_changed(self, visible):
            if self._disposed:
                return
            self._visible = bool(visible)
            if self._visible:
                if not self._timer.isActive():
                    self._timer.start()
                self._poll()
            else:
                self._poll_generation += 1
                self._poll_inflight = False
                self._timer.stop()

        @pyqtSlot()
        def show(self):
            if self._disposed:
                return
            self._visible = True
            if self.view is not None:
                try:
                    self.view.show()
                    self.view.raise_()
                    self.view.requestActivate()
                except (AttributeError, RuntimeError):
                    pass
            if not self._timer.isActive():
                self._timer.start()
            self._poll()

        @pyqtSlot()
        def dispose(self):
            if self._disposed:
                return
            self._disposed = True
            self._visible = False
            self._generation += 1
            self._poll_generation += 1
            self._poll_inflight = False
            for cancel_event in list(self._operation_cancels.values()):
                cancel_event.set()
            self._operation_cancels.clear()
            self._timer.stop()
            view = self.view
            self.view = None
            if view is not None:
                try:
                    view.hide()
                    view.deleteLater()
                except (AttributeError, RuntimeError):
                    pass

        def _next_op(self):
            self._next_operation += 1
            return self._next_operation

        def _start(self, function, kind, command=False, cancel_event=None):
            if self._disposed:
                return 0
            operation = self._next_op()
            self._operation_cancels[operation] = cancel_event or threading.Event()
            generation = self._poll_generation if kind == "poll" else self._generation
            if kind == "poll":
                self._poll_inflight = True
                self._poll_operation = operation
            if command:
                self._command_pending = True
                self._command_operation = operation
                # Any idle poll already on the wire predates this command;
                # its response must not make the composer look idle again.
                self._poll_fence = operation
                self.operationChanged.emit()
            QThreadPool.globalInstance().start(_Worker(self._signals, operation, generation,
                                                        function, kind))
            return operation

        def _poll(self):
            if (self._disposed or not self._visible or self._poll_inflight or
                    (self._command_pending and not self._ack_waiting_poll)):
                return
            cursor = self._cursor
            self._start(lambda: self.client.state(cursor), "poll")

        def _worker_result(self, operation, generation, value, kind):
            self._operation_cancels.pop(operation, None)
            if kind == "poll":
                if operation != self._poll_operation:
                    return
                self._poll_inflight = False
                if (self._disposed or generation != self._poll_generation or not self._visible or
                        (self._poll_fence and operation < self._poll_fence)):
                    if self._visible and not self._disposed and not self._command_pending:
                        self._poll()
                    return
                self._apply_state(value)
                if self._ack_waiting_poll:
                    self._ack_waiting_poll = False
                    self._command_pending = False
                    self._command_operation = 0
                    self._cancel_requested = False
                    self.operationChanged.emit()
                    self.busyChanged.emit()
                if getattr(self, "_conversation_refetch", False):
                    self._conversation_refetch = False
                    if self._visible:
                        self._poll()
                return
            if self._disposed or generation != self._generation:
                return
            matching_command = (operation == self._command_operation and self._command_pending and
                                not self._ack_waiting_poll)
            receipt_kind = None
            if matching_command:
                if kind in ("message", "settings", "requirements", "tool", "continue", "conversation"):
                    self._ack_waiting_poll = True
                else:
                    self._command_pending = False
                self._command_operation = 0
                self.operationChanged.emit()
            if kind == "message" and matching_command:
                if self._draft_revision == self._submitted_draft_revision:
                    self._draft = ""
                    self._draft_revision += 1
                    self.draftChanged.emit()
                if (self._attachments_revision == self._submitted_attachments_revision and
                        [item.get("id") for item in self._attachments if isinstance(item, dict)] ==
                        self._submitted_attachment_ids):
                    self._attachments = []
                    self._attachments_revision += 1
                    self.attachmentsChanged.emit()
                elif self._submitted_attachment_ids:
                    # New files may have been attached while the POST was in
                    # flight.  Drop only IDs acknowledged by that POST and
                    # keep the newer attachment chips editable.
                    submitted = set(self._submitted_attachment_ids)
                    retained = [item for item in self._attachments
                                if not isinstance(item, dict) or item.get("id") not in submitted]
                    if len(retained) != len(self._attachments):
                        self._attachments = retained
                        self._attachments_revision += 1
                        self.attachmentsChanged.emit()
            elif kind == "upload" and isinstance(value, dict):
                self._uploads_pending = max(0, self._uploads_pending - 1)
                self._attachments.append(value)
                self._attachments_revision += 1
                self._upload_failures.pop(operation, None)
                self._refresh_upload_error()
                self.attachmentsChanged.emit()
                self.uploadsChanged.emit()
                self.uploadErrorChanged.emit()
            elif kind == "settings":
                if (matching_command and isinstance(value, dict) and
                        self._settings_revision == self._submitted_settings_revision and
                        set(("base_url", "model", "has_key")).issubset(set(value))):
                    self._settings_dirty = False
                    self._transient_api_key = ""
                    self._clear_api_key = False
                    self._settings.update(value)
                    self.settingsChanged.emit()
                    receipt_kind = "settings"
            elif kind == "requirements":
                if (matching_command and isinstance(value, dict) and
                        self._requirements_revision == self._submitted_requirements_revision and
                        set(("settings", "material_id", "preserve_dimensions", "notes")).issubset(set(value))):
                    self._requirements_dirty = False
                    self._requirements = dict(value)
                    self.requirementsChanged.emit()
                    receipt_kind = "requirements"
            elif kind == "conversation":
                if matching_command and isinstance(value, dict):
                    self._reset_conversation(value.get("conversation_id"))
            if matching_command and self._cancel_requested:
                # The accepted command may be a message, tool, or continue
                # operation.  In every case, serialize the cancel mutation
                # behind its acknowledgement rather than racing the POST.
                self._start(lambda: self.client.cancel(), "cancel", command=False)
            if receipt_kind == "settings":
                self.settingsSaved.emit()
            elif receipt_kind == "requirements":
                self.requirementsSaved.emit()
            self._status = "Готов" if not self._command_pending else self._status
            self.stateChanged.emit()
            if self._visible:
                self._poll()

        def _worker_error(self, operation, generation, error, kind):
            self._operation_cancels.pop(operation, None)
            if kind == "poll":
                if operation != self._poll_operation:
                    return
                self._poll_inflight = False
                if (self._disposed or generation != self._poll_generation or not self._visible or
                        (self._poll_fence and operation < self._poll_fence)):
                    if self._visible and not self._disposed and not self._command_pending:
                        self._poll()
                    return
                self._poll_error = str(error) if str(error) else "Состояние помощника недоступно."
                self.errorChanged.emit()
                self._status = "Нет связи"
                self.stateChanged.emit()
                return
            elif self._disposed or generation != self._generation:
                return
            if operation == self._command_operation and self._command_pending:
                self._command_pending = False
                self._command_operation = 0
                self._ack_waiting_poll = False
                self._cancel_requested = False
                self.operationChanged.emit()
            self._error = str(error) if str(error) else "Ошибка native операции."
            if kind == "upload":
                self._uploads_pending = max(0, self._uploads_pending - 1)
                self._upload_failures[operation] = str(error) if str(error) else "Не удалось загрузить вложение."
                self._refresh_upload_error()
                self.uploadsChanged.emit()
                self.uploadErrorChanged.emit()
            self._status = "Ошибка"
            if kind == 'tool':
                self._action_message = "Ошибка: " + self._error
                self.actionMessageChanged.emit()
            self.errorChanged.emit()
            self.stateChanged.emit()

        def _clear_error(self):
            if self._error:
                self._error = ""
                self.errorChanged.emit()

        def _refresh_upload_error(self):
            values = list(self._upload_failures.values())
            self._upload_error = values[-1] if values else ""

        @pyqtSlot()
        def dismissError(self):
            self._clear_error()

        @pyqtSlot()
        def clearUploadError(self):
            self._upload_failures.clear()
            if self._upload_error:
                self._upload_error = ""
                self.uploadErrorChanged.emit()

        def _reset_conversation(self, conversation_id=None):
            if conversation_id is not None:
                self._conversation_id = conversation_id
            self._cursor = 0
            self._events = []
            self._messages = []
            self._requirements = {"settings": [], "material_id": "",
                                  "preserve_dimensions": False, "notes": ""}
            self._requirements_dirty = False
            self._requirements_revision += 1
            self._draft = ""
            self._draft_revision += 1
            self._attachments = []
            self._attachments_revision += 1
            self.messagesChanged.emit()
            self.eventsChanged.emit()
            self.requirementsChanged.emit()
            self.draftChanged.emit()
            self.attachmentsChanged.emit()
            self.conversationChanged.emit()

        def _apply_state(self, value):
            if not isinstance(value, dict) or value.get("api_version") != 3:
                return
            self._state = dict(value)
            incoming_id = value.get("conversation_id")
            conversation_changed = (incoming_id is not None and self._conversation_id and
                                     incoming_id != self._conversation_id)
            if conversation_changed:
                self._reset_conversation(incoming_id)
                self._conversation_refetch = True
            elif incoming_id is not None and not self._conversation_id:
                self._conversation_id = incoming_id
                self.conversationChanged.emit()
            if not conversation_changed:
                self._cursor = max(self._cursor, int(value.get("cursor", self._cursor) or 0))
                self._messages = list(value.get("messages") or [])
            new_events = [] if conversation_changed else list(value.get("events") or [])
            for event in new_events:
                if event.get('type') == 'tool_result':
                    message = _scene_action_text(event.get('name'), event.get('result'))
                    if message:
                        self._action_message = message
                        self.actionMessageChanged.emit()
                elif event.get('type') == 'error':
                    self._action_message = "Ошибка: " + str(event.get('message') or 'Действие не выполнено.')
                    self.actionMessageChanged.emit()
            if new_events:
                known = set(item.get("id") for item in self._events if isinstance(item, dict))
                self._events.extend(item for item in new_events
                                   if not isinstance(item, dict) or item.get("id") not in known)
                self._events = self._events[-1000:]
            self._conversations = list(value.get("conversations") or self._conversations)
            self._outputs = list(value.get("outputs") or [])
            self._scene = value.get("scene") or {}
            self._slice_info = value.get("slice") or {}
            self._conversation_id = value.get("conversation_id") or self._conversation_id
            if not self._settings_dirty and isinstance(value.get("settings"), dict):
                self._settings = dict(value["settings"])
                self.settingsChanged.emit()
            if not conversation_changed and not self._requirements_dirty and isinstance(value.get("requirements"), dict):
                self._requirements = dict(value["requirements"])
                self.requirementsChanged.emit()
            self._status = value.get("status") or ("Занят" if value.get("busy") else "Готов")
            self.messagesChanged.emit()
            self.eventsChanged.emit()
            self.conversationsChanged.emit()
            self.outputsChanged.emit()
            self.sceneChanged.emit()
            self.sliceChanged.emit()
            self.busyChanged.emit()
            self.stateChanged.emit()
            if self._poll_error:
                self._poll_error = ""
                self.errorChanged.emit()

        @pyqtSlot(str)
        def setDraft(self, text):
            self._draft = text if isinstance(text, str) else ""
            self._draft_revision += 1
            self.draftChanged.emit()

        @pyqtSlot()
        def sendMessage(self):
            attachments = [item.get("id") for item in self._attachments if isinstance(item, dict)]
            if (self._command_pending or self._uploads_pending or self.busy or
                    (not self._draft.strip() and not attachments)):
                return
            text = self._draft
            self._submitted_draft_revision = self._draft_revision
            self._submitted_attachments_revision = self._attachments_revision
            self._submitted_attachment_ids = list(attachments)
            self._start(lambda: self.client.message(text, attachments), "message", command=True)
            # Clear only after the POST acknowledgement arrives.  A failed or
            # disconnected operation therefore leaves both draft and files editable.
            self._pending_draft = text
            self._pending_attachment_ids = attachments

        @pyqtSlot()
        def cancel(self):
            if self._command_pending or self._state.get("busy", False):
                # A mutation POST may still be on the wire.  Record the
                # user's intent and replay it from the acknowledgement path;
                # racing a second request here can cancel the POST itself and
                # leave us without an acknowledgement to fence the poll.
                self._cancel_requested = True
                if not self._command_pending or self._ack_waiting_poll:
                    self._start(lambda: self.client.cancel(), "cancel", command=False)

        @pyqtSlot()
        def continuePending(self):
            if self._command_pending or not self.canContinue:
                return
            self._start(lambda: self.client.continue_pending(), "continue", command=True)

        @pyqtSlot(str)
        def setApiKey(self, value):
            self._transient_api_key = value if isinstance(value, str) else ""
            self._settings_revision += 1

        @pyqtSlot(str, str)
        def editSettings(self, base_url, model):
            self._settings_dirty = True
            self._settings_revision += 1
            self._settings["base_url"] = base_url or ""
            self._settings["model"] = model or ""
            self.settingsChanged.emit()

        @pyqtSlot()
        def saveSettings(self):
            if self._command_pending:
                return
            base_url = self._settings.get("base_url", "")
            model = self._settings.get("model", "")
            key = getattr(self, "_transient_api_key", "")
            clear = bool(getattr(self, "_clear_api_key", False))
            self._submitted_settings_revision = self._settings_revision
            self._start(lambda: self.client.settings(base_url, model, key, clear),
                        "settings", command=True)

        @pyqtSlot(bool)
        def clearApiKey(self, clear):
            self._clear_api_key = bool(clear)
            self._settings_revision += 1

        @pyqtSlot("QVariant", str, bool, str, result=bool)
        def editRequirements(self, settings_json, material_id, preserve_dimensions, notes):
            import json
            try:
                settings = (list(settings_json) if isinstance(settings_json, (list, tuple))
                            else json.loads(settings_json or "[]"))
                if not isinstance(settings, list):
                    raise ValueError("settings должен быть массивом")
            except (ValueError, TypeError):
                self._error = "Требования должны содержать корректный JSON-массив."
                self.errorChanged.emit()
                return False
            self._requirements_dirty = True
            self._requirements_revision += 1
            self._requirements = {"settings": settings, "material_id": material_id or "",
                                  "preserve_dimensions": bool(preserve_dimensions),
                                  "notes": notes or ""}
            self.requirementsChanged.emit()
            return True

        @pyqtSlot()
        def saveRequirements(self):
            if self._command_pending:
                return
            req = dict(self._requirements)
            self._submitted_requirements_revision = self._requirements_revision
            self._start(lambda: self.client.requirements(req.get("settings", []),
                         req.get("material_id", ""), req.get("preserve_dimensions", False),
                         req.get("notes", "")), "requirements", command=True)

        @pyqtSlot()
        def chooseAttachments(self):
            if self.view is None:
                return
            paths, _ = QFileDialog.getOpenFileNames(
                None, "Прикрепить к сообщению", "",
                "Все поддерживаемые файлы (*.stl *.obj *.3mf *.docx *.txt *.png *.jpg *.jpeg);;"
                "Модели (*.stl *.obj *.3mf);;Документы (*.docx *.txt);;Изображения (*.png *.jpg *.jpeg)")
            if paths:
                self.uploadPaths(paths)

        @pyqtSlot("QVariant")
        def uploadPaths(self, paths):
            if not isinstance(paths, (list, tuple)):
                return
            if len(self._attachments) + self._uploads_pending + len(paths) > _MAX_ATTACHMENTS:
                self._error = "Можно прикрепить не более 8 файлов."
                self.errorChanged.emit()
                return
            for path in list(paths):
                self._uploads_pending += 1
                self._start(lambda path=path: self._upload_one(path), "upload", command=False)
            self.uploadsChanged.emit()

        def _upload_one(self, path):
            data = _read_bounded(path)
            return self.client.upload(os.path.basename(path), data)

        @pyqtSlot(str, str)
        def downloadOutput(self, output_id, target_path):
            cancel_event = threading.Event()
            self._start(lambda: self.client.download(output_id, target_path, cancel_event),
                        "download", command=False, cancel_event=cancel_event)

        @pyqtSlot(str)
        def saveOutput(self, output_id):
            if self.view is None:
                return
            filename = "result.gcode"
            for item in self._outputs:
                if isinstance(item, dict) and item.get("id") == output_id:
                    filename = item.get("filename") or filename
                    break
            target, _ = QFileDialog.getSaveFileName(None, "Сохранить результат", filename,
                                                    "G-code/3MF (*.gcode *.3mf); Все файлы (*)")
            if target:
                self.downloadOutput(output_id, target)

        @pyqtSlot(str)
        def chooseDownload(self, output_id):
            self.saveOutput(output_id)

        @pyqtSlot(str)
        def removeAttachment(self, attachment_id):
            before = len(self._attachments)
            self._attachments = [item for item in self._attachments
                                 if not isinstance(item, dict) or item.get("id") != attachment_id]
            if len(self._attachments) != before:
                self._attachments_revision += 1
                self.attachmentsChanged.emit()

        @pyqtSlot(str, "QVariantMap")
        def manualTool(self, name, arguments):
            if self.busy:
                return
            # Qt converts the QML object into a Python dict on the GUI thread.
            # Do not pass an engine-owned QJSValue to the HTTP worker.
            values = dict(arguments or {})
            self._clear_error()
            self._action_message = "Выполняется…"
            self.actionMessageChanged.emit()
            self._start(lambda: self.client.tool(name, values), "tool", command=True)

        @pyqtSlot(str)
        def selectConversation(self, conversation_id):
            if self._command_pending:
                return
            self._start(lambda: self.client.select_conversation(conversation_id),
                        "conversation", command=True)

        @pyqtSlot()
        def newConversation(self):
            if self._command_pending:
                return
            self._start(lambda: self.client.conversations(), "conversation", command=True)

        @pyqtProperty(list, notify=messagesChanged)
        def messages(self):
            return self._messages

        @pyqtProperty(list, notify=eventsChanged)
        def events(self):
            return self._events

        @pyqtProperty(list, notify=conversationsChanged)
        def conversations(self):
            return self._conversations

        @pyqtProperty(list, notify=attachmentsChanged)
        def attachments(self):
            return self._attachments

        @pyqtProperty(list, notify=outputsChanged)
        def outputs(self):
            return self._outputs

        @pyqtProperty("QVariant", notify=sceneChanged)
        def scene(self):
            return self._scene

        @pyqtProperty("QVariant", notify=sliceChanged)
        def sliceInfo(self):
            return self._slice_info

        @pyqtProperty(str, notify=draftChanged)
        def draft(self):
            return self._draft

        @pyqtProperty(str, notify=errorChanged)
        def error(self):
            return self._error or self._poll_error

        @pyqtProperty(str, notify=uploadErrorChanged)
        def uploadError(self):
            return self._upload_error

        @pyqtProperty(str, notify=stateChanged)
        def status(self):
            return self._status

        @pyqtProperty(bool, notify=busyChanged)
        def busy(self):
            return bool(self._state.get("busy", False)) or self._command_pending

        @pyqtProperty(bool, notify=stateChanged)
        def canContinue(self):
            return bool(self._state.get("can_continue", False)) and not self._command_pending

        @pyqtProperty(bool, notify=operationChanged)
        def commandPending(self):
            return self._command_pending

        @pyqtProperty(int, notify=uploadsChanged)
        def uploadsPending(self):
            return self._uploads_pending

        @pyqtProperty(str, notify=settingsChanged)
        def settingsBaseUrl(self):
            return self._settings.get("base_url", "")

        @pyqtProperty(str, notify=settingsChanged)
        def settingsModel(self):
            return self._settings.get("model", "")

        @pyqtProperty(bool, notify=settingsChanged)
        def hasKey(self):
            return bool(self._settings.get("has_key", False))

        @pyqtProperty(str, notify=conversationChanged)
        def conversationId(self):
            return self._conversation_id

        @pyqtProperty(str, notify=actionMessageChanged)
        def actionMessage(self):
            return self._action_message

        @pyqtProperty("QVariant", notify=requirementsChanged)
        def requirements(self):
            return self._requirements
