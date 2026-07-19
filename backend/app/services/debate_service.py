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
from .valuation_engine import Claim, assemble, quality_base_multiple
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

# Structural valuation — the Bull emits tiered DRIVER-CLAIMS, the Bear rebuts them, the
# Judge ADJUDICATES each (keep/haircut/reject + reason), and valuation_engine.assemble()
# prices the survivors through a real P&L (covariance-aware) with warranted multiples +
# fade priors. No agent emits a target/return/confidence — code owns every number.
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
        f"You are a long-biased equity analyst in a MULTI-ROUND debate about {ticker}. Build the bull "
        f"case as a set of CITED, FORWARD-LOOKING DRIVER-CLAIMS. You do NOT set a price target, an EPS "
        f"beat, or a multiple — code computes ALL of those from your claims. Your only job is to surface "
        f"the real, quantified, cited drivers and defend them.\n\n"
        f"{_EVIDENCE_RULES}\n"
        f"FORMAT: at most 5 one-line, number-first prose bullets (the narrative), THEN a CLAIMS JSON block.\n\n"
        f"Each claim is a forward driver off the STRUCTURAL BASE in the dossier:\n"
        f"  driver ∈ revenue | margin | buyback | other | multiple. UNITS (strict):\n"
        f"   • revenue: fractional NEXT-YEAR COMPANY-WIDE growth (0.03 = +3%). A single-segment or "
        f"PAST-quarter number is NOT company-wide forward growth — scale it to the whole company and tier it E4.\n"
        f"   • margin: forward operating-margin change in BPS (+150, -120)\n"
        f"   • buyback: fractional share change, NEGATIVE for a repurchase (-0.02 = 2% fewer shares)\n"
        f"   • other: one-off pretax dollars, absolute (-3.0e8 = $300M charge)\n"
        f"   • multiple: re-rating in P/E POINTS — ONLY with a named, dated catalyst; otherwise omit it.\n"
        f"  tier — classify HONESTLY (the Judge re-tiers and can REJECT): E1 disclosed figure in a filing/"
        f"release · E2 management guidance · E3 analyst consensus · E4 historical/segment extrapolation · "
        f"E5 narrative. A narrative with no magnitude ('strong brand') is NOT a claim — leave it in prose.\n"
        f"  cite — the SPECIFIC figure + form, e.g. '10-Q: op margin 15.1% vs 14.0% PY'. No figure → no claim.\n\n"
        f"Iterative debate: Round 1 opens your claim set. Later rounds — DROP or DOWNGRADE any claim the "
        f"Bear rebutted with evidence, ADD new cited claims, and RE-EMIT your FULL current set (the latest "
        f"CLAIMS block is the only one that counts, so a claim you don't repeat is dropped).\n\n"
        f"End EVERY response with the CLAIMS block then the signal line, nothing after:\n"
        f'CLAIMS: [{{"id":"c1","driver":"revenue","magnitude":0.03,"tier":"E2","cite":"<form: figure>","label":"<=8 words>"}}, ...]\n'
        f"NEW_ARGUMENT: <yes|no>\n\n"
        f"=== EVIDENCE DOSSIER ===\n{dossier}"
    )


def _bear_prompt(ticker: str, dossier: str) -> str:
    return (
        f"You are a forensic short-seller in a MULTI-ROUND debate about {ticker}. Your job is to REBUT the "
        f"Bull's specific CLAIMS with cited counter-evidence, and to surface your own cited negative "
        f"drivers. You do NOT publish a price target — code prices the surviving claims.\n\n"
        f"Hunt for: net-income vs. FCF divergence, receivables/inventory build, one-off / 'other income' "
        f"boosts, stock-based comp & dilution, tax-rate or margin flattery, a single-segment or past-quarter "
        f"number dressed up as company-wide forward growth, weak guidance, and secular/competitive/macro threats.\n\n"
        f"{_EVIDENCE_RULES}\n"
        f"FORMAT: at most 5 one-line, number-first prose bullets, THEN a REBUTTALS JSON block. Attack each "
        f"Bull claim BY its id with a cited counter and a severity. You MAY add your own negative CLAIMS "
        f"(same schema as the Bull, negative magnitudes) for risks the code should price in.\n\n"
        f"Iterative debate — attack the weakest NEW claims, concede genuinely sound ones, never repeat.\n\n"
        f"End EVERY response with the blocks then the signal, nothing after:\n"
        f'REBUTTALS: [{{"target":"c1","counter":"<form: counter-figure>","severity":"high|med|low"}}, ...]\n'
        f'CLAIMS: []   (optional negative drivers; [] if none)\n'
        f"NEW_ARGUMENT: <yes|no>\n\n"
        f"=== EVIDENCE DOSSIER ===\n{dossier}"
    )


