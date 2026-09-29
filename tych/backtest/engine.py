"""Bar-by-bar execution simulator with explicit, conservative fill rules.

Rules (all deliberately pessimistic):
  * entry  : market order at the OPEN of the bar after the signal, paying
             taker fee + half spread + slippage.
  * stop   : market, filled at stop price minus slippage (taker).
  * target : resting limit at TP (maker fee) unless `tp_taker=True`.
  * if a bar touches both TP and SL we assume the STOP was hit first.
  * time stop: exit at the close of the `max_bars`-th bar (taker).
  * one open position per coin; a new signal while in a trade is ignored.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from tych.data.hyperliquid import TAKER_FEE, MAKER_FEE


@dataclass
class CostModel:
    taker_fee: float = TAKER_FEE
    maker_fee: float = MAKER_FEE
    spread_bps: float = 1.0        # full spread; half is paid on each market fill
    slippage_bps: float = 0.5      # extra adverse move on market fills
    tp_taker: bool = False         # True = exits at target also pay taker (most conservative)
    entry_mode: str = "market"     # "market": taker at next open; "limit": maker at signal close, must trade through

    def to_dict(self) -> dict:
        return asdict(self)


def simulate(df: pd.DataFrame, signals: pd.DataFrame, sl_atr: float, tp_atr: float, max_bars: int,
             cost: CostModel, decide=None, coin: str = "") -> pd.DataFrame:
    """Run trades for one instrument.

    ``decide(i, direction, setup) -> (go: bool, meta: dict)`` is called on each
    raw signal and may veto it (this is where the Jev panel plugs in).  ``meta``
    is stored on the trade record (e.g. panel score, size multiplier).
    """
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    atr = df["atr"].values
    sig = signals["signal"].values
    setup_names = signals["setup"].values
    idx = df.index
    n = len(df)
    half_spread = cost.spread_bps / 2e4
    slip = cost.slippage_bps / 1e4

    trades = []
    i = 0
    while i < n - 1:
        d = int(sig[i])
        if d == 0 or np.isnan(atr[i]):
            i += 1
            continue
        meta = {"size_mult": 1.0}
        if decide is not None:
            go, m = decide(i, d, setup_names[i])
            meta.update(m)
            if not go:
                i += 1
                continue
        entry_bar = i + 1
        if cost.entry_mode == "limit":
            # resting limit at the signal bar close; filled only if the next bar trades THROUGH it
            limit_px = c[i]
            traded_through = (l[entry_bar] < limit_px * (1 - 1e-4)) if d > 0 else (h[entry_bar] > limit_px * (1 + 1e-4))
            if not traded_through:
                i += 1
                continue
            entry = limit_px
            fee_in = cost.maker_fee
        else:
            entry = o[entry_bar] * (1 + d * (half_spread + slip))
            fee_in = cost.taker_fee
        sl_dist = sl_atr * atr[i]
        tp_dist = tp_atr * atr[i]
        sl_px = entry - d * sl_dist
        tp_px = entry + d * tp_dist
        exit_px = None
        exit_bar = None
        reason = None
        last = min(entry_bar + max_bars - 1, n - 1)
        for j in range(entry_bar, last + 1):
            hit_sl = (l[j] <= sl_px) if d > 0 else (h[j] >= sl_px)
            hit_tp = (h[j] >= tp_px) if d > 0 else (l[j] <= tp_px)
            if j == entry_bar:
                # gap through the stop at the open: fill at the open
                if (d > 0 and o[j] <= sl_px) or (d < 0 and o[j] >= sl_px):
                    exit_px, exit_bar, reason = o[j] * (1 - d * (half_spread + slip)), j, "stop_gap"
                    break
            if hit_sl:                       # stop first when both are touched
                exit_px, exit_bar, reason = sl_px * (1 - d * (half_spread + slip)), j, "stop"
                break
            if hit_tp:
                exit_px, exit_bar, reason = (tp_px * (1 - d * (half_spread + slip)) if cost.tp_taker else tp_px), j, "target"
                break
        if exit_px is None:
            exit_bar = last
            exit_px = c[last] * (1 - d * (half_spread + slip))
            reason = "time"
        fee_out = cost.maker_fee if (reason == "target" and not cost.tp_taker) else cost.taker_fee
        gross = d * (exit_px - entry) / entry
        net = gross - fee_in - fee_out
        r_mult = net / (sl_dist / entry)
        trades.append({
            "coin": coin, "setup": setup_names[i], "direction": d,
            "signal_time": idx[i], "entry_time": idx[entry_bar], "exit_time": idx[exit_bar],
            "entry": entry, "exit": exit_px, "sl": sl_px, "tp": tp_px, "reason": reason,
            "bars_held": exit_bar - entry_bar + 1, "gross_pct": gross, "fees_pct": fee_in + fee_out,
            "net_pct": net, "sl_pct": sl_dist / entry, "r": r_mult, **meta,
        })
        i = exit_bar + 1
    cols = ["coin", "setup", "direction", "signal_time", "entry_time", "exit_time", "entry", "exit", "sl", "tp", "reason",
            "bars_held", "gross_pct", "fees_pct", "net_pct", "sl_pct", "r", "size_mult"]
    if not trades:
        return pd.DataFrame(columns=cols + ["panel_score"])
    return pd.DataFrame(trades)


def portfolio_equity(trades: pd.DataFrame, risk_pct: float = 0.005, max_leverage: float = 5.0,
                     start_equity: float = 10_000.0) -> pd.DataFrame:
    """Fixed-fractional sizing: notional = equity * risk_pct / sl_pct (capped)."""
    if trades.empty:
        return pd.DataFrame(columns=["time", "equity", "pnl"])
    t = trades.sort_values("exit_time").reset_index(drop=True)
    eq = start_equity
    rows = []
    for _, tr in t.iterrows():
        lev = min(max_leverage, risk_pct / max(tr["sl_pct"], 1e-6)) * float(tr.get("size_mult", 1.0))
        pnl = eq * lev * tr["net_pct"]
        eq += pnl
        rows.append({"time": tr["exit_time"], "equity": eq, "pnl": pnl, "leverage": lev})
    return pd.DataFrame(rows)
