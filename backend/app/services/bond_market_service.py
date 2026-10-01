"""Bond market data for the Bond Desk — free, keyless, official sources.

Sources (all verified keyless):

* **TreasuryDirect FedInvest** — end-of-day prices for EVERY marketable Treasury
  (bills, notes, bonds, TIPS real clean prices, FRNs) by CUSIP. The site needs a real
  session: GET the form (bot-manager cookies + ``_csrf``) → POST the date with
  Referer/Origin → manually follow the 302 to ``securityPriceDetail`` with the same
  cookies. A naive ``curl -L`` POST gets 403.
* **FiscalData auctions_query** — static terms per CUSIP (dated date, original term,
  TIPS reference CPI on the dated date, latest auction yield).
* **treasury.gov daily par curves** — nominal (1mo–30y) and real (5y–30y), one small
  CSV per calendar year (used for "curve on the purchase date" too).
* **FRED** (keyless CSV, limited with ``cosd``) — CPI-U NSA (TIPS index ratio), ICE BofA
  OAS + effective yields by rating and maturity bucket, breakevens, national average
  bank-CD rates, and long real/nominal yield history for percentile context.
* **OpenFIGI** — maps a corporate/muni/agency CUSIP to "ISSUER cpn mm/dd/yy".
* **yfinance** — bond ETF / mutual fund profiles and defined-maturity ETF rungs.

Everything is memoised in-process (small payloads — fits the shared 512 MiB budget)
and degrades gracefully: a failed source returns ``None`` and the payload says so in
``sources``. Nothing here needs the user's API keys.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import httpx

from . import bond_math as bm

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

# ---------------------------------------------------------------------------
# Memo (in-process, TTL) + per-key locks so concurrent callers share one fetch
# ---------------------------------------------------------------------------
_memo: dict[str, tuple[float, object]] = {}
_neg: dict[str, float] = {}          # key → monotonic time until which a failed source is not retried
_locks: dict[str, asyncio.Lock] = {}
_NEG_TTL = 600.0


def _mget(key: str):
    hit = _memo.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    return None


def _mstale(key: str):
    hit = _memo.get(key)
    return hit[1] if hit else None


def _mset(key: str, val, ttl: float):
    _memo[key] = (time.monotonic() + ttl, val)
    return val


def _lock(key: str) -> asyncio.Lock:
    lk = _locks.get(key)
    if lk is None:
        lk = _locks[key] = asyncio.Lock()
    return lk


async def _memoized(key: str, ttl: float, producer):
    """Return memoised value or build it once (serialised per key). Failures → stale/None, and the
    source is not retried for ``_NEG_TTL`` seconds (a down upstream must not add latency to every request)."""
    v = _mget(key)
    if v is not None:
        return v
    if _neg.get(key, 0.0) > time.monotonic():
        return _mstale(key)
    async with _lock(key):
        v = _mget(key)
        if v is not None:
            return v
        if _neg.get(key, 0.0) > time.monotonic():
            return _mstale(key)
        try:
            v = await producer()
        except Exception as exc:  # noqa: BLE001
            logger.warning("bond market source %s failed: %s", key, exc)
            v = None
        if v is None:
            _neg[key] = time.monotonic() + _NEG_TTL
            return _mstale(key)
        _neg.pop(key, None)
        return _mset(key, v, ttl)


def us_today() -> date:
    """Today in New York — Treasury data is published on US Eastern dates (servers often run in UTC,
    which is already 'tomorrow' every US evening)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:  # noqa: BLE001 — no tzdata: UTC minus 5h is close enough for a date
        return (datetime.utcnow() - timedelta(hours=5)).date()


def _f(x) -> float | None:
    try:
        if x in (None, "", "null", "."):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _d(x) -> date | None:
    if not x or x == "null":
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(x, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# FRED (bounded window)
# ---------------------------------------------------------------------------
_FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv"


async def fred_recent(series_id: str, days: int = 90) -> list[tuple[str, float]]:
    """``[(iso_date, value)]`` for the last ``days`` calendar days (memo 6h)."""
    async def _get():
        start = (us_today() - timedelta(days=days)).isoformat()
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as c:
            r = await c.get(_FRED_CSV, params={"id": series_id, "cosd": start})
            r.raise_for_status()
        out = []
        for row in list(csv.reader(io.StringIO(r.text)))[1:]:
            if len(row) == 2 and row[1] not in (".", ""):
                v = _f(row[1])
                if v is not None:
                    out.append((row[0], v))
        return out or None
    return (await _memoized(f"fred:{series_id}:{days}", 6 * 3600, _get)) or []


async def fred_latest(series_id: str, days: int = 90) -> tuple[str, float] | None:
    s = await fred_recent(series_id, days)
    return s[-1] if s else None


def _pctile(series: list[tuple[str, float]], x: float | None) -> float | None:
    if x is None or len(series) < 20:
        return None
    return round(100.0 * sum(1 for _, v in series if v <= x) / len(series), 1)


# ---------------------------------------------------------------------------
# treasury.gov par curves (nominal + real)
# ---------------------------------------------------------------------------
_TSY_CSV = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
            "daily-treasury-rates.csv/{y}/all?type={t}&field_tdr_date_value={y}&page&_format=csv")
_CURVE_TYPES = {"nominal": "daily_treasury_yield_curve", "real": "daily_treasury_real_yield_curve"}


def _tenor_from_label(lbl: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*(mo|month|yr|year)", lbl.strip().lower())
    if not m:
        return None
    n = float(m.group(1))
    return round(n / 12.0, 4) if m.group(2).startswith("mo") else n


async def curve_year(year: int, kind: str = "nominal") -> list[dict]:
    """All daily curves in ``year`` → ``[{date, points:[(tenor_years, yield_decimal)]}]`` (oldest→newest)."""
    async def _get():
        url = _TSY_CSV.format(y=year, t=_CURVE_TYPES[kind])
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=True, headers={"User-Agent": _UA}) as c:
            r = await c.get(url)
            r.raise_for_status()
        rows = list(csv.reader(io.StringIO(r.text)))
        if len(rows) < 2:
            return None
        tenors = [_tenor_from_label(h) for h in rows[0][1:]]
        out = []
        for row in rows[1:]:
            d = _d(row[0])
            if not d:
                continue
            pts = [(t, _f(v) / 100.0) for t, v in zip(tenors, row[1:]) if t is not None and _f(v) is not None]
            if pts:
                out.append({"date": d, "points": pts})
        out.sort(key=lambda x: x["date"])
        return out or None
    ttl = 3 * 3600 if year >= us_today().year else 7 * 86400
    return (await _memoized(f"curve:{kind}:{year}", ttl, _get)) or []


