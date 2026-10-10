# Script de démo (3 minutes, clics exacts)

## Avant de monter sur scène

```
python pipeline.py --all --offline --book     # ~15 s, régénère graphes et rapports depuis le cache
python app.py                                  # puis ouvrir http://127.0.0.1:8000
```
Ouvrir l'onglet avant de parler (le premier calcul prend ~2 s, les suivants ~1 s). Sans Internet tout fonctionne : le badge en haut à droite dit « Données en cache du 2026-10-10 ».

Onglets de secours à préparer (copier les liens) :
- `http://127.0.0.1:8000/?incident=issuer:ethena&p=30`
- `http://127.0.0.1:8000/?incident=perp:binance&p=30`
- `http://127.0.0.1:8000/?incident=issuer:ethena&p=30&view=prudent`

## Le déroulé

| Temps | Clic | Ce qu'on montre | Ce qu'on dit |
|---|---|---|---|
| 0:00 | Page d'accueil (PT-sUSDe, oracle, p = 30 %) | Boîte « Rester : ne rien vendre. » | « Exemple de Belem : l'oracle cède. Pour un PT gardé jusqu'à l'échéance : perte attendue 0,22 %. On ne paie pas 11 %. » |
| 0:25 | Descendre sur le graphe, survoler le nœud rouge (méta-oracle) | Info-bulle : primaire / secours, bascule 16 h, preuve Etherscan | « Tout est lu on-chain. 25 dépendances, 0 supposée. » |
| 0:50 | Survoler l'arête Morpho → AMM | « 7,9 M$ de collatéral face à 3,8 M$ de liquidité » | « Le seul prêteur réel de ce PT. Aave ne le liste pas. » |
| 1:10 | Liste « Composant en alerte » → **Ethena (émetteur d'USDe)**, Recalculer | Boîte orange : « Vendre 28 % maintenant, garder 72 %. » | « Même probabilité, autre chemin : I = 1. Coût 5,3 % contre 6,2 % et 11 %. » |
| 1:40 | Section 02 | Tableau des 3 stratégies + courbe | « C(x) = c·x², x\* = L/2c = 6,19 % / 0,22 = 28 %. Économie : 17 388 $. » |
| 2:00 | Section 03 | « tient pour p entre 6 % et 100 % » ; entrée la plus sensible = sévérité | « On dit où la recommandation casse. » |
| 2:20 | « Vue du risque » → **Prudente**, Recalculer | « Tout vendre » | « Un gérant prudent qui regarde le pire 5 % (35 %) sort entièrement : le modèle rend ce choix explicite. » |
| 2:35 | Section 04 (livre) | 60 % du livre exposé, Aave USDC touché | « Une alerte Ethena touche aussi des poches qui ne parlent jamais d'Ethena. » |
| 2:50 | Section 05, déplier « Incidents réels » | 14 incidents avec liens | « Chaque nombre a sa source. » |

## Si quelque chose casse

- Page d'erreur : relancer `python pipeline.py --all --offline --book`, puis `python app.py`.
- Port occupé : `python app.py --port 8001`.
- Pas de Python sur la machine de démo : montrer les captures dans `docs/` (à faire avant la démo : faire une capture de chaque étape ci-dessus).
