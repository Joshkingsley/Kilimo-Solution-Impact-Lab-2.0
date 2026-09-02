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

A real phone needs a **live** account with something that can *receive*.
`AFTKNG` (or any approved alphanumeric sender ID) is **send-only** by design.
Telecoms have no route for a reply to an alphanumeric name, so a farmer
texting back to it never reaches Africa's Talking, and `/sms/inbound` never
fires. Confirmed against Africa's Talking's own docs: shortcodes can send
and receive, alphanumerics can only send.

1. **Check the dashboard first.** Live app → **SMS → Sender IDs /
   Shortcodes**. If a shortcode is already listed alongside `AFTKNG`, skip
   to step 3, you already have what you need.
2. **If there is no shortcode yet, request one.** Dedicated shortcodes are
   slow and paid; ask Africa's Talking support for a **shared shortcode +
   keyword** instead, the realistic same-week path for a hackathon.
3. **Wire it up:**
   ```bash
   # .env
   AT_USERNAME=<live username>
   AT_API_KEY=<live key>
   AT_SENDER_ID=AFTKNG
   ```
   Live app → **SMS → Callback URLs → Incoming Messages** → paste the tunnel
   URL + `/sms/inbound` → Save. Restart `scripts/run_sms.sh`.
4. A farmer texts the **shortcode** (not `AFTKNG`, that never appears as a
   destination). The reply they see back is sent *from* `AFTKNG`, since
   that's what `AT_SENDER_ID` sets on outbound.

If a shortcode does not land before the demo, present on the simulator and
**say so**. SPEC.md §14 criterion 12 makes the honest statement part of done.

## 7. Before walking on stage

- `python3 -m pytest evals tests -q` green.
- Tunnel and webhook terminals both alive; `curl <tunnel>/health` returns `ok`.
- Recorded fallback ready: `python3 demo/nitapata_demo.py` runs offline.
- Have `docs/pins/PIN-2-ai-bill-2026-risk-sheet.md` open for the "high risk?" question.

## What is still engineering, not manual

Cloudflare Vectorize/D1 and the TypeScript Worker are the production target
(SPEC §9). They need `wrangler login`, so they are not built; the Python
pipeline in `nitapata/` is the working demo and keeps the same `/sms/inbound`
contract.

## 8. Judge panel and the recorded fallback (added with the working demo)

- `scripts/run_sms.sh` builds the corpus and the recorded run on first start,
  then open **http://localhost:8000/judge**. Left: phone thread. Right: outcome,
  citations with page and date, every retrieved chunk (used ones highlighted),
  guardrail hits, declared state. Scripted prompt buttons are under the input.
- **Seed a bad figure** injects a wrong price into a draft so the audience sees
  the citation check discard it and the fallback line go out instead.
- **Play recorded run** switches to the transcript in `web/recorded_run.json`
  (regenerate with `python3 evals/record_run.py`). It is labelled RECORDED RUN
  on screen; never present it as live.
- Optional: export `ANTHROPIC_API_KEY` to turn on Claude Haiku classification
  and generation (`/health` then reports `"llm": "claude-haiku-4-5"`). The
  citation check still runs on every draft; on any API error the templated
  path answers instead. Test it once before the demo, it has not been
  exercised live on the build machine.
