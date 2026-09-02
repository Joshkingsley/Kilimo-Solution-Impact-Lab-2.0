#!/usr/bin/env bash
# Expose the local webhook on a public HTTPS URL for Africa's Talking callbacks.
# Prefers cloudflared (no account needed for a quick tunnel), falls back to ngrok.
# Usage: scripts/tunnel.sh [port]
set -euo pipefail
PORT="${1:-8000}"
if command -v cloudflared >/dev/null 2>&1; then
  echo "Starting cloudflared quick tunnel → http://localhost:${PORT}"
  echo "Copy the https://*.trycloudflare.com URL below and set the AT callback to <URL>/sms/inbound"
  exec cloudflared tunnel --url "http://localhost:${PORT}"
elif command -v ngrok >/dev/null 2>&1; then
  echo "Starting ngrok → http://localhost:${PORT}  (callback = <forwarding URL>/sms/inbound)"
  exec ngrok http "${PORT}"
else
  cat <<'EOF'
Neither cloudflared nor ngrok is installed. Install one (manual step):
  cloudflared:  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
                Debian/Ubuntu: curl -L https://pkg.cloudflare.com/cloudflared-linux-amd64.deb -o /tmp/cf.deb && sudo dpkg -i /tmp/cf.deb
  ngrok:        https://ngrok.com/download  (needs a free account + authtoken)
Then re-run: scripts/tunnel.sh
EOF
  exit 1
fi
