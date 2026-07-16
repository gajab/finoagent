import re

def clean_boilerplate(text: str) -> str:
    # Check if there is an index/TOC at the beginning of the MD&A block
    # Typically indicated by "INDEX TO" or "Table of Contents" in the first 600 chars
    has_index = False
    header_snippet = text[:800]
    if re.search(r"index\s+to\s+management", header_snippet, re.IGNORECASE) or re.search(r"table\s+of\s+contents", header_snippet, re.IGNORECASE):
        has_index = True
        
    if not has_index:
        return text
        
    # If there is an index, we want to find the first real section header to start from.
    # We look for "Overview of Our Business", "Overview of Business", "Executive Summary", or "General"
    # that is NOT part of the index. Index entries are typically in the first 1000 characters.
    # So we search for these headers starting after index 500.
    targets = [
        r"overview\s+of\s+(our\s+)?business",
        r"executive\s+summary",
        r"general\s+management",
        r"results\s+of\s+operations",
    ]
    
    candidates = []
    for target in targets:
        for m in re.finditer(target, text, re.IGNORECASE):
            idx = m.start()
            if idx > 500:
                # Double check that this match itself doesn't look like a TOC entry 
                # (e.g. followed by a page number)
                snippet = text[idx + len(m.group(0)): idx + len(m.group(0)) + 30]
                if not re.match(r"^[\s\.\-\_]*\d+\b", snippet):
                    candidates.append(idx)
                    break # Take the first occurrence for this pattern after index 500
                    
    if candidates:
        chosen_start = min(candidates)
        # Clean up any leading punctuation/dashes
        sliced = text[chosen_start:]
        # If it starts with some prefix character like "." or "-", strip it
        sliced = re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        return sliced
        
    return text

# Test on MCK MD&A
with open("scratch/mck_mda_full.txt", "r") as f:
    mck_text = f.read()
    
print("MCK Original Start:")
print(mck_text[:500])
print("-" * 50)

mck_cleaned = clean_boilerplate(mck_text)
print("MCK Cleaned Start:")
print(mck_cleaned[:1000])
print("-" * 50)
print(f"Original length: {len(mck_text)}, Cleaned length: {len(mck_cleaned)} (Saved {len(mck_text) - len(mck_cleaned)} chars)")
