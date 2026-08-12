"""Trade-Setup engine — fuse the four institutional TA reads into ranked, concrete plans.

The microstructure, market-structure, regime and dealer-positioning services each answer
part of the question. This engine answers the whole one a trader actually asks: *given
everything, what are the highest-probability setups right now, and exactly where do I get
in, get out, and take profit?*

Pipeline (pure functions so the logic is unit-testable without yfinance):
  1. `_collect_levels` — every actionable price from all four reads → a unified list tagged
     support / resistance / magnet with a reliability weight.
  2. `_cluster_zones` — levels that stack within ~0.4·ATR become a **confluence zone**;
     score = Σ weights. Confluence across independent methods = conviction.
  3. `_derive_bias` — structure trend + regime + dealer gamma → a directional bias.
  4. `_build_setups` — regime-appropriate plans (trend continuation / mean-reversion fade /
     range income / breakout), each with entry, stop, targets, R:R, sizing vs the expected
     move, a fitting option structure, a plain-English thesis and an evidence trail.

`compute_trade_setups` runs the four sub-computes concurrently and degrades gracefully if
any one is unavailable. Best-effort throughout — never raises.
"""

from __future__ import annotations

import datetime as _dt
import math
from concurrent.futures import ThreadPoolExecutor

from .microstructure_service import _r, _now_str, compute_microstructure
from .market_structure_service import compute_market_structure
from .regime_service import compute_regime
from .dealer_positioning_service import compute_dealer_positioning
from .stock_service import safe_float
from .zebra_service import _bs_price, _norm_cdf, DEFAULT_RISK_FREE


# ---------------------------------------------------------------------------
# probability & edge — market-implied (risk-neutral lognormal), consistent with the
# income desk's BS-prob fallback (stock_service.bs_prob_otm / hedging RND use the same basis)
# ---------------------------------------------------------------------------

def _p_above(spot, level, t, iv, r=DEFAULT_RISK_FREE):
    """Risk-neutral P(S_T ≥ level) = N(d2)."""
    if not (spot and level and t and iv) or spot <= 0 or level <= 0 or t <= 0 or iv <= 0:
        return None
    d2 = (math.log(spot / level) + (r - 0.5 * iv * iv) * t) / (iv * math.sqrt(t))
    return _norm_cdf(d2)


def _p_below(spot, level, t, iv, r=DEFAULT_RISK_FREE):
    p = _p_above(spot, level, t, iv, r)
    return (1.0 - p) if p is not None else None


def _edge(setup, spot, atm_iv, dte) -> dict:
    """PoP + expected value + Kelly for the equity and options expressions of the trade."""
    t = max(dte or 30, 1) / 365.0
    iv = atm_iv if (atm_iv and atm_iv > 0) else 0.30
    out: dict = {}
    direction = setup.get("direction")
    entry = (setup.get("entry") or {}).get("level")
    stop = (setup.get("stop") or {}).get("level")
    t1 = (setup.get("targets") or [{}])[0].get("level")

    if direction in ("long", "short") and entry and stop and t1:
        if direction == "long":
            p_win, p_lose = _p_above(spot, t1, t, iv), _p_below(spot, stop, t, iv)
        else:
            p_win, p_lose = _p_below(spot, t1, t, iv), _p_above(spot, stop, t, iv)
        risk, reward = abs(entry - stop), abs(t1 - entry)
        b = reward / risk if risk > 0 else 0.0
        if p_win is not None and p_lose is not None:
            ev = p_win * reward - p_lose * risk
            kelly = (p_win - (1 - p_win) / b) if b > 0 else 0.0
            out["equity"] = {"pop_pct": _r(p_win * 100, 1), "ev_per_share": _r(ev, 2),
                             "payoff_ratio": _r(b, 2), "kelly_pct": _r(max(0.0, kelly) * 100, 1),
                             "half_kelly_risk_pct": _r(min(2.0, max(0.0, kelly) / 2 * 100), 2)}

    op = setup.get("options_plan") or {}
    if op.get("available") and op.get("breakevens") and op.get("max_profit") is not None:
        be = op["breakevens"][0]
        legs = op.get("legs") or []
        bull = ("Call" in (op.get("structure") or "")) or (legs and legs[0].get("right") == "Call" and legs[0].get("action") == "Buy")
        pop = _p_above(spot, be, t, iv) if bull else _p_below(spot, be, t, iv)
        if pop is not None:
            ev = pop * op["max_profit"] + (1 - pop) * op["max_loss"]
            out["options"] = {"pop_pct": _r(pop * 100, 1), "ev": _r(ev, 0), "basis": "BS-implied (ATM IV)"}
    return out

_RISK_BUDGET = 250.0        # $ risked per trade = 1% of a nominal $25k book (share-sizing basis)


def _indicators(stock) -> dict:
    """Classic Indicators-tab read (RSI/MACD/Bollinger/SMA/EMA/S-R/volume) on the
    medium-term swing timeframe — same pattern desk_review uses. Best-effort."""
    try:
        from .stock_service import compute_technical_block, compute_momentum_indicators
        block = compute_technical_block(stock, "medium_term") or {}
        try:
            block.update(compute_momentum_indicators(stock))
        except Exception:  # noqa: BLE001
            pass
        return block
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------------------
# 1. collect every actionable level
# ---------------------------------------------------------------------------

def _lvl(price, kind, source, label, weight):
    if price is None:
        return None
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return {"price": round(p, 2), "kind": kind, "source": source, "label": label, "weight": float(weight)}


def _dir_kind(price: float, spot: float) -> str:
    return "resistance" if price >= spot else "support"


