"""`nitapata` operator CLI (SPEC §9.1): fetch | ingest | verify | stats | search | ask

    .venv/bin/python -m app.rag.cli ingest --fetch
    .venv/bin/python -m app.rag.cli verify
    .venv/bin/python -m app.rag.cli ask "bei ya DAP ni ngapi?" --county Machakos
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from app.rag.config import get_settings
from app.rag.corpus import fetch_snapshot, load_sources
from app.rag.pipeline import build_pipeline
from app.rag.schema import AnswerRequest
from app.rag.security import install_log_redaction

VERIFY_QUESTIONS = [  # SPEC §9.1 step 5 — the three Nyambura questions
    ("mbolea ya Kangundo bado?", "Machakos"),
    ("nikienda depot nikuje na nini?", None),
    ("bei ya DAP ni ngapi?", None),
]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    install_log_redaction()
    ap = argparse.ArgumentParser(prog="nitapata")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="snapshot documents from sources.yaml into corpus/raw/")
    f.add_argument("doc_ids", nargs="*")
    f.add_argument("--force", action="store_true")
    i = sub.add_parser("ingest", help="parse → chunk → embed → index (offline unless --fetch)")
    i.add_argument("doc_ids", nargs="*")
    i.add_argument("--fetch", action="store_true")
    i.add_argument("--force", action="store_true")
    sub.add_parser("stats")
    sub.add_parser("verify", help="run the three Nyambura questions against the index")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--county")
    s.add_argument("--superseded", action="store_true")
    s.add_argument("-k", type=int, default=5)
    s.add_argument("--intent")
    a = sub.add_parser("ask")
    a.add_argument("text")
    a.add_argument("--county")
    a.add_argument("--depot")
    a.add_argument("--declared", help='JSON, e.g. {"has_id": true, "has_allocation_sms": false}')
    a.add_argument("--clarify-used", action="store_true")
    args = ap.parse_args(argv)

    settings = get_settings()
    if args.cmd == "fetch":
        docs = [d for d in load_sources(settings) if d.enabled and (not args.doc_ids or d.doc_id in args.doc_ids)]
        for d in docs:
            try:
                print(d.doc_id, json.dumps(fetch_snapshot(settings, d, force=args.force)))
            except Exception as exc:
                print(d.doc_id, "ERROR", exc, file=sys.stderr)
        return 0

    p = build_pipeline(settings)
    if args.cmd == "ingest":
        report = p.ingester.run(doc_ids=args.doc_ids or None, fetch=args.fetch, force=args.force)
        print(json.dumps(report, indent=2))
        return 0 if all(r["status"] in ("ingested", "unchanged") for r in report) else 1
    if args.cmd == "stats":
        print(json.dumps(p.store.stats(), indent=2))
        return 0
    if args.cmd == "search":
        for h in p.search(args.query, county=args.county, cycle=None, include_superseded=args.superseded, top_k=args.k,
                          intent=args.intent):
            c = h.chunk
            print(f"{h.score:.3f} lex={h.lexical_rank} dense={h.dense_rank} {c.chunk_id} [{c.authority}] {c.doc_title} {c.page_label} {c.publish_date}")
            print("   ", c.text[:220].replace("\n", " "), "…")
        return 0
    if args.cmd == "verify":
        ok = True
        for q, county in VERIFY_QUESTIONS:
            print(f"\n=== {q}  (county={county})")
            ans = p.answer(AnswerRequest(message_id="verify", text=q, channel="eval", county=county, clarify_used=True))
            print(f"   outcome={ans.outcome} kind={ans.boundary_kind} intent={ans.intent} hits={ans.diagnostics.guardrail_hits}")
            print(f"   text: {ans.text[:160]}")
            if not ans.diagnostics.retrieved:
                ok = False
                print("   NO CHUNKS RETRIEVED")
            for r in ans.diagnostics.retrieved[:3]:
                print(f"   {r.score:.3f} used={r.used} {r.doc_title[:60]} | {r.page_label} | {r.publish_date} | {r.chunk_id}")
        return 0 if ok else 1
    if args.cmd == "ask":
        req = AnswerRequest(message_id="cli", text=args.text, channel="demo", county=args.county, depot=args.depot,
                            declared=json.loads(args.declared) if args.declared else {}, clarify_used=args.clarify_used)
        ans = p.answer(req)
        print(json.dumps(ans.model_dump(mode="json"), indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
