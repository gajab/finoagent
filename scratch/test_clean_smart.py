import re

def clean_boilerplate_smart(text: str) -> str:
    # Look for TOC start markers
    toc_match = re.search(r"index\s+to\s+management", text[:1000], re.IGNORECASE) or re.search(r"table\s+of\s+contents", text[:1000], re.IGNORECASE)
    
    if not toc_match:
        return text
        
    toc_start = toc_match.start()
    
    # We want to find where the actual text starts.
    # We look for:
    # 1. "GENERAL" in all-caps (which usually starts the text after MCK's index)
    # 2. "Overview of Our Business:" or "Overview of Business:" (with a colon, indicating the section start)
    # 3. "Executive Summary:" (with a colon)
    # after the TOC start.
    
    patterns = [
        r"\bGENERAL\b",
        r"overview\s+of\s+(our\s+)?business\s*:",
        r"executive\s+summary\s*:",
    ]
    
    candidates = []
    for pattern in patterns:
        for m in re.finditer(pattern, text):
            idx = m.start()
            if idx > toc_start:
                # Make sure it's not a false positive in the TOC itself 
                # (TOC entries do not have colons, and "GENERAL" is usually lowercase "General" in MCK's TOC)
                snippet = text[idx:idx+50]
                print(f"Candidate found at {idx}: '{snippet}'")
                candidates.append(idx)
                break # Take the first match for this pattern
                
    if candidates:
        chosen_start = min(candidates)
        sliced = text[chosen_start:]
        sliced = re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        return sliced
        
    return text

with open("scratch/mck_mda_full.txt", "r") as f:
    mck_text = f.read()

mck_cleaned = clean_boilerplate_smart(mck_text)
print("\nMCK Cleaned Start (Smart):")
print(mck_cleaned[:1000])
print("-" * 50)
print(f"Original length: {len(mck_text)}, Cleaned length: {len(mck_cleaned)} (Saved {len(mck_text) - len(mck_cleaned)} chars)")
