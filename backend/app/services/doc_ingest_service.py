"""Document ingestion for Pick & Shovel v2.

Two jobs, both best-effort and key-free:

1. Pull readable text from a URL the user (or the pipeline) supplies — an HTML
   page (blog / IR page / news) or a PDF (investor presentation / fact sheet).
2. Discover a company's investor-relations materials starting from the homepage
   yfinance reports, so the deep-dive can read recent presentations.

Images are handled via GPT-4o vision (``describe_image``) — no extra dependency,
since ``call_llm`` forwards OpenAI multimodal ``content`` blocks verbatim.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from .cache_service import get_cached, set_cached
from .llm_service import call_llm

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; FinoAgentResearch/1.0; +https://finoagent.app)"
_HEADERS = {"User-Agent": _UA, "Accept": "text/html,application/pdf,*/*"}

_TTL_URL = 86400        # 24 h — fetched pages/PDFs
_TEXT_CAP = 16000       # chars kept per document
_PDF_MAX_PAGES = 40


# ---------------------------------------------------------------------------
# URL → text
# ---------------------------------------------------------------------------

def _looks_like_pdf(url: str, content_type: str, body: bytes) -> bool:
    if "application/pdf" in (content_type or "").lower():
        return True
    if url.lower().split("?")[0].endswith(".pdf"):
        return True
    return body[:5] == b"%PDF-"


def _pdf_to_text(body: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(body))
        pages = reader.pages[:_PDF_MAX_PAGES]
        text = "\n".join((p.extract_text() or "") for p in pages)
        return re.sub(r"\n{3,}", "\n\n", text)
    except Exception as exc:
        logger.debug("PDF parse failed: %s", exc)
        return ""


def _html_to_text(html: str) -> tuple[str, str]:
    """Return (title, text)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = (soup.title.string if soup.title else "") or ""
        text = soup.get_text(" ")
    except Exception:
        title = ""
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()
    return title.strip(), text


async def fetch_url_text(db, url: str) -> dict:
    """Fetch ``url`` and return ``{ok, kind, title, text, url}`` (best-effort, cached)."""
    url = (url or "").strip()
    if not url or not re.match(r"^https?://", url):
        return {"ok": False, "kind": "invalid", "title": "", "text": "", "url": url}

    cache_key = "ingest:url:" + hashlib.md5(url.encode()).hexdigest()
    cached = await get_cached(db, cache_key) if db is not None else None
    if cached and isinstance(cached, dict):
        return cached

    result = {"ok": False, "kind": "error", "title": "", "text": "", "url": url}
    try:
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            body = resp.content
        if _looks_like_pdf(url, ctype, body):
            text = _pdf_to_text(body)
            result = {"ok": bool(text), "kind": "pdf", "title": url.rsplit("/", 1)[-1],
                      "text": text[:_TEXT_CAP], "url": url}
        else:
            title, text = _html_to_text(body.decode("utf-8", "ignore"))
            result = {"ok": bool(text), "kind": "html", "title": title,
                      "text": text[:_TEXT_CAP], "url": url}
    except Exception as exc:
        logger.info("fetch_url_text failed for %s: %s", url, exc)
        result = {"ok": False, "kind": "error", "title": "", "text": "", "url": url}

    if db is not None and result["ok"]:
        await set_cached(db, cache_key, result, ttl_seconds=_TTL_URL)
    return result


# ---------------------------------------------------------------------------
# Investor-relations discovery
# ---------------------------------------------------------------------------

_IR_HINTS = ("investor", "investors", "/ir", "ir.", "shareholder", "/news", "presentation")


async def discover_ir_documents(db, website: str, limit: int = 3) -> list[dict]:
    """Best-effort: from a company homepage, find the IR page and recent PDF decks.

    Returns up to ``limit`` ``{url, kind, title, text}`` entries.  Empty if the site
    is JS-only or unreachable.
    """
    website = (website or "").strip()
    if not website or not re.match(r"^https?://", website):
        return []

    home = await fetch_url_text(db, website)
    docs: list[dict] = []

    # Parse homepage links to locate an IR section.
    try:
        from bs4 import BeautifulSoup
        async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
            resp = await client.get(website)
            soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as exc:
        logger.debug("IR homepage parse failed for %s: %s", website, exc)
        return []

    base = website
    ir_links: list[str] = []
    pdf_links: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text(" ") or "").lower()
        full = urljoin(base, href)
        low = full.lower()
        if low.endswith(".pdf"):
            pdf_links.append(full)
        elif any(h in low or h in text for h in _IR_HINTS):
            ir_links.append(full)

    # Visit the most promising IR page to harvest recent PDF decks.
    for ir_url in ir_links[:2]:
        try:
            async with httpx.AsyncClient(timeout=25.0, headers=_HEADERS, follow_redirects=True) as client:
                resp = await client.get(ir_url)
                from bs4 import BeautifulSoup
                isoup = BeautifulSoup(resp.text, "html.parser")
            for a in isoup.find_all("a", href=True):
                full = urljoin(ir_url, a["href"])
                if full.lower().endswith(".pdf"):
                    pdf_links.append(full)
        except Exception:
            continue

    # Dedupe, prefer presentation-like names, fetch a couple.
    seen: set[str] = set()
    ordered = sorted(set(pdf_links), key=lambda u: (0 if re.search(r"present|deck|investor|overview", u.lower()) else 1))
    for url in ordered:
        if url in seen:
            continue
        seen.add(url)
        doc = await fetch_url_text(db, url)
        if doc["ok"]:
            docs.append(doc)
        if len(docs) >= limit:
            break

    return docs


# ---------------------------------------------------------------------------
# Image understanding (GPT-4o vision)
# ---------------------------------------------------------------------------

async def describe_image(data_url: str, prompt: str, openai_key: str, model: str = "gpt-4o") -> str:
    """Describe an image (base64 data URL) for research context, via GPT-4o vision."""
    if not data_url or not openai_key:
        return ""
    try:
        out = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=700,
            temperature=0.2,
        )
        return (out or "").strip()
    except Exception as exc:
        logger.info("describe_image failed: %s", exc)
        return ""
