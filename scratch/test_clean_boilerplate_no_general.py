import re

def clean_boilerplate(text: str) -> str:
    # Check if there is an index/TOC at the beginning of the MD&A block
    has_index = False
    header_snippet = text[:800]
    if re.search(r"index\s+to\s+management", header_snippet, re.IGNORECASE) or re.search(r"table\s+of\s+contents", header_snippet, re.IGNORECASE):
        has_index = True
        
    if not has_index:
        return text
        
    # We remove "general" and look for the actual sections: Overview of Our Business or Executive Summary
    targets = [
        r"overview\s+of\s+(our\s+)?business",
        r"executive\s+summary",
        r"overview\s+of\s+consolidated\s+results",
        r"results\s+of\s+operations",
    ]
    
    candidates = []
    for target in targets:
        for m in re.finditer(target, text, re.IGNORECASE):
            idx = m.start()
            if idx > 500:
                snippet = text[idx + len(m.group(0)): idx + len(m.group(0)) + 30]
                if not re.match(r"^[\s\.\-\_]*\d+\b", snippet):
                    candidates.append(idx)
                    break
                    
    if candidates:
        chosen_start = min(candidates)
        sliced = text[chosen_start:]
        sliced = re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        return sliced
        
    return text

with open("scratch/mck_mda_full.txt", "r") as f:
    mck_text = f.read()

mck_cleaned = clean_boilerplate(mck_text)
print("MCK Cleaned Start (Without General):")
print(mck_cleaned[:1000])
print("-" * 50)
print(f"Original length: {len(mck_text)}, Cleaned length: {len(mck_cleaned)} (Saved {len(mck_text) - len(mck_cleaned)} chars)")
