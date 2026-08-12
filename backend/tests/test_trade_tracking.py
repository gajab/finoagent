"""Tests for the trade tracking & management engine (pure functions + verdict logic)."""
import numpy as np
import pandas as pd

from app.services.trade_tracking_service import (
    _session_vwap_bands, _rsi_last, _rejection_candle, _cvd, _atr,
    _position_math, _entry_checks, _decide_entry, _decide_exit, _decide_neutral,
    _mk, _headline, VERDICT_LABEL,
)


def _intraday_df(highs, lows, closes, opens=None, vols=None, day="2026-08-11"):
    n = len(closes)
    idx = pd.date_range(f"{day} 09:30", periods=n, freq="5min")
    return pd.DataFrame({
        "Open": opens if opens is not None else closes,
        "High": highs, "Low": lows, "Close": closes,
        "Volume": vols if vols is not None else [1000] * n,
    }, index=idx)


def _snap(spot=100.0, **over):
    base = {
        "as_of": "now", "spot": spot, "atr_15m": 0.5,
        "vwap": {"vwap": 99.0, "sigma": 0.5, "z": 2.2, "upper_2": 100.0, "lower_2": 98.0},
        "rsi_1h": 63.0,
        "choch_5m": {"last_event": {"direction": "bearish", "type": "CHOCH", "level": 100.5}},
        "choch_15m": {"last_event": {"direction": "bearish", "type": "CHOCH", "level": 100.7}, "trend": "down"},
        "choch_1h": {"trend": "down"},
        "trend_daily": {"trend": "down"},
        "cvd": {"divergence": "bearish", "last": -100},
        "volume": {"ratio": 1.8},
        "gamma": {"net_gex": {"sign": "long"}, "gamma_flip": {"level": 102, "side": "above"},
                  "walls": {"call_wall": {"strike": 105}}, "gamma_levels": {"call_resistance": {"strike": 105}}},
    }
    base.update(over)
    return base


_SHORT = {"ticker": "T", "direction": "short", "instrument": "equity", "setup_type": "mean_reversion_fade",
          "status": "watching", "entry_low": 99.0, "entry_high": 101.0, "entry_level": 100.0,
          "stop_level": 104.0, "target_levels": [96.0, 92.0], "setup_snapshot": {}}


