# Local TLS fixtures

`localhost-cert.pem` and `localhost-key.pem` are public, disposable test data
for `tests/test_tls.py`. The server binds only to `127.0.0.1` on an ephemeral
port. These files contain no provider API key or production certificate.
Never use this public private key for a real service.
