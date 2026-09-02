"""Embedding providers behind one protocol.

  local       deterministic hashed n-gram vectors. No network, no model download.
              Good enough for tests, CI and offline demos when paired with BM25;
              NOT a substitute for a real multilingual model in production.
  cloudflare  Workers AI `@cf/baai/bge-m3` over REST (SPEC §11.1 default).
  voyage      Voyage AI `voyage-3` (multilingual) — the swap-in named by the Day-1 gate.

Every provider is batched, L2-normalised, and reports (model_name, dim). The store
records which model produced the index and refuses to mix models.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from functools import lru_cache
from typing import Protocol, Sequence

import httpx
import numpy as np

from app.rag.config import Settings

log = logging.getLogger("nitapata.embed")


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _normalise(m: np.ndarray) -> np.ndarray:
    m = np.asarray(m, dtype=np.float32)
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


# ------------------------------------------------------------------ local
_TOKEN_RE = re.compile(r"[a-z0-9]+", re.I)


class LocalHashEmbedder:
    """Feature-hashed word unigrams + character trigrams, log-tf weighted.

    Deterministic across processes (blake2b, not Python's salted hash()). Captures
    lexical/morphological overlap — useful for Kiswahili prefix/suffix variation
    (mbolea / mbolea-ya / mbolee) — but has no semantics; hybrid retrieval carries it.
    """

    name = "local-hash-v1"

    def __init__(self, dim: int = 768):
        self.dim = dim

    def _features(self, text: str):
        for tok in _TOKEN_RE.findall(text.lower()):
            yield "w:" + tok
            padded = f"#{tok}#"
            if len(padded) >= 3:
                for i in range(len(padded) - 2):
                    yield "c:" + padded[i:i + 3]

    def _vec(self, text: str) -> np.ndarray:
        counts: dict[str, int] = {}
        for f in self._features(text):
            counts[f] = counts.get(f, 0) + 1
        v = np.zeros(self.dim, dtype=np.float32)
        for f, c in counts.items():
            h = hashlib.blake2b(f.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            v[idx] += sign * (1.0 + math.log(c))
        return v

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _normalise(np.stack([self._vec(t) for t in texts]))

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed_documents([text])[0]


# ------------------------------------------------------------------ cloudflare
class CloudflareEmbedder:
    """Workers AI bge-m3 via REST. 1024 dims, multilingual (SPEC §11.1)."""

    dim = 1024
    BATCH = 50

    def __init__(self, account_id: str, api_token: str, model: str = "@cf/baai/bge-m3", timeout: float = 30.0):
        if not account_id or not api_token:
            raise ValueError("CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN are required for the cloudflare embedder")
        self.name = f"cloudflare:{model}"
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"
        self._client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_token}"})

    def _call(self, texts: Sequence[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = list(texts[i:i + self.BATCH])
            r = self._client.post(self._url, json={"text": batch})
            r.raise_for_status()
            body = r.json()
            if not body.get("success", False):
                raise RuntimeError(f"cloudflare embeddings failed: {body.get('errors')}")
            out.extend(body["result"]["data"])
        return _normalise(np.asarray(out, dtype=np.float32))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._call(texts) if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._call([text])[0]


# ------------------------------------------------------------------ voyage
class VoyageEmbedder:
    dim = 1024
    BATCH = 64

    def __init__(self, api_key: str, model: str = "voyage-3", timeout: float = 30.0):
        if not api_key:
            raise ValueError("VOYAGE_API_KEY is required for the voyage embedder")
        self.name = f"voyage:{model}"
        self._model = model
        self._client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})

    def _call(self, texts: Sequence[str], input_type: str) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.BATCH):
            batch = list(texts[i:i + self.BATCH])
            r = self._client.post("https://api.voyageai.com/v1/embeddings",
                                  json={"input": batch, "model": self._model, "input_type": input_type})
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            out.extend(d["embedding"] for d in data)
        return _normalise(np.asarray(out, dtype=np.float32))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self._call(texts, "document") if texts else np.zeros((0, self.dim), dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._call([text], "query")[0]


# ------------------------------------------------------------------ factory + cache
def build_embedder(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "local":
        return LocalHashEmbedder()
    if settings.embedding_provider == "cloudflare":
        return CloudflareEmbedder(settings.cloudflare_account_id or "", settings.cloudflare_api_token or "",
                                  model=settings.embedding_model or "@cf/baai/bge-m3")
    if settings.embedding_provider == "voyage":
        return VoyageEmbedder(settings.voyage_api_key or "", model=settings.embedding_model or "voyage-3")
    raise ValueError(f"unknown embedding provider {settings.embedding_provider}")


class CachedQueryEmbedder:
    """Small LRU in front of embed_query — repeated farmer phrasings are common."""

    def __init__(self, inner: EmbeddingProvider, maxsize: int = 512):
        self.inner = inner
        self.name = inner.name
        self.dim = inner.dim

        @lru_cache(maxsize=maxsize)
        def _cached(text: str) -> bytes:
            return inner.embed_query(text).astype(np.float32).tobytes()

        self._cached = _cached

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        return self.inner.embed_documents(texts)

    def embed_query(self, text: str) -> np.ndarray:
        return np.frombuffer(self._cached(text), dtype=np.float32)
