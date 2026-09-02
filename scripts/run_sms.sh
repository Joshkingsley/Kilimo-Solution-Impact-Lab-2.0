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
python3 -c "import fastapi, uvicorn, africastalking, multipart" 2>/dev/null || \
  pip install --break-system-packages -q fastapi uvicorn africastalking python-multipart httpx
echo "Webhook: http://localhost:${PORT}/sms/inbound   Judge API: POST http://localhost:${PORT}/demo/message   Docs: /docs"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --reload
