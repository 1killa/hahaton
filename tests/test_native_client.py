import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import URLError
from unittest.mock import patch

from plugin.AIAssistant.agent.native_client import LoopbackClient, NativeClientError


class _NativeHandler(BaseHTTPRequestHandler):
    requests = []
    download = b"G1 X1\n"
    redirect = False
    reject = False
    reject_body = b""
    truncated = False
    delay = 0

    def log_message(self, *args):
        pass

    def _json(self, value, status=200):
        raw = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except OSError:
            # The timeout test intentionally closes the socket first.
            pass

    def do_GET(self):
        type(self).requests.append(("GET", self.path, self.headers.get("Origin"),
                                    self.headers.get("X-AIAssistant-Token")))
        if self.path.startswith("/api/download/"):
            self.send_response(302 if type(self).redirect else 200)
            self.send_header("Location", "/api/download/other") if type(self).redirect else None
            length = len(type(self).download) + 3 if type(self).truncated else len(type(self).download)
            self.send_header("Content-Length", str(length))
            self.end_headers()
            if not type(self).redirect:
                self.wfile.write(type(self).download)
            return
        if type(self).reject:
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(type(self).reject_body)))
            self.end_headers()
            self.wfile.write(type(self).reject_body)
            return
        if type(self).delay:
            time.sleep(type(self).delay)
        self._json({"api_version": 3, "messages": [], "events": [], "cursor": 2,
                    "settings": {"base_url": "http://127.0.0.1", "model": "x", "has_key": False}})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append(("POST", self.path, self.headers.get("Origin"),
                                    self.headers.get("X-AIAssistant-Token"), body))
        if type(self).reject:
            self.send_response(403)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(type(self).reject_body)))
            self.end_headers()
            self.wfile.write(type(self).reject_body)
            return
        self._json({"accepted": True, "api_version": 3}, 202)


class NativeClientTests(unittest.TestCase):
    def setUp(self):
        _NativeHandler.requests = []
        _NativeHandler.redirect = False
        _NativeHandler.reject = False
        _NativeHandler.reject_body = b""
        _NativeHandler.truncated = False
        _NativeHandler.delay = 0
        self.server = HTTPServer(("127.0.0.1", 0), _NativeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        self.origin = "http://127.0.0.1:%s" % self.server.server_port
        self.client = LoopbackClient(self.origin, "session-secret", timeout=0.5)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def test_state_uses_private_token_and_origin(self):
        state = self.client.state(4)
        self.assertEqual(state["api_version"], 3)
        self.assertEqual(_NativeHandler.requests[0][0:4],
                         ("GET", "/api/state?after=4", None, "session-secret"))

    def test_mutation_sends_exact_origin_and_fixed_route(self):
        self.client.message("hello", ["upload-1"])
        request = _NativeHandler.requests[-1]
        self.assertEqual(request[0:4], ("POST", "/api/message", self.origin, "session-secret"))
        self.assertEqual(json.loads(request[4].decode("utf-8")),
                         {"text": "hello", "attachments": ["upload-1"]})

    def test_external_origin_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            LoopbackClient("https://example.com", "secret")
        self.assertEqual(_NativeHandler.requests, [])

    def test_arbitrary_route_is_rejected(self):
        with self.assertRaises(ValueError):
            self.client.request("GET", "/api/secret")
        with self.assertRaises(ValueError):
            self.client.request("GET-DOWNLOAD", "/api/secret")
        with self.assertRaises(ValueError):
            self.client.request("GET", "/api/state?token=leak")
        self.assertEqual(_NativeHandler.requests, [])

    def test_redirect_is_rejected_without_following_or_leaking_token(self):
        _NativeHandler.redirect = True
        with self.assertRaises(NativeClientError):
            self.client.download("result-1", os.path.join(tempfile.gettempdir(), "native-result.gcode"))
        self.assertEqual(len(_NativeHandler.requests), 1)

    def test_download_validates_output_id_and_atomically_writes(self):
        destination = os.path.join(tempfile.gettempdir(), "native-client-result.gcode")
        try:
            for output_id in ("../escape", "", "a/b", "a\\b"):
                with self.assertRaises(ValueError):
                    self.client.download(output_id, destination)
            self.client.download("result-1", destination)
            with open(destination, "rb") as stream:
                self.assertEqual(stream.read(), _NativeHandler.download)
        finally:
            try:
                os.remove(destination)
            except OSError:
                pass

    def test_download_rejects_premature_eof_before_publishing(self):
        destination = os.path.join(tempfile.gettempdir(), "native-client-truncated.gcode")
        with open(destination, "wb") as stream:
            stream.write(b"existing")
        _NativeHandler.truncated = True
        try:
            with self.assertRaises(NativeClientError):
                self.client.download("result-1", destination)
            with open(destination, "rb") as stream:
                self.assertEqual(stream.read(), b"existing")
        finally:
            try:
                os.remove(destination)
            except OSError:
                pass

    def test_download_honors_cancellation_after_final_read_before_replace(self):
        destination = os.path.join(tempfile.gettempdir(), "native-client-cancelled.gcode")
        with open(destination, "wb") as stream:
            stream.write(b"existing")

        class _Response(object):
            headers = {"Content-Length": "6"}

            def __init__(self):
                self._reads = [b"G1 X1\n", b""]

            def read(self, size):
                return self._reads.pop(0)

            def close(self):
                pass

        class _CancelAfterEOF(object):
            def __init__(self):
                self.checks = 0

            def is_set(self):
                self.checks += 1
                # before first read, before EOF read, then immediately before
                # os.replace: cancellation must win at that last fence.
                return self.checks >= 3

        cancel_event = _CancelAfterEOF()
        try:
            with patch.object(self.client, "_request_raw", return_value=_Response()):
                with self.assertRaises(NativeClientError):
                    self.client.download("result-1", destination, cancel_event)
            with open(destination, "rb") as stream:
                self.assertEqual(stream.read(), b"existing")
        finally:
            try:
                os.remove(destination)
            except OSError:
                pass

    def test_timeout_is_reported_as_native_error(self):
        _NativeHandler.delay = 1
        with self.assertRaises(NativeClientError):
            self.client.state()

    def test_http_error_does_not_echo_session_token(self):
        _NativeHandler.reject = True
        _NativeHandler.reject_body = b'{"error":"detail session-secret secret-api-key"}'
        with self.assertRaises(NativeClientError) as raised:
            self.client.settings(api_key="secret-api-key")
        self.assertNotIn("session-secret", str(raised.exception))
        self.assertNotIn("secret-api-key", str(raised.exception))
        self.assertIn("detail [SESSION HIDDEN] [API KEY HIDDEN]", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
