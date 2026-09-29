"""Stage 2: 200-iteration random search on cached Jev decisions.

    python scripts/optimize.py --interval 5m --iters 200 --seed 42 --panel jev
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402
from tych.optimize import Runner, random_search  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--panel", default="jev", choices=["jev", "mock"])
    ap.add_argument("--coins", default="")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    uni = universe()
    coins = [c for c in args.coins.split(",") if c] or uni["coins"]
    feats = load_features(coins, args.interval)
    cache = PanelCache(args.panel)
    print(f"interval={args.interval} coins={list(feats)} panel={args.panel} cache={len(cache)}", flush=True)
    runner = Runner(feats, args.interval, cache, uni.get("spreads_bps", {}))
    print(f"split at {runner.split_time}  train_days={runner.days_train:.1f} val_days={runner.days_val:.1f}", flush=True)
    label = args.label or f"{args.interval}-{args.panel}-s{args.seed}"
    log = Path("reports/optimize_runs") / f"{label}.jsonl"
    if log.exists():
        log.unlink()
    res = random_search(runner, args.iters, args.seed, log, label=label)
    print(f"missing panel entries encountered: {runner.missing}")
    res.sort(key=lambda r: -r["with_panel"]["objective"])
    Path("reports/optimize_runs").mkdir(exist_ok=True, parents=True)
    (Path("reports/optimize_runs") / f"{label}_top.json").write_text(json.dumps(res[:10], indent=1, default=str))
    for r in res[:5]:
        print(json.dumps({"iter": r["iter"], "obj": r["with_panel"]["objective"], "train": r["with_panel"]["train"], "val": r["with_panel"]["val"]}, default=str))


if __name__ == "__main__":
    main()
