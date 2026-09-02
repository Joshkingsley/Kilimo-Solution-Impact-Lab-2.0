"""SQLite-backed chunk store: metadata + FTS5 lexical index + dense vectors.

One file, rebuildable offline from corpus/raw/ (SPEC §13.10). Mirrors the spec's
Vectorize+D1 split in a single process. Vectors are brute-force cosine over an
in-memory float32 matrix, which is the right tool below ~50k chunks.

All SQL is parameterised. Filters are applied *before* ranking (SPEC §9.2 step 5).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np

from app.rag.schema import Chunk

log = logging.getLogger("nitapata.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents (
  doc_id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL, doc_type TEXT NOT NULL,
  authority TEXT NOT NULL, url TEXT NOT NULL, publish_date TEXT NOT NULL, cycle TEXT, county TEXT,
  lang TEXT NOT NULL, sha256 TEXT NOT NULL, retrieved_at TEXT NOT NULL, raw_path TEXT NOT NULL,
  ingest_version INTEGER NOT NULL, embedding_model TEXT NOT NULL, n_chunks INTEGER NOT NULL,
  skipped_pages TEXT NOT NULL DEFAULT '[]',
  short_cite TEXT, use_for TEXT NOT NULL DEFAULT '[]', do_not_use_for TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  page INTEGER NOT NULL CHECK(page >= 1), page_label TEXT NOT NULL, text TEXT NOT NULL,
  token_count INTEGER NOT NULL, ocr INTEGER NOT NULL DEFAULT 0,
  -- denormalised for filtering
  cycle TEXT, county TEXT, authority TEXT NOT NULL, publish_date TEXT NOT NULL, lang TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_filter ON chunks(cycle, county, authority);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id UNINDEXED, text, tokenize = 'unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS embeddings (
  chunk_id TEXT PRIMARY KEY REFERENCES chunks(chunk_id) ON DELETE CASCADE,
  model TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS embedding_cache (
  model TEXT NOT NULL, text_sha256 TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,
  PRIMARY KEY (model, text_sha256)
);
"""

AUTHORITY_WEIGHT = {"legal_basis": 1.0, "primary": 1.0, "supporting": 0.92, "secondary": 0.6}


@dataclass
class Candidate:
    chunk: Chunk
    lexical_rank: int | None = None
    dense_rank: int | None = None
    dense_score: float = 0.0
    fused: float = 0.0
    term_hits: int = 0
    strong_hit: bool = False


