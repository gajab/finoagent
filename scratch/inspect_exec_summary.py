with open("scratch/mck_dossier.txt", "r") as f:
    content = f.read()

# Let's inspect index ranges in the 10-K section of filings
# The 10-K section is between '[10-K filed' and '[10-Q filed'
k_start = content.find("[10-K filed")
q_start = content.find("[10-Q filed")
k_section = content[k_start:q_start]

print("=== 10-K SECTION (Length: {}) ===".format(len(k_section)))
# Find all occurrences of "Executive Summary" in the 10-K section
import re
for m in re.finditer(r"executive\s+summary", k_section, re.IGNORECASE):
    idx = m.start()
    print(f"Match at {idx}:")
    print(k_section[idx-100:idx+500])
    print("-" * 50)
