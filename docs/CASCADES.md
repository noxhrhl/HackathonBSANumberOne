# Stress test de liquidations Morpho PT-sUSDe/USDC

Ce calcul est un **stress test conditionnel**, pas une probabilité de crise ni une prévision de pertes. Il utilise un instantané réel du marché, puis applique des hypothèses choisies par l'utilisateur. Le résultat n'entre **pas** dans la recommandation « rester ou vendre » du modèle général : l'additionner compterait potentiellement la même perte deux fois.

## 1. Marché et données observées

Marché Ethereum : `0x5613b170ded221c588b6e9637b910f49e5508680ceede207f44aa72e5c46cb05`. Prêt : USDC (6 décimales) ; garantie : PT-sUSDe-26NOV2026 (18 décimales). `data/cascade/morpho-ptsusde-usdc.json` est une **sortie de calcul régénérée en ligne à chaque lancement du site**. Les chiffres du tableau et de l'exemple ci-dessous proviennent du relevé du **10 octobre 2026 à 18:02 UTC** ; le relevé actuel peut différer.

| Élément | Observation du relevé | Origine |
| --- | ---: | --- |
| LLTV | 91,5 % | Morpho GraphQL, `marketById` |
| Emprunteurs listés | 19, dont 13 avec dette positive | Morpho GraphQL, `marketPositions`, pagination complète |
| Dette totale du marché | 6 994 139,209313 USDC | Morpho GraphQL |
| Somme des dettes des positions | 6 994 139,209307 USDC | Morpho GraphQL ; écart de 0,000006 USDC dû aux arrondis |
| Garanties | 7 982 359,346813 PT | Somme des positions = état du marché |
| Oracle Morpho | ≈ 0,992398 USDC/PT | `price()` du contrat oracle, bloc Ethereum 26 163 698 ; `currentOracle()` = primaire |
| Référence du PT sur Pendle | ≈ 0,992923 USD/PT | API de prix Pendle ; distincte du prix oracle |
| Plus grosse vente cotée sans ordre limite | 2,5 M PT | Pendle v3 PT→sUSDe, environ 2,464 M$ de sUSDe au prix USD relevé |
| Vente 3 M PT sans ordre limite | refusée | Pendle, `MarketProportionTooHigh` |

Les requêtes Morpho ne sont pas figées au bloc RPC : elles peuvent avoir été indexées à des instants légèrement différents. Les sommes de dettes et garanties sont rapprochées de l'état du marché ; ce contrôle ne prouve pas une synchronisation parfaite. Le prix oracle est lu à un bloc précis. Les cotations sont des devis, pas des transactions exécutées.

