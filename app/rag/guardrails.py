"""Guardrail 3 in code (SPEC §9.4): the deterministic citation check.

No model is involved. Any failure discards the draft; the caller sends the
fallback line and records `guardrail_hits`. Silently repairing a draft is forbidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.rag.llm import Draft
from app.rag.schema import DECLARED_FLAGS, Chunk

# numbers: 2,500 | 2500 | 2.5 | 50kg | 2026 | 7-step
_NUM_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:[,\s]\d{3})+|\d+(?:\.\d+)?)(?![\w.]*@)")
# a hedge counts only when it sits within two words of a number ("about 2,500", "labda KSh 2,000")
_HEDGES = re.compile(
    r"\b(probably|maybe|perhaps|approximately|approx\.?|around|about|roughly|likely|i think|"
    r"labda|inawezekana|huenda|takriban|yapata|karibu)\b\W+(?:[\w']+\W+){0,2}\d", re.I)
_RECORD_ASSERTIONS = re.compile(
    r"\b(you are (not |already )?(registered|eligible|enrolled)|you (do not |don't )?qualify|your registration (is|was)|"
    r"you have (not )?been registered|hujasajiliwa|umesajiliwa|hustahili|unastahili|ume(sha)?sajiliwa|"
    r"hauko kwenye|uko kwenye (orodha|mfumo))\b", re.I)
FLAG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "has_id": ("national id", "national identity", "identity card", "id card", "kitambulisho", " id ", " id,", " id."),
    "is_registered_kiamis": ("kiamis", "register", "usajili", "sajili"),
    "has_allocation_sms": ("sms", "allocation", "e-voucher", "voucher", "mgao", "vocha"),
    "has_ecitizen_payment": ("ecitizen", "e-citizen", "e citizen"),
}


def normalise_number(tok: str) -> str:
    return re.sub(r"[,\s]", "", tok)


def numbers_in(text: str) -> list[str]:
    return [normalise_number(m.group(1)) for m in _NUM_RE.finditer(text)]


@dataclass
class CheckResult:
    ok: bool
    hits: list[str] = field(default_factory=list)
    cited_chunk_ids: list[str] = field(default_factory=list)

    def add(self, hit: str) -> None:
        self.hits.append(hit)
        self.ok = False


def check_draft(draft: Draft, retrieved: dict[str, Chunk], declared: dict[str, bool]) -> CheckResult:
    """Validate a generated draft against the chunks step 5 actually returned."""
    res = CheckResult(ok=True)
    if draft.insufficient:
        return res                                         # nothing to check; caller sends boundary

    answer = (draft.answer or "").strip()
    if not answer:
        res.add("empty_answer")
        return res

    # 3. every cited chunk must have been retrieved
    cited = [cid for cid in dict.fromkeys(draft.answer_chunk_ids)]
    unknown = [cid for cid in cited if cid not in retrieved]
    if unknown:
        res.add("citation_not_retrieved")
    cited = [cid for cid in cited if cid in retrieved]
    if not cited:
        res.add("no_citation")
        return res
    res.cited_chunk_ids = cited
    cited_text = "\n".join(normalise_number(retrieved[c].text) for c in cited)
    cited_text_lower = cited_text.lower()

    # 1–2. every number in the draft must be a substring of a cited chunk's text
    for num in numbers_in(answer):
        if num not in cited_text:
            res.add(f"uncited_figure:{num}")

    # 5. hedges attached to a figure
    if numbers_in(answer) and _HEDGES.search(answer):
        res.add("hedged_figure")

    # secondary sources are never the sole citation for a figure (SPEC §5 Pin 2)
    if numbers_in(answer) and all(retrieved[c].authority == "secondary" for c in cited):
        res.add("secondary_only_figure")

    # 7. record assertions
    if _RECORD_ASSERTIONS.search(answer):
        res.add("record_assertion")

    # 6. requirements must be real, retrieved, and actually mentioned by their chunk
    seen_flags: set[str] = set()
    for req in draft.requirements:
        if req.flag not in DECLARED_FLAGS or req.flag in seen_flags:
            res.add(f"bad_requirement_flag:{req.flag}")
            continue
        seen_flags.add(req.flag)
        chunk = retrieved.get(req.chunk_id)
        if chunk is None:
            res.add(f"requirement_not_retrieved:{req.flag}")
            continue
        low = " " + chunk.text.lower() + " "
        if not any(k in low for k in FLAG_KEYWORDS[req.flag]):
            res.add(f"requirement_not_in_chunk:{req.flag}")
        if _RECORD_ASSERTIONS.search(req.label_en) or _RECORD_ASSERTIONS.search(req.label_sw):
            res.add("record_assertion")

    # 7b. gap is derived only from declared state — enforced when the caller builds
    #     Requirement.missing; here we make sure the draft did not smuggle it into prose
    for flag in DECLARED_FLAGS:
        if declared.get(flag) is None and re.search(rf"\b{flag}\b", answer):
            res.add("flag_leaked_into_prose")

    _ = cited_text_lower
    return res
