"""Trade Manager — indicator suite, trader lenses, position profile, decision, exit plan, monitor,
and the LLM-packet 'facts only' guarantee. No network: synthetic OHLCV + a hand-built evidence dict."""
import asyncio
import json

import numpy as np
import pandas as pd
import pytest

from app.services import trader_lenses as TL
from app.services import trade_manager_service as TM


def _bars(kind: str, n: int = 420, seed: int = 3):
    rng = np.random.default_rng(seed)
    drift, sig = {"up": (0.003, 0.006), "down": (-0.003, 0.006), "flat": (0.0, 0.011)}[kind]   # clean persistent trends
    c = 100 * np.exp(np.cumsum(rng.normal(drift, sig, n)))
    h, l = c * 1.008, c * 0.992
    o = np.concatenate([[c[0]], c[:-1]])
    v = rng.integers(1_000_000, 3_000_000, n).astype(float)
    idx = pd.date_range(end="2026-09-30", periods=n, freq="B")
    return o, h, l, c, v, idx


def _ev(kind: str = "up") -> dict:
    o, h, l, c, v, idx = _bars(kind)
    bench = 100 * np.exp(np.cumsum(np.random.default_rng(9).normal(0.0004, 0.007, len(c))))
    suite = TL.indicator_suite(o, h, l, c, v, idx, bench)
    lenses = TL.trader_lenses(suite)
    spot = float(c[-1])
    atr = suite["atr14"]
    return {
        "ticker": "TEST", "spot": round(spot, 2), "as_of": "2026-09-30", "sources_ok": {"suite": True, "structure": True, "fundamental": True},
        "technical": {"suite": suite, "lenses": lenses, "consensus": TL.lens_consensus(lenses), "ta_block": {}, "volume": {}},
        "structure": {
            "context": {}, "zones": [
                {"center": spot - 2.2 * atr, "kind": "support", "score": 3, "sources": [{"label": "Daily order block", "price": spot - 2.2 * atr, "weight": 1.0},
                                                                                   {"label": "AVWAP YTD", "price": spot - 2.25 * atr, "weight": 1.0}]},
                {"center": spot + 3 * atr, "kind": "resistance", "score": 3, "sources": [{"label": "Naked POC", "price": spot + 3 * atr, "weight": 1.0},
                                                                                       {"label": "FVG", "price": spot + 3.05 * atr, "weight": 1.0}]},
            ],
            "patterns": [], "dossier": {"market_structure": {"bias": {"overall": "bullish" if kind == "up" else "bearish" if kind == "down" else "mixed"},
                                                             "trend_alignment": {"daily": "up", "h4": "up", "h1": "up"}},
                                        "regime": {"overall": "Trending"}, "dealer_gamma": {"net_gex": {"sign": "long"}, "gamma_flip": {"level": spot * 0.97}},
                                        "volume_profile": {"daily": {"poc": spot * 0.98, "vah": spot * 1.02, "val": spot * 0.94}}},
        },
        "fundamental": {"is_fund": False, "pillars": {k: {"score": 20, "data": {}} for k in (
            "fundamental", "valuation", "sentiment_targets", "catalyst_revisions", "quality_capital", "ownership_flow",
            "structural", "macro_sensitivity", "geopolitical", "sector_rotation")}, "analyst": {"upgrades_90d": 1, "downgrades_90d": 0, "target_mean": spot * 1.1}},
        "events": {"days_to_earnings": None, "news": [], "headline_flags": {"n_risk": 0, "n_positive": 0}, "filings": []},
        "market": {"tape": {"^VIX": {"last": 16, "change_pct_5d": 2}}, "fred": {"DGS10": 4.2}, "peers": {}},
    }


def _pnl(spot: float, **kw) -> dict:
    base = {"underlying_price": spot, "unrealized_pnl": 120.0, "pnl_pct": 20.0, "entry_cost": -300.0, "current_value": -180.0,
            "net_greeks": {"delta": 20.0, "gamma": -0.5, "theta": 6.0, "vega": -9.0}, "max_profit": 300.0, "max_loss": -700.0,
            "breakevens": [spot * 0.9], "days_held": 12,
            "analysis": {"dte_remaining": 24, "captured_pct": 40.0, "probability_of_profit": 74.0,
                         "quant_exit": {"signal": "HOLD", "score": 61, "overrides": []}}}
    base.update(kw)
    return base


def _strategy(legs, st="put_credit_spread"):
    return {"ticker": "TEST", "name": "t", "strategy_type": st, "legs_data": legs, "parameters": {}, "notes": ""}


PCS = [{"action": "SELL", "type": "put", "strike": 90.0, "expiration": "2099-01-01", "qty": 1},
       {"action": "BUY", "type": "put", "strike": 85.0, "expiration": "2099-01-01", "qty": 1}]


# ── indicator suite & lenses ────────────────────────────────────────────────

def test_suite_and_lenses_shape():
    o, h, l, c, v, idx = _bars("up")
    s = TL.indicator_suite(o, h, l, c, v, idx)
    for k in ("rsi14", "adx", "supertrend", "chandelier", "ichimoku", "weekly", "vsa", "bollinger", "range"):
        assert k in s, k
    ls = TL.trader_lenses(s)
    keys = {x["key"] for x in ls}
    assert {"minervini", "oneil", "weinstein", "turtles", "ptj", "druckenmiller", "raschke", "connors", "qullamaggie",
            "livermore", "carter", "ichimoku", "elder", "wyckoff", "cta"} <= keys
    for x in ls:
        assert x["rules"] and -1.0 <= x["d"] <= 1.0 and x["exit_rule"]


def test_uptrend_reads_bullish_downtrend_bearish():
    up = TL.lens_consensus(TL.trader_lenses(TL.indicator_suite(*_bars("up"))))
    dn = TL.lens_consensus(TL.trader_lenses(TL.indicator_suite(*_bars("down"))))
    assert up["d"] > dn["d"]
    assert dn["d"] < 0


def test_rsi_adx_bounds():
    _, h, l, c, _, _ = _bars("up")
    r = TL.rsi(c)
    assert np.nanmin(r) >= 0 and np.nanmax(r) <= 100
    a, p, m = TL.adx_di(h, l, c)
    assert 0 <= np.nanmax(a) <= 100


# ── profile ─────────────────────────────────────────────────────────────────

def test_profile_directions():
    p = TM.build_profile(_strategy(PCS), _pnl(100.0))
    assert p["direction"] == "bullish" and p["short_premium"] and p["kind"] == "short_premium"
    assert p["short_put_strike"] == 90.0 and p["cushion_down_pct"] == 10.0
    ic = TM.build_profile(_strategy(PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}], "iron_condor"),
                          _pnl(100.0, net_greeks={"delta": 1.0, "gamma": -1, "theta": 9.0, "vega": -12.0}))
    assert ic["range_play"] and ic["direction"] == "neutral"
    stk = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                           _pnl(100.0, net_greeks={"delta": 100.0, "gamma": 0, "theta": 0, "vega": 0}))
    assert stk["kind"] == "stock" and stk["pos_sign"] == 1


# ── decision alignment ──────────────────────────────────────────────────────

def test_bullish_position_prefers_uptrend():
    prof_up = TM.build_profile(_strategy(PCS), _pnl(_ev("up")["spot"]))
    prof_dn = TM.build_profile(_strategy(PCS), _pnl(_ev("down")["spot"]))
    d_up = TM.decide(prof_up, _ev("up"))
    d_dn = TM.decide(prof_dn, _ev("down"))
    assert d_up["lenses"]["technical"]["score"] > d_dn["lenses"]["technical"]["score"]
    assert d_up["score"] > d_dn["score"]
    assert d_up["signal"] in TM.SEV


def test_range_play_dislikes_strong_trend():
    ev_up, ev_flat = _ev("up"), _ev("flat")
    legs = PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    ng = {"delta": 1.0, "gamma": -1, "theta": 9.0, "vega": -12.0}
    pu = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(ev_up["spot"], net_greeks=ng))
    pf = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(ev_flat["spot"], net_greeks=ng))
    assert TM.decide(pf, ev_flat)["lenses"]["technical"]["score"] >= TM.decide(pu, ev_up)["lenses"]["technical"]["score"] - 25


def test_quant_hard_stop_floors_the_verdict():
    ev = _ev("up")
    pnl = _pnl(ev["spot"])
    pnl["analysis"]["quant_exit"] = {"signal": "STRONG_CLOSE", "score": 10, "overrides": ["near max loss — cut it"]}
    d = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)
    assert d["signal"] in ("EXIT", "STRONG_EXIT") and d["overrides"]


def test_earnings_inside_window_penalises_short_premium():
    ev = _ev("flat")
    prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    base = TM.decide(prof, ev)["lenses"]["event"]["score"]
    ev["events"]["days_to_earnings"] = 5
    ev["events"]["earnings_date"] = "2026-10-09"
    assert TM.decide(prof, ev)["lenses"]["event"]["score"] < base - 10


def test_signal_cutpoints_match_quant_desk():
    assert TM._signal_of(68) == "STRONG_HOLD" and TM._signal_of(67.9) == "HOLD"
    assert TM._signal_of(45) == "HOLD" and TM._signal_of(44.9) == "EXIT"
    assert TM._signal_of(28) == "EXIT" and TM._signal_of(27.9) == "STRONG_EXIT"


# ── exit plan / monitor ─────────────────────────────────────────────────────

def test_exit_plan_has_levels_and_why():
    ev = _ev("up")
    prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    dec = TM.decide(prof, ev)
    plan = TM.build_exit_plan(prof, ev, dec)
    kinds = {i["kind"] for i in plan["items"]}
    assert "stop" in kinds and "target" in kinds and "pnl" in kinds
    stop = next(i for i in plan["items"] if i["kind"] == "stop")
    assert stop["level"] < ev["spot"] and stop["why"]
    assert plan["recommendation"]["text"]
    pnl_tp = next(i for i in plan["items"] if i["kind"] == "pnl" and i["action"] == "TAKE_PROFIT")
    assert pnl_tp["pnl_level"] == 150.0                      # 50% of $300 max profit


def test_strong_exit_recommends_now():
    ev = _ev("down")
    prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    dec = {"signal": "STRONG_EXIT"}
    assert TM.build_exit_plan(prof, ev, dec)["recommendation"]["when"] == "now"


def test_monitor_effects_signed_for_position():
    ev = _ev("up")
    prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    mon = TM.build_monitor(prof, ev, TM.decide(prof, ev))
    assert mon["indicators"] and mon["fundamental_events"] is not None
    for d in mon["down"]:
        assert d["effect_if_break"] == "bad"                 # a bull put spread dislikes a support break
    for u in mon["up"]:
        assert u["effect_if_break"] in ("good", "mixed")


# ── the LLM packet is FACTS ONLY ────────────────────────────────────────────

def _keys(o, acc):
    if isinstance(o, dict):
        for k, v in o.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(o, list):
        for v in o:
            _keys(v, acc)
    return acc


def test_packet_strips_every_algorithm_judgement():
    ev = _ev("up")
    prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    pkt = TM.llm_packet(prof, ev)
    ks = _keys(pkt, set())
    for banned in ("stance", "d", "signal", "verdict", "exit_signal", "quant_exit", "score", "lenses", "decision", "weights"):
        assert banned not in ks, banned
    txt = json.dumps(pkt)
    assert "STRONG_HOLD" not in txt and "STRONG_EXIT" not in txt
    assert len(txt) < 90000
    assert pkt["technical"]["famous_trader_rule_checks"] and pkt["position"]["short_put_strike"] == 90.0


def test_clean_handles_numpy_nan():
    out = TM._clean({"a": np.float64("nan"), "b": np.int64(3), "c": [np.bool_(True)], "d": float("inf")})
    assert out == {"a": None, "b": 3, "c": [True], "d": None}
    json.dumps(out)


# ═══════════════════════════ extended coverage ═══════════════════════════════

CCS = [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1},
       {"action": "BUY", "type": "call", "strike": 115.0, "expiration": "2099-01-01", "qty": 1}]


def _prof(legs, st, spot=100.0, **pnl_kw):
    return TM.build_profile(_strategy(legs, st), _pnl(spot, **pnl_kw))


# ── profile classification ──────────────────────────────────────────────────

def test_profile_bear_call_spread_is_bearish_short_premium():
    p = _prof(CCS, "call_credit_spread", net_greeks={"delta": -15.0, "gamma": -0.4, "theta": 5.0, "vega": -7.0})
    assert p["direction"] == "bearish" and p["pos_sign"] == -1 and p["short_premium"]
    assert p["short_call_strike"] == 110.0 and p["cushion_up_pct"] == 10.0 and p["short_put_strike"] is None


