"""Multi-agent debate — Bull vs. Bear, adjudicated by a Judge.

Three LLM personas run in a fixed graph over one shared evidence dossier, so the
argument is grounded in filings/data rather than hallucinated:

  1. **Bull**  — long-biased analyst; builds the strongest upside case and commits
     to a definitive 12-month upside %.
  2. **Bear**  — forensic short-seller; dismantles the Bull's thesis (no new
     target of its own).
  3. **Judge** — quant PM; scores how much of the Bull thesis survives the Bear
     and emits strict JSON: ``{view_return, base_confidence, rationale}``.

The Judge's ``view_return`` / ``base_confidence`` are exactly the (Q, ω) inputs a
Black-Litterman step needs — but for now the endpoint just surfaces the whole
debate on the Stock Research page so the reasoning can be inspected.

Evidence dossier = EDGAR filing excerpts (``edgar_service``) + fundamentals /
earnings (``stock_service``) + a macro snapshot (``rates_service``, keyless FRED).
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import re

from .stock_service import fetch_stock_data
from .edgar_service import gather_company_filings
from .dcf_service import run_dcf_analysis, dcf_from_override
from .valuation_data import fetch_valuation_inputs
from .valuation_engine import Claim, assemble
from . import rates_service

logger = logging.getLogger(__name__)

# Keep the dossier bounded so prompts stay cheap/fast. The extractor returns the
# MD&A narrative first (up to ~12k chars, safe-harbor preamble stripped); 10000
# captures the results-of-operations discussion (revenue/margin drivers). The whole
# dossier is re-sent on each of ~N rounds × 3 agents, so this is the main cost/quality
# knob — raise it for deeper reading (higher token cost) or move to agentic retrieval.
_MAX_DOCS = 3
_EXCERPT_CHARS = 10000

# 8-K cover pages / registration boilerplate carry no analytical signal — drop
# any excerpt that trips several of these markers so a doc slot isn't wasted.
_BOILERPLATE_MARKERS = (
    "check the appropriate box",
    "i.r.s. employer identification",
    "telephone number, including area code",
    "emerging growth company",
    "securities registered pursuant to section 12",
    "former name or former address",
)

# Debate loop: iterate until the Judge is confident or the debate stops adding
# signal. Each round = 3 LLM calls, so cap hard to bound cost/latency.
_MAX_ROUNDS = 4
_ROUNDS_CEILING = 6
_CONFIDENCE_TARGET = 0.80

# Valuation-bridge guardrails — the price target is EPS×multiple, recomputed in
# Python, then sanity-checked so the LLM can't dump a number.
_MULT_BAND = 0.30          # target multiple must sit within ±30% of forward P/E …
_LARGE_BEAT_PCT = 15.0     # … and an EPS beat over consensus above this is "large"
_MEGACAP_USD = 500e9       # above this market cap …
_MEGACAP_UPSIDE = 60.0     # … an upside beyond this is flagged as aggressive
_STATED_VS_COMPUTED_TOL = 5.0   # pp gap between LLM-stated & recomputed upside

# Structural valuation — after the debate concludes, an extractor turns the surviving
# arguments into tiered DRIVER-CLAIMS, and valuation_engine.assemble() prices them through
# a real P&L (covariance-aware) with warranted multiples + fade priors. The LLM only
# extracts+classifies; the engine owns every number. This is the authoritative headline.
_CLAIM_DRIVERS = {"revenue", "margin", "buyback", "other", "multiple"}
_MAX_CLAIMS = 6

# Compact macro snapshot (keyless FRED series ids → label).
_MACRO_SERIES = {
    "DGS10": "10Y Treasury %",
    "DGS2": "2Y Treasury %",
    "T10Y2Y": "10Y–2Y spread %",
    "FEDFUNDS": "Fed Funds %",
    "UNRATE": "Unemployment %",
    "VIXCLS": "VIX",
}


# ---------------------------------------------------------------------------
# Evidence dossier
# ---------------------------------------------------------------------------

def _money(v: float) -> str:
    a = abs(v)
    if a >= 1e9:
        return f"${v / 1e9:.1f}B"
    if a >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"{v:.2f}"


def _financial_deltas(sd: dict) -> list[str]:
    """Turn the raw history arrays into trend FACTS (latest, YoY, multi-yr CAGR)
    so agents can cite growth/deceleration instead of eyeballing raw numbers.

    A net-income-vs-FCF gap is called out explicitly — it's the single most useful
    quality-of-earnings tell and exactly the kind of thing a retail user misses.
    """
    fin = sd.get("financials", {}) or {}
    out: list[str] = []

    def _trend(label: str, arr, money: bool = True) -> None:
        vals = [float(v) for v in (arr or []) if isinstance(v, (int, float))]
        if len(vals) < 2:
            return
        latest, prev = vals[-1], vals[-2]
        n = len(vals) - 1
        s = f"{label}: {_money(latest) if money else f'{latest:.2f}'}"
        if prev:
            s += f" ({(latest - prev) / abs(prev) * 100:+.1f}% YoY"
            if len(vals) >= 3 and vals[0] > 0 and latest > 0:
                s += f", {((latest / vals[0]) ** (1 / n) - 1) * 100:+.1f}% {n}y CAGR"
            s += ")"
        out.append(s)

    _trend("Revenue", fin.get("revenue"))
    _trend("EPS", fin.get("eps"), money=False)
    _trend("Free cash flow", fin.get("freeCashFlow"))

    # Flag an EPS-vs-FCF divergence (buyback-flattered EPS while cash flow falls) —
    # a classic quality-of-earnings tell a retail investor misses. Uses same-basis
    # YoY growth rates, so no annual-vs-quarterly period mismatch.
    def _yoy(arr):
        v = [float(x) for x in (arr or []) if isinstance(x, (int, float))]
        return ((v[-1] - v[-2]) / abs(v[-2]) * 100) if len(v) >= 2 and v[-2] else None
    eps_g, fcf_g = _yoy(fin.get("eps")), _yoy(fin.get("freeCashFlow"))
    if eps_g is not None and fcf_g is not None and (eps_g - fcf_g) >= 15:
        out.append(f"⚠ EPS growth ({eps_g:+.0f}%) outrunning FCF growth ({fcf_g:+.0f}%) YoY — check buybacks/quality of earnings")
    return out


def _fundamentals_text(ticker: str, sd: dict) -> str:
    """Compact fundamentals + computed trend facts for the dossier."""
    if not sd:
        return f"Ticker: {ticker} (no market data available)"
    parts = [
        f"Company: {sd.get('companyName', ticker)} ({ticker})",
        f"Price: ${sd.get('price', 'N/A')} ({sd.get('changePercent', 'N/A')}%)",
        f"Market Cap: {sd.get('marketCap', 'N/A')} | Sector: {sd.get('sector', 'N/A')} | Industry: {sd.get('industry', 'N/A')}",
    ]
    earn = sd.get("earnings", {}) or {}
    if earn.get("available"):
        parts.append(
            f"Valuation/Earnings — Trailing P/E {earn.get('trailingPE', 'N/A')}, "
            f"Forward P/E {earn.get('forwardPE', 'N/A')}, PEG {earn.get('pegRatio', 'N/A')}, "
            f"EPS {earn.get('reportedEPS', 'N/A')} (surprise {earn.get('epsSurprisePct', 'N/A')}%), "
            f"Revenue {earn.get('revenueFormatted', 'N/A')}, Net Income {earn.get('netIncomeFormatted', 'N/A')}"
        )
    deltas = _financial_deltas(sd)
    if deltas:
        parts.append("Computed trends:")
        parts.extend(f"  • {d}" for d in deltas)
    tech = sd.get("technical", {}) or {}
    if tech:
        parts.append(f"Technicals — RSI {tech.get('currentRSI', 'N/A')} ({tech.get('rsiSignal', '')})")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Valuation anchors — the objective reference numbers the debate argues over
# ---------------------------------------------------------------------------

def _numf(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _upside(target: Optional[float], price: Optional[float]) -> Optional[float]:
    if target is None or not price:
        return None
    return round((target / price - 1) * 100, 1)


def _market_cap_num(sd: dict, dcf: dict) -> Optional[float]:
    v = _numf((dcf or {}).get("market_cap"))
    if v and v > 0:
        return v
    s = str((sd or {}).get("marketCap", "")).strip().upper().lstrip("$")
    m = re.match(r"([\d.]+)\s*([TBM])?", s)
    if not m:
        return None
    return float(m.group(1)) * {"T": 1e12, "B": 1e9, "M": 1e6}.get(m.group(2), 1.0)


def _valuation_anchors(sd: dict, dcf: dict) -> dict:
    """Pull the four anchor families: multiples, consensus EPS/growth, DCF, analysts."""
    earn = (sd or {}).get("earnings", {}) or {}
    g = earn.get("guidance", {}) or {}
    price = _numf(sd.get("price"))
    fwd_pe = _numf(earn.get("forwardPE"))
    fwd_eps = _numf(g.get("forwardEps"))
    if fwd_eps is None and price and fwd_pe:
        fwd_eps = round(price / fwd_pe, 2)   # market-implied NTM EPS fallback
    val = (dcf or {}).get("valuation", {}) or {}
    dcf_fv = _numf(val.get("fair_value_per_share"))
    if dcf_fv is not None and dcf_fv <= 0:
        dcf_fv = None
    dcf_low, dcf_high = _numf(val.get("fair_value_low")), _numf(val.get("fair_value_high"))
    dcf_implied_growth = _numf(val.get("market_implied_growth"))
    a_mean, a_high, a_low = _numf(g.get("targetMeanPrice")), _numf(g.get("targetHighPrice")), _numf(g.get("targetLowPrice"))
    a_n = _numf(g.get("numberOfAnalysts"))
    return {
        "price": price,
        "forward_pe": fwd_pe, "trailing_pe": _numf(earn.get("trailingPE")), "peg": _numf(earn.get("pegRatio")),
        "forward_eps": fwd_eps, "trailing_eps": _numf(g.get("trailingEps")),
        "consensus_eps_growth_pct": _numf(g.get("epsGrowthPct")),
        "revenue_growth_pct": _numf(g.get("revenueGrowthPct")),
        "dcf_fair_value": dcf_fv, "dcf_upside_pct": _upside(dcf_fv, price),
        "dcf_low": dcf_low, "dcf_high": dcf_high, "dcf_implied_growth": dcf_implied_growth,
        "analyst_mean": a_mean, "analyst_high": a_high, "analyst_low": a_low,
        "analyst_mean_upside_pct": _upside(a_mean, price),
        "analyst_high_upside_pct": _upside(a_high, price),
        "analyst_low_upside_pct": _upside(a_low, price),
        "analyst_count": int(a_n) if a_n else None,
        "market_cap": _market_cap_num(sd, dcf),
    }


def _anchors_text(a: dict) -> str:
    def d(x):
        return "N/A" if x is None else f"${x:,.2f}"
    def u(x):
        return "" if x is None else f" ({x:+.0f}% vs price)"
    ig = a.get("dcf_implied_growth")
    reverse = (
        f"Reverse-DCF: today's price implies ~{ig:.0f}% FCF growth/yr for 10y"
        f" (vs consensus EPS growth {a['consensus_eps_growth_pct'] if a['consensus_eps_growth_pct'] is not None else 'N/A'}%)"
        if ig is not None else "Reverse-DCF: n/a"
    )
    return "\n".join([
        f"Current price: {d(a['price'])}",
        f"Consensus fwd EPS (NTM): {a['forward_eps'] if a['forward_eps'] is not None else 'N/A'}"
        f" | trailing EPS: {a['trailing_eps'] if a['trailing_eps'] is not None else 'N/A'}"
        f" | consensus EPS growth: {a['consensus_eps_growth_pct'] if a['consensus_eps_growth_pct'] is not None else 'N/A'}%",
        f"Multiples: forward P/E {a['forward_pe'] or 'N/A'}x | trailing P/E {a['trailing_pe'] or 'N/A'}x | PEG {a['peg'] or 'N/A'}",
        f"DCF (conservative intrinsic): {d(a['dcf_fair_value'])} — range {d(a['dcf_low'])}–{d(a['dcf_high'])}{u(a['dcf_upside_pct'])}",
        reverse,
        f"Analyst targets (n={a['analyst_count'] or '?'}): mean {d(a['analyst_mean'])}{u(a['analyst_mean_upside_pct'])},"
        f" high {d(a['analyst_high'])}{u(a['analyst_high_upside_pct'])}, low {d(a['analyst_low'])}{u(a['analyst_low_upside_pct'])}",
    ])


async def _macro_snapshot() -> tuple[dict, str]:
    """Best-effort FRED macro snapshot. Never raises — macro is contextual."""
    try:
        raw = await rates_service.fred_many(list(_MACRO_SERIES.keys()))
    except Exception as exc:
        logger.info("Macro snapshot unavailable: %s", exc)
        return {}, "Macro snapshot unavailable."
    snap: dict[str, float] = {}
    bits: list[str] = []
    for sid, label in _MACRO_SERIES.items():
        val = rates_service.latest(raw.get(sid, []))
        if val is not None:
            snap[sid] = round(float(val), 2)
            bits.append(f"{label}: {snap[sid]}")
    return snap, (" | ".join(bits) if bits else "Macro snapshot unavailable.")


def _is_boilerplate(excerpt: str) -> bool:
    low = (excerpt or "").lower()
    return sum(1 for m in _BOILERPLATE_MARKERS if m in low) >= 2


def _doc_priority(d: dict) -> int:
    """Rank docs by analytical richness: earnings release > annual > quarterly > 8-K."""
    form = (d.get("form") or "").upper()
    if "EX-99" in form:                      # earnings press release — mgmt commentary + guidance
        return 0
    if form.startswith(("10-K", "20-F")):    # annual: full business + risk narrative
        return 1
    if form.startswith(("10-Q", "6-K")):     # quarterly financials
        return 2
    return 3                                 # 8-K cover / other


def _select_docs(bundle: dict) -> list[dict]:
    """Keep the richest _MAX_DOCS filings for the debate: drop boilerplate cover
    pages AND plain 8-Ks (no useful company update — the earnings-release EX-99 is
    a separate form and is kept). Debate-only; Pick & Shovel reads the full bundle."""
    docs = [
        d for d in (bundle or {}).get("docs", [])
        if not _is_boilerplate(d.get("excerpt") or "")
        and (d.get("form") or "").strip().upper() != "8-K"
    ]
    docs.sort(key=_doc_priority)
    return docs[:_MAX_DOCS]


def _filings_text(docs: list[dict]) -> str:
    if not docs:
        return "No substantive SEC filing text available for this issuer."
    chunks = []
    for d in docs:
        excerpt = (d.get("excerpt") or "")[:_EXCERPT_CHARS]
        chunks.append(f"[{d.get('form')} filed {d.get('date')}]\n{excerpt}")
    return "\n\n".join(chunks)


async def _build_evidence(db, ticker: str, dcf_override: dict | None = None) -> dict:
    try:
        sd = await fetch_stock_data(ticker)
    except Exception:
        sd = {}
    try:
        filings = await gather_company_filings(db, ticker)
    except Exception as exc:
        logger.info("EDGAR gather failed for %s: %s", ticker, exc)
        filings = {"docs": [], "documents_found": False, "cik": None}

    # The user's tweaked DCF (from the DCF page) overrides the auto-computed anchor.
    try:
        dcf = dcf_from_override(dcf_override) if dcf_override else await run_dcf_analysis(ticker)
    except Exception as exc:
        logger.info("DCF failed for %s: %s", ticker, exc)
        dcf = {}

    macro, macro_text = await _macro_snapshot()
    selected = _select_docs(filings)
    fundamentals = _fundamentals_text(ticker, sd)
    filings_text = _filings_text(selected)
    anchors = _valuation_anchors(sd, dcf)

    # Structural base for the post-debate valuation engine (and to anchor the agents'
    # claims to the REAL base EPS / operating leverage, not the model's memory).
    try:
        vi = await fetch_valuation_inputs(ticker)
    except Exception as exc:
        logger.info("valuation inputs failed for %s: %s", ticker, exc)
        vi = None
    structural_text = _structural_base_text(vi) if vi else "(structural base unavailable)"

    dossier = (
        f"=== FUNDAMENTALS & EARNINGS ===\n{fundamentals}\n\n"
        f"=== STRUCTURAL BASE (anchor every claim to THIS; the engine prices them) ===\n{structural_text}\n\n"
        f"=== VALUATION ANCHORS (debate these NUMBERS; reconcile any target to them) ===\n{_anchors_text(anchors)}\n\n"
        f"=== MACRO SNAPSHOT (FRED) ===\n{macro_text}\n\n"
        f"=== SEC FILINGS (EDGAR excerpts — cite these by form) ===\n{filings_text}"
    )
    return {
        "company_name": (sd or {}).get("companyName", ticker),
        "dossier": dossier,
        "macro": macro,
        "anchors": anchors,
        "valuation_inputs": vi,                      # in-process only (dataclasses; not serialized)
        "structural_text": structural_text,
        "structural_base": _structural_base_dict(vi),
        "filings_meta": [
            {"form": d.get("form"), "date": d.get("date"), "url": d.get("url")}
            for d in selected
        ],
        "documents_found": bool(selected),
    }


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------

# Every agent gets this discipline block so claims are grounded in the dossier,
# not the model's stale training memory, and lean toward non-obvious insight.
_EVIDENCE_RULES = (
    "RULES (strict — followed literally):\n"
    "- BE TERSE. Each point is ONE line: the number/fact + an inline source tag. "
    "e.g. 'FCF $4.6B vs capex $4.2B → ~$0.4B left [10-K]'. No preamble, no summary sentence, no rhetoric.\n"
    "- BAN generic filler. Delete any sentence with no specific number/fact from the dossier — phrases "
    "like 'critical for long-term growth', 'positions the company well', 'strong fundamentals', "
    "'supports sustainable cash flow' are FORBIDDEN unless immediately backed by a cited figure.\n"
    "- Argue ONLY from figures/facts in the dossier (filings + computed trends + valuation anchors). "
    "Never use outside or remembered facts.\n"
    "- If a point needs a fact the dossier does NOT contain, do not assert or speculate — write "
    "'NOT IN FILINGS: <the specific missing item>'. The Judge widens uncertainty and lowers the target "
    "for each such gap, and surfaces it to the user as a research to-do.\n"
    "- Prefer NON-obvious, number-driven points a retail investor would miss.\n"
)


def _bull_prompt(ticker: str, dossier: str) -> str:
    return (
        f"You are a long-biased equity analyst in a MULTI-ROUND debate about {ticker}. Build a "
        f"SOPHISTICATED bull case. Hunt the dossier specifically for: hidden/under-covered segment "
        f"strength, margin or mix inflections, operating leverage, capital-return capacity (buybacks/"
        f"dividends), and guidance vs. buy-side expectations.\n\n"
        f"{_EVIDENCE_RULES}\n"
        f"FORMAT: at most 5 one-line, number-first bullets — no intro or summary paragraph.\n\n"
        f"Iterative debate — you see the full transcript.\n"
        f"- Round 1: open with your thesis and a valuation bridge.\n"
        f"- Later rounds: rebut the Bear's newest points, CONCEDE what's valid, and adjust the SPECIFIC "
        f"driver the Bear undercut (e.g. lower margin_delta_bps → lower EPS → lower target). Add ONLY "
        f"new, evidence-backed reasoning — never repeat.\n\n"
        f"PRICE TARGET — build it, don't guess it. Your target is derived, not stated:\n"
        f"  target_price = consensus_fwd_EPS × (1 + eps_beat_pct/100) × target_multiple\n"
        f"We recompute this in code from your two inputs, so make BOTH defensible against the "
        f"VALUATION ANCHORS section:\n"
        f"  • eps_beat_pct — how much your NTM EPS beats consensus; it MUST follow from your drivers "
        f"(revenue growth, margin delta, buyback share reduction) and be consistent with the computed "
        f"trends, or explicitly justify the divergence.\n"
        f"  • target_multiple — vs the CURRENT forward P/E; a re-rating >±30% needs a named catalyst.\n"
        f"Your implied target should land within the band set by the DCF fair value and the analyst "
        f"high/low unless you name a specific catalyst to exceed it. Reconcile explicitly.\n\n"
        f"End EVERY response with a one-line JSON VALUATION block, then the two lines, and nothing after:\n"
        f'VALUATION: {{"eps_beat_pct": <num>, "drivers": {{"revenue_growth_pct": <num>, '
        f'"margin_delta_bps": <num>, "buyback_reduction_pct": <num>}}, "target_multiple": <num>, '
        f'"multiple_anchor": "<why vs current fwd P/E + peers>", "catalyst": "<needed if multiple '
        f'>±30% from fwd P/E, else empty>", "reconciliation": "<how your target sits vs DCF + analyst mean/high/low>"}}\n'
        f"TARGET_UPSIDE: <number>%\n"
        f"NEW_ARGUMENT: <yes|no>\n\n"
        f"=== EVIDENCE DOSSIER ===\n{dossier}"
    )


def _bear_prompt(ticker: str, dossier: str) -> str:
    return (
        f"You are a forensic short-seller in a MULTI-ROUND debate about {ticker}. Invalidate the Bull "
        f"using the SAME dossier, and find what a retail investor would NEVER check. Hunt specifically "
        f"for:\n"
        f"- Quality-of-earnings red flags: net-income vs. free-cash-flow divergence, rising receivables/"
        f"inventory vs. sales, one-off or 'other income' boosts, stock-based comp & share-count "
        f"dilution, tax-rate or margin flattery.\n"
        f"- Decelerating segments, adverse mix shifts, weak guidance, and valuation vs. the ACTUAL "
        f"growth in the numbers.\n"
        f"- Secular/competitive threats and macro sensitivity (rates, FX, demand) from the snapshot.\n"
        f"Do NOT publish your own price target.\n\n"
        f"{_EVIDENCE_RULES}\n"
        f"FORMAT: at most 5 one-line, number-first bullets — no intro or summary paragraph.\n\n"
        f"Iterative debate — attack the Bull's weakest NEW points and add fresh, non-obvious red flags "
        f"each round; CONCEDE genuinely sound points; never repeat.\n\n"
        f"End EVERY response with exactly this line and nothing after it:\n"
        f"NEW_ARGUMENT: <yes|no>\n\n"
        f"=== EVIDENCE DOSSIER ===\n{dossier}"
    )


def _judge_prompt(ticker: str, confidence_target: float) -> str:
    return (
        f"You are a skeptical quantitative PM MODERATING a multi-round Bull vs. Bear debate on {ticker}. "
        f"Judge on EVIDENCE, not eloquence: reward claims cited to the dossier's real figures and "
        f"HEAVILY discount uncited or generic assertions from either side. Apply base rates — most "
        f"12-month single-stock views should be modest.\n\n"
        f"Output ONLY a JSON object, no prose, no markdown fences:\n"
        f'{{"view_return": <float>, "confidence": <float>, "new_information": <bool>, '
        f'"should_continue": <bool>, "open_questions": ["<...>"], "rationale": "<2-3 sentences>"}}\n\n'
        f"- view_return: expected 12-month TOTAL return as a decimal (0.12 = +12%, -0.05 = -5%). "
        f"ANCHOR it to the code-recomputed target upside in the COMPUTED VALUATION note (NOT the Bull's "
        f"stated number), then adjust DOWN for every well-evidenced Bear risk and every valuation flag.\n"
        f"- confidence: 0.0 (thesis destroyed / coin-flip) to 1.0 (robust, verdict settled); lower it "
        f"for uncited claims, thin/contradictory evidence, AND for each valuation flag.\n"
        f"- UNCERTAINTY PENALTY: for every 'NOT IN FILINGS' item or claim the documents can't verify, "
        f"pull view_return toward the LOW end of the DCF/analyst range and lower confidence — do not give "
        f"credit for facts that aren't in the filings.\n"
        f"- open_questions: a short list (may be empty) of what the FILINGS don't answer but would change "
        f"the view — written for the user as concrete research to-dos / data to provide "
        f"(e.g. 'What share of Delta's $4.6B FCF is recurring vs. working-capital timing?'). "
        f"Roll up the debate's 'NOT IN FILINGS' items here.\n"
        f"- new_information: true only if THIS round surfaced a substantively new, evidence-backed point.\n"
        f"- should_continue: true if another round would sharpen the verdict; false if the debate has "
        f"CONVERGED, is repeating, or confidence is already at/above {confidence_target:.2f}.\n"
        f"- rationale: 2-3 sentences, number-first, citing the pivotal evidence. No filler."
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_upside(text: str) -> float | None:
    """Pull the Bull's `TARGET_UPSIDE: X%` line (fallback: last %). Returns %."""
    m = re.search(r"TARGET[_\s]?UPSIDE\s*[:=]\s*([+-]?\d+(?:\.\d+)?)\s*%", text, re.IGNORECASE)
    if m:
        try:
            return round(float(m.group(1)), 2)
        except ValueError:
            return None
    return None


