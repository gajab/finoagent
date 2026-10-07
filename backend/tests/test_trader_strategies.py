"""Famous-trader rule-sets as per-ticker strategies: coverage, single-source-of-truth with the Trade Manager's lenses, stop maths,
level consistency, honest 'unavailable' states, JSON safety, and that the pinned backtest copies still equal the Trade Manager's."""
import asyncio
import json

import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from app.routers import stock_router as R
from app.services import trader_lenses as TL
from app.services import trader_strategy_service as S


def ohlcv(closes, spread=0.01, vol=1_000_000.0):
    c = np.asarray(closes, dtype=float)
    n = len(c)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) * (1 + spread)
    l = np.minimum(o, c) * (1 - spread)
    v = np.full(n, vol)
    idx = pd.date_range(end="2026-09-30", periods=n, freq="B")
    return o, h, l, c, v, idx


def walk(n=420, drift=0.003, sig=0.004, seed=1, start=100.0):
    rng = np.random.default_rng(seed)
    return start * np.exp(np.cumsum(rng.normal(drift, sig, n)))


UP = ohlcv(walk(420, 0.003))
DOWN = ohlcv(walk(420, -0.003, seed=2))
by_key = lambda out: {r["key"]: r for r in out["strategies"]}      # noqa: E731


def test_every_trader_the_trade_manager_runs_is_on_the_board_exactly_once():
    out = S.build_trader_strategies(*UP)
    keys = [r["key"] for r in out["strategies"]]
    assert sorted(keys) == sorted(S.CATALOGUE) and len(keys) == len(set(keys)) == 15
    assert all(r["family"] in S.FAMILIES for r in out["strategies"])
    assert {r["key"] for r in out["strategies"] if r["named_elsewhere"]} == {"connors", "qullamaggie"}   # the two that already have a full screen
    assert sum(1 for r in out["strategies"] if not r["named_elsewhere"]) == 13
    # the catalogue covers every lens the trade manager can emit (a new lens must be placed in a family, not silently dropped)
    emitted = {l["key"] for l in TL.trader_lenses(TL.indicator_suite(*UP[:5], UP[5]))}
    assert emitted <= set(S.CATALOGUE)


def test_rules_are_the_trade_managers_rules_not_a_reimplementation():
    out = S.build_trader_strategies(*UP)
    suite = TL.indicator_suite(*UP[:5], UP[5])
    for lens in TL.trader_lenses(suite):
        r = by_key(out)[lens["key"]]
        assert r["met"] == lens["met"] and r["total"] == lens["total"] and r["stance"] == lens["stance"] and r["d"] == pytest.approx(lens["d"], abs=1e-9)
        assert [x["id"] for x in r["rules"]] == [x["id"] for x in lens["rules"]]
        assert r["exit_rule"] == lens["exit_rule"] and r["philosophy"] == lens["philosophy"]


def test_consensus_counts_match_the_stances_shown():
    out = S.build_trader_strategies(*UP)
    avail = [r for r in out["strategies"] if r["available"]]
    c = out["consensus"]
    assert c["n"] == len(avail) == c["bull"] + c["neutral"] + c["bear"]
    assert c["bull"] == sum(1 for r in avail if r["stance"] == "bullish") and c["bear"] == sum(1 for r in avail if r["stance"] == "bearish")


def test_a_strong_uptrend_reads_bullish_with_its_stops_below_price():
    out = S.build_trader_strategies(*UP)
    assert out["consensus"]["bull"] > out["consensus"]["bear"]
    r = by_key(out)
    for k in ("minervini", "ptj", "cta"):
        assert r[k]["stance"] == "bullish", k
        st = r[k]["stop"]
        assert st["breached"] is False and st["distance_pct"] < 0 and st["atr_multiple"] > 0, (k, st)


def test_a_downtrend_has_already_lost_the_trend_levels():
    out = S.build_trader_strategies(*DOWN)
    r = by_key(out)
    assert out["consensus"]["bear"] > out["consensus"]["bull"]
    for k in ("ptj", "minervini"):
        st = r[k]["stop"]
        assert st["breached"] is True and st["distance_pct"] > 0 and st["atr_multiple"] < 0, (k, st)    # price is BELOW the level: reclaim, not a future stop


