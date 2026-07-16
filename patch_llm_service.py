import re

with open("backend/app/services/exit_strategy_service.py", "r") as f:
    content = f.read()

# Change function signature
content = content.replace('def analyze_exit_strategy(\n    ticker: str,\n    persona: str,\n    portfolio_summary: list[dict],\n    openai_key: str,\n    model: str = "gpt-4o"\n) -> dict:', 'def analyze_exit_strategy(\n    ticker: str,\n    persona: str,\n    openai_key: str,\n    model: str = "gpt-4o"\n) -> dict:')

# Remove portfolio compression logic
content = re.sub(r'    # Compress portfolio into a readable string\n    portfolio_context = "\\n"\.join\(\[\n        f"- \{p\[\'ticker\'\]\}: \{p\[\'shares\'\]\} shares @ \$\{p\[\'cost_basis\'\]\} avg" \n        for p in portfolio_summary\n    \]\)', '', content)

# Remove USER'S CURRENT PORTFOLIO CONTEXT from prompt
content = content.replace("USER'S CURRENT PORTFOLIO CONTEXT:\n{portfolio_context}\n", "")

# Remove reference to user's overall portfolio exposure
content = content.replace("and the user's overall portfolio exposure", "")

with open("backend/app/services/exit_strategy_service.py", "w") as f:
    f.write(content)
