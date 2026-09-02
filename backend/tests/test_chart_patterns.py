"""Tests for the chart-pattern detection engine (synthetic pivot series)."""
import numpy as np
import pandas as pd

from app.services.chart_pattern_service import (
    _zigzag, _atr, _double_top, _double_bottom, _head_shoulders, _triangle,
    _fibonacci, compute_chart_patterns,
)


def _df(prices, wig=0.4):
    # High/Low track Close (+/- a small wick) so swing peaks/troughs are STRICT local extrema —
    # deriving Open from the previous close creates 2-bar plateaus that argrelextrema skips.
    n = len(prices)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    c = np.asarray(prices, float)
    v = np.full(n, 1_000_000.0)
    return pd.DataFrame({"Open": c, "High": c + wig, "Low": c - wig, "Close": c, "Volume": v}, index=idx)


def _path(points, per=9):
    out = []
    for i in range(len(points) - 1):
        out.extend(np.linspace(points[i], points[i + 1], per, endpoint=False).tolist())
    out.append(points[-1])
    return out


class _FakeStock:
    def __init__(self, df):
        self._df = df

    def history(self, period=None, interval=None, **kw):
        return self._df


class TestZigzag:
    def test_alternates(self):
        df = _df(_path([100, 120, 108, 122, 104]))
        z = _zigzag(df, order=5)
        kinds = [p["kind"] for p in z]
        assert len(z) >= 3
        assert all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1))   # strictly alternating


class TestReversal:
    def test_double_top_bearish_target_below_neckline(self):
        df = _df(_path([100, 120, 108, 120, 104]))
        z = _zigzag(df, order=5)
        p = _double_top(z, df, float(df["Close"].values[-1]), _atr(df))
        assert p and p["type"] == "double_top" and p["direction"] == "bearish"
        assert p["breakout"]["side"] == "down"
        assert p["target"]["price"] < p["breakout"]["level"]        # target below the neckline
        assert set(("what", "where", "how_to_spot", "confirms", "invalidates")).issubset(p["education"])

    def test_double_top_rejected_when_price_back_at_highs(self):
        # peaks equal but price recovered into the upper half (a "top" pinned at the highs) → invalid
        df = _df(_path([100, 120, 108, 120, 119]))
        z = _zigzag(df, order=5)
        assert _double_top(z, df, float(df["Close"].values[-1]), _atr(df)) is None

    def test_double_top_rejected_when_peaks_unequal(self):
        # 2nd peak an 8% higher high → an uptrend, not a double top (the QQQ-style false positive)
        df = _df(_path([100, 120, 108, 130, 112]))
        z = _zigzag(df, order=5)
        assert _double_top(z, df, float(df["Close"].values[-1]), _atr(df)) is None

    def test_double_bottom_bullish_target_above_neckline(self):
        df = _df(_path([120, 100, 112, 100, 116]))
        z = _zigzag(df, order=5)
        p = _double_bottom(z, df, float(df["Close"].values[-1]), _atr(df))
        assert p and p["direction"] == "bullish" and p["breakout"]["side"] == "up"
        assert p["target"]["price"] > p["breakout"]["level"]

    def test_head_and_shoulders_bearish(self):
        df = _df(_path([100, 115, 107, 125, 107, 113, 96]))     # level necklines (both ~107)
        z = _zigzag(df, order=5)
        p = _head_shoulders(z, df, float(df["Close"].values[-1]), _atr(df), inverse=False)
        assert p and p["type"] == "head_shoulders" and p["direction"] == "bearish"
        assert p["target"]["price"] < p["breakout"]["level"]

    def test_head_shoulders_rejected_when_troughs_uneven(self):
        # neckline troughs at very different levels (90 vs 108) → absurd diagonal neckline (HD bug)
        df = _df(_path([100, 115, 90, 125, 108, 113, 96]))
        z = _zigzag(df, order=5)
        assert _head_shoulders(z, df, float(df["Close"].values[-1]), _atr(df), inverse=False) is None


