"""Which expiries the credit-roll search looks at. Earnings-aware: when a print is coming, EVERY listed expiry that
lands before it is searched (those are the rolls that don't carry a short through the binary gap) — a
monthlies-plus-nearest-weekly search skipped them (DDOG: Oct 16 short, print ~Nov 5 → only Oct 23 was pre-print)."""
import datetime as dt

from app.services.roll_optimizer_service import _is_monthly, select_roll_expiries

TODAY = dt.date(2026, 9, 25)
CURRENT_DTE = 21                                            # the trade's own expiry: Oct 16, 2026


def _window():
    """Every Friday listing from the day after the trade's expiry out to the 130d horizon, as (iso, dte)."""
    out, d = [], TODAY + dt.timedelta(days=CURRENT_DTE + 3)
    while (d - TODAY).days <= 130:
        if d.weekday() == 4:
            out.append((d.isoformat(), (d - TODAY).days))
        d += dt.timedelta(days=1)
    return out


def _isos(sel):
    return [x[0] for x in sel]


def test_the_calendar_fixture_is_what_the_comments_say():
    w = dict(_window())
    assert _is_monthly("2026-11-20") and _is_monthly("2026-12-18") and not _is_monthly("2026-10-30")
    assert w["2026-10-23"] == 28 and w["2026-10-30"] == 35 and w["2026-11-06"] == 42


def test_without_earnings_it_is_the_monthlies_plus_the_nearest_weekly():
    sel = select_roll_expiries(_window(), earn_days=None)
    assert _isos(sel) == ["2026-10-23", "2026-11-20", "2026-12-18", "2027-01-15"]     # unchanged behavior


def test_with_earnings_ahead_every_pre_print_expiry_is_searched():
    # print on Nov 5 = 41d out: Oct 23 (28d) and Oct 30 (35d) land before it; Nov 6 (42d) is after
    sel = select_roll_expiries(_window(), earn_days=41)
    assert _isos(sel)[:2] == ["2026-10-23", "2026-10-30"]                              # pre-print first…
    assert "2026-11-20" in _isos(sel) and "2026-11-06" not in _isos(sel)               # …monthlies still searched, not every weekly


def test_when_the_cap_bites_the_pre_print_expiries_win():
    sel = select_roll_expiries(_window(), earn_days=41, max_expiries=3)
    assert _isos(sel) == ["2026-10-23", "2026-10-30", "2026-11-20"]


def test_at_most_three_pre_print_weeklies_are_added():
    sel = select_roll_expiries(_window(), earn_days=90, max_expiries=10)
    pre = [x for x in sel if x[1] < 90]
    weeklies = [x for x in pre if not _is_monthly(x[0])]
    assert len(weeklies) <= 4                                                          # nearest weekly + ≤3 pre-print adds


def test_a_print_before_any_candidate_changes_nothing():
    # earnings in 10 days — sooner than the earliest roll target (28d): nothing lands before it
    assert select_roll_expiries(_window(), earn_days=10) == select_roll_expiries(_window(), earn_days=None)


def test_no_monthly_in_the_window_keeps_only_the_nearest_weekly():
    only_weeklies = [("2026-10-23", 28), ("2026-10-30", 35)]
    assert _isos(select_roll_expiries(only_weeklies, earn_days=None)) == ["2026-10-23"]
    # …but with a print at 40d both pre-print weeklies are searched
    assert _isos(select_roll_expiries(only_weeklies, earn_days=40)) == ["2026-10-23", "2026-10-30"]
