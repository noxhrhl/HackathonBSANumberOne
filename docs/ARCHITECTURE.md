# Architecture du projet

Le site utilise une seule application : `app.py`. Il reconstruit en mémoire les graphes et rapports depuis les sources en ligne. `pipeline.py` exporte séparément ces résultats en JSON pour analyse ou tests. Les fichiers de `risk_engine/` contiennent les calculs ; ils ne définissent pas une seconde interface.

```text
HackathonBSANumberOne/
├── app.py                    Serveur local et assemblage de la page
├── pipeline.py               Produit les graphes et rapports à partir des données
├── calibrate.py              Recalcule, si besoin, les taux historiques d'incident
├── risk_engine/              Logique réutilisable, indépendante de la page
│   ├── discover.py           Découvre les dépendances des positions
│   ├── sources.py            Lit API et Ethereum en ligne
│   ├── keccak.py             Sélecteurs des appels aux contrats
│   ├── schema.py             Format et validation des graphes
│   ├── rules.py              Hypothèses et fourchettes de risque
│   ├── model.py              Propagation et simulations de pertes
│   ├── decide.py             Compare rester, vendre une partie ou tout vendre
│   ├── simulate.py           Simulateur de défaillance : propagation d'état, sévérité financière
│   ├── amm.py                Courbe de l'AMM Pendle V2 (MarketMathCore)
│   ├── risk.py               Petit calcul de seuil utilisé par la page
│   └── svg.py                Graphes et courbes SVG
├── templates/index.html      Structure HTML du site
├── static/style.css          Présentation visuelle du site
├── data/
│   ├── positions.json        Les 10 positions analysées et leurs tailles d'exemple
│   ├── documented.json       Relations sourcées qui ne sont pas lues automatiquement
│   ├── evidence.json         Incidents historiques cités par la page
│   ├── calibration/          Résultat daté de la calibration des taux
│   ├── graphs/               Exports du pipeline, un graphe par position
│   └── reports/              Exports des analyses et du portefeuille
├── tests/                    Vérifications du moteur et du rendu de la page
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
   rules.py + model.py + decide.py + evidence.json
                         ↓
              rapport en mémoire
                         ↓
     app.py + templates/index.html + static/style.css
                         ↓
                   page locale
```

`pipeline.py` orchestre les mêmes calculs pour produire des fichiers JSON. `app.py` relève en ligne la position principale avant d'ouvrir le site et les autres positions lorsqu'on les choisit ; chaque position est actualisée après cinq minutes ou à la demande. La page recalcule la décision quand un visiteur change l'incident ou une hypothèse. Le site n'ouvre pas les fichiers JSON exportés.

## Où intervenir

| Besoin | Fichiers |
| --- | --- |
| Ajouter une position | `data/positions.json`, puis `risk_engine/discover.py` si son type est nouveau ; vérifier le graphe produit. |
| Ajouter une source ou une relation | `risk_engine/sources.py`, `risk_engine/discover.py`, éventuellement `data/documented.json`. |
| Modifier une hypothèse de risque | `risk_engine/rules.py`, sa justification dans `NOTES.md`, puis les tests pertinents. |
| Modifier le calcul de perte ou de décision | `risk_engine/model.py`, `risk_engine/decide.py`, puis les tests. |
| Ajouter une défaillance ou un protocole au simulateur | `risk_engine/simulate.py` : une entrée dans `SCENARIOS`, un modèle dans `MODELS` et sa règle dans `classify()`, un évaluateur de nœud ou de position ; puis `tests/test_simulate.py`. |
| Changer l'apparence | `templates/index.html`, `static/style.css` et la composition de la page dans `app.py`. |

Les commandes pour démarrer et vérifier le projet sont dans le [README](../README.md). L'analyse du site porte sur des positions d'exemple ; les tailles sont illustratives, même si les adresses et marchés étudiés sont réels.
