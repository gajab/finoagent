"""Pick & Shovel theme analysis service.

Given a theme or news event, uses a multi-stage LLM reasoning process to map
the FULL investment supply chain across three tiers — going far beyond the
obvious names to surface truly hidden beneficiaries:

  Tier 1 — Theme Core   : direct beneficiaries (the obvious plays)
  Tier 2 — The Backbone : infrastructure, tooling, specialized equipment,
                          components, materials — companies WITHOUT WHICH
                          the theme cannot happen physically
  Tier 3 — Hidden Picks : often-overlooked second/third-order beneficiaries —
                          utilities, industrials, specialty chemicals, REITs,
                          logistics, precision manufacturing, niche SaaS —
                          companies that almost nobody covers in thematic reports

Key examples of what "deep" means:
  AI infrastructure → ASM International (ASMI, NASDAQ: ASMI), MACOM Technology,
    Watts Water Technologies (cooling), Atkore (electrical conduit/cable mgmt),
    Preformed Line Products (grid hardware), Quanta Services (grid construction)
  GLP-1 drug boom   → Gerresheimer (vials), West Pharmaceutical (stoppers),
    Stevanato Group (drug-delivery systems), Catalent (fill/finish)
  Nuclear energy    → BWX Technologies (reactor components), Curtiss-Wright
    (nuclear instrumentation), NuScale (SMR), Centrus Energy (uranium enrichment)
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime, timezone

import yfinance as yf

from .llm_service import call_llm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# LLM prompt — multi-stage, chain-of-thought style
# ---------------------------------------------------------------------------

_SYSTEM = """You are a Bloomberg Intelligence–grade thematic equity analyst with
deep sector expertise across semiconductors, energy, healthcare, industrials,
materials, and real estate.

Your specialty: identifying the COMPLETE physical and economic supply chain
for any investment theme — going far beyond the headline names every analyst
covers, to surface companies that almost no thematic reports mention but which
are essential enablers.

RESEARCH METHODOLOGY — follow these steps mentally before generating output:
1. Map the physical value chain end-to-end: raw materials → components →
   sub-assembly → integration → deployment → servicing → real estate/utilities.
2. For each step, ask: "What company makes the specialized tool, material,
   component, or service that enables this step — and is listed on a major
   exchange?"
3. Prefer SPECIFIC over GENERIC: "MACOM Technology Solutions (RF power amps for
   data center optical transceivers)" beats "a semiconductor company."
4. Include non-obvious geographies: Dutch, German, Japanese, Taiwanese companies
   listed on US exchanges (NASDAQ/NYSE) or their major home exchanges are valid.
5. Tier 3 must include at minimum: one utility/REIT/infrastructure play, one
   specialty industrial or materials company, one precision manufacturing or
   specialty chemicals company.

RULES:
1. Output ONLY valid JSON — no prose, no markdown fences.
2. Tickers must be real, currently-listed tickers on NYSE, NASDAQ, NYSE American,
   EURONEXT, TSE, or other major global exchanges. Include exchange suffix for
   non-US tickers (e.g. "ASMI" for ASM International on NASDAQ, "ASML" on NASDAQ).
3. 5–7 companies per tier — quality and depth over quantity.
4. NO overlap between tiers.
5. "deep_picks" MUST be meaningfully non-obvious — if a company appears in
   every thematic ETF, it belongs in Tier 1/2, not Tier 3.
6. revenue_exposure: be specific — cite percentage, product line, customer.
7. risk: a real, specific downside (not generic "competition").
8. earnings_signal: actual language or data point from recent earnings, or omit.
9. For Tier 2 (backbone): always include at least one company from each relevant
   category: specialized equipment/tooling, materials/components, construction
   or engineering services.
10. For Tier 3 (hidden): always include at least one from: utilities/power,
    specialty REITs or infrastructure, specialty chemicals or materials,
    precision manufacturing, niche software/data providers.
11. If a "USER RESEARCH BRIEF" block is present, treat it as the authoritative
    statement of the PM's intent: honor its scope, weight its learned preferences
    heavily, and NEVER recommend anything in its exclusion list."""

_USER_TEMPLATE = """THEME / CATALYST: {theme}

STEP 1 — Map the full physical supply chain in your reasoning.
STEP 2 — Identify the most compelling companies at each tier, prioritizing
         depth and non-obviousness for Tiers 2 and 3.
STEP 3 — Output the result as JSON with EXACTLY this schema:

{{
  "theme_title": "Compelling short title (≤8 words)",
  "theme_summary": "4–5 sentence professional summary: opportunity size, structural driver, timeline, key risk.",
  "key_trends": [
    "Specific quantified trend (e.g. '500 GW of new data center capacity planned 2024-2030')",
    "Specific trend 2",
    "Specific trend 3",
    "Specific trend 4",
    "Specific trend 5"
  ],
  "supply_chain_map": "2–3 sentence description of the end-to-end supply chain steps",
  "direct_plays": [
    {{
      "ticker": "XXXX",
      "name": "Full legal company name",
      "exchange": "NASDAQ | NYSE | EURONEXT | TSE etc.",
      "thesis": "Sharp one-sentence specific mechanism of direct benefit.",
      "catalysts": ["Specific cat 1", "Specific cat 2", "Specific cat 3"],
      "revenue_exposure": "~X% of FY revenue from [specific segment]",
      "earnings_signal": "Quote or paraphrase from most recent relevant earnings",
      "risk": "Specific key downside risk",
      "why_this_not_another": "One sentence on competitive moat or uniqueness"
    }}
  ],
  "enablers": [
    {{
      "ticker": "XXXX",
      "name": "Full legal company name",
      "exchange": "...",
      "supply_chain_role": "Exactly where in the supply chain (e.g. 'lithography equipment for EUV chip production')",
      "thesis": "Specific mechanism — what product/service and who buys it.",
      "catalysts": ["cat1", "cat2", "cat3"],
      "revenue_exposure": "specific % or description",
      "earnings_signal": "optional",
      "risk": "specific risk",
      "why_overlooked": "Why most thematic investors miss this company"
    }}
  ],
  "deep_picks": [
    {{
      "ticker": "XXXX",
      "name": "Full legal company name",
      "exchange": "...",
      "hidden_link": "The non-obvious connection to the theme (1–2 sentences)",
      "thesis": "Specific benefit mechanism.",
      "catalysts": ["cat1", "cat2"],
      "revenue_exposure": "specific or 'indirect beneficiary via X'",
      "risk": "specific risk",
      "discovery_insight": "Why almost no analysts cover this in the context of this theme"
    }}
  ]
}}"""


# ---------------------------------------------------------------------------
# Verification pass — ticker / name / evidence check
# ---------------------------------------------------------------------------

_VERIFY_SYSTEM = """You are a financial data accuracy auditor fact-checking thematic equity research.

