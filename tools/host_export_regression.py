"""Exercise STE 7.10's real writer and Qt dispatch decorator in isolation.

No user scene or output is touched. OpenGL rendering and scene metadata are
controlled fixtures; GCodeWriter.write/_getPreviewImage/_createSnapshot and
call_on_qt_thread are the installed implementations.
"""
import importlib
import importlib.util
import os
import threading
from unittest.mock import patch
from tests.test_exports import ExportTests
from UM.Application import Application
from steslicer.SteSlicerApplication import SteSlicerApplication
from steslicer.Snapshot import Snapshot
from UM.Scene.Camera import Camera
from UM.Scene.SceneNode import SceneNode
from UM.Mesh.MeshData import MeshData
from steslicer.Scene.SliceableObjectDecorator import SliceableObjectDecorator
from steslicer.LayerDataDecorator import LayerDataDecorator
import numpy

path = os.path.join(os.path.dirname(__import__('sys').executable), 'plugins', 'GCodeWriter', 'GCodeWriter.py')
spec = importlib.util.spec_from_file_location('assistant_native_gcode_regression', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
threading_module = importlib.import_module('steslicer.Utils.Threading')
test = ExportTests()
test.setUp()
try:
    scene = test.app.controller.scene
    model = SceneNode()
    model.setName('snapshot')  # User models must not be filtered by name.
    model.setMeshData(MeshData(vertices=numpy.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]], dtype=numpy.float32)))
    model.addDecorator(SliceableObjectDecorator())
    preview = SceneNode()
    preview.setMeshData(MeshData())
    preview.addDecorator(LayerDataDecorator())
    scene.nodes = [model, Camera('3d'), preview]
    assert test.adapter._scene_nodes() == [model]
    test.adapter.call('start_slice', {})
    # The native decorator's callLater must enqueue, exactly as the app does.
    class TestQtApplication:
        @staticmethod
        def getInstance():
            return TestQtApplication()
        def callLater(self, function, *args, **kwargs):
            test.pending.put(lambda: function(*args, **kwargs))

    def snapshot(**kwargs):
        assert threading.current_thread().ident == test.owner
        test.callbacks.append('native snapshot on Qt')
        # Snapshot.snapshot creates a new Camera('snapshot', root) and leaves
        # it in the scene. Reproduce that side effect with the actual class.
        scene.nodes.append(Camera('snapshot'))
        return None

    with patch.object(Application, 'getInstance', return_value=test.app), \
            patch.object(threading_module, 'SteSlicerApplication', TestQtApplication), \
            patch.object(Snapshot, 'snapshot', side_effect=snapshot):
        writer = module.GCodeWriter()
        writer._getSlicerVersion = lambda: ';VERSION:regression\n'
        writer._getPrintingMode = lambda: ';PRINTING_MODE:[classic]\n'
        writer._getSerializedBounding = lambda: ';MINX:0\n'
        writer._serialiseSettings = lambda stack: ';SETTING_fixture\n'
        test.app.getGlobalContainerStack = lambda: test.app.manager.stack
        test.app.gcode_writer = writer
        result = test.export()
        assert result['ok'], result
        assert test.callbacks == ['native snapshot on Qt'], test.callbacks
        with open(result['path']) as stream:
            data = stream.read()
        assert 'fresh-gcode' in data and ';VERSION:regression' in data and ';SETTING_fixture' in data
        assert result['output']['size_bytes'] == os.path.getsize(result['path'])
        second = test.export()
        assert second['ok']
        assert test.adapter._scene_nodes() == [model]
        assert test.adapter.call('get_slice_status', {})['state'] == 'done'
        # Moving an actual model must still invalidate the completed slice.
        from UM.Math.Vector import Vector
        model.setPosition(Vector(5, 0, 0))
        assert not test.adapter._validate_completed_generation()
        print('Installed STE GCodeWriter + native call_on_qt_thread: PASS')
        print('Native cameras and layer preview excluded; repeated export stays valid: PASS')
        print('Qt snapshot callback processed; native G-code, headers and output link preserved.')
finally:
    test.tearDown()
