"""Earnings timing + post-crush VRP baseline.

Two quant fixes:
  1. The ex-earnings DIFFUSION HV (`_diffusion_hv`) strips the earnings event window so a crushed
     post-earnings IV is judged against normal vol, not the print-inflated trailing HV.
  2. `_algo_grade` relieves the VRP penalty/veto within a ≤5-trading-day post-earnings crush window
     when the clean ratio is healthy — a fairly-priced post-crush premium is no longer falsely vetoed.
"""
import math

import numpy as np
import pandas as pd

import app.services.derivative_income_service as di
import app.services.desk_review_service as dr
from app.services.quote_providers.base import OptionQuote


# ── 1) Diffusion HV strips the earnings event window ────────────────────────
def _series_with_gap(gap_pos_from_end: int = 3, gap: float = 0.14, n: int = 60):
    idx = pd.date_range("2026-06-01", periods=n, freq="B", tz="America/New_York")
    rng = np.random.default_rng(0)
    rets = rng.normal(0, 0.015, n)          # quiet ~1.5%/day diffusion
    rets[-gap_pos_from_end] = gap           # one big earnings gap
    return pd.Series(rets, index=idx)


def test_diffusion_hv_removes_the_earnings_pop():
    s = _series_with_gap()
    earn = [s.index[-3]]                      # earnings date = the gap bar
    raw = float(s.tail(30).std(ddof=1) * math.sqrt(252))
    diff = di._diffusion_hv(s, earn)
    assert diff is not None
    assert diff < raw * 0.6                   # the pop is gone → diffusion well below the inflated trailing HV
    assert 0.15 < diff < 0.32                 # ≈ the quiet 1.5%/day annualized


def test_diffusion_hv_winsor_fallback_without_earnings_date():
    s = _series_with_gap()
    diff = di._diffusion_hv(s, [])            # no earnings date → winsorize the outlier
    raw = float(s.tail(30).std(ddof=1) * math.sqrt(252))
    assert diff is not None and diff < raw * 0.6


def test_diffusion_hv_none_on_thin_data():
    idx = pd.date_range("2026-06-01", periods=8, freq="B")
    assert di._diffusion_hv(pd.Series([0.01] * 8, index=idx), []) is None


# ── 2) Post-earnings VRP relief in the grader ───────────────────────────────
def _q(k, r, mid, iv):
    return OptionQuote(strike=k, right=r, expiration="2026-10-16", bid=mid * 0.9, ask=mid * 1.1,
                       last=mid, mid=mid, iv=iv, oi=300, volume=50)


def _crushed_strangle():
    # far-OTM GLD strangle with a CRUSHED IV (iv/hv 0.62) — vetoes on trailing HV alone.
    return di._focus_strangle(_q(300, "P", 0.17, 0.27), _q(470, "C", 1.05, 0.22), spot=399.54, dte=40,
                              exp="2026-10-16", rnd=None, r=0.045, atm_iv=0.236, iv_hv_ratio=0.62,
                              richness="cheap", european=False, ticker="GLD")


_DM = {"pm": {}, "trader": {}, "risk": {}}


def _grade(**kw):
    return dr._algo_grade(_crushed_strangle(), _DM, 399.54, 5.0, atm_iv_pct=23.6, iv_rank=40,
                          beta=0.4, hv=0.38, next_earnings=None, today=None, **kw)


def test_crushed_vol_vetoes_without_post_earnings_context():
    g = _grade()
    assert any("crushed vol" in b for b in g["blocking"])     # the old behaviour: negative-VRP veto


def test_post_earnings_crush_relieves_the_veto():
    # print 2 trading days ago + a clean diffusion HV of 0.16 → IV/diffusion ≈ 1.48 → fair/rich, no veto.
    g = _grade(diffusion_hv=0.16, days_since_earnings=2)
    assert not any("crushed vol" in b for b in g["blocking"])
    assert any("post-earnings vol crush" in m for m in g["merits"])


def test_relief_only_inside_the_crush_window():
    # same trade, but the print was 10 trading days ago (> _POST_EARN_CRUSH_DAYS) → no relief, still vetoes.
    g = _grade(diffusion_hv=0.16, days_since_earnings=10)
    assert any("crushed vol" in b for b in g["blocking"])


# ── 3) Earnings-timing fallbacks that work WITHOUT lxml (the earnings_dates frame) ───
def test_info_earnings_ts_gives_past_and_next_with_times():
    # DELL-style .info: earningsTimestamp = last print (Sep 1, 16:00 ET / AMC); Start = next (Nov 27).
    ts = di._info_earnings_ts({"earningsTimestamp": 1788292800, "earningsTimestampStart": 1795809600})
    dates = {str(t.date()) for t in ts}
    assert "2026-09-01" in dates and "2026-11-27" in dates
    sep = next(t for t in ts if str(t.date()) == "2026-09-01")
    assert sep.hour >= 12                                   # afternoon ET → AMC-classifiable


def test_amc_bmo_classification():
    assert di._amc_bmo("2026-09-01T20:00:00+00:00") == "amc"    # 16:00 ET (after close)
    assert di._amc_bmo("2026-09-01T11:00:00+00:00") == "bmo"    # 07:00 ET (before open)
    assert di._amc_bmo("2026-09-01") is None                    # no time → unknown


def test_dedupe_prefers_timed_over_midnight():
    import pandas as pd
    midnight = pd.Timestamp("2026-09-01")
    timed = pd.Timestamp("2026-09-01T16:00:00-04:00")
    out = di._dedupe_earn_ts([midnight, timed])
    assert len(out) == 1 and out[0].hour != 0                   # kept the one carrying a real time
