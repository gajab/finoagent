import re

# Load the saved dossier
with open("scratch/mck_dossier.txt", "r") as f:
    content = f.read()

# Let's see the structure of the MD&A sections in the file
sections = content.split("=== SEC FILINGS")[1] if "=== SEC FILINGS" in content else content

print("Length of filings section:", len(sections))

# Find sub-headers commonly used in MD&A
targets = [
    r"executive\s+summary",
    r"results\s+of\s+operations",
    r"overview\s+of\s+consolidated\s+results",
    r"overview\s+of\s+segment\s+results",
    r"business\s+overview"
]

for target in targets:
    matches = list(re.finditer(target, sections, re.IGNORECASE))
    print(f"Target '{target}': {len(matches)} matches")
    for i, m in enumerate(matches):
        print(f"  Match {i+1} at index {m.start()}: '{sections[m.start():m.start()+100]}'")