async def curve_on(d: date, kind: str = "nominal") -> dict | None:
    """The par curve published on or before ``d`` (walks into the prior year if needed)."""
    for y in (d.year, d.year - 1):
        rows = await curve_year(y, kind)
        cand = [r for r in rows if r["date"] <= d]
        if cand:
            return cand[-1]
    return None


# ---------------------------------------------------------------------------
# FedInvest — end-of-day prices for every marketable Treasury
# ---------------------------------------------------------------------------
_FEDINVEST = "https://www.treasurydirect.gov/GA-FI/FedInvest/"
_FI_TYPES = {
    "MARKET BASED BILL": "bill", "MARKET BASED NOTE": "note", "MARKET BASED BOND": "bond",
    "TIPS": "tips", "MARKET BASED FRN": "frn",
}


def _fedinvest_sync(d: date) -> list[dict]:
    """One day's FedInvest price table (``[]`` if none published that day)."""
    headers = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
               "Accept-Language": "en-US,en;q=0.9"}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=False) as c:
        r = c.get(_FEDINVEST + "selectSecurityPriceDate")
        r.raise_for_status()
        m = re.search(r'name="_csrf" value="([^"]+)"', r.text)
        if not m:
            raise RuntimeError("FedInvest: no csrf token")
        r2 = c.post(
            _FEDINVEST + "selectSecurityPriceDate",
            data={"priceDate": d.isoformat(), "_csrf": m.group(1), "submit": "Show Prices"},
            headers={"Referer": _FEDINVEST + "selectSecurityPriceDate", "Origin": "https://www.treasurydirect.gov"},
        )
        loc = r2.headers.get("location")
        if r2.status_code == 200:
            # FedInvest re-renders the form (HTTP 200) for a date it has no prices for — e.g. today before
            # the afternoon publish, weekends/holidays, or a server clock already on tomorrow (UTC).
            return []
        if r2.status_code not in (301, 302, 303) or not loc:
            raise RuntimeError(f"FedInvest: unexpected {r2.status_code}")
        url = loc if loc.startswith("http") else "https://www.treasurydirect.gov" + loc
        r3 = c.get(url, headers={"Referer": _FEDINVEST + "selectSecurityPriceDate"})
        r3.raise_for_status()
        html = r3.text
    hm = re.search(r"Prices For:\s*([A-Za-z]+ \d{1,2}, \d{4})", html)
    if hm:
        try:
            if datetime.strptime(hm.group(1), "%B %d, %Y").date() != d:
                return []
        except ValueError:
            pass
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) != 8 or cells[1] not in _FI_TYPES:
            continue
        eod = _f(cells[7])
        if not eod:
            continue
        out.append({
            "cusip": cells[0], "type": _FI_TYPES[cells[1]], "rate": _f(cells[2].rstrip("%")),
            "maturity": _d(cells[3]), "call_date": _d(cells[4]),
            "buy": _f(cells[5]) or None, "sell": _f(cells[6]) or None, "eod": eod,
        })
    return out


async def treasury_prices() -> dict | None:
    """Most recent FedInvest table → ``{as_of, rows}`` — walks back from today (New York date) over up
    to 7 weekdays, skipping days with nothing published (today before ~3pm ET, holidays)."""
    async def _get():
        d = us_today()
        tries = 0
        while tries < 7:
            if d.weekday() < 5:
                tries += 1
                rows = await asyncio.to_thread(_fedinvest_sync, d)
                if rows:
                    return {"as_of": d.isoformat(), "rows": rows}
            d -= timedelta(days=1)
        return None
    return await _memoized("fedinvest", 3 * 3600, _get)


# ---------------------------------------------------------------------------
# FiscalData — static terms per outstanding CUSIP
# ---------------------------------------------------------------------------
_AUCTIONS = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"


async def treasury_reference() -> dict[str, dict] | None:
    """``{cusip: {dated_date, original_term, ref_cpi, first_coupon, last_auction_*}}`` (memo 24h)."""
    async def _get():
        params = {
            "filter": f"maturity_date:gte:{us_today().isoformat()},security_type:in:(Note,Bond)",
            "fields": ("cusip,security_type,security_term,original_security_term,int_rate,maturity_date,"
                       "dated_date,original_dated_date,issue_date,ref_cpi_on_dated_date,"
                       "inflation_index_security,floating_rate,high_yield,auction_date,reopening,"
                       "first_int_payment_date"),
            "sort": "-auction_date", "page[size]": "10000",
        }
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.get(_AUCTIONS, params=params)
            r.raise_for_status()
        out: dict[str, dict] = {}
        for x in r.json().get("data", []):
            cu = x["cusip"]
            rec = out.setdefault(cu, {
                "cusip": cu, "tips": x.get("inflation_index_security") == "Yes",
                "frn": x.get("floating_rate") == "Yes", "coupon_pct": _f(x.get("int_rate")),
                "maturity": x.get("maturity_date"), "auctions": 0,
                "last_auction_date": x.get("auction_date"), "last_auction_yield_pct": _f(x.get("high_yield")),
            })
            rec["auctions"] += 1
            # the ORIGINAL (non-reopening) auction carries the canonical term/dates
            if x.get("reopening") == "No" or "dated_date" not in rec:
                rec["dated_date"] = x.get("original_dated_date") if x.get("original_dated_date") not in (None, "null") else x.get("dated_date")
                rec["original_term"] = x.get("original_security_term") or x.get("security_term")
                rec["first_coupon"] = x.get("first_int_payment_date")
            if _f(x.get("ref_cpi_on_dated_date")):
                rec["ref_cpi"] = _f(x.get("ref_cpi_on_dated_date"))
        return out or None
    return await _memoized("tsy_reference", 24 * 3600, _get)


