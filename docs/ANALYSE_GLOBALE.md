# Analyse globale des risques

## Lancer le site

À la racine du dépôt :

```bash
python app_global.py
# ou, selon votre installation : python3 app_global.py
```

Ouvrir `http://127.0.0.1:8000`. Si l'ancien serveur occupe le port, l'arrêter ou lancer `python3 app_global.py --port 8001`. `python app.py` conserve exactement son interface et son comportement. Le nouveau point d'entrée produit la page actuelle via `app.render_page()`, lui ajoute une navigation, et expose `/global?pos=<id>` et `/api/global?pos=<id>`. Les deux pages gardent l'identifiant de position dans le lien. Le formulaire de la page globale change de position sans demander d'incident. Le bouton **Actualiser** force un nouveau relevé.

### Deux pages et un seul graphe sur l'analyse actuelle

- **Analyse actuelle** (`/`) : l'utilisateur choisit une position, un montant, puis un composant en défaillance. Le formulaire soumet le choix du composant immédiatement. Si la position et le composant sont compatibles avec le simulateur approfondi existant, la page montre son graphe de propagation et ses paramètres supplémentaires : type de choc, sévérité et durée. Sinon, elle montre le graphe de dépendances habituel. Il n'y a jamais deux graphes de dépendances ni deux sous-onglets dans cette page. La recommandation et les calculs de `app.py`, y compris l'ajustement du coût de sortie au montant choisi, restent ceux du site original.
- **Analyse globale des risques** (`/global`) : la position et le montant suffisent ; aucun composant en défaillance n'est choisi. Le montant saisi est conservé dans les liens entre les deux pages.

Dans le graphe approfondi, cliquer un autre nœud sélectionne aussi ce nœud comme défaillance de la page. Changer de composant remet à zéro une simulation précédente ; il faut relancer le stress test avec les nouveaux paramètres.

## Architecture ajoutée

| Nouveau fichier | Rôle |
| --- | --- |
| `app_global.py` | Serveur : hérite des routes du site et ajoute les deux routes globales. |
| `global_risk/current.py` | Sur la page actuelle, synchronise le choix de défaillance et garde le graphe simple ou approfondi, selon la compatibilité. |
| `global_risk/analysis.py` | Profil déterministe de tous les nœuds du graphe, regroupement, scénarios et manques. |
| `global_risk/web.py` | Page, graphe cliquable, navigation, état « données indisponibles ». |
| `tests/test_global_risk.py` | Tests sans réseau du calcul et du rendu. |

Le code existant n'est ni copié, ni modifié. L'onglet importe `app.ensure_live_data`, `app.graph_for`, `app.report_for`, `app.sim_ctx_for`, `risk_engine.model.PositionModel`, `risk_engine.model.best_path`, `risk_engine.schema.coverage` et `risk_engine.svg.render`. Le relevé est gardé en mémoire par le site pendant cinq minutes. Lorsqu'un rafraîchissement échoue, `app.ensure_live_data` vide les données de la position avant de relancer la collecte : la nouvelle route affiche **données indisponibles**, jamais un fichier `data/graphs` ou `data/reports` ancien. Les fichiers de `data/` peuvent servir de fixtures aux tests, jamais au site global en production.

## Ce qui est réutilisable, et ce qui ne l'est pas

Le graphe indique une arête `A → B` quand la défaillance de A peut toucher B. Les nœuds et arêtes portent leurs preuves et leur statut : lecture on-chain, API, documentation, ou hypothèse. Le chemin affiché est celui avec le plus grand produit de poids de transmission jusqu'à la position. Les cotations de sortie du rapport et le contexte oracle du simulateur sont réutilisés séparément.

`PositionModel.losses(None)` **n'est pas** un calcul fiable du risque total : il traite chaque nœud comme une défaillance indépendante, additionne ses pertes simulées, puis plafonne à 100 %. Une perte issue d'Ethena peut déjà passer par USDe puis sUSDe. Ajouter séparément les pertes « Ethena », « USDe » et « sUSDe » peut compter plusieurs fois cette même cause. Les probabilités de leurs défaillances ne sont pas calibrées conjointement ; les corrélations ne sont pas connues. Le mode de propagation `sum` peut lui aussi compter plusieurs chemins d'une même cause. L'onglet utilise donc uniquement le mode `max` et **ne publie aucune probabilité globale ni perte totale**.

Le simulateur de liquidations Morpho/Pendle est un stress test conditionnel propre à un marché. Son modèle de liquidateurs, de vitesse de vente et de liquidité en crise ne constitue ni une probabilité générale pour tous les nœuds ni une perte à ajouter aux expositions de cette page. L'analyse globale ne le lance pas et ne modifie pas la recommandation « rester ou vendre ».

