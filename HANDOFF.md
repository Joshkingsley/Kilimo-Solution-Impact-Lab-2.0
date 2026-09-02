# Handoff — Nitapata? (Kilimo track, AI Mashinani)

Last session: 2026-09-02 (Day 0 complete). Repo: `~/Desktop/AI Mashinani/Kilimo` (git, branch `main`).

## What this is

**Nitapata?** — citation-locked SMS assistant answering fertiliser subsidy
**eligibility, process and price** questions for Kenyan smallholders. Every
figure cites `[Document, Page, Date]` from a public-document corpus, or the
answer is replaced with a fallback line. Three outcomes only: cited answer /
one clarifying question / stated boundary. Never a fourth.

## Fixed rules (non-negotiable, from the hackathon brief)

1. No agronomic advice. 2. Not a marketplace (no payments/credit/insurance).
3. A figure with no document behind it is a failed guardrail.
4. No farmer database — corpus is the only persistent store.
Rejected in ideation, do not resurrect: satellite risk scoring, yield
prediction, insurance, credit.

## State of play

| Artefact | Status |
|---|---|
| `SPEC.md` | **Approved** (2026-09-02, no changes) |
| `index.html` | Team build hub (demo mock, pins, plan, collab steps) — done |
| `wrangler.toml`, `pyproject.toml` | Scaffolded |
| `corpus/sources.yaml` | 7 real sources registered, tiered, `wanted_not_found` gaps logged |
| `corpus/raw/` | NCPB FAQ PDF + 5 government HTML pages snapshotted |
| `corpus/chunks.jsonl` | 20 hand-cut chunks, verbatim from real documents, page/date/cycle/county tagged |
| `demo/nitapata_demo.py` | Rules-based (no LLM yet) pipeline demo — runs clean, verified |
| `evals/messages.yaml` + `test_outcomes.py` | 12/≥40 seeded cases, all passing |
| Worker/ingestion build code | **None yet** — Day 1 |

## The two HACKATHON pins (brief, 11:00 wall) — DONE, in `docs/pins/`

1. **Who this helps, as a real role** — registered smallholder on the NFSP,
   feature phone, about to walk to a collection point (persona Nafula, Malava,
   Kakamega). `docs/pins/PIN-1-who-this-helps.md`
2. **Risk level under Kenya's AI Bill 2026** — declared **high risk** by the
   Bill's sector list (agriculture; Senate Bills No. 4 of 2026, Digest p.10),
   built to the high-risk obligations. One page:
   `docs/pins/PIN-2-ai-bill-2026-risk-sheet.md` (+ Parliament digest PDF).
   The sijui rule ("I don't know" correctly beats a guess) = outcome 3 + the
   citation check.

**Spec is now v1.1** — merged from the original approved spec and the
team-contract draft (both in `docs/archive/`). Outcome classes are exactly
`cite | clarify | boundary`; the fallback line is a `boundary` with
`guardrail_hits` recording why. Demo and evals aligned; 12/12 green.

## The two Day-0 BUILD DECISIONS (spec §5A) — CLOSED 2026-09-02

1. **Demo county: Kakamega, not Machakos.** Machakos has exactly one usable
   document (KNA Machakos, 26/03/2025) — it names "four allocated depots"
   with no names and **no price figure anywhere**. It fails the bag-price
   question outright. Kakamega has a dated price trail (KSh 2,500 →
   26/02/2026 → KSh 2,000 24/08/2026) plus a depot-count figure, and inherits
   eligibility/process from the national NCPB FAQ. The demo persona must be
   rewritten from Nyambura/Machakos to a Kakamega farmer (placeholder name
   "Nafula" used in the demo script — rename freely).
   Document-answerability table:
   | Question | Kakamega | Machakos |
   |---|---|---|
   | Depot serves me? | Partial — 20 points exist, none named anywhere online | Partial — "four depots", none named |
   | What am I missing? | Yes (NCPB FAQ) | Yes (same national FAQ) |
   | Bag price this cycle? | **Yes, cited, dated** | **No document exists** |
2. **Citation hierarchy: confirmed as specced.** Tier 1 = Ministry
   (kilimo.go.ke) + NCPB (ncpb.co.ke). Tier 2 = county government sites.
   Tier 3 = Kenya News Agency (kenyanews.go.ke) statements, used only when no
   tier-1/2 document covers the fact (e.g. Machakos depot count). Media
   (Star, Nation, Kenyans.co.ke) are discovery aids only — never citable.
   Encoded in `corpus/sources.yaml`.
   **Gap, not blocking but flagged for the pitch:** no Kenya Gazette notice
   for the NFSP was locatable — gazettes.africa and new.kenyalaw.org search
   returned nothing on 2026-09-02, and new.kenyalaw.org blocks fetches with a
   403. The programme's "legal basis" claim currently has no Gazette anchor.
   Retry with Gazette Vol/No browsing rather than free-text search, or drop
   the legal-basis claim from the pitch if not found by Day 3.