def _parse_valuation(text: str) -> Optional[dict]:
    """Extract the Bull's ``VALUATION: {json}`` block (balanced-brace, nested-safe)."""
    m = re.search(r"VALUATION\s*:\s*", text or "", re.IGNORECASE)
    if not m:
        return None
    i = text.find("{", m.end())
    if i < 0:
        return None
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[i:j + 1])
                except Exception:
                    return None
    return None


def _recompute_bridge(val: dict, anchors: dict, stated_upside: Optional[float]) -> dict:
    """Recompute the price target from the Bull's inputs (we own the arithmetic) and
    run the guardrails. target = consensus_fwd_EPS × (1 + eps_beat) × target_multiple."""
    price = anchors.get("price")
    cons_eps = anchors.get("forward_eps")
    fwd_pe = anchors.get("forward_pe")
    beat = _numf(val.get("eps_beat_pct")) or 0.0
    mult = _numf(val.get("target_multiple"))
    catalyst = str(val.get("catalyst") or "").strip()
    flags: list[str] = []

    target = upside = None
    if cons_eps and mult:
        target = round(cons_eps * (1 + beat / 100) * mult, 2)
        upside = _upside(target, price)

    # 1) Multiple must sit within ±band of the current forward P/E unless a catalyst is named.
    if mult and fwd_pe:
        lo, hi = fwd_pe * (1 - _MULT_BAND), fwd_pe * (1 + _MULT_BAND)
        if (mult > hi or mult < lo) and not catalyst:
            flags.append(f"target multiple {mult:.1f}x is outside ±{int(_MULT_BAND*100)}% of forward P/E {fwd_pe:.1f}x with no catalyst named")
    # 2) A large EPS beat must be backed by the drivers.
    if abs(beat) >= _LARGE_BEAT_PCT:
        flags.append(f"large EPS beat vs consensus ({beat:+.0f}%) — must be justified by the drivers")
    # 3) Triangulation: target should land within the DCF + analyst band.
    refs = [x for x in (anchors.get("dcf_fair_value"), anchors.get("analyst_high"),
                        anchors.get("analyst_low"), anchors.get("analyst_mean")) if x]
    if target and refs:
        hi_ref, lo_ref = max(refs), min(refs)
        if target > hi_ref * 1.10:
            flags.append(f"implied target ${target:,.0f} exceeds the DCF/analyst band (max ${hi_ref:,.0f}) by >10%")
        elif target < lo_ref * 0.90:
            flags.append(f"implied target ${target:,.0f} is below the DCF/analyst band (min ${lo_ref:,.0f}) by >10%")
    # 4) Absurd move for a mega-cap.
    mc = anchors.get("market_cap")
    if mc and mc > _MEGACAP_USD and upside is not None and abs(upside) > _MEGACAP_UPSIDE:
        flags.append(f"{abs(upside):.0f}% move is aggressive for a ${mc/1e9:.0f}B mega-cap")
    # 5) The LLM's own stated number must match our recomputation.
    if stated_upside is not None and upside is not None and abs(stated_upside - upside) > _STATED_VS_COMPUTED_TOL:
        flags.append(f"Bull's stated target ({stated_upside:+.0f}%) diverges from the recomputed bridge ({upside:+.0f}%)")

    return {
        "eps_beat_pct": beat,
        "target_multiple": mult,
        "drivers": val.get("drivers") if isinstance(val.get("drivers"), dict) else None,
        "multiple_anchor": str(val.get("multiple_anchor") or "") or None,
        "catalyst": catalyst or None,
        "reconciliation": str(val.get("reconciliation") or "") or None,
        "computed_target_price": target,
        "computed_upside_pct": upside,
        "stated_upside_pct": stated_upside,
        "flags": flags,
    }


