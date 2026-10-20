"""Regenerate selloffFixture.json + selloffInternalsFixture.json + selloffBookFixture.json from the REAL
Selloff Monitor engines (Phases 1–3) on live market data. The book is a SAMPLE book (real tickers, live
spots, illustrative strikes/IVs) — the preview never touches a real account.

    backend/venv/bin/python frontend/scripts/beta-tests/preview/gen_selloff_fixture.py
"""
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "../../../../backend")))

import yfinance as yf  # noqa: E402

from app.services.dealer_positioning_service import compute_dealer_positioning  # noqa: E402
from app.services.selloff_monitor_service import get_selloff_monitor  # noqa: E402
from app.services.market_internals_service import fetch_constituents, get_market_internals  # noqa: E402

dealer = compute_dealer_positioning(yf.Ticker("SPY"))
payload = asyncio.run(get_selloff_monitor(dealer))
with open(os.path.join(HERE, "selloffFixture.json"), "w") as f:
    json.dump(payload, f, allow_nan=False)
print("wrote selloffFixture.json —", payload.get("as_of"), "available:", payload.get("available"))

internals = asyncio.run(get_market_internals(asyncio.run(fetch_constituents())))
with open(os.path.join(HERE, "selloffInternalsFixture.json"), "w") as f:
    json.dump(internals, f, allow_nan=False)
print("wrote selloffInternalsFixture.json —", internals.get("as_of"), "available:", internals.get("available"))

# ---- Phase 3: a SAMPLE book run through the real scenario engine ----
# net_* greeks are the OPTION layer only (as in book_tail_risk._position_greeks); covered shares ride in cov_shares.
import datetime as _dt
from app.services.selloff_book_service import book_scenarios, book_read
from app.services.zebra_service import _bs_greeks

SAMPLE = [  # (trade_id, name, ticker, purpose, [(right, strike_mult, sign, qty, iv, dte)], shares, beta)
    (1, "NVDA short put", "NVDA", "income", [("P", 0.95, -1, 4, 0.48, 33)], 0, 1.9),
    (2, "AAPL covered call", "AAPL", "income", [("C", 1.05, -1, 3, 0.26, 33)], 300, 1.1),
    (3, "MSFT short strangle", "MSFT", "income", [("P", 0.92, -1, 1, 0.27, 26), ("C", 1.07, -1, 1, 0.25, 26)], 0, 1.05),
    (4, "SPY put spread hedge", "SPY", "hedge", [("P", 0.97, 1, 1, 0.17, 33), ("P", 0.90, -1, 1, 0.22, 33)], 0, 1.0),
    (5, "IWM short put", "IWM", "income", [("P", 0.94, -1, 3, 0.24, 19)], 0, 1.2),
]
spots = {}
for t in {x[2] for x in SAMPLE}:
    spots[t] = float(yf.Ticker(t).history(period="5d")["Close"].iloc[-1])
r = 0.04
positions = []
for tid, name, tk, purpose, legs, shares, beta in SAMPLE:
    S = spots[tk]
    lg_out, nd, ng, nv, nt = [], 0.0, 0.0, 0.0, 0.0
    for right, km, sign, qty, iv, dte in legs:
        K = round(S * km)
        g = _bs_greeks(S, K, dte / 365, iv, right == "C", r)
        nd += sign * qty * 100 * g["delta"]; ng += sign * qty * 100 * g["gamma"]
        nv += sign * qty * 100 * g["vega"]; nt += sign * qty * 100 * g["theta"]
        lg_out.append({"right": right, "strike": float(K), "sign": sign, "qty": qty, "iv": iv, "dte_years": dte / 365,
                       "exp": (_dt.date.today() + _dt.timedelta(days=dte)).isoformat()})
    p = {"ticker": tk, "name": name, "trade_id": tid, "spot": S, "iv": legs[0][4], "beta": beta, "purpose": purpose,
         "capital": sum(l["strike"] * 100 * l["qty"] for l in lg_out if l["sign"] < 0), "shares": float(shares),
         "legs": lg_out, "net_delta": nd, "net_gamma": ng, "net_vega": nv, "net_theta": nt}
    if shares:
        p.update(cov_shares=float(shares), structure="covered_call", has_stock=True)
    positions.append(p)
book = book_scenarios(positions, r, payload)
book.update(coverage={"requested": len(SAMPLE), "analysed": len(positions), "skipped": []}, quote_source="yfinance",
            monitor_session=payload.get("last_session"), as_of=_dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            indicative=False)
book["read"] = book_read(book)
with open(os.path.join(HERE, "selloffBookFixture.json"), "w") as f:
    json.dump(book, f, allow_nan=False)
print("wrote selloffBookFixture.json —", book.get("available"), [l[:90] for l in book["read"]])

# ---- sample dealer note (the REAL dealer_context wording) for the ?dealer preview variant ----
from app.services.selloff_monitor_service import dealer_context
_spot = payload["state"]["spy_close"]
with open(os.path.join(HERE, "selloffDealerNoteFixture.json"), "w") as f:
    json.dump(dealer_context({"net_gex": {"sign": "short"}, "gamma_flip": {"level": round(_spot * 1.015, 2)}}, _spot), f)
print("wrote selloffDealerNoteFixture.json (sample dealer state)")

# ---- the research report exactly as the API serves it (/selloff-monitor/evidence/report) ----
from app.services.selloff_evidence import REPORT_TITLE, report_markdown
_md = report_markdown()
with open(os.path.join(HERE, "selloffReportFixture.json"), "w") as f:
    json.dump({"title": REPORT_TITLE, "markdown": _md, "words": len(_md.split())}, f)
print("wrote selloffReportFixture.json")
