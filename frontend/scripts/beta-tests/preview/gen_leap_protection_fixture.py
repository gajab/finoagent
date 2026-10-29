"""Regenerate leapProtectionFixture.json — REAL LEAP Protection payloads (run_leap_protection on live chains) for the preview,
one per proceeds destination the preview can switch between (index, bond).

    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_leap_protection_fixture.py [TICKER] [BASIS] [INDEX]
"""
import asyncio, json, logging, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../../../backend")))
logging.disable(logging.CRITICAL)
from app.services.leap_protection_service import run_leap_protection  # noqa: E402

t = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
basis = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0
idx = sys.argv[3] if len(sys.argv) > 3 else "SPY"
out = {}
for dest in ("index", "bond"):
    out[dest] = asyncio.run(run_leap_protection(ticker=t, shares=1000, cost_basis=basis, index=idx, horizon_years=2,
                                                tax={"lt_rate_pct": 23.8, "st_rate_pct": 40.8}, proceeds_to=dest,
                                                bond_rate_pct=4.5))
with open(os.path.join(HERE, "leapProtectionFixture.json"), "w") as f:
    json.dump(out, f, allow_nan=False, default=str, separators=(",", ":"))
for dest, res in out.items():
    best = next((x for x in res.get("strategies") or [] if x["id"] == res.get("best_id")), {})
    print(f"{dest}: {len(res.get('strategies') or [])} structures; best: {best.get('name')}")
