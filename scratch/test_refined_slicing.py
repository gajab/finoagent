import re

with open("scratch/mck_mda_full.txt", "r") as f:
    text = f.read()

# Let's test a simple heuristic:
# We look for common headers: "Executive Summary", "Overview of Consolidated Results", "Results of Operations"
# We find their positions. For each match, we look at the next 20 characters.
# If the next 20 characters contain a page number (like " 35 " or " . . . 35" or just a number at the end of dots), we classify it as TOC/Index.
# Otherwise, it's a real section header!
# We choose the first real section header as our slice start.

def get_clean_start(text: str) -> int:
    headers = [
        r"executive\s+summary",
        r"overview\s+of\s+consolidated\s+results",
        r"results\s+of\s+operations",
        r"overview\s+of\s+segment\s+results",
    ]
    
    candidates = []
    for header in headers:
        for m in re.finditer(header, text, re.IGNORECASE):
            idx = m.start()
            # Grab the 40 characters after the match
            snippet = text[idx + len(m.group(0)): idx + len(m.group(0)) + 40]
            # Check if there is a number at the end of dots/spaces (e.g., "  35", " ... 35")
            # If the snippet is just spaces/dots followed by a number, it's definitely a TOC entry.
            is_toc = re.match(r"^[\s\.\-\_]*\d+\b", snippet)
            if is_toc:
                print(f"[TOC Match] '{m.group(0)}' followed by '{snippet.strip()}' at index {idx}")
            else:
                print(f"[REAL Match] '{m.group(0)}' at index {idx}. Snippet: '{snippet[:50]}'")
                candidates.append(idx)
                
    if candidates:
        # We want to take the first real section start. 
        # But wait! If we have multiple different headers, we should take the minimum index.
        # But we must be careful: if "Results of Operations" matches the header of the whole Item 7:
        # "Item 7. Management's Discussion and Analysis of Financial Condition and Results of Operations"
        # we don't want to choose that index 72!
        # How do we avoid matching the main Item 7 header?
        # The main Item 7 header starts with "Item 7" or "Item 2".
        # So any match within the first 150 characters of the whole text is likely the main header, which we should ignore.
        valid_candidates = [c for c in candidates if c > 150]
        if valid_candidates:
            return min(valid_candidates)
            
    return 0

start_idx = get_clean_start(text)
print(f"\nRecommended start index: {start_idx}")
if start_idx > 0:
    print("Preview of sliced text:")
    print(text[start_idx:start_idx+1000])
else:
    print("No slicing recommended, using full text.")