## Other document-hunt gaps (logged in `corpus/sources.yaml: wanted_not_found`)

- No Machakos County Government document on the subsidy exists (checked
  machakos.go.ke directly — nothing).
- Kakamega's 20 collection points are never named anywhere online (only
  counted: "16 county-managed + 4 NCPB").
- No Ministry/NCPB primary statement for the Aug-2026 KSh 2,000 cut — only
  the county government's restatement and media coverage of a presidential
  church-service announcement (23/08/2026, Taita Taveta). If a kilimo.go.ke
  or ncpb.co.ke statement surfaces, prefer it as the tier-1 citation instead.
- KIAMIS's current 7-step e-voucher redemption flow has no primary-source
  write-up found; the only detailed steps online are from a 2020 pilot-era
  article (snapshotted as `kenyanews-evoucher-unveiled.html`, dated
  2020-08-27 — pre-dates the current programme, do not cite as current
  process without re-verifying against kiamislive.kalro.org).

## Architecture (agreed in spec)

Python 3.12 ingestion CLI (pdfplumber + tesseract OCR) → chunk with page
metadata → Workers AI `bge-m3` embeddings → Cloudflare Vectorize + D1.
TypeScript Worker per-message pipeline: normalise → scope gate → split
compound → resolve depot (one clarifying Q; hashed-key KV, 15-min TTL, the
only per-farmer state) → retrieve (cycle+county filter) → generate (Claude
Haiku 4.5, cite-or-refuse) → post-generation citation check → GSM-7 reply
≤2 segments. Channels: Africa's Talking sandbox + Astro demo UI with judge
citation panel. Eval harness `evals/messages.yaml` ≥30 cases asserting
outcome class; recorded-replay demo mode for dead venue Wi-Fi.

## Verified document sources

- NCPB subsidy FAQ (PDF): ncpb.co.ke/wp-content/uploads/dlm_uploads/2022/10/Faqs-A3.pdf
- Ministry NFSP statements + subsidy policy framework: kilimo.go.ke
- KIAMIS (registration, 7-step e-voucher flow): kiamislive.kalro.org
- County example: kakamega.go.ke KSh 2,000 price announcement
- Gazettes: new.kenyalaw.org → Gazettes; gazettes.africa/gazettes/ke
- Known fact: price moved KSh 2,500 → 2,000 mid-programme → cycle tags +
  document dates on every chunk are mandatory.

## Next session — in order (Day 1)

1. Rewrite the demo persona from Nyambura/Machakos to a Kakamega farmer
   (index.html team hub + demo script) to match the decided pin.
2. Retry the Gazette search (Vol/No browsing on new.kenyalaw.org, not
   free-text) — nice-to-have, not blocking.
3. Build the real Python ingestion CLI (`nitapata ingest`) per SPEC.md
   scope item 2, replacing the hand-cut `corpus/chunks.jsonl` with the real
   pdfplumber/tesseract → chunk → embed pipeline against the sources already
   registered in `corpus/sources.yaml`.
4. Push chunks + metadata to Cloudflare Vectorize + D1 (need
   `wrangler d1 create` / `wrangler vectorize create` — `wrangler.toml` has
   placeholder IDs marked `REPLACE_AFTER_...`).
5. Retrieval sanity check against the three Kakamega-persona questions.
6. Gate on `bge-m3` Kiswahili/Sheng quality; hosted multilingual embedding
   API is the fallback per SPEC.md open question 4.
7. Then Days 2–4 per SPEC.md build plan (Worker pipeline replaces
   `demo/nitapata_demo.py`'s templated generation with real Haiku calls;
   grow `evals/messages.yaml` from 12 to ≥40 cases).

## Kickoff prompt for next session

> Continue the Nitapata? build in ~/Desktop/AI Mashinani/Kilimo. Read
> HANDOFF.md and SPEC.md first — spec is approved, both pins are decided
> (Kakamega demo county, tiered citation hierarchy). Start Day 1: build the
> real ingestion CLI against corpus/sources.yaml, push to Vectorize + D1,
> and run the retrieval sanity check on the three Kakamega-persona questions.

## Spec v1.2 (merged with teammate's v0.2 contract update)

Teammate pushed a v0.2 draft to GitHub mid-session; folded into `SPEC.md` as
v1.2 (their draft archived at `docs/archive/SPEC-0.2-contract-draft.md`). New
in v1.2: requirements + gap components with a closed `DeclaredFlag` enum
(§4.1), corpus coverage checklist filled from Day 0 (§7.5), frozen reply
template + trim order, 306-char budget (§9.5), keyword commands EN/SW/
MSAADA/STOP (§9.6), translate-before-retrieve as first embedding fallback,
evals floor 40, simulator-first handset criterion. **Open Day-0 item: request
the Africa's Talking sender ID — not done, do first on Day 1.** The remote
also has a teammate FastAPI skeleton under `app/` — fold into `ingest/` or
remove on Day 1.

## SECURITY — action required (2026-09-02)

