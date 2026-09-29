"""Download / refresh Hyperliquid candles for the trading universe.

Usage:  python scripts/fetch_data.py [--coins BTC,ETH] [--intervals 1m,5m,15m,1h] [--top 10]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tych.data import hyperliquid as hl  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="")
    ap.add_argument("--intervals", default="1m,5m,15m,1h")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args()

    coins = [c for c in args.coins.split(",") if c] or hl.top_universe(args.top)
    intervals = args.intervals.split(",")
    summary = {}
    for coin in coins:
        for iv in intervals:
            df = hl.update_candles(coin, iv)
            summary[f"{coin}_{iv}"] = {"bars": len(df), "from": str(df.index[0]) if len(df) else None,
                                       "to": str(df.index[-1]) if len(df) else None}
            print(f"{coin:6s} {iv:3s} {len(df):6d} bars  {summary[f'{coin}_{iv}']['from']} -> {summary[f'{coin}_{iv}']['to']}")
    spreads = hl.measure_spreads(coins)
    print("spreads_bps", json.dumps({k: round(v, 3) for k, v in spreads.items()}))
    (hl.CACHE_DIR / "universe.json").write_text(json.dumps({"coins": coins, "spreads_bps": spreads, "summary": summary}, indent=1))


if __name__ == "__main__":
    main()
