# Belem Capital — risque de dépendances DeFi

Un maillon cède. On reste, on vend une partie, ou on sort ?

Le prototype **découvre automatiquement** ce dont dépend une position (données réelles : API Pendle, Morpho, Aave, DeFiLlama, Ethena, et lectures on-chain sur Ethereum), **simule** la propagation d'un incident, puis **calcule combien vendre** en comparant un coût de sortie certain à une perte incertaine.

## Démarrer (aucune installation : Python 3.10+ suffit)

```bash
python pipeline.py --all --offline --book    # graphes + rapports depuis le cache (~15 s)
python app.py                                 # puis ouvrir http://127.0.0.1:8000
python -m unittest discover -s tests          # vérifications du moteur et du site
```

Rafraîchir les données avec Internet : `python pipeline.py --all --book` (sans `--offline`). Les réponses brutes sont enregistrées dans `data/cache/` avec leur heure de relevé ; recalcul des taux d'exploits : `python calibrate.py`.

## Ce que montre la page

1. **La réponse à 3 h du matin** : la décision, son coût comparé aux deux extrêmes, et pourquoi (chemin du dommage, facteur d'impact, preuves).
2. **Le graphe découvert** : colonnes par profondeur, couleur par type, chemin de propagation en rouge, preuve et heure au survol.
3. **Combien vendre** : C(x) = c·x², x\* = L / 2c, trois stratégies côte à côte, cotations réelles Pendle hors crise.
4. **Robustesse** : plage de probabilité où la recommandation tient, scénarios BAS / BASE / HAUT, entrée la plus sensible.
5. **Le livre** (10 poches de 10 %) : qui d'autre est touché, perte corrélée du livre, concentrations de TVL.
6. **Preuves** : paramètres de chaque composant, 14 incidents réels, toutes les données relevées.
7. **Simulateur de défaillance** (sans probabilité) : cliquer un nœud du graphe, choisir un scénario (chute de prix / dépeg, défaillance d'oracle, crise de liquidité), régler la sévérité, lancer. La page montre les nœuds touchés, chaque étape de propagation (état initial, état simulé, mécanisme, formule), la LTV et le facteur de santé, l'éligibilité à la liquidation, le coût de sortie sur l'AMM et la perte en USD. API : `/api/simulate?pos=…&node=…&type=…&severity=0.05&hours=24`.

## Où sont les fichiers ?

| Dossier ou fichier | Rôle |
| --- | --- |
| `app.py` | Lance le site local et compose la page. |
| `pipeline.py` | Découvre les dépendances, simule les pertes et prépare les résultats. |
| `calibrate.py` | Recalcule les taux historiques à partir de DeFiLlama si l'on veut actualiser les hypothèses. |
| `risk_engine/` | Modules internes : collecte, graphe, règles, simulations, décision et dessin SVG. |
| `templates/`, `static/` | HTML et CSS du site. |
| `data/positions.json` | Les 10 positions : adresses réelles et tailles illustratives. |
| `data/documented.json`, `data/evidence.json` | Relations et incidents documentés, avec leurs sources. |
| `data/cache/` | Réponses API et Ethereum datées, nécessaires pour la démo hors ligne. |
| `data/graphs/`, `data/reports/` | Graphes et analyses produits par le pipeline ; le site les lit directement. |
| `data/sim/` | Données de base du simulateur (état des AMM Pendle, `price()` des oracles Morpho, prix), relevées on-chain en lecture seule. |
| `tests/` | Vérifications des calculs, de la découverte et du rendu du site. |
| `tools/` | Utilitaire de collecte du cache quand le réseau est limité. |
| `NOTES.md`, `docs/` | Justification des nombres, méthode, architecture et documents de démonstration. |

Le rôle précis de chaque module et le chemin des données sont dans [l'architecture](docs/ARCHITECTURE.md). Le brief original est conservé dans `BelemCapital_EPFL-Hackathon_DeFi-Risk-Dependency-Challenge_EN_v1.pdf`. Aucun conseil d'investissement.