def _collect_levels(micro, structure, regime, dealer, spot, indicators=None) -> list[dict]:
    """Every tradeable price from all five reads → tagged, weighted levels."""
    out: list[dict] = []
    add = lambda *a: (lambda L: out.append(L) if L else None)(_lvl(*a))

    # --- microstructure: MTF volume profile, naked POCs, AVWAP ---
    if micro:
        tf_w = {"macro": 3.0, "swing": 2.0, "micro": 1.5}
        for key, prof in (micro.get("timeframe_profiles") or {}).items():
            if not prof:
                continue
            w = tf_w.get(key, 1.5)
            add(prof.get("poc"), "magnet", f"vp_{key}", f"{key.title()} POC", w)
            add(prof.get("vah"), "resistance", f"vp_{key}", f"{key.title()} VAH", w * 0.7)
            add(prof.get("val"), "support", f"vp_{key}", f"{key.title()} VAL", w * 0.7)
            for lvn in (prof.get("lvns") or [])[:2]:
                add(lvn.get("price"), "magnet", f"vp_{key}", f"{key.title()} LVN", 0.6)
        for nk in (micro.get("naked_pocs") or [])[:4]:
            add(nk.get("price"), _dir_kind(nk.get("price", spot), spot), "naked_poc", "Naked POC", 2.0)
        for k, a in (micro.get("avwap") or {}).items():
            if a and a.get("value"):
                w = 1.5 if k == "earnings" else 2.0
                add(a["value"], _dir_kind(a["value"], spot), "avwap", a.get("label", "AVWAP"), w)

    # --- market structure: order blocks, unmitigated FVGs, liquidity pools, swings ---
    if structure:
        tf_w = {"daily": 3.0, "h4": 2.0, "h1": 1.0}
        for key, block in (structure.get("timeframes") or {}).items():
            if not block:
                continue
            w = tf_w.get(key, 1.0)
            for ob in (block.get("order_blocks") or []):
                if ob.get("mitigated"):
                    continue
                kind = "support" if ob.get("type") == "bullish" else "resistance"
                add(ob.get("price"), kind, f"ob_{key}", f"{key.upper()} {'Demand' if kind=='support' else 'Supply'} OB", w)
            for fv in (block.get("fair_value_gaps") or []):
                if fv.get("filled"):
                    continue
                kind = "support" if fv.get("type") == "bullish" else "resistance"
                add(fv.get("mid"), kind, f"fvg_{key}", f"{key.upper()} {'Bull' if kind=='support' else 'Bear'} FVG", w * 0.7)
            for pool in (block.get("liquidity_pools") or []):
                kind = "resistance" if pool.get("type") == "BSL" else "support"
                pw = 2.0 if pool.get("strength") == "strong" else 1.4
                add(pool.get("price"), kind, f"pool_{key}", f"{pool.get('type')} pool", pw)
            st = block.get("structure") or {}
            add(st.get("recent_swing_high"), "resistance", f"swing_{key}", f"{key.upper()} swing high", w * 0.5)
            add(st.get("recent_swing_low"), "support", f"swing_{key}", f"{key.upper()} swing low", w * 0.5)

    # --- regime: 50-day VWAP (mean anchor) ---
    if regime:
        z = regime.get("zscore")
        if z and z.get("vwap"):
            add(z["vwap"], "magnet", "vwap50", "50-day VWAP", 2.0)

    # --- dealer: gamma flip, Call Resistance / Put Support / HVL, expected-move bounds ---
    if dealer:
        gf = dealer.get("gamma_flip")
        if gf and gf.get("level"):
            add(gf["level"], "magnet", "gamma_flip", "Gamma flip", 3.0)
        gl = dealer.get("gamma_levels") or {}
        walls = dealer.get("walls") or {}
        cr = (gl.get("call_resistance") or {}).get("strike") or (walls.get("call_wall") or {}).get("strike")
        ps = (gl.get("put_support") or {}).get("strike") or (walls.get("put_wall") or {}).get("strike")
        hvl = (gl.get("hvl") or {}).get("strike")
        if cr:
            add(cr, "resistance", "call_resistance", "Call Resistance", 2.5)
        if ps:
            add(ps, "support", "put_support", "Put Support", 2.5)
        if hvl:
            add(hvl, "magnet", "hvl", "HVL (gamma magnet)", 2.5)
        em = (dealer.get("expected_move") or {}).get("em_30d")
        if em and em.get("upper"):
            add(em["upper"], "resistance", "expected_move", "Expected-move high (30d)", 1.5)
            add(em["lower"], "support", "expected_move", "Expected-move low (30d)", 1.5)

    # --- classic indicators: Bollinger bands, moving averages, base support/resistance ---
    ind = indicators or {}
    bb = ind.get("bollingerBands") or {}
    add(bb.get("upper"), "resistance", "bollinger", "Bollinger upper", 1.2)
    add(bb.get("lower"), "support", "bollinger", "Bollinger lower", 1.2)
    ma = ind.get("movingAverages") or {}
    add(ma.get("sma50"), "magnet", "sma50", "SMA-50", 1.6)
    add(ma.get("sma200"), "magnet", "sma200", "SMA-200", 2.0)
    add(ind.get("supportLevel"), "support", "ta_sr", "Support (swing)", 1.6)
    add(ind.get("resistanceLevel"), "resistance", "ta_sr", "Resistance (swing)", 1.6)
    return out


# ---------------------------------------------------------------------------
# 2. cluster into confluence zones
# ---------------------------------------------------------------------------

def _cluster_zones(levels: list[dict], atr: float, spot: float) -> list[dict]:
    """Cluster nearby levels into confluence zones. Width is capped to ``tol`` (distance
    from the cluster's first member) so dense regions resolve into several tight zones
    rather than one runaway chain."""
    if not levels:
        return []
    tol = max(0.4 * atr, 0.004 * spot) if (atr and atr > 0) else 0.006 * spot
    ordered = sorted(levels, key=lambda x: x["price"])
    zones: list[dict] = []
    cur: list[dict] = [ordered[0]]
    for lv in ordered[1:]:
        if lv["price"] - cur[0]["price"] <= tol:          # cap span at the cluster START
            cur.append(lv)
        else:
            zones.append(_make_zone(cur, spot, atr))
            cur = [lv]
    zones.append(_make_zone(cur, spot, atr))
    zones.sort(key=lambda z: -z["score"])
    return zones


def _make_zone(members: list[dict], spot: float, atr: float) -> dict:
    """Zones are tagged by POSITION vs spot — a support is below, a resistance above, and a
    zone hugging spot is a 'pivot' (you're inside it, not an entry). The source *nature*
    (demand/supply/magnet) is preserved only in the evidence labels."""
    total_w = sum(m["weight"] for m in members) or 1e-9
    center = sum(m["price"] * m["weight"] for m in members) / total_w
    near = max(0.2 * atr, 0.0025 * spot) if (atr and atr > 0) else 0.004 * spot
    gap = center - spot
    kind = "pivot" if abs(gap) <= near else ("support" if gap < 0 else "resistance")
    return {
        "center": round(center, 2),
        "low": round(min(m["price"] for m in members), 2),
        "high": round(max(m["price"] for m in members), 2),
        "kind": kind,
        "score": round(total_w, 2),
        "n_sources": len({m["source"] for m in members}),
        "has_magnet": any(m["kind"] == "magnet" for m in members),
        "sources": [{"label": m["label"], "price": m["price"], "weight": m["weight"]}
                    for m in sorted(members, key=lambda x: -x["weight"])],
        "distance_pct": round((center - spot) / spot * 100, 2) if spot else None,
    }


def _nearest(zones, kind, spot, above=None):
    """Nearest zone of `kind` (optionally strictly above/below spot), by distance."""
    cands = [z for z in zones if z["kind"] == kind]
    if above is True:
        cands = [z for z in cands if z["center"] > spot]
    elif above is False:
        cands = [z for z in cands if z["center"] < spot]
    return min(cands, key=lambda z: abs(z["center"] - spot)) if cands else None


def _mean_zone(zones, mean_price, spot):
    """The zone nearest the statistical mean (50-day VWAP / macro POC) — the fade target."""
    if not mean_price:
        return None
    if zones:
        z = min(zones, key=lambda z: abs(z["center"] - mean_price))
        if abs(z["center"] - mean_price) / spot < 0.02:
            return z
    return {"center": round(float(mean_price), 2), "low": round(float(mean_price), 2),
            "high": round(float(mean_price), 2), "kind": "pivot", "score": 2.0,
            "sources": [{"label": "mean (VWAP/POC)", "price": round(float(mean_price), 2), "weight": 2.0}]}


# ---------------------------------------------------------------------------
# 3. directional bias
# ---------------------------------------------------------------------------

