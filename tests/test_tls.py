"""Actual TLS handshakes; the test-only key is never used outside loopback."""
import os
import json
import hashlib
import ssl
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent import core
from plugin.AIAssistant.agent.core import completion, provider_ssl_context, ProviderError


class TLSTests(unittest.TestCase):
    def empty_client_context(self, require_transport=False):
        context = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        if context.cert_store_stats()['x509_ca'] == 0 or require_transport:
            return context
        # Some runtimes preload trust into every public SSLContext. The empty
        # store case is verified on STE's Python rather than private SSL APIs.
        self.skipTest('runtime preloads CA roots; empty store is tested on STE Python')

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
        context = self.empty_client_context(require_transport=True)
        with patch.object(core, 'CA_BUNDLE_PATH', self.cert), \
                patch('plugin.AIAssistant.agent.core.ssl.create_default_context', return_value=context):
            with self.assertRaises(ProviderError) as raised:
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertIn('сертифик', str(raised.exception).lower())
        self.assertIn('прокс', str(raised.exception).lower())
        self.assertEqual(self.received, [])

    def test_provider_context_adds_module_relative_bundle(self):
        class RecordingContext:
            verify_mode = ssl.CERT_NONE
            check_hostname = False

            def load_verify_locations(self, cafile=None, *args, **kwargs):
                self.loaded = cafile

        original = RecordingContext()
        with patch('plugin.AIAssistant.agent.core.ssl.create_default_context', return_value=original) as factory:
            context = provider_ssl_context()
        self.assertIs(context, original)
        factory.assert_called_once_with(purpose=ssl.Purpose.SERVER_AUTH)
        self.assertEqual(context.loaded, core.CA_BUNDLE_PATH)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertTrue(os.path.isabs(core.CA_BUNDLE_PATH))
        self.assertEqual(os.path.basename(os.path.dirname(core.CA_BUNDLE_PATH)), 'resources')

    def test_context_keeps_platform_roots_when_bundle_is_added(self):
        baseline = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        baseline_roots = set(baseline.get_ca_certs(binary_form=True))
        context = provider_ssl_context()
        augmented_roots = set(context.get_ca_certs(binary_form=True))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertTrue(baseline_roots)
        self.assertTrue(baseline_roots.issubset(augmented_roots))
        self.assertGreater(len(augmented_roots), 0)

    def test_bundle_can_be_the_only_trust_source_when_platform_roots_are_empty(self):
        context = self.empty_client_context()
        self.assertEqual(context.cert_store_stats()['x509_ca'], 0)
        with patch('plugin.AIAssistant.agent.core.ssl.create_default_context', return_value=context):
            result = provider_ssl_context()
        self.assertIs(result, context)
        self.assertGreater(context.cert_store_stats()['x509_ca'], 0)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_completion_uses_augmented_real_context_for_tls_handshake(self):
        context = self.empty_client_context(require_transport=True)
        initial_roots = set(context.get_ca_certs(binary_form=True))
        with patch.object(core, 'CA_BUNDLE_PATH', self.cert), \
                patch('plugin.AIAssistant.agent.core.ssl.create_default_context', return_value=context):
            result = completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(result['content'], 'TLS verified')
        self.assertEqual(self.received, ['Bearer test-only-secret'])
        self.assertGreater(len(set(context.get_ca_certs(binary_form=True))), len(initial_roots))
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_missing_bundle_fails_before_provider_request(self):
        with patch.object(core, 'CA_BUNDLE_PATH', os.path.join(tempfile.gettempdir(), 'missing-aia-bundle.pem')), \
                patch('plugin.AIAssistant.agent.core.build_opener') as build_opener:
            with self.assertRaises(ProviderError) as raised:
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertIn('довер', str(raised.exception).lower())
        self.assertIn('missing-aia-bundle.pem', str(raised.exception))
        build_opener.assert_not_called()

    def test_malformed_bundle_fails_before_provider_request(self):
        with tempfile.NamedTemporaryFile(mode='wb', delete=False) as handle:
            handle.write(b'not a certificate bundle')
            malformed = handle.name
        try:
            with patch.object(core, 'CA_BUNDLE_PATH', malformed), \
                    patch('plugin.AIAssistant.agent.core.build_opener') as build_opener:
                with self.assertRaises(ProviderError) as raised:
                    completion(self.config, [{'role': 'user', 'content': 'test'}], [])
            self.assertIn('довер', str(raised.exception).lower())
            build_opener.assert_not_called()
        finally:
            os.unlink(malformed)

    def test_certificate_verification_error_is_helpful_redacted_and_not_retried(self):
        class FailingOpener:
            def __init__(self):
                self.calls = 0

            def open(self, *args, **kwargs):
                self.calls += 1
                raise core.URLError(ssl.SSLError('[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed'))

        opener = FailingOpener()
        with patch('plugin.AIAssistant.agent.core.build_opener', return_value=opener):
            with self.assertRaises(ProviderError) as raised:
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        message = str(raised.exception).lower()
        self.assertIn('сертифик', message)
        self.assertIn('дат', message)
        self.assertIn('прокс', message)
        self.assertIn('корнев', message)
        self.assertIn('обнов', message)
        self.assertIn('автоматического повтора нет', message)
        self.assertNotIn('test-only-secret', message)
        self.assertEqual(opener.calls, 1)

    def test_direct_certificate_error_is_classified_as_tls_verification(self):
        class FailingOpener:
            def open(self, *args, **kwargs):
                raise ssl.CertificateError('hostname mismatch')

        with patch('plugin.AIAssistant.agent.core.build_opener', return_value=FailingOpener()):
            with self.assertRaises(ProviderError) as raised:
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        message = str(raised.exception).lower()
        self.assertIn('сертифик', message)
        self.assertIn('автоматического повтора нет', message)

    def test_non_certificate_network_error_is_not_mislabeled(self):
        class FailingOpener:
            def open(self, *args, **kwargs):
                raise core.URLError('connection refused')

        with patch('plugin.AIAssistant.agent.core.build_opener', return_value=FailingOpener()):
            with self.assertRaises(ProviderError) as raised:
                completion(self.config, [{'role': 'user', 'content': 'test'}], [])
        self.assertNotIn('сертифик', str(raised.exception).lower())

    def test_vendored_bundle_provenance_hash_and_license_are_recorded(self):
        with open(core.CA_BUNDLE_PATH, 'rb') as bundle:
            digest = hashlib.sha256(bundle.read()).hexdigest().upper()
        self.assertEqual(digest, '50F79F98DAC192B20DBF73F35C56F0B0C557AB267FF5199D1117CC1180B70785')
        with open(core.CA_BUNDLE_NOTICE_PATH, 'r') as notice:
            text = notice.read()
        self.assertIn('certifi 2026.10.19', text)
        self.assertIn('2026-10-24', text)
        self.assertIn(digest, text)
        self.assertIn('Mozilla Public License 2.0', text)
        with open(core.CA_BUNDLE_LICENSE_PATH, 'rb') as license_file:
            self.assertIn(b'Mozilla Public License', license_file.read())
