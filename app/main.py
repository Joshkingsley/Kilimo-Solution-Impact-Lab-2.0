"""Nitapata? — SMS webhook, judge-panel API, static judge panel, and the RAG service router.

Africa's Talking POSTs each inbound SMS to /sms/inbound; we hash the number,
run nitapata.pipeline.handle (SPEC §9.2: keywords, scope gate, state, retrieval,
generation, citation check, frozen template, GSM-7) and reply through the
Africa's Talking SDK, or log the reply in DRY_RUN. The same pipeline is exposed
as JSON at /demo/message for the judge panel (served at /judge) and the
recorded replay (/replay).

The teammate's RAG service (app/rag, see docs/RAG.md) is mounted under
/v1/rag/* behind its own API keys. It is stateless retrieve -> generate ->
citation-check; the channel layer above (state, keywords, segmentation) is
what nitapata/ provides. Wiring nitapata's steps 5–7 onto app.rag is the
next integration step; today both engines run side by side and the SMS
webhook uses the nitapata pipeline (57 tests green on the templated path).

Environment (see .env.example):
    AT_USERNAME, AT_API_KEY, AT_SENDER_ID  — Africa's Talking ("sandbox" username for testing)
    MSISDN_HMAC_SECRET                     — server-side secret for hashing phone numbers
    DRY_RUN=1                              — compute replies, do not call Africa's Talking
    ANTHROPIC_API_KEY                      — optional; enables Claude Haiku classification + generation
    NITAPATA_USE_LLM=0                     — force the templated path even when a key exists

Run:  scripts/run_sms.sh   (uvicorn app.main:app --reload --port 8000)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nitapata import constants  # noqa: E402
from nitapata.pipeline import handle  # noqa: E402
from nitapata.retrieve import index  # noqa: E402

log = logging.getLogger("nitapata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HMAC_SECRET = os.environ.get("MSISDN_HMAC_SECRET", "")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
WEB = ROOT / "web"

# --- optional RAG service (app/rag) -------------------------------------------
# Imported defensively: the SMS webhook must keep working even if the RAG's
# dependencies (numpy, pdfplumber, bs4) or configuration are missing.
try:
    from app.rag.api import router as rag_router  # /v1/rag/*, API-key protected (docs/RAG.md)
    from app.rag.pipeline import build_pipeline
    from app.rag.security import install_log_redaction

    install_log_redaction()  # SPEC §13: anything phone-number-shaped is redacted from every log line
    RAG_AVAILABLE = True
except Exception as exc:  # noqa: BLE001 — any import/config problem just disables the optional router
    log.warning("RAG service not mounted: %s", exc)
    rag_router = None
    build_pipeline = None
    RAG_AVAILABLE = False


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Build the RAG pipeline once (SQLite index, embedder, LLM client) if available."""
    app.state.pipeline = None
    if build_pipeline is not None:
        try:
            app.state.pipeline = build_pipeline()
        except Exception as exc:  # noqa: BLE001 — the SMS webhook must keep working regardless
            log.error("RAG pipeline unavailable: %s", exc)
    yield
    if getattr(app.state, "pipeline", None) is not None:
        app.state.pipeline.store.close()


app = FastAPI(title="Nitapata?", version="0.3.0", docs_url="/docs", lifespan=_lifespan)
if rag_router is not None:
    app.include_router(rag_router)


# --- helpers -----------------------------------------------------------------
def hash_msisdn(msisdn: str) -> str:
    """HMAC-SHA256 of the phone number: the ONLY identifier that reaches the pipeline, state or logs."""
    if not HMAC_SECRET:
        log.warning("MSISDN_HMAC_SECRET not set: using an insecure dev default. Set it before any public tunnel.")
    key = (HMAC_SECRET or "dev-only-insecure").encode()
    return hmac.new(key, msisdn.strip().encode(), hashlib.sha256).hexdigest()[:24]


def deliver(msisdn: str, message: str) -> dict[str, Any] | None:
    """Send the reply via Africa's Talking unless DRY_RUN. Never logs the number."""
    if DRY_RUN:
        log.info("DRY_RUN, not sending to ...%s: %r", msisdn[-3:], message[:60])
        return {"dry_run": True}
    from app import sendsms  # lazy: keeps import time free of SDK/credential side effects

    return sendsms.send_sms(msisdn, message)


# --- routes ------------------------------------------------------------------
@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return ("Nitapata? webhook. POST /sms/inbound (Africa's Talking), POST /demo/message (JSON), "
            "GET /judge, GET /health" + (", /v1/rag/* (API key)" if RAG_AVAILABLE else ""))


@app.get("/health")
def health() -> dict[str, Any]:
    idx = index()
    return {
        "ok": True,
        "chunks": len(idx.chunks),
        "docs": len({c["doc_id"] for c in idx.chunks}),
        "cycle": constants.CURRENT_CYCLE,
        "dry_run": DRY_RUN,
        "at_configured": bool(os.environ.get("AT_API_KEY")),
        "llm": constants.MODEL_ID if os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("NITAPATA_USE_LLM", "1") != "0" else "rules-v1",
        "rag_service": RAG_AVAILABLE,
    }


@app.post("/sms/inbound")
async def sms_inbound(request: Request) -> JSONResponse:
    """Africa's Talking inbound SMS callback. Form fields: from, to, text, date, id, linkId."""
    ctype = request.headers.get("content-type", "")
    payload = await (request.json() if "json" in ctype else request.form())
    msisdn = str(payload.get("from", "")).strip()
    text = str(payload.get("text", "")).strip()
    if not msisdn or not text:
        return JSONResponse({"error": "missing 'from' or 'text'"}, status_code=400)

    from_hash = hash_msisdn(msisdn)
    reply = handle(text, from_hash)
    log.info("in  %s | %s", from_hash[:8], text[:80])
    log.info("out %s | %s | %s", from_hash[:8], reply["outcome"], reply["reply"][:80])

    # Africa's Talking concatenates long messages itself; send the joined text once.
    delivery = deliver(msisdn, reply["reply"])
    return JSONResponse({"from_hash": from_hash, **reply, "delivery": delivery})


class DemoMessage(BaseModel):
    text: str = Field(min_length=1, max_length=480)
    sender: str = Field(default="demo-phone", max_length=64)
    seed_bad_figure: bool = False


@app.post("/demo/message")
def demo_message(msg: DemoMessage) -> dict[str, Any]:
    """Judge panel / local testing: same pipeline, no SMS sent, full diagnostics returned."""
    from_hash = hash_msisdn(msg.sender)
    return {"from_hash": from_hash, **handle(msg.text, from_hash, seed_bad_figure=msg.seed_bad_figure)}


@app.get("/judge")
def judge_panel() -> FileResponse:
    return FileResponse(WEB / "judge.html")


@app.get("/replay", response_model=None)
def replay():
    path = WEB / "recorded_run.json"
    if not path.exists():
        return JSONResponse({"error": "no recorded run; run: python3 evals/record_run.py"}, status_code=404)
    return FileResponse(path, media_type="application/json")