def _derive_bias(structure, regime, dealer, indicators=None) -> dict:
    votes = 0.0
    reasons = []
    confirms = []                                    # every signal that voted, for the evidence trail
    def vote(w, msg, tag=None):
        nonlocal votes
        votes += w
        if tag:
            confirms.append({"signal": tag, "reads": ("bullish" if w > 0 else "bearish"), "detail": msg})
        if abs(w) >= 0.5:
            reasons.append(msg)

    s_bias = ((structure or {}).get("bias") or {}).get("overall")
    if s_bias == "bullish":
        vote(1, "market structure is bullish", "market_structure")
    elif s_bias == "bearish":
        vote(-1, "market structure is bearish", "market_structure")

    daily = ((structure or {}).get("timeframes") or {}).get("daily") or {}
    if daily.get("trend") == "up":
        vote(1, "daily trend is up", "daily_trend")
    elif daily.get("trend") == "down":
        vote(-1, "daily trend is down", "daily_trend")

    if dealer:
        gf = dealer.get("gamma_flip") or {}
        if gf.get("side") == "below":                # flip below spot → spot above flip → long gamma / supportive
            vote(0.5, "spot is above the gamma flip", "gamma")
        elif gf.get("side") == "above":
            vote(-0.5, "spot is below the gamma flip", "gamma")

    # --- classic Indicators-tab confirmations ---
    ind = indicators or {}
    rsi = ind.get("currentRSI")
    if rsi is not None:
        if rsi >= 55:
            vote(0.5, f"RSI {rsi:.0f} — bullish momentum", "rsi")
        elif rsi <= 45:
            vote(-0.5, f"RSI {rsi:.0f} — bearish momentum", "rsi")
    macd = ind.get("macd") or {}
    if macd.get("crossover") == "bullish_crossover":
        vote(0.75, "MACD bullish crossover", "macd")
    elif macd.get("crossover") == "bearish_crossover":
        vote(-0.75, "MACD bearish crossover", "macd")
    elif macd.get("signal") == "bullish":
        vote(0.35, "MACD above signal", "macd")
    elif macd.get("signal") == "bearish":
        vote(-0.35, "MACD below signal", "macd")
    ma = ind.get("movingAverages") or {}
    if ma.get("priceVsSma50") == "above":
        vote(0.3, "price above the 50-day MA", "sma50")
    elif ma.get("priceVsSma50") == "below":
        vote(-0.3, "price below the 50-day MA", "sma50")
    if ma.get("goldenDeathCross") == "golden_cross":
        vote(0.5, "golden cross (SMA50 > SMA200)", "sma_cross")
    elif ma.get("goldenDeathCross") == "death_cross":
        vote(-0.5, "death cross (SMA50 < SMA200)", "sma_cross")
    bb = ind.get("bollingerBands") or {}
    if (bb.get("percentB") or 0.5) > 0.55:
        vote(0.15, "upper half of the Bollinger band", "bollinger")
    elif (bb.get("percentB") or 0.5) < 0.45:
        vote(-0.15, "lower half of the Bollinger band", "bollinger")

    direction = "bullish" if votes >= 1 else "bearish" if votes <= -1 else "neutral"
    strength = "strong" if abs(votes) >= 2.5 else "moderate" if abs(votes) >= 1 else "weak"
    reg = (regime or {}).get("regime") or {}
    return {
        "direction": direction,
        "strength": strength,
        "score": round(votes, 2),
        "rationale": ("; ".join(reasons[:4]) or "no dominant directional signal").capitalize() + ".",
        "confirmations": confirms,
        "regime": reg.get("overall", "transitional"),
    }


# ---------------------------------------------------------------------------
# 4. build setups
# ---------------------------------------------------------------------------

_CONF = lambda s: "high" if s >= 9 else "medium" if s >= 5 else "low"


def _rr(entry: float, stop: float, target: float) -> float | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    return round(abs(target - entry) / risk, 2)


def _sizing(entry, stop, target, spot, em_pct):
    risk = abs(entry - stop)
    move_pct = abs(target - spot) / spot * 100 if spot else None
    within = (move_pct is not None and em_pct is not None and move_pct <= em_pct * 1.1)
    note = None
    if move_pct is not None and em_pct is not None:
        note = (f"Target is ~{move_pct:.1f}% away vs the ±{em_pct:.1f}% 30-day expected move — "
                + ("realistic within the horizon." if within else "beyond the 1-month expected move; allow more time or scale the target."))
    return {"risk_per_share": _r(risk), "target_move_pct": _r(move_pct, 1),
            "within_expected_move": bool(within), "note": note}


def _strike_increment(price: float) -> float:
    """Fallback strike grid when the real chain isn't available (approximate)."""
    if price < 25:
        return 0.5
    if price < 200:
        return 1.0
    return 5.0


def _snap(price, strikes, prefer: str = "near"):
    """Snap a raw level to the nearest ACTUAL tradable strike (options are standardized —
    never suggest a fractional strike like $387.22). ``prefer`` biases below/above so a
    long call sits just below spot (intrinsic) and short strikes clear the level."""
    if price is None:
        return None
    price = float(price)
    if strikes:
        if prefer == "below":
            below = [s for s in strikes if s <= price]
            if below:
                return max(below)
        elif prefer == "above":
            above = [s for s in strikes if s >= price]
            if above:
                return min(above)
        return min(strikes, key=lambda s: abs(s - price))
    inc = _strike_increment(price)
    return round(round(price / inc) * inc, 2)


def _next_strike(base, strikes, up: bool = True):
    if base is None:
        return None
    if strikes:
        cands = [s for s in strikes if (s > base if up else s < base)]
        if cands:
            return min(cands) if up else max(cands)
        return base
    inc = _strike_increment(base)
    return round(base + (inc if up else -inc), 2)


def _first(*vals):
    return next((v for v in vals if v is not None), None)


def _pick_expiry(dealer) -> dict | None:
    """The concrete expiration nearest the 30–45 DTE window (from the real option chain)."""
    em = (dealer or {}).get("expected_move") or {}
    for key in ("em_30d", "em_45d"):
        e = em.get(key)
        if e and e.get("expiration"):
            return {"date": e["expiration"], "dte": e.get("dte")}
    return None


def _exp_txt(expiry) -> str:
    if expiry and expiry.get("date"):
        return f" Suggested expiry ≈ {expiry['date']} ({expiry.get('dte')} DTE)."
    return ""


