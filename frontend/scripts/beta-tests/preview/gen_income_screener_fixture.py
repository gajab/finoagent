"""Regenerate incomeScreenerFixture.json from the REAL Income Screener stages on live market data — an
earnings-window screen (within 2 trading sessions), the vol/event-expiry metrics for every row, and
sell-the-print evaluations for a few names. No DB: caching is skipped.

    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_income_screener_fixture.py
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../../../backend")))

from app.services import income_screener_service as isc  # noqa: E402

screen_p = {"earnings_window": "within_2", "price_min": 20, "price_max": 500, "market_cap_min_b": 2,
            "avg_volume_min": 1_000_000, "max_results": 50, "sectors": []}
screen = asyncio.run(isc.screen_universe(screen_p))
rows = screen["rows"]
items = [{"ticker": r["ticker"], "price": r["current_price"], "next_earnings": r["next_earnings"],
          "earnings_timing": r["earnings_timing"]} for r in rows]
vol = []
for i in range(0, len(items), isc.MAX_VOL_BATCH):
    vol += asyncio.run(isc.vol_metrics(items[i:i + isc.MAX_VOL_BATCH]))
by_t = {r["ticker"]: r for r in rows}
picks = sorted((v for v in vol if v.get("event_move_pct")), key=lambda v: -v["event_move_pct"])[:4]
evals = {}
for v in picks:
    r = by_t[v["ticker"]]
    p = {"min_prob": 0.85, "min_income": 20, "min_dte": 14, "max_dte": 45, "earnings": "play",
         "earnings_aware": True, "grades": ["A", "B"], "quote_source": "yfinance",
         "next_earnings": r["next_earnings"], "earnings_timing": r["earnings_timing"]}
    evals[v["ticker"]] = asyncio.run(isc.evaluate_ticker(v["ticker"], p))
with open(os.path.join(HERE, "incomeScreenerFixture.json"), "w") as f:
    json.dump({"screen": screen, "vol": vol, "evals": evals}, f, allow_nan=False)
print("wrote incomeScreenerFixture.json —", len(rows), "rows,", len(vol), "vol,", list(evals))
