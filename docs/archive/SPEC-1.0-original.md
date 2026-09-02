# Spec: Nitapata? — citation-locked fertiliser subsidy assistant

Status: Approved (2026-09-02, no changes)        Date: 2026-09-02
Client: internal (AI Mashinani hackathon — Kilimo track)    Type: tool + automation

## Problem

A smallholder like Nyambura in Machakos wants three answers before she walks
to a depot: does the Kangundo depot serve me, what am I missing to qualify,
and how much is the bag this cycle. Today those answers live scattered across
Kenya Gazette notices, NCPB circulars, ministry press statements and county
advisories — documents she will never read, on websites she can't afford to
browse. Meanwhile the price itself moved mid-programme (KSh 2,500 → KSh 2,000
per 50kg bag), so word-of-mouth answers are stale and a wasted trip costs a
day's work.

Nitapata? answers eligibility, process and price questions over SMS, in
Kiswahili/Sheng/English, with every figure traceable to a named public
document and page. It either answers with a citation, asks exactly one
clarifying question, or states its boundary. There is no fourth option.

## Users

- **Farmer (SMS)** — feature phone, no data bundle, code-switching messages.
  Needs a correct, cited, ≤2-SMS answer or an honest "that's not in the
  public documents".
- **Judges / demo audience (web)** — need to see the citation discipline
  working live: the retrieved chunk and page displayed beside each answer,
  and clean refusals under adversarial prompts.
- **Operator (Nyaenya)** — runs the corpus sync when a new cycle's documents
  land; needs it to be one scripted command, not manual admin.

## Fixed rules (from the brief — non-negotiable)

1. **No agronomic advice.** Eligibility and process, never what/when to plant
   or what will happen to a crop.
2. **Not a marketplace.** No buying, selling, payments, credit, insurance —
   not even brokering the conversation toward one.
3. **A figure with no document behind it is a failed guardrail.** Every
   number or claim cites `[Document, Page, Date]` from the retrieved corpus,
   or the answer is replaced with the fallback line.
4. **No farmer database.** No accounts, no registration records, no history
   across sessions. The only persistent store is the public-document corpus.

## Scope

1. **Source registry** — `corpus/sources.yaml`: every document with URL,
   publisher, publish date, cycle tag, county/depot tags. The registry is the
   single place a new document enters the system.
2. **Ingestion pipeline (Python CLI)** — `nitapata ingest`: fetch PDFs/pages
   from the registry, parse (OCR fallback for scanned gazettes), chunk with
   page numbers preserved, embed (multilingual model), push chunks + metadata
   to Cloudflare Vectorize and D1. Idempotent and re-runnable; run when
   online, periodically — not real-time.
3. **Seed corpus** — minimum viable set, all real documents:
   - NCPB Government Subsidised Fertiliser Programme FAQ (process, redemption steps)
   - ≥1 Ministry of Agriculture NFSP statement from kilimo.go.ke (cycle launch, price)
   - ≥1 Kenya Gazette notice relevant to the programme (via Kenya Law / Gazettes.Africa)
   - ≥1 county advisory (Machakos preferred to match the demo persona; fall
     back to whichever county has the best published documents, e.g. Kakamega's
     price-reduction announcement)
   - KIAMIS/e-voucher registration process description (7-step redemption flow)
4. **Answer service (Cloudflare Worker, TypeScript)** — one endpoint serving
   both the SMS webhook and the demo UI. Pipeline per message:
   a. normalise (Kiswahili/Sheng/English detection, spelling variance);
   b. **pre-generation scope gate** — intent classification (rules + Haiku
      call); out-of-scope intents (agronomy, credit, insurance, weather/risk)
      are refused with the standard boundary line *before* retrieval;
   c. compound-message split — answer the in-scope parts, decline the rest
      explicitly, never silently drop half;
   d. location/depot resolution — if price or service area varies and no
      location was given, ask the one clarifying question (ward/depot);
   e. retrieval from Vectorize filtered by current cycle + county tags;
   f. generation (Claude Haiku 4.5) under a strict cite-or-refuse prompt;
   g. **post-generation citation check** — every number/named claim in the
      draft must string/semantic-match a retrieved chunk; otherwise discard
      and send the fallback line ("hii si kwenye hati za umma…");
   h. SMS segmentation — GSM-7 aware, target ≤2 segments, citation included.
5. **Clarifying-question state** — ephemeral KV entry keyed by hashed phone
   number: `{pending_intent, ts}` only, 15-minute TTL, auto-expiring. No
   message content stored, nothing survives the TTL. This is the entire
   extent of per-farmer state and it is documented as such.
6. **SMS channel** — Africa's Talking sandbox integration (inbound webhook +
   outbound reply). Real shortcode is out of scope for the hackathon.
