"""HTTP surface for the RAG. Every route requires `X-API-Key`; ingestion additionally
requires an admin key. The API never accepts URLs or raw phone numbers, never stores
anything about a farmer, and never returns anything that is not derived from the corpus."""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from app.rag.config import Settings, get_settings
from app.rag.pipeline import RagPipeline
from app.rag.schema import (CHUNK_ID_RE, AnswerRequest, Chunk, IngestRequest, RagAnswer, SearchHit, SearchRequest)
from app.rag.security import SlidingWindowLimiter, key_fingerprint, key_matches

log = logging.getLogger("nitapata.api")

router = APIRouter(prefix="/v1/rag", tags=["rag"])
_limiter: SlidingWindowLimiter | None = None


def get_pipeline(request: Request) -> RagPipeline:
    p = getattr(request.app.state, "pipeline", None)
    if p is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "RAG pipeline not initialised")
    return p


def _limiter_for(settings: Settings) -> SlidingWindowLimiter:
    global _limiter
    if _limiter is None:
        _limiter = SlidingWindowLimiter(settings.rate_limit_per_minute, 60.0)
    return _limiter


def require_api_key(response: Response, x_api_key: str | None = Header(default=None),
                    settings: Settings = Depends(get_settings)) -> str:
    if not settings.api_keys:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service not configured: RAG_API_KEYS is empty")
    if not key_matches(x_api_key, settings.api_keys | settings.admin_api_keys):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing API key",
                            headers={"WWW-Authenticate": "ApiKey"})
    fp = key_fingerprint(x_api_key)
    ok, retry = _limiter_for(settings).allow(fp)
    if not ok:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate limit exceeded", headers={"Retry-After": str(retry)})
    response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
    return fp


def require_admin_key(x_api_key: str | None = Header(default=None), settings: Settings = Depends(get_settings)) -> str:
    if not key_matches(x_api_key, settings.admin_api_keys):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin API key required")
    return key_fingerprint(x_api_key)


@router.get("/health")
def health(pipeline: RagPipeline = Depends(get_pipeline), settings: Settings = Depends(get_settings)) -> dict:
    st = pipeline.store.stats()
    return {"status": "ok", "documents": st["documents"], "chunks": st["chunks"], "embedding_model": st["embedding_model"],
            "llm": pipeline.llm.name, "current_cycle": settings.current_cycle}


@router.post("/answer", response_model=RagAnswer, response_model_exclude_none=False)
def answer(req: AnswerRequest, request: Request, key_fp: str = Depends(require_api_key),
           pipeline: RagPipeline = Depends(get_pipeline), settings: Settings = Depends(get_settings)) -> RagAnswer:
    if len(req.text) > settings.max_message_chars:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, f"text exceeds {settings.max_message_chars} chars")
    if pipeline.llm.name.startswith("fake:") and not settings.allow_fake_llm_in_api:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "LLM_PROVIDER=fake is not allowed to serve /answer")
    t0 = time.perf_counter()
    try:
        ans = pipeline.answer(req)
    except Exception:
        # never leak internals; never send an unclassified reply
        trace = uuid.uuid4().hex[:16]
        log.exception("answer failed trace=%s key=%s", trace, key_fp)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"internal error, trace {trace}")
    log.info("answer trace=%s key=%s outcome=%s intent=%s hits=%s ms=%d", ans.trace_id, key_fp, ans.outcome, ans.intent,
             ",".join(ans.diagnostics.guardrail_hits) or "-", int((time.perf_counter() - t0) * 1000))
    return ans


@router.post("/search", response_model=list[SearchHit])
def search(req: SearchRequest, key_fp: str = Depends(require_api_key),
           pipeline: RagPipeline = Depends(get_pipeline)) -> list[SearchHit]:
    return pipeline.search(req.query, county=req.county, cycle=req.cycle, include_superseded=req.include_superseded,
                           top_k=req.top_k, intent=req.intent)


@router.get("/chunks/{chunk_id}", response_model=Chunk)
def get_chunk(chunk_id: str, key_fp: str = Depends(require_api_key), pipeline: RagPipeline = Depends(get_pipeline)) -> Chunk:
    if not CHUNK_ID_RE.match(chunk_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "malformed chunk_id")
    c = pipeline.store.get_chunk(chunk_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "chunk not found")
    return c


@router.get("/stats")
def stats(key_fp: str = Depends(require_api_key), pipeline: RagPipeline = Depends(get_pipeline)) -> dict:
    return pipeline.store.stats()


@router.post("/ingest")
def ingest(req: IngestRequest, admin_fp: str = Depends(require_admin_key),
           pipeline: RagPipeline = Depends(get_pipeline)) -> dict:
    log.info("ingest requested by admin=%s doc_ids=%s fetch=%s force=%s", admin_fp, req.doc_ids, req.fetch, req.force)
    try:
        report = pipeline.ingester.run(doc_ids=req.doc_ids, fetch=req.fetch, force=req.force)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    return {"report": report, "stats": pipeline.store.stats()}
