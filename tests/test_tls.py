"""Actual TLS handshakes; the test-only key is never used outside loopback."""
import os
import json
import ssl
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.core import completion, provider_ssl_context, ProviderError


class TLSTests(unittest.TestCase):
    def setUp(self):
        folder = os.path.join(os.path.dirname(__file__), 'fixtures')
        self.cert = os.path.join(folder, 'localhost-cert.pem')
        self.received = []
        received = self.received

        class Provider(BaseHTTPRequestHandler):
            def do_POST(handler):
                handler.rfile.read(int(handler.headers['Content-Length']))
                received.append(handler.headers.get('Authorization'))
                body = json.dumps({'choices': [{'message': {'content': 'TLS verified'}}]}).encode('utf-8')
                handler.send_response(200)
                handler.send_header('Content-Length', str(len(body)))
                handler.end_headers()
                handler.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(('127.0.0.1', 0), Provider)
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        context.load_cert_chain(self.cert, os.path.join(folder, 'localhost-key.pem'))
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.worker = threading.Thread(target=self.server.serve_forever)
        self.worker.daemon = True
        self.worker.start()
        self.config = ConfigStore()
        self.config.update({'base_url': 'https://localhost:%s/v1' % self.server.server_port,
                            'api_key': 'test-only-secret'})

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(2)

    def trust_fixture(self):
        context = provider_ssl_context()
        context.load_verify_locations(cafile=self.cert)
        return context

    def test_system_context_keeps_chain_and_hostname_verification(self):
        context = provider_ssl_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)

    def test_untrusted_certificate_is_rejected_before_sending_credentials(self):
        with self.assertRaises(ProviderError):
            completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(self.received, [])

    def test_explicit_context_works_despite_host_default_override(self):
        context = self.trust_fixture()
        with patch('ssl._create_default_https_context', side_effect=RuntimeError('host override')), \
                patch('plugin.AIAssistant.agent.core.provider_ssl_context', return_value=context):
            result = completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(result['content'], 'TLS verified')
        self.assertEqual(self.received, ['Bearer test-only-secret'])

    def test_trusted_certificate_with_wrong_hostname_is_rejected(self):
        self.config.update({'base_url': 'https://127.0.0.1:%s/v1' % self.server.server_port})
        with patch('plugin.AIAssistant.agent.core.provider_ssl_context', return_value=self.trust_fixture()):
            with self.assertRaises(ProviderError):
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(self.received, [])
