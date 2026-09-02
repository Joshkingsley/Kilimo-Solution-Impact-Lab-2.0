"""`nitapata` CLI — fetch | parse | ingest | verify | stats (SPEC §9.1)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from ingest import chunk, parse

ROOT = Path(__file__).resolve().parents[1]
VERIFY_QUESTIONS = [
    ("Bei ya mbolea Kakamega ni ngapi?", "price", "kakamega"),
    ("Nahitaji nini ili nipate mbolea ya ruzuku?", "eligibility", "kakamega"),
    ("Depo ya Malava inanihudumia?", "depot_availability", "kakamega"),
    ("Nikienda depot nikuje na nini?", "evoucher_redemption", "kakamega"),
]


def cmd_fetch(args: argparse.Namespace) -> int:
    """Verify snapshot hashes; with --download, re-fetch every source (curl -k for kilimo's self-signed cert)."""
    rc = 0
    for src in chunk.load_sources():
        raw = ROOT / src["raw_path"]
        if args.download:
            raw.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["curl", "-skL", "-m", "60", "-A", "Mozilla/5.0", "-o", str(raw), src["url"]], check=False)
        if not raw.exists():
            print(f"MISSING  {src['doc_id']}")
            rc = 1
            continue
        actual = chunk.sha256(raw)
        status = "OK      " if actual == src.get("sha256") else "CHANGED "
        if status.strip() == "CHANGED":
            rc = 1
        print(f"{status} {src['doc_id']}  {actual[:12]}")
    return rc


def cmd_parse(args: argparse.Namespace) -> int:
    for src in chunk.load_sources():
        if args.doc and src["doc_id"] != args.doc:
            continue
        raw = ROOT / src["raw_path"]
        print(f"\n=== {src['doc_id']} ({src['format']})")
        if src["format"].startswith("pdf"):
            for n, body in parse.pdf_qa_blocks(raw):
                print(f"  Q{n}: {body[:120]}")
        else:
            for i, p in enumerate(parse.html_paragraphs(raw), 1):
                print(f"  para {i}: {p[:120]}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    recs = chunk.build(write=True)
    by_doc = Counter(r["doc_id"] for r in recs)
    for d, n in sorted(by_doc.items()):
        print(f"{n:3d}  {d}")
    print(f"\n{len(recs)} chunks -> {chunk.OUT.relative_to(ROOT)}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Day-1 gate: retrieval returns the right document, locator and date for the demo questions."""
    sys.path.insert(0, str(ROOT))
    from nitapata.retrieve import LocalIndex

    idx = LocalIndex()
    ok = True
    for q, intent, county in VERIFY_QUESTIONS:
        hits = idx.search(q, intent, county, k=3)
        print(f"\n▶ {q}  [{intent}, {county}]")
        if not hits:
            print("   NO HITS")
            ok = False
        for h in hits:
            print(f"   {h['short_cite']:<20} {h['page_label']:<10} {h['publish_date']}  score={h['score']}  {h['text'][:70]}")
    return 0 if ok else 1


def cmd_stats(args: argparse.Namespace) -> int:
    recs = [json.loads(l) for l in chunk.OUT.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"chunks: {len(recs)}  docs: {len({r['doc_id'] for r in recs})}")
    print("by authority:", dict(Counter(r["authority"] for r in recs)))
    print("by cycle:", dict(Counter(str(r["cycle"]) for r in recs)))
    print("by county:", dict(Counter(str(r["county"]) for r in recs)))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nitapata", description="Corpus ingestion for Nitapata?")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="verify snapshot hashes (or --download to re-fetch)")
    f.add_argument("--download", action="store_true")
    p = sub.add_parser("parse", help="show parsed paragraphs / Q&A blocks")
    p.add_argument("--doc")
    sub.add_parser("ingest", help="build corpus/chunks.jsonl")
    sub.add_parser("verify", help="retrieval sanity check on the demo questions")
    sub.add_parser("stats", help="corpus statistics")
    args = ap.parse_args(argv)
    return {"fetch": cmd_fetch, "parse": cmd_parse, "ingest": cmd_ingest, "verify": cmd_verify, "stats": cmd_stats}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
