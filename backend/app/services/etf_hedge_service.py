"""ETF hedges for the Income book — "buy a put on the nearest ETF, sell a call to recover some of the cost".

A book of single-name short premium is mostly a bet on a handful of THEMES (semis, software, small caps …). An index put
hedges it badly (basis risk); the ETF that actually moves with those names hedges it well — but a SINGLE name tracks any
ETF loosely (idiosyncratic noise), while a BASKET of similar names tracks it tightly. So this module

  1. GROUPS similar names: average-linkage clustering on measured 1-year daily correlation, with names that share a
     registry theme (AI data-center, semis, software …) pulled a little closer;
  2. matches each GROUP, as a capital-weighted basket, to the ETF it tracks best (liquid, optionable universe) — groups that
     land on the same ETF are merged — and reports the basket's correlation next to each member's own;
  3. reprices each group (full Black-Scholes, the same engine as the stress tables) when its ETF falls 5…30%: members move
     β-to-ETF × the ETF move;
  4. builds the hedge from the LIVE ETF option chain: a protective put (~8% OTM, ~60d) sized to offset a target share of
     the group's loss at a −15% ETF move, in four funding styles —
         put only · put + sell an OTM call (≈50% funded) · put + sell a call (≈zero-cost collar) · put spread (sell a lower put)
     each priced at what you would really pay (put at the ask, short leg at the bid), annualised, and compared with income;
  5. shows what each variant does in every ETF scenario — including the RALLY scenarios, because a sold call is a
     short call: it is covered only if you hold the ETF, otherwise it adds upside risk to a book that is already short calls.

Pure pricing (`build_etf_hedges`) is separated from I/O (`group_names_to_etfs_sync`, `fetch_etf_static`) so it is testable.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date
from typing import Optional

logger = logging.getLogger(__name__)

ETF_UNIVERSE: dict[str, str] = {
    "SPY": "S&P 500", "QQQ": "Nasdaq-100", "IWM": "Russell 2000 small caps",
    "SMH": "Semiconductors", "SOXX": "Semiconductors (iShares)", "IGV": "Software", "XLK": "Technology",
    "XLF": "Financials", "KRE": "Regional banks", "XLE": "Energy", "XLV": "Health care", "XBI": "Biotech",
    "XLY": "Consumer discretionary", "XLP": "Consumer staples", "XLI": "Industrials", "XLU": "Utilities",
    "XLC": "Communication services", "XRT": "Retail", "ARKK": "High-beta innovation", "GDX": "Gold miners",
}
# An index / index-ETF holding maps to its liquid ETF twin.
_ALIAS = {"XSP": "SPY", "SPXW": "SPY", "SPX": "SPY", "^SPX": "SPY", "^GSPC": "SPY", "NDX": "QQQ", "^NDX": "QQQ", "RUT": "IWM", "^RUT": "IWM"}

_MOVES = (-0.30, -0.20, -0.15, -0.10, -0.05, 0.10, 0.20)      # ETF scenarios, down and up
_REF_MOVE = -0.15                                            # the move a hedge is sized against
_MULT = 100


# ── I/O: group similar names, then match each group to its ETF ───────────────
def _group_label(names: list[str], etf: str) -> str:
    """The registry theme most of the group shares (e.g. "AI data-center buildout"), else the ETF's own description."""
    try:
        from .correlated_assets_service import themes_for, _THEMES
        counts: dict[str, int] = {}
        for n in names:
            for k in themes_for(n):
                counts[k] = counts.get(k, 0) + 1
        if counts:
            k, c = max(counts.items(), key=lambda kv: kv[1])
            if c >= max(2, math.ceil(0.4 * len(names))):             # a theme at least ~40% of the group rides
                return str(_THEMES[k]["label"])
    except Exception:  # noqa: BLE001
        pass
    return ETF_UNIVERSE.get(etf, etf)