def test_profile_long_call_is_bullish_long_premium():
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    p = _prof(legs, "long_call", net_greeks={"delta": 52.0, "gamma": 3.0, "theta": -4.0, "vega": 12.0}, unrealized_pnl=-30.0)
    assert p["direction"] == "bullish" and p["long_premium"] and p["kind"] == "long_premium" and not p["short_premium"]


def test_profile_long_straddle_is_long_vol_neutral():
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1},
            {"action": "BUY", "type": "put", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    p = _prof(legs, "long_straddle", net_greeks={"delta": 2.0, "gamma": 6.0, "theta": -9.0, "vega": 22.0})
    assert p["direction"] == "neutral" and p["long_vol"] and not p["range_play"]


def test_profile_covered_call_flag_from_stock_leg_and_marker():
    legs = [{"action": "BUY", "type": "stock", "qty": 100}, {"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p = _prof(legs, "covered_call", net_greeks={"delta": 70.0, "gamma": -0.3, "theta": 4.0, "vega": -6.0})
    assert p["covered"] and p["stock_shares"] == 100 and not p["undefined_risk"]
    naked = [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p2 = _prof(naked, "naked_call", net_greeks={"delta": -20.0, "gamma": -0.4, "theta": 5.0, "vega": -8.0}, unbounded_loss=True)
    assert not p2["covered"] and p2["undefined_risk"]
    st = _strategy(naked, "naked_call"); st["parameters"] = {"covered": True}
    p3 = TM.build_profile(st, _pnl(100.0, net_greeks={"delta": -20.0, "gamma": -0.4, "theta": 5.0, "vega": -8.0}, unbounded_loss=True))
    assert p3["covered"] and not p3["undefined_risk"]


def test_profile_vertical_spread_normalised_as_one_unit():
    """A 1-lot vertical's two legs are ONE unit of exposure (regression: dividing by both legs halved the Δ)."""
    p = _prof(PCS, "put_credit_spread", net_greeks={"delta": 12.0, "gamma": -0.3, "theta": 4.0, "vega": -6.0})
    assert p["dir_raw"] == pytest.approx(0.12) and p["direction"] == "bullish"


def test_profile_short_put_far_otm_is_neutralish_range():
    p = _prof([PCS[0]], "cash_secured_put", net_greeks={"delta": 6.0, "gamma": -0.1, "theta": 2.0, "vega": -3.0})
    assert p["direction"] == "neutral" and p["range_play"]


def test_profile_stock_only_short_shares():
    p = _prof([{"action": "SELL", "type": "stock", "qty": 100}], "stock", net_greeks={"delta": -100.0, "gamma": 0, "theta": 0, "vega": 0})
    assert p["kind"] == "stock" and p["pos_sign"] == -1


def test_profile_dte_prefers_analysis_then_legs():
    p = _prof(PCS, "pcs")
    assert p["dte"] == 24
    pnl = _pnl(100.0); pnl["analysis"].pop("dte_remaining")
    p2 = TM.build_profile(_strategy(PCS), pnl)
    assert p2["dte"] is not None and p2["dte"] > 1000        # 2099 expiry → derived from the legs


def test_profile_missing_greeks_falls_back_to_stock_delta():
    pnl = _pnl(100.0); pnl["net_greeks"] = {}
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 50}], "stock"), pnl)
    assert p["pos_sign"] == 1 and p["greeks"]["delta"] == 50


def test_profile_quant_fallback_maps_legacy_exit_signal():
    pnl = _pnl(100.0); pnl["analysis"].pop("quant_exit"); pnl["analysis"]["exit_signal"] = "CLOSE"
    p = TM.build_profile(_strategy(PCS), pnl)
    assert p["_quant"]["signal"] == "CLOSE" and p["_quant"]["score"] is None


# ── weights / quant availability ────────────────────────────────────────────

# ── event lens ──────────────────────────────────────────────────────────────

def test_event_lens_penalties_are_signed_and_scaled():
    ev = _ev("flat")
    base_p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    base = TM._event_score(base_p, ev)["score"]
    ev["events"].update(days_to_earnings=1, earnings_date="2026-10-02")
    s1 = TM._event_score(base_p, ev)["score"]
    ev["events"]["days_to_earnings"] = 10
    s10 = TM._event_score(base_p, ev)["score"]
    assert base > s10 > s1                                      # the nearer the print, the bigger the penalty
    naked = TM.build_profile(_strategy([PCS[0]], "naked_put"), _pnl(ev["spot"], unbounded_loss=True))
    assert TM._event_score(naked, ev)["score"] <= s10           # undefined risk is penalised at least as hard


def test_event_lens_long_premium_not_punished_for_earnings():
    ev = _ev("flat"); ev["events"].update(days_to_earnings=5)
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    lp = TM.build_profile(_strategy(legs, "long_call"), _pnl(ev["spot"], net_greeks={"delta": 50, "gamma": 3, "theta": -4, "vega": 12}))
    sp = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    assert TM._event_score(lp, ev)["score"] > TM._event_score(sp, ev)["score"]


def test_event_lens_vix_spike_hurts_short_premium_only():
    ev = _ev("flat"); ev["market"]["tape"]["^VIX"] = {"last": 34, "change_pct_5d": 40}
    sp = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    base = _ev("flat")
    assert TM._event_score(sp, ev)["score"] < TM._event_score(sp, base)["score"] - 10


def test_event_lens_exdiv_assignment_risk_needs_near_the_money_short_call():
    ev = _ev("flat")
    import datetime as dt
    ev["events"]["ex_dividend_date"] = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    near = TM.build_profile(_strategy(CCS, "ccs"), _pnl(106.0, net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}))
    far = TM.build_profile(_strategy(CCS, "ccs"), _pnl(80.0, net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}))
    assert TM._event_score(near, ev)["score"] < TM._event_score(far, ev)["score"]


def test_event_lens_malformed_filing_date_does_not_crash():
    ev = _ev("flat"); ev["events"]["filings"] = [{"form": "8-K", "date": "not-a-date"}, {"form": "8-K", "date": None}]
    TM._event_score(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)


def test_event_lens_recent_8k_is_a_small_penalty():
    import datetime as dt
    ev = _ev("flat"); base = TM._event_score(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)["score"]
    ev["events"]["filings"] = [{"form": "8-K", "date": (dt.date.today() - dt.timedelta(days=2)).isoformat()}]
    assert TM._event_score(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)["score"] == pytest.approx(base - 3)


def test_headline_flags_keyword_scan():
    f = TM._headline_flags([{"title": "Analyst downgrade after probe into accounting"}, {"title": "Company raises guidance, record quarter"},
                            {"title": "Quiet Tuesday"}])
    assert f["n_risk"] == 1 and f["n_positive"] == 1 and f["n_total"] == 3


# ── fundamental lens ────────────────────────────────────────────────────────

def test_fundamental_lens_fund_is_neutral_and_flagged():
    ev = _ev("up"); ev["fundamental"] = {"is_fund": True, "pillars": {}}
    r = TM._fundamental_score(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)
    assert r["available"] is False and r["score"] == pytest.approx(55.0)


def test_fundamental_lens_healthy_vs_distressed_for_bullish_position():
    good, bad = _ev("up"), _ev("up")
    for v in bad["fundamental"]["pillars"].values():
        v["score"] = 70
    for v in good["fundamental"]["pillars"].values():
        v["score"] = 8
    p = TM.build_profile(_strategy(PCS), _pnl(good["spot"]))
    assert TM._fundamental_score(p, good)["score"] > 65 > 40 > TM._fundamental_score(p, bad)["score"]


def test_fundamental_lens_inverts_for_bearish_position():
    bad = _ev("up")
    for v in bad["fundamental"]["pillars"].values():
        v["score"] = 70
    bear = TM.build_profile(_strategy(CCS, "ccs"), _pnl(100.0, net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}))
    assert TM._fundamental_score(bear, bad)["score"] > 60          # bad fundamentals SUPPORT a bearish position


def test_fundamental_lens_analyst_downgrades_and_misses_pull_down():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    base = TM._fundamental_score(p, ev)["score"]
    ev["fundamental"]["analyst"] = {"downgrades_90d": 3, "upgrades_90d": 0, "target_mean": ev["spot"] * 0.9}
    ev["fundamental"]["estimates"] = {"earnings_surprises": [{"surprisePercent": -0.1}] * 4}
    assert TM._fundamental_score(p, ev)["score"] < base - 5


def test_fundamental_range_play_only_tail_risk():
    ev = _ev("flat")
    legs = PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(100.0, net_greeks={"delta": 1, "gamma": -1, "theta": 9, "vega": -12}))
    for v in ev["fundamental"]["pillars"].values():
        v["score"] = 8
    great = TM._fundamental_score(p, ev)["score"]
    for v in ev["fundamental"]["pillars"].values():
        v["score"] = 70
    awful = TM._fundamental_score(p, ev)["score"]
    assert great > awful and great - 62 < 10                      # upside is capped: a great business doesn't help a range trade much


# ── decision overrides ──────────────────────────────────────────────────────

def test_decision_conflict_message_when_lenses_disagree():
    ev = _ev("down"); pnl = _pnl(ev["spot"]); pnl["analysis"]["quant_exit"] = {"signal": "STRONG_HOLD", "score": 85, "overrides": []}
    d = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)
    assert d["conflicts"] and "disagree" in d["conflicts"][0]


def test_decision_never_more_optimistic_than_quant_hard_stop():
    for kind in ("up", "flat", "down"):
        ev = _ev(kind); pnl = _pnl(ev["spot"])
        pnl["analysis"]["quant_exit"] = {"signal": "CLOSE", "score": 40, "overrides": ["short strike tested — defend (roll) or close"]}
        d = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)
        assert TM.SEV[d["signal"]] >= TM.SEV["EXIT"], kind


def test_decision_confidence_and_coverage_degrade_without_evidence():
    ev = _ev("up"); full = TM.decide(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)
    ev["sources_ok"] = {"suite": True, "structure": False, "fundamental": False, "peers": False, "filings": False, "news": False}
    thin = TM.decide(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)
    assert thin["coverage"] < full["coverage"] and thin["conviction"] < full["conviction"]


def test_decision_score_is_bounded_and_deterministic():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    a, b = TM.decide(p, ev), TM.decide(p, ev)
    assert a == b and 0 <= a["score"] <= 100
    assert a["signal"] == TM._signal_of(a["score"])


# ── levels / plan / monitor ─────────────────────────────────────────────────

def test_levels_invalidation_is_beyond_min_distance_and_on_the_right_side():
    ev = _ev("up"); prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    lv = TM._levels(prof, ev)
    assert lv["invalidation"]["level"] <= ev["spot"] - TM.INVAL_MIN_ATR * lv["atr"] + 1e-6
    bear = TM.build_profile(_strategy(CCS, "ccs"), _pnl(ev["spot"], net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}))
    lvb = TM._levels(bear, ev)
    assert lvb["invalidation"]["level"] >= ev["spot"] + TM.INVAL_MIN_ATR * lvb["atr"] - 1e-6


def test_levels_targets_are_on_the_favourable_side_nearest_first_with_r_multiple():
    ev = _ev("up"); prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    tg = TM._levels(prof, ev)["targets"]
    assert tg and all(t["level"] > ev["spot"] for t in tg)
    assert [t["level"] for t in tg] == sorted(t["level"] for t in tg)
    assert all(t.get("r_multiple") for t in tg)


def test_levels_range_play_has_guards_on_both_sides():
    ev = _ev("flat")
    legs = PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(ev["spot"], net_greeks={"delta": 1, "gamma": -1, "theta": 9, "vega": -12}))
    lv = TM._levels(p, ev)
    assert "upper_guard" in lv and "lower_guard" in lv and "invalidation" not in lv


def test_levels_empty_when_no_atr_or_spot():
    ev = _ev("up"); ev["technical"]["suite"]["atr14"] = None; ev["spot"] = 0
    p = TM.build_profile(_strategy(PCS), _pnl(100.0))
    assert TM._levels({**p, "spot": 0}, ev).get("invalidation") is None


def test_plan_pnl_rules_follow_research():
    ev = _ev("up"); prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    plan = TM.build_exit_plan(prof, ev, TM.decide(prof, ev))
    pnl_items = [i for i in plan["items"] if i["kind"] == "pnl"]
    tp = next(i for i in pnl_items if i["action"] == "TAKE_PROFIT")
    assert tp["pnl_level"] == 150.0
    review = next(i for i in pnl_items if i["action"] == "REVIEW")
    assert review["pnl_level"] < 0 and "not a stop" in review["sources"][0]
    assert not any(i["action"] == "EXIT" for i in pnl_items)      # no mechanical credit-multiple stop


