"""SEC EDGAR service — free primary-document access for Pick & Shovel v2.

No API key required.  SEC asks every client to send a descriptive ``User-Agent``
with a contact and to stay under ~10 requests/second; we send a UA from the
``SEC_USER_AGENT`` env var and cache aggressively (filings are immutable once
filed), so real traffic is tiny.

Public surface
--------------
* ``get_cik(db, ticker)``            — ticker → zero-padded CIK
* ``get_recent_filings(db, cik, …)`` — latest 10-K / 10-Q / 8-K refs (+ Ex-99)
* ``fetch_filing_excerpt(db, …)``    — fetch a filing doc, strip HTML, extract the
                                       business / risk / customer-supplier sections
* ``gather_company_filings(db, …)``  — high-level bundle the deep-dive consumes
"""

from __future__ import annotations

import asyncio
import os
import re
import logging

import httpx

from .cache_service import get_cached, set_cached

logger = logging.getLogger(__name__)

_SEC_UA = os.getenv("SEC_USER_AGENT", "FinoAgent Research contact@finoagent.app")
_HEADERS = {"User-Agent": _SEC_UA, "Accept-Encoding": "gzip, deflate"}

# Cache TTLs
_TTL_CIKMAP   = 7 * 86400    # ticker→CIK map rarely changes
_TTL_SUBS     = 86400        # company submission index — refresh daily
_TTL_EXCERPT  = 30 * 86400   # filing documents are immutable

# Keep LLM payloads sane.
_EXCERPT_CAP = 22000   # chars per filing excerpt
_SECTION_CAP = 12000   # chars per individual section


# ---------------------------------------------------------------------------
# Low-level HTTP
# ---------------------------------------------------------------------------

async def _get(url: str, *, as_json: bool = False):
    async with httpx.AsyncClient(timeout=30.0, headers=_HEADERS, follow_redirects=True) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json() if as_json else resp.text


# ---------------------------------------------------------------------------
# ticker → CIK
# ---------------------------------------------------------------------------

