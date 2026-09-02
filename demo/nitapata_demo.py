#!/usr/bin/env python3
"""Nitapata? offline replay — runs the real pipeline (nitapata/) against corpus/chunks.jsonl.

Usage:
    python3 demo/nitapata_demo.py                    # replay the demo script
    python3 demo/nitapata_demo.py --seed-bad-figure  # prove the citation check fires
    python3 demo/nitapata_demo.py -m "bei ya mbolea Kakamega?"
    NITAPATA_USE_LLM=0 python3 demo/nitapata_demo.py # force the templated path
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nitapata import state
from nitapata.pipeline import handle
from nitapata.retrieve import index

SCRIPT = [
    ("Nafula", "Bei ya mbolea msimu huu ni ngapi?"),                      # clarify (no county)
    ("Nafula", "Kakamega"),                                                # cite price via state
    ("Nafula", "Nahitaji nini ili nipate mbolea ya ruzuku?"),             # cite eligibility + requirements
    ("Nafula", "Nikienda depot nikuje na nini? Nina ID lakini sijasajiliwa"),  # cite + requirements + gap
    ("Nafula", "Depo ya Malava inanihudumia?"),                            # cite + honest limit
    ("Nafula", "Naweza pata mbolea kwa mkopo?"),                           # boundary (out_of_scope)
    ("Nafula", "Nipande mahindi lini Kakamega?"),                          # boundary (out_of_scope)
    ("Nafula", "Bei Kakamega ni ngapi na nipate bima ya mazao?"),          # compound cite + decline
    ("Nafula", "Bei Kakamega imepanda kuliko mwaka jana?"),                # cite + decline comparison
    ("Nyambura", "Kangundo depot Machakos inanihudumia?"),                 # cite four depots + limit
    ("Nyambura", "Bei Machakos ni ngapi?"),                                # boundary (not_in_corpus)
    ("Nyambura", "MSAADA"),                                                # keyword help
]


def render(who: str, msg: str, r: dict) -> None:
    d = r["diagnostics"]
    hits = f"   guardrail: {','.join(d['guardrail_hits'])}" if d["guardrail_hits"] else ""
    print(f"\n{who} > {msg}")
    print(f"  outcome : {r['outcome'].upper()} ({r['intent']}, {r['language']})   segments: {len(r['segments'])}   chars: {len(r['reply'])}{hits}")
    print(f"  reply   : {r['reply']}")
    for c in r["citations"]:
        print(f"  |- cite  {c['short_cite']}, {c['page_label']}, {c['publish_date']}  ({c['chunk_id']})")
    for n in d["notes"]:
        print(f"  '- note: {n}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--message")
    ap.add_argument("--phone", default="demo-hash-001")
    ap.add_argument("--seed-bad-figure", action="store_true", help="inject an uncited price to prove the check fires")
    a = ap.parse_args()
    idx = index()
    print(f"Nitapata? replay: {len(idx.chunks)} chunks from {len({c['doc_id'] for c in idx.chunks})} real documents")
    if a.message:
        render("you", a.message, handle(a.message, a.phone, seed_bad_figure=a.seed_bad_figure))
        return 0
    if a.seed_bad_figure:
        render("TEST", "Bei Kakamega? (seeded uncited figure)", handle("Bei Kakamega?", a.phone, seed_bad_figure=True))
        return 0
    state.reset_all()
    for who, msg in SCRIPT:
        render(who, msg, handle(msg, "hash-" + who))
    return 0


if __name__ == "__main__":
    sys.exit(main())
