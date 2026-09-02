# Nitapata RAG — how it works and how to integrate

The RAG service turns a farmer's message into exactly one of three outcomes — a **cited answer**, **one clarifying question**, or a **fixed boundary line** — using only the public documents registered in `corpus/sources.yaml`. It implements SPEC §7 (corpus), §8 (frozen interfaces), §9.1 (ingestion) and §9.2–9.4 (scope gate → retrieve → generate → citation check). SMS segmentation (§9.5), keyword commands (§9.6) and the 15‑minute KV state remain the channel layer's job; the RAG is stateless and receives that context per request.

Code lives in `app/rag/`. It is mounted into the existing FastAPI app (`app/main.py`) under `/v1/rag/*` and also exposed as a CLI (`python -m app.rag.cli`).

---

## 1. Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env           # fill in RAG_API_KEYS, RAG_ADMIN_API_KEYS, ANTHROPIC_API_KEY

# build the index from the committed snapshots — no network needed
.venv/bin/python -m app.rag.cli ingest
.venv/bin/python -m app.rag.cli stats
.venv/bin/python -m app.rag.cli verify                       # the three Nyambura questions (SPEC §9.1 step 5)

# ask from the terminal
.venv/bin/python -m app.rag.cli ask "Bei ya mbolea Kakamega ni ngapi?"
.venv/bin/python -m app.rag.cli ask "nikienda depot nikuje na nini? nina ID lakini sijapata SMS ya mgao"

# serve
.venv/bin/uvicorn app.main:app --reload --port 8000
curl -s localhost:8000/v1/rag/health
```

Offline / no API key: set `LLM_PROVIDER=fake` (and `ALLOW_FAKE_LLM_IN_API=true` if you want `/answer` to serve it). The fake is a rule-based stand‑in for tests and demos; every guardrail still runs on its output, but answer wording is extractive and rough.

Tests: `.venv/bin/python -m pytest` (84 tests: chunking, store, retrieval filters, guardrails, pipeline contract, API security, Claude request shape with a mocked client, plus the team's webhook and eval suites).

---

## 2. Architecture

```
 OPERATOR (periodic)                                  PER MESSAGE (stateless)
 ───────────────────                                  ───────────────────────
 corpus/sources.yaml  ── register: the only way        AnswerRequest {text, declared, county, clarify_used, …}
        │                a document enters                      │
        ▼                                                       1  classify  (Claude, structured output)
 fetch  → corpus/raw/<doc>   https-only, allowlisted hosts,        scope gate · split compound asks · intent
          sha256 pinned      public IPs only, byte cap             county/depot · declared flags · language
        │                                                       │   out of scope ──────────────► BOUNDARY (fixed line)
        ▼                                                       │   county needed & unknown ────► CLARIFY (once)
 parse  → pages   pdfplumber (column-aware) / HTML paragraphs /  ▼
        │         text with \f page breaks                     2  retrieve  (SQLite: FTS5 BM25 + dense cosine)
        ▼                                                          filter cycle+county → RRF → authority/recency
 chunk  → page-bounded, ~300 tokens, 1-sentence overlap            register hints (use_for / do_not_use_for)
        │                                                       │   nothing relevant ───────────► BOUNDARY (not in documents)
        ▼                                                       ▼
 embed  → local hash | Cloudflare bge-m3 | Voyage              3  generate  (Claude Haiku, cite-or-refuse, JSON)
        │   cached by sha256(text)                                 {answer, answer_chunk_ids, requirements[], declines[]}
        ▼                                                       │   insufficient ───────────────► BOUNDARY (not in documents)
 data/nitapata.sqlite3                                          ▼
   documents · chunks · chunks_fts · embeddings                4  citation check  (deterministic, no model)
                                                                │   any hit ────────────────────► BOUNDARY (fallback line) + guardrail_hits
                                                                ▼
                                                               5  assemble RagAnswer  → citations from metadata,
                                                                  requirements + gap from declared state, declines,
                                                                  rendered_text in the frozen §9.5 order