def _bridge_note(bridge: dict) -> str:
    """A compact, authoritative recomputation the Judge must anchor to."""
    if not bridge:
        return ""
    tgt = bridge.get("computed_target_price")
    up = bridge.get("computed_upside_pct")
    flags = bridge.get("flags") or []
    return (
        "\n=== COMPUTED VALUATION (recomputed in code — authoritative) ===\n"
        f"target ${tgt:,.2f} → {up:+.1f}% upside "
        f"(EPS beat {bridge.get('eps_beat_pct'):+.0f}% × multiple {bridge.get('target_multiple')}x)\n"
        f"Valuation flags ({len(flags)}): " + ("; ".join(flags) if flags else "none") + "\n\n"
    ) if tgt is not None and up is not None else (
        "\n=== COMPUTED VALUATION ===\nBull did not supply a parseable valuation bridge this round.\n\n"
    )


# ---------------------------------------------------------------------------
# Structural valuation — driver-claim extraction → valuation_engine.assemble()
# ---------------------------------------------------------------------------

def _structural_base_text(vi) -> str:
    """The base P&L, in the agents' dossier, so claims anchor to the REAL numbers."""
    lev = vi.leverage
    lev_desc = (f"{lev.inc_margin:.0%} incremental (avg {lev.avg_margin:.0%}, R²{lev.r2})"
                if lev.source == "regression"
                else f"{lev.avg_margin:.0%} avg margin (flat sample — no leverage fit)")
    pe = f"{vi.own_hist_pe:.1f}x" if vi.own_hist_pe else "n/a"
    return (
        f"base EPS ${vi.base_eps:.2f} | price ${vi.price:.2f} | trailing P/E {pe}\n"
        f"operating margin {vi.margin_quality:.1%} | operating leverage {lev_desc} | "
        f"latest YoY revenue growth {vi.trailing_growth:+.1%}\n"
        "→ Frame each thesis as a DRIVER off this base: revenue growth %, operating-margin Δ (bps), "
        "buyback (% shares), one-off $, or a re-rating (P/E points). Code composes them through the P&L."
    )