class ChunkStore:
    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else path
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL") if isinstance(self.path, Path) else None
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(_SCHEMA)
        self._matrix: np.ndarray | None = None
        self._matrix_ids: list[str] = []
        self.write_version = 0          # bumped on every write; retrievers use it to invalidate caches

    # ------------------------------------------------------------ meta
    def get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._conn.commit()

    # ------------------------------------------------------------ embedding cache
    def cached_vectors(self, model: str, hashes: Sequence[str]) -> dict[str, np.ndarray]:
        if not hashes:
            return {}
        out: dict[str, np.ndarray] = {}
        for i in range(0, len(hashes), 500):
            part = list(hashes[i:i + 500])
            q = f"SELECT text_sha256, vec FROM embedding_cache WHERE model=? AND text_sha256 IN ({','.join('?' * len(part))})"
            for row in self._conn.execute(q, [model, *part]):
                out[row["text_sha256"]] = np.frombuffer(row["vec"], dtype=np.float32)
        return out

    def put_cached_vectors(self, model: str, items: Iterable[tuple[str, np.ndarray]]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache(model,text_sha256,dim,vec) VALUES(?,?,?,?)",
                [(model, h, int(v.shape[0]), np.asarray(v, dtype=np.float32).tobytes()) for h, v in items],
            )
            self._conn.commit()

    # ------------------------------------------------------------ writes
    def document_state(self, doc_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()

    def replace_document(self, doc_row: dict, chunks: Sequence[Chunk], vectors: np.ndarray, model: str) -> None:
        if len(chunks) != vectors.shape[0]:
            raise ValueError("chunks/vectors length mismatch")
        existing = self.get_meta("embedding_model")
        if existing and existing != model:
            raise RuntimeError(f"index was built with {existing}; re-ingest everything to switch to {model}")
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            try:
                old = [r["chunk_id"] for r in cur.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_row["doc_id"],))]
                if old:
                    cur.executemany("DELETE FROM chunks_fts WHERE chunk_id=?", [(c,) for c in old])
                cur.execute("DELETE FROM documents WHERE doc_id=?", (doc_row["doc_id"],))
                cur.execute(
                    """INSERT INTO documents(doc_id,title,publisher,doc_type,authority,url,publish_date,cycle,county,lang,
                       sha256,retrieved_at,raw_path,ingest_version,embedding_model,n_chunks,skipped_pages,
                       short_cite,use_for,do_not_use_for)
                       VALUES(:doc_id,:title,:publisher,:doc_type,:authority,:url,:publish_date,:cycle,:county,:lang,
                       :sha256,:retrieved_at,:raw_path,:ingest_version,:embedding_model,:n_chunks,:skipped_pages,
                       :short_cite,:use_for,:do_not_use_for)""",
                    {**doc_row, "embedding_model": model, "n_chunks": len(chunks),
                     "skipped_pages": json.dumps(doc_row.get("skipped_pages", [])),
                     "short_cite": doc_row.get("short_cite"), "use_for": json.dumps(doc_row.get("use_for", [])),
                     "do_not_use_for": json.dumps(doc_row.get("do_not_use_for", []))},
                )
                cur.executemany(
                    """INSERT INTO chunks(chunk_id,doc_id,page,page_label,text,token_count,ocr,cycle,county,authority,publish_date,lang)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [(c.chunk_id, c.doc_id, c.page, c.page_label, c.text, c.token_count, int(c.ocr), c.cycle, c.county,
                      c.authority, c.publish_date.isoformat(), c.lang) for c in chunks],
                )
                cur.executemany("INSERT INTO chunks_fts(chunk_id,text) VALUES(?,?)", [(c.chunk_id, c.text) for c in chunks])
                cur.executemany(
                    "INSERT INTO embeddings(chunk_id,model,dim,vec) VALUES(?,?,?,?)",
                    [(c.chunk_id, model, int(vectors.shape[1]), np.asarray(vectors[i], dtype=np.float32).tobytes())
                     for i, c in enumerate(chunks)],
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise
            self.set_meta("embedding_model", model)
            self._matrix = None
            self.write_version += 1

    def delete_document(self, doc_id: str) -> None:
        with self._lock:
            old = [r["chunk_id"] for r in self._conn.execute("SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,))]
            self._conn.executemany("DELETE FROM chunks_fts WHERE chunk_id=?", [(c,) for c in old])
            self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
            self._conn.commit()
            self._matrix = None
            self.write_version += 1

    # ------------------------------------------------------------ reads
    def _row_to_chunk(self, r: sqlite3.Row) -> Chunk:
        return Chunk(
            chunk_id=r["chunk_id"], doc_id=r["doc_id"], doc_title=r["title"], publisher=r["publisher"],
            doc_type=r["doc_type"], authority=r["authority"], page=r["page"], page_label=r["page_label"],
            publish_date=date.fromisoformat(r["publish_date"]), cycle=r["cycle"], county=r["county"], lang=r["lang"],
            text=r["text"], token_count=r["token_count"], source_url=r["url"], retrieved_at=r["retrieved_at"],
            ingest_version=r["ingest_version"], ocr=bool(r["ocr"]), short_cite=r["short_cite"],
        )

    _CHUNK_SELECT = """SELECT c.*, d.title, d.publisher, d.doc_type, d.url, d.retrieved_at, d.ingest_version, d.short_cite
                       FROM chunks c JOIN documents d ON d.doc_id = c.doc_id"""

    def get_chunk(self, chunk_id: str) -> Chunk | None:
        r = self._conn.execute(self._CHUNK_SELECT + " WHERE c.chunk_id=?", (chunk_id,)).fetchone()
        return self._row_to_chunk(r) if r else None

    def get_chunks(self, chunk_ids: Sequence[str]) -> dict[str, Chunk]:
        if not chunk_ids:
            return {}
        q = self._CHUNK_SELECT + f" WHERE c.chunk_id IN ({','.join('?' * len(chunk_ids))})"
        return {r["chunk_id"]: self._row_to_chunk(r) for r in self._conn.execute(q, list(chunk_ids))}

    @staticmethod
    def _filter_sql(cycle: str | None, county: str | None, include_superseded: bool,
                    past_cycle_docs: tuple[str, ...] = ()) -> tuple[str, list]:
        """Cycle: current-cycle and cycle-independent chunks are eligible; past-cycle chunks only
        from documents the register explicitly allows for the asked intent (`use_for`).
        County: national chunks always; county-scoped chunks only for that county — and never
        when the farmer's county is unknown (a county figure must not pass as national)."""
        where, params = [], []
        if not include_superseded:
            clause = "(c.cycle IS NULL OR c.cycle = ?"
            params.append(cycle)
            if past_cycle_docs:
                clause += f" OR c.doc_id IN ({','.join('?' * len(past_cycle_docs))})"
                params.extend(past_cycle_docs)
            where.append(clause + ")")
        if county:
            where.append("(c.county IS NULL OR c.county = ?)")
            params.append(county)
        else:
            where.append("c.county IS NULL")
        return (" WHERE " + " AND ".join(where)) if where else "", params

    def lexical_search(self, fts_query: str, *, cycle: str | None, county: str | None,
                       include_superseded: bool, limit: int, past_cycle_docs: tuple[str, ...] = ()) -> list[tuple[str, float]]:
        if not fts_query.strip():
            return []
        where, params = self._filter_sql(cycle, county, include_superseded, past_cycle_docs)
        sql = f"""SELECT f.chunk_id, bm25(chunks_fts) AS rank
                  FROM chunks_fts f JOIN chunks c ON c.chunk_id = f.chunk_id
                  {where + ' AND ' if where else ' WHERE '} chunks_fts MATCH ?
                  ORDER BY rank LIMIT ?"""
        try:
            rows = self._conn.execute(sql, [*params, fts_query, limit]).fetchall()
        except sqlite3.OperationalError as exc:      # malformed MATCH expression → no lexical hits
            log.warning("fts query rejected: %s", exc)
            return []
        return [(r["chunk_id"], float(r["rank"])) for r in rows]

    def _ensure_matrix(self) -> None:
        if self._matrix is not None:
            return
        rows = self._conn.execute("SELECT chunk_id, vec, dim FROM embeddings ORDER BY chunk_id").fetchall()
        if not rows:
            self._matrix, self._matrix_ids = np.zeros((0, 1), dtype=np.float32), []
            return
        dim = rows[0]["dim"]
        self._matrix = np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows]).reshape(len(rows), dim)
        self._matrix_ids = [r["chunk_id"] for r in rows]

    def dense_search(self, qvec: np.ndarray, *, cycle: str | None, county: str | None,
                     include_superseded: bool, limit: int, past_cycle_docs: tuple[str, ...] = ()) -> list[tuple[str, float]]:
        with self._lock:
            self._ensure_matrix()
            if not self._matrix_ids:
                return []
            where, params = self._filter_sql(cycle, county, include_superseded, past_cycle_docs)
            eligible = {r["chunk_id"] for r in self._conn.execute(f"SELECT c.chunk_id FROM chunks c{where}", params)}
            ids = self._matrix_ids
            matrix = self._matrix
        mask = np.fromiter((cid in eligible for cid in ids), dtype=bool, count=len(ids))
        if not mask.any():
            return []
        q = np.asarray(qvec, dtype=np.float32)
        if q.shape[0] != matrix.shape[1]:
            raise RuntimeError(f"query dim {q.shape[0]} != index dim {matrix.shape[1]} — re-ingest with the current embedder")
        scores = matrix @ q
        scores[~mask] = -np.inf
        top = np.argpartition(-scores, min(limit, len(ids) - 1))[:limit]
        top = top[np.argsort(-scores[top])]
        return [(ids[i], float(scores[i])) for i in top if np.isfinite(scores[i])]

    def term_document_frequency(self, term: str) -> int:
        """How many chunks contain `term` (FTS5 token match). Used to judge how discriminative a query term is."""
        t = term.replace('"', "")
        if not t:
            return 0
        try:
            row = self._conn.execute("SELECT COUNT(*) n FROM chunks_fts WHERE chunks_fts MATCH ?", (f'"{t}"',)).fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row["n"])

    def chunk_count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"])

    def document_hints(self) -> dict[str, dict[str, list[str]]]:
        """Register hints per document: which intents it may / may not be cited for."""
        return {r["doc_id"]: {"use_for": json.loads(r["use_for"]), "do_not_use_for": json.loads(r["do_not_use_for"])}
                for r in self._conn.execute("SELECT doc_id, use_for, do_not_use_for FROM documents")}

    # ------------------------------------------------------------ stats
    def stats(self) -> dict:
        docs = self._conn.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"]
        chunks = self._conn.execute("SELECT COUNT(*) n FROM chunks").fetchone()["n"]
        by_doc = [dict(r) for r in self._conn.execute(
            "SELECT doc_id, title, short_cite, authority, cycle, county, publish_date, n_chunks, skipped_pages FROM documents ORDER BY doc_id")]
        for d in by_doc:
            d["skipped_pages"] = json.loads(d["skipped_pages"])
        return {"documents": docs, "chunks": chunks, "embedding_model": self.get_meta("embedding_model"), "by_document": by_doc}

    def close(self) -> None:
        self._conn.close()
