"""Step 6: generation. Templated by default; Claude Haiku when configured.

Either path returns a Draft of structured fields, never a finished SMS. The
model never writes the citation bracket, the gap line or the ordering (SPEC
§9.3); render.py does that from metadata and state. Whatever produced the
draft, guardrails.citation_check runs on it before anything is sent.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

from nitapata.constants import DECLARED_FLAGS, FLAG_LABELS, MODEL_ID

log = logging.getLogger("nitapata.generate")


@dataclass
class Draft:
    answer: str
    citations: list[str] = field(default_factory=list)      # chunk_ids
    requirements: list[dict] = field(default_factory=list)  # {flag,label_en,label_sw,chunk_id}
    declines: list[str] = field(default_factory=list)
    source: str = "template"


def _price_in(text: str) -> str | None:
    m = re.search(r"K?[Ss]h\.?\s?([\d]{1,3},\d{3})", text)
    return m.group(1) if m else None


def _req(flag: str, chunk_id: str) -> dict:
    return {"flag": flag, "label_en": FLAG_LABELS[flag]["en"], "label_sw": FLAG_LABELS[flag]["sw"], "chunk_id": chunk_id}


def _first(chunks: list[dict], **match) -> dict | None:
    for c in chunks:
        if all(c.get(k) == v for k, v in match.items()):
            return c
    return None


# --- templated generation ----------------------------------------------------
def template(intent: str, lang: str, chunks: list[dict], county: str | None, place: str | None) -> Draft | None:
    if not chunks:
        return None
    sw = lang == "sw"
    top = chunks[0]

    if intent == "price":
        # first chunk (already ranked county/current-cycle/newest first) that carries a price
        c = next((x for x in chunks if _price_in(x["text"])), None)
        if not c:
            return None
        price = _price_in(c["text"])
        ans = (f"Mfuko wa 50kg wa mbolea ya ruzuku ni KSh {price} msimu huu." if sw
               else f"A 50kg bag of subsidised fertiliser is KSh {price} this cycle.")
        return Draft(ans, [c["chunk_id"]])

    if intent == "eligibility":
        q2 = _first(chunks, page_label="Uk.1 Q2") or top
        q3 = _first(chunks, page_label="Uk.1 Q3")
        ans = ("Anayestahili ni mkulima aliyesajiliwa ambaye jina lake liko kwenye rejista ya wakulima ya NCPB." if sw
               else "A duly registered farmer whose name is on the NCPB farmers' register qualifies.")
        d = Draft(ans, [q2["chunk_id"]])
        if q3:
            d.requirements = [_req("is_registered_kiamis", q3["chunk_id"]), _req("has_id", q3["chunk_id"])]
            d.citations.append(q3["chunk_id"])
        return d

    if intent == "registration_process":
        q5 = _first(chunks, page_label="Uk.1 Q5")
        q6 = _first(chunks, page_label="Uk.1 Q6")
        if not q5:
            return None
        ans = ("Usajili: nenda ofisi ya kilimo ya kaunti, kaunti ndogo au wodi yako." if sw
               else "Registration: visit your county, sub-county or ward agricultural office.")
        cites = [q5["chunk_id"]]
        if q6:
            ans += " Ni bure." if sw else " It is free."
            cites.append(q6["chunk_id"])
        return Draft(ans, cites)

    if intent == "depot_availability":
        d = None
        if county == "kakamega":
            c = next((x for x in chunks if "county-managed collection centres" in x["text"]), None)
            if c:
                ans = ("Kakamega ina vituo 20 vya kuchukua (16 vya kaunti na nne za NCPB); chukua kwenye kituo kilichoteuliwa kilicho karibu nawe." if sw
                       else "Kakamega has 20 collection points (16 county-run and four NCPB outlets); collect at the designated point nearest you.")
                d = Draft(ans, [c["chunk_id"]])
        elif county == "machakos":
            c = next((x for x in chunks if "four allocated depots" in x["text"]), None)
            if c:
                ans = ("Machakos ina depo nne zilizotengwa za NCPB." if sw else "Machakos has four allocated NCPB depots.")
                d = Draft(ans, [c["chunk_id"]])
        if d is None:
            q7 = _first(chunks, page_label="Uk.1 Q7")
            if not q7:
                return None
            ans = ("Mbolea ya ruzuku inapatikana kwenye depo yoyote ya NCPB iliyo karibu ndani ya kaunti uliyosajiliwa." if sw
                   else "Subsidised fertiliser is available from any nearby NCPB depot within the county where you are registered.")
            d = Draft(ans, [q7["chunk_id"]])
        if place:
            d.declines.append(f"orodha ya vituo kwa jina ({place})" if sw else f"whether {place} is on the list of points")
        return d

    if intent == "evoucher_redemption":
        q3 = _first(chunks, page_label="Uk.1 Q3")
        q10 = _first(chunks, page_label="Uk.1 Q10")
        if not q3:
            return None
        ans = "Fika mwenyewe depo ya NCPB." if sw else "Go to the NCPB depot in person."
        d = Draft(ans, [q3["chunk_id"]], [_req("is_registered_kiamis", q3["chunk_id"]), _req("has_id", q3["chunk_id"])])
        if q10:
            d.answer += (" Malipo: M-Pesa au benki, si pesa taslimu." if sw else " Payment: M-Pesa or bank, no cash.")
            d.citations.append(q10["chunk_id"])
        return d

    if intent == "cycle_timing":
        c = next((x for x in chunks if "short rains" in x["text"].lower()), None)
        if not c:
            return None
        date = c["publish_date"]
        y, m, dd = date.split("-")
        ans = (f"Msimu wa sasa ni mvua za vuli (short rains) {y}; bei ya sasa ilitangazwa {dd}/{m}/{y}." if sw
               else f"The current cycle is the {y} short rains; the current price was announced on {dd}/{m}/{y}.")
        return Draft(ans, [c["chunk_id"]])
    return None


# --- Claude Haiku generation (optional) --------------------------------------
_client = None
_client_tried = False


def llm_client():
    """Anthropic client if credentials exist and NITAPATA_USE_LLM != 0, else None."""
    global _client, _client_tried
    if _client_tried:
        return _client
    _client_tried = True
    if os.environ.get("NITAPATA_USE_LLM", "1") == "0":
        return None
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    try:
        import anthropic

        _client = anthropic.Anthropic()
    except Exception as exc:  # SDK missing or misconfigured -> templated path
        log.warning("anthropic client unavailable: %s", type(exc).__name__)
        _client = None
    return _client


_GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "refuse": {"type": "boolean"},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "flag": {"type": "string", "enum": list(DECLARED_FLAGS)},
                    "label_en": {"type": "string"},
                    "label_sw": {"type": "string"},
                    "chunk_id": {"type": "string"},
                },
                "required": ["flag", "label_en", "label_sw", "chunk_id"],
                "additionalProperties": False,
            },
        },
        "declines": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["refuse", "answer", "citations", "requirements", "declines"],
    "additionalProperties": False,
}

_GEN_SYSTEM = (
    "You draft ONE SMS answer for Nitapata?, a citation-locked assistant about Kenya's National Fertiliser "
    "Subsidy Programme. The retrieved chunks are the ONLY permitted source of fact. Rules: every number, price, "
    "count or date you state must appear verbatim in a chunk you list in `citations`; if a chunk spells a "
    "number as a word (\"four\"), use the word. Never give agronomic advice, never mention loans, insurance, "
    "buying or selling. Never assert the farmer's registration or eligibility status; you may only echo what "
    "the farmer said. `requirements` lists what the documents say the farmer must have, each with the chunk_id "
    "that states it; omit the component entirely if no chunk enumerates requirements. Put sub-claims you "
    "cannot support into `declines` as short noun phrases. If the chunks do not contain the answer, set "
    "refuse=true and leave answer empty. Do NOT write citation brackets, labels or the gap line; another step "
    "renders those. Write the answer as one or two plain sentences, under 200 characters, in the requested "
    "language, GSM-7 characters only (no curly quotes, dashes or emoji)."
)


def with_llm(client, text: str, intent: str, lang: str, chunks: list[dict]) -> Draft | None:
    if client is None or not chunks:
        return None
    try:
        ctx = "\n\n".join(f"[{c['chunk_id']}] ({c['short_cite']}, {c['page_label']}, {c['publish_date']})\n{c['text']}" for c in chunks)
        user = f"Language: {lang}\nIntent: {intent}\nFarmer SMS: {text}\n\nRetrieved chunks:\n{ctx}"
        resp = client.with_options(timeout=15.0, max_retries=1).messages.create(
            model=MODEL_ID,
            max_tokens=600,
            system=[{"type": "text", "text": _GEN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": _GEN_SCHEMA}},
        )
        data = json.loads(next(b.text for b in resp.content if b.type == "text"))
        if data["refuse"] or not data["answer"].strip():
            return None
        return Draft(data["answer"].strip(), data["citations"], data["requirements"], data["declines"], source="haiku")
    except Exception as exc:  # any API problem -> templated path
        log.warning("haiku generation failed, using template: %s", type(exc).__name__)
        return None
