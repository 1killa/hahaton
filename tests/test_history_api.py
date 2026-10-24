"""Persistence and HTTP checks: local fixtures, no provider network calls."""
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

from plugin.AIAssistant.agent import conversations, core
from plugin.AIAssistant.agent.config import ConfigStore
from plugin.AIAssistant.agent.conversations import ConversationStore, HistoryError
from plugin.AIAssistant.agent.server import AssistantServer
from plugin.AIAssistant.agent.tools import ToolRegistry
from tests.test_agent_runs import tool_reply
from tests.test_core import TinyAdapter


class HistoryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, 'history')
        self.config = ConfigStore()
        self.config.update({'api_key': 'test-history-key'})

    def tearDown(self):
        self.tmp.cleanup()

    def session(self):
        return core.AgentSession(self.config, ToolRegistry(TinyAdapter(), self.tmp.name), self.directory)

    def wait_idle(self, session):
        until = time.time() + 4
        while session.state()['busy'] and time.time() < until:
            time.sleep(.005)
        self.assertFalse(session.state()['busy'])
        # Completion checkpoint runs under the same operation ownership.
        with session.tools.operation_lock:
            pass

    def test_restart_keeps_document_context_and_complete_tools_without_replay(self):
        session = self.session()
        replies = iter([tool_reply(1), {'content': 'Условия прочитаны'}])
        attachment = {'id': 'expired-upload', 'name': 'ТЗ.txt', 'kind': 'document',
                      'text': 'Сохранить размеры. Нагрузка 75 Н·м.', 'text_chars': 41}
        with patch.object(core, 'completion', side_effect=lambda *args, **kwargs: next(replies)):
            session.submit('Прочитай ТЗ', [attachment])
            self.wait_idle(session)
        restarted = self.session()
        self.assertEqual(restarted.tools.adapter.calls, [])
        captured = []
        def complete(config, messages, schemas, cancel=None):
            captured.append(copy.deepcopy(messages))
            return {'content': 'Контекст сохранён'}
        with patch.object(core, 'completion', side_effect=complete):
            restarted.submit('Какая была нагрузка?')
            self.wait_idle(restarted)
        text = json.dumps(captured[0], ensure_ascii=False)
        self.assertIn('75 Н·м', text)
        self.assertEqual(len([item for item in captured[0] if item['role'] == 'tool']), 1)
        self.assertEqual(restarted.tools.adapter.calls, [])

    def test_atomic_replace_failure_preserves_file_and_ram_chats(self):
        session = self.session()
        session._messages.append({'role': 'user', 'content': 'Первый диалог'})
        session.sync_metadata()
        first = session.conversation_id()
        with open(session.history.path, 'rb') as stream:
            before = stream.read()
        with patch.object(conversations.os, 'replace', side_effect=OSError('test disk failure')):
            second = session.create_conversation()
            self.assertTrue(session.history_error)
            session._messages.append({'role': 'user', 'content': 'Второй диалог'})
            session.sync_metadata()
            session.select_conversation(first)
            self.assertEqual(session.state()['messages'][0]['content'], 'Первый диалог')
            session.select_conversation(second)
            self.assertEqual(session.state()['messages'][0]['content'], 'Второй диалог')
        with open(session.history.path, 'rb') as stream:
            self.assertEqual(stream.read(), before)

    def test_read_size_limit_precedes_json_decode(self):
        store = ConversationStore(self.directory)
        os.makedirs(self.directory)
        with open(store.path, 'wb') as stream:
            stream.write(b'x' * 130)
        with patch.object(conversations, 'MAX_FILE_BYTES', 128), patch.object(store, '_decode', side_effect=AssertionError('must not decode')):
            store.load()
        self.assertIn('64 MiB', store.history_error)

    def test_corrupt_semantic_record_is_recoverable(self):
        store = ConversationStore(self.directory)
        state = store.load()
        state['records'][0]['cursor'] = 'invalid-cursor'
        state['records'][0]['messages'] = {'not': 'an array'}
        os.makedirs(self.directory)
        raw = json.dumps(state).encode('utf-8')
        with open(store.path, 'wb') as stream:
            stream.write(raw)
        session = self.session()
        self.assertTrue(session.history_error)
        self.assertEqual(session.state()['messages'], [])
        session.create_conversation()
        with open(store.path, 'rb') as stream:
            self.assertEqual(stream.read(), raw)

    def test_nonfinite_history_is_reported_as_corruption(self):
        store = ConversationStore(self.directory)
        state = store.load()
        state['records'][0]['variants'] = [{'metric': float('nan')}]
        os.makedirs(self.directory)
        with open(store.path, 'w', encoding='utf-8') as stream:
            json.dump(state, stream)
        store.load()
        self.assertTrue(store.history_error)

    def test_serialized_tool_arguments_redact_exact_secret_fields(self):
        arguments = {'headers': {'Authorization': 'unknown-secret'}, 'upload_id': 'ordinary',
                     'nested': {'password': 'another-secret', 'tool_token': 'keep'}}
        record = {'tool_calls': [{'function': {'name': 'example', 'arguments': json.dumps(arguments)}}]}
        sanitized = conversations.sanitize_value(record, self.config)
        restored = json.loads(sanitized['tool_calls'][0]['function']['arguments'])
        self.assertNotIn('headers', restored)
        self.assertNotIn('password', restored['nested'])
        self.assertEqual(restored['upload_id'], 'ordinary')
        self.assertEqual(restored['nested']['tool_token'], 'keep')

    def test_final_checkpoint_retains_operation_ownership(self):
        session = self.session()
        locked = []
        def checkpoint(reason):
            acquired = session.tools.operation_lock.acquire(False)
            locked.append(not acquired)
            if acquired:
                session.tools.operation_lock.release()
        session._begin()
        with patch.object(session, '_checkpoint', side_effect=checkpoint):
            session._finish()
        self.assertEqual(locked, [True])

    def test_offline_public_migration_only_empty_store_and_sanitized(self):
        legacy = os.path.join(self.tmp.name, 'legacy.json')
        value = {'messages': [{'role': 'user', 'content': 'Предыдущий запрос test-history-key'}],
                 'events': [{'id': 1, 'type': 'info', 'Headers': {'Authorization': 'secret'}, 'tool_token': 'ordinary'}],
                 'cursor': 1, 'turns': [{'secret': 'must not import'}]}
        with open(legacy, 'w', encoding='utf-8') as stream:
            json.dump(value, stream)
        session = self.session()
        self.assertTrue(session.import_public_if_empty(legacy))
        self.assertEqual(session.state()['messages'][0]['content'], 'Предыдущий запрос [API KEY HIDDEN]')
        self.assertEqual(session._turns, [])
        self.assertNotIn('Headers', session.state()['events'][0])
        self.assertEqual(session.state()['events'][0]['tool_token'], 'ordinary')
        with open(session.history.path, 'rb') as stream:
            self.assertNotIn(b'test-history-key', stream.read())
        session.create_conversation()
        current = session.conversation_id()
        self.assertFalse(session.import_public_if_empty(legacy))
        self.assertEqual(session.conversation_id(), current)


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = ConfigStore()
        self.tools = ToolRegistry(TinyAdapter(), self.tmp.name)
        self.session = core.AgentSession(self.config, self.tools, os.path.join(self.tmp.name, 'history'))
        self.server = AssistantServer(self.session, self.tools, self.config, self.tmp.name)
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.daemon = True
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.cleanup_uploads()
        self.tmp.cleanup()

    def request(self, endpoint, value=None):
        headers = {'Origin': self.server.origin, 'X-AIAssistant-Token': self.server.token}
        body = None
        if value is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(value).encode('utf-8')
        try:
            with urlopen(Request(self.server.origin + endpoint, data=body, headers=headers), timeout=3) as response:
                return response.status, response.read()
        except HTTPError as error:
            return error.code, error.read()

    def test_conversation_routes_busy_and_reset_preserves_prior(self):
        first = self.session.conversation_id()
        self.session._messages.append({'role': 'user', 'content': 'Сохранённый текст'})
        self.session.sync_metadata()
        with self.tools.operation_lock:
            for endpoint, value in (('/api/conversations', {}), ('/api/conversations/select', {'id': first}), ('/api/reset', {})):
                self.assertEqual(self.request(endpoint, value)[0], 400)
        code, raw = self.request('/api/reset', {})
        self.assertEqual(code, 200)
        self.assertNotEqual(first, json.loads(raw.decode('utf-8'))['conversation_id'])
        self.assertEqual(self.request('/api/conversations/select', {'id': first})[0], 200)
        code, raw = self.request('/api/state')
        state = json.loads(raw.decode('utf-8'))
        self.assertEqual(state['api_version'], 3)
        self.assertEqual(state['messages'][0]['content'], 'Сохранённый текст')
        self.assertEqual(len(state['conversations']), 2)
        for item in state['conversations']:
            self.assertEqual(set(item), set(('id', 'title', 'created_at', 'updated_at')))

    def test_download_rejects_missing_and_outside_restored_paths(self):
        outside = os.path.join(os.path.dirname(self.tmp.name), 'outside-do-not-open.gcode')
        self.tools.outputs = [{'id': 'outside', 'filename': 'outside.gcode', 'path': outside, 'url': '/api/download/outside'},
                              {'id': 'missing', 'filename': 'missing.gcode', 'path': os.path.join(self.tmp.name, 'missing.gcode')}]
        for output in self.tools.outputs:
            self.assertEqual(self.request('/api/download/' + output['id'])[0], 400)
        state = json.loads(self.request('/api/state')[1].decode('utf-8'))
        for output in state['outputs']:
            self.assertNotIn('path', output)
            self.assertFalse(output['available'])


if __name__ == '__main__':
    unittest.main()
