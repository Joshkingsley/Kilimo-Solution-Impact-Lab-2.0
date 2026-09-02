from urllib.parse import quote

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.rag.api import router
from app.rag.config import get_settings


def make_client(pipeline, settings):
    app = FastAPI()
    app.include_router(router)
    app.state.pipeline = pipeline
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def test_requires_api_key(pipeline, settings):
    c = make_client(pipeline, settings)
    assert c.post("/v1/rag/answer", json={"message_id": "1", "text": "hi"}).status_code == 401
    assert c.post("/v1/rag/answer", json={"message_id": "1", "text": "hi"}, headers={"X-API-Key": "wrong"}).status_code == 401


def test_answer_roundtrip(pipeline, settings):
    c = make_client(pipeline, settings)
    r = c.post("/v1/rag/answer", headers={"X-API-Key": "client-key"},
               json={"message_id": "m1", "text": "bei ya DAP ni ngapi?", "county": "Machakos", "channel": "demo"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == "cite" and body["citations"][0]["page_label"] == "Uk.1"
    assert set(body) >= {"trace_id", "outcome", "intent", "text", "rendered_text", "citations", "declines", "requirements",
                         "resolved", "declared", "diagnostics"}


def test_rejects_phone_number_and_unknown_fields(pipeline, settings):
    c = make_client(pipeline, settings)
    r = c.post("/v1/rag/answer", headers={"X-API-Key": "client-key"}, json={"message_id": "m", "text": "hi", "from_hash": "0712345678"})
    assert r.status_code == 422
    r = c.post("/v1/rag/answer", headers={"X-API-Key": "client-key"}, json={"message_id": "m", "text": "hi", "msisdn": "0712345678"})
    assert r.status_code == 422


def test_message_too_long(pipeline, settings):
    c = make_client(pipeline, settings)
    r = c.post("/v1/rag/answer", headers={"X-API-Key": "client-key"}, json={"message_id": "m", "text": "x" * 5000})
    assert r.status_code == 413


def test_search_and_chunk_lookup(pipeline, settings):
    c = make_client(pipeline, settings)
    r = c.post("/v1/rag/search", headers={"X-API-Key": "client-key"}, json={"query": "price 50kg bag", "top_k": 3})
    assert r.status_code == 200 and r.json()
    cid = r.json()[0]["chunk"]["chunk_id"]
    r2 = c.get(f"/v1/rag/chunks/{quote(cid, safe='')}", headers={"X-API-Key": "client-key"})   # `#` must be %23
    assert r2.status_code == 200 and r2.json()["chunk_id"] == cid
    assert c.get("/v1/rag/chunks/../etc/passwd", headers={"X-API-Key": "client-key"}).status_code in (404, 422)
    assert c.get("/v1/rag/chunks/nope", headers={"X-API-Key": "client-key"}).status_code == 422


def test_ingest_requires_admin_and_only_register_ids(pipeline, settings):
    c = make_client(pipeline, settings)
    assert c.post("/v1/rag/ingest", headers={"X-API-Key": "client-key"}, json={}).status_code == 403
    r = c.post("/v1/rag/ingest", headers={"X-API-Key": "admin-key"}, json={"doc_ids": ["not-in-register"]})
    assert r.status_code == 422
    r = c.post("/v1/rag/ingest", headers={"X-API-Key": "admin-key"}, json={"url": "https://evil.example/x"})
    assert r.status_code == 422
    r = c.post("/v1/rag/ingest", headers={"X-API-Key": "admin-key"}, json={"doc_ids": ["fx-kiamis-guide"]})
    assert r.status_code == 200 and r.json()["report"][0]["status"] in ("unchanged", "ingested")


def test_rate_limit(pipeline, settings):
    import app.rag.api as api
    from app.rag.security import SlidingWindowLimiter
    api._limiter = SlidingWindowLimiter(2, 60)
    try:
        c = make_client(pipeline, settings)
        h = {"X-API-Key": "client-key"}
        assert c.get("/v1/rag/stats", headers=h).status_code == 200
        assert c.get("/v1/rag/stats", headers=h).status_code == 200
        r = c.get("/v1/rag/stats", headers=h)
        assert r.status_code == 429 and "Retry-After" in r.headers
    finally:
        api._limiter = None


def test_health_open(pipeline, settings):
    c = make_client(pipeline, settings)
    r = c.get("/v1/rag/health")
    assert r.status_code == 200 and r.json()["documents"] == 5
