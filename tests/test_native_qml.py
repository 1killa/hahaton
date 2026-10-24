import os
import unittest

try:
    from PyQt5.QtCore import QCoreApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PyQt5/Qt is available only in the STE host runtime")
class NativeQmlTests(unittest.TestCase):
    def test_qml_is_direct_non_modal_window_with_composer(self):
        path = os.path.join(os.path.dirname(__file__), "..", "plugin", "AIAssistant", "qml",
                            "AssistantWindow.qml")
        with open(path, "rb") as stream:
            source = stream.read().decode("utf-8")
        self.assertIn("Window {", source)
        self.assertNotIn("Dialog {", source)
        self.assertNotIn("StackLayout", source)
        self.assertNotIn("property var manager", source)
        self.assertIn("Shift", source)
        self.assertIn("developer", source)


if __name__ == "__main__":
    unittest.main()
