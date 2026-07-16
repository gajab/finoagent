import re

# Load the saved full MD&A text
with open("scratch/mck_mda_full.txt", "r") as f:
    text = f.read()

# Let's write a parser helper that identifies the real "Executive Summary" or "Results of Operations" or "Overview of Consolidated Results"
# and slices the MD&A block to start from there.
def find_real_start(text: str) -> int:
    # A list of candidate starting patterns for the real content
    # We want to match headers like "Executive Summary:" or "Executive Summary" that are NOT followed by page numbers (e.g. "Executive Summary 35")
    # or "RESULTS OF OPERATIONS" or "Overview of Consolidated Results" (not followed by page numbers).
    
    # 1. Look for Executive Summary followed by non-digit content (e.g. colon, or just words)
    # We can match "Executive Summary" and check if it's followed by a page number.
    # An index entry usually looks like "Executive Summary [dots/spaces] [page number]"
    
    candidates = []
    
    # Check Executive Summary
    for m in re.finditer(r"executive\s+summary", text, re.IGNORECASE):
        idx = m.start()
        # Look ahead 50 characters. If there is a page number like " 35 " or " . . . 35", it's likely an index
        following = text[idx:idx+100]
        # Regex to detect if it looks like an index entry (e.g. followed by dots/spaces and a page number under 300)
        # For example: "Executive Summary 35" or "Executive Summary ... 35"
        if re.search(r"executive\s+summary\s+(\.?\s*){2,}\d+\b", following, re.IGNORECASE) or re.search(r"executive\s+summary\s+\d+\b", following, re.IGNORECASE):
            print(f"Skipping index match for Executive Summary at {idx}: '{text[idx:idx+80]}'")
            continue
        print(f"Found real Executive Summary start candidate at {idx}: '{text[idx:idx+150]}'")
        candidates.append(idx)
        break # Take the first non-index match
        
    # Check Results of Operations
    for m in re.finditer(r"results\s+of\s+operations", text, re.IGNORECASE):
        idx = m.start()
        following = text[idx:idx+100]
        if re.search(r"results\s+of\s+operations\s+(\.?\s*){2,}\d+\b", following, re.IGNORECASE) or re.search(r"results\s+of\s+operations\s+\d+\b", following, re.IGNORECASE):
            print(f"Skipping index match for Results of Operations at {idx}: '{text[idx:idx+80]}'")
            continue
        print(f"Found real Results of Operations start candidate at {idx}: '{text[idx:idx+150]}'")
        candidates.append(idx)
        break
        
    if candidates:
        return min(candidates)
    return 0

start_idx = find_real_start(text)
print(f"\nFinal chosen start index: {start_idx}")
print("Slices text from here:")
print(text[start_idx:start_idx+1000])
