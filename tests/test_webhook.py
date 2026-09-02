"""Webhook contract tests — FastAPI app in-process, SMS delivery stubbed, templated pipeline."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["DRY_RUN"] = "1"
os.environ["NITAPATA_USE_LLM"] = "0"
os.environ.setdefault("MSISDN_HMAC_SECRET", "test-secret")

from fastapi.testclient import TestClient

from app import main
from nitapata import constants, state

client = TestClient(main.app)
SENT: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def stub_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    SENT.clear()
    state.reset_all()
    monkeypatch.setattr(main, "deliver", lambda msisdn, message: SENT.append((msisdn, message)) or {"stub": True})


def inbound(text: str, sender: str = "+254700000111") -> dict:
    r = client.post("/sms/inbound", data={"from": sender, "to": "20880", "text": text, "id": "x", "date": "now"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health() -> None:
    body = client.get("/health").json()
    assert body["ok"] and body["chunks"] > 40 and body["docs"] >= 7


def test_price_flow_clarify_then_cite() -> None:
    first = inbound("Bei ya mbolea ni ngapi?")
    assert first["outcome"] == "clarify"
    second = inbound("Kakamega")
    assert second["outcome"] == "cite" and "2,000" in second["reply"]
    assert second["citations"][0]["doc_id"] == "kakamega-price-2026-08"
    assert len(SENT) == 2 and SENT[1][1] == second["reply"]


def test_out_of_scope_is_boundary_constant() -> None:
    r = inbound("Naweza pata mkopo wa mbolea?", sender="+254700000222")
    assert r["outcome"] == "boundary" and r["reply"] == constants.BOUNDARY_LINE["sw"]
    assert "out_of_scope" in r["diagnostics"]["guardrail_hits"] and r["citations"] == []


def test_keywords_whole_message_only() -> None:
    assert inbound("MSAADA", sender="+254700000333")["reply"] == constants.HELP_LINE["sw"]
    assert inbound("  stop ", sender="+254700000333")["reply"] == constants.STOP_LINE["sw"]
    assert inbound("stop, bei Kakamega ni ngapi?", sender="+254700000333")["outcome"] == "cite"


def test_stop_wipes_pending_state() -> None:
    inbound("Bei ya mbolea ni ngapi?", sender="+254700000444")
    inbound("STOP", sender="+254700000444")
    r = inbound("Kakamega", sender="+254700000444")
    assert r["outcome"] == "clarify"  # new conversation, no pending intent


def test_no_raw_msisdn_anywhere_in_response() -> None:
    r = inbound("Bei Kakamega ni ngapi?", sender="+254711222333")
    assert "254711222333" not in str(r)


def test_rejects_missing_fields() -> None:
    assert client.post("/sms/inbound", data={"from": "+254700000555"}).status_code == 400


def test_demo_endpoint_returns_diagnostics_and_never_sends() -> None:
    body = client.post("/demo/message", json={"text": "Bei Kakamega ni ngapi?"}).json()
    assert body["outcome"] == "cite"
    assert any(x["used"] for x in body["diagnostics"]["retrieved"])
    assert SENT == []


def test_demo_seed_bad_figure_is_caught() -> None:
    body = client.post("/demo/message", json={"text": "Bei Kakamega ni ngapi?", "seed_bad_figure": True}).json()
    assert body["outcome"] == "boundary" and "uncited_figure" in body["diagnostics"]["guardrail_hits"]
    assert "1,800" not in body["reply"]


def test_judge_panel_served() -> None:
    r = client.get("/judge")
    assert r.status_code == 200 and "Nitapata" in r.text
