"""One explicitly authorized synthetic request; no scene, documents or history."""
import configparser
import os
import time
from plugin.AIAssistant.agent.config import ConfigStore, DEFAULT_URL, DEFAULT_MODEL
from plugin.AIAssistant.agent.core import completion

settings = configparser.ConfigParser(interpolation=None)
settings.read(os.path.join(os.environ['APPDATA'], 'steslicer', '7.10', 'steslicer.cfg'), encoding='utf-8')
values = {
    'base_url': settings.get('ste_assistant', 'base_url', fallback=DEFAULT_URL),
    'model': settings.get('ste_assistant', 'model', fallback=DEFAULT_MODEL),
    'api_key': settings.get('ste_assistant', 'api_key', fallback='')}
if values['base_url'].rstrip('/') != 'https://api.timeweb.ai/v1':
    raise RuntimeError('Configured destination differs from the authorized Timeweb endpoint; no request sent.')
if not values['api_key'].strip():
    raise RuntimeError('No saved STE API key; no request sent.')
config = ConfigStore()
config.update(values)
started = time.monotonic()
reply = completion(config, [{'role': 'user', 'content': 'Ответь одним словом: OK.'}], [
    {'name': 'get_scene', 'description': 'Read the current scene when requested.',
     'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}])
answer = (reply.get('content') or '').strip()
print('PROVIDER_REPLIED; elapsed_seconds=%.2f; answer_is_OK=%s; response_chars=%s; tool_calls=%s' % (
    time.monotonic() - started, answer.rstrip('.!') == 'OK', len(answer), len(reply.get('tool_calls') or [])))
