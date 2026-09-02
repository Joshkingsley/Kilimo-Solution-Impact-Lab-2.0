"""Parsers that preserve locators. HTML -> paragraphs (ordinal), PDF -> numbered Q&A blocks or pages."""
from __future__ import annotations

import html as htmlmod
import re
import shutil
import subprocess
from pathlib import Path

NOISE_TAGS = r"<(script|style|nav|header|footer|aside|form)\b.*?</\1>"
MIN_PARA = 30


def _clean(fragment: str) -> str:
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", fragment))
    return re.sub(r"\s+", " ", t).strip()


def html_paragraphs(path: Path) -> list[str]:
    """Return the article's paragraphs in order. Ordinal (1-based) is the locator."""
    s = path.read_text(encoding="utf-8", errors="ignore")
    candidates = [
        re.search(r'<div class="entry-content[^"]*"[^>]*>(.*?)</div>\s*(?:</div>|<footer|<div class="post-)', s, re.DOTALL),
        re.search(r"<article\b.*?</article>", s, re.DOTALL),
        re.search(r"<main\b.*?</main>", s, re.DOTALL),
    ]
    for m in candidates:
        if not m:
            continue
        body = m.group(1) if m.lastindex else m.group(0)
        paras = [_clean(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.DOTALL)]
        paras = [p for p in paras if len(p) >= MIN_PARA]
        if len(paras) >= 2:
            return _dedupe(paras)
        # Some county sites paste social-media text as <div dir="auto"> blocks instead of <p>
        divs = [_clean(d) for d in re.findall(r'<div dir="auto">(.*?)</div>', body, re.DOTALL)]
        divs = [d for d in divs if len(d) >= MIN_PARA]
        if len(divs) >= 2:
            return _dedupe(divs)
    body = re.sub(NOISE_TAGS, " ", s, flags=re.DOTALL | re.IGNORECASE)
    paras = [_clean(p) for p in re.findall(r"<p\b[^>]*>(.*?)</p>", body, re.DOTALL)]
    return _dedupe([p for p in paras if len(p) >= MIN_PARA])


def _dedupe(paras: list[str]) -> list[str]:
    seen, out = set(), []
    for p in paras:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def pdf_text(path: Path) -> str:
    if shutil.which("pdftotext"):
        return subprocess.run(["pdftotext", "-raw", str(path), "-"], capture_output=True, text=True, check=True).stdout
    from pypdf import PdfReader  # fallback when poppler is absent

    return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(path)).pages)


def pdf_qa_blocks(path: Path) -> list[tuple[int, str]]:
    """Split an FAQ-style PDF into (question_number, 'Q? A...') blocks. Numbered list items without a '?' are not headers."""
    text = re.sub(r"\s+", " ", pdf_text(path))
    text = re.sub(r"GOVERNMENT-SUBSIDIZED FERTILIZER PROGRAM\s*FAQs?", " ", text, flags=re.IGNORECASE)
    # A question header has no digits in it; numbered price rows ("5. CAN 2,875") therefore never match.
    heads = list(re.finditer(r"(?<![\d,])(\d{1,2})\.\s+([A-Z][^?\d]{3,140}\?)", text))
    blocks = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.start():end].strip()
        body = re.sub(r"^\d{1,2}\.\s+", "", body)
        blocks.append((int(h.group(1)), body))
    blocks.sort(key=lambda b: b[0])
    return blocks


def pdf_pages(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [(pg.extract_text() or "").strip() for pg in PdfReader(str(path)).pages]
