"""Pick & Shovel v2 — structured, evidence-grounded research pipeline.

Unlike v1 (one LLM riffing on its own output → semiconductor tunnel-vision), v2
walks a 7-step pipeline the user steers, grounded in real ETF holdings and real
primary documents (SEC EDGAR filings + company investor materials):

  1. decompose_theme / refine_components  — the theme's real building blocks
  2. discover_etfs / etf_holdings         — Top-3 thematic ETFs → deduped holdings
  3. ingest_user_inputs / enrich_companies — user text/links/images + own tickers
  4. match_components                      — components ⟷ companies
  5. deep_dive_company                     — filings + IR → clients/suppliers/alpha
  6. summarize_research                    — grouped, logical cards
  7. pick_shovel_from_research            — reuse v1 engine, seeded by the research
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import yfinance as yf

from .llm_service import call_llm
from .pick_shovel_service import (
    _extract_json, _fetch_financials_sync, _fmt_market_cap, analyze_pick_shovel,
    _verify_companies, _apply_verification, _build_company_card,
)
from .stock_service import fetch_fund_details
from . import edgar_service, doc_ingest_service, edinet_service, social_service

logger = logging.getLogger(__name__)


async def _call_json(system: str, user: str, openai_key: str, model: str,
                     max_tokens: int = 2500, temperature: float = 0.3) -> dict:
    """call_llm + tolerant JSON extraction.  Raises ValueError on unparseable output."""
    raw = await call_llm(
        api_key=openai_key, model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens, temperature=temperature,
    )
    try:
        return _extract_json(raw)
    except Exception as exc:
        logger.error("v2 JSON parse failed: %s\nRaw: %.500s", exc, raw)
        raise ValueError(f"LLM returned unparseable response: {exc}") from exc


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# §1 — Theme decomposition
# ===========================================================================

_DECOMPOSE_SYSTEM = """You are a Bloomberg-Intelligence research analyst breaking an
investment theme into the REAL building blocks required to make it happen — the
physical components, materials, equipment, software, infrastructure, and services.

CRITICAL ANTI-BIAS RULE: do NOT collapse everything into "chips/semiconductors".
A theme like "humanoid robotics" needs actuators, harmonic drives, precision
gearboxes, force/torque sensors, batteries & BMS, electric motors, machine-vision,
lightweight materials, simulation software, contract manufacturing, and safety
certification — semiconductors are only ONE slice. Map the FULL breadth across
mechanical, electrical, materials, power, sensing, software, manufacturing, and
services. Prefer specificity ("harmonic-drive reduction gears") over generic
labels ("hardware").

Output ONLY valid JSON — no prose, no markdown fences."""

_DECOMPOSE_USER = """THEME: {theme}
{context}
Break this theme into 10–16 concrete building blocks spanning the WHOLE value
chain (not just electronics). Group them by category.

Return JSON:
{{
  "theme_title": "Short title (<=8 words)",
  "summary": "2-3 sentence read of what the theme really requires end-to-end.",
  "components": [
    {{
      "id": "kebab-case-slug",
      "name": "Specific building block",
      "category": "Mechanical | Electrical & Power | Sensing | Materials | Compute & Semiconductors | Software & AI | Manufacturing & Equipment | Infrastructure | Services & Integration",
      "description": "1 sentence: what it is and its role in the theme.",
      "why_essential": "1 sentence: why the theme cannot happen without it."
    }}
  ]
}}"""


async def decompose_theme(theme: str, openai_key: str, model: str = "gpt-4o",
                          extra_context: str = "") -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    ctx = f"\nADDITIONAL CONTEXT FROM THE USER:\n{extra_context}\n" if extra_context.strip() else ""
    data = await _call_json(
        _DECOMPOSE_SYSTEM,
        _DECOMPOSE_USER.format(theme=theme.strip(), context=ctx),
        openai_key, model, max_tokens=2500,
    )
    return {
        "theme": theme.strip(),
        "theme_title": data.get("theme_title", theme.strip()),
        "summary": data.get("summary", ""),
        "components": _norm_components(data.get("components")),
        "generated_at": _now(),
    }


def _norm_components(raw) -> list[dict]:
    out = []
    for i, c in enumerate(raw or []):
        if not isinstance(c, dict) or not c.get("name"):
            continue
        out.append({
            "id": str(c.get("id") or f"c{i}"),
            "name": str(c["name"]),
            "category": str(c.get("category") or "Other"),
            "description": str(c.get("description") or ""),
            "why_essential": str(c.get("why_essential") or ""),
        })
    return out


_REFINE_COMPONENTS_SYSTEM = """You refine a theme's building-block list based on the
PM's feedback. Keep the user's curated list as the source of truth: respect their
edits/deletions, then ADD anything genuinely missing and improve specificity. Keep
the same broad coverage rule (never collapse into just semiconductors). Output ONLY
valid JSON."""

_REFINE_COMPONENTS_USER = """THEME: {theme}

CURRENT BUILDING BLOCKS (the PM has already curated these):
{components_block}

PM FEEDBACK / WHAT TO CHANGE:
{user_input}

Return the FULL updated list as JSON with the same schema:
{{"components": [{{"id": "...", "name": "...", "category": "...", "description": "...", "why_essential": "..."}}]}}"""


async def refine_components(theme: str, components: list[dict], user_input: str,
                            openai_key: str, model: str = "gpt-4o") -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    block = "\n".join(f"  • [{c.get('category','')}] {c.get('name','')}: {c.get('description','')}"
                      for c in components) or "  (none yet)"
    data = await _call_json(
        _REFINE_COMPONENTS_SYSTEM,
        _REFINE_COMPONENTS_USER.format(theme=theme.strip(), components_block=block,
                                       user_input=(user_input or "").strip() or "(no extra text — just improve coverage)"),
        openai_key, model, max_tokens=2500,
    )
    return {"components": _norm_components(data.get("components")), "generated_at": _now()}


# ---------------------------------------------------------------------------
# Per-component company discovery — build the universe bottom-up (no ETF needed)
# ---------------------------------------------------------------------------

_FIND_CO_SYSTEM = """You are an equity analyst building a stock universe for ONE specific slice of an
investment theme. Cutting-edge themes often have NO ETF yet (by the time an ETF exists the trade is
old), so we assemble companies bottom-up per component.

