"""Ingestion orchestrator (SPEC §9.1): sources.yaml → snapshot → parse → chunk → embed → store.

Idempotent: a document whose snapshot sha256 and ingest_version are unchanged is
skipped. Embeddings are cached by (model, sha256(text)) so re-chunking only pays
for chunks whose text actually changed. Runs fully offline from corpus/raw/.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

import numpy as np

from app.rag.chunk import chunk_page
from app.rag.config import Settings
from app.rag.corpus import SourceDoc, fetch_snapshot, load_sources, raw_file, sha256_of
from app.rag.embeddings import EmbeddingProvider
from app.rag.parse import parse_file
from app.rag.schema import Chunk
from app.rag.store import ChunkStore

log = logging.getLogger("nitapata.ingest")


class Ingester:
    def __init__(self, store: ChunkStore, embedder: EmbeddingProvider, settings: Settings):
        self.store = store
        self.embedder = embedder
        self.s = settings

    def run(self, *, doc_ids: list[str] | None = None, fetch: bool = False, force: bool = False) -> list[dict]:
        docs = [d for d in load_sources(self.s) if d.enabled]
        if doc_ids is not None:
            wanted = set(doc_ids)
            missing = wanted - {d.doc_id for d in docs}
            if missing:
                raise ValueError(f"unknown or disabled doc_id(s): {sorted(missing)}")
            docs = [d for d in docs if d.doc_id in wanted]
        report = []
        for d in docs:
            try:
                report.append(self.ingest_one(d, fetch=fetch, force=force))
            except Exception as exc:
                log.exception("ingest failed for %s", d.doc_id)
                report.append({"doc_id": d.doc_id, "status": "error", "error": str(exc)})
        return report

    def ingest_one(self, doc: SourceDoc, *, fetch: bool, force: bool) -> dict:
        path = raw_file(self.s, doc)
        fetched = None
        if fetch:
            fetched = fetch_snapshot(self.s, doc, force=force)
        if not path.exists():
            return {"doc_id": doc.doc_id, "status": "missing_snapshot",
                    "hint": f"run `nitapata fetch {doc.doc_id}` or place the file at {doc.raw_path}"}
        digest = sha256_of(path)
        if doc.sha256 and doc.sha256 != digest:
            log.warning("%s: sources.yaml sha256 differs from snapshot on disk (register %s…, disk %s…)",
                        doc.doc_id, doc.sha256[:12], digest[:12])
        state = self.store.document_state(doc.doc_id)
        if (state and not force and state["sha256"] == digest and state["ingest_version"] == self.s.ingest_version
                and state["embedding_model"] == self.embedder.name):
            return {"doc_id": doc.doc_id, "status": "unchanged", "chunks": state["n_chunks"]}

        parsed = parse_file(path, doc.resolved_format())
        if not parsed.pages:
            raise RuntimeError(f"{doc.doc_id}: no extractable text on any page"
                               + (" (needs OCR)" if doc.needs_ocr or parsed.skipped_pages else ""))
        retrieved_at = (fetched or {}).get("retrieved_at") or (doc.retrieved_at.isoformat() if doc.retrieved_at
                                                                 else datetime.now(timezone.utc).date().isoformat())
        fmt = doc.resolved_format()
        chunks: list[Chunk] = []
        for page in parsed.pages:
            for rc in chunk_page(page.text, target_tokens=self.s.chunk_target_tokens,
                                 overlap_sentences=self.s.chunk_overlap_sentences,
                                 unit="paragraph" if fmt == "html" else "sentence"):
                # web pages have no printed page: the locator is the paragraph number (¶N)
                label = f"¶{rc.start_unit}" if fmt == "html" else page.page_label
                chunks.append(Chunk(
                    chunk_id=f"{doc.doc_id}#p{page.page}#{rc.ordinal}", doc_id=doc.doc_id, doc_title=doc.title,
                    publisher=doc.publisher, doc_type=doc.doc_type, authority=doc.authority, page=page.page,
                    page_label=label, publish_date=doc.publish_date, cycle=doc.cycle, county=doc.county,
                    lang=doc.lang, text=rc.text, token_count=rc.token_count, source_url=doc.url,
                    retrieved_at=retrieved_at, ingest_version=self.s.ingest_version, ocr=page.ocr,
                    short_cite=doc.short_cite,
                ))
        if not chunks:
            raise RuntimeError(f"{doc.doc_id}: parsed pages produced no chunks")

        vectors = self._embed(chunks)
        self.store.replace_document(
            {"doc_id": doc.doc_id, "title": doc.title, "publisher": doc.publisher, "doc_type": doc.doc_type,
             "authority": doc.authority, "url": doc.url, "publish_date": doc.publish_date.isoformat(), "cycle": doc.cycle,
             "county": doc.county, "lang": doc.lang, "sha256": digest, "retrieved_at": retrieved_at,
             "raw_path": doc.raw_path, "ingest_version": self.s.ingest_version, "skipped_pages": parsed.skipped_pages,
             "short_cite": doc.short_cite, "use_for": doc.use_for, "do_not_use_for": doc.do_not_use_for},
            chunks, vectors, self.embedder.name)
        log.info("ingested %s: %d pages, %d chunks, %d skipped pages", doc.doc_id, len(parsed.pages), len(chunks),
                 len(parsed.skipped_pages))
        return {"doc_id": doc.doc_id, "status": "ingested", "pages": len(parsed.pages), "chunks": len(chunks),
                "skipped_pages": parsed.skipped_pages, "sha256": digest,
                **({"fetch": fetched["status"]} if fetched else {})}

    def _embed(self, chunks: list[Chunk]) -> np.ndarray:
        hashes = [hashlib.sha256(c.text.encode("utf-8")).hexdigest() for c in chunks]
        cached = self.store.cached_vectors(self.embedder.name, hashes)
        todo = [i for i, h in enumerate(hashes) if h not in cached]
        if todo:
            fresh = self.embedder.embed_documents([chunks[i].text for i in todo])
            self.store.put_cached_vectors(self.embedder.name, ((hashes[i], fresh[j]) for j, i in enumerate(todo)))
            for j, i in enumerate(todo):
                cached[hashes[i]] = fresh[j]
        return np.vstack([cached[h] for h in hashes]).astype(np.float32)