## Formules et lecture des chiffres

Pour un composant `n`, le moteur existant donne une sévérité `sev(n)` et un poids `w(e)` pour chaque arête. La nouvelle page calcule :

```text
I(n) = max sur les chemins n → position du produit des w(e)
Perte conditionnelle isolée(n) = sev(n) × I(n)
Montant illustratif = perte conditionnelle isolée(n) × taille illustrative de la position
```

`I(n)` et `sev(n)` ne sont **pas** des pertes observées : ils sont produits par les règles du modèle. La valeur la plus élevée parmi les pertes conditionnelles isolées sert de **repère de stress d'un seul composant**. Elle n'est pas une borne de perte totale en cas de plusieurs causes, ni une prévision. Les scénarios bas / central / haut remplacent les valeurs de sévérité et de poids par les bornes définies dans `risk_engine/rules.py`; ils montrent la sensibilité des résultats à ces hypothèses.

Le commit `montant` permet de changer la taille illustrative de la position. Sur la page globale, le montant choisi remplace la taille de référence uniquement pour convertir les pertes en dollars ; les pourcentages conditionnels ne changent pas. Sur la page actuelle, `app.py` ajuste également le coût de sortie et met à l'échelle les unités de la simulation. Les cotations relevées restent associées à leurs tailles réellement demandées : la page globale n'affiche une cotation comme valable pour le montant choisi que si sa taille correspond. Sinon, elle renvoie au calcul de sortie de l'analyse actuelle, qui interpole ou extrapole les cotations et signale ses hypothèses.

Lorsqu'une règle et une preuve permettent de montrer une probabilité indicative, le moteur utilise `p = 1 − exp(−λ × H / 365)`, où `λ` est un taux annuel de **catégorie** et `H` l'horizon en jours. Certaines catégories viennent des incidents historiques, d'autres d'un jugement documenté. Cette probabilité n'est ni la fréquence observée du contrat exact ni la probabilité d'une alerte en cours. Pour une règle explicitement supposée, ou sans preuve de découverte, la page affiche **non estimable avec fiabilité**.

Exemple de lecture : si la sévérité supposée d'un émetteur est 20 % et que le meilleur chemin jusqu'à la position transmet 50 %, la perte conditionnelle isolée est `20 % × 50 % = 10 %`. Pour une taille illustrative de 2 M$, cela affiche 200 000 $. Ce n'est pas une perte réellement observée et ne doit pas être ajouté à la ligne de l'actif émis par cet émetteur.

## Prix de vente, oracle et qualité des données

Une cotation Pendle renseigne le **prix/coût d'une vente de PT** selon sa taille. Le `price()` de l'oracle Morpho sert aux règles du marché de prêt. La page garde ces deux canaux distincts. Une décote à la vente n'est jamais injectée automatiquement dans l'oracle. La page indique la présence d'un relevé `price()` on-chain lorsque le contexte en fournit un ; elle ne convertit pas une valeur brute en prix USD sans ses décimales et la configuration du marché.

Une ligne de risque donne le composant, son chemin, le mécanisme de chaque lien, la perte conditionnelle, la probabilité indicative si défendable, des liens vers les preuves, et une limite principale. La synthèse indique le nombre de nœuds couverts, l'horizon, la taille **illustrative**, la date du relevé et une confiance **partielle** ou **faible**. « Partielle » ne veut pas dire validation statistique : cela signifie seulement que les contrôles et collecteurs actuels ne signalent pas de manque explicite. Une preuve manquante, un prix supposé, un lien supposé, une erreur du graphe ou une cotation entière indisponible abaisse cette confiance et apparaît dans la liste des manques.

Les dépendances visibles sont limitées par la profondeur et les sources de `risk_engine/discover.py`. Les positions non découvertes, clés inconnues, oracles non décodés, expositions hors chaîne et réactions collectives en crise restent potentiellement absents. **« Tous les risques » signifie ici tous les risques modélisés dans le graphe découvert, jamais tous les risques possibles dans la réalité.**

## Vérifier et relancer les données

```bash
python3 -m unittest tests.test_global_risk -v
python3 -m unittest discover -s tests
```

Pour relancer la collecte utilisée par le site : ouvrir la position puis cliquer **Actualiser** dans son onglet. Les routes en ligne appellent les API via `app.ensure_live_data` ; aucune nouvelle donnée de marché n'est écrite dans le dépôt pour l'onglet. La route `/api/global?pos=<id>` rend le profil JSON de la même collecte, utile pour vérifier les chemins, sources et manques.
