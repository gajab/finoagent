"""Tests for the Qullamäggie momentum-breakout engine (synthetic daily/intraday frames)."""
import json

import numpy as np
import pandas as pd

from app.services.qullamaggie_service import (
    compute_qullamaggie_setups, _adr_pct, _find_base, _detect_ep, _ema,
)


def _daily(prices, wig=0.03, vol=1e6, start="2025-06-01"):
    n = len(prices)
    idx = pd.date_range(start, periods=n, freq="B")
    c = np.asarray(prices, float)
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"Open": o, "High": c * (1 + wig), "Low": c * (1 - wig),
                         "Close": c, "Volume": np.full(n, vol)}, index=idx)


def _intraday_5m(orh, orl, last, day="2026-09-16 09:30"):
    p = [orl, orh, (orh + orl) / 2, orh, orl, (orh + orl) / 2] + list(np.linspace((orh + orl) / 2, last, 20))
    idx = pd.date_range(day, periods=len(p), freq="5min")
    c = np.asarray(p, float)
    return pd.DataFrame({"Open": c, "High": c + 0.05, "Low": c - 0.05, "Close": c,
                         "Volume": np.full(len(p), 1000.0)}, index=idx)


class _Fake:
    calendar = {}

    def __init__(self, d1, d5=None):
        self._d1, self._d5 = d1, d5

    def history(self, period=None, interval=None, start=None, end=None, **kw):
        if interval == "1d":
            return self._d1
        if interval == "5m":
            return self._d5
        return None


def _breakout_prices():
    ramp = list(np.linspace(50, 100, 120))                 # +100% prior move
    base = list(100 + 2 * np.sin(np.linspace(0, 6, 14)))   # tight base ~98–102
    return ramp + base


class TestQualification:
    def test_leader_in_a_base_qualifies_and_fires_a_breakout(self):
        out = compute_qullamaggie_setups(_Fake(_daily(_breakout_prices())))
        assert out is not None
        q = out["qualification"]
        assert q["is_candidate"] and q["grade"] in ("A", "B")
        assert {c["key"] for c in q["checks"]} == {"prior_move", "adr", "ma_stack", "near_highs", "base", "volume"}
        vol = next(c for c in q["checks"] if c["key"] == "volume")
        assert vol["status"] in ("pass", "warn")   # volume is a SOFT confirmation — it never fails/gates
        brk = [s for s in out["setups"] if s["type"] == "qm_breakout"]
        assert brk, "a qualified leader with a tight base must produce a breakout setup"
        s = brk[0]
        assert s["direction"] == "long" and s["style"] == "qullamaggie"
        assert s["stop"]["level"] < s["entry"]["level"] < s["targets"][0]["level"]   # long geometry
        assert s["risk_reward"] and s["risk_reward"] >= 1.5

    def test_volume_dryup_and_surge_reads_as_confirmation(self):
        # a quiet base (volume drying up) then a surge on the breakout bar = the ideal volume picture
        d = _daily(_breakout_prices())
        n = len(d)
        vol = np.full(n, 2e6)
        vol[-14:] = 6e5          # base: volume dries up well below the prior run
        vol[-1] = 5e6            # breakout bar: volume expands vs the 20-day average
        d["Volume"] = vol
        out = compute_qullamaggie_setups(_Fake(d))
        v = next(c for c in out["qualification"]["checks"] if c["key"] == "volume")
        assert v["status"] == "pass"
        brk = next(s for s in out["setups"] if s["type"] == "qm_breakout")
        assert any("volume" in e.lower() for e in brk["evidence"])   # dry-up shown in the card

    def test_laggard_does_not_qualify_and_has_no_long_setup(self):
        chop = list(80 + 3 * np.sin(np.linspace(0, 30, 260)))
        out = compute_qullamaggie_setups(_Fake(_daily(chop)))
        assert out is not None
        assert not out["qualification"]["is_candidate"]
        assert not [s for s in out["setups"] if s["direction"] == "long"]

    def test_low_adr_bluechip_never_qualifies(self):
        # A strong, near-highs, stacked uptrend but with a tiny daily range (ADR ~1.4%, KO-like):
        # ADR is Kullamägi's first filter, so it must be disqualified whatever its score.
        out = compute_qullamaggie_setups(_Fake(_daily(_breakout_prices(), wig=0.007)))
        assert out["adr_pct"] and out["adr_pct"] < 3.0
        adr_check = next(c for c in out["qualification"]["checks"] if c["key"] == "adr")
        assert adr_check["status"] == "fail"
        assert out["qualification"]["is_candidate"] is False
        assert not [s for s in out["setups"] if s["direction"] == "long"]

    def test_live_opening_range_high_refines_the_entry(self):
        d1 = _daily(_breakout_prices())
        pivot = float(d1["High"].tail(20).max())
        # ORH just above the base pivot → the breakout is live; entry rides the ORH, stop tightens to the day low
        out = compute_qullamaggie_setups(_Fake(d1, _intraday_5m(orh=pivot * 1.01, orl=pivot * 0.995, last=pivot * 1.02)))
        s = next(s for s in out["setups"] if s["type"] == "qm_breakout")
        assert out["opening_range"] and out["meta"]["has_intraday"]
        assert s["entry"]["level"] >= pivot                # entry sits at/above the opening-range high
        # the intraday day-low stop is tighter than the wide base-low stop
        assert (s["entry"]["level"] - s["stop"]["level"]) < (pivot - float(d1["Low"].tail(14).min()))


