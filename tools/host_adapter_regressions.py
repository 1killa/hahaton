"""In-memory native STE classes; does not change the user's scene/settings."""
import gc
import json
import os
import tempfile
import importlib
from steslicer.SteSlicerApplication import SteSlicerApplication
from UM.Settings.ContainerRegistry import ContainerRegistry
from UM.Settings.ContainerStack import ContainerStack
from UM.Settings.DefinitionContainer import DefinitionContainer
from UM.Settings.InstanceContainer import InstanceContainer
from UM.Settings.SettingDefinition import SettingDefinition
from UM.Signal import Signal
from UM.Qt.Duration import Duration
from plugin.AIAssistant.agent.slicer import SlicerAdapter
from tests.test_slicer import FakeApp, RunningBackend

definition = DefinitionContainer('assistant-regression-definition')
definitions = {
    'wall_line_count': {'label': 'Wall count', 'type': 'int', 'default_value': 2, 'minimum_value': 0},
    'support_enable': {'label': 'Supports', 'type': 'bool', 'default_value': False}}
for key, properties in definitions.items():
    properties.setdefault('description', 'Native in-memory regression fixture')
    item = SettingDefinition(key, definition)
    item.deserialize(properties)
    definition.addDefinition(item)
changes = InstanceContainer('assistant-regression-overrides')
# Resolve test definitions without bootstrapping an Application or user files.
class TestRegistry:
    def findDefinitionContainers(self, **kwargs):
        return [definition] if kwargs.get('id') == definition.getId() else []
importlib.import_module('UM.Settings.InstanceContainer')._containerRegistry = TestRegistry()
changes.setMetaDataEntry('definition', definition.getId())
stack = ContainerStack('assistant-regression-stack')
stack.addContainer(definition)
stack.addContainer(changes)
stack.userChanges = changes
app = FakeApp()
app.manager.stack = stack
with tempfile.TemporaryDirectory(dir=os.path.join(os.getcwd(), '.runtime')) as directory:
    adapter = SlicerAdapter(app, directory)
    for key, value, expected in [('wall_line_count', '5', 5), ('support_enable', 'true', True)]:
        result = adapter._tool_set_setting({'key': key, 'value': value, 'scope': 'global'})
        assert result.get('ok'), result
        assert stack.getProperty(key, 'value') == expected
        assert changes.getProperty(key, 'value') == expected
    print('Native ContainerStack + InstanceContainer typed overrides/read-back: PASS')
    app = FakeApp()
    backend = RunningBackend()
    backend.scene = app.controller.scene
    for name in ('slicingStarted', 'processingProgress', 'backendStateChange', 'printDurationMessage'):
        setattr(backend, name, Signal(Signal.Direct))
    app.backend = backend
    class Information:
        currentPrintTime = Duration(1234)
        materialLengths = [1.25, 0]
        materialWeights = [3.4, 0]
    app.getPrintInformation = lambda: Information()
    adapter = SlicerAdapter(app, directory)
    assert adapter.call('start_slice', {})['state'] == 'running'
    gc.collect()
    backend.scene.gcode_dict[0] = ['fresh native async G-code fixture']
    backend.printDurationMessage.emit(0, {}, [])
    backend.backendStateChange.emit('finished')
    status = adapter.call('get_slice_status', {})
    assert status['state'] == 'done', status
    assert status['result']['time_seconds'] == 1234, status
    assert status['result']['material_mm'] == [1250, 0], status
    assert status['result']['material_g'] == [3.4, 0], status
    print('Native UM.Signal after GC + Duration + material conversion: PASS')
print('HOST_ADAPTER_REGRESSIONS_PASSED')
