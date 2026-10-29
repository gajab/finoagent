"""DEFEND PLAYBOOK — the leg-by-leg desk read a trapped short-premium position needs before any move is ranked.

The Defend menu ranks whole-position moves. A desk asks four things first, and this module answers them from the live
chain (pure: chain books in, numbers out — nothing is invented):

  · ANATOMY — what each leg is doing for you right now: time value left, theta, vega, the odds. An in-the-money put
    spread is the classic trap: the long, sitting at the money, decays faster than the deep short has left to earn,
    so the spread has NEGATIVE theta (waiting costs money at a flat price) and is LONG vega (a calmer market makes it
    dearer to close or roll — today's high IV is the cheaper moment to act). Plus the expiry mechanics a spread holder
    gets wrong: the pin zone between the strikes (assigned on the short, the long expires — unhedged shares over the
    weekend) and early assignment on a short with no time value left.
  · THE PRINT — the earnings move the market prices, from the STRADDLE vols of the expiries either side of it (the
    provider's IV field mixes stale-spot artefacts; one vol point of mismatch moves the implied event ≈ 3% of spot).
  · THE ASSIGNMENT LADDER — expiry by expiry, how low the assignment price can go if you roll for NO new money:
    as a cash-secured put (the lowest strike whose premium pays for closing what you hold) or as a wider defined-risk
    spread. Time and the event premium are what buy the lower strike.
  · WAITING — what rolling later costs versus now at the same price, with the event variance held FIXED: the quoted
    IV of a post-print expiry rises into the print (the same event variance squeezed into fewer days) but the dollars
    you collect do not — the event part stays, the ordinary time value decays.

Books are the router's chain dicts: {strike: {"P": {"mid", "iv", "bid", "ask"}, "C": {...}}}."""
from __future__ import annotations

import math
from typing import Optional

from .stock_service import bs_delta, bs_gamma, bs_price, bs_theta, bs_vega

_MULT = 100.0


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _T(days: float) -> float:
    return max(float(days), 0.5) / 365.0


def _kind(right: str) -> str:
    return "put" if right == "P" else "call"


def _q(book: Optional[dict], strike: float, right: str) -> dict:
    return ((book or {}).get(float(strike)) or {}).get(right) or {}


def _two_sided(q: dict) -> bool:
    b, a = q.get("bid"), q.get("ask")
    return bool(b is not None and a is not None and b > 0 and a >= b)


def _mid(q: dict) -> Optional[float]:
    m = q.get("mid")
    if m and m > 0:
        return float(m)
    if _two_sided(q):
        return 0.5 * (float(q["bid"]) + float(q["ask"]))
    return None


def _usd(x: float, signed: bool = False) -> str:
    s = f"${abs(x):,.0f}"
    if signed:
        return ("+" if x >= 0 else "−") + s
    return ("−" + s) if x < 0 else s


def _px(x: float) -> str:
    return f"${x:,.2f}"


def _k(x: float) -> str:
    return f"${x:,.0f}" if abs(x - round(x)) < 1e-6 else f"${x:,.2f}"


def _date(iso) -> str:
    """'2026-11-05' → 'Nov 5' (the way every other line on the panel writes a date)."""
    import datetime as _d
    try:
        d = _d.date.fromisoformat(str(iso)[:10])
        return f"{d.strftime('%b')} {d.day}"
    except (ValueError, TypeError):
        return str(iso or "")


