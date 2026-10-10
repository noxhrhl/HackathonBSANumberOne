# La méthode en une page

**La question de Belem :** une alerte tombe sur un composant (un oracle, un émetteur, une bourse). Faut-il rester, ou payer un coût de sortie certain pour éviter une perte incertaine ?

## 1. Découvrir le graphe (30 % de la note)

On part de la position (par exemple 2 M$ de PT-sUSDe-26NOV2026) et on demande à chaque élément « de quoi dépends-tu ? », couche par couche, jusqu'à 5 niveaux :

| Étape | Question | Source | Statut |
|---|---|---|---|
| PT → SY → sUSDe | Contre quoi se rachète le PT ? | contrat du marché Pendle, `readTokens()` puis `yieldToken()` | lu on-chain |
| sUSDe → USDe → Ethena | Qu'y a-t-il dans l'enveloppe, qui émet ? | `asset()` (ERC-4626) puis table documentée | on-chain + doc |
| Ethena → Binance, OKX, Bybit… | Où Ethena se couvre-t-il ? | API de transparence d'Ethena (part par bourse) | API |
| PT → marché Morpho | Qui accepte le PT en garantie ? | API Morpho (identifiant de marché obligatoire) | API |
| marché → oracle → flux | Qui donne le prix ? | décodage du contrat (primaire, secours, délai de bascule) | lu on-chain |
| AMM Pendle | Où peut-on vendre ? | API Pendle (liquidité) + cotation réelle | API |

**Règle :** une arête n'existe que si une donnée la prouve. Chaque arête porte sa preuve (source, lien, heure). Aucune arête « supposée » dans nos graphes. Contrôles automatiques : preuve présente, pas de cycle (les boucles réflexives sont signalées à part, par exemple Ethena qui place 36 % de son collatéral sur Aave et Morpho, lesquels acceptent sUSDe en garantie), chaque nœud atteint la position, pas de lien de prêt sans marché réel.

Chaque arête dit **comment** le dommage voyage : valeur du sous-jacent, prix, liquidité de sortie, liquidations forcées, règlement, couverture, garde, créance douteuse.

## 2. Estimer l'impact (30 %)

Trois nombres, tous avec une fourchette BAS / BASE / HAUT et une source (NOTES.md) :

- **p** — probabilité qu'un composant défaille sur l'horizon (jusqu'à l'échéance du PT) : `p = 1 − exp(−λ·H/365)`, avec λ tiré de 5 ans d'exploits DeFiLlama (par exemple prêteurs : 3 exploits sur 310 protocole-années ≈ 1 %/an).
- **sev** — perte du composant s'il défaille, prise sur des incidents réels (USDC 2023 −12 %, xUSD 2025 −77 %, Mango 2022 prix ×24).
- **w** — part du dommage qui passe par l'arête, selon le mécanisme ; mesurée quand c'est possible (part d'Ethena sur Binance : 13,6 %).

**Facteur d'impact** I = produit des w sur le chemin le plus fort jusqu'à la position. **Perte si l'incident se produit ≈ sev × I.**
Monte Carlo (20 000 scénarios) : chaque composant défaille au hasard selon son p ; on obtient la perte attendue et la moyenne des 5 % pires cas (CVaR 95).

Exemple de lecture : un oracle cède ⇒ mauvaises liquidations sur Morpho ⇒ ventes forcées de PT dans l'AMM ⇒ baisse temporaire du prix du PT. Pour quelqu'un qui garde le PT jusqu'à l'échéance, c'est surtout une perte de valeur de marché, donc un I petit. Un émetteur qui cède (Ethena) ⇒ le sous-jacent perd de la valeur ⇒ I = 1.

## 3. Décider combien vendre (25 %)

Vendre sur un AMM coûte de plus en plus cher par dollar. Coût total pour vendre la fraction x : **C(x) = c·x²**, avec C(1) = 11 % (chiffre de Belem en crise). Garder le reste coûte (1 − x) × L, où L est la perte attendue (ou le pire 5 %).

**x\* = L / (2c)**, borné entre 0 et 1. Exemple : L = 6 %, c = 11 % ⇒ vendre 27 %, coût 5,2 %, contre 6 % (tout garder) et 11 % (tout vendre).

La page montre les trois stratégies côte à côte, le coût en dollars, puis la **robustesse** :
- x\* pour p de 0 à 100 % : « la recommandation tient pour p entre A % et B % » ;
- BAS / BASE / HAUT pour tous les paramètres ;
- un paramètre à la fois : lequel fait le plus bouger la décision.

Hors crise, la cotation réelle de Pendle montre que sortir est presque gratuit (≈ 0,03 % pour 1 M PT) mais **plafonné** : l'AMM refuse 5 M PT. Le coût de 11 % est donc un coût de crise.

## 4. Le livre (10 poches)

Les mêmes tirages aléatoires sont utilisés pour un même composant dans toutes les poches : si Ethena défaille dans un scénario, il défaille pour toutes les poches qui en dépendent. On voit donc la part du livre exposée à un nœud, la perte du livre, et les concentrations (Lido représente environ 47 % de la TVL d'Ethereum selon DeFiLlama).

## Limites (dites-les avant qu'on vous les dise)

- Les λ souffrent d'un biais du survivant ; c'est pourquoi la borne HAUTE utilise une limite statistique (Poisson 95 %).
- Le modèle est linéaire le long des chemins : il ne capture pas les effets de seuil (une liquidation en cascade qui s'emballe).
- Les dépendances hors chaîne (dépositaires, bourses) viennent de la documentation et de l'API d'Ethena, pas d'une preuve on-chain.
- C(x) = c·x² est une première approximation ; la cotation réelle donne la forme hors crise.
