# Pitch — 3 minutes : « la décision de 3 h du matin »

**0:00 – 0:20 · Le problème**
« 3 h du matin. Une alerte tombe sur un oracle. Vous avez 2 M$ de PT-sUSDe. La question n'est pas *est-ce grave*, c'est *qu'est-ce que ça fait à ma position, et est-ce que je paie 11 % pour sortir*. Aujourd'hui, ça se décide à l'instinct. Nous l'avons rendu explicite. »

**0:20 – 1:00 · On découvre tout seul ce dont dépend la position**
« On donne seulement l'adresse du PT. Le moteur lit le contrat Pendle on-chain, remonte PT → SY → sUSDe → USDe → Ethena, trouve via l'API Morpho le seul marché réel qui accepte ce PT en garantie (7,9 M$ de collatéral, deux fois la liquidité de l'AMM), et décode on-chain son oracle : un méta-oracle Steakhouse, primaire en décote linéaire Pendle, secours Chainlink USDe/USD, bascule après 16 heures. Il va jusqu'aux bourses où Ethena se couvre, avec leur part réelle : Binance 13,6 %. 25 dépendances, 5 niveaux, 34 arêtes, chacune avec sa preuve, aucune supposée. »

**1:00 – 1:40 · On chiffre l'impact, et on montre que le chemin compte**
« L'exemple de Belem : l'oracle cède, avec 30 % de chances. Facteur d'impact 0,009 : le dommage doit passer par le marché Morpho, puis par des liquidations, puis par l'AMM, et un PT se rembourse au pair à l'échéance. Perte attendue 0,22 %. Réponse : **rester, ne pas payer 11 %**.
Même alerte, mais sur Ethena : facteur d'impact 1, la valeur de rachat elle-même est touchée. Perte attendue 6,2 %, pire 5 % : 35 %. Chaque nombre vient de 5 ans d'exploits DeFiLlama et de 14 incidents réels, avec une fourchette basse et haute. »

**1:40 – 2:30 · L'innovation : combien vendre, pas seulement rester ou sortir**
« Vendre sur un AMM coûte de plus en plus cher par dollar : C(x) = c·x². Donc la bonne réponse n'est ni 0 ni 100 % : x\* = L / 2c. Ici : **vendre 28 % maintenant, garder 72 %**. Coût attendu 5,3 %, contre 6,2 % si on garde tout et 11 % si on vend tout. La page dit aussi jusqu'où la recommandation tient (de 6 % à 100 % de probabilité) et quelle hypothèse la fait bouger le plus : la sévérité de l'incident. »

**2:30 – 3:00 · Le livre, et ce qu'on a appris**
« Sur 10 poches de 10 %, une alerte Ethena touche 60 % du livre, y compris des poches qui ne mentionnent jamais Ethena : un prêt USDC sur Aave, via USDe en garantie. Nous avons aussi trouvé, lu on-chain, qu'Aave évalue USDe au prix de l'USDT, et que les 11 % de Belem sont un coût de crise : hors crise, la cotation réelle Pendle est de 0,07 % pour notre taille, mais l'AMM refuse au-delà de 5 M PT. **Exiting is expensive and certain, staying is cheap and uncertain : nous calculons le point exact entre les deux.** »