def implied_vol(price: float, S: float, K: float, T: float, r: float, right: str) -> Optional[float]:
    """Black-Scholes implied vol of one quote by bisection. None when the price sits at/below the no-arbitrage floor
    (no time value to invert) or above the 500%-vol ceiling (a garbage quote)."""
    if not price or price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    floor = max(0.0, K * math.exp(-r * T) - S) if right == "P" else max(0.0, S - K * math.exp(-r * T))
    if price <= floor + 1e-4:
        return None
    kind = _kind(right)
    lo, hi = 0.01, 5.0
    if bs_price(S, K, T, r, lo, kind) > price or bs_price(S, K, T, r, hi, kind) < price:
        return None
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if bs_price(S, K, T, r, mid, kind) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def implied_spot(book: Optional[dict], spot: float, dte: float, r: float) -> Optional[float]:
    """The underlying price the option quotes are actually priced off, by put-call parity at the strikes nearest spot
    (S = C − P + K·e^(−rT), median of three two-sided pairs). A chain snapshot routinely lags the last trade on a fast
    day (AAOI 2026-10-22: quotes priced off ≈$69.0 with the stock last at $68.26), and an in-the-money option is nearly
    all intrinsic — splitting it into intrinsic + time value against the wrong price reads a $1 extrinsic as $0.21
    and fires a false early-assignment alarm. Near-the-money American early-exercise premium is negligible. None when
    no two-sided pair exists or the implied price is more than 3% from spot (then the chain is not trustworthy)."""
    if not book or not spot or spot <= 0:
        return None
    T = _T(dte)
    ks = sorted((k for k in book if _two_sided(_q(book, k, "C")) and _two_sided(_q(book, k, "P"))),
                key=lambda k: abs(k - spot))[:3]
    est = sorted(_mid(_q(book, k, "C")) - _mid(_q(book, k, "P")) + k * math.exp(-r * T) for k in ks)
    if not est:
        return None
    s = est[len(est) // 2]
    return float(s) if abs(s / spot - 1.0) <= 0.03 else None


def straddle_vol(book: Optional[dict], spot: float, dte: float, r: float) -> Optional[float]:
    """The ATM vol the STRADDLE MID implies — exact BS inversion, averaged over the two nearest two-sided strikes.
    The same estimator on every expiry, so the term structure (and the event split off it) is never a mix of estimators."""
    if not book or not spot or spot <= 0:
        return None
    T = _T(dte)
    ks = sorted((k for k in book if _two_sided(_q(book, k, "C")) and _two_sided(_q(book, k, "P"))),
                key=lambda k: abs(k - spot))[:2]
    out = []
    for k in ks:
        st = _mid(_q(book, k, "C")) + _mid(_q(book, k, "P"))

        def f(v, k=k, st=st):
            return bs_price(spot, k, T, r, v, "call") + bs_price(spot, k, T, r, v, "put") - st
        lo, hi = 0.01, 5.0
        if f(lo) > 0 or f(hi) < 0:
            continue
        for _ in range(60):
            m = 0.5 * (lo + hi)
            lo, hi = (lo, m) if f(m) > 0 else (m, hi)
        out.append(0.5 * (lo + hi))
    return sum(out) / len(out) if out else None


def leg_iv(book: Optional[dict], strike: float, right: str, S: float, T: float, r: float,
           fallback: Optional[float]) -> float:
    """The vol to use for ONE strike: invert the OUT-OF-THE-MONEY side's quote at that strike (a put below spot, a call
    above). An in-the-money option is mostly intrinsic, so its own quote barely constrains the vol (the 80 put at $68:
    a few cents of time value against a dollar-wide market); by put-call parity the OTM side carries the same vol."""
    otm = "P" if strike <= S else "C"
    for rt in (otm, ("C" if otm == "P" else "P")):
        m = _mid(_q(book, strike, rt))
        v = implied_vol(m, S, strike, T, r, rt) if m else None
        if v:
            return v
    v = _q(book, strike, otm).get("iv") or _q(book, strike, right).get("iv")
    if v and v > 0:
        return float(v)
    return float(fallback or 0.5)


def _p_below(S: float, K: float, T: float, r: float, iv: float) -> float:
    """Risk-neutral P(S_T < K) under a lognormal at the strike's own vol."""
    sig = max(iv, 0.02) * math.sqrt(T)
    d2 = (math.log(S / K) + (r - 0.5 * max(iv, 0.02) ** 2) * T) / sig
    return _ncdf(-d2)


# ── THE PRINT ─────────────────────────────────────────────────────────────────────────────────────────────────────
def term_structure(books: list, spot: float, r: float, earn_days: Optional[float]) -> dict:
    """ATM straddle vol per expiry + the market-implied earnings move.

    `books` = [(expiry_iso, dte, book)]. The event is isolated from the LAST expiry before the print and the FIRST one
    after it — the tightest pair: σ_event² = σ_post²·T_post − σ_pre²·T_post (the pre-print vol taken as the base rate
    over the post expiry's life). A far month dilutes the event and drags in the base vol's own term slope (AAOI
    2026-10: Oct 30 vs Dec 18 read a ±3% event; Oct 30 vs Nov 6 — the pair that straddles the Nov 5 print — reads
    ±16%). Returns {expiries:[{expiry, dte, atm_iv_pct, spans_earnings}], event_move_pct, event_var, pre, post}."""
    rows = []
    for exp, dte, book in sorted(books or [], key=lambda x: x[1]):
        S = implied_spot(book, spot, dte, r) or spot
        v = straddle_vol(book, S, dte, r)
        rows.append({"expiry": exp, "dte": int(dte), "atm_iv_pct": (round(v * 100, 1) if v else None),
                     "spans_earnings": bool(earn_days is not None and dte >= earn_days), "_v": v})
    out = {"expiries": [{k: v for k, v in r_.items() if k != "_v"} for r_ in rows],
           "event_move_pct": None, "event_var": None, "pre": None, "post": None}
    if earn_days is None:
        return out
    pre = [x for x in rows if x["dte"] < earn_days and x["_v"]]
    post = [x for x in rows if x["dte"] >= earn_days and x["_v"]]
    if not pre or not post:
        return out
    a, b = pre[-1], post[0]
    Tb = _T(b["dte"])
    var = b["_v"] ** 2 * Tb - a["_v"] ** 2 * Tb
    if var > 0:
        out.update({"event_move_pct": round(min(60.0, math.sqrt(var) * 100.0), 1), "event_var": var,
                    "pre": a["expiry"], "post": b["expiry"]})
    return out


# ── ANATOMY ───────────────────────────────────────────────────────────────────────────────────────────────────────
def _vertical(legs: list[dict]) -> Optional[tuple]:
    """(short, long) when the held options are exactly one short + one same-size, same-right protective long."""
    if len(legs) != 2:
        return None
    s = next((l for l in legs if l["sign"] < 0), None)
    lg = next((l for l in legs if l["sign"] > 0), None)
    if not s or not lg or s["right"] != lg["right"] or int(s["qty"]) != int(lg["qty"]):
        return None
    protective = lg["strike"] < s["strike"] if s["right"] == "P" else lg["strike"] > s["strike"]
    return (s, lg) if protective else None


def position_anatomy(*, held: dict, spot: float, dte: float, r: float, book: Optional[dict], ticker: str = "",
                     event: Optional[dict] = None, ex_div: Optional[dict] = None) -> Optional[dict]:
    """What each held leg is doing for you NOW, the position's net greeks in dollars, and — for a vertical — the
    expiry zones, the pin and early-assignment mechanics, and what each leg can be done with on its own.

    Greeks are from YOUR side of the trade, in dollars per contract set: theta $/day (+ = time pays you), vega $ per
    vol point (+ = a rise in IV helps you), delta in share-equivalents. Prices: the chain mid; time value is split
    against the option-implied underlying (see `implied_spot`)."""
    legs_in = [l for l in (held or {}).get("legs") or [] if l.get("right") in ("P", "C")]
    if not legs_in or not spot or spot <= 0:
        return None
    T = _T(dte)
    S_impl = implied_spot(book, spot, dte, r)
    S = S_impl or spot
    atm = straddle_vol(book, S, dte, r)
    tk = ticker or "the stock"

    legs = []
    for l in legs_in:
        K, rt, sg, n = float(l["strike"]), l["right"], int(l["sign"]), int(l.get("qty") or 1)
        q = _q(book, K, rt)
        mid = _mid(q) or float(l.get("mark") or 0.0)
        intr = max(0.0, K - S) if rt == "P" else max(0.0, S - K)
        ext = max(0.0, mid - intr)
        iv = leg_iv(book, K, rt, S, T, r, atm or q.get("iv"))
        kind = _kind(rt)
        m = sg * n * _MULT
        p_itm = _p_below(S, K, T, r, iv) if rt == "P" else 1.0 - _p_below(S, K, T, r, iv)
        legs.append({
            "label": f"{'Short' if sg < 0 else 'Long'} {_k(K)} {'put' if rt == 'P' else 'call'}",
            "right": rt, "strike": K, "sign": sg, "qty": n, "expiry": l.get("expiry"),
            "bid": (round(float(q["bid"]), 2) if q.get("bid") is not None else None),
            "ask": (round(float(q["ask"]), 2) if q.get("ask") is not None else None),
            "mid": round(mid, 2), "entry": round(float(l.get("entry") or 0.0), 2),
            "intrinsic": round(intr, 2), "extrinsic": round(ext, 2), "iv_pct": round(iv * 100, 1),
            "delta": round(float(bs_delta(S, K, T, r, iv, kind)), 3),
            "delta_sh": round(float(m * bs_delta(S, K, T, r, iv, kind)), 1),
            "theta_day": round(float(m * bs_theta(S, K, T, r, iv, kind)), 2),
            "vega": round(float(m * bs_vega(S, K, T, r, iv)), 2),
            "gamma": round(float(m * bs_gamma(S, K, T, r, iv)), 3),
            "p_itm_pct": round(float(p_itm) * 100, 1),
            "moneyness": ("at the money" if abs(K - S) / S < 0.03 else "in the money" if intr > 0 else "out of the money"),
            "_iv": iv,
        })
    net = {"delta_sh": round(sum(l["delta_sh"] for l in legs), 1), "theta_day": round(sum(l["theta_day"] for l in legs), 2),
           "vega": round(sum(l["vega"] for l in legs), 2), "gamma": round(sum(l["gamma"] for l in legs), 3)}

    out = {"underlying": {"spot": round(spot, 2), "implied": (round(S_impl, 2) if S_impl else None),
                          "stale_quotes": bool(S_impl and abs(S_impl / spot - 1.0) > 0.005)},
           "dte": int(dte), "atm_iv_pct": (round(atm * 100, 1) if atm else None),
           "legs": legs, "net": net, "spread": None, "zones": [], "early_assignment": [], "pin": None,
           "leg_moves": [], "insights": []}
    ins = out["insights"]
    if out["underlying"]["stale_quotes"]:
        ins.append({"kind": "data", "tone": "info",
                    "text": f"The option quotes are priced off ≈{_px(S_impl)} (the stock last traded {_px(spot)}) — the chain "
                            "snapshot lags the tape, so time values below are split against the option-implied price."})

    # EARLY ASSIGNMENT — an American short in the money with no time value left: the holder gains nothing by keeping it.
    for l in legs:
        if l["sign"] > 0 or l["intrinsic"] <= 0:
            continue
        carry = l["strike"] * (1.0 - math.exp(-r * T)) if l["right"] == "P" else 0.0
        ext_bid = (l["bid"] - l["intrinsic"]) if l.get("bid") is not None else None
        div = float((ex_div or {}).get("amount") or 0.0) if l["right"] == "C" else 0.0
        ext_ask = (l["ask"] - l["intrinsic"]) if l.get("ask") is not None else None
        if l["right"] == "C" and div > 0 and l["extrinsic"] < div:
            level, why = "high", f"the {_px(div)} dividend is worth more than its {_px(l['extrinsic'])} of time value — calls are exercised the day before ex-div"
        elif l["extrinsic"] <= max(0.05, carry) or (ext_ask is not None and ext_ask <= 0):
            level, why = "high", (f"only {_px(l['extrinsic'])} of time value left (≤ the {_px(carry)} of interest the holder earns by exercising)"
                                  if l["right"] == "P" else f"only {_px(l['extrinsic'])} of time value left")
        elif ext_bid is not None and ext_bid <= 0:
            level, why = "possible", (f"its bid ({_px(l['bid'])}) is at or below its {_px(l['intrinsic'])} intrinsic value — a holder can't sell it "
                                      "for more than exercising pays")
        elif abs(l["delta"]) >= 0.85 and l["extrinsic"] <= carry + 0.35:
            level, why = "possible", f"deep in the money with just {_px(l['extrinsic'])} of time value"
        else:
            level, why = "low", f"still {_px(l['extrinsic'])} of time value — the holder does better selling it than exercising"
        out["early_assignment"].append({"leg": l["label"], "level": level, "extrinsic": l["extrinsic"],
                                        "carry": round(carry, 2), "why": why})

    # PARITY — a deep in-the-money option's own mid is often stale/wide; the other side of the same strike says what it is
    # worth (C − P = S − K·e^(−rT)). Buying back a leg that trades well above parity overpays: work the spread as ONE order.
    out["parity"] = []
    for l in legs:
        oth = _q(book, l["strike"], "C" if l["right"] == "P" else "P")
        om = _mid(oth)
        if not om or l["intrinsic"] <= 0:
            continue
        par = (om + l["strike"] * math.exp(-r * T) - S) if l["right"] == "P" else (om + S - l["strike"] * math.exp(-r * T))
        rich = l["mid"] - par
        if rich >= max(0.15, 0.02 * l["mid"]):
            out["parity"].append({"leg": l["label"], "mid": l["mid"], "parity": round(par, 2), "rich": round(rich, 2)})
            ins.append({"kind": "parity", "tone": "warn",
                        "text": (f"The {l['label'].lower()} is quoted {_px(l['mid'])} mid but its {_k(l['strike'])} "
                                 f"{'call' if l['right'] == 'P' else 'put'} says it is worth ≈{_px(par)} (put-call parity) — "
                                 f"{_usd(rich * _MULT * l['qty'])} of air. Never buy it back on its own at the leg price: close or roll "
                                 "the spread as ONE combo order with a limit, or let it be assigned.")})

    vert = _vertical(legs)
    ev = event or {}
    em = ev.get("implied_move_pct")
    earn_after = bool(ev.get("days") is not None and ev.get("position") == "after")
    earn_txt = (f" — into earnings {_date(ev.get('date'))}" + (f" (±{float(em):.0f}% implied)" if em else "")) if earn_after else ""

    if vert:
        s, lg = vert
        n = int(s["qty"])
        put = s["right"] == "P"
        W = abs(s["strike"] - lg["strike"])
        mark = s["mid"] - lg["mid"]
        nat = ((s["ask"] if s.get("ask") else s["mid"]) - (lg["bid"] if lg.get("bid") is not None else lg["mid"]))
        credit = float(held.get("collected") or 0.0) / (_MULT * n)
        mult = _MULT * n
        out["spread"] = {
            "width": W, "mark": round(mark, 2), "close_natural": round(nat, 2), "credit": round(credit, 2),
            "loss_now": round((credit - mark) * mult), "max_loss": round((credit - W) * mult), "max_gain": round(credit * mult),
            "more_to_lose": round(-(W - mark) * mult), "more_to_gain": round(mark * mult),
            "slippage": round((nat - mark) * mult), "pct_of_max_loss": round(100 * mark / W) if W else None,
        }
        sp = out["spread"]
        # ZONES at expiry. For a put spread: below the long = full loss; between = partial (and the pin); above the short = full credit.
        p_short_itm = s["p_itm_pct"] / 100.0
        p_long_itm = min(lg["p_itm_pct"] / 100.0, p_short_itm)
        hi_k, lo_k = (s["strike"], lg["strike"]) if put else (lg["strike"], s["strike"])
        p_full_loss, p_keep = p_long_itm, 1.0 - p_short_itm
        p_pin = max(0.0, 1.0 - p_full_loss - p_keep)
        out["zones"] = [
            {"key": "max_loss", "label": (f"Below {_k(lo_k)}" if put else f"Above {_k(hi_k)}"), "p_pct": round(p_full_loss * 100),
             "pnl": sp["max_loss"], "what": "both legs finish in the money — the full width is lost; the long caps it there"},
            {"key": "pin", "label": f"Between {_k(lo_k)} and {_k(hi_k)}", "p_pct": round(p_pin * 100),
             "pnl": None, "pnl_range": [sp["max_loss"], sp["max_gain"]],
             "what": (f"the {_k(s['strike'])} {'put' if put else 'call'} is assigned, the {_k(lg['strike'])} expires worthless — "
                      f"you {'own' if put else 'are short'} {100 * n} shares at {_k(s['strike'])} with NO protection")},
            {"key": "max_gain", "label": (f"Above {_k(hi_k)}" if put else f"Below {_k(lo_k)}"), "p_pct": round(p_keep * 100),
             "pnl": sp["max_gain"], "what": "both legs expire worthless — you keep the original credit"},
        ]
        out["pin"] = {"p_pct": round(p_pin * 100), "low": lo_k, "high": hi_k,
                      "text": (f"If {tk} closes between {_k(lo_k)} and {_k(hi_k)} at expiry (≈{p_pin * 100:.0f}% odds), the "
                               f"{_k(s['strike'])} {'put' if put else 'call'} is assigned but the {_k(lg['strike'])} expires worthless: "
                               f"you'd be {'long' if put else 'short'} {100 * n} shares at {_k(s['strike'])} with no protection over the "
                               f"weekend{earn_txt}. Close or roll before the closing bell on expiry day unless you want the shares.")}

        # THETA — who is earning the decay?
        th, th_l, th_s = net["theta_day"], lg["theta_day"], s["theta_day"]
        drift = None
        if dte > 1.5:
            T1 = _T(1.0)
            v_s = bs_price(S, s["strike"], T1, r, s["_iv"], _kind(s["right"]))
            v_l = bs_price(S, lg["strike"], T1, r, lg["_iv"], _kind(lg["right"]))
            drift = v_s - v_l
        if th < -0.5:
            ins.append({"kind": "theta", "tone": "bad",
                        "text": (f"Time now works AGAINST you: {_usd(th, True)}/day. Your long {_k(lg['strike'])} {'put' if put else 'call'} "
                                 f"({lg['moneyness']}) still holds {_px(lg['extrinsic'])} of time value and sheds {_usd(-th_l)}/day; the short "
                                 f"{_k(s['strike'])} has only {_px(s['extrinsic'])} left to earn ({_usd(th_s, True)}/day)."
                                 + (f" If {tk} sits at {_px(S)}, the spread drifts {_px(mark)} → {_px(drift)} by the day before "
                                    f"expiry ({_usd(-(drift - mark) * mult, True)}) — holding only pays if it bounces."
                                    if drift is not None and drift > mark + 0.05 else ""))})
        elif th > 0.5:
            ins.append({"kind": "theta", "tone": "good",
                        "text": f"Time still works FOR you: {_usd(th, True)}/day — the short {_k(s['strike'])} has {_px(s['extrinsic'])} of time "
                                f"value left to decay against the long's {_px(lg['extrinsic'])}."})
        # VEGA — which way does a change in IV push the cost of getting out?
        vg = net["vega"]
        atm_txt = f" ({out['atm_iv_pct']:.0f}% ATM)" if out.get("atm_iv_pct") else ""
        if vg > 0.5:
            ins.append({"kind": "vega", "tone": "info",
                        "text": (f"Net vega {_usd(vg, True)} per vol point — you are LONG volatility now (the near-the-money long "
                                 "dominates). A calmer market pushes the spread toward its full width, so closing or rolling is "
                                 f"CHEAPER while IV is elevated{atm_txt} than after things settle.")})
        elif vg < -0.5:
            ins.append({"kind": "vega", "tone": "info",
                        "text": f"Net vega {_usd(vg, True)} per vol point — you are SHORT volatility: an IV spike makes the spread dearer to "
                                "close; a vol crush helps you."})
        # WHAT IS LEFT — the honest risk/reward from today's price.
        ins.append({"kind": "remaining", "tone": "info",
                    "text": (f"From today's {_px(mark)} mark: at most {_usd(sp['more_to_lose'], True)} more (≈{p_full_loss * 100:.0f}% odds — "
                             f"{'below' if put else 'above'} {_k(lg['strike'])}) and at most {_usd(sp['more_to_gain'], True)} back "
                             f"(≈{p_keep * 100:.0f}% odds — {'above' if put else 'below'} {_k(s['strike'])}). "
                             f"{sp['pct_of_max_loss']}% of the maximum loss is already priced in.")})
        if sp["slippage"] >= 10:
            ins.append({"kind": "execution", "tone": "warn",
                        "text": (f"Execution: the spread is {_px(mark)} mid / {_px(nat)} natural — a market order gives away "
                                 f"{_usd(sp['slippage'])}"
                                 + (f" ({round(100 * sp['slippage'] / abs(sp['more_to_lose']))}% of the remaining risk)" if sp["more_to_lose"] else "")
                                 + ". Close or roll it as ONE combo order with a limit near mid; never leg out of the short first.")})
        ea = next((e for e in out["early_assignment"] if e["leg"] == s["label"]), None)
        if ea and ea["level"] in ("high", "possible"):
            ins.append({"kind": "early_assignment", "tone": "warn",
                        "text": (f"Early assignment is {'LIKELY' if ea['level'] == 'high' else 'possible'} on the short "
                                 f"{_k(s['strike'])}: {ea['why']}. If it comes you {'own' if put else 'are short'} {100 * n} shares at "
                                 f"{_k(s['strike'])} while the {_k(lg['strike'])} still caps the loss — the same P&L, but "
                                 f"{_usd(s['strike'] * mult)} of buying power until you sell the shares (or exercise the long) together.")})
        if out["pin"]["p_pct"] >= 10:
            ins.append({"kind": "pin", "tone": "warn", "text": out["pin"]["text"]})

        # WHAT EACH LEG CAN DO ON ITS OWN — the legging questions, priced honestly.
        lbid = lg["bid"] if lg.get("bid") is not None else lg["mid"]
        sig_T = (atm or s["_iv"]) * math.sqrt(T)
        s_down = S * math.exp(-2.0 * sig_T) if put else S * math.exp(2.0 * sig_T)
        naked_pnl = (credit + lbid - max(0.0, (s["strike"] - s_down) if put else (s_down - s["strike"]))) * mult
        out["leg_moves"].append({
            "leg": lg["label"], "move": f"Sell the {_k(lg['strike'])} alone", "cash": round(lbid * mult),
            "new_max_loss": None,
            "assignment_basis": round(s["strike"] - credit - lbid, 2) if put else round(s["strike"] + credit + lbid, 2),
            "verdict": "avoid",
            "text": (f"+{_usd(lbid * mult)} now, but it is the only thing capping the loss: the short {_k(s['strike'])} becomes naked. "
                     f"At {_px(s_down)} (a 2σ move by expiry) you'd be down {_usd(naked_pnl)} instead of {_usd(sp['max_loss'])}. "
                     + (f"It only makes sense if you WANT {100 * n} shares at an effective {_px(s['strike'] - credit - lbid)} "
                        f"({(s['strike'] - credit - lbid) / spot * 100 - 100:+.1f}% vs today)." if put else
                        "Only if you are happy to be short the shares."))})
        # roll the long further out of the money (same expiry): cash now for a wider worst case
        ks = sorted(k for k in (book or {}) if _two_sided(_q(book, k, lg["right"])))
        tgt = lg["strike"] - W if put else lg["strike"] + W
        cands = [k for k in ks if (k < lg["strike"] - 1e-6 if put else k > lg["strike"] + 1e-6)]
        if cands:
            k2 = min(cands, key=lambda k: abs(k - tgt))
            q2 = _q(book, k2, lg["right"])
            cash_ps = lbid - float(q2["ask"])
            if cash_ps > 0.02:
                new_ml = (credit + cash_ps - abs(s["strike"] - k2)) * mult
                pivot = lg["strike"] - cash_ps if put else lg["strike"] + cash_ps
                out["leg_moves"].append({
                    "leg": lg["label"], "move": f"Roll the long to {_k(k2)} (same expiry)", "cash": round(cash_ps * mult),
                    "new_max_loss": round(new_ml), "assignment_basis": None, "verdict": "directional",
                    "text": (f"Sell the {_k(lg['strike'])} and buy the {_k(k2)}: {_usd(cash_ps * mult, True)} now, the worst case "
                             f"{_usd(sp['max_loss'])} → {_usd(new_ml)}. Better only if {tk} finishes {'above' if put else 'below'} "
                             f"{_px(pivot)}; worse {'below' if put else 'above'} it — a bet that the slide is over, not a defense.")})
        sask = s["ask"] if s.get("ask") else s["mid"]
        solo_ml = (credit - sask) * mult                    # all-in if the long you keep then expires worthless
        out["leg_moves"].append({
            "leg": s["label"], "move": f"Buy back the {_k(s['strike'])} alone", "cash": round(-sask * mult),
            "new_max_loss": round(solo_ml), "assignment_basis": None, "verdict": "directional",
            "text": (f"{_usd(-sask * mult, True)} to buy it back leaves you holding the {_k(lg['strike'])} {'put' if put else 'call'} "
                     f"({_px(lg['mid'])}) — a {'bearish' if put else 'bullish'} bet that pays only if {tk} keeps "
                     f"{'falling' if put else 'rising'}. Worst case all-in {_usd(solo_ml)} (vs {_usd(sp['max_loss'])} holding the "
                     "spread) if it then expires worthless.")})
    else:
        if net["theta_day"] < -0.5:
            ins.append({"kind": "theta", "tone": "bad",
                        "text": f"Net theta {_usd(net['theta_day'], True)}/day — time works against this position at a flat price."})
        elif net["theta_day"] > 0.5:
            ins.append({"kind": "theta", "tone": "good", "text": f"Net theta {_usd(net['theta_day'], True)}/day — time still pays you."})
        for e in out["early_assignment"]:
            if e["level"] in ("high", "possible"):
                ins.append({"kind": "early_assignment", "tone": "warn",
                            "text": f"Early assignment is {'LIKELY' if e['level'] == 'high' else 'possible'} on the {e['leg']}: {e['why']}."})
    for l in legs:
        l.pop("_iv", None)
    return out


# ── THE ASSIGNMENT LADDER ─────────────────────────────────────────────────────────────────────────────────────────
_MAX_SPREAD_PCT = 0.60       # a quote wider than 60% of its mid is not a price you can roll at


def _liquid(q: dict) -> bool:
    m = _mid(q)
    return bool(_two_sided(q) and m and (float(q["ask"]) - float(q["bid"])) <= _MAX_SPREAD_PCT * m)


def assignment_ladder(*, right: str, short_strike: float, long_strike: Optional[float], qty: int, credit_ps: float,
                      unwind_ps: float, unwind_nat_ps: Optional[float], books: list, spot: float, r: float,
                      earn_days: Optional[float] = None, max_width_mult: float = 1.5, cash_secured: bool = True) -> dict:
    """Expiry by expiry, how far the short strike can move AWAY for NO new money — what time and the event premium buy.

    Close what you hold (`unwind_ps` = today's per-share mid cost to close it; for a spread, short − long) and open:
      · CASH-SECURED — the single short furthest from spot whose premium alone pays for the close. Assigned, you own
        (put) / are short (call) the shares at  basis = strike − all the premium collected  (put) — the lowest cost
        of being assigned that a no-new-money roll can reach at that expiry. Its tail is the stock's, not capped.
      · DEFINED RISK — the spread with the short furthest away that still pays for the close, at the current width or
        wider (up to `max_width_mult`×). A wider wing is what buys the better strike: the worst case grows with it.
    Never moves the short TOWARD spot (that raises the assignment price). `books` = [(expiry, dte, book)];
    returns {rungs:[…], unwind:{mid, natural}} — every rung priced at mids, with the natural (worst-fill) cash beside it."""
    put = right == "P"
    n = int(qty or 1)
    mult = _MULT * n
    width0 = abs(float(short_strike) - float(long_strike)) if long_strike is not None else None
    rungs = []
    for exp, dte, book in sorted(books or [], key=lambda x: x[1]):
        if not book:
            continue
        S = implied_spot(book, spot, dte, r) or spot
        T = _T(dte)
        atm = straddle_vol(book, S, dte, r)
        spans = bool(earn_days is not None and dte >= earn_days)
        ks = sorted(k for k in book if _liquid(_q(book, k, right)))
        away = [k for k in ks if (k <= short_strike + 1e-6 if put else k >= short_strike - 1e-6)]
        rung = {"expiry": exp, "dte": int(dte), "spans_earnings": spans, "atm_iv_pct": (round(atm * 100, 1) if atm else None),
                "csp": None, "spread": None, "same_width": None}

        # cash-secured: the farthest strike whose premium covers the close
        pays = [k for k in away if _mid(_q(book, k, right)) >= unwind_ps - 1e-9] if cash_secured else []
        if pays:
            K = min(pays) if put else max(pays)
            q = _q(book, K, right)
            prem = _mid(q)
            net = prem - unwind_ps
            basis = K - credit_ps - net if put else K + credit_ps + net
            iv = leg_iv(book, K, right, S, T, r, atm)
            p_assign = _p_below(S, K, T, r, iv) if put else 1.0 - _p_below(S, K, T, r, iv)
            rung["csp"] = {
                "strike": K, "premium": round(prem, 2), "bid": round(float(q["bid"]), 2), "ask": round(float(q["ask"]), 2),
                "iv": round(iv, 4), "net_cash": round(net * mult), "net_cash_natural": (round((float(q["bid"]) - unwind_nat_ps) * mult) if unwind_nat_ps is not None else None),
                "basis": round(basis, 2), "basis_vs_spot_pct": round((basis / spot - 1.0) * 100, 1),
                "p_assigned_pct": round(p_assign * 100), "capital": round(K * mult),
            }

        if width0:
            best = None
            for K in (sorted(away) if put else sorted(away, reverse=True)):      # farthest from spot first
                mk = _mid(_q(book, K, right))
                for kl in (sorted((k for k in ks if 0 < (K - k if put else k - K) <= max_width_mult * width0 + 1e-6
                                   and (K - k if put else k - K) >= width0 - 1e-6),
                                  key=lambda k: abs(K - k))):
                    c = mk - _mid(_q(book, kl, right))
                    if c >= unwind_ps - 1e-9:
                        best = (K, kl, c)
                        break
                if best:
                    break
            if best:
                K, kl, c = best
                w = abs(K - kl)
                net = c - unwind_ps
                tot = credit_ps + net
                be = K - tot if put else K + tot
                iv_b = leg_iv(book, be, right, S, T, r, atm) if be > 0 else (atm or 0.5)
                p_profit = (1.0 - _p_below(S, be, T, r, iv_b)) if put else _p_below(S, be, T, r, iv_b)
                iv_l = leg_iv(book, kl, right, S, T, r, atm)
                p_max = _p_below(S, kl, T, r, iv_l) if put else 1.0 - _p_below(S, kl, T, r, iv_l)
                qs, ql = _q(book, K, right), _q(book, kl, right)
                rung["spread"] = {
                    "short": K, "long": kl, "width": w, "credit": round(c, 2), "net_cash": round(net * mult),
                    "net_cash_natural": (round((float(qs["bid"]) - float(ql["ask"]) - unwind_nat_ps) * mult) if unwind_nat_ps is not None else None),
                    "short_iv": round(leg_iv(book, K, right, S, T, r, atm), 4), "long_iv": round(iv_l, 4),
                    "short_mid": round(mk, 2), "long_mid": round(_mid(ql), 2),
                    "max_loss": round(-(w - tot) * mult), "breakeven": round(be, 2),
                    "p_profit_pct": round(p_profit * 100), "p_max_loss_pct": round(p_max * 100),
                    "wider": bool(w > width0 + 1e-6),
                }
            # what the SAME spread costs to roll here (the trade most people try first)
            kl0 = short_strike - width0 if put else short_strike + width0
            if _liquid(_q(book, short_strike, right)) and _liquid(_q(book, kl0, right)):
                qs0, ql0 = _q(book, short_strike, right), _q(book, kl0, right)
                c0 = _mid(qs0) - _mid(ql0)
                other = "C" if put else "P"                      # the short strike's OTHER side — what a covered call / put would sell
                rung["same_width"] = {
                    "short": short_strike, "long": kl0, "net_cash": round((c0 - unwind_ps) * mult),
                    "net_cash_natural": (round((float(qs0["bid"]) - float(ql0["ask"]) - unwind_nat_ps) * mult) if unwind_nat_ps is not None else None),
                    "short_mid": round(_mid(qs0), 2), "long_mid": round(_mid(ql0), 2),
                    "short_iv": round(leg_iv(book, short_strike, right, S, T, r, atm), 4), "long_iv": round(leg_iv(book, kl0, right, S, T, r, atm), 4),
                    "other_mid": (round(_mid(_q(book, short_strike, other)), 2) if _mid(_q(book, short_strike, other)) else None),
                }
        if rung["csp"] or rung["spread"] or rung["same_width"]:
            rungs.append(rung)
    return {"rungs": rungs, "unwind": {"mid": round(unwind_ps * mult), "natural": (round(unwind_nat_ps * mult) if unwind_nat_ps is not None else None)},
            "credit_collected": round(credit_ps * mult)}


def _monthly_like(iso: str) -> bool:
    """A standard monthly: the 3rd Friday, or the Thursday before it when the Friday is a market holiday (Jun 17 2027)."""
    import datetime as _d
    try:
        d = _d.date.fromisoformat(iso)
    except (ValueError, TypeError):
        return False
    return (d.weekday() == 4 and 15 <= d.day <= 21) or (d.weekday() == 3 and 14 <= d.day <= 20)


def ladder_expiries(exps: list, *, cur_dte: int, earn_days: Optional[float] = None,
                    targets: tuple = (30, 60, 90, 150, 240, 450), max_n: int = 7) -> list:
    """Which expiries the assignment ladder prices: the first one after an upcoming print (where the event premium is
    richest per day), then the liquid MONTHLY nearest each horizon target out to ~15 months. `exps` = [(iso, dte)]."""
    cands = sorted(((e, int(d)) for e, d in exps if d is not None and d >= cur_dte + 3), key=lambda x: x[1])
    chosen = []
    if earn_days is not None and earn_days > cur_dte:
        post = next((x for x in cands if x[1] >= earn_days), None)
        if post:
            chosen.append(post)
    pool = [x for x in cands if _monthly_like(x[0])] or cands
    for t in targets:
        if not pool:
            break
        best = min(pool, key=lambda x: abs(x[1] - t))
        if abs(best[1] - t) <= max(20, 0.35 * t) and best not in chosen:
            chosen.append(best)
    return sorted(chosen, key=lambda x: x[1])[:max_n]


# ── WAITING ───────────────────────────────────────────────────────────────────────────────────────────────────────
def wait_cost(*, held_legs: list[dict], new_legs: list[dict], spot: float, r: float, dte_now: float,
              event_var: Optional[float] = None, earn_days: Optional[float] = None, wait_days: Optional[float] = None,
              near_vol: Optional[float] = None) -> Optional[dict]:
    """Net cash of a roll done NOW vs the same roll done after `wait_days` (default: the day before the current expiry),
    at the same stock price, and at a ±1σ move over the wait.

    held_legs: [{right, strike, sign, qty, iv, mark}] (the position closed); new_legs: [{right, strike, sign, qty, dte, iv,
    mid}] (opened). Each held leg is re-priced with less life at its own vol. A new leg that spans the print keeps its
    EVENT variance whole and loses only the ordinary (base) part: σ_later² · T_later = σ_base² · T_later + event_var —
    which is why its quoted IV rises into the print while its premium does not."""
    if dte_now is None or dte_now <= 1.5 or not (held_legs or new_legs):
        return None
    w = float(wait_days if wait_days is not None else dte_now - 1.0)
    if w <= 0.5:
        return None

    def close_cash(S, after):
        tot = 0.0
        for l in held_legs:
            v = (bs_price(S, l["strike"], _T(dte_now - w), r, max(l["iv"], 0.02), _kind(l["right"])) if after
                 else float(l["mark"]))
            tot += int(l["sign"]) * int(l.get("qty") or 1) * v
        return tot

    def new_vol(l, after):
        iv = max(float(l["iv"]), 0.02)
        if not after:
            return iv
        Tn = _T(l["dte"])
        spans = bool(event_var and earn_days is not None and l["dte"] >= earn_days and earn_days > w)
        if not spans:
            return iv
        base_rate = max(iv * iv * Tn - float(event_var), (0.25 * iv) ** 2 * Tn) / Tn
        Tl = _T(l["dte"] - w)
        return math.sqrt((base_rate * Tl + float(event_var)) / Tl)

    def open_cash(S, after):
        tot = 0.0
        for l in new_legs:
            if after:
                v = bs_price(S, l["strike"], _T(l["dte"] - w), r, new_vol(l, True), _kind(l["right"]))
            else:
                v = float(l["mid"])
            tot += -int(l["sign"]) * int(l.get("qty") or 1) * v
        return tot

    now = (close_cash(spot, False) + open_cash(spot, False)) * _MULT
    later = (close_cash(spot, True) + open_cash(spot, True)) * _MULT
    out = {"wait_days": round(w), "net_now": round(now), "net_later_flat": round(later), "cost_flat": round(later - now)}
    sh = next((l for l in new_legs if int(l["sign"]) < 0), None)
    if sh:
        out["new_short"] = {"strike": sh["strike"], "iv_now_pct": round(float(sh["iv"]) * 100, 1),
                            "iv_later_pct": round(new_vol(sh, True) * 100, 1), "premium_now": round(float(sh["mid"]), 2),
                            "premium_later": round(bs_price(spot, sh["strike"], _T(sh["dte"] - w), r, new_vol(sh, True), _kind(sh["right"])), 2)}
    def later_at(S):
        return (close_cash(S, True) + open_cash(S, True)) * _MULT

    def now_iv_shift(dv):
        """The same move TODAY if every implied vol (held and new legs) moved by `dv` (absolute, e.g. −0.10): repriced vs the
        model's own today-value, so only the vol change shows (a selloff that cools = IV down)."""
        T0 = _T(dte_now)
        d = 0.0
        for l in held_legs:
            iv = max(float(l["iv"]), 0.02)
            k = _kind(l["right"])
            d += int(l["sign"]) * int(l.get("qty") or 1) * (bs_price(spot, l["strike"], T0, r, max(iv + dv, 0.02), k) - bs_price(spot, l["strike"], T0, r, iv, k))
        for l in new_legs:
            iv = max(float(l["iv"]), 0.02)
            k = _kind(l["right"])
            Tn = _T(l["dte"])
            d += -int(l["sign"]) * int(l.get("qty") or 1) * (bs_price(spot, l["strike"], Tn, r, max(iv + dv, 0.02), k) - bs_price(spot, l["strike"], Tn, r, iv, k))
        return round(now + d * _MULT)

    out["iv_down"] = now_iv_shift(-0.10)
    out["iv_up"] = now_iv_shift(+0.10)

    if near_vol:
        k = float(near_vol) * math.sqrt(w / 365.0)
        up, dn = spot * math.exp(k), spot * math.exp(-k)
        out["up"] = {"spot": round(up, 2), "net_later": round(later_at(up))}
        out["down"] = {"spot": round(dn, 2), "net_later": round(later_at(dn))}
    if sh:
        out["new_short"]["premium_later"] = float(out["new_short"]["premium_later"])
    return out


# ── THE THREE GOALS ───────────────────────────────────────────────────────────────────────────────────────────────
def objectives(menu: dict) -> list[dict]:
    """The ranked menu, re-read by GOAL — lowest risk · recover with defined risk · lowest cost if assigned — so the user
    can see the best move for each aim (with the same numbers as the cards) next to the desk's single pick."""
    alts = menu.get("alternatives") or []
    close = next((a for a in alts if a.get("category") == "exit"), None)
    hold = next((a for a in alts if a.get("category") == "hold"), None)
    pick = next((a for a in alts if a.get("is_desk_pick")), None)
    sp = (menu.get("anatomy") or {}).get("spread") or {}
    out = []
    if close is not None:
        nat = sp.get("close_natural")
        pts = [f"Locks {_usd(float(close.get('ev') or 0.0), True)} with certainty; nothing more can go wrong."]
        if nat is not None and sp.get("mark") is not None and sp.get("slippage", 0) >= 10:
            pts.append(f"Work a limit near {_px(sp['mark'])} (mid); the natural {_px(nat)} gives away {_usd(sp['slippage'])}.")
        if hold is not None and hold.get("max_loss") is not None:
            pts.append(f"Versus holding: gives up the {hold.get('pop_pct')}% chance of getting back to profit to remove the last "
                       f"{_usd(float(hold['max_loss']) - float(close.get('ev') or 0.0), True)} of downside.")
        out.append({"key": "min_risk", "title": "Lowest risk", "alt": close.get("name"), "is_pick": close is pick,
                    "headline": f"Close it — lock {_usd(float(close.get('ev') or 0.0), True)}", "points": pts,
                    "net_cash": close.get("net_cash"), "worst": close.get("ev"), "best": close.get("ev"), "pop_pct": None})
    def capped(a):
        """Every short option has a same-right long behind it that lives at least as long — a spread, not a naked short
        (`defined_risk` alone only flags an uncovered CALL; a naked put is 'bounded' at the strike but it is the stock's tail)."""
        legs = [l for l in a.get("legs") or [] if l.get("right") in ("P", "C")]
        for sh in (l for l in legs if l.get("action") == "SELL"):
            if not any(l.get("action") == "BUY" and l.get("right") == sh.get("right") and (l.get("dte_days") or 0) >= (sh.get("dte_days") or 0)
                       and ((l["strike"] < sh["strike"]) if sh["right"] == "P" else (l["strike"] > sh["strike"])) for l in legs):
                return False
        return True

    rec = [a for a in alts if a.get("defined_risk") and capped(a) and a.get("category") not in ("exit",)
           and a.get("pop_pct") is not None and float(a.get("net_cash") or 0.0) >= -1.0]
    if rec:
        best = max(rec, key=lambda a: (a.get("pop_pct") or 0, a.get("desk_score") or 0))
        pts = []
        if best is not hold and hold is not None:
            pts.append(f"P(back to profit) {hold.get('pop_pct')}% → {best.get('pop_pct')}%; worst case "
                       f"{_usd(float(hold.get('max_loss') or 0.0), True)} → {_usd(float(best.get('max_loss') or 0.0), True)}.")
            rm = best.get("roll_meta") or {}
            if rm.get("dte"):
                pts.append(f"Ties the trade up {rm['dte']} more days" + (" — through earnings" if rm.get("spans_earnings") else "") + ".")
        else:
            pts.append("No defined-risk move improves the odds without new money — holding IS the recovery path; manage the expiry.")
        out.append({"key": "recover", "title": "Recover, defined risk", "alt": best.get("name"), "is_pick": best is pick,
                    "headline": best.get("name"), "points": pts, "net_cash": best.get("net_cash"),
                    "worst": best.get("max_loss"), "best": best.get("max_gain"), "pop_pct": best.get("pop_pct")})
    csp = [a for a in alts if (a.get("roll_meta") or {}).get("route") == "csp"]
    if csp:
        best = min(csp, key=lambda a: float(a["roll_meta"]["basis"]))
        rm = best["roll_meta"]
        pts = [f"If assigned you own the shares at {_px(rm['basis'])} ({rm['basis_vs_spot_pct']:+.1f}% vs today) — "
               f"≈{rm.get('p_assigned_pct')}% odds; {_usd(rm['capital'])} cash-secured for {rm['dte']} days.",
               "The tail is the stock's (no long put) — size it as shares you are happy to own."]
        out.append({"key": "assign", "title": "Lowest cost if assigned", "alt": best.get("name"), "is_pick": best is pick,
                    "headline": best.get("name"), "points": pts, "net_cash": best.get("net_cash"),
                    "worst": None, "best": best.get("max_gain"), "pop_pct": best.get("pop_pct"), "basis": rm["basis"]})
    return out


# ── THE ACTION PLAN ───────────────────────────────────────────────────────────────────────────────────────────────
def action_plan(menu: dict) -> Optional[dict]:
    """The whole desk on one card: a few short FACTS and the ACTIONS, each with the same numbers — cash today (at a realistic
    fill), worst case, best case (all-in, the trade you hold included), the odds it ends meaningfully better than closing now,
    and E[P&L] — its orders, and WHEN to do it.

    With the risk-budget search (phase 2) the rows follow the user's rule: first the moves that keep the worst case at or
    below today's (Hold is that baseline), then the moves that add a small, stated amount of risk for more time — the user's
    own "same strikes, later" roll always answered — and Close. The pick is the best within the rule; a move must beat
    holding by ≥3 points of odds to replace it. Without the search (phase 1), the ranked menu's rows stand in."""
    alts = menu.get("alternatives") or []
    if not alts:
        return None
    hold = next((a for a in alts if a.get("category") == "hold"), None)
    close = next((a for a in alts if a.get("category") == "exit"), None)
    an = menu.get("anatomy") or {}
    sp = an.get("spread") or {}
    ev = menu.get("event") or {}
    held = menu.get("held") or {}
    exp_now = _date(next((l.get("expiry") for l in held.get("legs") or [] if l.get("expiry")), None))
    pin = an.get("pin") or {}
    th = (an.get("net") or {}).get("theta_day")

    facts = []
    if sp:
        facts.append(f"Now {_usd(sp['loss_now'], True)} · worst {_usd(sp['max_loss'], True)}")
    elif menu.get("unrealized_pnl") is not None:
        facts.append(f"Now {_usd(float(menu['unrealized_pnl']), True)}")
    if th is not None and abs(th) >= 0.5:
        facts.append(f"Time {_usd(th, True)}/day")
    zl = next((z for z in an.get("zones") or [] if z["key"] == "max_loss"), None)
    if zl:
        facts.append(f"{zl['p_pct']}% odds of the max loss")
    if ev.get("date"):
        facts.append(f"Earnings {_date(ev['date'])}" + (f" ±{float(ev['implied_move_pct']):.0f}%" if ev.get("implied_move_pct") else "")
                     + (" (after expiry)" if ev.get("position") == "after" else " (before expiry)"))
    eh = menu.get("earnings_history") or {}
    if eh.get("ratio"):
        facts.append(f"Last {eh['n']} prints: median ±{eh['median_abs_pct']:.0f}% vs ±{eh['implied_abs_pct']:.0f}% priced"
                     + (f", {eh['violent']} blew out" if eh.get("violent") else ""))
    pr = menu.get("pin_read") or {}
    if pr.get("max_pain") is not None:
        facts.append(f"Max pain {_k(pr['max_pain'])}")
    if (pin.get("p_pct") or 0) >= 10:
        facts.append(f"{_k(pin['low'])}–{_k(pin['high'])} at expiry = assigned: {pin['p_pct']}%")

    def when_of(w, what):
        c = (w or {}).get("cost_flat")
        if c is not None and c <= -25:
            return f"Do it today: {what} on the last day costs ≈{_usd(-c)} more if the stock goes nowhere."
        if c is not None and c >= 25:
            return f"No rush: {what} on the last day nets ≈{_usd(c)} more if the stock goes nowhere."
        return None

    hold_when = (f"By the {exp_now} close: if it is between {_k(pin['low'])} and {_k(pin['high'])}, roll or close — otherwise you own "
                 f"shares at {_k(pin['high'])} with no protection.") if (pin.get("p_pct") or 0) >= 10 else None
    rb = menu.get("risk_budget")
    if rb:
        rows = []
        h = rb["hold"]

        def rb_row(c, tag, tier):
            return {"alt": None, "tag": tag, "tier": tier, "title": c["title"], "is_pick": False, "kind": c.get("kind"),
                    "at_spot": c.get("at_spot"), "worst_side": c.get("worst_side"), "meta": c.get("meta"),
                    "cash": c["cash_fill"], "cash_mid": c["cash"], "worst": c["worst"], "best": c["best"],
                    "beat_close_pct": c["p_beat"], "ev": c["ev"], "days": c["days"], "orders": c["orders"],
                    "added_risk": c.get("added_risk") or 0, "capital": c.get("capital"),
                    "why": c.get("note") or ("Runs through earnings." if c.get("spans_earnings") else ""),
                    "when": when_of(c.get("wait"), "the same move"), "wait": c.get("wait"), "alt_route": None}

        hold_row = {"alt": hold.get("name") if hold else None, "tag": "Do nothing", "tier": "rule", "title": f"Hold to {exp_now}",
                    "is_pick": False, "cash": 0, "cash_mid": 0, "worst": h["worst"], "best": h["best"], "beat_close_pct": h["p_beat"],
                    "ev": h["ev"], "days": menu.get("dte_days"), "orders": [], "added_risk": 0, "capital": None,
                    "why": (f"Time costs {_usd(-th)}/day; " if th is not None and th < -0.5 else "") + "the baseline every move is measured against.",
                    "when": hold_when, "wait": None, "alt_route": None}
        ev_tol = max(15.0, 0.02 * abs(float(rb.get("close_fill") or 0.0)))
        inside = [rb_row(c, "Within your rule", "rule") for c in rb.get("best") or []
                  if not ((c["p_beat"] or 0) <= (h["p_beat"] or 0) and (c["ev"] or 0) <= (h["ev"] or 0))]   # holding dominates it
        better = [x for x in inside if (x["beat_close_pct"] or 0) >= (h["p_beat"] or 0) + 3 and (x["ev"] or 0) >= (h["ev"] or 0) - ev_tol]
        pick_row = max(better, key=lambda x: (x["beat_close_pct"] or 0, x["ev"] or 0)) if better else hold_row
        pick_row["is_pick"] = True
        inside = sorted(inside + [hold_row], key=lambda x: (0 if x["is_pick"] else 1, -(x["beat_close_pct"] or 0), -(x["ev"] or 0)))
        rows += inside
        more_rows = [rb_row(c, f"+{_usd(c['added_risk'])} worst case", "more") for c in rb.get("more_time") or []]
        trade = max(more_rows, key=lambda x: ((x["beat_close_pct"] or 0) - (h["p_beat"] or 0)) / max(x["added_risk"], 25)) if more_rows else None
        near_miss = max((x for x in inside if x is not hold_row), key=lambda x: x["beat_close_pct"] or 0, default=None)
        if pick_row is hold_row:
            verdict = "Within your rule nothing clearly beats holding"
            if near_miss is not None:
                verdict += (f" — the closest, {near_miss['title']}, moves the odds {h['p_beat']}% → {near_miss['beat_close_pct']}% "
                            f"for {_usd((near_miss['ev'] or 0) - (h['ev'] or 0), True)} of expected value")
            verdict += "."
        elif pick_row.get("kind") == "lock":
            tk = menu.get("ticker") or "the stock"
            k2 = (pick_row.get("meta") or {}).get("k2")
            where = {"up": f" — and that only if {tk} rallies past {_k(k2)}" if k2 else " — and that only on a big rally",
                     "down": " — on a further drop", "middle": "", "both": ""}.get(pick_row.get("worst_side") or "", "")
            verdict = (f"{pick_row['title']} (+{_usd(pick_row['cash'] or 0)}): you end at about {_usd(pick_row.get('at_spot') or 0, True)} "
                       f"if {tk} stays where it is, {_usd(pick_row['best'], True)} at best and {_usd(pick_row['worst'], True)} at worst{where}. "
                       f"Closing now locks {_usd(rb['close_fill'], True)}. Better than closing {pick_row['beat_close_pct']}% of the time; "
                       f"it gives up the {h.get('p_profit') or 0}% chance of getting back to profit.")
        else:
            verdict = (f"{pick_row['title']}: odds of losing less than closing {h['p_beat']}% → {pick_row['beat_close_pct']}%, "
                       f"worst case {_usd(h['worst'], True)} → {_usd(pick_row['worst'], True)}.")
        if trade is not None and (trade["beat_close_pct"] or 0) >= (pick_row["beat_close_pct"] or 0) + 5:
            verdict += (f" Best small-risk trade: {trade['title']} — odds {h['p_beat']}% → {trade['beat_close_pct']}% "
                        f"for {_usd(trade['added_risk'])} more worst case.")
        rows += [rb_row(c, f"+{_usd(c['added_risk'])} worst case", "more") for c in rb.get("more_time") or []]
        if rb.get("same_strikes"):
            c = rb["same_strikes"]
            r_ = rb_row(c, f"Your idea · +{_usd(c['added_risk'])} worst case" if c.get("added_risk") else "Your idea", "more")
            exp_c = (c.get("meta") or {}).get("exp")
            same_alt = next((a for a in alts if (a.get("roll_meta") or {}).get("route") == "same"
                             and (a.get("roll_meta") or {}).get("expiry") == exp_c), None)
            r_["alt_route"] = (same_alt or {}).get("alt_route")
            rows.append(r_)
        if close is not None:
            rows.append({"alt": close.get("name"), "tag": "Lowest risk", "tier": "close", "title": "Close now", "is_pick": False,
                         "cash": rb["close_fill"] - round(float(held.get("collected") or 0.0)), "cash_mid": close.get("net_cash"),
                         "worst": rb["close_fill"], "best": rb["close_fill"], "beat_close_pct": None, "ev": rb["close_fill"],
                         "days": 0, "orders": [], "added_risk": 0, "capital": None,
                         "why": (f"Locks {_usd(rb['close_fill'], True)} at a realistic fill ({_usd(close.get('ev') or 0, True)} at mid). "
                                 "One combo order with a limit — never the legs one by one."),
                         "when": when_of(close.get("wait"), "closing"), "wait": close.get("wait"), "alt_route": None})
        return {"facts": facts, "rows": rows, "rule_worst": rb["rule_worst"], "benchmark": rb["close_fill"], "need": rb["need"],
                "searched": rb.get("searched"), "allowance": rb.get("allowance"), "verdict": verdict,
                "explorer": rb.get("explorer") or []}

    # ── phase 1 / no search: the ranked menu stands in ──
    pick = next((a for a in alts if a.get("is_desk_pick")), None)
    rows = []
    for a, tag in ((pick, "Desk pick"), (close, "Lowest risk"), (hold, "Do nothing")):
        if a is None or any(r["alt"] == a.get("name") for r in rows):
            continue
        rows.append({"alt": a.get("name"), "tag": tag, "tier": "rule", "title": (f"Hold to {exp_now}" if a.get("category") == "hold" else
                     "Close now" if a.get("category") == "exit" else a.get("name")), "is_pick": a is pick,
                     "cash": a.get("net_cash"), "cash_mid": a.get("net_cash"),
                     "worst": a.get("max_loss") if a.get("category") != "exit" else a.get("ev"),
                     "best": a.get("max_gain") if a.get("category") != "exit" else a.get("ev"),
                     "beat_close_pct": None, "ev": a.get("ev"), "days": menu.get("dte_days"), "orders": [], "added_risk": 0,
                     "capital": None, "why": "", "when": (hold_when if a.get("category") == "hold" else None), "wait": None,
                     "alt_route": None})
    return {"facts": facts, "rows": rows, "rule_worst": (hold or {}).get("max_loss"), "benchmark": (close or {}).get("ev"),
            "need": None, "searched": None, "allowance": None, "verdict": None}


# ── TODAY OR WAIT? ────────────────────────────────────────────────────────────────────────────────────────────────
def timing_read(menu: dict) -> Optional[dict]:
    """Is today the right day for each action, or does waiting (to the last trading day before expiry) do better?

    For every action on the plan: the cash it brings TODAY vs the same action on the last day — at an unchanged price, after a
    ±1σ move over the wait, and today if implied vol drops 10 points (a selloff that cools). The end position is the same either
    way, so the difference in that cash IS the difference in wealth — higher is better. Beside it the read that decides which
    of those worlds is likelier: the technicals (bounce vs further-drop signals), the tape (stock vs market, VIX), the vol read
    and the print (what the event adds to post-print options, and what it takes away after).
    Pure — reads `wait` on each alternative, `tape`, `vol_read`, `event`, `recoverability.factors`."""
    ap = menu.get("action_plan") or {}
    by = {a.get("name"): a for a in menu.get("alternatives") or []}
    tk = menu.get("ticker") or "the stock"
    rows, w0 = [], None
    for r in ap.get("rows") or []:
        w = r.get("wait") or (by.get(r["alt"]) or {}).get("wait")
        if not w:
            continue
        w0 = w0 or w
        now, flat = w["net_now"], w["net_later_flat"]
        rows.append({"alt": r["alt"], "title": r["title"], "now": now, "flat": flat,
                     "up": (w.get("up") or {}).get("net_later"), "down": (w.get("down") or {}).get("net_later"),
                     "iv_down": w.get("iv_down"),
                     "verdict": ("today" if flat - now <= -25 else "last_day" if flat - now >= 25 else "either")})
    signals = []
    for f in (menu.get("recoverability") or {}).get("factors") or []:
        if f.get("kind") != "ta" or f.get("favorable") is None:
            continue
        signals.append({"side": "bounce" if f["favorable"] else "drop",
                        "text": f"{f['label']}: {str(f.get('detail') or '').split(' — ')[0]}"})
    tp = menu.get("tape") or {}
    if tp.get("ret5") is not None:
        mkt = f" (SPY {tp['spy5']:+.1f}%)" if tp.get("spy5") is not None else ""
        signals.append({"side": "market", "text": f"{tk} {tp['ret5']:+.1f}% in 5 days, {tp.get('ret20', 0):+.1f}% in 20{mkt}"})
    if tp.get("vix") is not None:
        signals.append({"side": "market", "text": f"VIX {tp['vix']:.1f}" + (f" ({tp['vix5']:+.1f} in 5 days)" if tp.get("vix5") is not None else "")})
    vr = menu.get("vol_read") or {}
    if vr.get("atm_iv_pct") and vr.get("hv20_pct"):
        vrp = vr.get("vrp_pct") or 0.0
        signals.append({"side": "vol", "text": f"IV {vr['atm_iv_pct']:.0f}% vs realized {vr['hv20_pct']:.0f}% — options "
                                               + ("rich" if vrp > 10 else "cheap" if vrp < -10 else "fairly priced")})
    if tp.get("hv20") and tp.get("hv60"):
        st = "rising" if tp["hv20"] > 1.15 * tp["hv60"] else "cooling" if tp["hv20"] < 0.85 * tp["hv60"] else "steady"
        signals.append({"side": "vol", "text": f"Realized vol {st} (HV20 {tp['hv20']:.0f}% vs HV60 {tp['hv60']:.0f}%)"})

    eh = menu.get("earnings_history") or {}
    if eh.get("read"):
        signals.append({"side": "event", "text": (f"Last {eh['n']} prints moved ±{eh['mean_abs_pct']:.0f}% on average (median ±{eh['median_abs_pct']:.0f}%, "
                                                  f"{eh['ups']} up / {eh['n'] - eh['ups']} down) vs ±{eh['implied_abs_pct']:.0f}% priced now — it {eh['read']}.")})
    pr = menu.get("pin_read") or {}
    if pr.get("text"):
        signals.append({"side": "market", "text": pr["text"]})
    ev = menu.get("event") or {}
    crush = None
    post = next((t for t in ev.get("term") or [] if t.get("spans_earnings") and t.get("atm_iv_pct")), None)
    if post and ev.get("event_var"):
        Tp = _T(post["dte"])
        sp_ = post["atm_iv_pct"] / 100.0
        after = math.sqrt(max(sp_ * sp_ * Tp - float(ev["event_var"]), (0.25 * sp_) ** 2 * Tp) / Tp)
        crush = {"expiry": post["expiry"], "iv_now_pct": round(sp_ * 100), "iv_after_pct": round(after * 100),
                 "text": (f"Earnings {_date(ev.get('date'))}: {_date(post['expiry'])} options price a ±{float(ev.get('implied_move_pct') or 0):.0f}% move "
                          f"(ATM IV {sp_ * 100:.0f}%); right after the print that falls to ≈{after * 100:.0f}%. Anything you SELL across the print "
                          "is paid for the event; anything you BUY across it pays for it.")}
        signals.append({"side": "event", "text": crush["text"]})

    if not rows:
        return {"verdict": None, "headline": None, "rows": [], "signals": signals, "crush": crush} if signals else None
    votes = [r["verdict"] for r in rows]
    verdict = max(("today", "last_day", "either"), key=votes.count)
    costs = sorted(r["flat"] - r["now"] for r in rows)
    bounce = (w0.get("up") or {}).get("spot")
    bounce_pct = round((bounce / float(menu.get("spot") or bounce) - 1.0) * 100) if bounce else None
    pays_on_bounce = [r for r in rows if r["up"] is not None and r["up"] > r["now"] + 25]
    if verdict == "today":
        head = (f"Today. If {tk} goes nowhere, doing it on the last day costs {_usd(-costs[-1])}–{_usd(-costs[0])} more"
                if len(costs) > 1 and costs[0] != costs[-1] else f"Today. If {tk} goes nowhere, waiting costs {_usd(-costs[0])}")
        head += (f"; waiting only pays on a bounce to ≈{_px(bounce)} ({bounce_pct:+d}%, about 1-in-6 odds) — and mostly for closing."
                 if (bounce and pays_on_bounce) else ".")
    elif verdict == "last_day":
        head = f"No rush. At an unchanged price the same move nets {_usd(costs[0])}–{_usd(costs[-1])} more on the last day — do it before the closing bell."
    else:
        head = "The clock barely matters at an unchanged price — decide on the view (bounce or not), not the date."
    ivd = [r for r in rows if r["iv_down"] is not None and r["iv_down"] < r["now"] - 10]
    if ivd:
        worst = min(r["iv_down"] - r["now"] for r in ivd)
        head += f" If IV cools 10 points, it gets up to {_usd(-worst)} dearer — today's high IV is on your side."
    return {"verdict": verdict, "headline": head, "wait_days": (w0 or {}).get("wait_days"),
            "up_spot": bounce, "down_spot": (w0.get("down") or {}).get("spot"), "rows": rows, "signals": signals, "crush": crush}


# ── THE RISK-BUDGET SEARCH: never add risk, raise the odds of losing less ─────────────────────────────────────────
def vol_curve(points: dict) -> "callable":
    """σ(dte) from known ATM vols {dte: σ}: linear in TOTAL variance between known expiries, flat beyond them."""
    pts = sorted((int(k), float(v)) for k, v in (points or {}).items() if v)
    if not pts:
        return lambda d: 0.6

    def f(d):
        d = max(float(d), 0.5)
        if d <= pts[0][0]:
            return pts[0][1]
        for (ta, sa), (tb, sb) in zip(pts, pts[1:]):
            if ta <= d <= tb:
                va, vb = sa * sa * ta, sb * sb * tb
                return math.sqrt(max((va + (vb - va) * (d - ta) / (tb - ta)) / d, 1e-6))
        return pts[-1][1]
    return f


def _pav(y: list) -> list:
    """Pool-adjacent-violators: the closest non-decreasing sequence (least squares) — a CDF can't go down."""
    blocks = [[v, 1] for v in y]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0] + 1e-12:
            v = (blocks[i][0] * blocks[i][1] + blocks[i + 1][0] * blocks[i + 1][1]) / (blocks[i][1] + blocks[i + 1][1])
            blocks[i] = [v, blocks[i][1] + blocks[i + 1][1]]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    out = []
    for v, w in blocks:
        out += [v] * w
    return out


