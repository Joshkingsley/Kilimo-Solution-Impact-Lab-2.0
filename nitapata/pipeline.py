"""The per-message pipeline (SPEC §9.2). handle() is the only way in.

Returns an OutboundReply-shaped dict (SPEC §8.2). Exactly one outcome:
cite | clarify | boundary. Never a fourth.
"""
from __future__ import annotations

import time
import uuid

from nitapata import generate, guardrails, intents, render, state
from nitapata.constants import (
    BOUNDARY_LINE,
    CLARIFY_COUNTY,
    CLARIFY_INTENT,
    CURRENT_CYCLE,
    FALLBACK_LINE,
    HELP_LINE,
    LANG_PIN_LINE,
    MODEL_ID,
    STOP_LINE,
)
from nitapata.retrieve import index

LOCATION_INTENTS = {"price", "depot_availability", "cycle_timing"}
OOS_LABELS = {
    "sw": {"credit": "mkopo", "insurance": "bima", "agronomy": "ushauri wa kilimo", "weather": "hali ya hewa",
           "personal_record": "hali ya usajili wako (uliza ofisi ya wodi)", "comparison": "kulinganisha na bei ya awali",
           "injection": "maagizo ya kubadilisha sheria", "other": "hilo"},
    "en": {"credit": "loans", "insurance": "insurance", "agronomy": "farming advice", "weather": "weather",
           "personal_record": "your registration status (ask the ward office)", "comparison": "comparison with earlier prices",
           "injection": "instructions to change the rules", "other": "that"},
}


def _reply(outcome: str, intent: str, lang: str, text: str, *, hits: list[str], st: state.ConvState,
           analysis: intents.Analysis, retrieved=None, citations=None, requirements=None, declines=None,
           started: float, notes=None, model="rules-v1") -> dict:
    segs = [{"index": 1, "of": 1, "text": render.gsm7(text)}] if isinstance(text, str) else text
    return {
        "trace_id": uuid.uuid4().hex[:12],
        "outcome": outcome,
        "intent": intent,
        "language": lang,
        "segments": segs,
        "reply": " ".join(s["text"] for s in segs),
        "citations": citations or [],
        "requirements": requirements or [],
        "declared": dict(st.declared),
        "declines": declines or [],
        "resolved": dict(st.resolved),
        "diagnostics": {
            "retrieved": retrieved or [],
            "guardrail_hits": hits,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "model": model,
            "classifier": analysis.classifier,
            "out_of_scope": analysis.out_of_scope,
            "notes": notes or [],
        },
    }


def _cite_record(c: dict) -> dict:
    return {k: c[k] for k in ("doc_id", "chunk_id", "doc_title", "short_cite", "locator", "page_label", "publish_date")}