Find currently-listed PUBLIC companies whose business is SPECIFICALLY in this component's space.
Rules:
- Prefer PURE-PLAYS and specialists that actually do THIS — not mega-cap generalists (include a
  mega-cap only if it is a genuine leader in this exact niche).
- Real, currently-listed tickers only (exchange suffix for non-US, e.g. ASML, 6857.T, ROG.SW).
- Include relevant global names (US, Europe, Japan, Taiwan, Korea) — not just US.
- Exclude anything already in the provided list.
- 6–12 companies, most relevant first.
Output ONLY valid JSON — no prose, no markdown fences."""

_FIND_CO_USER = """THEME: {theme}
COMPONENT (the ONLY focus area): {component}
COMPONENT DESCRIPTION: {description}

ALREADY HAVE (do NOT repeat these tickers): {existing}

Return JSON:
{{"companies": [
  {{"ticker": "XXXX", "name": "Full company name", "exchange": "NASDAQ | NYSE | EURONEXT | TSE | ...",
    "why": "1 line: how this company plays specifically in THIS component's space"}}
]}}"""


async def find_component_companies(theme: str, component: str, description: str,
                                   existing: list[str], openai_key: str, model: str = "gpt-4o") -> dict:
    """Find listed public companies specific to ONE component's space, enriched with financials."""
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    existing_upper = {str(t).upper() for t in (existing or [])}
    data = await _call_json(
        _FIND_CO_SYSTEM,
        _FIND_CO_USER.format(theme=theme.strip(), component=component,
                             description=description or "(no description)",
                             existing=", ".join(sorted(existing_upper)) or "(none)"),
        openai_key, model, max_tokens=1800,
    )
    raw = [c for c in (data.get("companies") or [])
           if isinstance(c, dict) and c.get("ticker")
           and str(c["ticker"]).upper() not in existing_upper]
    tickers = list(dict.fromkeys(str(c["ticker"]).strip().upper() for c in raw))
    fins = await asyncio.to_thread(_fetch_financials_sync, tickers) if tickers else {}

    out = []
    seen: set[str] = set()
    for c in raw:
        t = str(c["ticker"]).strip().upper()
        if not t or t in seen:
            continue
        seen.add(t)
        f = fins.get(t) or {}
        out.append({
            "ticker": t,
            "name": c.get("name") or f.get("short_name") or t,
            "exchange": c.get("exchange"),
            "sector": f.get("sector"),
            "price": f.get("price"),
            "price_chg_1d": f.get("price_chg_1d"),
            "pe_ratio": f.get("pe_ratio"),
            "forward_pe": f.get("forward_pe"),
            "week52_high": f.get("week52_high"),
            "week52_low": f.get("week52_low"),
            "market_cap": _fmt_market_cap(f.get("market_cap")),
            "why": c.get("why", ""),
            "source": "component",
        })
    return {"companies": out, "generated_at": _now()}


# ===========================================================================
# §2 — ETF discovery + holdings
# ===========================================================================

_ETF_SYSTEM = """You are an ETF specialist. Given an investment theme, name the 3
most relevant, liquid, currently-listed thematic ETFs whose holdings best capture
the theme's value chain. Prefer real thematic/industry ETFs over broad-market
funds. Output ONLY valid JSON."""

_ETF_USER = """THEME: {theme}

KEY BUILDING BLOCKS:
{components_block}

Return JSON:
{{"etfs": [{{"ticker": "XXXX", "name": "Full ETF name", "rationale": "1 sentence why this ETF fits the theme"}}]}}
Exactly 3 ETFs, most relevant first."""


async def _holdings_for(ticker: str) -> list[dict]:
    try:
        details = await fetch_fund_details(ticker)
        return details.get("top_holdings") or []
    except Exception as exc:
        logger.info("ETF holdings fetch failed for %s: %s", ticker, exc)
        return []


def _dedupe_holdings(per_etf: dict[str, list[dict]]) -> list[dict]:
    """Merge holdings across ETFs.

    Each holding records its weight in every ETF whose top-10 lists it
    (``etf_weights``); the headline ``weight`` is the AVERAGE across those ETFs
    (sum of weights / number of ETFs holding it). Appearing in multiple ETFs is a
    strong signal — surfaced via ``in_etfs``. Sorted by the average weight desc.
    """
    merged: dict[str, dict] = {}
    for etf, holdings in per_etf.items():
        for h in holdings:
            tkr = str(h.get("ticker", "")).upper().strip()
            if not tkr or tkr in ("", "N/A"):
                continue
            entry = merged.setdefault(tkr, {"ticker": tkr, "name": h.get("name", tkr),
                                            "etf_weights": {}})
            entry["etf_weights"][etf] = h.get("weight_pct")
            if (not entry.get("name") or entry["name"] == tkr) and h.get("name"):
                entry["name"] = h["name"]

    out: list[dict] = []
    for e in merged.values():
        weights = [w for w in e["etf_weights"].values() if w is not None]
        avg = round(sum(weights) / len(weights), 2) if weights else None
        out.append({
            "ticker": e["ticker"],
            "name": e["name"],
            "etf_weights": e["etf_weights"],
            "in_etfs": list(e["etf_weights"].keys()),
            "weight": avg,
        })
    # Breadth first (held by more ETFs = stronger conviction), then avg weight —
    # so one ETF's single concentrated position can't dominate the top.
    return sorted(out, key=lambda e: (-len(e["in_etfs"]), -(e["weight"] or 0)))


