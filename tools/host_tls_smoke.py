"""Verify provider TLS with STE Python, without API keys or model requests."""
import ssl
from urllib.error import HTTPError
from urllib.request import HTTPSHandler, build_opener
from plugin.AIAssistant.agent.core import NoCredentialRedirect, provider_ssl_context

context = provider_ssl_context()
assert context.verify_mode == ssl.CERT_REQUIRED
assert context.check_hostname
print('OpenSSL:', ssl.OPENSSL_VERSION)
print('System trust:', context.cert_store_stats())
try:
    response = build_opener(NoCredentialRedirect(), HTTPSHandler(context=context)).open(
        'https://api.timeweb.ai/v1/models', timeout=20)
    print('Verified HTTPS; HTTP', response.status)
    response.close()
except HTTPError as error:
    if error.code != 401:
        raise
    print('Verified HTTPS; HTTP 401 expected without credentials')
print('HOST_TLS_SMOKE_PASSED')