class TestContinuation:
    def test_ascending_triangle_bullish(self):
        df = _df(_path([100, 120, 110, 120, 114, 120, 117]))
        z = _zigzag(df, order=4)
        p = _triangle(z, df, float(df["Close"].values[-1]), _atr(df))
        assert p and p["type"] == "ascending_triangle" and p["direction"] == "bullish"
        assert p["breakout"]["side"] == "up" and p["target"]["price"] > p["breakout"]["level"]

    def test_falling_wedge_requires_lower_highs(self):
        from app.services.chart_pattern_service import _wedge
        # lower highs AND lower lows, converging → valid falling wedge (bullish)
        df = _df(_path([100, 130, 100, 120, 96, 112, 94]))
        z = _zigzag(df, order=5)
        p = _wedge(z, df, float(df["Close"].values[-1]), _atr(df))
        assert p and p["type"] == "falling_wedge" and p["direction"] == "bullish"
        # a HIGHER high in the sequence (140 > 130) is NOT a lower-high → rejected (the GOOG bug)
        df2 = _df(_path([100, 130, 100, 140, 96, 112, 94]))
        z2 = _zigzag(df2, order=5)
        assert _wedge(z2, df2, float(df2["Close"].values[-1]), _atr(df2)) is None

    def test_wedge_stop_matches_invalidation_text(self):
        from app.services.chart_pattern_service import _wedge
        df = _df(_path([100, 130, 100, 120, 96, 112, 94]))
        z = _zigzag(df, order=5)
        p = _wedge(z, df, float(df["Close"].values[-1]), _atr(df))
        assert p and str(p["stop"]) in p["education"]["invalidates"]   # chart Stop == "invalidated at" text

    def test_cup_handle_requires_level_rims(self):
        from app.services.chart_pattern_service import _cup_handle
        # valid cup: a U from 620→558→620 (level rims) then a shallow handle
        df = _df(_path([620, 558, 620], per=60) + np.linspace(620, 612, 11).tolist())
        p = _cup_handle(None, df, float(df["Close"].values[-1]), _atr(df))
        assert p and p["type"] == "cup_handle" and p["direction"] == "bullish"
        # invalid: right rim 715 far above left rim 620 — a V-recovery/uptrend, NOT a cup (the QQQ bug)
        df2 = _df(_path([620, 558, 715], per=60) + np.linspace(715, 708, 11).tolist())
        assert _cup_handle(None, df2, float(df2["Close"].values[-1]), _atr(df2)) is None


class TestFibonacci:
    def test_levels_of_up_swing(self):
        df = _df(_path([130, 100, 150, 120]))     # dominant leg is the 100→150 up-swing
        z = _zigzag(df, order=5)
        fib = _fibonacci(z, df, 140.0)
        assert fib and fib["direction"] == "up"
        by = {round(l["ratio"], 3): l["price"] for l in fib["levels"]}
        assert abs(by[0.618] - (150 - 0.618 * 50)) < 1.0      # 61.8% retracement ≈ 119.1

    def test_pullback_recovery_is_up_swing_not_down(self):
        # high early ($300), low middle ($150), price recovered to $230 → an UP move. The old logic
        # (window high older than low → "down-swing") projected targets toward $0/negative (CRDO bug).
        df = _df(_path([100, 300, 150, 230]))
        z = _zigzag(df, order=5)
        fib = _fibonacci(z, df, float(df["Close"].values[-1]))
        assert fib and fib["direction"] == "up"
        assert all(e["price"] > 0 for e in fib["extensions"])                          # no negative targets
        assert all(e["price"] > fib["swing"]["to"]["price"] for e in fib["extensions"])  # targets above the swing

    def test_extensions_project_targets_above_the_swing(self):
        # the "price after breakout" targets: 127–262% extensions sit ABOVE the up-swing high
        df = _df(_path([130, 100, 150, 120]))
        z = _zigzag(df, order=5)
        fib = _fibonacci(z, df, 140.0)
        assert fib and fib.get("extensions")
        hi = fib["swing"]["to"]["price"]; lo = fib["swing"]["from"]["price"]; rng = hi - lo
        assert all(e["price"] > hi for e in fib["extensions"])          # all extensions above the swing high
        by = {round(e["ratio"], 3): e["price"] for e in fib["extensions"]}
        assert abs(by[1.618] - (lo + 1.618 * rng)) < 1.0               # 161.8% extension target


class TestComputeInvariants:
    def test_no_negative_or_wrong_sided_targets(self):
        # a clean double-top path through a fake stock; the whole pipeline must be sane
        df = _df(_path([90, 100, 120, 108, 120, 104]))
        out = compute_chart_patterns(_FakeStock(df))
        assert out and "series" in out and isinstance(out["patterns"], list)
        for p in out["patterns"]:
            tgt = (p.get("target") or {}).get("price")
            bk = (p.get("breakout") or {}).get("level")
            assert tgt is None or tgt > 0                      # never a negative target
            if tgt is not None and bk is not None:
                if p["breakout"]["side"] == "down":
                    assert tgt < bk                            # bearish target below trigger
                else:
                    assert tgt > bk                            # bullish target above trigger

    def test_short_history_returns_none(self):
        assert compute_chart_patterns(_FakeStock(_df([100, 101, 102]))) is None
