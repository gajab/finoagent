"""Regenerates defendFixture.json — the REAL backend Defend flow (GET /repair-menu then POST /defend/refine, including the live
spread-roll search) run against a synthetic Black-Scholes AAOI-style option chain with an earnings bump (backend/tests/spread_world.py:
a put credit spread 80/70, spot $81, clock pinned to 2026-10-15). Synthetic data only; run from the repo root:
    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_defend_fixture.py
Cases: after = print AFTER this expiry (Nov 20 75/65 even-cost roll exists) · inside = print INSIDE the trade (no even-cost roll) ·
       healthy = nothing to defend · naked = a naked short call (no income-viable fix)."""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "backend", "tests"))
from _pytest.monkeypatch import MonkeyPatch   # noqa: E402
import spread_world as W                       # noqa: E402

CASES = {
    "after": dict(earn_days=28, cur_dte=8),
    "inside": dict(earn_days=21, cur_dte=22),
    "healthy": dict(earn_days=28, cur_dte=8, posture="healthy", p_touch=8.0, vrp=5.0, trend=3.0),
    "naked": dict(earn_days=28, cur_dte=8, legs=[("call", "SELL", 85)], entry=(1.8,), p_touch=55.0, vrp=-19.4, trend=60.0),
}
out = {}
for name, kw in CASES.items():
    mp = MonkeyPatch()
    try:
        p1, final = W.run_flow(mp, **kw)
    finally:
        mp.undo()
    # phase 1 is only kept where the preview shows the "building the recommendation" state; elsewhere it IS the final payload
    out[name] = {"final": json.loads(json.dumps(final))}
    if name == "after":
        out[name]["phase1"] = p1
    d = final["desk_recommendation"]
    print(f"{name:8} pick = {d['name']}  [{d['desk_score']}]  policy={d.get('policy')}  timing={final['timing']['verdict']}")
dst = os.path.join(os.path.dirname(__file__), "defendFixture.json")
json.dump(out, open(dst, "w"), separators=(",", ":"))
print("wrote", dst, os.path.getsize(dst), "bytes")
