from datetime import date

from app.rag.guardrails import check_draft, numbers_in
from app.rag.llm import Draft, DraftRequirement
from app.rag.schema import Chunk


def mk(cid="doc-a#p1#1", text="The price is KSh 2,000 per 50kg bag.", authority="primary"):
    doc = cid.split("#")[0]
    return Chunk(chunk_id=cid, doc_id=doc, doc_title="Doc", publisher="P", doc_type="ncpb_notice", authority=authority,
                 page=int(cid.split("#")[1][1:]), page_label="Uk.1", publish_date=date(2026, 2, 1), cycle="2026-LR", county=None,
                 lang="en", text=text, token_count=10, source_url="https://ncpb.co.ke/x", retrieved_at="2026-09-02", ingest_version=1)


def test_numbers_normalised():
    assert numbers_in("KSh 2,500 for 50kg in 2026") == ["2500", "50", "2026"]


def test_ok_draft_passes():
    r = check_draft(Draft(answer="Bei ni KSh 2,000 kwa gunia la 50kg.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert r.ok and r.cited_chunk_ids == ["doc-a#p1#1"]


def test_uncited_figure_rejected():
    r = check_draft(Draft(answer="The price is KSh 2,500.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert not r.ok and "uncited_figure:2500" in r.hits


def test_citation_to_unretrieved_chunk_rejected():
    r = check_draft(Draft(answer="Price KSh 2,000.", answer_chunk_ids=["doc-b#p9#1"]), {"doc-a#p1#1": mk()}, {})
    assert not r.ok and "citation_not_retrieved" in r.hits and "no_citation" in r.hits


def test_hedged_figure_rejected():
    r = check_draft(Draft(answer="The price is probably KSh 2,000.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert "hedged_figure" in r.hits


def test_kama_is_not_a_hedge():
    r = check_draft(Draft(answer="Bei ni KSh 2,000 kama ilivyotangazwa.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert r.ok


def test_secondary_only_figure_rejected():
    r = check_draft(Draft(answer="Price KSh 2,000.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk(authority="secondary")}, {})
    assert "secondary_only_figure" in r.hits


def test_record_assertion_rejected():
    r = check_draft(Draft(answer="You are not registered, so you cannot buy.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert "record_assertion" in r.hits
    r2 = check_draft(Draft(answer="Hujasajiliwa kwenye KIAMIS.", answer_chunk_ids=["doc-a#p1#1"]), {"doc-a#p1#1": mk()}, {})
    assert "record_assertion" in r2.hits


def test_invented_requirement_rejected():
    req = DraftRequirement(flag="has_ecitizen_payment", label_en="eCitizen payment", label_sw="malipo", chunk_id="doc-a#p1#1")
    r = check_draft(Draft(answer="Price KSh 2,000.", answer_chunk_ids=["doc-a#p1#1"], requirements=[req]), {"doc-a#p1#1": mk()}, {})
    assert "requirement_not_in_chunk:has_ecitizen_payment" in r.hits


def test_requirement_backed_by_chunk_passes():
    chunk = mk(text="To redeem, a farmer must present the national ID card and the allocation SMS. Price KSh 2,000.")
    reqs = [DraftRequirement(flag="has_id", label_en="national ID", label_sw="kitambulisho", chunk_id="doc-a#p1#1"),
            DraftRequirement(flag="has_allocation_sms", label_en="allocation SMS", label_sw="SMS ya mgao", chunk_id="doc-a#p1#1")]
    r = check_draft(Draft(answer="Price KSh 2,000.", answer_chunk_ids=["doc-a#p1#1"], requirements=reqs), {"doc-a#p1#1": chunk}, {})
    assert r.ok


def test_insufficient_draft_is_not_checked():
    assert check_draft(Draft(insufficient=True), {}, {}).ok
