"""Exercise native QML and shared menus with isolated data, without model calls."""
import os
import sys
import tempfile
import threading
import time
from unittest.mock import patch

_INSTALL = os.path.dirname(sys.executable)
os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_INSTALL, "platforms")
os.environ["QT_QUICK_BACKEND"] = "software"

from PyQt5.QtCore import QObject, QCoreApplication, QUrl, Qt, pyqtSignal, QMetaObject, QEvent, QThreadPool, qInstallMessageHandler
from PyQt5.QtWidgets import QApplication
from PyQt5.QtQml import QQmlComponent, QQmlContext, QQmlEngine
from PyQt5.QtTest import QTest
from steslicer.SteSlicerApplication import SteSlicerApplication
from UM.Resources import Resources
from plugin.AIAssistant.AIAssistant import AIAssistant
from plugin.AIAssistant.agent import core
from plugin.AIAssistant.agent.native_client import LoopbackClient
from plugin.AIAssistant.agent.tools import tool_schema

QCoreApplication.setLibraryPaths([_INSTALL])
application = QApplication.instance() or QApplication([])
application.setQuitOnLastWindowClosed(False)
engine = QQmlEngine()
engine.addImportPath(os.path.join(_INSTALL, "qml"))
qml_errors = []
def qt_message(kind, context, message):
    if "TypeError" in message or "ReferenceError" in message or "Cannot assign" in message:
        qml_errors.append(message)
qInstallMessageHandler(qt_message)

class Scheduler(QObject):
    invoke = pyqtSignal(object)
    def __init__(self):
        QObject.__init__(self)
        self.invoke.connect(lambda function: function(), Qt.QueuedConnection)

class Preferences:
    def __init__(self):
        self.data = {}
    def addPreference(self, key, value):
        self.data.setdefault(key, value)
    def getValue(self, key):
        return self.data.get(key, "")
    def setValue(self, key, value):
        self.data[key] = value

class App:
    def __init__(self):
        self.preferences = Preferences()
        self.scheduler = Scheduler()
        self.views = []
        self.contexts = []
    def getPreferences(self):
        return self.preferences
    def callLater(self, function):
        self.scheduler.invoke.emit(function)
    def createQmlComponent(self, path, values):
        context = QQmlContext(engine.rootContext())
        for key, value in values.items():
            context.setContextProperty(key, value)
        component = QQmlComponent(engine, QUrl.fromLocalFile(path))
        if component.errors():
            raise RuntimeError(" | ".join(error.toString() for error in component.errors()))
        view = component.create(context)
        self.contexts.append(context)
        self.views.append(view)
        return view

class Adapter:
    def __init__(self, app, output_dir):
        self.output_dir = output_dir
    def list_tools(self):
        result = [tool_schema(name, 'Isolated manual operation', {})
                  for name in ('get_scene', 'check_5d', 'start_slice')]
        result.extend(tool_schema(name, 'Isolated output operation',
                                  {'filename': {'type': 'string'}}, ['filename'])
                      for name in ('save_project', 'export_gcode'))
        return result
    def call(self, name, arguments):
        if name == "get_scene":
            return {"objects": [], "mode": "classic", "material_id": ""}
        if name == "check_5d":
            return {"facts": {"mode": "classic"}, "warnings": ["Изолированная проверка основания"],
                    "assumptions": [], "verified_geometry": False}
        if name == "start_slice":
            return {"ok": True, "state": "running", "plugin_generation_id": "isolated-test"}
        if name in ('save_project', 'export_gcode'):
            target = os.path.join(self.output_dir, arguments['filename'])
            with open(target, 'wb') as stream:
                stream.write(b'isolated manual-button fixture; not a print result\n')
            return {"ok": True, "path": target}
        return {"state": "not_started", "progress": 0, "result": {"available": False}}

