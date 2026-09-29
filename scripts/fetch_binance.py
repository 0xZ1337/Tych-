import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tych.data import binance as bn

ap = argparse.ArgumentParser()
ap.add_argument("--coins", default="BTC,ETH,SOL,XRP,NEAR,ENA,ONDO,ZEC,HYPE,PUMP,DOGE,SUI,AVAX,LINK")
ap.add_argument("--days5m", type=float, default=90)
ap.add_argument("--days1m", type=float, default=21)
args = ap.parse_args()
summary = {}
for coin in args.coins.split(","):
    for iv, days in (("5m", args.days5m), ("1m", args.days1m)):
        df = bn.load_history(coin, iv, days, refresh=True)
        summary[f"{coin}_{iv}"] = len(df)
        print(f"{coin:6s} {iv} {len(df):7d} bars {df.index[0] if len(df) else '-'} -> {df.index[-1] if len(df) else '-'}", flush=True)
(bn.CACHE_DIR / "binance_summary.json").write_text(json.dumps(summary, indent=1))