7. **Demo UI (Astro, static)** — phone-framed SMS thread talking to the same
   Worker, plus a judge panel showing, per answer: the retrieved chunks, the
   document name, page and date, and which of the three outcomes fired
   (cited answer / clarifying question / boundary). This panel *is* the pitch.
8. **Eval harness** — `evals/messages.yaml`: ≥30 scripted messages covering
   the six intents plus adversarial cases (loan ask, insurance ask, "when
   should I plant", compound asks, no-location price ask, price-comparison
   ask, "registration problem last time"). Each has an expected outcome class
   (cite / clarify / boundary) and, for cites, the expected document. A pytest
   run replays them against the Worker and fails on any wrong outcome class.
9. **Demo-mode fallback** — if the venue has no connectivity, the demo UI
   replays the eval harness transcript locally (clearly labelled "recorded
   run"), so the pitch never dies on Wi-Fi. Honest labelling required.

## Non-goals

Explicitly not in this build (most were proposed and rejected in ideation
because they violate the fixed rules):

- Satellite-based risk-of-loss assessment, yield/days-to-reap prediction —
  agronomic inference with no document behind it.
- Insurance or credit offering/brokering — transactions, marketplace.
- Any agronomic advice, however hedged.
- Farmer accounts, registration-status lookup, cross-session memory.
- Custom payment flows of any kind.
- USSD, IVR/voice, WhatsApp — SMS + web demo only for this build.
- Production shortcode approval, multi-county corpus completeness — one
  county done properly beats eight done vaguely.
- Answering historical comparisons the corpus can't support ("has the price
  gone up?") — the system states the current cited figure and declines the
  comparison.

## Constraints

- **Stack**: Python 3.12 ingestion (pypdf/pdfplumber + tesseract OCR
  fallback, ruff, pytest); TypeScript strict Worker; Cloudflare Workers +
  Vectorize + D1 + KV; Workers AI `bge-m3` for multilingual embeddings;
  Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for classification +
  generation; Africa's Talking sandbox; Astro demo UI. `wrangler.toml`
  committed; secrets via `wrangler secret`.
- **Farmer-side offline**: the farmer needs only GSM/SMS — no data. Server
  side is online; corpus sync is a periodic online job.
- **Deadline**: hackathon timeline — build plan below assumes ~4 working days.
- **Budget**: sandbox/free tiers throughout; Haiku for all LLM calls.
- **Language**: replies in the language of the incoming message (default
  Kiswahili), British English in code/docs.
- **Privacy**: no farmer PII at rest beyond the 15-min hashed-key KV entry;
  logs strip phone numbers.

## Architecture flow

```
[ONLINE, PERIODIC]                      [PER MESSAGE]
Gazette / NCPB / kilimo.go.ke           Farmer SMS ──► Africa's Talking ──► Worker
/ county PDFs                                              │
   │ nitapata ingest (Python)               1 normalise ───┤
   ▼                                        2 scope gate ──┼─► out-of-scope → boundary line
parse ► chunk(+page) ► embed                3 split compound│
   │                                        4 resolve depot ┼─► missing → 1 clarifying Q (KV, 15min TTL)
   ▼                                        5 retrieve (Vectorize, cycle+county filter)
Cloudflare Vectorize + D1                   6 generate (Haiku, cite-or-refuse)
(corpus is the ONLY database)               7 citation check ─► uncited figure → fallback line
                                            8 GSM-7 segment ──► reply SMS (≤2 segments)
Astro demo UI ──────────────────────────► same Worker + judge citation panel
```

## Pain points (named up front, with the mitigation)

1. **Scanned gazettes** — many notices are image PDFs; OCR (tesseract) with
   manual spot-check of every seed document before indexing.
2. **Page numbers surviving chunking** — chunker must carry page metadata
   through; a citation without a real page is the failed-guardrail case.
3. **Price changed mid-programme** (2,500 → 2,000 verified in the wild) —
   cycle + publish-date tags on every chunk; answers always include the
   document date; newest-document-wins at retrieval.
4. **Depot-level facts may not exist in public documents** — the honest
   boundary line is the designed behaviour, but the demo county's corpus is
   verified to actually answer the three Nyambura questions before demo day.
5. **Sheng/code-switching classification** — rules alone won't cut it; the
   scope gate uses Haiku with few-shot Sheng examples, and the eval harness
   includes Sheng messages.
6. **Citations eat SMS characters** — compact citation format
   (`[NCPB Tangazo, Uk.1, 12/8]`), tested against the 160-char GSM-7 budget.
7. **Clarifying question needs state vs no-persistence rule** — reconciled
   as the documented 15-min hashed-key KV entry, nothing else, auto-expiry.
8. **Post-generation citation check is the hard engineering** — v1 is
   conservative: extract every numeral/entity from the draft, require a
   match in retrieved chunks, discard on any miss. False refusals are
   acceptable; false citations are not.
9. **Venue connectivity** — demo-mode recorded replay (scope item 9).
10. **Source drift** — government URLs move; `sources.yaml` snapshots every
    fetched document into R2/`corpus/raw/` so the corpus is reproducible
    even if the source page dies.

## Where the citable documents live

| Publisher | What | Access point |
|---|---|---|
| Kenya Law (official gazette host) | Kenya Gazette notices | new.kenyalaw.org → Gazettes |
| Gazettes.Africa | Gazette mirror, searchable | gazettes.africa/gazettes/ke |
| NCPB | Subsidy FAQ, circulars, depot/price notices | ncpb.co.ke (verified: `/wp-content/uploads/dlm_uploads/2022/10/Faqs-A3.pdf`) |
| Ministry of Agriculture | NFSP cycle launches, prices, subsidy policy framework | kilimo.go.ke (verified: 2025 Long Rains NFSP launch statement; subsidy financing policy PDF) |
| KALRO/KIAMIS | Farmer registration + e-voucher process | kiamislive.kalro.org |
| County governments | County advisories, local price announcements | county sites (verified example: kakamega.go.ke KSh 2,000 announcement) |
| Parliament (secondary) | Programme statements, depot allocation answers | parliament.go.ke |

## Build plan

- **Day 0 (half-day)**: scaffold — repo, `.gitignore`, `wrangler.toml`,
  `sources.yaml` with the seed documents actually downloaded and spot-checked.
- **Day 1**: ingestion pipeline end-to-end; seed corpus indexed in Vectorize;
  retrieval sanity-checked against the three Nyambura questions.
- **Day 2**: Worker pipeline — scope gate, retrieval, generation, citation
  check, fallback lines, KV clarify flow. Eval harness written alongside.
- **Day 3**: Africa's Talking sandbox wiring; Astro demo UI + judge panel;
  GSM-7 segmentation.
- **Day 4 (half-day)**: red-team pass (adversarial evals), demo-mode replay,
  `reviewer` skill run, pitch dry-run against the judges' question:
  *"which document, which page?"*

## Acceptance criteria

- [ ] Every answer containing a figure includes `[Document, Page, Date]`
      matching an actually retrieved chunk (verified by eval harness).
- [ ] The three Nyambura questions (depot serves me? / what am I missing? /
      bag price?) answered correctly and cited for the demo county.
- [ ] Loan, insurance, agronomy and yield-prediction asks each get the same
      clean boundary statement — no hedging, no near-offers (eval cases).
- [ ] Compound message: in-scope part answered + cited, out-of-scope part
      explicitly declined in the same reply.
- [ ] No-location price ask triggers exactly one clarifying question; the
      follow-up answer works within the KV TTL.
- [ ] Price-comparison ask returns current cited figure + explicit decline
      of the comparison.
- [ ] Post-generation check demonstrably fires: a seeded uncited-figure test
      produces the fallback line, never the invented number.
- [ ] No farmer PII at rest: KV entry is hashed-key, intent-only, expires
      ≤15 min; logs contain no phone numbers (grep-verified).
- [ ] Corpus rebuild is one command from `sources.yaml` + snapshotted raw docs.
- [ ] Eval harness ≥30 cases, all passing outcome-class assertions in CI.
- [ ] Reply fits ≤2 GSM-7 SMS segments in eval cases.
- [ ] Demo UI works at 360px width first.
- [ ] LCP < 2s on throttled connection (demo UI).
- [ ] Semantic HTML + meta/OG on demo UI.
- [ ] No secrets in repo; Cloudflare secrets via `wrangler secret`.

## Open questions (working assumptions inline)

1. **Demo county** — assumption: Machakos (matches the Nyambura persona) if
   its published documents answer all three questions; otherwise switch to
   the best-documented county and rewrite the persona to match. Decided on
   Day 0 after the document hunt, not later.
2. **Current-cycle gazette notice** — a gazette notice specifically pricing
   the current cycle may not exist (prices often land via ministry/NCPB
   statements). Assumption: ministry + NCPB documents are acceptable primary
   citations; the gazette notice is included for the programme's legal basis.
3. **Live SMS at the demo** — assumption: judge panel demo is primary,
   Africa's Talking sandbox shown from one phone if venue connectivity
   allows; recorded replay as the stated fallback.
4. **Embedding model** — assumption: Workers AI `bge-m3` handles
   Kiswahili/Sheng well enough; Day 1 retrieval sanity check is the gate,
   with a hosted multilingual embedding API as the swap if it fails.
