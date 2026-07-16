import re

with open("frontend/src/components/ExitTab.tsx", "r") as f:
    content = f.read()

# Remove PositionSummary import
content = content.replace("  PositionSummary, ExitSignalGauge, TechnicalSignals, PillarCard,\n", "  ExitSignalGauge, TechnicalSignals, PillarCard,\n")

# Remove PositionSummary render
content = re.sub(
    r'<div className="lg:col-span-3">\s*<PositionSummary position=\{data\.position\} ticker=\{ticker\} />\s*</div>',
    '',
    content
)

# Remove data.position references from types
with open("frontend/src/types.ts", "r") as f:
    types_content = f.read()
types_content = re.sub(r'  position: \{.*?\};\n', '', types_content, flags=re.DOTALL)
with open("frontend/src/types.ts", "w") as f:
    f.write(types_content)

with open("frontend/src/components/ExitTab.tsx", "w") as f:
    f.write(content)