def _option_play(setup_type, direction, spot, target, strong_support, strong_resistance, dealer, strikes, expiry=None):
    """Map the plan to a REAL, executable options structure: strikes snapped to the board,
    premium-selling anchored to the STRONGEST structural level (not the directional entry),
    with the concrete ~30–45 DTE expiration surfaced."""
    walls = (dealer or {}).get("walls") or {}
    call_wall = (walls.get("call_wall") or {}).get("strike")
    put_wall = (walls.get("put_wall") or {}).get("strike")

    if setup_type in ("range_income", "range_bracket"):
        sp = _snap(_first(put_wall, strong_support), strikes, "below")
        sc = _snap(_first(call_wall, strong_resistance), strikes, "above")
        return {"structure": "Iron Condor",
                "detail": f"Sell the ${sp} put and ${sc} call — a defined-risk condor bracketing the range at the "
                          f"dealer walls / structural edges. Profit if price stays between them." + _exp_txt(expiry),
                "strikes": {"short_put": sp, "short_call": sc}, "expiry": expiry, "bias": "neutral"}

    if direction == "long":
        long_call = _snap(spot, strikes, "below")                       # just below spot → intrinsic
        short_call = _snap(_first(target, call_wall, strong_resistance), strikes, "near")
        if short_call is not None and long_call is not None and short_call <= long_call:
            short_call = _next_strike(long_call, strikes, up=True)
        csp = _snap(_first(strong_support, put_wall), strikes, "below")  # fortress support, NOT the entry
        return {"structure": "Bull Call Spread / Cash-Secured Put",
                "detail": f"Bullish: buy the ${long_call} call and sell the ${short_call} call to define risk into "
                          f"resistance — or sell the ${csp} cash-secured put at structural support to get paid to wait "
                          f"(only assigned near a defended floor)." + _exp_txt(expiry),
                "strikes": {"long_call": long_call, "short_call": short_call, "csp_put": csp}, "expiry": expiry, "bias": "bullish"}

    long_put = _snap(spot, strikes, "above")
    short_put = _snap(_first(target, put_wall, strong_support), strikes, "near")
    if short_put is not None and long_put is not None and short_put >= long_put:
        short_put = _next_strike(long_put, strikes, up=False)
    ccs = _snap(_first(strong_resistance, call_wall), strikes, "above")
    return {"structure": "Bear Put Spread / Call Credit Spread",
            "detail": f"Bearish: buy the ${long_put} put and sell the ${short_put} put — or sell a call credit "
                      f"spread above the ${ccs} structural resistance." + _exp_txt(expiry),
            "strikes": {"long_put": long_put, "short_put": short_put, "ccs_call": ccs}, "expiry": expiry, "bias": "bearish"}


def _evidence(zone, extra):
    ev = [f"{s['label']} @ ${s['price']}" for s in (zone.get("sources") or [])[:4]] if zone else []
    return ev + [e for e in extra if e]


def _build_setups(bias, zones, spot, atr, dealer, regime, em_pct, mean_price, strikes) -> list[dict]:
    setups: list[dict] = []
    reg = ((regime or {}).get("regime") or {}).get("overall", "transitional")
    zval = ((regime or {}).get("zscore") or {})
    buf = max(0.5 * atr, 0.004 * spot) if (atr and atr > 0) else 0.006 * spot

    sup = _nearest(zones, "support", spot, above=False)
    res = _nearest(zones, "resistance", spot, above=True)
    mean_z = _mean_zone(zones, mean_price, spot)
    # the STRONGEST (highest-confluence) zones — where premium-selling is anchored, so a CSP
    # sits at the structural fortress, not at a weak level just under spot.
    sups = [z for z in zones if z["kind"] == "support"]
    ress = [z for z in zones if z["kind"] == "resistance"]
    strong_support = max(sups, key=lambda z: z["score"])["center"] if sups else None
    strong_resistance = max(ress, key=lambda z: z["score"])["center"] if ress else None
    expiry = _pick_expiry(dealer)

    def mk(kind, direction, entry_zone, entry, stop, targets, base_score, thesis, extra_ev, fit):
        tgt = [t for t in targets if t]
        if not tgt:
            return
        rr = _rr(entry, stop, tgt[0]["level"])
        score = base_score + (entry_zone["score"] if entry_zone else 0) + min(rr or 0, 3)
        if fit == "counter_regime":
            score -= 3
        setups.append({
            "type": kind, "direction": direction, "regime_fit": fit,
            "confidence": _CONF(score), "score": round(score, 2),
            "entry": {"low": entry_zone["low"] if entry_zone else _r(entry),
                      "high": entry_zone["high"] if entry_zone else _r(entry),
                      "level": _r(entry), "label": entry_zone["sources"][0]["label"] if entry_zone else "entry"},
            "stop": {"level": _r(stop), "label": "beyond the entry zone / nearest thin level"},
            "targets": tgt,
            "risk_reward": rr,
            "sizing": _sizing(entry, stop, tgt[0]["level"], spot, em_pct),
            "options": _option_play(kind, direction, spot, tgt[0]["level"], strong_support, strong_resistance, dealer, strikes, expiry),
            "thesis": thesis,
            "evidence": _evidence(entry_zone, extra_ev),
        })

    trend_up = bias["direction"] == "bullish"
    trend_dn = bias["direction"] == "bearish"

    # A) trend / bias continuation (fires unless the regime is explicitly mean-reverting)
    cont_fit = "with_regime" if reg == "trending" else "neutral"
    if reg != "mean_reverting" and trend_up and sup and res:
        mk("trend_continuation", "long", sup, sup["center"], sup["low"] - buf,
           [{"level": res["center"], "label": res["sources"][0]["label"], "rr": _rr(sup["center"], sup["low"] - buf, res["center"])}],
           4, f"{'Uptrend' if reg == 'trending' else 'Bullish bias'} — buy the pullback into support ${sup['low']}–${sup['high']}, target resistance ${res['center']}.",
           [f"regime: {reg} (H {((regime or {}).get('regime') or {}).get('hurst_daily')})", bias["rationale"]], cont_fit)
    if reg != "mean_reverting" and trend_dn and res and sup:
        mk("trend_continuation", "short", res, res["center"], res["high"] + buf,
           [{"level": sup["center"], "label": sup["sources"][0]["label"], "rr": _rr(res["center"], res["high"] + buf, sup["center"])}],
           4, f"{'Downtrend' if reg == 'trending' else 'Bearish bias'} — sell the bounce into resistance ${res['low']}–${res['high']}, target support ${sup['center']}.",
           [f"regime: {reg}", bias["rationale"]], cont_fit)

    # B) mean-reversion fade (mean-reverting regime, or a stretched z-score) — target the mean
    # A stretched z-score only justifies a FADE when we're NOT trending — in a trend (esp. a
    # short-gamma tape) stretched = momentum and support/resistance is made to break, not bought.
    allow_stretch = reg != "trending"
    stretched_hi = zval.get("z", 0) >= 1.5 and allow_stretch
    stretched_lo = zval.get("z", 0) <= -1.5 and allow_stretch
    if (reg == "mean_reverting" or stretched_hi) and res and mean_z and mean_z["center"] < res["center"]:
        fit = "with_regime" if reg == "mean_reverting" else "neutral"
        mk("mean_reversion_fade", "short", res, res["center"], res["high"] + buf,
           [{"level": mean_z["center"], "label": mean_z["sources"][0]["label"], "rr": _rr(res["center"], res["high"] + buf, mean_z["center"])}],
           3, f"Fade the extreme — short resistance ${res['center']} back toward the mean ${mean_z['center']}.",
           [f"regime: {reg}", f"z-score {zval.get('z')}" if zval else ""], fit)
    if (reg == "mean_reverting" or stretched_lo) and sup and mean_z and mean_z["center"] > sup["center"]:
        fit = "with_regime" if reg == "mean_reverting" else "neutral"
        mk("mean_reversion_fade", "long", sup, sup["center"], sup["low"] - buf,
           [{"level": mean_z["center"], "label": mean_z["sources"][0]["label"], "rr": _rr(sup["center"], sup["low"] - buf, mean_z["center"])}],
           3, f"Fade the flush — buy support ${sup['center']} back toward the mean ${mean_z['center']}.",
           [f"regime: {reg}", f"z-score {zval.get('z')}" if zval else ""], fit)

    # C) range income (mean-reverting + dealers long gamma → premium selling)
    long_gamma = ((dealer or {}).get("net_gex") or {}).get("sign") == "long"
    if reg == "mean_reverting" and long_gamma and sup and res:
        credit_score = 4 + sup["score"] * 0.3 + res["score"] * 0.3
        setups.append({
            "type": "range_income", "direction": "neutral", "regime_fit": "with_regime",
            "confidence": _CONF(credit_score), "score": round(credit_score, 2),
            "entry": {"low": sup["center"], "high": res["center"], "level": _r(spot), "label": "sell the range"},
            "stop": {"level": None, "label": "manage if either wall breaks / at ~2× credit"},
            "targets": [{"level": _r(mean_z["center"]) if mean_z else _r(spot), "label": "decay toward POC/VWAP", "rr": None}],
            "risk_reward": None,
            "sizing": {"risk_per_share": None, "target_move_pct": None, "within_expected_move": True,
                       "note": "Premium-selling: defined-risk condor; profit if price stays between the walls."},
            "options": _option_play("range_income", "neutral", spot, None, strong_support, strong_resistance, dealer, strikes, expiry),
            "thesis": f"Range-bound + dealers long gamma (pinning) — sell an iron condor between support ${sup['center']} and resistance ${res['center']}.",
            "evidence": [f"regime: mean-reverting", "dealers long gamma (vol suppressed)",
                         f"support {sup['sources'][0]['label']} ${sup['center']}", f"resistance {res['sources'][0]['label']} ${res['center']}"],
        })

    # D) breakout (trending + price pressing a strong resistance/support edge)
    if reg == "trending" and trend_up and res and res["score"] >= 5 and res["distance_pct"] is not None and res["distance_pct"] <= 3:
        beyond = _nearest([z for z in zones if z["center"] > res["center"]], "resistance", spot, above=True)
        tgt_level = beyond["center"] if beyond else res["center"] + max(2 * atr, res["center"] * 0.03)
        mk("breakout", "long", res, res["high"] + buf, res["center"] - buf,
           [{"level": _r(tgt_level), "label": beyond["sources"][0]["label"] if beyond else "measured move", "rr": _rr(res["high"] + buf, res["center"] - buf, tgt_level)}],
           3.5, f"Coiling under strong resistance ${res['center']} in an uptrend — buy the breakout, target ${round(tgt_level,2)}.",
           ["regime: trending", f"resistance confluence score {res['score']}"], "with_regime")

    # Fallback — nothing fired but we have a bracket: rotate the range.
    if not setups and sup and res:
        rr_long = _rr(sup["center"], sup["low"] - buf, res["center"])
        setups.append({
            "type": "range_bracket", "direction": "neutral", "regime_fit": "neutral",
            "confidence": _CONF(sup["score"] + res["score"]), "score": round(sup["score"] + res["score"], 2),
            "entry": {"low": sup["center"], "high": res["center"], "level": _r(spot), "label": "trade the bracket"},
            "stop": {"level": None, "label": "outside the bracket"},
            "targets": [{"level": res["center"], "label": res["sources"][0]["label"], "rr": rr_long}],
            "risk_reward": rr_long,
            "sizing": {"risk_per_share": None, "target_move_pct": _r(abs(res["center"] - spot) / spot * 100, 1) if spot else None,
                       "within_expected_move": True,
                       "note": "No directional edge — buy the support / sell the resistance, or sell premium against the bracket."},
            "options": _option_play("range_income", "neutral", spot, None, strong_support, strong_resistance, dealer, strikes, expiry),
            "thesis": f"No clear trend — rotate the range: buy support ${sup['center']}, sell resistance ${res['center']}.",
            "evidence": [f"support {sup['sources'][0]['label']} ${sup['center']}",
                         f"resistance {res['sources'][0]['label']} ${res['center']}", f"regime: {reg}"],
        })

    setups.sort(key=lambda s: -s["score"])
    for i, s in enumerate(setups[:4]):
        s["rank"] = i + 1
    return setups[:4]


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def _atr_from_structure(structure) -> float:
    for key in ("daily", "h4", "h1"):
        block = ((structure or {}).get("timeframes") or {}).get(key)
        if block and block.get("atr"):
            return float(block["atr"])
    return 0.0


