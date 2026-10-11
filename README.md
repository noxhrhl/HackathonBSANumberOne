# Belem Capital — risque de dépendances DeFi

Un maillon cède : combien perd la position, et faut-il rester ou sortir ?

Le site **découvre automatiquement** ce dont dépend une position (API Pendle, Morpho, Aave, DeFiLlama, Ethena et lectures on-chain sur Ethereum), **simule la défaillance** d'un maillon avec les formules des protocoles, puis **recommande de rester, vendre une partie ou tout vendre**, en dollars.

## Démarrer (aucune installation : Python 3.10+ suffit)

```bash
python app.py                                 # collecte en ligne, puis http://127.0.0.1:8000
python -m unittest discover -s tests          # vérifications du moteur et du site
```

Une connexion Internet est requise. Le site relève la position principale au démarrage, puis chaque autre position lorsqu'on la sélectionne. Il relève à nouveau une position après cinq minutes ou via « Actualiser ». Une source indisponible produit une erreur visible ; aucune ancienne réponse n'est utilisée. Pour exporter les graphes, rapports et données du simulateur des 5 positions sans ouvrir le site (fixtures des tests) : `python pipeline.py --all`. Recalcul des taux d'exploits historiques : `python calibrate.py`.

## Ce que montrent les deux pages

**Si un bloc casse : rester ou sortir ?**

1. À gauche, choisir une des 5 positions et son montant.
2. Cliquer sur un bloc du graphe : on suppose qu'il défaille (aucune probabilité sur cette page).
3. En haut, la recommandation : 🟢 **RESTER**, 🟠 **VENDRE X %** ou 🔴 **TOUT VENDRE**, avec les montants. Elle compare la perte si l'on reste (simulée) au coût réel de vente maintenant sur le pool Pendle, pour chaque part de 0 à 100 %, et retient la moins chère (`risk_engine/exit_rule.py`). Un emprunteur qui vend une partie de sa garantie rembourse sa dette : sa position restante est re-simulée.
4. Le simulateur suit le choc nœud par nœud : prix, oracle Morpho, facteur de santé, liquidation Morpho Blue, liquidations des autres emprunteurs (positions réelles), coût de sortie sur l'AMM Pendle V2. Ce qui ne peut pas être chiffré est marqué **INCONNU** et n'entre pas dans les chiffres.
5. En bas, la courbe « coût total selon la part vendue » justifie le pourcentage recommandé.

**Analyse globale des risques** : tous les blocs du graphe, classés selon probabilité × perte si le bloc casse. Les probabilités sont des repères historiques par catégorie (`data/calibration/hacks_rates.json`) ; les pertes de causes corrélées ne sont pas additionnées. Méthode : [docs/ANALYSE_GLOBALE.md](docs/ANALYSE_GLOBALE.md).

API : `/api/simulate?pos=…&node=…&type=…&severity=0.05`, `/api/global?pos=…`.

## Où sont les fichiers ?

| Dossier ou fichier | Rôle |
| --- | --- |
| `app.py` | Point d'entrée unique ; sert les deux pages et leurs API. |
| `global_risk/` | Analyse globale et présentation du graphe unique de l'analyse actuelle. |
| `pipeline.py` | Découvre les dépendances et prépare le rapport et les données du simulateur d'une position. |
| `calibrate.py` | Recalcule les taux historiques à partir de DeFiLlama si l'on veut actualiser les hypothèses. |
| `risk_engine/` | Modules internes : collecte, graphe, règles, simulateur de défaillance (`simulate.py`), règle rester / sortir (`exit_rule.py`), courbe Pendle (`amm.py`) et dessin SVG. |
| `templates/`, `static/` | HTML et CSS du site. |
| `data/positions.json` | Les 5 positions du site : adresses réelles, tailles illustratives. |
| `data/documented.json` | Relations documentées (émetteurs, dépositaires…), avec leurs sources. |
| `data/graphs/`, `data/reports/`, `data/sim/` | Exports produits par la commande `pipeline.py` et exemples de test ; le site ne les lit pas. |
| `tests/` | Vérifications des calculs, de la découverte et du rendu du site. |
| `tools/`, `data/cascade/` | Collecte des positions réelles des emprunteurs Morpho (`risk_engine/cascade.py`), réutilisées pour les ventes forcées du simulateur. |
| `NOTES.md`, `docs/` | Justification des nombres, méthode et architecture. |

Le rôle précis de chaque module et le chemin des données sont dans [l'architecture](docs/ARCHITECTURE.md). Le brief original est conservé dans `BelemCapital_EPFL-Hackathon_DeFi-Risk-Dependency-Challenge_EN_v1.pdf`. Aucun conseil d'investissement.