def _cluster_names(names: list[str], corr, themes: dict[str, set], cut: float, theme_bonus: float) -> list[list[str]]:
    """Average-linkage clusters on distance = 1 − ρ, minus a small bonus for a shared registry theme."""
    if len(names) <= 1:
        return [list(names)] if names else []
    import numpy as np
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import squareform
    D = 1.0 - corr.loc[names, names].fillna(0.0).values
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j and themes.get(a) and themes.get(b) and (themes[a] & themes[b]):
                D[i, j] = max(0.0, D[i, j] - theme_bonus)
    np.fill_diagonal(D, 0.0)
    D = (D + D.T) / 2.0
    labels = fcluster(linkage(squareform(D, checks=False), method="average"), t=cut, criterion="distance")
    out: dict[int, list[str]] = {}
    for n, l in zip(names, labels):
        out.setdefault(int(l), []).append(n)
    return list(out.values())


def group_names_to_etfs_sync(tickers: list[str], weights: Optional[dict] = None, *, min_rho: float = 0.45,
                             cut: float = 0.55, theme_bonus: float = 0.10) -> dict:
    """Group similar names, then match each GROUP (as a capital-weighted basket) to the ETF it tracks best.

    Returns {"members": {ticker: {etf, rho, beta, alts, self, group}}, "groups": {etf: {names, basket_rho, basket_beta,
    alts, label}}, "unmatched": [tickers]}. A name that IS an ETF in the universe (or an index alias such as XSP→SPY) joins
    the group of its own ETF. A group whose basket correlates below `min_rho` with every ETF is unmatched, not forced."""
    empty = {"members": {}, "groups": {}, "unmatched": []}
    names = [t for t in dict.fromkeys(tickers) if t]
    if not names:
        return empty
    weights = weights or {}
    lookup = {t: _ALIAS.get(t, t) for t in names}
    selfs = {t: lookup[t] for t in names if lookup[t] in ETF_UNIVERSE}
    rest = [t for t in names if t not in selfs and not lookup[t].startswith("^")]
    members: dict[str, dict] = {}
    out_groups: dict[str, dict] = {}
    assign: dict[str, list[str]] = {}
    unmatched: list[str] = []
    rets = None
    if rest:
        try:
            import pandas as pd
            import yfinance as yf
            from .correlated_assets_service import themes_for
            syms = list(dict.fromkeys([lookup[t] for t in rest] + list(ETF_UNIVERSE)))
            data = yf.download(syms, period="1y", progress=False, auto_adjust=True)["Close"]
            if data is not None and not getattr(data, "empty", True):
                rets = data.pct_change()
        except Exception as exc:  # noqa: BLE001 — best effort: no mapping simply means no ETF hedge
            logger.info("etf-hedge: download failed: %s", exc)
    if rets is not None:
        avail = [t for t in rest if lookup[t] in rets.columns]
        unmatched += [t for t in rest if t not in avail]
        sym_of = {t: lookup[t] for t in avail}
        r_names = rets[[sym_of[t] for t in avail]].copy()
        r_names.columns = avail
        corr = r_names.corr(min_periods=60)
        themes = {t: set(themes_for(t)) for t in avail}
        for cl in _cluster_names(avail, corr, themes, cut, theme_bonus):
            basket = _basket(r_names, cl, weights)
            best = _best_etf(basket, rets)
            if best and best[0] >= min_rho:
                assign.setdefault(best[1], []).extend(cl)
            else:
                unmatched += cl
        etf_ret = {e: rets[e] for e in ETF_UNIVERSE if e in rets.columns}
        for etf, mem in assign.items():
            basket = _basket(r_names, mem, weights)
            rho_b, beta_b = _rho_beta(basket, etf_ret.get(etf))
            alts = [{"etf": e, "rho": round(r, 2)} for r, e in _rank_etfs(basket, rets) if e != etf][:3]
            for t in mem:
                rho, beta = _rho_beta(r_names[t], etf_ret.get(etf))
                members[t] = {"etf": etf, "rho": round(rho if rho is not None else 0.0, 2),
                              "beta": round(min(3.0, max(0.2, beta if beta is not None else 1.0)), 2), "alts": [], "self": False}
            out_groups[etf] = {"names": sorted(mem), "basket_rho": round(rho_b or 0.0, 2), "basket_beta": round(beta_b or 1.0, 2), "alts": alts}
    for t, e in selfs.items():                                   # an ETF held in the book hedges with itself
        members[t] = {"etf": e, "rho": 1.0, "beta": 1.0, "alts": [], "self": True}
        g = out_groups.setdefault(e, {"names": [], "basket_rho": 1.0, "basket_beta": 1.0, "alts": []})
        g["names"] = sorted(set(g["names"]) | {t})
    for e, g in out_groups.items():
        g["label"] = _group_label(g["names"], e)
        for t in g["names"]:
            if t in members:
                members[t]["group"] = e
    return {"members": members, "groups": out_groups, "unmatched": sorted(unmatched)}