def market_law(book: Optional[dict], spot: float, dte: float, r: float, fallback_vol: float):
    """The MARKET'S distribution of the price at this expiry, read off the option prices (Breeden-Litzenberger on the smile):
    each listed strike's OUT-OF-THE-MONEY mid is inverted to an implied vol (the skew), the smile is interpolated across
    strikes and held flat beyond the last ones, and P(S_T < K) = e^{rT}·∂Put/∂K is taken from the smooth Black-Scholes put
    curve priced on it — cleaned to a monotone CDF. Every listed strike reprices by construction (the law integrates back to
    the put curve), so a position priced off it is worth what the market charges: holding and closing agree in expectation
    (a flat ATM lognormal put AAOI's $80/70 at $7.68 against a $8.35 market — a phantom $67 'edge' for holding), and the
    skew's fat left tail is in the odds. Returns a quantile function u → S, or None when fewer than four strikes invert."""
    import numpy as np
    from scipy.special import ndtr
    if not book or not spot:
        return None
    S = implied_spot(book, spot, dte, r) or spot
    T = _T(dte)
    pts = []
    for k in sorted(book):
        if abs(k / S - 1.0) > 0.8:
            continue
        rt = "P" if k <= S else "C"
        q = _q(book, k, rt)
        if not _liquid(q):
            continue
        v = implied_vol(_mid(q), S, k, T, r, rt)
        if v:
            pts.append((float(k), v))
    if len(pts) < 4:
        return None
    ks = np.array([k for k, _ in pts])
    vs = np.array([v for _, v in pts])
    # a SMOOTH smile: quadratic in log-moneyness (level · skew · curvature), held flat beyond the data. Interpolating straight
    # through the quotes turns a few vol points of noise between $1 strikes into fake density spikes (∂σ/∂K enters the CDF):
    # AAOI's Nov 6 law put 15% of the probability between $75 and $80.
    x = np.log(ks / S)
    coef = np.polyfit(x, vs, 2 if len(pts) >= 5 else 1)
    sig = max(float(fallback_vol or 0.5), float(vs.max()), 0.05) * math.sqrt(T)
    grid = np.exp(np.linspace(math.log(S) - 7 * sig, math.log(S) + 7 * sig, 1200))
    gx = np.log(grid / S)
    # beyond the listed strikes the smile continues along its TANGENT (a flat extrapolation puts a kink in σ(K) at the last
    # strike — a butterfly arbitrage that shifts probability mass), capped by Lee's moment bound σ²T ≤ 2|x|
    d = np.polyder(coef)
    lo_x, hi_x = float(x.min()), float(x.max())
    inside = np.polyval(coef, np.clip(gx, lo_x, hi_x))
    tang = np.where(gx < lo_x, np.polyval(coef, lo_x) + np.polyval(d, lo_x) * (gx - lo_x),
                    np.where(gx > hi_x, np.polyval(coef, hi_x) + np.polyval(d, hi_x) * (gx - hi_x), inside))
    lee = np.sqrt(np.maximum(2.0 * np.abs(gx), 1e-12) / T)
    iv = np.clip(np.where((gx < lo_x) | (gx > hi_x), np.minimum(tang, np.maximum(lee, inside)), inside), 0.05, None)
    sd = iv * math.sqrt(T)
    d1 = (np.log(S / grid) + (r + 0.5 * iv * iv) * T) / sd
    put = grid * math.exp(-r * T) * ndtr(-(d1 - sd)) - S * ndtr(-d1)
    cdf = np.gradient(put, grid) * math.exp(r * T)
    cdf = np.maximum.accumulate(np.clip(cdf, 0.0, 1.0))
    cdf = cdf + np.linspace(0, 1e-9, len(grid))       # strictly increasing for the inverse
    return lambda u: np.interp(u, cdf, grid)