def _judge_prompt(ticker: str, confidence_target: float) -> str:
    return (
        f"You are a skeptical PM ADJUDICATING a Bull vs. Bear debate on {ticker}. You emit NO price target, "
        f"NO return, NO confidence — code computes every number from the claims you rule on. Your job is to "
        f"decide, for EACH Bull claim (and any Bear negative claim), whether it survives, and WHY.\n\n"
        f"For every claim id, rule:\n"
        f"  • verdict: keep | haircut | reject\n"
        f"     keep    — cited to a real figure, forward-looking, unrebutted.\n"
        f"     haircut — partly valid but weaker than stated: set a LOWER tier_final and/or unanswered=true.\n"
        f"     reject  — narrative/uncited, backward-looking or single-segment sold as company-wide, or a "
        f"Bear rebuttal fully invalidated it.\n"
        f"  • tier_final: the corrected E1..E5 tier (downgrade backward-looking / single-segment / thin claims).\n"
        f"  • unanswered: true if a Bear rebuttal landed on this claim and the Bull did not refute it.\n"
        f"  • reason: ONE line, number-first, citing the pivotal figure or the exact defect.\n\n"
        f"Judge on EVIDENCE, not eloquence. Output ONLY this JSON (no prose, no fences):\n"
        f'{{"adjudication":[{{"id":"c1","verdict":"haircut","tier_final":"E4","unanswered":true,'
        f'"reason":"<one line, number-first>"}}, ...], "new_information":<bool>, "should_continue":<bool>, '
        f'"open_questions":["<what the filings do NOT answer but would move the view>"], '
        f'"rationale":"<2-3 sentences, number-first>"}}\n\n'
        f"- Every claim id in the Bull/Bear CLAIMS must appear exactly once in adjudication.\n"
        f"- Roll every 'NOT IN FILINGS' item into open_questions as a concrete research to-do for the user.\n"
        f"- new_information: true only if THIS round added a substantively new, evidence-backed claim/rebuttal.\n"
        f"- should_continue: false once the claim set has stabilized, is repeating, or is fully adjudicated "
        f"(you decide whether another round would change the CLAIMS; the code decides the confidence)."
    )


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _json_after_key(text: str, key: str):
    """Balanced-bracket JSON ([...] or {...}) after ``KEY:`` — robust to surrounding prose."""
    m = re.search(rf"{key}\s*:\s*", text or "", re.IGNORECASE)
    if not m:
        return None
    start = None
    for ch in ("[", "{"):
        j = text.find(ch, m.end())
        if j >= 0 and (start is None or j < start):
            start = j
    if start is None:
        return None
    open_ch, close_ch = text[start], ("]" if text[start] == "[" else "}")
    depth = 0
    for k in range(start, len(text)):
        if text[k] == open_ch:
            depth += 1
        elif text[k] == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:k + 1])
                except Exception:
                    return None
    return None


def _parse_bull_claims(text: str) -> list:
    """The Bull's ``CLAIMS: [...]`` block (its full current claim set)."""
    v = _json_after_key(text, "CLAIMS")
    return v if isinstance(v, list) else []


def _parse_bear_rebuttals(text: str) -> tuple[list, list]:
    """The Bear's ``REBUTTALS: [...]`` (targeting claim ids) and optional negative ``CLAIMS: [...]``."""
    reb = _json_after_key(text, "REBUTTALS")
    neg = _json_after_key(text, "CLAIMS")
    return (reb if isinstance(reb, list) else [], neg if isinstance(neg, list) else [])