def test_every_trader_says_whether_its_exit_is_a_price_line():
    assert set(S.EXIT_IS_LEVEL) == set(S.CATALOGUE)                       # a new trader must be classified, not silently defaulted
    out = S.build_trader_strategies(*UP)
    for r in out["strategies"]:
        assert isinstance(r["exit_is_level"], bool)
    r = by_key(out)
    assert r["ptj"]["exit_is_level"] is True and r["turtles"]["exit_is_level"] is True and r["cta"]["exit_is_level"] is True
    assert r["druckenmiller"]["exit_is_level"] is False and r["carter"]["exit_is_level"] is False and r["connors"]["exit_is_level"] is False
    # the exit rule TEXT must agree with the flag for the clear cases
    assert "Close below the 200d" in r["ptj"]["exit_rule"] and "Relative-strength" in r["druckenmiller"]["exit_rule"]


def test_stop_distance_is_exact():
    st = S._stop_block(95.0, 100.0, 2.5)
    assert st == {"level": 95.0, "distance_pct": -5.0, "atr_multiple": 2.0, "breached": False}
    assert S._stop_block(105.0, 100.0, 2.5) == {"level": 105.0, "distance_pct": 5.0, "atr_multiple": -2.0, "breached": True}
    assert S._stop_block(100.0, 100.0, 2.5)["breached"] is True                  # touching the level counts as lost
    assert S._stop_block(95.0, 100.0, None)["atr_multiple"] is None and S._stop_block(None, 100.0, 1.0) is None and S._stop_block(95.0, 0, 1.0) is None


def test_every_drawable_exit_level_is_the_traders_own_exit_level():
    for data in (UP, DOWN):
        out = S.build_trader_strategies(*data)
        for r in out["strategies"]:
            if not r["available"] or not r["stop"]:
                continue
            exits = [lv for lv in r["levels"] if lv["role"] == "exit"]
            assert exits, f"{r['key']} has a stop but no drawable exit level"
            assert any(abs(lv["price"] - r["stop"]["level"]) < 1e-9 for lv in exits), (r["key"], r["stop"], exits)
            assert all(lv["price"] > 0 and len(lv["label"]) <= 11 for lv in r["levels"]), r["levels"]     # label + price fits the chart gutter


def test_reference_levels_come_from_the_suite_prices_not_invented():
    out = S.build_trader_strategies(*UP)
    suite = TL.indicator_suite(*UP[:5], UP[5])
    mv = by_key(out)["minervini"]["levels"]
    want = {"50d avg": suite["sma"]["50"], "150d avg": suite["sma"]["150"], "200d avg": suite["sma"]["200"]}
    assert {lv["label"]: lv["price"] for lv in mv} == {k: round(v, 2) for k, v in want.items()}
    tu = {lv["label"]: lv["price"] for lv in by_key(out)["turtles"]["levels"]}
    assert tu["20d high"] == round(suite["prior_range"]["hi_20"], 2) and tu["10d low"] == round(suite["prior_range"]["lo_10"], 2)


def test_short_history_is_refused_and_missing_traders_say_why():
    assert S.build_trader_strategies(*ohlcv(walk(40))) is None
    mid = S.build_trader_strategies(*ohlcv(walk(150)))           # enough for Ichimoku, not for the weekly stage
    r = by_key(mid)
    assert r["weinstein"]["available"] is False and "200" in r["weinstein"]["reason"] and r["weinstein"]["grade"] == "no_data"
    assert r["ichimoku"]["available"] is True
    assert mid["meta"]["unavailable"] == ["weinstein"] and mid["consensus"]["n"] == 14
    short = by_key(S.build_trader_strategies(*ohlcv(walk(70))))
    assert short["ichimoku"]["available"] is False and "80" in short["ichimoku"]["reason"]


