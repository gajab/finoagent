"""Bond math — deterministic fixed-income analytics (pure functions, no I/O).

Everything the Bond Desk shows about an individual bond is derived here from ONE
pricing function per bond, so every number reconciles with every other:

* ``dirty_from_yield`` is the single pricer (street convention: compound discounting
  at the coupon frequency; the money-market simple formula for the final period,
  exactly like Excel ``PRICE``/``YIELD``).
* Yields (YTM / YTC / YTW) invert that pricer; durations, convexity and DV01 are
  finite-difference derivatives OF THAT SAME PRICER (so a callable bond's effective
  duration is the slope of its price-to-worst curve, and DV01 always equals
  ModDur × dirty × 1bp).
* Key-rate durations are shares of the bond's own discounted cash-flow time mass,
  scaled so they SUM EXACTLY to modified duration.

Units: every *rate* crossing a function boundary here is a DECIMAL (0.0425), prices
are per 100 of face. Percent conversion happens at the service/payload boundary.

Conventions implemented
-----------------------
Day counts: ``30/360`` (US/NASD bond basis — corporates, munis, agencies),
``ACT/ACT`` (ICMA — Treasuries/TIPS), ``ACT/360`` and ``ACT/365`` (CDs / money market).
Coupon schedules are generated backward from maturity (end-of-month rule when the
maturity is a month end). Zero-coupon bonds use semiannual quasi-coupon periods.
"""

from __future__ import annotations

import calendar
import math
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, timedelta

DAY_COUNTS = ("30/360", "ACT/ACT", "ACT/360", "ACT/365")
BP = 1e-4


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------
def is_eom(d: date) -> bool:
    return d.day == calendar.monthrange(d.year, d.month)[1]


def add_months(d: date, n: int, eom: bool = False) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    m += 1
    last = calendar.monthrange(y, m)[1]
    return date(y, m, last if eom else min(d.day, last))


def days_30_360(d1: date, d2: date) -> int:
    """US (NASD) 30/360 day count — the Excel ``basis 0`` convention."""
    y1, m1, dd1 = d1.year, d1.month, d1.day
    y2, m2, dd2 = d2.year, d2.month, d2.day
    if m1 == 2 and is_eom(d1):
        if m2 == 2 and is_eom(d2):
            dd2 = 30
        dd1 = 30
    if dd2 == 31 and dd1 >= 30:
        dd2 = 30
    if dd1 == 31:
        dd1 = 30
    return 360 * (y2 - y1) + 30 * (m2 - m1) + (dd2 - dd1)


def year_frac(d1: date, d2: date) -> float:
    """Actual/365.25 year fraction — used for horizons, tax years and curve tenors."""
    return (d2 - d1).days / 365.25


# ---------------------------------------------------------------------------
# Bond specification
# ---------------------------------------------------------------------------
@dataclass
class BondSpec:
    maturity: date
    coupon: float = 0.0                 # annual coupon, DECIMAL (0.0425)
    freq: int = 2                       # coupons per year; 0 ⇒ zero-coupon (semiannual quasi-periods)
    day_count: str = "30/360"
    issue: date | None = None           # dated date; accrual starts here in the first period
    redemption: float = 100.0
    # [(call_date, call_price)] — a date in the past means "callable now" (continuous call)
    calls: list[tuple[date, float]] = field(default_factory=list)

    @property
    def period_freq(self) -> int:
        return self.freq if self.freq in (1, 2, 4, 12) else 2

    @property
    def coupon_per_period(self) -> float:
        return 100.0 * self.coupon / self.period_freq if self.freq in (1, 2, 4, 12) else 0.0


def default_day_count(kind: str) -> str:
    return {
        "treasury": "ACT/ACT", "tips": "ACT/ACT",
        "cd": "ACT/365",
    }.get(kind, "30/360")


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------
@lru_cache(maxsize=8192)
def _schedule_to(settle: date, end: date, freq: int) -> tuple[date, date, tuple[date, ...]]:
    """Quasi-coupon schedule ending at ``end`` (a maturity or workout date).

    Returns ``(prev, next, future)`` where ``prev <= settle < next`` and ``future`` is
    every schedule date after ``settle`` up to and including ``end``. Cached: a yield solve calls this
    once per iteration with the same arguments (it was ~90% of the time to analyze a book).
    """
    step = 12 // freq
    eom = is_eom(end)
    future: list[date] = []
    k = 0
    while True:
        d = add_months(end, -step * k, eom)
        if d <= settle:
            prev = d
            break
        future.append(d)
        k += 1
        if k > 2400:  # 200y of monthly periods — defensive
            raise ValueError("schedule overflow")
    future.reverse()
    return prev, future[0], tuple(future)