class TestSetups:
    def test_parabolic_short_fires_on_a_climax_with_a_target_below_entry(self):
        up = list(np.linspace(60, 100, 150))
        d = _daily(up + [108, 118, 128, 135])
        d.iloc[-1, d.columns.get_loc("Open")] = 138       # last bar reverses (red, off highs)
        d.iloc[-1, d.columns.get_loc("High")] = 140
        d.iloc[-1, d.columns.get_loc("Close")] = 132
        out = compute_qullamaggie_setups(_Fake(d))
        shorts = [s for s in out["setups"] if s["type"] == "qm_parabolic_short"]
        assert shorts, "a parabolic climax printing its first break must produce a short"
        s = shorts[0]
        assert s["direction"] == "short" and s["regime_fit"] == "counter_regime"
        assert s["targets"][0]["level"] < s["entry"]["level"] < s["stop"]["level"]   # short geometry
        assert s["risk_reward"] and s["risk_reward"] >= 1.5

    def test_episodic_pivot_detects_a_gap_on_volume(self):
        qbase = list(40 + 0.5 * np.sin(np.linspace(0, 20, 200)))
        d = _daily(qbase + [52] + list(np.linspace(52, 55, 8)))
        gi = len(d) - 1 - 8
        d.iloc[gi, d.columns.get_loc("Open")] = 48        # +20% gap vs the ~40 base
        d.iloc[gi, d.columns.get_loc("Volume")] = 5e6     # on a big volume surge
        out = compute_qullamaggie_setups(_Fake(d))
        assert out["metrics"]["episodic_pivot"] is not None
        eps = [s for s in out["setups"] if s["type"] == "qm_episodic_pivot"]
        assert eps and eps[0]["direction"] == "long"
        assert eps[0]["stop"]["level"] < eps[0]["entry"]["level"] < eps[0]["targets"][0]["level"]


class TestRobustness:
    def test_payload_is_json_serializable(self):
        out = compute_qullamaggie_setups(_Fake(_daily(_breakout_prices())))
        json.dumps(out)                                    # must not raise (no numpy types leak)

    def test_too_little_history_returns_none(self):
        assert compute_qullamaggie_setups(_Fake(_daily(list(np.linspace(10, 12, 20))))) is None

    def test_adr_and_base_primitives(self):
        prices = _breakout_prices()
        d = _daily(prices)
        h, l, c = d["High"].values, d["Low"].values, d["Close"].values
        adr = _adr_pct(h, l)
        assert adr and 5.0 <= adr <= 7.5                   # High/Low = 1.03/0.97 → ~6.2%
        base = _find_base(h, l, c, adr)
        assert base and base["days"] >= 5 and base["depth_pct"] < 30
        assert float(_ema(c, 10)[-1]) > float(_ema(c, 20)[-1])   # rising trend → 10EMA above 20EMA
