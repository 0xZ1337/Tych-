"""200-round research loop: one named hypothesis per round, hill-climbing on TRAIN.

Each round either MUTATES the current best configuration (accepted only if the
train objective improves with at least 40 train trades) or MEASURES something
about the current best (robustness, ablation) without changing it.  Validation
metrics are logged for every round and never used for acceptance.
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path

import pandas as pd

from tych.optimize import Runner
from tych.panel import PanelParams
from tych.setups import SetupParams

MIN_TRAIN_TRADES = 40


def _m(name, phase, **delta):
    """Mutation round: set fields on SetupParams (sp.*) or PanelParams (pp.*)."""
    def apply(sp, pp):
        sd = {k[3:]: v for k, v in delta.items() if k.startswith("sp_")}
        pdd = {k[3:]: v for k, v in delta.items() if k.startswith("pp_")}
        return replace(sp, **sd), replace(pp, **pdd)
    return {"name": name, "phase": phase, "kind": "mutate", "apply": apply, "delta": delta}


def _measure(name, phase, fn):
    return {"name": name, "phase": phase, "kind": "measure", "fn": fn}


def catalogue(coins: list[str]) -> list[dict]:
    R = []
    # ---------------- A. execution & exits
    R.append(_m("entry: resting limit at signal close (maker) instead of market", "A-execution", sp_entry_mode="limit"))
    for v in (0.7, 0.85, 1.2, 1.4, 1.6, 2.0):
        R.append(_m(f"stop = {v} ATR", "A-exits", sp_sl_atr=v))
    for v in (1.0, 1.2, 2.0, 2.5, 3.0, 3.5, 4.0):
        R.append(_m(f"target = {v} ATR", "A-exits", sp_tp_atr=v))
    for v in (6, 9, 12, 18, 36, 60):
        R.append(_m(f"time stop = {v} bars", "A-exits", sp_max_bars=v))
    R.append(_m("reversion targets the session VWAP instead of a fixed ATR multiple", "A-exits", sp_tp_mode="vwap"))
    for v in (1.0, 1.5, 2.0, 3.0, 4.0):
        R.append(_m(f"trade only when 1 ATR >= {v} x round-trip cost", "A-costs", sp_min_atr_cost_ratio=v))
    R.append(_measure("cost sensitivity: target exits also pay taker", "A-costs", lambda run, sp, pp: run.evaluate_cost(sp, pp, tp_taker=True)))
    R.append(_measure("cost sensitivity: zero slippage", "A-costs", lambda run, sp, pp: run.evaluate_cost(sp, pp, slippage_bps=0.0)))
    R.append(_measure("cost sensitivity: 2 bps slippage", "A-costs", lambda run, sp, pp: run.evaluate_cost(sp, pp, slippage_bps=2.0)))
    R.append(_measure("cost sensitivity: all fills taker, 1 bp slippage", "A-costs", lambda run, sp, pp: run.evaluate_cost(sp, pp, entry_mode="market", tp_taker=True, slippage_bps=1.0)))
    for sl, tp in ((0.8, 1.2), (0.8, 2.0), (1.0, 2.0), (1.2, 1.8), (1.2, 2.4), (1.5, 1.5)):
        R.append(_m(f"joint exits stop {sl} / target {tp} ATR", "A-exits", sp_sl_atr=sl, sp_tp_atr=tp))
    # ---------------- B. setup gating
    R.append(_m("disable vwap_reversion", "B-setups", sp_mr_enabled=False))
    R.append(_m("disable trend_pullback", "B-setups", sp_pb_enabled=False))
    R.append(_m("disable range_breakout", "B-setups", sp_bo_enabled=False))
    R.append(_m("only vwap_reversion", "B-setups", sp_mr_enabled=True, sp_pb_enabled=False, sp_bo_enabled=False))
    R.append(_m("only trend_pullback", "B-setups", sp_mr_enabled=False, sp_pb_enabled=True, sp_bo_enabled=False))
    R.append(_m("only range_breakout", "B-setups", sp_mr_enabled=False, sp_pb_enabled=False, sp_bo_enabled=True))
    for v in (1.5, 1.75, 2.25, 2.5, 3.0):
        R.append(_m(f"reversion: extension >= {v} ATR from VWAP", "B-reversion", sp_mr_dist_atr=v))
    for lo, hi in ((35, 65), (25, 75), (22, 78), (38, 62)):
        R.append(_m(f"reversion: RSI extremes {lo}/{hi}", "B-reversion", sp_mr_rsi_low=float(lo), sp_mr_rsi_high=float(hi)))
    for v in (0.2, 0.45, 0.5):
        R.append(_m(f"reversion: rejection wick >= {v}", "B-reversion", sp_mr_wick_min=v))
    for v in (0.7, 1.5, 2.0):
        R.append(_m(f"reversion: volume >= {v} x median", "B-reversion", sp_mr_vol_z_min=v))
    for v in (0.4, 1.0, 1.5):
        R.append(_m(f"reversion: do not fade HTF slope beyond {v}", "B-reversion", sp_mr_max_htf_slope=v))
    R.append(_m("reversion: highest timeframe must agree with the fade", "B-reversion", sp_htf2_fade=True))
    for v in (0.05, 0.1, 0.4, 0.6):
        R.append(_m(f"pullback: HTF slope >= {v}", "B-pullback", sp_pb_min_htf_slope=v))
    for v in (0.05, 0.3, 0.4):
        R.append(_m(f"pullback: touch tolerance {v} ATR", "B-pullback", sp_pb_touch_tol_atr=v))
    for lo, hi in ((30, 70), (45, 55), (40, 65)):
        R.append(_m(f"pullback: RSI band {lo}-{hi}", "B-pullback", sp_pb_rsi_lo=float(lo), sp_pb_rsi_hi=float(hi)))
    for v in (1.0, 2.0, 3.0):
        R.append(_m(f"breakout: volume >= {v} x median", "B-breakout", sp_bo_vol_z_min=v))
    for v in (0.7, 1.1, 1.4):
        R.append(_m(f"breakout: volatility regime >= {v}", "B-breakout", sp_bo_atr_regime_min=v))
    for v in (0.35, 0.6, 0.75):
        R.append(_m(f"breakout: body >= {v} of range", "B-breakout", sp_bo_min_body=v))
    R.append(_m("trend setups must agree with the highest timeframe", "B-setups", sp_htf2_align=True))
    # ---------------- C. time & regime filters
    for sess in (("europe", "us"), ("us",), ("asia", "europe"), ("europe", "us", "late")):
        R.append(_m(f"sessions {'+'.join(sess)} only", "C-time", sp_sessions=sess))
    for h in ((0, 1, 2, 3), (4, 5, 6, 7), (8, 9, 10, 11), (12, 13, 14, 15), (16, 17, 18, 19), (20, 21, 22, 23)):
        R.append(_m(f"exclude UTC hours {h[0]}-{h[-1]}", "C-time", sp_exclude_hours=h))
    R.append(_m("weekdays only", "C-time", sp_weekdays_only=True))
    for v in (3.0, 4.0, 6.0):
        R.append(_m(f"skip climactic volume > {v} x median", "C-regime", sp_max_vol_z=v))
    for v in (0.8, 1.2):
        R.append(_m(f"skip quiet bars, volume < {v} x median", "C-regime", sp_min_vol_z=v))
    for lo, hi in ((0.8, 99.0), (1.0, 99.0), (0.0, 1.5), (0.7, 1.6)):
        R.append(_m(f"volatility regime window {lo}-{hi}", "C-regime", sp_atr_regime_min=lo, sp_atr_regime_max=hi))
    for v in (3, 4, 6):
        R.append(_m(f"skip after streaks longer than {v} candles", "C-regime", sp_max_abs_streak=v))
    for v in (1.5, 2.0, 3.0):
        R.append(_m(f"skip signal bars wider than {v} ATR", "C-regime", sp_max_range_atr=v))
    for v in (0.5, 0.8):
        R.append(_m(f"require signal bar >= {v} ATR", "C-regime", sp_min_range_atr=v))
    for v in (2.0, 3.0):
        R.append(_m(f"skip when |bollinger z| > {v}", "C-regime", sp_bb_z_abs_max=v))
    # ---------------- D. panel usage
    R.append(_measure("measure: deterministic layer alone (panel OFF) at this point", "D-panel", lambda run, sp, pp: run.evaluate(sp, None)))
    for v in (0.4, 0.45, 0.5, 0.6, 0.65, 0.7, 0.75):
        R.append(_m(f"panel threshold {v}", "D-panel", pp_threshold=v))
    R.append(_m("no veto on action == skip", "D-panel", pp_veto_skip=False))
    for v in (0.2, 0.4, 0.6):
        R.append(_m(f"require action confidence >= {v}", "D-panel", pp_min_action_conf=v))
    R.append(_m("size by quality OFF", "D-panel", pp_size_by_quality=False))
    R.append(_m("size by quality ON", "D-panel", pp_size_by_quality=True))
    for w in ("w_setup", "w_regime", "w_htf", "w_exhaustion", "w_cost", "w_quality"):
        R.append(_m(f"panel weight {w} = 0", "D-panel", **{f"pp_{w}": 0.0}))
        R.append(_m(f"panel weight {w} = 2", "D-panel", **{f"pp_{w}": 2.0}))
    R.append(_m("panel: quality only", "D-panel", pp_w_setup=0.0, pp_w_regime=0.0, pp_w_htf=0.0, pp_w_exhaustion=0.0, pp_w_cost=0.0, pp_w_quality=1.0))
    R.append(_m("panel: setup_valid only", "D-panel", pp_w_setup=1.0, pp_w_regime=0.0, pp_w_htf=0.0, pp_w_exhaustion=0.0, pp_w_cost=0.0, pp_w_quality=0.0))
    R.append(_m("panel: gate on p(take) instead of the weighted mix", "D-panel", pp_use_p_take=True, pp_threshold=0.2))
    R.append(_m("panel: weighted mix again", "D-panel", pp_use_p_take=False))
    R.append(_m("panel: require action == take", "D-panel", pp_require_take=True))
    R.append(_m("panel: do not require action == take", "D-panel", pp_require_take=False))
    R.append(_m("panel: stricter threshold for breakouts (+0.1)", "D-panel", pp_threshold_by_setup={"range_breakout": 0.75}))
    R.append(_m("panel: looser threshold for pullbacks (-0.1)", "D-panel", pp_threshold_by_setup={"trend_pullback": 0.45}))
    R.append(_m("panel OFF: keep the deterministic layer alone if it beats the tuned panel", "D-panel", pp_enabled=False))
    for v in (3, 6, 12):
        R.append(_m(f"cooldown {v} bars after each exit", "D-risk", sp_cooldown_bars=v))
    for v in (2, 4, 8):
        R.append(_m(f"max {v} trades per coin per day", "D-risk", sp_max_trades_per_day=v))
    # ---------------- E. coins, direction, robustness, ablation
    R.append(_m("long only", "E-direction", sp_long_only=True))
    R.append(_m("short only", "E-direction", sp_short_only=True, sp_long_only=False))
    R.append(_m("both directions (restore)", "E-direction", sp_short_only=False, sp_long_only=False))
    for c in coins:
        def drop(sp, pp, c=c):
            cur = sp.coins or tuple(coins)
            return replace(sp, coins=tuple(x for x in cur if x != c)), pp
        R.append({"name": f"drop {c}", "phase": "E-coins", "kind": "mutate", "apply": drop, "delta": {"sp_coins": f"minus {c}"}})
    R.append(_m("only high-ATR coins (ZEC, NEAR, ENA, PUMP, HYPE, ONDO)", "E-coins", sp_coins=tuple(x for x in coins if x in ("ZEC", "NEAR", "ENA", "PUMP", "HYPE", "ONDO"))))
    R.append(_m("only majors (BTC, ETH, SOL, XRP)", "E-coins", sp_coins=tuple(x for x in coins if x in ("BTC", "ETH", "SOL", "XRP"))))
    R.append(_m("all coins (restore)", "E-coins", sp_coins=()))

    def resweep(field, values, phase):
        for v in values:
            R.append(_m(f"re-sweep {field} = {v}", phase, **{field: v}))
    resweep("sp_sl_atr", (0.9, 1.1, 1.3), "E-refine")
    resweep("sp_tp_atr", (1.4, 1.8, 2.2), "E-refine")
    resweep("pp_threshold", (0.52, 0.58), "E-refine")
    resweep("sp_max_bars", (15, 30), "E-refine")
    for k, mult in (("sl_atr", 0.9), ("sl_atr", 1.1), ("tp_atr", 0.9), ("tp_atr", 1.1), ("max_bars", 0.8), ("max_bars", 1.25)):
        R.append(_measure(f"robustness: {k} x {mult}", "E-robustness", lambda run, sp, pp, k=k, mult=mult: run.evaluate(replace(sp, **{k: (int(round(getattr(sp, k) * mult)) if k == "max_bars" else round(getattr(sp, k) * mult, 3))}), pp)))
    for d in (-0.05, 0.05):
        R.append(_measure(f"robustness: panel threshold {d:+}", "E-robustness", lambda run, sp, pp, d=d: run.evaluate(sp, replace(pp, threshold=pp.threshold + d))))
    for q in range(4):
        R.append(_measure(f"walk-forward: quarter {q + 1} of the full period", "E-walkforward", lambda run, sp, pp, q=q: run.evaluate_window(sp, pp, q, 4)))
    R.append(_measure("ablation: final config WITHOUT panel", "E-ablation", lambda run, sp, pp: run.evaluate(sp, None)))
    R.append(_measure("ablation: final config with the offline mock panel", "E-ablation", lambda run, sp, pp: run.evaluate_mock(sp, pp)))
    return R


def run_loop(runner: Runner, base_sp: SetupParams, base_pp: PanelParams, rounds: list[dict], log_path: Path,
             n_rounds: int = 200, ablate_accepted: bool = True) -> dict:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    sp, pp = base_sp, base_pp
    best = runner.evaluate(sp, pp)
    best_obj = best["objective"]
    accepted: list[dict] = []
    t0 = time.time()
    rows = []
    fh = open(log_path, "a")

    def log(rec):
        rows.append(rec)
        fh.write(json.dumps(rec, default=str) + "\n")
        fh.flush()
        t, v = rec["train"], rec["val"]
        print(f"[{rec['round']:3d}] {rec['phase']:14s} {rec['kind']:7s} {rec['name'][:62]:62s} obj={rec['objective']:6.2f} "
              f"train n={t['n_trades']:4d} R={t['avg_r']:+.3f} | val n={v['n_trades']:4d} R={v['avg_r']:+.3f} t={v.get('tstat', 0):+.2f} "
              f"-> {rec['decision']}", flush=True)

    log({"round": 0, "phase": "base", "kind": "base", "name": "starting point", "objective": best_obj, "train": best["train"], "val": best["val"],
         "decision": "baseline", "setup": sp.to_dict(), "panel": pp.to_dict()})
    r_no = 0
    for r in rounds:
        if r_no >= n_rounds:
            break
        r_no += 1
        if r["kind"] == "mutate":
            nsp, npp = r["apply"](sp, pp)
            res = runner.evaluate(nsp, npp)
            ok = res["train"]["n_trades"] >= MIN_TRAIN_TRADES and res["objective"] > best_obj + 1e-9
            decision = "ACCEPT" if ok else ("reject (too few train trades)" if res["train"]["n_trades"] < MIN_TRAIN_TRADES else "reject")
            log({"round": r_no, "phase": r["phase"], "kind": "mutate", "name": r["name"], "delta": r.get("delta"), "objective": res["objective"],
                 "train": res["train"], "val": res["val"], "decision": decision, "best_obj_before": best_obj})
            if ok:
                sp, pp, best_obj, best = nsp, npp, res["objective"], res
                accepted.append({"round": r_no, "name": r["name"], "delta": r.get("delta"), "objective": best_obj, "val_R": res["val"]["avg_r"], "val_t": res["val"].get("tstat", 0)})
        else:
            res = r["fn"](runner, sp, pp)
            log({"round": r_no, "phase": r["phase"], "kind": "measure", "name": r["name"], "objective": res["objective"],
                 "train": res["train"], "val": res["val"], "decision": "measured"})
    # ablation of each accepted step (remove it from the final config, measure)
    if ablate_accepted:
        for a in accepted:
            if r_no >= n_rounds:
                break
            r_no += 1
            d = a["delta"] or {}
            sd = {k[3:]: getattr(base_sp, k[3:]) for k in d if k.startswith("sp_")}
            pdd = {k[3:]: getattr(base_pp, k[3:]) for k in d if k.startswith("pp_")}
            res = runner.evaluate(replace(sp, **sd), replace(pp, **pdd))
            log({"round": r_no, "phase": "E-ablation", "kind": "measure", "name": f"ablation: revert '{a['name']}'", "objective": res["objective"],
                 "train": res["train"], "val": res["val"], "decision": "measured", "contribution_train_obj": best_obj - res["objective"], "contribution_val_R": best["val"]["avg_r"] - res["val"]["avg_r"]})
    while r_no < n_rounds:
        r_no += 1
        res = runner.evaluate(sp, pp)
        log({"round": r_no, "phase": "final", "kind": "measure", "name": "final configuration (frozen)", "objective": res["objective"], "train": res["train"], "val": res["val"], "decision": "measured"})
    fh.close()
    return {"rounds": r_no, "accepted": accepted, "final_setup": sp.to_dict(), "final_panel": pp.to_dict(), "final": best, "elapsed": time.time() - t0, "rows": rows}
