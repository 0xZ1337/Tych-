# Tych v0.1 — Rapport de première version

Système de scalping M1/M5 sur perps Hyperliquid, couche déterministe + panel de
décisions Jev (TypeSafe, `jev-1.13.0`), backtesté avec fills pessimistes et
frais Hyperliquid réels, puis mis en paper-trading live.

**Date** : 2026-09-29. **Données** : Binance Vision (90 j M5, 21 j M1) + Hyperliquid
(17 j M5, 3,5 j M1, spreads et funding live). **Univers** : 10 perps les plus
liquides d'Hyperliquid ce jour (BTC, ETH, ZEC, HYPE, NEAR, SOL, PUMP, XRP, ONDO, ENA).
**Budget Jev consommé** : voir section 7.

---

## 1. Verdict en cinq lignes

1. **La couche déterministe (3 setups classiques de scalping) n'a aucun edge brut
   sur cette période** : la dérive moyenne après signal est comprise entre −0,12 et
   +0,09 ATR quel que soit l'horizon, avant tout frais.
2. **Les frais dominent** : un aller-retour taker (0,09 % + spread) vaut 0,65 à 0,90 ATR
   sur BTC/ETH en M5, et 1,7 à 2,5 ATR en M1. Sur BTC/ETH en M1, un stop de 1 ATR
   coûte moins que les frais : c'est structurellement injouable en ordres marché.
3. **Jev ne prédit pas la direction sur cet état** : AUC ≈ 0,50-0,52 pour toutes
   ses réponses, contre « target avant stop » comme contre « gain brut avant frais »,
   sur 13 600 candidats M5 et 11 400 candidats M1 (BTC/ETH). Enrichir l'état avec
   les 8 dernières bougies ne change rien (A/B sur 1 500 candidats).
4. **Le panel « aide » uniquement en tradant moins** : sur M1 l'uplift médian de
   +0,30 R sur validation vient du veto de ~80 % des candidats (Jev répond
   `skip`/`wait` presque toujours), pas d'une sélection informée.
5. **Ce qu'on a de solide** : un pipeline complet, reproductible et honnête
   (superset + cache, split temporel, ablation sans panel, calibration AUC, fills
   pessimistes, paper trader live). C'est l'outil qu'il faut pour itérer sur la
   vraie question : trouver une couche déterministe avec un edge brut > coûts.

## 2. Ce qui a été construit

Voir `README.md` et `docs/ARCHITECTURE.md`. En résumé : `tych/features.py`
(features sans lookahead, test dédié), `tych/setups.py` (vwap_reversion,
trend_pullback, range_breakout, exits ATR ou retour au VWAP, filtre
volatilité/coûts), `tych/jev/*` (état sémantique, panel de 7 questions, client
HTTP avec cache sqlite et garde-fou budget, stand-in hors-ligne),
`tych/panel.py` (combinaison pondérée + veto), `tych/backtest/engine.py`
(simulateur bar-par-bar), `tych/optimize.py` (recherche aléatoire, sélection
sur train, validation rapportée, ablation), `scripts/paper.py` (paper trading
live). 9 tests unitaires.

## 3. Jev : ce que la doc dit, ce que les données confirment

La doc TypeSafe est explicite (`model-jaggedness/jev-1.13`) : lecture littérale,
pas de comptage, pas de comparaison numérique, pas de dates ; « keep the
arithmetic in code ». Le modèle est fait pour juger du *sens* d'un état, pas
pour extraire un signal d'une série de prix. L'état a donc été construit en
buckets nommés (`rsi14: oversold`, `vs_session_vwap: far_below`,
`volatility_regime: expanding`…) et le panel en 7 questions atomiques, un seul
appel, conformément aux patterns *speculative fan-out* et *composite scoring*.

