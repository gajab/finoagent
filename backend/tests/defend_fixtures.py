"""Shared fixtures for the Defend-desk tests: the live AMD trade that exposed the P(touch) bug and the
split between the desk recommendation and the roll optimizer (short 1× $700 call sold for $0.90, ~22 DTE,
spot ≈ $629.5, IV 54% vs HV 67%, +723%/yr momentum). Candidates are built in EXACTLY the schema
`roll_optimizer_service.optimize_roll` returns, priced with the engine's own Black-Scholes so they are
internally consistent (credit = new price − buy-back mark)."""
import math

from app.services import trade_repair_service as tr
from app.services.stock_service import bs_price

SPOT, K0, DTE, IV, ENTRY, R = 629.5, 700.0, 22, 0.542, 0.90, 0.045
LEG = {"strike": K0, "right": "C", "sign": -1, "qty": 1, "entry": ENTRY}

# the enrichment the router merges into `recoverability` before ranking (P(touch)/VRP/trend as on screen)
MARGINAL = dict(posture="marginal", p_touch=44.7, vrp_pct=-19.4, trend_pct=723.4, iv_pct=54.2, hv_pct=67.2)


def amd_menu(**rec_overrides) -> dict:
    """The phase-1 payload: the fixed menu + the risk read, ranked provisionally."""
    menu = tr.repair_alternatives(legs=[dict(LEG)], spot=SPOT, dte_days=DTE, r=R, atm_iv=IV)
    menu["structure"] = "short_call"
    menu["recoverability"].update({**MARGINAL, **rec_overrides})
    return menu


def buyback_mark() -> float:
    return bs_price(SPOT, K0, DTE / 365, R, IV, "call")


def amd_candidates(strikes=(700.0, 720.0, 740.0), expiries=(("2026-10-23", 28), ("2026-10-30", 35), ("2026-11-20", 57))):
    """Net-credit roll candidates in the optimizer's output schema, best-first by the optimizer's own composite."""
    mark = buyback_mark()
    out = []
    for exp, dte in expiries:
        for K in strikes:
            px = bs_price(SPOT, K, dte / 365, R, IV, "call")
            net = round((px - mark) * 100)
            if net < 0:
                continue
            T = dte / 365
            sig = IV * math.sqrt(T)
            d2 = (math.log(SPOT / K) + (R - 0.5 * IV * IV) * T) / sig
            p_otm = 0.5 * (1 + math.erf(-d2 / math.sqrt(2)))
            tf = max(0.45, min(1.0, 1.0 - (dte - 30) / 140.0))
            cush = abs(math.log(K / SPOT)) / sig
            sc = dict(probability=p_otm * 100, structure=100 * tf, credit=min(100, net / 700 * 100),
                      cushion=min(100, cush / 1.5 * 100), cushion_sigma=cush, time_factor=tf)
            sc["composite"] = round(.4 * sc["probability"] + .3 * sc["structure"] + .15 * sc["credit"] + .15 * sc["cushion"], 1)
            out.append(dict(
                expiry=exp, dte=dte, strike=K, right="C", new_price=round(px, 4), iv=IV, roll_net_cash=net,
                credit_per_share=round(px - mark, 2), new_credit_total=ENTRY + net / 100,
                new_breakeven=round(K + ENTRY + (px - mark), 2), new_capital=70000,
                p_otm=round(p_otm * 100, 1), p_otm_source="RND", spans_earnings=(exp >= "2026-11-03"),
                structure={"cleared_count": 6, "levels_available": 6, "clears 15d resistance": True}, scores=sc,
                why=f"Roll to the ${K:.0f} call exp {exp} for +${net:,.0f}"))
    out.sort(key=lambda c: -c["scores"]["composite"])
    return out[:6]


def merged_amd_menu() -> dict:
    """Phase 1 + phase 2 exactly as the /defend/refine endpoint does it."""
    menu = amd_menu()
    tr.rank_defenses(menu)                                          # provisional (phase-1) ranking
    built = tr.build_roll_alternatives(
        candidates=amd_candidates(), tested={"right": "C", "strike": K0, "qty": 1, "entry": ENTRY},
        tested_mark=buyback_mark(), hold=menu["hold"], spot=SPOT, r=R, stock=None, iv_default=IV)
    tr.drop_plain_rolls(menu)
    menu["alternatives"] += built
    tr.rank_defenses(menu)
    return menu
