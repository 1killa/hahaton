"""Verify provider TLS with STE Python, without API keys or model requests."""
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from plugin.AIAssistant.agent.core import NoCredentialRedirect, provider_ssl_context

context = provider_ssl_context()
assert context.verify_mode == ssl.CERT_REQUIRED
assert context.check_hostname
try:
    with build_opener(NoCredentialRedirect(), HTTPSHandler(context=context)).open(
            'https://api.timeweb.ai/v1/models', timeout=20) as response:
        status = response.getcode()
except HTTPError as error:
    status = error.code
print('HTTP status:', status)
print('HOST_TLS_SMOKE_PASSED')