```

### 2.1 Ingestion (`app/rag/ingest.py`, `corpus.py`, `parse.py`, `chunk.py`)

1. **Register.** `corpus/sources.yaml` is hand-maintained (SPEC §7.4). Each entry carries `doc_id`, `title`, `short_cite`, `publisher`, `doc_type`, `authority`, `url`, `raw_path`, `publish_date`, `cycle`, `county`, `lang`, and the retrieval hints `use_for` / `do_not_use_for`. The loader validates every entry (slug-shaped ids, `raw_path` confined to `corpus/raw/`, known counties) and refuses duplicates.
2. **Snapshot.** `fetch` downloads each URL byte-for-byte into `corpus/raw/`. Only `https`, only hosts in `ALLOWED_SOURCE_HOSTS` (kenyalaw.org, gazettes.africa, ncpb.co.ke, kilimo.go.ke, kalro.org, parliament.go.ke, county sites…), only hosts that resolve to public IPs, at most 3 same-rule redirects, 25 MB cap, and a content sniff (a `pdf` entry must start with `%PDF`). Snapshots are committed; the index is rebuildable with no network (SPEC §7.3, §13.10).
3. **Parse, preserving pages.** PDFs go through pdfplumber one printed page at a time. Multi-column pages (the NCPB FAQ is a two-column A3 sheet) are detected from the x‑histogram of word positions and read column by column, otherwise the two columns interleave and Q&A lines get scrambled. Tables are appended as `cell | cell` rows so figures stay next to their labels. Pages with no text layer are recorded as skipped (they would need OCR — not implemented; a figure that only exists on a scanned page must be transcribed by hand into a `text` source). HTML pages are reduced to their `<article>`/`<main>` paragraphs with navigation, bylines, share buttons and similar boilerplate removed; plain-text sources use form-feed (`\f`) page breaks.
4. **Chunk within the page.** ~300 tokens, sentence-aligned, one sentence of overlap, table rows kept whole, duplicates dropped. A chunk never spans two pages because the page is part of the citation. For web pages the unit is the paragraph and the locator is the paragraph number.
5. **Embed and index.** Vectors are cached by `(model, sha256(text))`, so re-ingesting an unchanged chunk costs nothing. The store refuses to mix vectors from two embedding models; change `EMBEDDING_PROVIDER` and you re-ingest with `--force`.
6. **Idempotent.** A document whose snapshot hash, `ingest_version` and embedding model are unchanged is skipped.

**Citation locators.** `page_label` is what the farmer sees: `Uk.3` for a printed page, `¶7` for the seventh paragraph of a web page. `chunk_id` is `<doc_id>#p<page>#<ordinal>` and is stable across re-ingest as long as the chunking strategy (`INGEST_VERSION`) is unchanged.

### 2.2 Retrieval (`app/rag/retrieve.py`, `store.py`)

Everything happens on a single SQLite file: chunk metadata, an FTS5 full-text index (BM25), and float32 vectors held in memory as one matrix (brute-force cosine is the right tool below ~50k chunks). All SQL is parameterised; farmer text never reaches a `MATCH` expression unquoted.

Order of operations, per query:

1. **Filter before ranking** (SPEC §9.2 step 5).
   - *Cycle:* chunks from the current cycle (`CURRENT_CYCLE`) or cycle-independent documents (`cycle: null`) are eligible. A past-cycle document is eligible **only** for intents the register lists under its `use_for` (e.g. the 2022 NCPB FAQ for `eligibility`), and even then it is ranked below current-cycle chunks. This is what makes the KSh 2,500 → 2,000 change safe: the 2025 launch statement cannot be cited for `price`.
   - *County:* national chunks are always eligible; county-scoped chunks only for that county — and **never when the county is unknown**, so a Kakamega figure can never be presented as national.
   - *Register hints:* a document listed under `do_not_use_for` for the asked intent is dropped outright.