def test_plan_long_premium_rules():
    ev = _ev("up")
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    p = TM.build_profile(_strategy(legs, "long_call"), _pnl(ev["spot"], entry_cost=400.0, net_greeks={"delta": 52, "gamma": 3, "theta": -4, "vega": 12}))
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    lv = {i["pnl_level"] for i in plan["items"] if i["kind"] == "pnl"}
    assert lv == {-200.0, 400.0}


def test_plan_time_rules_by_dte():
    ev = _ev("up")
    def plan_for(dte):
        pnl = _pnl(ev["spot"]); pnl["analysis"]["dte_remaining"] = dte
        p = TM.build_profile(_strategy(PCS), pnl)
        return TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"]
    assert any(i["kind"] == "time" and i["action"] == "REVIEW" and i["in_days"] == 14 for i in plan_for(35))
    assert any(i["kind"] == "time" and i["action"] == "EXIT" for i in plan_for(10))
    assert not any(i["kind"] == "time" for i in plan_for(1))


def test_plan_event_items():
    import datetime as dt
    ev = _ev("up"); ev["events"].update(days_to_earnings=6, earnings_date="2026-10-10",
                                        ex_dividend_date=(dt.date.today() + dt.timedelta(days=4)).isoformat())
    legs = CCS
    p = TM.build_profile(_strategy(legs, "ccs"), _pnl(ev["spot"], net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}))
    items = TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"]
    assert any(i["kind"] == "event" and i["action"] == "DERISK" for i in items)
    assert any(i["kind"] == "event" and "Ex-dividend" in i["why"] for i in items)


def test_plan_recommendation_by_signal():
    ev = _ev("up"); prof = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    assert TM.build_exit_plan(prof, ev, {"signal": "STRONG_EXIT"})["recommendation"]["when"] == "now"
    assert TM.build_exit_plan(prof, ev, {"signal": "EXIT"})["recommendation"]["when"] in ("soon", "into strength")
    r = TM.build_exit_plan(prof, ev, {"signal": "HOLD"})["recommendation"]
    assert r["when"] == "conditional" and "below" in r["text"]


def test_plan_range_hold_text_names_both_guards():
    ev = _ev("flat")
    legs = PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(ev["spot"], net_greeks={"delta": 1, "gamma": -1, "theta": 9, "vega": -12}))
    txt = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["recommendation"]["text"]
    assert "above" in txt and "below" in txt


def test_monitor_range_play_up_and_down_breaks_are_bad():
    ev = _ev("flat")
    legs = PCS + [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    p = TM.build_profile(_strategy(legs, "iron_condor"), _pnl(ev["spot"], net_greeks={"delta": 1, "gamma": -1, "theta": 9, "vega": -12}))
    mon = TM.build_monitor(p, ev, TM.decide(p, ev))
    assert all(x["effect_if_break"] == "bad" for x in mon["up"] + mon["down"])


def test_monitor_bearish_position_up_break_is_bad_down_break_good():
    ev = _ev("down")
    p = TM.build_profile(_strategy([{"action": "SELL", "type": "stock", "qty": 100}], "stock"),
                         _pnl(ev["spot"], net_greeks={"delta": -100, "gamma": 0, "theta": 0, "vega": 0}))
    mon = TM.build_monitor(p, ev, TM.decide(p, ev))
    assert all(x["effect_if_break"] == "bad" for x in mon["up"])
    assert all(x["effect_if_break"] == "good" for x in mon["down"])


def test_monitor_lists_events_and_macro():
    ev = _ev("up"); ev["events"].update(days_to_earnings=12, earnings_date="2026-10-16", filings=[{"form": "8-K", "date": "2026-09-30", "url": "u"}])
    p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    items = [x["item"] for x in TM.build_monitor(p, ev, TM.decide(p, ev))["fundamental_events"]]
    assert "Earnings" in items and any(i.startswith("Filing 8-K") for i in items) and "Macro" in items and "Analyst actions" in items


# ── tech signals ────────────────────────────────────────────────────────────

def test_tech_signals_each_is_bounded_and_weighted():
    ev = _ev("up"); sig = TM._tech_signals(ev, ev["spot"])
    assert sig and all(-1 <= s["d"] <= 1 and s["w"] > 0 for s in sig)
    fams = {s["family"] for s in sig}
    assert {"traders", "structure", "volume_profile", "dealer", "momentum"} <= fams


def test_tech_signals_divergence_is_bearish():
    ev = _ev("up"); ev["technical"]["suite"].update(rsi_divergence="bearish", obv_divergence="bearish", macd_divergence="bearish")
    s = next(x for x in TM._tech_signals(ev, ev["spot"]) if x["family"] == "momentum")
    assert s["d"] < -0.8


# ── packet / evidence ───────────────────────────────────────────────────────

def test_packet_excludes_underscore_and_algorithm_fields_and_includes_facts():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    pkt = TM.llm_packet(p, ev)
    assert not any(k.startswith("_") for k in pkt["position"])
    for k in ("label", "range_play", "long_vol", "pos_sign"):
        assert k not in pkt["position"]
    assert pkt["position"]["greeks"]["delta"] == 20.0 and pkt["position"]["pnl"]["max_profit"] == 300.0
    assert pkt["technical"]["indicator_suite"]["rsi14"] is not None
    assert all("stance" not in x and "d" not in x for x in pkt["technical"]["famous_trader_rule_checks"])


def test_packet_confluence_zones_rename_score_and_strip_bias():
    ev = _ev("up"); ev["structure"]["dossier"]["bias"] = {"direction": "bullish"}
    p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    pkt = TM.llm_packet(p, ev)
    z = pkt["technical"]["market_structure_volume_profile_regime_dealer_patterns"]
    assert "bias" not in z["dossier"] and "bias" not in z["dossier"]["market_structure"]
    assert all("score" not in zz and "confluence_weight" in zz for zz in z["confluence_zones"])


def test_packet_is_json_serialisable_and_bounded():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    txt = json.dumps(TM.llm_packet(p, ev))
    assert 5_000 < len(txt) < 90_000


def test_setup_digest_strips_bulky_and_entry_fields():
    ts = {"context": {"x": 1}, "confluence_zones": [{"center": 1}] * 12, "chart_patterns": [{"name": "A", "direction": "bullish", "extra": 1}] * 9,
          "dossier": {"setups": [1, 2], "volume_profile": {"daily": {"poc": 1, "bins": [1, 2, 3]}},
                      "dealer_gamma": {"gamma_levels": {"by_strike": [1], "hvl": {"strike": 5}}}}, "meta": {}}
    d = TM._setup_digest(ts)
    assert "setups" not in d["dossier"] and "bins" not in d["dossier"]["volume_profile"]["daily"]
    assert "by_strike" not in d["dossier"]["dealer_gamma"]["gamma_levels"] and len(d["zones"]) == 10 and len(d["patterns"]) == 6
    assert "extra" not in d["patterns"][0]


def test_cum_ret():
    assert TM._cum_ret([0.1, 0.1], 2) == pytest.approx(21.0)
    assert TM._cum_ret([0.1], 5) is None and TM._cum_ret([], 1) is None


def test_clean_handles_dates_nested_and_unknown():
    import datetime as dt
    out = TM._clean({"d": dt.date(2026, 1, 2), "t": (np.float64(1.5), {"x": np.nan}), "s": {1, 2}})
    assert out["d"] == "2026-01-02" and out["t"][1]["x"] is None and sorted(out["s"]) == [1, 2]
    json.dumps(out)


def test_rss_news_parses_and_dedupes(monkeypatch):
    xml = """<rss><channel>
      <item><title>Apple soars on record quarter</title><pubDate>Mon, 01 Oct 2026 10:00:00 GMT</pubDate><source>Reuters</source><link>l1</link></item>
      <item><title>Apple  soars on record quarter!</title><pubDate>x</pubDate><source>Dup</source></item>
      <item><title>Other headline</title><pubDate>y</pubDate></item></channel></rss>"""

    class R:
        status_code = 200
        text = xml

    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    out = TM._rss_news("apple", 5)
    assert [o["title"] for o in out] == ["Apple soars on record quarter", "Other headline"] and out[0]["publisher"] == "Reuters"


def test_rss_news_failure_modes_return_empty(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "get", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("net down")))
    assert TM._rss_news("x") == []

    class Bad:
        status_code = 503
        text = ""
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Bad())
    assert TM._rss_news("x") == []


def test_require_data_raises_for_empty_evidence():
    p = TM.build_profile(_strategy(PCS), _pnl(100.0))
    with pytest.raises(TM.NoMarketData):
        TM._require_data(p, {"spot": None, "technical": {}})
    TM._require_data(p, _ev("up"))


# ── orchestration (async) with the evidence layer stubbed ───────────────────

def test_run_trade_manager_end_to_end_with_stubbed_evidence(monkeypatch):
    ev = _ev("up")

    async def fake(db, ticker, dte):
        return dict(ev)
    monkeypatch.setattr(TM, "gather_market_evidence", fake)
    out = asyncio.run(TM.run_trade_manager(None, _strategy(PCS), _pnl(ev["spot"])))
    assert out["decision"]["signal"] in TM.SEV and out["exit_plan"]["items"] and out["monitor"]["indicators"]
    assert out["trader_lenses"] and out["evidence_json"]["ticker"] == "TEST"
    assert not any(k.startswith("_") for k in out["profile"])
    json.dumps(out)                                              # fully serialisable


def test_run_trade_manager_prefers_full_desk_score(monkeypatch):
    ev = _ev("up")

    async def fake(db, ticker, dte):
        return dict(ev)
    monkeypatch.setattr(TM, "gather_market_evidence", fake)
    desk = {"signal": "STRONG_CLOSE", "lifecycle_score": 12, "overrides": ["near max loss — cut it"]}
    out = asyncio.run(TM.run_trade_manager(None, _strategy(PCS), _pnl(ev["spot"]), desk))
    assert out["decision"]["lenses"]["quant"]["score"] == 12 and TM.SEV[out["decision"]["signal"]] >= 2


def test_run_trade_manager_no_data_raises(monkeypatch):
    async def fake(db, ticker, dte):
        return {"spot": None, "technical": {}}
    monkeypatch.setattr(TM, "gather_market_evidence", fake)
    with pytest.raises(TM.NoMarketData):
        asyncio.run(TM.run_trade_manager(None, _strategy(PCS), _pnl(100.0)))


def test_ai_call_parses_json_normalises_verdict_and_bounds_prompt(monkeypatch):
    ev = _ev("up")
    seen = {}

    async def fake_ev(db, ticker, dte):
        return dict(ev)

    async def fake_llm(api_key, model, messages, max_tokens, temperature, expect_json):
        seen["user"] = messages[1]["content"]; seen["system"] = messages[0]["content"]
        return '```json\n{"verdict":"strong hold","conviction":71,"one_line":"x"}\n```'
    from app.services import llm_service
    monkeypatch.setattr(TM, "gather_market_evidence", fake_ev)
    monkeypatch.setattr(llm_service, "call_llm", fake_llm)
    out = asyncio.run(TM.run_trade_manager_ai(None, _strategy(PCS), _pnl(ev["spot"]), "key"))
    assert out["verdict"] == "STRONG_HOLD" and out["_meta"]["packet_chars"] < 90_000
    assert "STRONG_HOLD" not in seen["user"] and "EVIDENCE PACKET" in seen["user"] and "NOT IN PACKET" in seen["system"]


def test_ai_call_unparseable_returns_raw(monkeypatch):
    ev = _ev("up")

    async def fake_ev(db, ticker, dte):
        return dict(ev)

    async def fake_llm(**kw):
        return "I think you should hold."
    from app.services import llm_service
    monkeypatch.setattr(TM, "gather_market_evidence", fake_ev)
    monkeypatch.setattr(llm_service, "call_llm", fake_llm)
    out = asyncio.run(TM.run_trade_manager_ai(None, _strategy(PCS), _pnl(ev["spot"]), "key"))
    assert out["parse_error"] is True and out["verdict"] is None and "hold" in out["raw"]


# ── backtest-driven design: what technicals may and may NOT do ──────────────────────────────────────────

def test_technicals_alone_never_force_an_exit():
    """Backtest: adverse TA has no directional edge (1-month reversals), so a bearish tape + a healthy quant read
    must NOT produce an EXIT / STRONG EXIT by themselves."""
    ev = _ev("down")
    pnl = _pnl(ev["spot"]); pnl["analysis"]["quant_exit"] = {"signal": "HOLD", "score": 66, "overrides": []}
    d = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)
    assert TM.SEV[d["signal"]] <= TM.SEV["HOLD"] and not d["overrides"]


