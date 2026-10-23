import base64
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from plugin.AIAssistant.agent.config import ConfigStore, ConfigurationError
from plugin.AIAssistant.agent.core import AgentSession, completion, ProviderError
from plugin.AIAssistant.agent.dispatch import MainThreadBridge
from plugin.AIAssistant.agent.rpc import dispatch_rpc
from plugin.AIAssistant.agent.tools import ToolRegistry, ToolError


class TinyAdapter:
    def __init__(self):
        self.values = {"layer_height": 0.2, "support_enable": False}
        self.calls = []

    def list_tools(self):
        return [{"name": "set_setting", "description": "Set value", "inputSchema": {
            "type": "object", "properties": {"key": {"type": "string"}, "value": {}},
            "required": ["key", "value"], "additionalProperties": False}},
            {"name": "get_scene", "description": "Scene", "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]

    def call(self, name, args):
        self.calls.append((name, args))
        if name == "set_setting":
            self.values[args["key"]] = args["value"]
        if name == "get_scene":
            return {"objects": [], "mode": "classic"}
        return {"ok": True}


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.adapter = TinyAdapter()
        self.tools = ToolRegistry(self.adapter, self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_config_key_is_not_public_and_blank_keeps_it(self):
        config = ConfigStore()
        config.update({"api_key": "secret-value"})
        self.assertNotIn("secret-value", json.dumps(config.public()))
        config.update({"api_key": ""})
        self.assertEqual(config.private()["api_key"], "secret-value")
        config.update({"api_key": "   \t "})
        self.assertEqual(config.private()["api_key"], "secret-value")
        config.update({"clear_key": True})
        self.assertFalse(config.public()["has_key"])

    def test_redaction_handles_json_escaped_secret_in_nested_events(self):
        config = ConfigStore()
        secret = 'secret"with\\escapes'
        config.update({"api_key": secret})
        session = AgentSession(config, self.tools)
        session.emit("tool_result", result={"deep": [secret]})
        self.assertNotIn(secret, str(session.state()))
        self.assertEqual(session.state()["events"][-1]["result"]["deep"], ["[API KEY HIDDEN]"])

    def test_config_rejects_remote_http_and_url_credentials(self):
        config = ConfigStore()
        for url in ["http://example.com/v1", "https://user:pass@example.com/v1", "file:///key", "https://example.com/v1?key=secret"]:
            with self.assertRaises(ConfigurationError):
                config.update({"base_url": url})
        self.assertEqual(config.public()["base_url"], "https://api.timeweb.ai/v1")

    def test_hard_pin_prevents_mutation_and_unknown_args_are_rejected(self):
        self.tools.set_requirements({"settings": [{"key": "support_enable", "value": False}], "preserve_dimensions": True})
        with self.assertRaises(ToolError):
            self.tools.call("set_setting", {"key": "support_enable", "value": True})
        self.assertEqual(self.adapter.calls, [])
        with self.assertRaises(ToolError):
            self.tools.call("set_setting", {"key": "x", "value": 1, "unknown": 2})

    def test_rpc_ids_errors_and_tool_result(self):
        self.assertEqual(dispatch_rpc({"jsonrpc": "2.0", "id": 9, "method": "initialize"}, self.tools)["id"], 9)
        self.assertEqual(dispatch_rpc({"jsonrpc": "2.0", "id": "x", "method": "missing"}, self.tools)["error"]["code"], -32601)
        result = dispatch_rpc({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "get_scene", "arguments": {}}}, self.tools)
        self.assertFalse(result["result"]["isError"])
        self.assertIsNone(dispatch_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}, self.tools))

    def test_queued_callback_after_timeout_or_cancel_never_mutates(self):
        queued = []
        bridge = MainThreadBridge(lambda fn: queued.append(fn), timeout=0.03)
        changed = []
        with self.assertRaises(Exception):
            bridge.invoke(lambda: changed.append(1))
        queued.pop()()
        self.assertEqual(changed, [])
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Exception):
            bridge.invoke(lambda: changed.append(2), cancel=cancel)
        for callback in queued:
            callback()
        self.assertEqual(changed, [])

    def test_real_http_provider_tool_loop_and_base64_image(self):
        captured = []
        class Provider(BaseHTTPRequestHandler):
            def do_POST(handler):
                captured.append(json.loads(handler.rfile.read(int(handler.headers["Content-Length"])).decode("utf-8")))
                if len(captured) == 1:
                    message = {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_scene", "arguments": "{}"}}]}
                else:
                    message = {"role": "assistant", "content": "Сцена проверена."}
                body = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
                handler.send_response(200)
                handler.send_header("Content-Length", str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)
            def log_message(self, *args):
                pass
        provider = HTTPServer(("127.0.0.1", 0), Provider)
        thread = threading.Thread(target=provider.serve_forever)
        thread.daemon = True
        thread.start()
        try:
            config = ConfigStore()
            config.update({"base_url": "http://127.0.0.1:%s/v1" % provider.server_port, "model": "test", "api_key": "test-key"})
            session = AgentSession(config, self.tools)
            image_data = "data:image/png;base64," + base64.b64encode(b"test-image").decode("ascii")
            session.submit("Проверь сцену", [{"id": "image1", "name": "image.png", "kind": "image", "data_url": image_data}])
            deadline = time.time() + 4
            while session.state()["busy"] and time.time() < deadline:
                time.sleep(0.01)
            state = session.state()
            self.assertFalse(state["busy"])
            self.assertEqual(state["messages"][-1]["content"], "Сцена проверена.")
            self.assertEqual(len(captured), 2)
            content = captured[0]["messages"][-1]["content"]
            self.assertEqual(content[-1]["image_url"]["url"], image_data)
            self.assertEqual(captured[1]["messages"][-1]["tool_call_id"], "call_1")
            self.assertNotIn("test-key", json.dumps(state))
        finally:
            provider.shutdown()
            provider.server_close()

    def test_provider_redirect_is_not_followed_with_credentials(self):
        captured = []
        class Sink(BaseHTTPRequestHandler):
            def do_GET(handler):
                captured.append(handler.headers.get("Authorization"))
                handler.send_response(200)
                handler.end_headers()
                handler.wfile.write(b"{}")
            def log_message(self, *args):
                pass
        sink = HTTPServer(("127.0.0.1", 0), Sink)
        class Redirect(BaseHTTPRequestHandler):
            def do_POST(handler):
                handler.rfile.read(int(handler.headers["Content-Length"]))
                handler.send_response(302)
                handler.send_header("Location", "http://127.0.0.1:%s/" % sink.server_port)
                handler.end_headers()
            def log_message(self, *args):
                pass
        source = HTTPServer(("127.0.0.1", 0), Redirect)
        for server in (source, sink):
            worker = threading.Thread(target=server.serve_forever)
            worker.daemon = True
            worker.start()
        try:
            config = ConfigStore()
            config.update({"base_url": "http://127.0.0.1:%s/v1" % source.server_port, "api_key": "redirect-test-key"})
            with self.assertRaises(ProviderError):
                completion(config, [{"role": "user", "content": "test"}], self.tools.list_tools())
            self.assertEqual(captured, [])
        finally:
            for server in (source, sink):
                server.shutdown()
                server.server_close()
