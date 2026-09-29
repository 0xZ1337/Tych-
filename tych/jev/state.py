"""Build the *semantic* state that the decision model sees for one candidate.

Jev 1.13 is explicitly weak at arithmetic and numeric comparison (see
docs.typesafe.ai/model-jaggedness/jev-1.13), so every numeric feature is
converted in code to a named bucket.  Raw numbers are kept only as rounded
context fields the questions never need to compute with.
"""
from __future__ import annotations

import math

import pandas as pd

from tych.features import bucket, trend_label


def _f(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return float("nan")
    return x


def build_state(row: pd.Series, coin: str, interval: str, direction: int, setup: str,
                spread_bps: float | None = None, funding_rate: float | None = None) -> dict:
    """The state is independent of the exit plan (stop / target) on purpose:
    one Jev decision per candidate is reused by every exit configuration the
    optimiser tries.  Cost efficiency is judged on one ATR of movement."""
    d = "long" if direction > 0 else "short"
    dist = _f(row["dist_vwap_atr"])
    rsi = _f(row["rsi"])
    volz = _f(row["vol_z"])
    regime = _f(row["atr_regime"])
    streak = _f(row["streak"])
    m3, m10 = _f(row["move3_atr"]), _f(row["move10_atr"])
    pos = _f(row["pos_in_range20"])
    atr_pct = _f(row["atr_pct"])
    lw, uw, body = _f(row["lower_wick"]), _f(row["upper_wick"]), _f(row["body_ratio"])
    cost_bps = 2 * 4.5 + (spread_bps or 0.0)  # taker in + taker out + spread, in bps
    atr_bps = atr_pct * 1e4 if not math.isnan(atr_pct) else float("nan")

    candle = "neutral"
    if lw >= 0.4 and body < 0.4:
        candle = "bullish_rejection_wick"
    elif uw >= 0.4 and body < 0.4:
        candle = "bearish_rejection_wick"
    elif body >= 0.7 and row["close"] > row["open"]:
        candle = "strong_bullish_body"
    elif body >= 0.7 and row["close"] < row["open"]:
        candle = "strong_bearish_body"
    elif body < 0.2:
        candle = "doji_indecision"

    state = {
        "instrument": {"coin": coin, "market": "hyperliquid_perp", "timeframe": interval},
        "candidate": {
            "setup_type": setup,
            "direction": d,
            "plan": "stop and target are fixed multiples of ATR; a typical target is about 1.5 ATR",
            "one_atr_vs_round_trip_costs": bucket(atr_bps / cost_bps if cost_bps else float("nan"), [1.5, 3, 6],
                                                  ["atr_barely_covers_costs", "atr_small_vs_costs", "atr_comfortable_vs_costs", "atr_large_vs_costs"]),
        },
        "trend": {
            "local_ema21_slope": trend_label(_f(row["ema21_slope"])),
            "higher_timeframe": trend_label(_f(row["htf_slope"])),
            "highest_timeframe": trend_label(_f(row["htf2_slope"])),
            "price_vs_ema50": "above" if row["close"] > row["ema50"] else "below",
        },
        "location": {
            "vs_session_vwap": bucket(dist, [-3, -2, -1, 1, 2, 3],
                                      ["extremely_below", "far_below", "below", "near", "above", "far_above", "extremely_above"]),
            "position_in_20bar_range": bucket(pos, [0.1, 0.3, 0.7, 0.9], ["at_lows", "lower_part", "middle", "upper_part", "at_highs"]),
            "rsi14": bucket(rsi, [25, 35, 45, 55, 65, 75], ["extremely_oversold", "oversold", "weak", "neutral", "firm", "overbought", "extremely_overbought"]),
        },
        "momentum": {
            "last_3_bars": bucket(m3, [-2, -0.7, 0.7, 2], ["sharp_drop", "drop", "sideways", "rise", "sharp_rise"]),
            "last_10_bars": bucket(m10, [-3, -1, 1, 3], ["sharp_drop", "drop", "sideways", "rise", "sharp_rise"]),
            "candle_streak": f"{int(abs(streak)) if not math.isnan(streak) else 0}_consecutive_{'green' if streak > 0 else 'red'}",
            "last_candle": candle,
        },
        "activity": {
            "volume_vs_median": bucket(volz, [0.5, 0.9, 1.5, 2.5, 4], ["very_quiet", "quiet", "normal", "elevated", "surge", "climactic"]),
            "volatility_regime": bucket(regime, [0.6, 0.85, 1.15, 1.6], ["compressed", "below_normal", "normal", "expanding", "explosive"]),
            "session_utc": str(row["session"]),
        },
    }
    if spread_bps is not None and not math.isnan(spread_bps):
        state["activity"]["spread"] = bucket(spread_bps, [0.5, 1.5, 4], ["tight", "normal", "wide", "very_wide"])
    if funding_rate is not None and not math.isnan(funding_rate):
        state["activity"]["funding"] = bucket(funding_rate * 1e4, [-1.5, -0.3, 0.3, 1.5],
                                              ["strongly_negative", "negative", "neutral", "positive", "strongly_positive"])
    return state