def _structural_base_dict(vi) -> Optional[dict]:
    """Serializable summary of the base for the payload/frontend."""
    if vi is None:
        return None
    return {
        "base_eps": round(vi.base_eps, 2), "price": round(vi.price, 2),
        "op_margin": vi.margin_quality, "inc_margin": vi.leverage.inc_margin,
        "leverage_source": vi.leverage.source, "leverage_r2": vi.leverage.r2,
        "own_hist_pe": round(vi.own_hist_pe, 1) if vi.own_hist_pe else None,
        "trailing_growth": vi.trailing_growth,
    }


def _extract_claims_prompt(ticker: str, structural_text: str) -> str:
    return (
        f"You are a valuation analyst. From the CONCLUDED {ticker} debate, extract the price-relevant "
        "DRIVER-CLAIMS that SURVIVED, so a deterministic engine can price them. You do NOT compute a "
        "target — you only extract and CLASSIFY. Output STRICT JSON only, no prose:\n"
        '{"claims": [{"driver": "...", "magnitude": <number>, "tier": "E1|E2|E3|E4|E5", '
        '"unanswered": <bool>, "persistence_nudge": <number>, "label": "..."}]}\n\n'
        "driver ∈ revenue | margin | buyback | other | multiple. UNITS (strict):\n"
        "- revenue: fractional 1-yr growth (0.08 = +8%)\n"
        "- margin: operating-margin change in BPS (+150, -120)\n"
        "- buyback: fractional share change, NEGATIVE for a repurchase (-0.02 = 2% fewer shares)\n"
        "- other: one-off pretax dollars, absolute (e.g. -3.0e8 for a $300M charge)\n"
        "- multiple: re-rating in P/E POINTS (+2, -3) — use SPARINGLY, only for a named re-rating catalyst\n"
        "Evidence tier — classify HONESTLY (most 'story' claims are E4/E5):\n"
        "  E1 disclosed figure in a filing/release · E2 management guidance · E3 analyst consensus · "
        "E4 historical-trend extrapolation · E5 pure narrative.\n"
        "unanswered=true ONLY when the Bear landed a specific rebuttal to THIS driver that the Bull "
        "never refuted. persistence_nudge (−0.2..0.2, default 0): >0 ONLY with E1/E2 mechanism evidence "
        "(a disclosed backlog / contract / capacity) that the driver DURABLY persists; <0 if clearly "
        "one-off. label ≤8 words incl. the source tag.\n"
        f"Anchor magnitudes to the base:\n{structural_text}\n"
        f"At most {_MAX_CLAIMS} claims — only material, price-moving ones. If the debate supports no "
        'quantifiable driver, return {"claims": []}.'
    )


