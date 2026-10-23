"""Executed by STE's actual python35.dll, without modifying the application."""
import os
import sys
import importlib
import json
import tempfile
from unittest.mock import patch
from urllib.request import Request, urlopen

root = os.getcwd()
print("Interpreter:", sys.version)
count = 0
for directory, folders, filenames in os.walk(os.path.join(root, "plugin", "AIAssistant")):
    folders[:] = [item for item in folders if item != "__pycache__"]
    for filename in filenames:
        if filename.endswith(".py"):
            path = os.path.join(directory, filename)
            with open(path, "rb") as source:
                compile(source.read(), path, "exec")
            count += 1
print("Python 3.5 compile:", count, "plugin source files")
for name in ("config", "dispatch", "rpc", "tools", "core", "slicer", "server"):
    importlib.import_module("plugin.AIAssistant.agent." + name)
print("Agent imports: PASS")
from plugin.AIAssistant.agent.slicer import DemoAdapter
demo = DemoAdapter(os.path.join(root, ".runtime", "host-demo"))
assert demo.call("get_settings", {"keys": ["printing_mode"], "scope": "global"})["values"]["printing_mode"] == "classic"
assert demo.call("start_slice", {})["code"] == "UNAVAILABLE"
print("Demo call under Python 3.5: PASS")
# Import in application bootstrap order to avoid host-internal import cycles.
from steslicer.SteSlicerApplication import SteSlicerApplication
from plugin.AIAssistant.AIAssistant import AIAssistant
print("Native STE application and Extension imports: PASS")
class PreferencesStub:
    def __init__(self):
        self.data = {}
    def addPreference(self, key, value):
        self.data.setdefault(key, value)
    def getValue(self, key):
        return self.data.get(key)
    def setValue(self, key, value):
        self.data[key] = value
class AppStub:
    def __init__(self):
        self.preferences = PreferencesStub()
    def getPreferences(self):
        return self.preferences
    def callLater(self, function):
        function()
import plugin.AIAssistant as plugin_package
registration = plugin_package.register(AppStub())
extension = registration["extension"]
assert isinstance(extension, AIAssistant)
assert extension.getMenuName() == "STE Assistant"
assert len(extension.getMenuItemList()) == 2
print("API-5 registration with native Extension class: PASS (test app context)")

# Exercise the menu callback, not just registration. Keep the native Resources
# resolver and HTTP server real; redirect storage and the external browser only.
from UM.Resources import Resources
with tempfile.TemporaryDirectory(prefix="host-chat-", dir=os.path.join(root, ".runtime")) as storage:
    with patch.object(Resources, "_Resources__data_storage_path", storage), \
            patch("plugin.AIAssistant.AIAssistant.QDesktopServices") as desktop:
        server = None
        try:
            extension.activateMenuItem("Открыть чат и настройки")
            server = extension._server
            with urlopen(server.origin + "/", timeout=5) as response:
                assert response.status == 200
                assert b"STE Assistant" in response.read()
            request = Request(server.origin + "/api/state", headers={"X-AIAssistant-Token": server.token})
            with urlopen(request, timeout=5) as response:
                state = json.loads(response.read().decode("utf-8"))
            assert state["mode"] == "steslicer"
            assert os.path.isdir(os.path.join(storage, "ste_assistant", "outputs"))
            assert desktop.openUrl.call_args[0][0].toString() == server.browser_url()
            extension.activateMenuItem("Открыть чат и настройки")
            assert extension._server is server
            assert desktop.openUrl.call_count == 2
            print("Native menu callback and loopback chat: PASS (test app context)")
        finally:
            server = extension._server
            if server is not None:
                server.shutdown()
                extension._server_thread.join(5)
                server.server_close()
                server.cleanup_uploads()
                extension._server = None
print("HOST_SMOKE_PASSED")
