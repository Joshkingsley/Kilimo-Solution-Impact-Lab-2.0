"""corpus/sources.yaml — the hand-maintained register of what we are allowed to cite
(SPEC §7.4) — and byte-for-byte snapshotting into corpus/raw/ (SPEC §7.3).

Security posture: the ingester only ever fetches URLs that are already in the
register, over https, from allowlisted hosts that resolve to public IPs, following
at most a few same-host redirects, with a hard byte cap. Arbitrary URLs never enter
this module from the API.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.rag import geo
from app.rag.config import Settings
from app.rag.schema import DOC_ID_RE, Authority, DocType, Lang
from app.rag.security import validate_fetch_url

log = logging.getLogger("nitapata.corpus")

# Publisher hosts we may snapshot from. Mirrors SPEC §7.1. Sub-domains are allowed.
ALLOWED_SOURCE_HOSTS: set[str] = {
    "kenyalaw.org", "gazettes.africa", "ncpb.co.ke", "kilimo.go.ke", "kalro.org",
    "parliament.go.ke", "kakamega.go.ke", "machakos.go.ke", "treasury.go.ke",
    "agricultureauthority.go.ke",
}


class SourceDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    doc_id: str
    title: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    doc_type: DocType
    authority: Authority
    url: str
    raw_path: str
    publish_date: date
    cycle: str | None = None
    county: str | None = None
    lang: Lang = "en"
    needs_ocr: bool = False
    format: Literal["pdf", "html", "text"] | None = None
    short_cite: str | None = None
    use_for: list[str] = Field(default_factory=list)
    do_not_use_for: list[str] = Field(default_factory=list)
    retrieved_at: date | None = None
    sha256: str | None = None
    notes: str | None = None
    enabled: bool = True

    @field_validator("doc_id")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not DOC_ID_RE.match(v):
            raise ValueError(f"doc_id must be a lowercase slug: {v!r}")
        return v

    @field_validator("format", mode="before")
    @classmethod
    def _format_aliases(cls, v):
        return {"pdf-text": "pdf", "pdf-ocr": "pdf", "txt": "text"}.get(v, v)

    @field_validator("county", mode="before")
    @classmethod
    def _county_canonical(cls, v):
        if v in (None, "", "null"):
            return None
        c = geo.normalise_county(str(v))
        if c is None:
            raise ValueError(f"unknown county {v!r}")
        return c

    @field_validator("raw_path")
    @classmethod
    def _raw_inside_corpus(cls, v: str) -> str:
        p = Path(v)
        if p.is_absolute() or ".." in p.parts or not str(p).startswith("corpus/raw/"):
            raise ValueError("raw_path must be relative and under corpus/raw/")
        return v

    def resolved_format(self) -> str:
        if self.format:
            return self.format
        suffix = Path(self.raw_path).suffix.lower()
        return {"pdf": "pdf", ".pdf": "pdf", ".txt": "text", ".md": "text"}.get(suffix, "html")


def load_sources(settings: Settings) -> list[SourceDoc]:
    path = settings.sources_file
    if not path.exists():
        raise FileNotFoundError(f"sources register not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    if isinstance(data, dict):            # register format: {sources: [...], wanted_not_found: [...]}
        data = data.get("sources") or []
    if not isinstance(data, list):
        raise ValueError("sources.yaml must be a list of documents or a mapping with a `sources` list")
    docs = [SourceDoc.model_validate(d) for d in data]
    ids = [d.doc_id for d in docs]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate doc_id(s) in sources.yaml: {sorted(dupes)}")
    return docs


def raw_file(settings: Settings, doc: SourceDoc) -> Path:
    # raw_path is validated to be under corpus/raw; anchor it at the corpus dir's parent.
    root = settings.corpus_dir.parent
    p = (root / doc.raw_path).resolve()
    if not str(p).startswith(str((settings.corpus_dir / "raw").resolve())):
        raise ValueError("raw_path escapes corpus/raw")
    return p


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch_snapshot(settings: Settings, doc: SourceDoc, *, force: bool = False) -> dict:
    """Download `doc.url` into its raw_path. Returns {'status': 'unchanged'|'fetched', 'sha256': ..}."""
    target = raw_file(settings, doc)
    target.parent.mkdir(parents=True, exist_ok=True)

    url = doc.url
    content: bytes | None = None
    with httpx.Client(timeout=settings.fetch_timeout_seconds, follow_redirects=False,
                      headers={"User-Agent": settings.fetch_user_agent}) as client:
        for _ in range(settings.fetch_max_redirects + 1):
            validate_fetch_url(url, ALLOWED_SOURCE_HOSTS)
            with client.stream("GET", url) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise RuntimeError(f"redirect without location from {url}")
                    url = urljoin(url, loc)
                    continue
                resp.raise_for_status()
                declared = int(resp.headers.get("content-length") or 0)
                if declared > settings.fetch_max_bytes:
                    raise RuntimeError(f"{url} declares {declared} bytes > cap")
                buf = bytearray()
                for part in resp.iter_bytes():
                    buf.extend(part)
                    if len(buf) > settings.fetch_max_bytes:
                        raise RuntimeError(f"{url} exceeded byte cap while streaming")
                content = bytes(buf)
                ctype = resp.headers.get("content-type", "")
                break
        else:
            raise RuntimeError(f"too many redirects fetching {doc.url}")

    assert content is not None
    fmt = doc.resolved_format()
    if fmt == "pdf" and not content.startswith(b"%PDF"):
        raise RuntimeError(f"{doc.doc_id}: expected a PDF but got {ctype or 'unknown content-type'}")
    if fmt == "html" and b"<" not in content[:2048]:
        raise RuntimeError(f"{doc.doc_id}: expected HTML but body does not look like markup")

    digest = hashlib.sha256(content).hexdigest()
    if target.exists() and not force and sha256_of(target) == digest:
        return {"status": "unchanged", "sha256": digest, "bytes": len(content), "final_url": url}
    target.write_bytes(content)
    log.info("snapshotted %s (%d bytes) -> %s", doc.doc_id, len(content), target)
    return {"status": "fetched", "sha256": digest, "bytes": len(content), "final_url": url,
            "retrieved_at": datetime.now(timezone.utc).date().isoformat()}


def source_host(doc: SourceDoc) -> str:
    return urlparse(doc.url).hostname or ""
