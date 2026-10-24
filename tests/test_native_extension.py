"""Menu integration uses the real STE Extension when its runtime is available."""
import importlib
import os
import unittest
from unittest.mock import Mock, patch

try:
    from steslicer.SteSlicerApplication import SteSlicerApplication
    extension_module = importlib.import_module('plugin.AIAssistant.AIAssistant')
except ImportError:
    extension_module = None


@unittest.skipIf(extension_module is None, 'Requires installed STE runtime')
class NativeExtensionTests(unittest.TestCase):
    def setUp(self):
        self.preferences = Mock()
        self.preferences.getValue.return_value = ''
        self.app = Mock()
        self.app.getPreferences.return_value = self.preferences
        self.extension = extension_module.AIAssistant(self.app)

    def test_native_and_browser_actions_share_the_server_and_reuse_the_window(self):
        self.assertTrue(hasattr(self.extension, '_ensureServer'), 'Native and browser actions need a shared server factory')
        server = Mock()
        server.browser_url.return_value = 'http://127.0.0.1:43210/#token=test-only'
        controller = Mock()
        with patch.object(self.extension, '_ensureServer', return_value=server) as ensure, \
                patch('plugin.AIAssistant.native.NativeAssistantController', return_value=controller) as factory, \
                patch.object(extension_module, 'QDesktopServices') as desktop:
            self.extension.activateMenuItem('Открыть чат в STE Slicer')
            self.extension.activateMenuItem('Открыть чат и настройки')
            self.extension.activateMenuItem('Открыть чат в STE Slicer')
            factory.assert_called_once_with(server)
            self.assertEqual(self.app.createQmlComponent.call_count, 1)
            qml_path, context = self.app.createQmlComponent.call_args[0]
            self.assertTrue(os.path.isfile(qml_path))
            self.assertEqual(context, {'manager': controller})
            self.assertEqual(controller.show.call_count, 2)
            self.assertEqual(ensure.call_count, 3)
            self.assertEqual(desktop.openUrl.call_args[0][0].toString(), server.browser_url())

    def test_stop_disposes_native_window_even_without_running_server(self):
        controller = Mock()
        self.extension._native_controller = controller
        self.extension.stop()
        controller.dispose.assert_called_once_with()
        self.assertIsNone(self.extension._native_controller)

    def test_failed_qml_load_disposes_unpublished_controller_and_reports_error(self):
        self.assertTrue(hasattr(self.extension, 'showNativeChat'), 'Native menu callback is missing')
        controller = Mock()
        self.app.createQmlComponent.return_value = None
        with patch.object(self.extension, '_ensureServer', return_value=Mock()), \
                patch('plugin.AIAssistant.native.NativeAssistantController', return_value=controller), \
                patch.object(self.extension, '_showNativeError') as report:
            self.extension.showNativeChat()
            controller.dispose.assert_called_once_with()
            self.assertIsNone(self.extension._native_controller)
            self.assertTrue(report.called)
