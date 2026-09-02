"""The per-message RAG pipeline (SPEC §9.2 steps 2–7; step 1 keywords and step 8
segmentation belong to the channel layer).

    classify (scope gate, split, resolve, declared)
      → out of scope            → boundary
      → county needed, unknown  → clarify (once; caller passes clarify_used)
    retrieve (cycle + county filter, hybrid, authority re-rank)
      → nothing                 → boundary (not in documents)
    generate (structured cite-or-refuse draft)
      → insufficient            → boundary (not in documents)
    citation check (deterministic)
      → any hit                 → fallback line, outcome boundary, hits recorded
      → ok                      → cite, with citations/requirements/gap/declines

The pipeline is stateless. Everything per-thread (declared flags, clarify budget,
language pin) arrives in the request and leaves in the response.
"""
from __future__ import annotations

import logging
import time
import uuid

from app.rag import geo
from app.rag.config import Settings, get_settings
from app.rag.embeddings import CachedQueryEmbedder, build_embedder
from app.rag.guardrails import check_draft
from app.rag.ingest import Ingester
from app.rag.llm import LLM, Classification, Draft, LLMUsage, build_llm
from app.rag.prompts import (BOUNDARY_NOT_IN_DOCUMENTS, BOUNDARY_OUT_OF_SCOPE, CLARIFY_COUNTY, DECLINE_OUT_OF_SCOPE,
                             DECLINE_PERSONAL_RECORD, DECLINE_TREND, FALLBACK_LINE, LABELS)
from app.rag.retrieve import Retriever, normalised_scores
from app.rag.schema import (AnswerRequest, Chunk, Citation, Diagnostics, Lang, RagAnswer, Requirement, Resolved,
                            RetrievedRef, SearchHit)
from app.rag.store import ChunkStore

log = logging.getLogger("nitapata.pipeline")

# Intents that cannot be answered without knowing where the farmer is (SPEC §6.2).
COUNTY_REQUIRED_INTENTS = {"depot_availability", "price"}
# Intents whose answer is a figure: a secondary source is never the sole basis (SPEC §5 Pin 2).
FIGURE_INTENTS = {"price", "cycle_timing"}


def render_reply(ans: RagAnswer) -> str:
    """SPEC §9.5 order: answer, requirements, gap, declines, citation. Plain text, unsegmented."""
    if ans.outcome != "cite":
        return ans.text
    L = ans.language
    parts = [ans.text.rstrip(".") + "."]
    if ans.requirements:
        labels = [(r.label_sw if L == "sw" else r.label_en) for r in ans.requirements]
        parts.append(f"{LABELS['requirements'][L]}: " + " ".join(f"({i}) {t}" for i, t in enumerate(labels, 1)) + ".")
        missing = [(r.label_sw if L == "sw" else r.label_en) for r in ans.requirements if r.missing]
        if missing:
            parts.append(f"{LABELS['gap'][L]}: " + ", ".join(missing) + ".")
    if ans.declines:
        parts.append(f"{LABELS['declines'][L]}: " + "; ".join(ans.declines) + ".")
    c = ans.citations[0]
    parts.append(f"{LABELS['source'][L]}: {c.short_cite or c.doc_title}, {c.page_label}, {c.publish_date.isoformat()}.")
    return " ".join(parts)