def lattice_eval(legs: list, base: float, *, spot: float, r: float, vol_of, benchmark: Optional[float] = None,
                 n: int = 40, law_of=None) -> Optional[dict]:
    """ALL-IN outcome of a position whose legs may sit in TWO expiries — worst / best case EXACT, odds and E[P&L] on the
    joint law of the price at each expiry (the MARKET'S law at the first expiry when `law_of(dte)` gives one — see
    `market_law` — else a lognormal at the ATM vol).

    legs: [{right, strike, sign, qty, dte}] = everything held AFTER the move; base = what is already fixed (premium collected
    at open + today's net cash). At the first expiry each leg settles at intrinsic; a SHORT option assigned there is KEPT as
    stock to the second expiry when a later LONG option on the same side protects it (the collar / married-put plan: take the
    shares at $80, the Nov 20 $70 put floors them) — otherwise everything is closed at expiry. The price path: S1 lognormal at
    the first expiry's vol, S2 | S1 at the FORWARD vol between the expiries (σ2²T2 − σ1²T1 — the earnings variance sits in it
    when the print falls between). Payoffs are piecewise-linear, so the worst/best are read on the strike grid (exact), and
    "unbounded" is detected by comparing 3× and 6× spot. Returns {worst, best, ev, p_beat, p_profit} ($; % odds)."""
    import numpy as np
    if not legs:
        return {"worst": round(base), "best": round(base), "ev": round(base), "p_beat": None, "p_profit": 100 if base >= 0 else 0}
    dates = sorted({int(l["dte"]) for l in legs})
    if len(dates) > 2 or spot <= 0:
        return None
    d1, d2 = dates[0], dates[-1]
    two = d2 != d1
    later = [l for l in legs if int(l["dte"]) == d2] if two else []
    carry_p = any(l["right"] == "P" and l["sign"] > 0 for l in later)
    carry_c = any(l["right"] == "C" and l["sign"] > 0 for l in later)

    def pnl(S1, S2):
        tot = np.full(np.broadcast(S1, S2).shape, float(base))
        sh = np.zeros_like(tot)
        for l in legs:
            S = S1 if int(l["dte"]) == d1 else S2
            K, q = float(l["strike"]), int(l.get("qty") or 1) * _MULT
            I = np.maximum(0.0, K - S) if l["right"] == "P" else np.maximum(0.0, S - K)
            tot = tot + int(l["sign"]) * q * I
            if two and int(l["dte"]) == d1 and int(l["sign"]) < 0:
                if l["right"] == "P" and carry_p:
                    sh = sh + q * (S1 < K)
                elif l["right"] == "C" and carry_c:
                    sh = sh - q * (S1 > K)
        return tot + (sh * (S2 - S1) if two else 0.0)

    ks = sorted({float(l["strike"]) for l in legs} | {0.01, float(spot), 3.0 * spot})
    g1 = np.array(ks)
    G1, G2 = (np.meshgrid(g1, g1, indexing="ij") if two else (g1, g1))
    vals = pnl(G1, G2)
    worst, best = float(vals.min()), float(vals.max())
    hi = 6.0 * spot
    probe = [pnl(np.array([hi]), np.array([hi]))[0], pnl(np.array([3.0 * spot]), np.array([3.0 * spot]))[0]]
    if two:
        probe += [pnl(np.array([0.01]), np.array([hi]))[0], pnl(np.array([0.01]), np.array([3.0 * spot]))[0],
                  pnl(np.array([hi]), np.array([0.01]))[0], pnl(np.array([3.0 * spot]), np.array([0.01]))[0]]
    unb_low = any(probe[i] < probe[i + 1] - 1.0 for i in range(0, len(probe), 2))
    unb_high = any(probe[i] > probe[i + 1] + 1.0 for i in range(0, len(probe), 2))

    z, w = np.polynomial.hermite_e.hermegauss(n)
    w = w / w.sum()
    s1, T1 = vol_of(d1), _T(d1)
    q1 = law_of(d1) if law_of else None
    S1 = (q1(np.array([_ncdf(float(x)) for x in z])) if q1 is not None
          else spot * np.exp((r - 0.5 * s1 * s1) * T1 + s1 * math.sqrt(T1) * z))
    if two:
        s2, T2 = vol_of(d2), _T(d2)
        vf = math.sqrt(max((s2 * s2 * T2 - s1 * s1 * T1) / max(T2 - T1, 1e-6), (0.3 * s1) ** 2))
        dT = max(T2 - T1, 1e-6)
        A, Z2 = np.meshgrid(S1, z, indexing="ij")
        S2 = A * np.exp((r - 0.5 * vf * vf) * dT + vf * math.sqrt(dT) * Z2)
        P = pnl(A, S2)
        W = np.outer(w, w)
    else:
        P = pnl(S1, S1)
        W = w
    lo_v = float(pnl(np.array([0.01]), np.array([0.01]))[0])
    hi_v = float(pnl(np.array([3.0 * spot]), np.array([3.0 * spot]))[0])
    side = ("down" if abs(lo_v - worst) < 1.0 and abs(hi_v - worst) >= 1.0 else
            "up" if abs(hi_v - worst) < 1.0 and abs(lo_v - worst) >= 1.0 else "both" if abs(hi_v - worst) < 1.0 else "middle")
    out = {"worst": (None if unb_low else round(worst)), "best": (None if unb_high else round(best)),
           "ev": round(float((P * W).sum())), "p_profit": round(float((W * (P >= 0)).sum()) * 100),
           "at_spot": round(float(pnl(np.array([float(spot)]), np.array([float(spot)]))[0])), "worst_side": side}
    out["p_beat"] = round(float((W * (P > float(benchmark) + 0.5)).sum()) * 100) if benchmark is not None else None
    return out


