import unittest
import os
import tempfile
from unittest.mock import patch


class NativeAttachmentTests(unittest.TestCase):
    def test_attachment_extension_and_stat_size_are_checked_before_read(self):
        from plugin.AIAssistant.native import _read_bounded, _OTHER_LIMIT

        handle, path = tempfile.mkstemp(suffix=".TXT")
        os.close(handle)
        try:
            with open(path, "wb") as stream:
                stream.write(b"requirements")
            self.assertEqual(_read_bounded(path), b"requirements")
            with patch("plugin.AIAssistant.native.os.stat") as stat:
                stat.return_value.st_size = _OTHER_LIMIT + 1
                with self.assertRaises(ValueError):
                    _read_bounded(path)
        finally:
            os.remove(path)

try:
    from PyQt5.QtCore import QCoreApplication
    HAS_QT = True
except ImportError:
    HAS_QT = False


@unittest.skipUnless(HAS_QT, "PyQt5/Qt is available only in the STE host runtime")
class NativeControllerTests(unittest.TestCase):
    def test_native_controller_constructs_and_disposes_without_server_thread(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        application = QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        self.assertEqual(controller.client.origin, Server.origin)
        self.assertFalse(controller.editRequirements("{bad", "", False, ""))
        self.assertTrue(controller.editRequirements("[]", "", False, ""))
        cancel_event = __import__("threading").Event()
        controller._operation_cancels[99] = cancel_event
        controller.dispose()
        self.assertTrue(controller._disposed)
        self.assertTrue(cancel_event.is_set())

    def test_native_controller_exposes_shared_server_contract(self):
        from plugin.AIAssistant.native import NativeAssistantController
        self.assertTrue(hasattr(NativeAssistantController, "bind_view"))
        self.assertTrue(hasattr(NativeAssistantController, "show"))
        self.assertTrue(hasattr(NativeAssistantController, "dispose"))

    def test_failed_message_keeps_draft_but_accepted_message_clears_it(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller.setDraft("keep this draft")
        controller._attachments = [{"id": "upload-1", "name": "part.stl"}]
        controller._start = lambda function, kind, command=False: self._accept_fake_command(controller)
        controller.sendMessage()
        controller._worker_error(42, controller._generation, RuntimeError("offline"), "message")
        self.assertEqual(controller.draft, "keep this draft")
        self.assertEqual(len(controller.attachments), 1)
        controller._command_pending = True
        controller._command_operation = 42
        controller._worker_result(42, controller._generation, {"accepted": True}, "message")
        self.assertEqual(controller.draft, "")
        self.assertEqual(controller.attachments, [])
        controller.dispose()

    def test_stale_idle_poll_cannot_clear_new_command_fence_or_new_poll(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._visible = True
        controller._poll_operation = 1
        controller._poll_inflight = True
        controller._command_pending = True
        controller._command_operation = 2
        controller._poll_fence = 2
        controller._worker_result(1, controller._poll_generation, {"busy": False, "messages": []}, "poll")
        self.assertTrue(controller._command_pending)
        self.assertFalse(controller._poll_inflight)
        controller._poll_operation = 3
        controller._poll_inflight = True
        controller._worker_result(1, controller._poll_generation, {"busy": False, "messages": []}, "poll")
        self.assertTrue(controller._poll_inflight)
        controller.dispose()

    def test_settings_and_requirements_receipts_only_follow_matching_success(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        settings_receipts = []
        requirements_receipts = []
        controller.settingsSaved.connect(lambda: settings_receipts.append(True))
        controller.requirementsSaved.connect(lambda: requirements_receipts.append(True))

        controller._settings_dirty = True
        controller._settings["base_url"] = "draft-url"
        controller._command_pending = True
        controller._command_operation = 10
        controller._worker_result(9, controller._generation,
                                  {"base_url": "stale-url", "model": "stale", "has_key": False},
                                  "settings")
        self.assertEqual(controller.settingsBaseUrl, "draft-url")
        self.assertEqual(settings_receipts, [])
        self.assertTrue(controller._settings_dirty)

        controller._command_pending = True
        controller._command_operation = 7
        controller._worker_result(7, controller._generation,
                                  {"base_url": "https://provider", "model": "m", "has_key": False},
                                  "settings")
        controller._worker_result(7, controller._generation,
                                  {"base_url": "https://provider", "model": "m", "has_key": False},
                                  "settings")
        self.assertEqual(settings_receipts, [True])
        self.assertFalse(controller._settings_dirty)

        controller.editSettings("draft-after-submit", "m2")
        controller._command_pending = True
        controller._command_operation = 11
        controller._submitted_settings_revision = controller._settings_revision - 1
        controller._worker_result(11, controller._generation,
                                  {"base_url": "server-value", "model": "server", "has_key": False},
                                  "settings")
        self.assertTrue(controller._settings_dirty)
        self.assertEqual(controller.settingsBaseUrl, "draft-after-submit")
        self.assertEqual(settings_receipts, [True])

        controller._requirements_dirty = True
        controller._command_pending = True
        controller._command_operation = 8
        controller._worker_error(8, controller._generation, RuntimeError("rejected"), "requirements")
        self.assertEqual(requirements_receipts, [])
        self.assertTrue(controller._requirements_dirty)
        controller._command_pending = True
        controller._command_operation = 9
        controller._worker_result(9, controller._generation,
                                  {"settings": [], "material_id": "", "preserve_dimensions": False,
                                   "notes": ""}, "requirements")
        self.assertEqual(requirements_receipts, [True])
        self.assertFalse(controller._requirements_dirty)
        controller.editRequirements("[]", "new-material", False, "edited")
        controller._command_pending = True
        controller._command_operation = 12
        controller._submitted_requirements_revision = controller._requirements_revision - 1
        controller._worker_result(12, controller._generation,
                                  {"settings": [], "material_id": "server", "preserve_dimensions": False,
                                   "notes": "server"}, "requirements")
        self.assertTrue(controller._requirements_dirty)
        self.assertEqual(controller.requirements["material_id"], "new-material")
        self.assertEqual(requirements_receipts, [True])
        controller.dispose()

    def test_new_draft_and_attachments_survive_old_message_ack(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller.setDraft("old")
        controller._attachments = [{"id": "old-upload"}]
        controller._start = lambda function, kind, command=False: self._accept_fake_command(controller)
        controller.sendMessage()
        controller.setDraft("new")
        controller._attachments.append({"id": "new-upload"})
        controller._worker_result(42, controller._generation, {"accepted": True}, "message")
        self.assertEqual(controller.draft, "new")
        self.assertEqual([item["id"] for item in controller.attachments], ["new-upload"])
        controller.dispose()

    def test_attachment_only_message_is_submitted(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        submitted = []
        controller._start = lambda function, kind, command=False: submitted.append(function()) or 1
        controller.client.message = lambda text, attachments: (text, attachments)
        controller._attachments = [{"id": "model-upload"}]
        controller.sendMessage()
        self.assertEqual(submitted, [("", ["model-upload"])])
        controller.dispose()

    def test_requirements_ack_does_not_replace_full_chat_state(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._messages = [{"role": "user", "content": "keep"}]
        controller._scene = {"objects": ["keep"]}
        controller._outputs = [{"id": "keep-output"}]
        controller._command_pending = True
        controller._command_operation = 3
        controller._worker_result(3, controller._generation,
                                  {"settings": [], "material_id": "", "preserve_dimensions": False,
                                   "notes": ""}, "requirements")
        self.assertEqual(controller.messages[0]["content"], "keep")
        self.assertEqual(controller.scene, {"objects": ["keep"]})
        self.assertEqual(controller.outputs[0]["id"], "keep-output")
        controller.dispose()

    def test_conversation_identity_change_clears_old_cursor_events_and_draft(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._conversation_id = "old"
        controller._cursor = 12
        controller._events = [{"id": 12, "message": "old"}]
        controller._draft = "draft"
        controller._command_pending = False
        controller._apply_state({"api_version": 3, "conversation_id": "new", "cursor": 12,
                                 "messages": [{"role": "user", "content": "new"}],
                                 "events": [{"id": 12, "message": "new"}],
                                 "requirements": {"settings": [], "material_id": "",
                                                  "preserve_dimensions": False, "notes": ""}})
        self.assertEqual(controller.conversationId, "new")
        self.assertEqual(controller.events, [])
        self.assertEqual(controller._cursor, 0)
        self.assertTrue(controller._conversation_refetch)
        self.assertEqual(controller.draft, "")
        controller.dispose()

    def test_ack_keeps_busy_fence_until_fresh_poll_and_cancel_remains_available(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._state = {"busy": False}
        controller._draft = "next"
        controller._command_pending = True
        controller._command_operation = 4
        controller._worker_result(4, controller._generation, {"accepted": True}, "message")
        self.assertTrue(controller.commandPending)
        self.assertTrue(controller.busy)
        controller._state = {"busy": True}
        cancel_calls = []
        controller.client.cancel = lambda: cancel_calls.append(True) or {"cancel_requested": True}
        controller._start = lambda function, kind, command=False: function() or 9
        controller.cancel()
        self.assertEqual(cancel_calls, [True])
        controller.dispose()

    def test_cancel_running_work_without_pending_ack_dispatches_immediately(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        application = QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._state = {"busy": True}
        calls = []
        controller.client.cancel = lambda: calls.append(True) or {"cancel_requested": True}
        controller._start = lambda function, kind, command=False: function() or 9
        controller.cancel()
        self.assertEqual(calls, [True])
        controller.dispose()

    def test_cancel_intent_is_replayed_after_message_ack(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        cancel_calls = []
        controller.client.cancel = lambda: cancel_calls.append(True) or {"cancel_requested": True}
        controller._command_pending = True
        controller._command_operation = 5
        # The POST is still in flight: record the intent but do not race a
        # second mutation against it.  The controller must replay cancellation
        # only after the accepted message acknowledgement.
        controller._start = lambda function, kind, command=False, cancel_event=None: function() or 99
        controller.cancel()
        self.assertEqual(cancel_calls, [])
        self.assertTrue(controller._cancel_requested)
        controller._worker_result(5, controller._generation, {"accepted": True}, "message")
        self.assertEqual(cancel_calls, [True])
        self.assertTrue(controller._ack_waiting_poll)
        controller.dispose()

    def test_cancel_intent_is_replayed_after_tool_ack(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        cancel_calls = []
        controller.client.cancel = lambda: cancel_calls.append(True) or {"cancel_requested": True}
        controller._command_pending = True
        controller._command_operation = 6
        controller._start = lambda function, kind, command=False, cancel_event=None: function() or 99
        controller.cancel()
        self.assertEqual(cancel_calls, [])
        controller._worker_result(6, controller._generation, {"accepted": True}, "tool")
        self.assertEqual(cancel_calls, [True])
        controller.dispose()

    def test_action_error_survives_successful_poll_and_upload_error_is_not_cleared(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._visible = True
        controller._error = "failed upload"
        controller._upload_error = "failed upload"
        controller._poll_operation = 1
        controller._poll_inflight = True
        controller._worker_result(1, controller._poll_generation,
                                  {"api_version": 3, "busy": False, "messages": [], "events": [], "cursor": 0},
                                  "poll")
        self.assertEqual(controller.error, "failed upload")
        self.assertEqual(controller.uploadError, "failed upload")
        controller.dispose()

    def test_upload_success_does_not_clear_another_upload_failure(self):
        from plugin.AIAssistant.native import NativeAssistantController

        class Server(object):
            origin = "http://127.0.0.1:9"
            token = "test-session-token"

        QCoreApplication.instance() or QCoreApplication([])
        controller = NativeAssistantController(Server())
        controller._uploads_pending = 2
        controller._worker_error(20, controller._generation, RuntimeError("first failed"), "upload")
        controller._worker_error(21, controller._generation, RuntimeError("second failed"), "upload")
        controller._worker_result(20, controller._generation, {"id": "retry-1"}, "upload")
        self.assertEqual(controller.uploadError, "second failed")
        controller.dispose()

    @staticmethod
    def _accept_fake_command(controller):
        controller._command_pending = True
        controller._command_operation = 42
        return 42


if __name__ == "__main__":
    unittest.main()