# ---------------------------------------------------------------------------
class TestPrimitives:
    def test_rsi_bounds_and_direction(self):
        up = _rsi_last(np.arange(1, 60, dtype=float))
        down = _rsi_last(np.arange(60, 1, -1, dtype=float))
        assert up is not None and up > 70
        assert down is not None and down < 30

    def test_atr_positive(self):
        df = _intraday_df([101, 102, 103], [99, 100, 101], [100, 101, 102])
        assert _atr(df, period=2) > 0

    def test_vwap_bands_ordered(self):
        closes = [98, 99, 100, 101, 102, 103, 104, 105]
        df = _intraday_df([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes)
        vb = _session_vwap_bands(df, spot=105.0)
        assert vb is not None and vb["sigma"] > 0
        assert vb["lower_2"] < vb["lower_1"] < vb["vwap"] < vb["upper_1"] < vb["upper_2"]
        assert vb["z"] > 0                      # spot above VWAP

    def test_rejection_wick_short_detected(self):
        # last bar pierces 105 but closes back below with a big upper wick
        df = _intraday_df(highs=[101, 102, 106], lows=[99, 100, 99],
                          closes=[100, 101, 100.5], opens=[100, 101, 100.2])
        rej = _rejection_candle(df, level=105.0, side="short")
        assert rej is not None and rej["wick_pct"] > 50

    def test_rejection_wick_absent_when_closes_through(self):
        df = _intraday_df(highs=[101, 102, 106], lows=[99, 100, 104],
                          closes=[100, 101, 105.6], opens=[100, 101, 104.2])
        assert _rejection_candle(df, level=105.0, side="short") is None

    def test_cvd_returns_shape(self):
        n = 30
        closes = list(np.linspace(100, 110, n))
        df = _intraday_df([c + 0.5 for c in closes], [c - 0.5 for c in closes], closes,
                          opens=[c - 0.2 for c in closes])
        cvd = _cvd(df)
        assert cvd is not None and set(("last", "slope_10", "divergence")).issubset(cvd)


# ---------------------------------------------------------------------------
class TestPositionMath:
    def test_long_pnl_and_r_multiple(self):
        tr = {"direction": "long", "entry_level": 100.0, "stop_level": 95.0, "target_levels": [120.0],
              "executed_price": 100.0, "executed_qty": 10}
        pm = _position_math(tr, spot=110.0)
        assert pm["open_pnl_pct"] == 10.0
        assert pm["open_pnl"] == 100.0
        assert pm["r_multiple"] == 2.0               # (110-100)/(100-95)
        assert round(pm["dist_to_t1_pct"], 2) == 9.09

    def test_short_pnl_sign(self):
        tr = {"direction": "short", "entry_level": 100.0, "stop_level": 105.0, "target_levels": [90.0],
              "executed_price": 100.0, "executed_qty": 5}
        pm = _position_math(tr, spot=90.0)
        assert pm["open_pnl_pct"] == 10.0            # short gains as price falls
        assert pm["open_pnl"] == 50.0
        assert pm["r_multiple"] == 2.0


# ---------------------------------------------------------------------------
class TestEntryVerdict:
    def test_execute_when_confirmations_stack(self):
        snap = _snap(spot=100.0)
        checks = _entry_checks(_SHORT, snap, frames={})
        by = {c["key"]: c for c in checks}
        assert by["at_entry"]["status"] == "pass"
        assert by["level_integrity"]["status"] == "pass"
        assert by["ltf_choch"]["status"] == "pass"
        d = _decide_entry(checks, _position_math(_SHORT, 100.0), "short")
        assert d["verdict"] == "execute" and d["confidence_pct"] >= 65

    def test_invalid_when_price_beyond_stop(self):
        snap = _snap(spot=105.0)                      # above the 104 stop for a short
        checks = _entry_checks(_SHORT, snap, frames={})
        assert {c["key"]: c["status"] for c in checks}["level_integrity"] == "fail"
        d = _decide_entry(checks, _position_math(_SHORT, 105.0), "short")
        assert d["verdict"] == "invalid"

    def test_wait_when_not_at_entry(self):
        snap = _snap(spot=103.0)                      # above the band, below the stop
        checks = _entry_checks(_SHORT, snap, frames={})
        assert {c["key"]: c["status"] for c in checks}["at_entry"] == "warn"
        d = _decide_entry(checks, _position_math(_SHORT, 103.0), "short")
        assert d["verdict"] == "wait"

    def test_htf_flip_invalidates_trend_continuation(self):
        trade = {**_SHORT, "setup_type": "trend_continuation"}
        snap = _snap(spot=100.0, trend_daily={"trend": "up"}, choch_1h={"trend": "up"})
        checks = _entry_checks(trade, snap, frames={})
        htf = {c["key"]: c for c in checks}["htf_alignment"]
        assert htf["role"] == "critical" and htf["status"] == "fail"
        d = _decide_entry(checks, _position_math(trade, 100.0), "short")
        assert d["verdict"] == "invalid"


# ---------------------------------------------------------------------------
class TestExitVerdict:
    def test_stop_hit_exits(self):
        checks = [_mk("stop_hit", "Stop", "fail", "breached", role="trigger")]
        assert _decide_exit(checks, {})["verdict"] == "exit"

    def test_t1_scales_out_t2_exits(self):
        assert _decide_exit([_mk("target_t1", "T1", "pass", "hit", role="trigger")], {})["verdict"] == "scale_out"
        assert _decide_exit([_mk("target_t2", "T2", "pass", "hit", role="trigger")], {})["verdict"] == "exit"

    def test_single_caution_tightens(self):
        checks = [_mk("choch_against", "CHOCH", "fail", "against", role="caution")]
        assert _decide_exit(checks, {})["verdict"] == "tighten"

    def test_hold_when_clean(self):
        checks = [_mk("stop_hit", "Stop", "pass", "ok", role="trigger"),
                  _mk("choch_against", "CHOCH", "pass", "with you", role="caution")]
        assert _decide_exit(checks, {"r_multiple": 0.5})["verdict"] == "hold"


# ---------------------------------------------------------------------------
class TestNeutralVerdict:
    def test_breakout_invalidates_entry(self):
        checks = [_mk("in_range", "in range", "fail", "left range", role="gate"),
                  _mk("range_breakout", "no breakout", "fail", "broke", role="caution")]
        assert _decide_neutral(checks, "entry")["verdict"] == "invalid"
        assert _decide_neutral(checks, "exit")["verdict"] == "exit"

    def test_inside_calm_executes(self):
        checks = [_mk("in_range", "in range", "pass", "inside", role="gate"),
                  _mk("range_breakout", "no breakout", "pass", "holding", role="caution"),
                  _mk("gamma_pin", "gamma", "pass", "pin", weight=1.0),
                  _mk("vwap_calm", "calm", "pass", "calm", weight=0.6)]
        assert _decide_neutral(checks, "entry")["verdict"] == "execute"


# ---------------------------------------------------------------------------
class TestMisc:
    def test_headline_covers_all_verdicts(self):
        for v in VERDICT_LABEL:
            assert _headline("AAPL", v, "short")

    def test_mk_shape(self):
        c = _mk("k", "L", "pass", "d", value=1, weight=2.0, role="confirm")
        assert c["key"] == "k" and c["status"] == "pass" and c["weight"] == 2.0