def _basket(r_names, cl: list[str], weights: dict):
    """Capital-weighted daily return of the cluster (equal weights when no capital is known)."""
    w = [float(weights.get(t) or 0.0) for t in cl]
    tot = sum(w)
    w = [x / tot for x in w] if tot > 0 else [1.0 / len(cl)] * len(cl)
    return sum(r_names[t].fillna(0.0) * wi for t, wi in zip(cl, w))


def _rho_beta(a, e) -> tuple[Optional[float], Optional[float]]:
    if e is None:
        return None, None
    import pandas as pd
    pair = pd.concat([a, e], axis=1).dropna()
    pair.columns = ["a", "e"]
    if len(pair) < 60:
        return None, None
    var_e = float(pair["e"].var())
    rho = float(pair["a"].corr(pair["e"]))
    if var_e <= 0 or not math.isfinite(rho):
        return None, None
    return rho, float(pair["a"].cov(pair["e"])) / var_e


def _rank_etfs(basket, rets) -> list[tuple[float, str]]:
    out = []
    for e in ETF_UNIVERSE:
        if e in rets.columns:
            rho, _ = _rho_beta(basket, rets[e])
            if rho is not None:
                out.append((rho, e))
    return sorted(out, reverse=True)


def _best_etf(basket, rets) -> Optional[tuple[float, str]]:
    ranked = _rank_etfs(basket, rets)
    return ranked[0] if ranked else None


def _pick_expiry(exps: list, today: date, target: int = 60, min_dte: int = 28) -> Optional[tuple[str, int]]:
    best = None
    for e in exps or []:
        try:
            dte = (date.fromisoformat(str(e)[:10]) - today).days
        except (ValueError, TypeError):
            continue
        if dte < min_dte:
            continue
        if best is None or abs(dte - target) < abs(best[1] - target):
            best = (str(e)[:10], dte)
    return best


async def fetch_etf_static(positions: list[dict], provider, today: date, *, hedge_dte: int = 60, top: int = 3) -> Optional[dict]:
    """Everything the pricing needs from the network, fetched once: the name→ETF mapping and, for the ETFs that carry the
    most book capital, the spot and one live option chain each. None when nothing usable came back."""
    from .book_tail_risk import _quote_dict
    from .hedging_service import _split_chain
    tickers = sorted({p["ticker"] for p in positions})
    weights: dict[str, float] = {}
    for p in positions:
        weights[p["ticker"]] = weights.get(p["ticker"], 0.0) + (p.get("capital") or 0.0)
    grouped = await asyncio.to_thread(group_names_to_etfs_sync, tickers, weights)
    mapping = grouped["members"]
    if not mapping:
        return None
    cap: dict[str, float] = {}
    for p in positions:
        m = mapping.get(p["ticker"])
        if m:
            cap[m["etf"]] = cap.get(m["etf"], 0.0) + (p.get("capital") or 0.0)
    etfs: dict[str, dict] = {}
    for etf in sorted(cap, key=cap.get, reverse=True)[: top + 1]:
        try:
            spot = float(getattr(await provider.get_underlying_price(etf), "price", 0.0) or 0.0)
            pick = _pick_expiry(await provider.get_option_expirations(etf), today, hedge_dte)
            if spot <= 0 or not pick:
                continue
            chain = await provider.get_option_chain(etf, pick[0])
            calls, puts = _split_chain(chain)
            etfs[etf] = {"spot": spot, "expiry": pick[0], "dte": pick[1],
                         "puts": {float(k): _quote_dict(q) for k, q in puts.items()},
                         "calls": {float(k): _quote_dict(q) for k, q in calls.items()},
                         "indicative": "indicative" in str(getattr(chain, "source", "") or "")}
        except Exception as exc:  # noqa: BLE001
            logger.info("etf-hedge: chain for %s failed: %s", etf, exc)
    return {"mapping": mapping, "groups": grouped["groups"], "unmatched": grouped["unmatched"], "etfs": etfs} if etfs else None


