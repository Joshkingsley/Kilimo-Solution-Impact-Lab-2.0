"""Step 5: retrieval over corpus/chunks.jsonl (Interface A records).

Local lexical index: filter by intent (via the source's use_for), county and
cycle; rank by county match, current cycle, newest publish date, authority,
then term overlap. The Worker swaps this for Vectorize + D1 without changing
the returned record shape.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

from nitapata.constants import CURRENT_CYCLE

ROOT = Path(__file__).resolve().parents[1]
CHUNKS_PATH = ROOT / "corpus" / "chunks.jsonl"
AUTHORITY_RANK = {"legal_basis": 3, "primary": 3, "supporting": 2, "secondary": 1}
ANCHORS = {
    "eligibility": [("ncpb-faq-2022", "Uk.1 Q2"), ("ncpb-faq-2022", "Uk.1 Q3")],
    "registration_process": [("ncpb-faq-2022", "Uk.1 Q5"), ("ncpb-faq-2022", "Uk.1 Q6")],
    "evoucher_redemption": [("ncpb-faq-2022", "Uk.1 Q3"), ("ncpb-faq-2022", "Uk.1 Q10")],
    "depot_availability": [("ncpb-faq-2022", "Uk.1 Q7")],
}
STOP = {"na", "ya", "wa", "kwa", "ni", "je", "the", "a", "an", "of", "to", "in", "is", "are", "for", "and", "or", "i", "my", "me", "niko", "nina", "bei", "ngapi"}
# English hint terms per intent: farmer messages are Kiswahili, the documents are English.
INTENT_HINTS = {
    "price": ["ksh", "sh", "price", "bag", "50kg", "purchase", "reduction"],
    "eligibility": ["qualifies", "registered", "register", "farmer", "entitled"],
    "registration_process": ["registration", "register", "registered", "office", "ward", "free"],
    "depot_availability": ["depot", "depots", "collection", "centres", "points", "outlets", "stores", "distribution", "nearest"],
    "evoucher_redemption": ["voucher", "vouchers", "identity", "card", "person", "payment", "pesa", "register", "redeem"],
    "cycle_timing": ["season", "rains", "short", "long", "prepare", "commence"],
}


def _tokens(s: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", s.lower()) if t not in STOP and len(t) > 1]


class LocalIndex:
    def __init__(self, path: Path = CHUNKS_PATH):
        self.path = path
        self.chunks: list[dict] = []
        self._df: dict[str, int] = {}
        self.reload()

    def reload(self) -> None:
        self.chunks = [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]
        self._df = {}
        for c in self.chunks:
            for t in set(_tokens(c["text"])):
                self._df[t] = self._df.get(t, 0) + 1

    def _score(self, q: list[str], text: str) -> float:
        toks = set(_tokens(text))
        n = len(self.chunks) or 1
        return sum(math.log(1 + n / (1 + self._df.get(t, 0))) for t in q if t in toks)

    def by_id(self, chunk_id: str) -> dict | None:
        return next((c for c in self.chunks if c["chunk_id"] == chunk_id), None)

    def find(self, doc_id: str, page_label: str | None = None) -> dict | None:
        for c in self.chunks:
            if c["doc_id"] == doc_id and (page_label is None or c["page_label"] == page_label):
                return c
        return None

    def search(self, query: str, intent: str, county: str | None, k: int = 5) -> list[dict]:
        q = _tokens(query) + INTENT_HINTS.get(intent, [])
        hits = []
        for c in self.chunks:
            if intent not in c.get("use_for", []) or intent in c.get("do_not_use_for", []):
                continue
            if c["county"] not in (None, county):
                continue
            score = self._score(q, c["text"])
            hits.append((c, score))
        # County-specific and current-cycle first; then relevance; newest wins among equals (SPEC §7.2).
        hits.sort(
            key=lambda cs: (
                cs[0]["county"] == county and county is not None,
                cs[0]["cycle"] == CURRENT_CYCLE,
                round(cs[1], 1),
                cs[0]["publish_date"],
                AUTHORITY_RANK.get(cs[0]["authority"], 0),
            ),
            reverse=True,
        )
        out = [dict(c, score=round(s, 3)) for c, s in hits[:k]]
        # Anchor chunks: the national FAQ entries that enumerate requirements/process for an intent are
        # always in the retrieved set (score 0 when lexical overlap missed them), so the citation check
        # can validate a citation to them. Visible in the judge panel like any other retrieved chunk.
        have = {c["chunk_id"] for c in out}
        for doc_id, label in ANCHORS.get(intent, []):
            c = self.find(doc_id, label)
            if c and c["chunk_id"] not in have:
                out.append(dict(c, score=0.0))
        return out


_index: LocalIndex | None = None


def index() -> LocalIndex:
    global _index
    if _index is None:
        _index = LocalIndex()
    return _index
