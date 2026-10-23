import copy
import os
import tempfile
import threading
import unittest

from plugin.AIAssistant.agent.tools import ToolRegistry, ToolError, tool_schema


class WorkflowAdapter:
    """Explicit test double: metrics here are fixtures, never delivered output."""
    is_demo = False

    def __init__(self, directory):
        self.directory = directory
        self.values = {"layer_height": 0.2, "support_enable": False}
        self.before_slice = []
        self.generation = 0
        self.cancel_after_slice = None
        self.fail_restore = False

    def list_tools(self):
        return [tool_schema("set_setting", "", {"key": {"type": "string"}, "value": {}, "scope": {"type": "string"}}, ["key", "value", "scope"]),
                tool_schema("start_slice", "", {}), tool_schema("get_slice_status", "", {}),
                tool_schema("get_settings", "", {"keys": {"type": "array"}, "scope": {"type": "string"}, "extruder": {"type": "integer"}}, ["keys", "scope"]),
                tool_schema("save_project", "", {"filename": {"type": "string"}}, ["filename"]),
                tool_schema("export_gcode", "", {"filename": {"type": "string"}}, ["filename"])]

    def snapshot_settings(self):
        return copy.deepcopy(self.values)

    def restore_settings(self, snapshot):
        if self.fail_restore:
            return {"ok": False, "error": "restore failed"}
        self.values = copy.deepcopy(snapshot)
        return {"ok": True}

    def call(self, name, args):
        if name == "set_setting":
            if "scope" not in args:
                return {"ok": False, "error": "scope required"}
            self.values[args["key"]] = args["value"]
        elif name == "get_settings":
            return {"values": {key: self.values.get(key) for key in args["keys"]}}
        elif name == "start_slice":
            self.before_slice.append(dict(self.values))
            self.generation += 1
            if self.cancel_after_slice:
                self.cancel_after_slice.set()
            return {"state": "running", "plugin_generation_id": self.generation}
        elif name == "get_slice_status":
            return {"state": "done", "plugin_generation_id": self.generation, "result": {"available": True, "time_seconds": 100 + self.generation}}
        elif name in ("save_project", "export_gcode"):
            path = os.path.join(self.directory, args["filename"])
            with open(path, "wb") as stream:
                stream.write(b"unit-test-native-writer-fixture")
            return {"ok": True, "path": path}
        return {"ok": True}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = WorkflowAdapter(self.tmp.name)
        self.registry = ToolRegistry(self.adapter, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_compare_restores_before_each_and_after_and_captures_factual_results(self):
        original = dict(self.adapter.values)
        result = self.registry.call("compare_variants", {"variants": [
            {"label": "A", "settings": {"layer_height": 0.3}},
            {"label": "B", "settings": {"support_enable": True}}]})
        self.assertTrue(result["baseline_restored"])
        self.assertEqual(self.adapter.values, original)
        self.assertEqual(self.adapter.before_slice[1]["layer_height"], 0.2)
        self.assertEqual([item["result"]["time_seconds"] for item in result["variants"]], [101, 102])
        self.assertEqual(len(result["variants"][0]["outputs"]), 2)

    def test_cancel_comparison_still_restores_original(self):
        cancel = threading.Event()
        self.adapter.cancel_after_slice = cancel
        original = dict(self.adapter.values)
        with self.assertRaisesRegex(ToolError, "отменена"):
            self.registry.call("compare_variants", {"variants": [
                {"label": "A", "settings": {"layer_height": 0.3}},
                {"label": "B", "settings": {"layer_height": 0.4}}]}, cancel=cancel)
        self.assertEqual(self.adapter.values, original)

    def test_failed_restore_is_not_reported_as_baseline_restored(self):
        self.adapter.fail_restore = True
        with self.assertRaisesRegex(ToolError, "restore failed"):
            self.registry.call("compare_variants", {"variants": [
                {"label": "A", "settings": {"layer_height": 0.3}},
                {"label": "B", "settings": {"layer_height": 0.4}}]})

    def test_pinned_unmet_setting_blocks_slice_before_native_call(self):
        self.adapter.values["support_enable"] = True
        self.registry.set_requirements({"settings": [{"key": "support_enable", "value": False}]})
        with self.assertRaises(ToolError):
            self.registry.call("start_slice", {})
        self.assertEqual(self.adapter.before_slice, [])