Mesures réelles : latence 550-650 ms (pics à 2,6 s), 1 200-1 340 tokens d'entrée
par décision, soit **≈ 0,005 centime par décision**, sorties gratuites. Rate
limit annoncé 1 200 req/min ; 6 workers tiennent ~12 décisions/s sans 429.
Quelques erreurs 502 transitoires côté proxy (11 sur 26 000).

### 3.1 Calibration (BTC/ETH, exits canoniques stop 1 ATR / target 1,5 ATR / 24 bougies)

| | M5 | M1 |
|---|---|---|
| candidats | 13 582 | 11 418 |
| taux de target | 36,5 % | 36,8 % |
| R brut moyen (avant frais) | −0,11 | −0,17 |
| R net moyen (frais taker + spread) | −0,82 | −2,04 |
| AUC `setup_valid` (gain brut) | 0,514 | 0,516 |
| AUC `quality` (gain brut) | 0,512 | 0,502 |
| AUC `p(take)` (gain brut) | 0,510 | 0,501 |
| AUC `htf_pressure_against` inversée | 0,498 | — |
| répartition `action` | skip 52 % / wait 48 % / take 0,3 % | skip 80 % / wait 20 % / take 0,03 % |

Lecture : Jev est **cohérent et prudent** (il refuse presque tout, ce qui est
sain), mais **non informatif** sur la direction. Sa note `quality` est monotone
avec le R net (−1,02 → −0,46 quand la qualité monte) : ce n'est pas de la
prédiction, c'est qu'il note mieux les états où un ATR couvre les frais.

### 3.2 Contrôle : un modèle classique sur les mêmes features

Pour savoir si le plafond vient de Jev ou des features, régression logistique
et gradient boosting ont été entraînés sur les 32 features numériques
sous-jacentes (celles qui sont bucketisées pour Jev), split temporel 70/30,
mêmes labels (`scripts/feature_baseline.py`).

| AUC validation (gain brut) | M5 | M1 |
|---|---|---|
| logistique | 0,525 | 0,510 |
| gradient boosting | 0,520 (train 0,733) | 0,507 (train 0,746) |
| Jev `p(take)` | 0,510 | 0,501 |
| Jev `quality` | 0,512 | 0,502 |

Le top-décile du boosting sur validation a un R brut de −0,12 (M5), identique
à la moyenne. **Les features ne portent pas d'information directionnelle à ces
horizons** ; Jev est au niveau de ce plafond. Le goulot d'étranglement est
l'information donnée au panel, pas le panel.

### 3.3 A/B : état enrichi (tape des 8 dernières bougies)

1 499 candidats identiques, deux états : AUC `p(take)` 0,514 → 0,517, `quality`
0,511 → 0,514. Aucune différence significative. Plus de contexte n'aide pas.

## 4. Edge brut de la couche déterministe (recherche hors Jev)

Dérive moyenne en ATR après signal, période d'entraînement seulement, 10 coins :

| signal (M5) | n | 3 bougies | 6 bougies | 12 bougies |
|---|---|---|---|---|
| vwap_reversion | 2 580 | +0,03 | +0,02 | +0,09 |
| bb_fade (bollinger 2,5σ + RSI) | 2 318 | +0,03 | −0,00 | −0,04 |
| climax_fade | 1 295 | −0,04 | −0,01 | −0,05 |
| trend_pullback | 9 844 | −0,02 | −0,03 | −0,03 |
| range_breakout | 3 553 | −0,09 | −0,07 | −0,02 |
| momentum_cont | 2 722 | +0,01 | −0,04 | −0,02 |
| vwap_reclaim_trend | 1 398 | −0,09 | −0,12 | −0,11 |

En M1, tout est ≤ 0. Le seul signal marginalement positif est la réversion VWAP,
et son edge (+0,09 ATR à 12 bougies) est inférieur au coût aller-retour même en
maker sur BTC/ETH.

