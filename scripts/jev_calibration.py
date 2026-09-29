"""Does Jev's opinion predict the outcome?  (the question that matters)

For every labelled candidate we compute a *canonical* outcome with the
engine's fill rules (stop 1.0 ATR, target 1.5 ATR, 24 bars, market entry) and
compare Jev's answers with it:

  * AUC of each probability / score against "target hit before stop"
  * mean realised R by decile of the `quality` score and by `action`
  * reliability table for `setup_valid` (calibration)

    python scripts/jev_calibration.py --interval 5m --panel jev
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tych.backtest.engine import CostModel, simulate  # noqa: E402
from tych.candidates import enumerate_candidates  # noqa: E402
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402


def auc(score: np.ndarray, label: np.ndarray) -> float:
    """Rank-based AUC (Mann-Whitney)."""
    pos, neg = score[label == 1], score[label == 0]
    if len(pos) == 0 or len(neg) == 0:
        return float("nan")
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def canonical_outcomes(f: pd.DataFrame, cands: pd.DataFrame, coin: str, spread: float) -> pd.DataFrame:
    """One independent trade per candidate (overlaps allowed) with canonical exits."""
    rows = []
    cost = CostModel(spread_bps=spread)
    for setup in cands["setup"].unique():
        sub = cands[cands["setup"] == setup]
        for d in (1, -1):
            s2 = sub[sub["direction"] == d]
            if s2.empty:
                continue
            for ts in s2["ts"]:
                i = f.index.get_loc(ts)
                if i + 2 >= len(f):
                    continue
                win = f.iloc[i: i + 26]
                sig = pd.DataFrame({"signal": [0] * len(win), "setup": [""] * len(win)}, index=win.index)
                sig.iloc[0, 0] = d
                sig.iloc[0, 1] = setup
                tr = simulate(win, sig, 1.0, 1.5, 24, cost, coin=coin)
                if tr.empty:
                    continue
                t = tr.iloc[0]
                rows.append({"ts": ts, "direction": d, "setup": setup, "r": t["r"], "net_pct": t["net_pct"], "reason": t["reason"],
                             "gross_r": t["gross_pct"] / t["sl_pct"], "gross_win": int(t["gross_pct"] > 0)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--panel", default="jev")
    ap.add_argument("--coins", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    uni = universe()
    coins = [c for c in args.coins.split(",") if c] or uni["coins"]
    cache = PanelCache(args.panel)
    feats = load_features(coins, args.interval)
    recs = []
    for coin, (f, src) in feats.items():
        cands = enumerate_candidates(f)
        have = [PanelCache.key(coin, args.interval, r["ts"], r["direction"], r["setup"]) in cache.d for _, r in cands.iterrows()]
        cands = cands[have]
        if cands.empty:
            continue
        out = canonical_outcomes(f, cands, coin, float(uni.get("spreads_bps", {}).get(coin, 1.0) or 1.0))
        for _, r in out.iterrows():
            a = cache.get(PanelCache.key(coin, args.interval, r["ts"], r["direction"], r["setup"]))["answers"]
            recs.append({"coin": coin, "ts": r["ts"], "setup": r["setup"], "direction": r["direction"], "r": r["r"], "win": int(r["reason"] == "target"),
                         "gross_r": r["gross_r"], "gross_win": r["gross_win"],
                         "setup_valid": a["setup_valid"]["noul"], "regime": a["regime_tradable"]["noul"], "cost": a["cost_efficiency"]["noul"],
                         "htf_against": a["htf_pressure_against"]["score"], "exhaustion": a["move_exhaustion"]["score"],
                         "quality": a["quality"]["score"], "p_take": a["action"]["probabilities"].get("take", 0.0),
                         "action": a["action"]["choice"]})
        print(f"{coin}: {len(out)} candidates evaluated", flush=True)
    df = pd.DataFrame(recs)
    if df.empty:
        print("nothing to evaluate")
        return
    lab = df["win"].values
    result = {"panel": args.panel, "interval": args.interval, "n": int(len(df)), "base_win_rate": float(lab.mean()), "base_avg_r": float(df["r"].mean()),
              "base_gross_win_rate": float(df["gross_win"].mean()), "base_gross_avg_r": float(df["gross_r"].mean()),
              "auc_target_before_stop": {k: auc(df[k].values, lab) for k in ["setup_valid", "regime", "cost", "quality", "p_take"]},
              "auc_gross_win": {k: auc(df[k].values, df["gross_win"].values) for k in ["setup_valid", "regime", "cost", "quality", "p_take"]},
              "auc_neg_target": {k: auc(-df[k].values, lab) for k in ["htf_against"]},
              "auc_neg_gross": {k: auc(-df[k].values, df["gross_win"].values) for k in ["htf_against"]}}
    df["q_bin"] = pd.cut(df["quality"], bins=[-0.1, 1, 1.5, 2, 2.5, 3, 4.1])
    result["by_quality"] = df.groupby("q_bin", observed=True).agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean"), gross_win=("gross_win", "mean"), gross_r=("gross_r", "mean")).round(3).reset_index().astype(str).to_dict("records")
    result["by_action"] = df.groupby("action").agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean"), gross_win=("gross_win", "mean"), gross_r=("gross_r", "mean")).round(3).reset_index().to_dict("records")
    result["by_coin"] = df.groupby("coin").agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean"), gross_r=("gross_r", "mean"), auc_quality_gross=("quality", lambda s: auc(s.values, df.loc[s.index, "gross_win"].values))).round(3).reset_index().to_dict("records")
    df["sv_bin"] = pd.cut(df["setup_valid"], bins=[0, 0.2, 0.4, 0.6, 0.8, 1.0])
    result["by_setup_valid"] = df.groupby("sv_bin", observed=True).agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean")).round(3).reset_index().astype(str).to_dict("records")
    result["by_setup"] = df.groupby("setup").agg(n=("r", "size"), win=("win", "mean"), avg_r=("r", "mean"), auc_quality=("quality", lambda s: auc(s.values, df.loc[s.index, "win"].values))).round(3).reset_index().to_dict("records")
    print(json.dumps(result, indent=1, default=str))
    out = Path(args.out or f"reports/calibration_{args.panel}_{args.interval}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1, default=str))
    df.to_csv(out.with_suffix(".csv"), index=False)


if __name__ == "__main__":
    main()
