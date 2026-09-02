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
