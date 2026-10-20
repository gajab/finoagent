"""Walk-forward calibration + small-sample honesty helpers of the Selloff Monitor (research-driven)."""
import math

import numpy as np
import pandas as pd
import pytest

from app.services import selloff_calibration as sc
from app.services import selloff_monitor_service as sm

from test_selloff_monitor import _index_history


def test_greedy_non_overlap_nearest_first():
    order = np.array([50, 55, 80, 10, 71, 120])
    picked = sc.greedy_non_overlap(order, k=10, horizon=20, n=200)
    assert picked == [50, 80, 10, 120]                    # 55 and 71 sit within 20 sessions of a kept one
    assert all(abs(a - b) > 20 for i, a in enumerate(picked) for b in picked[i + 1:])
    assert sc.greedy_non_overlap(order, k=2, horizon=20, n=200) == [50, 80]


def test_vif_matches_orthant_approximation():
    assert sc.vif_overlap(1) == 1.0
    assert sc.vif_overlap(5) == pytest.approx(3.8, abs=0.15)
    assert sc.vif_overlap(20) == pytest.approx(14.6, abs=0.3)


def test_holm_step_down():
    assert sc.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert sc.holm([0.5]) == [0.5]


def test_episodes_and_bootstrap():
    eps = sc.episodes([10, 40, 200, 230, 500])
    assert eps == [[10, 40], [200, 230], [500]]
    groups = [np.ones(4), np.zeros(4), np.ones(4), np.zeros(4), np.ones(4)]
    lo, hi, se = sc.episode_bootstrap(groups)
    assert lo < 0.6 < hi and se > 0.1                       # clustered → wide
    flat = [np.array([1, 0, 1, 0])] * 6
    lo2, hi2, se2 = sc.episode_bootstrap(flat)
    assert lo2 == pytest.approx(0.5) and hi2 == pytest.approx(0.5) and se2 == pytest.approx(0.0)
    assert sc.episode_bootstrap(groups[:2]) == (None, None, None)


def test_brier_skill_bootstrap_ci():
    rng = np.random.default_rng(0)
    o = (rng.random(400) < 0.6).astype(float)
    p0 = np.full(400, 0.6)
    lo, hi = sc.block_bootstrap_bss(p0, p0, o, block=8)
    assert lo == pytest.approx(0.0) and hi == pytest.approx(0.0)
    perfect = np.where(o > 0, 0.95, 0.05)
    lo2, hi2 = sc.block_bootstrap_bss(perfect, p0, o, block=8)
    assert lo2 > 0.5


def _feat(n=8000, seed=7):
    index, vix = _index_history(n, seed, start="1994-01-03")       # long enough to tune on 2000–2012
    return sm.index_features(index, vix)


def test_walk_forward_shape_and_no_skill_on_noise():
    feat = _feat()
    cal = sc.walk_forward(feat, sm._FEATURES, sm._WEIGHTS, k=40, horizon=20)
    assert cal["available"] and cal["n_origins"] > 100
    assert cal["m_is_inf"] or cal["m"] in sc.M_GRID
    for h in ("h1", "h5", "h20"):
        assert cal["skill"][h]["badge"] in {"skill", "no_skill", "worse", "insufficient"}
        assert cal["skill"][h]["badge"] != "skill"         # GBM noise: the engine must not claim skill
        for reg in ("all", "stress"):
            c = cal["coverage"][h][reg]
            assert 0 <= c["analog_p10_p90"] <= 100
        assert cal["fan_factor"][h]["all"]["factor"] > 0
    assert "kth_sorted" not in sc.public(cal)


def test_walk_forward_has_no_look_ahead():
    """Tuning uses only origins ≤ 2012 whose candidates and outcomes closed by then: rewriting the
    post-2013 tail must leave the tuned scores untouched."""
    feat = _feat()
    base = sc.walk_forward(feat, sm._FEATURES, sm._WEIGHTS, k=40, horizon=20)
    f2 = feat.copy()
    tail = f2.index > pd.Timestamp("2013-03-01")
    f2.loc[tail, "close"] = f2.loc[tail, "close"] * np.linspace(1.0, 3.0, int(tail.sum()))
    alt = sc.walk_forward(f2, sm._FEATURES, sm._WEIGHTS, k=40, horizon=20)
    assert alt["m_scores"] == base["m_scores"]
    assert alt["fan_factor"]["h5"]["all"]["factor"] == base["fan_factor"]["h5"]["all"]["factor"]


def test_fan_factor_interpolation_and_calibrated_fan():
    cal = {"available": True, "fan_factor": {"h1": {"all": {"factor": 1.2}, "stress": {"factor": 1.6}},
                                             "h5": {"all": {"factor": 1.2}, "stress": {"factor": 1.6}},
                                             "h20": {"all": {"factor": 1.0}, "stress": {"factor": 1.2}}}}
    assert sm._fan_factor(cal, 3, True) == pytest.approx(1.6)
    assert sm._fan_factor(cal, 12.5, False) == pytest.approx(1.1)
    assert sm._fan_factor(None, 5, True) is None
    feat = _feat(3000)
    a = sm.find_analogs(feat, calibration=cal)
    assert a["fan_calibrated"]
    for raw, c in zip(a["fan_pct_raw"], a["fan_pct"]):
        fac = sm._fan_factor(cal, raw["h"], a["fan_factor"]["regime"] == "stress")
        assert c["p50"] == raw["p50"]
        assert c["p90"] - c["p50"] == pytest.approx(fac * (raw["p90"] - raw["p50"]), abs=2e-3)
    assert sm.find_analogs(feat)["fan_calibrated"] is False


def test_shrinkage_display_uses_tuned_prior():
    feat = _feat(3000)
    inf_cal = {"available": True, "m_is_inf": True, "m": None}
    a = sm.find_analogs(feat, calibration=inf_cal)
    assert a["stats"]["h5"]["p_up_shrunk"] == a["stats"]["h5"]["baseline_p_up"]
    m_cal = {"available": True, "m_is_inf": False, "m": 40}
    b = sm.find_analogs(feat, calibration=m_cal)
    st = b["stats"]["h5"]
    n = b["n"]
    exp = (st["p_up"] / 100 * n + 40 * st["baseline_p_up"] / 100) / (n + 40) * 100
    assert st["p_up_shrunk"] == pytest.approx(exp, abs=0.11)
    assert b["episodes"]["n"] >= 1 and sum(b["episodes"]["decades"].values()) == n
    assert set(b["era"]) == {"pre_2000", "from_2000"}
    assert b["filter_baseline"]["h5"]["n"] > 0
