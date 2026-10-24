"""Native writers must be free to synchronously dispatch work to Qt."""
import os
import queue
import tempfile
import threading
import time
import unittest

from plugin.AIAssistant.agent.dispatch import MainThreadBridge
from plugin.AIAssistant.agent.slicer import SlicerAdapter
from plugin.AIAssistant.agent.tools import ToolRegistry, ToolError
from tests.test_slicer import FakeApp


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.owner = threading.current_thread().ident
        self.pending = queue.Queue()
        self.bridge = MainThreadBridge(self.pending.put, timeout=2, owner_thread=self.owner)
        self.app = FakeApp()
        self.adapter = SlicerAdapter(self.app, self.tmp.name)
        self.registry = ToolRegistry(self.adapter, self.tmp.name, self.bridge.invoke)
        self.adapter.call('start_slice', {})
        self.callbacks = []
        self.on_snapshot = lambda: None
        self.fail_writer = False
        self.writer_error = False
        test = self

        class DispatchingWriter:
            def write(self, stream, nodes):
                test.assertNotEqual(threading.current_thread().ident, test.owner,
                                    'writer must not block Qt while waiting for its own queued callback')
                def snapshot():
                    test.assertEqual(threading.current_thread().ident, test.owner)
                    test.callbacks.append('snapshot')
                    test.on_snapshot()
                test.bridge.invoke(snapshot)
                if test.writer_error:
                    raise RuntimeError('native writer failed')
                stream.write(b'3mf fixture' if 'b' in stream.mode else 'G1 X1\n')
                return not test.fail_writer

        self.app.gcode_writer = DispatchingWriter()
        self.app.workspace_writer = DispatchingWriter()

    def tearDown(self):
        self.tmp.cleanup()

    def run_worker(self, function):
        result = {}
        def work():
            try:
                result['value'] = function()
            except Exception as error:
                result['error'] = error
        worker = threading.Thread(target=work)
        worker.daemon = True
        worker.start()
        deadline = time.monotonic() + 5
        while worker.is_alive() and time.monotonic() < deadline:
            try:
                self.pending.get(timeout=.01)()
            except queue.Empty:
                pass
        worker.join(.1)
        self.assertFalse(worker.is_alive(), 'export deadlocked')
        if 'error' in result:
            raise result['error']
        return result['value']

    def export(self, name='export_gcode', cancel=None):
        filename = 'result.gcode' if name == 'export_gcode' else 'project.3mf'
        return self.run_worker(lambda: self.registry.call(name, {'filename': filename}, cancel=cancel))

    def assert_no_output(self):
        self.assertEqual(os.listdir(self.tmp.name), [])
        self.assertEqual(self.registry.public_outputs(), [])

    def test_gcode_writer_can_dispatch_preview_and_produce_download(self):
        result = self.export()
        self.assertEqual(self.callbacks, ['snapshot'])
        self.assertEqual(result['output']['size_bytes'], 6)
        self.assertTrue(result['output']['url'].startswith('/api/download/'))
        with open(result['path']) as stream:
            self.assertEqual(stream.read(), 'G1 X1\n')

    def test_workspace_writer_can_dispatch_entire_write(self):
        self.assertTrue(self.export('save_project')['ok'])
        self.assertEqual(self.callbacks, ['snapshot'])

    def test_scene_change_during_write_discards_partial_file(self):
        self.on_snapshot = lambda: setattr(self.app.controller.scene.nodes[0], 'position', [99, 0, 0])
        with self.assertRaisesRegex(ToolError, 'изменились'):
            self.export()
        self.assert_no_output()

    def test_settings_change_during_write_discards_partial_file(self):
        self.on_snapshot = lambda: self.app.manager.stack.setProperty('layer_height', 'value', .3)
        with self.assertRaisesRegex(ToolError, 'изменились'):
            self.export()
        self.assert_no_output()

    def test_new_slice_during_write_discards_partial_file(self):
        self.on_snapshot = lambda: self.adapter.call('start_slice', {})
        with self.assertRaisesRegex(ToolError, 'другая нарезка'):
            self.export()
        self.assert_no_output()

    def test_cancel_during_write_discards_partial_file(self):
        cancel = threading.Event()
        self.on_snapshot = cancel.set
        with self.assertRaisesRegex(ToolError, 'отменена'):
            self.export(cancel=cancel)
        self.assert_no_output()

    def test_failed_native_writer_discards_partial_file(self):
        self.fail_writer = True
        with self.assertRaisesRegex(ToolError, 'did not return true'):
            self.export()
        self.assert_no_output()

    def test_native_writer_exception_discards_partial_file(self):
        self.writer_error = True
        with self.assertRaisesRegex(ToolError, 'native writer failed'):
            self.export()
        self.assert_no_output()

    def test_invoking_export_from_qt_is_rejected_without_entering_writer(self):
        with self.assertRaisesRegex(ToolError, 'рабочего потока'):
            self.registry.call('export_gcode', {'filename': 'result.gcode'})
        self.assert_no_output()

    def test_stale_slice_is_rejected_before_writer(self):
        self.app.controller.scene.nodes[0].position[1] = 99
        with self.assertRaisesRegex(ToolError, 'устарел'):
            self.export()
        self.assertEqual(self.callbacks, [])
        self.assert_no_output()

    def test_gcode_change_during_write_is_not_published(self):
        self.on_snapshot = lambda: self.app.controller.scene.gcode_dict.update({0: ['replaced']})
        with self.assertRaisesRegex(ToolError, 'устарел'):
            self.export()
        self.assert_no_output()

    def test_workspace_change_during_write_is_not_published(self):
        self.on_snapshot = lambda: setattr(self.app.controller.scene.nodes[0], 'position', [99, 0, 0])
        with self.assertRaisesRegex(ToolError, 'изменились'):
            self.export('save_project')
        self.assert_no_output()

    def test_cancel_before_export_never_enters_writer(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(ToolError, 'отменена'):
            self.export(cancel=cancel)
        self.assertEqual(self.callbacks, [])
        self.assert_no_output()
