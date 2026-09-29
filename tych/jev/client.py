"""Minimal HTTP client for TypeSafe's System One endpoint (Jev).

    POST https://api.typesafe.ai/v1/systemone
    {"state": ..., "model": "jev-latest", "questions": {...}}

Responses are cached on disk by content hash so a backtest never pays twice
for the same (state, questions) pair and so results are reproducible.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import requests

API_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-latest")
PRICE_PER_MTOK = 0.042  # USD, input tokens only; output tokens are free


class BudgetExceeded(RuntimeError):
    pass


class JevClient:
    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL,
                 cache_path: str | Path | None = None, timeout: float = 20.0, max_cost_usd: float = 3.5):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY")
        if not self.api_key:
            raise RuntimeError("TYPESAFE_API_KEY is not set")
        self.model = model
        self.timeout = timeout
        cache_path = Path(cache_path or Path(__file__).resolve().parents[2] / "data_cache" / "jev_cache.sqlite")
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(cache_path, check_same_thread=False, timeout=60)
        self.lock = threading.Lock()
        self.max_cost_usd = max_cost_usd
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, model TEXT, response TEXT, ts REAL)")
        self.input_tokens = 0
        self.calls = 0
        self.cache_hits = 0

    @staticmethod
    def _key(state, questions, model) -> str:
        blob = json.dumps({"s": state, "q": questions, "m": model}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode()).hexdigest()

    def system_one(self, state, questions: dict, use_cache: bool = True) -> dict:
        k = self._key(state, questions, self.model)
        if use_cache:
            with self.lock:
                row = self.db.execute("SELECT response FROM cache WHERE k=?", (k,)).fetchone()
            if row:
                self.cache_hits += 1
                return json.loads(row[0])
        if self.cost_usd >= self.max_cost_usd:
            raise BudgetExceeded(f"Jev spend {self.cost_usd:.2f} USD reached the cap of {self.max_cost_usd} USD")
        body = {"state": state, "model": self.model, "questions": questions}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        delay = 1.0
        for attempt in range(6):
            r = requests.post(API_URL, json=body, headers=headers, timeout=self.timeout)
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("retry-after")
                time.sleep(float(ra) if ra else delay)
                delay = min(delay * 2, 30)
                continue
            r.raise_for_status()
            resp = r.json()
            with self.lock:
                self.calls += 1
                self.input_tokens += int(resp.get("usage", {}).get("input_tokens", 0))
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?,?)", (k, resp.get("model", self.model), json.dumps(resp), time.time()))
                self.db.commit()
            return resp
        raise RuntimeError("TypeSafe API: too many retries")

    @property
    def cost_usd(self) -> float:
        return self.input_tokens / 1e6 * PRICE_PER_MTOK

    def stats(self) -> dict:
        return {"calls": self.calls, "cache_hits": self.cache_hits, "input_tokens": self.input_tokens, "cost_usd": round(self.cost_usd, 4)}
