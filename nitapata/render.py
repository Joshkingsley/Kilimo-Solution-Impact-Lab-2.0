"""Step 8: the frozen template, trim order and GSM-7 segmentation (SPEC §9.5).

Order for a cited reply: ANSWER, REQUIREMENTS, GAP, DECLINES, CITATION.
Trim order when over two segments: declines -> gap -> requirements -> answer
elaboration. The figure and the citation are never dropped.
"""
from __future__ import annotations

import re

from nitapata.constants import GSM7_BASIC, GSM7_EXT, LABEL_DECLINE, LABEL_GAP, LABEL_REQ

SINGLE, CONCAT, MAX_SEGMENTS = 160, 153, 2
TRANSLIT = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "…": "...", "¶": "para", "§": "para", "→": "->", "▶": ">"}


def gsm7(text: str) -> str:
    for k, v in TRANSLIT.items():
        text = text.replace(k, v)
    return "".join(ch if ch in GSM7_BASIC or ch in GSM7_EXT else "?" for ch in text)


def gsm7_len(text: str) -> int:
    return sum(2 if ch in GSM7_EXT else 1 for ch in text)


def citation_bracket(c: dict) -> str:
    y, m, d = c["publish_date"].split("-")
    return f"[{c['short_cite']}, {c['page_label']}, {d}/{m}/{y}]"


def _components(lang: str, answer: str, requirements: list[dict], declines: list[str], cited: list[dict]) -> dict:
    parts: dict[str, str] = {"answer": answer.strip()}
    if requirements:
        labels = [r["label_sw" if lang == "sw" else "label_en"] for r in requirements]
        parts["requirements"] = LABEL_REQ[lang] + " " + " ".join(f"({i}) {l}" for i, l in enumerate(labels, 1)) + "."
    missing = [r["label_sw" if lang == "sw" else "label_en"] for r in requirements if r.get("missing")]
    if missing:
        parts["gap"] = LABEL_GAP[lang] + " " + ", ".join(missing) + "."
    if declines:
        parts["declines"] = LABEL_DECLINE[lang] + " " + "; ".join(declines) + "."
    seen, brackets = set(), []
    for c in cited:
        key = (c["doc_id"], c["page_label"])
        if key not in seen:
            seen.add(key)
            brackets.append(citation_bracket(c))
    parts["citation"] = " ".join(brackets)
    return parts


def _segment(text: str) -> list[dict] | None:
    text = gsm7(text)
    if gsm7_len(text) <= SINGLE:
        return [{"index": 1, "of": 1, "text": text}]
    words, segs, cur = text.split(" "), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if gsm7_len(trial) + 4 <= CONCAT:  # room for the "1/2 " marker
            cur = trial
        else:
            segs.append(cur)
            cur = w
    if cur:
        segs.append(cur)
    if len(segs) > MAX_SEGMENTS:
        return None
    return [{"index": i, "of": len(segs), "text": f"{i}/{len(segs)} {s}"} for i, s in enumerate(segs, 1)]


def render(lang: str, answer: str, requirements: list[dict], declines: list[str], cited: list[dict]) -> tuple[list[dict] | None, list[str]]:
    """Returns (segments, dropped_components). segments is None when even answer+citation overflow."""
    parts = _components(lang, answer, requirements, declines, cited)
    order = ["answer", "requirements", "gap", "declines", "citation"]
    dropped: list[str] = []
    for drop in ("declines", "gap", "requirements", None):
        text = " ".join(parts[k] for k in order if k in parts)
        segs = _segment(text)
        if segs:
            return segs, dropped
        if drop and drop in parts:
            parts.pop(drop)
            dropped.append(drop)
    # last resort: first sentence of the answer only
    first = re.split(r"(?<=[.!?])\s+", parts["answer"])[0]
    segs = _segment(f"{first} {parts['citation']}")
    if segs:
        dropped.append("elaboration")
    return segs, dropped
