import re

def clean_boilerplate_smart(text: str) -> str:
    if not text:
        return ""
        
    # Check if there is an index/TOC at the beginning of the MD&A block
    has_index = False
    header_snippet = text[:1000]
    if re.search(r"index\s+to\s+management", header_snippet, re.IGNORECASE) or re.search(r"table\s+of\s+contents", header_snippet, re.IGNORECASE):
        has_index = True
        
    if not has_index:
        return text
        
    # We want to find the first real section header to start from.
    # Common headers: Overview, Executive Summary, Results of Operations, General
    targets = [
        r"overview\s+of\s+(our\s+)?business",
        r"executive\s+summary",
        r"\bgeneral\b",
        r"results\s+of\s+operations",
    ]
    
    candidates = []
    for target in targets:
        for m in re.finditer(target, text, re.IGNORECASE):
            idx = m.start()
            if idx > 400: # TOC items are concentrated at the very beginning
                # Verify it is not a TOC match by checking if the next 30 chars have a page number
                snippet = text[idx + len(m.group(0)): idx + len(m.group(0)) + 30]
                if not re.match(r"^[\s\.\-\_]*\d+\b", snippet):
                    candidates.append(idx)
                    break # Take the first non-TOC match for this pattern
                    
    if candidates:
        chosen_start = min(candidates)
        sliced = text[chosen_start:]
        sliced = re.sub(r"^[\s\.\-\:\,\—\–]+", "", sliced)
        return sliced
        
    return text

# Let's test on the four tickers
for ticker in ["AAPL", "MSFT", "NVDA", "MCK"]:
    print(f"\n--- Testing {ticker} ---")
    filename = f"scratch/{ticker.lower()}_mda_full.txt"
    # For tickers other than MCK, let's create their scratch files if they don't exist
    import os
    if not os.path.exists(filename):
        # We can just fetch them or use a mock start
        if ticker == "MCK":
            pass
        else:
            # Create a mock text for AAPL/MSFT/NVDA
            print(f"Creating mock text for {ticker}...")
            with open(filename, "w") as f:
                f.write(f"Item 7. Management's Discussion and Analysis. This is the real discussion for {ticker} results of operations...")
                
    with open(filename, "r") as f:
        text = f.read()
        
    cleaned = clean_boilerplate_smart(text)
    print(f"Original length: {len(text)}, Cleaned length: {len(cleaned)}")
    print(f"Start: '{cleaned[:150]}...'")
