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
5. **Les 200 tours de recherche (section 5.4) aboutissent à une configuration
   positive en validation** (+0,21 R sur 84 trades, t = 1,76, profit factor 1,71)
   : pullback de tendance seul, entrée maker, stop 2 / target 3 ATR, coins où un
   ATR vaut ≥ 4× les frais. Signe robuste aux perturbations, ampleur non
   significative, période effective ~45 jours. C'est la piste à confirmer en
   paper trading, pas un système prêt.
6. **Ce qu'on a de solide** : un pipeline complet, reproductible et honnête
   (superset + cache, split temporel, ablation sans panel, calibration AUC, fills
   pessimistes, boucle de 200 hypothèses journalisées, paper trader live).

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

### 5.2 M5 (10 coins, Jev réel) — le run principal

Données : BTC/ETH 90 j, 7 coins 45 j (Binance), HYPE 17 j (Hyperliquid). Split
au 2026-09-02 (train 63 j / validation 27 j). 52 900 décisions Jev en cache,
200 itérations en 84 s (aucun appel réseau pendant la recherche).

| indicateur | valeur |
|---|---|
| itérations avec ≥ 30 trades en train | 161 / 200 |
| corrélation train/validation des t-stats | 0,68 |
| itérations avec R validation > 0 | 16 % |
| … parmi le top 20 par objectif train | 20 % |
| uplift médian du panel Jev sur validation (mêmes signaux) | **+0,08 R**, positif dans **85 %** des itérations |
| R validation médian sans panel | −0,19 |
| R validation médian, entrée limite vs marché | −0,09 vs −0,16 |

**La meilleure configuration selon le protocole (sélection sur train, #176)
échoue en validation** : train +0,13 R (n = 82), validation **−0,28 R** (n = 144,
t = −2,2, −14,8 %). C'est le résultat à retenir : rien de robuste n'a été trouvé.

Deux configurations sont positives en validation, mais elles sont choisies *en
regardant* la validation (donc in-sample de fait) et leurs t-stats sont < 1 :

| # | entrée | cible | stop/target (ATR) | bougies max | seuil panel | train n / R | validation n / R / t | trades/jour |
|---|---|---|---|---|---|---|---|---|
| 126 | limite | retour VWAP | 1,44 / 3,08 | 18 | 0,624, veto skip, conf ≥ 0,4 | 146 / +0,07 | 188 / **+0,10** / 0,98 | 7,0 |
| 200 | limite | retour VWAP | 1,11 / 2,68 | 12 | 0,724, veto skip | 35 / +0,18 | 71 / +0,17 / 0,90 | 2,6 |

Pour #126 en validation, le setup `trend_pullback` porte tout (152 trades,
+0,17 R, 47 % de gains) ; `vwap_reversion` est négatif (36 trades, −0,23 R).
Par coin : PUMP, SOL, HYPE, NEAR positifs ; ZEC, ETH, ENA négatifs. La
configuration #126 est celle qui tourne maintenant en paper trading live : c'est
le seul vrai test hors échantillon possible pour elle.

Ce que le panel Jev apporte vraiment ici : un **filtre de fréquence cohérent**.
Sur les mêmes signaux il relève le R moyen de +0,08 dans 85 % des cas, en ne
gardant que 4 à 10 % des candidats (`action == skip` veto + seuil). Ce n'est pas
de la prédiction directionnelle (AUC 0,52 sur 40 149 candidats, 10 coins), c'est
un tri qui favorise les états où la volatilité paie les frais et où le contexte
n'est pas contradictoire. Utile, pas suffisant.

Calibration 10 coins M5 (40 149 candidats) : AUC gain brut `quality` 0,521,
`cost_efficiency` 0,521, `p(take)` 0,518, `setup_valid` 0,515 ; `action` =
skip 34 % / wait 65 % / take 0,8 %. R brut moyen par bin de `quality` :
−0,13 (≤ 1) → −0,07 → −0,04 → +0,06 (2 à 2,5, n = 2 575).

