# SMS live-demo runbook — manual steps only

Everything automatable is in the repo. What follows is the short list a human
must do, in order. Total hands-on time: about 15 minutes plus sender-ID wait.

## 0. Rotate the leaked key (do this first, once)

A live Africa's Talking API key was committed in `app/sendsms.py` (remote commit
`46f3be6`) and the repo is public. In the Africa's Talking dashboard →
**Settings → API Key**, generate a new key. The old one is compromised forever.

## 1. Get sandbox credentials (2 min)

1. Log in at <https://account.africastalking.com>, open the **Sandbox** app.
2. **Settings → API Key** → generate a sandbox key.
3. On this machine:
   ```bash
   cp .env.example .env
   # fill AT_USERNAME=sandbox, AT_API_KEY=<sandbox key>, leave AT_SENDER_ID empty
   # set MSISDN_HMAC_SECRET=$(openssl rand -hex 32)
   ```

## 2. Start the webhook (1 min)

```bash
scripts/run_sms.sh            # http://localhost:8000
scripts/smoke_sms.sh          # in a second terminal — replays the demo thread via HTTP
```
Expected: clarify → cite KSh 2,000 → cite requirements → cite + limit → boundary →
fallback → MSAADA → STOP. If any outcome differs, stop and fix; do not demo.

## 3. Expose it publicly (2 min, one-time install)

```bash
scripts/tunnel.sh             # prints an https://… URL
```
If it says neither `cloudflared` nor `ngrok` is installed, install one (the
script prints the commands) and re-run. Keep this terminal open.

## 4. Point Africa's Talking at it (1 min)

Sandbox app → **SMS → Callbacks → Incoming Messages** → paste
`https://<tunnel-url>/sms/inbound` → Save.

## 5. Test from the simulator (2 min)

Sandbox → **Launch Simulator** → any phone number → SMS → send to the sandbox
shortcode shown (e.g. `20880`): `Bei ya mbolea ni ngapi?` then `Kakamega`.
The reply appears in the simulator and in the webhook terminal. This is the
acceptance path in SPEC.md §14 criterion 12.

## 6. Real handset (only if you want live-on-stage)

A real phone needs a **live** account with an approved sender ID or shortcode.
- **Alphanumeric sender ID**: request under Live app → SMS → Sender IDs. Approval
  takes hours to days. Sender IDs can *send* to handsets but **cannot receive**
  replies, so for a two-way thread you need a shortcode.
- **Shortcode**: dedicated ones are slow and paid; ask Africa's Talking support
  for a **shared shortcode + keyword** for the hackathon — that is the realistic
  same-week path.
- Once approved: set `AT_USERNAME=<live username>`, `AT_API_KEY=<live key>`,
  `AT_SENDER_ID=<code>`, set the live app's incoming callback to the tunnel URL,
  restart `scripts/run_sms.sh`.
If none of that lands before the demo, present on the simulator and **say so**
— SPEC.md §14 criterion 12 makes the honest statement part of done.

## 7. Before walking on stage

- `python3 -m pytest evals tests -q` green.
- Tunnel and webhook terminals both alive; `curl <tunnel>/health` returns `ok`.
- Recorded fallback ready: `python3 demo/nitapata_demo.py` runs offline.
- Have `docs/pins/PIN-2-ai-bill-2026-risk-sheet.md` open for the "high risk?" question.

## What is still Day 1–3 engineering (not manual)

The pipeline behind the webhook is the rules-based stand-in. Haiku generation,
Vectorize retrieval, the Astro judge panel and the ≥40 eval cases are the build
plan in SPEC.md §11 — the webhook keeps the same `/sms/inbound` contract when
they land.
