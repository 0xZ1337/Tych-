"""Hyperliquid market-data access with a local on-disk cache.

Only the public ``/info`` endpoint is used. No keys, no orders.

Known limitation (measured 2026-09-29): ``candleSnapshot`` returns at most
the ~5000 most recent candles per interval, and refuses to paginate further
back.  That gives roughly:

    1m  -> 3.5 days      5m -> 17 days     15m -> 52 days     1h -> 208 days

The cache therefore *accumulates*: every fetch merges new candles into the
stored file so the history grows the longer the collector runs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Iterable

import pandas as pd
import requests

INFO_URL = "https://api.hyperliquid.xyz/info"
CACHE_DIR = Path(os.environ.get("TYCH_CACHE_DIR", Path(__file__).resolve().parents[2] / "data_cache"))
INTERVAL_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
MAX_CANDLES_PER_CALL = 5000

# Hyperliquid perps base-tier fees (checked 2026-09-29): taker 0.045 %, maker 0.015 %.
TAKER_FEE = 0.00045
MAKER_FEE = 0.00015


def _post(payload: dict, retries: int = 5, timeout: int = 30) -> object:
    delay = 1.0
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            r = requests.post(INFO_URL, json=payload, timeout=timeout)
            if r.status_code == 429:
                time.sleep(delay)
                delay *= 2
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:  # network or bad JSON
            last_exc = exc
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Hyperliquid request failed after {retries} tries: {last_exc}")


def _candles_to_df(raw: list[dict]) -> pd.DataFrame:
    if not raw:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", "trades"]).set_index("ts")
    df = pd.DataFrame(raw)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["t"].astype("int64"), unit="ms", utc=True),
        "open": df["o"].astype(float),
        "high": df["h"].astype(float),
        "low": df["l"].astype(float),
        "close": df["c"].astype(float),
        "volume": df["v"].astype(float),
        "trades": df["n"].astype(int),
    }).set_index("ts").sort_index()
    return out[~out.index.duplicated(keep="last")]


def fetch_candles_raw(coin: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    raw = _post({"type": "candleSnapshot", "req": {"coin": coin, "interval": interval,
                                                   "startTime": int(start_ms), "endTime": int(end_ms)}})
    return _candles_to_df(raw)


def cache_path(coin: str, interval: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{coin}_{interval}.csv"


def load_cached(coin: str, interval: str) -> pd.DataFrame:
    p = cache_path(coin, interval)
    if not p.exists():
        return _candles_to_df([])
    df = pd.read_csv(p, parse_dates=["ts"]).set_index("ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def update_candles(coin: str, interval: str, lookback_days: float = 400) -> pd.DataFrame:
    """Fetch the newest candles from Hyperliquid and merge them into the cache.

    The API caps at ~5000 candles; asking for a wide window simply returns the
    newest 5000, which is what we want.  The last cached candle is always
    re-fetched because it may have been incomplete when stored.
    """
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - int(lookback_days * 86_400_000)
    fresh = fetch_candles_raw(coin, interval, start_ms, now_ms)
    cached = load_cached(coin, interval)
    merged = pd.concat([cached, fresh])
    merged = merged[~merged.index.duplicated(keep="last")].sort_index()
    # drop the still-open last candle so downstream code only sees closed bars
    step = pd.Timedelta(milliseconds=INTERVAL_MS[interval])
    now = pd.Timestamp.now(tz="UTC")
    merged = merged[merged.index + step <= now]
    merged.to_csv(cache_path(coin, interval))
    return merged


def load_candles(coin: str, interval: str, refresh: bool = False) -> pd.DataFrame:
    if refresh or not cache_path(coin, interval).exists():
        return update_candles(coin, interval)
    return load_cached(coin, interval)


def top_universe(n: int = 10, exclude: Iterable[str] = ()) -> list[str]:
    """Most liquid perps by 24h notional volume (public meta endpoint)."""
    meta, ctxs = _post({"type": "metaAndAssetCtxs"})
    rows = []
    for asset, ctx in zip(meta["universe"], ctxs):
        if asset.get("isDelisted") or asset["name"] in set(exclude):
            continue
        rows.append((asset["name"], float(ctx.get("dayNtlVlm", 0.0))))
    rows.sort(key=lambda x: -x[1])
    return [name for name, _ in rows[:n]]


def asset_contexts() -> dict[str, dict]:
    """Funding, open interest, mark/oracle price per coin (live snapshot)."""
    meta, ctxs = _post({"type": "metaAndAssetCtxs"})
    return {a["name"]: c for a, c in zip(meta["universe"], ctxs)}


def l2_spread(coin: str) -> dict:
    """Best bid/ask and relative spread from the live order book."""
    book = _post({"type": "l2Book", "coin": coin})
    bids, asks = book["levels"]
    bid, ask = float(bids[0]["px"]), float(asks[0]["px"])
    mid = (bid + ask) / 2
    return {"bid": bid, "ask": ask, "mid": mid, "spread_bps": (ask - bid) / mid * 1e4,
            "bid_sz": float(bids[0]["sz"]), "ask_sz": float(asks[0]["sz"]), "time": book["time"]}


def measure_spreads(coins: Iterable[str]) -> dict[str, float]:
    """One-shot spread measurement, used to calibrate backtest slippage per coin."""
    out = {}
    for c in coins:
        try:
            out[c] = l2_spread(c)["spread_bps"]
        except Exception:
            out[c] = float("nan")
    return out


if __name__ == "__main__":
    print(json.dumps(top_universe(12), indent=1))