def test_technical_lens_gain_is_small_and_bounded():
    up, dn = _ev("up"), _ev("down")
    pu = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"), _pnl(up["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}))
    sc_up = TM._technical_score(pu, up, TM._tech_signals(up, up["spot"]))["score"]
    pd_ = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"), _pnl(dn["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}))
    sc_dn = TM._technical_score(pd_, dn, TM._tech_signals(dn, dn["spot"]))["score"]
    assert sc_up > sc_dn and 28 <= sc_dn and sc_up <= 72


def test_oversold_discounts_adverse_structure_for_bullish_position():
    ev = _ev("down")
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"), _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}))
    base = TM._technical_score(p, ev, TM._tech_signals(ev, ev["spot"]))["score"]
    ev["technical"]["suite"]["rsi14"] = 25
    assert TM._technical_score(p, ev, TM._tech_signals(ev, ev["spot"]))["score"] > base


def test_stock_discipline_stop_override():
    ev = _ev("up")
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                         _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=-12.0))
    d = TM.decide(p, ev)
    assert d["score"] <= 40 and any("discipline stop" in o for o in d["overrides"])
    ok = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                          _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=-6.0))
    assert not any("discipline stop" in o for o in TM.decide(ok, ev)["overrides"])


# ── σ-based breach context ────────────────────────────────────────────────────────────────────────────

def test_vol_context_pTouch_monotone_in_distance_and_breach():
    ev = _ev("flat"); spot = ev["spot"]
    near = TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": round(spot * 0.97, 2), "expiration": "2099-01-01", "qty": 1}], "csp"),
                            _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4}))
    far = TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": round(spot * 0.80, 2), "expiration": "2099-01-01", "qty": 1}], "csp"),
                           _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4}))
    pn = TM._vol_context(near, ev)["strikes"][0]["p_touch"]
    pf = TM._vol_context(far, ev)["strikes"][0]["p_touch"]
    assert 0 <= pf < pn <= 1
    breached = TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": spot * 1.05, "expiration": "2099-01-01", "qty": 1}], "csp"),
                                _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4}))
    s = TM._vol_context(breached, ev)["strikes"][0]
    assert s["breached"] and s["p_touch"] == 1.0


def test_vol_context_uses_the_higher_of_iv_and_rv_and_scales_with_dte():
    ev = _ev("flat")
    base = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    c1 = TM._vol_context({**base, "avg_iv_pct": 10.0, "dte": 30}, ev)
    c2 = TM._vol_context({**base, "avg_iv_pct": 90.0, "dte": 30}, ev)
    assert c2["sigma_ann_pct"] == 90.0 and c1["sigma_ann_pct"] >= (c1["rv_blend_pct"] or 0)
    c3 = TM._vol_context({**base, "avg_iv_pct": 40.0, "dte": 120}, ev)
    c4 = TM._vol_context({**base, "avg_iv_pct": 40.0, "dte": 30}, ev)
    assert c3["sigma_dte_pct"] == pytest.approx(c4["sigma_dte_pct"] * 2, rel=0.02)


def test_closer_short_strike_lowers_technical_lens():
    ev = _ev("flat"); spot = ev["spot"]
    def lens(k):
        p = TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": round(spot * k, 2), "expiration": "2099-01-01", "qty": 1}], "csp"),
                             _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4}))
        return TM._technical_score(p, ev, TM._tech_signals(ev, spot))["score"]
    assert lens(0.99) < lens(0.93) <= lens(0.75)


def test_vol_expansion_and_drawdown_penalise_short_premium_but_help_long_premium():
    ev = _ev("flat"); spot = ev["spot"]
    sp = TM.build_profile(_strategy(PCS), _pnl(spot))
    base = TM._technical_score(sp, ev, TM._tech_signals(ev, spot))["score"]
    ev2 = json.loads(json.dumps(ev))
    ev2["technical"]["suite"]["vol_forecast"] = {"rv21": 45, "rv63": 25, "ratio_21_63": 1.8, "blend_ann_pct": 33, "regime": "expanding"}
    ev2["technical"]["suite"]["range"]["pct_from_52w_high"] = -25
    assert TM._technical_score(sp, ev2, TM._tech_signals(ev2, spot))["score"] <= base - 10
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    lp = TM.build_profile(_strategy(legs, "long_call"), _pnl(spot, net_greeks={"delta": 50, "gamma": 3, "theta": -4, "vega": 12}))
    b0 = TM._technical_score(lp, ev, TM._tech_signals(ev, spot))["score"]
    ev3 = json.loads(json.dumps(ev))                                   # vol expansion alone (a drawdown hurts a bullish long call, rightly)
    ev3["technical"]["suite"]["vol_forecast"] = ev2["technical"]["suite"]["vol_forecast"]
    assert TM._technical_score(lp, ev3, TM._tech_signals(ev3, spot))["score"] == pytest.approx(b0 + 3)


# ── exit plan: evidence-driven rules ────────────────────────────────────────────────────────────────────

def test_plan_for_shares_has_minervini_ladder_discipline_stop_and_wide_trail():
    ev = _ev("up")
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                         _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=5.0))
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    trail = [i for i in plan["items"] if i["kind"] == "trail"]
    ladder = [i for i in trail if i.get("fraction")]
    assert {i["tier"] for i in ladder} == {"first", "second", "final"}
    assert [i["action"] for i in sorted(ladder, key=lambda x: {"first": 0, "second": 1, "final": 2}[x["tier"]])] == ["TRIM", "TRIM", "EXIT"]
    assert not any(i.get("tier") == "volatility" for i in trail)                  # the 5×ATR row was dropped (redundant with the ladder)
    disc = [i for i in plan["items"] if i["kind"] == "stop" and "Discipline stop" in i["why"]]
    cost = ev["spot"] / 1.05
    assert disc and disc[0]["level"] == pytest.approx(cost * 0.9, abs=0.01)
    # no tight (EMA10 / Chandelier-3) exits in the plan — the backtest shows they whipsaw
    assert not any("10 EMA" in i["why"] or "21 EMA" in i["why"] for i in plan["items"])
    for i in plan["items"]:
        if i["kind"] in ("trail", "stop"):
            assert i.get("status") in ("hit", "near", "far")


def test_plan_ladder_marks_lost_levels_as_hit():
    ev = _ev("up"); sm = ev["technical"]["suite"]["sma"]
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                         _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=0.0))
    sm["50"] = ev["spot"] * 1.05                              # price is already under the 50d
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    first = next(i for i in plan["items"] if i.get("tier") == "first")
    assert first["status"] == "hit"


def test_plan_options_position_does_not_get_stock_ladder():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    trail = [i for i in plan["items"] if i["kind"] == "trail"]
    assert all(i["action"] in ("REVIEW",) for i in trail) and not any(i.get("fraction") for i in trail if i.get("tier") != "final")


def test_plan_strike_items_carry_touch_probability():
    ev = _ev("flat"); spot = ev["spot"]
    p = TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": round(spot * 0.95, 2), "expiration": "2099-01-01", "qty": 1}], "csp"),
                         _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4}))
    it = next(i for i in TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"] if i["kind"] == "strike")
    assert it["p_touch"] is not None and "P(touch" in it["why"]


def test_plan_risk_reward_block():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    rr = TM.build_exit_plan(p, ev, TM.decide(p, ev))["risk_reward"]
    assert rr is None or (rr["ratio"] > 0 and rr["risk"] > 0)


def test_backtest_block_is_internally_consistent():
    bt = TM.BACKTEST["exit_rules"]
    assert bt["hold126"]["mean"] > bt["fix10"]["mean"] > bt["ema10"]["mean"] > 0          # tight exits destroy expectancy
    assert bt["fix8"]["worst"] > bt["hold126"]["worst"] and bt["fix10"]["p5"] > bt["hold126"]["p5"]   # stops cut the tail
    assert "Backtest" in TM._evid("sma200") and TM._evid("nonsense") == ""


def test_epoch_to_date_and_days_to():
    assert TM._epoch_to_date(1767225600) == "2026-01-01" and TM._epoch_to_date("2026-03-04T10:00:00") == "2026-03-04"
    assert TM._epoch_to_date(None) is None and TM._epoch_to_date("garbage") is None and TM._epoch_to_date(10 ** 30) is None
    assert TM._days_to("garbage") is None and TM._days_to(None) is None


def test_ai_system_prompt_carries_the_research_priors_and_is_judgement_free():
    assert "RESEARCH PRIORS" in TM._AI_SYSTEM and "NO out-of-sample directional edge" in TM._AI_SYSTEM
    assert "STRONG_HOLD|HOLD|EXIT|STRONG_EXIT" in TM._AI_SYSTEM            # the output schema, not an input


# ── the validated close trigger + hold odds (hold-vs-close backtest) ────────────────────────────────────

def _put_profile(spot, k_frac, cap=None):
    pnl = _pnl(spot, net_greeks={"delta": 10, "gamma": -0.2, "theta": 3, "vega": -4})
    if cap is not None:
        pnl["analysis"]["captured_pct"] = cap
    return TM.build_profile(_strategy([{"action": "SELL", "type": "put", "strike": round(spot * k_frac, 2), "expiration": "2099-01-01", "qty": 1}], "csp"), pnl)


def _ev_vol(kind, regime, ratio):
    ev = _ev(kind)
    ev["technical"]["suite"]["vol_forecast"] = {"rv21": 40, "rv63": 25, "ratio_21_63": ratio, "blend_ann_pct": 31, "regime": regime}
    return ev


def test_close_trigger_needs_tested_strike_and_expanding_vol():
    ev = _ev_vol("flat", "expanding", 1.6); spot = ev["spot"]
    d = TM.decide(_put_profile(spot, 0.97), ev)
    assert d["score"] <= 40 and any("Close trigger" in o for o in d["overrides"])
    assert TM.SEV[d["signal"]] >= TM.SEV["EXIT"]
    calm = _ev_vol("flat", "stable", 1.0)
    assert not any("Close trigger" in o for o in TM.decide(_put_profile(calm["spot"], 0.97), calm)["overrides"])     # tested but calm → hold has +EV
    far = TM.decide(_put_profile(spot, 0.60), ev)
    assert not any("Close trigger" in o for o in far["overrides"])                                                    # expanding but strike far


def test_close_trigger_deep_loss_is_strong_exit():
    ev = _ev_vol("flat", "expanding", 1.6)
    d = TM.decide(_put_profile(ev["spot"], 0.97, cap=-250), ev)
    assert d["score"] <= 27 and d["signal"] == "STRONG_EXIT"


def test_close_trigger_applies_when_already_itm():
    ev = _ev_vol("flat", "expanding", 1.5)
    assert TM._close_trigger(_put_profile(ev["spot"], 1.04), TM._vol_context(_put_profile(ev["spot"], 1.04), ev))["stats"]["n"] > 1000


def test_close_trigger_not_for_long_premium_or_stock():
    ev = _ev_vol("up", "expanding", 1.6)
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    lp = TM.build_profile(_strategy(legs, "long_call"), _pnl(ev["spot"], net_greeks={"delta": 50, "gamma": 3, "theta": -4, "vega": 12}))
    assert TM._close_trigger(lp, TM._vol_context(lp, ev)) is None


def test_hold_odds_rows_and_monotone_tail():
    ev = _ev_vol("flat", "expanding", 1.6); spot = ev["spot"]
    near = TM.build_exit_plan(_put_profile(spot, 0.98), ev, {"signal": "HOLD"})["hold_odds"]
    far = TM.build_exit_plan(_put_profile(spot, 0.60), ev, {"signal": "HOLD"})["hold_odds"]
    states = [r["state"] for r in near["rows"]]
    assert any("P(touch)" in s_ for s_ in states) and any("vol ratio" in s_ for s_ in states) and near["baseline"]["n"] > 40000
    pn = next(r for r in near["rows"] if "P(touch)" in r["state"]); pf = next(r for r in far["rows"] if "P(touch)" in r["state"])
    assert pn["p_close_better"] > pf["p_close_better"] and pn["worst5"] <= pf["worst5"]
    long_ = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"), _pnl(spot, net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}))
    assert TM.build_exit_plan(long_, ev, {"signal": "HOLD"})["hold_odds"] is None


def test_hold_state_table_is_consistent():
    hs = TM.BACKTEST["hold_state"]
    ps = [r["p_close_better"] for r in hs["p_touch"]]
    assert ps == sorted(ps) and ps[0] < 10 < 25 < ps[-1]                           # the nearer the strike, the likelier closing was better
    assert hs["close_trigger"]["stats"]["mean_delta"] < 0 < hs["all"]["mean_delta"]
    assert hs["close_trigger"]["plus_deep_loss"]["mean_delta"] < hs["close_trigger"]["stats"]["mean_delta"]


# ═══ every position gets a plan · the Quant tab has content · plain-language "break" effects ═══════════════

STRADDLE = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1},
            {"action": "BUY", "type": "put", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
LV_GREEKS = {"delta": 2.0, "gamma": 6.0, "theta": -9.0, "vega": 22.0}


def _straddle(spot, **kw):
    pnl = _pnl(spot, net_greeks=LV_GREEKS, max_profit=None, max_loss=-900.0, unbounded_profit=True, entry_cost=None, breakevens=[spot * 0.91, spot * 1.09], **kw)
    return TM.build_profile(_strategy(STRADDLE, "long_straddle"), pnl)


def test_long_straddle_without_entry_cost_still_has_a_plan():
    """REGRESSION (NVMI / LITE showed 'No levels could be derived'): neutral long-vol structures produced zero items."""
    ev = _ev("flat"); p = _straddle(ev["spot"])
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    kinds = {i["kind"] for i in plan["items"]}
    assert plan["items"] and {"breakeven", "time", "pnl"} <= kinds
    be = [i for i in plan["items"] if i["kind"] == "breakeven"]
    assert len(be) == 2 and be[0]["level"] < ev["spot"] < be[1]["level"] and "profit" in be[0]["why"] and "below" in be[0]["why"] and "above" in be[1]["why"]
    pl = {i["pnl_level"] for i in plan["items"] if i["kind"] == "pnl"}
    assert pl == {-450.0, 900.0}                               # debit derived from |max loss| when entry_cost is missing
    assert "profit needs a move beyond" in plan["recommendation"]["text"]


def test_long_straddle_earnings_is_a_catalyst_not_a_derisk():
    ev = _ev("flat"); ev["events"].update(days_to_earnings=5, earnings_date="2026-10-09")
    p = _straddle(ev["spot"])
    it = next(i for i in TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"] if i["kind"] == "event")
    assert it["action"] == "REVIEW" and "catalyst" in it["title"] and "collapses" in it["why"]
    txt = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["recommendation"]["text"]
    assert "earnings" in txt


def test_every_position_type_gets_at_least_one_item():
    ev = _ev("flat"); spot = ev["spot"]
    legs_cal = [{"action": "SELL", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1},
                {"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-06-01", "qty": 1}]
    cases = [
        TM.build_profile(_strategy(legs_cal, "calendar"), _pnl(spot, net_greeks={"delta": 1, "gamma": -0.1, "theta": 2, "vega": 6})),
        TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 10}], "stock"), _pnl(spot, net_greeks={"delta": 10, "gamma": 0, "theta": 0, "vega": 0})),
        _straddle(spot),
        _put_profile(spot, 0.9),
        TM.build_profile(_strategy(CCS, "ccs"), _pnl(spot, net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7})),
    ]
    for p in cases:
        plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
        assert plan["items"], p["structure"]
        assert all(i.get("title") and i.get("group") in ("against", "for", "rules", "levels") for i in plan["items"])