async def discover_etfs(theme: str, components: list[dict], openai_key: str,
                        model: str = "gpt-4o") -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    block = "\n".join(f"  • {c.get('name','')}" for c in (components or [])[:16]) or "  (n/a)"
    data = await _call_json(
        _ETF_SYSTEM,
        _ETF_USER.format(theme=theme.strip(), components_block=block),
        openai_key, model, max_tokens=700,
    )
    etfs_raw = [e for e in (data.get("etfs") or []) if isinstance(e, dict) and e.get("ticker")][:3]

    per_etf: dict[str, list[dict]] = {}
    holdings_lists = await asyncio.gather(*[_holdings_for(e["ticker"].upper()) for e in etfs_raw])
    etfs_out = []
    for e, holdings in zip(etfs_raw, holdings_lists):
        tkr = e["ticker"].upper()
        per_etf[tkr] = holdings
        etfs_out.append({"ticker": tkr, "name": e.get("name", tkr),
                         "rationale": e.get("rationale", ""), "holding_count": len(holdings)})

    return {
        "theme": theme.strip(),
        "etfs": etfs_out,
        "holdings": _dedupe_holdings(per_etf),
        "generated_at": _now(),
    }


async def etf_holdings(ticker: str) -> dict:
    """Top holdings for a single user-added ETF."""
    ticker = (ticker or "").strip().upper()
    holdings = await _holdings_for(ticker)
    name = ticker
    try:
        details = await fetch_fund_details(ticker)
        name = details.get("company_name") or ticker
    except Exception:
        pass
    return {"ticker": ticker, "name": name, "holdings": holdings, "generated_at": _now()}


# ===========================================================================
# §3 — User inputs (text / links / images) + ticker enrichment
# ===========================================================================

def _company_website(ticker: str) -> str | None:
    try:
        info = yf.Ticker(ticker).info or {}
        return info.get("website") or info.get("irWebsite")
    except Exception:
        return None


def _enrich_sync(tickers: list[str]) -> list[dict]:
    fins = _fetch_financials_sync(tickers)
    out = []
    for t in tickers:
        f = fins.get(t) or {}
        out.append({
            "ticker": t,
            "name": f.get("short_name") or t,
            "sector": f.get("sector"),
            "price": f.get("price"),
            "price_chg_1d": f.get("price_chg_1d"),
            "pe_ratio": f.get("pe_ratio"),
            "forward_pe": f.get("forward_pe"),
            "week52_high": f.get("week52_high"),
            "week52_low": f.get("week52_low"),
            "market_cap": _fmt_market_cap(f.get("market_cap")),
        })
    return out


async def enrich_companies(tickers: list[str]) -> dict:
    clean = list(dict.fromkeys((t or "").strip().upper() for t in tickers if t and t.strip()))
    if not clean:
        return {"companies": [], "generated_at": _now()}
    companies = await asyncio.to_thread(_enrich_sync, clean)
    return {"companies": companies, "generated_at": _now()}


_INGEST_SYSTEM = """You extract investment-research signal from materials the PM
supplied (their notes, web pages, presentation text, image descriptions). From this
plus the theme, surface (a) specific listed companies worth adding, and (b) any
building blocks the theme map is missing. Be concrete; only suggest tickers you are
confident are real and listed. Output ONLY valid JSON."""

_INGEST_USER = """THEME: {theme}

MATERIALS THE PM PROVIDED:
{materials}

Return JSON:
{{
  "suggested_companies": [{{"ticker": "XXXX", "name": "Company", "why": "why relevant from the material"}}],
  "suggested_components": [{{"name": "building block", "category": "...", "description": "..."}}],
  "notes": "1-2 sentence summary of what these materials add."
}}"""


async def ingest_user_inputs(db, theme: str, inputs: list[dict], openai_key: str,
                             model: str = "gpt-4o") -> dict:
    """inputs: ``[{type: 'text'|'link'|'image', value}]`` → suggestions to fold in."""
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    materials: list[str] = []
    sources: list[dict] = []
    for inp in inputs or []:
        kind = inp.get("type")
        val = inp.get("value", "")
        if kind == "text" and val.strip():
            materials.append(f"[NOTE] {val.strip()[:4000]}")
            sources.append({"type": "text", "ok": True, "label": val.strip()[:60]})
        elif kind == "link" and val.strip():
            doc = await doc_ingest_service.fetch_url_text(db, val.strip())
            if doc["ok"]:
                materials.append(f"[LINK {doc.get('title') or val}] {doc['text'][:5000]}")
            sources.append({"type": "link", "ok": doc["ok"], "label": doc.get("title") or val.strip(), "url": val.strip()})
        elif kind == "image" and val.strip():
            desc = await doc_ingest_service.describe_image(
                val, f"Describe this image for research on the theme '{theme}'. "
                     f"Note any companies, products, charts, or supply-chain details.",
                openai_key, model)
            if desc:
                materials.append(f"[IMAGE] {desc[:3000]}")
            sources.append({"type": "image", "ok": bool(desc), "label": "uploaded image"})

    if not materials:
        return {"suggested_companies": [], "suggested_components": [], "notes": "",
                "sources": sources, "generated_at": _now()}

    data = await _call_json(
        _INGEST_SYSTEM,
        _INGEST_USER.format(theme=theme.strip(), materials="\n\n".join(materials)[:18000]),
        openai_key, model, max_tokens=1500,
    )
    return {
        "suggested_companies": [c for c in (data.get("suggested_companies") or []) if isinstance(c, dict) and c.get("ticker")],
        "suggested_components": _norm_components(data.get("suggested_components")),
        "notes": data.get("notes", ""),
        "sources": sources,
        "generated_at": _now(),
    }


# ===========================================================================
# §4 — Component ⟷ company matching
# ===========================================================================

