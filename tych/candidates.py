"""Candidate enumeration for the Jev labelling stage.

The optimiser only ever searches *inside* the SUPERSET bounds, so every
signal a tighter configuration can emit is already a labelled candidate
and the 200 iterations run entirely on cached decisions.
"""
from __future__ import annotations

from dataclasses import replace

import pandas as pd

from tych.setups import SetupParams, generate_signals

SUPERSET = SetupParams(
    mr_dist_atr=1.5, mr_rsi_low=38.0, mr_rsi_high=62.0, mr_wick_min=0.2, mr_vol_z_min=0.7, mr_max_htf_slope=1.5,
    pb_min_htf_slope=0.05, pb_rsi_lo=30.0, pb_rsi_hi=70.0, pb_touch_tol_atr=0.4,
    bo_vol_z_min=1.0, bo_atr_regime_min=0.7, bo_min_body=0.35,
)

# search bounds (must stay inside SUPERSET so that candidates are a superset)
SEARCH_SPACE = {
    "mr_dist_atr": (1.5, 3.0), "mr_rsi_low": (22.0, 38.0), "mr_rsi_high": (62.0, 78.0), "mr_wick_min": (0.2, 0.5),
    "mr_vol_z_min": (0.7, 2.0), "mr_max_htf_slope": (0.4, 1.5),
    "pb_min_htf_slope": (0.05, 0.6), "pb_rsi_lo": (30.0, 45.0), "pb_rsi_hi": (55.0, 70.0), "pb_touch_tol_atr": (0.05, 0.4),
    "bo_vol_z_min": (1.0, 3.0), "bo_atr_regime_min": (0.7, 1.4), "bo_min_body": (0.35, 0.75),
    "sl_atr": (0.7, 1.6), "tp_atr": (1.0, 3.2), "max_bars": (6, 60),
}


def enumerate_candidates(f: pd.DataFrame) -> pd.DataFrame:
    """All (bar, direction, setup) triples that any in-bounds config could emit."""
    rows = []
    for name, flags in (("vwap_reversion", dict(mr_enabled=True, pb_enabled=False, bo_enabled=False)),
                        ("trend_pullback", dict(mr_enabled=False, pb_enabled=True, bo_enabled=False)),
                        ("range_breakout", dict(mr_enabled=False, pb_enabled=False, bo_enabled=True))):
        s = generate_signals(f, replace(SUPERSET, **flags))
        hit = s[s["signal"] != 0]
        for ts, r in hit.iterrows():
            rows.append((ts, int(r["signal"]), name))
    return pd.DataFrame(rows, columns=["ts", "direction", "setup"])
