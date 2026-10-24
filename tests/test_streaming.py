"""Streaming transport and recovery with no external API calls."""
import io
import json
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch, MagicMock

from plugin.AIAssistant.agent import core
from plugin.AIAssistant.agent.config import ConfigStore
from tests import test_agent_runs as agent_runs


def frame(delta=None, finish=None):
    return ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta or {},
            'finish_reason': finish}]}, ensure_ascii=False) + '\r\n\r\n').encode('utf-8')


class Response(io.BytesIO):
    headers = {'Content-Type': 'text/event-stream; charset=utf-8'}


class StreamingTests(unittest.TestCase):
    def test_fragmented_unicode_and_interleaved_tool_arguments(self):
        parts = [b': keepalive\r\n\r\n', frame({'content': 'Проверяю '}),
                 frame({'content': 'сцену', 'tool_calls': [
                     {'index': 0, 'id': 'call_a', 'type': 'function',
                      'function': {'name': 'get_settings', 'arguments': '{"keys":'}},
                     {'index': 1, 'id': 'call_b', 'function': {'name': 'get_scene', 'arguments': '{'}}]}),
                 frame({'tool_calls': [{'index': 0, 'function': {'arguments': '["layer_height"],"scope":"global"}'}},
                                       {'index': 1, 'function': {'arguments': '}'}}]}),
                 frame(finish='tool_calls'), b'data: [DONE]\r\n\r\n']
        result = core.read_completion(Response(b''.join(parts)))
        self.assertEqual(result['content'], 'Проверяю сцену')
        self.assertEqual([c['id'] for c in result['tool_calls']], ['call_a', 'call_b'])
        self.assertEqual(json.loads(result['tool_calls'][0]['function']['arguments'])['keys'], ['layer_height'])
        self.assertEqual(json.loads(result['tool_calls'][1]['function']['arguments']), {})

    def test_truncated_stream_and_token_limit_do_not_return_partial_calls(self):
        partial = frame({'tool_calls': [{'index': 0, 'id': 'call', 'function': {
            'name': 'remove_model', 'arguments': '{"object_id":'}}]})
        for body in [partial, partial + frame(finish='length') + b'data: [DONE]\n\n',
                     partial + b'data: [DONE]\n\n']:
            with self.assertRaises(core.ProviderError):
                core.read_completion(Response(body))

    def test_active_stream_has_size_duration_and_cancellation_limits(self):
        body = frame({'content': 'hello'}) + frame(finish='stop') + b'data: [DONE]\n\n'
        with patch.object(core, 'MAX_RESPONSE_BYTES', 10):
            with self.assertRaises(core.ProviderError):
                core.read_completion(Response(body))
        with patch.object(core.time, 'monotonic', side_effect=[0, 601]):
            with self.assertRaises(core.ProviderTimeout):
                core.read_completion(Response(body))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(core.ProviderError, 'отменена'):
            core.read_completion(Response(body), cancel=cancel)

    def test_slow_stream_outlives_single_idle_timeout_without_retry(self):
        received = []
        class Provider(BaseHTTPRequestHandler):
            def do_POST(handler):
                received.append(json.loads(handler.rfile.read(int(handler.headers['Content-Length'])).decode('utf-8')))
                handler.send_response(200)
                handler.send_header('Content-Type', 'text/event-stream')
                handler.end_headers()
                # Total duration exceeds the socket idle timeout; each frame arrives within it.
                for part in [frame({'content': 'a'})] * 6 + [frame(finish='stop'), b'data: [DONE]\n\n']:
                    handler.wfile.write(part)
                    handler.wfile.flush()
                    time.sleep(0.1)
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Provider)
        worker = threading.Thread(target=server.serve_forever)
        worker.daemon = True
        worker.start()
        try:
            config = ConfigStore()
            config.update({'base_url': 'http://127.0.0.1:%s/v1' % server.server_port, 'api_key': 'fixture-key'})
            with patch.object(core, 'REQUEST_TIMEOUT_SECONDS', 0.4):
                result = core.completion(config, [{'role': 'user', 'content': 'test'}], [])
            self.assertEqual(result['content'], 'aaaaaa')
            self.assertEqual(len(received), 1)
            self.assertTrue(received[0]['stream'])
        finally:
            server.shutdown()
            server.server_close()
            worker.join(2)

    def test_timeout_classification_and_no_automatic_retry(self):
        config = ConfigStore()
        config.update({'api_key': 'fixture-key'})
        opener = MagicMock()
        opener.open.side_effect = socket.timeout('The read operation timed out')
        with patch.object(core, 'build_opener', return_value=opener):
            with self.assertRaises(core.ProviderTimeout):
                core.completion(config, [{'role': 'user', 'content': 'test'}], [])
        self.assertEqual(opener.open.call_count, 1)
        self.assertEqual(opener.open.call_args[1]['timeout'], 180)


class TimeoutRecoveryTests(unittest.TestCase):
    setUp = agent_runs.AgentRunTests.setUp
    tearDown = agent_runs.AgentRunTests.tearDown
    provider = agent_runs.AgentRunTests.provider
    finished = agent_runs.AgentRunTests.finished
    assert_complete_tool_pairs = agent_runs.AgentRunTests.assert_complete_tool_pairs

    def test_timeout_pauses_then_continue_does_not_replay_tools(self):
        def reply(n):
            if n == 1:
                return agent_runs.tool_reply(n)
            raise core.ProviderTimeout('provider idle')
        with self.provider(reply):
            self.session.submit('Prepare this part')
            state = self.finished()
        self.assertEqual(state['status'], 'paused')
        self.assertEqual(state['pause_reason'], 'provider_timeout')
        self.assertTrue(state['can_continue'])
        self.assertEqual(len(self.requests), 2)
        with self.provider(lambda n: {'content': 'Продолжено'}):
            self.session.continue_pending()
            self.finished()
        self.assertEqual(len(self.adapter.calls), 1)
        self.assert_complete_tool_pairs(self.requests[-1])
