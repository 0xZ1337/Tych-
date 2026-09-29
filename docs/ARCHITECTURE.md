# Choix de conception

## Pourquoi Jev n'est pas « la stratégie »

Jev (jev-1.13.0) renvoie des décisions typées calibrées, pas du texte. Sa doc
(`model-jaggedness/jev-1.13`) est explicite : il lit littéralement, ne compte
pas, ne compare pas de nombres, ne fait pas d'arithmétique. Il n'a jamais vu de
microstructure de marché. On l'utilise donc comme un **panel d'avis sur un état
sémantique**, jamais comme un calculateur :

* le code produit les candidats (setups déterministes, backtestables sans Jev) ;
* le code traduit chaque feature en bucket nommé (`rsi14: oversold`,
  `vs_session_vwap: far_below`, `volatility_regime: expanding`…) ;
* Jev répond à 7 questions atomiques en un appel (pattern *speculative fan-out*
  + *composite scoring* de la doc TypeSafe) ;
* le code combine, pondère, met un veto, et gère l'exécution et le risque.

L'état est **indépendant du plan de sortie** (stop/target) pour qu'une seule
décision Jev par candidat serve à toutes les configurations d'exit testées par
l'optimiseur. Le coût d'un ATR par rapport aux frais est jugé à la place.

## Panel (tych/jev/questions.py)

| id | type | question |
|---|---|---|
| setup_valid | Noul | entrée « textbook » pour ce setup / direction ? |
| regime_tradable | Noul | volume et volatilité adaptés à un scalp ? |
| htf_pressure_against | Score 0-3 | pression des UT supérieures contre le trade |
| move_exhaustion | Score 0-3 | épuisement du mouvement précédent |
| cost_efficiency | Noul | un ATR est-il large vs frais + spread ? |
| quality | Score 0-4 | qualité globale |
| action | Choice | take / wait / skip |

Les questions sont formulées positivement, avec critères alignés, sans double
négation, conformément aux recommandations TypeSafe.

## Protocole d'évaluation honnête

1. **Superset** : les candidats sont énumérés avec des bornes larges ; l'espace
   de recherche de l'optimiseur est inclus dans ces bornes, donc les 200
   itérations ne déclenchent aucun appel réseau (cache).
2. **Split temporel** 70/30 commun à tous les coins. Sélection sur TRAIN
   uniquement. VALIDATION rapportée pour chaque itération, jamais optimisée.
3. **Ablation** : chaque itération est aussi simulée *sans* panel (mêmes
   signaux) pour mesurer l'uplift réel de Jev.
4. **Calibration** : `jev_calibration.py` mesure l'AUC des probabilités Jev
   contre l'issue réelle (target avant stop) de chaque candidat.
5. **Fills pessimistes** : stop prioritaire si stop et target touchés dans la
   même bougie ; gap à l'ouverture rempli à l'ouverture ; ordre limite rempli
   seulement si le prix le traverse ; taker + demi-spread + slippage sur tout
   fill au marché.

## Ce qui reste volontairement hors du modèle

Aucun LLM dans la boucle de trading. L'orchestration est du code déterministe
(reproductible, rejouable). Un LLM a sa place hors ligne : concevoir les
questions, analyser les trades perdants, proposer des features.
