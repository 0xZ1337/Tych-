# Jev pour le trading : comment il marche, quoi lui donner, et le plan v2

*Rédigé le 2026-09-29 après lecture intégrale de la doc TypeSafe (concepts,
primitives, patterns, 14 cookbooks, jaggedness jev-1.13), revue des projets
communautaires « Jev + trading », et 54 000 décisions Jev mesurées sur nos
propres données (voir `reports/REPORT.md`).*

---

## 1. Ce que Jev est, mécaniquement

| propriété | fait vérifié | source |
|---|---|---|
| architecture | encodeur **bidirectionnel** Transformer, une passe sur le `state`, puis des têtes de décision parallèles (Choice / Score / Noul). Pas de boucle autorégressive. | mindstudio, getmaxim, doc « System One » |
| entraînement | **RLCD** (reinforcement learning for calibrated decisions) : le modèle est récompensé pour des probabilités calibrées, pas pour un texte plaisant | doc « AI primer » |
| calibration | propriété **de groupe** : parmi les réponses à 0,8, ~80 % sont justes. Ne dit rien d'une réponse isolée | doc « AI primer », « Confidence » |
| déterminisme | quasi total : sur 5 répétitions, la plupart des réponses ont un écart-type **exactement 0**. Batcher N questions ne change pas les réponses (chaque question est évaluée seule contre le state) | cookbook « Parallel questions » |
| coût / latence | 0,042 $/M tokens d'entrée, sortie gratuite, 70-500 ms mesurés (nous : 550-650 ms via proxy), 1 200 req/min | doc « Models », nos mesures |
| contexte | 64k tokens par requête, 32k pour state + la plus longue question ; 255 options max par Choice | doc « Models » |
| structure | `instructions` et `criteria` acceptent du JSON ; on peut mettre des données dans la question et y référer par nom en backticks | doc « Advanced: structure » |
| personnalisation | **aucun fine-tuning**, mêmes poids pour tous. On façonne les réponses par le state, les instructions, les critères, et en **combinant en code** | doc « Models » |
| faiblesses officielles | lecture littérale ; **pas d'arithmétique, pas de comptage, pas de comparaison de nombres ni de dates** ; indirection ; state long et bruité ; contenu adversarial ; pas d'invariants structurels (P(noul) ≠ 1 − P(non-noul)) | doc « Jev 1.13 jaggedness » |

La phrase qui résume la doc : **« Jev judges, code executes. »** Toutes les
implémentations trading recensées la respectent : seuils, vétos de risque et
envoi d'ordres restent en code déterministe.

## 2. Ce que Jev n'est pas, mesuré

Ce qu'on a fait en v1 : transformer des indicateurs en buckets nommés et
demander à Jev de juger le setup. Résultat sur 40 149 candidats M5, 10 coins :
AUC 0,51-0,52 sur « gain brut avant frais ». Un gradient boosting sur les mêmes
features fait 0,52. Un état enrichi des 8 dernières bougies ne change rien.

