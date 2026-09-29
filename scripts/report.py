"""Stage 3: turn optimisation logs into a report (tables, equity curves, best config).

    python scripts/report.py --runs reports/optimize_runs/5m-jev-s42.jsonl [...] --interval 5m
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tych.backtest.engine import portfolio_equity  # noqa: E402
from tych.backtest.metrics import breakdown, summarize  # noqa: E402
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402
from tych.optimize import Runner, tstat  # noqa: E402
from tych.panel import PanelParams  # noqa: E402
from tych.setups import SetupParams  # noqa: E402


def load_runs(paths: list[str]) -> list[dict]:
    recs = []
    for p in paths:
        with open(p) as fh:
            recs += [json.loads(line) for line in fh if line.strip()]
    return recs


def flat(recs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in recs:
        wt, wv, nt, nv = r["with_panel"]["train"], r["with_panel"]["val"], r["no_panel"]["train"], r["no_panel"]["val"]
        rows.append({"iter": r["iter"], "label": r["label"], "obj": r["with_panel"]["objective"],
                     "t_n": wt["n_trades"], "t_R": wt["avg_r"], "t_pf": wt["profit_factor"], "t_wr": wt["win_rate"], "t_ret": wt["total_return_pct"], "t_dd": wt["max_drawdown_pct"], "t_t": wt.get("tstat", 0),
                     "v_n": wv["n_trades"], "v_R": wv["avg_r"], "v_pf": wv["profit_factor"], "v_wr": wv["win_rate"], "v_ret": wv["total_return_pct"], "v_dd": wv["max_drawdown_pct"], "v_t": wv.get("tstat", 0),
                     "np_t_n": nt["n_trades"], "np_t_R": nt["avg_r"], "np_v_n": nv["n_trades"], "np_v_R": nv["avg_r"], "np_v_ret": nv["total_return_pct"],
                     "entry": r["setup"].get("entry_mode", "market"), "sl": r["setup"]["sl_atr"], "tp": r["setup"]["tp_atr"], "max_bars": r["setup"]["max_bars"],
                     "mr": r["setup"]["mr_enabled"], "pb": r["setup"]["pb_enabled"], "bo": r["setup"]["bo_enabled"], "thr": r["panel"]["threshold"], "veto": r["panel"]["veto_skip"]})
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in r.values) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--panel", default="jev")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(exist_ok=True, parents=True)
    recs = load_runs(args.runs)
    df = flat(recs)
    n = len(df)
    valid = df[df["t_n"] >= 30]
    corr = float(np.corrcoef(valid["t_t"], valid["v_t"])[0, 1]) if len(valid) > 5 else float("nan")
    uplift = (df["v_R"] - df["np_v_R"])[(df["v_n"] >= 20) & (df["np_v_n"] >= 20)]
    summary = {
        "iterations": n, "with_train_trades_ge_30": int(len(valid)),
        "corr_train_val_tstat": corr,
        "share_val_positive_R_all": float((df["v_R"] > 0).mean()),
        "share_val_positive_R_top20_by_train": float((df.sort_values("obj", ascending=False).head(20)["v_R"] > 0).mean()),
        "panel_uplift_val_R_median": float(uplift.median()) if len(uplift) else float("nan"),
        "panel_uplift_val_R_share_positive": float((uplift > 0).mean()) if len(uplift) else float("nan"),
        "no_panel_val_R_median": float(df.loc[df["np_v_n"] >= 20, "np_v_R"].median()) if (df["np_v_n"] >= 20).any() else float("nan"),
        "entry_mode_val_R_median": df[df["v_n"] >= 20].groupby("entry")["v_R"].median().round(3).to_dict(),
    }
    top = df.sort_values("obj", ascending=False).head(args.top)
    show = top[["iter", "obj", "entry", "sl", "tp", "max_bars", "mr", "pb", "bo", "thr", "t_n", "t_R", "t_pf", "t_wr", "t_ret", "t_dd", "v_n", "v_R", "v_pf", "v_wr", "v_ret", "v_dd", "np_v_n", "np_v_R"]].copy()

    # --- re-run the best config to get trades, breakdowns and an equity curve
    best = max(recs, key=lambda r: r["with_panel"]["objective"])
    uni = universe()
    feats = load_features(uni["coins"], args.interval)
    cache = PanelCache(args.panel)
    runner = Runner(feats, args.interval, cache, uni.get("spreads_bps", {}))
    sp, pp = SetupParams.from_dict(best["setup"]), PanelParams.from_dict(best["panel"])
    tr = runner.run(sp, pp)
    tr_np = runner.run(sp, None)
    best_cfg = {"interval": args.interval, "panel_model": args.panel, "objective": best["with_panel"]["objective"], "iter": best["iter"], "label": best["label"],
                "setup": best["setup"], "panel": best["panel"], "train": best["with_panel"]["train"], "val": best["with_panel"]["val"], "split_time": str(runner.split_time)}
    (out / f"best_config_{args.interval}.json").write_text(json.dumps(best_cfg, indent=1, default=str))
    tr.to_csv(out / f"best_trades_{args.interval}.csv", index=False)

    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    for label, t, style in (("panel (Jev)", tr, "-"), ("no panel", tr_np, "--")):
        if t.empty:
            continue
        eq = portfolio_equity(t)
        ax[0].plot(pd.to_datetime(eq["time"]), eq["equity"], style, label=label)
    ax[0].axvline(runner.split_time, color="grey", lw=1)
    ax[0].set_title(f"Best config #{best['iter']} on {args.interval}: equity (risk 0.5%/trade, start 10k) - grey line = train/validation split")
    ax[0].legend(); ax[0].grid(alpha=.3)
    if not tr.empty:
        for setup, g in tr.sort_values("exit_time").groupby("setup"):
            ax[1].plot(pd.to_datetime(g["exit_time"]), g["r"].cumsum(), label=f"{setup} cum R")
    ax[1].axvline(runner.split_time, color="grey", lw=1)
    ax[1].legend(); ax[1].grid(alpha=.3); ax[1].set_ylabel("cumulative R")
    fig.tight_layout()
    fig.savefig(out / f"equity_best_{args.interval}.png", dpi=110)

    fig2, ax2 = plt.subplots(1, 2, figsize=(11, 4))
    ax2[0].scatter(df["t_t"], df["v_t"], s=12, alpha=.6)
    ax2[0].set_xlabel("train t-stat"); ax2[0].set_ylabel("validation t-stat"); ax2[0].set_title(f"train vs validation across {n} iterations (corr={corr:.2f})"); ax2[0].grid(alpha=.3)
    ok = (df["v_n"] >= 20) & (df["np_v_n"] >= 20)
    ax2[1].scatter(df.loc[ok, "np_v_R"], df.loc[ok, "v_R"], s=12, alpha=.6)
    lim = [min(df.loc[ok, "np_v_R"].min(), df.loc[ok, "v_R"].min()), max(df.loc[ok, "np_v_R"].max(), df.loc[ok, "v_R"].max())] if ok.any() else [-1, 1]
    ax2[1].plot(lim, lim, "k--", lw=1)
    ax2[1].set_xlabel("validation avg R without panel"); ax2[1].set_ylabel("validation avg R with Jev panel"); ax2[1].set_title("panel uplift (above the line = Jev helped)"); ax2[1].grid(alpha=.3)
    fig2.tight_layout()
    fig2.savefig(out / f"search_{args.interval}.png", dpi=110)

    train = tr[tr["signal_time"] < runner.split_time] if not tr.empty else tr
    val = tr[tr["signal_time"] >= runner.split_time] if not tr.empty else tr
    md = [f"# Optimisation report ({args.interval}, panel={args.panel})", "",
          f"Iterations: {n}. Split: {runner.split_time} (train {runner.days_train:.0f} d / validation {runner.days_val:.0f} d). Coins: {', '.join(feats)}.", "",
          "## Search summary", "", "```", json.dumps(summary, indent=1, default=str), "```", "",
          f"## Top {args.top} by train objective (t-stat of R x min(1, n/60)); v_* = validation, np_* = same signals without the panel", "",
          md_table(show.round(3)), "",
          f"## Best config #{best['iter']}", "", "```", json.dumps({"setup": best["setup"], "panel": best["panel"]}, indent=1), "```", "",
          "### Best config: train vs validation", "", md_table(pd.DataFrame([{"split": "train", **summarize(train, runner.days_train)}, {"split": "validation", **summarize(val, runner.days_val)}]).round(3)), "",
          "### Best config: by setup (all trades)", "", md_table(breakdown(tr, "setup").reset_index()) if not tr.empty else "-", "",
          "### Best config: by coin (all trades)", "", md_table(breakdown(tr, "coin").reset_index()) if not tr.empty else "-", "",
          "### Best config: by direction", "", md_table(breakdown(tr, "direction").reset_index()) if not tr.empty else "-", "",
          "### Best config: by exit reason", "", md_table(breakdown(tr, "reason").reset_index()) if not tr.empty else "-", "",
          f"![equity](equity_best_{args.interval}.png)", "", f"![search](search_{args.interval}.png)", ""]
    (out / f"REPORT_{args.interval}.md").write_text("\n".join(md))
    print(json.dumps(summary, indent=1, default=str))
    print(show.round(3).to_string())


if __name__ == "__main__":
    main()
