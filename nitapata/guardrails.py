"""Step 7: the deterministic citation check (guardrail 3, SPEC §9.4). No model involved.

Any hit discards the draft; the caller sends FALLBACK_LINE and records the hit.
Silently repairing a draft is forbidden.
"""
from __future__ import annotations

import re

FLAG_EVIDENCE = {
    "has_id": r"identity card|\bID\b|kitambulisho",
    "is_registered_kiamis": r"regist",
    "has_allocation_sms": r"\bsms\b|allocation",
    "has_ecitizen_payment": r"e-?citizen",
}
RECORD_ASSERTIONS = [
    r"\bhujasajiliwa\b", r"\bumesajiliwa\b", r"\bhauko kwenye rejista\b", r"\buko kwenye rejista\b",
    r"\byou are (not )?registered\b", r"\byour registration is\b", r"\bunastahili\b", r"\bhustahili\b",
    r"\byou (do not|don't) qualify\b", r"\byou qualify\b",
]
DECLARATION_PREFIX = r"(ulisema|you said|umesema)\W{0,3}$"
HEDGES = r"\b(probably|labda|inawezekana|huenda|maybe|approximately|roughly|kama|about)\b"


def _hay(chunks: list[dict]) -> str:
    return re.sub(r"[,\s]", "", " ".join(f"{c['text']} {c['page_label']} {c['publish_date']}" for c in chunks))


def citation_check(answer: str, citation_ids: list[str], requirements: list[dict],
                   declared: dict, retrieved: list[dict]) -> list[str]:
    hits: list[str] = []
    by_id = {c["chunk_id"]: c for c in retrieved}
    cited = [by_id[i] for i in citation_ids if i in by_id]

    # 3. every citation must be a chunk retrieval actually returned
    if any(i not in by_id for i in citation_ids) or not cited:
        hits.append("unretrieved_citation")

    # 1-2. every numeral in the answer must be a substring of a cited chunk (or its locator/date)
    hay = _hay(cited)
    for num in re.findall(r"\d[\d,]*", answer):
        if num.replace(",", "") not in hay:
            hits.append("uncited_figure")
            break

    # 5. hedges attached to a figure
    for sentence in re.split(r"(?<=[.!?])\s+", answer):
        if re.search(r"\d", sentence) and re.search(HEDGES, sentence, re.IGNORECASE):
            hits.append("hedged_figure")
            break

    # 6. requirements must come from a retrieved chunk that actually mentions them
    for r in requirements:
        c = by_id.get(r.get("chunk_id"))
        if c is None or not re.search(FLAG_EVIDENCE.get(r.get("flag", ""), r"$^"), c["text"], re.IGNORECASE):
            hits.append("invented_requirement")
            break

    # 7. never assert a record state; only echo a declaration
    for pat in RECORD_ASSERTIONS:
        for m in re.finditer(pat, answer, re.IGNORECASE):
            before = answer[max(0, m.start() - 20):m.start()]
            if not re.search(DECLARATION_PREFIX, before, re.IGNORECASE):
                hits.append("record_assertion")
                break
        if "record_assertion" in hits:
            break
    for r in requirements:
        if r.get("missing") and declared.get(r["flag"]) is not False:
            hits.append("record_assertion")
            break

    return sorted(set(hits))
