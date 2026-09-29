"""Raw-edge research: mean forward move (in ATR) after each candidate signal, before costs.

This is the cleanest way to see whether a signal has *any* directional
information, independent of exits and fees.  Train period only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from tych.datasets import load_features, universe  # noqa: E402
from tych.setups import SetupParams, generate_signals  # noqa: E402

HORIZONS = [1, 3, 6, 12, 24]


def fwd_edge(f: pd.DataFrame, mask: np.ndarray, d: np.ndarray) -> dict:
    c, atr = f["close"].values, f["atr"].values
    out = {"n": int(mask.sum())}
    for k in HORIZONS:
        fwd = (np.roll(c, -k) - c) / atr
        fwd[-k:] = np.nan
        v = (fwd * d)[mask]
        out[f"e{k}"] = float(np.nanmean(v)) if len(v) else np.nan
        out[f"t{k}"] = float(np.nanmean(v) / (np.nanstd(v) / np.sqrt(np.sum(~np.isnan(v))))) if len(v) > 2 else np.nan
    return out


def experimental_signals(f: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    c, o, h, l = f["close"].values, f["open"].values, f["high"].values, f["low"].values
    atr, volz, rsi = f["atr"].values, f["vol_z"].values, f["rsi"].values
    bbz, streak = f["bb_z"].values, f["streak"].values
    rng_atr = f["range_atr"].values
    dist, htf = f["dist_vwap_atr"].values, f["htf_slope"].values
    ema21 = f["ema21"].values
    prev_c = np.roll(c, 1)
    prev_vwap = np.roll(f["vwap"].values, 1)
    vwap = f["vwap"].values
    sig = {}
    # climactic bar fade
    up_clim = (rng_atr >= 2.5) & (volz >= 3) & (c > o) & ((c - l) / np.maximum(h - l, 1e-9) > 0.7)
    dn_clim = (rng_atr >= 2.5) & (volz >= 3) & (c < o) & ((h - c) / np.maximum(h - l, 1e-9) > 0.7)
    sig["climax_fade"] = (up_clim | dn_clim, np.where(up_clim, -1, 1))
    # bollinger extreme fade
    bb_hi, bb_lo = (bbz >= 2.5) & (rsi >= 70), (bbz <= -2.5) & (rsi <= 30)
    sig["bb_fade"] = (bb_hi | bb_lo, np.where(bb_hi, -1, 1))
    # streak reversal
    s_up, s_dn = streak >= 6, streak <= -6
    sig["streak6_fade"] = (s_up | s_dn, np.where(s_up, -1, 1))
    # vwap reclaim with HTF trend
    reclaim_up = (prev_c < prev_vwap) & (c > vwap) & (htf > 0.2)
    reclaim_dn = (prev_c > prev_vwap) & (c < vwap) & (htf < -0.2)
    sig["vwap_reclaim_trend"] = (reclaim_up | reclaim_dn, np.where(reclaim_up, 1, -1))
    # far-from-vwap fade without any confirmation
    far_lo, far_hi = dist <= -2.5, dist >= 2.5
    sig["vwap_far_fade"] = (far_lo | far_hi, np.where(far_lo, 1, -1))
    # momentum continuation: strong bar with trend
    mom_up = (c > o) & (rng_atr >= 1.5) & (htf > 0.4) & (volz >= 1.5)
    mom_dn = (c < o) & (rng_atr >= 1.5) & (htf < -0.4) & (volz >= 1.5)
    sig["momentum_cont"] = (mom_up | mom_dn, np.where(mom_up, 1, -1))
    # ema21 pullback in trend, no candle confirmation
    pb_up = (htf > 0.3) & (l <= ema21) & (c > ema21)
    pb_dn = (htf < -0.3) & (h >= ema21) & (c < ema21)
    sig["ema21_touch_trend"] = (pb_up | pb_dn, np.where(pb_up, 1, -1))
    return sig


def main() -> None:
    interval = sys.argv[1] if len(sys.argv) > 1 else "5m"
    uni = universe()
    feats = load_features(uni["coins"], interval)
    starts = [f.index[0] for f, _ in feats.values()]
    ends = [f.index[-1] for f, _ in feats.values()]
    split = min(starts) + (max(ends) - min(starts)) * 0.7
    rows = []
    for coin, (f, src) in feats.items():
        f = f[f.index < split]
        if len(f) < 200:
            continue
        base = generate_signals(f, SetupParams())
        for name in ("vwap_reversion", "trend_pullback", "range_breakout"):
            m = (base["setup"].values == name)
            rows.append({"signal": name, "coin": coin, **fwd_edge(f, m, base["signal"].values)})
        for name, (m, d) in experimental_signals(f).items():
            m = m & ~np.isnan(f["atr"].values)
            rows.append({"signal": name, "coin": coin, **fwd_edge(f, m, d)})
    df = pd.DataFrame(rows)
    agg = df.groupby("signal").apply(lambda g: pd.Series({"n": g["n"].sum(), **{f"e{k}": np.average(g[f"e{k}"].fillna(0), weights=g["n"].clip(lower=1)) for k in HORIZONS}}))
    # pooled t-stats
    print(f"== {interval}: mean forward move in ATR after signal (train period only, before costs); n = signals across {len(feats)} coins")
    print(agg.round(3).sort_values("e6", ascending=False).to_string())
    print("\nper-coin e6 (6-bar horizon):")
    print(df.pivot(index="signal", columns="coin", values="e6").round(2).to_string())
    print("\nround-trip taker cost in ATR units, per coin (10 bps + spread):")
    for coin, (f, src) in feats.items():
        atr_bps = (f["atr_pct"].median()) * 1e4
        print(f"  {coin:5s} median ATR = {atr_bps:5.1f} bps  -> cost/ATR = {(10 + uni['spreads_bps'].get(coin, 1)) / atr_bps:.2f}  ({src})")


if __name__ == "__main__":
    main()