def _context(bias, structure, regime, dealer) -> dict:
    reg = (regime or {}).get("regime") or {}
    em = ((dealer or {}).get("expected_move") or {}).get("em_30d") or {}
    gf = (dealer or {}).get("gamma_flip") or {}
    ng = (dealer or {}).get("net_gex") or {}
    tf = (structure or {}).get("timeframes") or {}
    return {
        "bias": bias,
        "regime": {"label": reg.get("overall"), "confidence": reg.get("confidence"),
                   "hurst": reg.get("hurst_daily"), "note": reg.get("playbook"), "favored": reg.get("favored")},
        "expected_move": {"pct_30d": em.get("move_pct"), "upper": em.get("upper"),
                          "lower": em.get("lower"), "iv": em.get("iv_atm_pct")} if em else None,
        "dealer": {"gamma": ng.get("sign"), "flip": gf.get("level"), "note": ng.get("label")} if dealer else None,
        "trend_alignment": {k: (tf.get(k) or {}).get("trend") for k in ("daily", "h4", "h1")} if structure else None,
    }


def _atr_from_indicators(indicators, spot) -> float:
    bb = (indicators or {}).get("bollingerBands") or {}
    bw = bb.get("bandwidthPct")
    if bw and spot:
        return spot * (bw / 100.0) / 4.0        # band width ≈ 4 ATR — a rough fallback
    return spot * 0.015 if spot else 0.0


# ---------------------------------------------------------------------------
# per-setup enrichment: T2 · equity plan · priced options payoff · management
# ---------------------------------------------------------------------------

def _second_target(zones, spot, direction, t1) -> float | None:
    if direction == "long":
        cands = [z["center"] for z in zones if z["kind"] == "resistance" and z["center"] > t1]
        return min(cands) if cands else round(t1 + (t1 - spot), 2)          # measured move
    cands = [z["center"] for z in zones if z["kind"] == "support" and z["center"] < t1]
    return max(cands) if cands else round(t1 - (spot - t1), 2)


