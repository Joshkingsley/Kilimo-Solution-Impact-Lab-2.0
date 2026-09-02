#!/usr/bin/env bash
# Simulate Africa's Talking inbound callbacks against a running webhook.
# Usage: scripts/smoke_sms.sh [base_url]    default http://localhost:8000
set -euo pipefail
BASE="${1:-http://localhost:8000}"
FROM="+254700000001"
post() { curl -s -X POST "$BASE/sms/inbound" -d "from=$FROM" -d "to=20880" -d "text=$1" -d "id=smoke" -d "date=now"; echo; }
echo "health:"; curl -s "$BASE/health"; echo; echo
for m in "Bei ya mbolea ni ngapi?" "Kakamega" "Nahitaji nini ili nipate mbolea ya ruzuku?" \
         "Depo ya Malava inanihudumia?" "Naweza pata mbolea kwa mkopo?" "Bei Machakos ni ngapi?" "MSAADA" "STOP"; do
  echo "▶ $m"; post "$m" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  ", d["outcome"].upper(), "|", d["reply"]); [print("   cite:", c["short_cite"], c["page_label"], c["publish_date"]) for c in d["citations"]]'
done
