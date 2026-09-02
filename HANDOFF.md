# Handoff — Nitapata? (Kilimo track, AI Mashinani)

Last session: 2026-09-02. Repo: `~/Desktop/AI Mashinani/Kilimo` (git, branch `main`).

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
| `SPEC.md` | Written, committed — **Status: Draft, NOT yet approved** |
| `index.html` | Team build hub (demo mock, pins, plan, collab steps) — done |
| Build code | **None yet.** Factory rule: builder starts only on approved spec |

## The two pins — must be decided at Day 0, before build code

1. **Demo county**: Machakos (Nyambura persona) *iff* its published documents
   answer all three questions (depot serves me? / what am I missing? / bag
   price?); otherwise best-documented county (Kakamega is a candidate — has
   a published KSh 2,000 price announcement) and rewrite the persona.
2. **Citation hierarchy**: Ministry (kilimo.go.ke) + NCPB statements are
   acceptable primary citations for figures; Gazette notice anchors the
   programme's legal basis. Confirm during the document hunt and encode in
   `corpus/sources.yaml`.

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

## Next session — in order

1. Get the spec **approved** (or amended) — flip `SPEC.md` Status to Approved.
2. Day 0: scaffold (`wrangler.toml`, `requirements.txt`, `corpus/sources.yaml`),
   run the document hunt, download + spot-check seed docs into `corpus/raw/`,
   **decide both pins**, freeze the two interfaces (chunk metadata schema,
   Worker message contract).
3. Day 1: ingestion pipeline end-to-end; retrieval sanity check on the three
   Nyambura questions; gate on `bge-m3` Kiswahili quality.
4. Then Days 2–4 per SPEC.md build plan.

## Kickoff prompt for next session

> Continue the Nitapata? build in ~/Desktop/AI Mashinani/Kilimo. Read
> HANDOFF.md and SPEC.md first. I approve the spec [/ with these changes: …].
> Start Day 0: scaffold, run the document hunt, decide the two pins (demo
> county + citation hierarchy), and report what the documents can actually
> answer before moving to Day 1.
