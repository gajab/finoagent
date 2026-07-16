"""Institutional vol modeling for the hedging desk — make strike selection
data-driven instead of preference-driven.

Built in dependency order:
  1. SVI smile calibration per expiry (Gatheral *raw* parameterization), so we
     fit a smooth, arbitrage-checked curve to a noisy options chain and can read
     a synthetic IV at ANY strike (even between listed strikes).
  2. Risk-neutral density (RND) via Breeden-Litzenberger off the SVI call curve:
     f(K) = e^{rT} ∂²C/∂K².  A smooth SVI curve gives a clean 2nd derivative,
     where the raw chain would be too noisy.
  3. Hedge probabilities — P(breach the floor), P(hit the cap), P(finish in the
     protected band), and the risk-neutral expected terminal price. This is what
     turns "I want a −20% floor" into "−20% carries an 18% breach probability;
     for a 10% target use −27%."

QuantLib (Heston calibration, Vanna-Volga greeks) layers on top of this; the SVI
and RND layers are pure numpy/scipy so they ship without an image rebuild.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.optimize import least_squares

_SQRT2 = math.sqrt(2.0)


def _norm_cdf(x: np.ndarray | float) -> np.ndarray | float:
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, dtype=float) / _SQRT2))


def _bs_call(F: float, K: np.ndarray, T: float, sigma: np.ndarray, r: float) -> np.ndarray:
    """Black-76 call on the forward F (discounted), vectorized over K/sigma."""
    K = np.asarray(K, dtype=float)
    sigma = np.asarray(sigma, dtype=float)
    disc = math.exp(-r * T)
    out = np.maximum(F - K, 0.0) * disc  # intrinsic fallback
    m = (sigma > 1e-9) & (T > 0)
    if np.any(m):
        sq = sigma[m] * math.sqrt(T)
        d1 = (np.log(F / K[m]) + 0.5 * sq * sq) / sq
        d2 = d1 - sq
        out[m] = disc * (F * _norm_cdf(d1) - K[m] * _norm_cdf(d2))
    return out


# ---------------------------------------------------------------------------
# 1. SVI smile (Gatheral raw): total variance w(k) = a + b(ρ(k−m) + √((k−m)²+σ²))
#    where k = ln(K/F) is log-moneyness and w = σ_BS² · T.
# ---------------------------------------------------------------------------

@dataclass
class SVISmile:
    a: float
    b: float
    rho: float
    m: float
    sigma: float
    forward: float
    T: float
    r: float
    rmse: float = 0.0          # fit error in vol points
    n_quotes: int = 0
    arb_free: bool = True      # butterfly (density ≥ 0) check
    arb_note: str = ""

    def total_variance(self, k: np.ndarray | float) -> np.ndarray | float:
        k = np.asarray(k, dtype=float)
        return self.a + self.b * (self.rho * (k - self.m)
                                  + np.sqrt((k - self.m) ** 2 + self.sigma ** 2))

    def iv(self, K: np.ndarray | float) -> np.ndarray | float:
        """Synthetic Black implied vol at strike(s) K."""
        K = np.asarray(K, dtype=float)
        k = np.log(K / self.forward)
        w = np.maximum(self.total_variance(k), 1e-8)
        return np.sqrt(w / self.T)


def _forward_from_parity(strikes, call_mids, put_mids, spot, r, T) -> float:
    """Forward implied by put-call parity at the strike nearest the money
    (C − P = e^{−rT}(F − K) ⇒ F = K + e^{rT}(C − P)). Falls back to spot·e^{rT}."""
    best = None
    for K, c, p in zip(strikes, call_mids, put_mids):
        if c is None or p is None or c <= 0 or p <= 0:
            continue
        d = abs(K - spot)
        if best is None or d < best[0]:
            best = (d, K, c, p)
    if best is None:
        return spot * math.exp(r * T)
    _, K, c, p = best
    return K + math.exp(r * T) * (c - p)


def calibrate_svi(strikes, ivs, spot: float, dte: int,
                  r: float = 0.045, forward: Optional[float] = None,
                  call_mids=None, put_mids=None,
                  moneyness_band: tuple[float, float] = (0.70, 1.30)) -> Optional[SVISmile]:
    """Fit a raw-SVI smile to one expiry's (strike, iv) quotes.

    Only liquid strikes inside `moneyness_band` (K/spot) with a positive IV are
    used, to keep deep-wing noise out of the fit.
    """
    strikes = np.asarray(strikes, dtype=float)
    ivs = np.asarray(ivs, dtype=float)
    T = max(dte, 1) / 365.0

    if forward is None:
        forward = (_forward_from_parity(strikes, call_mids, put_mids, spot, r, T)
                   if call_mids is not None and put_mids is not None
                   else spot * math.exp(r * T))

    lo, hi = moneyness_band
    mask = (ivs > 1e-3) & (strikes > 0) & (strikes >= lo * spot) & (strikes <= hi * spot)
    if mask.sum() < 5:
        return None
    K = strikes[mask]
    iv = ivs[mask]
    k = np.log(K / forward)             # log-moneyness
    w = (iv ** 2) * T                   # observed total variance

    # Raw-SVI residuals. params = [a, b, rho, m, sigma]
    def resid(p):
        a, b, rho, m, sig = p
        model = a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sig ** 2))
        return model - w

    a0 = max(float(np.min(w)) * 0.5, 1e-4)
    p0 = [a0, 0.1, -0.3, 0.0, 0.1]      # equity skew prior: rho < 0
    bounds = ([0.0, 1e-6, -0.999, -1.0, 1e-4],
              [float(np.max(w)) + 1e-3, 5.0, 0.999, 1.0, 2.0])
    try:
        sol = least_squares(resid, p0, bounds=bounds, method="trf", max_nfev=4000)
    except Exception:  # noqa: BLE001
        return None

    a, b, rho, m, sig = (float(x) for x in sol.x)
    rmse_w = float(np.sqrt(np.mean(resid(sol.x) ** 2)))
    # convert variance-rmse to an approximate vol-point rmse at the money
    atm_w = max(a + b * (rho * (-m) + math.sqrt(m * m + sig * sig)), 1e-8)
    rmse_iv = 0.5 * rmse_w / math.sqrt(atm_w * T) if atm_w > 0 else rmse_w

    smile = SVISmile(a=a, b=b, rho=rho, m=m, sigma=sig, forward=float(forward),
                     T=T, r=r, rmse=rmse_iv, n_quotes=int(mask.sum()))
    _check_butterfly(smile)
    return smile


def _check_butterfly(smile: SVISmile) -> None:
    """Gatheral–Jacquier g(k) ≥ 0 butterfly (no-arb) condition over a strike grid.
    Flags (does not reject) — a slightly negative density is usually numerical."""
    k = np.linspace(-0.6, 0.6, 121)
    w = np.asarray(smile.total_variance(k), dtype=float)
    h = k[1] - k[0]
    wp = np.gradient(w, h)
    wpp = np.gradient(wp, h)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = (1 - 0.5 * k * wp / w) ** 2 - 0.25 * (wp ** 2) * (1.0 / w + 0.25) + 0.5 * wpp
    g = g[np.isfinite(g)]
    worst = float(np.min(g)) if g.size else 0.0
    smile.arb_free = worst > -1e-3
    smile.arb_note = "" if smile.arb_free else f"butterfly g(k) min={worst:.4f} (<0)"


# ---------------------------------------------------------------------------
# 2 & 3. Risk-neutral density (Breeden-Litzenberger) and hedge probabilities
# ---------------------------------------------------------------------------

@dataclass
class RND:
    K: np.ndarray            # strike grid
    pdf: np.ndarray          # risk-neutral density f(K)
    cdf: np.ndarray          # P(S_T ≤ K)
    forward: float
    T: float
    smile: SVISmile = field(repr=False)

    def prob_below(self, level: float) -> float:
        return float(np.interp(level, self.K, self.cdf))

    def prob_above(self, level: float) -> float:
        return 1.0 - self.prob_below(level)

    def strike_for_prob_below(self, p: float) -> float:
        """Inverse: the strike with a target P(S_T ≤ K) — e.g. a 10% breach floor."""
        p = min(max(p, 1e-4), 1 - 1e-4)
        return float(np.interp(p, self.cdf, self.K))


def risk_neutral_density(smile: SVISmile, n: int = 801,
                         width: float = 0.6) -> RND:
    """Breeden-Litzenberger: f(K) = e^{rT} ∂²C/∂K² off the smooth SVI call curve.
    Grid spans forward·e^{±width} in log space."""
    F, T, r = smile.forward, smile.T, smile.r
    K = F * np.exp(np.linspace(-width, width, n))
    iv = np.asarray(smile.iv(K), dtype=float)
    C = _bs_call(F, K, T, iv, r)
    # second derivative on the (non-uniform) grid
    dC = np.gradient(C, K)
    d2C = np.gradient(dC, K)
    pdf = np.maximum(math.exp(r * T) * d2C, 0.0)
    area = np.trapezoid(pdf, K)
    if area > 0:
        pdf = pdf / area                      # renormalize (numerical safety)
    cdf = np.concatenate([[0.0], np.cumsum(0.5 * (pdf[1:] + pdf[:-1]) * np.diff(K))])
    cdf = np.clip(cdf, 0.0, 1.0)
    return RND(K=K, pdf=pdf, cdf=cdf, forward=F, T=T, smile=smile)


def rnd_curve(rnd: RND, spot: float, lo: float = -45.0, hi: float = 45.0,
              step: float = 1.5) -> dict:
    """Downsampled RND curve on a %-of-spot grid, for charting and client-side
    probability recompute when the user edits strikes.
      pct          — % move from spot at expiry
      cdf_pct      — P(S_T ≤ spot·(1+pct/100)) in %
      pdf_per_pct  — probability mass (in %) per 1% move bucket
    """
    pct = np.arange(lo, hi + 1e-9, step)
    K = spot * (1 + pct / 100.0)
    cdf = np.interp(K, rnd.K, rnd.cdf, left=0.0, right=1.0)
    pdf_k = np.interp(K, rnd.K, rnd.pdf, left=0.0, right=0.0)
    pdf_per_pct = pdf_k * spot / 100.0
    return {
        "pct": [round(float(x), 1) for x in pct],
        "cdf_pct": [round(float(x) * 100, 2) for x in cdf],
        "pdf_per_pct": [round(float(x) * 100, 3) for x in pdf_per_pct],
    }


def hedge_probabilities(rnd: RND, spot: float,
                        floor_strike: Optional[float],
                        cap_strike: Optional[float],
                        buffer_bottom: Optional[float] = None) -> dict:
    """Market-implied probabilities for a hedge's key levels, from the RND.
    (Surface-level fields — forward, arb-free, fit error — live in the response's
    `rnd` summary, not repeated on every structure.)"""
    out: dict = {}
    if floor_strike:
        out["p_breach_floor_pct"] = round(rnd.prob_below(floor_strike) * 100, 1)
        out["floor_strike"] = round(floor_strike, 2)
    if cap_strike:
        out["p_hit_cap_pct"] = round(rnd.prob_above(cap_strike) * 100, 1)
        out["cap_strike"] = round(cap_strike, 2)
    if floor_strike and cap_strike:
        out["p_in_band_pct"] = round((rnd.prob_below(cap_strike)
                                      - rnd.prob_below(floor_strike)) * 100, 1)
    if buffer_bottom:
        out["p_below_buffer_pct"] = round(rnd.prob_below(buffer_bottom) * 100, 1)
    return out


# ---------------------------------------------------------------------------
# 4. Vanna-Volga smile risk — the higher-order exposure that bites a short-vol
#    collar when the VIX gaps. Pure Black-Scholes closed form (no QuantLib).
#       vega  = F·e^{−rT}·φ(d1)·√T        (price move per 1.00 vol)
#       vanna = −e^{−rT}·φ(d1)·d2/σ       (∂Δ/∂σ = ∂Vega/∂S)
#       volga = vega·d1·d2/σ              (∂Vega/∂σ)
# ---------------------------------------------------------------------------

def _phi(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def _leg_vol_greeks(F: float, K: float, T: float, sigma: float, r: float) -> tuple[float, float, float]:
    """Per-share (vega, vanna, volga) for one option, in *price per 1.00 vol* units."""
    if sigma <= 1e-6 or T <= 0 or K <= 0 or F <= 0:
        return 0.0, 0.0, 0.0
    sq = sigma * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sq * sq) / sq
    d2 = d1 - sq
    disc = math.exp(-r * T)
    vega = F * disc * _phi(d1) * math.sqrt(T)
    vanna = -disc * _phi(d1) * d2 / sigma
    volga = vega * d1 * d2 / sigma
    return vega, vanna, volga


def structure_smile_risk(legs: list[dict], spot: float, dte: int, forward: Optional[float] = None,
                         r: float = 0.045, vix_shock_pts: float = 10.0) -> dict:
    """Net Vanna/Volga of a structure and the P&L hit from a VIX spike.

    For a short-vol structure (collar / covered call) a vol spike inflates the
    short legs → a mark-to-market loss and higher margin. We estimate it as
    ΔV ≈ vega·Δσ + ½·volga·Δσ² over a `vix_shock_pts`-point IV jump.
    """
    T = max(dte, 1) / 365.0
    F = forward if forward else spot * math.exp(r * T)
    net_vega = net_vanna = net_volga = 0.0
    for lg in legs:
        iv = lg.get("iv")
        if not iv:
            continue
        sigma = iv / 100.0 if iv > 3 else float(iv)   # accept % or decimal
        qty = (1 if lg["action"] == "BUY" else -1) * lg["contracts"] * 100.0
        veg, van, vol = _leg_vol_greeks(F, float(lg["strike"]), T, sigma, r)
        net_vega += qty * veg
        net_vanna += qty * van
        net_volga += qty * vol
    dsig = vix_shock_pts / 100.0
    pnl_on_spike = net_vega * dsig + 0.5 * net_volga * dsig * dsig
    return {
        "net_vega": round(net_vega, 1),                       # $ per 1.00 (100 vol-pt) move
        "net_vanna": round(net_vanna, 2),
        "net_volga": round(net_volga, 1),
        "vix_shock_pts": vix_shock_pts,
        "pnl_on_vol_spike": round(pnl_on_spike, 0),           # $ P&L if IV jumps the shock
        "short_vol": net_vega < 0,                            # True = hurt by a VIX spike
    }


# ---------------------------------------------------------------------------
# 5. Heston stochastic-vol calibration (QuantLib) — a panic-aware view of the
#    smile. Lazy import + best-effort: returns None if QuantLib is absent or the
#    fit fails, so the rest of the desk keeps working.
# ---------------------------------------------------------------------------

def calibrate_heston(strikes, ivs, spot: float, dte: int, r: float = 0.045,
                     forward: Optional[float] = None,
                     moneyness_band: tuple[float, float] = (0.80, 1.20)) -> Optional[dict]:
    try:
        import QuantLib as ql
    except Exception:  # noqa: BLE001
        return None
    try:
        strikes = np.asarray(strikes, float); ivs = np.asarray(ivs, float)
        T = max(dte, 1) / 365.0
        lo, hi = moneyness_band
        mask = (ivs > 1e-3) & (strikes >= lo * spot) & (strikes <= hi * spot)
        if mask.sum() < 6:
            return None
        K = strikes[mask]; iv = ivs[mask]

        today = ql.Date.todaysDate()
        ql.Settings.instance().evaluationDate = today
        dc = ql.Actual365Fixed()
        cal = ql.NullCalendar()
        rTS = ql.YieldTermStructureHandle(ql.FlatForward(today, r, dc))
        qTS = ql.YieldTermStructureHandle(ql.FlatForward(today, 0.0, dc))
        S0 = ql.QuoteHandle(ql.SimpleQuote(spot))
        # Single expiry can't identify mean-reversion κ (that needs the term
        # structure), so we PIN κ=2.0 and calibrate {θ, σ_v, ρ, v0}. This keeps
        # the desk-relevant reads — vol-of-vol and spot/vol correlation — sane.
        # Single expiry can't identify mean-reversion κ or pin the level AND shape,
        # so we PIN κ=2 and v0=ATM-variance and fit the smile *shape* {θ, σ_v, ρ}.
        # Calibrate on IMPLIED-VOL error (not relative price — which explodes on the
        # cheap wings) so all strikes weigh sensibly.
        atm_var = float(np.interp(spot, K, iv) ** 2) if K.size else 0.04
        kappa_fixed = 2.0
        process = ql.HestonProcess(rTS, qTS, S0, atm_var, kappa_fixed, atm_var, 0.8, -0.6)
        model = ql.HestonModel(process)
        engine = ql.AnalyticHestonEngine(model)
        p = ql.Period(int(max(dte, 1)), ql.Days)
        err_type = ql.BlackCalibrationHelper.ImpliedVolError
        helpers = []
        for k, v in zip(K, iv):
            h = ql.HestonModelHelper(p, cal, spot, float(k),
                                     ql.QuoteHandle(ql.SimpleQuote(float(v))),
                                     rTS, qTS, err_type)
            h.setPricingEngine(engine)
            helpers.append(h)
        lm = ql.LevenbergMarquardt(1e-8, 1e-8, 1e-8)
        endc = ql.EndCriteria(800, 80, 1e-8, 1e-8, 1e-8)
        # params order: [theta, kappa, sigma, rho, v0] → fix κ (1) and v0 (4).
        model.calibrate(helpers, lm, endc, ql.NoConstraint(), [],
                        [False, True, False, False, True])
        theta, kappa, sigma_v, rho, v0 = model.params()
        rmse = float(np.sqrt(np.mean([h.calibrationError() ** 2 for h in helpers]))) * 100
        feller_ok = 2 * kappa * theta > sigma_v * sigma_v   # 2κθ > σ_v² ⇒ variance stays > 0
        # Sanity gate — reject a degenerate fit rather than surface nonsense.
        if not (1e-4 < theta < 1.0 and 1e-4 < v0 < 1.0
                and 0.01 < sigma_v < 4.0 and rmse < 3.0):
            return None
        return {
            "v0": round(float(v0), 4), "kappa": round(float(kappa), 3),
            "theta": round(float(theta), 4), "sigma_v": round(float(sigma_v), 3),
            "rho": round(float(rho), 3),
            "vol_of_vol": round(float(sigma_v), 3),
            "long_run_vol_pct": round(math.sqrt(max(theta, 0)) * 100, 1),
            "spot_vol_corr": round(float(rho), 3),
            "feller_ok": bool(feller_ok),
            "rmse_vol_pts": round(rmse, 2),
        }
    except Exception:  # noqa: BLE001
        return None
