"""A/B: does a richer state (last 8 bars described in words) improve Jev's AUC?

Labels a random subset of already-labelled candidates again with the rich
state (separate cache 'jev_rich') and compares AUC on identical candidates.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_env  # noqa: E402

load_env()
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tych.backtest.engine import CostModel, simulate  # noqa: E402
from tych.candidates import enumerate_candidates  # noqa: E402
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402
from tych.jev.client import JevClient  # noqa: E402
from tych.jev.questions import panel_questions  # noqa: E402
from tych.jev.state import build_state  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jev_calibration import auc  # noqa: E402


def outcome(f, i, d, setup, spread):
    win = f.iloc[i: i + 26]
    sig = pd.DataFrame({"signal": [0] * len(win), "setup": [""] * len(win)}, index=win.index)
    sig.iloc[0, 0] = d
    sig.iloc[0, 1] = setup
    tr = simulate(win, sig, 1.0, 1.5, 24, CostModel(spread_bps=spread))
    if tr.empty:
        return None
    t = tr.iloc[0]
    return {"gross_win": int(t["gross_pct"] > 0), "gross_r": t["gross_pct"] / t["sl_pct"], "win": int(t["reason"] == "target"), "r": t["r"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--coins", default="BTC,ETH")
    ap.add_argument("--n", type=int, default=1500)
    ap.add_argument("--bars", type=int, default=8)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args()
    uni = universe()
    spreads = uni.get("spreads_bps", {})
    base, rich = PanelCache("jev"), PanelCache("jev_rich")
    client = JevClient(max_cost_usd=3.5)
    rng = random.Random(7)
    feats = load_features(args.coins.split(","), args.interval)
    pool = []
    for coin, (f, _) in feats.items():
        cands = enumerate_candidates(f)
        for _, r in cands.iterrows():
            k = PanelCache.key(coin, args.interval, r["ts"], r["direction"], r["setup"])
            if k in base.d:
                pool.append((coin, k, r["ts"], int(r["direction"]), r["setup"]))
    rng.shuffle(pool)
    pool = pool[: args.n]
    print(f"A/B on {len(pool)} candidates", flush=True)

    def work(item):
        coin, k, ts, d, setup = item
        f = feats[coin][0]
        i = f.index.get_loc(ts)
        recent = f.iloc[max(0, i - args.bars + 1): i + 1]
        st = build_state(f.iloc[i], coin, args.interval, d, setup, spread_bps=spreads.get(coin), recent=recent)
        resp = client.system_one(st, panel_questions(st["candidate"]["direction"], setup))
        return k, resp, outcome(f, i, d, setup, float(spreads.get(coin, 1.0) or 1.0))

    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for fut in as_completed([ex.submit(work, it) for it in pool]):
            try:
                k, resp, out = fut.result()
            except Exception as exc:
                print("ERROR", exc, flush=True)
                continue
            if out is None:
                continue
            rich.put(k, resp["answers"], resp.get("model", ""))
            a, b = base.get(k)["answers"], resp["answers"]
            rows.append({**out, "base_setup_valid": a["setup_valid"]["noul"], "base_quality": a["quality"]["score"], "base_take": a["action"]["probabilities"].get("take", 0),
                         "rich_setup_valid": b["setup_valid"]["noul"], "rich_quality": b["quality"]["score"], "rich_take": b["action"]["probabilities"].get("take", 0),
                         "rich_action": b["action"]["choice"], "base_action": a["action"]["choice"]})
    rich.save()
    df = pd.DataFrame(rows)
    res = {"n": len(df), "cost_usd": client.stats()["cost_usd"], "input_tokens_per_call": client.stats()["input_tokens"] / max(1, client.stats()["calls"])}
    for lab in ("gross_win", "win"):
        res[f"auc_{lab}"] = {c: round(auc(df[c].values, df[lab].values), 4) for c in ["base_setup_valid", "base_quality", "base_take", "rich_setup_valid", "rich_quality", "rich_take"]}
    res["rich_action_counts"] = df["rich_action"].value_counts().to_dict()
    res["base_action_counts"] = df["base_action"].value_counts().to_dict()
    res["gross_r_by_rich_action"] = df.groupby("rich_action")["gross_r"].mean().round(3).to_dict()
    res["gross_r_by_base_action"] = df.groupby("base_action")["gross_r"].mean().round(3).to_dict()
    print(json.dumps(res, indent=1))
    Path("reports").mkdir(exist_ok=True)
    Path(f"reports/state_ab_{args.interval}.json").write_text(json.dumps(res, indent=1))
    df.to_csv(f"reports/state_ab_{args.interval}.csv", index=False)


if __name__ == "__main__":
    main()
