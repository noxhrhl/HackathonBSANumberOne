# Belem Capital — risque de dépendances DeFi

Un maillon cède : combien perd la position, et faut-il rester ou sortir ?

Le site **découvre automatiquement** ce dont dépend une position (données réelles : API Pendle, Morpho, Aave, DeFiLlama, Ethena, et lectures on-chain sur Ethereum). Il comporte deux pages : **Analyse actuelle**, où l'on clique sur un bloc du graphe pour supposer sa défaillance et simuler sa propagation jusqu'à la position, sans probabilité ; **Analyse globale des risques**, pour parcourir les risques couverts par le graphe sans choisir de défaillance.

## Démarrer (aucune installation : Python 3.10+ suffit)

```bash
python app.py                                 # collecte en ligne, puis http://127.0.0.1:8000
python -m unittest discover -s tests          # vérifications du moteur et du site
```

Une connexion Internet est requise. Le site relève la position principale au démarrage, puis chaque autre position lorsqu'on la sélectionne. Il relève à nouveau une position après cinq minutes ou via « Actualiser ». Une source indisponible produit une erreur visible ; aucune ancienne réponse n'est utilisée. Pour exporter les graphes, rapports et données du simulateur des 5 positions sans ouvrir le site (fixtures des tests) : `python pipeline.py --all`. Recalcul des taux d'exploits historiques : `python calibrate.py`.

## Ce que montrent les deux pages

1. **Le panneau « Scénario »** : 5 positions et le montant. Le bloc en défaillance se choisit en cliquant dessus dans le graphe.
2. **La défaillance supposée** : le bloc choisi et ce que la page sait calculer pour lui (ou pourquoi il n'y a pas de chiffre, par exemple pour Ethereum).
3. **Le graphe découvert** : colonnes par profondeur, couleur par type, preuve et heure au survol, chaque bloc cliquable.
4. **Simulateur de défaillance** (sans probabilité) : choisir un scénario (chute de prix / dépeg, défaillance d'oracle, crise de liquidité), régler la sévérité, lancer. La page montre les nœuds touchés, chaque étape de propagation (état initial, état simulé, mécanisme, formule), la LTV et le facteur de santé, l'éligibilité à la liquidation, le coût de sortie sur l'AMM et la perte en USD. API : `/api/simulate?pos=…&node=…&type=…&severity=0.05&hours=24`.

La page actuelle affiche **un seul graphe** : elle montre le graphe approfondi et ses paramètres lorsque la défaillance choisie est compatible, sinon le graphe simple. La page globale classe les risques du graphe, leurs chemins, sources et limites ; elle ne somme pas les pertes de nœuds corrélés. La [méthode de l'analyse globale](docs/ANALYSE_GLOBALE.md) décrit ce périmètre.

## Où sont les fichiers ?

| Dossier ou fichier | Rôle |
| --- | --- |
| `app.py` | Point d'entrée unique ; sert les deux pages et leurs API. |
| `global_risk/` | Analyse globale et présentation du graphe unique de l'analyse actuelle. |
| `pipeline.py` | Découvre les dépendances et prépare le rapport et les données du simulateur d'une position. |
| `calibrate.py` | Recalcule les taux historiques à partir de DeFiLlama si l'on veut actualiser les hypothèses. |
| `risk_engine/` | Modules internes : collecte, graphe, règles, simulateur de défaillance, courbe Pendle et dessin SVG. |
| `templates/`, `static/` | HTML et CSS du site. |
| `data/positions.json` | Les 5 positions du site : adresses réelles, tailles illustratives. |
| `data/documented.json` | Relations documentées (émetteurs, dépositaires…), avec leurs sources. |
| `data/graphs/`, `data/reports/`, `data/sim/` | Exports produits par la commande `pipeline.py` et exemples de test ; le site ne les lit pas. |
| `tests/` | Vérifications des calculs, de la découverte et du rendu du site. |
| `tools/`, `data/cascade/` | Collecte des positions réelles des emprunteurs Morpho (`risk_engine/cascade.py`), réutilisées pour les ventes forcées du simulateur. |
| `NOTES.md`, `docs/` | Justification des nombres, méthode, architecture et documents de démonstration. |

Le rôle précis de chaque module et le chemin des données sont dans [l'architecture](docs/ARCHITECTURE.md). Le brief original est conservé dans `BelemCapital_EPFL-Hackathon_DeFi-Risk-Dependency-Challenge_EN_v1.pdf`. Aucun conseil d'investissement.
