# Trade-Manager backtests

Point-in-time harness behind the numbers in `app/services/trade_manager_service.py::BACKTEST`.
All scripts need network once (yfinance) and write pickles next to the panel. Run from `backend/`:

```bash
P=/tmp/tm; mkdir -p $P
PYTHONPATH=. venv/bin/python scripts/tm_backtest_panel.py   $P/panel.pkl                    # ~14 min: 99 tickers × 12y, indicator suite + 15 lenses + forward outcomes
PYTHONPATH=. venv/bin/python scripts/tm_backtest_analyze.py $P/panel.pkl                    # lens / feature IC vs 21d & 63d returns, composite quintiles
PYTHONPATH=. venv/bin/python scripts/tm_backtest_stress.py  $P/panel.pkl                    # logistic tail/direction models, walk-forward AUC
PYTHONPATH=. venv/bin/python scripts/tm_backtest_flags.py   $P/panel.pkl                    # adverse-flag ON vs OFF: mean, P(≤−8%), MAE, dispersion
PYTHONPATH=. venv/bin/python scripts/tm_backtest_exits.py   $P/panel.pkl $P/panel_prices.pkl 0.25   # 21 exit rules on bullish entries
PYTHONPATH=. venv/bin/python scripts/tm_backtest_shortput.py $P/panel.pkl $P/panel_prices.pkl 2     # synthetic 20Δ short puts: hold / 50% / stops / technical exits
PYTHONPATH=. venv/bin/python scripts/tm_backtest_hold.py    $P/panel.pkl $P/panel_prices.pkl        # open short put: hold-vs-close by state (→ close trigger)
```

Headline results (2016-01 → 2026-10, 99 liquid US stocks/ETFs incl. laggards):

| Question | Result |
|---|---|
| Do the 15 trader rule-sets predict 21/63-day returns? | No. Cross-sectional IC −0.02…+0.01, \|t\| < 1.2, both halves. Adverse flags are followed by *higher* mean 21d returns but a fatter left tail. |
| Do technical features add anything after vol-normalising? | No (OOS AUC 0.51–0.55). Only the 21d/63d realized-vol ratio predicts forward dispersion. |
| Which exit rule for a bullish long? | Tight trails (10/21 EMA, Chandelier ≤3×ATR) whipsaw (+0.5…1.2% vs +7.8% hold). Wide exits (200d, 150d, 5×ATR) keep the trend; a −8…−10% hard stop cuts worst-5% by ~45% / worst case ~60% for the best return per unit of tail. |
| Manage short puts on TA / stops? | Buys tail protection at a steep price (CVaR5 halves, 60–90% of expected return given up, mean/sd worse than holding). 50%-profit take raises win-rate 88→94%. |
| When did CLOSING an open short put beat holding? | Tested strike (P(touch) ≥ 50% / ITM) **and** expanding vol (21d RV ≥ 1.4× 63d): avg Δ −0.18% of collateral (−1.0% if already < −2× credit); crash years flip the sign. |

Caveats: survivor-biased universe (mitigated, not removed), bull-heavy sample, option results are Black-Scholes synthetic
(IV = 1.15 × blended RV, vol spike on drops) — relative comparisons only. Tests of the harness: `tests/test_tm_backtest_scripts.py`.