def _order(action: str, l: dict, px: Optional[float], qty: int = 1) -> str:
    word = "put" if l["right"] == "P" else "call"
    return f"{action} {qty}× {_k(l['strike'])} {word} {_date(l.get('expiry'))}" + (f" @ {px:.2f}" if px is not None else "")


def _family_note(kind: str, upside: Optional[float], best: Optional[float], tk: str) -> Optional[str]:
    """One line on what a searched move gives up — from its own numbers."""
    if kind == "lock":
        return ("Together with your spread this is a near-box: the result is pinned to a narrow band (closing, done cheaper — it avoids "
                "buying back the deep in-the-money put, which trades wide and above its parity value). It gives up any recovery. On "
                "expiry day, sell any long option that is in the money instead of letting it exercise, so no shares are left over the weekend.")
    if kind == "overlay" and upside is not None:
        return f"Paid now; better unless {tk} rallies past {_k(upside)} — then it gives back up to the call spread's width."
    if kind == "condor" and upside is not None:
        return f"Buys time AND sells the rally past {_k(upside)}: best case {_usd(best or 0, True)}."
    if kind == "roll":
        return f"Same idea, later: best case {_usd(best or 0, True)}."
    return None


_FAMILY = {
    "lock": "Lock it via the {ow} side — closing, done cheaper",
    "roll": "Roll the spread to a later expiry", "restrike": "Re-strike in the expiry you hold",
    "diagonal": "Diagonal — keep the short, buy later protection", "overlay": "Sell a {ow} spread on the other side (iron condor)",
    "condor": "Roll AND sell a {ow} spread (iron condor later)", "naked": "Sell a naked {ow}",
    "jade": "Jade lizard — sell your long {word} back + a {ow} spread", "fly": "{Word} butterfly", "bwb": "Broken-wing {word} butterfly",
    "bull": "Buy a {ow} spread — a bet on the {move}", "backspread": "{Ow} backspread (1×2)", "calendar": "{Ow} calendar",
    "collar_now": "Collar now (take the shares, call sold today)",
}