2. **Hybrid search.** The message's in-scope sub-asks plus the classifier's English rendition (translate-before-retrieve, SPEC §11 Day‑1 gate) are each run through BM25 and dense cosine. Kiswahili terms get a bilingual lexical expansion (`bei → price`, `mgao → allocation/voucher`, …) so BM25 still bites on English documents.
3. **Fusion and re-ranking.** Reciprocal Rank Fusion across all retrievers, then × authority weight (`legal_basis`/`primary` 1.0, `supporting` 0.92, `secondary` 0.6), × 0.85 for past-cycle chunks, × 1.2 for chunks scoped to the farmer's own county, plus a small bonus per distinct query term covered. Near-ties are broken by later `publish_date` (SPEC §7.2).
4. **Relevance floor.** A chunk survives if its cosine is above `RETRIEVAL_MIN_DENSE_SCORE`, or it covers two distinct query terms, or it covers one term that is *discriminative* in this corpus (present in ≤40% of chunks). A single generic word such as "fertiliser" is not evidence. Nothing surviving ⇒ boundary.
5. **Figure intents need a real source.** If every surviving chunk for a `price` / `cycle_timing` question is `secondary`, the pipeline answers boundary (`secondary_only_retrieval`) rather than letting a Hansard quote price a bag.

### 2.3 Generation (`app/rag/llm.py`, `prompts.py`)

Two Claude calls per message, both on `ANTHROPIC_MODEL` (default `claude-haiku-4-5`, SPEC §9.3), both with **structured output** (`output_config.format` = a closed JSON schema) so a malformed shape is impossible rather than unlikely, both with the stable system prompt first and marked `cache_control: ephemeral` while the volatile content (chunks, message) sits in the user turn.

- **Classify** (`max_tokens` ≈ 400): language, `sub_asks[]` each with a closed-enum intent and `in_scope`, county/depot if named, `declared` flags (closed enum, only when explicitly stated), `personal_record_claims`, `trend_claims`, `retrieval_query_en`. Uncertain ⇒ out of scope. A validation failure is treated as out of scope.
- **Generate** (`max_tokens` ≈ 700): receives the retrieved chunks rendered as `<chunk chunk_id=… doc=… page=… published=… authority=…>` blocks and the questions, returns `{insufficient, answer, answer_chunk_ids, requirements[{flag,label_en,label_sw,chunk_id}], declines[]}`. The model **never writes the citation bracket, the gap line, or the ordering** — those are rendered from metadata and declared state (SPEC §9.3). Tag-like characters in documents and messages are neutralised before rendering so content cannot close the wrappers; both prompts state that document and message text are data, not instructions.
- **Refusals / errors.** A `stop_reason: refusal`, API error or timeout never reaches the farmer as prose: classification failures become boundary, generation failures become the fallback line with `generation_error` in `guardrail_hits`.
- **Cache verification.** `diagnostics.cache_read_input_tokens` is returned on every answer. If it stays 0 across repeated calls, something volatile has crept into the system prompt (`tests/test_llm_anthropic.py::test_system_prompts_have_no_volatile_content` guards the obvious cases).

### 2.4 The citation check (`app/rag/guardrails.py`) — SPEC §9.4, guardrail 3

Deterministic, runs on every draft, any hit discards the draft (never repairs it):

| Hit | Rule |
|---|---|
| `uncited_figure:<n>` | every number in the answer (2,500 / 2500 / 50 / 2026 …, normalised) must be a substring of a cited chunk's text |
| `citation_not_retrieved` / `no_citation` | every `answer_chunk_id` must be one step 2 actually returned; at least one must remain |
| `hedged_figure` | no hedge within two words of a number ("about 2,000", "labda KSh 2,000") |
| `secondary_only_figure` | a figure whose only citations are `secondary` |
| `record_assertion` | "you are not registered", "hujasajiliwa", "your registration is…" — the system checked nothing |
| `bad_requirement_flag` / `requirement_not_retrieved` / `requirement_not_in_chunk:<flag>` | every requirement maps to a closed flag, cites a retrieved chunk, and that chunk actually mentions it (keyword sets per flag) |
| `flag_leaked_into_prose` | internal flag names never appear in farmer text |