def _parse_adjudication(text: str) -> dict:
    """Parse the Judge's per-claim adjudication JSON. On failure the continue/new-info
    gates default False so the loop halts rather than spinning on an unreadable verdict."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    candidate = fence.group(1) if fence else None
    if candidate is None:
        brace = re.search(r"\{.*\}", raw, re.DOTALL)
        candidate = brace.group(0) if brace else raw
    try:
        obj = json.loads(candidate)
        adj = obj.get("adjudication")
        oq = obj.get("open_questions") or []
        if isinstance(oq, str):
            oq = [oq]
        return {
            "adjudication": adj if isinstance(adj, list) else [],
            "new_information": _as_bool(obj.get("new_information"), True),
            "should_continue": _as_bool(obj.get("should_continue"), True),
            "open_questions": [str(q).strip() for q in oq if str(q).strip()][:8],
            "rationale": str(obj.get("rationale", "")).strip(),
            "parse_error": False,
        }
    except Exception as exc:
        logger.info("Judge adjudication parse failed: %s", exc)
        return {"adjudication": [], "new_information": False, "should_continue": False,
                "open_questions": [], "rationale": raw, "parse_error": True}


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


# --- claim sanitization / citation linking / engine mapping ---
_TIERS = {"E1", "E2", "E3", "E4", "E5"}
_CLAMP = {"revenue": (-0.9, 3.0), "margin": (-2000.0, 2000.0),
          "buyback": (-0.5, 0.5), "multiple": (-20.0, 20.0)}


def _sanitize_claim(raw, idx: int) -> Optional[dict]:
    """Validate + clamp one raw agent claim into the canonical dict. Per-driver clamps
    stop a hallucinated magnitude from running away before it reaches the engine."""
    if not isinstance(raw, dict):
        return None
    driver = str(raw.get("driver", "")).strip().lower()
    mag = _numf(raw.get("magnitude"))
    if driver not in _CLAIM_DRIVERS or mag is None:
        return None
    if driver in _CLAMP:
        lo, hi = _CLAMP[driver]
        mag = max(lo, min(hi, mag))
    tier = str(raw.get("tier", "E5")).strip().upper()
    if tier not in _TIERS:
        tier = "E5"
    return {
        "id": str(raw.get("id") or f"c{idx}").strip()[:12],
        "driver": driver, "magnitude": mag, "tier": tier,
        "cite": str(raw.get("cite", "") or "")[:200],
        "label": str(raw.get("label", "") or "")[:80] or driver,
        "unanswered": _as_bool(raw.get("unanswered"), False),
        "persistence_nudge": max(-0.2, min(0.2, _numf(raw.get("persistence_nudge")) or 0.0)),
    }


def _sanitize_claims(raw_list, source: str) -> list:
    """Sanitize an agent's claim list, de-dup by id, cap the count, tag the source side."""
    out, seen = [], set()
    for i, raw in enumerate(raw_list or [], 1):
        c = _sanitize_claim(raw, i)
        if c and c["id"] not in seen:
            c["source"] = source
            seen.add(c["id"])
            out.append(c)
        if len(out) >= _MAX_CLAIMS:
            break
    return out


def _link_citations(claims: list, filings_meta: list) -> list:
    """Attach the EDGAR source URL to each claim by matching the form named in its cite."""
    form_urls: dict = {}
    for f in filings_meta or []:
        form = (f.get("form") or "").upper()
        if form and f.get("url") and form not in form_urls:
            form_urls[form] = f["url"]
    for c in claims:
        cu = (c.get("cite") or "").upper()
        c["source_url"] = next((url for form, url in form_urls.items() if form in cu), None)
    return claims


def _claims_to_engine(claim_dicts: list) -> list:
    """Build engine ``Claim`` objects from the surviving (non-rejected) claim dicts."""
    return [
        Claim(driver=c["driver"], magnitude=c["magnitude"], tier=c["tier"],
              persistence_nudge=c.get("persistence_nudge", 0.0),
              unanswered=bool(c.get("unanswered")), label=c.get("label", ""))
        for c in claim_dicts if not c.get("rejected")
    ]


def _apply_adjudication(claims: list, adjudication: list) -> list:
    """Fold the Judge's per-claim verdicts back onto the claim dicts (final tier, unanswered,
    rejected, reason). Claims the Judge didn't rule on are flagged 'unreviewed' and kept."""
    by_id = {str(a.get("id")): a for a in (adjudication or []) if isinstance(a, dict)}
    out = []
    for c in claims:
        c = dict(c)
        a = by_id.get(c["id"])
        if a:
            verdict = str(a.get("verdict", "keep")).strip().lower()
            if verdict not in {"keep", "haircut", "reject"}:
                verdict = "keep"
            tf = str(a.get("tier_final", "") or "").strip().upper()
            if tf in _TIERS:
                c["tier"] = tf
            c["unanswered"] = _as_bool(a.get("unanswered"), c.get("unanswered", False))
            c["verdict"] = verdict
            c["rejected"] = verdict == "reject"
            c["judge_reason"] = str(a.get("reason", "") or "")[:240]
        else:
            c["verdict"] = "unreviewed"
            c["judge_reason"] = ""
        out.append(c)
    return out


