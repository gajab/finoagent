"""Dealer positioning — the option-mechanics overlay.

Where price *can* go is often gated by where option dealers (market makers) must hedge:

  • Net GEX (Gamma Exposure) — Σ over the chain of dealer gamma. Convention: dealers long
    call gamma, short put gamma. **Positive net GEX** → dealers are long gamma and hedge
    AGAINST moves (sell rallies / buy dips) → volatility SUPPRESSED, price pins/mean-reverts.
    **Negative net GEX** → short gamma, dealers hedge WITH moves → volatility EXPANSION,
    trends accelerate.
  • Gamma Flip Level — the spot at which net GEX crosses zero (long-gamma above → short-gamma
    below). The single most important line: regime of volatility changes across it.
  • Gamma Walls — the strikes with the largest +GEX (call wall, a magnet/resistance) and
    −GEX (put wall, support) — where dealer hedging concentrates.
  • Volatility Cone / Expected Move — the options-implied ±1σ move for ~30- and ~45-day
    expiries (spot · ATM-IV · √(dte/365)) — the market's own range forecast.

Reuses the Black-Scholes greeks from ``zebra_service``. Best-effort: no chain / thin data
yields ``None`` and never raises.
"""

from __future__ import annotations

import datetime as _dt
from collections import defaultdict

import numpy as np

from .zebra_service import _bs_greeks, DEFAULT_RISK_FREE
from .stock_service import safe_float, safe_int
from .microstructure_service import _safe_history, _price_series, _now_str, _r


def _dollar_gex(gamma: float, oi: float, spot: float) -> float:
    """Dollar gamma exposure per 1% move: γ · OI · 100 shares · spot² · 1%."""
    return gamma * oi * 100.0 * spot * spot * 0.01


def _collect_contracts(stock, spot: float, max_exps: int = 8,
                       min_dte: int = 3, max_dte: int = 60, strike_band: float = 0.25):
    """Pull near-dated option contracts with usable OI & IV, within ±band of spot."""
    today = _dt.date.today()
    lo, hi = spot * (1 - strike_band), spot * (1 + strike_band)
    contracts: list[dict] = []
    exps_used: list[str] = []
    try:
        exps = list(stock.options or [])
    except Exception:
        return [], []
    for exp in exps:
        try:
            d = _dt.date.fromisoformat(exp)
        except Exception:
            continue
        dte = (d - today).days
        if dte < min_dte or dte > max_dte:
            continue
        try:
            chain = stock.option_chain(exp)
        except Exception:
            continue
        t = dte / 365.0
        added = False
        for df, is_call in ((chain.calls, True), (chain.puts, False)):
            if df is None or df.empty:
                continue
            for _, row in df.iterrows():
                strike = safe_float(row.get("strike"))
                if not strike or strike < lo or strike > hi:
                    continue
                iv = safe_float(row.get("impliedVolatility"))
                oi = safe_int(row.get("openInterest"))
                if iv <= 0 or oi <= 0:
                    continue
                contracts.append({"strike": strike, "t": t, "iv": iv, "oi": oi, "is_call": is_call, "exp": exp})
                added = True
        if added:
            exps_used.append(exp)
        if len(exps_used) >= max_exps:
            break
    return contracts, exps_used


def _net_gex_at(contracts: list[dict], spot: float) -> float:
    total = 0.0
    for c in contracts:
        g = _bs_greeks(spot, c["strike"], c["t"], c["iv"], c["is_call"], DEFAULT_RISK_FREE)["gamma"]
        dg = _dollar_gex(g, c["oi"], spot)
        total += dg if c["is_call"] else -dg
    return total


def _gamma_flip(contracts: list[dict], spot: float) -> float | None:
    """Spot where net GEX crosses zero — recompute gamma across a ±20% spot grid and
    interpolate the sign change nearest current spot."""
    grid = spot * np.linspace(0.8, 1.2, 81)
    curve = np.array([_net_gex_at(contracts, float(s)) for s in grid])
    signs = np.sign(curve)
    crossings = []
    for i in range(1, len(grid)):
        if signs[i - 1] != 0 and signs[i] != 0 and signs[i - 1] != signs[i]:
            x0, x1, y0, y1 = grid[i - 1], grid[i], curve[i - 1], curve[i]
            level = x0 - y0 * (x1 - x0) / (y1 - y0)      # linear interpolation to y=0
            crossings.append(float(level))
    if not crossings:
        return None
    return min(crossings, key=lambda x: abs(x - spot))