_MATCH_SYSTEM = """You assign companies to a theme's building blocks (components).

EVERY company in the list MUST be placed in exactly ONE component — its single
nearest/best fit. Rules:
- STRONGLY prefer the EXISTING building blocks. Pick the closest one even if the fit
  isn't perfect — most companies should land in an existing block.
- Only create a NEW component when a company genuinely fits NONE of the existing
  blocks, and keep new components to an absolute minimum (reuse existing wherever
  reasonable; never invent near-duplicates of an existing block).
- Use each company's DEEP-DIVE PROFILE (what it does, clients, suppliers) to place it
  accurately rather than guessing from the name.
- NEVER leave a company unplaced. Every provided ticker appears under exactly one component.
Reuse the given component id for existing blocks; for a new block use a short kebab-case id.
Output ONLY valid JSON."""

_MATCH_USER = """THEME: {theme}

EXISTING BUILDING BLOCKS (prefer these — reuse the id):
{components_block}

COMPANIES (place EVERY one of these tickers in exactly one component):
{companies_block}

DEEP-DIVE PROFILES (evidence from filings — use to place companies accurately):
{deepdive_block}

Return JSON:
{{
  "matches": [
    {{"component_id": "existing-id-or-new-slug", "component_name": "...",
      "companies": [{{"ticker": "XXXX", "how": "how this company serves this block"}}]}}
  ],
  "new_components": [{{"id": "new-slug", "name": "...", "category": "..."}}],
  "unmatched_components": ["names of EXISTING blocks that ended up with no company — a gap = opportunity"]
}}"""


async def match_components(theme: str, components: list[dict], companies: list[dict],
                           openai_key: str, model: str = "gpt-4o",
                           deep_dives: list[dict] | None = None) -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    comp_block = "\n".join(f"  • [{c.get('id')}] {c.get('name')}" for c in components) or "  (none)"
    co_block = "\n".join(f"  • {c.get('ticker')} — {c.get('name','')}" for c in companies) or "  (none)"
    dd_lines = []
    for d in (deep_dives or []):
        clients = ", ".join(c.get("name", "") for c in (d.get("clients") or [])[:5])
        suppliers = ", ".join(s.get("name", "") for s in (d.get("suppliers") or [])[:5])
        dd_lines.append(f"  • {d.get('ticker','')}: {(d.get('what_it_does') or '')[:160]}"
                        f" | clients: {clients or 'n/a'} | suppliers: {suppliers or 'n/a'}")
    dd_block = "\n".join(dd_lines)[:8000] or "  (no deep dives yet)"
    data = await _call_json(
        _MATCH_SYSTEM,
        _MATCH_USER.format(theme=theme.strip(), components_block=comp_block,
                           companies_block=co_block, deepdive_block=dd_block),
        openai_key, model, max_tokens=2500,
    )

    matches = [m for m in (data.get("matches") or []) if isinstance(m, dict)]
    provided_ids = {str(c.get("id")) for c in (components or [])}
    provided_names = {str(c.get("name", "")).lower() for c in (components or [])}

    # Safety net: guarantee EVERY company is placed — straggler the LLM missed goes
    # into a single "Other relevant companies" bucket (review prompt).
    assigned = {str(x.get("ticker", "")).upper() for m in matches for x in (m.get("companies") or [])}
    orphans = [c for c in companies if str(c.get("ticker", "")).upper() not in assigned]
    if orphans:
        matches.append({
            "component_id": "other-relevant",
            "component_name": "Other relevant companies",
            "companies": [{"ticker": c.get("ticker"), "how": "No close component yet — review / reassign."} for c in orphans],
        })

    # Derive the components that don't already exist (new + the safety bucket) so the
    # UI can render them as sections and persist them.
    new_components: list[dict] = []
    seen: set[str] = set()
    for m in matches:
        cid = str(m.get("component_id", ""))
        cname = str(m.get("component_name", "") or cid)
        if cid and cid not in provided_ids and cname.lower() not in provided_names and cid not in seen:
            seen.add(cid)
            new_components.append({"id": cid, "name": cname, "category": "Discovered"})

    return {
        "matches": matches,
        "new_components": new_components,
        "unmatched_components": data.get("unmatched_components") or [],
        "generated_at": _now(),
    }


# ===========================================================================
# §5 — Per-company deep dive (EDGAR + IR)  — the alpha
# ===========================================================================

_DEEPDIVE_SYSTEM = """You are an elite buy-side analyst extracting supply-chain
intelligence from a company's PRIMARY documents (SEC filings and investor
materials). Ground every claim in the provided document text. For clients and
suppliers, cite the document evidence. Then — the alpha — infer NON-OBVIOUS
businesses that already help this company or will need to as it grows (new
suppliers it will pull on, adjacent services, second-order beneficiaries). Avoid
obvious names everyone knows; find what the general public hasn't connected yet.

If NO document text is provided, say so: set documents_found=false, base your
answer on general knowledge, and keep it appropriately hedged.

You are ALSO given recent SOCIAL CHATTER (StockTwits). Treat it as noisy retail
sentiment and a source of LEADS (names/themes to investigate next) — never as
established fact. Summarize it separately in the "social" field.

Output ONLY valid JSON — no prose, no markdown fences."""

_DEEPDIVE_USER = """COMPANY: {name} ({ticker})
THEME CONTEXT: {theme}

PRIMARY DOCUMENT EXCERPTS:
{documents}

SOCIAL CHATTER (recent StockTwits — noisy retail; use only as leads):
{social}

Return JSON:
{{
  "what_it_does": "2-3 sentence plain description of the business.",
  "segments": ["key business segment 1", "segment 2"],
  "clients": [{{"name": "Customer", "ticker": "XXXX or null", "evidence": "where in the docs / how known"}}],
  "suppliers": [{{"name": "Supplier", "ticker": "XXXX or null", "evidence": "where in the docs / how known"}}],
  "hidden_opportunities": [
    {{"insight": "the non-obvious connection / who will need them next",
      "beneficiary": "company type or named ticker that benefits",
      "why_nonobvious": "why the market hasn't priced/noticed this"}}
  ],
  "social": {{
    "summary": "1-2 sentences on what social media is currently saying (or 'no notable chatter').",
    "sentiment": "bullish | bearish | mixed | quiet",
    "themes": ["recurring topic 1", "topic 2"],
    "mentioned": [{{"name": "related company/product social connects to it", "ticker": "XXXX or null"}}]
  }},
  "documents_found": {documents_found}
}}"""


