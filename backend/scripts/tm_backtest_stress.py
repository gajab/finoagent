"""Trade-Manager backtest, step 5 — can TA calibrate RISK (not direction)?

Logistic models on the point-in-time panel, trained on the first half of the sample and scored on the
second half (walk-forward split), for:
  stress   : forward-21d max adverse excursion ≤ −3 ATR            (a short strike 3 ATR away gets threatened)
  drop8    : forward-21d close-to-close return ≤ −8 %
  up       : forward-21d return > 0                                (direction — expected to be ≈ coin-flip)
Reports out-of-sample AUC / Brier skill and the learned standardized coefficients.

    PYTHONPATH=. venv/bin/python scripts/tm_backtest_stress.py panel.pkl
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler

FEATS = ["vs50", "vs200", "from_hi", "above_lo", "atr_pct", "atr_pctile", "bw_pctile", "rv21", "rv63", "adx", "rsi14", "roc21", "roc63",
         "weekly_stage", "dist_days", "squeeze", "bench_above200", "cons", "cmf", "pctb", "rvol"]


def prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df["rv_ratio"] = df["rv21"] / df["rv63"]
    df["stress"] = (df["mae21"] <= -3.0).astype(float).where(df["mae21"].notna())
    df["drop8"] = (df["fwd21"] <= -8.0).astype(float).where(df["fwd21"].notna())
    df["up"] = (df["fwd21"] > 0).astype(float).where(df["fwd21"].notna())
    return df


def fit_eval(df: pd.DataFrame, target: str, feats: list[str]) -> dict:
    d = df.dropna(subset=feats + [target]).sort_values("date")
    cut = d["date"].iloc[len(d) // 2]
    tr, te = d[d.date < cut], d[d.date >= cut]
    sc = StandardScaler().fit(tr[feats])
    m = LogisticRegression(C=0.3, max_iter=500).fit(sc.transform(tr[feats]), tr[target])
    p = m.predict_proba(sc.transform(te[feats]))[:, 1]
    base = te[target].mean()
    brier = brier_score_loss(te[target], p)
    brier_base = brier_score_loss(te[target], np.full(len(te), tr[target].mean()))
    coefs = pd.Series(m.coef_[0], index=feats).sort_values(key=abs, ascending=False)
    return {"target": target, "n_train": len(tr), "n_test": len(te), "base_rate_test": base, "AUC_test": roc_auc_score(te[target], p),
            "brier_skill": 1 - brier / brier_base, "coefs": coefs.round(3), "model": m, "scaler": sc, "feats": feats}


if __name__ == "__main__":
    df = prep(pd.read_pickle(sys.argv[1]))
    feats = FEATS + ["rv_ratio"]
    for t in ("stress", "drop8", "up"):
        r = fit_eval(df, t, feats)
        print(f"\n== {t}: base rate (test) {r['base_rate_test']:.3f} | OOS AUC {r['AUC_test']:.3f} | Brier skill {r['brier_skill']:+.4f} | n_train {r['n_train']} n_test {r['n_test']}")
        print(r["coefs"].head(10).to_string())
    # single-feature OOS AUCs for the risk targets
    print("\n== single-feature OOS AUC (second half) for 'stress' and 'drop8' (0.5 = no info) ==")
    d = df.sort_values("date"); cut = d.date.iloc[len(d) // 2]; te = d[d.date >= cut]
    rows = []
    for f in feats:
        x = te.dropna(subset=[f, "stress", "drop8"])
        if x[f].nunique() > 3:
            rows.append((f, roc_auc_score(x["stress"], x[f]), roc_auc_score(x["drop8"], x[f])))
    print(pd.DataFrame(rows, columns=["feat", "AUC_stress", "AUC_drop8"]).set_index("feat").round(3).sort_values("AUC_drop8").to_string())
