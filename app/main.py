"""Nitapata? — SMS webhook + judge-panel API (FastAPI).

Africa's Talking POSTs each inbound SMS here; we run the citation-locked pipeline
(currently the Day-0 rules-based stand-in in demo/nitapata_demo.py) and reply
through the Africa's Talking SMS API. The same pipeline is exposed as JSON at
/demo/message for the judge panel and for local testing.

Environment (see .env.example):
    AT_USERNAME, AT_API_KEY, AT_SENDER_ID  — Africa's Talking (username "sandbox" for testing)
    MSISDN_HMAC_SECRET                     — server-side secret for hashing phone numbers
    DRY_RUN=1                              — compute replies but do not call Africa's Talking

Run:  uvicorn app.main:app --reload --port 8000   (or scripts/run_sms.sh)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import nitapata_demo as nd

from app.rag.api import router as rag_router          # RAG service — docs/RAG.md
from app.rag.pipeline import build_pipeline
from app.rag.security import install_log_redaction

log = logging.getLogger("nitapata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HMAC_SECRET = os.environ.get("MSISDN_HMAC_SECRET", "")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

# SPEC.md §9.6 — closed keyword set. Replies are `boundary` outcome, zero citations.
HELP_LINE = (
    "Nitapata? ni huduma ya SMS inayojibu maswali ya sifa, utaratibu na bei ya mbolea "
    "ya ruzuku kutoka hati za umma pekee. Haitoi ushauri wa kilimo, mikopo au bima. "
    "Tuma swali lako na kaunti yako."
)
STOP_LINE = "Sawa. Hatuhifadhi chochote kukuhusu; mazungumzo yamefutwa."
KEYWORDS = {"HELP", "MSAADA", "STOP"}

CHUNKS = nd.load_chunks()
install_log_redaction()   # SPEC §12 — anything phone-number-shaped is redacted from every log line


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Build the RAG pipeline once (opens the SQLite index, constructs the embedder + LLM client)."""
    try:
        app.state.pipeline = build_pipeline()
    except Exception as exc:  # the SMS webhook must keep working even if the RAG is misconfigured
        log.error("RAG pipeline unavailable: %s", exc)
        app.state.pipeline = None
    yield
    if getattr(app.state, "pipeline", None) is not None:
        app.state.pipeline.store.close()


app = FastAPI(title="Nitapata?", version="0.1.0", docs_url="/docs", lifespan=_lifespan)
app.include_router(rag_router)   # /v1/rag/* — API-key protected, see docs/RAG.md


# --- helpers -----------------------------------------------------------------
def hash_msisdn(msisdn: str) -> str:
    """HMAC-SHA256 of the phone number. This hash is the ONLY identifier that ever
    reaches the pipeline, KV or logs (SPEC.md §8.2, §13)."""
    if not HMAC_SECRET:
        log.warning("MSISDN_HMAC_SECRET not set — using an insecure dev default. Set it before any public tunnel.")
    key = (HMAC_SECRET or "dev-only-insecure").encode()
    return hmac.new(key, msisdn.strip().encode(), hashlib.sha256).hexdigest()[:24]


def keyword(text: str) -> str | None:
    """Whole-message, case-insensitive match only (SPEC.md §9.6)."""
    t = text.strip().upper()
    return t if t in KEYWORDS else None


def run_pipeline(text: str, from_hash: str) -> dict[str, Any]:
    """Run one message through the pipeline and shape the reply per Interface B (§8.2)."""
    started = time.perf_counter()
    kw = keyword(text)
    if kw == "STOP":
        nd._kv.pop(nd.kv_key(from_hash), None)
        result = nd.Result("boundary", STOP_LINE, guardrail_hits=["keyword_stop"])
    elif kw in ("HELP", "MSAADA"):
        result = nd.Result("boundary", HELP_LINE, guardrail_hits=["keyword_help"])
    else:
        result = nd.handle(text, from_hash, CHUNKS)

    return {
        "outcome": result.outcome,
        "reply": result.reply,
        "segments": result.segments,
        "chars": len(result.reply),
        "citations": [
            {"doc_id": c["source"], "chunk_id": c["id"], "short_cite": c["cite"],
             "page_label": c["page"], "publish_date": c["date"]}
            for c in result.chunks
        ],
        "retrieved": [{"chunk_id": c["id"], "text": c["text"], "cycle": c["cycle"], "county": c["county"]}
                      for c in result.chunks],
        "guardrail_hits": result.guardrail_hits,
        "notes": result.notes,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        "model": "rules-v0 (Day-0 stand-in; Haiku on Day 2)",
    }


def deliver(msisdn: str, message: str) -> dict[str, Any] | None:
    """Send the reply via Africa's Talking unless DRY_RUN. Never logs the number."""
    if DRY_RUN:
        log.info("DRY_RUN — not sending to ...%s: %r", msisdn[-3:], message)
        return {"dry_run": True}
    from app import sendsms  # lazy: keeps import-time free of SDK/credential side effects
    return sendsms.send_sms(msisdn, message)


# --- routes ------------------------------------------------------------------
@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Nitapata? SMS webhook. POST /sms/inbound (Africa's Talking), POST /demo/message (JSON), GET /health"


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "chunks": len(CHUNKS), "cycle": nd.CURRENT_CYCLE, "dry_run": DRY_RUN,
            "at_configured": bool(os.environ.get("AT_API_KEY"))}


@app.post("/sms/inbound")
async def sms_inbound(request: Request) -> JSONResponse:
    """Africa's Talking inbound SMS callback. Form-encoded fields: from, to, text, date, id, linkId."""
    ctype = request.headers.get("content-type", "")
    payload = await (request.json() if "json" in ctype else request.form())
    msisdn = str(payload.get("from", "")).strip()
    text = str(payload.get("text", "")).strip()
    if not msisdn or not text:
        return JSONResponse({"error": "missing 'from' or 'text'"}, status_code=400)

    from_hash = hash_msisdn(msisdn)
    reply = run_pipeline(text, from_hash)
    log.info("in  %s | %s", from_hash[:8], text[:80])
    log.info("out %s | %s | %s", from_hash[:8], reply["outcome"], reply["reply"][:80])

    delivery = deliver(msisdn, reply["reply"])
    # Africa's Talking only needs a 2xx; the body is for our own logs / the judge panel.
    return JSONResponse({"from_hash": from_hash, **reply, "delivery": delivery})


class DemoMessage(BaseModel):
    text: str = Field(min_length=1, max_length=480)
    sender: str = Field(default="demo-phone", max_length=64)


@app.post("/demo/message")
def demo_message(msg: DemoMessage) -> dict[str, Any]:
    """Judge panel / local testing: same pipeline, no SMS sent, full diagnostics returned."""
    from_hash = hash_msisdn(msg.sender)
    return {"from_hash": from_hash, **run_pipeline(msg.text, from_hash)}
