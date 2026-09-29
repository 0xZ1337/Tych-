"""Assemble backtest datasets: deep Binance history where available, else Hyperliquid."""
from __future__ import annotations

import json

import pandas as pd

from tych.data import binance as bn
from tych.data import hyperliquid as hl
from tych.features import compute_features


def universe() -> dict:
    p = hl.CACHE_DIR / "universe.json"
    return json.loads(p.read_text()) if p.exists() else {"coins": ["BTC", "ETH", "SOL"], "spreads_bps": {}}


def load_dataset(coin: str, interval: str) -> tuple[pd.DataFrame, str]:
    """Returns (ohlcv, source). Binance if it has more bars than Hyperliquid."""
    b = bn.load_history(coin, interval, days=0) if bn.cache_path(coin, interval).exists() else pd.DataFrame()
    h = hl.load_cached(coin, interval)
    if len(b) > len(h):
        return b, "binance"
    return h, "hyperliquid"


def load_features(coins: list[str], interval: str) -> dict[str, tuple[pd.DataFrame, str]]:
    out = {}
    for c in coins:
        df, src = load_dataset(c, interval)
        if len(df) < 300:
            continue
        out[c] = (compute_features(df, interval), src)
    return out
