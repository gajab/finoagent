"""Post-earnings reaction read — the facts of the last print's move and where price sits against it now.

Every pivot/indicator in the TA stack treats the earnings session as just another bar, so after a big
print the read rests on PRE-print structure (TTMI 2026-10: −27.7% on the print, then +20% in two
sessions while the verdict stayed "strong bearish, all timeframes aligned"). The levels traders anchor
to after a print are different: the print-day high/low, the prior close (the gap fill), and the AVWAP
anchored at the reaction session. This module reports those, deterministically, from the same daily
series and AVWAP anchor the rest of the TA uses.

It is CONTEXT, not a directional vote. Study (2026-10-26; S&P 500 + a few mid-caps, 2017-08..2026-08,
3,409 reaction days with |close-to-close| ≥ 7%, decision at the close 1/2/3 sessions after the print):
  * gap-down then a close back ABOVE the print-day high (n=114/179/220): forward excess vs SPY at
    5/10/20/40 days all |t| < 2 (quarter-clustered), same as the gaps that did not reclaim; halves agree.
    The TTMI-like subset (below the 50-day before the print, n=80) was +3.3% at 40 days but t=1.6.
  * the mirror (gap-up, close back below the print-day low) — likewise no edge.
  * which comes first, the gap fill or a retest of the print-day extreme: 70% fill after a reclaim vs 64%
    implied by the distances alone (no-drift geometry); the gap is ordinary equity drift, not the event.
So no post-print state earns a vote (the bar is OOS same-sign and t ≳ 3); the levels are what's useful.
"""

from __future__ import annotations

import math

# Only recent prints: after ~3 weeks the post-print bars have formed their own swings and the
# regular structure read covers them.
MAX_AGE_SESSIONS = 15
# A print is a "move" when the reaction-day close-to-close is at least this many pre-print daily σ
# AND at least MIN_MOVE — below that it is an ordinary session and the regular read is fine.
SIGMA_MULT = 2.0
MIN_MOVE = 0.04

EVIDENCE = ("Context, not a signal: across 3,409 S&P 500 earnings moves of 7%+ (2017–2026), reclaiming or "
            "extending past the print-day range in the next 1–3 sessions did not predict the following 5–40 days "
            "vs SPY, and the gap filled about as often as the distances alone imply.")


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _money(x: float) -> str:
    return f"${x:,.2f}"


def earnings_reaction(timestamps, closes, highs, lows, volumes, anchor_date: str | None,
                      avwap: float | None = None) -> dict | None:
    """Reaction to the most recent print, or ``None`` when there is no recent, material one.

    ``timestamps`` are daily 'YYYY-MM-DD' labels aligned with the OHLCV lists; ``anchor_date`` is the
    REACTION session (the same anchor as the "Last Earnings" AVWAP, so the two always agree)."""
    if not anchor_date or not timestamps:
        return None
    try:
        e = list(timestamps).index(str(anchor_date)[:10])
    except ValueError:
        return None
    n = len(closes)
    if e < 21 or e >= n or n - 1 - e > MAX_AGE_SESSIONS:
        return None
    c = [_f(x) for x in closes]
    h = [_f(x) for x in highs]
    lo = [_f(x) for x in lows]
    v = [_f(x) for x in volumes] if volumes else []
    if None in (c[e - 1], c[e], c[-1], h[e], lo[e]) or c[e - 1] <= 0:
        return None

    move = c[e] / c[e - 1] - 1
    rets = [math.log(c[i] / c[i - 1]) for i in range(max(1, e - 60), e)
            if c[i] and c[i - 1] and c[i] > 0 and c[i - 1] > 0]
    sigma = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 10 else None
    if abs(move) < MIN_MOVE or (sigma and abs(move) < SIGMA_MULT * sigma):
        return None
    pre_vol = [x for x in v[max(0, e - 50):e] if x] if v else []
    rvol = (v[e] / (sum(pre_vol) / len(pre_vol))) if (pre_vol and v[e]) else None

    down = move < 0
    spot, ph, pl, prior = c[-1], h[e], lo[e], c[e - 1]
    sessions = n - 1 - e
    retraced = (spot - c[e]) / (prior - c[e]) if prior != c[e] else None   # share of the print move given back
    if down:
        state = "reclaimed" if spot > ph else "extended" if spot < pl else "inside"
    else:
        state = "reclaimed" if spot < pl else "extended" if spot > ph else "inside"

    avw = _f(avwap)
    avwap_side = None if avw is None else ("above" if spot > avw else "below")

    # one deterministic sentence, every number from the series above
    head = (f"Earnings {timestamps[e]}: {move * 100:+.1f}%"
            + (f" on {rvol:.1f}× normal volume" if rvol else "")
            + (f"; {spot / c[e] * 100 - 100:+.1f}% since" if sessions else "") + ".")
    if down:
        body = {"reclaimed": f"Back above the print-day high {_money(ph)} — sellers from the print are under water",
                "inside": f"Inside the print-day range {_money(pl)}–{_money(ph)}; above {_money(ph)} puts print-day sellers under water",
                "extended": f"Below the print-day low {_money(pl)} — the post-print drift is still lower"}[state]
    else:
        body = {"reclaimed": f"Back below the print-day low {_money(pl)} — buyers from the print are under water",
                "inside": f"Inside the print-day range {_money(pl)}–{_money(ph)}; below {_money(pl)} puts print-day buyers under water",
                "extended": f"Above the print-day high {_money(ph)} — the post-print drift is still higher"}[state]
    tail = []
    if avw is not None:
        tail.append(f"{avwap_side} the earnings AVWAP {_money(avw)}")
    if retraced is not None and sessions and state != "extended":
        tail.append(f"{max(0.0, retraced) * 100:.0f}% of the move retraced; the gap fills at {_money(prior)}")
    read = f"{head} {body}" + (f" · {' · '.join(tail)}" if tail else "") + "."

    return {
        "date": timestamps[e],
        "sessions_since": sessions,
        "direction": "down" if down else "up",
        "move_pct": round(move * 100, 2),
        "sigma_multiple": round(abs(move) / sigma, 1) if sigma else None,
        "rvol": round(rvol, 1) if rvol else None,
        "print_high": round(ph, 2),
        "print_low": round(pl, 2),
        "prior_close": round(prior, 2),
        "since_pct": round((spot / c[e] - 1) * 100, 2),
        "retraced_pct": round(retraced * 100, 1) if retraced is not None else None,
        "state": state,
        "avwap": round(avw, 2) if avw is not None else None,
        "avwap_side": avwap_side,
        "read": read,
        "evidence": EVIDENCE,
    }
