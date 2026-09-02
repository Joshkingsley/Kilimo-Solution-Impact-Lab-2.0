#!/usr/bin/env python3
"""Record the demo script as web/recorded_run.json for the judge panel's replay mode (SPEC §6, §13).

The panel labels this as a recorded run. It is never presented as live.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("NITAPATA_USE_LLM", "0")

from demo.nitapata_demo import SCRIPT
from nitapata import state
from nitapata.pipeline import handle

state.reset_all()
steps = []
for who, msg in SCRIPT:
    r = handle(msg, "recorded-" + who)
    steps.append({"who": who, "message": msg, "reply": r})
out = ROOT / "web" / "recorded_run.json"
out.write_text(json.dumps({"recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "label": "RECORDED RUN",
                           "steps": steps}, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"wrote {out.relative_to(ROOT)} with {len(steps)} steps")
