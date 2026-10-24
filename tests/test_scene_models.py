"""Scene validity must depend on models, never cameras or layer previews."""
import unittest
from tests.test_slicer import FakeNode
from tests import test_exports as export_fixtures
from plugin.AIAssistant.agent.tools import ToolError


class HostNode(FakeNode):
    def __init__(self, node_id, sliceable=False, mesh=True, group=False):
        super(HostNode, self).__init__(node_id)
        self.sliceable = sliceable
        self.mesh = object() if mesh else None
        self.group = group

    def callDecoration(self, name):
        return {'isSliceable': self.sliceable, 'isGroup': self.group}.get(name)

    def getMeshData(self):
        return self.mesh


class SceneModelTests(unittest.TestCase):
    def setUp(self):
        self.host = export_fixtures.ExportTests()
        self.host.setUp()
        self.scene = self.host.app.controller.scene
        self.model = HostNode('part', sliceable=True)
        self.scene.nodes = [HostNode('volume'), HostNode('camera', mesh=False), self.model,
                            HostNode('shadow'), HostNode('layers')]
        self.host.adapter.call('start_slice', {})

    def tearDown(self):
        self.host.tearDown()

    def test_scene_lists_only_printable_models_and_groups(self):
        group = HostNode('group', mesh=False, group=True)
        self.scene.nodes.append(group)
        objects = self.host.adapter.call('get_scene', {})['objects']
        self.assertEqual([item['id'] for item in objects], ['part', 'group'])
        self.assertEqual(self.host.adapter._native_nodes(), [self.model, group])

    def test_service_objects_cannot_be_transformed_or_removed_by_assistant(self):
        self.assertIsNone(self.host.adapter._find_node('camera'))
        self.assertIsNone(self.host.adapter._find_node('volume'))
        self.assertIs(self.host.adapter._find_node('part'), self.model)

    def test_each_native_export_can_add_snapshot_camera(self):
        def snapshot():
            self.scene.nodes.append(HostNode('snapshot-%s' % len(self.scene.nodes), mesh=False))
        self.host.on_snapshot = snapshot
        first = self.host.export()
        second = self.host.export()
        self.assertTrue(first['ok'] and second['ok'])
        self.assertEqual(len(self.host.registry.public_outputs()), 2)
        self.assertEqual(self.host.adapter.call('get_slice_status', {})['state'], 'done')

    def test_delayed_layer_preview_and_camera_motion_do_not_stale_slice(self):
        self.scene.nodes[1].position = [42, 42, 42]
        self.scene.nodes.append(HostNode('new-layer-preview'))
        self.assertTrue(self.host.export()['ok'])

    def test_scene_reordering_and_model_rename_do_not_stale_slice(self):
        self.scene.nodes.reverse()
        self.model.name = 'renamed gear'
        self.assertTrue(self.host.export()['ok'])

    def test_model_named_snapshot_is_still_a_model(self):
        self.model.name = 'snapshot'
        self.model.position[0] = 7
        with self.assertRaisesRegex(ToolError, 'изменилась модель'):
            self.host.export()

    def test_service_only_scene_is_empty_for_slicing(self):
        self.scene.nodes.remove(self.model)
        calls = self.host.app.backend.force_calls
        result = self.host.adapter.call('start_slice', {})
        self.assertEqual(result['state'], 'error')
        self.assertEqual(self.host.app.backend.force_calls, calls)

    def test_stale_reason_is_preserved_on_second_export_attempt(self):
        self.model.position[0] = 7
        errors = []
        for attempt in range(2):
            try:
                self.host.export()
            except ToolError as error:
                errors.append(str(error))
        self.assertEqual(len(errors), 2)
        self.assertEqual(errors[0], errors[1])
        self.assertIn('изменилась модель', errors[0])