Coût aller-retour taker en ATR (médiane, M5 / M1) : BTC 0,90 / 2,53 · ETH 0,65 / 1,67 ·
SOL 0,57 / 1,24 · XRP 0,60 / 1,04 · ONDO 0,43 / 0,87 · HYPE 0,36 / 1,40 ·
ZEC 0,31 / 0,58 · NEAR 0,30 / 0,38 · ENA 0,28 / 0,51 · PUMP 0,23 / 0,58.

## 5. Optimisation : 200 itérations

Protocole : recherche aléatoire (seed 42) sur 13 paramètres de setups, 3 d'exits,
mode d'entrée (marché / limite), cible (ATR / VWAP), filtre volatilité-coûts,
sessions, et 10 paramètres de combinaison du panel. Sélection sur TRAIN (70 %
du temps), VALIDATION (30 %) rapportée. Chaque itération est aussi simulée sans
panel (mêmes signaux). Objectif = t-stat du R moyen × min(1, n/60).

### 5.1 M1 (BTC/ETH, Jev réel, 21 jours)

- 200 itérations, seulement 32 avec ≥ 30 trades en train (Jev veto massif).
- Corrélation train/validation des t-stats : **0,04** → rien ne généralise.
- 8,5 % des itérations ont un R validation > 0 ; 20 % parmi le top 20 train.
- Uplift médian du panel sur validation : +0,30 R (75 % positif) — obtenu en
  supprimant des trades perdants, avec un R sans panel médian de −0,99.
- Mode d'entrée : limite −1,04 R médian vs marché −1,61 R.
- Détail : `reports/REPORT_1m.md`, `reports/equity_best_1m.png`, `reports/search_1m.png`.

### 5.2 M5 (10 coins, Jev réel)

_(section complétée à la fin de l'étiquetage des 8 coins restants — voir plus bas)_

### 5.3 M5 baseline avec le stand-in heuristique (10 coins, 90 jours)

200 itérations, meilleur R validation −0,11 (pf 0,81). Confirme que le problème
est la couche déterministe, pas le panel.

## 6. Paper trading live

`scripts/paper.py` tourne sur les 10 coins en M5 avec le vrai Jev depuis
03:53 UTC (session asiatique calme). Chaque décision est journalisée avec l'état
complet envoyé, les 7 réponses, la latence et le coût
(`reports/paper/decisions.jsonl`, `trades.jsonl`, `state.json`). Il sera
relancé avec la meilleure configuration M5 dès qu'elle est disponible.

## 7. Coûts Jev de cette session

_(complété en fin de session)_

## 8. Recommandations pour la v0.2

1. **Chercher l'edge avant de chercher le filtre.** Le pipeline `research_edges.py`
   mesure la dérive brute d'un signal en minutes : c'est là qu'il faut itérer
   (microstructure : imbalance du carnet, funding extrême, liquidations, ouverture
   de session, corrélation inter-coins), pas sur le panel.
2. **Maker par défaut.** Le mode limite divise le coût par ~3. Une vraie v0.2
   doit modéliser la file d'attente (fill uniquement si le prix traverse, comme
   ici) et privilégier les coins où 1 ATR ≥ 3× le coût (ZEC, NEAR, ENA, PUMP).
3. **Réserver Jev à ce qu'il sait faire** : juger des *états sémantiques* riches
   en information non numérique — news/annonces (« faut-il couper le trading
   aujourd'hui ? »), sanity-check de risque avant envoi d'ordre, classification
   de régime à partir de descriptions. Sur des buckets d'indicateurs, un modèle
   classique (logistique/gradient boosting) entraîné sur vos labels sera
   meilleur, et Jev peut le compléter comme « garde-fou » quasi gratuit.
4. **Ne pas faire tourner un LLM dans la boucle.** L'orchestrateur reste du
   code ; Claude sert hors ligne (conception de questions, analyse des pertes).
5. **Quantité ≠ scalabilité.** À −0,1 R par trade, multiplier les trades
   multiplie les pertes. Une stratégie ne « scale » qu'avec un edge net positif
   et une capacité (slippage) mesurée.
