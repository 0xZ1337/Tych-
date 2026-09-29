"""Control experiment: do the features Jev sees carry ANY predictive information?

Fit classical models (logistic regression, gradient boosting) on the same
per-candidate features, time-split 70/30, and report validation AUC on the
same labels used for Jev's calibration.  If these are ~0.5 too, the features
are the bottleneck, not the decision model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import roc_auc_score  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

FEATS = ["dist_vwap_atr", "dist_ema21_atr", "rsi", "bb_z", "vol_z", "atr_regime", "range_atr", "body_ratio", "upper_wick", "lower_wick",
         "streak", "move3_atr", "move10_atr", "pos_in_range20", "ema21_slope", "htf_slope", "htf2_slope", "hour", "atr_pct"]


def main():
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    coins = (sys.argv[2] if len(sys.argv) > 2 else "BTC,ETH").split(",")
    from tych.datasets import load_features
    cal = pd.read_csv(f"reports/calibration_jev_{interval}_btc_eth.csv")
    cal["ts"] = pd.to_datetime(cal["ts"], utc=True)
    feats = load_features(coins, interval)
    rows = []
    for coin, (f, _) in feats.items():
        sub = cal[cal["coin"] == coin]
        if sub.empty:
            continue
        x = f.loc[pd.DatetimeIndex(sub["ts"]), FEATS].copy()
        x["direction"] = sub["direction"].values
        for s in ("vwap_reversion", "trend_pullback", "range_breakout"):
            x[f"is_{s}"] = (sub["setup"].values == s).astype(int)
        # direction-signed versions so long/short share structure
        for c in ("dist_vwap_atr", "dist_ema21_atr", "bb_z", "streak", "move3_atr", "move10_atr", "ema21_slope", "htf_slope", "htf2_slope"):
            x[f"{c}_signed"] = x[c].values * sub["direction"].values
        x["ts"] = pd.DatetimeIndex(sub["ts"])
        x["gross_win"] = sub["gross_win"].values
        x["win"] = sub["win"].values
        x["gross_r"] = sub["gross_r"].values
        rows.append(x)
    df = pd.concat(rows).dropna()
    split = df["ts"].min() + (df["ts"].max() - df["ts"].min()) * 0.7
    tr, va = df[df["ts"] < split], df[df["ts"] >= split]
    X_cols = [c for c in df.columns if c not in ("ts", "gross_win", "win", "gross_r")]
    out = {"interval": interval, "n_train": len(tr), "n_val": len(va), "features": len(X_cols)}
    for label in ("gross_win", "win"):
        res = {}
        lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000, C=0.5))
        lr.fit(tr[X_cols], tr[label])
        res["logistic_val_auc"] = round(roc_auc_score(va[label], lr.predict_proba(va[X_cols])[:, 1]), 4)
        res["logistic_train_auc"] = round(roc_auc_score(tr[label], lr.predict_proba(tr[X_cols])[:, 1]), 4)
        gb = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, l2_regularization=1.0)
        gb.fit(tr[X_cols], tr[label])
        p = gb.predict_proba(va[X_cols])[:, 1]
        res["gboost_val_auc"] = round(roc_auc_score(va[label], p), 4)
        res["gboost_train_auc"] = round(roc_auc_score(tr[label], gb.predict_proba(tr[X_cols])[:, 1]), 4)
        # economic check: top-decile of gboost score on validation
        top = va[p >= np.quantile(p, 0.9)]
        res["gboost_top_decile_val_gross_r"] = round(float(top["gross_r"].mean()), 3)
        res["val_gross_r_all"] = round(float(va["gross_r"].mean()), 3)
        out[label] = res
    print(json.dumps(out, indent=1))
    Path(f"reports/feature_baseline_{interval}.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
