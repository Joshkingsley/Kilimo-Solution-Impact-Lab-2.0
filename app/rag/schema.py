"""Frozen interfaces (SPEC §8) as pydantic models.

Interface A — `Chunk` — is produced by ingestion and consumed by retrieval.
Interface B — `RagAnswer` — is the RAG half of `OutboundReply`: everything the
SMS renderer needs, minus segmentation (which is the channel's job, SPEC §9.5).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DocType = Literal[
    "gazette", "ministry_statement", "ncpb_notice", "ncpb_faq",
    "county_advisory", "kiamis_guide", "parliamentary_statement", "kna_report",
]
Authority = Literal["legal_basis", "primary", "supporting", "secondary"]
Lang = Literal["en", "sw"]
Outcome = Literal["cite", "clarify", "boundary"]
Intent = Literal[
    "eligibility", "registration_process", "price",
    "depot_availability", "evoucher_redemption", "cycle_timing",
]
IntentOrOOS = Literal[
    "eligibility", "registration_process", "price",
    "depot_availability", "evoucher_redemption", "cycle_timing", "out_of_scope",
]
DeclaredFlag = Literal["has_id", "is_registered_kiamis", "has_allocation_sms", "has_ecitizen_payment"]
DECLARED_FLAGS: tuple[str, ...] = ("has_id", "is_registered_kiamis", "has_allocation_sms", "has_ecitizen_payment")
INTENTS: tuple[str, ...] = (
    "eligibility", "registration_process", "price",
    "depot_availability", "evoucher_redemption", "cycle_timing",
)

DOC_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
CHUNK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}#p\d{1,4}#\d{1,4}$")
# Anything that looks like a phone number. Guardrail 4: never accept or emit one.
MSISDN_RE = re.compile(r"(?<!\d)(?:\+?254|0)\s?[17]\d{2}\s?\d{3}\s?\d{3}(?!\d)")
FROM_HASH_RE = re.compile(r"^[a-f0-9]{64}$")


# --------------------------------------------------------------------------- A
class Chunk(BaseModel):
    """Interface A (SPEC §8.1). `page` and `doc_title` are non-null by construction."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    doc_id: str
    doc_title: str = Field(min_length=1)
    publisher: str
    doc_type: DocType
    authority: Authority
    page: int = Field(ge=1)
    page_label: str = Field(min_length=1)
    publish_date: date
    cycle: str | None
    county: str | None
    lang: Lang
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    source_url: str
    retrieved_at: str
    ingest_version: int
    ocr: bool = False
    short_cite: str | None = None   # how the SMS names the document; full title stays in doc_title

    @field_validator("chunk_id")
    @classmethod
    def _chunk_id_shape(cls, v: str) -> str:
        if not CHUNK_ID_RE.match(v):
            raise ValueError(f"chunk_id must look like doc-id#p3#2, got {v!r}")
        return v

    @field_validator("doc_id")
    @classmethod
    def _doc_id_shape(cls, v: str) -> str:
        if not DOC_ID_RE.match(v):
            raise ValueError(f"doc_id must be a lowercase slug, got {v!r}")
        return v

    @model_validator(mode="after")
    def _id_matches_page(self):
        if not self.chunk_id.startswith(f"{self.doc_id}#p{self.page}#"):
            raise ValueError("chunk_id must encode doc_id and page")
        return self


class Citation(BaseModel):
    doc_id: str
    chunk_id: str
    doc_title: str
    page: int
    page_label: str
    publish_date: date
    authority: Authority
    short_cite: str | None = None


class Requirement(BaseModel):
    flag: DeclaredFlag
    label_en: str = Field(min_length=1, max_length=60)
    label_sw: str = Field(min_length=1, max_length=60)
    chunk_id: str
    missing: bool = False


class RetrievedRef(BaseModel):
    chunk_id: str
    score: float
    used: bool
    doc_title: str
    page_label: str
    publish_date: date
    authority: Authority
    lexical_rank: int | None = None
    dense_rank: int | None = None


class Resolved(BaseModel):
    county: str | None = None
    depot: str | None = None
    cycle: str | None = None


class Diagnostics(BaseModel):
    retrieved: list[RetrievedRef] = Field(default_factory=list)
    guardrail_hits: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    model: str = ""
    embedding_model: str = ""
    cache_read_input_tokens: int | None = None
    sub_asks: list[str] = Field(default_factory=list)
    retrieval_queries: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- B