For each company entry you MUST verify three things:

1. TICKER ACCURACY — does this exact ticker symbol belong to the named company on the stated exchange?
   Fail if: ticker belongs to a different company entirely, ticker has been reassigned post-M&A,
   exchange suffix is wrong, or it looks like a common confusion (e.g. APLE ≠ AAPL, MSFT ≠ MFST).

2. COMPANY IDENTITY — is the full legal name correct and unambiguous?
   Fail if: name matches a different well-known company (e.g. "Applied Signal" vs "Applied Materials"),
   the company was acquired/delisted and no longer trades independently, or the name is
   so generic it could describe multiple listed companies.

3. DOCUMENTED THEME EXPOSURE — does this company have ACTUAL published evidence of exposure
   to the stated theme? Acceptable evidence (cite at least one):
     • Earnings call transcript: specific management quote about the theme
     • SEC filing (10-K, 10-Q, 8-K): named segment, revenue %, or explicit disclosure
     • Investor Relations presentation or press release with specific data
   NOT acceptable: generic "benefits from macro trend" with no specific citation.
   If you cannot cite specific documented evidence from public filings/calls, set pass=false.

Output ONLY valid JSON — no prose, no markdown fences."""

_VERIFY_USER_TEMPLATE = """THEME: {theme}

Audit each company below. Be strict: when in doubt, fail it.

{companies_block}

Return JSON with exactly this structure:
{{
  "results": [
    {{
      "ticker": "XXXX",
      "pass": true,
      "ticker_correct": true,
      "name_correct": true,
      "evidence": "Specific citation — e.g. Q3 2024 earnings call: CEO stated '$X revenue from [theme segment]'. 10-K FY2024 p.47: '[theme] segment grew X%'.",
      "fail_reason": null
    }}
  ]
}}