def _parse_claims_json(text: str) -> list:
    """Defensive JSON extraction (mirrors ``_parse_judge``), returns the raw claims list."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = brace.group(0) if brace else raw
    try:
        obj = json.loads(candidate)
    except Exception as exc:
        logger.info("claim extraction JSON parse failed: %s", exc)
        return []
    items = obj.get("claims") if isinstance(obj, dict) else obj
    return items if isinstance(items, list) else []


def _claims_from_json(items: list) -> list:
    """Validate + clamp extracted claims into ``Claim`` objects. Per-driver clamps stop a
    hallucinated magnitude from running away before it reaches the engine."""
    _CLAMP = {"revenue": (-0.9, 3.0), "margin": (-2000.0, 2000.0),
              "buyback": (-0.5, 0.5), "multiple": (-20.0, 20.0)}
    claims: list = []
    for it in (items or [])[:_MAX_CLAIMS]:
        if not isinstance(it, dict):
            continue
        driver = str(it.get("driver", "")).strip().lower()
        mag = _numf(it.get("magnitude"))
        if driver not in _CLAIM_DRIVERS or mag is None:
            continue
        if driver in _CLAMP:
            lo, hi = _CLAMP[driver]
            mag = max(lo, min(hi, mag))
        tier = str(it.get("tier", "E5")).strip().upper()
        if tier not in {"E1", "E2", "E3", "E4", "E5"}:
            tier = "E5"
        nudge = max(-0.2, min(0.2, _numf(it.get("persistence_nudge")) or 0.0))
        claims.append(Claim(
            driver=driver, magnitude=mag, tier=tier, persistence_nudge=nudge,
            unanswered=_as_bool(it.get("unanswered"), False),
            label=str(it.get("label", "") or "")[:80],
        ))
    return claims


async def _structural_valuation(vi, transcript: list, structural_text: str, ticker: str,
                                api_key: str, model: str, call_llm) -> Optional[dict]:
    """Extract driver-claims from the concluded debate and price them with the engine.
    Returns assemble()'s dict (target/range/confidence/waterfall) + the parsed claims,
    or None if inputs are missing or nothing quantifiable survived."""
    if vi is None:
        return None
    sys_prompt = _extract_claims_prompt(ticker, structural_text)
    user = (f"Concluded debate transcript:\n\n{_join_transcript(transcript)}\n\n"
            "Extract the surviving driver-claims as JSON.")
    try:
        text = await call_llm(
            api_key=api_key, model=model, max_tokens=700, temperature=0.1,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user}],
        )
    except Exception as exc:
        logger.info("claim extraction call failed: %s", exc)
        return None
    claims = _claims_from_json(_parse_claims_json(text))
    if not claims:
        return None
    result = assemble(vi.base, vi.leverage, claims, price=vi.price,
                      margin_quality=vi.margin_quality, own_hist_pe=vi.own_hist_pe)
    result["claims"] = [
        {"driver": c.driver, "magnitude": c.magnitude, "tier": c.tier,
         "unanswered": c.unanswered, "persistence_nudge": c.persistence_nudge, "label": c.label}
        for c in claims
    ]
    return result


def _reconcile_conclusion(judge_verdict: dict, structural: Optional[dict]) -> dict:
    """Headline view_return comes from the STRUCTURAL engine when available (that's the
    whole point — the number is tied to priced claims, not the Judge's gestalt); the
    Judge's number/confidence are retained for provenance. Confidence = the more
    conservative of the two, so both the debate AND the arithmetic must be tight."""
    vr = judge_verdict.get("view_return")
    conf = judge_verdict.get("confidence")
    source = "judge"
    if structural and structural.get("upside_pct") is not None:
        vr = round(structural["upside_pct"] / 100, 4)
        source = "structural"
        s_conf = structural.get("confidence")
        if s_conf is not None:
            conf = round(min(conf, s_conf), 2) if conf is not None else s_conf
    return {"view_return": vr, "base_confidence": conf, "view_source": source,
            "judge_view_return": judge_verdict.get("view_return"),
            "judge_confidence": judge_verdict.get("confidence")}


def _parse_new_argument(text: str) -> bool:
    """Read an agent's `NEW_ARGUMENT: yes|no` self-signal. Missing → assume yes
    (stay productive; the Judge's ``new_information`` gate still catches repetition)."""
    m = re.search(r"NEW[_\s]?ARGUMENT\s*[:=]\s*(yes|no|true|false)", text or "", re.IGNORECASE)
    if not m:
        return True
    return m.group(1).lower() in {"yes", "true"}


def _as_bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"true", "yes", "1"}
    if v is None:
        return default
    return bool(v)


def _parse_judge(text: str) -> dict:
    """Strict-ish JSON parse of the per-round Judge verdict; defensive to fences/prose.

    On failure, ``should_continue``/``new_information`` default to False so the
    loop halts rather than spinning on an unreadable verdict.
    """
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)  # first {...} block
        candidate = brace.group(0) if brace else raw
    try:
        obj = json.loads(candidate)
        vr = obj.get("view_return")
        conf = obj.get("confidence", obj.get("base_confidence"))
        oq = obj.get("open_questions") or []
        if isinstance(oq, str):
            oq = [oq]
        oq = [str(q).strip() for q in oq if str(q).strip()][:8]
        return {
            "view_return": float(vr) if vr is not None else None,
            "confidence": float(conf) if conf is not None else None,
            "new_information": _as_bool(obj.get("new_information"), True),
            "should_continue": _as_bool(obj.get("should_continue"), True),
            "open_questions": oq,
            "rationale": str(obj.get("rationale", "")).strip(),
            "parse_error": False,
        }
    except Exception as exc:
        logger.info("Judge JSON parse failed: %s", exc)
        return {
            "view_return": None, "confidence": None,
            "new_information": False, "should_continue": False,
            "open_questions": [],
            "rationale": raw, "parse_error": True,
        }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _join_transcript(lines: list[str]) -> str:
    return "\n\n".join(lines) if lines else "(debate has not started)"