Les projets communautaires confirment : `sosopop/jev_stock` (direction J+1 en 3
classes sur 30 sessions d'OHLCV verbalisées) obtient **45 % sur 120 cas**, au
niveau du hasard ; `jarrodwatts/jev-trader` (carnet MON-USDC, « le prix montera-t-il
dans 30 s ? ») ne publie aucun PnL ; `buberlo/jev-trader` (6 jugements atomiques,
state < 400 tokens, vétos en code) est l'architecture la plus propre mais ne
publie aucun résultat ; `Gamma-Software/jev-signals-lab` écrit noir sur blanc
« does not establish predictive performance ».

Conclusion mécanique, pas idéologique : un encodeur de texte entraîné à juger
du sens ne trouve pas de signal dans une série de prix, parce que **le signal
n'est pas dans le sens des mots** que le code lui écrit. Lui demander « ce
pullback est-il textbook ? » revient à lui demander de redécouvrir un edge que
nos features n'ont pas.

## 3. Où est son plein potentiel en trading

Jev est fort exactement là où sa doc le place : **classification, détection,
scoring et routage d'un état sémantique**, vite, à coût nul, de façon
reproductible et calibrée. En trading, les états sémantiques qui bougent les
prix à l'horizon de 5 minutes existent, et ils sont textuels :

1. **Événements et annonces** : listings/delistings (Binance, Upbit, Bithumb,
   Coinbase, OKX), hacks et exploits, dépegs, décisions réglementaires, ETF,
   annonces macro, tweets de fondateurs/exchanges, buybacks, unlocks. C'est le
   terrain du « news scalping », un edge documenté mais **latence-sensible**.
2. **Le contexte de l'événement, verbalisé par le code** : « le prix a déjà bougé
   de +3,2 % en 2 minutes », « funding fortement positif », « open interest en
   hausse », « spread large », « 3 nouvelles similaires dans l'heure ». Jev ne
   calcule rien ; il lit ces phrases et juge : *déjà pricé ? nouveau ? matériel ?*
3. **Garde-fous et régime** : « annonce macro dans 10 min », « incident exchange »,
   « marché en panique » — décisions de *ne pas trader*, où une probabilité
   calibrée vaut de l'or.
4. **Extraction de features** (pattern officiel *AutoResearch feature discovery*) :
   poser 30 à 60 questions spéculatives par événement, en un appel, et entraîner
   un modèle classique (CatBoost) sur les probabilités. Dans le cookbook, 38
   questions découvertes en boucle battent le score demandé directement à Jev.
   C'est la manière prévue par TypeSafe d'exploiter Jev à fond : **beaucoup de
   questions atomiques, combinées par apprentissage en code**, pas une question
   « faut-il trader ? ».

Ce que ça donne pour ton objectif « 5 minutes par trade, beaucoup de trades » :
le flux Tree of Alpha (accessible, gratuit, testé) publie **~400 items/jour**
avec horodatage milliseconde, source, coins détectés et symboles par exchange ;
Binance et OKX exposent leurs annonces ; Hyperliquid expose funding, OI, carnet.
Une décision Jev par item coûte ~0,005 centime : **100 % du flux pour 2 centimes
par jour**.

## 4. Architecture v2 : le scalper d'événements

```
Tree of Alpha / Binance / OKX / usGov  (websocket + polling, horodaté)
        │  item brut (source, titre, texte, coins détectés)
        ▼
 dédoublonnage + rattachement au coin Hyperliquid (code)
        │
        ▼
 contexte marché verbalisé (code, < 150 tokens) :
   réaction déjà observée depuis l'item, ATR, spread, funding, OI, session,
   nombre d'items similaires récents, liquidité du coin
        │
        ▼
 UN appel Jev, ~12 questions atomiques (~600 tokens, ~80-500 ms)
        │  probabilités calibrées + confiance
        ▼
 policy engine (code) : seuils, gating par confiance, taille par catégorie
        │
        ▼
 vétos de risque (code, jamais délégués) : perte max/jour, exposition, spread,
   coin illiquide, kill switch, annonce macro imminente
        │
        ▼
 exécution Hyperliquid : entrée taker (la vitesse prime), sortie temps 3-15 min
   ou stop dur ; journal complet (state, réponses, latences, fills)
```

### 4.1 Le state (exemple réel, format JSON, tout est texte)

```json
{
  "event": {
    "source": "Binance EN",
    "source_type": "exchange_official_announcement",
    "published_seconds_ago": "14",
    "title": "Binance Will List Hyperliquid (HYPE) with Seed Tag Applied",
    "body_excerpt": "Binance will list HYPE ... trading opens at 2026-09-24 08:00 UTC ...",
    "coins_detected": ["HYPE"],
    "similar_items_last_hour": "none"
  },
  "instrument": {"coin": "HYPE", "venue": "hyperliquid_perp", "liquidity": "top_20_by_volume"},
  "market_reaction_so_far": {
    "move_since_event": "up_moderately (about one ATR)",
    "volume": "surge",
    "spread": "normal",
    "funding": "neutral",
    "open_interest_change_1h": "rising"
  },
  "context": {"session_utc": "europe", "btc_last_hour": "flat", "macro_event_within_30_min": "none"}
}
```

Règles : jamais un nombre à comparer ; les seuils sont convertis en mots par le
code ; on n'envoie que ce dont les questions ont besoin (la doc insiste : le
bruit dégrade la précision).

### 4.2 Les questions (un seul appel, fan-out)