def _equity_plan(direction, entry, stop, targets, spot, risk_pct=None) -> dict | None:
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    # edge-based budget (half-Kelly %, capped 0.2–2% of a $25k book); default flat 1%
    budget, basis = _RISK_BUDGET, "1% flat"
    if risk_pct and risk_pct > 0:
        budget = max(50.0, min(500.0, risk_pct / 100.0 * 25000.0))
        basis = f"half-Kelly ({risk_pct}% of book)"
    shares = max(1, int(budget / risk))
    tgs = [t for t in targets if t.get("level") is not None]
    t1 = tgs[0]["level"] if tgs else entry
    reward = abs(t1 - entry)
    rr = round(reward / risk, 2)
    return {
        "side": "Buy (long)" if direction == "long" else "Short (sell)",
        "entry": _r(entry), "stop": _r(stop),
        "targets": [{"level": t["level"], "gain_per_share": _r(abs(t["level"] - entry))} for t in tgs],
        "risk_per_share": _r(risk), "reward_per_share_t1": _r(reward), "risk_reward": rr,
        "suggested_shares": shares, "risk_budget": _r(budget, 0), "sizing_basis": basis,
        "dollar_risk": _r(shares * risk, 0), "dollar_reward_t1": _r(shares * reward, 0),
        "note": (f"Risking ~${round(budget)} ({basis}) ⇒ {shares} shares. "
                 f"Max loss ≈ ${round(shares * risk)}, T1 gain ≈ ${round(shares * reward)} (R:R {rr})."),
    }


def _next_earnings(stock) -> str | None:
    try:
        cal = stock.calendar
        ed = cal.get("Earnings Date") if isinstance(cal, dict) else None
        if isinstance(ed, (list, tuple)):
            ed = ed[0] if ed else None
        return ed.isoformat() if hasattr(ed, "isoformat") else (str(ed) if ed else None)
    except Exception:  # noqa: BLE001
        return None


def _event_risk(next_earnings, expiry) -> dict | None:
    exp = (expiry or {}).get("date")
    if not next_earnings or not exp:
        return None
    try:
        ed = _dt.date.fromisoformat(str(next_earnings)[:10])
        xd = _dt.date.fromisoformat(str(exp)[:10])
    except Exception:  # noqa: BLE001
        return None
    today = _dt.date.today()
    if today <= ed <= xd:
        return {"type": "earnings", "date": ed.isoformat(), "in_days": (ed - today).days,
                "warning": (f"Earnings on {ed.isoformat()} ({(ed - today).days}d) falls INSIDE the option's expiry — "
                            f"expect an IV crush after the report and gap risk. Prefer the stock trade, or close the "
                            f"option before the print.")}
    return None


def _build_legs(st, quotes) -> list[dict]:
    def q(strike, right):
        return (quotes.get(round(float(strike), 2)) or {}).get(right) or {}
    legs: list[dict] = []
    if st.get("long_call") and st.get("short_call"):
        for k, sign in ((st["long_call"], 1), (st["short_call"], -1)):
            d = q(k, "C"); legs.append({"strike": float(k), "right": "C", "sign": sign, "qty": 1, "mid": d.get("mid"), "iv": d.get("iv")})
    elif st.get("long_put") and st.get("short_put"):
        for k, sign in ((st["long_put"], 1), (st["short_put"], -1)):
            d = q(k, "P"); legs.append({"strike": float(k), "right": "P", "sign": sign, "qty": 1, "mid": d.get("mid"), "iv": d.get("iv")})
    elif st.get("short_put") and st.get("short_call"):
        pw, cw = float(st["short_put"]), float(st["short_call"])
        lpw, lcw = round(pw * 0.975), round(cw * 1.025)                     # modelled condor wings
        for k, right, sign in ((lpw, "P", 1), (pw, "P", -1), (cw, "C", -1), (lcw, "C", 1)):
            d = q(k, right); legs.append({"strike": float(k), "right": right, "sign": sign, "qty": 1, "mid": d.get("mid"), "iv": d.get("iv")})
    return legs


def _payoff_curve(legs, net_debit, spot, lo=0.7, hi=1.3, n=60) -> list[dict]:
    out = []
    for i in range(n + 1):
        P = spot * (lo + (hi - lo) * i / n)
        val = 0.0
        for lg in legs:
            intr = max(0.0, P - lg["strike"]) if lg["right"] == "C" else max(0.0, lg["strike"] - P)
            val += lg["sign"] * lg.get("qty", 1) * intr
        out.append({"price": round(P, 2), "pnl": round((val - net_debit) * 100.0, 2)})
    return out


def _breakevens_from_curve(curve) -> list[float]:
    bes = []
    for a, b in zip(curve, curve[1:]):
        if a["pnl"] == 0:
            bes.append(a["price"])
        elif (a["pnl"] < 0 < b["pnl"]) or (a["pnl"] > 0 > b["pnl"]):
            bes.append(round(a["price"] + (0 - a["pnl"]) * (b["price"] - a["price"]) / (b["pnl"] - a["pnl"]), 2))
    return bes


def _options_plan(op, expiry, quotes, spot, dealer) -> dict:
    st = (op or {}).get("strikes") or {}
    structure = (op or {}).get("structure") or "Options structure"
    dte = (expiry or {}).get("dte")
    t_years = (dte / 365.0) if dte else 0.08
    legs = _build_legs(st, quotes)
    if not legs or not spot:
        return {"available": False, "structure": structure,
                "note": "Couldn't price a defined-risk structure from the chain — see the option idea in the plan text."}
    net_debit, priced, any_real = 0.0, [], False
    for lg in legs:
        mid = lg.get("mid")
        if mid and mid > 0:
            any_real = True
        else:
            iv = lg.get("iv") or 0.30
            iv = iv / 100.0 if iv > 3 else iv
            mid = _bs_price(spot, lg["strike"], t_years, iv, lg["right"] == "C")
        net_debit += lg["sign"] * lg["qty"] * float(mid)
        priced.append({**lg, "price": round(float(mid), 2)})
    curve = _payoff_curve(priced, net_debit, spot)
    pnls = [c["pnl"] for c in curve]
    return {
        "available": True,
        "structure": structure.split(" / ")[0],
        "expiry": expiry,
        "priced_from": "live chain" if any_real else "model (thin quotes)",
        "legs": [{"action": "Buy" if lg["sign"] > 0 else "Sell", "right": "Call" if lg["right"] == "C" else "Put",
                  "strike": lg["strike"], "price": lg["price"]} for lg in priced],
        "net_cost": round(net_debit * 100.0, 2),
        "net_cost_label": "debit" if net_debit >= 0 else "credit",
        "max_profit": round(max(pnls), 2),
        "max_loss": round(min(pnls), 2),
        "breakevens": _breakevens_from_curve(curve),
        "payoff": curve,
    }