One result object per company, in the same order as the input."""


def _normalize_name(s: str) -> str:
    """Strip noise words for name comparison."""
    import re as _re
    s = s.lower()
    for w in ("inc.", "inc", "corp.", "corp", "ltd.", "ltd", "plc", "co.", " co",
              "llc", "holdings", "group", "international", "technologies", "technology",
              "solutions", "systems", "services", "the ", ",", ".", "-", "&"):
        s = s.replace(w, " ")
    return " ".join(_re.sub(r"\s+", " ", s).split())


def _names_plausibly_match(llm_name: str, fin_name: str) -> bool:
    """Return True if yfinance name and LLM name are plausibly the same company."""
    if not llm_name or not fin_name:
        return True   # can't check → allow through
    n1 = set(_normalize_name(llm_name).split())
    n2 = set(_normalize_name(fin_name).split())
    stopwords = {"a", "of", "for", "and", "by", "the", "in", "on"}
    n1 -= stopwords
    n2 -= stopwords
    if not n1 or not n2:
        return True
    overlap = n1 & n2
    ratio = len(overlap) / min(len(n1), len(n2))
    return ratio >= 0.35   # at least 35% meaningful-word overlap


async def _verify_companies(
    companies: list[dict],
    theme: str,
    openai_key: str,
    model: str = "gpt-4o",
) -> dict[str, dict]:
    """LLM verification pass. Returns dict[ticker → {pass, evidence, fail_reason}]."""
    if not companies or not openai_key:
        return {}

    lines = []
    for c in companies:
        tkr = c.get("ticker", "")
        lines.append(
            f"  • {tkr} | {c.get('name', '')} | Exchange: {c.get('exchange', 'US')} | "
            f"Claimed connection: {(c.get('thesis') or c.get('hidden_link') or '')[:120]}"
        )
    companies_block = "\n".join(lines)

    try:
        raw = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[
                {"role": "system", "content": _VERIFY_SYSTEM},
                {"role": "user",   "content": _VERIFY_USER_TEMPLATE.format(
                    theme=theme, companies_block=companies_block,
                )},
            ],
            max_tokens=2500,
            temperature=0.1,   # near-deterministic for fact-checking
        )
        data = _extract_json(raw)
        results = data.get("results") or []
        return {
            r["ticker"].upper(): r
            for r in results
            if r.get("ticker")
        }
    except Exception as exc:
        logger.warning("Verification LLM pass failed: %s — skipping verification", exc)
        return {}


def _apply_verification(
    cards: list[dict],
    verify_map: dict[str, dict],
    financials: dict[str, dict],
) -> list[dict]:
    """Annotate cards with verification results and drop hard failures.

    A company is DROPPED if:
      • LLM verification explicitly says pass=False AND ticker_correct=False
        (ticker belongs to a completely different company), OR
      • yfinance name diverges massively from the LLM name AND LLM also fails verification.

    Borderline failures (evidence missing but ticker correct) are KEPT but flagged
    so the frontend can display a warning badge.
    """
    verified = []
    for card in cards:
        tkr = card["ticker"]
        vr = verify_map.get(tkr, {})

        # yfinance name sanity-check
        fin_name = (financials.get(tkr) or {}).get("short_name", "")
        llm_name = card.get("name", "")
        name_ok = _names_plausibly_match(llm_name, fin_name)

        ticker_correct = vr.get("ticker_correct", True)   # default allow if no LLM result
        llm_pass       = vr.get("pass", True)              # default allow

        # Hard drop: LLM says wrong ticker AND yfinance name also disagrees
        if not ticker_correct and not name_ok:
            logger.info(
                "DROPPED %s — ticker mismatch (LLM named '%s', yfinance: '%s') reason: %s",
                tkr, llm_name, fin_name, vr.get("fail_reason", "")
            )
            continue

        # Soft flag: evidence missing or name confusion but ticker is right
        card["verified"]          = llm_pass and name_ok
        card["verification_note"] = vr.get("fail_reason") if not llm_pass else vr.get("evidence")
        card["yfinance_name"]     = fin_name if fin_name else None

        verified.append(card)

    return verified


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _extract_json(raw: str) -> dict:
    text = re.sub(r"```(?:json)?", "", raw).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in LLM response")
    blob = text[start: end + 1]
    blob = re.sub(r",\s*([\]}])", r"\1", blob)
    return json.loads(blob)


# ---------------------------------------------------------------------------
# yfinance enrichment
# ---------------------------------------------------------------------------

def _safe_float(v, default=None):
    try:
        f = float(v)
        return default if math.isnan(f) or math.isinf(f) else f
    except Exception:
        return default


def _fetch_financials_sync(tickers: list[str]) -> dict[str, dict]:
    """Batch-fetch price/PE/52W/market-cap for all tickers."""
    result: dict[str, dict] = {t: {} for t in tickers}
    if not tickers:
        return result

    try:
        ticker_obj = yf.Tickers(" ".join(tickers))
        for tkr in tickers:
            try:
                t = ticker_obj.tickers.get(tkr) or yf.Ticker(tkr)
                info = t.info or {}
                curr = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
                prev = _safe_float(info.get("previousClose") or info.get("regularMarketPreviousClose"))
                pct_chg = ((curr - prev) / prev * 100.0) if curr and prev and prev != 0 else None

                w52_high = _safe_float(info.get("fiftyTwoWeekHigh"))
                w52_low  = _safe_float(info.get("fiftyTwoWeekLow"))
                if not w52_high or not w52_low:
                    try:
                        hist = t.history(period="1y", interval="1d", auto_adjust=True)
                        if not hist.empty:
                            w52_high = float(hist["Close"].max())
                            w52_low  = float(hist["Close"].min())
                    except Exception:
                        pass

                result[tkr] = {
                    "price":        round(curr, 2) if curr else None,
                    "price_chg_1d": round(pct_chg, 2) if pct_chg is not None else None,
                    "pe_ratio":     round(_safe_float(info.get("trailingPE")), 1) if _safe_float(info.get("trailingPE")) else None,
                    "forward_pe":   round(_safe_float(info.get("forwardPE")), 1) if _safe_float(info.get("forwardPE")) else None,
                    "market_cap":   _safe_float(info.get("marketCap")),
                    "week52_high":  round(w52_high, 2) if w52_high else None,
                    "week52_low":   round(w52_low, 2) if w52_low else None,
                    "sector":       info.get("sector") or info.get("industryDisp"),
                    "short_name":   info.get("shortName") or info.get("longName") or tkr,
                }
            except Exception as exc:
                logger.debug("Financial fetch failed for %s: %s", tkr, exc)
    except Exception as exc:
        logger.warning("Batch yfinance fetch failed: %s — trying individually", exc)
        for tkr in tickers:
            if result[tkr]:
                continue
            try:
                info = yf.Ticker(tkr).info or {}
                curr = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
                prev = _safe_float(info.get("previousClose"))
                pct_chg = ((curr - prev) / prev * 100.0) if curr and prev and prev != 0 else None
                result[tkr] = {
                    "price":        round(curr, 2) if curr else None,
                    "price_chg_1d": round(pct_chg, 2) if pct_chg is not None else None,
                    "pe_ratio":     round(_safe_float(info.get("trailingPE")), 1) if _safe_float(info.get("trailingPE")) else None,
                    "forward_pe":   round(_safe_float(info.get("forwardPE")), 1) if _safe_float(info.get("forwardPE")) else None,
                    "market_cap":   _safe_float(info.get("marketCap")),
                    "week52_high":  round(_safe_float(info.get("fiftyTwoWeekHigh")), 2) if _safe_float(info.get("fiftyTwoWeekHigh")) else None,
                    "week52_low":   round(_safe_float(info.get("fiftyTwoWeekLow")), 2) if _safe_float(info.get("fiftyTwoWeekLow")) else None,
                    "sector":       info.get("sector"),
                    "short_name":   info.get("shortName") or tkr,
                }
            except Exception as exc2:
                logger.debug("Individual fallback failed for %s: %s", tkr, exc2)
    return result


def _fmt_market_cap(v) -> str | None:
    if v is None:
        return None
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _build_company_card(ticker: str, llm_data: dict, fin: dict) -> dict:
    return {
        # LLM-sourced fields
        "ticker":            ticker.upper(),
        "name":              llm_data.get("name") or fin.get("short_name") or ticker,
        "exchange":          llm_data.get("exchange"),
        "thesis":            llm_data.get("thesis", ""),
        "catalysts":         llm_data.get("catalysts") or [],
        "revenue_exposure":  llm_data.get("revenue_exposure"),
        "earnings_signal":   llm_data.get("earnings_signal"),
        "risk":              llm_data.get("risk"),
        # Tier-specific depth fields
        "supply_chain_role": llm_data.get("supply_chain_role"),   # Tier 2
        "why_overlooked":    llm_data.get("why_overlooked"),       # Tier 2
        "hidden_link":       llm_data.get("hidden_link"),          # Tier 3
        "discovery_insight": llm_data.get("discovery_insight"),    # Tier 3
        "why_this_not_another": llm_data.get("why_this_not_another"),  # Tier 1
        # yfinance-sourced
        "price":         fin.get("price"),
        "price_chg_1d":  fin.get("price_chg_1d"),
        "pe_ratio":      fin.get("pe_ratio"),
        "forward_pe":    fin.get("forward_pe"),
        "market_cap":    _fmt_market_cap(fin.get("market_cap")),
        "week52_high":   fin.get("week52_high"),
        "week52_low":    fin.get("week52_low"),
        "sector":        fin.get("sector"),
    }


# ---------------------------------------------------------------------------
# Thesis interpretation — the collaborative "understand intent" pass
# ---------------------------------------------------------------------------

_INTERPRET_SYSTEM = """You are a Bloomberg-terminal research analyst sitting across
the desk from a portfolio manager. The PM has just handed you an investment thesis
for a "pick-and-shovel" study (mapping the supply chain of beneficiaries behind a
theme). Your job in THIS step is NOT to pick stocks yet — it is to make sure you
understand exactly what the PM is after before you spend effort researching.

