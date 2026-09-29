"""The decision panel: independent typed questions asked in ONE Jev call.

Design rules taken from the TypeSafe docs:
  * each factor is its own atomic question, combined in code (composite scoring)
  * literal, positive wording; criteria aligned with instructions
  * no arithmetic asked of the model; buckets are pre-computed in `state.py`
"""
from __future__ import annotations


def panel_questions(direction: str, setup: str) -> dict:
    against = "bearish" if direction == "long" else "bullish"
    with_ = "bullish" if direction == "long" else "bearish"
    return {
        "setup_valid": {
            "type": "noul",
            "instructions": f"Based on `candidate`, `location` and `momentum`, is this a textbook {setup} {direction} entry?",
            "criteria": {
                "true": f"The location and last candle match what a disciplined trader wants before entering a {setup} {direction}.",
                "false": "A key ingredient of the setup is missing or contradicted by the state.",
            },
        },
        "regime_tradable": {
            "type": "noul",
            "instructions": "Based on `activity`, are volume and volatility suitable for a short-term scalp?",
            "criteria": {
                "true": "Volume is at least normal and volatility is normal or expanding, so the target is reachable within a few bars.",
                "false": "The market is very quiet or compressed, or so explosive that stops are likely to be hit by noise.",
            },
        },
        "htf_pressure_against": {
            "type": "score",
            "instructions": f"How strong is the higher-timeframe pressure AGAINST a {direction} trade, based on `trend`?",
            "criteria": [
                f"Higher timeframes are {with_} or flat: no pressure against the trade",
                f"One higher timeframe is mildly {against}",
                f"Higher timeframes are clearly {against}",
                f"Higher timeframes are strongly {against}: the trade fights a strong trend",
            ],
        },
        "move_exhaustion": {
            "type": "score",
            "instructions": "How exhausted is the price move that preceded this candidate, based on `momentum` and `location`?",
            "criteria": [
                "No extended move: price drifted sideways",
                "A modest move with room to continue",
                "An extended move with early signs of stalling (rejection wick, long streak)",
                "A climactic move at an extreme location with clear rejection",
            ],
        },
        "cost_efficiency": {
            "type": "noul",
            "instructions": "Based on `candidate.target_vs_costs` and `activity.spread`, is the planned target large enough relative to fees and spread?",
            "criteria": {"true": "The target is comfortable or large versus costs.", "false": "The target barely covers, or is small versus, costs."},
        },
        "quality": {
            "type": "score",
            "instructions": f"Overall, how good is this {setup} {direction} candidate as a scalp?",
            "criteria": [
                "Poor: skip",
                "Weak: only with a strong reason",
                "Acceptable: tradable at reduced size",
                "Good: tradable at normal size",
                "Excellent: a high-conviction entry",
            ],
        },
        "action": {
            "type": "choice",
            "instructions": f"What should the trader do with this {direction} candidate right now?",
            "criteria": {
                "take": "Enter at the next bar open with the planned stop and target",
                "wait": "The idea is right but the timing is early: wait for confirmation",
                "skip": "Do not trade this candidate",
            },
        },
    }


QUESTION_IDS = ["setup_valid", "regime_tradable", "htf_pressure_against", "move_exhaustion", "cost_efficiency", "quality", "action"]
