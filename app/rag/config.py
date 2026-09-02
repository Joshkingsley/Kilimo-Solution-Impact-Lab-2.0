"""Runtime configuration. Everything secret comes from the environment (or .env);
nothing here is ever hard-coded at a call site."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    # --- paths -----------------------------------------------------------------
    corpus_dir: Path = REPO_ROOT / "corpus"
    sources_file: Path = REPO_ROOT / "corpus" / "sources.yaml"
    db_path: Path = REPO_ROOT / "data" / "nitapata.sqlite3"

    # --- corpus / retrieval -----------------------------------------------------
    current_cycle: str = "2026-SR"         # SPEC §7.2 — the cycle retrieval filters to
    default_county: str | None = None               # SPEC §5 Pin 1 — set once the demo county is pinned
    ingest_version: int = 1                         # bump when chunking strategy changes (SPEC §8.1)
    chunk_target_tokens: int = 300                  # SPEC §9.1 step 3 — ~200–400 tokens
    chunk_overlap_sentences: int = 1
    retrieval_top_k: int = 6
    retrieval_candidates: int = 40                  # per retriever before fusion
    retrieval_min_dense_score: float = 0.25         # cosine floor when there is no lexical hit
    retrieval_rrf_k: int = 60

    # --- embeddings -------------------------------------------------------------
    embedding_provider: Literal["local", "cloudflare", "voyage"] = "local"
    embedding_model: str | None = None              # provider default if None
    cloudflare_account_id: str | None = None
    cloudflare_api_token: str | None = None
    voyage_api_key: str | None = None

    # --- LLM --------------------------------------------------------------------
    llm_provider: Literal["anthropic", "fake"] = "anthropic"
    anthropic_api_key: str | None = None            # SDK also reads ANTHROPIC_API_KEY itself
    anthropic_model: str = "claude-haiku-4-5"       # SPEC §9.3 — pinned in config, never at call sites
    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 1
    generation_max_tokens: int = 700
    classification_max_tokens: int = 400

    # --- API security -----------------------------------------------------------
    rag_api_keys: str = ""                          # comma-separated; empty disables the service (fail closed)
    rag_admin_api_keys: str = ""                    # keys allowed to trigger ingestion
    rate_limit_per_minute: int = 60                 # per API key
    max_message_chars: int = 1000
    max_declines_returned: int = 5
    allow_fake_llm_in_api: bool = False             # guard against shipping the rule-based stub by mistake
    cors_origins: str = ""                          # comma-separated; empty = no CORS

    # --- ingestion network safety ----------------------------------------------
    fetch_timeout_seconds: float = 30.0
    fetch_max_bytes: int = 25 * 1024 * 1024
    fetch_max_redirects: int = 3
    fetch_user_agent: str = "NitapataIngest/1.0 (+public-document-corpus)"

    @field_validator("rag_api_keys", "rag_admin_api_keys", "cors_origins", mode="before")
    @classmethod
    def _strip(cls, v):  # noqa: D401
        return "" if v is None else str(v)

    @property
    def api_keys(self) -> set[str]:
        return {k.strip() for k in self.rag_api_keys.split(",") if k.strip()}

    @property
    def admin_api_keys(self) -> set[str]:
        return {k.strip() for k in self.rag_admin_api_keys.split(",") if k.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
