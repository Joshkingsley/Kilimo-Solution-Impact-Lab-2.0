# PIN 2 — Risk level under Kenya's Artificial Intelligence Bill, 2026

**Pinned 2026-09-02 · Kilimo track · Nitapata? · one page · no sheet, no build**

**Source:** Parliament of Kenya, Senate Bill Digest, *The Artificial Intelligence
Bill, 2026* (Senate Bills No. 4 of 2026). Sponsor Sen. Karen Nyamu. Published
19 Feb 2026; first reading 2 Apr 2026; with the Senate ICT Committee. Copy at
`docs/pins/ai-bill-2026-parliament-digest.pdf`. The Bill is **not yet law**;
classification will be made by the Office of the AI Commissioner once
established. This sheet is our self-classification and the standard we build to.

## Declared level: **HIGH RISK** (by sector), built to the high-risk obligations

The Bill's classification (Digest p.10) reads: *"high risk, for systems used in
critical sectors including healthcare, education, **agriculture**, finance,
security, employment or public administration."* Nitapata? is used in the
agriculture sector and touches a public-administration programme. On the face
of the text it is high risk. We do not argue our way down; we build up to it.

**Our view for the Commissioner's future guidance:** the system makes **no
decision about any individual** — it retrieves and quotes public documents and
holds no personal data — so it is functionally closer to *limited risk*
("moderate risks", p.11). That argument is recorded here and nowhere in the
build; the build assumes high risk.

## What the Bill requires of high-risk systems, and where we meet it

| Bill obligation (Digest p.11) | How Nitapata? meets it | Where it lives |
|---|---|---|
| (a) Risk and human-rights impact assessment before deployment, with mitigation | This sheet + SPEC §3 guardrails + §12 pain-point mitigations; full assessment before any non-sandbox deployment | `SPEC.md` §3, §12; this file |
| (b) Human-rights impact assessment | Harm analysed: wasted trips, stale prices, exclusion by language. Mitigation: cite-or-refuse, dates shown, Kiswahili default | `docs/pins/PIN-1-who-this-helps.md` |
| (c) Transparency, traceability, explainability of decision-making | Every reply is one of three outcomes; every figure cites `[Document, Locator, Date]`; judge panel shows the retrieved chunk; `guardrail_hits` records every refusal reason | `SPEC.md` §4, §8.2, §9.4 |
| (d) Keep datasets and system documentation | `corpus/sources.yaml` + byte-for-byte snapshots with sha256; eval transcripts; this spec | `corpus/`, `evals/` |
| (e) Comply with the Data Protection Act | **No personal data at rest.** Only per-farmer state is an HMAC-hashed key holding intent + county, expiring ≤15 min. Logs strip MSISDNs, CI-checked | `SPEC.md` §3 rule 4, §13 |
| (f) Robustness and cybersecurity | Deterministic post-generation citation check (the model is never the last guardrail); scope gate before retrieval; secrets via `wrangler secret`; input treated as untrusted (prompt-injection eval) | `SPEC.md` §9.2, §9.4, §10 |
| (g) Consent for likeness or voice | Not applicable — no likeness, voice or synthetic media is produced | — |

## Transparency duties for all providers/deployers (Digest p.12)

| Duty | Our disclosure |
|---|---|
| Nature and purpose of the system | First reply of any conversation and the demo UI state: *"Nitapata? ni huduma ya SMS inayojibu maswali ya ruzuku ya mbolea kutoka hati za umma pekee."* |
| How much automation is involved | Fully automated retrieval and drafting; **no automated decision about the farmer** — eligibility is determined by NCPB/KIAMIS, never by us |
| Measures taken to mitigate bias | Kiswahili default; Sheng/code-switch eval cases; one county documented properly rather than eight vaguely, stated openly |
| Human intervention rights where automated decisions affect individuals | No decision is made; every fallback names the human route: the ward agricultural office / depot clerk |

## Residual risks we accept and state

1. **Stale figure served as current** — mitigated by cycle tags, newest-wins,
   dates in every reply; required eval case. Residual: a new price published
   somewhere we have not snapshotted. Mitigation: operator corpus sync per cycle.
2. **False refusal** — the citation check is deliberately conservative; a
   correct fact may be refused. Accepted: the sijui rule says a correct "I
   don't know" beats a guess.
3. **Gazette anchor not yet located** — the programme's legal basis is not in
   the corpus. We do not claim it until it is.
4. **Sandbox only** — Africa's Talking sandbox, no production shortcode. A
   production deployment would go through the Bill's regulatory sandbox
   (Digest p.12) and the public register of high-risk systems (p.11).

*Signed off for the build window by the team, 2026-09-02. Re-issue when the
Bill passes or the Commissioner publishes classification guidance.*
