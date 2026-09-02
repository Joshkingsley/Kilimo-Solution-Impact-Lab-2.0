"""Webhook contract tests — run the FastAPI app in-process with delivery stubbed out."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DRY_RUN", "1")
os.environ.setdefault("MSISDN_HMAC_SECRET", "test-secret")

from fastapi.testclient import TestClient

from app import main

client = TestClient(main.app)
SENT: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def stub_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    SENT.clear()
    monkeypatch.setattr(main, "deliver", lambda msisdn, message: SENT.append((msisdn, message)) or {"stub": True})


def inbound(text: str, sender: str = "+254700000111") -> dict:
    r = client.post("/sms/inbound", data={"from": sender, "to": "20880", "text": text, "id": "x", "date": "now"})
    assert r.status_code == 200, r.text
    return r.json()


def test_health() -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["chunks"] > 0


def test_inbound_price_flow_clarify_then_cite() -> None:
    first = inbound("Bei ya mbolea ni ngapi?")
    assert first["outcome"] == "clarify"
    second = inbound("Kakamega")
    assert second["outcome"] == "cite"
    assert "2,000" in second["reply"]
    assert second["citations"][0]["doc_id"] == "kakamega-price-2026-08"
    assert len(SENT) == 2 and SENT[1][1] == second["reply"]


def test_inbound_out_of_scope_is_boundary_constant() -> None:
    r = inbound("Naweza pata mkopo wa mbolea?", sender="+254700000222")
    assert r["outcome"] == "boundary" and r["reply"] == main.nd.BOUNDARY
    assert r["guardrail_hits"] == ["out_of_scope"] and r["citations"] == []


def test_keywords_whole_message_only() -> None:
    assert inbound("MSAADA", sender="+254700000333")["reply"] == main.HELP_LINE
    assert inbound("  stop ", sender="+254700000333")["reply"] == main.STOP_LINE
    # a sentence merely containing "stop" is not a command
    r = inbound("stop, bei Kakamega ni ngapi?", sender="+254700000333")
    assert r["outcome"] == "cite"


def test_stop_wipes_pending_clarify_state() -> None:
    inbound("Bei ya mbolea ni ngapi?", sender="+254700000444")   # sets pending intent
    inbound("STOP", sender="+254700000444")
    r = inbound("Kakamega", sender="+254700000444")              # no pending intent → not a price answer
    assert r["outcome"] != "cite" or "2,000" not in r["reply"]


def test_no_raw_msisdn_in_response() -> None:
    r = inbound("Bei Kakamega ni ngapi?", sender="+254711222333")
    assert "+254711222333" not in str(r) and "711222333" not in str(r)


def test_rejects_missing_fields() -> None:
    assert client.post("/sms/inbound", data={"from": "+254700000555"}).status_code == 400


def test_demo_endpoint_returns_diagnostics() -> None:
    r = client.post("/demo/message", json={"text": "Bei Kakamega ni ngapi?"})
    body = r.json()
    assert r.status_code == 200 and body["outcome"] == "cite"
    assert body["retrieved"][0]["chunk_id"] == "kkg-2026sr-price"
    assert SENT == []  # demo endpoint never sends SMS
