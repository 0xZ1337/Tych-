import numpy as np
import pandas as pd

from tych.backtest.engine import CostModel, simulate


def _df(rows):
    idx = pd.date_range("2026-01-01", periods=len(rows), freq="5min", tz="UTC")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx)
    df["atr"] = 1.0
    return df


def _sig(n, at, d):
    s = pd.DataFrame({"signal": np.zeros(n, dtype=int), "setup": [""] * n})
    s.iloc[at, 0] = d
    s.iloc[at, 1] = "t"
    return s


def test_long_target_hit_uses_maker_fee():
    df = _df([[100, 101, 99, 100], [100, 100.5, 99.8, 100.2], [100.2, 102, 100, 101.5]])
    zero = CostModel(spread_bps=0, slippage_bps=0)
    t = simulate(df, _sig(3, 0, 1), sl_atr=1.0, tp_atr=1.5, max_bars=5, cost=zero)
    assert len(t) == 1 and t.reason[0] == "target"
    assert abs(t.gross_pct[0] - 0.015) < 1e-9
    assert abs(t.fees_pct[0] - (zero.taker_fee + zero.maker_fee)) < 1e-12


def test_stop_before_target_when_both_touched():
    df = _df([[100, 101, 99, 100], [100, 103, 98, 100]])
    t = simulate(df, _sig(2, 0, 1), 1.0, 1.5, 5, CostModel(spread_bps=0, slippage_bps=0))
    assert t.reason[0] == "stop" and t.r[0] < -0.99


def test_short_time_stop():
    df = _df([[100, 100.2, 99.8, 100]] * 5)
    t = simulate(df, _sig(5, 0, -1), 1.0, 1.5, 2, CostModel(spread_bps=0, slippage_bps=0))
    assert t.reason[0] == "time" and t.bars_held[0] == 2


def test_veto_blocks_trade():
    df = _df([[100, 101, 99, 100], [100, 102, 99.9, 101.6]])
    t = simulate(df, _sig(2, 0, 1), 1.0, 1.5, 5, CostModel(), decide=lambda i, d, s: (False, {}))
    assert t.empty
