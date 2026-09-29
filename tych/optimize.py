"""Random-search optimiser over the deterministic layer + panel combination.

Every iteration:
  1. sample setup / exit / panel parameters inside SEARCH_SPACE
  2. regenerate signals for each coin (numpy, cheap)
  3. veto each signal through the cached Jev panel + `combine`
  4. simulate with conservative fills, split trades into TRAIN / VALIDATION by time
  5. log everything to a JSONL file

Selection is done on TRAIN only; VALIDATION is reported, never optimised on.
The objective is the t-statistic of the mean R multiple (edge x sqrt(n)), which
rewards a real edge on many trades rather than a lucky handful.
"""
from __future__ import annotations

import json
import math
import random
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from tych.backtest.engine import CostModel, simulate
from tych.backtest.metrics import summarize
from tych.candidates import SEARCH_SPACE
from tych.jev.cache import PanelCache
from tych.panel import PanelParams, combine
from tych.setups import SetupParams, generate_signals

SETUP_KEYS = [k for k in SEARCH_SPACE if k not in ("sl_atr", "tp_atr", "max_bars")]


def sample_params(rng: random.Random, base: SetupParams | None = None) -> tuple[SetupParams, PanelParams]:
    d = {}
    for k in SETUP_KEYS:
        lo, hi = SEARCH_SPACE[k]
        d[k] = round(rng.uniform(lo, hi), 3)
    d["sl_atr"] = round(rng.uniform(*SEARCH_SPACE["sl_atr"]), 2)
    d["tp_atr"] = round(rng.uniform(*SEARCH_SPACE["tp_atr"]), 2)
    d["max_bars"] = int(rng.choice([6, 9, 12, 18, 24, 36, 48, 60]))
    d["mr_enabled"] = rng.random() < 0.8
    d["pb_enabled"] = rng.random() < 0.8
    d["bo_enabled"] = rng.random() < 0.8
    if not (d["mr_enabled"] or d["pb_enabled"] or d["bo_enabled"]):
        d["pb_enabled"] = True
    sess_choice = rng.random()
    d["sessions"] = ("asia", "europe", "us", "late") if sess_choice < 0.6 else (("europe", "us") if sess_choice < 0.85 else ("us",))
    sp = SetupParams(**d)
    sp.entry_mode = rng.choice(["market", "limit", "limit"])
    pp = PanelParams(
        enabled=True,
        w_setup=round(rng.uniform(0.2, 1.5), 2), w_regime=round(rng.uniform(0.0, 1.5), 2), w_htf=round(rng.uniform(0.0, 1.5), 2),
        w_exhaustion=round(rng.uniform(0.0, 1.0), 2), w_cost=round(rng.uniform(0.0, 1.5), 2), w_quality=round(rng.uniform(0.3, 2.0), 2),
        threshold=round(rng.uniform(0.35, 0.75), 3), min_action_conf=round(rng.choice([0.0, 0.0, 0.2, 0.4, 0.6]), 2),
        veto_skip=rng.random() < 0.7, size_by_quality=rng.random() < 0.6,
    )
    return sp, pp


class Runner:
    def __init__(self, feats: dict[str, tuple[pd.DataFrame, str]], interval: str, cache: PanelCache,
                 spreads: dict[str, float], split: float = 0.7, cost: CostModel | None = None):
        self.feats = feats
        self.interval = interval
        self.cache = cache
        self.spreads = spreads
        self.cost = cost or CostModel()
        # time split shared by all coins
        starts = [f.index[0] for f, _ in feats.values()]
        ends = [f.index[-1] for f, _ in feats.values()]
        t0, t1 = min(starts), max(ends)
        self.split_time = t0 + (t1 - t0) * split
        self.days_train = (self.split_time - t0).total_seconds() / 86400
        self.days_val = (t1 - self.split_time).total_seconds() / 86400
        self.missing = 0

    def run(self, sp: SetupParams, pp: PanelParams | None) -> pd.DataFrame:
        all_trades = []
        for coin, (f, _) in self.feats.items():
            sig = generate_signals(f, sp)
            if (sig["signal"] != 0).sum() == 0:
                continue
            cost = replace(self.cost, spread_bps=float(self.spreads.get(coin, 1.0) or 1.0), entry_mode=getattr(sp, "entry_mode", "market"))
            decide = None
            if pp is not None and pp.enabled:
                idx = f.index
                cache = self.cache

                def decide(i, d, setup, _idx=idx, _coin=coin):
                    k = PanelCache.key(_coin, self.interval, _idx[i], d, setup)
                    ent = cache.get(k)
                    if ent is None:
                        self.missing += 1
                        return False, {"panel_score": float("nan")}
                    go, score, size = combine(ent["answers"], setup, pp)
                    return go, {"panel_score": score, "size_mult": size}
            tr = simulate(f, sig, sp.sl_atr, sp.tp_atr, sp.max_bars, cost, decide=decide, coin=coin)
            if not tr.empty:
                all_trades.append(tr)
        if not all_trades:
            return pd.DataFrame()
        return pd.concat(all_trades, ignore_index=True)

    def evaluate(self, sp: SetupParams, pp: PanelParams | None) -> dict:
        tr = self.run(sp, pp)
        if tr.empty:
            return {"train": summarize(tr), "val": summarize(tr), "objective": -9.0, "n_total": 0}
        train = tr[tr["signal_time"] < self.split_time]
        val = tr[tr["signal_time"] >= self.split_time]
        mt, mv = summarize(train, self.days_train), summarize(val, self.days_val)
        mt["tstat"] = tstat(train["r"]) if len(train) > 1 else 0.0
        mv["tstat"] = tstat(val["r"]) if len(val) > 1 else 0.0
        # objective: t-stat on train, penalised when too few trades to be meaningful
        n = len(train)
        obj = mt["tstat"] * min(1.0, n / 60.0)
        return {"train": mt, "val": mv, "objective": float(obj), "n_total": int(len(tr))}


def tstat(r: pd.Series) -> float:
    r = np.asarray(r, dtype=float)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return 0.0
    return float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r))))


def random_search(runner: Runner, n_iter: int, seed: int, log_path: Path, label: str = "") -> list[dict]:
    rng = random.Random(seed)
    results = []
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with open(log_path, "a") as fh:
        for it in range(1, n_iter + 1):
            sp, pp = sample_params(rng)
            with_panel = runner.evaluate(sp, pp)
            no_panel = runner.evaluate(sp, None)
            rec = {"iter": it, "label": label, "seed": seed, "setup": sp.to_dict(), "panel": pp.to_dict(),
                   "with_panel": with_panel, "no_panel": no_panel, "elapsed": round(time.time() - t0, 1)}
            results.append(rec)
            fh.write(json.dumps(rec, default=str) + "\n")
            fh.flush()
            wt, wv = with_panel["train"], with_panel["val"]
            nt = no_panel["train"]
            print(f"[{label}] it {it:3d}/{n_iter}  obj={with_panel['objective']:6.2f}  "
                  f"train n={wt['n_trades']:4d} R={wt['avg_r']:+.3f} pf={wt['profit_factor']:.2f} | "
                  f"val n={wv['n_trades']:4d} R={wv['avg_r']:+.3f} pf={wv['profit_factor']:.2f} | "
                  f"no-panel train n={nt['n_trades']:4d} R={nt['avg_r']:+.3f}  ({rec['elapsed']:.0f}s)", flush=True)
    return results