Do three things:

1. RESTATE the thesis in one or two sharp sentences, in your own words — showing the
   PM you understood the actual intent, not just echoing keywords.

2. EXTRACT the specific angles/scope the PM's OWN WORDS imply — e.g. a named
   sub-segment, a geography they mentioned, a stage of the value chain, a type of
   company, or something they said to avoid. These are observations pulled FROM the
   thesis, never a generic profiling checklist.

3. ASK A CLARIFYING QUESTION **ONLY IF** the thesis is genuinely ambiguous in a way
   that would change which companies you surface. Each question must:
     • resolve a real, specific ambiguity in THIS thesis (not generic profiling),
     • be a crisp either/or or short multiple-choice with concrete options,
     • help you target better.
   STRICT: Do NOT ask about risk appetite, market cap, time horizon, position size,
   or other generic investor-profile attributes UNLESS the PM's own words raised them.
   If the thesis is already clear enough to research well, return an EMPTY questions
   list and set ready=true. Asking nothing is the correct, preferred answer when in
   no real doubt. At most 3 questions, and only the ones that truly matter.

Output ONLY valid JSON — no prose, no markdown fences."""

_INTERPRET_USER_TEMPLATE = """ORIGINAL THESIS FROM THE PM:
{thesis}
{prior_block}{reply_block}{answers_block}
Return JSON with EXACTLY this schema:
{{
  "interpretation": "1-2 sentence restatement of what the PM is really after.",
  "scope_notes": [
    "Specific angle/boundary inferred from the PM's words (e.g. 'Focus: physical build-out, not chips')",
    "Another concrete, thesis-derived note"
  ],
  "exclusions": ["Only include things the PM implied avoiding; else empty list"],
  "clarifying_questions": [
    {{
      "id": "short_slug",
      "question": "A crisp, thesis-specific question that resolves a real ambiguity.",
      "options": [
        {{"label": "Concrete option A", "value": "Concrete option A"}},
        {{"label": "Concrete option B", "value": "Concrete option B"}}
      ],
      "allow_multiple": false,
      "allow_custom": true
    }}
  ],
  "ready": true
}}

Remember: clarifying_questions should be EMPTY unless a genuine ambiguity in this
thesis would change which companies you'd surface. When empty, set ready=true."""


def _brief_to_block(brief: dict | None) -> str:
    """Render a research brief as a compact text block for generation prompts."""
    if not brief:
        return ""
    parts: list[str] = []
    interp = (brief.get("interpretation") or "").strip()
    if interp:
        parts.append(f"PM intent (as understood): {interp}")
    scope = [s for s in (brief.get("scope_notes") or []) if s and s.strip()]
    if scope:
        parts.append("Scope / angles to honor:\n" + "\n".join(f"  • {s}" for s in scope))
    excl = [e for e in (brief.get("exclusions") or []) if e and e.strip()]
    if excl:
        parts.append("Exclude (do NOT recommend these / anything matching):\n"
                     + "\n".join(f"  • {e}" for e in excl))
    learned = [p for p in (brief.get("preferences_learned") or []) if p and p.strip()]
    if learned:
        parts.append("Preferences learned from the PM's keep/drop feedback (weight heavily):\n"
                     + "\n".join(f"  • {p}" for p in learned))
    if not parts:
        return ""
    return ("\n\n=== USER RESEARCH BRIEF — honor this as the PM's intent ===\n"
            + "\n\n".join(parts)
            + "\n=== END BRIEF ===\n")


