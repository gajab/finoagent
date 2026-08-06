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

from concurrent.futures import ThreadPoolExecutor

from .microstructure_service import _r, _now_str, compute_microstructure
from .market_structure_service import compute_market_structure
from .regime_service import compute_regime
from .dealer_positioning_service import compute_dealer_positioning


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


def _collect_levels(micro, structure, regime, dealer, spot) -> list[dict]:
    """Every tradeable price from the four reads → tagged, weighted levels."""
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

    # --- dealer: gamma flip, walls, expected-move bounds ---
    if dealer:
        gf = dealer.get("gamma_flip")
        if gf and gf.get("level"):
            add(gf["level"], "magnet", "gamma_flip", "Gamma flip", 3.0)
        walls = dealer.get("walls") or {}
        if walls.get("call_wall") and walls["call_wall"].get("strike"):
            add(walls["call_wall"]["strike"], "resistance", "call_wall", "Call wall", 2.5)
        if walls.get("put_wall") and walls["put_wall"].get("strike"):
            add(walls["put_wall"]["strike"], "support", "put_wall", "Put wall", 2.5)
        em = (dealer.get("expected_move") or {}).get("em_30d")
        if em and em.get("upper"):
            add(em["upper"], "resistance", "expected_move", "Expected-move high (30d)", 1.5)
            add(em["lower"], "support", "expected_move", "Expected-move low (30d)", 1.5)
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

def _derive_bias(structure, regime, dealer) -> dict:
    votes = 0
    reasons = []
    s_bias = ((structure or {}).get("bias") or {}).get("overall")
    if s_bias == "bullish":
        votes += 1; reasons.append("market structure is bullish")
    elif s_bias == "bearish":
        votes -= 1; reasons.append("market structure is bearish")

    daily = ((structure or {}).get("timeframes") or {}).get("daily") or {}
    if daily.get("trend") == "up":
        votes += 1
    elif daily.get("trend") == "down":
        votes -= 1

    if dealer:
        gf = dealer.get("gamma_flip") or {}
        if gf.get("side") == "below":            # spot above flip → long gamma / supportive
            votes += 0.5; reasons.append("spot is above the gamma flip")
        elif gf.get("side") == "above":
            votes -= 0.5; reasons.append("spot is below the gamma flip")

    direction = "bullish" if votes >= 1 else "bearish" if votes <= -1 else "neutral"
    strength = "strong" if abs(votes) >= 2 else "moderate" if abs(votes) >= 1 else "weak"
    reg = (regime or {}).get("regime") or {}
    return {
        "direction": direction,
        "strength": strength,
        "score": round(votes, 1),
        "rationale": ("; ".join(reasons[:3]) or "no dominant directional signal").capitalize() + ".",
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
    stretched_hi = zval.get("z", 0) >= 1.5
    stretched_lo = zval.get("z", 0) <= -1.5
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


def compute_trade_setups(stock) -> dict | None:
    """Fuse all four TA reads into ranked setups. Runs sub-computes concurrently; degrades
    gracefully. ``None`` only if nothing usable could be computed."""
    try:
        with ThreadPoolExecutor(max_workers=4) as ex:
            f_micro = ex.submit(compute_microstructure, stock)
            f_struct = ex.submit(compute_market_structure, stock)
            f_regime = ex.submit(compute_regime, stock)
            f_dealer = ex.submit(compute_dealer_positioning, stock)
            micro, structure, regime, dealer = (f.result() for f in (f_micro, f_struct, f_regime, f_dealer))

        spot = None
        for src in (structure, regime, micro, dealer):
            if src and src.get("price"):
                spot = float(src["price"]); break
        if spot is None:
            return None

        atr = _atr_from_structure(structure)
        bias = _derive_bias(structure, regime, dealer)
        levels = _collect_levels(micro, structure, regime, dealer, spot)
        zones = _cluster_zones(levels, atr, spot)
        em_pct = (((dealer or {}).get("expected_move") or {}).get("em_30d") or {}).get("move_pct")
        mean_price = ((regime or {}).get("zscore") or {}).get("vwap")
        if not mean_price and micro:
            mean_price = ((micro.get("timeframe_profiles") or {}).get("macro") or {}).get("poc")
        strikes = (dealer or {}).get("strikes") or None
        setups = _build_setups(bias, zones, spot, atr, dealer, regime, em_pct, mean_price, strikes)

        return {
            "price": _r(spot),
            "as_of": _now_str(),
            "context": _context(bias, structure, regime, dealer),
            "confluence_zones": zones[:8],
            "setups": setups,
            "price_series": (structure or micro or regime or {}).get("price_series"),
            "meta": {"sources_ok": {"micro": bool(micro), "structure": bool(structure),
                                    "regime": bool(regime), "dealer": bool(dealer)}},
        }
    except Exception:  # noqa: BLE001
        return None
