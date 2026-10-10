# Belem Capital — risque de dépendances DeFi

Un maillon cède. On reste, on vend une partie, ou on sort ?

Le prototype **découvre automatiquement** ce dont dépend une position (données réelles : API Pendle, Morpho, Aave, DeFiLlama, Ethena, et lectures on-chain sur Ethereum), **simule** la propagation d'un incident, puis **calcule combien vendre** en comparant un coût de sortie certain à une perte incertaine.

## Démarrer (aucune installation : Python 3.10+ suffit)

```bash
python pipeline.py --all --offline --book    # graphes + rapports depuis le cache (~15 s)
python app.py                                 # puis ouvrir http://127.0.0.1:8000
python -m unittest discover -s tests          # 41 tests (~2 min)
```

Rafraîchir les données avec Internet : `python pipeline.py --all --book` (sans `--offline`). Les réponses brutes sont enregistrées dans `data/cache/` avec leur heure de relevé ; recalcul des taux d'exploits : `python calibrate.py`.

## Ce que montre la page

1. **La réponse à 3 h du matin** : la décision, son coût comparé aux deux extrêmes, et pourquoi (chemin du dommage, facteur d'impact, preuves).
2. **Le graphe découvert** : colonnes par profondeur, couleur par type, chemin de propagation en rouge, preuve et heure au survol.
3. **Combien vendre** : C(x) = c·x², x\* = L / 2c, trois stratégies côte à côte, cotations réelles Pendle hors crise.
4. **Robustesse** : plage de probabilité où la recommandation tient, scénarios BAS / BASE / HAUT, entrée la plus sensible.
5. **Le livre** (10 poches de 10 %) : qui d'autre est touché, perte corrélée du livre, concentrations de TVL.
6. **Preuves** : paramètres de chaque composant, 14 incidents réels, toutes les données relevées.

## Fichiers

| Fichier | Rôle |
| --- | --- |
| `pipeline.py` | Une commande : découvrir → enrichir → simuler → décider → `data/graphs/` et `data/reports/` |
| `discover.py` | Découverte en largeur, un résolveur par type de nœud (Pendle, collatéral, oracle, actif, émetteur) |
| `sources.py` | Accès aux API et au RPC Ethereum, avec cache disque (`--offline`) |
| `keccak.py` | Keccak-256 en Python pur (sélecteurs des appels on-chain) |
| `schema.py` | Contrat de données (types, mécanismes, statuts) et contrôles automatiques |
| `rules.py` | Tous les nombres de risque (λ, sévérité, w) avec fourchettes ; justification dans `NOTES.md` |
| `calibrate.py` | Taux d'exploits par catégorie depuis la base DeFiLlama |
| `model.py` | Facteur d'impact et Monte Carlo (nombres aléatoires communs) |
| `decide.py` | Coût de sortie, vente partielle optimale, robustesse |
| `svg.py` | Dessin du graphe et des courbes (SVG côté serveur) |
| `app.py`, `templates/`, `static/` | La page locale |
| `risk.py` | Calcul de seuil simple de l'équipe (`assess`, utilisé dans la boîte de réponse) et premier modèle de graphe |
| `data/positions.json` | Les 10 positions (adresses réelles, tailles illustratives) |
| `data/documented.json`, `data/evidence.json` | Faits documentés et incidents, chacun avec un lien |
| `NOTES.md` | D'où vient chaque nombre |
| `docs/` | Méthode, questions du jury, pitch, script de démo, checklist des critères |

Le brief original est conservé dans `BelemCapital_EPFL-Hackathon_DeFi-Risk-Dependency-Challenge_EN_v1.pdf`. Aucun conseil d'investissement.
