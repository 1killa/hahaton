"""STE Slicer API-5 Extension. All host work stays on the Qt thread."""
import os
import threading
from UM.Extension import Extension
from UM.Resources import Resources
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices

from .agent.config import ConfigStore, DEFAULT_MODEL, DEFAULT_URL
from .agent.core import AgentSession
from .agent.dispatch import MainThreadBridge
from .agent.server import AssistantServer
from .agent.slicer import SlicerAdapter
from .agent.tools import ToolRegistry


class AIAssistant(Extension):
    def __init__(self, app):
        super().__init__()
        self._app = app
        self._server = None
        self._server_thread = None
        self._native_controller = None
        self._native_window = None
        self._bridge = MainThreadBridge(app.callLater, owner_thread=threading.current_thread().ident)
        self.setMenuName("STE Assistant")
        self.addMenuItem("Открыть чат и настройки", self.showChat)
        self.addMenuItem("Открыть чат в STE Slicer", self.showNativeChat)
        self.addMenuItem("Остановить помощника", self.stop)
        preferences = app.getPreferences()
        for key, value in (("base_url", DEFAULT_URL), ("model", DEFAULT_MODEL), ("api_key", "")):
            preferences.addPreference("ste_assistant/" + key, value)
        self._config = ConfigStore(
            read=lambda key: preferences.getValue("ste_assistant/" + key),
            write=lambda key, value: self._bridge.invoke(
                lambda: preferences.setValue("ste_assistant/" + key, value)))
        try:
            from PyQt5.QtCore import QCoreApplication
            QCoreApplication.instance().aboutToQuit.connect(self.stop)
        except (AttributeError, RuntimeError):
            pass

    def _ensureServer(self):
        if self._server is None:
            runtime = os.path.join(Resources.getDataStoragePath(), "ste_assistant")
            output_dir = os.path.join(runtime, "outputs")
            history_dir = os.path.join(runtime, "history")
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(history_dir, exist_ok=True)
            adapter = SlicerAdapter(self._app, output_dir)
            tools = ToolRegistry(adapter, output_dir, invoke=self._bridge.invoke)
            session = AgentSession(self._config, tools, history_dir=history_dir)
            self._server = AssistantServer(session, tools, self._config, runtime, mode="steslicer")
            self._server_thread = threading.Thread(target=self._server.serve_forever)
            self._server_thread.daemon = True
            self._server_thread.start()
        return self._server

    def showChat(self):
        server = self._ensureServer()
        QDesktopServices.openUrl(QUrl(server.browser_url()))

    def showNativeChat(self):
        controller = None
        try:
            server = self._ensureServer()
            if self._native_controller is None:
                # The browser entry point does not depend on the native UI.
                from .native import NativeAssistantController
                controller = NativeAssistantController(server)
                path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "qml", "AssistantWindow.qml")
                view = self._app.createQmlComponent(path, {"manager": controller})
                if view is None:
                    raise RuntimeError("Не удалось загрузить окно чата. Обновите папку плагина целиком.")
                controller.bind_view(view)
                self._native_controller = controller
                self._native_window = view
            self._native_controller.show()
        except Exception as error:
            if controller is not None and controller is not self._native_controller:
                controller.dispose()
            self._showNativeError(error)

    def _showNativeError(self, error):
        from UM.Message import Message
        detail = self._config.redact(str(error))
        if self._server is not None:
            detail = detail.replace(self._server.token, "[SESSION HIDDEN]")
        Message("Не удалось открыть чат в STE Slicer: " + detail,
                title="STE Assistant", lifetime=0).show()

    def stop(self):
        controller = self._native_controller
        self._native_controller = None
        self._native_window = None
        if controller is not None:
            controller.dispose()
        server = self._server
        if server is None:
            return
        server.session.cancel()
        self._server = None
        # shutdown waits on the HTTP serve loop, never on Qt callbacks.
        def close():
            server.shutdown()
            server.server_close()
            server.cleanup_uploads()
        worker = threading.Thread(target=close)
        worker.daemon = True
        worker.start()
