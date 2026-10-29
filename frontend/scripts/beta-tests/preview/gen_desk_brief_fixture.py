"""Regenerate deskBriefFixture.json — a REAL single-ticker desk payload (rank_desk on live data) for the
"Before you trade" technical-brief preview.

    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_desk_brief_fixture.py [TICKER] [EXPIRY]
"""
import asyncio, json, logging, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../../../backend")))
logging.disable(logging.CRITICAL)
from app.services.desk_review_service import rank_desk  # noqa: E402

t = sys.argv[1] if len(sys.argv) > 1 else "VLO"
exp = sys.argv[2] if len(sys.argv) > 2 else None
res = asyncio.run(rank_desk(t, min_prob=0.85, min_income=20, structures=["covered_call", "cash_secured_put"],
                            target_expiration=exp, owns_underlying=False, earnings_aware=True))
with open(os.path.join(HERE, "deskBriefFixture.json"), "w") as f:
    json.dump(res, f, allow_nan=False, default=str)
print("wrote deskBriefFixture.json —", t, len(res.get("ranked") or []), "trades,", len((res.get("ta_brief") or {}).get("items") or []), "brief items")