class RagPipeline:
    def __init__(self, store: ChunkStore, retriever: Retriever, llm: LLM, settings: Settings, ingester: Ingester):
        self.store = store
        self.retriever = retriever
        self.llm = llm
        self.s = settings
        self.ingester = ingester

    # ------------------------------------------------------------ public
    def answer(self, req: AnswerRequest) -> RagAnswer:
        t0 = time.perf_counter()
        trace_id = uuid.uuid4().hex[:16]
        diag = Diagnostics(model=self.llm.name, embedding_model=self.retriever.embedder.name)
        declared = dict(req.declared)

        def finish(ans: RagAnswer) -> RagAnswer:
            ans.diagnostics.latency_ms = int((time.perf_counter() - t0) * 1000)
            ans.rendered_text = render_reply(ans)
            return RagAnswer.model_validate(ans.model_dump())   # re-run invariants after mutation

        # 1. classify ------------------------------------------------------------
        try:
            cls, usage = self.llm.classify(req.text)
        except Exception as exc:
            log.warning("classification error trace=%s: %s", trace_id, type(exc).__name__)
            cls, usage = Classification(language=req.language_pin or "sw", sub_asks=[]), LLMUsage()
        self._record_usage(diag, usage)
        lang: Lang = req.language_pin or cls.language
        diag.sub_asks = [a.text for a in cls.sub_asks]

        # merge declared state: the message's own statements win over what the thread already held
        for k, v in cls.declared.items():
            declared[k] = v

        county = geo.normalise_county(cls.county) or geo.normalise_county(req.county) or self.s.default_county
        depot = geo.normalise_depot(cls.depot) or geo.normalise_depot(req.depot)
        if depot and not county:
            county = geo.county_for_depot(depot)
        resolved = Resolved(county=county, depot=depot, cycle=self.s.current_cycle)

        in_scope = cls.in_scope_asks
        if not in_scope:
            return finish(self._boundary(trace_id, "out_of_scope", lang, diag, declared, resolved))
        intent = cls.primary_intent

        # 2. resolve / clarify ---------------------------------------------------
        needs_county = any(a.intent in COUNTY_REQUIRED_INTENTS for a in in_scope)
        if needs_county and not county and not req.clarify_used:
            return finish(RagAnswer(trace_id=trace_id, outcome="clarify", intent=intent, language=lang,
                                    text=CLARIFY_COUNTY[lang], rendered_text="", declared=declared, resolved=resolved,
                                    diagnostics=diag))

        # 3. retrieve ------------------------------------------------------------
        queries = [a.text for a in in_scope]
        if cls.retrieval_query_en and cls.retrieval_query_en.strip():
            queries.append(cls.retrieval_query_en.strip())
        result = self.retriever.retrieve(queries, county=county, include_superseded=req.include_superseded,
                                         intents={a.intent for a in in_scope})
        diag.retrieval_queries = result.queries
        scores = normalised_scores(result.candidates)
        chunks: list[Chunk] = [c.chunk for c in result.candidates]
        by_id = {c.chunk_id: c for c in chunks}
        diag.retrieved = [RetrievedRef(chunk_id=c.chunk.chunk_id, score=scores[c.chunk.chunk_id], used=False,
                                       doc_title=c.chunk.doc_title, page_label=c.chunk.page_label,
                                       publish_date=c.chunk.publish_date, authority=c.chunk.authority,
                                       lexical_rank=c.lexical_rank, dense_rank=c.dense_rank)
                          for c in result.candidates]
        if not chunks:
            return finish(self._boundary(trace_id, "not_in_documents", lang, diag, declared, resolved, intent))
        if intent in FIGURE_INTENTS and all(c.authority == "secondary" for c in chunks):
            diag.guardrail_hits.append("secondary_only_retrieval")
            return finish(self._boundary(trace_id, "not_in_documents", lang, diag, declared, resolved, intent))

        # 4. generate ------------------------------------------------------------
        try:
            draft, usage = self.llm.generate(queries[:len(in_scope)], chunks, lang)
        except Exception as exc:
            log.warning("generation error trace=%s: %s", trace_id, type(exc).__name__)
            diag.guardrail_hits.append("generation_error")
            return finish(self._boundary(trace_id, "guardrail_fallback", lang, diag, declared, resolved, intent,
                                         text=FALLBACK_LINE[lang]))
        self._record_usage(diag, usage)
        if draft.insufficient or not draft.answer.strip():
            return finish(self._boundary(trace_id, "not_in_documents", lang, diag, declared, resolved, intent))

        # 5. citation check -----------------------------------------------------
        check = check_draft(draft, by_id, declared)
        if not check.ok:
            diag.guardrail_hits.extend(check.hits)
            log.info("draft discarded trace=%s hits=%s", trace_id, check.hits)
            return finish(self._boundary(trace_id, "guardrail_fallback", lang, diag, declared, resolved, intent,
                                         text=FALLBACK_LINE[lang]))

        # 6. assemble ------------------------------------------------------------
        used_ids = set(check.cited_chunk_ids) | {r.chunk_id for r in draft.requirements}
        for ref in diag.retrieved:
            ref.used = ref.chunk_id in used_ids
        citations = [self._citation(by_id[cid]) for cid in check.cited_chunk_ids]
        requirements = [Requirement(flag=r.flag, label_en=r.label_en, label_sw=r.label_sw, chunk_id=r.chunk_id,
                                    missing=(declared.get(r.flag) is False)) for r in draft.requirements]
        declines = self._declines(cls, draft, lang)
        return finish(RagAnswer(trace_id=trace_id, outcome="cite", intent=intent, language=lang, text=draft.answer.strip(),
                                rendered_text="", citations=citations, declines=declines, requirements=requirements,
                                resolved=resolved, declared=declared, diagnostics=diag))

    def search(self, query: str, *, county: str | None, cycle: str | None, include_superseded: bool,
               top_k: int, intent: str | None = None) -> list[SearchHit]:
        result = self.retriever.retrieve([query], county=geo.normalise_county(county) or county, cycle=cycle,
                                         include_superseded=include_superseded, top_k=top_k,
                                         intents={intent} if intent else None)
        scores = normalised_scores(result.candidates)
        return [SearchHit(chunk=c.chunk, score=scores[c.chunk.chunk_id], lexical_rank=c.lexical_rank,
                          dense_rank=c.dense_rank) for c in result.candidates]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _record_usage(diag: Diagnostics, usage: LLMUsage) -> None:
        if usage.model:
            diag.cache_read_input_tokens = (diag.cache_read_input_tokens or 0) + usage.cache_read_input_tokens

    @staticmethod
    def _citation(c: Chunk) -> Citation:
        return Citation(doc_id=c.doc_id, chunk_id=c.chunk_id, doc_title=c.doc_title, page=c.page, page_label=c.page_label,
                        publish_date=c.publish_date, authority=c.authority, short_cite=c.short_cite)

    def _declines(self, cls: Classification, draft: Draft, lang: Lang) -> list[str]:
        out: list[str] = []
        if cls.personal_record_claims:
            out.append(DECLINE_PERSONAL_RECORD[lang])
        if cls.trend_claims:
            out.append(DECLINE_TREND[lang])
        if cls.out_of_scope_asks:
            out.append(DECLINE_OUT_OF_SCOPE[lang])
        for d in draft.declines:
            d = d.strip().rstrip(".")
            if d and d not in out and len(d) <= 80:
                out.append(d)
        return out[: self.s.max_declines_returned]

    def _boundary(self, trace_id: str, kind: str, lang: Lang, diag: Diagnostics, declared: dict, resolved: Resolved,
                  intent: str = "out_of_scope", text: str | None = None) -> RagAnswer:
        if text is None:
            text = (BOUNDARY_OUT_OF_SCOPE if kind == "out_of_scope" else BOUNDARY_NOT_IN_DOCUMENTS)[lang]
        return RagAnswer(trace_id=trace_id, outcome="boundary", boundary_kind=kind, intent=intent, language=lang, text=text,
                         rendered_text=text, declared=declared, resolved=resolved, diagnostics=diag)


def build_pipeline(settings: Settings | None = None, *, store: ChunkStore | None = None, llm: LLM | None = None) -> RagPipeline:
    s = settings or get_settings()
    store = store or ChunkStore(s.db_path)
    embedder = CachedQueryEmbedder(build_embedder(s))
    retriever = Retriever(store, embedder, s)
    llm = llm or build_llm(s)
    return RagPipeline(store, retriever, llm, s, Ingester(store, embedder, s))
