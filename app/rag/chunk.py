"""Page-bounded chunking (SPEC §9.1 step 3).

Target ~200–400 tokens, sentence-aligned, one sentence of overlap, never across a
page. Table rows (lines containing ' | ') are kept intact. Token counts are an
estimate (chars/4) — good enough for budgeting; the citation check never depends on it.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n{2,}")
_MIN_CHUNK_TOKENS = 40


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / 4))


@dataclass(frozen=True)
class RawChunk:
    ordinal: int
    text: str
    token_count: int
    content_hash: str
    start_unit: int = 1   # 1-based index of the first unit (sentence or paragraph) in the page


def _units(text: str) -> list[str]:
    """Split page text into sentence-ish units; table rows and headings stay whole."""
    units: list[str] = []
    for para in re.split(r"\n{2,}", text):
        para = para.strip()
        if not para:
            continue
        if " | " in para:                      # table block: one unit per row
            units.extend(r.strip() for r in para.split("\n") if r.strip())
            continue
        flat = re.sub(r"\s*\n\s*", " ", para)
        units.extend(s.strip() for s in _SENT_SPLIT.split(flat) if s and s.strip())
    return units


def _paragraphs(text: str) -> list[str]:
    return [re.sub(r"\s*\n\s*", " ", p).strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def chunk_page(text: str, *, target_tokens: int = 300, overlap_sentences: int = 1,
               unit: str = "sentence") -> list[RawChunk]:
    """unit='sentence' for paged documents; unit='paragraph' for web pages, whose citation
    locator is the paragraph number (¶N) rather than a page."""
    if unit == "paragraph":
        units = _paragraphs(text)
        overlap_sentences = 0
    else:
        units = _units(text)
    if not units:
        return []
    max_tokens = int(target_tokens * 1.35)
    chunks: list[tuple[int, list[str]]] = []          # (start_unit, units)
    cur: list[str] = []
    cur_start = 1
    cur_tokens = 0
    for idx, u in enumerate(units, start=1):
        ut = estimate_tokens(u)
        if ut > max_tokens:                    # pathological unit: hard-split by words
            words = u.split()
            step = max(1, int(len(words) * target_tokens / ut))
            if cur:
                chunks.append((cur_start, cur))
                cur, cur_tokens = [], 0
            for i in range(0, len(words), step):
                chunks.append((idx, [" ".join(words[i:i + step])]))
            cur_start = idx + 1
            continue
        if cur and cur_tokens + ut > target_tokens:
            chunks.append((cur_start, cur))
            keep = cur[-overlap_sentences:] if overlap_sentences > 0 else []
            cur = list(keep)
            cur_start = idx - len(keep)
            cur_tokens = sum(estimate_tokens(k) for k in cur)
        if not cur:
            cur_start = idx
        cur.append(u)
        cur_tokens += ut
    if cur:
        # merge a tiny tail into the previous chunk rather than emit a fragment
        if chunks and cur_tokens < _MIN_CHUNK_TOKENS and len(cur) <= 2:
            start, prev = chunks[-1]
            chunks[-1] = (start, prev + [x for x in cur if x not in prev])
        else:
            chunks.append((cur_start, cur))

    out: list[RawChunk] = []
    seen: set[str] = set()
    for start, c in chunks:
        body = "\n".join(c).strip()
        if not body:
            continue
        h = hashlib.sha256(body.encode("utf-8")).hexdigest()
        if h in seen:
            continue
        seen.add(h)
        out.append(RawChunk(ordinal=len(out) + 1, text=body, token_count=estimate_tokens(body), content_hash=h,
                            start_unit=start))
    return out
