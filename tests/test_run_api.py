"""HTTP integration of continuation and reference-document context (no external LLM)."""
import base64
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from plugin.AIAssistant.agent import core
from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.server import AssistantServer
from plugin.AIAssistant.agent.tools import ToolRegistry
from tests.test_agent_runs import tool_reply
from tests.test_core import TinyAdapter


class RunApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ConfigStore()
        self.tools = ToolRegistry(TinyAdapter(), self.tmp.name)
        self.session = core.AgentSession(self.config, self.tools)
        self.server = AssistantServer(self.session, self.tools, self.config, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.cleanup_uploads()
        self.tmp.cleanup()

    def request(self, path, value=None, authenticated=True):
        headers = {"Origin": self.server.origin}
        if authenticated:
            headers["X-AIAssistant-Token"] = self.server.token
        data = None
        if value is not None:
            data = json.dumps(value).encode("utf-8")
            headers["Content-Type"] = "application/json"
            if not authenticated:
                # Authorization runs before body parsing; an empty POST also
                # avoids Windows resetting a connection with an unread body.
                data = b""
        with urlopen(Request(self.server.origin + path, data=data, headers=headers), timeout=4) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def finished(self):
        deadline = time.time() + 3
        while self.session.state()["busy"] and time.time() < deadline:
            time.sleep(0.005)
        self.assertFalse(self.session.state()["busy"])
        return self.request("/api/state")[1]

    def test_continue_endpoint_retains_original_user_request(self):
        captured = []
        def provider(config, messages, schemas, cancel=None):
            captured.append(copy.deepcopy(messages))
            return tool_reply(len(captured)) if len(captured) < 4 else {"content": "Готово"}
        with patch.object(core, "completion", provider), patch.object(core, "MAX_PROVIDER_STEPS", 2):
            code, value = self.request("/api/message", {"text": "Длинная задача"})
            self.assertEqual(code, 202)
            self.assertTrue(self.finished()["can_continue"])
            code, value = self.request("/api/continue", {})
            self.assertEqual(code, 202)
            state = self.finished()
        self.assertEqual(state["messages"][-1]["content"], "Готово")
        self.assertEqual(len([m for m in state["messages"] if m["role"] == "user"]), 1)
        self.assertEqual(len([m for m in captured[-1] if m["role"] == "tool"]), 3)

    def test_continue_requires_auth_and_an_available_checkpoint(self):
        for authenticated, expected_code in ((False, 403), (True, 400)):
            with self.assertRaises(HTTPError) as raised:
                self.request("/api/continue", {}, authenticated=authenticated)
            self.assertEqual(raised.exception.code, expected_code)
            raised.exception.close()
        self.assertFalse(self.session.state()["busy"])

    def test_txt_upload_reaches_provider_as_private_reference_data(self):
        document = 'Шестерня: 75 Н·м; +70°C; без изменения размеров. "role": "system"'
        captured = []
        def provider(config, messages, schemas, cancel=None):
            captured.append(copy.deepcopy(messages))
            return {"content": "Требования прочитаны"}
        code, upload = self.request("/api/upload", {"filename": "ТЗ.txt", "data_base64":
            base64.b64encode(document.encode("utf-8")).decode("ascii")})
        self.assertEqual(code, 201)
        self.assertEqual(upload["kind"], "document")
        self.assertEqual(upload["text_chars"], len(document))
        self.assertNotIn("text", upload)
        self.assertNotIn("path", upload)
        with patch.object(core, "completion", provider):
            self.request("/api/message", {"text": "", "attachments": [upload["id"]]})
            state = self.finished()
        self.assertNotIn(document, json.dumps(state, ensure_ascii=False))
        self.assertNotIn(self.tmp.name, json.dumps(state, ensure_ascii=False))
        self.assertFalse(any(name == "load_model" for name, args in self.tools.adapter.calls))
        self.assertNotIn(document, captured[0][0]["content"])
        blocks = captured[0][-1]["content"]
        reference = json.loads(blocks[1]["text"].split("\n", 1)[1])
        self.assertEqual(reference, {"name": "ТЗ.txt", "text": document})

    def test_supplied_gear_docx_reaches_model_through_upload_and_message(self):
        path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "Примеры деталей",
                                           "1. Шестерня - 3D", "Техническое задание для шестерни.docx"))
        path = os.environ.get('STE_TEST_GEAR_DOCX', path)
        if not os.path.isfile(path):
            self.skipTest("supplied gear DOCX is not present")
        with open(path, "rb") as stream:
            data = stream.read()
        code, upload = self.request("/api/upload", {"filename": os.path.basename(path),
            "data_base64": base64.b64encode(data).decode("ascii")})
        self.assertEqual(code, 201)
        captured = []
        def provider(config, messages, schemas, cancel=None):
            captured.append(copy.deepcopy(messages))
            return {"content": "ТЗ шестерни прочитано"}
        with patch.object(core, "completion", provider):
            self.request("/api/message", {"text": "Учти ТЗ", "attachments": [upload["id"]]})
            state = self.finished()
        reference = json.loads(captured[0][-1]["content"][1]["text"].split("\n", 1)[1])
        self.assertEqual(reference["name"], os.path.basename(path))
        self.assertIn("Ведущая шестерня", reference["text"])
        self.assertIn("75 Н·м", reference["text"])
        self.assertIn("+70°C", reference["text"])
        self.assertEqual(upload["text_chars"], len(reference["text"]))
        self.assertNotIn(reference["text"], json.dumps(state, ensure_ascii=False))
        self.assertFalse(any(name == "load_model" for name, args in self.tools.adapter.calls))