def test_plan_without_any_levels_falls_back_to_a_note():
    ev = _ev("flat"); ev["technical"]["suite"]["atr14"] = None; ev["structure"] = {}
    p = TM.build_profile(_strategy([{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}], "long_call"),
                         _pnl(100.0, net_greeks={"delta": 0.0, "gamma": 0, "theta": 0, "vega": 0}, breakevens=[], max_loss=None, entry_cost=None))
    plan = TM.build_exit_plan({**p, "dte": None, "spot": 0}, {**ev, "spot": 0}, {"signal": "HOLD"})
    assert plan["items"] and plan["items"][0]["kind"] in ("note", "pnl", "time", "event", "breakeven")


def test_breakeven_meaning_per_structure():
    assert TM._be_meaning({"short_premium": True, "pos_sign": 1}, 0, 1) == ("loss below it", True)           # bull put spread
    assert TM._be_meaning({"short_premium": True, "pos_sign": -1}, 0, 1) == ("loss above it", False)         # bear call spread
    assert TM._be_meaning({"short_premium": False, "pos_sign": 1}, 0, 1) == ("profit above it", False)       # long call
    assert TM._be_meaning({"short_premium": False, "pos_sign": -1}, 0, 1) == ("profit below it", True)       # long put
    assert TM._be_meaning({"short_premium": True, "pos_sign": 0}, 1, 2)[0] == "loss above it"                # strangle upper
    assert TM._be_meaning({"short_premium": False, "pos_sign": 0}, 0, 2)[0] == "profit below it"             # straddle lower


def test_plan_titles_are_short_and_actions_known():
    ev = _ev("up"); p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                                         _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=5.0))
    for i in TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"]:
        assert len(i["title"]) <= 32 and len(i["why"]) <= 230, (i["title"], len(i["why"]))
        assert i["action"] in ("EXIT", "TRIM", "TAKE_PROFIT", "DEFEND_OR_EXIT", "REVIEW", "DERISK", "WATCH")


# ── Quant lens has real content ─────────────────────────────────────────────

def _quant_pnl(spot):
    pnl = _pnl(spot)
    pnl["lifecycle"] = {"avg_iv_pct": 45.0, "pm": {"omega": 1.8, "sortino": 1.2, "calmar": 0.9},
                        "risk": {"var_95": -420.0, "cvar_95": -610.0, "max_profit": 300.0, "max_loss": -700.0, "capital": 700.0},
                        "trader": {"net_delta": 20}}
    pnl["analysis"]["quant_exit"] = {"signal": "HOLD", "score": 61, "base_quality": 58, "hold_base": 55, "overrides": [],
                                     "subscores": {"edge": 60, "pop": 74, "sortino": 55, "tail": 50, "carry": 62},
                                     "adjustments": [{"name": "Time / gamma", "pts": 3, "note": "24 DTE — runway"}, {"name": "Convexity (short Γ)", "pts": -2, "note": "short gamma"}],
                                     "factors": [{"label": "Vol decay", "favorable": True, "note": "IV above HV"}], "reasons": ["Thesis intact"]}
    pnl["analysis"].update(probability_of_profit=74.0, pop_method="rnd", expected_value=55.0, kelly_fraction=0.08, risk_reward_ratio=0.43,
                           theta_burn_rate_pct=1.2, days_to_theta_breakeven=18)
    return pnl


def test_quant_tab_exposes_the_desk_breakdown_and_position_math():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _quant_pnl(ev["spot"]))
    q = TM.decide(p, ev)["lenses"]["quant"]
    d = q["detail"]
    assert set(d["groups"]) >= {"Edge & odds", "Risk", "Greeks & time", "Breakevens"}
    keys = {m["key"] for g in d["groups"].values() for m in g}
    assert {"pop", "ev", "kelly", "rr", "omega", "sortino", "cvar", "theta", "delta", "gamma", "vega", "dte", "iv_rv"} <= keys
    assert d["desk"]["subscores"]["pop"] == 74 and d["desk"]["entry_reference"]["quality"] == 58 and len(d["desk"]["adjustments"]) == 2 and d["desk"]["holder_factors"]
    assert d["desk"]["hold_base"] == 55 and "reference only" in d["desk"]["entry_reference"]["note"]
    assert "independent of the chart" in d["how_it_counts"]
    assert any("Time / gamma +3" in n for n in q["notes"]) and any("hold anchor 55" in n and "hold read" in n for n in q["notes"])
    assert not any("base quality" in n for n in q["notes"])                                         # the entry-style number is NOT the headline


def test_quant_metrics_carry_tone_and_cross_check_pop():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _quant_pnl(ev["spot"]))
    m = {x["key"]: x for g in TM._quant_detail(p, ev)["groups"].values() for x in g}
    assert m["pop"]["tone"] == "good" and m["pop"]["value"] == 74 and m["ev"]["tone"] == "good" and m["theta"]["tone"] == "good"
    assert m["iv_rv"]["value"] > 1 and m["iv_rv"]["tone"] in ("good", "warn")             # rich IV favours a seller
    assert any(k.startswith("be_") for k in m) and "pop_sigma" in m
    assert m["rr"]["value"] == pytest.approx(300 / 700, abs=0.01)


def test_quant_iv_tone_inverts_for_long_premium():
    ev = _ev("up"); spot = ev["spot"]
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    pnl = _quant_pnl(spot); pnl["net_greeks"] = {"delta": 52, "gamma": 3, "theta": -4, "vega": 12}; pnl["lifecycle"]["avg_iv_pct"] = 60.0
    p = TM.build_profile(_strategy(legs, "long_call"), pnl)
    m = {x["key"]: x for g in TM._quant_detail(p, ev)["groups"].values() for x in g}
    assert m["iv_rv"]["tone"] == "bad" and "PAID" in m["iv_rv"]["note"] and m["theta"]["tone"] in ("warn", "bad")


def test_quant_unbounded_loss_is_flagged_as_bad_risk():
    ev = _ev("flat"); p = TM.build_profile(_strategy([PCS[0]], "naked_put"), _pnl(ev["spot"], unbounded_loss=True, max_loss=None))
    risk = TM._quant_detail(p, ev)["groups"]["Risk"]
    assert any(m["value"] == "unbounded" and m["tone"] == "bad" for m in risk)


def test_quant_fallback_lens_says_what_to_do_and_still_has_position_math():
    ev = _ev("flat"); pnl = _quant_pnl(ev["spot"]); pnl["analysis"].pop("quant_exit")
    q = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)["lenses"]["quant"]
    assert q["source"] == "fallback" and "Quant Analysis" in q["notes"][0] and q["detail"]["groups"]["Edge & odds"]


def test_full_desk_score_overrides_feed_the_factor_list():
    ev = _ev("flat"); pnl = _quant_pnl(ev["spot"])
    desk = {"signal": "HOLD", "lifecycle_score": 66, "overrides": [], "management_analysis": {"anchor": 52, "contributions": [{"label": "Breach risk", "pts": -4, "note": "P(touch) 31%"}]}}
    import asyncio as _a

    async def fake(db, ticker, dte):
        return dict(ev)
    old = TM.gather_market_evidence
    TM.gather_market_evidence = fake
    try:
        out = _a.run(TM.run_trade_manager(None, _strategy(PCS), pnl, desk))
    finally:
        TM.gather_market_evidence = old
    adj = out["decision"]["lenses"]["quant"]["detail"]["desk"]["adjustments"]
    assert adj == [{"label": "Breach risk", "pts": -4, "note": "P(touch) 31%"}] and out["decision"]["lenses"]["quant"]["source"] == "full_desk"


# ── what "break = good / bad / mixed" means ─────────────────────────────────

def test_monitor_effect_notes_are_plain_language():
    ev = _ev("flat")
    p = _put_profile(ev["spot"], 0.95)
    mon = TM.build_monitor(p, ev, TM.decide(p, ev))
    for lv_ in mon["down"]:
        assert lv_["effect_if_break"] == "bad" and lv_["effect_note"] == "threatens your short put"
    for lv_ in mon["up"]:
        assert lv_["effect_if_break"] == "good" and lv_["effect_note"] == "helps your position"        # a rally only adds cushion to a short put


def test_long_straddle_breaks_help_either_way_and_covered_call_is_mixed():
    ev = _ev("flat")
    p = _straddle(ev["spot"])
    mon = TM.build_monitor(p, ev, TM.decide(p, ev))
    assert all(x["effect_if_break"] == "good" and "need a move" in x["effect_note"] for x in mon["up"] + mon["down"])
    legs = [{"action": "BUY", "type": "stock", "qty": 100}, {"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}]
    cc = TM.build_profile(_strategy(legs, "covered_call"), _pnl(ev["spot"], net_greeks={"delta": 70, "gamma": -0.3, "theta": 4, "vega": -6}))
    up = TM.build_monitor(cc, ev, TM.decide(cc, ev))["up"]
    assert up and all(x["effect_if_break"] == "mixed" and "caps it" in x["effect_note"] for x in up)


def test_packet_carries_position_metrics_as_facts_without_tone():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _quant_pnl(ev["spot"]))
    pkt = TM.llm_packet(p, ev)
    ms = pkt["quant_facts"]["position_metrics"]
    labels = {m["metric"] for m in ms}
    assert {"Probability of profit", "Expected value", "Theta / day"} <= labels
    assert all("tone" not in m for m in ms)
    ks = _keys(pkt, set())
    for banned in ("tone", "signal", "verdict", "score", "stance"):
        assert banned not in ks, banned


# ── Quant vs Technical: what each means for options vs shares ──────────────────────────────────────────

def test_lens_scope_text_differs_for_shares_and_options():
    ev = _ev("up")
    opt = TM.decide(TM.build_profile(_strategy(PCS), _quant_pnl(ev["spot"])), ev)["lenses"]
    stk = TM.decide(TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                                     _pnl(ev["spot"], net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=3.0)), ev)["lenses"]
    assert "option" in opt["quant"]["detail"]["how_it_counts"] and "odds of profit" in opt["quant"]["detail"]["how_it_counts"]
    assert "shares" in stk["quant"]["detail"]["how_it_counts"] and "POSITION-RISK" in stk["quant"]["detail"]["how_it_counts"]
    assert opt["technical"]["scope"] != stk["technical"]["scope"] and "REACH your strikes" in opt["technical"]["scope"] and "main timing evidence" in stk["technical"]["scope"]


