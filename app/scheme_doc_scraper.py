"""
scheme_doc_scraper.py

Scrapes published Kenya farm-input subsidy scheme documents (HTML pages and PDFs)
from a configured list of source domains, extracts page-level text with citation
metadata, and hashes content for versioning so price/eligibility changes don't
silently overwrite prior answers.

Intended to be called from a FastAPI ingestion endpoint / background job, e.g.:

    from scheme_doc_scraper import SchemeDocScraper

    scraper = SchemeDocScraper()
    docs = scraper.scrape_url("https://kilimo.go.ke/some-notice/")
    for doc in docs:
        save_to_db(doc)  # your storage layer

Install:
    pip install requests beautifulsoup4 pdfplumber --break-system-packages
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

import pdfplumber
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("scheme_doc_scraper")

# Domains this scraper is allowed to pull from. Add/remove as you verify sources.
ALLOWED_DOMAINS = {
    "kilimo.go.ke",
    "ncpb.co.ke",
    "kntc.co.ke",
}

USER_AGENT = "SchemeDocScraper/1.0 (+farmer-input-assistant)"
REQUEST_TIMEOUT = 20  # seconds


@dataclass
class DocPage:
    """One citable unit: a single page (PDF) or the whole page (HTML)."""

    source_url: str
    doc_title: str
    page_number: Optional[int]  # None for HTML pages (no pagination)
    text: str
    content_hash: str
    scraped_at: str
    doc_type: str  # "html" | "pdf"
    published_date: Optional[str] = None  # best-effort, from page metadata


@dataclass
class ScrapeResult:
    url: str
    pages: list[DocPage] = field(default_factory=list)
    error: Optional[str] = None


class SchemeDocScraper:
    def __init__(self, allowed_domains: set[str] = ALLOWED_DOMAINS):
        self.allowed_domains = allowed_domains
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ---------- public API ----------

    def scrape_url(self, url: str) -> ScrapeResult:
        """Fetch a single URL and return citable page-level chunks."""
        if not self._is_allowed(url):
            return ScrapeResult(url=url, error=f"Domain not in allowlist: {urlparse(url).netloc}")

        try:
            resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("Fetch failed for %s: %s", url, exc)
            return ScrapeResult(url=url, error=str(exc))

        content_type = resp.headers.get("Content-Type", "")
        scraped_at = datetime.now(timezone.utc).isoformat()

        if "application/pdf" in content_type or url.lower().endswith(".pdf"):
            pages = self._parse_pdf(resp.content, url, scraped_at)
        else:
            pages = self._parse_html(resp.text, url, scraped_at)

        return ScrapeResult(url=url, pages=pages)

    def scrape_many(self, urls: list[str]) -> list[ScrapeResult]:
        return [self.scrape_url(u) for u in urls]

    # ---------- internals ----------

    def _is_allowed(self, url: str) -> bool:
        netloc = urlparse(url).netloc.replace("www.", "")
        return netloc in self.allowed_domains

    def _parse_html(self, html: str, url: str, scraped_at: str) -> list[DocPage]:
        soup = BeautifulSoup(html, "html.parser")

        title_tag = soup.find("h1") or soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else url

        published_date = self._extract_published_date(soup)

        # Strip nav/footer/script noise, keep main article body if identifiable.
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        main = soup.find("article") or soup.find("main") or soup.body or soup
        text = main.get_text(separator="\n", strip=True) if main else ""

        if not text:
            return []

        return [
            DocPage(
                source_url=url,
                doc_title=title,
                page_number=None,
                text=text,
                content_hash=self._hash(text),
                scraped_at=scraped_at,
                doc_type="html",
                published_date=published_date,
            )
        ]

    def _parse_pdf(self, pdf_bytes: bytes, url: str, scraped_at: str) -> list[DocPage]:
        pages: list[DocPage] = []
        title = url.rsplit("/", 1)[-1]

        try:
            with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
                if pdf.metadata and pdf.metadata.get("Title"):
                    title = pdf.metadata["Title"]

                for i, page in enumerate(pdf.pages, start=1):
                    text = (page.extract_text() or "").strip()
                    if not text:
                        continue
                    pages.append(
                        DocPage(
                            source_url=url,
                            doc_title=title,
                            page_number=i,
                            text=text,
                            content_hash=self._hash(text),
                            scraped_at=scraped_at,
                            doc_type="pdf",
                        )
                    )
        except Exception as exc:  # malformed/scanned PDFs, etc.
            logger.warning("PDF parse failed for %s: %s", url, exc)

        return pages

    @staticmethod
    def _extract_published_date(soup: BeautifulSoup) -> Optional[str]:
        # Common WordPress/CMS patterns used by kilimo.go.ke-style sites.
        meta = soup.find("meta", attrs={"property": "article:published_time"})
        if meta and meta.get("content"):
            return meta["content"]
        time_tag = soup.find("time")
        if time_tag and time_tag.get("datetime"):
            return time_tag["datetime"]
        return None

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- FastAPI wiring example ----------
#
# from fastapi import APIRouter
# router = APIRouter()
# scraper = SchemeDocScraper()
#
# @router.post("/ingest")
# def ingest(urls: list[str]):
#     results = scraper.scrape_many(urls)
#     new_or_changed = []
#     for r in results:
#         if r.error:
#             continue
#         for page in r.pages:
#             if not db_has_hash(page.content_hash):   # your dedup check
#                 new_or_changed.append(page)
#     store_pages(new_or_changed)                        # your storage layer
#     return {"ingested": len(new_or_changed)}