# ── pure pricing ─────────────────────────────────────────────────────────────
def _buy_px(q: Optional[dict]) -> Optional[float]:
    if not q:
        return None
    if (q.get("ask") or 0) > 0:
        return float(q["ask"])
    if (q.get("mid") or 0) > 0:
        return float(q["mid"])
    return None


def _sell_px(q: Optional[dict]) -> Optional[float]:
    if not q:
        return None
    if (q.get("bid") or 0) > 0:
        return float(q["bid"])
    if (q.get("mid") or 0) > 0:
        return float(q["mid"]) * 0.97            # no bid: assume giving up a little to the mid
    return None


def _mid_px(q: Optional[dict]) -> Optional[float]:
    if not q:
        return None
    for k in ("mid", "last"):
        if (q.get(k) or 0) > 0:
            return float(q[k])
    if (q.get("ask") or 0) > 0 and (q.get("bid") or 0) > 0:
        return (q["ask"] + q["bid"]) / 2
    return None


def _usable(q: Optional[dict], S: float, K: float, T: float, r: float, right: str) -> bool:
    """A strike worth trading: it has a price, some open interest / volume (when the feed says), and a price that implies a
    plausible vol — a stale last trade on a far-OTM strike (a $3 call struck at 2× spot) fails the vol check."""
    from .book_tail_risk import _solve_iv
    if not q:
        return False
    oi, vol = q.get("oi"), q.get("volume")
    if oi is not None and vol is not None and (oi or 0) < 20 and (vol or 0) < 5:
        return False
    px = _mid_px(q)
    if not px:
        return False
    iv = _solve_iv(px, S, K, T, r, "call" if right == "C" else "put")
    return iv is not None and 0.08 <= iv <= 1.6


def _nearest(strikes: list[float], target: float) -> Optional[float]:
    return min(strikes, key=lambda k: abs(k - target)) if strikes else None


