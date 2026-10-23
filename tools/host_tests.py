"""Run the same behavioral suite on STE's Python 3.5 runtime."""
import os
import tempfile
import unittest

task_temp = os.path.join(os.getcwd(), ".runtime", "host-test-tmp")
os.makedirs(task_temp, exist_ok=True)
tempfile.tempdir = task_temp
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise RuntimeError("Behavioral tests failed on native Python 3.5")
print("HOST_BEHAVIOR_TESTS_PASSED", result.testsRun)
