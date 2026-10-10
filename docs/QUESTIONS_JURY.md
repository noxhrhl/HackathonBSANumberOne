# Les 12 questions les plus dures de l'équipe risque de Belem — et des réponses honnêtes

**1. « Comment savez-vous que votre graphe est complet ? »**
Nous ne le savons pas, et nous le disons : le graphe est complet *par rapport aux sources interrogées*. Chaque type de nœud a un résolveur qui pose une question précise (contre quoi se rachète le PT, qui l'accepte en garantie, qui fixe le prix, qui émet, où l'émetteur se couvre). Ce qu'aucune source ne relie n'apparaît pas : nous préférons un trou visible à une arête inventée. La ligne de couverture (nombre de dépendances, profondeur, part vérifiée) est affichée en haut du graphe.

**2. « Une arête "documentée" ne vaut pas une arête on-chain. »**
D'accord, c'est pour cela que le statut est visible sur chaque arête. On-chain : PT → SY → sUSDe → USDe, et tout le décodage de l'oracle. API : liquidité Pendle, marchés Morpho et Aave, parts d'Ethena par bourse. Documenté : émetteur d'un jeton, dépositaires d'Ethena. Prochaine étape : vérifier on-chain les rôles admin (propriétaire des contrats, timelocks).

**3. « Pourquoi Aave n'apparaît-il pas comme prêteur du PT ? »**
Parce que l'API d'Aave ne liste pas PT-sUSDe-26NOV2026 parmi ses réserves (seulement des échéances passées, gelées). Nous ne créons une arête de prêt que si un marché réel existe ; ici, le seul est le marché Morpho PT-sUSDe/USDC (identifiant 0x5613…cb05, 7,9 M$ de collatéral).

**4. « D'où vient votre 1 %/an pour un protocole de prêt ? »**
De la base d'exploits DeFiLlama : 3 exploits de 5 M$ ou plus sur 62 prêteurs (TVL ≥ 10 M$) en 5 ans, soit 3/310 ≈ 1 %. Pour les gros prêteurs, 0 sur 60 protocole-années, donc la règle de trois donne une borne haute de 5 %. Le biais du survivant pousse ces taux vers le bas : c'est pourquoi la robustesse teste la borne HAUTE.

**5. « Votre sévérité de 20 % pour un émetteur, c'est arbitraire. »**
C'est une valeur centrale entre deux cas réels : USDC (−12 % au plus bas, retour à 0,97 $ le soir même) et xUSD (−77 % en novembre 2025). La page montre la décision sous les trois valeurs (3 %, 20 %, 77 %), et le tableau « une entrée à la fois » dit si la sévérité est ce qui fait basculer la décision.

**6. « Pourquoi le chemin le plus fort et pas la somme des chemins ? »**
Le chemin le plus fort est simple à expliquer et ne compte pas deux fois le même dommage. Sa limite : il ignore qu'un dommage peut arriver par deux routes à la fois. La somme plafonnée est disponible d'un clic, la page montre les deux facteurs pour le composant choisi, et le rapport liste les nœuds où le choix change quelque chose.

**7. « Vos poids w sont-ils mesurés ? »**
Certains, oui : la part d'Ethena sur chaque bourse (API Ethena), la part de chaque collatéral dans Aave (API Aave), le rapport entre le collatéral Morpho et la liquidité de l'AMM (2,1×, d'où la borne haute pour les liquidations). Les autres viennent d'incidents : USDe/Binance 2025 montre qu'un prix faux local ne contamine pas les marchés dont l'oracle ne l'utilise pas (w « prix » bas) ; Mango montre l'inverse (w haut).

**8. « Les 11 % de glissement de l'énoncé sont-ils réalistes ? »**
Pas hors crise : la cotation réelle de Pendle donne ~0,03 % pour vendre 1 M PT et ~0,13 % pour 3 M PT. Mais l'AMM refuse 5 M PT (« MarketProportionTooHigh »). Les 11 % sont donc un coût de crise, quand la liquidité s'en va. Nous utilisons 11 % par défaut pendant un incident, et le régime « hors crise » est à un clic.

**9. « Pourquoi C(x) = c·x² ? »**
Sur un AMM, l'impact de prix par dollar grandit à peu près en proportion de la taille vendue ; le coût total (taille × impact) grandit donc comme le carré. C'est une première approximation qui donne une forme fermée, x* = L/(2c). La cotation réelle donne la vraie forme hors crise ; le code accepte des points mesurés à la place de la parabole.

**10. « Un PT se rachète au pair à l'échéance : pourquoi vendre ? »**
Exactement le bon point. Si l'incident n'attaque pas la valeur de rachat (oracle, AMM), le risque est surtout une perte de valeur de marché, temporaire, et le modèle recommande de rester. Si l'incident attaque la valeur de rachat (Ethena, sUSDe, contrat SY), garder jusqu'à l'échéance ne protège pas : le facteur d'impact vaut 1.

**11. « Et l'oracle du marché Morpho : un dépeg d'USDe déclencherait-il des liquidations ? »**
Lu on-chain : c'est un méta-oracle Steakhouse. Le primaire est une décote linéaire Pendle (6 %/an) qui ne regarde pas le marché ; le secours multiplie par le flux Chainlink USDe/USD. La bascule n'a lieu qu'après une contestation (écart > 0,75 %) et un délai de 16 h. Conséquence : pas de cascade instantanée, mais une fenêtre de 16 h pour agir avant d'éventuelles liquidations forcées.

**12. « Qu'est-ce qui manque pour la production ? »**
Une surveillance en continu (relance du pipeline toutes les N minutes et alerte sur variation), une vérification on-chain des rôles admin, d'autres chaînes (Solana, Arbitrum), une calibration des sévérités par type d'actif, et un modèle de liquidité en crise fondé sur l'historique des pools plutôt que sur un chiffre unique.