def handle(text: str, from_hash: str, *, seed_bad_figure: bool = False) -> dict:
    started = time.perf_counter()
    st = state.get(from_hash)
    a = intents.analyse(text)
    lang = st.language_pin or a.language
    notes: list[str] = []

    # 1. keywords (whole message only)
    if a.keyword == "stop":
        state.wipe(from_hash)
        return _reply("boundary", "out_of_scope", lang, STOP_LINE[lang], hits=["keyword_stop"], st=state.ConvState(), analysis=a, started=started)
    if a.keyword == "help":
        return _reply("boundary", "out_of_scope", lang, HELP_LINE[lang], hits=["keyword_help"], st=st, analysis=a, started=started)
    if a.keyword in ("lang_en", "lang_sw"):
        st.language_pin = "en" if a.keyword == "lang_en" else "sw"
        state.put(from_hash, st)
        return _reply("boundary", "out_of_scope", st.language_pin, LANG_PIN_LINE[st.language_pin], hits=["keyword_lang"], st=st, analysis=a, started=started)

    client = generate.llm_client()
    a = intents.refine_with_llm(a, client)
    model = MODEL_ID if client else "rules-v1"

    # 4a. declared state and county merge into the 15-minute state
    st.merge_declared(a.declared)
    if a.county:
        st.resolved["county"] = a.county
    county = st.resolved["county"]

    # follow-up to a clarifying question: farmer sent a county (or short answer) only
    if not a.intents and st.intent_last and (a.county or len(a.text.split()) <= 3) and not a.out_of_scope and not a.hard_out_of_scope:
        a.intents = [st.intent_last]
        notes.append("resolved pending intent from state")

    # 2. scope gate
    if a.hard_out_of_scope or (not a.intents and a.out_of_scope):
        state.put(from_hash, st)
        return _reply("boundary", "out_of_scope", lang, BOUNDARY_LINE[lang], hits=["out_of_scope"], st=st, analysis=a, started=started, notes=notes, model=model)
    if not a.intents:
        if st.clarify_used:
            state.put(from_hash, st)
            return _reply("boundary", "out_of_scope", lang, FALLBACK_LINE[lang], hits=["not_in_corpus", "clarify_budget_spent"], st=st, analysis=a, started=started, notes=notes, model=model)
        st.clarify_used = True
        state.put(from_hash, st)
        return _reply("clarify", "out_of_scope", lang, CLARIFY_INTENT[lang], hits=[], st=st, analysis=a, started=started, notes=notes, model=model)

    primary = a.intents[0]

    # 4b. location gate: one clarifying question, once
    if LOCATION_INTENTS & set(a.intents) and not county:
        if st.clarify_used:
            state.put(from_hash, st)
            return _reply("boundary", primary, lang, FALLBACK_LINE[lang], hits=["not_in_corpus", "clarify_budget_spent"], st=st, analysis=a, started=started, notes=notes, model=model)
        st.intent_last = next(i for i in a.intents if i in LOCATION_INTENTS)
        st.clarify_used = True
        state.put(from_hash, st)
        return _reply("clarify", primary, lang, CLARIFY_COUNTY[lang], hits=[], st=st, analysis=a, started=started, notes=notes, model=model)

    # 5-6. retrieve and generate, per in-scope intent (compound asks are split)
    idx = index()
    retrieved: list[dict] = []
    answers: list[str] = []
    citation_ids: list[str] = []
    requirements: list[dict] = []
    declines: list[str] = []
    for i, intent in enumerate(a.intents[:2]):
        chunks = idx.search(a.text, intent, county, k=6)
        for c in chunks:
            if c["chunk_id"] not in {r["chunk_id"] for r in retrieved}:
                retrieved.append(c)
        draft = None
        if i == 0 and client:
            draft = generate.with_llm(client, a.text, intent, lang, chunks)
        if draft is None:
            draft = generate.template(intent, lang, chunks, county, a.place)
        if draft is None:
            notes.append(f"no draft for {intent}")
            continue
        answers.append(draft.answer)
        citation_ids += [c for c in draft.citations if c not in citation_ids]
        if draft.requirements and not requirements:
            requirements = draft.requirements
        declines += draft.declines
        if i == 0:
            model = MODEL_ID if draft.source == "haiku" else "rules-v1"

    if not answers:
        state.put(from_hash, st)
        return _reply("boundary", primary, lang, FALLBACK_LINE[lang], hits=["not_in_corpus"], st=st, analysis=a, started=started,
                      retrieved=[{"chunk_id": c["chunk_id"], "score": c["score"], "used": False, "text": c["text"]} for c in retrieved], notes=notes, model=model)

    answer = " ".join(answers)
    if seed_bad_figure:
        answer = answer.replace("2,000", "1,800").replace("2,500", "1,800")
        notes.append("seeded an uncited figure on purpose")

    # gap: only from what the farmer declared (never inferred)
    for r in requirements:
        r["missing"] = st.declared.get(r["flag"]) is False
    # declines for out-of-scope sub-asks and unsupported comparisons
    for reason in a.out_of_scope:
        label = OOS_LABELS[lang].get(reason, OOS_LABELS[lang]["other"])
        if label not in declines:
            declines.append(label)

    # 7. citation check
    hits = guardrails.citation_check(answer, citation_ids, requirements, st.declared, retrieved)
    used_ids = set(citation_ids) | {r["chunk_id"] for r in requirements}
    diag_retrieved = [{"chunk_id": c["chunk_id"], "score": c["score"], "used": c["chunk_id"] in used_ids, "text": c["text"]} for c in retrieved]
    if hits:
        notes.append("draft discarded by citation check")
        state.put(from_hash, st)
        return _reply("boundary", primary, lang, FALLBACK_LINE[lang], hits=hits, st=st, analysis=a, started=started,
                      retrieved=diag_retrieved, notes=notes, model=model)

    # 8. render + segment
    cited = [c for c in retrieved if c["chunk_id"] in citation_ids]
    segments, dropped = render.render(lang, answer, requirements, declines, cited)
    if dropped:
        notes.append("trimmed: " + ", ".join(dropped))
    if segments is None:
        state.put(from_hash, st)
        return _reply("boundary", primary, lang, FALLBACK_LINE[lang], hits=["over_budget"], st=st, analysis=a, started=started,
                      retrieved=diag_retrieved, notes=notes, model=model)

    st.intent_last = None
    st.resolved["cycle"] = CURRENT_CYCLE
    state.put(from_hash, st)
    return _reply("cite", primary, lang, segments, hits=[], st=st, analysis=a, started=started,
                  retrieved=diag_retrieved, citations=[_cite_record(c) for c in cited], requirements=requirements,
                  declines=declines, notes=notes, model=model)
