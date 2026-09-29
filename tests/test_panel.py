import pandas as pd

from tych.data import hyperliquid as hl
from tych.features import compute_features
from tych.jev.mock import MockJev
from tych.jev.questions import QUESTION_IDS, panel_questions
from tych.jev.state import build_state
from tych.panel import PanelParams, combine
from tych.setups import SetupParams, generate_signals


def _synthetic():
    idx = pd.date_range("2026-01-01", periods=600, freq="5min", tz="UTC")
    import numpy as np
    rng = np.random.default_rng(0)
    close = 100 + np.cumsum(rng.normal(0, 0.1, len(idx)))
    df = pd.DataFrame({"open": close + rng.normal(0, 0.02, len(idx)), "close": close}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1) + abs(rng.normal(0, 0.05, len(idx)))
    df["low"] = df[["open", "close"]].min(axis=1) - abs(rng.normal(0, 0.05, len(idx)))
    df["volume"] = abs(rng.normal(10, 3, len(idx)))
    df["trades"] = 1
    return df


def test_state_is_semantic_and_exit_independent():
    f = compute_features(_synthetic(), "5m")
    row = f.iloc[-1]
    st = build_state(row, "BTC", "5m", 1, "vwap_reversion", spread_bps=0.5)
    assert set(st) == {"instrument", "candidate", "trend", "location", "momentum", "activity"}
    # no raw floats leak into the state: every leaf is a string
    def leaves(o):
        if isinstance(o, dict):
            for v in o.values():
                yield from leaves(v)
        else:
            yield o
    assert all(isinstance(v, str) for v in leaves(st))
    assert "planned_stop" not in st["candidate"]


def test_mock_answers_match_schema_and_combine():
    f = compute_features(_synthetic(), "5m")
    st = build_state(f.iloc[-1], "BTC", "5m", -1, "trend_pullback", spread_bps=0.5)
    q = panel_questions("short", "trend_pullback")
    assert set(q) == set(QUESTION_IDS)
    resp = MockJev().system_one(st, q)
    a = resp["answers"]
    assert 0 <= a["setup_valid"]["noul"] <= 1
    assert a["action"]["choice"] in ("take", "wait", "skip")
    assert abs(sum(a["action"]["probabilities"].values()) - 1) < 1e-6
    go, score, size = combine(a, "trend_pullback", PanelParams())
    assert 0 <= score <= 1 and isinstance(go, bool)


def test_signals_only_use_closed_bars():
    df = _synthetic()
    f_full = compute_features(df, "5m")
    f_cut = compute_features(df.iloc[:-50], "5m")
    s_full = generate_signals(f_full, SetupParams())
    s_cut = generate_signals(f_cut, SetupParams())
    # signals on the common history must not change when future bars are appended
    common = s_cut.index[-100:]
    assert (s_full.loc[common, "signal"].values == s_cut.loc[common, "signal"].values).all()