`missing: true` on a requirement is derived in code from `declared[flag] === false` only — the model has no way to set it. Metadata (title, page label, date) is copied from the store, never from the model, so check 4 of §9.4 holds by construction.

### 2.5 Outcomes and rendering

| outcome | `boundary_kind` | text |
|---|---|---|
| `cite` | — | one answer sentence from the model; `rendered_text` = answer · `Unahitaji:`/`You need:` (1)…(2)… · `Bado huna (ulisema):`/`You still need (you said):` … · `Sijui kama:`/`Not in the documents:` … · `Chanzo:`/`Source:` short_cite, page_label, date |
| `clarify` | — | `CLARIFY_COUNTY` (asked only when `clarify_used` is false) |
| `boundary` | `out_of_scope` | `BOUNDARY_OUT_OF_SCOPE` — byte-identical every time |
| `boundary` | `not_in_documents` | `BOUNDARY_NOT_IN_DOCUMENTS` |
| `boundary` | `guardrail_fallback` | `FALLBACK_LINE`, with `diagnostics.guardrail_hits` saying why |

All constants live in `app/rag/prompts.py` and nowhere else. `rendered_text` follows the frozen §9.5 order but is **not** GSM‑7 transliterated or segmented; the channel does that and applies the trim order (declines → gap → requirements → answer elaboration; never the figure or the citation).

---

## 3. Data model (Interfaces A and B)

**Chunk** (`app/rag/schema.py::Chunk`, SPEC §8.1) — `chunk_id, doc_id, doc_title, publisher, doc_type, authority, page (≥1), page_label, publish_date, cycle, county, lang, text, token_count, source_url, retrieved_at, ingest_version`, plus `ocr` and `short_cite`. A chunk without a page cannot be constructed.

**AnswerRequest** (inbound half of §8.2 plus the per-thread context the RAG cannot hold):

```json
{
  "message_id": "ATXid_…",
  "text": "nikienda depot nikuje na nini? nina ID lakini sijapata SMS ya mgao",
  "from_hash": "<64-hex HMAC-SHA256 of the MSISDN, optional>",
  "channel": "sms",
  "declared": {"has_id": true},          // what this thread already held (KV)
  "county": "Kakamega",                  // resolved earlier in the thread, if any
  "depot": null,
  "clarify_used": false,                 // has a clarifying question already been sent this thread?
  "language_pin": null,                  // "en" | "sw" after the EN/SW keyword
  "include_superseded": false            // judge panel / debugging only
}
```

`from_hash` is validated: anything that looks like a phone number, or is not 64 hex characters, is rejected with 422. Unknown fields are rejected.

**RagAnswer** (outbound half of §8.2 minus `segments`):

```json
{
  "trace_id": "9f2c…",
  "outcome": "cite",
  "boundary_kind": null,
  "intent": "evoucher_redemption",
  "language": "sw",
  "text": "…one sentence…",
  "rendered_text": "… Unahitaji: (1) kitambulisho (2) usajili wa KIAMIS. Bado huna (ulisema): SMS ya mgao. Chanzo: NCPB FAQ, Uk.1, 2022-10-01.",
  "citations": [{"doc_id": "ncpb-faq-2022", "chunk_id": "ncpb-faq-2022#p1#1", "doc_title": "…", "short_cite": "NCPB FAQ",
                 "page": 1, "page_label": "Uk.1", "publish_date": "2022-10-01", "authority": "primary"}],
  "declines": ["rekodi yako ya usajili"],
  "requirements": [{"flag": "has_id", "label_en": "national ID", "label_sw": "kitambulisho", "chunk_id": "ncpb-faq-2022#p1#1", "missing": false},
                   {"flag": "has_allocation_sms", "label_en": "allocation SMS", "label_sw": "SMS ya mgao", "chunk_id": "…", "missing": true}],
  "resolved": {"county": "Kakamega", "depot": null, "cycle": "2026-SR"},
  "declared": {"has_id": true, "has_allocation_sms": false},
  "diagnostics": {
    "retrieved": [{"chunk_id": "…", "score": 1.0, "used": true, "doc_title": "…", "page_label": "Uk.1", "publish_date": "2022-10-01",
                   "authority": "primary", "lexical_rank": 1, "dense_rank": 2}],
    "guardrail_hits": [], "latency_ms": 812, "model": "anthropic:claude-haiku-4-5", "embedding_model": "local-hash-v1",
    "cache_read_input_tokens": 1450, "sub_asks": ["…"], "retrieval_queries": ["…"]
  }
}
```

