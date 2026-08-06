"""Tests for the microstructure / multi-timeframe volume-profile pure math."""
import numpy as np
import pandas as pd

from app.services.microstructure_service import (
    _low_volume_nodes, _anchored_vwap, _naked_pocs, _tf_profile,
    compute_microstructure,
)


def _synth_daily(n=260):
    """Deterministic OHLCV with real structure (trend + cycle) for shape tests."""
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="D")
    t = np.arange(n)
    base = 100 + 10 * np.sin(t / 15.0) + t * 0.05
    return pd.DataFrame({
        "Open": base + np.where(t % 2 == 0, 0.3, -0.3),
        "High": base + 1.5,
        "Low": base - 1.5,
        "Close": base,
        "Volume": 1000 + np.abs(np.sin(t / 7.0)) * 5000,
    }, index=idx)


class _FakeStock:
    """Returns the same synthetic frame for any history() call + a past earnings date."""
    def __init__(self, df):
        self._df = df

    def history(self, period=None, interval=None, start=None, end=None):
        return self._df.copy()

    @property
    def earnings_dates(self):
        d = pd.Timestamp.today().normalize() - pd.Timedelta(days=40)
        return pd.DataFrame({"EPS Estimate": [1.0]}, index=[d])

    @property
    def earnings_history(self):
        raise RuntimeError("unavailable")   # exercise the fallback path


class _EmptyStock:
    def history(self, period=None, interval=None, start=None, end=None):
        return pd.DataFrame()

    @property
    def earnings_dates(self):
        raise RuntimeError("nope")

    @property
    def earnings_history(self):
        raise RuntimeError("nope")


class TestLowVolumeNodes:
    def test_detects_a_valley_between_two_shelves(self):
        bins = [
            {"price": 1.0, "volume": 800, "pct": 80.0},
            {"price": 2.0, "volume": 100, "pct": 10.0},   # the LVN (thin)
            {"price": 3.0, "volume": 900, "pct": 90.0},
        ]
        lvns = _low_volume_nodes(bins)
        assert len(lvns) == 1 and abs(lvns[0]["price"] - 2.0) < 1e-9

    def test_monotonic_profile_has_no_lvn(self):
        bins = [{"price": float(i), "volume": i * 10, "pct": float(i * 10)} for i in range(1, 6)]
        assert _low_volume_nodes(bins) == []

    def test_empty_is_safe(self):
        assert _low_volume_nodes([]) == []


class TestAnchoredVwap:
    def test_hand_computed_value(self):
        # typical price == the level here; vwap = (10*1 + 20*3) / (1+3) = 17.5
        h = l = c = [10.0, 20.0]
        v = [1.0, 3.0]
        assert _anchored_vwap(h, l, c, v, anchor_idx=0) == 17.5

    def test_anchor_at_last_bar(self):
        h = l = c = [10.0, 20.0]
        v = [1.0, 3.0]
        assert _anchored_vwap(h, l, c, v, anchor_idx=1) == 20.0

    def test_zero_volume_is_none(self):
        assert _anchored_vwap([10, 20], [10, 20], [10, 20], [0, 0], 0) is None


class TestNakedPocs:
    def _three_sessions(self):
        # Session A ~100; Session B tight ~110 but with ONE wide bar [95,115] that
        # revisits 100; Session C ~120 (skipped as the most-recent session, spot=120).
        # >=10 bars total so it clears the thin-data guard; real dates so grouping works.
        import datetime as dt
        d1, d2, d3 = dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 7)
        rows = [
            # session A (d1) — POC ~100
            (d1, 100, 101, 99, 100, 1000),
            (d1, 100, 101, 99, 100, 1000),
            (d1, 100, 101, 99, 100, 1000),
            (d1, 100, 101, 99, 100, 1000),
            # session B (d2) — POC ~110, plus a wide low-vol bar spanning 100
            (d2, 110, 112, 108, 111, 1000),
            (d2, 110, 111, 109, 110, 1000),
            (d2, 110, 112, 108, 110, 1000),
            (d2, 110, 111, 109, 111, 1000),
            (d2, 105, 115, 95, 100, 50),
            # session C (d3) — POC ~120 (most recent, skipped)
            (d3, 120, 122, 118, 121, 800),
            (d3, 121, 122, 119, 120, 800),
            (d3, 120, 122, 118, 120, 800),
        ]
        dates = [r[0] for r in rows]
        o = [r[1] for r in rows]; h = [r[2] for r in rows]
        l = [r[3] for r in rows]; c = [r[4] for r in rows]; v = [r[5] for r in rows]
        return dates, h, l, c, v

    def test_retested_poc_is_not_naked_untested_is(self):
        dates, h, l, c, v = self._three_sessions()
        naked = _naked_pocs(dates, h, l, c, v, price=120.0, skip_recent=1)
        prices = [nk["price"] for nk in naked]
        # Session B's POC (~110) was never revisited by the later (C) session → naked.
        assert any(abs(p - 110) < 3 for p in prices)
        # Session A's POC (~100) was revisited by B's wide bar [95,115] → NOT naked.
        assert all(abs(p - 100) > 3 for p in prices)

    def test_side_and_distance_signs(self):
        dates, h, l, c, v = self._three_sessions()
        naked = _naked_pocs(dates, h, l, c, v, price=120.0, skip_recent=1)
        b = next(nk for nk in naked if abs(nk["price"] - 110) < 3)
        assert b["side"] == "below" and b["distance_pct"] < 0

    def test_thin_data_is_safe(self):
        assert _naked_pocs(["d1"], [100], [99], [100], [10], price=100.0) == []


class TestTimeframeProfile:
    def test_shape(self):
        df = _synth_daily(80)
        prof = _tf_profile(df, "Macro", "3mo", "1d")
        assert prof and {"poc", "vah", "val", "lvns", "bins", "label"} <= set(prof)
        assert prof["val"] <= prof["poc"] <= prof["vah"]

    def test_too_short_is_none(self):
        assert _tf_profile(_synth_daily(3), "x", "p", "i") is None


class TestComputeMicrostructure:
    def test_end_to_end_shape(self):
        out = compute_microstructure(_FakeStock(_synth_daily()))
        assert out is not None
        assert out["price"] is not None
        assert set(out["timeframe_profiles"]) == {"macro", "swing", "micro"}
        assert isinstance(out["naked_pocs"], list)
        avw = out["avwap"]
        # anchors that always exist from a 1y daily frame
        for key in ("ytd", "high_52w", "low_52w"):
            assert avw[key] is not None and avw[key]["value"] is not None
        assert avw["earnings"] is not None      # FakeStock supplies a past earnings date
        assert out["price_series"] and len(out["price_series"]["closes"]) > 20

    def test_no_data_returns_none(self):
        assert compute_microstructure(_EmptyStock()) is None