async def get_cik(db, ticker: str) -> str | None:
    """Return the zero-padded 10-digit CIK for ``ticker`` (or None)."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return None

    cache_key = "edgar:cikmap:v1"
    mapping = await get_cached(db, cache_key) if db is not None else None
    if not mapping:
        try:
            raw = await _get("https://www.sec.gov/files/company_tickers.json", as_json=True)
        except Exception as exc:
            logger.warning("EDGAR CIK map fetch failed: %s", exc)
            return None
        # raw is {"0": {"cik_str":..., "ticker":"NVDA", ...}, ...}
        mapping = {}
        for row in raw.values():
            t = str(row.get("ticker", "")).upper()
            cik = row.get("cik_str")
            if t and cik is not None:
                mapping[t] = f"{int(cik):010d}"
        if db is not None and mapping:
            await set_cached(db, cache_key, mapping, ttl_seconds=_TTL_CIKMAP)

    return mapping.get(ticker)


# ---------------------------------------------------------------------------
# recent filings
# ---------------------------------------------------------------------------

async def get_recent_filings(
    db,
    cik: str,
    forms: tuple[str, ...] = ("10-K", "20-F", "10-Q", "6-K", "8-K"),
    per_form: int = 1,
) -> list[dict]:
    """Return the most recent filing refs for each requested form.

    Domestic filers report on 10-K/10-Q/8-K; foreign private issuers (ADRs such
    as ASML, TSMC, Sony, SAP) report on 20-F (annual) / 6-K (interim) instead, so
    both families are requested — whichever the company doesn't use returns 0.

    Each ref: ``{form, date, accession, primary_doc, url}``.  For 8-K filings we
    also (best-effort) append any Exhibit-99 documents (earnings press releases /
    investor presentations) as extra refs with ``form="8-K EX-99"``.
    """
    cik_int = int(cik)
    cache_key = f"edgar:subs:{cik_int}"
    subs = await get_cached(db, cache_key) if db is not None else None
    if not subs:
        try:
            subs = await _get(f"https://data.sec.gov/submissions/CIK{cik_int:010d}.json", as_json=True)
        except Exception as exc:
            logger.warning("EDGAR submissions fetch failed for CIK %s: %s", cik, exc)
            return []
        if db is not None:
            # Only cache the slimmer "recent" block to keep the row small.
            await set_cached(db, cache_key, {"recent": subs.get("filings", {}).get("recent", {})},
                             ttl_seconds=_TTL_SUBS)
            recent = subs.get("filings", {}).get("recent", {})
        else:
            recent = subs.get("filings", {}).get("recent", {})
    else:
        recent = subs.get("recent", {})

    form_list   = recent.get("form", [])
    acc_list    = recent.get("accessionNumber", [])
    doc_list    = recent.get("primaryDocument", [])
    date_list   = recent.get("filingDate", [])

    out: list[dict] = []
    counts: dict[str, int] = {f: 0 for f in forms}
    for i, form in enumerate(form_list):
        if form not in counts or counts[form] >= per_form:
            continue
        acc_raw = acc_list[i] if i < len(acc_list) else ""
        doc = doc_list[i] if i < len(doc_list) else ""
        date = date_list[i] if i < len(date_list) else ""
        if not acc_raw or not doc:
            continue
        acc = acc_raw.replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"
        out.append({"form": form, "date": date, "accession": acc, "primary_doc": doc, "url": url})
        counts[form] += 1

        # Best-effort: pull Ex-99 attachments (earnings releases / investor decks)
        # from 8-K (domestic) and 6-K (foreign) filings.
        if form in ("8-K", "6-K"):
            try:
                out.extend(await _get_ex99_exhibits(cik_int, acc, form))
            except Exception as exc:
                logger.debug("%s exhibit lookup failed (%s/%s): %s", form, cik_int, acc, exc)

    return out


async def _get_ex99_exhibits(cik_int: int, accession: str, parent_form: str) -> list[dict]:
    """Find Exhibit-99 documents (press releases / investor decks) in a filing."""
    index = await _get(
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/index.json",
        as_json=True,
    )
    items = (index.get("directory", {}) or {}).get("item", [])
    refs: list[dict] = []
    for it in items:
        name = str(it.get("name", "")).lower()
        # ex-99 / ex99 / exhibit 99 — earnings release or presentation
        if re.search(r"ex[-_ ]?99", name) and name.endswith((".htm", ".html", ".pdf", ".txt")):
            refs.append({
                "form": f"{parent_form} EX-99",
                "date": "",
                "accession": accession,
                "primary_doc": it.get("name"),
                "url": f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{it.get('name')}",
            })
    return refs[:2]


# ---------------------------------------------------------------------------
# document fetch + section extraction
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        text = soup.get_text(" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"[ \t ]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()
def _strip_mda_boilerplate(text: str) -> str:
    """Strip local Table of Contents index and legal disclaimers from the start of MD&A."""
    if not text:
        return ""
    # Check if there is an index/TOC in the first 1000 characters
    toc_match = (
        re.search(r"index\s+to\s+management", text[:1000], re.IGNORECASE) or 
        re.search(r"table\s+of\s+contents", text[:1000], re.IGNORECASE)
    )
    if not toc_match:
        return text

    # Identify the real start of content (after the TOC entries)
    # TOC entries don't have colons, whereas section headers do. 
    # GENERAL is typically in all-caps starting the first real paragraph.
    patterns = [
        r"\bGENERAL\b",
        r"overview\s+of\s+(our\s+)?business\s*:",
        r"executive\s+summary\s*:",
    ]
    
    candidates = []
    toc_end = toc_match.end()
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            idx = m.start()
            if idx >= toc_end:  # Skip TOC matches inside the TOC itself
                snippet = text[idx + len(m.group(0)): idx + len(m.group(0)) + 30]
                if not re.match(r"^[\s\.\-\_]*\d+\b", snippet):
                    candidates.append(idx)
                    break      # Take first occurrence
                
    if candidates:
        chosen_start = min(candidates)
        sliced = text[chosen_start:]
        return re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        
    return text


def _strip_safe_harbor(text: str) -> str:
    """Trim a leading forward-looking-statements / safe-harbor preamble from MD&A —
    the substance starts at 'Overview' / 'Results of Operations' / 'Executive Overview'.
    No-op unless the preamble is near the top and a substantive heading follows."""
    if not text:
        return text
    low = text.lower()
    fwd = low.find("forward-looking statement")
    if fwd < 0:
        fwd = low.find("forward looking statement")
    if fwd < 0 or fwd > 1500:          # preamble must be near the top to strip
        return text
    # Prefer the "Overview" heading (in priority order) — NOT the earliest match,
    # since "results of operations" also appears mid-sentence inside the preamble.
    for pat in ("overview", "executive overview", "executive summary", "results of operations"):
        i = low.find(pat, fwd + 50)
        if i != -1 and i < 9000:
            return text[i:].lstrip(" .:—–-")
    return text


def extract_relevant_sections(text: str, form: str | None = None) -> str:
    """Condense a filing to its highest-signal narrative sections.

    Form-aware because 10-K and 10-Q number their sections differently — using
    10-K anchors on a 10-Q (the old behavior) overran "Item 1A. Risk Factors"
    straight into the financial-statement tables. Now:

    * **10-K / 20-F** → MD&A (Item 7), Business (Item 1/4), Risk Factors (Item 1A/3.D)
    * **10-Q / 6-K**  → MD&A (Item 2) and Risk Factors (Item 1A, Part II end-anchors)
    * **8-K EX-99**   → the earnings press release verbatim (no Item structure)

    Customer/supplier keyword windows are always appended (Pick & Shovel v2 reads
    these to map clients/suppliers). Falls back to the document head.
    """
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text)
    f = (form or "").upper()
    is_quarter = f.startswith(("10-Q", "6-K"))
    is_annual = f.startswith(("10-K", "20-F")) or not f   # unknown → annual-style anchors
    is_release = "EX-99" in f

    # Earnings press releases have no Item structure — dense management commentary
    # and guidance; return a generous head.
    if is_release:
        return flat[:_EXCERPT_CAP]

    chunks: list[str] = []

    def _grab(start_pat: str, end_pats: list[str], cap: int) -> str | None:
        # Each item header appears twice — once in the Table of Contents (a tiny
        # stub) and once at the real section. Keep the candidate whose body is
        # LONGEST once bounded by its end anchors — that's the real section.
        starts = list(re.finditer(start_pat, flat, re.IGNORECASE))
        if not starts:
            return None
        best: str | None = None
        for sm in starts:
            start = sm.start()
            end = len(flat)
            for ep in end_pats:
                em = re.search(ep, flat[start + 50:], re.IGNORECASE)
                if em:
                    end = min(end, start + 50 + em.start())
            body = flat[start:end]
            if best is None or len(body) > len(best):
                best = body
        return best[:cap] if best else None

    # 1) MD&A — management's own discussion of results, drivers and outlook.
    if is_quarter:
        mdna = _grab(r"item\s*2\.\s*management.?s\s+discussion",
                     [r"item\s*3\.\s*quantitative", r"item\s*4\.\s*controls"], _SECTION_CAP)
    else:
        mdna = _grab(r"item\s*7\.\s*management.?s\s+discussion",
                     [r"item\s*7a\.\s*quantitative", r"item\s*8\.\s*financial"], _SECTION_CAP)
    if mdna:
        mdna = _strip_safe_harbor(_strip_mda_boilerplate(mdna))
    if mdna:
        chunks.append("=== MD&A ===\n" + mdna)

    # 2) Business (annual reports only — 10-Qs don't restate it).
    if is_annual:
        business = _grab(r"item\s*1\.\s*business",
                         [r"item\s*1a\.\s*risk", r"item\s*2\.\s*propert"], _SECTION_CAP)
        if not business:
            business = _grab(r"item\s*4\.\s*information\s+on\s+the\s+company",
                             [r"item\s*4a\.", r"item\s*5\.\s*operating"], _SECTION_CAP)
        if business:
            chunks.append("=== BUSINESS ===\n" + business)

    # 3) Risk Factors — 10-Q anchors on Part II items so it stops correctly.
    if is_quarter:
        risk = _grab(r"item\s*1a\.\s*risk\s*factors",
                     [r"item\s*2\.\s*unregistered", r"item\s*5\.\s*other\s+information",
                      r"item\s*6\.\s*exhibits", r"item\s*3\.\s*defaults"], _SECTION_CAP)
    else:
        risk = _grab(r"item\s*1a\.\s*risk\s*factors",
                     [r"item\s*1b\.", r"item\s*2\.\s*propert", r"item\s*3\.\s*legal"], _SECTION_CAP)
        if not risk:
            risk = _grab(r"item\s*3\.?\s*d?\.?\s*risk\s*factors",
                         [r"item\s*4\.\s*information", r"item\s*3\.?\s*e"], _SECTION_CAP)
    if risk:
        chunks.append("=== RISK FACTORS ===\n" + risk)

    # 4) Customer/supplier/concentration windows — always, for Pick & Shovel v2.
    kw_windows: list[str] = []
    for kw in ("customer", "supplier", "concentration", "depend on", "depends on", "single source"):
        for m in re.finditer(re.escape(kw), flat, re.IGNORECASE):
            lo = max(0, m.start() - 350)
            hi = min(len(flat), m.end() + 350)
            kw_windows.append(flat[lo:hi])
            if len(kw_windows) >= 12:
                break
        if len(kw_windows) >= 12:
            break
    if kw_windows and not chunks:
        chunks.append("=== CUSTOMER / SUPPLIER CONTEXT ===\n" + "\n…\n".join(kw_windows))
    elif kw_windows:
        chunks.append("=== ADDITIONAL CUSTOMER / SUPPLIER CONTEXT ===\n" + "\n…\n".join(kw_windows[:6]))

    if not chunks:
        chunks.append(flat[:_EXCERPT_CAP])

    return "\n\n".join(chunks)[:_EXCERPT_CAP]


async def fetch_filing_excerpt(db, cik: str, accession: str, primary_doc: str, form: str | None = None) -> str:
    """Fetch a filing document and return its condensed, relevant excerpt (cached).

    ``form`` steers form-aware section extraction (10-K vs 10-Q vs EX-99). The
    cache key is versioned (``:v2``) so the form-aware rewrite supersedes any
    excerpts cached by the previous 10-K-only extractor.
    """
    cik_int = int(cik)
    cache_key = f"edgar:excerpt:v4:{cik_int}:{accession}:{primary_doc}"
    cached = await get_cached(db, cache_key) if db is not None else None
    if cached and isinstance(cached, dict):
        return cached.get("text", "")

    url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession}/{primary_doc}"
    try:
        raw = await _get(url)
    except Exception as exc:
        logger.warning("EDGAR doc fetch failed %s: %s", url, exc)
        return ""

    text = _strip_html(raw) if "<" in raw[:2000] else raw
    excerpt = extract_relevant_sections(text, form=form)
    if db is not None and excerpt:
        await set_cached(db, cache_key, {"text": excerpt}, ttl_seconds=_TTL_EXCERPT)
    return excerpt


# ---------------------------------------------------------------------------
# high-level bundle
# ---------------------------------------------------------------------------

async def gather_company_filings(db, ticker: str, max_8k: int = 2) -> dict:
    """Return a deep-dive-ready bundle of recent filing excerpts for ``ticker``.

    ``{ticker, cik, documents_found, docs:[{form,date,url,excerpt}]}``
    """
    ticker = (ticker or "").strip().upper()
    cik = await get_cik(db, ticker)
    if not cik:
        return {"ticker": ticker, "cik": None, "documents_found": False, "docs": []}

    refs = await get_recent_filings(db, cik, forms=("10-K", "20-F", "10-Q", "6-K", "8-K"), per_form=1)
    # Cap interim/event-driven refs (8-K, 6-K and their exhibits) so we don't
    # over-fetch; the annual (10-K/20-F) carries most of the signal.
    seen_event = 0
    capped: list[dict] = []
    for r in refs:
        if r["form"].startswith(("8-K", "6-K")):
            if seen_event >= max_8k:
                continue
            seen_event += 1
        capped.append(r)

    # NOTE: fetch sequentially — a single AsyncSession (and its asyncpg
    # connection) cannot be used by concurrent coroutines, and these calls all
    # touch the shared request session via the cache. It's only a few docs.
    docs: list[dict] = []
    for ref in capped:
        try:
            excerpt = await fetch_filing_excerpt(db, cik, ref["accession"], ref["primary_doc"], form=ref["form"])
        except Exception as exc:
            logger.debug("excerpt fetch failed %s: %s", ref.get("url"), exc)
            continue
        if excerpt:
            docs.append({"form": ref["form"], "date": ref["date"], "url": ref["url"], "excerpt": excerpt})

    return {
        "ticker": ticker,
        "cik": cik,
        "documents_found": bool(docs),
        "docs": docs,
    }