class AnswerRequest(BaseModel):
    """Inbound half of Interface B (SPEC §8.2), plus the per-thread context the
    stateless RAG cannot hold itself (declared state, clarify budget, pin)."""

    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1)
    from_hash: str | None = Field(default=None, description="HMAC-SHA256 hex of the MSISDN. Never the raw number.")
    channel: Literal["sms", "eval", "demo", "api"] = "api"
    received_at: str | None = None
    # per-thread context supplied by the channel layer (KV), never stored here
    declared: dict[DeclaredFlag, bool] = Field(default_factory=dict)
    county: str | None = Field(default=None, max_length=40)
    depot: str | None = Field(default=None, max_length=60)
    clarify_used: bool = False
    language_pin: Lang | None = None
    include_superseded: bool = Field(default=False, description="Debug/judge-panel only: also search past cycles.")

    @field_validator("text")
    @classmethod
    def _no_msisdn_in_text_ok_but_bounded(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("text is empty")
        return v

    @field_validator("from_hash")
    @classmethod
    def _from_hash_is_hash(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if MSISDN_RE.search(v) or not FROM_HASH_RE.match(v):
            raise ValueError("from_hash must be a 64-hex HMAC digest, never a phone number")
        return v

    @field_validator("received_at")
    @classmethod
    def _received_at_no_pii(cls, v: str | None) -> str | None:
        if v is not None and MSISDN_RE.search(v):
            raise ValueError("received_at contains a phone-number-like value")
        return v


class RagAnswer(BaseModel):
    """Outbound half of Interface B minus `segments` (SPEC §8.2)."""

    trace_id: str
    outcome: Outcome
    boundary_kind: Literal["out_of_scope", "not_in_documents", "guardrail_fallback", "error"] | None = None
    intent: IntentOrOOS
    language: Lang
    text: str = Field(description="Answer sentence (cite), the question (clarify) or the fixed constant (boundary).")
    rendered_text: str = Field(description="SPEC §9.5 component order rendered as plain text. Not GSM-7 segmented.")
    citations: list[Citation] = Field(default_factory=list)
    declines: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    resolved: Resolved = Field(default_factory=Resolved)
    declared: dict[DeclaredFlag, bool] = Field(default_factory=dict)
    diagnostics: Diagnostics = Field(default_factory=Diagnostics)

    @model_validator(mode="after")
    def _invariants(self):
        # SPEC §8.2 invariants, enforced in code.
        if self.outcome == "cite" and not self.citations:
            raise ValueError("cite without citations")
        if self.outcome != "cite" and (self.citations or self.requirements):
            raise ValueError("components only live inside cited answers")
        retrieved_used = {r.chunk_id for r in self.diagnostics.retrieved if r.used}
        for req in self.requirements:
            if req.chunk_id not in retrieved_used:
                raise ValueError(f"requirement {req.flag} cites chunk not marked used")
            if req.missing and self.declared.get(req.flag) is not False:
                raise ValueError("missing requirement without declared=false")
        for c in self.citations:
            if c.chunk_id not in retrieved_used:
                raise ValueError("citation to a chunk that was not retrieved/used")
        blob = self.model_dump_json()
        if MSISDN_RE.search(blob):
            raise ValueError("reply contains a phone-number-like value")
        return self


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=500)
    county: str | None = Field(default=None, max_length=40)
    cycle: str | None = Field(default=None, max_length=40)
    include_superseded: bool = False
    top_k: int = Field(default=6, ge=1, le=20)
    intent: Intent | None = Field(default=None, description="Applies the register's use_for/do_not_use_for hints for this intent.")


class SearchHit(BaseModel):
    chunk: Chunk
    score: float
    lexical_rank: int | None
    dense_rank: int | None


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_ids: list[str] | None = Field(default=None, description="Subset of sources.yaml doc_ids; None = all.")
    fetch: bool = Field(default=False, description="Also download/refresh snapshots (network).")
    force: bool = False

    @field_validator("doc_ids")
    @classmethod
    def _slugs(cls, v):
        if v is None:
            return v
        bad = [d for d in v if not DOC_ID_RE.match(d)]
        if bad:
            raise ValueError(f"invalid doc_ids: {bad}")
        return v
