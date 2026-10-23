# -*- coding: utf-8 -*-
"""Behaviour checks for the host boundary (no STE installation required)."""

from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest

from plugin.AIAssistant.agent.slicer import DemoAdapter, SlicerAdapter


class Signal(object):
    def __init__(self):
        self.callbacks = []

    def connect(self, callback):
        self.callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self.callbacks):
            callback(*args)


class FakeNode(object):
    def __init__(self, node_id="part-1"):
        self.node_id = node_id
        self.name = "Part"
        self.position = [0.0, 5.0, 0.0]
        self.scale = [1.0, 1.0, 1.0]
        self.bounds_mm = {"x": 10.0, "y": 5.0, "z": 8.0}

    def getId(self):
        return self.node_id

    def getName(self):
        return self.name

    def getPosition(self):
        return self.position

    def setPosition(self, value):
        self.position = list(value)

    def getScale(self):
        return self.scale


class FakeScene(object):
    def __init__(self):
        self.nodes = [FakeNode()]
        self.gcode_dict = {0: ["gcode"]}
        self.activeBuildPlate = 0

    def getRoot(self):
        return self

    def getAllChildren(self):
        return list(self.nodes)

    def removeNode(self, node):
        self.nodes.remove(node)


class FakeStack(object):
    def __init__(self):
        self.values = {
            "printing_mode": "classic",
            "layer_height": 0.2,
            "cylindrical_mode_base_diameter": 20.0,
            "machine_five_axis": True,
            "material_id": "pla",
        }
        self.userChanges = {"layer_height": 0.2}
        self.definitions = {
            "printing_mode": {"type": "enum", "values": ["classic", "cylindrical", "cylindrical_full"]},
            "layer_height": {"type": "float", "minimum": 0.05, "maximum": 1.0},
            "cylindrical_mode_base_diameter": {"type": "float", "minimum": 4.0},
            "machine_five_axis": {"type": "bool"},
            "material_id": {"type": "string"},
        }
        self.extruders = []

    def getProperty(self, key, *args):
        if key not in self.values:
            raise KeyError(key)
        return self.values[key]

    def setProperty(self, key, *args):
        self.values[key] = args[-1]
        self.userChanges[key] = args[-1]


class FakeManager(object):
    def __init__(self):
        self.stack = FakeStack()
        self.stack.extruders = [FakeStack()]
        self.material_calls = []

    def getGlobalContainerStack(self):
        return self.stack

    def setMaterialById(self, position, material_id):
        self.material_calls.append((position, material_id))

    def setQualityGroupByQualityType(self, quality_type):
        self.quality_type = quality_type


class FakeWriter(object):
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def write(self, stream, nodes):
        self.calls += 1
        payload = self.payload
        if "b" not in getattr(stream, "mode", "") and isinstance(payload, bytes):
            payload = payload.decode("ascii")
        stream.write(payload)
        return True


class FakeHandler(object):
    def __init__(self, writer):
        self.writer = writer

    def getWriter(self, name):
        return self.writer


class FakeBackend(object):
    def __init__(self):
        self.processingProgress = Signal()
        self.backendStateChange = Signal()
        self.slicingStarted = Signal()
        self.printDurationMessage = Signal()
        self.scene = None
        self.force_calls = 0
        self.stop_calls = 0

    def forceSlice(self):
        self.force_calls += 1
        if self.scene is not None:
            self.scene.gcode_dict[0] = []
        self.slicingStarted.emit()
        if self.scene is not None:
            self.scene.gcode_dict[0] = ["fresh-gcode"]
        self.processingProgress.emit(0.5)
        self.printDurationMessage.emit(0, {}, [1.0])
        self.backendStateChange.emit("finished")
        return True

    def stopSlicing(self):
        self.stop_calls += 1
        self.backendStateChange.emit("notstarted")


class FakeDuration(object):
    last_format = None

    def getDisplayString(self, display_format):
        FakeDuration.last_format = display_format
        return "12"