async def cpi_monthly() -> dict[tuple[int, int], float]:
    """CPI-U NSA by (year, month) since 1996 — covers every TIPS dated date and any purchase
    date (index ratio at purchase), ~360 monthly points."""
    s = await fred_recent("CPIAUCNS", 31 * 366)
    out = {}
    for d, v in s:
        dd = _d(d)
        if dd:
            out[(dd.year, dd.month)] = v
    return out


# ---------------------------------------------------------------------------
# Treasury + TIPS catalogue (priced, with yields computed by bond_math)
# ---------------------------------------------------------------------------
def _years_label(t: float) -> str:
    return f"{t:.1f}y" if t >= 1 else f"{int(round(t * 12))}mo"


async def treasury_catalogue() -> dict | None:
    """Every marketable Treasury with its EOD price and computed yield/duration.

    TIPS rows carry the REAL clean price/yield, the current index ratio (CPI-U NSA
    interpolation from FRED; projected at the 5y breakeven when CPI isn't out yet) and
    a breakeven vs the nominal par curve at the same maturity.
    """
    prices = await treasury_prices()
    if not prices:
        return None
    key = f"tsy_catalogue:{prices['as_of']}"
    cached = _mget(key)
    if cached is not None:
        return cached
    ref, cpi, nominal = await asyncio.gather(treasury_reference(), cpi_monthly(), curve_on(us_today(), "nominal"))
    ref = ref or {}
    as_of = _d(prices["as_of"]) or us_today()
    settle = as_of + timedelta(days=1)
    be5 = await fred_latest("T5YIE", 30)
    infl = (be5[1] / 100.0) if be5 else 0.025
    npts = nominal["points"] if nominal else []

    def _build():
        rows = []
        for p in prices["rows"]:
            mat = p["maturity"]
            if not mat or mat <= settle:
                continue
            rr = ref.get(p["cusip"], {})
            dated = _d(rr.get("dated_date") or "")
            cpn = (p["rate"] or 0.0) / 100.0 if p["type"] not in ("bill",) else 0.0
            spec = bm.BondSpec(maturity=mat, coupon=cpn, freq=2, day_count="ACT/ACT", issue=dated)
            t = bm.year_frac(settle, mat)
            row = {
                "cusip": p["cusip"], "type": p["type"], "coupon_pct": round(cpn * 100, 4),
                "maturity": mat.isoformat(), "years": round(t, 3), "tenor_label": _years_label(t),
                "price": p["eod"], "buy": p["buy"], "sell": p["sell"],
                "dated_date": dated.isoformat() if dated else None,
                "original_term": rr.get("original_term"),
                "last_auction_date": rr.get("last_auction_date"),
                "last_auction_yield_pct": rr.get("last_auction_yield_pct"),
            }
            if p["type"] in ("note", "bond") and t < 30 / 365.25:
                # 1/32nds price granularity makes a days-to-maturity coupon yield meaningless
                row.update(ytm_pct=None, mod_duration=round(t, 3), accrued=None, note="matures within 30 days")
                rows.append(row)
                continue
            if p["type"] == "frn":
                row.update(ytm_pct=None, mod_duration=None, accrued=None)
                rows.append(row)
                continue
            try:
                a = bm.analytics(spec, settle, clean=p["eod"])
            except Exception:  # noqa: BLE001
                a = None
            if not a or a["ytm"] is None:
                continue
            row.update(
                ytm_pct=round(a["ytm"] * 100, 3), accrued=round(a["accrued"], 5),
                mod_duration=round(a["mod_duration"], 3) if a["mod_duration"] else None,
                convexity=round(a["convexity"], 2) if a["convexity"] else None,
            )
            if p["type"] == "tips":
                refc = rr.get("ref_cpi")
                ir, projected = bm.latest_index_ratio(settle, refc, cpi, infl) if refc else (None, True)
                nom = bm.interp(npts, t)
                row.update(
                    real_yield_pct=row["ytm_pct"], ref_cpi=refc, index_ratio=ir, index_ratio_projected=projected,
                    adjusted_price=round((p["eod"] + a["accrued"]) * ir, 4) if ir else None,
                    breakeven_pct=round((nom - a["ytm"]) * 100, 3) if nom is not None else None,
                )
            rows.append(row)
        rows.sort(key=lambda r: r["maturity"])
        return rows

    rows = await asyncio.to_thread(_build)
    out = {"as_of": prices["as_of"], "settle": settle.isoformat(), "count": len(rows), "rows": rows,
           "inflation_assumption_pct": round(infl * 100, 3),
           "source": "TreasuryDirect FedInvest end-of-day prices; terms from FiscalData; yields computed (ACT/ACT, semiannual)."}
    return _mset(key, out, 3 * 3600)


async def tips_catalogue() -> dict | None:
    cat = await treasury_catalogue()
    if not cat:
        return None
    tips = [r for r in cat["rows"] if r["type"] == "tips"]
    years = sorted({int(r["maturity"][:4]) for r in tips})
    gaps = [y for y in range(years[0], years[-1] + 1) if y not in years] if years else []
    return {"as_of": cat["as_of"], "settle": cat["settle"], "rows": tips, "count": len(tips),
            "maturity_years": years, "gap_years": gaps,
            "inflation_assumption_pct": cat["inflation_assumption_pct"], "source": cat["source"]}


async def find_treasury(cusip: str) -> dict | None:
    cat = await treasury_catalogue()
    if not cat:
        return None
    cu = cusip.strip().upper()
    return next((r for r in cat["rows"] if r["cusip"] == cu), None)


