"""Regressions for native weak signals, setting scalars and large uploads."""
import gc
import os
import tempfile
import unittest
import weakref

from plugin.AIAssistant.agent.slicer import SlicerAdapter
from plugin.AIAssistant.agent.tools import ToolRegistry, ToolError
from plugin.AIAssistant.agent.server import validate_upload
from tests.test_slicer import FakeApp, FakeBackend, FakePrintInformation, Signal, RunningBackend


class WeakSignal:
    def __init__(self):
        self.callbacks = []
    def connect(self, callback):
        self.callbacks.append(weakref.ref(callback))
    def disconnect(self, callback):
        self.callbacks = [ref for ref in self.callbacks if ref() is not callback]
    def emit(self, *args):
        for ref in list(self.callbacks):
            callback = ref()
            if callback is not None:
                callback(*args)


class BugfixTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = FakeApp()
        self.adapter = SlicerAdapter(self.app, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_weak_native_callbacks_survive_gc_and_complete_slice(self):
        backend = RunningBackend()
        for name in ('processingProgress', 'backendStateChange', 'slicingStarted', 'printDurationMessage'):
            setattr(backend, name, WeakSignal())
        self.app.backend = backend
        backend.scene = self.app.controller.scene
        self.assertEqual(self.adapter.call('start_slice', {})['state'], 'running')
        gc.collect()
        backend.scene.gcode_dict[0] = ['fresh after real async backend']
        backend.printDurationMessage.emit(0, {}, [1])
        backend.backendStateChange.emit('finished')
        status = self.adapter.call('get_slice_status', {})
        self.assertEqual(status['state'], 'done')
        self.assertEqual(status['result']['time_seconds'], 12)
        self.assertEqual(status['result']['material_mm'], [25000.0])
        self.assertEqual(status['result']['material_g'], [0.7])
        # Restart disconnects old callbacks instead of accumulating closures.
        self.adapter.call('start_slice', {})
        self.assertTrue(all(len(getattr(backend, name).callbacks) == 1 for name in (
            'processingProgress', 'backendStateChange', 'slicingStarted', 'printDurationMessage')))

    def test_metrics_refresh_when_print_information_arrives_after_done(self):
        info = FakePrintInformation()
        self.app.getPrintInformation = lambda: info
        self.assertEqual(self.adapter.call('start_slice', {})['state'], 'done')
        info.materialLengths = [1.25, 0]
        info.materialWeights = [3.4, 0]
        status = self.adapter.call('get_slice_status', {})
        self.assertEqual(status['result']['material_mm'], [1250, 0])
        self.assertEqual(status['result']['material_g'], [3.4, 0])

    def test_failed_time_field_does_not_hide_known_material(self):
        class BadDuration:
            def getDisplayString(self, fmt):
                raise RuntimeError('not ready')
        info = FakePrintInformation()
        info.currentPrintTime = BadDuration()
        self.app.getPrintInformation = lambda: info
        self.assertEqual(self.adapter.call('start_slice', {})['state'], 'done')
        result = self.adapter.call('get_slice_status', {})['result']
        self.assertTrue(result['available'])
        self.assertEqual(result['material_g'], [0.7])
        self.assertNotIn('time_seconds', result)

    def test_string_numbers_and_booleans_use_native_types_and_preserve_pins(self):
        stack = self.app.manager.stack
        stack.values.update(wall_line_count=2, support_enable=True)
        stack.definitions.update(wall_line_count={'type': 'int', 'minimum': 0}, support_enable={'type': 'bool'})
        registry = ToolRegistry(self.adapter, self.tmp.name)
        registry.set_requirements({'settings': [{'key': 'wall_line_count', 'value': 5}]})
        result = registry.call('set_setting', {'key': 'wall_line_count', 'value': '5', 'scope': 'global'})
        self.assertEqual(result['value'], 5)
        self.assertIsInstance(stack.values['wall_line_count'], int)
        with self.assertRaises(ToolError):
            registry.call('set_setting', {'key': 'wall_line_count', 'value': '6', 'scope': 'global'})
        for value, expected in [('true', True), ('False', False), ('"true"', True), ('no', False)]:
            result = registry.call('set_setting', {'key': 'support_enable', 'value': value, 'scope': 'global'})
            self.assertIs(result['value'], expected)
        for value in ('5.5', '5 walls', 'NaN'):
            self.assertFalse(self.adapter.call('set_setting', {'key': 'wall_line_count', 'value': value, 'scope': 'global'})['ok'])

    def test_writes_user_changes_instead_of_qobject_property(self):
        stack = self.app.manager.stack
        class Changes:
            def setProperty(self, key, prop, value):
                stack.values[key] = value
            def getAllKeys(self):
                return []
        stack.userChanges = Changes()
        stack.setProperty = lambda *args: self.fail('QObject.setProperty must not be called')
        result = self.adapter.call('set_setting', {'key': 'layer_height', 'value': '0.3', 'scope': 'global'})
        self.assertTrue(result['ok'])
        self.assertEqual(stack.values['layer_height'], 0.3)

    def test_setting_that_does_not_change_is_not_reported_successful(self):
        self.app.manager.stack.setProperty = lambda *args: None
        result = self.adapter.call('set_setting', {'key': 'layer_height', 'value': 0.3, 'scope': 'global'})
        self.assertFalse(result['ok'])

    def test_upload_limits_still_reject_oversized_models_images_and_documents(self):
        for name, size, limit in [('large.stl', 64, 64), ('large.png', 24, 24), ('large.docx', 24, 24)]:
            with self.assertRaisesRegex(ToolError, '%s MiB' % limit):
                validate_upload(name, bytes(size * 1024 * 1024 + 1))