async def deep_dive_company(db, ticker: str, name: str, theme: str, openai_key: str,
                            model: str = "gpt-4o", website: str | None = None) -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    ticker = (ticker or "").strip().upper()

    # Gather primary documents: EDGAR filings + (best-effort) IR presentations.
    # Sequential on purpose — both share the request's DB session via the cache,
    # and one AsyncSession can't be driven by concurrent coroutines.
    if website is None:
        website = await asyncio.to_thread(_company_website, ticker)

    # Route by listing: Japanese tickers (e.g. 7203 / 7203.T) → EDINET when a key
    # is configured; everything else → EDGAR (10-K and now 20-F/6-K for ADRs).
    sec_code = edinet_service.to_sec_code(ticker)
    if sec_code and edinet_service.is_enabled():
        bundle = await edinet_service.gather_company_filings(db, ticker)
        if not bundle.get("documents_found"):
            bundle = await edgar_service.gather_company_filings(db, ticker)
    else:
        bundle = await edgar_service.gather_company_filings(db, ticker)

    ir_docs = await doc_ingest_service.discover_ir_documents(db, website) if website else []

    sources: list[dict] = []
    doc_blocks: list[str] = []
    for d in bundle.get("docs", []):
        sources.append({"type": d["form"], "date": d.get("date", ""), "url": d["url"]})
        doc_blocks.append(f"--- SEC {d['form']} ({d.get('date','')}) ---\n{d['excerpt']}")
    for d in (ir_docs or []):
        sources.append({"type": "Investor material", "date": "", "url": d["url"]})
        doc_blocks.append(f"--- INVESTOR MATERIAL ({d.get('title','')}) ---\n{d['text']}")

    documents_found = bool(doc_blocks)
    documents_text = "\n\n".join(doc_blocks)[:30000] if documents_found else "(No primary documents were available for this company.)"

    # Social chatter (best-effort, free) — leads only, not fact.
    social = await social_service.stocktwits_messages(db, ticker)
    if social.get("ok"):
        sources.append({"type": "StockTwits", "date": "", "url": social["url"]})
        social_text = "\n".join(f"  • {m}" for m in social["messages"])[:6000]
    else:
        social_text = "(no notable social chatter found)"

    data = await _call_json(
        _DEEPDIVE_SYSTEM,
        _DEEPDIVE_USER.format(name=name or ticker, ticker=ticker, theme=theme,
                              documents=documents_text, social=social_text,
                              documents_found="true" if documents_found else "false"),
        openai_key, model, max_tokens=2600,
    )

    social_out = data.get("social") or {}
    return {
        "ticker": ticker,
        "name": name or ticker,
        "what_it_does": data.get("what_it_does", ""),
        "segments": data.get("segments") or [],
        "clients": data.get("clients") or [],
        "suppliers": data.get("suppliers") or [],
        "hidden_opportunities": data.get("hidden_opportunities") or [],
        "social": {
            "summary": social_out.get("summary", ""),
            "sentiment": social_out.get("sentiment", ""),
            "themes": social_out.get("themes") or [],
            "mentioned": social_out.get("mentioned") or [],
            "found": bool(social.get("ok")),
            "url": social.get("url", ""),
        },
        "sources": sources,
        "documents_found": documents_found,
        "model_only": not documents_found,
        "generated_at": _now(),
    }


# ===========================================================================
# §6 — Summarize the research
# ===========================================================================

_SUMMARY_SYSTEM = """You synthesize a multi-company research dossier into a clean,
logically-grouped briefing AND act as a critical second pair of eyes.

Two jobs:
1. SYNTHESIZE the companies the analyst actually deep-dived: cluster the core
   companies, the client/supplier web connecting them, and the non-obvious
   opportunities (the alpha) into emergent sub-themes.
2. CATCH BLIND SPOTS. You are also given the full theme building-blocks, the ETF
   holdings, and the list of companies the analyst did NOT deep-dive. Cross-check:
   which important holdings were never examined? which building blocks have no
   company coverage? what likely second-order beneficiary is missing entirely?
   Be specific and honest — the value is telling the analyst what they MIGHT HAVE
   MISSED, not just confirming what they found.

Output ONLY valid JSON — no prose, no markdown fences."""

_SUMMARY_USER = """THEME: {theme}

A) COMPANIES DEEP-DIVED (what it does, clients, suppliers, hidden opportunities):
{dossier}

B) FULL THEME BUILDING BLOCKS:
{components}

C) ETF HOLDINGS IN PLAY:
{holdings}

D) COMPANIES NOT YET DEEP-DIVED (candidate blind spots):
{undived}

E) LEADS THE ANALYST FLAGGED AS HIGH-PRIORITY (weight these heavily, build on them):
{emphasis}

Return JSON:
{{
  "headline": "1 sentence takeaway of the whole study.",
  "groups": [
    {{"title": "Group label", "kind": "core | suppliers | clients | opportunity",
      "summary": "1 sentence", "items": [{{"label": "ticker or name", "note": "why it's here"}}]}}
  ],
  "alpha": [{{"insight": "non-obvious opportunity / sub-theme", "beneficiaries": ["ticker or type"], "why": "why overlooked"}}],
  "gaps": ["Specific blind spot — an important holding/component/second-order name not examined, and why it matters"],
  "recommended_deep_dives": [{{"ticker": "XXXX", "name": "Company", "why": "why it deserves a deep dive next"}}]
}}"""


