"""Free, deep candle history from Binance's public market-data mirror.

Used only to *extend the backtest* beyond Hyperliquid's ~5000-candle cap.
Live paper trading always runs on Hyperliquid candles.  Spot USDT pairs are
used; on liquid coins they track the perp within a few bps at 5m granularity,
and the backtest applies Hyperliquid fees and spreads regardless.
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

from tych.data.hyperliquid import CACHE_DIR, INTERVAL_MS

BASE = "https://data-api.binance.vision/api/v3/klines"


def symbol_for(coin: str) -> str:
    return f"{coin}USDT"


def fetch_history(coin: str, interval: str, days: float, end_ms: int | None = None) -> pd.DataFrame:
    end_ms = end_ms or int(time.time() * 1000)
    start_ms = end_ms - int(days * 86_400_000)
    step = INTERVAL_MS[interval]
    frames = []
    cur = start_ms
    sym = symbol_for(coin)
    while cur < end_ms:
        for attempt in range(5):
            try:
                r = requests.get(BASE, params={"symbol": sym, "interval": interval, "startTime": cur, "limit": 1000}, timeout=30)
                if r.status_code == 400:
                    return pd.DataFrame()  # unknown symbol
                r.raise_for_status()
                rows = r.json()
                break
            except requests.RequestException:
                time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"binance fetch failed for {sym}")
        if not rows:
            break
        frames.append(pd.DataFrame(rows).iloc[:, :6])
        cur = int(rows[-1][0]) + step
        if len(rows) < 1000:
            break
        time.sleep(0.05)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames)
    df.columns = ["t", "open", "high", "low", "close", "volume"]
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True),
        "open": df["open"].astype(float), "high": df["high"].astype(float),
        "low": df["low"].astype(float), "close": df["close"].astype(float),
        "volume": df["volume"].astype(float), "trades": 0,
    }).set_index("ts").sort_index()
    out = out[~out.index.duplicated(keep="last")]
    # drop the still-open candle
    out = out[out.index + pd.Timedelta(milliseconds=step) <= pd.Timestamp.now(tz="UTC")]
    return out


def cache_path(coin: str, interval: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"binance_{coin}_{interval}.csv"


def load_history(coin: str, interval: str, days: float, refresh: bool = False) -> pd.DataFrame:
    p = cache_path(coin, interval)
    if p.exists() and not refresh:
        df = pd.read_csv(p, parse_dates=["ts"]).set_index("ts")
        df.index = pd.to_datetime(df.index, utc=True)
        return df
    df = fetch_history(coin, interval, days)
    if not df.empty:
        df.to_csv(p)
    return df