A teammate committed a live Africa's Talking API key (username
`ClearPath_Credit`, sender `AFTKNG`) in `app/sendsms.py` (remote commit
`46f3be6`). The repo is public, so the key is compromised regardless of the
follow-up fix. **Rotate it in the Africa's Talking dashboard now.** The file
now reads `AT_USERNAME` / `AT_API_KEY` / `AT_SENDER_ID` from the environment
(see `.env.example`); `.env` is git-ignored. Do not paste keys into source again.

## SMS webhook — built 2026-09-02 (automated part of the live demo)

`app/main.py` (FastAPI): `POST /sms/inbound` receives Africa's Talking
callbacks, HMAC-hashes the number, runs the pipeline, replies via the AT SDK
(or logs in `DRY_RUN`). `POST /demo/message` returns full judge-panel
diagnostics without sending. Keywords `MSAADA`/`HELP`/`STOP` per spec §9.6.
Scripts: `scripts/run_sms.sh`, `scripts/tunnel.sh`, `scripts/smoke_sms.sh`.
Tests: `tests/test_webhook.py` (8) + `evals/` (12) — 20 green.
**Manual steps that remain are in `docs/SMS-LIVE-RUNBOOK.md`** (rotate key,
sandbox key, tunnel install, callback URL, simulator, sender-ID request).

## Presentation redesign, 2026-09-02 (design-taste-frontend skill)

`index.html` rewritten as a modern, minimalist team pitch hub (design read:
redesign-overhaul, trust-first/civic-tech register, native CSS single-file
architecture, no build step). Design language: mono type renders anything a
farmer or judge could verify against a document (citations, dates, chunk
ids); sans renders everything else. One locked accent (forest green), status
colours (amber/clay) reserved strictly for the cite/clarify/boundary system,
consistent light/dark tokens. Demo phone thread updated to the Kakamega/Nafula
personas per SPEC.md §6; the old stale Machakos-vs-Kakamega and citation-tier
"pins" framing is gone from the page (that's now §5A build decisions, noted
as a footnote, not one of the two hackathon pins).

**The two pins now live on their own page, `pins.html`**, linked from a
compact teaser card on the hub and from top-nav "Pins". Shared design tokens
factored into `styles.css`, linked by both pages, no duplication drift.

Zero em/en-dashes anywhere (mechanically verified). Both pages parse clean
with Python's `html.parser` and serve 200 over a local static server.

## Working demo (without the shortcode) — built 2026-09-02, later session

Everything behind the webhook is now real code, not the Day-0 stand-in:

| Piece | Where | State |
|---|---|---|
| Ingestion CLI | `ingest/` (`python3 -m ingest.cli fetch|parse|ingest|verify|stats`) | Parses the 8 snapshots into 60 Interface-A chunks with locators (PDF Q&A blocks, HTML paragraphs, curated override for the KIAMIS portal). sha256 verified against `sources.yaml`. |
| Pipeline package | `nitapata/` (intents, state, retrieve, generate, guardrails, render, pipeline) | Steps 1–8 of SPEC §9.2 incl. clause-level scope gate, declared state + gap, requirements component, frozen template + trim order, GSM-7 segmentation, keyword commands EN/SW/MSAADA/HELP/STOP. |
| Retrieval | `nitapata/retrieve.py` (LocalIndex) | Lexical index with intent/county/cycle filters, authority rerank, newest-wins, FAQ anchor chunks. **Vectorize/D1 + the TS Worker are NOT built**: they need `wrangler login` (manual) and the Python path is the working demo. Same record shape, swap later. |
| Claude Haiku | `nitapata/generate.py`, `nitapata/intents.py` | Structured-output classification + cite-or-refuse generation, `claude-haiku-4-5`, prompt-cached system block, key-gated by `ANTHROPIC_API_KEY`. **Untested live** (no key on the build machine); every path falls back to templates on any API error and the deterministic citation check runs regardless. |
| Judge panel | `web/judge.html` served at `/judge`; `/replay` serves `web/recorded_run.json` | Phone thread + outcome, citations, retrieved chunks (used/unused), guardrail hits, declared state; "Seed a bad figure" button; labelled recorded-run mode. Plain static HTML, not Astro (deliberate: zero build step). |
| Evals | `evals/messages.yaml` (47 cases), `evals/test_outcomes.py`, `tests/test_webhook.py` | 57 tests green on the templated path (`NITAPATA_USE_LLM=0`). Covers all six intents, stale-price traps, injection, personal-record lookup, clarify-once, keywords, contradiction-latest-wins, budget. |
| Webhook | `app/main.py` | `/sms/inbound` → pipeline → Africa's Talking (or DRY_RUN); `/demo/message`; `/health`. |

Run it: `scripts/run_sms.sh` then open http://localhost:8000/judge. Manual steps for a
phone are still in `docs/SMS-LIVE-RUNBOOK.md` (sandbox key, tunnel, callback URL;
a shortcode is the one thing that can't be automated).

Known limits, stated: Kiswahili replies are templated (Haiku off without a key);
Machakos has no price document so that fallback is real; no Gazette located.
