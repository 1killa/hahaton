import base64
import json
import os
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.core import AgentSession
from plugin.AIAssistant.agent.server import AssistantServer
from plugin.AIAssistant.agent.tools import ToolRegistry
from tests.test_core import TinyAdapter


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ConfigStore()
        self.tools = ToolRegistry(TinyAdapter(), self.tmp.name)
        self.session = AgentSession(self.config, self.tools)
        self.server = AssistantServer(self.session, self.tools, self.config, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.url = self.server.origin

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, path, body=None, token=True, origin=True, extra=None):
        headers = {}
        if token:
            headers["X-AIAssistant-Token"] = self.server.token
        if origin:
            headers["Origin"] = self.url
        if extra:
            headers.update(extra)
        if body is not None:
            headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        return urlopen(Request(self.url + path, data=data, headers=headers), timeout=4)

    def test_token_is_header_only_and_host_origin_are_checked(self):
        for path, kwargs in [("/api/state", {"token": False}),
                             ("/api/state?token=" + self.server.token, {"token": False}),
                             ("/api/state", {"extra": {"Host": "evil.example"}}),
                             ("/api/settings", {"body": {}, "extra": {"Origin": "https://evil.example"}}),
                             ("/api/settings", {"body": {}, "origin": False})]:
            with self.assertRaises(HTTPError) as raised:
                self.request(path, **kwargs)
            self.assertEqual(raised.exception.code, 403)
        with self.request("/api/state") as response:
            state = response.read().decode("utf-8")
        self.assertNotIn(self.server.token, state)

    def test_static_and_upload_reject_path_traversal_and_invalid_content(self):
        for filename, content in [("../x.stl", b"solid x"), ("x.exe", b"binary"), ("x.png", b"not png")]:
            with self.assertRaises(HTTPError):
                self.request("/api/upload", {"filename": filename, "data_base64": base64.b64encode(content).decode("ascii")})
        with self.assertRaises(HTTPError):
            self.request("/../agent/config.py")
        with self.request("/api/upload", {"filename": "cube.obj", "data_base64": base64.b64encode(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n").decode("ascii")}) as response:
            upload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(upload["kind"], "model")
        self.assertNotIn("path", upload)

    def test_settings_do_not_echo_key_and_spa_is_served(self):
        with self.request("/api/settings", {"api_key": "my-test-secret"}) as response:
            self.assertNotIn("my-test-secret", response.read().decode("utf-8"))
        with self.request("/api/state") as response:
            self.assertNotIn("my-test-secret", response.read().decode("utf-8"))
        with self.request("/", token=False) as response:
            self.assertIn(b"STE", response.read())

    def test_settings_update_respects_shared_operation_lock(self):
        self.tools.operation_lock.acquire()
        try:
            with self.assertRaises(HTTPError) as raised:
                self.request("/api/settings", {"model": "must-not-apply"})
            self.assertEqual(raised.exception.code, 400)
            self.assertNotEqual(self.config.public()["model"], "must-not-apply")
        finally:
            self.tools.operation_lock.release()
