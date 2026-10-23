"""F2 and F5 (security scan 2026-10-22): the host that is checked is the host that is connected
to, and the connection goes to the address that was checked (no second DNS answer)."""
from datetime import datetime, timezone

import httpcore
import pytest

from evidence_echo.sources import base

PUBLIC = "93.184.215.14"


def _resolver(table):
    """host -> list of addresses, or a list of answers given in turn (DNS rebinding)."""
    calls = []

    def resolve(host):
        calls.append(host)
        answer = table.get(host, [PUBLIC])
        return answer.pop(0) if answer and isinstance(answer[0], list) else answer
    resolve.calls = calls
    return resolve


@pytest.fixture
def connects(monkeypatch):
    """Record what the real backend would connect to, without touching the network."""
    seen = []

    def fake_connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        seen.append((host, port))
        return "stream"
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", fake_connect)
    return seen


# --- F2: the guard vets the ASCII (IDNA2008) host httpx connects to ---

@pytest.mark.parametrize("url", ["http://xn--fa-hia.attacker.example/", "http://faß.attacker.example/"])
def test_guard_vets_the_ascii_host(monkeypatch, url):
    resolve = _resolver({"xn--fa-hia.attacker.example": ["127.0.0.1"],
                         "fass.attacker.example": [PUBLIC]})
    monkeypatch.setattr(base, "resolve_host", resolve)
    with pytest.raises(base.FetchError, match="refused"):
        base.check_public_host(url)
    assert resolve.calls == ["xn--fa-hia.attacker.example"]


def test_guard_still_passes_a_public_idn(monkeypatch):
    monkeypatch.setattr(base, "resolve_host", _resolver({}))
    assert base.check_public_host("https://bücher.example/") == "xn--bcher-kva.example"


# --- F5: the connection is pinned to the checked address ---

def test_backend_connects_to_the_checked_address(monkeypatch, connects):
    monkeypatch.setattr(base, "resolve_host", _resolver({"example.com": [PUBLIC]}))
    assert base.PublicOnlyBackend().connect_tcp("example.com", 443) == "stream"
    assert connects == [(PUBLIC, 443)]


@pytest.mark.parametrize("answer", [["127.0.0.1"], ["10.0.0.1"], [PUBLIC, "192.168.1.5"],
                                    ["169.254.169.254"], ["::1"]])
def test_backend_refuses_a_non_public_answer(monkeypatch, connects, answer):
    monkeypatch.setattr(base, "resolve_host", _resolver({"rebind.example": answer}))
    with pytest.raises(httpcore.ConnectError, match="refused"):
        base.PublicOnlyBackend().connect_tcp("rebind.example", 80)
    assert connects == []


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "printer.local", "2130706433"])
def test_backend_refuses_local_hosts_without_resolving(monkeypatch, connects, host):
    monkeypatch.setattr(base, "resolve_host", lambda h: pytest.fail("resolved " + h))
    with pytest.raises(httpcore.ConnectError, match="refused"):
        base.PublicOnlyBackend().connect_tcp(host, 80)
    assert connects == []


def test_backend_tries_the_next_checked_address(monkeypatch):
    tried = []

    def connect(self, host, port, timeout=None, local_address=None, socket_options=None):
        tried.append(host)
        if host == PUBLIC:
            raise httpcore.ConnectError("unreachable")
        return "stream"
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(base, "resolve_host", _resolver({"two.example": [PUBLIC, "93.184.215.15"]}))
    assert base.PublicOnlyBackend().connect_tcp("two.example", 80) == "stream"
    assert tried == [PUBLIC, "93.184.215.15"]


def test_make_client_uses_the_public_only_backend():
    """Pins the httpx/httpcore hook: an upgrade that moves it must fail here, not silently."""
    client = base.make_client()
    assert isinstance(client._transport._pool._network_backend, base.PublicOnlyBackend)


def test_rebinding_between_check_and_connect_is_refused(monkeypatch, connects):
    """The guard sees a public answer, the connection would see 127.0.0.1: refused, no connect."""
    monkeypatch.setattr(base, "resolve_host",
                        _resolver({"rebind.example": [[PUBLIC], ["127.0.0.1"]]}))
    ctx = base.FetchContext(cutoff=datetime.now(timezone.utc), client=base.make_client(),
                            limiter=base.RateLimiter(interval=0))
    with pytest.raises(base.FetchError, match="refused"):
        base.http_get("http://rebind.example:8080/report", ctx)
    assert connects == []