def is_treasury_cusip(cusip: str | None) -> bool:
    """U.S. Treasury CUSIPs all start with issuer prefix 912 (912796/7 bills, 912810 bonds,
    912828/91282C notes & TIPS, 9128xx STRIPS)."""
    return bool(cusip) and cusip.strip().upper().startswith("912")


async def treasury_terms(cusip: str) -> dict | None:
    """Static terms for an outstanding note/bond/TIPS from FiscalData — works even when FedInvest
    prices are unavailable. TIPS rows carry the reference CPI and today's index ratio."""
    ref = (await treasury_reference() or {}).get(cusip.strip().upper())
    if not ref:
        return None
    out = {**ref}
    if ref.get("tips") and ref.get("ref_cpi"):
        cpi = await cpi_monthly()
        be5 = await fred_latest("T5YIE", 30)
        ir, projected = bm.latest_index_ratio(us_today(), ref["ref_cpi"], cpi, (be5[1] / 100.0) if be5 else 0.025)
        out["index_ratio"], out["index_ratio_projected"] = ir, projected
    return out


# ---------------------------------------------------------------------------
# Credit spreads, deposit rates, breakevens, rate context
# ---------------------------------------------------------------------------
RATINGS = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC"]
_OAS = {"AAA": "BAMLC0A1CAAA", "AA": "BAMLC0A2CAA", "A": "BAMLC0A3CA", "BBB": "BAMLC0A4CBBB",
        "BB": "BAMLH0A1HYBB", "B": "BAMLH0A2HYB", "CCC": "BAMLH0A3HYC",
        "IG": "BAMLC0A0CM", "HY": "BAMLH0A0HYM2"}
_IG_BUCKETS = [(2.0, "BAMLC1A0C13Y"), (4.0, "BAMLC2A0C35Y"), (6.0, "BAMLC3A0C57Y"),
               (8.5, "BAMLC4A0C710Y"), (12.5, "BAMLC7A0C1015Y"), (22.0, "BAMLC8A0C15PY")]


def rating_bucket(r: str | None) -> str | None:
    """Map any agency rating (AA+, Aa2, BBB-, Baa1, NR…) to AAA/AA/A/BBB/BB/B/CCC."""
    if not r:
        return None
    s = r.strip().upper().replace("+", "").replace("-", "")
    s = re.sub(r"[0-9]", "", s)
    moody = {"AAA": "AAA", "AA": "AA", "A": "A", "BAA": "BBB", "BA": "BB", "B": "B",
             "CAA": "CCC", "CA": "CCC", "C": "CCC"}
    if s in ("AAA", "AA", "A", "BBB", "BB", "B"):
        return s
    if s in moody:
        return moody[s]
    if s.startswith("CC") or s in ("C", "D", "SD"):
        return "CCC"
    return None


async def credit_spreads() -> dict | None:
    async def _get():
        ids = list(_OAS.values()) + [v + "EY" for v in _OAS.values()] + [b for _, b in _IG_BUCKETS]
        res = await asyncio.gather(*(fred_recent(s, 45) for s in ids))
        last = {s: (r[-1] if r else None) for s, r in zip(ids, res)}
        by_rating = {}
        for k, sid in _OAS.items():
            o, ey = last.get(sid), last.get(sid + "EY")
            by_rating[k] = {"oas_bp": round(o[1] * 100) if o else None,
                            "effective_yield_pct": ey[1] if ey else None,
                            "as_of": (o or ey or (None,))[0]}
        buckets = [{"mid_years": t, "oas_bp": round(last[s][1] * 100) if last.get(s) else None} for t, s in _IG_BUCKETS]
        hist = await asyncio.gather(fred_recent("BAMLC0A0CM", 3 * 366), fred_recent("BAMLH0A0HYM2", 3 * 366))
        ctx = {}
        for name, h in zip(("IG", "HY"), hist):
            cur = h[-1][1] if h else None
            ctx[name] = {"percentile_3y": _pctile(h, cur),
                         "min_3y_bp": round(min(v for _, v in h) * 100) if h else None,
                         "max_3y_bp": round(max(v for _, v in h) * 100) if h else None}
        if not any(v["oas_bp"] for v in by_rating.values()):
            return None
        return {"by_rating": by_rating, "ig_buckets": buckets, "context": ctx,
                "source": "ICE BofA indices via FRED (option-adjusted spreads + effective yields)."}
    return await _memoized("credit_spreads", 6 * 3600, _get)


def spread_for(spreads: dict | None, rating: str | None, t: float) -> float | None:
    """Estimated spread (decimal) for a rating at tenor ``t``: rating OAS × IG maturity-bucket shape."""
    if not spreads:
        return None
    rb = rating_bucket(rating) or "A"
    base = (spreads["by_rating"].get(rb) or {}).get("oas_bp")
    ig = (spreads["by_rating"].get("IG") or {}).get("oas_bp")
    if base is None:
        return None
    shape = 1.0
    pts = [(b["mid_years"], b["oas_bp"]) for b in spreads.get("ig_buckets", []) if b["oas_bp"]]
    if pts and ig:
        bt = bm.interp(pts, t)
        if bt:
            shape = max(0.4, min(1.6, bt / ig))
    return base * shape / 10000.0


async def deposit_rates() -> dict | None:
    async def _get():
        ids = {"6mo": "NDR6MCD", "12mo": "NDR12MCD", "60mo": "NDR60MCD"}
        res = await asyncio.gather(*(fred_latest(s, 120) for s in ids.values()))
        out = {k: ({"rate_pct": round(r[1], 3), "as_of": r[0]} if r else None) for k, r in zip(ids, res)}
        return out if any(out.values()) else None
    return await _memoized("deposit_rates", 12 * 3600, _get)


