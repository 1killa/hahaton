"""Executed by STE's actual python35.dll, without modifying the application."""
import os
import sys
import importlib

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
print("HOST_SMOKE_PASSED")
