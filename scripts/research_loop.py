"""200-round hypothesis loop on cached Jev decisions.

    python scripts/research_loop.py --interval 5m --rounds 200 --panel jev
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402
from tych.optimize import Runner  # noqa: E402
from tych.panel import PanelParams  # noqa: E402
from tych.research import catalogue, run_loop  # noqa: E402
from tych.setups import SetupParams  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--rounds", type=int, default=200)
    ap.add_argument("--panel", default="jev")
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    uni = universe()
    feats = load_features(uni["coins"], args.interval)
    cache = PanelCache(args.panel)
    runner = Runner(feats, args.interval, cache, uni.get("spreads_bps", {}))
    coins = list(runner.feats)
    print(f"interval={args.interval} coins={coins} split={runner.split_time} train_days={runner.days_train:.0f} val_days={runner.days_val:.0f}", flush=True)
    rounds = catalogue(coins)
    print(f"catalogue: {len(rounds)} hypotheses", flush=True)
    base_sp = SetupParams(entry_mode="market", sl_atr=1.0, tp_atr=1.5, max_bars=24)
    base_pp = PanelParams()
    label = args.label or f"rounds-{args.interval}-{args.panel}"
    out = run_loop(runner, base_sp, base_pp, rounds, Path("reports/research") / f"{label}.jsonl", n_rounds=args.rounds)
    summary = {k: v for k, v in out.items() if k != "rows"}
    Path("reports/research").mkdir(exist_ok=True, parents=True)
    (Path("reports/research") / f"{label}_summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (Path("reports") / f"config_research_{args.interval}.json").write_text(json.dumps({"interval": args.interval, "setup": out["final_setup"], "panel": out["final_panel"]}, indent=1))
    print(json.dumps({"rounds": out["rounds"], "accepted": len(out["accepted"]), "elapsed_s": round(out["elapsed"]), "final_train": out["final"]["train"], "final_val": out["final"]["val"]}, indent=1, default=str))


if __name__ == "__main__":
    main()
