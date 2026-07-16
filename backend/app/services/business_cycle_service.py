"""Business cycle classification service.

Data sources
------------
1. OECD Composite Leading Indicators (CLI), amplitude-adjusted
   URL: stats.oecd.org/SDMX-JSON/data/MEI_CLI/...
   Methodology: CLI > 100 & rising = Expansion; > 100 & falling = Slowdown;
                < 100 & falling = Contraction; < 100 & rising = Recovery.
   Why CLI: designed specifically to detect cycle turning points 6–9 months ahead.

2. IMF DataMapper — real GDP growth (NGDP_RPCH, %)
   URL: imf.org/external/datamapper/api/v1/NGDP_RPCH/...
   Provides annual actuals + WEO forecasts for all countries.

3. LLM (optional) — 3 key signals + outlook sentence, grounded in real data.
   Only called when the user has an OpenAI key; classification does NOT depend on it.
"""

import asyncio
import json
import logging
from datetime import datetime

import httpx

from .llm_service import call_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Region definitions
# ---------------------------------------------------------------------------
# oecd_code: country code for MEI_CLI dataset (OECD 3-letter)
# imf_code:  country code for IMF DataMapper (ISO 3-letter)
# proxy_note: if using a proxy country, note it for display

REGIONS = [
    {"id": "usa",         "name": "United States",  "flag": "🇺🇸",
     "oecd_code": "USA",  "imf_code": "USA",  "proxy_note": None},
    {"id": "china",       "name": "China",           "flag": "🇨🇳",
     "oecd_code": "CHN",  "imf_code": "CHN",  "proxy_note": None},
    {"id": "india",       "name": "India",           "flag": "🇮🇳",
     "oecd_code": "IND",  "imf_code": "IND",  "proxy_note": None},
    {"id": "japan",       "name": "Japan",           "flag": "🇯🇵",
     "oecd_code": "JPN",  "imf_code": "JPN",  "proxy_note": None},
    {"id": "uk",          "name": "United Kingdom",  "flag": "🇬🇧",
     "oecd_code": "GBR",  "imf_code": "GBR",  "proxy_note": None},
    {"id": "europe",      "name": "Europe",          "flag": "🇪🇺",
     "oecd_code": "DEU",  "imf_code": "DEU",  "proxy_note": "Germany as proxy"},
    {"id": "latam",       "name": "Latin America",   "flag": "🌎",
     "oecd_code": "BRA",  "imf_code": "BRA",  "proxy_note": "Brazil as proxy"},
    {"id": "israel",      "name": "Israel",          "flag": "🇮🇱",
     "oecd_code": "ISR",  "imf_code": "ISR",  "proxy_note": None},
    {"id": "south_korea", "name": "South Korea",     "flag": "🇰🇷",
     "oecd_code": "KOR",  "imf_code": "KOR",  "proxy_note": None},
]

# ---------------------------------------------------------------------------
# Static asset-class performance matrix (research-based)
# ---------------------------------------------------------------------------

