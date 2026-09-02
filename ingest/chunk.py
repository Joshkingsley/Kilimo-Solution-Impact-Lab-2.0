"""Build Interface A chunk records (SPEC §8.1) from corpus/sources.yaml + snapshots.

A chunk with no locator is rejected loudly, never stored with locator 0.
Curated overrides (corpus/curated/<doc_id>.jsonl) replace parsing for
documents whose page structure is noise (marketing portals).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import yaml

from ingest import parse
from nitapata.constants import INGEST_VERSION

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "corpus" / "sources.yaml"
CURATED = ROOT / "corpus" / "curated"
OUT = ROOT / "corpus" / "chunks.jsonl"


def load_sources() -> list[dict]:
    return yaml.safe_load(SOURCES.read_text(encoding="utf-8"))["sources"]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record(src: dict, kind: str, locator: int, label: str, text: str, ordinal: int = 1) -> dict:
    if not locator or not label or not text.strip():
        raise ValueError(f"{src['doc_id']}: chunk without locator/text rejected")
    pub = src["publish_date"]
    return {
        "chunk_id": f"{src['doc_id']}#{kind[0]}{locator}#{ordinal}",
        "doc_id": src["doc_id"],
        "doc_title": src["title"],
        "short_cite": src["short_cite"],
        "publisher": src["publisher"],
        "doc_type": src["doc_type"],
        "authority": src["authority"],
        "locator_kind": kind,
        "locator": locator,
        "page_label": label,
        "publish_date": pub.isoformat() if isinstance(pub, date) else str(pub),
        "cycle": src.get("cycle"),
        "county": src.get("county"),
        "lang": src.get("lang", "en"),
        "text": text.strip(),
        "token_count": len(text.split()),
        "source_url": src["url"],
        "retrieved_at": str(src.get("retrieved_at")),
        "ingest_version": INGEST_VERSION,
        "use_for": [u for u in src.get("use_for", []) if not u.endswith("-historic")] + [u for u in src.get("use_for", []) if u.endswith("-historic")],
        "do_not_use_for": src.get("do_not_use_for", []),
    }


def chunks_for(src: dict) -> list[dict]:
    raw = ROOT / src["raw_path"]
    curated = CURATED / f"{src['doc_id']}.jsonl"
    if curated.exists():
        out = []
        for i, line in enumerate(l for l in curated.read_text(encoding="utf-8").splitlines() if l.strip()):
            c = json.loads(line)
            out.append(_record(src, c.get("locator_kind", "paragraph"), c["locator"], c["page_label"], c["text"], i + 1))
        return out
    if src["format"].startswith("pdf"):
        blocks = parse.pdf_qa_blocks(raw)
        if blocks:
            return [_record(src, "page", 1, f"Uk.1 Q{n}", body, ordinal=n) for n, body in blocks]
        return [_record(src, "page", i, f"p.{i}", t) for i, t in enumerate(parse.pdf_pages(raw), 1) if t]
    paras = parse.html_paragraphs(raw)
    return [_record(src, "paragraph", i, f"para {i}", p) for i, p in enumerate(paras, 1)]


def build(write: bool = True) -> list[dict]:
    records: list[dict] = []
    problems: list[str] = []
    for src in load_sources():
        raw = ROOT / src["raw_path"]
        if not raw.exists():
            problems.append(f"{src['doc_id']}: snapshot missing at {src['raw_path']}")
            continue
        if src.get("sha256") and sha256(raw) != src["sha256"]:
            problems.append(f"{src['doc_id']}: sha256 mismatch, snapshot changed since registration")
        try:
            recs = chunks_for(src)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        if not recs:
            problems.append(f"{src['doc_id']}: parser produced no chunks")
        records += recs
    if write:
        OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n", encoding="utf-8")
    for p in problems:
        print("WARN", p)
    return records