def coupon_schedule(spec: BondSpec, settle: date) -> tuple[date, date, tuple[date, ...]]:
    return _schedule_to(settle, spec.maturity, spec.period_freq)


def _period_days(spec: BondSpec, prev: date, nxt: date, settle: date) -> tuple[float, float, float]:
    """(A, DSC, E): accrued days, days settle→next, days in period, per the day count."""
    if spec.day_count == "30/360":
        e = 360.0 / spec.period_freq
        a = float(days_30_360(prev, settle))
        return a, e - a, e
    e = float((nxt - prev).days)
    a = float((settle - prev).days)
    return a, e - a, e


def accrued_interest(spec: BondSpec, settle: date) -> float:
    """Accrued interest per 100 face at ``settle`` (0 for zero-coupon)."""
    if spec.coupon_per_period == 0 or settle >= spec.maturity:
        return 0.0
    prev, nxt, _ = coupon_schedule(spec, settle)
    start = max(prev, spec.issue) if spec.issue and spec.issue > prev else prev
    if start >= settle:  # when-issued / before the dated date
        return 0.0
    if spec.day_count == "ACT/360":
        return 100.0 * spec.coupon * (settle - start).days / 360.0
    if spec.day_count == "ACT/365":
        return 100.0 * spec.coupon * (settle - start).days / 365.0
    a, _, e = _period_days(spec, prev, nxt, settle)
    if start != prev:  # first (short) period from the dated date
        if spec.day_count == "30/360":
            a = float(days_30_360(start, settle))
        else:
            a = float((settle - start).days)
    return spec.coupon_per_period * a / e


# ---------------------------------------------------------------------------
# Cash flows + the ONE pricer
# ---------------------------------------------------------------------------
@dataclass
class Workout:
    date: date
    price: float          # redemption price at the workout (100 at maturity, call price at a call)
    kind: str             # "maturity" | "call"


