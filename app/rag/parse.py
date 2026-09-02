"""Document → pages. Page boundaries are preserved because the page number is part
of the citation (SPEC §9.1 step 2). No chunk may ever span two pages.

Formats:
  pdf  — pdfplumber, one Page per printed page. Pages with no extractable text are
         flagged `needs_ocr` and skipped (a figure that only exists on a scanned
         page must be hand-checked, not silently invented).
  html — one logical page (page=1, page_label 'web'), boilerplate stripped.
  text — plain text; form-feed (\\f) separates pages. Used for hand-transcribed
         documents and test fixtures.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from bs4 import BeautifulSoup

log = logging.getLogger("nitapata.parse")

MAX_PDF_PAGES = 400
MIN_GUTTER_PX = 30          # narrowest vertical whitespace band that counts as a column gutter
_BOILERPLATE_RE = re.compile(r"(nav|menu|breadcrumb|share|social|meta|byline|author|comment|sidebar|widget|footer|"
                             r"header|cookie|related|tags|categor|post-date|entry-date|trending)", re.I)


@dataclass(frozen=True)
class Page:
    page: int
    page_label: str
    text: str
    ocr: bool = False


@dataclass(frozen=True)
class ParsedDoc:
    pages: list[Page]
    skipped_pages: list[int]
    title_hint: str | None


def _clean(text: str) -> str:
    text = text.replace(" ", " ").replace("\r", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _column_ranges(words: list[dict], width: float) -> list[tuple[float, float]]:
    """Detect column gutters from the x-histogram of word starts. Government notices are
    frequently 2–3 columns; reading them as one flow interleaves unrelated Q&A lines."""
    if len(words) < 40:
        return [(0.0, width)]
    bin_w = 10
    nbins = int(width // bin_w) + 1
    counts = [0] * nbins
    for w in words:
        counts[min(nbins - 1, int(w["x0"] // bin_w))] += 1
    body_lo, body_hi = int(width * 0.12 // bin_w), int(width * 0.88 // bin_w)
    gutters: list[tuple[int, int]] = []
    run_start = None
    for b in range(body_lo, body_hi + 1):
        if counts[b] <= 1:
            run_start = b if run_start is None else run_start
        else:
            if run_start is not None and (b - run_start) * bin_w >= MIN_GUTTER_PX:
                gutters.append((run_start, b))
            run_start = None
    if run_start is not None and (body_hi + 1 - run_start) * bin_w >= MIN_GUTTER_PX:
        gutters.append((run_start, body_hi + 1))
    if not gutters:
        return [(0.0, width)]
    edges = [0.0]
    for g0, g1 in gutters:
        edges.append(((g0 + g1) / 2) * bin_w)
    edges.append(width)
    cols = [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
    # a "column" holding almost no words is a margin artefact — drop it
    kept = []
    for x0, x1 in cols:
        n = sum(1 for w in words if x0 <= w["x0"] < x1)
        if n >= max(10, len(words) * 0.05):
            kept.append((x0, x1))
    return kept or [(0.0, width)]


def _page_text(p) -> str:
    words = p.extract_words(use_text_flow=False)
    cols = _column_ranges(words, float(p.width))
    if len(cols) == 1:
        return p.extract_text() or ""
    parts = []
    for x0, x1 in cols:
        region = p.crop((x0, 0, x1, float(p.height)))
        parts.append(region.extract_text() or "")
    return "\n\n".join(t for t in parts if t.strip())


def parse_pdf(data: bytes) -> ParsedDoc:
    import pdfplumber  # heavy import, keep local

    pages: list[Page] = []
    skipped: list[int] = []
    title = None
    with pdfplumber.open(BytesIO(data)) as pdf:
        if pdf.metadata and pdf.metadata.get("Title"):
            title = str(pdf.metadata["Title"]).strip() or None
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has {len(pdf.pages)} pages > {MAX_PDF_PAGES}")
        for i, p in enumerate(pdf.pages, start=1):
            try:
                text = _clean(_page_text(p))
                tables = p.extract_tables() or []
            except Exception as exc:  # malformed page
                log.warning("page %d failed to parse: %s", i, exc)
                text, tables = "", []
            # Tables are appended as pipe-rows so figures stay adjacent to their labels.
            for tbl in tables:
                rows = [" | ".join((c or "").strip() for c in row) for row in tbl if row]
                rows = [r for r in rows if r.replace("|", "").strip()]
                if rows:
                    text = (text + "\n\n" + "\n".join(rows)).strip()
            if not text:
                skipped.append(i)
                continue
            pages.append(Page(page=i, page_label=f"Uk.{i}", text=text))
    return ParsedDoc(pages=pages, skipped_pages=skipped, title_hint=title)


def parse_html(data: bytes) -> ParsedDoc:
    soup = BeautifulSoup(data, "html.parser")
    title_tag = soup.find("h1") or soup.find("title")
    title = title_tag.get_text(" ", strip=True) if title_tag else None
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript", "iframe", "button", "svg"]):
        tag.decompose()
    for tag in list(soup.find_all(True)):
        if tag.decomposed or tag.attrs is None:
            continue
        ident = " ".join([*(tag.get("class") or []), tag.get("id") or "", tag.get("role") or ""])
        if ident and _BOILERPLATE_RE.search(ident) and tag.name not in ("article", "main", "body", "html"):
            tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    # one paragraph per block element so the ¶N locator is stable and human-checkable
    blocks = main.find_all(["p", "li", "h2", "h3", "h4", "td", "blockquote"]) if main else []
    paras = [re.sub(r"\s+", " ", b.get_text(" ", strip=True)).strip() for b in blocks]
    paras = [x for x in paras if len(x) >= 25]          # drop menu stubs, share buttons, dates-only lines
    text = "\n\n".join(dict.fromkeys(paras)) if paras else (_clean(main.get_text("\n", strip=True)) if main else "")
    if not text:
        return ParsedDoc(pages=[], skipped_pages=[1], title_hint=title)
    return ParsedDoc(pages=[Page(page=1, page_label="¶1", text=text)], skipped_pages=[], title_hint=title)


def parse_text(data: bytes) -> ParsedDoc:
    raw = data.decode("utf-8", errors="replace")
    pages: list[Page] = []
    for i, block in enumerate(raw.split("\f"), start=1):
        t = _clean(block)
        if t:
            pages.append(Page(page=i, page_label=f"Uk.{i}", text=t))
    return ParsedDoc(pages=pages, skipped_pages=[], title_hint=None)


def parse_file(path: Path, fmt: str) -> ParsedDoc:
    data = path.read_bytes()
    if fmt == "pdf":
        return parse_pdf(data)
    if fmt == "html":
        return parse_html(data)
    if fmt == "text":
        return parse_text(data)
    raise ValueError(f"unknown format {fmt!r}")
