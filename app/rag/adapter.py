"""Bridge between the RAG (`RagAnswer`) and the SMS webhook's existing reply dict in
app/main.py (`run_pipeline`). Lets the webhook swap the Day-0 rules stand-in for the
RAG with a one-line change — see docs/RAG.md § Integrating with the webhook.

    from app.rag.adapter import rag_reply
    reply = rag_reply(app.state.pipeline, text, from_hash, declared=state.declared, clarify_used=state.clarify_used)
"""
from __future__ import annotations

import math
from typing import Any

from app.rag.pipeline import RagPipeline
from app.rag.schema import AnswerRequest


def segments_for(text: str) -> int:
    n = len(text)
    return 1 if n <= 160 else math.ceil(n / 153)


def rag_reply(pipeline: RagPipeline, text: str, from_hash: str, *, message_id: str = "sms", declared: dict | None = None,
              county: str | None = None, depot: str | None = None, clarify_used: bool = False,
              language_pin: str | None = None, channel: str = "sms") -> dict[str, Any]:
    """Run the RAG and shape the result like app.main.run_pipeline's return value.

    `from_hash` must already be the HMAC of the MSISDN (AnswerRequest rejects anything phone-shaped);
    pass None when the caller's hash is not 64 hex chars — the RAG does not need it.
    """
    fh = from_hash if (from_hash and len(from_hash) == 64) else None
    ans = pipeline.answer(AnswerRequest(message_id=message_id, text=text, from_hash=fh, channel=channel,  # type: ignore[arg-type]
                                        declared=declared or {}, county=county, depot=depot, clarify_used=clarify_used,
                                        language_pin=language_pin))  # type: ignore[arg-type]
    reply = ans.rendered_text
    return {
        "trace_id": ans.trace_id,
        "outcome": ans.outcome,
        "intent": ans.intent,
        "language": ans.language,
        "reply": reply,
        "segments": segments_for(reply),
        "chars": len(reply),
        "citations": [
            {"doc_id": c.doc_id, "chunk_id": c.chunk_id, "short_cite": c.short_cite or c.doc_title, "doc_title": c.doc_title,
             "page": c.page, "page_label": c.page_label, "publish_date": c.publish_date.isoformat()}
            for c in ans.citations
        ],
        "requirements": [r.model_dump() for r in ans.requirements],
        "declines": ans.declines,
        "declared": ans.declared,
        "resolved": ans.resolved.model_dump(),
        "retrieved": [
            {"chunk_id": r.chunk_id, "score": r.score, "used": r.used, "doc_title": r.doc_title, "page_label": r.page_label,
             "publish_date": r.publish_date.isoformat(),
             "text": (pipeline.store.get_chunk(r.chunk_id).text if r.used else None)}
            for r in ans.diagnostics.retrieved
        ],
        "guardrail_hits": ans.diagnostics.guardrail_hits,
        "notes": [f"boundary_kind={ans.boundary_kind}"] if ans.boundary_kind else [],
        "latency_ms": ans.diagnostics.latency_ms,
        "model": ans.diagnostics.model,
    }
