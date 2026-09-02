import logging

import pytest

from app.rag.security import RedactMsisdnFilter, SlidingWindowLimiter, key_matches, validate_fetch_url


def test_key_matching_is_exact_and_fails_closed():
    assert key_matches("abc", {"abc", "def"})
    assert not key_matches("ab", {"abc"})
    assert not key_matches("abc", set())
    assert not key_matches(None, {"abc"})


def test_limiter():
    lim = SlidingWindowLimiter(2, 60)
    assert lim.allow("k")[0] and lim.allow("k")[0]
    ok, retry = lim.allow("k")
    assert not ok and retry >= 1
    assert lim.allow("other")[0]


def test_log_redaction():
    f = RedactMsisdnFilter()
    rec = logging.LogRecord("x", logging.INFO, "f", 1, "inbound from +254712345678 and 0722 333 444", (), None)
    f.filter(rec)
    assert "254712345678" not in rec.getMessage() and "[msisdn-redacted]" in rec.getMessage()


@pytest.mark.parametrize("url", [
    "http://ncpb.co.ke/x.pdf",                 # not https
    "https://evil.example/x.pdf",              # not allowlisted
    "https://ncpb.co.ke.evil.example/x.pdf",   # suffix trick
    "https://user:pw@ncpb.co.ke/x.pdf",        # credentials
    "https://ncpb.co.ke:8443/x.pdf",           # odd port
    "https://127.0.0.1/x.pdf",                 # loopback
])
def test_fetch_url_rejections(url):
    with pytest.raises(ValueError):
        validate_fetch_url(url, {"ncpb.co.ke", "kilimo.go.ke"})


def test_private_ip_rejected_even_when_host_allowlisted(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(ValueError):
        validate_fetch_url("https://ncpb.co.ke/x.pdf", {"ncpb.co.ke"})