async def rate_context() -> dict:
    async def _get():
        ids = {"real_10y": "DFII10", "nominal_10y": "DGS10", "be_5y": "T5YIE", "be_10y": "T10YIE",
               "fed_funds": "DFF", "sofr": "SOFR"}
        long_ids = {"real_10y", "nominal_10y"}
        res = await asyncio.gather(*(fred_recent(s, 20 * 366 if k in long_ids else 60) for k, s in ids.items()))
        out = {}
        for (k, _), h in zip(ids.items(), res):
            cur = h[-1] if h else None
            out[k] = {"value_pct": cur[1] if cur else None, "as_of": cur[0] if cur else None,
                      "percentile_20y": _pctile(h, cur[1]) if (k in long_ids and cur) else None,
                      "avg_20y_pct": round(sum(v for _, v in h) / len(h), 3) if (k in long_ids and h) else None}
        return out
    return (await _memoized("rate_context", 6 * 3600, _get)) or {}


# ---------------------------------------------------------------------------
# yfinance: funds, muni ratio, defined-maturity ETF rungs
# ---------------------------------------------------------------------------
_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="bondyf")

DEFINED_MATURITY: dict[str, dict[int, str]] = {
    "treasury": {2026: "IBTG", 2027: "IBTH", 2028: "IBTI", 2029: "IBTJ", 2030: "IBTK", 2031: "IBTL",
                 2032: "IBTM", 2033: "IBTO", 2034: "IBTP", 2035: "IBTQ", 2036: "IBTR"},
    "corporate": {2026: "IBDR", 2027: "IBDS", 2028: "IBDT", 2029: "IBDU", 2030: "IBDV", 2031: "IBDW",
                  2032: "IBDX", 2033: "IBDY", 2034: "IBDZ", 2035: "BSCZ"},
    "muni": {2026: "IBMO", 2027: "IBMP", 2028: "IBMQ", 2029: "IBMR", 2030: "IBMS", 2031: "IBMT",
             2032: "IBMU", 2033: "IBMV", 2034: "BSMY"},
    "tips": {2026: "IBIC", 2027: "IBID", 2028: "IBIE", 2029: "IBIF", 2030: "IBIG", 2031: "IBIH",
             2032: "IBII", 2033: "IBIJ", 2034: "IBIK"},
    "high_yield": {2026: "BSJQ", 2027: "BSJR", 2028: "BSJS", 2029: "BSJT", 2030: "BSJU", 2031: "BSJV",
                   2032: "BSJW", 2033: "BSJX"},
    "corporate_alt": {2026: "BSCQ", 2027: "BSCR", 2028: "BSCS", 2029: "BSCT", 2030: "BSCU", 2031: "BSCV",
                      2032: "BSCW", 2033: "BSCX", 2034: "BSCY", 2035: "BSCZ"},
    "muni_alt": {2026: "BSMQ", 2027: "BSMR", 2028: "BSMS", 2029: "BSMT", 2030: "BSMU", 2031: "BSMV",
                 2032: "BSMW", 2034: "BSMY"},
}


def _est_duration(maturity_years: float | None, y: float | None) -> float | None:
    """Modified duration of a par bond with this maturity/yield — fallback for funds."""
    if not maturity_years or maturity_years <= 0:
        return None
    yy = y if (y and y > 0) else 0.04
    today = us_today()
    spec = bm.BondSpec(maturity=today + timedelta(days=int(maturity_years * 365.25)), coupon=yy, freq=2)
    a = bm.analytics(spec, today, clean=100.0)
    return a["mod_duration"] if a else None


def _fund_sync(ticker: str) -> dict | None:
    import yfinance as yf  # lazy — keep import cost off module load
    tk = yf.Ticker(ticker)
    info = tk.info or {}
    if not info or not (info.get("regularMarketPrice") or info.get("previousClose") or info.get("navPrice")):
        return None
    price = info.get("regularMarketPrice") or info.get("previousClose") or info.get("navPrice")
    dist_yield = info.get("yield")
    if dist_yield is None and info.get("dividendYield"):
        dist_yield = info["dividendYield"] / 100.0
    duration = maturity = cat_duration = None
    ratings: dict = {}
    # yfinance quirk: info.netExpenseRatio is in PERCENT units (BND → 0.03 = 0.03%), while
    # funds_data.fund_operations "Annual Report Expense Ratio" is a DECIMAL (0.0003).
    expense = info.get("netExpenseRatio")
    expense = expense / 100.0 if expense is not None else None
    try:
        fd = tk.funds_data
        bh = fd.bond_holdings
        if hasattr(bh, "to_dict"):
            bh = bh.to_dict()
        own = (bh or {}).get(ticker) or (bh or {}).get(ticker.upper()) or {}
        cat = (bh or {}).get("Category Average") or {}
        duration = _f(own.get("Duration"))
        maturity = _f(own.get("Maturity"))
        cat_duration = _f(cat.get("Duration"))
        ratings = {k: round(float(v), 4) for k, v in (fd.bond_ratings or {}).items() if v is not None}
        if expense is None:
            ops = fd.fund_operations
            ops = ops.to_dict() if hasattr(ops, "to_dict") else {}
            er = ((ops or {}).get(ticker) or {}).get("Annual Report Expense Ratio")
            expense = _f(er)
    except Exception:  # noqa: BLE001
        pass
    dur_source = "fund"
    if duration is None and maturity is not None:
        duration, dur_source = _est_duration(maturity, dist_yield), "estimated from average maturity"
    if duration is None and cat_duration is not None:
        duration, dur_source = cat_duration, "category average"
    return {
        "ticker": ticker.upper(), "name": info.get("longName") or info.get("shortName") or ticker.upper(),
        "quote_type": info.get("quoteType"), "price": price, "previous_close": info.get("previousClose"),
        "nav": info.get("navPrice"), "distribution_yield_pct": round(dist_yield * 100, 3) if dist_yield else None,
        "expense_ratio_pct": round(expense * 100, 3) if expense is not None else None,
        "total_assets": info.get("totalAssets"), "category": info.get("category"),
        "family": info.get("fundFamily"), "duration": round(duration, 2) if duration else None,
        "duration_source": dur_source if duration else None, "avg_maturity": maturity,
        "credit_mix": ratings,
    }


async def fund_profile(ticker: str) -> dict | None:
    t = ticker.upper().strip()
    async def _get():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_POOL, _fund_sync, t)
    return await _memoized(f"fund:{t}", 3 * 3600, _get)