def _plan_from_legs(name, kind, legs, quotes, spot, expiry, direction, atm_iv) -> dict | None:
    """Price an explicit leg set (real chain mid, BS fallback) → payoff + PoP + EV. Used to
    compare candidate structures (debit vs credit) and pick the best by expected value."""
    if not legs or not spot:
        return None
    dte = (expiry or {}).get("dte")
    t = (dte / 365.0) if dte else 0.08
    iv0 = atm_iv if (atm_iv and atm_iv > 0) else 0.30
    net, priced, any_real = 0.0, [], False
    for lg in legs:
        d = (quotes.get(round(float(lg["strike"]), 2)) or {}).get(lg["right"]) or {}
        mid = d.get("mid")
        if mid and mid > 0:
            any_real = True
        else:
            liv = d.get("iv") or iv0
            liv = liv / 100.0 if liv > 3 else liv
            mid = _bs_price(spot, lg["strike"], t, liv, lg["right"] == "C")
        net += lg["sign"] * float(mid)
        priced.append({**lg, "price": round(float(mid), 2)})
    curve = _payoff_curve(priced, net, spot)
    pnls = [c["pnl"] for c in curve]
    bes = _breakevens_from_curve(curve)
    mp, ml = round(max(pnls), 2), round(min(pnls), 2)
    pop = ev = None
    if bes:
        pop = _p_above(spot, bes[0], t, iv0) if direction == "long" else _p_below(spot, bes[0], t, iv0)
        if pop is not None:
            ev = round(pop * mp + (1 - pop) * ml, 0)
    return {
        "available": True, "structure": name, "kind": kind, "expiry": expiry,
        "priced_from": "live chain" if any_real else "model (thin quotes)",
        "legs": [{"action": "Buy" if l["sign"] > 0 else "Sell", "right": "Call" if l["right"] == "C" else "Put",
                  "strike": l["strike"], "price": l["price"]} for l in priced],
        "net_cost": round(net * 100.0, 2), "net_cost_label": "debit" if net >= 0 else "credit",
        "max_profit": mp, "max_loss": ml, "breakevens": bes, "payoff": curve,
        "pop_pct": _r(pop * 100, 1) if pop is not None else None, "ev": ev,
    }


def _candidate_plans(direction, setup_type, spot, entry, target, ssup, sres, dealer, strikes, quotes, expiry, atm_iv) -> list[dict]:
    """Both a DEBIT spread (directional/convex) and a CREDIT spread (sell premium AT the level
    you're trading into) with sane strikes. The PRIMARY is chosen STRUCTURALLY, not by EV:
    a pullback / fade entry INTO a level = premium selling → credit spread (positive theta, wider
    margin, no 'OTM debit on a bounce'); only a BREAKOUT (entering ON a break) favors the debit
    spread's convexity. EV/PoP are still computed for transparency and to break ties."""
    walls = (dealer or {}).get("walls") or {}
    cw = (walls.get("call_wall") or {}).get("strike")
    pw = (walls.get("put_wall") or {}).get("strike")
    width = max(1.0, round(spot * 0.03))
    specs = []
    if direction == "long":
        lc = _snap(spot, strikes, "below"); sc = _snap(_first(target, sres, cw), strikes, "near")
        if lc and sc and sc <= lc:
            sc = _next_strike(lc, strikes, True)
        if lc and sc:
            specs.append(("Bull Call Spread", "debit", [{"strike": lc, "right": "C", "sign": 1}, {"strike": sc, "right": "C", "sign": -1}]))
        sp = _snap(_first(entry, ssup, pw), strikes, "below"); lp = _snap((sp or spot) - width, strikes, "below")
        if sp and lp and lp >= sp:
            lp = _next_strike(sp, strikes, False)
        if sp and lp and lp < sp:
            specs.append(("Put Credit Spread", "credit", [{"strike": sp, "right": "P", "sign": -1}, {"strike": lp, "right": "P", "sign": 1}]))
    elif direction == "short":
        lp = _snap(spot, strikes, "above"); sp = _snap(_first(target, ssup, pw), strikes, "near")
        if lp and sp and sp >= lp:
            sp = _next_strike(lp, strikes, False)
        if lp and sp:
            specs.append(("Bear Put Spread", "debit", [{"strike": lp, "right": "P", "sign": 1}, {"strike": sp, "right": "P", "sign": -1}]))
        sc = _snap(_first(entry, sres, cw), strikes, "above"); lc = _snap((sc or spot) + width, strikes, "above")
        if sc and lc and lc <= sc:
            lc = _next_strike(sc, strikes, True)
        if sc and lc and lc > sc:
            specs.append(("Call Credit Spread", "credit", [{"strike": sc, "right": "C", "sign": -1}, {"strike": lc, "right": "C", "sign": 1}]))
    else:
        return []
    # Premium-selling setups (pullback/fade INTO a level) prefer the credit spread; only a
    # breakout (entering ON a break) prefers the debit spread's convexity.
    prefer_credit = setup_type != "breakout"
    built = [p for p in (_plan_from_legs(n, k, legs, quotes, spot, expiry, direction, atm_iv) for n, k, legs in specs) if p]
    for p in built:
        p["preferred"] = (p["kind"] == "credit") if prefer_credit else (p["kind"] == "debit")
        p["why"] = ("Sells premium AT the level you're trading into — positive theta and a wider margin of "
                    "safety than a debit spread bought on the bounce (which goes OTM and fights theta)."
                    if p["kind"] == "credit" else
                    "Directional debit spread — defined risk with convexity if the move runs; best when you "
                    "enter ON a break, not on a fade into a level.")
    # primary = structurally preferred first, then higher EV as the tiebreak
    built.sort(key=lambda p: (1 if p.get("preferred") else 0, p.get("ev") if p.get("ev") is not None else -1e9), reverse=True)
    return built


def _what_to_watch(s, dealer) -> list[str]:
    d, out = s.get("direction"), []
    stop = (s.get("stop") or {}).get("level")
    if stop is not None:
        out.append(f"Invalidation — a daily close {'below' if d == 'long' else 'above'} ${stop} kills the thesis; exit.")
    out.append("Take partial profit at T1 and trail the stop to breakeven; let the rest run to T2.")
    gf = (dealer or {}).get("gamma_flip") or {}
    if gf.get("level") is not None:
        out.append(f"Gamma flip ${gf['level']} — losing it flips dealers short-gamma → expect bigger, faster moves.")
    if s.get("regime_fit") == "counter_regime":
        out.append("Counter-trend trade — keep size small and be quick to cut.")
    out.append("A change-of-character (CHOCH) against you on the 1H/4H is your early warning to tighten or exit.")
    return out