ASSET_MATRIX = {
    "early": {
        "label": "Recovery",
        "color": "#22c55e",
        "description": "GDP bottoming, credit spreads tightening, PMI <50 but rising",
        "performers": [
            {"category": "Small Cap",              "stars": 5, "note": "High beta captures the recovery bounce"},
            {"category": "Consumer Discretionary", "stars": 5, "note": "Pent-up demand drives spending"},
            {"category": "Financials",             "stars": 4, "note": "Loan growth + steepening yield curve"},
            {"category": "Real Estate (REITs)",    "stars": 4, "note": "Falling rates + economic revival"},
            {"category": "High Yield Bonds",       "stars": 4, "note": "Credit spreads compress as risk-on returns"},
            {"category": "Industrials",            "stars": 4, "note": "Inventory restocking drives production"},
            {"category": "Large Value",            "stars": 3, "note": "Beaten-down cyclicals rebound"},
            {"category": "Consumer Staples",       "stars": 2, "note": "Defensive; underperforms strong recoveries"},
            {"category": "Utilities",              "stars": 2, "note": "Rate-sensitive; left behind in recovery"},
            {"category": "Treasuries (Long)",      "stars": 1, "note": "Rising yields hurt duration"},
        ],
    },
    "mid": {
        "label": "Expansion",
        "color": "#3b82f6",
        "description": "Sustained GDP growth, employment rising, corporate profits strong",
        "performers": [
            {"category": "Large Growth",           "stars": 5, "note": "Earnings growth premium in sustained expansion"},
            {"category": "Technology",             "stars": 5, "note": "Capex cycle + margins at peak"},
            {"category": "Industrials",            "stars": 4, "note": "Full capacity utilization, strong order books"},
            {"category": "Healthcare",             "stars": 4, "note": "Secular growth with low economic sensitivity"},
            {"category": "Mid Cap",                "stars": 4, "note": "Sweet spot of size and earnings momentum"},
            {"category": "International Developed","stars": 4, "note": "Global synchronization supports ex-US markets"},
            {"category": "Energy",                 "stars": 3, "note": "Rising demand, inflation not yet a headwind"},
            {"category": "Small Cap",              "stars": 3, "note": "Good but less alpha vs early cycle"},
            {"category": "Consumer Staples",       "stars": 2, "note": "Defensive positioning premature"},
            {"category": "Treasuries (Long)",      "stars": 1, "note": "Rising terminal-rate fears weigh"},
        ],
    },
    "late": {
        "label": "Slowdown",
        "color": "#f59e0b",
        "description": "Growth decelerating, inflation elevated, yield curve flat/inverted",
        "performers": [
            {"category": "Energy",                 "stars": 5, "note": "Inflation + supply constraints peak together"},
            {"category": "Materials & Commodities","stars": 5, "note": "Input price inflation spills into returns"},
            {"category": "Healthcare",             "stars": 4, "note": "Defensive quality holds as growth fades"},
            {"category": "Large Value",            "stars": 4, "note": "Cheap, cash-generative businesses outperform"},
            {"category": "Consumer Staples",       "stars": 4, "note": "Pricing power + non-discretionary demand"},
            {"category": "Utilities",              "stars": 3, "note": "Rate curve anchoring; defensive bid builds"},
            {"category": "Investment Grade Bonds", "stars": 3, "note": "Flight to quality begins"},
            {"category": "Small Cap",              "stars": 2, "note": "Credit tightening hurts leveraged small firms"},
            {"category": "Large Growth",           "stars": 2, "note": "Multiple compression as rates stay high"},
            {"category": "High Yield Bonds",       "stars": 1, "note": "Spread widening as recession risk rises"},
        ],
    },
    "recession": {
        "label": "Contraction",
        "color": "#ef4444",
        "description": "GDP contracting, unemployment rising, PMI <50 and falling",
        "performers": [
            {"category": "Treasuries (Long)",      "stars": 5, "note": "Flight to safety + Fed rate cuts"},
            {"category": "Consumer Staples",       "stars": 5, "note": "Non-discretionary demand holds revenue"},
            {"category": "Healthcare",             "stars": 5, "note": "Inelastic demand regardless of cycle"},
            {"category": "Utilities",              "stars": 4, "note": "Regulated revenues, high relative yield"},
            {"category": "Gold",                   "stars": 4, "note": "Safe haven and USD hedge as Fed eases"},
            {"category": "Investment Grade Bonds", "stars": 4, "note": "Quality credit benefits from duration rally"},
            {"category": "Large Cap Blend",        "stars": 3, "note": "Quality bias outperforms but still down"},
            {"category": "Real Estate (REITs)",    "stars": 2, "note": "Rate cuts help but rent growth slows"},
            {"category": "High Yield Bonds",       "stars": 2, "note": "Default risk offsets yield; mixed"},
            {"category": "Small Cap",              "stars": 1, "note": "Highest default risk; credit crunch hits hardest"},
            {"category": "Cyclicals (Energy/Mats)","stars": 1, "note": "Demand destruction overwhelms supply cuts"},
        ],
    },
}