# ---------------------------------------------------------------------------
# MEASURED fund duration — yfinance's fund duration/maturity fields are unusable
# (TLT "3.6", SHY "9.8y maturity", IEF filed as "Long Government"; most funds report ~9–10y maturity).
# ---------------------------------------------------------------------------
_DUR_TENORS = {"nominal": [2.0, 5.0, 10.0, 30.0], "real": [5.0, 10.0, 30.0]}


def _empirical_duration_sync(ticker: str, curve: dict, tenors: list[float]) -> dict | None:
    """Effective duration from 1y of daily TOTAL returns regressed on daily Treasury yield changes.

    r_t = α + Σ_k β_k·Δy_k,t  →  D = −Σ β_k  (the price response to a PARALLEL shift). Validated
    2026-09-30: TLT 15.2, IEF 7.1, AGG 6.0, BND 5.9, LQD 8.0, BSV 2.3, SCHP 6.0 (real curve), R² 0.8–0.96.
    """
    import numpy as np
    import yfinance as yf
    try:
        hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)["Close"]
    except Exception:  # noqa: BLE001
        return None
    px = {ts.date(): float(v) for ts, v in hist.items() if v == v and v > 0}
    ds = sorted(d for d in px if d in curve and all(k in curve[d] for k in tenors))
    X, y = [], []
    for a, b in zip(ds, ds[1:]):
        if (b - a).days > 5:
            continue
        y.append(px[b] / px[a] - 1.0)
        X.append([1.0] + [curve[b][k] - curve[a][k] for k in tenors])
    if len(y) < 100:
        return None
    X, y = np.asarray(X), np.asarray(y)
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    var = float(y.var())
    r2 = 1.0 - float((y - X @ beta).var()) / var if var > 0 else 0.0
    return {"duration": float(-beta[1:].sum()), "r2": max(0.0, r2), "n": len(y)}


async def empirical_duration(ticker: str, *, real: bool = False) -> dict | None:
    kind = "real" if real else "nominal"
    t = ticker.upper().strip()

    async def _get():
        today = us_today()
        rows = (await curve_year(today.year - 1, kind)) + (await curve_year(today.year, kind))
        curve = {r["date"]: dict(r["points"]) for r in rows}
        if not curve:
            return None
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(_POOL, _empirical_duration_sync, t, curve, _DUR_TENORS[kind])
    return await _memoized(f"empdur:{t}:{kind}", 24 * 3600, _get)


async def fund_profile_full(ticker: str) -> dict | None:
    """``fund_profile`` + a MEASURED effective duration (what every analytic should use for funds).

    TIPS funds are regressed on REAL yields. Muni funds: muni yields move ≈ ratio × Treasury moves, so the
    Treasury-measured duration is divided by the muni/Treasury ratio to get the fund's own duration
    (MUB: 3.86 ÷ 0.643 ≈ 6.0 — its published duration). Confidence from R²; low-R² funds (high yield,
    floating rate, EM) keep the measurement but are flagged.
    """
    t = ticker.upper().strip()

    async def _get():
        base = await fund_profile(t)
        if not base:
            return None
        text = " ".join(str(base.get(k) or "") for k in ("name", "category")).lower()
        tips = any(k in text for k in ("tips", "inflation"))
        muni = any(k in text for k in ("muni", "municipal", "tax-exempt", "tax exempt", "tx-ex", "tax-free", "tax free"))
        out = {**base, "duration_vendor": base.get("duration")}
        emp = await empirical_duration(t, real=tips)
        if emp:
            d = emp["duration"]
            note = ""
            if muni:
                pts = (await muni_ratio())["points"]
                ratio = bm.interp(pts, max(0.5, d / 0.65)) or 0.65
                ratio = bm.interp(pts, max(0.5, d / ratio)) or ratio
                d, note = d / ratio, f" ÷ muni/Treasury ratio {ratio:.2f}"
            conf = "high" if emp["r2"] >= 0.5 else ("medium" if emp["r2"] >= 0.25 else "low")
            out.update(duration=round(min(30.0, max(0.0, d)), 2), duration_r2=round(emp["r2"], 2),
                       duration_confidence=conf,
                       duration_source=f"measured: 1y daily returns vs {'real' if tips else 'Treasury'} yield moves "
                                       f"(R² {emp['r2']:.2f}){note}")
        else:
            out.update(duration_confidence="unverified",
                       duration_source=f"{base.get('duration_source') or 'vendor'} — data vendor figure, unverified")
        return out
    return await _memoized(f"fundfull:{t}", 12 * 3600, _get)


async def etf_rungs(family: str) -> list[dict]:
    reg = DEFINED_MATURITY.get(family) or {}
    today = us_today()
    items = [(y, t) for y, t in sorted(reg.items()) if y >= today.year]
    async def _one(y, t):
        p = await fund_profile(t)
        return {"year": y, "ticker": t, **({k: p.get(k) for k in ("name", "price", "distribution_yield_pct",
                                                               "expense_ratio_pct", "total_assets")} if p else {})}
    return list(await asyncio.gather(*(_one(y, t) for y, t in items)))


# Typical shape of the AAA muni/Treasury ratio curve relative to ~5y (short end flat,
# long end much richer in ratio terms). Anchored to ONE live point (MUB) below.
_MUNI_RATIO_SHAPE = [(1.0, 1.02), (2.0, 1.0), (5.0, 1.0), (10.0, 1.07), (20.0, 1.28), (30.0, 1.38)]
_MUNI_RATIO_DEFAULT_5Y = 0.66


