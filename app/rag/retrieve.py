"""Hybrid retrieval (SPEC §9.2 step 5).

  1. filter by cycle + county (national / cycle-independent always eligible)
  2. BM25 over FTS5  +  cosine over dense vectors, both on the filtered set
  3. Reciprocal Rank Fusion
  4. authority weighting; later publish_date wins ties within a cycle (SPEC §7.2)
  5. threshold: no lexical hit AND best cosine below floor  →  nothing retrieved  →  boundary

Kiswahili queries get a small bilingual lexical expansion so BM25 still bites on
English documents; the dense side uses the query as written plus (optionally) the
classifier's English rendition (translate-before-retrieve, SPEC §11 Day-1 gate).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.rag.config import Settings
from app.rag.embeddings import EmbeddingProvider
from app.rag.schema import Chunk
from app.rag.store import AUTHORITY_WEIGHT, Candidate, ChunkStore

log = logging.getLogger("nitapata.retrieve")

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

STOPWORDS = {
    # en
    "the", "a", "an", "is", "are", "of", "to", "in", "for", "on", "at", "and", "or", "how", "what", "which", "do",
    "does", "i", "my", "me", "can", "this", "that", "it", "be", "with", "still", "get", "will",
    # sw / sheng
    "na", "ya", "wa", "za", "la", "kwa", "ni", "je", "au", "ama", "bado", "sasa", "hii", "hiyo", "yangu", "wangu",
    "nini", "gani", "lini", "wapi", "ngapi", "pia", "niaje", "sawa", "mimi", "wewe", "nilikuwa", "nataka",
}

# Kiswahili / Sheng → English retrieval synonyms. Lexical only; never shown to the farmer.
BILINGUAL = {
    "mbolea": ["fertiliser", "fertilizer"],
    "bei": ["price", "cost"],
    "gharama": ["price", "cost"],
    "mgao": ["allocation", "voucher"],
    "vocha": ["voucher", "e-voucher"],
    "sajili": ["register", "registration"],
    "kujisajili": ["register", "registration"],
    "usajili": ["registration", "register"],
    "kusajiliwa": ["registered", "registration"],
    "kitambulisho": ["id", "identity", "identification"],
    "id": ["identity", "identification", "kitambulisho"],
    "mkulima": ["farmer"],
    "wakulima": ["farmers"],
    "msimu": ["season", "cycle"],
    "mvua": ["rains", "season"],
    "gunia": ["bag", "50kg"],
    "mfuko": ["bag", "50kg"],
    "depo": ["depot"],
    "ghala": ["depot", "store"],
    "malipo": ["payment", "pay"],
    "lipa": ["pay", "payment"],
    "kupata": ["get", "receive", "eligible"],
    "naweza": ["eligible", "qualify"],
    "ruzuku": ["subsidy", "subsidised", "subsidized"],
    "punguzo": ["subsidy", "subsidised"],
    "wilaya": ["county"],
    "kaunti": ["county"],
    "inaanza": ["start", "begin", "launch"],
    "kuanza": ["start", "begin", "launch"],
    "hatua": ["step", "steps", "process"],
    "nahitaji": ["need", "required", "requirements"],
    "unahitaji": ["need", "required", "requirements"],
    "vinavyohitajika": ["requirements", "required"],
    "inanihudumia": ["serve", "serves", "nearest", "collection"],
    "kituo": ["collection", "centre", "depot", "outlet"],
    "vituo": ["collection", "centres", "depots", "outlets"],
    "kuchukua": ["collect", "redeem", "collection"],
    "nikuje": ["bring", "present", "need"],
}


def query_tokens(text: str) -> list[str]:
    toks = []
    for t in _TOKEN_RE.findall(text.lower()):
        t = t.strip("'")
        if not t or t in STOPWORDS or (len(t) < 2 and not t.isdigit()):
            continue
        toks.append(t)
    return toks


def expand_terms(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        out.append(t)
        out.extend(BILINGUAL.get(t, []))
        # numbers written with thousands separators tokenise oddly; keep both forms
        if t.isdigit() and len(t) > 3:
            out.append(f"{int(t):,}".replace(",", " "))
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def build_fts_query(text: str) -> str:
    """Safe FTS5 MATCH expression: quoted terms joined by OR. Never interpolates raw user text."""
    terms = expand_terms(query_tokens(text))
    if not terms:
        return ""
    quoted = []
    for t in terms:
        t = t.replace('"', "")
        if not t:
            continue
        if " " in t:
            quoted.append('"' + t + '"')
        else:
            quoted.append('"' + t + '"' + ("*" if len(t) >= 5 and not t.isdigit() else ""))
    return " OR ".join(quoted)


@dataclass
class RetrievalResult:
    candidates: list[Candidate]
    queries: list[str]
    filters: dict


class Retriever:
    GENERIC_DF_RATIO = 0.4      # a term present in >40% of chunks says nothing about relevance on its own

    def __init__(self, store: ChunkStore, embedder: EmbeddingProvider, settings: Settings):
        self.store = store
        self.embedder = embedder
        self.s = settings
        self._df_cache: dict[str, int] = {}
        self._df_version = -1

    def retrieve(self, queries: list[str], *, county: str | None, cycle: str | None = None,
                 include_superseded: bool = False, top_k: int | None = None,
                 intents: set[str] | None = None) -> RetrievalResult:
        cycle = cycle or self.s.current_cycle
        top_k = top_k or self.s.retrieval_top_k
        n = self.s.retrieval_candidates
        queries = [q.strip() for q in queries if q and q.strip()]
        if not queries:
            return RetrievalResult([], [], {"cycle": cycle, "county": county})
        # register overrides: past-cycle documents the register lists under `use_for` for an asked
        # intent stay eligible (e.g. the NCPB FAQ for eligibility); everything else is cycle-filtered
        hints = self.store.document_hints()
        intents = intents or set()
        past_docs = tuple(sorted(d for d, h in hints.items() if intents & set(h.get("use_for", []))))
        blocked = {d for d, h in hints.items() if intents & set(h.get("do_not_use_for", []))}

        indexed_model = self.store.get_meta("embedding_model")
        use_dense = indexed_model is not None and indexed_model == self.embedder.name
        if indexed_model and not use_dense:
            log.warning("index built with %s but embedder is %s — dense retrieval disabled", indexed_model, self.embedder.name)

        by_id: dict[str, Candidate] = {}
        k = self.s.retrieval_rrf_k

        def bump(cid: str, rank: int, kind: str, score: float | None = None) -> None:
            c = by_id.get(cid)
            if c is None:
                chunk = self.store.get_chunk(cid)
                if chunk is None:
                    return
                c = by_id[cid] = Candidate(chunk=chunk)
            c.fused += 1.0 / (k + rank)
            if kind == "lex":
                c.lexical_rank = rank if c.lexical_rank is None else min(c.lexical_rank, rank)
            else:
                c.dense_rank = rank if c.dense_rank is None else min(c.dense_rank, rank)
                c.dense_score = max(c.dense_score, score or 0.0)

        for q in queries:
            fts = build_fts_query(q)
            for rank, (cid, _bm25) in enumerate(self.store.lexical_search(
                    fts, cycle=cycle, county=county, include_superseded=include_superseded, limit=n,
                    past_cycle_docs=past_docs), start=1):
                bump(cid, rank, "lex")
            if use_dense:
                qv = self.embedder.embed_query(q)
                for rank, (cid, cos) in enumerate(self.store.dense_search(
                        qv, cycle=cycle, county=county, include_superseded=include_superseded, limit=n,
                        past_cycle_docs=past_docs), start=1):
                    bump(cid, rank, "dense", cos)

        cands = [c for c in by_id.values() if c.chunk.doc_id not in blocked]
        # threshold: a chunk stays if it is semantically close enough, or if it covers two distinct
        # query terms, or one term that is discriminative in this corpus (low document frequency).
        # A single generic word such as "fertiliser" or "price" is not evidence of relevance.
        floor = self.s.retrieval_min_dense_score
        groups = self._term_groups(queries)
        discriminative = self._discriminative_groups(groups)
        for c in cands:
            c.term_hits, strong = self._coverage(c.chunk.text, groups, discriminative)
            c.strong_hit = strong
        cands = [c for c in cands if c.dense_score >= floor
                 or (c.lexical_rank is not None and (c.term_hits >= 2 or c.strong_hit))]
        for c in cands:
            c.fused *= AUTHORITY_WEIGHT[c.chunk.authority]
            c.fused *= 1.0 + 0.05 * min(c.term_hits, 6)      # more query terms covered → rank higher
            if county and c.chunk.county == county:
                c.fused *= 1.2                                 # local specificity (SPEC §5 Pin 2 'supporting')
            if c.chunk.cycle and c.chunk.cycle != cycle:
                c.fused *= 0.85          # past cycle, admitted by register override: rank below current

        # later publish_date wins near-ties (SPEC §7.2)
        cands.sort(key=lambda c: (round(c.fused, 4), c.chunk.publish_date), reverse=True)
        return RetrievalResult(cands[:top_k], queries, {"cycle": cycle, "county": county, "include_superseded": include_superseded})


    @staticmethod
    def _term_groups(queries: list[str]) -> list[set[str]]:
        """One group per distinct query token: the token plus its bilingual expansions."""
        groups: dict[str, set[str]] = {}
        for q in queries:
            for t in query_tokens(q):
                groups.setdefault(t, {t, *BILINGUAL.get(t, [])})
        return list(groups.values())

    def _discriminative_groups(self, groups: list[set[str]]) -> set[int]:
        """Indices of groups whose every present form is rare enough to carry signal."""
        if self.store.write_version != self._df_version:
            self._df_cache, self._df_version = {}, self.store.write_version
        total = max(1, self.store.chunk_count())
        out: set[int] = set()
        for i, g in enumerate(groups):
            dfs = []
            for t in g:
                if t not in self._df_cache:
                    self._df_cache[t] = self.store.term_document_frequency(t)
                dfs.append(self._df_cache[t])
            if any(0 < d <= total * self.GENERIC_DF_RATIO for d in dfs):
                out.add(i)
        return out

    @staticmethod
    def _coverage(text: str, groups: list[set[str]], discriminative: set[int]) -> tuple[int, bool]:
        low = " " + re.sub(r"[^\w']+", " ", text.lower()) + " "
        hits, strong = 0, False
        for i, g in enumerate(groups):
            if any((f" {t} " in low) or (len(t) >= 5 and f" {t}" in low) for t in g):
                hits += 1
                strong |= i in discriminative
        return hits, strong


def normalised_scores(cands: list[Candidate]) -> dict[str, float]:
    """Map fused scores to [0,1] for diagnostics (the judge panel shows these)."""
    if not cands:
        return {}
    hi = max(c.fused for c in cands) or 1.0
    return {c.chunk.chunk_id: round(c.fused / hi, 4) for c in cands}


def chunk_list(cands: list[Candidate]) -> list[Chunk]:
    return [c.chunk for c in cands]