# ---------------------------------------------------------------------------
# OECD CLI fetch + parse
# stats.oecd.org was retired March 2024; use sdmx.oecd.org (new OECD Data API).
# We try two URL patterns and a flexible parser that handles both SDMX-JSON v1
# (LOCATION dim) and v2 (REF_AREA dim) response shapes.
# ---------------------------------------------------------------------------

_OECD_CANDIDATE_URLS: list[tuple[str, str]] = []  # built lazily below

def _build_oecd_urls() -> list[tuple[str, str]]:
    # OECD CLI covers G20 + Spain; Israel is not in the CLI dataset.
    # Dimension key order: REF_AREA.FREQ.MEASURE.UNIT_MEASURE.ACTIVITY.ADJUSTMENT.TRANSFORMATION.TIME_HORIZ.METHODOLOGY
    # AA = amplitude-adjusted (headline CLI), LI = leading indicator measure.
    codes = "+".join(r["oecd_code"] for r in REGIONS if r["oecd_code"] != "ISR")
    return [
        (
            "https://sdmx.oecd.org/public/rest/data/"
            f"OECD.SDD.STES,DSD_STES@DF_CLI/"
            f"{codes}.M.LI.IX._Z.AA.IX._Z.H"
            "?lastNObservations=13&format=jsondata",
            "sdmx.oecd.org/DF_CLI/AA",
        ),
    ]


async def _fetch_oecd_cli() -> dict[str, list[float]]:
    """Try each OECD candidate URL in order; return first non-empty result."""
    for url, label in _build_oecd_urls():
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                logger.warning("OECD CLI %s → HTTP %s: %s", label, resp.status_code, resp.text[:200])
                continue
            raw = resp.json()
            # New API: {"meta":…, "data": {"dataSets":[…], "structures":[…]}}
            # Old API: {"dataSets":[…], "structure":{…}}
            payload = raw.get("data", raw)
            result = _parse_oecd_sdmx(payload)
            if result:
                logger.info("OECD CLI fetched via %s: %d countries", label, len(result))
                return result
            logger.warning("OECD CLI %s → parsed empty", label)
        except Exception as exc:
            logger.warning("OECD CLI %s failed: %s", label, exc)
    return {}


def _parse_oecd_sdmx(data: dict) -> dict[str, list[float]]:
    """
    Parse OECD SDMX-JSON (v1 or v2) → {country_code: [cli_values oldest→newest]}.

    New API (sdmx.oecd.org) returns {"data": {"dataSets": [...], "structures": [...]}}
    Old API returned {"dataSets": [...], "structure": {...}}
    Country dimension may be LOCATION (old) or REF_AREA (new).
    """
    result: dict[str, list[float]] = {}
    try:
        # Handle both old ("structure") and new ("structures") API shapes
        if "structures" in data:
            structure = data["structures"][0]
        else:
            structure = data["structure"]

        dims = structure["dimensions"]["series"]

        # Find country dimension by id (position varies between API versions)
        country_dim_idx, country_codes = None, []
        for i, d in enumerate(dims):
            if d["id"] in ("LOCATION", "REF_AREA"):
                country_dim_idx = i
                country_codes   = [v["id"] for v in d["values"]]
                break
        if country_dim_idx is None:
            logger.warning("OECD parse: no LOCATION/REF_AREA dimension found. Dims: %s",
                           [d["id"] for d in dims])
            return {}

        obs_dims = structure["dimensions"].get("observation", [])
        time_dim = next((d for d in obs_dims if d["id"] == "TIME_PERIOD"), None)

        # Build index → time-period-string mapping so we can sort chronologically.
        # The OECD sdmx.oecd.org API returns time periods newest-first (descending),
        # which is the opposite of the older stats.oecd.org API. Sorting by the
        # time-period ID string (YYYY-MM) gives us oldest→newest regardless of
        # which direction the API returns them.
        if time_dim:
            idx_to_period = {str(i): v["id"] for i, v in enumerate(time_dim["values"])}
        else:
            idx_to_period = None

        for series_key, series_data in data["dataSets"][0]["series"].items():
            parts   = series_key.split(":")
            loc_idx = int(parts[country_dim_idx])
            if loc_idx >= len(country_codes):
                continue
            country = country_codes[loc_idx]
            obs     = series_data.get("observations", {})

            if idx_to_period:
                # Sort by time-period string ascending (oldest → newest)
                sorted_keys = sorted(
                    (k for k in obs if obs[k] and obs[k][0] is not None),
                    key=lambda k: idx_to_period.get(k, k),
                )
                values = [float(obs[k][0]) for k in sorted_keys]
            else:
                values = [
                    float(obs[k][0])
                    for k in sorted(obs, key=lambda x: int(x))
                    if obs[k] and obs[k][0] is not None
                ]

            if values:
                result[country] = values
    except Exception as exc:
        logger.warning("OECD SDMX parse error: %s", exc)
    return result