Invariants are enforced by the model itself (a violating object cannot be built): `cite ⇒ citations ≥ 1`; `outcome ≠ cite ⇒ citations = requirements = []`; every citation and requirement `chunk_id` appears in `diagnostics.retrieved` with `used: true`; `missing ⇒ declared[flag] === false`; no field contains a phone number. The channel must carry `declared` and `resolved` back into KV (bounded shapes only, ≤15 min) and set `clarify_used` after sending a clarify.

---

## 4. HTTP API

All routes except `/health` need `X-API-Key`. Keys come from `RAG_API_KEYS` (comma-separated); ingestion additionally needs a key from `RAG_ADMIN_API_KEYS`. With no keys configured the service returns 503 for everything (fail closed). Comparison is constant-time; each key gets `RATE_LIMIT_PER_MINUTE` requests (429 + `Retry-After` beyond that). Responses carry `Cache-Control: no-store`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/rag/health` | documents/chunks indexed, embedding model, LLM, current cycle (no auth) |
| POST | `/v1/rag/answer` | full pipeline → `RagAnswer` |
| POST | `/v1/rag/search` | retrieval only (judge panel, debugging) → `[{chunk, score, lexical_rank, dense_rank}]` |
| GET | `/v1/rag/chunks/{chunk_id}` | one chunk with full metadata — the "which document, which page" panel. Encode `#` as `%23` |
| GET | `/v1/rag/stats` | per-document counts |
| POST | `/v1/rag/ingest` | admin: `{doc_ids?: [...], fetch?: bool, force?: bool}` — only ids already in `sources.yaml`; URLs are never accepted |

```bash
KEY=…   # one of RAG_API_KEYS
curl -s localhost:8000/v1/rag/answer -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"message_id":"demo-1","text":"Bei ya mbolea Kakamega ni ngapi?","channel":"demo"}' | jq '.outcome, .rendered_text, .citations'

curl -s localhost:8000/v1/rag/search -H "X-API-Key: $KEY" -H 'content-type: application/json' \
  -d '{"query":"price per 50kg bag","county":"Kakamega","intent":"price","top_k":3}' | jq '.[].chunk | {chunk_id, page_label, publish_date}'

curl -s "localhost:8000/v1/rag/chunks/kakamega-price-2026-08%23p1%231" -H "X-API-Key: $KEY" | jq .text

curl -s localhost:8000/v1/rag/ingest -H "X-API-Key: $ADMIN_KEY" -H 'content-type: application/json' -d '{"fetch":false}'
```

Errors: 401 bad key · 403 non-admin on ingest · 413 text longer than `MAX_MESSAGE_CHARS` · 422 invalid body (unknown field, phone-shaped `from_hash`, malformed `chunk_id`, unknown `doc_id`) · 429 rate limit · 503 not configured, or `LLM_PROVIDER=fake` without `ALLOW_FAKE_LLM_IN_API` · 500 unexpected (a `trace` id is returned; nothing else).

Interactive docs: `/docs`.

---

## 5. Integrating

