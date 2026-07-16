from app.services.hedging_service import _build_tailored, _split_chain
from app.services.quote_providers.base import OptionQuote, OptionChain

spot, exp, dte = 700.0, "2026-09-18", 90
quotes = []
for k in range(280, 1121, 5):
    k = float(k)
    cm = max(spot - k, 0) + 3.0
    pm = max(k - spot, 0) + 3.0
    quotes.append(OptionQuote(k, "C", exp, cm - .1, cm + .1, cm, cm, iv=.4, oi=100, volume=50))
    quotes.append(OptionQuote(k, "P", exp, pm - .1, pm + .1, pm, pm, iv=.4, oi=100, volume=50))
chain = OptionChain(symbol="T", expiration=exp, underlying_price=spot, quotes=quotes)
calls, puts = _split_chain(chain)
sp, sc = sorted(puts), sorted(calls)

# User's exact scenario: Floor=0, Cap(down)=20, Upside=25, Give-up=0
h = _build_tailored(calls, puts, sp, sc, 100, spot, dte, exp, 70000.0,
                    down=0, up=25, buf=0, giveup=0, contracts=1, max_cost_pct=2.0, down_cap=20)
print("PLAIN:", h["plain"] if h else None)
for l in (h["legs"] if h else []):
    print(f"  {l['action']:4} {l['type']} {l['strike']:.0f}")
