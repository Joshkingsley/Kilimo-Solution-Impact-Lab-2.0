"""Nitapata? — SMS webhook, judge-panel API and static judge panel (FastAPI).

Africa's Talking POSTs each inbound SMS to /sms/inbound; we hash the number,
run nitapata.pipeline.handle (SPEC §9.2) and reply through the Africa's Talking
SDK, or log the reply in DRY_RUN. The same pipeline is exposed as JSON at
/demo/message for the judge panel (served at /judge) and the recorded replay
(/replay).

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
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nitapata import constants
from nitapata.pipeline import handle
from nitapata.retrieve import index

log = logging.getLogger("nitapata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

HMAC_SECRET = os.environ.get("MSISDN_HMAC_SECRET", "")
DRY_RUN = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
WEB = ROOT / "web"

app = FastAPI(title="Nitapata?", version="0.2.0", docs_url="/docs")


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


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return "Nitapata? webhook. POST /sms/inbound (Africa's Talking), POST /demo/message (JSON), GET /judge, GET /health"


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
