# Script de démo (3 minutes, clics exacts)

## Avant de monter sur scène

```
python app.py                                  # collecte en ligne, puis http://127.0.0.1:8000
```
Vérifier la connexion Internet avant la présentation. Attendre la fin du relevé au démarrage ; le badge indique l'heure des données relevées. Le lien « Actualiser » force un nouveau relevé.

Onglets de secours à préparer (copier les liens) :
- `http://127.0.0.1:8000/?incident=issuer:ethena&p=30`
- `http://127.0.0.1:8000/?incident=perp:binance&p=30`
- `http://127.0.0.1:8000/?incident=issuer:ethena&p=30&view=prudent`

## Le déroulé

| Temps | Clic | Ce qu'on montre | Ce qu'on dit |
|---|---|---|---|
| 0:00 | Page d'accueil (PT-sUSDe, oracle, p = 30 %) | Réponse calculée avec le relevé courant | « Exemple de Belem : nous comparons la perte estimée au coût de sortie. » |
| 0:25 | Descendre sur le graphe, survoler le nœud rouge (méta-oracle) | Info-bulle : primaire / secours, preuve Etherscan | « Chaque dépendance indique son mécanisme et sa source. » |
| 0:50 | Survoler l'arête Morpho → AMM | Collatéral et liquidité du relevé courant | « Les liquidations pourraient peser sur la liquidité de sortie ; ce n'est pas une liquidation observée. » |
| 1:10 | Liste « Composant en alerte » → **Ethena (émetteur d'USDe)** | Nouvelle réponse calculée automatiquement | « Même probabilité, autre chemin de risque : la valeur du sous-jacent est touchée. » |
| 1:40 | Section 02 | Tableau des 3 stratégies + courbe | « Le coût de sortie augmente avec la fraction vendue ; nous comparons les trois stratégies. » |
| 2:00 | Section 03 | Plage de robustesse du relevé courant | « On dit où la recommandation change. » |
| 2:20 | « Vue du risque » → **Prudente**, Recalculer | Réponse fondée sur les 5 % pires scénarios | « La vue prudente tient davantage compte des pertes extrêmes. » |
| 2:35 | Section 04 (livre) | Poches touchées dans le relevé courant | « Une alerte Ethena peut aussi toucher des poches qui ne portent pas son nom. » |
| 2:50 | Section 05, déplier « Incidents réels » | 14 incidents avec liens | « Chaque nombre a sa source. » |

## Si quelque chose casse

- Page d'erreur : vérifier la source en ligne signalée par l'erreur, puis actualiser. Le site ne montre pas d'ancien résultat en cas d'échec.
- Port occupé : `python app.py --port 8001`.
