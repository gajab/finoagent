"""Post-earnings reaction read (earnings_reaction_service) — pure, on synthetic daily series."""
import pytest

from app.services.earnings_reaction_service import earnings_reaction, MAX_AGE_SESSIONS


def _series(pre=60, print_bar=(102.0, 84.09, 71.71, 73.89), after=((80.57, 73.56, 80.11), (88.80, 79.80, 88.39))):
    """TTMI-shaped: a calm run-up to 102, a −27.7% print day (H 84.09 / L 71.71 / C 73.89) on heavy
    volume, then two bounce sessions to 88.39. Returns (timestamps, closes, highs, lows, volumes, anchor)."""
    ts, c, h, l, v = [], [], [], [], []
    px = 95.0
    for i in range(pre):
        px += 0.12 if i % 2 else -0.02
        ts.append(f"2026-07-{(i % 28) + 1:02d}-{i}")          # unique labels; only the anchor must match
        c.append(px); h.append(px + 0.8); l.append(px - 0.8); v.append(4_000_000)
    c[-1] = print_bar[0]; h[-1] = print_bar[0] + 0.4; l[-1] = print_bar[0] - 1
    anchor = "2026-10-22"
    ts.append(anchor); h.append(print_bar[1]); l.append(print_bar[2]); c.append(print_bar[3]); v.append(17_500_000)
    for k, (hh, ll, cc) in enumerate(after):
        ts.append(f"2026-10-2{3 + k}"); h.append(hh); l.append(ll); c.append(cc); v.append(6_000_000)
    return ts, c, h, l, v, anchor


def test_ttmi_gap_down_reclaimed_print_high():
    ts, c, h, l, v, a = _series()
    r = earnings_reaction(ts, c, h, l, v, a, avwap=78.44)
    assert r["direction"] == "down" and r["state"] == "reclaimed"
    assert r["move_pct"] == pytest.approx((73.89 / 102.0 - 1) * 100, abs=0.01)
    assert r["print_high"] == 84.09 and r["print_low"] == 71.71 and r["prior_close"] == 102.0
    assert r["retraced_pct"] == pytest.approx((88.39 - 73.89) / (102.0 - 73.89) * 100, abs=0.1)
    assert r["avwap_side"] == "above" and r["sessions_since"] == 2
    assert r["rvol"] > 4
    assert "$84.09" in r["read"] and "$102.00" in r["read"] and "$78.44" in r["read"]


def test_gap_down_still_inside_range_and_extended():
    ts, c, h, l, v, a = _series(after=((80.57, 73.56, 80.11),))
    assert earnings_reaction(ts, c, h, l, v, a)["state"] == "inside"            # 80.11 between 71.71 and 84.09
    ts, c, h, l, v, a = _series(after=((74.0, 68.0, 69.0),))
    r = earnings_reaction(ts, c, h, l, v, a)
    assert r["state"] == "extended" and "gap fills" not in r["read"]             # follow-through, no fill talk


def test_gap_up_mirror():
    ts, c, h, l, v, a = _series(print_bar=(100.0, 121.0, 108.0, 118.0), after=((112.0, 104.0, 105.0),))
    r = earnings_reaction(ts, c, h, l, v, a)
    assert r["direction"] == "up" and r["state"] == "reclaimed"                  # back below the print-day low 108


def test_quiet_print_and_stale_print_are_skipped():
    ts, c, h, l, v, a = _series(print_bar=(102.0, 103.0, 100.5, 101.0))       # −1%: an ordinary session
    assert earnings_reaction(ts, c, h, l, v, a) is None
    many = tuple((80.0, 78.0, 79.0) for _ in range(MAX_AGE_SESSIONS + 1))
    ts, c, h, l, v, a = _series(after=many)
    assert earnings_reaction(ts, c, h, l, v, a) is None


def test_missing_anchor_or_short_history():
    ts, c, h, l, v, a = _series()
    assert earnings_reaction(ts, c, h, l, v, None) is None
    assert earnings_reaction(ts, c, h, l, v, "2025-01-01") is None
    ts, c, h, l, v, a = _series(pre=10)
    assert earnings_reaction(ts, c, h, l, v, a) is None