class FakePrintInformation(object):
    currentPrintTime = FakeDuration()
    materialLengths = [25.0]
    materialWeights = [0.7]


class FakeBuildPlateModel(object):
    activeBuildPlate = 0


class FakeQUrl(object):
    def __init__(self, path):
        self.path = path

    @classmethod
    def fromLocalFile(cls, path):
        return cls(path)


class FakeVector(object):
    Unit_X = "X"
    Unit_Y = "Y"
    Unit_Z = "Z"

    def __init__(self, x, y, z):
        self.values = (x, y, z)


class FakeQuaternion(object):
    def __init__(self, value="identity"):
        self.value = value

    @classmethod
    def fromAngleAxis(cls, angle, axis):
        return cls((angle, axis))

    def __mul__(self, other):
        return FakeQuaternion((self.value, other.value))


class FakeOperation(object):
    pushed = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def push(self):
        FakeOperation.pushed.append(self)


class FakeRemoveOperation(FakeOperation):
    pass


class FakeInstanceChanges(object):
    def __init__(self, values):
        self.values = dict(values)
        self.removed = []

    def getAllKeys(self):
        return set(self.values)

    def getProperty(self, key, prop):
        return self.values[key]

    def setProperty(self, key, prop, value):
        self.values[key] = value

    def removeInstance(self, key):
        self.removed.append(key)
        self.values.pop(key, None)


class FakeController(object):
    def __init__(self):
        self.scene = FakeScene()

    def getScene(self):
        return self.scene


class FakeApp(object):
    def __init__(self):
        self.manager = FakeManager()
        self.backend = FakeBackend()
        self.controller = FakeController()
        self.backend.scene = self.controller.scene
        self.multi_build_plate_model = FakeBuildPlateModel()
        self.workspace_writer = FakeWriter(b"3mf")
        self.gcode_writer = FakeWriter(b"gcode")

    def getMachineManager(self):
        return self.manager

    def getBackend(self):
        return self.backend

    def getController(self):
        return self.controller

    def getMultiBuildPlateModel(self):
        return self.multi_build_plate_model

    def getPrintInformation(self):
        return FakePrintInformation()

    def getWorkspaceFileHandler(self):
        return FakeHandler(self.workspace_writer)

    def getMeshFileHandler(self):
        return FakeHandler(self.gcode_writer)

    def readLocalFile(self, url):
        self.read_url = url


class RunningBackend(FakeBackend):
    def forceSlice(self):
        self.force_calls += 1
        self.slicingStarted.emit()
        return None


class SilentBackend(FakeBackend):
    def forceSlice(self):
        self.force_calls += 1
        return None


class FalseWriter(FakeWriter):
    def write(self, stream, nodes):
        self.calls += 1
        stream.write("partial")
        return False


class FakeMaterialNode(object):
    def __init__(self, name):
        self.name = name

    def getName(self):
        return self.name


class FakeQualityManager(object):
    def getQualityGroups(self, stack):
        return {"draft": {"id": "draft", "name": "Draft"}}


class FakeMaterialManager(object):
    def __init__(self):
        self.calls = []

    def getAvailableMaterialsForMachineExtruder(self, machine, extruder):
        self.calls.append((machine, extruder))
        return {"pla-root": FakeMaterialNode("PLA")}


class ProfileApp(FakeApp):
    def __init__(self):
        FakeApp.__init__(self)
        self.quality_manager = FakeQualityManager()
        self.material_manager = FakeMaterialManager()

    def getQualityManager(self):
        return self.quality_manager

    def getMaterialManager(self):
        return self.material_manager


class SlicerAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="slicer-adapter-", dir=os.path.abspath("tests"))

    def tearDown(self):
        if os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp)

    def test_demo_is_explicit_and_never_fakes_exports(self):
        adapter = DemoAdapter(self.tmp)
        self.assertIsInstance(adapter.list_tools(), list)
        self.assertEqual({item["name"] for item in adapter.list_tools()} & {"start_slice", "export_gcode"},
                         {"start_slice", "export_gcode"})
        scene = adapter.call("get_scene", {})
        self.assertEqual(scene["objects"][0]["id"], "demo-cube")
        self.assertEqual(adapter.call("get_settings", {"keys": ["layer_height"], "scope": "global"})["values"],
                         {"layer_height": 0.2})
        self.assertEqual(adapter.call("start_slice", {})["code"], "UNAVAILABLE")
        self.assertEqual(adapter.call("export_gcode", {"filename": "x.gcode"})["code"], "UNAVAILABLE")
        self.assertEqual(adapter.call("save_project", {"filename": "x.3mf"})["code"], "UNAVAILABLE")
        self.assertEqual(os.listdir(self.tmp), [])

    def test_setting_validation_snapshot_and_restore_remove_new_override(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        snapshot = adapter.snapshot_settings()
        self.assertEqual(snapshot["global"]["values"]["layer_height"], 0.2)
        self.assertEqual(adapter.call("set_setting", {
            "key": "layer_height", "value": 0.3, "scope": "global",
        })["ok"], True)
        self.assertEqual(adapter.call("set_setting", {
            "key": "layer_height", "value": 5.0, "scope": "global",
        })["code"], "INVALID_ARGUMENTS")
        # restore_settings is an adapter method, as required by the contract.
        app.manager.stack.userChanges["new_override"] = 123
        self.assertTrue(adapter.restore_settings(snapshot)["ok"])
        self.assertNotIn("new_override", app.manager.stack.userChanges)
        self.assertIn("layer_height", app.manager.stack.userChanges)
        self.assertEqual(app.manager.stack.values["layer_height"], 0.2)

    def test_generation_requires_completion_and_mutation_stales_gcode(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        class DurationFormat(object):
            class Format(object):
                Seconds = 0
        adapter._duration_format = DurationFormat
        started = adapter.call("start_slice", {})
        self.assertEqual(started["state"], "done")
        status = adapter.call("get_slice_status", {})
        self.assertEqual(status["state"], "done")
        self.assertTrue(status["result"]["available"])
        exported = adapter.call("export_gcode", {"filename": "result.gcode"})
        self.assertTrue(exported["ok"])
        self.assertEqual(exported["size_bytes"], 5)
        self.assertTrue(os.path.isfile(exported["path"]))
        adapter.call("set_setting", {"key": "layer_height", "value": 0.3, "scope": "global"})
        self.assertEqual(adapter.call("get_slice_status", {})["state"], "stale")
        self.assertEqual(adapter.call("export_gcode", {"filename": "again.gcode"})["code"], "UNAVAILABLE")
        # The installed backends emit DurationFormat.Format.Seconds, not a
        # string or a top-level DurationFormat.Seconds member.
        self.assertEqual(FakeDuration.last_format, 0)

    def test_terminal_backend_state_after_done_invalidates_generation(self):
        for terminal in ("Processing", "NotStarted", "Disabled", "Error"):
            app = FakeApp()
            adapter = SlicerAdapter(app, self.tmp)
            self.assertEqual(adapter.call("start_slice", {})["state"], "done")
            app.backend.backendStateChange.emit(terminal)
            self.assertEqual(adapter.call("get_slice_status", {})["state"], "stale")
            self.assertEqual(adapter.call("export_gcode", {"filename": "terminal.gcode"})["code"],
                             "UNAVAILABLE")

    def test_empty_scene_does_not_start_native_backend(self):
        app = FakeApp()
        app.controller.scene.nodes = []
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("start_slice", {})
        self.assertEqual(result["state"], "error")
        self.assertEqual(result["code"] if "code" in result else None, None)
        self.assertEqual(app.backend.force_calls, 0)
        self.assertEqual(adapter.call("get_slice_status", {})["state"], "error")

    def test_no_start_signal_has_bounded_error_state(self):
        app = FakeApp()
        app.backend = SilentBackend()
        app.backend.scene = app.controller.scene
        adapter = SlicerAdapter(app, self.tmp)
        self.assertEqual(adapter.call("start_slice", {})["state"], "running")
        adapter._slice_started_at -= 31.0
        status = adapter.call("get_slice_status", {})
        self.assertEqual(status["state"], "error")
        self.assertIn("no slicingStarted", status["error"])

    def test_restore_running_slice_stops_backend_before_restore(self):
        app = FakeApp()
        app.backend = RunningBackend()
        app.backend.scene = app.controller.scene
        adapter = SlicerAdapter(app, self.tmp)
        snapshot = adapter.snapshot_settings()
        self.assertEqual(adapter.call("start_slice", {})["state"], "running")
        result = adapter.restore_settings(snapshot)
        self.assertTrue(result["ok"])
        self.assertEqual(app.backend.stop_calls, 1)
        self.assertEqual(adapter.call("get_slice_status", {})["state"], "stale")

    def test_list_profiles_uses_machine_extruder_compatibility_query(self):
        app = ProfileApp()
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("list_profiles", {})
        self.assertEqual(result["materials"], [{"id": "pla-root", "name": "PLA"}])
        self.assertEqual(len(app.material_manager.calls), 1)
        self.assertIs(app.material_manager.calls[0][0], app.manager.stack)
        self.assertIs(app.material_manager.calls[0][1], app.manager.stack.extruders[0])

    def test_check_5d_reports_native_geometry_base_and_bounded_assumptions(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("check_5d", {})
        facts = result["facts"]
        self.assertEqual(facts["base_diameter_mm"], 20.0)
        self.assertEqual(facts["objects"], [{"id": "part-1", "bounds_mm": {"x": 10.0, "y": 5.0, "z": 8.0},
                                              "position_mm": [0.0, 5.0, 0.0]}])
        self.assertFalse(result["verified_geometry"])
        self.assertTrue(any("Loads and mating faces" in item for item in result["assumptions"]))
        self.assertTrue(any("strength" in item for item in result["assumptions"]))

    def test_check_5d_cylindrical_warnings_do_not_claim_cut_geometry(self):
        app = FakeApp()
        app.manager.stack.values["printing_mode"] = "cylindrical"
        app.manager.stack.values["machine_five_axis"] = False
        app.manager.stack.values["cylindrical_mode_base_diameter"] = 3.0
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("check_5d", {})
        warnings = " ".join(result["warnings"])
        self.assertIn("without reported five-axis", warnings)
        self.assertIn("below the native minimum", warnings)
        self.assertIn("inner-cylinder exclusion", warnings)
        self.assertFalse(result["verified_geometry"])

    def test_check_5d_does_not_invent_missing_base_diameter(self):
        app = FakeApp()
        app.manager.stack.values.pop("cylindrical_mode_base_diameter")
        app.manager.stack.values["printing_mode"] = "cylindrical"
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("check_5d", {})
        self.assertNotIn("base_diameter_mm", result["facts"])
        self.assertTrue(any("base diameter is unavailable" in item for item in result["warnings"]))

    def test_failed_writer_preserves_existing_output_and_cleans_temp(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        self.assertEqual(adapter.call("start_slice", {})["state"], "done")
        target = os.path.join(self.tmp, "result.gcode")
        with open(target, "w") as stream:
            stream.write("old output")
        app.gcode_writer = FalseWriter(b"ignored")
        result = adapter.call("export_gcode", {"filename": "result.gcode"})
        self.assertEqual(result["code"], "UNAVAILABLE")
        with open(target, "r") as stream:
            self.assertEqual(stream.read(), "old output")
        self.assertEqual([name for name in os.listdir(self.tmp) if name.endswith(".tmp")], [])

    def test_external_scene_change_stales_completed_export(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        self.assertEqual(adapter.call("start_slice", {})["state"], "done")
        app.controller.scene.nodes[0].position[1] = 99.0
        result = adapter.call("export_gcode", {"filename": "scene-change.gcode"})
        self.assertEqual(result["code"], "UNAVAILABLE")
        self.assertEqual(adapter.call("get_slice_status", {})["state"], "stale")

    def test_symlink_output_is_rejected_when_supported(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        link = os.path.join(self.tmp, "link.gcode")
        target = os.path.join(self.tmp, "outside.gcode")
        with open(target, "w") as stream:
            stream.write("outside")
        try:
            os.symlink(target, link)
        except (AttributeError, OSError):
            self.skipTest("symlink creation is unavailable")
        self.assertEqual(adapter.call("start_slice", {})["state"], "done")
        result = adapter.call("export_gcode", {"filename": "link.gcode"})
        self.assertEqual(result["code"], "INVALID_ARGUMENTS")
        with open(target, "r") as stream:
            self.assertEqual(stream.read(), "outside")

    def test_native_3mf_uses_workspace_writer_and_rejects_path(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        result = adapter.call("save_project", {"filename": "project.3mf"})
        self.assertTrue(result["ok"])
        self.assertEqual(app.workspace_writer.calls, 1)
        self.assertEqual(app.gcode_writer.calls, 0)
        self.assertEqual(adapter.call("save_project", {"filename": "..\\outside.3mf"})["code"], "INVALID_ARGUMENTS")

    def test_import_uses_application_read_local_file_and_stays_pending(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        adapter._qt = FakeQUrl
        adapter._operations = {}
        adapter._math_types = {}
        model_path = os.path.join(self.tmp, "part.stl")
        with open(model_path, "w") as stream:
            stream.write("solid demo")
        result = adapter.call("load_model", {"path": model_path})
        self.assertEqual(result["state"], "pending")
        self.assertEqual(app.read_url.path, model_path)

    def test_transforms_use_native_value_types_and_push_operations(self):
        app = FakeApp()
        adapter = SlicerAdapter(app, self.tmp)
        FakeOperation.pushed = []
        adapter._operations = {"translate": FakeOperation, "rotate": FakeOperation,
                               "scale": FakeOperation, "remove": FakeRemoveOperation}
        adapter._math_types = {"Vector": FakeVector, "Quaternion": FakeQuaternion}
        self.assertTrue(adapter.call("transform_model", {"object_id": "part-1", "action": "translate",
                                                          "x": 1, "y": 2, "z": 3})["ok"])
        self.assertIsInstance(FakeOperation.pushed[-1].args[1], FakeVector)
        self.assertTrue(adapter.call("transform_model", {"object_id": "part-1", "action": "rotate",
                                                          "x": 90, "y": 0, "z": 0})["ok"])
        self.assertIsInstance(FakeOperation.pushed[-1].args[1], FakeQuaternion)
        self.assertTrue(adapter.call("remove_model", {"object_id": "part-1"})["ok"])
        self.assertIsInstance(FakeOperation.pushed[-1], FakeRemoveOperation)

    def test_instance_container_snapshot_removes_new_overrides_exactly(self):
        app = FakeApp()
        app.manager.stack.userChanges = FakeInstanceChanges({"layer_height": 0.2})
        app.manager.stack.extruders[0].userChanges = FakeInstanceChanges({"nozzle_size": 0.4})
        adapter = SlicerAdapter(app, self.tmp)
        snapshot = adapter.snapshot_settings()
        app.manager.stack.userChanges.setProperty("new_override", "value", 123)
        app.manager.stack.userChanges.setProperty("layer_height", "value", 0.5)
        result = adapter.restore_settings(snapshot)
        self.assertTrue(result["ok"])
        self.assertEqual(app.manager.stack.userChanges.values, {"layer_height": 0.2})
        self.assertIn("new_override", app.manager.stack.userChanges.removed)


if __name__ == "__main__":
    unittest.main()
