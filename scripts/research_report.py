"""Report for the 200-round research loop: trajectory chart, accepted steps, robustness, walk-forward, ablations."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else "rounds-5m-jev"
    rows = [json.loads(l) for l in open(f"reports/research/{label}.jsonl")]
    summ = json.load(open(f"reports/research/{label}_summary.json"))
    df = pd.DataFrame([{"round": r["round"], "phase": r["phase"], "kind": r["kind"], "name": r["name"], "decision": r["decision"], "obj": r["objective"],
                        "t_n": r["train"]["n_trades"], "t_R": r["train"]["avg_r"], "t_t": r["train"].get("tstat", 0),
                        "v_n": r["val"]["n_trades"], "v_R": r["val"]["avg_r"], "v_t": r["val"].get("tstat", 0), "v_pf": r["val"]["profit_factor"], "v_ret": r["val"]["total_return_pct"]} for r in rows])
    # trajectory of the incumbent
    best_obj, traj = None, []
    cur = None
    for r in rows:
        if r["kind"] == "base" or r["decision"] == "ACCEPT":
            cur = r
        traj.append({"round": r["round"], "obj": cur["objective"], "v_R": cur["val"]["avg_r"], "v_t": cur["val"].get("tstat", 0), "t_n": cur["train"]["n_trades"], "v_n": cur["val"]["n_trades"]})
    tj = pd.DataFrame(traj)
    fig, ax = plt.subplots(3, 1, figsize=(11, 9), sharex=True)
    ax[0].plot(tj["round"], tj["obj"], label="incumbent train objective (t-stat x min(1,n/60))")
    ax[0].scatter(df[df.kind == "mutate"]["round"], df[df.kind == "mutate"]["obj"], s=8, alpha=.4, label="tested hypothesis (train objective)")
    ax[0].legend(); ax[0].grid(alpha=.3)
    ax[1].plot(tj["round"], tj["v_t"], color="tab:red", label="incumbent VALIDATION t-stat (never used for selection)")
    ax[1].axhline(0, color="k", lw=.8); ax[1].axhline(2, color="grey", lw=.8, ls="--"); ax[1].legend(); ax[1].grid(alpha=.3)
    ax[2].plot(tj["round"], tj["t_n"], label="train trades"); ax[2].plot(tj["round"], tj["v_n"], label="validation trades"); ax[2].legend(); ax[2].grid(alpha=.3); ax[2].set_xlabel("round")
    for r in rows:
        if r["decision"] == "ACCEPT":
            ax[0].axvline(r["round"], color="green", lw=.5, alpha=.4)
    fig.suptitle(f"{label}: 200 hypothesis rounds, hill-climbing on train (green = accepted)")
    fig.tight_layout(); fig.savefig(f"reports/research/{label}_trajectory.png", dpi=110)

    def md(d: pd.DataFrame) -> str:
        cols = list(d.columns)
        out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
        for _, r in d.iterrows():
            out.append("| " + " | ".join(f"{v:.3f}" if isinstance(v, float) else str(v) for v in r.values) + " |")
        return "\n".join(out)

    acc = df[df.decision == "ACCEPT"][["round", "phase", "name", "obj", "t_n", "t_R", "v_n", "v_R", "v_t"]]
    meas = df[df.kind == "measure"][["round", "phase", "name", "t_n", "t_R", "t_t", "v_n", "v_R", "v_t"]]
    abl = [r for r in rows if r["name"].startswith("ablation: revert")]
    abl_df = pd.DataFrame([{"round": r["round"], "reverted step": r["name"].replace("ablation: revert ", ""), "train obj without it": round(r["objective"], 3),
                            "contribution to train obj": round(r.get("contribution_train_obj", 0), 3), "contribution to val R": round(r.get("contribution_val_R", 0), 3), "val R without it": round(r["val"]["avg_r"], 3), "val n": r["val"]["n_trades"]} for r in abl])
    base, fin = rows[0], summ["final"]
    lines = [f"# Research loop report: {label}", "",
             f"{summ['rounds']} rounds, {len(summ['accepted'])} accepted, {summ['elapsed']:.0f} s of compute (all Jev decisions cached).", "",
             "## Trajectory", "", f"![trajectory]({label}_trajectory.png)", "",
             "## Start vs end", "",
             md(pd.DataFrame([{"config": "start", "train n": base["train"]["n_trades"], "train R": base["train"]["avg_r"], "train t": base["train"].get("tstat", 0), "val n": base["val"]["n_trades"], "val R": base["val"]["avg_r"], "val t": base["val"].get("tstat", 0), "val pf": base["val"]["profit_factor"], "val return %": base["val"]["total_return_pct"], "val maxDD %": base["val"]["max_drawdown_pct"]},
                              {"config": "final", "train n": fin["train"]["n_trades"], "train R": fin["train"]["avg_r"], "train t": fin["train"].get("tstat", 0), "val n": fin["val"]["n_trades"], "val R": fin["val"]["avg_r"], "val t": fin["val"].get("tstat", 0), "val pf": fin["val"]["profit_factor"], "val return %": fin["val"]["total_return_pct"], "val maxDD %": fin["val"]["max_drawdown_pct"]}]).round(3)), "",
             "## Accepted steps (in order)", "", md(acc.round(3)), "",
             "## Measurements (cost sensitivity, robustness, walk-forward, ablations)", "", md(meas.round(3)), "",
             "## Ablation of each accepted step from the final configuration", "", md(abl_df) if len(abl_df) else "-", "",
             "## Final configuration", "", "```", json.dumps({"setup": summ["final_setup"], "panel": summ["final_panel"]}, indent=1), "```", "",
             "## All rounds", "", md(df[["round", "phase", "kind", "name", "decision", "obj", "t_n", "t_R", "v_n", "v_R", "v_t"]].round(3)), ""]
    Path(f"reports/research/{label}.md").write_text("\n".join(lines))
    print(f"written reports/research/{label}.md")


if __name__ == "__main__":
    main()