async def _one_round(
    *, r: int, total_rounds: int, transcript: list[str], bull_sys: str, bear_sys: str,
    judge_sys: str, anchors: dict, api_key: str, model: str, call_llm, user_note: str = "",
) -> tuple[dict, bool, bool, dict]:
    """Run one Bull→Bear→Judge round. Appends the BULL/BEAR turns to ``transcript``
    (mutated) and returns (round_dict, bull_new, bear_new, verdict). ``user_note``
    injects verified analyst input (answers to prior open questions) into every turn."""
    ctx = f"\n\n{user_note}\n" if user_note else ""

    # --- Bull ---
    if r == 1 and not transcript:
        bull_user = f"Round 1. Open the debate: make the definitive bull case.{ctx}"
    else:
        bull_user = (
            f"Debate so far:\n\n{_join_transcript(transcript)}{ctx}\n\n"
            f"Round {r}. As the BULL, rebut the Bear's latest points, concede what's valid, and "
            f"refine your target. Add ONLY new reasoning not already above."
        )
    bull_text = await call_llm(
        api_key=api_key, model=model, max_tokens=450, temperature=0.7,
        messages=[{"role": "system", "content": bull_sys}, {"role": "user", "content": bull_user}],
    )
    bull_new = _parse_new_argument(bull_text)
    stated = _extract_upside(bull_text)
    bull_val = _parse_valuation(bull_text)
    bridge = _recompute_bridge(bull_val, anchors, stated) if bull_val else None
    up = bridge["computed_upside_pct"] if (bridge and bridge["computed_upside_pct"] is not None) else stated
    transcript.append(f"[Round {r} · BULL]\n{bull_text}")

    # --- Bear ---
    bear_user = (
        f"Debate so far:\n\n{_join_transcript(transcript)}{ctx}\n\n"
        f"Round {r}. As the BEAR, dismantle the Bull's latest argument — attack its weakest new "
        f"points and add fresh red flags. Add ONLY new reasoning not already above."
    )
    bear_text = await call_llm(
        api_key=api_key, model=model, max_tokens=450, temperature=0.7,
        messages=[{"role": "system", "content": bear_sys}, {"role": "user", "content": bear_user}],
    )
    bear_new = _parse_new_argument(bear_text)
    transcript.append(f"[Round {r} · BEAR]\n{bear_text}")

    # --- Judge ---
    judge_user = (
        f"Full debate transcript through round {r} (of up to {total_rounds}):\n\n"
        f"{_join_transcript(transcript)}{ctx}\n"
        f"{_bridge_note(bridge)}"
        f"Output your JSON verdict for round {r}."
    )
    judge_text = await call_llm(
        api_key=api_key, model=model, max_tokens=600, temperature=0.2,
        messages=[{"role": "system", "content": judge_sys}, {"role": "user", "content": judge_user}],
    )
    verdict = _parse_judge(judge_text)

    round_dict = {
        "round": r,
        "bull": {"argument": bull_text, "upside_pct": up, "new_argument": bull_new, "valuation": bridge},
        "bear": {"argument": bear_text, "new_argument": bear_new},
        "judge": {**verdict, "raw": judge_text},
    }
    return round_dict, bull_new, bear_new, verdict