Fichiers : `reports/REPORT_5m.md` (tableau top 10, ventilations, meilleure
config par protocole), `reports/equity_best_5m.png`, `reports/search_5m.png`,
`reports/best_config_5m.json` (#176), `reports/config_5m_iter126.json`,
`reports/config_5m_iter200.json`, `reports/optimize_runs/5m-jev-s42.jsonl`
(les 200 itérations complètes), `reports/calibration_jev_5m_all.json`.

### 5.4 Les 200 tours de recherche (hypothèse → backtest → garder ou jeter)

Différent de la recherche aléatoire ci-dessus : chaque tour teste **une hypothèse
nommée** sur la configuration courante, l'accepte si l'objectif TRAIN s'améliore
(avec ≥ 40 trades train), et enregistre la VALIDATION sans jamais s'en servir.
Catalogue de 192 hypothèses en 5 phases (exécution/exits, gating des setups,
filtres temps/régime, usage du panel Jev, coins/direction/robustesse), puis
ablation de chaque étape acceptée. Journal complet : `reports/research/rounds-5m-jev.jsonl`
et `reports/research/rounds-5m-jev.md` (tableau des 200 tours), trajectoire :
`reports/research/rounds-5m-jev_trajectory.png`. Temps de calcul : 36 s
(décisions Jev en cache).

Point de départ : 3 setups, entrée marché, stop 1 ATR, target 1,5 ATR, panel par
défaut → train −0,29 R (994 trades), validation −0,14 R (t = −4,0).

**25 étapes acceptées**, dans l'ordre : entrée limite (maker) ; stop élargi
1,2 → 2,0 ATR ; target 2,5 puis 3,0 ATR ; filtre « 1 ATR ≥ 1 → 4× le coût
aller-retour » ; désactivation de la réversion VWAP puis du breakout (il ne
reste que `trend_pullback`) ; pente HTF ≥ 0,4 ; tolérance de retour 0,3 ATR ;
RSI 45-55 ; exclusion des heures UTC 4-7 ; volume ≥ 1,2× la médiane ; seuil
panel 0,40 sans veto `skip` ; max 4 trades/coin/jour ; retrait de ZEC, NEAR, PUMP.

| | train (63 j) | validation (27 j) |
|---|---|---|
| trades | 54 | 84 (3,1 / jour sur 7 coins) |
| R moyen | +0,49 | **+0,21** |
| t-stat | 3,5 (in-sample) | **1,76** |
| profit factor | — | 1,71 |
| rendement (risque 0,5 %/trade) | — | +6,3 %, max DD −2,2 % |

Ce qu'il faut lire honnêtement :

- **Le signe est robuste, l'ampleur ne l'est pas.** Les 8 perturbations (stop,
  target, durée ±10-25 %, seuil ±0,05) gardent une validation entre +0,11 et
  +0,19 R ; le walk-forward donne +0,42 R (n = 60) et +0,22 R (n = 75) sur les
  deux derniers quarts. Mais t = 1,76 < 2 : ce n'est pas encore significatif, et
  54 trades train c'est peu.
- **Les deux premiers quarts sont vides** : le filtre « 1 ATR ≥ 4× coût »
  exclut BTC/ETH (seuls coins avec de l'historique avant le 15 août). La config
  n'a donc été observée que sur ~45 jours effectifs.
- **L'essentiel vient de trois idées simples** (ablations) : le filtre
  volatilité/coûts (sans lui : −0,14 R train, −0,02 R val), les exits larges
  (stop 2 / target 3 ATR), et l'entrée maker. Le retrait de coins est de
  l'ajustement fin (probablement du surapprentissage).
- **Le panel Jev y contribue +0,015 R** sur validation (avec : +0,214 ; sans :
  +0,199), à seuil 0,40 sans veto, c'est-à-dire presque transparent. Le stand-in
  hors-ligne donne le même résultat. Cohérent avec l'AUC de 0,52.

Cette configuration (`reports/config_research_5m.json`) tourne en paper trading
live depuis 10:36 UTC (le processus précédent, sur la #126, s'est arrêté vers
05:40 UTC lors d'un redémarrage raté ; l'interruption est visible dans
`reports/paper/decisions.jsonl`). C'est son premier test réellement hors
échantillon.

### 5.3 M5 baseline avec le stand-in heuristique (10 coins, 90 jours)

200 itérations, meilleur R validation −0,11 (pf 0,81). Confirme que le problème
est la couche déterministe, pas le panel.

## 6. Paper trading live

`scripts/paper.py` a tourné sur les 10 coins en M5 avec le vrai Jev de 03:53 à
05:05 UTC avec les paramètres par défaut (4 candidats, 4 refus Jev, latence
530-650 ms), puis relancé à 05:07 UTC avec la configuration #126, interrompu vers 05:40 UTC,
et relancé à 10:36 UTC avec la configuration issue des 200 tours
(`reports/config_research_5m.json` : pullback seul, entrée limite, stop 2 / target
3 ATR, 7 coins, seuil panel 0,40 sans veto). Chaque décision est
journalisée avec l'état complet envoyé, les 7 réponses, la latence et le coût
(`reports/paper/decisions.jsonl`, `trades.jsonl`, `state.json`, `errors.jsonl`).
Aucun ordre n'est envoyé. Le processus vit tant que le conteneur de session vit ;
pour un test de plusieurs jours, le lancer sur une machine persistante :

```bash
python scripts/paper.py --config reports/config_research_5m.json
```

Attendu à ~3 trades/jour sur 7 coins : il faut 4 à 6 semaines de paper trading
(≈ 100 à 130 trades) pour distinguer +0,2 R de zéro avec t ≈ 2.

## 7. Coûts Jev de cette session

| poste | décisions | coût |
|---|---|---|
| étiquetage BTC/ETH (M5 90 j + M1 21 j) | 26 311 | 1,35 $ |
| étiquetage 8 coins M5 45 j | 26 567 | 1,36 $ |
| A/B état enrichi | 1 499 | 0,08 $ |
| paper trading live + tests | ~10 | < 0,01 $ |
| **total** (66,5 M tokens d'entrée à 0,042 $/M) | **54 039** | **2,79 $** |

Garde-fou logiciel à 3,50 $ dans `JevClient` ; il reste ≈ 2,2 $ sur les 5 $.
Toutes les réponses sont en cache (`data_cache/jev_cache.sqlite`,
`data_cache/panel_jev.pkl`, non versionnés) : relancer les 200 itérations, ou
1 000, ne coûte plus rien.

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
