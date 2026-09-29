"""Combine the panel's typed answers into a go / no-go and a size multiplier.

This is the 'code stays in control' part of the TypeSafe pattern: the model
gives atomic, calibrated opinions; the weights, thresholds and veto rules
live here and are what the optimiser tunes.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class PanelParams:
    enabled: bool = True
    w_setup: float = 1.0
    w_regime: float = 0.8
    w_htf: float = 0.8
    w_exhaustion: float = 0.4
    w_cost: float = 0.6
    w_quality: float = 1.2
    threshold: float = 0.55          # composite score in [0,1] required to trade
    min_action_conf: float = 0.0     # confidence floor on the `action` choice
    veto_skip: bool = True           # `action == skip` vetoes regardless of score
    size_by_quality: bool = True     # scale size with quality level

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PanelParams":
        return cls(**d)


def combine(answers: dict, setup: str, p: PanelParams) -> tuple[bool, float, float]:
    """Returns (go, composite_score, size_multiplier)."""
    a = answers
    sv = float(a["setup_valid"]["noul"])
    rt = float(a["regime_tradable"]["noul"])
    ce = float(a["cost_efficiency"]["noul"])
    htf_against = float(a["htf_pressure_against"]["score"]) / 3.0          # 0..1, 1 = fights the trend
    exh = float(a["move_exhaustion"]["score"]) / 3.0
    exh_good = exh if setup == "vwap_reversion" else 1.0 - exh              # reversion wants exhaustion
    q = float(a["quality"]["score"]) / 4.0
    act = a["action"]

    w = [p.w_setup, p.w_regime, p.w_htf, p.w_exhaustion, p.w_cost, p.w_quality]
    parts = [sv, rt, 1.0 - htf_against, exh_good, ce, q]
    tot = sum(w) or 1.0
    score = sum(wi * xi for wi, xi in zip(w, parts)) / tot

    go = score >= p.threshold
    if p.veto_skip and act.get("choice") == "skip":
        go = False
    if float(act.get("confidence", 1.0)) < p.min_action_conf:
        go = False
    size = 1.0
    if p.size_by_quality:
        size = {0: 0.0, 1: 0.5, 2: 0.75, 3: 1.0, 4: 1.25}.get(int(a["quality"]["score"]), 1.0)
        if size == 0.0:
            go = False
    return go, score, size
