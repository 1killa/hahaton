"""Run native Python-3.5-compatible tests inside the installed STE runtime."""
import unittest

names = ["tests.test_native_client", "tests.test_native_controller", "tests.test_native_qml"]
suite = unittest.TestSuite()
for name in names:
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Native host tests failed")
print("HOST_NATIVE_TESTS_PASSED", result.testsRun)