def wait_for(condition, label, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        application.processEvents()
        if condition():
            return
        QTest.qWait(15)
    raise AssertionError("Timed out: " + label + ("; QML: " + " | ".join(qml_errors) if qml_errors else ""))

def find_control(view, name):
    item = view.findChild(QObject, name)
    if item is not None:
        return item
    # ListView delegates belong to the visual tree rather than the window's
    # QObject ownership tree on Qt 5.10.
    pending = [view.contentItem()]
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == name:
            return candidate
        pending.extend(candidate.childItems())
    return None

def control(view, name):
    item = find_control(view, name)
    assert item is not None, "Missing native control: " + name
    return item

def click(view, name):
    button = control(view, name)
    assert button.property("enabled"), "Disabled control: " + name
    assert QMetaObject.invokeMethod(button, "clicked", Qt.DirectConnection) is None

def capture(view, name):
    QTest.qWait(80)
    image = view.grabWindow()
    assert not image.isNull(), "Native screenshot is empty"
    folder = os.path.join(os.getcwd(), "docs", "screenshots")
    os.makedirs(folder, exist_ok=True)
    assert image.save(os.path.join(folder, name)), "Cannot save screenshot"

with tempfile.TemporaryDirectory(prefix="native-smoke-", dir=os.path.join(os.getcwd(), ".runtime")) as storage:
    app = App()
    with patch.object(Resources, "_Resources__data_storage_path", storage), \
            patch("plugin.AIAssistant.AIAssistant.SlicerAdapter", Adapter), \
            patch("plugin.AIAssistant.AIAssistant.QDesktopServices") as browser:
        extension = AIAssistant(app)
        try:
            extension.showNativeChat()
            assert extension._native_window is not None, "Native menu failed"
            view = extension._native_window
            controller = extension._native_controller
            server = extension._server
            server.config.update({"base_url": "http://127.0.0.1:9/v1", "api_key": "test-key-only"})
            server.session._messages.extend([
                {"role": "user", "content": "Изолированная проверка окна чата STE Assistant."},
                {"role": "assistant", "content": "Это тест отображения интерфейса. Реальная модель не вызывается; печать и нарезка не выполнялись."}
            ])
            server.session.emit("info", message="Тестовое событие журнала. Оно отделено от переписки.")
            wait_for(lambda: len(controller.messages) == 2, "initial state")
            assert view.modality() == Qt.NonModal
            extension.showNativeChat()
            extension.showChat()
            assert extension._server is server and extension._native_window is view
            assert len(app.views) == 1 and browser.openUrl.call_count == 1
            print("Shared native/browser menus and nonmodal QML: PASS", flush=True)
            capture(view, "native-chat-0.4.png")

            view.setProperty("page", "settings")
            control(view, "providerUrl").setProperty("text", "http://127.0.0.1:9/v1")
            control(view, "providerModel").setProperty("text", "isolated-test-model")
            control(view, "providerKey").setProperty("text", "test-key-replaced")
            click(view, "saveSettingsButton")
            wait_for(lambda: not controller.commandPending and server.config.public()["model"] == "isolated-test-model", "settings saved")
            assert server.config.private()["api_key"] == "test-key-replaced"
            assert control(view, "providerKey").property("text") == ""
            capture(view, "native-settings-0.4.png")
            print("Settings button sends and clears write-only key: PASS", flush=True)

            view.setProperty("page", "requirements")
            control(view, "requirementMaterial").setProperty("text", "")
            control(view, "requirementSettings").setProperty("text", "[]")
            control(view, "requirementNotes").setProperty("text", "Тестовое пожелание")
            click(view, "saveRequirementsButton")
            wait_for(lambda: not controller.commandPending and server.tools.requirements()["notes"] == "Тестовое пожелание", "requirements saved")
            print("QML requirements save reaches shared backend: PASS", flush=True)

            view.setProperty("page", "chat")
            composer = control(view, "chatComposer")
            composer.forceActiveFocus()
            composer.setProperty("text", "Черновик")
            QTest.keyClick(view, Qt.Key_Return, Qt.ShiftModifier)
            assert "\n" in composer.property("text"), "Shift+Enter must insert a newline"
            controller.setDraft("Черновик после скрытия")
            view.hide()
            controller.show()
            assert controller.draft == "Черновик после скрытия"
            wait_for(lambda: not controller.commandPending, "idle before send")
            composer.forceActiveFocus()
            calls = []
            def offline_completion(config, messages, schemas, cancel=None):
                calls.append(True)
                return {"content": "Тестовый ответ без сетевого обращения к LLM."}
            with patch.object(core, "completion", offline_completion):
                QTest.keyClick(view, Qt.Key_Return)
                wait_for(lambda: len(calls) == 1 and any(item.get("content", "").startswith("Тестовый ответ") for item in controller.messages), "Enter submits once")
            assert view.isVisible(), "Enter must not close the native window"
            wait_for(lambda: not controller.busy and not server.session.state()["busy"],
                     "isolated reply completed")
            print("Enter, Shift+Enter and hide/reopen draft: PASS", flush=True)

            document = os.path.join(storage, "requirements.txt")
            with open(document, "wb") as stream:
                stream.write("Тестовое ТЗ: сохранить размеры детали.".encode("utf-8"))
            with patch("plugin.AIAssistant.native.QFileDialog.getOpenFileNames", return_value=([document], "")):
                click(view, "attachButton")
            wait_for(lambda: controller.uploadsPending == 0 and len(controller.attachments) == 1,
                     "native document upload")
            controller.setDraft("")
            with patch.object(core, "completion", offline_completion):
                click(view, "sendButton")
                wait_for(lambda: len(calls) == 2 and not controller.busy and not server.session.state()["busy"],
                         "attachment-only message")
            assert controller.attachments == []
            print("Native file picker, real TXT extraction and attachment-only send: PASS", flush=True)

            fixture = os.path.join(server.tools.output_dir, "isolated-result.txt")
            expected = b"isolated output, not G-code\n"
            with open(fixture, "wb") as stream:
                stream.write(expected)
            output = server.tools._record_output({"ok": True, "path": fixture})["output"]
            wait_for(lambda: len(controller.outputs) == 1, "native output list")
            view.setProperty("page", "scene")
            button_name = "saveOutput_" + output["id"]
            capture(view, "native-scene-0.4.png")
            wait_for(lambda: find_control(view, button_name) is not None, "output button")
            destination = os.path.join(storage, "saved-result.txt")
            with patch("plugin.AIAssistant.native.QFileDialog.getSaveFileName", return_value=(destination, "")):
                click(view, button_name)
            wait_for(lambda: os.path.isfile(destination), "authenticated output save")
            with open(destination, "rb") as stream:
                assert stream.read() == expected
            capture(view, "native-scene-0.4.png")
            print("Native save picker and authenticated output download: PASS", flush=True)

            view.setProperty("page", "chat")
            controller.setDraft("Изолированный тест остановки")
            entered = threading.Event()
            stopped = threading.Event()
            def cancellable_completion(config, messages, schemas, cancel=None):
                entered.set()
                if cancel is not None and cancel.wait(5):
                    stopped.set()
                    raise core.ProviderError("Изолированный запуск отменён пользователем.")
                raise core.ProviderError("Тест не получил запрос отмены.")
            with patch.object(core, "completion", cancellable_completion):
                click(view, "sendButton")
                wait_for(lambda: entered.is_set() and controller.busy and not controller.commandPending,
                         "running task after first state poll")
                click(view, "cancelButton")
                wait_for(lambda: stopped.is_set() and not server.session.state()["busy"] and not controller.busy,
                         "running task cancelled")
            print("Native Stop cancels an already running task: PASS", flush=True)

            previous_events = len(controller.events)
            server.session.emit("info", message="Ещё одно тестовое событие")
            wait_for(lambda: len(controller.events) > previous_events, "developer events")
            view.setProperty("page", "developer")
            capture(view, "native-developers-0.4.png")
            assert all("Тестовое событие журнала" not in item.get("content", "") for item in controller.messages)

            view.setProperty("page", "scene")
            operations = (
                ('Обновить сцену', 'get_scene', {}),
                ('Проверить 5D', 'check_5d', {}),
                ('Нарезать', 'start_slice', {}),
                ('Сохранить 3MF', 'save_project', {'filename': 'project.3mf'}),
                ('Экспорт G-code', 'export_gcode', {'filename': 'result.gcode'}),
            )
            for label, name, arguments in operations:
                wait_for(lambda: not controller.busy and not controller.commandPending, 'ready for ' + name)
                controller.dismissError()
                cursor = controller._cursor
                pending = [view.contentItem()]
                button = None
                while pending:
                    candidate = pending.pop()
                    if candidate.property('text') == label and candidate.property('pressed') is not None:
                        button = candidate
                        break
                    pending.extend(candidate.childItems())
                assert button is not None, 'Missing scene button: ' + label
                assert button.property('enabled')
                QMetaObject.invokeMethod(button, 'clicked', Qt.DirectConnection)
                wait_for(lambda: controller.error or any(
                    item.get('id', 0) > cursor and item.get('type') == 'tool_result' and item.get('name') == name
                    for item in controller.events), 'scene button ' + name)
                assert not controller.error, label + ': ' + controller.error
                starts = [item for item in controller.events if item.get('id', 0) > cursor and
                          item.get('type') == 'tool_start' and item.get('name') == name]
                assert starts and starts[-1].get('arguments') == arguments, 'Lost QML arguments: ' + name
                assert controller.actionMessage and controller.actionMessage != 'Выполняется…'
                assert control(view, 'sceneActionMessage').property('text') == controller.actionMessage
                if name == 'check_5d':
                    assert 'Изолированная проверка основания' in controller.actionMessage
                print('Actual QML scene button -> HTTP -> ToolRegistry: ' + name + ' PASS', flush=True)

            assert not qml_errors, "QML runtime errors: " + " | ".join(qml_errors)
            before = time.monotonic()
            extension.stop()
            assert time.monotonic() - before < 0.5, "Stop blocked GUI"
            print("Separate developer journal and asynchronous stop: PASS", flush=True)
        finally:
            extension.stop()
            wait_for(lambda: QThreadPool.globalInstance().activeThreadCount() == 0,
                     'background workers drained', seconds=25)
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            application.processEvents()
            engine.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            application.quit()
            application.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
print("HOST_NATIVE_SMOKE_PASSED")
