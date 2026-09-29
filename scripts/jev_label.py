"""Stage 1: ask Jev once for every candidate in the datasets and cache the answers.

    python scripts/jev_label.py --intervals 5m,1m --workers 8 [--mock]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_env  # noqa: E402

load_env()
from tych.candidates import enumerate_candidates  # noqa: E402
from tych.datasets import load_features, universe  # noqa: E402
from tych.jev.cache import PanelCache  # noqa: E402
from tych.jev.mock import MockJev  # noqa: E402
from tych.jev.questions import panel_questions  # noqa: E402
from tych.jev.state import build_state  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--intervals", default="5m,1m")
    ap.add_argument("--coins", default="")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--max-cost", type=float, default=3.5)
    ap.add_argument("--limit", type=int, default=0, help="debug: max candidates per coin/interval")
    ap.add_argument("--days", default="5m:90,1m:21", help="history depth per interval used for labelling")
    args = ap.parse_args()

    uni = universe()
    coins = [c for c in args.coins.split(",") if c] or uni["coins"]
    spreads = uni.get("spreads_bps", {})
    if args.mock:
        model, tag = MockJev(), "mock"
    else:
        from tych.jev.client import JevClient
        model, tag = JevClient(max_cost_usd=args.max_cost), "jev"
    cache = PanelCache(tag)
    print(f"model={tag} cache_entries={len(cache)}", flush=True)

    days = {kv.split(":")[0]: float(kv.split(":")[1]) for kv in args.days.split(",")}
    jobs = []
    for iv in args.intervals.split(","):
        feats = load_features(coins, iv)
        for coin, (f, src) in feats.items():
            if iv in days:
                f = f[f.index >= f.index[-1] - __import__("pandas").Timedelta(days=days[iv])]
            cands = enumerate_candidates(f)
            if args.limit:
                cands = cands.head(args.limit)
            todo = 0
            for _, r in cands.iterrows():
                k = PanelCache.key(coin, iv, r["ts"], r["direction"], r["setup"])
                if cache.get(k) is None:
                    jobs.append((k, coin, iv, f.loc[r["ts"]], int(r["direction"]), r["setup"]))
                    todo += 1
            print(f"{coin:6s} {iv} src={src:11s} bars={len(f):6d} candidates={len(cands):6d} to_label={todo}", flush=True)
    print(f"total to label: {len(jobs)}  est_cost={len(jobs) * 1250 / 1e6 * 0.042:.2f} USD", flush=True)

    def work(job):
        k, coin, iv, row, d, setup = job
        st = build_state(row, coin, iv, d, setup, spread_bps=spreads.get(coin))
        q = panel_questions(st["candidate"]["direction"], setup)
        resp = model.system_one(st, q)
        return k, resp

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(work, j) for j in jobs]
        for fut in as_completed(futs):
            try:
                k, resp = fut.result()
            except Exception as exc:  # budget cap, network...
                print("ERROR", type(exc).__name__, exc, flush=True)
                if "Budget" in type(exc).__name__:
                    break
                continue
            cache.put(k, resp["answers"], resp.get("model", tag))
            done += 1
            if done % 500 == 0:
                cache.save()
                el = time.time() - t0
                print(f"  labelled {done}/{len(jobs)}  {done / el:.1f}/s  stats={model.stats()}", flush=True)
    cache.save()
    print(f"done {done} in {time.time() - t0:.0f}s; cache={len(cache)} stats={model.stats()}", flush=True)


if __name__ == "__main__":
    main()