def _band_agreement(target: Optional[float], anchors: dict) -> float:
    """1.0 if the target sits inside the analyst/DCF band, decaying with distance outside it."""
    lo = anchors.get("analyst_low") or anchors.get("dcf_low")
    hi = anchors.get("analyst_high") or anchors.get("dcf_high")
    if not target or not lo or not hi or hi <= lo:
        return 0.6
    if lo <= target <= hi:
        return 1.0
    d = (lo - target) / (hi - lo) if target < lo else (target - hi) / (hi - lo)
    return round(max(0.15, 1.0 - d), 3)


def _derive_confidence(structural: dict, anchors: dict) -> dict:
    """Confidence COMPUTED (not an LLM number) from three named, auditable factors:
    band tightness, surviving-evidence quality, and analyst/DCF-band agreement."""
    target = structural.get("target") or 0.0
    lo, hi = structural.get("range", [0.0, 0.0]) or [0.0, 0.0]
    dispersion = round(1 - min(1.0, (hi - lo) / target), 3) if target else 0.0
    surviving = [c for c in structural.get("claims", []) if not c.get("rejected")]
    tiers = [c.get("tier", "E5") for c in surviving]
    evidence_quality = round(sum(t in ("E1", "E2") for t in tiers) / len(tiers), 3) if tiers else 0.0
    band = _band_agreement(target, anchors)
    # A tight range earns credit only if enough claims actually survived — otherwise a lone
    # weak claim (tiny range) would masquerade as high conviction.
    coverage = round(min(1.0, len(surviving) / 3.0), 3)
    conf = round(min(0.9, max(0.15, 0.20 + 0.30 * dispersion * coverage
                                    + 0.25 * evidence_quality + 0.25 * band)), 2)
    return {"confidence": conf, "factors": {
        "dispersion": dispersion, "coverage": coverage, "evidence_quality": evidence_quality,
        "band_agreement": band, "n_surviving": len(surviving),
        "formula": "0.20 + 0.30·tightness·coverage + 0.25·(E1/E2 share) + 0.25·(analyst/DCF-band agreement)",
    }}


def _price_claims(vi, claim_dicts: list, anchors: dict) -> Optional[dict]:
    """Price the adjudicated claims through the engine and attach the full verification
    detail: per-claim verdicts/cites/links, the multiple breakdown, and confidence factors."""
    if vi is None or not claim_dicts:
        return None
    engine_claims = _claims_to_engine(claim_dicts)
    if not engine_claims:
        return None
    result = assemble(vi.base, vi.leverage, engine_claims, price=vi.price,
                      margin_quality=vi.margin_quality, own_hist_pe=vi.own_hist_pe)
    result["claims"] = claim_dicts                 # full set (incl. rejected/flagged) with reasons
    result["multiple_breakdown"] = {
        "quality_base": quality_base_multiple(vi.margin_quality),
        "op_margin": round(vi.margin_quality, 4),
        "sustainable_growth_pct": result.get("sustainable_growth_pct"),
        "own_hist_pe": round(vi.own_hist_pe, 1) if vi.own_hist_pe else None,
        "warranted": result.get("multiple"),
        "formula": "warranted P/E = quality_base(margin) + 1.6 × max(0, sustainable_growth% − 3), capped [8, 45]",
    }
    conf = _derive_confidence(result, anchors)
    result["confidence"] = conf["confidence"]
    result["confidence_factors"] = conf["factors"]
    return result


def _final_conclusion(structural: Optional[dict], judge: dict, stop_reason: str, rounds_len: int) -> dict:
    """The headline: view_return AND confidence are both CODE-derived from the structural
    valuation. The Judge supplies only the narrative rationale + open questions."""
    vr = (round(structural["upside_pct"] / 100, 4)
          if (structural and structural.get("upside_pct") is not None) else None)
    return {
        "view_return": vr,
        "base_confidence": structural.get("confidence") if structural else None,
        "view_source": "structural" if structural else "none",
        "target_price": structural.get("target") if structural else None,
        "rationale": (judge or {}).get("rationale", ""),
        "open_questions": (judge or {}).get("open_questions", []),
        "rounds": rounds_len,
        "stop_reason": stop_reason,
        "parse_error": (judge or {}).get("parse_error", False),
    }


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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def _join_transcript(lines: list[str]) -> str:
    return "\n\n".join(lines) if lines else "(debate has not started)"