async def summarize_research(theme: str, deep_dives: list[dict], openai_key: str,
                             model: str = "gpt-4o", components: list[dict] | None = None,
                             holdings: list[dict] | None = None,
                             companies: list[dict] | None = None,
                             emphasis: list[str] | None = None) -> dict:
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")
    lines = []
    for d in deep_dives:
        clients = ", ".join(c.get("name", "") for c in (d.get("clients") or [])[:6])
        suppliers = ", ".join(s.get("name", "") for s in (d.get("suppliers") or [])[:6])
        opps = "; ".join(o.get("insight", "") for o in (d.get("hidden_opportunities") or [])[:4])
        lines.append(
            f"• {d.get('ticker')} — {d.get('name','')}: {d.get('what_it_does','')[:200]}\n"
            f"    clients: {clients or 'n/a'}\n    suppliers: {suppliers or 'n/a'}\n    alpha: {opps or 'n/a'}"
        )

    comp_block = "\n".join(f"  • [{c.get('category','')}] {c.get('name','')}" for c in (components or [])) or "  (n/a)"
    hold_block = "\n".join(f"  • {h.get('ticker','')} — {h.get('name','')}" for h in (holdings or [])) or "  (n/a)"
    dived = {str(d.get("ticker", "")).upper() for d in deep_dives}
    undived = [c for c in (companies or []) if str(c.get("ticker", "")).upper() not in dived]
    undived_block = "\n".join(f"  • {c.get('ticker','')} — {c.get('name','')}" for c in undived) or "  (all selected companies were deep-dived)"

    emph = [e for e in (emphasis or []) if e and e.strip()]
    emph_block = "\n".join(f"  • {e}" for e in emph) or "  (none flagged)"

    data = await _call_json(
        _SUMMARY_SYSTEM,
        _SUMMARY_USER.format(
            theme=theme.strip(),
            dossier="\n".join(lines)[:16000] or "  (none deep-dived yet)",
            components=comp_block[:4000],
            holdings=hold_block[:4000],
            undived=undived_block[:4000],
            emphasis=emph_block[:3000],
        ),
        openai_key, model, max_tokens=2800,
    )
    # Never re-recommend a company that's already been deep-dived.
    dived_tk = {str(d.get("ticker", "")).upper() for d in deep_dives}
    recommended = [r for r in (data.get("recommended_deep_dives") or [])
                   if isinstance(r, dict) and str(r.get("ticker", "")).upper() not in dived_tk]
    return {
        "headline": data.get("headline", ""),
        "groups": data.get("groups") or [],
        "alpha": data.get("alpha") or [],
        "gaps": data.get("gaps") or [],
        "recommended_deep_dives": recommended,
        "generated_at": _now(),
    }


# ===========================================================================
# §7 — Pick & Shovel synthesized from ALL the collected research
# ===========================================================================

_SYNTH_SYSTEM = """You are a Bloomberg-Intelligence analyst assembling the final
pick-and-shovel list for an investment theme — and you must GROUND every pick in the
research dossier you are given, not brainstorm from scratch.

YOUR PRIMARY ENGINE: traverse the document-derived supply-chain graph. Each company
the analyst deep-dived has clients, suppliers, social mentions, and hidden-alpha leads
that were extracted from REAL primary documents (SEC 10-K/20-F, Japan EDINET filings,
investor presentations, StockTwits). Mine those relationships to source actual listed
companies: a supplier named in a core company's 10-K is a Backbone pick; an alpha lead
is a Hidden pick. Also use the theme's building blocks, the ETF holdings (and how many
ETFs hold each), the user's own companies, the component↔company matches and uncovered
gaps, and the user's EMPHASIZED leads (weight these the most).

Assign each company to exactly one tier:
  • CORE PLAYS — companies central to the theme, validated by the research (deep-dived,
    matched to components, and/or held across multiple ETFs).
  • THE BACKBONE — the suppliers / equipment / materials / enablers the core depends on,
    sourced from the deep-dives' supplier relationships and the component map. This is the
    heart of "picks & shovels".
  • HIDDEN PICKS — non-obvious 2nd/3rd-order beneficiaries from the hidden-alpha, emphasized
    leads, and uncovered component gaps.

For EVERY company, write a one-line `provenance` citing the concrete evidence in the
dossier (e.g. "supplier to NVDA per its FY25 10-K", "held by 3 of 4 ETFs", "your
emphasized lead", "fills the harmonic-drive gap no holding covered"). Do NOT invent
provenance — if a name isn't traceable to the dossier, don't include it.

RULES: real, currently-listed tickers (exchange suffix for non-US). 4–6 per tier. No
overlap between tiers. Output ONLY valid JSON — no prose, no markdown fences."""

_SYNTH_USER = """THEME: {theme}

=== RESEARCH DOSSIER (everything collected in steps 1–6) ===
{dossier}
=== END DOSSIER ===

Return JSON with EXACTLY this schema:
{{
  "theme_title": "Short title (<=8 words)",
  "theme_summary": "3-4 sentences tying the picks back to the research.",
  "supply_chain_map": "2-3 sentences on the end-to-end chain this list covers.",
  "selection_notes": "2-3 sentences: HOW you chose Core vs Backbone vs Hidden from the dossier.",
  "direct_plays": [
    {{"ticker":"X","name":"","exchange":"","thesis":"specific benefit mechanism","provenance":"evidence in dossier","catalysts":["c1","c2"],"revenue_exposure":"","risk":"","why_this_not_another":""}}
  ],
  "enablers": [
    {{"ticker":"X","name":"","exchange":"","thesis":"","provenance":"evidence in dossier","catalysts":["c1","c2"],"revenue_exposure":"","risk":"","supply_chain_role":"exact role","why_overlooked":""}}
  ],
  "deep_picks": [
    {{"ticker":"X","name":"","exchange":"","thesis":"","provenance":"evidence in dossier","catalysts":["c1"],"revenue_exposure":"","risk":"","hidden_link":"non-obvious connection","discovery_insight":""}}
  ]
}}"""