def _enrich_setup(s, spot, atr, em_pct, zones, quotes, expiry, dealer, atm_iv, next_earnings, strikes) -> None:
    direction = s.get("direction")
    entry = (s.get("entry") or {}).get("level")
    stop = (s.get("stop") or {}).get("level")
    tgts = s.get("targets") or []
    t1 = tgts[0].get("level") if tgts else None
    if direction in ("long", "short") and t1 is not None and entry is not None:
        t2 = _second_target(zones, spot, direction, t1)
        if t2 is not None and abs(t2 - entry) > abs(t1 - entry) * 1.05:
            s["targets"] = tgts + [{"level": _r(t2), "label": "extended (T2)",
                                    "rr": _rr(entry, stop, t2) if stop is not None else None}]

    # cap targets to a REALISTIC distance (≤1.5× the 30-day expected move) — no fantasy 43% targets
    if em_pct and direction in ("long", "short"):
        max_dist = spot * (em_pct / 100.0) * 1.5
        for t in s.get("targets", []):
            lvl = t.get("level")
            if lvl is None:
                continue
            if direction == "long" and lvl > spot + max_dist:
                t["level"], t["capped"] = _r(spot + max_dist), True
            elif direction == "short" and lvl < spot - max_dist:
                t["level"], t["capped"] = _r(spot - max_dist), True
        nt1 = s["targets"][0].get("level") if s.get("targets") else None
        if nt1 is not None and entry is not None and stop is not None:
            s["risk_reward"] = _rr(entry, stop, nt1)
            s["targets"][0]["rr"] = s["risk_reward"]

    t1c = s["targets"][0].get("level") if s.get("targets") else None
    # EV-ranked candidate structures (debit vs credit spread) — primary + alternatives
    if direction in ("long", "short"):
        sups = [z for z in zones if z["kind"] == "support"]
        ress = [z for z in zones if z["kind"] == "resistance"]
        ssup = max(sups, key=lambda z: z["score"])["center"] if sups else None
        sres = max(ress, key=lambda z: z["score"])["center"] if ress else None
        cands = _candidate_plans(direction, s.get("type"), spot, entry, t1c, ssup, sres, dealer, strikes, quotes, expiry, atm_iv)
        if cands:
            s["options_plan"] = cands[0]
            s["options_alternatives"] = [{k: c.get(k) for k in ("structure", "kind", "net_cost", "net_cost_label",
                                                                 "max_profit", "max_loss", "breakevens", "pop_pct", "ev", "legs")}
                                         for c in cands[1:]]
        else:
            s["options_plan"] = _options_plan(s.get("options"), expiry, quotes, spot, dealer)
    else:
        s["options_plan"] = _options_plan(s.get("options"), expiry, quotes, spot, dealer)

    s["edge"] = _edge(s, spot, atm_iv, (expiry or {}).get("dte"))
    if direction in ("long", "short") and entry is not None and stop is not None:
        risk_pct = ((s["edge"].get("equity") or {}).get("half_kelly_risk_pct"))
        s["equity_plan"] = _equity_plan(direction, entry, stop, s["targets"], spot, risk_pct)
    s["event_risk"] = _event_risk(next_earnings, expiry)
    s["what_to_watch"] = _what_to_watch(s, dealer)


def _leg_quotes(stock, expiry_date) -> dict:
    """Real per-strike option mids for one expiry: {strike: {'C': {mid,iv}, 'P': {mid,iv}}}."""
    if not expiry_date:
        return {}
    try:
        oc = stock.option_chain(expiry_date)
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for df, right in ((getattr(oc, "calls", None), "C"), (getattr(oc, "puts", None), "P")):
        if df is None or getattr(df, "empty", True):
            continue
        for _, row in df.iterrows():
            k = safe_float(row.get("strike"))
            if not k:
                continue
            bid, ask = safe_float(row.get("bid")), safe_float(row.get("ask"))
            last, iv = safe_float(row.get("lastPrice")), safe_float(row.get("impliedVolatility"))
            mid = (bid + ask) / 2 if (bid and ask and ask >= bid) else (last or bid or ask or 0.0)
            out.setdefault(round(k, 2), {})[right] = {"mid": round(mid, 2), "iv": iv}
    return out


def _dossier(spot, bias, structure, regime, dealer, micro, indicators, zones, setups) -> dict:
    """One consolidated JSON of every indicator + advanced metric + the quant trades — the
    exact payload handed to the LLM for verification."""
    ind = indicators or {}
    tf = (structure or {}).get("timeframes") or {}
    return {
        "spot": _r(spot),
        "bias": bias,
        "regime": (regime or {}).get("regime"),
        "vwap_zscore": (regime or {}).get("zscore"),
        "market_structure": {"bias": (structure or {}).get("bias"),
                             "trend_alignment": {k: (tf.get(k) or {}).get("trend") for k in ("daily", "h4", "h1")},
                             "confluence": (structure or {}).get("confluence")},
        "dealer_gamma": {"net_gex": (dealer or {}).get("net_gex"), "gamma_flip": (dealer or {}).get("gamma_flip"),
                         "gamma_levels": (dealer or {}).get("gamma_levels"), "expected_move": (dealer or {}).get("expected_move")},
        "volume_profile": (micro or {}).get("timeframe_profiles"),
        "naked_pocs": (micro or {}).get("naked_pocs"),
        "avwap": (micro or {}).get("avwap"),
        "indicators": {k: ind.get(k) for k in ("currentRSI", "rsiSignal", "supportLevel", "resistanceLevel",
                                               "macd", "bollingerBands", "movingAverages", "emaCrossover", "volumeAnalysis")},
        "confluence_zones": zones[:8],
        "setups": setups,
    }


def compute_trade_setups(stock) -> dict | None:
    """Fuse all five TA reads into ranked setups (equity + priced-options plans) plus a
    consolidated dossier. Runs sub-computes concurrently; degrades gracefully."""
    try:
        with ThreadPoolExecutor(max_workers=5) as ex:
            f_micro = ex.submit(compute_microstructure, stock)
            f_struct = ex.submit(compute_market_structure, stock)
            f_regime = ex.submit(compute_regime, stock)
            f_dealer = ex.submit(compute_dealer_positioning, stock)
            f_ind = ex.submit(_indicators, stock)
            micro, structure, regime, dealer, indicators = (
                f.result() for f in (f_micro, f_struct, f_regime, f_dealer, f_ind))

        spot = None
        for src in (structure, regime, micro, dealer):
            if src and src.get("price"):
                spot = float(src["price"]); break
        if spot is None and indicators and indicators.get("prices"):
            spot = float(indicators["prices"][-1])
        if spot is None:
            return None

        atr = _atr_from_structure(structure) or _atr_from_indicators(indicators, spot)
        bias = _derive_bias(structure, regime, dealer, indicators)
        levels = _collect_levels(micro, structure, regime, dealer, spot, indicators)
        zones = _cluster_zones(levels, atr, spot)
        em_pct = (((dealer or {}).get("expected_move") or {}).get("em_30d") or {}).get("move_pct")
        mean_price = ((regime or {}).get("zscore") or {}).get("vwap")
        if not mean_price and micro:
            mean_price = ((micro.get("timeframe_profiles") or {}).get("macro") or {}).get("poc")
        strikes = (dealer or {}).get("strikes") or None
        setups = _build_setups(bias, zones, spot, atr, dealer, regime, em_pct, mean_price, strikes)

        # enrich each setup with an equity plan, T1/T2, a management plan, and a priced
        # options plan (real leg quotes + payoff). Fetch the one expiry chain we need.
        expiry = _pick_expiry(dealer)
        quotes = _leg_quotes(stock, expiry.get("date")) if expiry else {}
        atm_iv_pct = (((dealer or {}).get("expected_move") or {}).get("em_30d") or {}).get("iv_atm_pct")
        atm_iv = (atm_iv_pct / 100.0) if atm_iv_pct else None
        next_earn = _next_earnings(stock)
        for s in setups:
            _enrich_setup(s, spot, atr, em_pct, zones, quotes, expiry, dealer, atm_iv, next_earn, strikes)

        return {
            "price": _r(spot),
            "as_of": _now_str(),
            "context": _context(bias, structure, regime, dealer),
            "confluence_zones": zones[:8],
            "setups": setups,
            "dossier": _dossier(spot, bias, structure, regime, dealer, micro, indicators, zones, setups),
            "price_series": (structure or micro or regime or {}).get("price_series"),
            "meta": {"sources_ok": {"micro": bool(micro), "structure": bool(structure),
                                    "regime": bool(regime), "dealer": bool(dealer), "indicators": bool(indicators)}},
        }
    except Exception:  # noqa: BLE001
        return None
