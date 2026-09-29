"""Deterministic candidate generators (the strategy's own edge).

Each generator returns a DataFrame indexed like the features with columns:
    signal   : +1 long / -1 short / 0 none
    setup    : name of the setup
Only the *last closed bar* is used, so signals are executable at next open.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np
import pandas as pd


@dataclass
class SetupParams:
    # vwap mean reversion
    mr_enabled: bool = True
    mr_dist_atr: float = 2.0          # |close - vwap| / atr threshold
    mr_rsi_low: float = 30.0
    mr_rsi_high: float = 70.0
    mr_wick_min: float = 0.35         # rejection wick ratio
    mr_vol_z_min: float = 1.0
    mr_max_htf_slope: float = 0.8     # do not fade a strongly trending HTF
    # trend pullback
    pb_enabled: bool = True
    pb_min_htf_slope: float = 0.2
    pb_rsi_lo: float = 38.0
    pb_rsi_hi: float = 62.0
    pb_touch_tol_atr: float = 0.15    # how close to ema21 the pullback must come
    # range breakout
    bo_enabled: bool = True
    bo_vol_z_min: float = 1.5
    bo_atr_regime_min: float = 0.9
    bo_min_body: float = 0.5
    # exits (in ATR multiples) and time stop (bars)
    sl_atr: float = 1.0
    tp_atr: float = 1.5
    max_bars: int = 24
    # global
    sessions: tuple = ("asia", "europe", "us", "late")
    entry_mode: str = "market"    # execution style, see backtest.engine.CostModel
    tp_mode: str = "atr"              # "atr" fixed multiple; "vwap" = reversion trades target the session VWAP
    min_atr_cost_ratio: float = 0.0   # only trade when 1 ATR (bps) >= ratio x round-trip taker cost (bps)
    cost_bps_ref: float = 10.0        # reference round-trip cost used by the filter
    # --- research-loop filters (default = no effect)
    max_vol_z: float = 99.0           # skip climactic volume bars above this
    min_vol_z: float = 0.0            # skip dead bars below this
    atr_regime_min: float = 0.0       # global volatility-regime window
    atr_regime_max: float = 99.0
    max_abs_streak: int = 99          # skip after long one-sided streaks
    htf2_align: bool = False          # require the highest timeframe to agree with the direction (pullback/breakout)
    htf2_fade: bool = False           # for reversion: require the highest timeframe to be against the extension
    exclude_hours: tuple = ()         # UTC hours excluded
    weekdays_only: bool = False
    long_only: bool = False
    short_only: bool = False
    coins: tuple = ()                 # restrict to these coins (empty = all)
    max_range_atr: float = 99.0       # skip huge signal bars
    min_range_atr: float = 0.0
    bb_z_abs_max: float = 99.0        # skip when already beyond N sigma
    cooldown_bars: int = 0            # bars to wait after any trade exit (engine-level)
    max_trades_per_day: int = 0       # 0 = unlimited (engine-level)

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("sessions", "exclude_hours", "coins"):
            d[k] = list(getattr(self, k))
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "SetupParams":
        d = dict(d)
        for k in ("sessions", "exclude_hours", "coins"):
            if k in d:
                d[k] = tuple(d[k])
        return cls(**d)


def generate_signals(f: pd.DataFrame, p: SetupParams) -> pd.DataFrame:
    n = len(f)
    if n == 0:
        return pd.DataFrame({"signal": np.zeros(0, dtype=int), "setup": np.array([], dtype=object)}, index=f.index)
    signal = np.zeros(n, dtype=int)
    setup = np.array([""] * n, dtype=object)

    c, o, h, l = f["close"].values, f["open"].values, f["high"].values, f["low"].values
    rsi = f["rsi"].values
    dist = f["dist_vwap_atr"].values
    lw, uw = f["lower_wick"].values, f["upper_wick"].values
    volz = f["vol_z"].values
    hslope = f["htf_slope"].values
    htrend = f["htf_trend"].values
    ema21, ema9, ema50, atr = f["ema21"].values, f["ema9"].values, f["ema50"].values, f["atr"].values
    hi20, lo20 = f["hi20"].values, f["lo20"].values
    hi20_prev = np.roll(hi20, 1); lo20_prev = np.roll(lo20, 1)
    hi20_prev[0] = np.nan; lo20_prev[0] = np.nan
    regime = f["atr_regime"].values
    body = f["body_ratio"].values
    ok_session = f["session"].isin(list(p.sessions)).values
    valid = ~np.isnan(atr) & ~np.isnan(dist) & ~np.isnan(hslope) & ok_session
    if p.min_atr_cost_ratio > 0:
        atr_bps = atr / c * 1e4
        valid = valid & (atr_bps >= p.min_atr_cost_ratio * p.cost_bps_ref)
    valid = valid & (volz <= p.max_vol_z) & (volz >= p.min_vol_z) & (regime >= p.atr_regime_min) & (regime <= p.atr_regime_max)
    valid = valid & (np.abs(f["streak"].values) <= p.max_abs_streak) & (f["range_atr"].values <= p.max_range_atr) & (f["range_atr"].values >= p.min_range_atr)
    valid = valid & (np.abs(f["bb_z"].values) <= p.bb_z_abs_max)
    if p.exclude_hours:
        valid = valid & ~np.isin(f.index.hour, list(p.exclude_hours))
    if p.weekdays_only:
        valid = valid & (f.index.dayofweek < 5)

    if p.mr_enabled:
        long_mr = valid & (dist <= -p.mr_dist_atr) & (rsi <= p.mr_rsi_low) & (lw >= p.mr_wick_min) \
            & (volz >= p.mr_vol_z_min) & (hslope > -p.mr_max_htf_slope) & (c > l + 0.3 * (h - l))
        short_mr = valid & (dist >= p.mr_dist_atr) & (rsi >= p.mr_rsi_high) & (uw >= p.mr_wick_min) \
            & (volz >= p.mr_vol_z_min) & (hslope < p.mr_max_htf_slope) & (c < h - 0.3 * (h - l))
        signal[long_mr] = 1; setup[long_mr] = "vwap_reversion"
        signal[short_mr] = -1; setup[short_mr] = "vwap_reversion"

    if p.pb_enabled:
        tol = p.pb_touch_tol_atr * atr
        long_pb = valid & (hslope >= p.pb_min_htf_slope) & (htrend > 0) & (c > ema50) \
            & (l <= ema21 + tol) & (c > ema21) & (c > o) & (rsi >= p.pb_rsi_lo) & (rsi <= p.pb_rsi_hi + 8)
        short_pb = valid & (hslope <= -p.pb_min_htf_slope) & (htrend < 0) & (c < ema50) \
            & (h >= ema21 - tol) & (c < ema21) & (c < o) & (rsi <= p.pb_rsi_hi) & (rsi >= p.pb_rsi_lo - 8)
        m = (signal == 0)
        signal[long_pb & m] = 1; setup[long_pb & m] = "trend_pullback"
        signal[short_pb & m] = -1; setup[short_pb & m] = "trend_pullback"

    htf2 = f["htf2_trend"].values
    if p.bo_enabled:
        long_bo = valid & (c > hi20_prev) & (volz >= p.bo_vol_z_min) & (regime >= p.bo_atr_regime_min) \
            & (body >= p.bo_min_body) & (c > o) & (htrend >= 0)
        short_bo = valid & (c < lo20_prev) & (volz >= p.bo_vol_z_min) & (regime >= p.bo_atr_regime_min) \
            & (body >= p.bo_min_body) & (c < o) & (htrend <= 0)
        m = (signal == 0)
        signal[long_bo & m] = 1; setup[long_bo & m] = "range_breakout"
        signal[short_bo & m] = -1; setup[short_bo & m] = "range_breakout"

    if p.htf2_fade:
        # reversion only when the highest timeframe opposes the extension, i.e. agrees with the fade
        bad = (setup == "vwap_reversion") & (np.sign(signal) != htf2)
        signal[bad] = 0; setup[bad] = ""
    if p.htf2_align:
        bad = (setup != "vwap_reversion") & (signal != 0) & (np.sign(signal) != htf2)
        signal[bad] = 0; setup[bad] = ""
    if p.long_only:
        setup[signal < 0] = ""; signal[signal < 0] = 0
    if p.short_only:
        setup[signal > 0] = ""; signal[signal > 0] = 0
    out = pd.DataFrame({"signal": signal, "setup": setup}, index=f.index)
    if p.tp_mode == "vwap":
        tp_px = np.full(n, np.nan)
        m = (setup == "vwap_reversion") & (signal != 0)
        tp_px[m] = f["vwap"].values[m]
        out["tp_px"] = tp_px
    return out