async def run_debate(
    db, ticker: str, api_key: str, model: str,
    *, max_rounds: int = _MAX_ROUNDS, confidence_target: float = _CONFIDENCE_TARGET,
    dcf_override: dict | None = None,
) -> dict:
    """Run an iterative Bull↔Bear debate, refereed each round by the Judge.

    The loop continues while it stays productive and stops as soon as any of:
    the Judge hits ``confidence_target``, the Judge rules the debate converged,
    a round adds no new information, both sides report nothing new, or the round
    cap is hit. Every agent sees the full transcript so it can avoid repetition.
    """
    from .llm_service import call_llm  # lazy import

    ticker = (ticker or "").strip().upper()
    max_rounds = max(1, min(int(max_rounds), _ROUNDS_CEILING))
    confidence_target = min(max(float(confidence_target), 0.5), 0.99)

    evidence = await _build_evidence(db, ticker, dcf_override)
    dossier = evidence["dossier"]
    anchors = evidence["anchors"]
    bull_sys = _bull_prompt(ticker, dossier)
    bear_sys = _bear_prompt(ticker, dossier)
    judge_sys = _judge_prompt(ticker, confidence_target)

    transcript: list[str] = []   # running, labelled debate log shown to every agent
    rounds: list[dict] = []
    last_upside: float | None = None
    stop_reason = "reached the round cap without converging"

    for r in range(1, max_rounds + 1):
        round_dict, bull_new, bear_new, verdict = await _one_round(
            r=r, total_rounds=max_rounds, transcript=transcript,
            bull_sys=bull_sys, bear_sys=bear_sys, judge_sys=judge_sys, anchors=anchors,
            api_key=api_key, model=model, call_llm=call_llm,
        )
        rounds.append(round_dict)

        # --- Termination gates (individual agents + judge) ---
        conf = verdict["confidence"]
        if verdict["parse_error"]:
            stop_reason = "Judge verdict was unreadable — halting"
            break
        if conf is not None and conf >= confidence_target:
            stop_reason = f"Judge reached the confidence target ({conf:.0%} ≥ {confidence_target:.0%})"
            break
        if not verdict["should_continue"]:
            stop_reason = "Judge ruled the debate had converged"
            break
        if not verdict["new_information"]:
            stop_reason = "the round added no new information"
            break
        if not bull_new and not bear_new:
            stop_reason = "both sides had nothing new to add"
            break

    final = rounds[-1]["judge"] if rounds else {}
    # Price the surviving arguments through the structural engine — the authoritative view.
    structural = await _structural_valuation(
        evidence.get("valuation_inputs"), transcript, evidence.get("structural_text", ""),
        ticker, api_key, model, call_llm)
    reconciled = _reconcile_conclusion(final, structural)
    return {
        "ticker": ticker,
        "company_name": evidence["company_name"],
        "model": model,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "evidence": {
            "documents_found": evidence["documents_found"],
            "filings": evidence["filings_meta"],
            "macro": evidence["macro"],
        },
        "anchors": anchors,
        "structural_base": evidence.get("structural_base"),
        "structural_valuation": structural,
        "dcf_source": "user" if dcf_override else "auto",
        "prompts": {"bull": bull_sys, "bear": bear_sys, "judge": judge_sys},
        "rounds": rounds,
        "conclusion": {
            **reconciled,
            "rationale": final.get("rationale", ""),
            "open_questions": final.get("open_questions", []),
            "rounds": len(rounds),
            "stop_reason": stop_reason,
            "parse_error": final.get("parse_error", False),
        },
        "settings": {"max_rounds": max_rounds, "confidence_target": confidence_target},
    }


