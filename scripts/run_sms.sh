#!/usr/bin/env bash
# Start the Nitapata? SMS webhook locally. Loads .env if present.
# Usage: scripts/run_sms.sh [port]       DRY_RUN=1 scripts/run_sms.sh  (no real sends)
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PORT="${1:-8000}"
if [ -z "${AT_API_KEY:-}" ] && [ -z "${DRY_RUN:-}" ]; then
  echo "AT_API_KEY not set — starting in DRY_RUN mode (replies computed, nothing sent)."
  export DRY_RUN=1
fi
python3 -c "import fastapi, uvicorn, africastalking, multipart, yaml, anthropic" 2>/dev/null || \
  pip install --break-system-packages -q fastapi uvicorn africastalking python-multipart httpx pyyaml anthropic pypdf
[ -f corpus/chunks.jsonl ] || python3 -m ingest.cli ingest
[ -f web/recorded_run.json ] || NITAPATA_USE_LLM=0 python3 evals/record_run.py
if [ -n "${ANTHROPIC_API_KEY:-}" ] && [ "${NITAPATA_USE_LLM:-1}" != "0" ]; then
  echo "LLM: Claude ${NITAPATA_MODEL:-claude-haiku-4-5} (classification + generation; citation check still deterministic)"
else
  echo "LLM: off (rules-v1 templated path). Set ANTHROPIC_API_KEY to enable Claude Haiku."
fi
echo "Webhook: http://localhost:${PORT}/sms/inbound   Judge panel: http://localhost:${PORT}/judge   Docs: /docs"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --reload