async def _one_round(
    *, r: int, total_rounds: int, transcript: list[str], bull_sys: str, bear_sys: str,
    judge_sys: str, vi, anchors: dict, filings_meta: list, prior_claims: list,
    api_key: str, model: str, call_llm, user_note: str = "",
) -> tuple[dict, bool, bool, dict, dict]:
    """One round: Bull emits CITED CLAIMS → Bear REBUTS them → Judge ADJUDICATES each →
    code PRICES the survivors. No agent emits a target/return/confidence. Mutates
    ``transcript`` and returns (round_dict, bull_new, bear_new, verdict, structural)."""
    ctx = f"\n\n{user_note}\n" if user_note else ""

    # --- Bull → cited driver-claims ---
    if r == 1 and not transcript:
        bull_user = f"Round 1. Open: build the bull case and your CLAIMS set.{ctx}"
    else:
        bull_user = (
            f"Debate so far:\n\n{_join_transcript(transcript)}{ctx}\n\n"
            f"Round {r}. As the BULL: drop/downgrade any claim the Bear rebutted, add new cited claims, "
            f"and RE-EMIT your FULL current CLAIMS set."
        )
    bull_text = await call_llm(
        api_key=api_key, model=model, max_tokens=600, temperature=0.6,
        messages=[{"role": "system", "content": bull_sys}, {"role": "user", "content": bull_user}],
    )
    bull_new = _parse_new_argument(bull_text)
    claims = _link_citations(_sanitize_claims(_parse_bull_claims(bull_text), "bull"), filings_meta)
    if not claims:
        claims = prior_claims or []           # fall back to the last good set if this round didn't re-emit
    transcript.append(f"[Round {r} · BULL]\n{bull_text}")

    # --- Bear → rebuttals (+ optional negative claims) ---
    bear_user = (
        f"Debate so far:\n\n{_join_transcript(transcript)}{ctx}\n\n"
        f"Round {r}. As the BEAR: rebut the Bull's CLAIMS by id with cited counters; add red flags "
        f"and any negative claims of your own."
    )
    bear_text = await call_llm(
        api_key=api_key, model=model, max_tokens=600, temperature=0.6,
        messages=[{"role": "system", "content": bear_sys}, {"role": "user", "content": bear_user}],
    )
    bear_new = _parse_new_argument(bear_text)
    rebuttals, bear_raw_claims = _parse_bear_rebuttals(bear_text)
    bear_claims = _link_citations(_sanitize_claims(bear_raw_claims, "bear"), filings_meta)
    all_claims = claims + [c for c in bear_claims if c["id"] not in {x["id"] for x in claims}]
    transcript.append(f"[Round {r} · BEAR]\n{bear_text}")

    # --- Judge → per-claim adjudication (no numbers) ---
    claims_view = json.dumps([{k: c[k] for k in ("id", "driver", "magnitude", "tier", "cite", "label")}
                              for c in all_claims])
    judge_user = (
        f"Bull+Bear CLAIMS to adjudicate:\n{claims_view}\n\n"
        f"Bear REBUTTALS (target = claim id):\n{json.dumps(rebuttals)}\n\n"
        f"Full transcript through round {r} of {total_rounds}:\n\n{_join_transcript(transcript)}{ctx}\n\n"
        f"Adjudicate EVERY claim id. JSON only."
    )
    judge_text = await call_llm(
        api_key=api_key, model=model, max_tokens=800, temperature=0.15,
        messages=[{"role": "system", "content": judge_sys}, {"role": "user", "content": judge_user}],
    )
    verdict = _parse_adjudication(judge_text)

    # --- Code prices the survivors — the ONLY place a number is produced ---
    adjudicated = _apply_adjudication(all_claims, verdict["adjudication"])
    structural = _price_claims(vi, adjudicated, anchors)

    round_dict = {
        "round": r,
        "bull": {"argument": bull_text, "new_argument": bull_new, "claims": adjudicated},
        "bear": {"argument": bear_text, "new_argument": bear_new, "rebuttals": rebuttals},
        "judge": {
            "adjudication": verdict["adjudication"], "rationale": verdict["rationale"],
            "open_questions": verdict["open_questions"], "new_information": verdict["new_information"],
            "should_continue": verdict["should_continue"], "parse_error": verdict["parse_error"],
            "raw": judge_text,
        },
        "valuation": structural,
        "upside_pct": structural.get("upside_pct") if structural else None,
        "confidence": structural.get("confidence") if structural else None,
    }
    return round_dict, bull_new, bear_new, verdict, structural


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

    vi = evidence.get("valuation_inputs")
    filings_meta = evidence["filings_meta"]
    transcript: list[str] = []   # running, labelled debate log shown to every agent
    rounds: list[dict] = []
    prior_claims: list = []      # last good claim set, so a non-re-emitting round doesn't lose it
    structural: Optional[dict] = None
    stop_reason = "reached the round cap without converging"

    for r in range(1, max_rounds + 1):
        round_dict, bull_new, bear_new, verdict, structural = await _one_round(
            r=r, total_rounds=max_rounds, transcript=transcript,
            bull_sys=bull_sys, bear_sys=bear_sys, judge_sys=judge_sys,
            vi=vi, anchors=anchors, filings_meta=filings_meta, prior_claims=prior_claims,
            api_key=api_key, model=model, call_llm=call_llm,
        )
        rounds.append(round_dict)
        if structural and structural.get("claims"):
            prior_claims = [dict(c) for c in structural["claims"] if not c.get("rejected")]

        # --- Termination gates: CODE confidence (not an LLM number) + Judge's convergence calls ---
        conf = structural.get("confidence") if structural else None
        if verdict["parse_error"]:
            stop_reason = "Judge adjudication was unreadable — halting"
            break
        if conf is not None and conf >= confidence_target:
            stop_reason = f"computed confidence hit the target ({conf:.0%} ≥ {confidence_target:.0%})"
            break
        if not verdict["should_continue"]:
            stop_reason = "Judge ruled the claim set had stabilized"
            break
        if not verdict["new_information"]:
            stop_reason = "the round added no new information"
            break
        if not bull_new and not bear_new:
            stop_reason = "both sides had nothing new to add"
            break

    final_judge = rounds[-1]["judge"] if rounds else {}
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
        "structural_valuation": structural,   # the last round's priced result (authoritative)
        "dcf_source": "user" if dcf_override else "auto",
        "prompts": {"bull": bull_sys, "bear": bear_sys, "judge": judge_sys},
        "rounds": rounds,
        "conclusion": _final_conclusion(structural, final_judge, stop_reason, len(rounds)),
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

    # Rebuild the running transcript + carry the prior surviving claim set forward.
    transcript: list[str] = []
    for rd in prior_rounds:
        n = rd.get("round")
        transcript.append(f"[Round {n} · BULL]\n{(rd.get('bull') or {}).get('argument', '')}")
        transcript.append(f"[Round {n} · BEAR]\n{(rd.get('bear') or {}).get('argument', '')}")
    prior_sv = prior.get("structural_valuation") or {}
    prior_claims = [dict(c) for c in (prior_sv.get("claims") or []) if not c.get("rejected")]

    # Re-fetch the base (prompts are stored, but the ValuationInputs are not).
    try:
        vi = await fetch_valuation_inputs(ticker)
    except Exception as exc:
        logger.info("valuation inputs failed on continue for %s: %s", ticker, exc)
        vi = None

    user_note = (
        "=== USER-PROVIDED ANSWERS (verified analyst input — treat as FACT; it resolves prior open "
        f"questions where applicable, so update your claims accordingly) ===\n{user_input.strip()}"
    )
    r = len(prior_rounds) + 1
    round_dict, _bn, _rn, verdict, structural = await _one_round(
        r=r, total_rounds=r, transcript=transcript,
        bull_sys=bull_sys, bear_sys=bear_sys, judge_sys=judge_sys,
        vi=vi, anchors=anchors, filings_meta=(prior.get("evidence") or {}).get("filings", []),
        prior_claims=prior_claims, api_key=api_key, model=model, call_llm=call_llm, user_note=user_note,
    )
    round_dict["user_input"] = user_input.strip()   # shown above this round in the UI

    updated = dict(prior)
    updated["rounds"] = prior_rounds + [round_dict]
    updated["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if structural is not None:
        updated["structural_valuation"] = structural
        updated["structural_base"] = _structural_base_dict(vi)
    updated["conclusion"] = _final_conclusion(structural, round_dict["judge"],
                                              "continued with your input", len(updated["rounds"]))
    updated["available"] = True
    return updated