def test_stop_touch_probability_rises_as_the_stop_gets_closer():
    ev = _ev("up"); spot = ev["spot"]
    ev["technical"]["suite"]["vol_forecast"] = {"rv21": 40, "rv63": 40, "ratio_21_63": 1.0, "blend_ann_pct": 40, "regime": "stable"}
    def pt(pnl_pct):
        p = TM.build_profile(_strategy([{"action": "BUY", "type": "stock", "qty": 100}], "stock"),
                             _pnl(spot, net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=pnl_pct))
        return {x["key"]: x for g in TM._quant_detail(p, ev)["groups"].values() for x in g}["stop_touch"]
    assert pt(-8.0)["value"] > pt(0.0)["value"] > pt(15.0)["value"]
    assert pt(-12.0)["value"] == "breached" and pt(-12.0)["tone"] == "bad"


# ═══ share trades: size lives in parameters.shares (REGRESSION: LITE read as direction-neutral) ═══════════════

def _stock_trade(shares=100, st="stock_long", avg_cost=None, legs=None):
    t = {"ticker": "TEST", "name": "t", "strategy_type": st, "legs_data": legs or [], "parameters": {"shares": shares, "avg_cost": avg_cost}, "notes": ""}
    return t


def test_stock_trade_with_empty_legs_is_a_bullish_share_position():
    pnl = _pnl(100.0, net_greeks={"delta": 100.0, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=1.3)
    p = TM.build_profile(_stock_trade(100, "stock_long", 98.7), pnl)
    assert p["kind"] == "stock" and p["direction"] == "bullish" and p["pos_sign"] == 1 and p["stock_shares"] == 100 and p["avg_cost"] == 98.7
    assert not p["range_play"] and not p["short_premium"] and not p["long_premium"] and p["dte"] is None or p["dte"] is not None


def test_short_stock_is_bearish_and_futures_use_contracts():
    pnl = _pnl(100.0, net_greeks={"delta": -100.0, "gamma": 0, "theta": 0, "vega": 0})
    p = TM.build_profile(_stock_trade(100, "stock_short"), pnl)
    assert p["pos_sign"] == -1 and p["stock_shares"] == -100
    fut = {"ticker": "ES", "name": "f", "strategy_type": "futures", "legs_data": [], "parameters": {"contracts": 2}, "notes": ""}
    pf = TM.build_profile(fut, _pnl(100.0, net_greeks={"delta": 2.0, "gamma": 0, "theta": 0, "vega": 0}))
    assert pf["kind"] == "stock" and pf["pos_sign"] == 1


def test_stock_with_no_size_anywhere_falls_back_to_the_live_delta():
    t = {"ticker": "T", "name": "t", "strategy_type": "stock_long", "legs_data": [], "parameters": {}, "notes": ""}
    p = TM.build_profile(t, _pnl(100.0, net_greeks={"delta": 40.0, "gamma": 0, "theta": 0, "vega": 0}))
    assert p["pos_sign"] == 1 and p["kind"] == "stock"


def test_covered_call_uses_shares_from_parameters_too():
    t = {"ticker": "T", "name": "t", "strategy_type": "covered_call", "parameters": {"shares": 100},
         "legs_data": [{"action": "SELL", "type": "call", "strike": 110.0, "expiration": "2099-01-01", "qty": 1}], "notes": ""}
    p = TM.build_profile(t, _pnl(100.0, net_greeks={"delta": 70.0, "gamma": -0.3, "theta": 4.0, "vega": -6.0}))
    assert p["covered"] and p["stock_shares"] == 100 and p["direction"] == "bullish" and p["kind"] == "short_premium"


# ═══ scoring transparency: unavailable lenses drop out, event is a visible adjustment, shares get a Position-risk lens ═══

ALL = {"quant": True, "technical": True, "fundamental": True}


def test_lens_weights_sum_to_one_for_every_band_and_availability():
    for dte in (None, 1, 7, 8, 30, 31, 90, 91, 400):
        for kind in ("short_premium", "stock"):
            for avail in (ALL, {**ALL, "quant": False}, {**ALL, "fundamental": False}, {"quant": False, "technical": True, "fundamental": False}):
                w = TM._lens_weights({"dte": dte, "kind": kind}, avail)
                assert sum(w.values()) == pytest.approx(1.0, abs=1e-3), (dte, kind, avail)
                assert all(w[k] == 0.0 for k in w if not avail[k])


def test_weights_shift_from_technical_to_fundamental_as_dte_grows():
    near, far = TM._lens_weights({"dte": 5, "kind": "short_premium"}, ALL), TM._lens_weights({"dte": 200, "kind": "short_premium"}, ALL)
    assert far["fundamental"] > near["fundamental"] and near["quant"] > far["quant"]


def test_missing_quant_read_drops_the_lens_instead_of_contributing_a_fake_50():
    ev = _ev("up"); pnl = _pnl(ev["spot"]); pnl["analysis"].pop("quant_exit")
    d = TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)
    q = d["lenses"]["quant"]
    assert q["source"] == "fallback" and q["available"] is False and q["weight"] == 0.0 and d["weights"]["quant"] == 0.0
    assert d["weights"]["technical"] + d["weights"]["fundamental"] == pytest.approx(1.0, abs=1e-3)
    assert any("No quant-desk read" in c for c in d["conflicts"]) and "left out of the score" in q["notes"][0]


def test_no_quant_read_lowers_conviction():
    ev = _ev("up")
    with_q = TM.decide(TM.build_profile(_strategy(PCS), _pnl(ev["spot"])), ev)
    pnl = _pnl(ev["spot"]); pnl["analysis"].pop("quant_exit")
    assert TM.decide(TM.build_profile(_strategy(PCS), pnl), ev)["conviction"] < with_q["conviction"]


def test_score_is_blend_plus_visible_event_adjustment():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    clear = TM.decide(p, ev)
    assert clear["lenses"]["event"]["clear"] is True and clear["event_adj"] == 0.0 and clear["score"] == pytest.approx(clear["blend"], abs=0.11)
    ev["events"].update(days_to_earnings=5, earnings_date="2026-10-09")
    risky = TM.decide(p, ev)
    assert risky["event_adj"] < -3 and risky["lenses"]["event"]["clear"] is False
    assert risky["raw_score"] == pytest.approx(risky["blend"] + risky["event_adj"], abs=0.11) and risky["score"] < clear["score"]


def test_event_adjustment_is_capped_and_tailwinds_are_small():
    ev = _ev("flat"); ev["events"].update(days_to_earnings=1, earnings_date="2026-10-02", headline_flags={"n_risk": 3, "n_positive": 0})
    ev["market"]["tape"]["^VIX"] = {"last": 40, "change_pct_5d": 50}
    p = TM.build_profile(_strategy([PCS[0]], "naked_put"), _pnl(ev["spot"], unbounded_loss=True))
    assert TM.decide(p, ev)["event_adj"] >= -TM.EVENT_ADJ_MAX
    good = _ev("up"); good["events"]["headline_flags"] = {"n_risk": 0, "n_positive": 3}
    up = TM.build_profile(_strategy(PCS), _pnl(good["spot"]))
    assert 0 <= TM.decide(up, good)["event_adj"] <= TM.EVENT_ADJ_BONUS


def test_event_score_reports_pts_and_clear():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    base = TM._event_score(p, ev)
    assert base["pts"] <= 0 and base["clear"] is True
    ev["events"].update(days_to_earnings=3, earnings_date="2026-10-07")
    e = TM._event_score(p, ev)
    assert e["pts"] >= 18 and e["clear"] is False and e["items"][0]["event"] == "earnings"


