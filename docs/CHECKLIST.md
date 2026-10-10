# Critères du jury → ce qui y répond

| Critère (poids) | Ce que le jury doit voir | Où | Statut |
|---|---|---|---|
| **Découverte des dépendances : profondeur et justesse (30 %)** | Graphe découvert automatiquement à partir de la seule adresse | `discover.py`, page section 01 | ✅ |
| | Profondeur 5, comme l'exemple de Belem (chaîne, émetteur, oracle, AMM, prêt) | PT-sUSDe : 25 dépendances, 34 arêtes | ✅ |
| | Justesse : chaque arête prouvée (29 % lues on-chain, 41 % API, 29 % documentées, 0 % supposées) | ligne de couverture + infobulles | ✅ |
| | Contrôles automatiques : preuve, pas de cycle, tout nœud atteint la position, prêt seulement si marché réel | `schema.validate`, `tests/test_schema.py` | ✅ |
| | Qualité des oracles décodée on-chain (décote linéaire, méta-oracle 16 h, USDe évalué comme USDT sur Aave) | `discover.resolve_oracle`, `rules.oracle_adjust` | ✅ |
| | Boucles réflexives signalées (Ethena ↔ Aave, spirale de liquidation Morpho ↔ AMM) | section 01, « Boucles » | ✅ |
| | Généralité : 10 positions réelles et différentes (Pendle, Aave, Morpho, détention) | liste « Position » | ✅ |
| **Estimation d'impact : le nombre est-il défendable (30 %)** | λ calculés sur 5 ans d'exploits DeFiLlama, bornes de Poisson | `calibrate.py`, `data/calibration/hacks_rates.json` | ✅ |
| | Sévérités et poids issus de 14 incidents réels avec liens | `data/evidence.json`, `NOTES.md` | ✅ |
| | Poids mesurés quand possible (part d'Ethena par bourse, part des collatéraux Aave) | `rules.edge_weight` | ✅ |
| | Fourchettes BAS / BASE / HAUT partout ; Monte Carlo 20 000 scénarios | `rules.py`, `model.py` | ✅ |
| | Deux modes de propagation (chemin le plus fort / somme plafonnée) | formulaire « Propagation » | ✅ |
| **Utilité pour la décision : rester ou sortir, et pourquoi (25 %)** | Phrase de décision lisible à 3 h du matin, avec le pourquoi | boîte « La réponse » | ✅ |
| | Vente partielle optimale x\* = L/2c, trois stratégies côte à côte, économie en $ | section 02 | ✅ |
| | Vue neutre (perte attendue) et prudente (pire 5 %) | formulaire « Vue du risque » | ✅ |
| | Robustesse : plage de p où la recommandation tient, scénarios, entrée la plus sensible | section 03 | ✅ |
| | Coût de sortie réel (cotations Pendle) vs coût de crise (11 %) | section 02, « hors crise » | ✅ |
| | Livre 10 × 10 % : part exposée, perte corrélée du livre, concentrations TVL | section 04 | ✅ |
| **Démo sur données réelles (15 %)** | Données réelles en cache, démo hors ligne, badge de date | `data/cache/`, en-tête | ✅ |
| | Une commande par position, test de fumée de toutes les pages | `pipeline.py`, `tests/test_app.py` (41 tests OK) | ✅ |

## Limites (à dire avant qu'on les demande)

- Biais du survivant dans les λ, d'où la borne HAUTE de Poisson.
- Propagation linéaire : pas d'effet de seuil (la spirale de liquidation est signalée mais pas simulée).
- Dépendances hors chaîne (bourses, dépositaires) : API Ethena et documentation, pas de preuve on-chain.
- C(x) = c·x² est une approximation en crise ; hors crise, on utilise les vraies cotations.
- Tailles des poches illustratives (10 × 2 M$) ; positions et marchés réels.

## Prochaines étapes

Surveillance continue (relance du pipeline et alerte sur variation), vérification on-chain des rôles admin et timelocks, autres chaînes (Arbitrum, Solana), modèle de liquidité en crise à partir de l'historique des pools, « valeur d'attendre » avec le délai de 16 h du méta-oracle.
