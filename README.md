# Tych — scalping M1/M5 piloté par un panel de décisions Jev (TypeSafe)

Système de trading automatisé **paper-trade** sur Hyperliquid perps.  Le code garde le
contrôle (détection de setups, exécution, risque) et délègue à Jev, le modèle
« System One » de TypeSafe, un panel de questions typées évaluées en parallèle
(~600 ms, ~1200 tokens, ≈ 0,005 centime par décision).

```
Hyperliquid / Binance candles
        │
        ▼
 tych/features.py      features vectorisées (ATR, VWAP session, RSI, HTF trend, anatomie bougie…)
        │
        ▼
 tych/setups.py        3 setups déterministes : vwap_reversion, trend_pullback, range_breakout
        │  candidat (bar, direction, setup)
        ▼
 tych/jev/state.py     état SÉMANTIQUE (buckets nommés, jamais de calcul demandé au modèle)
 tych/jev/questions.py panel : 4 Noul + 2 Score + 1 Choice, un seul appel
 tych/jev/client.py    client HTTP TypeSafe (cache sqlite, retries 429, garde-fou budget)
 tych/jev/mock.py      stand-in heuristique hors-ligne (même schéma de réponse)
        │  answers + probabilités + confiance
        ▼
 tych/panel.py         combinaison pondérée + veto (`action == skip`) + taille par qualité
        │  go / no-go, size
        ▼
 tych/backtest/engine.py   fills conservateurs (stop avant target, gap, limit doit être traversé)
 scripts/paper.py          boucle live paper-trade (aucun ordre envoyé)
```

## Pipeline en 3 étapes

| Étape | Script | Rôle |
|---|---|---|
| 0 | `scripts/fetch_data.py`, `scripts/fetch_binance.py` | Hyperliquid ne sert que ~5000 bougies/intervalle (3,5 j en M1, 17 j en M5) ; Binance Vision fournit l'historique long pour le backtest |
| 1 | `scripts/jev_label.py` | Une décision Jev par candidat du *superset* (bornes larges), mise en cache → les itérations suivantes ne coûtent rien |
| 2 | `scripts/optimize.py` | 200 itérations de recherche aléatoire sur la couche déterministe + la combinaison du panel, sélection sur TRAIN, VALIDATION rapportée |
| 3 | `scripts/report.py`, `scripts/jev_calibration.py` | Rapport, courbes, et surtout : Jev prédit-il l'issue des trades ? (AUC, calibration) |
| live | `scripts/paper.py` | Paper trading sur bougies Hyperliquid avec le vrai Jev, spread et funding live |

## Lancer

```bash
pip install -r requirements.txt
echo "TYPESAFE_API_KEY=..." > .env          # jamais commité
python scripts/fetch_data.py --top 10
python scripts/fetch_binance.py
python scripts/jev_label.py --intervals 5m,1m --workers 6
python scripts/optimize.py --interval 5m --iters 200 --seed 42
python scripts/report.py --runs reports/optimize_runs/5m-jev-s42.jsonl --interval 5m
python scripts/jev_calibration.py --interval 5m
python scripts/paper.py --config reports/best_config_5m.json
python -m pytest -q
```

Sans clé API, `--panel mock` / `MockJev` fait tourner tout le pipeline avec le stand-in.

## Hypothèses de coût (Hyperliquid, palier de base, vérifié 2026-09-29)

taker 0,045 %, maker 0,015 %, spread mesuré par coin (0,1 à 2 bps), slippage 0,5 bp
sur chaque fill au marché.  Un aller-retour taker coûte ≈ 0,10 % : sur BTC en M5
c'est plus qu'un ATR, d'où l'importance du mode d'entrée `limit`.

Voir `reports/REPORT.md` pour les résultats et `docs/ARCHITECTURE.md` pour les choix de conception.