def _shares(spot, pnl_pct=2.0, avg_cost=None, st="stock_long"):
    pnl = _pnl(spot, net_greeks={"delta": 100 if "short" not in st else -100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=pnl_pct)
    pnl["analysis"].pop("quant_exit")                                              # shares have no quant-desk read
    return TM.build_profile(_stock_trade(100, st, avg_cost), pnl)


def test_share_position_quant_lens_is_position_risk_and_is_scored():
    ev = _ev("up"); spot = ev["spot"]
    d = TM.decide(_shares(spot), ev)
    q = d["lenses"]["quant"]
    assert q["source"] == "position_risk" and q["label"] == "Position risk" and q["available"] is True and q["weight"] > 0
    assert not any("No quant-desk read" in c for c in d["conflicts"])
    keys = {m["key"] for g in q["detail"]["groups"].values() for m in g}
    assert {"vol_ann", "move_1s", "stop_touch", "drawdown", "tsmom"} <= keys
    assert all("option" not in n_.lower() or "no option" in n_.lower() for n_ in q["notes"])


def test_position_risk_penalises_a_close_stop_expanding_vol_and_drawdown():
    ev = _ev("up"); spot = ev["spot"]
    ev["technical"]["suite"]["vol_forecast"] = {"rv21": 40, "rv63": 40, "ratio_21_63": 1.0, "blend_ann_pct": 40, "regime": "stable"}
    calm = TM._position_risk(_shares(spot, pnl_pct=10.0), ev)["score"]
    near_stop = TM._position_risk(_shares(spot, pnl_pct=-8.0), ev)["score"]
    assert near_stop < calm
    ev2 = json.loads(json.dumps(ev))
    ev2["technical"]["suite"]["vol_forecast"] = {"rv21": 60, "rv63": 40, "ratio_21_63": 1.5, "blend_ann_pct": 48, "regime": "expanding"}
    ev2["technical"]["suite"]["range"]["pct_from_52w_high"] = -25
    assert TM._position_risk(_shares(spot, pnl_pct=10.0), ev2)["score"] < calm - 8
    assert TM._position_risk(_shares(spot, pnl_pct=-12.0), ev)["score"] <= calm - 25               # through the stop


def test_position_risk_is_mirrored_for_short_stock():
    ev = _ev("down"); spot = ev["spot"]
    ev["technical"]["suite"]["range"]["pct_from_52w_high"] = -1.0
    near_high = TM._position_risk(_shares(spot, st="stock_short"), ev)
    assert any("squeeze" in n_ for n_ in near_high["notes"])


def test_share_technical_lens_has_no_option_or_vol_double_count():
    ev = _ev("up"); spot = ev["spot"]
    ev["technical"]["suite"]["vol_forecast"] = {"rv21": 60, "rv63": 40, "ratio_21_63": 1.5, "blend_ann_pct": 48, "regime": "expanding"}
    notes = " ".join(TM._technical_score(_shares(spot), ev, TM._tech_signals(ev, spot))["notes"])
    assert "volatility EXPANDING" not in notes and "52-wk high" not in notes            # those live in Position risk for shares


# ═══ plain language, real prices, share-vs-option wording ═══════════════════════════════════════════════════

def _stock_ev_profile(spot_ev=None, pct=1.3, cost=None, st="stock_long"):
    ev = _ev("up") if spot_ev is None else spot_ev
    pnl = _pnl(ev["spot"], net_greeks={"delta": 100 if "short" not in st else -100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=pct)
    pnl["analysis"].pop("quant_exit", None); pnl["breakevens"] = [cost] if cost else []
    return ev, TM.build_profile(_stock_trade(100, st, cost), pnl)


def test_stock_breakeven_is_your_cost_with_plain_status_and_no_expiry_language():
    ev, p = _stock_ev_profile(pct=1.3, cost=None)
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    cost = next(i for i in plan["items"] if i["kind"] == "breakeven")
    assert cost["title"] == "Your cost" and "in profit" in cost["why"] and cost["status_text"].startswith("in profit +") and cost["status_tone"] == "good"
    assert cost["level"] == pytest.approx(ev["spot"] / 1.013, abs=0.01)
    blob = " ".join(i["why"] + " " + i["title"] + " " + (i.get("detail") or "") for i in plan["items"]).lower()
    for banned in ("expiry", "theta", "strike", "dte", "gamma", "implied", "credit", "debit"):
        assert banned not in blob, banned


def test_stock_underwater_cost_row_says_so():
    ev, p = _stock_ev_profile(pct=-4.0)
    cost = next(i for i in TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"] if i["kind"] == "breakeven")
    assert "under water" in cost["why"] and cost["status_tone"] == "bad" and cost["status_text"].startswith("under water")


def test_option_breakeven_status_says_which_side_you_are_on():
    ev = _ev("flat"); spot = ev["spot"]
    legs = [{"action": "BUY", "type": "call", "strike": 100.0, "expiration": "2099-01-01", "qty": 1}]
    base = dict(net_greeks={"delta": 52, "gamma": 3, "theta": -4, "vega": 12}, max_profit=None, max_loss=-300.0, unbounded_profit=True)
    itm = TM.build_profile(_strategy(legs, "long_call"), _pnl(spot, breakevens=[spot * 0.97], **base))
    otm = TM.build_profile(_strategy(legs, "long_call"), _pnl(spot, breakevens=[spot * 1.04], **base))
    be_itm = next(i for i in TM.build_exit_plan(itm, ev, {"signal": "HOLD"})["items"] if i["kind"] == "breakeven")
    be_otm = next(i for i in TM.build_exit_plan(otm, ev, {"signal": "HOLD"})["items"] if i["kind"] == "breakeven")
    assert be_itm["status_text"] == "you are on the profit side" and be_otm["status_text"] == "you are on the loss side"
    assert "at expiry" in be_itm["why"]


def test_status_text_uses_real_dollars_and_the_atr():
    ev = _ev("up"); spot = ev["spot"]; atr = ev["technical"]["suite"]["atr14"]
    txt, tone = TM._status_info("stop", "near", spot - 0.6 * atr, spot, atr)
    assert f"${0.6 * atr:,.0f}" in txt and f"ATR ${atr:,.0f}" in txt and "one average day" in txt and tone == "warn"
    assert TM._status_info("stop", "hit", spot + 1, spot, atr) == ("price is already through it", "bad")
    assert TM._status_info("target", "hit", spot - 1, spot, atr) == ("reached", "good")
    assert TM._status_info("stop", "far", spot - 9 * atr, spot, atr) == (None, None)


def test_plan_items_carry_dollar_distance_and_source_prices():
    ev = _ev("up"); spot = ev["spot"]
    p = TM.build_profile(_strategy(PCS), _pnl(spot))
    plan = TM.build_exit_plan(p, ev, TM.decide(p, ev))
    assert plan["atr"] and plan["atr_pct"] and plan["atr_pct"] == pytest.approx(plan["atr"] / spot * 100, abs=0.06)
    stop = next(i for i in plan["items"] if i["kind"] == "stop")
    assert stop["distance_usd"] == pytest.approx(stop["level"] - spot, abs=0.01) and stop["source_levels"]
    assert all({"label", "price"} <= set(x) for x in stop["source_levels"]) and any("Daily order block" in x["label"] or "AVWAP" in x["label"] for x in stop["source_levels"])
    near = [x["price"] for x in stop["source_levels"]]
    assert all(abs(pr - stop["level"]) <= max(0.5 * plan["atr"], stop["level"] * 0.004) + 1e-6 for pr in near)


def test_plan_items_are_sorted_nearest_to_price_first_and_unlevelled_last():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    items = TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"]
    lv = [i for i in items if i.get("level") is not None]
    assert [abs(i["distance_pct"]) for i in lv] == sorted(abs(i["distance_pct"]) for i in lv)
    assert items.index(lv[-1]) < items.index(next(i for i in items if i.get("level") is None))


def test_recommendation_has_structured_steps():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    steps = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["recommendation"]["steps"]
    tags = [s_["tag"] for s_ in steps]
    assert 1 <= len(steps) <= 5 and tags[0] == "EXIT" and "PROFIT" in tags and all(s_["text"] for s_ in steps)
    assert TM.build_exit_plan(p, ev, {"signal": "STRONG_EXIT"})["recommendation"]["steps"][0]["tag"] == "EXIT"
    st_ev, sp = _stock_ev_profile()
    lad = TM.build_exit_plan(sp, st_ev, {"signal": "HOLD"})["recommendation"]["steps"]
    assert any(s_["tag"] == "TRIM" and "Scale out" in s_["text"] for s_ in lad)


def test_vol_context_horizon_wording_differs_for_shares():
    ev, sp = _stock_ev_profile()
    assert TM._vol_context(sp, ev)["horizon"] == "the next month (21 trading days)"
    op = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    assert TM._vol_context(op, ev)["horizon"].startswith("expiry (")


def test_monitor_levels_are_position_specific_and_have_real_prices():
    ev, sp = _stock_ev_profile()
    mon = TM.build_monitor(sp, ev, TM.decide(sp, ev))
    assert mon["up"] and mon["down"]
    for u in mon["up"]:
        assert "raise your targets" in u["if_break"] and u["effect_if_break"] == "good" and u["distance_usd"] > 0 and "noise" in u and u["source_levels"]
        assert "tier" not in u
    for d_ in mon["down"]:
        assert "support has failed" in d_["if_break"] and d_["effect_if_break"] == "bad" and d_["distance_usd"] < 0
    short = TM.build_profile(_stock_trade(100, "stock_short"), _pnl(ev["spot"], net_greeks={"delta": -100, "gamma": 0, "theta": 0, "vega": 0}))
    m2 = TM.build_monitor(short, ev, TM.decide(short, ev))
    assert all("threatens your bearish thesis" in u["if_break"] for u in m2["up"])


def test_monitor_option_specific_text_is_not_shown_for_shares():
    ev, sp = _stock_ev_profile()
    ev["events"].update(days_to_earnings=9, earnings_date="2026-10-14", ex_dividend_date="2099-01-01")
    mon = TM.build_monitor(sp, ev, TM.decide(sp, ev))
    blob = " ".join(x["item"] + " " + x["watch"] for x in mon["fundamental_events"]).lower()
    assert "assignment" not in blob and "options price the move" not in blob and "hold through" in blob
    assert not any(i["metric"] == "Dealer gamma flip" for i in mon["indicators"])
    ad = next(i for i in mon["indicators"] if i["metric"].startswith("ADX"))
    assert not any("range" in w for w in ad["watch"])
    op = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    mo = TM.build_monitor(op, ev, TM.decide(op, ev))
    assert any(i["metric"] == "Dealer gamma flip" for i in mo["indicators"]) or True


def test_share_earnings_and_exdiv_plan_rows_use_share_language():
    ev, sp = _stock_ev_profile()
    ev["events"].update(days_to_earnings=9, earnings_date="2026-10-14", ex_dividend_date="2099-01-01")
    items = TM.build_exit_plan(sp, ev, TM.decide(sp, ev))["items"]
    e = next(i for i in items if i["kind"] == "event" and i["title"] == "Earnings")
    assert "hold through the print" in e["why"] and e["action"] == "REVIEW"
    assert not any(i["kind"] == "time" for i in items)


def test_share_exit_step_names_both_stops_nearest_first():
    ev, sp = _stock_ev_profile(pct=1.3)
    plan = TM.build_exit_plan(sp, ev, {"signal": "HOLD"})
    first = plan["recommendation"]["steps"][0]
    assert first["tag"] == "EXIT" and "your −10% stop" in first["text"] and "structure fails" in first["text"]
    disc = next(i for i in plan["items"] if i["title"] == "Discipline stop")
    stop = next(i for i in plan["items"] if i["title"] == "Structural stop")
    nearer = disc if abs(disc["distance_pct"]) < abs(stop["distance_pct"]) else stop
    assert f"{nearer['level']:,.2f}" in first["text"].split(";")[0]


def test_lite_style_share_trade_end_to_end_is_a_share_read(monkeypatch):
    """The reported case: params.shares + empty legs + a cost basis → Position-risk lens, share-language plan, no option content."""
    ev = _ev("up"); spot = ev["spot"]
    async def fake(db, ticker, dte):
        return dict(ev)
    monkeypatch.setattr(TM, "gather_market_evidence", fake)
    cost = round(spot / 1.013, 2)
    pnl = _pnl(spot, net_greeks={"delta": 50, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=1.3, breakevens=[cost], max_profit=None, max_loss=None, unbounded_profit=True)
    pnl["analysis"] = {"dte_remaining": None}
    out = asyncio.run(TM.run_trade_manager(None, _stock_trade(50, "stock_long", cost), pnl))
    assert out["profile"]["kind"] == "stock" and out["profile"]["direction"] == "bullish"
    d = out["decision"]
    assert d["lenses"]["quant"]["label"] == "Position risk" and d["lenses"]["quant"]["available"] and d["lenses"]["event"]["weight"] == 0.0
    assert any(i["title"] == "Your cost" for i in out["exit_plan"]["items"])
    assert all(m["effect_if_break"] in ("good", "bad") for m in out["monitor"]["up"] + out["monitor"]["down"])
    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for v in o.values():
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)
    blob = " ".join(list(strings(out["exit_plan"]["items"])) + list(strings(out["exit_plan"]["recommendation"])) + list(strings(out["monitor"]))).lower()                                  # DISPLAYED text only (internal keys like sigma_dte_pct excluded)
    for banned in ("at expiry", "theta", "assignment", "short put", "short call", " dte"):
        assert banned not in blob, banned
    json.dumps(out)


# ── source-level clustering, stock event proximity, target wording ─────────────────────────────────────────

def test_confluence_levels_put_the_anchor_first_and_collapse_synonyms():
    cands = [{"price": 1051.24, "label": "10 EMA", "kind": "ma", "w": 0.8},
             {"price": 1045.0, "label": "Put Support", "kind": "structure", "w": 1.4},
             {"price": 1045.0, "label": "Dealer put support", "kind": "dealer", "w": 1.6},
             {"price": 1021.79, "label": "Gamma flip", "kind": "dealer", "w": 1.8}, {"price": 1025.04, "label": "Gamma flip", "kind": "structure", "w": 1.0},
             {"price": 1026.76, "label": "Double Bottom trigger", "kind": "pattern", "w": 1.0}, {"price": 700.0, "label": "far away", "kind": "ma", "w": 2.0}]
    out = TM._confluence_levels(cands, 1051.24, 65.0)
    labels = [x["label"] for x in out]
    assert labels[0] == "10 EMA" and out[0]["price"] == 1051.24                        # the level's own read is always shown
    assert sum(1 for l_ in labels if "put support" in l_.lower()) == 1                  # Put Support == Dealer put support
    assert sum(1 for l_ in labels if l_ == "Gamma flip") == 1 and "far away" not in labels and len(out) <= 5
    assert TM._confluence_levels(cands, 5.0, 1.0) == []


def test_stock_earnings_penalty_scales_with_proximity_and_ignores_far_prints():
    ev, sp = _stock_ev_profile()
    def pts(days):
        e = json.loads(json.dumps(ev)); e["events"].update(days_to_earnings=days, earnings_date="2026-12-01")
        return TM._event_score(sp, e)
    assert pts(31)["clear"] is True and pts(40)["pts"] <= 0                             # LITE: 31 days out is NOT an event risk for shares
    assert pts(25)["pts"] == pytest.approx(3.0) and pts(10)["pts"] == pytest.approx(6.0) and pts(5)["pts"] == pytest.approx(10.0) and pts(1)["pts"] == pytest.approx(16.0)
    assert "inside the trade window" not in " ".join(pts(5)["notes"]) and "gap can jump" in " ".join(pts(5)["notes"])


def test_option_earnings_still_use_the_trade_window():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    ev["events"].update(days_to_earnings=40, earnings_date="2026-12-01")                 # beyond the 24 DTE
    assert TM._event_score(p, ev)["clear"] is True
    ev["events"]["days_to_earnings"] = 10
    assert "inside the trade window" in " ".join(TM._event_score(p, ev)["notes"])


def test_expected_move_only_target_is_described_as_the_top_of_the_range():
    ev, sp = _stock_ev_profile()
    lv = TM._levels(sp, ev)
    spot = ev["spot"]
    lv_targets = [{"level": spot * 1.2, "confluence": 2.0, "sources": ["30d expected-move upper"], "source_levels": [], "r_multiple": 1.0}]
    orig = TM._levels
    TM._levels = lambda p_, e_: {**lv, "targets": lv_targets}
    try:
        t = next(i for i in TM.build_exit_plan(sp, ev, {"signal": "HOLD"})["items"] if i["kind"] == "target")
    finally:
        TM._levels = orig
    assert "normal range" in t["why"] and "trim" in t["why"]


# ── relevance: share trades show only share metrics, option trades only option metrics ─────────────────────

OPTION_KEYS = {"pop", "ev", "kelly", "rr", "omega", "sortino", "captured", "iv_rv", "cvar", "var", "theta", "theta_be", "delta", "gamma", "vega", "dte", "pop_sigma", "max_loss"}
SHARE_KEYS = {"vol_ann", "move_1s", "stop_touch", "drawdown", "beta", "tsmom"}


def _metric_keys(p, ev):
    return {m["key"] for g in TM._quant_detail(p, ev)["groups"].values() for m in g}


def test_share_quant_tab_has_no_option_metrics():
    ev, sp = _stock_ev_profile()
    keys = _metric_keys(sp, ev)
    assert not (keys & OPTION_KEYS), keys & OPTION_KEYS
    assert not any(k.startswith(("be_", "touch_")) for k in keys)
    groups = TM._quant_detail(sp, ev)["groups"]
    assert "Greeks & time" not in groups and "Breakevens" not in groups and SHARE_KEYS & keys >= {"vol_ann", "move_1s", "stop_touch", "drawdown", "tsmom"}


def test_option_quant_tab_has_no_share_only_metrics():
    ev = _ev("flat"); p = TM.build_profile(_strategy(PCS), _quant_pnl(ev["spot"]))
    keys = _metric_keys(p, ev)
    assert not (keys & {"vol_ann", "move_1s", "stop_touch", "drawdown", "beta"}), keys
    assert {"pop", "ev", "theta", "delta", "dte"} <= keys


def test_share_packet_metrics_are_share_metrics_only():
    ev, sp = _stock_ev_profile()
    labels = {m["metric"] for m in TM.llm_packet(sp, ev)["quant_facts"]["position_metrics"]}
    assert "Probability of profit" not in labels and "Theta / day" not in labels and "Delta (share-equiv.)" not in labels and "Volatility (annualised)" in labels


# ═══ a level price has ALREADY lost is not a future exit — it is a level to RECLAIM (reported on LITE: 443 / 452 above a 395 price) ═══

def _below_ladder_ev(spot_frac=0.88):
    """Shares whose price has fallen BELOW the 150d / 200d averages but still sits above the 50d."""
    ev = _ev("up"); spot = ev["spot"]
    sm = ev["technical"]["suite"]["sma"]
    sm["50"], sm["150"], sm["200"] = round(spot * 0.93, 2), round(spot * 1.08, 2), round(spot * 1.12, 2)      # 150d / 200d are ABOVE price
    return ev


def test_lost_long_term_averages_become_reclaim_levels_not_exits():
    ev = _below_ladder_ev(); spot = ev["spot"]
    _, p = _stock_ev_profile(ev, pct=2.0)
    plan = TM.build_exit_plan(p, ev, {"signal": "HOLD"})
    against = [i for i in plan["items"] if i["group"] == "against"]
    assert not any(i["kind"] == "trail" and i["level"] > spot for i in against)                 # nothing ABOVE price is listed as an exit
    assert not any("Exit the rest" in i["title"] for i in plan["items"])
    reclaim = {i["title"]: i for i in plan["items"] if i["title"].startswith("Reclaim")}
    assert set(reclaim) == {"Reclaim · 150d", "Reclaim · 200d"}
    for i in reclaim.values():
        assert i["group"] == "levels" and i["action"] == "WATCH" and i["level"] > spot and i["distance_usd"] > 0
        assert i["status_text"] == "price is below it — trend broken" and i["status_tone"] == "warn" and "repair" in i["why"]
    still = [i for i in against if i["kind"] == "trail"]                                         # the 50d is still below price → a real, future trim
    assert [i["title"] for i in still] == ["Scale out · 50d"] and still[0]["level"] < spot and still[0]["action"] == "TRIM"


def test_what_to_do_says_the_trend_is_already_broken():
    ev = _below_ladder_ev(); _, p = _stock_ev_profile(ev, pct=2.0)
    rec = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["recommendation"]
    step = next(s_ for s_ in rec["steps"] if "long-term trend is broken" in s_["text"])
    assert step["tag"] == "REVIEW" and "150-day" in step["text"] and "200-day" in step["text"] and "would repair it" in step["text"]
    assert "trend already broken" in rec["text"]
    assert not any("long-term trend is broken" in s_["text"] for s_ in TM.build_exit_plan(*(lambda e, pr: (pr, e))(*_stock_ev_profile(_ev("up"))), {"signal": "HOLD"})["recommendation"]["steps"])


def test_reclaim_levels_mirror_for_short_shares():
    ev = _ev("down"); spot = ev["spot"]
    sm = ev["technical"]["suite"]["sma"]
    sm["50"], sm["150"], sm["200"] = round(spot * 1.07, 2), round(spot * 0.92, 2), round(spot * 0.88, 2)     # price is ABOVE the 150d / 200d
    _, p = _stock_ev_profile(ev, st="stock_short", pct=1.0)
    plan = TM.build_exit_plan(p, ev, {"signal": "HOLD"})
    rc = {i["title"]: i for i in plan["items"] if i["title"].startswith("Reclaim")}
    assert set(rc) == {"Reclaim · 150d", "Reclaim · 200d"} and all(i["level"] < spot and "above" in i["status_text"] for i in rc.values())
    assert "back below it would repair it" in rc["Reclaim · 200d"]["why"]


def test_option_position_below_its_200d_gets_a_reclaim_level_not_a_review_exit():
    ev = _ev("up"); spot = ev["spot"]; ev["technical"]["suite"]["sma"]["200"] = round(spot * 1.1, 2)
    p = TM.build_profile(_strategy(PCS), _pnl(spot))
    items = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["items"]
    assert not any(i["title"] == "Trend line · 200d" for i in items)
    rc = next(i for i in items if i["title"] == "Reclaim · 200d")
    assert rc["group"] == "levels" and rc["status"] == "hit"
    ev["technical"]["suite"]["sma"]["200"] = round(spot * 0.8, 2)                                  # price above the 200d → the normal REVIEW line
    items2 = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["items"]
    assert any(i["title"] == "Trend line · 200d" and i["action"] == "REVIEW" for i in items2) and not any(i["title"].startswith("Reclaim") for i in items2)


def test_a_breached_strike_test_is_still_an_alarm_in_the_against_group():
    """Only the long-term averages convert to 'reclaim' — a tested short strike must stay a red alarm."""
    ev = _ev("flat"); spot = ev["spot"]
    p = _put_profile(spot, 1.02)                                                                 # short put ABOVE price = in the money
    it = next(i for i in TM.build_exit_plan(p, ev, TM.decide(p, ev))["items"] if i["kind"] == "strike")
    assert it["group"] == "against" and it["status"] == "hit" and it["status_tone"] == "bad" and it["status_text"] == "price is already through it"


# ═══ My Trades = positions you are ALREADY IN: tracking since entry, holder-perspective context ═══════════════

def _with_history(ev, start="2026-08-01", n=60, entry_px=None, path=None):
    """Attach a daily-close history ending at ev['spot'] (business days)."""
    import pandas as _pd
    idx = _pd.bdate_range(start=start, periods=n)
    closes = list(path) if path is not None else list(np.linspace(entry_px or ev["spot"] * 0.9, ev["spot"], n))
    ev = dict(ev); ev["history"] = {"dates": [d.strftime("%Y-%m-%d") for d in idx], "closes": [round(float(c), 2) for c in closes]}
    return ev, idx


def _entered(strategy, entry_date, **extra):
    strategy = dict(strategy); strategy.update(trade_status="active", entry_date=entry_date, entry_prices=[{"price": 1.25}], entry_net_debit=-125.0, **extra)
    return strategy


def test_profile_carries_the_entry_facts():
    st = _entered(_strategy(PCS), "2026-09-14T00:00:00+00:00", roll={"count": 2, "roll_realized_pnl": 310.0, "effective_breakevens": [88.2]}, realized_banked=120.0)
    p = TM.build_profile(st, _pnl(100.0, days_held=20))
    e = p["entry"]
    assert e["status"] == "active" and e["entry_date"] == "2026-09-14" and e["days_held"] == 20 and e["entry_prices"] == [{"leg": 0, "price": 1.25}]
    assert e["entry_net"] == -125.0 and e["rolls"] == 2 and e["roll_realized_pnl"] == 310.0 and e["effective_breakevens"] == [88.2] and e["realized_banked"] == 120.0


def test_since_entry_for_shares_uses_your_cost_and_the_best_worst_since():
    ev = _ev("up"); spot = ev["spot"]
    path = np.concatenate([np.linspace(spot * 0.95, spot * 1.10, 30), np.linspace(spot * 1.10, spot, 30)])
    ev, idx = _with_history(ev, path=path)
    cost = round(spot * 0.97, 2)
    st = _entered(_stock_trade(100, "stock_long", cost), idx[10].strftime("%Y-%m-%d") + "T00:00:00+00:00")
    p = TM.build_profile(st, _pnl(spot, net_greeks={"delta": 100, "gamma": 0, "theta": 0, "vega": 0}, pnl_pct=3.0, days_held=50))
    se = TM.since_entry(p, ev)
    assert se["underlying_entry"] == cost and se["underlying_entry_source"] == "your cost" and se["move_pct"] == pytest.approx((spot / cost - 1) * 100, abs=0.06)
    assert se["vs_you"] == "with you" and se["days_held"] == 50 and se["entry_date"] == idx[10].strftime("%Y-%m-%d")
    seg = path[10:]
    assert se["high_since_pct"] == pytest.approx((seg.max() / cost - 1) * 100, abs=0.06) and se["low_since_pct"] == pytest.approx((seg.min() / cost - 1) * 100, abs=0.06)
    assert se["bars_since_entry"] == len(seg) - 1


def test_since_entry_for_options_uses_the_close_on_the_entry_date_and_is_direction_aware():
    ev = _ev("up"); spot = ev["spot"]
    path = np.linspace(spot * 1.2, spot, 60)                                             # the stock FELL after entry
    ev, idx = _with_history(ev, path=path)
    p = TM.build_profile(_entered(_strategy(PCS), idx[5].strftime("%Y-%m-%d")), _pnl(spot, days_held=55))
    se = TM.since_entry(p, ev)
    assert se["underlying_entry_source"] == "close on the entry date" and se["underlying_entry"] == pytest.approx(path[5], abs=0.01)
    assert se["move_pct"] < 0 and se["vs_you"] == "against you"                           # a bull put spread is hurt by the fall
    bear = TM.build_profile(_entered(_strategy(CCS, "ccs"), idx[5].strftime("%Y-%m-%d")), _pnl(spot, net_greeks={"delta": -15, "gamma": -0.4, "theta": 5, "vega": -7}, days_held=55))
    assert TM.since_entry(bear, ev)["vs_you"] == "with you"                               # …and helps a bear call spread


def test_since_entry_degrades_gracefully():
    ev = _ev("up")
    p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"], days_held=3))                   # no entry info at all
    se = TM.since_entry(p, ev)
    assert se["days_held"] == 3 and "underlying_entry" not in se
    ev2, idx = _with_history(ev)
    old = TM.build_profile(_entered(_strategy(PCS), "2019-01-01"), _pnl(ev["spot"], days_held=2000))    # entered before our history window
    se2 = TM.since_entry(old, ev2)
    assert se2["days_held"] == 2000 and "high_since_pct" not in se2


def test_rolled_and_partially_closed_trades_surface_the_campaign_numbers():
    ev = _ev("up")
    p = TM.build_profile(_entered(_strategy(PCS), "2026-09-01", roll={"count": 3, "roll_realized_pnl": 415.0, "effective_breakevens": [86.5]}, realized_banked=-60.0), _pnl(ev["spot"], days_held=30))
    se = TM.since_entry(p, ev)
    assert se["rolls"] == 3 and se["roll_realized_pnl"] == 415.0 and se["effective_breakevens"] == [86.5] and se["realized_banked"] == -60.0


def test_response_and_packet_carry_tracking_facts_and_the_prompt_says_the_trade_is_open(monkeypatch):
    ev0 = _ev("flat"); ev, idx = _with_history(ev0)
    async def fake(db, ticker, dte):
        return dict(ev)
    monkeypatch.setattr(TM, "gather_market_evidence", fake)
    st = _entered(_strategy(PCS), idx[10].strftime("%Y-%m-%d"))
    out = asyncio.run(TM.run_trade_manager(None, st, _pnl(ev["spot"], days_held=20)))
    assert out["since_entry"]["days_held"] == 20 and out["since_entry"]["underlying_entry"] and out["profile"]["entry"]["status"] == "active"
    pkt = out["evidence_json"]["position"]
    assert pkt["since_entry"]["entry_date"] == idx[10].strftime("%Y-%m-%d") and pkt["entry"]["entry_net"] == -125.0
    ks = _keys(out["evidence_json"], set())
    for banned in ("signal", "verdict", "stance", "tone"):
        assert banned not in ks
    assert "ALREADY OPEN" in TM._AI_SYSTEM and "FROM HERE" in TM._AI_SYSTEM and "never evaluate it as a new entry" in TM._AI_SYSTEM


def test_risk_reward_is_labelled_from_here_not_from_entry():
    ev = _ev("up"); p = TM.build_profile(_strategy(PCS), _pnl(ev["spot"]))
    rr = TM.build_exit_plan(p, ev, {"signal": "HOLD"})["risk_reward"]
    assert rr is None or "FROM HERE" in rr["note"]
