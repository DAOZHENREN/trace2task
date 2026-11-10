"""Forwarded browser ports retain literal loopback, Origin and CSRF checks."""
from email.message import Message
from types import SimpleNamespace
import pytest
from trace2task.web_console import WebConsoleHandler


def handler(host, origin=None, token='test-token'):
    headers = Message()
    headers['Host'] = host
    if origin is not None:
        headers['Origin'] = origin
    headers['Content-Type'] = 'application/json'
    headers['X-Trace2Task-CSRF'] = token
    value = SimpleNamespace(headers=headers, csrf_token='test-token',
                            server=SimpleNamespace(server_address=('127.0.0.1', 8770)))
    value._require_loopback_host = lambda: WebConsoleHandler._require_loopback_host(value)
    return value


@pytest.mark.parametrize('host', ['localhost:53290', '127.0.0.1:63553', '[::1]:54321', 'localhost', '127.0.0.1:8770'])
def test_forwarded_port_can_read_and_submit_same_origin(host):
    h = handler(host, 'http://' + host)
    assert h._require_loopback_host() == host
    WebConsoleHandler._require_api_same_origin(h)


@pytest.mark.parametrize('host', ['evil.test:8770', 'localhost.evil:53290', 'localhost:0',
                                  'localhost:65536', 'user@localhost:8770', '127.0.0.1:8770/path',
                                  '192.0.2.85:8770', 'localhost:8770,evil.test'])
def test_non_loopback_or_malformed_authority_is_rejected(host):
    with pytest.raises(ValueError):
        handler(host)._require_loopback_host()


def test_duplicate_host_is_rejected():
    h = handler('localhost:53290')
    h.headers['Host'] = 'evil.test'
    with pytest.raises(ValueError):
        h._require_loopback_host()


@pytest.mark.parametrize('origin,token', [('http://localhost:8770', 'test-token'),
    ('https://evil.test', 'test-token'), ('http://localhost:53290', 'wrong-token')])
def test_forwarding_does_not_bypass_origin_or_csrf(origin, token):
    with pytest.raises(ValueError):
        WebConsoleHandler._require_api_same_origin(handler('localhost:53290', origin, token))
