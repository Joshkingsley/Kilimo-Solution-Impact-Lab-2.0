"""Replay evals/messages.yaml against the Day-0 pipeline and assert outcome classes (SPEC §10)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo"))

import nitapata_demo as nd

CASES = yaml.safe_load((ROOT / "evals" / "messages.yaml").read_text())["cases"]
CHUNKS = nd.load_chunks()
FIXED_LINES = {nd.BOUNDARY, nd.FALLBACK}


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_outcome_class(case: dict) -> None:
    r = nd.handle(case["message"], "+254799" + case["id"], CHUNKS, case.get("seed_bad_figure", False))
    assert r.outcome in {"cite", "clarify", "boundary"}, "a fourth outcome class is a bug"
    assert r.outcome == case["expect"], (r.reply, r.notes, r.guardrail_hits)
    if "expect_source" in case:
        assert case["expect_source"] in {c["source"] for c in r.chunks}
    if "expect_figure" in case:
        assert case["expect_figure"] in r.reply
    if "expect_guardrail" in case:
        assert case["expect_guardrail"] in r.guardrail_hits
    if r.outcome == "cite":
        assert r.chunks, "cite without a citation"
        assert nd.citation_check(r.reply, r.chunks) == []
        assert r.segments <= 2
    if r.outcome == "boundary":
        assert r.reply in FIXED_LINES, "boundary reply must be byte-identical to a fixed constant"
        assert not r.chunks or r.guardrail_hits, "boundary with chunks must record why"
