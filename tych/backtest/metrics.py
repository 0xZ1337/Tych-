from __future__ import annotations

import math

import numpy as np
import pandas as pd

from tych.backtest.engine import portfolio_equity


def summarize(trades: pd.DataFrame, days: float | None = None, risk_pct: float = 0.005) -> dict:
    n = len(trades)
    if n == 0:
        return {"n_trades": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy_pct": 0.0, "profit_factor": 0.0,
                "total_return_pct": 0.0, "max_drawdown_pct": 0.0, "sharpe_trade": 0.0, "trades_per_day": 0.0, "avg_bars": 0.0}
    net = trades["net_pct"].values
    r = trades["r"].values
    wins = net[net > 0].sum()
    losses = -net[net <= 0].sum()
    pf = wins / losses if losses > 0 else (math.inf if wins > 0 else 0.0)
    eq = portfolio_equity(trades, risk_pct=risk_pct)
    curve = eq["equity"].values
    peak = np.maximum.accumulate(curve)
    dd = (curve - peak) / peak
    per_trade_ret = eq["pnl"].values / np.concatenate([[10_000.0], curve[:-1]])
    sharpe = per_trade_ret.mean() / per_trade_ret.std(ddof=1) * math.sqrt(n) if n > 1 and per_trade_ret.std(ddof=1) > 0 else 0.0
    out = {
        "n_trades": int(n),
        "win_rate": float((net > 0).mean()),
        "avg_r": float(r.mean()),
        "expectancy_pct": float(net.mean() * 100),
        "profit_factor": float(min(pf, 99.0)),
        "total_return_pct": float((curve[-1] / 10_000.0 - 1) * 100),
        "max_drawdown_pct": float(dd.min() * 100),
        "sharpe_trade": float(sharpe),
        "avg_bars": float(trades["bars_held"].mean()),
        "pct_target": float((trades["reason"] == "target").mean()),
        "pct_stop": float(trades["reason"].isin(["stop", "stop_gap"]).mean()),
    }
    if days:
        out["trades_per_day"] = float(n / days)
    return out


def breakdown(trades: pd.DataFrame, by: str) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby(by)
    return pd.DataFrame({
        "n": g.size(),
        "win_rate": g["net_pct"].apply(lambda s: (s > 0).mean()),
        "avg_r": g["r"].mean(),
        "expectancy_pct": g["net_pct"].mean() * 100,
        "sum_net_pct": g["net_pct"].sum() * 100,
    }).round(4)
