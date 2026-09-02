# Day-0 document hunt — evidence for the two pins

Date: 2026-09-02. Method: web search for discovery, then direct fetch and
byte-for-byte snapshot of every candidate primary document into `corpus/raw/`
(hashes in `corpus/sources.yaml`). Text extracted with `pdftotext` (PDF) and a
paragraph splitter (HTML); locators below are what the chunks cite.

## Pin 1 — which county can the documents actually answer for?

| Question | Kakamega | Machakos |
|---|---|---|
| **Bag price this cycle?** | **Yes.** "farmers can now purchase the subsidized fertilizer at KSh 2,000 per 50kg bag … as farmers prepare for the short rains" — *Kaunti ya Kakamega*, ¶2, 24/08/2026. Prior figure: "Ksh 2,500 per 50-kilogram bag through an E-voucher system" — *Kaunti ya Kakamega*, ¶2, 26/02/2026. | **No.** No Machakos County Government document on the subsidy exists (machakos.go.ke checked directly). The only Machakos document (KNA, 26/03/2025) carries no price. |
| **What am I missing to qualify?** | **Yes** (national). Registered farmer, original ID, in person — *NCPB FAQ*, Uk.1 Q2–Q3. Not registered → County / Sub-county / Ward Agricultural Office, free — Q5–Q6. | Same national FAQ. |
| **Does my collection point serve me?** | **Partial, honestly.** "16 county-managed collection centres, in addition to four NCPB outlets, bringing the total number of collection points to 20 … collect … at the designated collection point nearest to them" — *Kaunti ya Kakamega*, ¶4, 26/02/2026; "16 stores … distribution points" — ¶4, 24/08/2026. **No document names the points.** | **Partial.** "enough subsidised fertiliser at the four allocated depots in Machakos" — *KNA Machakos*, ¶1, 26/03/2025. No names, so "Kangundo" cannot be confirmed. |

**Decision: Kakamega.** Persona rewritten to a Kakamega farmer (working name
Nafula, Malava). Machakos/Nyambura retained as the honest-limit thread.

## Pin 2 — what carries the current-cycle price?

| Document | Publisher | Authority | Carries a price? |
|---|---|---|---|
| NCPB FAQ (2022-10) | NCPB | primary | Yes, but 2022 prices (DAP 3,500 etc.) — superseded; process only |
| 2025 LR NFSP launch (18/12/2024) | Ministry | primary | KSh 2,500 — previous cycles |
| Streaming distribution (19/12/2024) | Ministry | primary | KSh 2,500 from 6,500 — previous cycles |
| Kakamega last-mile (26/02/2026) | County | primary (county-scoped) | KSh 2,500 — 2026-LR |
| **Kakamega price cut (24/08/2026)** | County | **primary (county-scoped)** | **KSh 2,000 — 2026-SR, the only current-cycle price document found** |
| KNA Machakos (26/03/2025) | KNA | supporting | No |
| KIAMIS home | KALRO | supporting | No |
| Kenya Gazette | Kenya Law | legal_basis | **Not located** |

**Finding that changed the ranking:** the current price exists *only* in a
county-government page. The draft spec ranked county publications as
`supporting` (never sole citation for a figure), which would have made the demo
un-citable. County publications are therefore `primary` for figures scoped to
that county. Ministry/NCPB remain preferred when a same-cycle document exists.

The KSh 2,000 cut was announced by the President on 23/08/2026 (media reports,
not citable). No kilimo.go.ke or ncpb.co.ke statement was found; if one
appears it should be added and will outrank the county page.

## Gaps (also in `sources.yaml: wanted_not_found`)

1. Kenya Gazette notice for the NFSP — gazettes.africa and new.kenyalaw.org
   free-text search returned nothing; kenyalaw returns 403 to fetchers. Retry
   by browsing Gazette Vol/No. Until found, the pitch must not claim a Gazette
   anchor.
2. Named list of Kakamega's 20 collection points.
3. Ministry/NCPB primary statement for the Aug-2026 price.
4. KIAMIS step-by-step e-voucher redemption from a primary page. The only
   detailed steps online are a 2020 pilot-era KNA article (snapshotted, not
   cited as current process).
5. Any Machakos County Government document.

## Fetch notes

- kilimo.go.ke serves a self-signed certificate; fetched with `curl -k` and
  pinned by sha256.
- NCPB FAQ is a single A3 page with a real text layer — no OCR needed.
- kenyans.co.ke, the-star.co.ke and gazettes.africa search block automated
  fetches (403); they are discovery aids only anyway.

## §7.5 coverage checklist (added with spec v1.2)

Corpus count: 8 registered (7 citable + 1 context-only). Floor 5 met, ceiling 15 respected.

| # | Intent | Result | Document · locator · date |
|---|---|---|---|
| 1 | Eligibility | FOUND | NCPB FAQ · Uk.1 Q2 · 2022-10 |
| 2 | Registration process | PARTIAL | NCPB FAQ · Uk.1 Q5–Q6 · 2022-10 (where, free). Enumerated KIAMIS steps: **NOT FOUND** |
| 3 | Price (current, primary) | FOUND | Kaunti ya Kakamega · ¶2 · 24/08/2026 (KSh 2,000). Machakos: **NOT FOUND** |
| 4 | Depot / availability | PARTIAL | Kaunti ya Kakamega · ¶4 · 26/02/2026 (20 points). Named list: **NOT FOUND** |
| 5 | E-voucher / requirements | FOUND (minimal) | NCPB FAQ · Uk.1 Q3 (in person, original ID, name in register), Q10 (M-Pesa/bank, no cash), Q11 (no credit) · 2022-10; Wizara ya Kilimo · ¶6 · 18/12/2024 (e-vouchers to registered farmers). Allocation-SMS / eCitizen steps: **NOT FOUND** |
| 6 | Cycle / timing | PARTIAL | Kaunti ya Kakamega · ¶2 · 24/08/2026 ("prepare for the short rains"). Start dates only for 2025-LR: Wizara ya Kilimo · ¶5 · 18/12/2024 |
| 7 | Stale-price trap | FOUND | NCPB FAQ · Uk.1 Q9 · 2022-10 (3,500); Wizara ya Kilimo · ¶7 · 18/12/2024 (2,500); Kaunti ya Kakamega · ¶2 · 26/02/2026 (2,500) |

Day-1 hunt priority: a KIAMIS or NCPB page enumerating redemption steps (row 5),
then a Kakamega collection-point list (row 4), then the Gazette.