def _expected_move(contracts: list[dict], spot: float, target_dte: int) -> dict | None:
    """±1σ implied move for the expiry nearest ``target_dte`` days, using ATM IV."""
    by_t: dict[float, list[dict]] = defaultdict(list)
    for c in contracts:
        by_t[c["t"]].append(c)
    if not by_t:
        return None
    target_t = target_dte / 365.0
    t = min(by_t.keys(), key=lambda x: abs(x - target_t))
    near = by_t[t]
    atm = min(near, key=lambda c: abs(c["strike"] - spot))
    # blend call+put ATM IV at the nearest strike
    ivs = [c["iv"] for c in near if abs(c["strike"] - atm["strike"]) < 1e-6]
    iv = float(np.mean(ivs)) if ivs else atm["iv"]
    dte = int(round(t * 365))
    move = spot * iv * np.sqrt(t)
    return {
        "dte": dte, "expiration": near[0].get("exp"), "iv_atm_pct": _r(iv * 100, 1),
        "move": _r(move), "move_pct": _r(move / spot * 100, 1) if spot else None,
        "upper": _r(spot + move), "lower": _r(spot - move),
    }


def compute_dealer_positioning(stock) -> dict | None:
    """Full dealer-positioning read. ``None`` if there's no usable option chain."""
    try:
        hist = _safe_history(stock, "1mo", "1d")
        spot = float(hist["Close"].values[-1]) if hist is not None and not hist.empty else None
        if not spot or spot <= 0:
            return None

        contracts, exps = _collect_contracts(stock, spot)
        if len(contracts) < 10:
            return None

        # net GEX at spot + per-strike aggregation
        net = 0.0
        by_strike: dict[float, float] = defaultdict(float)
        for c in contracts:
            g = _bs_greeks(spot, c["strike"], c["t"], c["iv"], c["is_call"], DEFAULT_RISK_FREE)["gamma"]
            dg = _dollar_gex(g, c["oi"], spot)
            signed = dg if c["is_call"] else -dg
            net += signed
            by_strike[c["strike"]] += signed

        long_gamma = net >= 0
        net_block = {
            "value": _r(net, 0), "value_millions": _r(net / 1e6, 1),
            "sign": "long" if long_gamma else "short",
            "label": ("Dealers LONG gamma → volatility suppressed; price tends to pin / mean-revert."
                      if long_gamma else
                      "Dealers SHORT gamma → volatility expansion; moves get amplified / trend."),
        }

        flip = _gamma_flip(contracts, spot)
        flip_block = None
        if flip is not None:
            flip_block = {
                "level": _r(flip), "distance_pct": _r((flip - spot) / spot * 100),
                "side": "above" if flip >= spot else "below",
                "note": (f"Spot ${round(spot,2)} is {'above' if spot >= flip else 'below'} the gamma flip "
                         f"${round(flip,2)} → dealers currently {'LONG' if spot >= flip else 'SHORT'} gamma. "
                         f"Losing the flip flips the volatility regime."),
            }

        # gamma walls + top strikes
        strikes_sorted = sorted(by_strike.items(), key=lambda kv: kv[0])
        pos = [(k, v) for k, v in strikes_sorted if v > 0]
        neg = [(k, v) for k, v in strikes_sorted if v < 0]
        call_wall = max(pos, key=lambda kv: kv[1]) if pos else None
        put_wall = min(neg, key=lambda kv: kv[1]) if neg else None
        top = sorted(by_strike.items(), key=lambda kv: -abs(kv[1]))[:12]
        walls = {
            "call_wall": {"strike": _r(call_wall[0]), "gex_millions": _r(call_wall[1] / 1e6, 1)} if call_wall else None,
            "put_wall": {"strike": _r(put_wall[0]), "gex_millions": _r(put_wall[1] / 1e6, 1)} if put_wall else None,
            "by_strike": [{"strike": _r(k), "gex_millions": _r(v / 1e6, 1)}
                          for k, v in sorted(top, key=lambda kv: kv[0])],
        }

        return {
            "price": _r(spot), "as_of": _now_str(),
            "net_gex": net_block,
            "gamma_flip": flip_block,
            "walls": walls,
            "expected_move": {
                "em_30d": _expected_move(contracts, spot, 30),
                "em_45d": _expected_move(contracts, spot, 45),
            },
            "expirations_used": exps,
            # the ACTUAL tradable strikes on the board — lets downstream snap suggestions to real strikes
            "strikes": sorted({round(float(c["strike"]), 2) for c in contracts}),
            "price_series": _price_series(_safe_history(stock, "6mo", "1d")),
        }
    except Exception:  # noqa: BLE001
        return None