def _synth_dossier(components, holdings, companies, matches, deep_dives, summary, emphasis) -> str:
    parts: list[str] = []
    if components:
        parts.append("BUILDING BLOCKS:\n" + "\n".join(
            f"  • [{c.get('category','')}] {c.get('name','')}" for c in components[:20]))
    if holdings:
        parts.append("ETF HOLDINGS (ticker — name — avg wt% — # ETFs):\n" + "\n".join(
            f"  • {h.get('ticker','')} — {h.get('name','')} — {h.get('weight','?')}% — {len(h.get('in_etfs') or [])} ETFs"
            for h in holdings[:40]))
    if companies:
        parts.append("USER-ADDED / SELECTED COMPANIES:\n" + "\n".join(
            f"  • {c.get('ticker','')} — {c.get('name','')}" for c in companies[:40]))
    if matches:
        mlines = []
        for m in matches[:25]:
            cos = ", ".join(f"{x.get('ticker','')}" for x in (m.get('companies') or []))
            mlines.append(f"  • {m.get('component_name','')}: {cos or '(GAP — no coverage)'}")
        parts.append("COMPONENT ↔ COMPANY MATCHES:\n" + "\n".join(mlines))
    if deep_dives:
        for d in deep_dives[:15]:
            clients = ", ".join(f"{c.get('name','')}{'('+c['ticker']+')' if c.get('ticker') else ''}" for c in (d.get('clients') or [])[:8])
            suppliers = ", ".join(f"{s.get('name','')}{'('+s['ticker']+')' if s.get('ticker') else ''}" for s in (d.get('suppliers') or [])[:8])
            alpha = "; ".join(f"{o.get('beneficiary','')}: {o.get('insight','')}" for o in (d.get('hidden_opportunities') or [])[:4])
            soc = (d.get('social') or {}).get('summary', '')
            parts.append(
                f"DEEP DIVE — {d.get('ticker','')} {d.get('name','')} (docs_found={d.get('documents_found')}):\n"
                f"  what: {(d.get('what_it_does') or '')[:200]}\n"
                f"  clients: {clients or 'n/a'}\n  suppliers: {suppliers or 'n/a'}\n"
                f"  social: {soc or 'n/a'}\n  hidden alpha: {alpha or 'n/a'}")
    if summary:
        s = f"SUMMARY: {summary.get('headline','')}"
        gaps = summary.get('gaps') or []
        if gaps:
            s += "\n  gaps: " + "; ".join(gaps[:6])
        parts.append(s)
    emph = [e for e in (emphasis or []) if e and e.strip()]
    if emph:
        parts.append("⭐ USER-EMPHASIZED LEADS (weight most heavily):\n" + "\n".join(f"  • {e}" for e in emph))
    return "\n\n".join(parts)[:24000]


async def synthesize_pick_shovel(theme: str, openai_key: str, model: str = "gpt-4o",
                                 components: list[dict] | None = None,
                                 holdings: list[dict] | None = None,
                                 companies: list[dict] | None = None,
                                 matches: list[dict] | None = None,
                                 deep_dives: list[dict] | None = None,
                                 summary: dict | None = None,
                                 emphasis: list[str] | None = None) -> dict:
    """Assemble the final pick-and-shovel tiers grounded in ALL collected research.

    Same payload shape as ``analyze_pick_shovel`` (so the recursive/Track/Deep-Dive UI
    keeps working) plus a ``provenance`` per card and ``selection_notes``.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    dossier = _synth_dossier(components or [], holdings or [], companies or [],
                             matches or [], deep_dives or [], summary or {}, emphasis or [])

    analysis = await _call_json(
        _SYNTH_SYSTEM,
        _SYNTH_USER.format(theme=theme.strip(), dossier=dossier or "(no research collected)"),
        openai_key, model, max_tokens=4000, temperature=0.2,
    )

    direct_raw  = analysis.get("direct_plays") or []
    enabler_raw = analysis.get("enablers") or []
    deep_raw    = analysis.get("deep_picks") or []
    all_raw = direct_raw + enabler_raw + deep_raw
    all_tickers = list(dict.fromkeys(
        (c.get("ticker") or "").strip().upper() for c in all_raw if c.get("ticker")))

    financials, verify_map = await asyncio.gather(
        asyncio.to_thread(_fetch_financials_sync, all_tickers),
        _verify_companies(all_raw, theme.strip(), openai_key, model),
    )

    def _build_tier(raw_list: list[dict]) -> list[dict]:
        cards = []
        for item in raw_list:
            tkr = (item.get("ticker") or "").strip().upper()
            if not tkr:
                continue
            card = _build_company_card(tkr, item, financials.get(tkr) or {})
            card["provenance"] = item.get("provenance")
            cards.append(card)
        return _apply_verification(cards, verify_map, financials)

    return {
        "theme":            theme.strip(),
        "theme_title":      analysis.get("theme_title", theme.strip()),
        "theme_summary":    analysis.get("theme_summary", ""),
        "supply_chain_map": analysis.get("supply_chain_map", ""),
        "selection_notes":  analysis.get("selection_notes", ""),
        "key_trends":       [],
        "direct_plays":     _build_tier(direct_raw),
        "enablers":         _build_tier(enabler_raw),
        "deep_picks":       _build_tier(deep_raw),
        "seeded_from_research": True,
        "generated_at":     _now(),
    }


# ===========================================================================
# Component-level picks — focused ONLY on a component's matched companies
# ===========================================================================

_COMPONENT_PICKS_SYSTEM = """You find the SUPPLIERS (and supply-chain input providers) of a SPECIFIC,
small set of anchor companies — companies that supply / sell to / provide raw materials, components,
equipment, tooling, or critical inputs that the anchors need to make what they make.

