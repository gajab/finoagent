"""Tests for the dealer-positioning (GEX / gamma-flip / expected-move) pure math."""
import datetime as dt

import numpy as np
import pandas as pd

from app.services.dealer_positioning_service import (
    _dollar_gex, _net_gex_at, _gamma_flip, _expected_move, compute_dealer_positioning,
)


def _c(strike, is_call, oi=1000, iv=0.30, t=0.1):
    return {"strike": strike, "t": t, "iv": iv, "oi": oi, "is_call": is_call}


class TestDollarGex:
    def test_scales_with_oi(self):
        assert _dollar_gex(0.05, 1000, 100) > _dollar_gex(0.05, 500, 100) > 0


class TestNetGex:
    def test_calls_positive_puts_negative(self):
        assert _net_gex_at([_c(100, True)], 100.0) > 0
        assert _net_gex_at([_c(100, False)], 100.0) < 0


class TestGammaFlip:
    def test_all_calls_no_crossing_is_none(self):
        assert _gamma_flip([_c(100, True), _c(105, True)], 100.0) is None

    def test_mixed_book_has_a_flip(self):
        # puts stacked below, calls above → net GEX flips sign between them
        flip = _gamma_flip([_c(105, True, oi=1000), _c(95, False, oi=1000)], 100.0)
        assert flip is not None and 80.0 <= flip <= 120.0


class TestExpectedMove:
    def test_matches_closed_form(self):
        t = 30 / 365
        em = _expected_move([_c(100, True, iv=0.20, t=t), _c(100, False, iv=0.20, t=t)], 100.0, 30)
        expected = 100 * 0.20 * np.sqrt(t)                 # ≈ 5.73
        assert abs(em["move"] - expected) < 0.2
        assert em["dte"] == 30 and abs(em["upper"] - (100 + expected)) < 0.2


class _Chain:
    def __init__(self, calls, puts):
        self.calls, self.puts = calls, puts


class _OptStock:
    """Fake with a realistic asymmetric chain (puts heavy below, calls heavy above)."""
    def __init__(self, spot=100.0):
        self._spot = spot
        self.options = [(dt.date.today() + dt.timedelta(days=d)).isoformat() for d in (30, 45)]

    def history(self, period=None, interval=None, start=None, end=None):
        idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=130, freq="D")
        base = np.linspace(self._spot - 5, self._spot, 130)
        return pd.DataFrame({"Open": base, "High": base + 1, "Low": base - 1,
                             "Close": base, "Volume": 1000.0}, index=idx)

    def option_chain(self, exp):
        strikes = np.arange(90, 111, 1.0)
        calls = pd.DataFrame({"strike": strikes, "openInterest": np.where(strikes >= 100, 800, 200),
                              "impliedVolatility": 0.30})
        puts = pd.DataFrame({"strike": strikes, "openInterest": np.where(strikes <= 100, 800, 200),
                             "impliedVolatility": 0.30})
        return _Chain(calls, puts)


class _NoOptStock(_OptStock):
    def __init__(self):
        super().__init__()
        self.options = []


class TestComputeDealerPositioning:
    def test_end_to_end_shape(self):
        out = compute_dealer_positioning(_OptStock())
        assert out is not None and out["price"] == 100.0
        assert out["net_gex"]["sign"] in ("long", "short")
        assert out["walls"]["call_wall"] and out["walls"]["put_wall"]
        assert out["walls"]["call_wall"]["strike"] >= 100 >= out["walls"]["put_wall"]["strike"]
        assert out["expected_move"]["em_30d"]["dte"] == 30
        assert out["gamma_flip"] is None or 80 <= out["gamma_flip"]["level"] <= 120

    def test_no_chain_returns_none(self):
        assert compute_dealer_positioning(_NoOptStock()) is None