def cash_flows(spec: BondSpec, settle: date, workout: Workout | None = None) -> list[tuple[date, float, float]]:
    """Future cash flows per 100 face → ``[(date, amount, t_periods)]`` to ``workout``.

    ``t_periods`` is measured in coupon periods from ``settle`` (the first flow at
    DSC/E, then +1 each period) — the exponent the street formula discounts at.
    """
    wk = workout or Workout(spec.maturity, spec.redemption, "maturity")
    if settle >= wk.date:
        return []
    f = spec.period_freq
    cpn = spec.coupon_per_period
    # Coupon dates are anchored to MATURITY (a call on an off-cycle date still collects the
    # regular coupons before it); the workout date itself pays accrued-to-date + price.
    prev, nxt, dates = coupon_schedule(spec, settle)
    _, dsc, e = _period_days(spec, prev, nxt, settle)
    w = dsc / e
    out: list[tuple[date, float, float]] = []
    for i, d in enumerate(dates):
        if d > wk.date:
            break
        t = w + i
        amt = cpn
        if d == wk.date:
            amt += wk.price
        out.append((d, amt, t))
    if not out or out[-1][0] != wk.date:
        # Workout between coupon dates (e.g. a call on a non-coupon date): pay accrued stub + price.
        last_cpn = out[-1][0] if out else prev
        nxt_after = add_months(last_cpn, 12 // f, is_eom(spec.maturity))
        if spec.day_count == "30/360":
            stub = days_30_360(last_cpn, wk.date) / (360.0 / f)
            span = 1.0
            t_last = (out[-1][2] if out else w - 1) + stub
        else:
            per = (nxt_after - last_cpn).days or 1
            stub = (wk.date - last_cpn).days / per
            t_last = (out[-1][2] if out else w - 1) + stub
        out.append((wk.date, cpn * stub + wk.price, t_last))
    return out


def dirty_from_yield(spec: BondSpec, settle: date, y: float, workout: Workout | None = None) -> float:
    """Dirty price per 100 at yield ``y`` (decimal) to ``workout`` (default maturity).

    Street convention: Σ CF/(1+y/f)^t. With one flow left the money-market simple
    formula ``CF/(1 + t·y/f)`` is used (Excel ``PRICE`` with N=1) so short bonds match
    broker quotes.
    """
    flows = cash_flows(spec, settle, workout)
    if not flows:
        return 0.0
    f = spec.period_freq
    if len(flows) == 1:
        _, amt, t = flows[0]
        return amt / (1.0 + t * y / f)
    base = 1.0 + y / f
    if base <= 0:
        return float("inf")
    return sum(amt / base ** t for _, amt, t in flows)


def clean_from_yield(spec: BondSpec, settle: date, y: float, workout: Workout | None = None) -> float:
    return dirty_from_yield(spec, settle, y, workout) - accrued_interest(spec, settle)


def _solve(fn, target: float, lo: float = -0.5, hi: float = 2.0) -> float | None:
    """Root of the monotone-decreasing ``fn(y) = target`` by bisection (robust, ~1e-12)."""
    try:
        flo, fhi = fn(lo) - target, fn(hi) - target
    except (OverflowError, ZeroDivisionError):
        return None
    if flo < 0 or fhi > 0:
        return None
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        fm = fn(mid) - target
        if abs(fm) < 1e-11 or hi - lo < 1e-13:
            return mid
        if fm > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def yield_from_clean(spec: BondSpec, settle: date, clean: float, workout: Workout | None = None) -> float | None:
    """Yield (decimal) that reprices to ``clean`` per 100, to ``workout``."""
    target = clean + accrued_interest(spec, settle)
    f = spec.period_freq
    return _solve(lambda y: dirty_from_yield(spec, settle, y, workout), target, lo=-0.9 * f, hi=3.0)


# ---------------------------------------------------------------------------
# Calls / yield-to-worst
# ---------------------------------------------------------------------------
def workouts(spec: BondSpec, settle: date) -> list[Workout]:
    """Every candidate redemption: each future call date at its price, plus maturity.

    A call date already passed means "callable now" — modelled as a call on the next
    coupon date at that price (the usual continuous-call approximation).
    """
    out: list[Workout] = []
    if settle >= spec.maturity:
        return out
    live_now: tuple[date, float] | None = None
    for cd, cp in sorted(spec.calls or []):
        if cd >= spec.maturity:
            continue
        if cd <= settle:
            live_now = (cd, cp)       # latest past call date → current call price
        else:
            out.append(Workout(cd, cp, "call"))
    if live_now:
        _, nxt, _ = coupon_schedule(spec, settle)
        if nxt < spec.maturity and all(w.date != nxt for w in out):
            out.append(Workout(nxt, live_now[1], "call"))
    out.sort(key=lambda w: w.date)
    out.append(Workout(spec.maturity, spec.redemption, "maturity"))
    return out


def price_to_worst(spec: BondSpec, settle: date, y: float) -> tuple[float, Workout]:
    """Dirty price at yield ``y`` assuming the issuer acts against the holder (min over workouts)."""
    best: tuple[float, Workout] | None = None
    for wk in workouts(spec, settle):
        p = dirty_from_yield(spec, settle, y, wk)
        if best is None or p < best[0] - 1e-12:
            best = (p, wk)
    assert best is not None
    return best


# ---------------------------------------------------------------------------
# Full analytics
# ---------------------------------------------------------------------------
def analytics(spec: BondSpec, settle: date, *, clean: float | None = None, y: float | None = None) -> dict | None:
    """Price, yields, duration, convexity and DV01 for ONE bond at ``settle``.

    Give either the market ``clean`` price (per 100) or a yield ``y`` (to worst). All
    risk measures are finite differences of the price-to-worst function, so the
    duration of a callable bond reflects its call option (negative convexity shows).
    """
    if settle >= spec.maturity:
        return None
    ai = accrued_interest(spec, settle)
    wks = workouts(spec, settle)
    ytm_wk = wks[-1]
    if clean is None:
        if y is None:
            raise ValueError("need clean price or yield")
        dirty, wk = price_to_worst(spec, settle, y)
        clean = dirty - ai
    dirty = clean + ai
    ytm = yield_from_clean(spec, settle, clean, ytm_wk)
    per_wk = []
    for wk in wks:
        yy = yield_from_clean(spec, settle, clean, wk)
        per_wk.append((yy, wk))
    valid = [(yy, wk) for yy, wk in per_wk if yy is not None]
    ytw, ytw_wk = min(valid, key=lambda t: t[0]) if valid else (ytm, ytm_wk)
    ytc_first = next((yy for yy, wk in per_wk if wk.kind == "call"), None)

    if ytw is None:
        return None
    f = spec.period_freq
    h = BP
    p0, _ = price_to_worst(spec, settle, ytw)
    p_up, _ = price_to_worst(spec, settle, ytw + h)
    p_dn, _ = price_to_worst(spec, settle, ytw - h)
    eff_dur = (p_dn - p_up) / (2 * p0 * h) if p0 > 0 else None
    eff_cvx = (p_up + p_dn - 2 * p0) / (p0 * h * h) if p0 > 0 else None
    # Maturity-workout (option-free) modified duration & convexity, for reference.
    q0 = dirty_from_yield(spec, settle, ytm) if ytm is not None else None
    if ytm is not None and q0:
        q_up = dirty_from_yield(spec, settle, ytm + h)
        q_dn = dirty_from_yield(spec, settle, ytm - h)
        mod_dur = (q_dn - q_up) / (2 * q0 * h)
        cvx = (q_up + q_dn - 2 * q0) / (q0 * h * h)
        mac_dur = mod_dur * (1 + ytm / f)
    else:
        mod_dur = cvx = mac_dur = None

    annual_cpn = 100.0 * spec.coupon if spec.freq in (1, 2, 4, 12) else 0.0
    years = year_frac(settle, spec.maturity)
    return {
        "clean": clean,
        "dirty": dirty,
        "accrued": ai,
        "ytm": ytm,
        "ytw": ytw,
        "ytw_date": ytw_wk.date,
        "ytw_kind": ytw_wk.kind,
        "ytc": ytc_first,
        "current_yield": (annual_cpn / clean) if clean > 0 and annual_cpn else None,
        "mac_duration": mac_dur,
        "mod_duration": mod_dur,
        "convexity": cvx,
        "eff_duration": eff_dur,      # to worst — THE duration used for risk
        "eff_convexity": eff_cvx,
        "dv01": (eff_dur or 0.0) * dirty * h,  # $ per 100 face per 1bp
        "years_to_maturity": years,
        "callable": any(w.kind == "call" for w in wks),
        "likely_called": ytw_wk.kind == "call",
    }


def scenario_price(spec: BondSpec, settle: date, ytw: float, shift: float) -> float:
    """Dirty price after an instantaneous parallel yield shift (full reprice, to worst)."""
    return price_to_worst(spec, settle, ytw + shift)[0]


# ---------------------------------------------------------------------------
# Key-rate durations (reconcile exactly to modified duration)
# ---------------------------------------------------------------------------
KEY_TENORS = [0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 7.0, 10.0, 20.0, 30.0]


def _tenor_weights(t: float, tenors: list[float] = KEY_TENORS) -> dict[float, float]:
    """Linear (triangular) interpolation weights of time ``t`` onto the key tenors."""
    if t <= tenors[0]:
        return {tenors[0]: 1.0}
    if t >= tenors[-1]:
        return {tenors[-1]: 1.0}
    for a, b in zip(tenors, tenors[1:]):
        if a <= t <= b:
            wb = (t - a) / (b - a)
            return {a: 1.0 - wb, b: wb}
    return {tenors[-1]: 1.0}


def key_rate_durations(spec: BondSpec, settle: date, ytw: float, total_duration: float,
                       workout: Workout | None = None) -> dict[float, float]:
    """Split ``total_duration`` across key tenors by each cash flow's PV·time mass.

    ``Σ KRD == total_duration`` exactly (the reconciliation invariant). Uses the bond's
    own yield for PVs (no curve fit needed) — the standard cash-flow-mapping approach.
    """
    flows = cash_flows(spec, settle, workout)
    f = spec.period_freq
    base = 1.0 + ytw / f
    mass: dict[float, float] = {k: 0.0 for k in KEY_TENORS}
    tot = 0.0
    for _, amt, t in flows:
        ty = t / f
        pv = amt / base ** t if base > 0 else 0.0
        m = ty * pv
        tot += m
        for k, wgt in _tenor_weights(ty).items():
            mass[k] += wgt * m
    if tot <= 0:
        return {k: 0.0 for k in KEY_TENORS}
    return {k: total_duration * v / tot for k, v in mass.items()}


# ---------------------------------------------------------------------------
# Curves
# ---------------------------------------------------------------------------
def interp(points: list[tuple[float, float]], t: float) -> float | None:
    """Linear interpolation on sorted ``(tenor, value)``; flat extrapolation at the ends."""
    pts = sorted(p for p in points if p[1] is not None)
    if not pts:
        return None
    if t <= pts[0][0]:
        return pts[0][1]
    if t >= pts[-1][0]:
        return pts[-1][1]
    for (a, va), (b, vb) in zip(pts, pts[1:]):
        if a <= t <= b:
            return va + (vb - va) * (t - a) / (b - a) if b > a else va
    return pts[-1][1]


def bootstrap_zero_curve(par: list[tuple[float, float]], max_t: float = 30.0) -> list[tuple[float, float]]:
    """Semiannual zero curve (decimals) bootstrapped from a par curve (decimals).

    Bills (t ≤ 1y) are taken as zero rates; coupon tenors are bootstrapped at 0.5y
    steps on a linearly-interpolated par curve.
    """
    zeros: list[tuple[float, float]] = []
    dfs: dict[float, float] = {}
    n = int(round(max_t * 2))
    for i in range(1, n + 1):
        t = i / 2.0
        c = interp(par, t)
        if c is None:
            break
        if t <= 1.0:
            z = c
            dfs[t] = 1.0 / (1 + z / 2) ** (2 * t)
        else:
            s = sum(dfs[j / 2.0] for j in range(1, i))
            df = (1 - c / 2 * s) / (1 + c / 2)
            if df <= 0:
                break
            dfs[t] = df
            z = 2 * (df ** (-1 / (2 * t)) - 1)
        zeros.append((t, z))
    return zeros


def forward_rate(zeros: list[tuple[float, float]], start: float, tenor: float) -> float | None:
    """Semiannual forward rate from ``start`` for ``tenor`` years, off a zero curve."""
    z1 = interp(zeros, start) if start > 0 else None
    z2 = interp(zeros, start + tenor)
    if z2 is None:
        return None
    g2 = (1 + z2 / 2) ** (2 * (start + tenor))
    g1 = (1 + z1 / 2) ** (2 * start) if z1 is not None and start > 0 else 1.0
    return 2 * ((g2 / g1) ** (1 / (2 * tenor)) - 1)


# ---------------------------------------------------------------------------
# TIPS
# ---------------------------------------------------------------------------
def ref_cpi(d: date, cpi: dict[tuple[int, int], float]) -> float | None:
    """TIPS reference CPI for ``d``: CPI-U NSA of M-3 interpolated toward M-2.

    RefCPI(d) = CPI(M−3) + (day−1)/days_in_month × (CPI(M−2) − CPI(M−3)).
    ``cpi`` maps (year, month) → CPI-U NSA. Returns None when a month is missing.
    """
    def mm(k: int) -> tuple[int, int]:
        x = add_months(date(d.year, d.month, 1), -k)
        return x.year, x.month
    c3, c2 = cpi.get(mm(3)), cpi.get(mm(2))
    if c3 is None or c2 is None:
        return None
    dim = calendar.monthrange(d.year, d.month)[1]
    return c3 + (d.day - 1) / dim * (c2 - c3)


@dataclass(frozen=True)
class InflationPath:
    """Expected CPI inflation as two regimes: ``short`` for the first ``short_years`` (3y), then a
    ``long``-run average. Every TIPS / inflation-goal projection compounds along this path, so a TIPS
    maturing in 2y and one maturing in 20y each get the inflation THEY will actually see."""
    short: float
    long: float
    short_years: float = 3.0

    def factor(self, t: float) -> float:
        """CPI growth multiplier from today to ``t`` years out."""
        t = max(0.0, t)
        s = min(t, self.short_years)
        return (1 + self.short) ** s * (1 + self.long) ** (t - s)

    def avg(self, t: float) -> float:
        """Average annual inflation (geometric) from today to ``t`` years out."""
        return self.factor(t) ** (1 / t) - 1 if t > 1e-6 else self.short

    def rate_at(self, t: float) -> float:
        """Annual inflation rate during year ``t`` (0 = the coming year)."""
        return self.short if t < self.short_years else self.long


def as_path(x: "float | InflationPath | None") -> InflationPath:
    if isinstance(x, InflationPath):
        return x
    v = float(x or 0.0)
    return InflationPath(v, v)


def index_ratio(d: date, ref_cpi_dated: float, cpi: dict[tuple[int, int], float]) -> float | None:
    r = ref_cpi(d, cpi)
    return round(r / ref_cpi_dated, 5) if r and ref_cpi_dated else None


def latest_index_ratio(d: date, ref_cpi_dated: float, cpi: dict[tuple[int, int], float],
                       inflation: "float | InflationPath") -> tuple[float | None, bool]:
    """Index ratio at ``d``; if CPI isn't published that far, roll the last known month
    forward at the (short-term) ``inflation`` and flag it as projected."""
    ir = index_ratio(d, ref_cpi_dated, cpi)
    if ir is not None:
        return ir, False
    if not cpi or not ref_cpi_dated:
        return None, True
    monthly = (1 + as_path(inflation).short) ** (1 / 12)
    (ly, lm), lv = max(cpi.items())
    ext = dict(cpi)
    cur = date(ly, lm, 1)
    val = lv
    for _ in range(4):
        cur = add_months(cur, 1)
        val *= monthly
        ext[(cur.year, cur.month)] = val
    return index_ratio(d, ref_cpi_dated, ext), True


# ---------------------------------------------------------------------------
# Tax
# ---------------------------------------------------------------------------
@dataclass
class TaxRates:
    fed: float = 0.24          # marginal ordinary federal
    state: float = 0.05        # marginal state (+ local)
    niit: float = 0.0          # 3.8% net investment income tax if applicable
    ltcg: float = 0.15         # federal long-term capital gains


@dataclass
class TaxTreatment:
    fed_taxable: bool = True
    state_taxable: bool = True
    taxable_account: bool = True
    muni: bool = False
    tips: bool = False
    zero_coupon: bool = False  # OID accrues annually (taxable yearly without cash)


def interest_tax_rate(rates: TaxRates, tr: TaxTreatment) -> float:
    """Marginal rate on this bond's interest (0 in a tax-advantaged account)."""
    if not tr.taxable_account:
        return 0.0
    r = 0.0
    if tr.fed_taxable:
        r += rates.fed + rates.niit
    if tr.state_taxable:
        r += rates.state
    return r


def fully_taxable_rate(rates: TaxRates) -> float:
    return rates.fed + rates.niit + rates.state


def simple_after_tax_yield(y: float, rates: TaxRates, tr: TaxTreatment) -> float:
    """Par-bond after-tax yield ``y·(1−t)`` — used for rung-level comparisons."""
    return y * (1 - interest_tax_rate(rates, tr))


def tax_equivalent_yield(after_tax: float, rates: TaxRates) -> float:
    """Fully-taxable yield that nets the same after tax (the TEY)."""
    return after_tax / max(1e-9, 1 - fully_taxable_rate(rates))


def _irr(flows: list[tuple[float, float]], price: float, f: int) -> float | None:
    """Yield (decimal, compounding ``f``) equating ``Σ amt/(1+r/f)^t`` to ``price``."""
    def pv(r: float) -> float:
        base = 1 + r / f
        return sum(a / base ** t for t, a in flows)
    return _solve(pv, price, lo=-0.9 * f, hi=3.0)


def after_tax_yield(spec: BondSpec, settle: date, clean: float, rates: TaxRates, tr: TaxTreatment,
                    *, workout: Workout | None = None, inflation: "float | InflationPath" = 0.0) -> dict | None:
    """After-tax IRR of the bond's after-tax cash flows (decimal), and its TEY.

    Rules (simplified but faithful to US treatment):

    * Coupons taxed at the interest rate for this bond (fed/NIIT/state per flags).
    * **Premium** on a bond whose interest is taxable is amortized straight-line
      across remaining coupons (approximates the IRS constant-yield method) — each
      coupon's taxable amount is reduced, so the premium becomes a tax shield. Muni
      premium must be amortized but gives no deduction → no shield.
    * **Market discount** (coupon bond bought below redemption): ordinary income at
      redemption at the FEDERAL rate (even on a muni) + state per flags, unless under
      the de-minimis threshold (0.25 × full years) → long-term capital gain.
    * **Zero coupon / OID > 1y**: accretion taxed every year as ordinary income
      (phantom income) — no cash arrives until maturity.
    * **TIPS**: ``inflation`` (a rate or an :class:`InflationPath`) accretes principal; that accretion is
      taxed yearly at the federal rate (phantom income). Returns NOMINAL figures (pre_tax_nominal,
      after_tax) + the after-tax REAL yield — ``pre_tax`` stays the real yield the price implies.
    * Tax-advantaged account → after-tax yield == pre-tax yield (nominal for TIPS).
    """
    path = as_path(inflation)
    wk = workout or Workout(spec.maturity, spec.redemption, "maturity")
    flows = cash_flows(spec, settle, wk)
    if not flows:
        return None
    f = spec.period_freq
    ai = accrued_interest(spec, settle)
    dirty = clean + ai
    pre = yield_from_clean(spec, settle, clean, wk)
    if pre is None:
        return None
    t_int = interest_tax_rate(rates, tr)
    t_full = fully_taxable_rate(rates)
    years = year_frac(settle, wk.date)
    pre_nom = ((1 + pre) * (1 + path.avg(years)) - 1) if tr.tips else pre
    if not tr.taxable_account:
        return {"pre_tax": pre, "pre_tax_nominal": pre_nom, "after_tax": pre_nom, "tey": pre_nom, "tax_rate": 0.0,
                "after_tax_real": pre if tr.tips else None,
                "notes": ["Tax-advantaged account: no current tax on interest."]}

    notes: list[str] = []
    out_flows: list[tuple[float, float]] = []

    if tr.tips:
        # Nominal flows along the inflation path; accretion taxed yearly at the federal rate.
        infl_growth = path.factor
        prev_t = 0.0
        for _, amt, t in flows:
            ty = t / f
            g = infl_growth(ty)
            is_last = (t == flows[-1][2])
            coupon_part = spec.coupon_per_period if not is_last else amt - wk.price
            cash = coupon_part * g * (1 - t_int)
            # phantom income: principal accretion since previous flow
            accr = 100.0 * (g - infl_growth(prev_t))
            cash -= accr * (rates.fed + rates.niit if tr.fed_taxable else 0.0)
            if is_last:
                cash += wk.price * max(g, 1.0)
            out_flows.append((t, cash))
            prev_t = ty
        aty = _irr(out_flows, dirty, f)
        notes.append("TIPS inflation accretion is taxed every year (phantom income) — hold TIPS in an IRA when you can.")
        if aty is None:
            return None
        real = (1 + aty) / (1 + path.avg(years)) - 1
        return {"pre_tax": pre, "pre_tax_nominal": pre_nom, "after_tax": aty, "tey": tax_equivalent_yield(aty, rates),
                "tax_rate": t_int, "after_tax_real": real, "notes": notes}

    if tr.zero_coupon or spec.coupon_per_period == 0:
        redemption = wk.price
        if years <= 1.0:
            gain = redemption - dirty
            # T-bill / short discount: interest taxed at maturity
            out_flows = [(flows[-1][2], redemption - gain * t_int)]
        else:
            # OID accretes at the pre-tax yield; tax each accretion step (use the flow grid + yearly steps)
            n_years = int(math.floor(years))
            prev_val = dirty
            t_last = flows[-1][2]
            for k in range(1, n_years + 1):
                t = k * f
                if t >= t_last:
                    break
                val = redemption / (1 + pre / f) ** (t_last - t)
                out_flows.append((t, -(val - prev_val) * t_int))
                prev_val = val
            out_flows.append((t_last, redemption - (redemption - prev_val) * t_int))
            notes.append("Zero-coupon OID is taxed yearly as it accretes (phantom income) — better in an IRA.")
        aty = _irr(out_flows, dirty, f)
        if aty is None:
            return None
        return {"pre_tax": pre, "after_tax": aty, "tey": tax_equivalent_yield(aty, rates),
                "tax_rate": t_int, "after_tax_real": None, "notes": notes}

    # Coupon bond — the SAME two helpers price the cash-flow projection's after-tax amounts
    n = len(flows)
    premium = clean - wk.price
    shield = premium_amortization_shield(premium, n, t_int, tr)
    if premium > 0 and tr.muni and not tr.fed_taxable:
        notes.append("Muni premium is amortized with no deduction — the premium simply lowers your tax-free yield.")
    elif premium > 0:
        notes.append("Premium is amortized against coupon income (tax shield).")
    for i, (_, amt, t) in enumerate(flows):
        is_last = i == n - 1
        cpn = amt - (wk.price if is_last else 0.0)
        cash = cpn * (1 - t_int) + shield
        if is_last:
            tax, note = redemption_gain_tax(wk.price - clean, int(math.floor(years)), rates, tr)
            cash += wk.price - tax
            if note:
                notes.append(note)
        out_flows.append((t, cash))
    aty = _irr(out_flows, dirty, f)
    if aty is None:
        return None
    return {"pre_tax": pre, "after_tax": aty, "tey": tax_equivalent_yield(aty, rates),
            "tax_rate": t_int, "after_tax_real": None, "notes": notes}


def premium_amortization_shield(premium_per100: float, n_coupons: int, t_int: float, tr: TaxTreatment) -> float:
    """Tax saved on EACH remaining coupon (per 100 face) by amortizing a bond premium straight-line
    (approximates the IRS constant-yield method): taxable interest falls by premium/n per coupon, so tax
    falls by that × your rate. Zero when the interest isn't taxed (in-state munis, IRAs)."""
    if premium_per100 <= 0 or n_coupons <= 0 or not (tr.fed_taxable or tr.state_taxable):
        return 0.0
    return premium_per100 / n_coupons * t_int


def redemption_gain_tax(discount_per100: float, full_years: int, rates: TaxRates,
                        tr: TaxTreatment) -> tuple[float, str | None]:
    """Tax due AT REDEMPTION on a market discount (per 100 face; discount = redemption − what you paid).

    Market discount is ORDINARY income at the federal rate (+NIIT) — even on a tax-free muni — plus state
    if the bond's interest is state-taxable. Under the de-minimis threshold (0.25 × full years from purchase
    to maturity) it is a long-term capital gain instead. A premium (discount ≤ 0) owes nothing here: it was
    amortized against the coupons. Tax-advantaged account → 0.
    """
    if discount_per100 <= 0 or not tr.taxable_account:
        return 0.0, None
    if discount_per100 < 0.25 * max(0, full_years):
        return (discount_per100 * (rates.ltcg + rates.niit + rates.state),
                "Discount is under the de-minimis threshold → taxed as a capital gain.")
    state = rates.state if tr.state_taxable else 0.0
    return (discount_per100 * (rates.fed + rates.niit + state),
            "Market discount is taxed as ORDINARY income at redemption" + (" (even on a muni)." if tr.muni else "."))


def de_minimis_price(purchase: date, maturity: date, redemption: float = 100.0) -> float:
    """Below this purchase price, market discount is ordinary income (not a cap gain)."""
    full_years = max(0, int(math.floor(year_frac(purchase, maturity))))
    return redemption - 0.25 * full_years


# ---------------------------------------------------------------------------
# Horizon analysis
# ---------------------------------------------------------------------------
def horizon_return(spec: BondSpec, settle: date, ytw: float, horizon_years: float,
                   shift: float = 0.0, reinvest: float | None = None) -> dict | None:
    """Total return over a horizon: yields shift by ``shift`` immediately, coupons are
    reinvested at ``reinvest`` (default: the shifted yield), bond is valued at the
    horizon at the shifted yield (or redeemed if it matures/gets called first)."""
    start = price_to_worst(spec, settle, ytw)[0]
    if start <= 0:
        return None
    h_date = settle + timedelta(days=int(round(horizon_years * 365.25)))
    y1 = ytw + shift
    rr = y1 if reinvest is None else reinvest
    _, wk = price_to_worst(spec, settle, y1)
    flows = cash_flows(spec, settle, wk)
    fv = 0.0
    income = 0.0
    redeemed = False
    for d, amt, _ in flows:
        if d > h_date:
            break
        yrs_left = year_frac(d, h_date)
        fv += amt * (1 + rr / 2) ** (2 * yrs_left)
        if d == wk.date:
            redeemed = True
            income += amt - wk.price
        else:
            income += amt
    if not redeemed:
        fv += price_to_worst(spec, h_date, y1)[0] if h_date < spec.maturity else 0.0
    tr = fv / start - 1
    ann = (1 + tr) ** (1 / horizon_years) - 1 if horizon_years > 0 and tr > -1 else None
    return {"start": start, "end_value": fv, "total_return": tr, "annualized": ann, "income": income}