# ---------------------------------------------------------------------------
# IMF GDP fetch + parse
# ---------------------------------------------------------------------------

async def _fetch_imf_gdp() -> dict[str, dict[int, float]]:
    """Fetch real GDP growth (NGDP_RPCH) from IMF DataMapper."""
    codes = "/".join(r["imf_code"] for r in REGIONS)
    url = f"https://www.imf.org/external/datamapper/api/v1/NGDP_RPCH/{codes}"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            raw = resp.json()
        gdp_map = raw.get("values", {}).get("NGDP_RPCH", {})
        return {
            country: {int(y): float(v) for y, v in year_data.items() if v is not None}
            for country, year_data in gdp_map.items()
        }
    except Exception as exc:
        logger.warning("IMF GDP fetch failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Phase classification from OECD CLI
# ---------------------------------------------------------------------------

def _classify_phase(cli_values: list[float]) -> tuple[str, str, str, float | None, float | None]:
    """
    Classify business cycle phase from CLI amplitude-adjusted values.

    Returns (phase, phase_label, momentum, cli_current, cli_3m_change).

    OECD methodology:
      CLI > 100 & rising  → mid  (Expansion)
      CLI > 100 & falling → late (Slowdown)
      CLI < 100 & falling → recession (Contraction)
      CLI < 100 & rising  → early (Recovery)

    Direction is measured using the 3-month change to avoid noise.
    """
    if not cli_values:
        return "mid", "Expansion", "stable", None, None

    current = cli_values[-1]
    # 3-month change for trend direction (filters single-month noise)
    lookback = cli_values[-4] if len(cli_values) >= 4 else cli_values[0]
    cli_3m_change = round(current - lookback, 3)
    rising = cli_3m_change > 0
    above_100 = current >= 100.0

    if above_100 and rising:
        phase, label = "mid", "Expansion"
    elif above_100 and not rising:
        phase, label = "late", "Slowdown"
    elif not above_100 and not rising:
        phase, label = "recession", "Contraction"
    else:
        phase, label = "early", "Recovery"

    # Momentum: is the CLI accelerating or decelerating?
    if len(cli_values) >= 3:
        d_recent = cli_values[-1] - cli_values[-2]
        d_prior  = cli_values[-2] - cli_values[-3]
        if d_recent > d_prior + 0.03:
            momentum = "accelerating"
        elif d_recent < d_prior - 0.03:
            momentum = "decelerating"
        else:
            momentum = "stable"
    else:
        momentum = "stable"

    return phase, label, momentum, round(current, 3), cli_3m_change


def _months_in_phase(cli_values: list[float], current_phase: str) -> int | None:
    """Count consecutive months at end of series that share the current phase."""
    if len(cli_values) < 2:
        return None
    count = 0
    for i in range(len(cli_values) - 1, 0, -1):
        cur = cli_values[i]
        prv = cli_values[i - 1]
        rising = cur > prv
        above = cur >= 100.0
        if above and rising:
            p = "mid"
        elif above:
            p = "late"
        elif not rising:
            p = "recession"
        else:
            p = "early"
        if p == current_phase:
            count += 1
        else:
            break
    return count or 1


# ---------------------------------------------------------------------------
# GDP-trajectory fallback (when OECD CLI is unavailable)
# ---------------------------------------------------------------------------

def _classify_from_gdp(
    gdp_map: dict[int, float],
    current_year: int,
) -> tuple[str, str, str]:
    """
    Rough cycle phase from IMF GDP growth trajectory.
    Less precise than OECD CLI (annual vs monthly, lags real turning points)
    but produces country-specific results instead of a uniform default.

    Returns (phase, phase_label, momentum).
    """
    current  = gdp_map.get(current_year)  or gdp_map.get(current_year - 1)
    prev     = gdp_map.get(current_year - 1) or gdp_map.get(current_year - 2)
    forecast = gdp_map.get(current_year + 1) or gdp_map.get(current_year)

    if current is None:
        return "mid", "Expansion", "stable"

    # ── Phase ──────────────────────────────────────────────────────────────
    if current < 0:
        phase, label = "recession", "Contraction"

    elif current < 1.0:
        # Very low growth: bottom of recession or early turnaround
        if prev is not None and current > prev:
            phase, label = "early", "Recovery"
        else:
            phase, label = "recession", "Contraction"

    elif current < 2.0:
        # Sluggish growth: early recovery or late slowdown
        if prev is not None and current > prev + 0.4:
            phase, label = "early", "Recovery"
        elif forecast is not None and forecast < current - 0.3:
            phase, label = "late", "Slowdown"
        else:
            phase, label = "mid", "Expansion"

    elif current < 3.5:
        # Moderate growth: expansion or late-cycle deceleration
        if forecast is not None and forecast < current - 0.6:
            phase, label = "late", "Slowdown"
        else:
            phase, label = "mid", "Expansion"

    else:
        # Strong growth (≥ 3.5 %): expansion, or late if sharply decelerating
        if forecast is not None and forecast < current - 1.0:
            phase, label = "late", "Slowdown"
        else:
            phase, label = "mid", "Expansion"

    # ── Momentum ───────────────────────────────────────────────────────────
    momentum = "stable"
    if prev is not None:
        diff = current - prev
        if diff > 0.5:
            momentum = "accelerating"
        elif diff < -0.5:
            momentum = "decelerating"

    return phase, label, momentum


# ---------------------------------------------------------------------------
# LLM enrichment — signals and outlook grounded in real data
# ---------------------------------------------------------------------------

async def _llm_enrich(
    region_summaries: list[dict],
    openai_key: str,
    model: str,
) -> dict[str, dict]:
    today = datetime.now().strftime("%B %Y")
    current_year = datetime.now().year
    lines = []
    for r in region_summaries:
        cli  = f"CLI={r['cli_current']:.3f} (3M Δ {r['cli_3m_change']:+.3f})" if r["cli_current"] else "CLI=N/A"
        gdp  = f"GDP {current_year}={r['gdp_current_year']}%" if r["gdp_current_year"] is not None else "GDP=N/A"
        fct  = f"Forecast {current_year+1}={r['gdp_next_year']}%" if r["gdp_next_year"] is not None else ""
        lines.append(f"{r['name']}: Phase={r['phase_label']}, {cli}, {gdp} {fct}".strip())

    prompt = (
        f"You are a global macro strategist. Today is {today}.\n\n"
        "Based on the REAL OECD CLI and IMF GDP data below, for each region give:\n"
        "  1. Three specific, data-grounded key signals explaining WHY this economy is in this phase\n"
        "  2. A clear one-sentence outlook for the next 6 months\n\n"
        "Data:\n" + "\n".join(lines) + "\n\n"
        "Return ONLY valid JSON:\n"
        '{"regions": [{"id": "usa", "key_signals": ["...", "...", "..."], '
        '"outlook_headline": "..."}, ...]}\n'
        "Classify all 9 regions: usa china india japan uk europe latam israel south_korea"
    )
    try:
        resp = await call_llm(
            api_key=openai_key,
            model=model,
            messages=[
                {"role": "system", "content": "You are a global macro strategist. Return only valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=2000,
            temperature=0.3,
        )
        text = (resp or "").strip()
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
        return {r["id"]: r for r in json.loads(text).get("regions", [])}
    except Exception as exc:
        logger.warning("LLM enrichment failed: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def get_business_cycle(openai_key: str | None, model: str = "gpt-4o") -> dict:
    """Classify business cycle phases using OECD CLI + IMF GDP; optional LLM signals."""
    oecd_data, imf_data = await asyncio.gather(
        _fetch_oecd_cli(),
        _fetch_imf_gdp(),
        return_exceptions=True,
    )
    if isinstance(oecd_data, Exception):
        logger.warning("OECD failed: %s", oecd_data)
        oecd_data = {}
    if isinstance(imf_data, Exception):
        logger.warning("IMF failed: %s", imf_data)
        imf_data = {}

    oecd_available = bool(oecd_data)
    current_year   = datetime.now().year

    region_summaries = []
    for region in REGIONS:
        oc = region["oecd_code"]
        ic = region["imf_code"]

        cli_series = oecd_data.get(oc, [])
        gdp_map    = imf_data.get(ic, {})

        if cli_series:
            # Primary: OECD CLI — monthly, purpose-built for cycle detection
            phase, label, momentum, cli_cur, cli_3m = _classify_phase(cli_series)
            months     = _months_in_phase(cli_series, phase)
            confidence = "high"
            phase_src  = "oecd_cli"
        elif gdp_map:
            # Fallback: IMF GDP trajectory — annual, less timely but country-specific
            phase, label, momentum = _classify_from_gdp(gdp_map, current_year)
            cli_cur, cli_3m, months = None, None, None
            confidence = "medium"
            phase_src  = "imf_gdp_fallback"
        else:
            phase, label, momentum = "mid", "Expansion", "stable"
            cli_cur, cli_3m, months = None, None, None
            confidence = "low"
            phase_src  = "default"

        gdp_current = gdp_map.get(current_year) or gdp_map.get(current_year - 1)
        gdp_next    = gdp_map.get(current_year + 1) or gdp_map.get(current_year)
        gdp_series  = [
            {"year": y, "value": round(gdp_map[y], 2)}
            for y in sorted(gdp_map)
            if y >= current_year - 4 and gdp_map[y] is not None
        ]

        region_summaries.append({
            "id":               region["id"],
            "name":             region["name"],
            "flag":             region["flag"],
            "proxy_note":       region["proxy_note"],
            "phase":            phase,
            "phase_label":      label,
            "momentum":         momentum,
            "confidence":       confidence,
            "phase_source":     phase_src,
            "months_in_phase":  months,
            "cli_current":      cli_cur,
            "cli_3m_change":    cli_3m,
            "cli_series":       [round(v, 3) for v in cli_series[-12:]],
            "gdp_current_year": round(gdp_current, 2) if gdp_current is not None else None,
            "gdp_next_year":    round(gdp_next, 2)    if gdp_next    is not None else None,
            "gdp_year":         current_year,
            "gdp_series":       gdp_series,
            "key_signals":      [],
            "outlook_headline": "",
        })

    if openai_key:
        llm = await _llm_enrich(region_summaries, openai_key, model)
        for r in region_summaries:
            enriched = llm.get(r["id"], {})
            r["key_signals"]      = enriched.get("key_signals", [])
            r["outlook_headline"] = enriched.get("outlook_headline", "")

    cycle_src = (
        "OECD Composite Leading Indicators (CLI), amplitude-adjusted"
        if oecd_available
        else "IMF GDP growth trajectory (OECD CLI unavailable — see proxy note on cards)"
    )

    return {
        "regions":            region_summaries,
        "asset_matrix":       ASSET_MATRIX,
        "as_of":              datetime.now().strftime("%Y-%m-%d"),
        "generated_with_llm": bool(openai_key),
        "oecd_available":     oecd_available,
        "data_sources": {
            "cycle_phase": cycle_src,
            "gdp":         "IMF World Economic Outlook DataMapper (NGDP_RPCH)",
            "signals":     "AI-generated from real macro data" if openai_key
                           else "Add OpenAI key in Settings for AI-driven signals",
        },
    }
