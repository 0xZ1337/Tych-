"""Offline stand-in for Jev used when no TYPESAFE_API_KEY is available.

It answers the *same* questions from the *same* semantic state, with the same
response schema, so the rest of the system cannot tell the difference.  It is
a hand-written heuristic, not a learned model, and it must never look at
anything that is not in the state (no future data, no labels).

Its purpose is to exercise the full pipeline (state -> panel -> combination
-> execution) and to give the deterministic strategy a reproducible, cheap
"second opinion" while waiting for real Jev access.  Replace it with the real
client to measure the actual uplift.
"""
from __future__ import annotations

import math

MOCK_MODEL_NAME = "mock-jev-heuristic-0.1"

_TREND_SCORE = {"strong_down": -2, "down": -1, "flat": 0, "up": 1, "strong_up": 2, "unknown": 0}
_RSI_SCORE = {"extremely_oversold": -3, "oversold": -2, "weak": -1, "neutral": 0, "firm": 1, "overbought": 2, "extremely_overbought": 3, "unknown": 0}
_VWAP_SCORE = {"extremely_below": -3, "far_below": -2, "below": -1, "near": 0, "above": 1, "far_above": 2, "extremely_above": 3, "unknown": 0}
_MOVE3 = {"sharp_drop": -2, "drop": -1, "sideways": 0, "rise": 1, "sharp_rise": 2, "unknown": 0}
_VOL = {"very_quiet": 0, "quiet": 1, "normal": 2, "elevated": 3, "surge": 4, "climactic": 5, "unknown": 2}
_REG = {"compressed": 0, "below_normal": 1, "normal": 2, "expanding": 3, "explosive": 4, "unknown": 2}
_COST = {"target_barely_covers_costs": 0.1, "target_small_vs_costs": 0.35, "target_comfortable_vs_costs": 0.8, "target_large_vs_costs": 0.95, "unknown": 0.5}


def _sig(x: float, k: float = 1.4) -> float:
    return 1.0 / (1.0 + math.exp(-k * x))


def _clip(x: float, lo: float = 0.02, hi: float = 0.98) -> float:
    return max(lo, min(hi, x))


def _score_answer(levels: list[str], centre: float, spread: float = 0.55) -> dict:
    n = len(levels)
    w = [math.exp(-((i - centre) ** 2) / (2 * spread ** 2)) for i in range(n)]
    s = sum(w)
    probs = {str(i): w[i] / s for i in range(n)}
    score = max(range(n), key=lambda i: probs[str(i)])
    peak = max(probs.values())
    conf = _clip((n * peak - 1) / (n - 1), 0.0, 1.0)
    return {"type": "score", "score": float(score), "confidence": round(conf, 3),
            "legend": {str(i): levels[i] for i in range(n)}, "probabilities": {k: round(v, 4) for k, v in probs.items()}}


