import sys
import os
import asyncio

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend')))

from app.services.edgar_service import get_cik, get_recent_filings, fetch_filing_excerpt, _get, _strip_html
import re

async def main():
    print("Fetching raw MCK 10-K...")
    cik = await get_cik(None, "MCK")
    refs = await get_recent_filings(None, cik, forms=("10-K",))
    ref = refs[0]
    
    url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{ref['accession']}/{ref['primary_doc']}"
    raw = await _get(url)
    text = _strip_html(raw)
    flat = re.sub(r"\s+", " ", text)
    
    # Let's find "Item 7" start and see the first 30,000 characters of the MD&A section
    starts = list(re.finditer(r"item\s*7\.\s*management.?s\s+discussion", flat, re.IGNORECASE))
    print(f"Found {len(starts)} matches for Item 7 header in the flat text.")
    
    # We want to examine the second/longest match (which is the real section, not the TOC)
    best_body = ""
    for sm in starts:
        start = sm.start()
        end = len(flat)
        # Search for end anchor (like Item 8 or Item 7A)
        em = re.search(r"item\s*8\.\s*financial", flat[start + 50:], re.IGNORECASE)
        if em:
            end = start + 50 + em.start()
        body = flat[sm.start():end]
        if len(body) > len(best_body):
            best_body = body
            
    print(f"Real Item 7 MD&A section length: {len(best_body)} characters.")
    
    # Write the first 30,000 chars of MD&A to a file to examine
    with open("scratch/mck_mda_full.txt", "w") as f:
        f.write(best_body[:30000])
    print("Saved first 30,000 chars of Item 7 to scratch/mck_mda_full.txt")
    
    # Find headers inside the first 30,000 chars
    print("\nCommon sub-headers in MD&A:")
    sub_headers = [
        r"Executive Summary",
        r"Overview of Consolidated Results",
        r"Overview of Segment Results",
        r"Results of Operations",
        r"Financial Condition, Liquidity, and Capital Resources"
    ]
    for sh in sub_headers:
        matches = list(re.finditer(sh, best_body, re.IGNORECASE))
        print(f"  Header '{sh}': found {len(matches)} matches")
        for i, m in enumerate(matches):
            idx = m.start()
            context = best_body[max(0, idx-50):idx+150]
            print(f"    Match {i+1} at index {idx}: '{context}'")

if __name__ == "__main__":
    asyncio.run(main())