def _explore(explored: list, hold: dict, budget: float, word: str, ow: str, put: bool) -> list:
    """Every strategy family the search priced, its best version (within the rule first, then by the odds of losing less, then
    E[P&L]) and, in one line, why it is or isn't the answer — so "did you look at X?" is answered with X's numbers."""
    out = []
    h_beat, h_ev = hold.get("p_beat") or 0, hold.get("ev") or 0
    for kind in _FAMILY:
        pool = [c for c in explored if c["kind"] == kind]
        if not pool:
            continue
        rank = lambda c: (0 if (c["worst"] is not None and c["worst"] >= budget) else 1 if c["worst"] is not None else 2,
                          -(c["p_beat"] or 0), -(c["ev"] or 0), -(c["cash_fill"] or 0), c["days"])     # the same order as the plan
        b = min(pool, key=rank)
        if b["worst"] is None:
            status, why = "unlimited", f"breaks your rule — the loss is open-ended on a big {'rally' if put else 'drop'}"
        elif b["worst"] >= budget:
            status = "rule"
            if (b["p_beat"] or 0) <= h_beat and (b["ev"] or 0) <= h_ev + 5:
                why = "keeps your worst case, but no better than holding"
            elif (b["p_beat"] or 0) >= h_beat + 3 and (b["ev"] or 0) < h_ev - 15:
                why = f"odds {h_beat}% → {b['p_beat']}%, but costs {_usd(h_ev - b['ev'])} of expected value"
            elif (b["p_beat"] or 0) >= h_beat + 3:
                why = f"odds {h_beat}% → {b['p_beat']}% within your rule"
            else:
                why = f"about the same as holding ({b['p_beat']}% vs {h_beat}%)"
        else:
            status = "more"
            why = f"adds {_usd(b['added_risk'])} to the worst case for odds {h_beat}% → {b['p_beat']}%"
        out.append({"family": _FAMILY[kind].format(ow=ow, Ow=ow.capitalize(), word=word, Word=word.capitalize(),
                                                   move="bounce" if put else "drop"),
                    "kind": kind, "tested": len(pool), "best": {k: b[k] for k in ("title", "orders", "cash", "cash_fill", "worst", "best",
                                                                                   "ev", "p_beat", "days", "added_risk")},
                    "status": status, "why": why})
    order = {"rule": 0, "more": 1, "unlimited": 2}
    out.sort(key=lambda x: (order[x["status"]], -(x["best"]["p_beat"] or 0)))
    return out


_FILL = 0.5      # a combo fills ~halfway between mid and the natural — the price every candidate (and closing) is judged at