| id | type | question | rôle |
|---|---|---|---|
| `materiality` | Score 0-4 | Importance de l'événement pour le prix de `instrument.coin` dans les 15 prochaines minutes | filtre principal |
| `direction` | Choice bullish / bearish / ambiguous | Sens attendu de la réaction pour ce coin | signe |
| `event_type` | Choice (listing, delisting, hack_exploit, partnership, token_unlock, buyback_burn, regulatory, macro, product_update, rumor, other) | catégorie | taille & règles par type |
| `is_new_information` | Noul | L'information est-elle nouvelle par rapport à `similar_items_last_hour` ? | anti-doublon sémantique |
| `already_priced` | Noul | La réaction décrite dans `market_reaction_so_far` a-t-elle déjà consommé l'essentiel du mouvement attendu ? | anti-retard |
| `source_credibility` | Score 0-3 | Fiabilité de la source pour ce type d'annonce | anti-fake |
| `is_official` | Noul | L'émetteur est-il l'entité concernée ou un exchange officiel ? | anti-fake |
| `scope_is_this_coin` | Noul | L'événement concerne-t-il spécifiquement `instrument.coin` (et non un homonyme ou un secteur) ? | anti-erreur de mapping |
| `crowding_risk` | Score 0-3 | Probabilité que le mouvement soit surexploité (mème, pump évident) | taille |
| `reversal_risk` | Score 0-3 | Risque de renversement rapide (« buy the rumor, sell the news ») | horizon |
| `do_not_trade` | Noul | Y a-t-il une raison de ne pas trader (incident exchange, annonce macro imminente, marché en panique) ? | véto |
| `horizon` | Choice (minutes, hours, days) | Horizon de la réaction principale | durée de détention |

Plus, en mode recherche, **30 à 50 questions spéculatives** (fan-out) dont les
probabilités deviennent les colonnes d'un CatBoost entraîné sur le rendement
réalisé à 5/15 minutes : c'est le cookbook AutoResearch appliqué aux news.

### 4.3 Policy (code)

```
go_long  = materiality >= 2.5 AND direction = bullish AND direction.confidence >= 0.6
           AND is_new_information >= 0.7 AND already_priced <= 0.4
           AND source_credibility >= 2 AND scope_is_this_coin >= 0.8
           AND do_not_trade <= 0.2
size     = base × f(event_type) × (1 − crowding_risk/3) × min(1, direction.confidence / 0.8)
hold     = 5 min si horizon = minutes, 15 min si hours, pas de trade si days
stop     = 1,5 ATR dur ; sortie anticipée si Jev répond `already_priced` sur un re-check à +2 min
```

Les seuils ne sont pas devinés : ils sont **ajustés sur l'historique** (section 5)
et vérifiés par la courbe de fiabilité (Brier, ECE, comme dans `buberlo/jev-trader`).

## 5. Protocole d'évaluation, avant d'écrire une ligne d'exécution

1. **Constituer l'historique** : items Tree of Alpha (le feed expose `firstPrice`,
   `impact`, `chartAt` par item, donc la réaction est déjà mesurée côté
   fournisseur), annonces Binance/OKX/Upbit, bougies 1m Hyperliquid/Binance
   pour les coins concernés. Cible : 60-90 jours, ~25 000 items.
2. **Mesurer l'edge brut sans Jev** : rendement moyen à +1, +5, +15 min après
   chaque item, par source et par type (regex grossière), après coûts taker.
   Si rien n'est positif à +5 min sur aucune catégorie, **on s'arrête là**.
   L'edge des listings Binance/Upbit sur les 5-30 minutes suivantes est documenté ;
   c'est le premier test.
3. **Étiqueter avec Jev** (~25 000 items × ~800 tokens = **0,85 $**), une fois,
   en cache. Calibration : AUC et courbe de fiabilité de `materiality`,
   `direction`, `already_priced` contre le rendement réalisé. **Critère go :
   AUC ≥ 0,60** sur `direction` × `materiality` pour les items matériels.
   En v1 nous étions à 0,52 : c'est le seuil qui dit si on continue.
4. **Boucle AutoResearch** : 5 rounds de questions proposées par Claude,
   répondues par Jev, apprises par CatBoost, avec erreur hors échantillon par
   round (exactement le cookbook). Coût : ~5 × 0,85 $.
