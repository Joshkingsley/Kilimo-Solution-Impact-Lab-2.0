from app.rag.retrieve import build_fts_query, query_tokens


def test_fts_query_is_quoted_and_expanded():
    q = build_fts_query('bei ya mbolea "DROP TABLE" 2,500')
    assert '"bei"' in q and '"price"' in q and '"fertiliser"' in q
    assert "DROP" not in q or '"drop"' in q          # raw text never reaches MATCH unquoted
    assert "2" in q and "500" in q


def test_stopwords_removed():
    assert "ya" not in query_tokens("bei ya mbolea") and "the" not in query_tokens("the price")


def test_store_roundtrip_and_stats(store):
    st = store.stats()
    assert st["documents"] == 5 and st["chunks"] >= 8
    c = store.get_chunk("fx-kiamis-guide#p2#1")
    assert c is not None and c.page == 2 and c.page_label == "Uk.2" and "national ID" in c.text


def test_cycle_filter_excludes_superseded_price(pipeline):
    hits = pipeline.search("price of DAP per 50kg bag", county=None, cycle="2026-LR", include_superseded=False, top_k=5)
    ids = {h.chunk.doc_id for h in hits}
    assert "fx-ncpb-price-2026-lr" in ids
    assert "fx-ministry-price-2025-lr" not in ids, "stale-cycle document leaked past the cycle filter"


def test_include_superseded_allows_past_cycle(pipeline):
    hits = pipeline.search("KSh 2,500 per 50kg bag", county=None, cycle="2026-LR", include_superseded=True, top_k=8)
    assert "fx-ministry-price-2025-lr" in {h.chunk.doc_id for h in hits}


def test_county_scoped_docs_hidden_without_county_and_visible_with(pipeline):
    q = "Kangundo depot collection"
    national = pipeline.search(q, county=None, cycle="2026-LR", include_superseded=False, top_k=8)
    assert all(h.chunk.county is None for h in national)
    machakos = pipeline.search(q, county="Machakos", cycle="2026-LR", include_superseded=False, top_k=8)
    assert "fx-machakos-advisory" in {h.chunk.doc_id for h in machakos}
    kakamega = pipeline.search(q, county="Kakamega", cycle="2026-LR", include_superseded=False, top_k=8)
    assert "fx-machakos-advisory" not in {h.chunk.doc_id for h in kakamega}


def test_register_hints_block_intent(pipeline):
    # hansard fixture carries do_not_use_for: [price]
    hits = pipeline.search("1,500 next season cost", county=None, cycle="2026-LR", include_superseded=False, top_k=8, intent="price")
    assert "fx-hansard-secondary" not in {h.chunk.doc_id for h in hits}
    hits2 = pipeline.search("1,500 next season cost", county=None, cycle="2026-LR", include_superseded=False, top_k=8)
    assert "fx-hansard-secondary" in {h.chunk.doc_id for h in hits2}


def test_authority_reranks_secondary_below_primary(pipeline):
    hits = pipeline.search("bags distributed Machakos season", county="Machakos", cycle="2026-LR", include_superseded=False, top_k=8)
    ranks = {h.chunk.authority: i for i, h in reversed(list(enumerate(hits)))}
    if "secondary" in ranks and "primary" in ranks:
        assert ranks["primary"] < ranks["secondary"]