### 5.1 From the SMS webhook in this repo (`app/main.py`)

`app/main.py` already builds the pipeline at startup (`app.state.pipeline`) and mounts the router. To have the webhook reply from the RAG instead of the Day‑0 rules stand-in, replace the `nd.handle(...)` call inside `run_pipeline` with the adapter, which returns the same dict shape the webhook and judge panel already consume:

```python
from app.rag.adapter import rag_reply

reply = rag_reply(app.state.pipeline, text, from_hash,
                  declared=state.get("declared", {}),        # from the 15-min KV entry
                  county=state.get("resolved", {}).get("county"),
                  clarify_used=state.get("clarify_used", False),
                  language_pin=state.get("language_pin"))
# reply["reply"] is the rendered text; reply["outcome"], ["citations"], ["retrieved"], ["guardrail_hits"] as before
# then: write reply["declared"] / reply["resolved"] back to KV; set clarify_used if reply["outcome"] == "clarify"
```

Keyword commands (`EN`/`SW`/`MSAADA`/`STOP`) stay in the webhook, before the RAG is called (SPEC §9.6). GSM‑7 transliteration and the 2‑segment trim also stay there; use `requirements`, `declines` and `citations` from the reply to drop components in the §9.5 order rather than cutting the string.

### 5.2 From the Cloudflare Worker or any other client

Call `POST /v1/rag/answer` over HTTPS with the API key. The Worker owns KV: read `ConvState` (SPEC §12), pass `declared`, `resolved.county/depot`, `clarify_used`, `language_pin`; write back `declared` and `resolved` from the response; expire at 15 minutes; `STOP` deletes the key without calling the RAG. Never send the MSISDN — send the HMAC or nothing.

### 5.3 Judge panel

For each `cite` answer, show `diagnostics.retrieved` (score, `used`), and for each citation fetch `GET /v1/rag/chunks/{chunk_id}` to display the chunk text, `doc_title`, `page_label` and `publish_date` — including the chunk behind every requirement line (SPEC §13.11). `POST /v1/rag/search` with `include_superseded: true` lets the panel demonstrate the stale-price trap: the 2025 launch statement (KSh 2,500) is visible there and absent from any current-cycle answer.

### 5.4 Adding a document