def build_etf_hedges(positions: list[dict], static: Optional[dict], r: float, annual_income: float, tail_side: Optional[str],
                     *, target_offset: float = 0.60, budget_pct: float = 10.0, top: int = 3) -> Optional[dict]:
    """The ETF hedge view for the current book — see the module docstring. No I/O."""
    from .book_tail_risk import reprice_scenario, _vol_shock_for, _sane_iv
    if not static or not static.get("etfs"):
        return None
    mapping, etfs = static["mapping"], static["etfs"]
    income_ok = annual_income and annual_income > 0
    cands, skipped = [], []

    for etf, st in etfs.items():
        members = [p for p in positions if (mapping.get(p["ticker"]) or {}).get("etf") == etf]
        if not members:
            continue
        S, dte = st["spot"], st["dte"]
        T, rolls = dte / 365.0, 365.0 / max(dte, 1)
        beta_of = {p["ticker"]: mapping[p["ticker"]]["beta"] for p in members}

        def grp(m, subset=None):
            mem = [{**p, "beta": beta_of[p["ticker"]]} for p in (subset or members)]
            return reprice_scenario(mem, m, _vol_shock_for(m), r)

        group = {m: grp(m) for m in _MOVES}
        loss_ref = -group[_REF_MOVE]
        if loss_ref <= 0:
            continue                                  # the group does not lose when this ETF falls — nothing to hedge here
        names_loss = []
        for tk in sorted({p["ticker"] for p in members}):
            sub = [p for p in members if p["ticker"] == tk]
            names_loss.append({"ticker": tk, "rho": mapping[tk]["rho"], "beta": mapping[tk]["beta"], "loss_ref": round(max(0.0, -grp(_REF_MOVE, sub)), 0)})
        names_loss.sort(key=lambda n: -n["loss_ref"])
        grp = (static.get("groups") or {}).get(etf) or {}
        wsum = sum(n["loss_ref"] for n in names_loss)
        member_avg = (sum(n["rho"] * n["loss_ref"] for n in names_loss) / wsum) if wsum > 0 else sum(n["rho"] for n in names_loss) / len(names_loss)
        avg_rho = grp.get("basket_rho") or member_avg          # the BASKET's tracking of the ETF (diversified), not the loose single names'
        strength = "strong" if avg_rho >= 0.8 else "good" if avg_rho >= 0.65 else "loose" if avg_rho >= 0.5 else "weak"
        rhos = [n["rho"] for n in names_loss]

        puts = {k: q for k, q in st["puts"].items() if _usable(q, S, k, T, r, "P")}
        calls = {k: q for k, q in st["calls"].items() if k <= S * 1.30 and _usable(q, S, k, T, r, "C")}
        pk = _nearest([k for k, q in puts.items() if k < S and _buy_px(q)], S * 0.92)
        if pk is None:
            continue
        put_q = puts[pk]
        put_ask, put_mid = _buy_px(put_q), _mid_px(put_q) or _buy_px(put_q)
        iv_p = _sane_iv(put_q, S, pk, T, r, "P", 0.25)[0]

        def leg(strike, right, sign, qty, q):
            return {"strike": strike, "right": right, "sign": sign, "qty": qty, "dte_years": T,
                    "iv": _sane_iv(q, S, strike, T, r, right, 0.25)[0]}

        def variant(key, label, short_leg):         # short_leg: ("C"|"P", strike, quote) | None
            legs1 = [leg(pk, "P", +1, 1, put_q)]
            credit_bid = credit_mid = 0.0
            if short_leg:
                right, sk, sq = short_leg
                sp = _sell_px(sq)
                if sp is None:
                    return None
                legs1.append(leg(sk, right, -1, 1, sq))
                credit_bid, credit_mid = sp, (_mid_px(sq) or sp)
            epos1 = {"ticker": etf, "spot": S, "beta": 1.0, "legs": legs1}
            gain1 = reprice_scenario([epos1], _REF_MOVE, _vol_shock_for(_REF_MOVE), r)
            if gain1 <= 0:
                return None
            n = max(1, int(round(target_offset * loss_ref / gain1)))
            epos = {**epos1, "legs": [{**l, "qty": n} for l in legs1]}
            net_debit = n * _MULT * (put_ask - credit_bid)
            exec_cost = n * _MULT * ((put_ask - (put_mid or put_ask)) + ((credit_mid - credit_bid) if short_leg else 0.0))
            unit_annual = _MULT * (put_ask - credit_bid) * rolls
            annual = max(net_debit, 0.0) * rolls
            scen = []
            for m in _MOVES:
                h = reprice_scenario([epos], m, _vol_shock_for(m), r) - exec_cost
                scen.append({"move_pct": round(m * 100), "group_pnl": round(group[m], 0), "hedge_pnl": round(h, 0), "net": round(group[m] + h, 0)})
            ref = next(x for x in scen if x["move_pct"] == round(_REF_MOVE * 100))
            up = [x for x in scen if x["move_pct"] > 0]
            has_short_call = bool(short_leg and short_leg[0] == "C")
            n_budget = int(((annual_income or 0) * budget_pct / 100.0) // unit_annual) if (income_ok and unit_annual > 0) else None
            return {"key": key, "label": label, "qty": n, "put": {"strike": pk, "ask": round(put_ask, 2), "iv": round(iv_p, 3)},
                    "short": ({"right": short_leg[0], "strike": short_leg[1], "bid": round(credit_bid, 2)} if short_leg else None),
                    "net_debit": round(net_debit, 0), "is_credit": net_debit < 0, "annual_cost": round(annual, 0),
                    "cost_pct_income": round(annual / annual_income * 100, 1) if income_ok else None,
                    "within_budget": bool(income_ok and annual / annual_income * 100 <= budget_pct),
                    "n_within_budget": n_budget,
                    "funded_pct": round(credit_bid / put_ask * 100, 0) if put_ask else 0.0,
                    "offset_pct": round(ref["hedge_pnl"] / loss_ref * 100, 0),
                    "net_at_ref": ref["net"], "scenarios": scen,
                    "adds_upside_risk": has_short_call and any(x["hedge_pnl"] < 0 for x in up),
                    "upside_given_up": round(min((x["hedge_pnl"] for x in up), default=0.0), 0) if has_short_call else 0.0,
                    "tail_is_upside": bool(has_short_call and tail_side == "up")}

        def call_for(fund):                           # an OTM call whose bid ≈ fund × the put's ask (≥ 3% OTM)
            cs = [k for k, q in calls.items() if k >= S * 1.03 and _sell_px(q)]
            if not cs:
                return None
            k = min(cs, key=lambda c: abs(_sell_px(calls[c]) - fund * put_ask))
            return ("C", k, calls[k])

        sp_k = _nearest([k for k, q in puts.items() if k < pk * 0.97 and _sell_px(q)], pk * 0.90)
        vs = [variant("put", "Put only", None),
              variant("collar50", "Put + sell a call (≈50% funded)", call_for(0.5)),
              variant("collar100", "Put + sell a call (≈zero-cost collar)", call_for(1.0)),
              variant("putspread", "Put spread (sell a lower put)", ("P", sp_k, puts[sp_k]) if sp_k else None)]
        vs = [v for v in vs if v]
        if not vs:
            continue
        if min(v["offset_pct"] for v in vs) > 200:
            skipped.append({"etf": etf, "label": ETF_UNIVERSE.get(etf, etf), "loss_ref": round(loss_ref, 0),
                            "reason": f"the group only loses ~${loss_ref:,.0f} at a {round(_REF_MOVE * 100)}% ETF move — less than ONE option contract's protection"})
            continue
        cands.append({"etf": etf, "label": ETF_UNIVERSE.get(etf, etf), "group_label": grp.get("label") or ETF_UNIVERSE.get(etf, etf),
                      "member_rho_min": min(rhos), "member_rho_max": max(rhos), "spot": round(S, 2), "expiry": st["expiry"], "dte": dte,
                      "indicative": bool(st.get("indicative")), "members": names_loss, "n_members": len(names_loss),
                      "avg_rho": round(float(avg_rho), 2), "strength": strength, "loss_ref": round(loss_ref, 0), "ref_move": round(_REF_MOVE * 100),
                      "capital": round(sum(p.get("capital") or 0.0 for p in members), 0),
                      "theta": round(sum(p.get("net_theta") or 0.0 for p in members), 1),
                      "group_pnl": {str(round(m * 100)): round(v, 0) for m, v in group.items()}, "variants": vs})
    if not cands:
        return {"candidates": [], "skipped": skipped, "target_offset_pct": round(target_offset * 100), "budget_pct": budget_pct,
                "mapping": [{"ticker": t, **{k: v for k, v in m.items() if k != "self"}, "self": bool(m.get("self"))} for t, m in sorted(mapping.items())],
                "groups": [{"etf": e, "label": g.get("label"), "names": g["names"], "basket_rho": g["basket_rho"], "alts": g.get("alts", [])} for e, g in (static.get("groups") or {}).items()],
                "unmapped": sorted({p["ticker"] for p in positions if p["ticker"] not in mapping} | set(static.get("unmatched") or []))} if (skipped or mapping) else None
    cands.sort(key=lambda c: -c["loss_ref"])
    unmapped = sorted({p["ticker"] for p in positions if p["ticker"] not in mapping} | set(static.get("unmatched") or []))
    groups_out = [{"etf": e, "label": g.get("label"), "names": g["names"], "basket_rho": g["basket_rho"], "alts": g.get("alts", [])}
                  for e, g in sorted((static.get("groups") or {}).items(), key=lambda kv: -len(kv[1]["names"]))]
    return {"candidates": cands[:top], "skipped": skipped, "groups": groups_out, "target_offset_pct": round(target_offset * 100), "budget_pct": budget_pct,
            "mapping": [{"ticker": t, **{k: v for k, v in m.items() if k != "self"}, "self": bool(m.get("self"))} for t, m in sorted(mapping.items())],
            "unmapped": unmapped}
