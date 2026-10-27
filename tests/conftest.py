"""A fake requests.Session so tests never touch the network."""

import json

import pytest

from lipa_mpesa import MpesaClient, MpesaConfig, SANDBOX_PASSKEY, SANDBOX_SHORTCODE


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Records requests and replies from queues keyed by URL path fragment."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def add(self, fragment, *responses):
        self.routes.setdefault(fragment, []).extend(responses)

    def _reply(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        for fragment, queue in self.routes.items():
            if fragment in url and queue:
                resp = queue.pop(0) if len(queue) > 1 else queue[0]
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"No fake response for {method} {url}")

    def get(self, url, **kwargs):
        return self._reply("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._reply("POST", url, **kwargs)

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


TOKEN_OK = FakeResponse(200, {"access_token": "tok-123", "expires_in": "3599"})


@pytest.fixture
def session():
    s = FakeSession()
    s.add("/oauth/", TOKEN_OK)
    return s


@pytest.fixture
def config():
    return MpesaConfig(
        consumer_key="key",
        consumer_secret="secret",
        shortcode=SANDBOX_SHORTCODE,
        passkey=SANDBOX_PASSKEY,
        callback_url="https://example.com/payments/stk/callback/",
        initiator_name="testapi",
        security_credential="ENCRYPTED==",
        result_url="https://example.com/payments/result/",
        timeout_url="https://example.com/payments/timeout/",
    )


@pytest.fixture
def client(config, session):
    return MpesaClient(config, session=session)