async def interpret_thesis(
    thesis: str,
    openai_key: str,
    model: str = "gpt-4o",
    brief: dict | None = None,
    user_reply: str | None = None,
    answers: list[dict] | None = None,
) -> dict:
    """Collaborative 'understand intent' pass.

    Reads the PM's thesis, restates it, extracts thesis-derived scope, and asks
    clarifying questions ONLY when the thesis is genuinely ambiguous. Incorporates
    any prior brief, a free-text refinement (``user_reply``), and answers to earlier
    questions (``answers`` = ``[{"question": str, "answer": str}, ...]``).

    Returns ``{brief, interpretation, clarifying_questions, ready}``.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    # Prior understanding (so refinement is incremental, not a reset)
    prior_block = ""
    if brief and (brief.get("interpretation") or brief.get("scope_notes")):
        prior = _brief_to_block(brief).strip()
        if prior:
            prior_block = f"\nYOUR CURRENT UNDERSTANDING SO FAR (refine, don't restart):\n{prior}\n"

    reply_block = ""
    if user_reply and user_reply.strip():
        reply_block = (f"\nThe PM just added this clarification IN THEIR OWN WORDS — "
                       f"weight it heavily and fold it into your understanding:\n"
                       f"\"{user_reply.strip()}\"\n")

    answers_block = ""
    if answers:
        lines = [
            f"  • {a.get('question', '').strip()} → {a.get('answer', '').strip()}"
            for a in answers if a.get("answer")
        ]
        if lines:
            answers_block = ("\nThe PM answered your earlier clarifying questions "
                             "(do NOT ask these again):\n" + "\n".join(lines) + "\n")

    raw = await call_llm(
        api_key=openai_key,
        model=model,
        messages=[
            {"role": "system", "content": _INTERPRET_SYSTEM},
            {"role": "user",   "content": _INTERPRET_USER_TEMPLATE.format(
                thesis=thesis.strip(),
                prior_block=prior_block,
                reply_block=reply_block,
                answers_block=answers_block,
            )},
        ],
        max_tokens=1200,
        temperature=0.3,
    )

    try:
        data = _extract_json(raw)
    except Exception as exc:
        logger.error("JSON parse failed for interpret output: %s\nRaw: %.500s", exc, raw)
        # Graceful fallback: treat the thesis itself as the interpretation, no questions.
        data = {"interpretation": thesis.strip(), "scope_notes": [],
                "exclusions": [], "clarifying_questions": [], "ready": True}

    questions = data.get("clarifying_questions") or []
    # Normalize/guard question objects so the frontend never crashes on bad shapes.
    norm_questions = []
    for i, q in enumerate(questions):
        if not isinstance(q, dict) or not q.get("question"):
            continue
        opts = []
        for o in (q.get("options") or []):
            if isinstance(o, dict) and o.get("label"):
                opts.append({"label": str(o["label"]), "value": str(o.get("value") or o["label"])})
            elif isinstance(o, str):
                opts.append({"label": o, "value": o})
        norm_questions.append({
            "id": str(q.get("id") or f"q{i}"),
            "question": str(q["question"]),
            "options": opts,
            "allow_multiple": bool(q.get("allow_multiple", False)),
            "allow_custom": bool(q.get("allow_custom", True)),
        })

    # Merge into / build the running brief (preserve learned prefs across rounds).
    merged_brief = {
        "thesis": thesis.strip(),
        "interpretation": (data.get("interpretation") or thesis.strip()).strip(),
        "scope_notes": [s for s in (data.get("scope_notes") or []) if isinstance(s, str) and s.strip()],
        "exclusions": [e for e in (data.get("exclusions") or []) if isinstance(e, str) and e.strip()],
        "preferences_learned": list((brief or {}).get("preferences_learned") or []),
    }

    return {
        "brief": merged_brief,
        "interpretation": merged_brief["interpretation"],
        "clarifying_questions": norm_questions,
        "ready": bool(data.get("ready", not norm_questions)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def analyze_pick_shovel(
    theme: str,
    openai_key: str,
    model: str = "gpt-4o",
    brief: dict | None = None,
) -> dict:
    """Run a deep pick-and-shovel analysis for ``theme``.

    1. Multi-stage LLM reasoning with Bloomberg-grade prompt.
    2. Batch yfinance enrichment for all identified tickers.
    3. Returns structured payload for frontend.

    When ``brief`` is supplied (from the collaborative interpret/refine loop) its
    interpretation, scope, exclusions, and learned preferences are injected so the
    generation reflects the PM's intent rather than the raw theme alone.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    brief_block = _brief_to_block(brief)

    raw = await call_llm(
        api_key=openai_key,
        model=model,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": _USER_TEMPLATE.format(theme=theme.strip()) + brief_block},
        ],
        max_tokens=4500,
        temperature=0.2,   # low temp = precise, structured, reproducible
    )

    try:
        analysis = _extract_json(raw)
    except Exception as exc:
        logger.error("JSON parse failed for pick-shovel output: %s\nRaw: %.500s", exc, raw)
        raise ValueError(f"LLM returned unparseable response: {exc}") from exc

    # Collect all tickers
    def _tickers_from(lst: list[dict]) -> list[str]:
        return [c.get("ticker", "").strip().upper() for c in lst if c.get("ticker")]

    direct_raw  = analysis.get("direct_plays") or []
    enabler_raw = analysis.get("enablers") or []
    deep_raw    = analysis.get("deep_picks") or []

    all_tickers = list(dict.fromkeys(
        _tickers_from(direct_raw) + _tickers_from(enabler_raw) + _tickers_from(deep_raw)
    ))

    # Batch yfinance enrichment + verification pass (run concurrently)
    all_raw_companies = direct_raw + enabler_raw + deep_raw
    financials, verify_map = await asyncio.gather(
        asyncio.to_thread(_fetch_financials_sync, all_tickers),
        _verify_companies(all_raw_companies, theme.strip(), openai_key, model),
    )

    def _build_tier(raw_list: list[dict]) -> list[dict]:
        cards = []
        for item in raw_list:
            tkr = (item.get("ticker") or "").strip().upper()
            if not tkr:
                continue
            fin = financials.get(tkr) or {}
            cards.append(_build_company_card(tkr, item, fin))
        return _apply_verification(cards, verify_map, financials)

    return {
        "theme":            theme.strip(),
        "theme_title":      analysis.get("theme_title", theme),
        "theme_summary":    analysis.get("theme_summary", ""),
        "supply_chain_map": analysis.get("supply_chain_map", ""),
        "key_trends":       analysis.get("key_trends") or [],
        "direct_plays":     _build_tier(direct_raw),
        "enablers":         _build_tier(enabler_raw),
        "deep_picks":       _build_tier(deep_raw),
        "generated_at":     datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# "Dig Deeper" — iterative supply-chain discovery prompt
# ---------------------------------------------------------------------------

_DEEPER_SYSTEM = """You are an elite buy-side analyst running an exhaustive
supply-chain forensics investigation.

You have already identified the obvious and first-order indirect plays for a
given investment theme. Your job now is to go FURTHER DOWN the rabbit hole —
finding companies that almost no analyst or thematic ETF covers, but which
are genuine beneficiaries because of their specific role as:
  • A critical SUPPLIER to the companies already identified
  • A critical CLIENT / CUSTOMER of those companies (downstream pull)
  • A BOTTLENECK resource or service provider those companies depend on
  • A company that provides TESTING, CERTIFICATION, REGULATORY or INSPECTION
    services that are mandatory in this industry
  • A company that provides SPECIALTY FINANCING, INSURANCE or RISK MANAGEMENT
    unique to this sector
  • A company that provides SPECIALIZED DATA, SOFTWARE or MEASUREMENT tools
    only this industry uses
  • A company that owns or operates PHYSICAL INFRASTRUCTURE (land, ports,
    rail lines, storage, water rights) essential to this supply chain

HARD RULES:
1. NEVER recommend any ticker in the "already_shown" list. Violating this
   disqualifies the entire response.
2. Output ONLY valid JSON — no prose, no markdown.
3. Return exactly 6–8 companies total across all depth_finds.
4. Be extremely specific about WHY this company is connected — cite product
   names, customer names, contract details, or regulatory requirements.
5. Include global tickers (NASDAQ, NYSE, EURONEXT, TSE, LSE, ASX, HKEX) —
   use the primary ticker where it trades most liquidity.
6. Prefer: small/mid-cap companies, non-obvious sectors, names absent from
   mainstream thematic ETFs.
"""

_DEEPER_USER_TEMPLATE = """THEME: {theme}

DEPTH LEVEL: {depth_level} (higher = go deeper and further from the obvious)

ALREADY SHOWN — DO NOT REPEAT ANY OF THESE TICKERS:
{already_shown_block}

ANCHOR COMPANIES FROM PREVIOUS ROUND (use these as starting points to trace
their actual suppliers, clients, and dependency chains):
{anchor_block}

DIGGING STRATEGY FOR DEPTH {depth_level}:
{strategy}

Find 6–8 new companies. Output JSON:
{{
  "depth_label": "Short label for this layer (e.g. 'Tier-4: Grid Raw Materials')",
  "depth_rationale": "2–3 sentences explaining the investigation angle used at this depth",
  "companies": [
    {{
      "ticker": "XXXX",
      "name": "Full legal company name",
      "exchange": "NASDAQ | NYSE | EURONEXT | TSE | LSE | ASX | HKEX etc.",
      "connection_type": "supplier | client | bottleneck | testing | infra | data | finance",
      "connection_to": "Which anchor company/companies this relates to",
      "hidden_link": "The specific non-obvious connection — cite product, contract, regulation, or dependency",
      "thesis": "Why this company benefits from the theme via this connection",
      "catalysts": ["specific cat1", "specific cat2"],
      "revenue_exposure": "Specific % or 'indirect via X'",
      "risk": "Specific downside risk",
      "why_nobody_covers_this": "Why this name is absent from thematic ETFs and analyst reports"
    }}
  ]
}}"""

# Escalating dig strategies by depth level
_DEPTH_STRATEGIES = {
    1: ("Trace the DIRECT SUPPLIERS of the Tier-1/Tier-2 companies already shown. "
        "What raw materials, components, sub-assemblies, or specialized services do they "
        "purchase? Who makes those? Focus on single-source or near-monopoly suppliers."),
    2: ("Trace the CUSTOMERS and END-USERS of the companies already shown. "
        "Who buys from them, and what does that customer need to do its job? "
        "Find companies that are downstream beneficiaries or that enable the "
        "deployment/adoption of what's already been identified. "
        "Also look for TESTING, CERTIFICATION or INSPECTION firms whose services "
        "are mandatory in this industry."),
    3: ("Go to the PHYSICAL INFRASTRUCTURE layer. What real estate, utilities, "
        "water, power, transport or logistics infrastructure does this entire "
        "supply chain depend on? Think: specialized REITs, industrial gas suppliers, "
        "specialty chemical distributors, rail/port operators, water treatment. "
        "Also look for SPECIALTY INSURERS, ESG RATING agencies, or "
        "regulatory-compliance software providers unique to this sector."),
    4: ("Look at the RAW MATERIAL origins — the mines, wells, forests, farms, "
        "or chemical precursor producers that feed into tier-1 through tier-3. "
        "Also: what MAINTENANCE, REPAIR & OVERHAUL (MRO) companies service the "
        "equipment? What TRAINING or SIMULATION companies certify the workers? "
        "What STAFFING or ENGINEERING services firms fill specialized roles?"),
    5: ("Explore FINANCIAL INFRASTRUCTURE: specialty lenders, equipment lessors, "
        "trade-finance providers, royalty streamers, or commodity hedging firms "
        "uniquely positioned around this theme. Also: STANDARDS BODIES that are "
        "listed (rare but exist), PATENT LICENSORS, or IP holding companies. "
        "Finally: companies in ADJACENT GEOGRAPHIES that are the non-US "
        "equivalents of already-found companies."),
}


async def dig_deeper_pick_shovel(
    theme: str,
    already_shown: list[str],
    anchor_companies: list[dict],
    depth_level: int,
    openai_key: str,
    model: str = "gpt-4o",
) -> dict:
    """Find the next layer of non-obvious supply-chain companies for ``theme``.

    Parameters
    ----------
    theme:            Original theme string.
    already_shown:    Tickers shown in all previous rounds — LLM must avoid these.
    anchor_companies: Subset of previous-round companies to use as starting points
                      (ticker + name + supply_chain_role/hidden_link summary).
    depth_level:      1 = first "next" click, 2 = second, etc.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    # Format the exclusion block
    shown_block = "\n".join(f"  • {t}" for t in sorted(set(already_shown))) or "  (none yet)"

    # Format anchor companies for context
    anchor_lines = []
    for c in anchor_companies[:12]:   # cap context length
        role = c.get("supply_chain_role") or c.get("hidden_link") or c.get("thesis", "")[:80]
        anchor_lines.append(f"  • {c['ticker']} — {c['name']}: {role}")
    anchor_block = "\n".join(anchor_lines) if anchor_lines else "  (use the theme itself as anchor)"

    # Pick escalating strategy (cap at max defined level)
    strategy_key = min(depth_level, max(_DEPTH_STRATEGIES.keys()))
    strategy = _DEPTH_STRATEGIES[strategy_key]

    raw = await call_llm(
        api_key=openai_key,
        model=model,
        messages=[
            {"role": "system", "content": _DEEPER_SYSTEM},
            {"role": "user",   "content": _DEEPER_USER_TEMPLATE.format(
                theme=theme,
                depth_level=depth_level,
                already_shown_block=shown_block,
                anchor_block=anchor_block,
                strategy=strategy,
            )},
        ],
        max_tokens=3500,
        temperature=0.25,
    )

    try:
        analysis = _extract_json(raw)
    except Exception as exc:
        logger.error("JSON parse failed for dig-deeper output: %s\nRaw: %.500s", exc, raw)
        raise ValueError(f"LLM returned unparseable response: {exc}") from exc

    companies_raw = analysis.get("companies") or []

    # Filter out any tickers LLM ignored from already_shown (safety net)
    shown_upper = {t.upper() for t in already_shown}
    companies_raw = [
        c for c in companies_raw
        if (c.get("ticker") or "").strip().upper() not in shown_upper
    ]

    all_tickers = [c.get("ticker", "").strip().upper() for c in companies_raw if c.get("ticker")]
    all_tickers = list(dict.fromkeys(all_tickers))

    # Batch yfinance enrichment + verification pass (run concurrently)
    financials, verify_map = await asyncio.gather(
        asyncio.to_thread(_fetch_financials_sync, all_tickers),
        _verify_companies(companies_raw, theme, openai_key, model),
    )

    cards = []
    for item in companies_raw:
        tkr = (item.get("ticker") or "").strip().upper()
        if not tkr:
            continue
        fin = financials.get(tkr) or {}
        base = _build_company_card(tkr, item, fin)
        # Deeper-specific extra fields
        base["connection_type"] = item.get("connection_type")
        base["connection_to"]   = item.get("connection_to")
        base["why_nobody_covers_this"] = item.get("why_nobody_covers_this")
        # hidden_link is already set by _build_company_card from item
        cards.append(base)

    # Apply verification (drops hard ticker mismatches, flags weak evidence)
    cards = _apply_verification(cards, verify_map, financials)

    return {
        "depth_level":    depth_level,
        "depth_label":    analysis.get("depth_label", f"Depth {depth_level}"),
        "depth_rationale": analysis.get("depth_rationale", ""),
        "companies":      cards,
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Refine — learn from kept (positive) + dropped (negative), backfill dropped slots
# ---------------------------------------------------------------------------

_TIER_LABELS = {
    "direct":  "Theme Core (direct beneficiaries)",
    "enabler": "The Backbone (infrastructure / tooling / component enablers)",
    "deep":    "Hidden Picks (overlooked 2nd/3rd-order beneficiaries)",
}

_TIER_FIELDS = {
    "direct":  '"why_this_not_another": "competitive moat / uniqueness"',
    "enabler": '"supply_chain_role": "exact role in the chain", "why_overlooked": "why investors miss it"',
    "deep":    '"hidden_link": "the non-obvious connection", "discovery_insight": "why analysts miss this"',
}

_REFINE_SYSTEM = """You are a Bloomberg-terminal research analyst working iteratively
with a portfolio manager on a pick-and-shovel study. You already surfaced a set of
companies. The PM has now KEPT some and DROPPED others (sometimes with a reason).

Treat this as a strong, two-sided learning signal:
  • KEPT companies tell you what resonates — their style, size, sub-segment, business
    model, and how direct/indirect they are.
  • DROPPED companies (and any reasons) tell you what to steer away from.

Infer the PM's REVEALED PREFERENCE from the CONTRAST between the two sets, then
replace ONLY the dropped slots with fresh, better-targeted companies that fit what
the PM clearly wants.

HARD RULES:
1. NEVER recommend any ticker in the "DO NOT REPEAT" list (kept ∪ dropped ∪ anything
   already shown). Violating this disqualifies the response.
2. Return EXACTLY the requested number of new companies for EACH tier — no more, no less.
3. New companies must fit the SAME tier definition they are backfilling.
4. Apply what you learned: lean toward the qualities of the KEPT names and away from
   the qualities/reasons of the DROPPED names. Honor the USER RESEARCH BRIEF.
5. Be specific and evidence-oriented, exactly like the original study.
6. Output ONLY valid JSON — no prose, no markdown fences.
7. Ask a follow_up_question ONLY if the keep/drop pattern reveals a genuine, specific
   ambiguity that would change your picks; otherwise set it to null. Never ask generic
   investor-profiling questions."""

_REFINE_USER_TEMPLATE = """ORIGINAL THEME: {theme}
{brief_block}
COMPANIES THE PM KEPT (positive signal — emulate these qualities):
{kept_block}

COMPANIES THE PM DROPPED (negative signal — steer away from these qualities):
{dropped_block}

DO NOT REPEAT ANY OF THESE TICKERS:
{shown_block}

BACKFILL NEEDED (produce exactly this many NEW companies per tier):
{need_block}

Return JSON with EXACTLY this schema:
{{
  "learning_note": "1-2 sentences: what you inferred from the keep/drop pattern and how you adjusted.",
  "preferences_learned": [
    "Concise new preference bullet inferred from the contrast",
    "Another, if warranted"
  ],
  "new_companies": {{
{companies_schema}
  }},
  "follow_up_question": null
}}

Each company object uses this shape (include the tier-specific fields shown):
{{
  "ticker": "XXXX",
  "name": "Full legal company name",
  "exchange": "NASDAQ | NYSE | EURONEXT | TSE | LSE etc.",
  "thesis": "Sharp, specific mechanism of benefit.",
  "catalysts": ["specific cat1", "specific cat2"],
  "revenue_exposure": "~X% of revenue from [segment] or 'indirect via X'",
  "earnings_signal": "optional quote/data point, or omit",
  "risk": "specific downside risk"
  <TIER FIELDS>
}}

If you choose to ask one, follow_up_question uses this shape:
{{"id": "slug", "question": "thesis-specific question", "options": [{{"label": "A", "value": "A"}}, {{"label": "B", "value": "B"}}], "allow_multiple": false, "allow_custom": true}}"""


def _refine_company_lines(companies: list[dict], with_reason: bool = False) -> str:
    lines = []
    for c in companies:
        tier = c.get("tier") or c.get("source_tier") or "?"
        desc = (c.get("thesis") or c.get("supply_chain_role") or c.get("hidden_link") or "")[:110]
        base = f"  • [{tier}] {c.get('ticker', '?')} — {c.get('name', '')}: {desc}"
        if with_reason and c.get("reason"):
            base += f"  (reason dropped: {c['reason']})"
        lines.append(base)
    return "\n".join(lines) if lines else "  (none)"


async def refine_recommendations(
    brief: dict | None,
    kept: list[dict],
    dropped: list[dict],
    already_shown: list[str],
    openai_key: str,
    model: str = "gpt-4o",
) -> dict:
    """Learn from the PM's keep/drop pattern and backfill only the dropped slots.

    Parameters
    ----------
    brief:         Running research brief (interpretation + scope + learned prefs).
    kept:          Companies the PM did NOT drop — ``[{ticker,name,tier,thesis,...}]``.
    dropped:       Companies the PM dropped — ``[{ticker,name,tier,reason?}]``.
    already_shown: Every ticker shown so far (kept ∪ dropped ∪ deeper) to exclude.

    Returns ``{brief, learning_note, new_companies:{direct,enabler,deep}, follow_up_question}``.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    theme = (brief or {}).get("thesis") or (brief or {}).get("interpretation") or ""

    # How many to backfill per tier = number dropped in that tier.
    need: dict[str, int] = {"direct": 0, "enabler": 0, "deep": 0}
    for d in dropped:
        t = d.get("tier")
        if t in need:
            need[t] += 1
    active_tiers = [t for t, n in need.items() if n > 0]
    if not active_tiers:
        # Nothing was dropped from the base tiers — nothing to backfill.
        return {
            "brief": brief or {},
            "learning_note": "",
            "new_companies": {"direct": [], "enabler": [], "deep": []},
            "follow_up_question": None,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    need_block = "\n".join(
        f"  • {_TIER_LABELS[t]}: {need[t]} new compan{'y' if need[t] == 1 else 'ies'}"
        for t in active_tiers
    )
    companies_schema = ",\n".join(f'    "{t}": []' for t in active_tiers)
    # Build the per-tier field hint into the shared company shape note.
    tier_field_hint = "\n  ".join(
        f"# for {t} tier add: {_TIER_FIELDS[t]}" for t in active_tiers
    )

    shown_block = "\n".join(f"  • {t}" for t in sorted(set(already_shown))) or "  (none)"

    raw = await call_llm(
        api_key=openai_key,
        model=model,
        messages=[
            {"role": "system", "content": _REFINE_SYSTEM},
            {"role": "user", "content": _REFINE_USER_TEMPLATE.format(
                theme=theme,
                brief_block=_brief_to_block(brief),
                kept_block=_refine_company_lines(kept),
                dropped_block=_refine_company_lines(dropped, with_reason=True),
                shown_block=shown_block,
                need_block=need_block,
                companies_schema=companies_schema,
            ).replace("<TIER FIELDS>", ",\n  " + tier_field_hint)},
        ],
        max_tokens=3500,
        temperature=0.3,
    )

    try:
        analysis = _extract_json(raw)
    except Exception as exc:
        logger.error("JSON parse failed for refine output: %s\nRaw: %.500s", exc, raw)
        raise ValueError(f"LLM returned unparseable response: {exc}") from exc

    new_raw = analysis.get("new_companies") or {}
    shown_upper = {t.upper() for t in already_shown}

    # Flatten for batch enrichment + verification, tracking tier per company.
    flat: list[tuple[str, dict]] = []   # (tier, raw_item)
    for tier in ("direct", "enabler", "deep"):
        for item in (new_raw.get(tier) or []):
            tkr = (item.get("ticker") or "").strip().upper()
            if not tkr or tkr in shown_upper:
                continue
            flat.append((tier, item))

    all_tickers = list(dict.fromkeys(t.get("ticker", "").strip().upper() for _, t in flat if t.get("ticker")))

    financials, verify_map = await asyncio.gather(
        asyncio.to_thread(_fetch_financials_sync, all_tickers),
        _verify_companies([it for _, it in flat], theme, openai_key, model),
    )

    new_companies: dict[str, list[dict]] = {"direct": [], "enabler": [], "deep": []}
    for tier, item in flat:
        tkr = (item.get("ticker") or "").strip().upper()
        fin = financials.get(tkr) or {}
        card = _build_company_card(tkr, item, fin)
        new_companies[tier].append(card)

    # Verification (drop hard ticker mismatches, flag weak evidence) per tier.
    for tier in new_companies:
        new_companies[tier] = _apply_verification(new_companies[tier], verify_map, financials)
        # Enforce the requested count (trim any overflow the LLM produced).
        if need.get(tier):
            new_companies[tier] = new_companies[tier][: need[tier]]

    # Merge learned preferences into the brief (dedup, preserve order).
    learned_new = [p for p in (analysis.get("preferences_learned") or []) if isinstance(p, str) and p.strip()]
    out_brief = dict(brief or {})
    existing_prefs = list(out_brief.get("preferences_learned") or [])
    for p in learned_new:
        if p not in existing_prefs:
            existing_prefs.append(p)
    out_brief["preferences_learned"] = existing_prefs

    # Normalize an optional follow-up question.
    follow_up = None
    fq = analysis.get("follow_up_question")
    if isinstance(fq, dict) and fq.get("question"):
        opts = []
        for o in (fq.get("options") or []):
            if isinstance(o, dict) and o.get("label"):
                opts.append({"label": str(o["label"]), "value": str(o.get("value") or o["label"])})
            elif isinstance(o, str):
                opts.append({"label": o, "value": o})
        follow_up = {
            "id": str(fq.get("id") or "followup"),
            "question": str(fq["question"]),
            "options": opts,
            "allow_multiple": bool(fq.get("allow_multiple", False)),
            "allow_custom": bool(fq.get("allow_custom", True)),
        }

    return {
        "brief": out_brief,
        "learning_note": (analysis.get("learning_note") or "").strip(),
        "new_companies": new_companies,
        "follow_up_question": follow_up,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