Sources officielles : [règles de liquidation Morpho](https://docs.morpho.org/learn/concepts/liquidation/), [API Morpho et positions du marché](https://docs.morpho.org/developers/api/morpho/), [vue d'ensemble API Pendle](https://docs.pendle.finance/pendle-v2-dev/Backend/ApiOverview). Le code de découverte du projet identifie le flux primaire du méta-oracle comme un **linear discount** de Pendle, qui ne lit pas directement le prix du marché PT ; la collecte vérifie en plus au même bloc `primaryOracle()`, `backupOracle()` et `currentOracle()`. Dans ce relevé, le primaire est actif. Une vente PT sur Pendle ne provoque donc pas, à elle seule, la même baisse dans l'oracle Morpho. Si le secours est actif lors d'une nouvelle collecte, le simulateur refuse le relevé jusqu'à réexamen du mécanisme du nouvel oracle.

## 2. Règles calculées

Pour chaque emprunteur :

```text
LTV = dette_USDC / (garantie_PT × prix_oracle_USDC_par_PT)
éligible si LTV > LLTV
LIF = min(1,15 ; 1 / (0,3 × LLTV + 0,7))
PT_saisis = min(garantie_PT ; USDC_remboursés × LIF / prix_oracle)
```

Ici, le LIF vaut environ **1,02617**. Une liquidation peut être partielle. Si toute la garantie est saisie et qu'une dette subsiste, cette dette est classée non couverte dans la simulation. Une position éligible qui n'est pas liquidée reste une **exposition**, jamais une perte déjà réalisée. La simulation vérifie après chaque exécution : `dette initiale = dette remboursée + dette non couverte + dette restante` et `garantie initiale = PT saisis + garantie restante` (avec tolérance d'arrondi numérique).

La courbe de vente contient les devis cumulés PT→sUSDe aux tailles 250 k, 500 k, 1 M, 1,5 M, 2 M et 2,5 M PT. Les demandes à 3 M et plus ont été refusées. Le collecteur désactive l'agrégation externe et les ordres limites (`enableAggregator=false`, `useLimitOrder=false`) : une autre route pourrait offrir davantage, mais elle n'est pas supposée immédiatement disponible dans ce scénario. Entre deux tailles, interpolation linéaire ; au-delà de 2,5 M, **aucune extrapolation**. Le produit affiché en USDC suppose que le sUSDe obtenu est échangeable au cours USD de l'API et que 1 USDC = 1 USD. Ce n'est pas une cotation de conversion sUSDe→USDC. Les frais indiqués par Pendle font déjà partie du devis de sortie ; le modèle ajoute un coût fixe hypothétique de 100 USDC par liquidation pour représenter gas et exécution.

## 3. Déroulé d'un scénario

Valeurs par défaut : choc exogène de **−5 % sur l'oracle**, **−5 % sur le prix de vente**, et **50 % de la capacité de vente cotée** disponible pendant la crise. Ces trois nombres sont **hypothétiques**. Réduire la liquidité de moitié veut dire : pour vendre `q` PT, lire la courbe normale à la taille `q / 0,5`, puis multiplier le produit par `0,5`. La capacité totale devient `2,5 M × 0,5 = 1,25 M PT`. Ce modèle est un choix de stress simple, pas une loi de fonctionnement de Pendle.

À chaque tour, le moteur :

1. Recalcule les LTV avec le prix oracle courant et trie les positions éligibles par LTV décroissante.
2. Tente au plus **deux** ventes. Pour chaque position, il cherche la plus grande liquidation partielle rentable dans une grille déterministe, dans la limite de la garantie et de la capacité de vente restante.
3. N'exécute que si `produit estimé de la vente − dette remboursée − 100 USDC ≥ 0`.
4. Met à jour dettes, garanties, ventes cumulées et capacité restante. La capacité ne se reconstitue pas entre les tours.
5. S'arrête s'il n'y a plus de position éligible, aucune liquidation rentable ou exécutable, ou après **12 tours** au maximum. Il n'y a pas de tirage aléatoire : même instantané + mêmes paramètres = même résultat.

Le mode réel laisse le prix de l'oracle inchangé après les ventes (`oracle_feedback=0`). Un mode **contrefactuel** dans l'interface peut faire suivre à l'oracle la baisse du prix marginal de vente. Il sert à explorer une autre architecture d'oracle ; il **ne décrit pas le fonctionnement observé** de ce marché.

### Exemple chiffré du relevé fourni

Le choc porte le prix oracle de **0,992398** à **0,942778 USDC/PT**. Quatre emprunteurs dépassent LLTV 91,5 %. La première position a un LTV d'environ **95,38 %** après le choc. Le liquidateur rembourse **1 148 421 USDC**, saisit **1 250 000 PT** (calcul : `1 148 421 × 1,02617 / 0,942778`) et la vente stressée est estimée à **1 170 375 USDC**. Après 100 USDC de frais supposés, l'opération laisse environ **21 854 USDC** au liquidateur. La position conserve environ **1 794 130 USDC** de dette et **2 022 384 PT** ; elle reste au-dessus du seuil. Toute la capacité cotée du scénario est épuisée. Les quatre positions restent éligibles et non liquidées davantage. La perte non couverte **réalisée par les liquidations simulées** vaut zéro dans cet exemple ; cela ne signifie pas que les prêteurs ne courent aucun risque.

Ces chiffres ne sont valables que pour cet instantané et ces hypothèses. Ils ne sont pas des liquidations historiques constatées.

## 4. Limites et validation

- Les liquidateurs sont modélisés par une règle de rentabilité simple et un ordre déterministe. Leurs capitaux, bots, MEV, choix de route, arbitrages, vitesse réelle et concurrence ne sont pas connus. Les deux ventes par tour représentent une vitesse **choisie**, sans calibration temporelle en secondes.
- Une cotation n'est pas une profondeur de carnet garantie. Les devis Pendle peuvent expirer ou échouer lors d'une crise. La vraie capacité de liquidité, la conversion en USDC, le coût de gas en crise, les contreparties sur d'autres venues et le depeg USDC/sUSDe ne sont pas couverts.
- Le taux d'intérêt et les dépôts/retraits d'emprunteurs pendant la cascade ne sont pas simulés. Les calculs utilisent des nombres flottants, et non l'arithmétique entière exacte des contrats : les cas proches du seuil exigent une validation on-chain.
- Le relevé donne un **état ponctuel**. Les données historiques adéquates pour un test rétrospectif manquent ici : il faudrait des séries synchronisées par bloc des **positions de chaque emprunteur**, des prix effectivement lus par l'oracle, des cotations ou transactions PT de tailles diverses, des liquidations Morpho effectivement exécutées, et des frais/conditions de liquidité. L'historique des seuls totaux de marché ne suffit pas à comparer les positions prédites aux liquidations observées. **Aucun backtest chiffré n'est revendiqué.**
- Les tests unitaires emploient de petits marchés **synthétiques**, clairement séparés de l'instantané observé. Ils vérifient le seuil, l'absence de déclenchement par le seul prix Pendle, la capacité épuisée, la non-rentabilité, la conservation et la reproductibilité. Un test vérifie aussi que l'instantané observé est cohérent et simulable.

## 5. Relancer la collecte et le calcul

Depuis la racine du projet, avec Internet :

```bash
python app.py
python -m unittest tests/test_cascade.py
```

Choisir la position « USDC prêté sur Morpho contre PT-sUSDe » dans le site. Le lancement de `app.py` relance déjà ce collecteur. Il demande les positions Morpho (pagination), l'état du marché, `price()` au bloc Ethereum, le prix USD du sUSDe, puis une série de devis PT→sUSDe. Si une source échoue ou si les sommes ne concordent pas à 0,1 % près, le site ne démarre pas ; aucun ancien relevé n'est utilisé pour l'affichage.

Les devis utilisent `POST /v3/sdk/1/convert`, l'endpoint recommandé par la documentation Pendle actuelle. Le modèle n'invente pas de devis de remplacement si cette route échoue : le collecteur échoue explicitement et conserve l'ancien relevé.
