# Architecture du projet

Le site utilise une seule application et un seul point d'entrée : `app.py`. Il sert les pages « Analyse actuelle » et « Analyse globale des risques », et reconstruit en mémoire les graphes et rapports depuis les sources en ligne. `pipeline.py` exporte séparément ces résultats en JSON pour analyse ou tests. Les fichiers de `risk_engine/` contiennent les calculs ; ils ne définissent pas une seconde interface.

```text
HackathonBSANumberOne/
├── app.py                    Serveur unique, collecte en ligne et routes des deux pages
├── global_risk/              Graphe unique de l'analyse actuelle et profil global
├── pipeline.py               Produit les graphes et rapports à partir des données
├── calibrate.py              Recalcule, si besoin, les taux historiques d'incident
├── risk_engine/              Logique réutilisable, indépendante de la page
│   ├── discover.py           Découvre les dépendances des positions
│   ├── sources.py            Lit API et Ethereum en ligne
│   ├── keccak.py             Sélecteurs des appels aux contrats
│   ├── schema.py             Format et validation des graphes
│   ├── rules.py              Hypothèses et fourchettes de risque
│   ├── model.py              Facteurs d'impact et paramètres par composant (analyse globale)
│   ├── simulate.py           Simulateur de défaillance : propagation d'état, sévérité financière
│   ├── amm.py                Courbe de l'AMM Pendle V2 (MarketMathCore)
│   ├── exit_rule.py          Règle rester / vendre x % / tout vendre (SIMULATION_MODEL.md § 14)
│   ├── cascade.py            Liquidations Morpho avec les vrais emprunteurs (outil, ventes forcées)
│   └── svg.py                Dessin SVG des graphes
├── templates/index.html      Structure HTML du site
├── static/style.css          Présentation visuelle du site
├── data/
│   ├── positions.json        Les 5 positions du site et leurs tailles d'exemple
│   ├── documented.json       Relations sourcées qui ne sont pas lues automatiquement
│   ├── calibration/          Résultat daté de la calibration des taux
│   ├── cascade/              Positions réelles des emprunteurs Morpho (tools/collect_cascade.py)
│   ├── graphs/, reports/, sim/  Exports du pipeline par position (fixtures des tests)
├── tests/                    Vérifications du moteur et du rendu de la page
├── tools/collect_cascade.py  Collecte des positions des emprunteurs Morpho
├── docs/                     Explications, méthode et script de démonstration
├── README.md                 Démarrage rapide
├── NOTES.md                  Origine des paramètres et limites du modèle
└── BelemCapital_...pdf       Énoncé original
```

## Comment les fichiers travaillent ensemble

```text
positions.json + documented.json + API/RPC relevés en ligne
                         ↓
              risk_engine/discover.py
                         ↓
              graphe en mémoire
                         ↓
   simulate.py + amm.py + exit_rule.py (page actuelle) · rules.py + model.py (analyse globale)
                         ↓
              rapport et données du simulateur en mémoire
                         ↓
     app.py + global_risk/ + templates/index.html + static/style.css
                         ↓
              deux pages dans le même site
```

`pipeline.py` orchestre les mêmes calculs pour produire des fichiers JSON. `app.py` tente de relever en ligne la position principale au démarrage et les autres positions lorsqu'on les choisit ; chaque position est actualisée après cinq minutes ou à la demande. Si la collecte échoue, le serveur peut afficher « données indisponibles » sans présenter un ancien relevé. L'analyse actuelle simule la défaillance du bloc cliqué, sans probabilité ; l'analyse globale parcourt tous les nœuds du graphe découvert. Le site n'ouvre pas les fichiers JSON exportés.

## Où intervenir

| Besoin | Fichiers |
| --- | --- |
| Ajouter une position | `data/positions.json`, puis `risk_engine/discover.py` si son type est nouveau ; vérifier le graphe produit. |
| Ajouter une source ou une relation | `risk_engine/sources.py`, `risk_engine/discover.py`, éventuellement `data/documented.json`. |
| Modifier une hypothèse de risque | `risk_engine/rules.py`, sa justification dans `NOTES.md`, puis les tests pertinents. |
| Modifier les facteurs d'impact de l'analyse globale | `risk_engine/model.py`, `risk_engine/rules.py`, puis les tests. |
| Ajouter une défaillance ou un protocole au simulateur | `risk_engine/simulate.py` : une entrée dans `SCENARIOS`, un modèle dans `MODELS` et sa règle dans `classify()`, un évaluateur de nœud ou de position ; puis `tests/test_simulate.py`. |
| Changer l'apparence | `templates/index.html`, `static/style.css` et la composition de la page dans `app.py`. |
| Modifier l'analyse globale ou le graphe unique | `global_risk/analysis.py`, `global_risk/current.py`, `global_risk/web.py` et leurs tests. |

Les commandes pour démarrer et vérifier le projet sont dans le [README](../README.md). L'analyse du site porte sur des positions d'exemple ; les tailles sont illustratives, même si les adresses et marchés étudiés sont réels.
