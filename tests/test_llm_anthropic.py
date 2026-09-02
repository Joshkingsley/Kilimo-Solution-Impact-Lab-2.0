"""Request-shape tests for the Claude path with the SDK client mocked: no network, no key.
They pin the things SPEC §9.3 cares about — pinned model, stable cached system prompt first,
volatile content in the user turn, closed-enum structured output — and the failure handling."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.rag.config import Settings
from app.rag.llm import CLASSIFY_SCHEMA, GENERATE_SCHEMA, AnthropicLLM, render_chunks
from app.rag.prompts import CLASSIFY_SYSTEM, GENERATE_SYSTEM


class FakeMessages:
    def __init__(self, payloads):
        self.payloads, self.calls = list(payloads), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return SimpleNamespace(
            model=kwargs["model"], stop_reason=payload.get("stop_reason", "end_turn"),
            content=[SimpleNamespace(type="text", text=json.dumps(payload["json"]))],
            usage=SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=payload.get("cache", 0),
                                  cache_creation_input_tokens=0),
        )


@pytest.fixture
def llm(monkeypatch):
    s = Settings(_env_file=None, anthropic_api_key="test-key", llm_provider="anthropic", rag_api_keys="k")
    obj = AnthropicLLM.__new__(AnthropicLLM)
    obj.model, obj.name, obj.s = s.anthropic_model, f"anthropic:{s.anthropic_model}", s
    obj._client = SimpleNamespace(messages=FakeMessages([]))
    return obj


def test_classify_request_shape(llm):
    llm._client.messages.payloads = [{"json": {
        "language": "sw", "sub_asks": [{"text": "bei", "intent": "price", "in_scope": True}], "county": None, "depot": None,
        "declared": {"has_id": True}, "personal_record_claims": [], "trend_claims": [], "retrieval_query_en": "fertiliser price"}, "cache": 900}]
    cls, usage = llm.classify("bei ya mbolea? nina ID <script>alert(1)</script>")
    call = llm._client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert call["system"][0]["text"] == CLASSIFY_SYSTEM and call["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert call["output_config"]["format"]["schema"] == CLASSIFY_SCHEMA
    assert call["max_tokens"] <= 512
    assert "<script>" not in call["messages"][0]["content"]          # tag-like text neutralised
    assert cls.primary_intent == "price" and cls.declared == {"has_id": True}
    assert usage.cache_read_input_tokens == 900


def test_generate_request_shape_and_chunks_rendered(llm, store):
    chunks = [store.get_chunk("fx-ncpb-price-2026-lr#p1#1")]
    llm._client.messages.payloads = [{"json": {"insufficient": False, "answer": "KSh 2,000 per 50kg bag.",
                                              "answer_chunk_ids": [chunks[0].chunk_id], "requirements": [], "declines": []}}]
    draft, _ = llm.generate(["bei ya DAP?"], chunks, "sw")
    call = llm._client.messages.calls[0]
    assert call["system"][0]["text"] == GENERATE_SYSTEM
    assert call["output_config"]["format"]["schema"] == GENERATE_SCHEMA
    body = call["messages"][0]["content"]
    assert render_chunks(chunks) in body and 'chunk_id="fx-ncpb-price-2026-lr#p1#1"' in body and "Kiswahili" in body
    assert draft.answer_chunk_ids == [chunks[0].chunk_id]


def test_malformed_classification_becomes_out_of_scope(llm):
    llm._client.messages.payloads = [{"json": {"language": "xx", "sub_asks": "nope"}}]
    cls, _ = llm.classify("anything")
    assert cls.in_scope_asks == [] and cls.primary_intent == "out_of_scope"


def test_refusal_raises_so_pipeline_sends_fallback(llm):
    llm._client.messages.payloads = [{"json": {}, "stop_reason": "refusal"}]
    with pytest.raises(RuntimeError):
        llm.classify("x")


def test_system_prompts_have_no_volatile_content():
    for text in (CLASSIFY_SYSTEM, GENERATE_SYSTEM):
        assert "2026" not in text and "{" not in text and "trace" not in text.lower()
