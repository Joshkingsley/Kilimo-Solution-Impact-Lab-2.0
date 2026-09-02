"""Replay evals/messages.yaml through the pipeline and assert outcome classes (SPEC §10).

Runs the templated path (NITAPATA_USE_LLM=0) so CI is deterministic and needs no key.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["NITAPATA_USE_LLM"] = "0"

from nitapata import constants, state
from nitapata.pipeline import handle

CASES = yaml.safe_load((ROOT / "evals" / "messages.yaml").read_text(encoding="utf-8"))["cases"]
GSM7_OK = constants.GSM7_BASIC | constants.GSM7_EXT


def test_suite_size() -> None:
    assert len(CASES) >= 40, f"only {len(CASES)} cases; SPEC §10 requires at least 40"


@pytest.fixture(scope="module", autouse=True)
def fresh_state() -> None:
    state.reset_all()


def _run(case: dict) -> dict:
    sender = "thread-" + case["thread"] if case.get("thread") else "case-" + case["id"]
    return handle(case["message"], sender, seed_bad_figure=case.get("seed_bad_figure", False))


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case: dict) -> None:
    r = _run(case)
    d = r["diagnostics"]
    assert r["outcome"] in {"cite", "clarify", "boundary"}, "a fourth outcome class is a bug"
    assert r["outcome"] == case["expect"], (r["reply"], d["guardrail_hits"], d["notes"])
    lang = r["language"]

    if "expect_language" in case:
        assert lang == case["expect_language"]
    if "expect_source" in case:
        assert case["expect_source"] in {c["doc_id"] for c in r["citations"]}, r["citations"]
    if "expect_figure" in case:
        assert case["expect_figure"] in r["reply"]
    if "forbid_figure" in case:
        assert case["forbid_figure"] not in r["reply"]
    if "forbid_text" in case:
        assert case["forbid_text"].lower() not in r["reply"].lower()
    if "expect_guardrail" in case:
        assert case["expect_guardrail"] in d["guardrail_hits"], d["guardrail_hits"]
    if "expect_declines_contains" in case:
        assert any(case["expect_declines_contains"].lower() in x.lower() for x in r["declines"]), r["declines"]
    if case.get("expect_requirements"):
        assert r["requirements"], "requirements component missing"
    if "expect_gap" in case:
        assert sorted(x["flag"] for x in r["requirements"] if x.get("missing")) == sorted(case["expect_gap"]), r["requirements"]
    if "expect_declared" in case:
        for k, v in case["expect_declared"].items():
            assert r["declared"].get(k) is v, r["declared"]
    if "max_segments" in case:
        assert len(r["segments"]) <= case["max_segments"]

    # invariants (SPEC §8.2)
    assert len(r["segments"]) <= 2
    for s in r["segments"]:
        assert all(ch in GSM7_OK for ch in s["text"]), f"non-GSM-7 character in segment: {s['text']!r}"
        assert len(s["text"]) <= 160
    if r["outcome"] == "cite":
        assert r["citations"], "cite without a citation"
        assert "[" in r["reply"] and "]" in r["reply"], "cite reply must carry the citation bracket"
        assert not d["guardrail_hits"]
    else:
        assert r["citations"] == [] and r["requirements"] == []
    if r["outcome"] == "boundary":
        assert r["reply"] in constants.FIXED_LINES, f"boundary reply must be one of the fixed constants: {r['reply']!r}"
        if case.get("expect_wording") == "boundary":
            assert r["reply"] == constants.BOUNDARY_LINE[lang]
    for r_ in r["requirements"]:
        if r_.get("missing"):
            assert r["declared"].get(r_["flag"]) is False, "missing without a declaration is a record assertion"
