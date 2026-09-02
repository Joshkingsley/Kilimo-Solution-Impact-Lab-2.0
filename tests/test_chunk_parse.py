from app.rag.chunk import chunk_page, estimate_tokens
from app.rag.parse import parse_html, parse_text


def test_chunks_never_cross_pages_and_keep_order():
    doc = parse_text(b"Page one sentence A. Page one sentence B.\fPage two sentence C.")
    assert [p.page for p in doc.pages] == [1, 2]
    assert doc.pages[0].page_label == "Uk.1"
    for p in doc.pages:
        for c in chunk_page(p.text):
            assert "Page two" not in c.text or p.page == 2


def test_chunk_size_targets_and_overlap():
    text = " ".join(f"Sentence number {i} says something useful about fertiliser." for i in range(120))
    chunks = chunk_page(text, target_tokens=100, overlap_sentences=1)
    assert len(chunks) > 3
    assert all(c.token_count <= 140 for c in chunks)
    # consecutive chunks share the overlap sentence
    first_last = chunks[0].text.split("\n")[-1]
    assert first_last in chunks[1].text
    assert [c.ordinal for c in chunks] == list(range(1, len(chunks) + 1))


def test_paragraph_mode_tracks_paragraph_index():
    text = "\n\n".join(f"Paragraph {i} " + "word " * 60 for i in range(1, 6))
    chunks = chunk_page(text, target_tokens=120, unit="paragraph")
    assert chunks[0].start_unit == 1
    assert chunks[1].start_unit > 1


def test_table_rows_stay_whole():
    text = "Prices:\n\nDAP | 2,000\nCAN | 1,800\n"
    chunks = chunk_page(text)
    assert "DAP | 2,000" in chunks[0].text and "CAN | 1,800" in chunks[0].text


def test_html_strips_boilerplate_and_labels_paragraphs():
    html = """<html><head><title>T</title></head><body><nav>Home About</nav>
    <div class="post-meta">February 26, 2026 by Someone</div>
    <article><h1>Notice</h1><p>The subsidised price of fertiliser is KSh 2,000 per 50kg bag this season.</p>
    <div class="share-buttons">Share on X</div><p>Farmers collect at the designated depot nearest to them.</p></article>
    <footer>(c) county</footer></body></html>""".encode()
    doc = parse_html(html)
    assert len(doc.pages) == 1
    t = doc.pages[0].text
    assert "KSh 2,000" in t and "designated depot" in t
    assert "Share on X" not in t and "February 26" not in t and "Home About" not in t
    assert doc.title_hint == "Notice"


def test_estimate_tokens_positive():
    assert estimate_tokens("") == 1 and estimate_tokens("a" * 400) == 100