def risk_budget_search(*, held: dict, anatomy: Optional[dict], near_book: Optional[dict], near_exp: Optional[str], near_dte: int,
                       later: list, spot: float, r: float, vol_of, earn_days: Optional[float] = None,
                       tolerance: float = 5.0, top: int = 3, allowance_frac: float = 0.15, ticker: str = "") -> Optional[dict]:
    """Every move that keeps the ALL-IN worst case at or below what you hold today (money and capital may be added; legs may
    sit in different expiries), ranked by the odds of losing LESS than closing now — the user's rule for a defense.

    Everything — each candidate AND the close it is compared with — is priced at a realistic FILL (halfway from mid to the
    natural): at mids, a near-copy of "close it" (lock a slightly smaller loss) wins on paper by a few dollars that the
    bid/ask then takes back. "Losing less" must also be MEANINGFUL: by at least max($25, 5% of the loss). Families searched:
    (1) the spread rolled to a later expiry at any strikes / width up to 1.5× — and at TWICE the size when the width is half
    (the same notional risk, re-struck nearer); (2) diagonal — keep the short, move the protection to a later, equal-or-closer
    long (if assigned, the shares are kept and the later put floors them — the collar plan); (3) sell the untested side (same
    or later expiry); (4) roll + sell the untested side in the later expiry (an iron condor). Valued by `lattice_eval`; ties
    on odds go to the better E[P&L], then the smaller outlay. Returns the best of each family (top `top`), plus the hold —
    and a second tier, `more_time`: moves that add a SMALL, stated amount to the worst case (≤ `allowance_frac` of the width)
    for more time / better odds (where the user's own "same strikes, Nov 20" roll lands), ranked by odds then the extra risk."""
    legs_h = [dict(l) for l in (held or {}).get("legs") or [] if l.get("right") in ("P", "C")]
    if not legs_h or not spot:
        return None
    for l in legs_h:
        l["dte"] = int(near_dte)
    shorts = [l for l in legs_h if int(l["sign"]) < 0]
    if len(shorts) != 1:
        return None
    sh = shorts[0]
    right, put = sh["right"], sh["right"] == "P"
    longs = [l for l in legs_h if int(l["sign"]) > 0 and l["right"] == right]
    lg = longs[0] if len(longs) == 1 else None
    if len(legs_h) != (2 if lg else 1):
        return None
    n = int(sh.get("qty") or 1)
    collected = float(held.get("collected") or 0.0)
    q_an = {(a["right"], float(a["strike"])): a for a in (anatomy or {}).get("legs") or []}

    tk_ = ticker or "the stock"

    def fill(mid, bid, ask, buy):
        if bid is None or ask is None or ask < bid:
            return mid
        return mid + (_FILL * (ask - mid) if buy else -_FILL * (mid - bid))

    def held_q(l):
        a = q_an.get((l["right"], float(l["strike"]))) or {}
        return float(l.get("mark") or a.get("mid") or 0.0), a.get("bid"), a.get("ask")

    def close_cash(legs, how):              # how: "mid" | "fill" | "nat"
        c = 0.0
        for l in legs:
            mid, bid, ask = held_q(l)
            buy = int(l["sign"]) < 0
            px = mid if how == "mid" else fill(mid, bid, ask, buy) if how == "fill" else ((ask if buy else bid) if bid is not None and ask is not None else mid)
            c += int(l["sign"]) * int(l.get("qty") or n) * _MULT * px
        return c

    close_fill = collected + close_cash(legs_h, "fill")          # the loss closing everything actually locks in
    need = max(25.0, 0.05 * abs(min(close_fill, 0.0)))           # "losing less" has to be worth something
    bench = close_fill + need
    laws = {int(near_dte): market_law(near_book, spot, near_dte, r, vol_of(near_dte))}
    for _e, _d, _b in later:
        laws[int(_d)] = market_law(_b, spot, _d, r, vol_of(_d))
    law_of = lambda d: laws.get(int(d))
    hold_eval = lattice_eval([{**l} for l in legs_h], collected, spot=spot, r=r, vol_of=vol_of, benchmark=bench, law_of=law_of)
    if not hold_eval or hold_eval["worst"] is None:
        return None
    W_ = abs(sh["strike"] - lg["strike"]) if lg else max(5.0, round(0.1 * spot))
    budget = hold_eval["worst"] - tolerance
    loose = hold_eval["worst"] - max(tolerance, allowance_frac * W_ * _MULT * n)
    other = "C" if put else "P"
    S_cache: dict = {}
    iv_cache: dict = {}

    def liquid(book, k, rt):
        q = _q(book, k, rt)
        m = _mid(q)
        return m if (m and _two_sided(q) and (float(q["ask"]) - float(q["bid"])) <= _MAX_SPREAD_PCT * m) else None

    def mk(book, exp, dte, k, rt, sign):
        m = liquid(book, k, rt)
        if m is None:
            return None
        q = _q(book, k, rt)
        if exp not in S_cache:
            S_cache[exp] = implied_spot(book, spot, dte, r) or spot
        key = (exp, float(k), rt)
        if key not in iv_cache:
            iv_cache[key] = leg_iv(book, float(k), rt, S_cache[exp], _T(dte), r, vol_of(dte))
        return {"right": rt, "strike": float(k), "sign": sign, "dte": int(dte), "expiry": exp, "mid": m,
                "bid": float(q["bid"]), "ask": float(q["ask"]), "iv": iv_cache[key]}

    cands = []
    explored: list = []

    titles: set = set()

    def add(kind, title, closes, opens, keeps, note=None, capital=None, qty=None, meta=None, upside=None):
        if title in titles:
            return
        titles.add(title)
        qn = int(qty or n)
        cash_mid = close_cash(closes, "mid")
        cash_fill = close_cash(closes, "fill")
        cash_nat = close_cash(closes, "nat")
        orders = []
        for l in closes:
            mid, bid, ask = held_q(l)
            orders.append(_order("Buy to close" if int(l["sign"]) < 0 else "Sell to close", l, mid, int(l.get("qty") or n)))
        for l in opens:
            buy = int(l["sign"]) > 0
            lq = int(l.get("q") or qn)
            cash_mid += -int(l["sign"]) * lq * _MULT * l["mid"]
            cash_fill += -int(l["sign"]) * lq * _MULT * fill(l["mid"], l["bid"], l["ask"], buy)
            cash_nat += -int(l["sign"]) * lq * _MULT * (l["ask"] if buy else l["bid"])
            orders.append(_order("Buy to open" if buy else "Sell to open", l, l["mid"], lq))
        final = ([{"right": l["right"], "strike": float(l["strike"]), "sign": int(l["sign"]), "qty": int(l.get("qty") or n), "dte": int(l["dte"])} for l in keeps]
                 + [{"right": l["right"], "strike": float(l["strike"]), "sign": int(l["sign"]), "qty": int(l.get("q") or qn), "dte": int(l["dte"])} for l in opens])
        ev_ = lattice_eval(final, collected + cash_fill, spot=spot, r=r, vol_of=vol_of, benchmark=bench, law_of=law_of)
        if not ev_:
            return
        is_same = bool(meta and lg is not None and meta.get("k1") == sh["strike"] and meta.get("k2") == lg["strike"] and meta.get("qty") == n)
        explored.append({"kind": kind, "title": title, "orders": list(orders), "cash": round(cash_mid), "cash_fill": round(cash_fill),
                         "worst": ev_["worst"], "best": ev_["best"], "ev": ev_["ev"], "p_beat": ev_["p_beat"],
                         "days": max(int(l["dte"]) for l in final) if final else 0,
                         "added_risk": (max(0, round(hold_eval["worst"] - ev_["worst"])) if ev_["worst"] is not None else None)})
        if ev_["worst"] is None or (ev_["worst"] < loose and not is_same):
            return
        cands.append({"kind": kind, "title": title, "orders": orders, "cash": round(cash_mid), "cash_fill": round(cash_fill),
                      "cash_natural": round(cash_nat), "worst": ev_["worst"], "best": ev_["best"], "ev": ev_["ev"],
                      "p_beat": ev_["p_beat"], "p_profit": ev_["p_profit"],
                      "days": max(int(l["dte"]) for l in final) if final else 0,
                      "spans_earnings": bool(earn_days is not None and any(int(l["dte"]) >= earn_days for l in final)),
                      "capital": capital, "added_risk": max(0, round(hold_eval["worst"] - ev_["worst"])), "meta": meta,
                      "at_spot": ev_.get("at_spot"), "worst_side": ev_.get("worst_side"),
                      "note": note or _family_note(kind, upside, ev_["best"], tk_),
                      "_closes": [{"right": l["right"], "strike": float(l["strike"]), "sign": int(l["sign"]), "qty": int(l.get("qty") or n),
                                   "iv": (float((q_an.get((l["right"], float(l["strike"]))) or {}).get("iv_pct") or 0) / 100.0) or vol_of(near_dte),
                                   "mark": held_q(l)[0]} for l in closes],
                      "_opens": [{"right": l["right"], "strike": float(l["strike"]), "sign": int(l["sign"]), "qty": int(l.get("q") or qn), "dte": int(l["dte"]),
                                  "iv": l["iv"], "mid": l["mid"], "expiry": l["expiry"]} for l in opens]})

    away = (lambda k: k <= sh["strike"] + 1e-6) if put else (lambda k: k >= sh["strike"] - 1e-6)
    W0 = abs(sh["strike"] - lg["strike"]) if lg else max(5.0, round(0.1 * spot))
    word = "put" if put else "call"
    ow = "call" if put else "put"
    for exp, dte, book in later:
        ks = sorted(k for k in book if liquid(book, k, right) and abs(k / spot - 1.0) <= 0.35)
        for k1 in [k for k in ks if away(k)]:
            for k2 in ks:
                w_ = (k1 - k2) if put else (k2 - k1)
                if not (0 < w_ <= 1.5 * W0 + 1e-6):
                    continue
                a, b = mk(book, exp, dte, k1, right, -1), mk(book, exp, dte, k2, right, +1)
                if not (a and b):
                    continue
                add("roll", f"Roll to {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {word} spread", legs_h, [a, b], [],
                    capital=round(w_ * _MULT * n), meta={"k1": k1, "k2": k2, "exp": exp, "qty": n})
                if w_ <= 0.5 * W0 + 1e-6:                       # the same notional, re-struck nearer: 2× a half-width spread
                    add("roll", f"Roll to {_date(exp)} 2× {_k(k1)}/{_k(k2).lstrip('$')} {word} spread", legs_h, [a, b], [],
                        capital=round(w_ * _MULT * 2 * n), qty=2 * n)
        if lg:
            for k2 in ks:
                if not ((lg["strike"] - 1e-6 <= k2 <= sh["strike"] + 1e-6) if put else (sh["strike"] - 1e-6 <= k2 <= lg["strike"] + 1e-6)):
                    continue
                b = mk(book, exp, dte, k2, right, +1)
                if b:
                    add("diagonal", f"Keep the {_k(sh['strike'])} {word}, move the protection to {_date(exp)} {_k(k2)}", [lg], [b], [sh],
                        note=(f"If the {_k(sh['strike'])} {word} is assigned on {_date(near_exp)}, KEEP the shares — the {_date(exp)} "
                              f"{_k(k2)} {word} floors them; sell both together by {_date(exp)}."),
                        capital=round(sh["strike"] * _MULT * n))
    for exp, dte, book in [(near_exp, near_dte, near_book)] + list(later):
        if not book:
            continue
        sig = vol_of(dte) * math.sqrt(_T(dte))
        ks = sorted(k for k in book if liquid(book, k, other))
        lo = [k for k in ks if (k >= spot * (1 + 0.35 * sig) if put else k <= spot * (1 - 0.35 * sig))]
        for k1 in (lo[:8] if put else lo[-8:]):
            for k2 in ks:
                w_ = (k2 - k1) if put else (k1 - k2)
                if not (0 < w_ <= W0 + 1e-6):
                    continue
                a, b = mk(book, exp, dte, k1, other, -1), mk(book, exp, dte, k2, other, +1)
                if a and b and a["mid"] - b["mid"] >= 0.10:
                    add("overlay", f"Add: sell the {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread", [], [a, b], legs_h,
                        capital=round(w_ * _MULT * n), upside=k1)
    # ── the rest of the strategy library ─────────────────────────────────────────────────────────────────────────
    all_books = [(near_exp, int(near_dte), near_book)] + [(e, int(d_), b) for e, d_, b in later]
    up = (lambda k: k >= spot) if put else (lambda k: k <= spot)                      # the untested (recovery) side
    for exp, dte, book in all_books:
        if not book:
            continue
        sig = vol_of(dte) * math.sqrt(_T(dte))
        oks = sorted(k for k in book if liquid(book, k, other))
        oside = [k for k in oks if up(k) and abs(k / spot - 1.0) <= 0.45]
        oside = oside if put else list(reversed(oside))                               # nearest the money first
        # re-strike inside the SAME expiry you hold (2× a half-width spread nearer the money = the same notional)
        if dte == int(near_dte):
            ks = sorted(k for k in book if liquid(book, k, right) and abs(k / spot - 1.0) <= 0.35)
            for k1 in [k for k in ks if away(k)]:
                for k2 in ks:
                    w_ = (k1 - k2) if put else (k2 - k1)
                    if not (0 < w_ <= 1.5 * W0 + 1e-6) or (abs(k1 - sh["strike"]) < 1e-6 and lg is not None and abs(k2 - lg["strike"]) < 1e-6):
                        continue
                    a, b = mk(book, exp, dte, k1, right, -1), mk(book, exp, dte, k2, right, +1)
                    if a and b:
                        add("restrike", f"Re-strike {_date(exp)} to the {_k(k1)}/{_k(k2).lstrip('$')} {word} spread", legs_h, [a, b], [])
                        if w_ <= 0.5 * W0 + 1e-6:
                            add("restrike", f"Re-strike {_date(exp)} to 2× the {_k(k1)}/{_k(k2).lstrip('$')} {word} spread", legs_h, [a, b], [], qty=2 * n)
        # SELL THE UNTESTED SIDE, wider: the spread is already at max profit past the short strike, so up to ~the width can be
        # risked out there without touching the worst case (the "upside budget")
        for k1 in oside[:10]:
            # a short {ow} spread struck INSIDE your spread (below the short put) doesn't bet on anything — together with the
            # put spread it is a near-BOX: the result is locked whatever the stock does, and buying back a deep ITM put (wide,
            # quoted with "air") is avoided. That is closing, done cheaper — its own family, "lock".
            inside_ = (k1 < sh["strike"] - 1e-6) if put else (k1 > sh["strike"] + 1e-6)
            for k2 in oks:
                w_ = (k2 - k1) if put else (k1 - k2)
                if not (0 < w_ <= 2 * W0 + 1e-6) or (not inside_ and w_ <= W0 + 1e-6):
                    continue
                a, b = mk(book, exp, dte, k1, other, -1), mk(book, exp, dte, k2, other, +1)
                if a and b and a["mid"] - b["mid"] >= 0.10:
                    if inside_ and dte == int(near_dte):
                        add("lock", f"Lock it: sell the {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread", [], [a, b], legs_h,
                            meta={"k1": k1, "k2": k2, "exp": exp, "qty": n, "lock": True})
                    else:
                        add("overlay", f"Add: sell the {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread", [], [a, b], legs_h, upside=k1)
        # a NAKED short on the untested side (unlimited for a call)
        for k1 in oside[:6]:
            a = mk(book, exp, dte, k1, other, -1)
            if a and a["mid"] >= 0.10:
                add("naked", f"Add: sell the {_date(exp)} {_k(k1)} {ow} (naked)", [], [a], legs_h)
        # JADE LIZARD: sell the protective long back and sell an untested-side spread (short put naked + call spread)
        if lg and dte == int(near_dte):
            for k1 in oside[:6]:
                for k2 in oks:
                    w_ = (k2 - k1) if put else (k1 - k2)
                    if not (0 < w_ <= W0 + 1e-6):
                        continue
                    a, b = mk(book, exp, dte, k1, other, -1), mk(book, exp, dte, k2, other, +1)
                    if a and b:
                        add("jade", f"Jade lizard: sell the {_k(lg['strike'])} {word} back + sell the {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread",
                            [lg], [a, b], [sh])
        # A BET ON THE BOUNCE: a debit spread / backspread / calendar on the recovery side
        for k1 in oside[:5]:
            for k2 in oks:
                w_ = (k2 - k1) if put else (k1 - k2)
                if not (0 < w_ <= W0 + 1e-6):
                    continue
                a, b = mk(book, exp, dte, k1, other, +1), mk(book, exp, dte, k2, other, -1)
                if a and b:
                    add("bull", f"Add: buy the {_date(exp)} {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread (a bet on the {'bounce' if put else 'drop'})", [], [a, b], legs_h)
                s1, b2 = mk(book, exp, dte, k1, other, -1), mk(book, exp, dte, k2, other, +1)
                if s1 and b2:
                    b2 = dict(b2, q=2 * n)
                    add("backspread", f"Add: {ow} backspread — sell 1× {_k(k1)}, buy 2× {_k(k2)} ({_date(exp)})", [], [s1, b2], legs_h)
        # PUT BUTTERFLIES (same expiry you hold), centred where the stock could pin: a long fly and a broken-wing fly
        if dte == int(near_dte):
            pks = sorted(k for k in book if liquid(book, k, right))
            for c in [k for k in pks if abs(k / spot - 1.0) <= 0.15]:
                for wing in (2.5, 5.0):
                    hi_k, lo_k = c + wing, c - wing
                    lo_b = c - 2 * wing if put else c + 2 * wing
                    a, m_, z_ = mk(book, exp, dte, hi_k, right, +1), mk(book, exp, dte, c, right, -1), mk(book, exp, dte, lo_k, right, +1)
                    if a and m_ and z_:
                        add("fly", f"Add: {_date(exp)} {_k(lo_k)}/{_k(c).lstrip('$')}/{_k(hi_k).lstrip('$')} {word} butterfly", [], [a, dict(m_, q=2 * n), z_], legs_h)
                    zb = mk(book, exp, dte, lo_b, right, +1)
                    if a and m_ and zb:
                        add("bwb", f"Add: {_date(exp)} {_k(min(lo_b, hi_k))}/{_k(c).lstrip('$')}/{_k(max(lo_b, hi_k)).lstrip('$')} broken-wing {word} butterfly",
                            [], [a, dict(m_, q=2 * n), zb], legs_h)
    # a CALENDAR on the recovery side: sell the near call, buy the same strike later
    for exp, dte, book in later[:2]:
        for k1 in [k for k in sorted(near_book or {}) if up(k) and abs(k / spot - 1.0) <= 0.2][:4]:
            a, b = mk(near_book, near_exp, int(near_dte), k1, other, -1), mk(book, exp, dte, k1, other, +1)
            if a and b:
                add("calendar", f"Add: {_k(k1)} {ow} calendar — sell {_date(near_exp)}, buy {_date(exp)}", [], [a, b], legs_h)
    # the collar the user described, with the call sold NOW (before the shares arrive)
    if lg:
        for exp, dte, book in later[:2]:
            b_, c_ = mk(book, exp, dte, lg["strike"], right, +1), mk(book, exp, dte, sh["strike"], other, -1)
            if b_ and c_:
                add("collar_now", f"Collar now: roll the {_k(lg['strike'])} {word} to {_date(exp)} and sell the {_date(exp)} {_k(sh['strike'])} {ow} today",
                    [lg], [b_, c_], [sh])

    rolls = sorted((c for c in cands if c["kind"] == "roll"), key=lambda c: (-(c["p_beat"] or 0), -c["ev"]))[:6]
    for c in rolls:
        o = c["_opens"]
        exp_dte, exp = o[0]["dte"], o[0]["expiry"]
        book = next((b for e, d, b in later if e == exp), None)
        if not book:
            continue
        sig = vol_of(exp_dte) * math.sqrt(_T(exp_dte))
        ks = sorted(k for k in book if liquid(book, k, other))
        for m_ in (0.5, 1.0):
            k1 = (next((k for k in ks if k >= spot * (1 + m_ * sig)), None) if put
                  else next((k for k in reversed(ks) if k <= spot * (1 - m_ * sig)), None))
            if k1 is None:
                continue
            for w_ in (W0 / 2, W0):
                k2 = k1 + w_ if put else k1 - w_
                a, b = mk(book, exp, exp_dte, k1, other, -1), mk(book, exp, exp_dte, k2, other, +1)
                ra = [mk(book, exp, exp_dte, x["strike"], x["right"], x["sign"]) for x in o]
                if a and b and all(ra) and int(o[0]["qty"]) == n:
                    add("condor", c["title"] + f" + sell the {_k(k1)}/{_k(k2).lstrip('$')} {ow} spread", legs_h, ra + [a, b], [],
                        capital=c["capital"], upside=k1)
    def pick(pool, k):
        out_, seen_ = [], set()
        for c in pool:
            if c["kind"] not in seen_:
                out_.append(c)
                seen_.add(c["kind"])
        return out_[:k]

    same_all = [c for c in cands if (c.get("meta") or {}).get("k1") == sh["strike"] and lg is not None
                and (c.get("meta") or {}).get("k2") == lg["strike"]]
    cands = [c for c in cands if c["worst"] >= loose]
    inside = [c for c in cands if c["worst"] >= budget]
    ranked = sorted(inside, key=lambda c: (-(c["p_beat"] or 0), -c["ev"], -c["cash_fill"], c["days"]))
    best = sorted(pick(ranked, top), key=lambda c: (-(c["p_beat"] or 0), -c["ev"]))
    h_beat = hold_eval["p_beat"] or 0
    more = sorted((c for c in cands if c["worst"] < budget and (c["p_beat"] or 0) > h_beat),
                  key=lambda c: (-(c["p_beat"] or 0), c["added_risk"], -c["ev"]))
    more_time = pick(more, 2)
    # the user's own first instinct, always answered: the SAME strikes, later (first monthly preferred)
    same_pick = (next((c for c in same_all if _monthly_like((c.get("meta") or {}).get("exp") or "")), None) or
                 (max(same_all, key=lambda c: c["p_beat"] or 0) if same_all else None))
    if same_pick is not None and (same_pick in best or same_pick in more_time):
        same_pick = None
    hold_row = {"kind": "hold", "worst": hold_eval["worst"], "best": hold_eval["best"], "ev": hold_eval["ev"],
                "p_beat": hold_eval["p_beat"], "p_profit": hold_eval["p_profit"]}
    explorer = _explore(explored, hold_eval, budget, word, ow, put)
    return {"rule_worst": hold_eval["worst"], "close_fill": round(close_fill), "close_mid": round(collected + close_cash(legs_h, "mid")),
            "need": round(need), "hold": hold_row, "best": best, "more_time": more_time, "searched": len(explored),
            "explorer": explorer,
            "same_strikes": same_pick,
            "allowance": round(hold_eval["worst"] - loose),
            "beats_hold": [c for c in best if (c["p_beat"] or 0) >= (hold_eval["p_beat"] or 0) + 3]}