def test_payload_is_strict_json():
    for data in (UP, DOWN, ohlcv(walk(150))):
        out = S.build_trader_strategies(*data)
        json.dumps(out, allow_nan=False)                           # no NaN / inf / numpy scalars


def test_grade_buckets():
    assert [S._grade(m, t) for m, t in [(8, 8), (6, 8), (3, 8), (1, 8), (0, 0), (0, 5)]] == ["all", "most", "some", "few", "no_data", "few"]


def test_backtest_copies_equal_the_trade_managers_numbers():
    TM = pytest.importorskip("app.services.trade_manager_service")
    rows = TM.BACKTEST["exit_rules"]
    for key, row in S._EXIT_BACKTEST_ROWS.items():
        assert rows[key] == row, key
    for trader, (row_key, match) in S._EXIT_MATCH.items():
        assert row_key in S._EXIT_BACKTEST_ROWS and match in ("exact", "closest")
    finding = TM.BACKTEST["direction"]["finding"]
    for needle in ("99 liquid", "−0.02…+0.01", "|t| < 1.2"):
        assert needle in finding or needle in TM.BACKTEST["universe"], needle
    assert "99 liquid" in TM.BACKTEST["universe"] and "52.6k" in TM.BACKTEST["universe"] and "2016-2026" in TM.BACKTEST["universe"]
    assert "99 liquid" in S.EDGE_NOTE and "52.6k" in S.EDGE_NOTE and "2016-2026" in S.EDGE_NOTE and "−0.02 to +0.01" in S.EDGE_NOTE


def test_matched_exit_backtests_are_attached_where_one_exists():
    r = by_key(S.build_trader_strategies(*UP))
    assert r["ptj"]["exit_backtest"]["label"].startswith("exit on a close below the 200d") and r["ptj"]["exit_backtest"]["match"] == "exact"
    assert r["cta"]["exit_backtest"]["label"] == "Chandelier 3×ATR trail" and r["weinstein"]["exit_backtest"]["match"] == "closest"
    assert r["ptj"]["exit_backtest"]["baseline"]["mean"] == 7.8
    assert r["turtles"]["exit_backtest"] is None and r["carter"]["exit_backtest"] is None      # no backtested analogue → none claimed


# ── router ────────────────────────────────────────────────────────────────────────────────────────────────────────
class _DB:
    pass


def _patch_cache(monkeypatch, store):
    async def get(db, key):
        return store.get(key)

    async def put(db, key, val, ttl_seconds=0):
        store[key] = val
    monkeypatch.setattr(R, "get_cached", get)
    monkeypatch.setattr(R, "set_cached", put)


def test_endpoint_computes_once_then_serves_the_cache(monkeypatch):
    store, calls = {}, []
    _patch_cache(monkeypatch, store)
    import app.services.trader_strategy_service as svc
    monkeypatch.setattr(svc, "compute_trader_strategies", lambda stock: (calls.append(1), {"strategies": [], "price": 1.0})[1])
    a = asyncio.run(R.get_trader_strategies("aapl", user=SimpleNamespace(id=1), db=_DB()))
    b = asyncio.run(R.get_trader_strategies("AAPL", user=SimpleNamespace(id=1), db=_DB()))
    assert a["cached"] is False and b["cached"] is True and calls == [1]
    assert a["ticker"] == "AAPL" and "trader-strategies:AAPL:v1" in store


def test_endpoint_maps_no_history_to_404_and_engine_errors_to_500(monkeypatch):
    _patch_cache(monkeypatch, {})
    import app.services.trader_strategy_service as svc
    monkeypatch.setattr(svc, "compute_trader_strategies", lambda stock: None)
    with pytest.raises(HTTPException) as e:
        asyncio.run(R.get_trader_strategies("ZZZ", user=SimpleNamespace(id=1), db=_DB()))
    assert e.value.status_code == 404

    def boom(stock):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(svc, "compute_trader_strategies", boom)
    with pytest.raises(HTTPException) as e2:
        asyncio.run(R.get_trader_strategies("ZZZ", user=SimpleNamespace(id=1), db=_DB()))
    assert e2.value.status_code == 500 and "yfinance down" in e2.value.detail