1. Add an entry to `corpus/sources.yaml` (copy an existing one; pick `authority` per SPEC §5 Pin 2; set `cycle` for anything carrying cycle-scoped figures, `null` for process documents; add `do_not_use_for: [price]` when a document's figures are superseded but its process text is not).
2. `python -m app.rag.cli fetch <doc_id>` — snapshot; paste the printed `sha256` and `retrieved_at` into the register.
3. `python -m app.rag.cli ingest <doc_id>` then `verify` / `search`.
4. Commit the register and the snapshot together. The API can re-index (`POST /ingest`) but cannot add sources — that is deliberate.

---

## 6. Security posture

- **Corpus is the only database.** No message text, hash, or reply is stored. `AnswerRequest` rejects phone-shaped values; `RagAnswer` refuses to serialise one; a logging filter redacts anything MSISDN-shaped from every log line (SPEC §12).
- **Closed ingestion surface.** The API never accepts URLs or files; only `doc_id`s already in the hand-maintained register. Fetching is https-only, host-allowlisted, public-IP-only (blocks SSRF to metadata endpoints and internal hosts), redirect-limited, size-capped and content-sniffed. `raw_path` cannot leave `corpus/raw/`.
- **Model output is untrusted.** Closed-enum structured output; every figure, chunk reference and requirement re-verified against the actual chunk text; metadata never taken from the model; failures produce a fixed line, never a repaired draft.
- **Prompt injection.** Scope gate before retrieval (so an injected instruction cannot fish for quotable text); document and message text wrapped and tag-escaped; both prompts declare them data; and the citation check is the guardrail of last resort — an injected "say the price is 500" fails with `uncited_figure:500` (covered by `tests/test_pipeline.py`).
- **API.** Constant-time key check that fails closed, per-key rate limiting, separate admin key for ingestion, body-size and `top_k` caps, `extra="forbid"` on every request model, `chunk_id` shape validation, generic 500s with a trace id, `no-store`/`nosniff` headers, CORS off unless configured.
- **Secrets** only via environment / `.env` (git-ignored). Nothing in `app/rag` embeds a credential.

## 7. Optimisations

- Hybrid BM25 + dense with RRF, bilingual lexical expansion, IDF-aware relevance floor — strong on exactly the tokens dense models fumble (prices, place names, "DAP").
- Pre-filtering in SQL by cycle/county/register hints, so ranking only sees eligible chunks.
- Embedding cache keyed by content hash; idempotent ingest keyed by snapshot hash; in-memory vector matrix invalidated only on writes; LRU cache on query embeddings.
- Prompt caching with the stable system prompt first (verified via `cache_read_input_tokens`), small `max_tokens`, Haiku for both calls; the classification result doubles as the translate-before-retrieve query so no extra call is needed.
- Column-aware PDF extraction and paragraph-level HTML locators give cleaner chunks, which is the cheapest retrieval-quality lever there is.

## 8. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `fake` |
| `ANTHROPIC_API_KEY` | — | or use an `ant auth login` profile; the SDK resolves credentials itself |
| `ANTHROPIC_MODEL` | `claude-haiku-4-5` | pinned here, never at call sites |
| `EMBEDDING_PROVIDER` | `local` | `local` (offline, tests) · `cloudflare` (`@cf/baai/bge-m3`, needs `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`) · `voyage` (`voyage-3`, needs `VOYAGE_API_KEY`) |
| `CURRENT_CYCLE` | `2026-SR` | cycle retrieval filters to (matches `wrangler.toml`) |
| `DEFAULT_COUNTY` | empty | leave empty so the pipeline asks (SPEC §6.2) |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_CANDIDATES` / `RETRIEVAL_MIN_DENSE_SCORE` | 6 / 40 / 0.25 | ranking knobs |
| `CHUNK_TARGET_TOKENS` / `INGEST_VERSION` | 300 / 1 | bump `INGEST_VERSION` when chunking changes, then `ingest --force` |
| `RAG_API_KEYS` / `RAG_ADMIN_API_KEYS` | empty | comma-separated; empty = service refuses |
| `RATE_LIMIT_PER_MINUTE` / `MAX_MESSAGE_CHARS` | 60 / 1000 | |
| `ALLOW_FAKE_LLM_IN_API` | `false` | guard against serving the stub |
| `DB_PATH` | `data/nitapata.sqlite3` | index location (git-ignored) |

CLI: `python -m app.rag.cli fetch|ingest|stats|verify|search|ask` (`--help` on each).

## 9. Known limitations and next steps

- **No live Claude run yet.** The Anthropic path is exercised with a mocked client (request shape, caching flags, structured output schema, refusal handling); a real key was not available in this environment. First live run: `LLM_PROVIDER=anthropic python -m app.rag.cli verify`, then check `cache_read_input_tokens` is non-zero on the second call.
- **Local embedder is lexical, not semantic.** Fine with BM25 on this 25-chunk corpus; switch to `cloudflare` or `voyage` for the Day‑1 embedding gate and re-ingest.
- **Web-page paragraph numbers (`¶N`)** are derived from our own boilerplate stripping and may differ from a hand count of the page. The judge panel shows the chunk text, which is the honest anchor.
- **OCR is not implemented.** Scanned pages are reported as skipped; transcribe by hand into a `text` source with `\f` page breaks.
- **Kiswahili wording** of the boundary, fallback, clarify and label constants in `app/rag/prompts.py` needs the Lane‑D fluent-speaker review (SPEC §13.18).
- The `FakeLLM` is extractive: with a real model, answers are single clean sentences; with the stub they are the best-matching sentence from the top chunk.
