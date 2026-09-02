"""Security primitives shared by the API and the ingester.

* API-key auth with constant-time comparison (fail closed when no keys are configured)
* per-key sliding-window rate limiting (in-process; put a gateway in front for multi-replica)
* a logging filter that redacts anything phone-number-shaped (SPEC §12)
* SSRF-safe URL validation for the ingester (allowlisted hosts, public IPs only)
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import re
import socket
import threading
import time
from collections import deque
from urllib.parse import urlparse

from app.rag.schema import MSISDN_RE

log = logging.getLogger("nitapata.security")


# ------------------------------------------------------------------ API keys
def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


def key_matches(presented: str | None, allowed: set[str]) -> bool:
    """Constant-time membership test. Empty allowlist => nothing matches (fail closed)."""
    if not presented or not allowed:
        return False
    d = _digest(presented)
    ok = False
    for k in allowed:               # compare against every key so timing does not leak position
        ok |= hmac.compare_digest(d, _digest(k))
    return ok


def key_fingerprint(presented: str) -> str:
    """Short, non-reversible identifier for logs and rate-limit buckets."""
    return hashlib.sha256(presented.encode("utf-8")).hexdigest()[:12]


# ------------------------------------------------------------------ rate limit
class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = max(1, limit)
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, bucket: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(bucket, deque())
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                retry = int(self.window - (now - q[0])) + 1
                return False, retry
            q.append(now)
            # opportunistic cleanup of idle buckets
            if len(self._hits) > 10_000:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]:
                    self._hits.pop(k, None)
            return True, 0


# ------------------------------------------------------------------ PII in logs
class RedactMsisdnFilter(logging.Filter):
    """SPEC §12 — logs strip phone numbers. Applied to the root logger at startup."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        if MSISDN_RE.search(msg):
            record.msg = MSISDN_RE.sub("[msisdn-redacted]", msg)
            record.args = ()
        return True


def install_log_redaction() -> None:
    root = logging.getLogger()
    if not any(isinstance(f, RedactMsisdnFilter) for f in root.filters):
        root.addFilter(RedactMsisdnFilter())
    for h in root.handlers:
        if not any(isinstance(f, RedactMsisdnFilter) for f in h.filters):
            h.addFilter(RedactMsisdnFilter())


# ------------------------------------------------------------------ SSRF guard
_HOST_RE = re.compile(r"^[a-z0-9.-]+$")


def _is_public_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


def validate_fetch_url(url: str, allowed_hosts: set[str]) -> str:
    """Return the netloc if `url` is https, on an allowlisted host, and resolves only to public IPs."""
    p = urlparse(url)
    if p.scheme != "https":
        raise ValueError(f"only https sources are allowed: {url}")
    if p.username or p.password or p.port not in (None, 443):
        raise ValueError("credentials or non-standard ports are not allowed in source URLs")
    host = (p.hostname or "").lower()
    if not host or not _HOST_RE.match(host):
        raise ValueError(f"bad host in {url}")
    bare = host[4:] if host.startswith("www.") else host
    if not any(bare == h or bare.endswith("." + h) for h in allowed_hosts):
        raise ValueError(f"host {host} is not in the source allowlist")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve {host}: {exc}") from exc
    ips = {i[4][0] for i in infos}
    if not ips or not all(_is_public_ip(ip) for ip in ips):
        raise ValueError(f"{host} resolves to a non-public address")
    return host


def strip_msisdn(text: str) -> str:
    return MSISDN_RE.sub("[msisdn-redacted]", text)
