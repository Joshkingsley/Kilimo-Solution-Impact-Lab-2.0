#!/usr/bin/env python3
"""Nitapata? — Day 0 rules-based demo of the citation-locked pipeline.

Runs offline against corpus/chunks.jsonl (curated from real, snapshotted public
documents). No LLM yet: generation is templated so the *discipline* can be shown
today — scope gate, one clarifying question, cite-or-refuse, post-generation
citation check, GSM-7 budget. Haiku generation replaces the templates on Day 2.

Usage:
    python demo/nitapata_demo.py                 # replay demo script
    python demo/nitapata_demo.py --seed-bad-figure   # prove the citation check fires
    python demo/nitapata_demo.py -m "bei ya mbolea Kakamega?"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHUNKS = ROOT / "corpus" / "chunks.jsonl"
CURRENT_CYCLE = "2026-SR"
CLARIFY_TTL = 15 * 60

FALLBACK = "Samahani, hili si kwenye hati za umma tulizo nazo. Uliza ofisi ya kilimo ya wodi yako."
BOUNDARY = "Nitapata? hujibu tu maswali ya sifa, utaratibu na bei ya mbolea ya ruzuku. Hilo liko nje ya wigo wetu."

# --- intents -----------------------------------------------------------------
IN_SCOPE = {
    "price": r"\b(bei|pesa ngapi|ngapi|gharama|price|cost|how much|shilingi|ksh)\b",
    "eligibility": r"\b(sifa|qualify|eligib|nahitaji|ninahitaji|missing|kukosa|register|jiandikish|usajili|kitambulisho|id|nini)\b",
    "depot": r"\b(depot|depo|ghala|wapi|where|collect|chukua|kuchukua|store|kituo|inanihudumia|serve)\b",
}
OUT_OF_SCOPE = {
    "credit": r"\b(mkopo|loan|credit|deni|kukopa|lipa baadaye)\b",
    "insurance": r"\b(bima|insurance|cover)\b",
    "agronomy": r"\b(panda|kupanda|nipande|nitapanda|atapanda|alipanda|tutapanda|plant|planting|lima|mbegu gani|which seed|spray|dawa|mavuno|yield|harvest|nitavuna)\b",
    "weather": r"\b(mvua|rain|weather|hali ya hewa|forecast)\b",
    "comparison": r"\b(imepanda|imeshuka|gone up|gone down|compared|kuliko|last year|mwaka jana|ilikuwa)\b",
}
COUNTIES = {
    "kakamega": r"\b(kakamega|malava|mumias|butere|shinyalu|lurambi|matungu|likuyani|navakholo|khwisero|ikolomani|lugari)\b",
    "machakos": r"\b(machakos|kangundo|matungulu|mitaboni|masii|kathiani|mwala|yatta|athi river|mavoko)\b",
}

# --- ephemeral clarify state (the ONLY per-farmer state; hashed key, TTL) ------
_kv: dict[str, tuple[dict, float]] = {}


def kv_key(phone: str) -> str:
    return hashlib.sha256(phone.encode()).hexdigest()[:16]


def kv_get(phone: str) -> dict | None:
    item = _kv.get(kv_key(phone))
    if not item:
        return None
    val, ts = item
    if time.time() - ts > CLARIFY_TTL:
        _kv.pop(kv_key(phone), None)
        return None
    return val


def kv_put(phone: str, pending: dict) -> None:
    _kv[kv_key(phone)] = ({"pending_intent": pending}, time.time())


# --- corpus ------------------------------------------------------------------
def load_chunks() -> list[dict]:
    return [json.loads(l) for l in CHUNKS.read_text(encoding="utf-8").splitlines() if l.strip()]


def sort_key(c: dict) -> tuple:
    d = c["date"]
    parts = d.split("/")
    iso = "-".join(reversed(parts)) if len(parts) == 3 else d
    return (c["cycle"] == CURRENT_CYCLE, iso)


def retrieve(chunks: list[dict], topic: str, county: str | None) -> list[dict]:
    hits = [c for c in chunks if topic in c["topics"] and c["county"] in (None, county)]
    # county-specific and newest first
    hits.sort(key=lambda c: (c["county"] == county, *sort_key(c)), reverse=True)
    return hits


# --- pipeline ----------------------------------------------------------------
@dataclass
class Result:
    outcome: str  # cite | clarify | boundary — never a fourth (SPEC §4)
    reply: str
    chunks: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    guardrail_hits: list[str] = field(default_factory=list)  # out_of_scope | not_in_corpus | uncited_figure

    @property
    def segments(self) -> int:
        n = len(self.reply)
        return 1 if n <= 160 else math.ceil(n / 153)


def cite(c: dict) -> str:
    return f"[{c['cite']}, {c['page']}, {c['date']}]"


def detect(patterns: dict[str, str], text: str) -> list[str]:
    return [k for k, p in patterns.items() if re.search(p, text, re.IGNORECASE)]


def detect_county(text: str) -> str | None:
    for county, pat in COUNTIES.items():
        if re.search(pat, text, re.IGNORECASE):
            return county
    return None


def citation_check(reply: str, chunks: list[dict]) -> list[str]:
    """Every numeral in the reply must appear in a cited chunk. Conservative by design."""
    hay = " ".join(c["text"] + " " + c["page"] + " " + c["date"] for c in chunks)
    hay_n = re.sub(r"[,\s]", "", hay)
    missing = []
    for num in re.findall(r"\d[\d,]*", re.sub(r"\[[^\]]*\]", "", reply)):
        if num.replace(",", "") not in hay_n:
            missing.append(num)
    return missing


def answer_price(chunks: list[dict], county: str, seed_bad: bool) -> Result:
    hits = retrieve(chunks, "price", county)
    if not hits:
        return Result("boundary", FALLBACK, notes=["no current-cycle price chunk for county"], guardrail_hits=["not_in_corpus"])
    c = hits[0]
    m = re.search(r"K?Sh\.?\s?([\d,]+)", c["text"], re.IGNORECASE)
    price = "1,800" if seed_bad else m.group(1)
    reply = f"Mfuko wa 50kg wa mbolea ya ruzuku ni KSh {price} msimu huu (short rains). {cite(c)}"
    return Result("cite",reply, [c])


def answer_eligibility(chunks: list[dict], county: str | None) -> Result:
    hits = retrieve(chunks, "eligibility", county)
    reg = next((c for c in hits if c["id"] == "faq-q3"), hits[0])
    unreg = next((c for c in hits if c["id"] == "faq-q5"), None)
    reply = (
        f"Unahitaji: (1) kuwa umesajiliwa kama mkulima, (2) kitambulisho asili, (3) kufika mwenyewe. {cite(reg)}"
    )
    used = [reg]
    if unreg:
        reply += f" Kama hujasajiliwa, nenda ofisi ya kilimo ya wodi/kaunti — ni bure. {cite(unreg)}"
        used.append(unreg)
    return Result("cite",reply, used)


def answer_depot(chunks: list[dict], county: str, place: str | None) -> Result:
    hits = retrieve(chunks, "depot", county)
    local = [c for c in hits if c["county"] == county]
    if not local:
        return Result("boundary", FALLBACK, notes=["no county depot chunk"], guardrail_hits=["not_in_corpus"])
    c = local[0]
    if county == "kakamega":
        pts = next((x for x in local if x["id"] == "kkg-2026lr-points"), c)
        reply = (
            f"Kakamega ina vituo 20 vya kuchukua (16 vya kaunti + 4 vya NCPB); chukua kwenye kituo kilichoteuliwa "
            f"kilicho karibu nawe. {cite(pts)}"
        )
        if place:
            reply += f" Orodha ya vituo kwa jina ({place}) haiko kwenye hati za umma — thibitisha ofisi ya kilimo ya wodi."
        return Result("cite",reply, [pts])
    # source chunk spells the count as the word "four", not the numeral — match
    # it in the reply so the post-generation citation check (numerals only)
    # doesn't false-flag an accurate figure as uncited.
    reply = f"Machakos ina depo nne zilizotengwa za NCPB. {cite(c)}"
    if place:
        reply += f" Kama {place} ni moja yazo haiko kwenye hati za umma — thibitisha ofisi ya kilimo ya wodi."
    return Result("cite",reply, [c])


def handle(text: str, phone: str, chunks: list[dict], seed_bad: bool = False) -> Result:
    notes: list[str] = []
    county = detect_county(text)
    place_m = re.search(
        r"\b(kangundo|malava|mumias|butere|matungulu|masii|shinyalu)\b", text, re.IGNORECASE
    )
    place = place_m.group(0).title() if place_m else None

    pending = kv_get(phone)
    intents = detect(IN_SCOPE, text)
    oos = detect(OUT_OF_SCOPE, text)

    # follow-up to a clarifying question: farmer replied with a county only
    if pending and county and not intents:
        intents = [pending["pending_intent"]["intent"]]
        _kv.pop(kv_key(phone), None)
        notes.append("resolved from KV pending intent")

    if not intents and oos:
        return Result("boundary", BOUNDARY, notes=[f"out of scope: {oos}"], guardrail_hits=["out_of_scope"])
    if not intents:
        return Result("clarify", "Unauliza kuhusu bei, sifa, au depo ya mbolea ya ruzuku? Taja kaunti yako.", notes=["no intent"])

    # location gate: price and depot vary by county
    if any(i in intents for i in ("price", "depot")) and not county:
        kv_put(phone, {"intent": "price" if "price" in intents else "depot"})
        return Result("clarify", "Uko kaunti gani? (mf. Kakamega, Machakos) Bei na vituo hutegemea kaunti.", notes=["KV set, 15-min TTL"])

    parts: list[Result] = []
    if "price" in intents:
        parts.append(answer_price(chunks, county, seed_bad))
    if "eligibility" in intents:
        parts.append(answer_eligibility(chunks, county))
    if "depot" in intents and county:
        parts.append(answer_depot(chunks, county, place))

    reply = " ".join(p.reply for p in parts)
    used = [c for p in parts for c in p.chunks]
    if any(p.outcome == "boundary" for p in parts):
        hits = [h for p in parts for h in p.guardrail_hits] or ["not_in_corpus"]
        return Result("boundary", FALLBACK, used, notes, hits)

    if "comparison" in oos:
        reply += " Hatuwezi kulinganisha na bei za awali — hati zetu zinaonyesha bei ya sasa tu."
        notes.append("comparison declined explicitly")
    declined = [o for o in oos if o != "comparison"]
    if declined:
        reply += " Kuhusu " + "/".join(declined) + ": hilo liko nje ya wigo wa Nitapata?."
        notes.append(f"compound: declined {declined}")

    missing = citation_check(reply, used)
    if missing:
        return Result(
            "boundary", FALLBACK, used,
            notes + [f"CITATION CHECK FAILED: draft discarded, uncited figures {missing}"],
            ["uncited_figure"],
        )
    return Result("cite",reply, used, notes)


# --- demo script -------------------------------------------------------------
SCRIPT = [
    ("Nafula", "Bei ya mbolea msimu huu ni ngapi?"),                      # -> clarify (no county)
    ("Nafula", "Kakamega"),                                                # -> cite price via KV
    ("Nafula", "Nahitaji nini ili nipate mbolea ya ruzuku?"),             # -> cite eligibility
    ("Nafula", "Depo ya Malava inanihudumia?"),                            # -> cite + honest limit
    ("Nafula", "Naweza pata mbolea kwa mkopo?"),                           # -> boundary (out_of_scope)
    ("Nafula", "Nipande mahindi lini Kakamega?"),                          # -> boundary (out_of_scope)
    ("Nafula", "Bei Kakamega ni ngapi na nipate bima ya mazao?"),          # -> compound cite + decline
    ("Nafula", "Bei Kakamega imepanda kuliko mwaka jana?"),                # -> cite + decline comparison
    ("Nyambura", "Kangundo depot Machakos inanihudumia?"),                 # -> cite four depots + limit
    ("Nyambura", "Bei Machakos ni ngapi?"),                                # -> boundary (not_in_corpus)
]


def render(who: str, msg: str, r: Result) -> None:
    print(f"\n{who} ▶ {msg}")
    hits = f"   guardrail: {','.join(r.guardrail_hits)}" if r.guardrail_hits else ""
    print(f"  outcome : {r.outcome.upper()}   segments: {r.segments}   chars: {len(r.reply)}{hits}")
    print(f"  reply   : {r.reply}")
    for c in r.chunks:
        print(f"  ├─ chunk {c['id']}  {cite(c)}")
        print(f"  │    “{c['text'][:140]}{'…' if len(c['text']) > 140 else ''}”")
    for n in r.notes:
        print(f"  └─ note: {n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message")
    ap.add_argument("--phone", default="+254700000001")
    ap.add_argument("--seed-bad-figure", action="store_true", help="inject an uncited price to prove the check fires")
    a = ap.parse_args()
    chunks = load_chunks()
    print(f"Nitapata? Day-0 demo — {len(chunks)} chunks from {len({c['source'] for c in chunks})} real documents; cycle {CURRENT_CYCLE}")
    if a.message:
        render("you", a.message, handle(a.message, a.phone, chunks, a.seed_bad_figure))
        return 0
    if a.seed_bad_figure:
        render("TEST", "Bei Kakamega? (seeded uncited figure)", handle("Bei Kakamega?", a.phone, chunks, True))
        return 0
    for who, msg in SCRIPT:
        render(who, msg, handle(msg, "+254700" + who, chunks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