# ── HOW THIS STOCK ACTUALLY MOVES ON EARNINGS ─────────────────────────────────────────────────────────────────────
def earnings_history(releases: list, bars: list, implied_1sd_pct: Optional[float]) -> Optional[dict]:
    """The stock's REAL reaction to its last prints vs what the options price for the next one.

    releases: EDGAR acceptance timestamps (UTC ISO) of the earnings 8-Ks; bars: [(date_iso, open, close)] daily, oldest first.
    The reaction is measured close-to-close across the release: after-the-close (accepted after 4pm ET) → the release day's
    close to the next session's close; pre-market → the prior close to that day's close. Compared with the market's 1σ event
    move for the next print (expected ABSOLUTE move = 1σ·√(2/π)): history that moves more than priced says selling across the
    print is under-paid; less, that it has been paid. Returns None with fewer than 4 measurable prints."""
    import datetime as _d
    if not releases or not bars:
        return None
    idx = {b[0]: i for i, b in enumerate(bars)}
    dates = [b[0] for b in bars]
    rows = []
    for acc in releases:
        try:
            t = _d.datetime.fromisoformat(str(acc).replace("Z", "+00:00"))
        except ValueError:
            continue
        et = t - _d.timedelta(hours=4 if 3 <= t.month <= 10 else 5)            # ET (DST approximated by month)
        day = et.date().isoformat()
        amc = et.hour >= 16
        pre = et.hour < 9 or (et.hour == 9 and et.minute < 30)
        # first trading session ON/AFTER the release day
        j = next((i for i, dd in enumerate(dates) if dd >= day), None)
        if j is None:
            continue
        if amc:
            if dates[j] != day or j + 1 >= len(bars):
                continue
            a, b = bars[j][2], bars[j + 1][2]
            gap = bars[j + 1][1] / a - 1.0
            when = dates[j + 1]
        else:
            if j == 0:
                continue
            a, b = bars[j - 1][2], bars[j][2]
            gap = bars[j][1] / a - 1.0
            when = dates[j]
        rows.append({"release": day, "session": when, "move_pct": round((b / a - 1.0) * 100, 1), "gap_pct": round(gap * 100, 1),
                     "timing": "after close" if amc else ("pre-market" if pre else "intraday")})
    if len(rows) < 4:
        return None
    absm = sorted(abs(r["move_pct"]) for r in rows)
    mean_abs = sum(absm) / len(absm)
    med = absm[len(absm) // 2] if len(absm) % 2 else 0.5 * (absm[len(absm) // 2 - 1] + absm[len(absm) // 2])
    out = {"prints": rows, "n": len(rows), "mean_abs_pct": round(mean_abs, 1), "median_abs_pct": round(med, 1),
           "max_abs_pct": round(absm[-1], 1), "ups": sum(1 for r in rows if r["move_pct"] > 0),
           "implied_1sd_pct": implied_1sd_pct, "implied_abs_pct": None, "ratio": None, "read": None, "beyond_1sd": None}
    if implied_1sd_pct:
        ia = float(implied_1sd_pct) * math.sqrt(2.0 / math.pi)
        out["implied_abs_pct"] = round(ia, 1)
        out["ratio"] = round(mean_abs / ia, 2) if ia > 0 else None
        out["beyond_1sd"] = sum(1 for m in absm if m > float(implied_1sd_pct))
        rt = out["ratio"] or 1.0
        rm = med / ia if ia > 0 else 1.0
        big = sum(1 for m in absm if m > 2.0 * float(implied_1sd_pct))
        out["med_ratio"], out["violent"] = round(rm, 2), big
        if rm <= 0.85 and (rt >= 1.15 or big >= 2):
            out["read"] = (f"usually moves LESS than priced, but with violent outliers ({big} of {len(absm)} beyond ±{2 * float(implied_1sd_pct):.0f}%) — "
                           "a short across the print is paid most quarters and badly hurt in a few")
        elif rt >= 1.15 and rm >= 1.0:
            out["read"] = "moves MORE than the options price — selling across the print has been under-paid"
        elif rt <= 0.85 and rm <= 0.85:
            out["read"] = "moves LESS than the options price — selling across the print has been paid"
        else:
            out["read"] = "moves about as much as the options price — no edge either way"
    return out


# ── WHERE OPEN INTEREST WILL PULL INTO EXPIRY ─────────────────────────────────────────────────────────────────────
def pin_read(book: Optional[dict], spot: float, lo: Optional[float] = None, hi: Optional[float] = None,
             sigma_move: Optional[float] = None) -> Optional[dict]:
    """Open interest at the trade's own expiry: the strikes holding the most contracts (where hedging flows tend to pin a
    stock into the close) and MAX PAIN — the settlement price that pays option holders the least. A read on where the
    stock is likely to be pulled, not a forecast; matters most for the pin zone between a spread's strikes."""
    if not book or not spot:
        return None
    oi = {}
    for k, row in book.items():
        p_, c_ = int((row.get("P") or {}).get("oi") or 0), int((row.get("C") or {}).get("oi") or 0)
        if p_ + c_ > 0:
            oi[float(k)] = (p_, c_)
    if len(oi) < 3 or sum(p + c for p, c in oi.values()) < 200:
        return None
    ks = sorted(oi)
    near = [k for k in ks if abs(k / spot - 1.0) <= 0.25]

    def pain(x):
        return sum(p * max(0.0, k - x) + c * max(0.0, x - k) for k, (p, c) in oi.items())
    mp = min(near or ks, key=pain)
    top = sorted(near or ks, key=lambda k: -(oi[k][0] + oi[k][1]))[:3]
    plausible = sigma_move is None or abs(mp / spot - 1.0) <= float(sigma_move)
    out = {"max_pain": (mp if plausible else None), "top": [{"strike": k, "puts": oi[k][0], "calls": oi[k][1]} for k in top]}
    tops = ", ".join(f"{_k(t['strike'])} ({t['puts'] + t['calls']:,})" for t in out["top"])
    zone = (f" — inside your {_k(lo)}–{_k(hi)} pin zone" if plausible and lo is not None and hi is not None and lo <= mp <= hi else "")
    out["text"] = f"Open interest into expiry: heaviest at {tops}" + (f"; max pain {_k(mp)}{zone}." if plausible else ".")
    return out
