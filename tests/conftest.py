"""Shared fixtures: an isolated settings object, a fixture corpus indexed into a temp SQLite
file with the local embedder, and the rule-based FakeLLM. No network, no keys."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.rag.config import Settings
from app.rag.embeddings import CachedQueryEmbedder, LocalHashEmbedder
from app.rag.ingest import Ingester
from app.rag.llm import FakeLLM
from app.rag.pipeline import RagPipeline
from app.rag.retrieve import Retriever
from app.rag.store import ChunkStore

FIXTURES = Path(__file__).parent / "fixtures" / "corpus"


@pytest.fixture(scope="session")
def settings(tmp_path_factory) -> Settings:
    root = tmp_path_factory.mktemp("repo")
    shutil.copytree(FIXTURES, root / "corpus")
    return Settings(
        _env_file=None,
        corpus_dir=root / "corpus", sources_file=root / "corpus" / "sources.yaml", db_path=root / "data" / "test.sqlite3",
        current_cycle="2026-LR", embedding_provider="local", llm_provider="fake",
        rag_api_keys="client-key", rag_admin_api_keys="admin-key", rate_limit_per_minute=1000,
        allow_fake_llm_in_api=True,
    )


@pytest.fixture(scope="session")
def store(settings) -> ChunkStore:
    st = ChunkStore(settings.db_path)
    emb = CachedQueryEmbedder(LocalHashEmbedder())
    report = Ingester(st, emb, settings).run()
    assert all(r["status"] == "ingested" for r in report), report
    return st


@pytest.fixture(scope="session")
def pipeline(settings, store) -> RagPipeline:
    emb = CachedQueryEmbedder(LocalHashEmbedder())
    return RagPipeline(store, Retriever(store, emb, settings), FakeLLM(), settings, Ingester(store, emb, settings))
