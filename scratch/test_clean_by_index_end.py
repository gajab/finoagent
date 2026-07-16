import re

def clean_boilerplate_by_index_end(text: str) -> str:
    # Check if there is an index/TOC at the beginning of the MD&A block
    has_index = False
    header_snippet = text[:800]
    if re.search(r"index\s+to\s+management", header_snippet, re.IGNORECASE) or re.search(r"table\s+of\s+contents", header_snippet, re.IGNORECASE):
        has_index = True
        
    if not has_index:
        return text

    # Search for all "Title [spaces] PageNumber" patterns in the first 1500 characters
    # e.g., "Executive Summary 35", "New Accounting Pronouncements 56"
    pattern = r"\b[a-zA-Z\s\&\,\’\-\(\)\/]{3,50}\s+\d{1,3}\b"
    matches = list(re.finditer(pattern, text[:1500]))
    
    if matches:
        # The last match is the end of the table of contents
        last_match = matches[-1]
        end_pos = last_match.end()
        print(f"Index ends at pos {end_pos} with match '{last_match.group(0)}'")
        
        # Let's slice the text to start right after the index
        sliced = text[end_pos:]
        sliced = re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        return sliced
        
    return text

with open("scratch/mck_mda_full.txt", "r") as f:
    mck_text = f.read()

mck_cleaned = clean_boilerplate_by_index_end(mck_text)
print("MCK Cleaned Start (By Index End):")
print(mck_cleaned[:1000])
print("-" * 50)
print(f"Original length: {len(mck_text)}, Cleaned length: {len(mck_cleaned)} (Saved {len(mck_text) - len(mck_cleaned)} chars)")