async def muni_ratio() -> dict:
    """Muni/Treasury yield ratio by tenor: live MUB anchor × typical term shape (clamped)."""
    async def _get():
        nominal = await curve_on(us_today(), "nominal")
        p = await fund_profile("MUB")
        if not nominal or not p or not p.get("distribution_yield_pct") or not p.get("duration"):
            return None
        tsy = bm.interp(nominal["points"], p["duration"])
        if not tsy:
            return None
        raw = (p["distribution_yield_pct"] / 100.0) / tsy
        shape_at = bm.interp(_MUNI_RATIO_SHAPE, p["duration"]) or 1.0
        anchor5 = max(0.5, min(1.0, raw / shape_at))
        pts = [(t, round(max(0.45, min(1.1, anchor5 * k)), 4)) for t, k in _MUNI_RATIO_SHAPE]
        return {"points": pts, "anchor": {"ticker": "MUB", "duration": p["duration"],
                                          "yield_pct": p["distribution_yield_pct"],
                                          "treasury_pct": round(tsy * 100, 3), "ratio": round(raw, 3)},
                "source": "MUB distribution yield ÷ Treasury at MUB duration, shaped by the typical muni ratio term structure (estimate)"}
    got = await _memoized("muni_ratio:v2", 6 * 3600, _get)
    if got:
        return got
    return {"points": [(t, round(_MUNI_RATIO_DEFAULT_5Y * k, 4)) for t, k in _MUNI_RATIO_SHAPE], "anchor": None,
            "source": "Long-run typical AAA muni/Treasury ratios (live proxy unavailable)"}


# ---------------------------------------------------------------------------
# Model yields per instrument type (the ladder & estimated-mark engine)
# ---------------------------------------------------------------------------
KIND_LABELS = {
    "treasury": "Treasury", "tips": "TIPS", "muni": "Municipal", "corporate": "Corporate",
    "agency": "Agency", "cd": "CD", "etf": "Bond ETF", "mutual_fund": "Bond mutual fund",
}
_AGENCY_SPREAD = 0.0015
_CD_SPREAD = 0.0010


async def market_inputs() -> dict:
    """The small set of live inputs every model-yield lookup needs (curves, spreads, ratios)."""
    today = us_today()
    nominal, real, spreads, mr, tips = await asyncio.gather(
        curve_on(today, "nominal"), curve_on(today, "real"), credit_spreads(), muni_ratio(), tips_catalogue())
    tips_pts = []
    if tips:
        tips_pts = [(r["years"], r["real_yield_pct"] / 100.0) for r in tips["rows"] if r.get("real_yield_pct") is not None]
    return {"nominal": nominal, "real": real, "spreads": spreads, "muni_ratio": mr, "tips_points": tips_pts}


def model_yield(kind: str, t: float, mi: dict, rating: str | None = None) -> tuple[float | None, str]:
    """Estimated market yield (decimal) for ``kind`` at tenor ``t`` years + a basis note."""
    npts = (mi.get("nominal") or {}).get("points") or []
    tsy = bm.interp(npts, t)
    if kind == "treasury":
        return tsy, "Treasury par curve"
    if kind == "tips":
        if mi.get("tips_points"):
            return bm.interp(sorted(mi["tips_points"]), t), "Fitted to FedInvest TIPS real yields"
        rpts = (mi.get("real") or {}).get("points") or []
        return bm.interp(rpts, t), "Treasury real par curve"
    if tsy is None:
        return None, "curve unavailable"
    if kind == "agency":
        return tsy + _AGENCY_SPREAD, "Treasury + ~15bp (typical agency spread)"
    if kind == "cd":
        return tsy + _CD_SPREAD, "Treasury + ~10bp (typical brokered-CD pickup — verify your quote)"
    if kind == "muni":
        r = bm.interp((mi.get("muni_ratio") or {}).get("points") or [], t) or 0.7
        return tsy * r, f"Treasury × muni ratio {r:.2f} (tax-exempt yield)"
    if kind in ("corporate", "high_yield"):
        rb = rating or ("BB" if kind == "high_yield" else "A")
        s = spread_for(mi.get("spreads"), rb, t)
        if s is None:
            s = 0.01
        return tsy + s, f"Treasury + {rating_bucket(rb) or 'A'} OAS {s * 1e4:.0f}bp"
    return tsy, "Treasury par curve"


# ---------------------------------------------------------------------------
# CUSIP lookup (Treasury catalogue → OpenFIGI)
# ---------------------------------------------------------------------------
_OPENFIGI = "https://api.openfigi.com/v3/mapping"
_US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
                 "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI".split())


def looks_like_cusip(s: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Z]{8}[0-9]", (s or "").strip().upper()))


def parse_figi_ticker(tk: str) -> dict:
    """"AAPL 2.4 05/03/23" / "CA CAS 2.4 10/01/2024" / "XITII 2 3/8 07/15/36" → coupon & maturity."""
    out: dict = {}
    m = re.match(r"^(?P<iss>.+?)\s+(?P<cpn>\d+(?:\.\d+)?(?:\s+\d+/\d+)?|\d+/\d+)\s+(?P<mat>\d{1,2}/\d{1,2}/\d{2,4})", tk or "")
    if not m:
        return out
    cp = m.group("cpn").split()
    val = 0.0
    for part in cp:
        if "/" in part:
            a, b = part.split("/")
            val += float(a) / float(b)
        else:
            val += float(part)
    out["coupon_pct"] = round(val, 4)
    mat = _d(m.group("mat"))
    if mat and mat.year < 1950:  # %y parsed as 19xx for >68
        mat = mat.replace(year=mat.year + 100)
    out["maturity"] = mat.isoformat() if mat else None
    first = m.group("iss").split()[0]
    if first in _US_STATES:
        out["state"] = first
    return out


