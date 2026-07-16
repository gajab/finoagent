"""Japan EDINET service — annual securities reports (有価証券報告書) for Tokyo-listed names.

EDINET is the FSA's official disclosure system; the 有価証券報告書 (yūkashōken
hōkokusho) is the Japanese 10-K equivalent. The v2 API is free but needs a
subscription key (register at edinet-fsa.go.jp), read from ``EDINET_API_KEY``.

Practical constraint: EDINET has **no per-company lookup** — only a per-day
document list. So we scan the daily lists newest-first over a bounded window to
find a company's latest annual report, then cache the resolved docID (the
expensive step) so repeat lookups are instant. Day-lists are fetched concurrently
WITHOUT the DB session to keep the cold scan fast and avoid session contention.
If no key is set, every call returns "no documents" cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import date, timedelta

import httpx

from .cache_service import get_cached, set_cached
from .doc_ingest_service import _pdf_to_text

logger = logging.getLogger(__name__)

_BASE = "https://api.edinet-fsa.go.jp/api/v2"
_KEY = os.getenv("EDINET_API_KEY", "")

_WINDOW_DAYS = int(os.getenv("EDINET_WINDOW_DAYS", "365"))  # how far back to scan
_CHUNK = 12          # days fetched per concurrent batch
_CONCURRENCY = 5     # polite parallelism for the daily-list scan
_DOCTYPE_ANNUAL = "120"   # 有価証券報告書 (annual securities report)
_TTL_RESOLVE = 7 * 86400  # secCode → docID resolution
_TTL_TEXT = 30 * 86400    # document text (immutable)
_EXCERPT_CAP = 22000


def is_enabled() -> bool:
    return bool(_KEY)


def to_sec_code(ticker: str) -> str | None:
    """'7203' or '7203.T' → EDINET 5-digit secCode '72030'. None if not a JP code."""
    t = (ticker or "").strip().upper().replace(".T", "").replace(".JP", "")
    return f"{t}0" if re.fullmatch(r"\d{4}", t) else None


async def _get(url: str, *, as_json: bool = False):
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json() if as_json else resp.content


async def _day_results(d: date) -> list[dict]:
    """Document list for one day (no DB; tolerant of empty/non-business days)."""
    url = f"{_BASE}/documents.json?date={d.isoformat()}&type=2&Subscription-Key={_KEY}"
    try:
        data = await _get(url, as_json=True)
        return data.get("results") or []
    except Exception as exc:
        logger.debug("EDINET day fetch failed %s: %s", d, exc)
        return []


async def get_annual_report(db, sec_code: str) -> dict | None:
    """Resolve the latest annual-report docID for ``sec_code`` (cached).

    Returns ``{doc_id, date, filer}`` or None.
    """
    if not _KEY or not sec_code:
        return None

    cache_key = f"edinet:annual:{sec_code}"
    cached = await get_cached(db, cache_key) if db is not None else None
    if cached:
        return cached

    today = date.today()
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _guarded(d: date) -> tuple[date, list[dict]]:
        async with sem:
            return d, await _day_results(d)

    found: dict | None = None
    # Scan newest-first in chunks; stop as soon as we hit a match.
    for chunk_start in range(0, _WINDOW_DAYS, _CHUNK):
        days = [today - timedelta(days=chunk_start + i) for i in range(_CHUNK)
                if chunk_start + i < _WINDOW_DAYS]
        results = await asyncio.gather(*[_guarded(d) for d in days])
        # Preserve newest-first ordering within the chunk.
        for d, day_list in sorted(results, key=lambda x: x[0], reverse=True):
            for r in day_list:
                if str(r.get("secCode") or "") == sec_code and str(r.get("docTypeCode") or "") == _DOCTYPE_ANNUAL:
                    found = {"doc_id": r.get("docID"), "date": d.isoformat(),
                             "filer": r.get("filerName", "")}
                    break
            if found:
                break
        if found:
            break

    if found and db is not None:
        await set_cached(db, cache_key, found, ttl_seconds=_TTL_RESOLVE)
    return found


def _extract_jp_sections(text: str) -> str:
    """Condense a Japanese annual report to client/supplier-relevant context.

    The 有報 isn't structured like a 10-K, so we grab windows around Japanese
    (and English) signal terms plus the document head. GPT-4o reads Japanese.
    """
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text)
    terms = ["事業の内容", "事業等のリスク", "主要な顧客", "顧客", "販売先", "得意先",
             "仕入", "仕入先", "調達", "供給", "サプライ", "主要な設備",
             "customer", "supplier", "depend"]
    windows: list[str] = []
    for kw in terms:
        for m in re.finditer(re.escape(kw), flat):
            lo = max(0, m.start() - 250)
            hi = min(len(flat), m.end() + 350)
            windows.append(flat[lo:hi])
            if len(windows) >= 18:
                break
        if len(windows) >= 18:
            break
    head = flat[:6000]
    body = "=== 事業概要・顧客・仕入先コンテキスト ===\n" + "\n…\n".join(windows) if windows else ""
    return (head + "\n\n" + body)[:_EXCERPT_CAP]


async def fetch_document_text(db, doc_id: str) -> str:
    """Download an EDINET document (PDF) and return its condensed excerpt (cached)."""
    if not _KEY or not doc_id:
        return ""
    cache_key = f"edinet:text:{doc_id}"
    cached = await get_cached(db, cache_key) if db is not None else None
    if cached and isinstance(cached, dict):
        return cached.get("text", "")

    url = f"{_BASE}/documents/{doc_id}?type=2&Subscription-Key={_KEY}"  # type=2 → PDF
    try:
        body = await _get(url)
    except Exception as exc:
        logger.info("EDINET doc fetch failed %s: %s", doc_id, exc)
        return ""

    text = _pdf_to_text(body)
    excerpt = _extract_jp_sections(text)
    if db is not None and excerpt:
        await set_cached(db, cache_key, {"text": excerpt}, ttl_seconds=_TTL_TEXT)
    return excerpt


async def gather_company_filings(db, ticker: str) -> dict:
    """EDINET equivalent of edgar_service.gather_company_filings for a JP ticker."""
    sec_code = to_sec_code(ticker)
    if not sec_code or not _KEY:
        return {"ticker": ticker, "documents_found": False, "docs": []}

    ann = await get_annual_report(db, sec_code)
    if not ann:
        return {"ticker": ticker, "documents_found": False, "docs": []}

    excerpt = await fetch_document_text(db, ann["doc_id"])
    if not excerpt:
        return {"ticker": ticker, "documents_found": False, "docs": []}

    url = f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?{ann['doc_id']}"
    return {
        "ticker": ticker,
        "documents_found": True,
        "docs": [{
            "form": "EDINET 有価証券報告書",
            "date": ann.get("date", ""),
            "url": url,
            "excerpt": excerpt,
        }],
    }
