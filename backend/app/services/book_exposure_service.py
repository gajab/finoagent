"""Book-Exposure desk action — DETERMINISTIC portfolio-risk read on how a NEW income trade changes
the user's EXISTING book (their active My-Trades positions).

Rebuilt to be fully quantitative — NO LLM, nothing invented. Everything here is a computed number the
user can trust, reusing the SAME engine that powers "Manage Book" (``book_tail_risk``):

  • Same-name concentration — does the user ALREADY hold this underlying? (surfaced first & loudly)
  • $ book exposure across market moves — full-reprice the whole book at −20…+20%, WITH and WITHOUT the
    new trade, so the user sees today's exposure, the new trade's marginal P&L, and the combined book.
  • Assignment / capital — naked-assignment obligation and BPR, book vs book+trade.
  • Directional delta — beta-weighted $ per 1% SPY move, book vs book+trade (does it add or offset?).
  • Correlated names — REAL trailing-1y daily-return ρ of the candidate vs each book name (only links the
    data actually shows; a curated map is used ONLY to LABEL a correlation that is already measured).
  • Macro sensitivity — measured correlation of the candidate and of the book to rate/oil/gold/USD proxies.
  • Findings + recommendations — rule-based, each tied to a specific computed number.

Not part of the grade — portfolio-level risk context, available on an explicit click, with no LLM call.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

# Reuse the Manage-Book engine's building blocks so the numbers match that surface exactly.
from .book_tail_risk import (
    _position_greeks, _compute_betas, reprice_scenario, _vol_shock_for, _native,
)
# Shared correlated-assets registry — sector / theme / macro / peer profiles + the correlation
# primitives, used by BOTH this surface and Tax-Loss Harvesting so their numbers agree.
from .correlated_assets_service import (
    get_profiles, fetch_returns, pearson, related_earnings_in_window,
    _THEMES, _MACRO_PROXIES, _FACTOR_LABEL,
)

logger = logging.getLogger(__name__)

# Market-move ladder for the scenario table (each name moves × its β). Symmetric so the user sees both
# directions; the vol shock per move is the same skew-aware rule Manage-Book uses.
_MOVE_LADDER = [-0.20, -0.10, -0.05, 0.05, 0.10, 0.20]
# Macro factors we surface as a book-level concentration (exclude the broad 'market' factor — that is
# already the directional read — and keep the specific, actionable drivers).
_MACRO_FACTORS = [m for m in _MACRO_PROXIES if m["factor"] != "market"]


def _norm(t: Any) -> str:
    return str(t or "").strip().upper()


# ─────────────────────────────────────────────────────────────────────────────
# Candidate → synthetic strategy so it flows through the exact same _position_greeks builder.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class _SynthStrategy:
    ticker: str
    legs_data: str
    name: str = "Candidate trade"
    strategy_type: Optional[str] = None


_SHORT_CALL_STRUCTS = {"naked_call", "call_credit_spread"}


def _candidate_legs(candidate: dict) -> list[dict]:
    """Normalize the desk candidate's legs into the {type, action, strike, expiration, qty, iv} shape
    _position_greeks expects. Falls back to reconstructing from short/long strike + structure."""
    contracts = float(candidate.get("contracts") or 1) or 1
    out: list[dict] = []
    for l in candidate.get("legs") or []:
        typ = str(l.get("type") or l.get("right") or "").upper()
        right = "call" if typ.startswith("C") or "CALL" in typ else "put" if typ.startswith("P") or "PUT" in typ else None
        if right is None or not l.get("strike"):
            continue
        out.append({
            "type": right,
            "action": str(l.get("action") or l.get("side") or "SELL").upper(),
            "strike": float(l["strike"]),
            "expiration": l.get("expiration") or l.get("expiry") or candidate.get("expiration"),
            "qty": float(l.get("qty") or l.get("contracts") or contracts),
            "iv": l.get("iv"),
        })
    if not out and candidate.get("short_strike"):
        exp = candidate.get("expiration")
        right = "call" if candidate.get("structure") in _SHORT_CALL_STRUCTS else "put"
        out.append({"type": right, "action": "SELL", "strike": float(candidate["short_strike"]),
                    "expiration": exp, "qty": contracts, "iv": None})
        if candidate.get("long_strike"):
            out.append({"type": right, "action": "BUY", "strike": float(candidate["long_strike"]),
                        "expiration": exp, "qty": contracts, "iv": None})
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Correlation / macro helpers (the batched download + Pearson ρ live in correlated_assets_service).
# ─────────────────────────────────────────────────────────────────────────────
def _book_driver_returns(positions: list[dict], returns: dict[str, list[float]]) -> Optional[list[float]]:
    """Signed $-exposure-weighted return series for the whole book — its synthetic P&L driver, so we can
    measure what macro factor the BOOK (not just one name) actually loads on."""
    weights: dict[str, float] = {}
    for p in positions:
        tk = p["ticker"]
        w = p.get("net_delta", 0.0) * p.get("beta", 1.0) * p.get("spot", 0.0)   # signed $ per 1% move
        weights[tk] = weights.get(tk, 0.0) + w
    series = [(tk, returns[tk]) for tk in weights if tk in returns]
    if not series:
        return None
    n = min(len(r) for _, r in series)
    if n < 40:
        return None
    gross = sum(abs(w) for w in weights.values()) or 1.0
    agg = [0.0] * n
    for tk, r in series:
        w = weights[tk] / gross
        r = r[-n:]
        for i in range(n):
            agg[i] += w * r[i]
    return agg


# ─────────────────────────────────────────────────────────────────────────────
# Directional / capital summaries.
# ─────────────────────────────────────────────────────────────────────────────
def _spy_delta_per_pct(positions: list[dict]) -> float:
    """$ the book gains per +1% SPY move (β-weighted net delta notional)."""
    return sum(p.get("net_delta", 0.0) * p.get("beta", 1.0) * p.get("spot", 0.0) for p in positions) / 100.0


def _scenario_table(book: list[dict], withc: list[dict], r: float) -> list[dict]:
    rows = []
    for mv in _MOVE_LADDER:
        vshock = _vol_shock_for(mv)
        bp = reprice_scenario(book, mv, vshock, r) if book else 0.0
        wp = reprice_scenario(withc, mv, vshock, r)
        rows.append({"move_pct": round(mv * 100), "book_pnl": round(bp), "with_pnl": round(wp),
                     "delta_pnl": round(wp - bp)})
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Main — deterministic exposure computation.
# ─────────────────────────────────────────────────────────────────────────────
async def compute_book_exposure(candidate: dict, strategies: list, quote_source: str,
                                user, db) -> dict:
    """Compute how ``candidate`` changes the risk of the user's active ``strategies`` book.

    ``strategies`` are the raw active SavedStrategy ORM rows (My Trades, first tab) — passed straight
    through the Manage-Book position builder so the numbers reconcile with that surface.
    """
    from .quote_providers import get_provider
    from .derivative_income_service import _get_sofr

    candidate = candidate or {}
    new_tk = _norm(candidate.get("ticker"))
    provider = get_provider(quote_source, user=user, db=db)
    try:
        r = (await _get_sofr())[0]
    except Exception:  # noqa: BLE001
        r = 0.045
    today = date.today()
    spot_cache: dict = {}
    chain_cache: dict = {}

    # 1) Build the candidate position (must succeed — it's the whole point).
    cand_legs = _candidate_legs(candidate)
    if not cand_legs:
        return {"ticker": new_tk, "error": "Could not read the candidate trade's legs to compare against the book."}
    cand_strategy = _SynthStrategy(ticker=new_tk, legs_data=json.dumps(cand_legs),
                                   strategy_type=candidate.get("structure"))
    try:
        cand_pos = await _position_greeks(cand_strategy, provider, r, today, spot_cache, chain_cache)
    except Exception as exc:  # noqa: BLE001
        logger.info("book-exposure: candidate pricing failed for %s: %s", new_tk, exc)
        cand_pos = None
    if not cand_pos:
        return {"ticker": new_tk, "error": "Could not price the candidate trade (no live quotes) to compare against the book."}

    # 2) Build the existing book positions (same builder as Manage-Book).
    book_positions: list[dict] = []
    for s in strategies or []:
        try:
            p = await _position_greeks(s, provider, r, today, spot_cache, chain_cache)
        except Exception:  # noqa: BLE001
            p = None
        if p:
            book_positions.append(p)

    # 3) Betas (Manage-Book β) + trailing-1y returns from the shared registry (batched, DB-cached,
    #    refreshed with recent data — the 'recompute the correlation matrix' step).
    all_tickers = [p["ticker"] for p in book_positions] + [new_tk]
    macro_tickers = [m["proxy"] for m in _MACRO_PROXIES]
    betas, returns = await asyncio.gather(
        asyncio.to_thread(_compute_betas, all_tickers),
        fetch_returns(all_tickers + macro_tickers, db),
    )
    for p in book_positions + [cand_pos]:
        p["beta"] = betas.get(p["ticker"], 1.0)
    with_positions = book_positions + [cand_pos]

    # ── Empty book: no comparison to make. ──────────────────────────────────
    if not book_positions:
        return _native({
            "ticker": new_tk, "empty_book": True, "verdict": "neutral",
            "headline": "Your book is empty — this would be your first position.",
            "key_points": [{"tone": "info",
                            "text": "No open positions to overlap with yet; book-concentration risk starts here."}],
            "recommendation": "Size it on its own merits.",
            "book": {"position_count": 0, "names": []},
            "candidate": {"ticker": new_tk, "structure": candidate.get("structure"), "bpr": cand_pos.get("capital")},
        })

    book_names = sorted({p["ticker"] for p in book_positions})

    # Structural profiles (sector / theme / macro-factor / peers) from the shared registry.
    profiles = await get_profiles([new_tk] + book_names, db)
    cand_prof = profiles.get(new_tk, {})

    # ── Same-name — combined POSTURE (never sum mutually-exclusive strangle wings). ──
    same = [p for p in book_positions if p["ticker"] == new_tk]
    same_name = _same_name_analysis(same, cand_pos, new_tk) if same else None

    # ── $ scenario table (the hero): full reprice, book vs book+trade. ──────
    scenarios = _scenario_table(book_positions, with_positions, r)
    dir_lean, dir_plain = _direction_from_scenarios(scenarios)

    # ── Directional delta ($ per 1% market) — kept for the details drawer. ──
    book_delta = _spy_delta_per_pct(book_positions)
    with_delta = _spy_delta_per_pct(with_positions)
    cand_delta = _spy_delta_per_pct([cand_pos])
    book_bpr = sum(p.get("capital", 0.0) for p in book_positions)

    # ── Overlaps against the book (registry-enriched). ──────────────────────
    correlations = _correlations(new_tk, book_positions, returns, profiles)   # measured ρ, theme-labelled
    sector_overlap = _sector_overlap(cand_prof, profiles, book_positions)      # same GICS sector
    theme_overlap = _theme_overlap(cand_prof, profiles, book_names)            # crypto / gold / AI-datacenter …
    macro = _macro_overlap(new_tk, book_positions, returns)                    # measured rate/oil/gold/… concentration

    # ── Related bellwether earnings inside the trade window (real dates). ───
    themes_present = set(cand_prof.get("theme_keys", []))
    for bn in book_names:
        themes_present |= set(profiles.get(bn, {}).get("theme_keys", []))
    dte = int(candidate.get("dte") or 45)
    earnings = await related_earnings_in_window(themes_present, new_tk, dte, db)

    # ── Verdict + plain-language assessment. ───────────────────────────────
    verdict, headline, key_points, recommendation = _assess(
        candidate, cand_prof, same_name, scenarios, dir_lean, book_delta, with_delta, cand_delta,
        correlations, sector_overlap, theme_overlap, macro, earnings)

    return _native({
        "ticker": new_tk,
        "verdict": verdict,
        "headline": headline,
        "key_points": key_points,
        "recommendation": recommendation,
        "market_move": {
            "rows": scenarios,
            "down20": next((s for s in scenarios if s["move_pct"] == -20), None),
            "up20": next((s for s in scenarios if s["move_pct"] == 20), None),
            "direction_plain": dir_plain,
        },
        "same_name": same_name,
        "theme_overlap": theme_overlap,
        "sector_overlap": sector_overlap,
        "correlations": correlations[:12],
        "macro": macro,
        "related_earnings": earnings,
        "candidate_profile": {
            "sector": cand_prof.get("sector"), "industry": cand_prof.get("industry"),
            "themes": [t["label"] for t in cand_prof.get("themes", [])],
            "macro_factors": [_FACTOR_LABEL.get(f, f) for f in cand_prof.get("macro_factors", [])],
        },
        "direction": {"book_per_pct": round(book_delta), "with_per_pct": round(with_delta),
                      "trade_per_pct": round(cand_delta), "lean": dir_lean},
        "capital": {"book_bpr": round(book_bpr), "candidate_bpr": round(cand_pos.get("capital", 0.0))},
        "book": {"position_count": len(book_positions), "names": book_names},
    })


# ─────────────────────────────────────────────────────────────────────────────
# Assessment helpers — verdict, plain-language key points, correct same-name math.
# ─────────────────────────────────────────────────────────────────────────────
def _money(n) -> str:
    n = float(n or 0)
    return f"${abs(round(n)):,.0f}" if n >= 0 else f"−${abs(round(n)):,.0f}"


def _num(n) -> str:
    if n is None:
        return "—"
    n = float(n)
    return f"{n:,.0f}" if abs(n - round(n)) < 0.01 else f"{n:,.2f}"


def _pretty_structs(structs: list) -> str:
    seen: list[str] = []
    for s in structs:
        lbl = (s or "position").replace("_", " ")
        if lbl not in seen:
            seen.append(lbl)
    return ", ".join(seen) or "position"


def _short_legs_of(pos_list: list[dict]) -> tuple[list[dict], list[dict]]:
    """Short puts and short calls across positions, tagging each as covered/protected."""
    puts, calls = [], []
    for p in pos_list:
        legs = p.get("legs", [])
        covered = (p.get("structure") == "covered_call") or p.get("has_stock")
        long_put = any(l["right"] == "P" and l["sign"] > 0 for l in legs)
        long_call = any(l["right"] == "C" and l["sign"] > 0 for l in legs)
        for l in legs:
            if l["sign"] >= 0:
                continue
            days = round(float(l.get("dte_years") or 0) * 365)
            if l["right"] == "P":
                puts.append({"strike": l["strike"], "qty": l["qty"], "protected": long_put, "dte_days": days})
            else:
                calls.append({"strike": l["strike"], "qty": l["qty"], "covered": bool(covered or long_call), "dte_days": days})
    return puts, calls


def _same_name_analysis(same: list[dict], cand_pos: dict, new_tk: str) -> dict:
    """Combined same-name posture. A short put and short call on ONE name form a STRANGLE — only one
    wing can finish ITM, so we NEVER sum their capitals (that was the bug); we describe each side's
    real obligation and the true 'loses either way' shape."""
    puts, calls = _short_legs_of(same + [cand_pos])
    naked_calls = [c for c in calls if not c["covered"]]
    csp = [p for p in puts if not p["protected"]]
    downside_outlay = sum(p["strike"] * 100 * p["qty"] for p in csp)      # cash if short puts assigned on a drop
    highest_put = max((p["strike"] for p in puts), default=None)
    lowest_naked_call = min((c["strike"] for c in naked_calls), default=None)
    has_put, has_call = bool(puts), bool(calls)
    posture = ("short_strangle" if has_put and has_call
               else "stacked_calls" if len(calls) >= 2
               else "stacked_puts" if len(puts) >= 2
               else "combined")
    dtes = {l["dte_days"] for l in (puts + calls)}
    return {
        "ticker": new_tk, "count": len(same),
        "existing": [{"structure": p.get("structure"), "n_short": p.get("n_short"),
                      "capital": p.get("capital")} for p in same],
        "posture": posture,
        "downside_outlay": round(downside_outlay) if downside_outlay else None,
        "upside_unbounded": bool(naked_calls),
        "highest_put": highest_put, "lowest_naked_call": lowest_naked_call,
        "same_expiry": len(dtes) <= 1,
        "n_short_puts": len(puts), "n_short_calls": len(calls),
    }


def _direction_from_scenarios(scenarios: list[dict]) -> tuple[str, str]:
    """Plain-language net direction, read straight off the full-reprice ±20% P&L (no jargon)."""
    up = next((s["with_pnl"] for s in scenarios if s["move_pct"] == 20), None)
    dn = next((s["with_pnl"] for s in scenarios if s["move_pct"] == -20), None)
    if up is None or dn is None:
        return "unknown", "—"
    if up < 0 and dn < 0:
        return "short_vol", "Loses on a big move in EITHER direction — the book is short volatility."
    if up > 0 and dn > 0:
        return "long_vol", "Gains on a big move either way — long volatility."
    if up > 0 and dn < 0:
        return "long", "Net LONG — gains if the market rises, loses if it falls."
    return "short", "Net SHORT — loses if the market rises, gains if it falls."


def _correlations(new_tk: str, book_positions: list[dict], returns: dict, profiles: dict) -> list[dict]:
    """Measured 1-yr ρ (|ρ|≥0.6) of the candidate vs each book name; labelled with a SHARED theme
    only when both names actually sit in it (the label never asserts a link the data didn't show)."""
    out: list[dict] = []
    if new_tk not in returns:
        return out
    base, seen = returns[new_tk], set()
    cand_themes = set(profiles.get(new_tk, {}).get("theme_keys", []))
    for p in book_positions:
        tk = p["ticker"]
        if tk == new_tk or tk in seen or tk not in returns:
            continue
        seen.add(tk)
        rho = pearson(base, returns[tk])
        if rho is not None and abs(rho) >= 0.6:
            shared = cand_themes & set(profiles.get(tk, {}).get("theme_keys", []))
            label = _THEMES[sorted(shared)[0]]["label"] if shared else None
            out.append({"ticker": tk, "rho": round(rho, 2), "cluster": label})
    out.sort(key=lambda x: -abs(x["rho"]))
    return out


def _sector_overlap(cand_prof: dict, profiles: dict, book_positions: list[dict]) -> Optional[dict]:
    """Distinct book names (and their capital) in the candidate's GICS sector."""
    sec = (cand_prof.get("sector") or "").strip()
    if not sec:
        return None
    names: list[str] = []
    cap = 0.0
    for p in book_positions:
        if (profiles.get(p["ticker"], {}).get("sector") or "") == sec:
            cap += p.get("capital", 0.0)
            if p["ticker"] not in names:
                names.append(p["ticker"])
    if not names:
        return None
    return {"sector": sec, "book_tickers": sorted(names), "capital": round(cap)}


def _theme_overlap(cand_prof: dict, profiles: dict, book_names: list[str]) -> list[dict]:
    """Themes the candidate shares with book names (crypto / gold complex / AI data-center …)."""
    out: list[dict] = []
    for t in cand_prof.get("themes", []):
        key = t["key"]
        shares = sorted(bn for bn in book_names if key in profiles.get(bn, {}).get("theme_keys", []))
        if shares:
            out.append({"key": key, "theme": t["label"], "driver": t["driver"],
                        "bellwether": t.get("bellwether"), "note": t.get("note"), "book_tickers": shares})
    out.sort(key=lambda x: -len(x["book_tickers"]))
    return out


def _macro_overlap(new_tk: str, book_positions: list[dict], returns: dict) -> list[dict]:
    """Only surface a macro factor when BOTH the trade and the book load on it meaningfully (|ρ|≥0.4)
    and in the SAME direction — a real shared exposure the user can act on. Noise is dropped."""
    out: list[dict] = []
    book_driver = _book_driver_returns(book_positions, returns)
    base = returns.get(new_tk)
    if base is None or book_driver is None:
        return out
    for m in _MACRO_FACTORS:
        pr = returns.get(m["proxy"])
        if not pr:
            continue
        c, b = pearson(base, pr), pearson(book_driver, pr)
        if c is None or b is None or abs(c) < 0.4 or abs(b) < 0.4 or c * b <= 0:
            continue
        label = m["label"].lower()
        moves = "rises" if c > 0 else "falls"
        out.append({"factor": m["label"], "candidate_rho": round(c, 2), "book_rho": round(b, 2),
                    "plain": f"Both this trade and your book move with {label} (they gain when {label} {moves}) — a "
                             f"shared macro driver, so {label} moves them together."})
    return out


def _assess(candidate, cand_prof, same_name, scenarios, dir_lean, book_delta, with_delta, cand_delta,
            correlations, sector_overlap, theme_overlap, macro, earnings) -> tuple[str, str, list[dict], str]:
    """Deterministic verdict + prioritized, plain-language key points + one recommendation."""
    tk = _norm(candidate.get("ticker"))
    struct = (candidate.get("structure") or "trade").replace("_", " ")
    kp: list[dict] = []
    verdict = "neutral"
    rec = None

    # 1) Same underlying — the loudest signal, with CORRECT strangle/stack math.
    if same_name:
        p = same_name
        ex = _pretty_structs([e["structure"] for e in p["existing"]])
        when = "the same expiry" if p["same_expiry"] else "different expiries"
        if p["posture"] == "short_strangle":
            bits = []
            if p.get("downside_outlay"):
                bits.append(f"below ${_num(p['highest_put'])} you're assigned and buy shares for {_money(p['downside_outlay'])}")
            if p.get("upside_unbounded"):
                bits.append(f"above ${_num(p['lowest_naked_call'])} the loss is unlimited")
            kp.append({"tone": "bad",
                       "text": f"You already hold a {ex} on {tk}. Adding this {struct} turns {tk} into a short "
                               f"strangle — you now lose on a big move EITHER way: {', and '.join(bits)} "
                               f"(only one side can be assigned; they share {when})."})
        elif p["posture"] in ("stacked_calls", "stacked_puts"):
            side = "upside (calls)" if p["posture"] == "stacked_calls" else "downside (puts)"
            kp.append({"tone": "bad",
                       "text": f"You already hold a {ex} on {tk}. This stacks another short on the same {side} of the "
                               f"SAME name — a single {tk} move hits both. That's leverage on one name, not diversification."})
        else:
            kp.append({"tone": "bad",
                       "text": f"You already hold a {ex} on {tk}. Adding this piles more risk onto one name."})
        verdict = "concentrates"
        rec = f"Prefer a different underlying, or make {tk} defined-risk, rather than adding another uncovered short on a name you already hold."

    # 2) THEME / dependency overlap — the "AI data-center / crypto / gold complex" grouping.
    theme_names: set[str] = set()
    for th in theme_overlap[:2]:
        others = th["book_tickers"]
        theme_names.update(others)
        n = len(others) + 1
        dep = f" {th['note']}" if th.get("note") else ""
        kp.append({"tone": "warn",
                   "text": f"This is your #{n} {th['theme']} name (with {', '.join(others[:4])}"
                           f"{'…' if len(others) > 4 else ''}) — all keyed to {th['driver']}. One move in that driver "
                           f"hits them together, so it's one theme bet, not diversification.{dep}"})
        if verdict == "neutral":
            verdict = "concentrates"
        if not rec:
            rec = f"Treat your {th['theme']} names as one position when sizing — you're adding to {len(others)} you already hold."

    # 3) Correlated names not already explained by a shared theme.
    strong = [c for c in correlations if abs(c["rho"]) >= 0.6 and c["ticker"] not in theme_names][:3]
    if strong:
        names = ", ".join(c["ticker"] for c in strong)
        kp.append({"tone": "warn",
                   "text": f"{tk} moves almost in lockstep with {names} you already hold (correlation "
                           f"{strong[0]['rho']:+.2f}, measured on 1-year returns) — effectively the same bet."})
        if verdict == "neutral":
            verdict = "concentrates"
        if not rec:
            rec = f"Count {tk} and {strong[0]['ticker']} as one position when you size this."

    # 4) Sector concentration — only when themes didn't already explain it.
    if sector_overlap and not theme_overlap and len(sector_overlap["book_tickers"]) >= 2:
        so = sector_overlap
        kp.append({"tone": "warn",
                   "text": f"Your book already holds {len(so['book_tickers'])} {so['sector']} names "
                           f"({', '.join(so['book_tickers'][:4])}{'…' if len(so['book_tickers']) > 4 else ''}); "
                           f"this adds another to a book already tilted to that sector."})
        if verdict == "neutral":
            verdict = "concentrates"

    # 5) Directional lean (plain, from the reprice — no per-1% jargon in the summary).
    if abs(book_delta) > 5 and abs(cand_delta) > 1:
        if book_delta * cand_delta > 0:
            lean = "loses when the market rises" if with_delta < 0 else "gains when the market rises (net long)"
            kp.append({"tone": "warn",
                       "text": f"Your book already {lean}; this trade deepens that same tilt rather than balancing it."})
            if verdict == "neutral":
                verdict = "concentrates"
        else:
            kp.append({"tone": "good",
                       "text": "This trade leans the OPPOSITE way to your book, so it offsets and softens your net "
                               "market direction."})
            if verdict == "neutral":
                verdict = "diversifies"

    # 6) Related bellwether earnings inside the window — a shared catalyst.
    if earnings:
        e = earnings[0]
        kp.append({"tone": "warn",
                   "text": f"{e['bellwether']} reports in {e['days_out']} days ({e['date']}) — it moves the whole "
                           f"{e['theme']} group your book is exposed to, so one print can gap several positions at once."})
        if verdict == "neutral":
            verdict = "concentrates"

    # 7) Shared macro factor (one line, only if meaningful).
    if macro:
        kp.append({"tone": "info", "text": macro[0]["plain"]})

    # 8) Nothing flagged → genuinely clean.
    if not kp:
        kp.append({"tone": "good",
                   "text": "No overlap with your book — different name, sector and theme, low correlation, and it "
                           "doesn't deepen your market tilt."})
        verdict = "diversifies"

    if not rec:
        rec = ("Fits cleanly alongside your book — size it on its own merits." if verdict != "concentrates"
               else "Trim the size or pick a less-correlated name — this adds to risk you already carry.")

    headline = _headline(verdict, same_name, theme_overlap, strong, sector_overlap, tk)
    return verdict, headline, kp[:5], rec


def _headline(verdict: str, same_name, theme_overlap, strong, sector_overlap, tk: str) -> str:
    if same_name:
        return f"Doubles down on {tk} — you already hold a position there."
    if theme_overlap:
        return f"Concentrates your {theme_overlap[0]['theme']} exposure."
    if verdict == "concentrates" and strong:
        return f"Concentrates risk you already carry — {tk} tracks names in your book."
    if verdict == "concentrates" and sector_overlap:
        return f"Adds to a book already heavy in {sector_overlap['sector']}."
    if verdict == "concentrates":
        return "Deepens your book's existing market tilt."
    if verdict == "diversifies":
        return "Diversifies — it offsets risk already in your book."
    return "Sits cleanly alongside your book."
