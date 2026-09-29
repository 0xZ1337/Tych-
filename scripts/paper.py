"""Live paper trading on Hyperliquid candles with real Jev decisions.

No orders are ever sent.  Every closed bar:
  1. refresh candles for the universe (public API)
  2. recompute features, look for a signal on the last closed bar
  3. build the semantic state (with live spread + funding), ask Jev, combine
  4. open / manage virtual positions with the same rules as the backtest engine
Everything is appended to reports/paper/{decisions,trades}.jsonl and a
status.json is rewritten each loop.

    python scripts/paper.py --config reports/best_config.json --interval 5m
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_env  # noqa: E402

load_env()
import pandas as pd  # noqa: E402

from tych.backtest.engine import CostModel  # noqa: E402
from tych.data import hyperliquid as hl  # noqa: E402
from tych.features import compute_features  # noqa: E402
from tych.jev.mock import make_decision_model  # noqa: E402
from tych.jev.questions import panel_questions  # noqa: E402
from tych.jev.state import build_state  # noqa: E402
from tych.panel import PanelParams, combine  # noqa: E402
from tych.setups import SetupParams, generate_signals  # noqa: E402

OUT = Path("reports/paper")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def append(path: Path, rec: dict) -> None:
    with open(path, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


class PaperTrader:
    def __init__(self, coins, interval, sp: SetupParams, pp: PanelParams, cost: CostModel, risk_pct=0.005, equity=10_000.0):
        self.coins, self.interval, self.sp, self.pp, self.cost = coins, interval, sp, pp, cost
        self.model = make_decision_model()
        self.positions: dict[str, dict] = {}
        self.last_bar: dict[str, pd.Timestamp] = {}
        self.equity = equity
        self.start_equity = equity
        self.risk_pct = risk_pct
        self.n_signals = self.n_taken = self.n_closed = 0
        state_file = OUT / "state.json"
        if state_file.exists():
            st = json.loads(state_file.read_text())
            self.equity = st.get("equity", equity)
            self.positions = {k: v for k, v in st.get("positions", {}).items()}
            self.n_signals, self.n_taken, self.n_closed = st.get("n_signals", 0), st.get("n_taken", 0), st.get("n_closed", 0)

    # ---- position management on a newly closed bar
    def manage(self, coin: str, bar: pd.Series, ts: pd.Timestamp) -> None:
        pos = self.positions.get(coin)
        if not pos:
            return
        d = pos["direction"]
        half_spread, slip = self.cost.spread_bps / 2e4, self.cost.slippage_bps / 1e4
        o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
        exit_px = reason = None
        if not pos.get("filled"):
            # pending limit entry: fills if this bar traded through the limit price
            lim = pos["entry"]
            if (d > 0 and l < lim * (1 - 1e-4)) or (d < 0 and h > lim * (1 + 1e-4)):
                pos["filled"] = True
                pos["entry_time"] = str(ts)
            else:
                del self.positions[coin]
                append(OUT / "decisions.jsonl", {"t": now_iso(), "coin": coin, "event": "limit_not_filled", "bar": str(ts)})
                return
            pos["bars"] = 0
        pos["bars"] = pos.get("bars", 0) + 1
        hit_sl = (l <= pos["sl"]) if d > 0 else (h >= pos["sl"])
        hit_tp = (h >= pos["tp"]) if d > 0 else (l <= pos["tp"])
        if pos["bars"] == 1 and ((d > 0 and o <= pos["sl"]) or (d < 0 and o >= pos["sl"])):
            exit_px, reason = o * (1 - d * (half_spread + slip)), "stop_gap"
        elif hit_sl:
            exit_px, reason = pos["sl"] * (1 - d * (half_spread + slip)), "stop"
        elif hit_tp:
            exit_px, reason = pos["tp"], "target"
        elif pos["bars"] >= self.sp.max_bars:
            exit_px, reason = c * (1 - d * (half_spread + slip)), "time"
        if exit_px is None:
            return
        fee_out = self.cost.maker_fee if reason == "target" else self.cost.taker_fee
        gross = d * (exit_px - pos["entry"]) / pos["entry"]
        net = gross - pos["fee_in"] - fee_out
        pnl = self.equity * pos["leverage"] * net
        self.equity += pnl
        self.n_closed += 1
        rec = {**pos, "coin": coin, "exit": exit_px, "exit_time": str(ts), "reason": reason, "gross_pct": gross, "net_pct": net,
               "r": net / pos["sl_pct"], "pnl_usd": pnl, "equity_after": self.equity, "closed_at": now_iso()}
        append(OUT / "trades.jsonl", rec)
        del self.positions[coin]

    def maybe_enter(self, coin: str, f: pd.DataFrame, sig: pd.DataFrame, ts: pd.Timestamp, ctx: dict, spread: dict | None) -> None:
        d = int(sig.loc[ts, "signal"])
        if d == 0 or coin in self.positions:
            return
        setup = sig.loc[ts, "setup"]
        self.n_signals += 1
        row = f.loc[ts]
        spread_bps = spread["spread_bps"] if spread else None
        funding = float(ctx.get("funding", "nan")) if ctx else None
        st = build_state(row, coin, self.interval, d, setup, spread_bps=spread_bps, funding_rate=funding)
        q = panel_questions(st["candidate"]["direction"], setup)
        t0 = time.time()
        resp = self.model.system_one(st, q)
        latency = time.time() - t0
        go, score, size = combine(resp["answers"], setup, self.pp)
        atr = float(row["atr"])
        # entry reference: limit at close (maker) or next open approximated by close + half spread (taker)
        if self.sp.entry_mode == "limit":
            entry, fee_in, filled = float(row["close"]), self.cost.maker_fee, False
        else:
            entry = float(row["close"]) * (1 + d * (self.cost.spread_bps / 2e4 + self.cost.slippage_bps / 1e4))
            fee_in, filled = self.cost.taker_fee, True
        sl_pct = self.sp.sl_atr * atr / entry
        tp = entry + d * self.sp.tp_atr * atr
        if self.sp.tp_mode == "vwap" and setup == "vwap_reversion":
            tp = float(row["vwap"])
            if d * (tp - entry) < 0.5 * self.sp.sl_atr * atr:   # same rule as the backtest engine
                append(OUT / "decisions.jsonl", {"t": now_iso(), "coin": coin, "bar": str(ts), "event": "vwap_target_too_close"})
                return
        lev = min(5.0, self.risk_pct / max(sl_pct, 1e-6)) * size
        dec = {"t": now_iso(), "coin": coin, "bar": str(ts), "setup": setup, "direction": d, "go": go, "panel_score": score,
               "size_mult": size, "latency_ms": round(latency * 1000), "model": resp.get("model"),
               "answers": {k: (v.get("noul", v.get("score", v.get("choice")))) for k, v in resp["answers"].items()},
               "state": st, "usage": resp.get("usage")}
        append(OUT / "decisions.jsonl", dec)
        if not go:
            return
        self.n_taken += 1
        self.positions[coin] = {"direction": d, "setup": setup, "signal_time": str(ts), "entry": entry, "filled": filled,
                                "entry_time": str(ts) if filled else None, "sl": entry - d * self.sp.sl_atr * atr,
                                "tp": tp, "sl_pct": sl_pct, "fee_in": fee_in,
                                "leverage": lev, "panel_score": score, "size_mult": size, "bars": 0}

    def save_status(self) -> None:
        st = {"updated": now_iso(), "equity": self.equity, "return_pct": (self.equity / self.start_equity - 1) * 100,
              "positions": self.positions, "n_signals": self.n_signals, "n_taken": self.n_taken, "n_closed": self.n_closed,
              "model_stats": self.model.stats(), "interval": self.interval, "coins": self.coins,
              "setup": self.sp.to_dict(), "panel": self.pp.to_dict()}
        (OUT / "state.json").write_text(json.dumps(st, indent=1, default=str))

    def loop_once(self) -> None:
        try:
            ctxs = hl.asset_contexts()
        except Exception:
            ctxs = {}
        for coin in self.coins:
            try:
                df = hl.update_candles(coin, self.interval)
                f = compute_features(df.tail(1500), self.interval)
                ts = f.index[-1]
                if self.last_bar.get(coin) == ts:
                    continue
                self.last_bar[coin] = ts
                # manage open position first with the bar that just closed
                self.manage(coin, f.iloc[-1], ts)
                sig = generate_signals(f, self.sp)
                spread = None
                if int(sig.loc[ts, "signal"]) != 0 and coin not in self.positions:
                    try:
                        spread = hl.l2_spread(coin)
                    except Exception:
                        spread = None
                self.maybe_enter(coin, f, sig, ts, ctxs.get(coin, {}), spread)
            except Exception as exc:  # keep the loop alive
                append(OUT / "errors.jsonl", {"t": now_iso(), "coin": coin, "error": repr(exc), "trace": traceback.format_exc()[-800:]})
        self.save_status()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--coins", default="")
    ap.add_argument("--poll", type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    uni = json.loads((hl.CACHE_DIR / "universe.json").read_text())
    coins = [c for c in args.coins.split(",") if c] or uni["coins"]
    sp, pp = SetupParams(), PanelParams()
    if args.config:
        cfg = json.loads(Path(args.config).read_text())
        sp, pp = SetupParams.from_dict(cfg["setup"]), PanelParams.from_dict(cfg["panel"])
        args.interval = cfg.get("interval", args.interval)
    cost = CostModel(spread_bps=1.0, entry_mode=sp.entry_mode)
    pt = PaperTrader(coins, args.interval, sp, pp, cost)
    print(f"paper trading {coins} on {args.interval} entry={sp.entry_mode} model={type(pt.model).__name__}", flush=True)
    while True:
        t0 = time.time()
        pt.loop_once()
        time.sleep(max(5, args.poll - (time.time() - t0)))


if __name__ == "__main__":
    main()