5. **Backtest event-driven** avec latence simulée (item + 2 s d'ingestion +
   0,5 s Jev + 0,3 s d'envoi), entrée taker au premier prix disponible après
   cette latence, jamais au prix de l'item.
6. **Paper trading** sur machine persistante, 4 semaines, mêmes journaux.

## 6. Ce qui reste difficile, dit franchement

- **La latence.** Les bots de news trading pro sont dans la première seconde.
  Notre edge doit être dans la **continuation 1 à 15 minutes**, pas dans le
  premier tick. C'est mesurable à l'étape 2 ; si la dérive post-annonce est
  entièrement consommée en 10 secondes, le plan ne tient pas.
- **Les fausses nouvelles et le contenu adversarial.** La doc prévient que Jev
  ne traite pas le state comme hostile. D'où `source_credibility`, `is_official`
  en questions séparées, et une liste blanche de sources en code.
- **La calibration est de groupe.** Un `direction: bullish 0,8` n'est pas 80 %
  de trades gagnants ; il faut la courbe de fiabilité sur nos propres labels.
- **Le mapping coin.** Tree of Alpha fournit les coins détectés, mais les
  homonymes existent ; `scope_is_this_coin` et une table de correspondance
  Hyperliquid en code sont obligatoires.
- **Quantité.** ~400 items/jour, dont peut-être 5-10 % matériels et tradables :
  20 à 40 opportunités/jour multi-coins, c'est compatible avec « beaucoup de
  trades », mais l'exposition simultanée doit être plafonnée en code.

## 7. Ce qu'on abandonne et ce qu'on garde de la v1

Abandonné : le panel sur buckets d'indicateurs (mesuré sans valeur), la
recherche de paramètres sur setups techniques M5.

Gardé tel quel : client Jev avec cache et garde-fou budget, moteur d'exécution
à fills pessimistes, protocole superset + cache + split temporel + ablation,
journalisation, paper trader (adapté à des entrées événementielles).

## 8. Plan d'exécution proposé

| étape | livrable | critère go / no-go |
|---|---|---|
| 1. Collecte (2 j) | 60-90 j d'items + bougies 1m alignées | ≥ 15 000 items avec coin mappé |
| 2. Edge brut (1 j) | dérive post-item par source/type à +1/+5/+15 min après coûts | au moins une catégorie > 0 à +5 min avec t ≥ 2 |
| 3. Étiquetage Jev + calibration (1 j, < 1 $) | AUC, fiabilité, par type | AUC direction×materiality ≥ 0,60 |
| 4. AutoResearch (2 j, ~5 $) | 30-50 questions, CatBoost, erreur OOS par round | amélioration OOS vs étape 3 |
| 5. Backtest event-driven (2 j) | PnL après coûts et latence, par type | R moyen > 0, t ≥ 2, DD acceptable |
| 6. Paper trading (4 sem.) | journal live | cohérent avec le backtest |

Si l'étape 2 ou 3 échoue, on le saura pour moins de 2 $ et 4 jours, et on aura
la réponse honnête : ce n'est pas Jev qui manque, c'est l'edge.

## Sources

- TypeSafe docs : [System One](https://docs.typesafe.ai/concepts/system-one), [AI primer / RLCD](https://docs.typesafe.ai/introduction/machine-learning-primer), [State](https://docs.typesafe.ai/concepts/state), [Advanced structure](https://docs.typesafe.ai/primitives/advanced), [Confidence](https://docs.typesafe.ai/confidence), [Jev 1.13 jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13), [Models](https://docs.typesafe.ai/models), [Parallel questions](https://docs.typesafe.ai/cookbooks/parallel_questions), [AutoResearch feature discovery](https://docs.typesafe.ai/cookbooks/autoresearch_feature_discovery), [Classification using confidence](https://docs.typesafe.ai/cookbooks/classification_using_confidence), [Composite scoring](https://docs.typesafe.ai/patterns/composite-scoring), [Speculative fan-out](https://docs.typesafe.ai/patterns/fan-out), [Use-case map](https://docs.typesafe.ai/concepts/use-case-map)
- Architecture : [MindStudio](https://www.mindstudio.ai/blog/jev-system-one-model-launch), [Maxim](https://www.getmaxim.ai/articles/what-is-jev-system-one-model/), [Latent Space](https://www.latent.space/p/ainews-jev-a-system-one-model-that)
- Projets trading : [survey gist](https://gist.github.com/drillan/6916b16e8ea31a8ec36c8f59d6483150), [buberlo/jev-trader](https://github.com/buberlo/jev-trader), [jarrodwatts/jev-trader](https://github.com/jarrodwatts/jev-trader), [sosopop/jev_stock](https://github.com/sosopop/jev_stock), [Gamma-Software/jev-signals-lab](https://github.com/Gamma-Software/jev-signals-lab), [kenhuangus/jev-usecases](https://github.com/kenhuangus/jev-usecases)
- Données : Tree of Alpha `news.treeofalpha.com/api/news`, annonces Binance (`bapi/composite/v1/public/cms/article/list/query`), annonces OKX (`api/v5/support/announcements`), Hyperliquid `fundingHistory` / `metaAndAssetCtxs` — tous testés accessibles le 2026-09-29.