async def continue_debate(db, ticker: str, api_key: str, model: str,
                          prior: dict, user_input: str) -> dict:
    """Append ONE more round to an existing debate, treating the user's answers to
    the prior open questions as verified input. Reuses the stored prompts (dossier
    baked in) + anchors, so no re-fetch — the Judge re-summarizes, re-prices, and
    emits a fresh open_questions list the user can answer again."""
    from .llm_service import call_llm  # lazy import

    ticker = (ticker or "").strip().upper()
    prompts = prior.get("prompts") or {}
    bull_sys, bear_sys, judge_sys = prompts.get("bull"), prompts.get("bear"), prompts.get("judge")
    anchors = prior.get("anchors") or {}
    prior_rounds = prior.get("rounds") or []
    if not (bull_sys and bear_sys and judge_sys and prior_rounds):
        raise ValueError("Cannot continue — the stored debate is missing prompts or rounds.")

    # Rebuild the running transcript from the prior rounds.
    transcript: list[str] = []
    for rd in prior_rounds:
        n = rd.get("round")
        transcript.append(f"[Round {n} · BULL]\n{(rd.get('bull') or {}).get('argument', '')}")
        transcript.append(f"[Round {n} · BEAR]\n{(rd.get('bear') or {}).get('argument', '')}")

    user_note = (
        "=== USER-PROVIDED ANSWERS (verified analyst input — treat as FACT; it resolves prior open "
        f"questions where applicable, so update your view and confidence accordingly) ===\n{user_input.strip()}"
    )
    r = len(prior_rounds) + 1
    round_dict, _bn, _rn, verdict = await _one_round(
        r=r, total_rounds=r, transcript=transcript,
        bull_sys=bull_sys, bear_sys=bear_sys, judge_sys=judge_sys, anchors=anchors,
        api_key=api_key, model=model, call_llm=call_llm, user_note=user_note,
    )
    round_dict["user_input"] = user_input.strip()   # shown above this round in the UI

    # Re-price through the structural engine with the extra round (+ the user's answers)
    # folded in. Re-fetch the base (prompts are stored, but the ValuationInputs are not).
    try:
        vi = await fetch_valuation_inputs(ticker)
    except Exception as exc:
        logger.info("valuation inputs failed on continue for %s: %s", ticker, exc)
        vi = None
    structural_text = _structural_base_text(vi) if vi else ""
    structural = await _structural_valuation(
        vi, transcript, structural_text, ticker, api_key, model, call_llm)
    reconciled = _reconcile_conclusion(verdict, structural)

    updated = dict(prior)
    updated["rounds"] = prior_rounds + [round_dict]
    updated["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if structural is not None:
        updated["structural_valuation"] = structural
        updated["structural_base"] = _structural_base_dict(vi)
    updated["conclusion"] = {
        **reconciled,
        "rationale": verdict.get("rationale", ""),
        "open_questions": verdict.get("open_questions", []),
        "rounds": len(updated["rounds"]),
        "stop_reason": "continued with your input",
        "parse_error": verdict.get("parse_error", False),
    }
    updated["available"] = True
    return updated
