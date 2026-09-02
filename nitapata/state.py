"""Conversation state — the ONLY per-farmer state, and it is not a database (SPEC §13).

Keyed by the HMAC hash of the phone number (never the number), holds a closed
shape, expires after CLARIFY_TTL_SECONDS. In-memory here; the Worker uses KV
with the same shape and TTL.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from nitapata.constants import CLARIFY_TTL_SECONDS, DECLARED_FLAGS


@dataclass
class ConvState:
    intent_last: str | None = None
    resolved: dict = field(default_factory=lambda: {"county": None, "depot": None, "cycle": None})
    declared: dict = field(default_factory=dict)  # DeclaredFlag -> bool
    language_pin: str | None = None
    clarify_used: bool = False
    expires_at: float = 0.0

    def merge_declared(self, flags: dict) -> None:
        for k, v in flags.items():
            if k in DECLARED_FLAGS and isinstance(v, bool):
                self.declared[k] = v  # latest declaration wins

    def public(self) -> dict:
        d = asdict(self)
        d["expires_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.expires_at))
        return d


_store: dict[str, ConvState] = {}


def get(from_hash: str) -> ConvState:
    st = _store.get(from_hash)
    if st is None or st.expires_at < time.time():
        st = ConvState()
        _store.pop(from_hash, None)
    return st


def put(from_hash: str, st: ConvState) -> None:
    st.expires_at = time.time() + CLARIFY_TTL_SECONDS
    _store[from_hash] = st


def wipe(from_hash: str) -> None:
    _store.pop(from_hash, None)


def reset_all() -> None:  # tests only
    _store.clear()
