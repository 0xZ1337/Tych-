"""In-memory panel cache: (coin, interval, ts, direction, setup) -> answers."""
from __future__ import annotations

import pickle
from pathlib import Path

from tych.data.hyperliquid import CACHE_DIR


def cache_file(model_tag: str) -> Path:
    return CACHE_DIR / f"panel_{model_tag}.pkl"


class PanelCache:
    def __init__(self, model_tag: str):
        self.tag = model_tag
        self.path = cache_file(model_tag)
        self.d: dict = {}
        if self.path.exists():
            with open(self.path, "rb") as fh:
                self.d = pickle.load(fh)

    @staticmethod
    def key(coin: str, interval: str, ts, direction: int, setup: str) -> tuple:
        return (coin, interval, int(ts.value // 1_000_000), int(direction), setup)

    def get(self, k):
        return self.d.get(k)

    def put(self, k, answers: dict, model: str):
        self.d[k] = {"answers": answers, "model": model}

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "wb") as fh:
            pickle.dump(self.d, fh)
        tmp.replace(self.path)

    def coverage(self, coin: str, interval: str):
        """(first, last) labelled timestamp for a coin/interval, or None."""
        ts = [k[2] for k in self.d if k[0] == coin and k[1] == interval]
        if not ts:
            return None
        import pandas as pd
        return pd.Timestamp(min(ts), unit="ms", tz="UTC"), pd.Timestamp(max(ts), unit="ms", tz="UTC")

    def __len__(self):
        return len(self.d)
