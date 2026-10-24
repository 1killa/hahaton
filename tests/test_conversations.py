import json
import os
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.conversations import (ConversationStore, HistoryError,
                                                     MAX_FILE_BYTES, empty_state, sanitize_value)
from plugin.AIAssistant.agent.core import AgentSession
from plugin.AIAssistant.agent.server import _safe_output_path
from plugin.AIAssistant.agent.tools import ToolRegistry
from plugin.AIAssistant.agent.tools import ToolError
from tests.test_core import TinyAdapter


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(os.path.dirname(__file__), "..", ".runtime", "tmp", "history-tests")
        self.root = os.path.abspath(self.root)
        shutil.rmtree(self.root, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_atomic_round_trip_and_restart_status(self):
        store = ConversationStore(self.root)
        state = store.load()
        state["records"][0]["status"] = "paused"
        state["records"][0]["messages"] = [{"role": "user", "content": "сохрани"}]
        store.save(state)
        session = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root), history_dir=self.root)
        self.assertEqual(session.state()["status"], "interrupted")
        self.assertFalse(session.state()["can_continue"])
        self.assertEqual(session.state()["messages"][0]["content"], "сохрани")

    def test_corrupt_file_is_retained_and_not_replaced(self):
        os.makedirs(self.root)
        path = os.path.join(self.root, "conversations.json")
        with open(path, "wb") as stream:
            stream.write(b"{broken")
        store = ConversationStore(self.root)
        state = store.load()
        self.assertTrue(store.history_error)
        with self.assertRaises(HistoryError):
            store.save(state)
        with open(path, "rb") as stream:
            self.assertEqual(stream.read(), b"{broken")

    def test_exact_redaction_keeps_ordinary_ids(self):
        config = ConfigStore()
        config.update({"api_key": "secret-token"})
        value = sanitize_value({"api_key": "secret-token", "Headers": {"Authorization": "x"},
                                "upload_id": "abc", "tool_token": "keep", "message": "secret-token"}, config)
        self.assertNotIn("api_key", value)
        self.assertNotIn("Headers", value)
        self.assertEqual(value["upload_id"], "abc")
        self.assertEqual(value["tool_token"], "keep")
        self.assertEqual(value["message"], "[API KEY HIDDEN]")
        call = sanitize_value({"function": {"name": "upload", "arguments":
                                              '{"api_key":"secret-token","upload_id":"abc"}'}}, config)
        self.assertEqual(json.loads(call["function"]["arguments"]), {"upload_id": "abc"})

    def test_oversize_is_rejected_before_replace(self):
        store = ConversationStore(self.root)
        state = empty_state()
        state["records"][0]["messages"] = [{"role": "user", "content": "x" * (MAX_FILE_BYTES + 1)}]
        with self.assertRaises(HistoryError):
            store.save(state)
        self.assertFalse(os.path.exists(store.path))

    def test_output_containment_rejects_symlink_and_escape(self):
        output = os.path.join(self.root, "outputs")
        os.makedirs(output)
        path = os.path.join(output, "ok.gcode")
        with open(path, "wb") as stream:
            stream.write(b"G1")
        self.assertTrue(_safe_output_path(output, path))
        self.assertFalse(_safe_output_path(output, os.path.join(output, "..", "outside.gcode")))
        if hasattr(os, "symlink"):
            link = os.path.join(output, "link.gcode")
            try:
                os.symlink(path, link)
            except (OSError, NotImplementedError):
                return
            self.assertFalse(_safe_output_path(output, link))

    def test_in_memory_mode_has_real_conversation_ids_and_preserves_records(self):
        session = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root))
        first = session.conversation_id()
        second = session.create_conversation()
        self.assertNotEqual(first, second)
        self.assertEqual(len(session.conversations()), 2)
        session.select_conversation(first)
        self.assertEqual(session.conversation_id(), first)

    def test_create_rejects_the_51st_record(self):
        session = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root))
        for _ in range(49):
            session.create_conversation()
        with self.assertRaises(ToolError):
            session.create_conversation()
        self.assertEqual(len(session.conversations()), 50)

    def test_paused_turn_survives_same_process_switch(self):
        session = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root))
        first = session.conversation_id()
        turn = [{"role": "user", "content": [{"type": "text", "text": "pause"}]}]
        with session._lock:
            session._turns.append(turn)
            session._pending_turn = turn
            session._pending_turns[first] = turn
            session._status = "paused"
            session._record["status"] = "paused"
            session._checkpoint("test_pause")
        second = session.create_conversation()
        self.assertNotEqual(first, second)
        session.select_conversation(first)
        self.assertEqual(session.state()["status"], "paused")
        self.assertTrue(session.state()["can_continue"])

    def test_continue_after_switch_reuses_one_live_turn_and_persists_final(self):
        config = ConfigStore()
        tools = ToolRegistry(TinyAdapter(), self.root)
        session = AgentSession(config, tools, history_dir=self.root)
        replies = iter([
            {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_once", "type": "function",
                "function": {"name": "get_scene", "arguments": "{}"}}]},
            {"role": "assistant", "content": "Продолжено"}])
        requests = []

        def complete(_config, messages, _schemas):
            requests.append(json.loads(json.dumps(messages)))
            return next(replies) if len(requests) <= 2 else {"role": "assistant", "content": "Следующий ответ"}

        def wait_idle():
            deadline = time.time() + 3
            while session.state()["busy"] and time.time() < deadline:
                time.sleep(0.005)
            self.assertFalse(session.state()["busy"])

        first = session.conversation_id()
        with patch("plugin.AIAssistant.agent.core.completion", side_effect=complete), \
                patch("plugin.AIAssistant.agent.core.MAX_PROVIDER_STEPS", 1):
            session.submit("Проверь сцену")
            wait_idle()
            self.assertEqual(session.state()["status"], "paused")
            second = session.create_conversation()
            session.select_conversation(first)
            self.assertTrue(session.state()["can_continue"])
            session.continue_pending()
            wait_idle()
            session.submit("Следующий вопрос")
            wait_idle()

        self.assertEqual(len(requests), 3)
        resumed = requests[1]
        self.assertEqual(len([item for item in resumed if item.get("role") == "user"]), 1)
        tool_ids = [item["tool_call_id"] for item in resumed if item.get("role") == "tool"]
        self.assertEqual(tool_ids, ["call_once"])
        self.assertIn("Продолжено", json.dumps(requests[2], ensure_ascii=False))
        self.assertTrue(any(item.get("content") == "Продолжено" for item in session.state()["messages"]))
        self.assertEqual(session.state()["messages"][-1]["content"], "Следующий ответ")
        self.assertNotEqual(first, second)
        restarted = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root), history_dir=self.root)
        restored_messages = restarted.state()["messages"]
        self.assertTrue(any(item.get("content") == "Продолжено" for item in restored_messages))

    def test_memory_reset_creates_new_chat_and_preserves_previous(self):
        session = AgentSession(ConfigStore(), ToolRegistry(TinyAdapter(), self.root))
        first = session.conversation_id()
        session._messages.append({"role": "user", "content": "старый чат"})
        session.sync_metadata()
        second = session.reset()
        self.assertNotEqual(first, second)
        self.assertEqual(session.state()["messages"], [])
        session.select_conversation(first)
        self.assertEqual(session.state()["messages"][0]["content"], "старый чат")


if __name__ == "__main__":
    unittest.main()