class MockJev:
    """Same call signature as JevClient.system_one."""

    def __init__(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.cache_hits = 0

    def system_one(self, state: dict, questions: dict, use_cache: bool = True) -> dict:
        self.calls += 1
        c, t, loc, mom, act = state["candidate"], state["trend"], state["location"], state["momentum"], state["activity"]
        d = 1 if c["direction"] == "long" else -1
        setup = c["setup_type"]

        htf = _TREND_SCORE[t["higher_timeframe"]] + 0.5 * _TREND_SCORE[t["highest_timeframe"]]
        loc_ext = _VWAP_SCORE[loc["vs_session_vwap"]]           # >0 above vwap
        rsi = _RSI_SCORE[loc["rsi14"]]
        m3 = _MOVE3[mom["last_3_bars"]]
        candle = mom["last_candle"]
        streak = int(mom["candle_streak"].split("_")[0])
        streak_dir = 1 if mom["candle_streak"].endswith("green") else -1
        vol = _VOL[act["volume_vs_median"]]
        reg = _REG[act["volatility_regime"]]

        # --- setup_valid: does the state look like the textbook version of the setup?
        if setup == "vwap_reversion":
            ext = -d * loc_ext                      # long wants price far below vwap
            rej = (candle == "bullish_rejection_wick") if d > 0 else (candle == "bearish_rejection_wick")
            sv = _sig(0.9 * (ext - 1.5) + (1.2 if rej else -0.6) + 0.4 * (-d * rsi - 1.5) + 0.25 * (vol - 2))
        elif setup == "trend_pullback":
            align = d * htf
            pulled = (m3 * -d) >= 0                  # last bars moved against the trend (the pullback)
            sv = _sig(0.9 * (align - 1.0) + (0.6 if pulled else -0.4) + (0.5 if loc["position_in_20bar_range"] in ("middle", "lower_part", "upper_part") else -0.4))
        else:  # range_breakout
            at_edge = loc["position_in_20bar_range"] in (("at_highs",) if d > 0 else ("at_lows",))
            strong = candle in (("strong_bullish_body",) if d > 0 else ("strong_bearish_body",))
            sv = _sig((1.0 if at_edge else -1.0) + (0.8 if strong else -0.5) + 0.35 * (vol - 2.5) + 0.3 * (reg - 2) + 0.4 * d * htf)
        # --- regime
        rt = _sig(0.9 * (vol - 1.5) + 0.8 * (reg - 1.2) - (1.5 if reg == 4 else 0.0))
        # --- higher timeframe pressure against
        against = max(0.0, -d * htf)                # 0..3
        hp = _score_answer(["none", "mild", "clear", "strong"], centre=min(3.0, against * 1.1))
        # --- exhaustion of the preceding move
        exh = 0.35 * abs(loc_ext) + 0.3 * abs(rsi) + 0.25 * min(streak, 6) / 2 + (0.8 if "rejection" in candle else 0.0) + 0.2 * abs(m3)
        me = _score_answer(["none", "modest", "extended", "climactic"], centre=min(3.0, exh * 0.9 - 0.3))
        # --- costs
        ce = _COST[c["target_vs_costs"]]
        if act.get("spread") in ("wide", "very_wide"):
            ce *= 0.6
        # --- quality: aggregate
        exh_level = me["score"]
        wants_exh = setup == "vwap_reversion"
        exh_term = (exh_level - 1.5) if wants_exh else (1.5 - exh_level)
        q = 0.9 * (sv - 0.5) * 4 + 1.2 * (rt - 0.5) * 2 - 0.9 * hp["score"] + 0.45 * exh_term + 1.0 * (ce - 0.5) * 2
        quality = _score_answer(["poor", "weak", "acceptable", "good", "excellent"], centre=_clip(2.0 + q * 0.55, 0.0, 4.0), spread=0.7)
        # --- action
        take = _sig(1.6 * (quality["score"] - 2.2))
        wait = _sig(-0.8 * abs(quality["score"] - 2.0)) * 0.5
        skip = 1 - take
        tot = take + wait + skip
        probs = {"take": take / tot, "wait": wait / tot, "skip": skip / tot}
        choice = max(probs, key=probs.get)
        conf = _clip((3 * probs[choice] - 1) / 2, 0.0, 1.0)
        self.input_tokens += 420
        return {
            "model": MOCK_MODEL_NAME,
            "answers": {
                "setup_valid": {"type": "noul", "noul": round(_clip(sv), 4)},
                "regime_tradable": {"type": "noul", "noul": round(_clip(rt), 4)},
                "htf_pressure_against": hp,
                "move_exhaustion": me,
                "cost_efficiency": {"type": "noul", "noul": round(_clip(ce), 4)},
                "quality": quality,
                "action": {"type": "choice", "choice": choice, "confidence": round(conf, 3), "probabilities": {k: round(v, 4) for k, v in probs.items()}},
            },
            "usage": {"input_tokens": 420, "output_tokens": 0},
        }

    def stats(self) -> dict:
        return {"calls": self.calls, "cache_hits": 0, "input_tokens": self.input_tokens, "cost_usd": 0.0, "model": MOCK_MODEL_NAME}


def make_decision_model(prefer_real: bool = True):
    """Real Jev when a key is present, otherwise the offline stand-in."""
    import os
    if prefer_real and os.environ.get("TYPESAFE_API_KEY"):
        from tych.jev.client import JevClient
        return JevClient()
    return MockJev()