Return ONE FLAT LIST (no tiers, no theme buckets). Each supplier is one of two kinds:
  • "documented" — a supplier/vendor EXPLICITLY named in the anchors' deep-dive documents
    (10-K / 20-F / EDINET / earnings / investor materials). Cite where in `provenance`.
  • "inferred"   — a likely/possible supplier based on WHAT the anchors make and what their supply
    chain, raw materials, and business therefore REQUIRE (even if not named). Put the reasoning in
    `provenance` (e.g. "makes harmonic drives → needs precision bearings & specialty steel").

STAY STRICTLY on the anchors' upstream supply chain. Do NOT list the anchor companies themselves,
and do NOT broaden to the whole theme. Prefer real, currently-listed tickers (exchange suffix for
non-US); if a critical supplier is private/unlisted, you may include it with ticker null and say so.
6–12 suppliers total. Output ONLY valid JSON — no prose, no markdown fences."""

_COMPONENT_PICKS_USER = """THEME: {theme}
COMPONENT (the only focus area): {component}

ANCHOR COMPANIES — find THEIR suppliers (do NOT repeat these):
{anchors}

DEEP-DIVE PROFILES OF THE ANCHORS (documented suppliers / clients / what-they-make — your primary source):
{deepdives}

USER-EMPHASIZED LEADS (weight heavily):
{emphasis}

Return JSON with EXACTLY this schema:
{{
  "title": "Short label, e.g. 'Suppliers behind <anchors>'",
  "summary": "2-3 sentences on the upstream supply chain feeding these anchors.",
  "selection_notes": "1-2 sentences: how you separated documented vs inferred.",
  "suppliers": [
    {{
      "ticker": "XXXX or null",
      "name": "Full company name",
      "exchange": "NASDAQ | NYSE | EURONEXT | TSE etc.",
      "supply_kind": "documented | inferred",
      "thesis": "What input/component/material they supply to the anchor(s) and why they benefit.",
      "provenance": "Documented: which anchor + which doc names them. Inferred: the supply-chain reasoning.",
      "catalysts": ["specific catalyst"],
      "revenue_exposure": "approx % or 'indirect via X'",
      "risk": "specific downside"
    }}
  ]
}}"""


async def component_picks(theme: str, component: str, anchors: list[dict],
                          deep_dives: list[dict], openai_key: str, model: str = "gpt-4o",
                          emphasis: list[str] | None = None) -> dict:
    """Find the SUPPLIERS of ONE component's matched anchor companies — a flat list of
    documented (named in filings) and inferred (supply-chain reasoning) suppliers.

    Returns ``{theme, title, summary, selection_notes, suppliers:[card+supply_kind+provenance]}``.
    """
    if not openai_key:
        raise ValueError("OpenAI API key not configured.")

    anchor_tickers = {str(a.get("ticker", "")).upper() for a in anchors if a.get("ticker")}
    anchors_block = "\n".join(
        f"  • {a.get('ticker','')} — {a.get('name','')}"
        + (f": {a.get('how','')}" if a.get("how") else "")
        for a in anchors) or "  (none)"

    dd_lines = []
    for d in (deep_dives or []):
        suppliers = ", ".join(f"{s.get('name','')}{'('+s['ticker']+')' if s.get('ticker') else ''}" for s in (d.get("suppliers") or [])[:10])
        clients = ", ".join(f"{c.get('name','')}" for c in (d.get("clients") or [])[:6])
        dd_lines.append(
            f"  {d.get('ticker','')} — {(d.get('what_it_does') or '')[:200]}\n"
            f"     NAMED suppliers in docs: {suppliers or 'none named'}\n     clients: {clients or 'n/a'}")
    dd_block = "\n".join(dd_lines)[:16000] or "  (no deep dives — infer from what the anchors make)"

    emph = [e for e in (emphasis or []) if e and e.strip()]
    emph_block = "\n".join(f"  • {e}" for e in emph) or "  (none)"

    analysis = await _call_json(
        _COMPONENT_PICKS_SYSTEM,
        _COMPONENT_PICKS_USER.format(theme=theme.strip(), component=component,
                                     anchors=anchors_block, deepdives=dd_block, emphasis=emph_block),
        openai_key, model, max_tokens=3500, temperature=0.2,
    )

    raw = [s for s in (analysis.get("suppliers") or [])
           if isinstance(s, dict) and (s.get("ticker") or "").strip().upper() not in anchor_tickers]
    listed = [s for s in raw if (s.get("ticker") or "").strip()]   # only enrich/verify listed names
    all_tickers = list(dict.fromkeys((s.get("ticker") or "").strip().upper() for s in listed))

    financials, verify_map = await asyncio.gather(
        asyncio.to_thread(_fetch_financials_sync, all_tickers),
        _verify_companies(listed, theme.strip(), openai_key, model),
    )

    cards = []
    for item in listed:
        tkr = (item.get("ticker") or "").strip().upper()
        card = _build_company_card(tkr, item, financials.get(tkr) or {})
        card["provenance"] = item.get("provenance")
        card["supply_kind"] = item.get("supply_kind")
        cards.append(card)
    cards = _apply_verification(cards, verify_map, financials)
    # Append any unlisted/private suppliers (no ticker) so the user still sees the lead.
    for item in raw:
        if not (item.get("ticker") or "").strip():
            cards.append({
                "ticker": "—", "name": item.get("name", "Private/unlisted"),
                "thesis": item.get("thesis", ""), "catalysts": item.get("catalysts") or [],
                "risk": item.get("risk"), "provenance": item.get("provenance"),
                "supply_kind": item.get("supply_kind"), "unlisted": True,
            })

    return {
        "theme":            f"{theme.strip()} — {component}",
        "title":            analysis.get("title", component),
        "summary":          analysis.get("summary", ""),
        "selection_notes":  analysis.get("selection_notes", ""),
        "suppliers":        cards,
        "generated_at":     _now(),
    }
