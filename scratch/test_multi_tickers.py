import sys
import os
import asyncio
import re

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from app.services.edgar_service import get_cik, get_recent_filings, _get, _strip_html

async def test_ticker(ticker):
    print(f"\n================ TESTING {ticker} ================")
    cik = await get_cik(None, ticker)
    if not cik:
        print("CIK not found")
        return
    refs = await get_recent_filings(None, cik, forms=("10-K",))
    if not refs:
        print("No 10-K found")
        return
    ref = refs[0]
    
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{ref['accession']}/{ref['primary_doc']}"
    raw = await _get(url)
    text = _strip_html(raw)
    flat = re.sub(r"\s+", " ", text)
    
    # Grab Item 7
    starts = list(re.finditer(r"item\s*7\.\s*management.?s\s+discussion", flat, re.IGNORECASE))
    if not starts:
        print("Item 7 not found")
        return
        
    best_body = ""
    for sm in starts:
        start = sm.start()
        end = len(flat)
        em = re.search(r"item\s*8\.\s*financial", flat[start + 50:], re.IGNORECASE)
        if em:
            end = start + 50 + em.start()
        body = flat[sm.start():end]
        if len(body) > len(best_body):
            best_body = body
            
    print(f"MD&A section length: {len(best_body)}")
    print("Start of MD&A:")
    print(best_body[:800])
    print("-" * 50)
    
    # Let's search for the real start candidates
    # We want to find "Executive Summary" or "Overview" that is NOT followed by numbers
    for m in re.finditer(r"\b(executive\s+summary|overview|results\s+of\s+operations)\b", best_body, re.IGNORECASE):
        idx = m.start()
        following = best_body[idx:idx+150]
        # Check if it's an index entry
        is_index = re.search(r"^(executive\s+summary|overview|results\s+of\s+operations)\s*(\.?\s*)*\d+\b", following, re.IGNORECASE)
        if is_index:
            print(f"[TOC/Index Match] at {idx}: '{following[:100]}'")
        else:
            print(f"[REAL Section Start] at {idx}: '{following[:150]}'")
            print("Remaining text preview from this start:")
            print(best_body[idx:idx+800])
            break

async def main():
    for ticker in ["AAPL", "MSFT", "NVDA", "MCK"]:
        await test_ticker(ticker)

if __name__ == "__main__":
    asyncio.run(main())