async def lookup_cusip(cusip: str) -> dict | None:
    cu = cusip.strip().upper()
    tsy = await find_treasury(cu)
    if tsy:
        kind = "tips" if tsy["type"] == "tips" else "treasury"
        return {"cusip": cu, "kind": kind, "issuer": "U.S. Treasury", "coupon_pct": tsy["coupon_pct"],
                "maturity": tsy["maturity"], "issue_date": tsy.get("dated_date"), "price": tsy["price"],
                "ytm_pct": tsy.get("ytm_pct"), "security_type": tsy["type"], "rating": "AA+",
                "day_count": "ACT/ACT", "coupon_freq": 0 if tsy["type"] == "bill" else 2,
                "tips_ref_cpi": tsy.get("ref_cpi"), "index_ratio": tsy.get("index_ratio"),
                "source": "TreasuryDirect FedInvest"}
    terms = await treasury_terms(cu) if is_treasury_cusip(cu) else None
    if terms:
        kind = "tips" if terms.get("tips") else "treasury"
        return {"cusip": cu, "kind": kind, "issuer": "U.S. Treasury", "coupon_pct": terms.get("coupon_pct"),
                "maturity": terms.get("maturity"), "issue_date": terms.get("dated_date"), "price": None,
                "ytm_pct": None, "security_type": "tips" if terms.get("tips") else ("frn" if terms.get("frn") else "note"),
                "rating": "AA+", "day_count": "ACT/ACT", "coupon_freq": 2,
                "tips_ref_cpi": terms.get("ref_cpi"), "index_ratio": terms.get("index_ratio"),
                "source": "TreasuryDirect FiscalData (today's price not published yet)"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post(_OPENFIGI, json=[{"idType": "ID_CUSIP", "idValue": cu}])
            r.raise_for_status()
        data = (r.json() or [{}])[0].get("data") or []
    except Exception as exc:  # noqa: BLE001
        logger.info("OpenFIGI lookup failed for %s: %s", cu, exc)
        return None
    if not data:
        return None
    x = data[0]
    sector = (x.get("marketSector") or "").lower()
    tk = x.get("ticker") or x.get("securityDescription") or ""
    parsed = parse_figi_ticker(tk)
    kind = {"muni": "muni", "corp": "corporate", "govt": "agency"}.get(sector, "corporate")
    if is_treasury_cusip(cu):
        # OpenFIGI calls every Treasury "Govt"; a 912-prefixed CUSIP is the U.S. Treasury, never an agency.
        tips = "TII" in tk.upper().split()[0] if tk else False
        zero = (parsed.get("coupon_pct") or 0) == 0
        return {"cusip": cu, "kind": "tips" if tips else "treasury", "issuer": "U.S. Treasury", "description": tk,
                "security_type": "tips" if tips else ("bill" if zero else "note"), "rating": "AA+",
                "day_count": "ACT/ACT", "coupon_freq": 0 if zero else 2, **parsed, "source": "OpenFIGI"}
    name = x.get("name") or ""
    if "TXBL" in name.upper() and kind == "muni":
        parsed["federally_taxable_muni"] = True
    return {"cusip": cu, "kind": kind, "issuer": name.title(), "description": x.get("ticker"),
            "security_type": x.get("securityType"), "day_count": "30/360", "coupon_freq": 2,
            **parsed, "source": "OpenFIGI"}


# ---------------------------------------------------------------------------
# Market snapshot (Market tab)
# ---------------------------------------------------------------------------
async def market_snapshot() -> dict:
    today = us_today()
    mi, prev_m, prev_y, real_prev, dep, ctx, cat = await asyncio.gather(
        market_inputs(), curve_on(today - timedelta(days=30), "nominal"),
        curve_on(today - timedelta(days=365), "nominal"), curve_on(today - timedelta(days=365), "real"),
        deposit_rates(), rate_context(), treasury_catalogue())

    def _pts(c):
        return [{"tenor": t, "yield_pct": round(y * 100, 3)} for t, y in (c or {}).get("points", [])] if c else []

    nominal = mi.get("nominal")
    npts = (nominal or {}).get("points") or []
    shape = {}
    if npts:
        y3m, y2, y10, y30 = (bm.interp(npts, t) for t in (0.25, 2, 10, 30))
        shape = {"2s10s_bp": round((y10 - y2) * 1e4), "3m10y_bp": round((y10 - y3m) * 1e4),
                 "10s30s_bp": round((y30 - y10) * 1e4), "inverted_2s10s": y10 < y2, "inverted_3m10y": y10 < y3m}
    breakevens = []
    rpts = (mi.get("real") or {}).get("points") or []
    for t, r in rpts:
        n = bm.interp(npts, t)
        if n is not None:
            breakevens.append({"tenor": t, "breakeven_pct": round((n - r) * 100, 3)})

    menu = []
    for t in (0.25, 0.5, 1, 2, 3, 5, 7, 10, 20, 30):
        row = {"tenor": t}
        for k, rating in (("treasury", None), ("cd", None), ("agency", None), ("muni", None),
                          ("corporate", "AA"), ("corporate", "A"), ("corporate", "BBB"), ("tips", None)):
            key = k if not rating else f"corporate_{rating.lower()}"
            y, _ = model_yield(k, t, mi, rating)
            row[key] = round(y * 100, 3) if y is not None else None
        menu.append(row)

    auctions = []
    if cat:
        recent = sorted((r for r in cat["rows"] if r.get("last_auction_date")),
                        key=lambda r: r["last_auction_date"], reverse=True)[:12]
        auctions = [{k: r.get(k) for k in ("cusip", "type", "original_term", "maturity", "coupon_pct",
                                          "last_auction_date", "last_auction_yield_pct", "ytm_pct", "price")}
                    for r in recent]

    return {
        "as_of": nominal["date"].isoformat() if nominal else None,
        "nominal_curve": _pts(nominal), "nominal_curve_1m": _pts(prev_m), "nominal_curve_1y": _pts(prev_y),
        "nominal_curve_1m_date": prev_m["date"].isoformat() if prev_m else None,
        "nominal_curve_1y_date": prev_y["date"].isoformat() if prev_y else None,
        "real_curve": _pts(mi.get("real")), "real_curve_1y": _pts(real_prev),
        "curve_shape": shape, "breakevens": breakevens,
        "credit_spreads": mi.get("spreads"), "muni_ratio": mi.get("muni_ratio"),
        "deposit_rates": dep, "rate_context": ctx, "yield_menu": menu,
        "recent_auctions": auctions,
        "sources": {
            "curves": bool(nominal), "real_curve": bool(mi.get("real")), "fedinvest": bool(cat),
            "fedinvest_as_of": cat["as_of"] if cat else None, "spreads": bool(mi.get("spreads")),
            "deposit_rates": bool(dep),
        },
    }
