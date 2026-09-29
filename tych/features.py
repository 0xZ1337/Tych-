"""Vectorised feature engine.

Everything here is computed from *closed* bars only.  Higher-timeframe
features are shifted by one HTF bar so that a 5m bar never sees the 15m bar
it belongs to (that bar is still open at decision time).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

HTF_RULE = {"1m": "5min", "5m": "15min", "15m": "1h", "1h": "4h"}
HTF2_RULE = {"1m": "15min", "5m": "1h", "15m": "4h", "1h": "1D"}


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _rsi(close: pd.Series, n: int = 14) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0.0)
    dn = (-d).clip(lower=0.0)
    ru = up.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rd = dn.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = ru / rd.replace(0.0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50.0)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def _session_vwap(df: pd.DataFrame) -> pd.Series:
    """Daily (UTC) anchored VWAP using the typical price."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    day = df.index.floor("1D")
    pv = (tp * df["volume"]).groupby(day).cumsum()
    vv = df["volume"].groupby(day).cumsum()
    return (pv / vv.replace(0.0, np.nan)).ffill()


def _htf_trend(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Completed higher-timeframe bars only (shifted by one HTF bar)."""
    htf = df["close"].resample(rule, label="left", closed="left").last().dropna()
    ema_fast = _ema(htf, 9)
    ema_slow = _ema(htf, 21)
    atr_htf = (df["high"].resample(rule, label="left", closed="left").max()
               - df["low"].resample(rule, label="left", closed="left").min()).rolling(14).mean()
    slope = (ema_slow - ema_slow.shift(3)) / atr_htf.reindex(ema_slow.index).replace(0.0, np.nan)
    out = pd.DataFrame({"htf_close": htf, "htf_ema_fast": ema_fast, "htf_ema_slow": ema_slow, "htf_slope": slope})
    out = out.shift(1)  # only fully closed HTF bars are known at the LTF bar close
    return out.reindex(df.index, method="ffill")


def compute_features(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    f = df.copy()
    c, h, l, o, v = f["close"], f["high"], f["low"], f["open"], f["volume"]

    f["ret1"] = c.pct_change()
    f["atr"] = _atr(f, 14)
    f["atr_pct"] = f["atr"] / c
    f["atr_regime"] = f["atr_pct"] / f["atr_pct"].rolling(100, min_periods=30).mean()
    f["ema9"] = _ema(c, 9)
    f["ema21"] = _ema(c, 21)
    f["ema50"] = _ema(c, 50)
    f["ema21_slope"] = (f["ema21"] - f["ema21"].shift(5)) / f["atr"]
    f["rsi"] = _rsi(c, 14)
    f["vwap"] = _session_vwap(f)
    f["dist_vwap_atr"] = (c - f["vwap"]) / f["atr"]
    f["dist_ema21_atr"] = (c - f["ema21"]) / f["atr"]
    sma20 = c.rolling(20).mean()
    std20 = c.rolling(20).std()
    f["bb_z"] = (c - sma20) / std20.replace(0.0, np.nan)
    f["vol_med"] = v.rolling(50, min_periods=20).median()
    f["vol_z"] = v / f["vol_med"].replace(0.0, np.nan)
    rng = (h - l).replace(0.0, np.nan)
    f["range_pct"] = (h - l) / c
    f["range_atr"] = (h - l) / f["atr"]
    f["body_ratio"] = (c - o).abs() / rng
    f["upper_wick"] = (h - np.maximum(c, o)) / rng
    f["lower_wick"] = (np.minimum(c, o) - l) / rng
    f["bull"] = (c > o).astype(int)
    hi20 = h.rolling(20).max()
    lo20 = l.rolling(20).min()
    f["hi20"] = hi20
    f["lo20"] = lo20
    f["pos_in_range20"] = (c - lo20) / (hi20 - lo20).replace(0.0, np.nan)
    # consecutive same-colour candles
    sign = np.sign(c - o).replace(0, np.nan).ffill().fillna(0)
    grp = (sign != sign.shift()).cumsum()
    f["streak"] = sign.groupby(grp).cumcount() + 1
    f["streak"] = f["streak"] * sign
    # move over last 3 / 10 bars in ATR
    f["move3_atr"] = (c - c.shift(3)) / f["atr"]
    f["move10_atr"] = (c - c.shift(10)) / f["atr"]
    f["hour"] = f.index.hour
    f["session"] = pd.cut(f.index.hour, bins=[-1, 6, 12, 20, 23], labels=["asia", "europe", "us", "late"]).astype(str)

    htf = _htf_trend(f, HTF_RULE[interval])
    f["htf_slope"] = htf["htf_slope"]
    f["htf_trend"] = np.sign(htf["htf_ema_fast"] - htf["htf_ema_slow"]).fillna(0)
    htf2 = _htf_trend(f, HTF2_RULE[interval])
    f["htf2_slope"] = htf2["htf_slope"]
    f["htf2_trend"] = np.sign(htf2["htf_ema_fast"] - htf2["htf_ema_slow"]).fillna(0)
    return f


# ---------- semantic bucketing (what Jev is allowed to see) ----------

def bucket(value: float, edges: list[float], labels: list[str]) -> str:
    """labels has len(edges)+1 entries."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "unknown"
    for e, lab in zip(edges, labels):
        if value < e:
            return lab
    return labels[-1]


def trend_label(slope: float) -> str:
    return bucket(slope, [-0.6, -0.2, 0.2, 0.6], ["strong_down", "down", "flat", "up", "strong_up"])
