# NOTES — d'où vient chaque nombre

Toutes les valeurs du code (`rules.py`) renvoient à une section de ce fichier. Chaque valeur a une fourchette **BAS / BASE / HAUT** ; la page montre que la recommandation tient (ou non) sur toute la fourchette.

Trois familles de paramètres :

| Symbole | Sens | D'où il vient |
|---|---|---|
| **λ** (lam) | fréquence annuelle de défaillance d'un composant en temps normal | base d'exploits DeFiLlama 2021-2025, ou incidents documentés |
| **p** | probabilité de défaillance sur l'horizon H : `p = 1 − exp(−λ·H/365)` | calculé (H = jours jusqu'à l'échéance du PT, sinon 30 j) |
| **sev** | perte de valeur du composant *s'il* défaille (0 à 1) | incidents réels |
| **w** | part du dommage qui traverse une arête (selon son mécanisme) | incidents réels, ou part mesurée dans les données |
| **incident_p** | probabilité qu'une **alerte en cours** devienne un vrai dommage | entrée du gérant (fourchettes suggérées sur la page) |

Ne pas confondre **p** (temps normal, fréquences historiques) et **incident_p** (une alerte est déjà là ; c'est le gérant qui juge).

---

## Méthode de calibration de λ (base DeFiLlama)

Source : `https://api.llama.fi/hacks` et `https://api.llama.fi/protocols`, relevés le 2026-10-10, résultat dans `data/calibration/hacks_rates.json` (recalculable avec `python calibrate.py`).

1. Exploits de **5 M$ ou plus** entre le 1er janvier 2021 et le 1er janvier 2026 (5 ans).
2. Reliés à la catégorie DeFiLlama du protocole touché.
3. Taux = exploits / (nombre de protocoles de la catégorie × 5 ans). Deux groupes : TVL ≥ 10 M$ (« tous ») et TVL ≥ 500 M$ (« gros »).
4. Borne haute à 95 % d'une loi de Poisson. Avec 0 incident, c'est la **règle de trois** : 3 / exposition.

| Catégorie | Tous : exploits / protocole-années | Taux | Gros : exploits / protocole-années | Borne haute 95 % (gros) |
|---|---|---|---|---|
| Lending | 3 / 310 | 0,97 % | 0 / 60 | 5,0 % |
| Dexs | 5 / 415 | 1,20 % | 1 / 35 | 13,6 % |
| Liquid Staking | 1 / 355 | 0,28 % | 0 / 70 | 4,3 % |
| Yield (Pendle) | 1 / 145 | 0,69 % | 0 / 15 | 20 % |
| Basis Trading (Ethena) | 0 / 95 | 0 % (borne 3,2 %) | 0 / 10 | 30 % |
| CEX | 7 / 275 | 2,5 % | 4 / 120 | 7,6 % |
| Bridge | 6 / 260 | 2,3 % | 1 / 75 | 6,3 % |

**Limite honnête :** biais du survivant (on compte les protocoles encore vivants aujourd'hui, et un protocole mort après un exploit n'est plus dans la base). Les taux « tous » sont donc plutôt des bornes basses ; c'est pourquoi la borne HAUTE utilise la limite de Poisson.

---

## Nœuds

### <a id="chain"></a>Chaîne (Ethereum) — λ 10 % / 25 % / 75 %, sev 0 / 2 % / 10 %
Ethereum en preuve d'enjeu depuis septembre 2022 (~4 ans) : un incident notable, la perte de finalité des 11-12 mai 2023 (~25 min puis plus d'une heure), sans problème de transaction pour les utilisateurs ([The Block](https://www.theblock.co/post/230723)). 1 incident / 4 ans ⇒ λ BASE 25 %. Un arrêt empêche d'agir mais ne détruit pas la valeur (Solana, 6 février 2024 : ~5 h d'arrêt, reprise ensuite — [The Block](https://www.theblock.co/post/276237/solana-network-restarts-after-outage-that-lasted-five-hours)), d'où une sévérité très faible.

### <a id="asset"></a>Actif / enveloppe (sUSDe, wstETH, sUSDS) — λ 0,1 % / 0,28 % / 4,3 %, sev 6 % / 20 % / 70 %
λ : catégorie « Liquid Staking » de DeFiLlama (1 exploit / 355 protocole-années ; gros : 0 / 70 ⇒ règle de trois 4,3 %). Sévérité BAS : la décote de stETH en juin 2022 (5-6 %, ventes forcées de Celsius / 3AC dans Curve — [Fortune](https://fortune.com/2022/06/17/steth-token-trading-pool-crypto-investors/)). HAUT 70 % : l'ordre de grandeur des effondrements d'actifs adossés (xUSD −77 %).

### <a id="issuer"></a>Émetteur (Ethena, Lido, Sky, Circle, Tether) — λ 0,5 % / 1,5 % / 4 %, sev 3 % / 20 % / 77 %
Parmi une dizaine de grands dollars numériques sur 2021-2025, deux décrochages de plus de 10 % : UST (2022) et USDC (11 mars 2023). 2 / 50 coin-années ≈ 4 % ⇒ HAUT. La catégorie « Basis Trading » (Ethena) : 0 exploit sur 95 protocole-années (borne 3,2 %). BASE 1,5 %.
Sévérité : BAS 3 % = USDC après rétablissement (0,88 $ au plus bas, ~0,97 $ le soir — [Kitco/Reuters](https://www.kitco.com/news/off-the-wire/2023-03-11/update-3-circle-assures-market-after-stablecoin-usdc-breaks-dollar-peg)) ; HAUT 77 % = xUSD de Stream Finance (−77 %, novembre 2025 — [Decrypt](https://decrypt.co/347285/stream-finance-stablecoin-plunges-77-protocol-fund-manager-loses-93-million)).

### <a id="oracle"></a>Oracle — λ 0,3 % / 1 % / 8,7 %, sev 5 % / 30 % / 100 %
27 manipulations d'oracle ≥ 5 M$ en 5 ans dans la base DeFiLlama (Mango, UwU Lend, Polter…), surtout sur des flux peu liquides : 27 / (62 prêteurs × 5 ans) = 8,7 % ⇒ borne HAUTE pour un flux de marché à source unique. Sévérité BASE 30 % : DAI à +30 % sur Coinbase Pro, source de l'oracle de Compound, 88,4 M$ de liquidations le 26 novembre 2020 ([The Block](https://www.theblock.co/post/85850/dai-compound-dydx-liquidations-defi)). HAUT 100 % : Mango, MNGO de 0,038 $ à 0,91 $ (~24×), 116 M$ vidés ([CertiK](https://www.certik.com/blog/mango-market)).

**Qualité du flux (A5)** — la borne de λ dépend de ce qui est lu on-chain (`rules.oracle_adjust`) :

| Ce que le contrat révèle | λ retenu | Pourquoi |
|---|---|---|
| Décote linéaire Pendle (`PT()`, `baseDiscountPerYear()`) | BAS | prix calculé, impossible à manipuler — mais **aveugle à un dépeg** |
| Méta-oracle primaire + secours (`primaryOracle()`, `backupOracle()`) | BAS | défaillance propre rare ; le risque passe par ses flux |
| Flux Chainlink (`aggregator()`), ≥ 10 opérateurs (USDe/USD : 16 opérateurs, seuil 0,5 % — [Chainlink](https://data.chain.link/feeds/ethereum/mainnet/usde-usd)) | BASE | agrégé |
| Source unique de prix de marché | HAUT | cas Mango / Compound |
| Contrat non décodé | HAUT | prudence |

### <a id="venue"></a>AMM / DEX — λ 0,5 % / 1,2 % / 2,5 %, sev 5 % / 30 % / 80 %
« Dexs » : 5 exploits / 415 protocole-années (Curve juillet 2023 61,7 M$, Balancer V2 novembre 2025 > 128 M$ — [DappRadar](https://dappradar.com/blog/balancer-exploit-november-2025)).

### <a id="lending"></a>Marché de prêt — λ 0,3 % / 0,97 % / 5 %, sev 2 % / 15 % / 60 %
« Lending » : 3 / 310 ; gros prêteurs : 0 / 60 ⇒ règle de trois 5 %. Sévérité HAUT : Euler, 13 mars 2023, 197 M$ volés (plus de 177 M$ rendus ensuite — [The Block](https://www.theblock.co/post/224705/euler-hacker-returns-funds)). HYPOTHÈSE sur le rapport perte / dépôts (60 %).

### <a id="protocol"></a>Contrat de protocole (SY de Pendle) — λ 0,2 % / 0,69 % / 3,3 %, sev 10 % / 50 % / 100 %
« Yield » : 1 / 145. Penpie (construit sur Pendle) : 27,3 M$ volés le 3 septembre 2024 ; les contrats de Pendle n'ont pas été touchés et Pendle dit avoir protégé ~105 M$ en mettant en pause ([The Block](https://www.theblock.co/news/regulation/2024-09-04-pendle-says-it-saved-105-million-that-could-have-been-further-drained-amid-penpie-hack-314616)).

### <a id="perp"></a>Bourse de couverture (Binance, OKX, Bybit…) — λ 1 % / 3,3 % / 7,6 %, sev 0 / 25 % / 100 %
Gros CEX : 4 exploits / 120 protocole-années (Bybit, BingX, SwissBorg, Deribit). Sévérité BAS 0 : Bybit (21 février 2025, 1,4 Md$ volés) a couvert ses clients ; Ethena n'avait aucun collatéral au comptant sur Bybit, ~30 M$ d'exposition aux dérivés, fonds de réserve ~46 M$ ([Cointelegraph](https://cointelegraph.com/news/ethena-assures-users-solvency-after-bybit-hack)). HAUT 100 % : FTX, retraits suspendus, trou jusqu'à 8 Md$ ([InvestmentNews](https://investmentnews.com/alternatives/ftx-hurtles-toward-bankruptcy-with-8-billion-hole/229054)).

### <a id="custodian"></a>Dépositaire — λ 0,2 % / 0,5 % / 2 %, sev 5 % / 30 % / 100 % — **HYPOTHÈSE**
Peu d'historique public. Repère : SVB (mars 2023) gelait 3,3 Md$ des 40 Md$ de réserves de Circle, soit ~8 %.

### <a id="admin"></a>Gouvernance — **HYPOTHÈSE** (λ 0,2 % / 0,5 % / 2 %).

---

## Mécanismes (w)

| Mécanisme | BAS / BASE / HAUT | Justification |
|---|---|---|
| <a id="backing"></a>backing (valeur du sous-jacent) | 0,9 / 1 / 1 | à l'échéance, 1 PT = 1 sous-jacent via le SY : une perte du sous-jacent passe entièrement |
| <a id="pricing"></a>pricing (flux de prix) | 0,05 / 0,3 / 0,8 | **USDe, 10 octobre 2025** : 0,65 $ sur Binance seulement, ~1 $ sur Curve, résorbé en ~40 min ([21Shares](https://www.21shares.com/research/why-did-ethenas-stablecoin-remain-stable-onchain-but-depegged-on-binance)) ⇒ un prix faux local reste local (w bas). Mango ⇒ w haut. |
| exit_liquidity (AMM) | 0 / 0,1 / 0,3 | pour un PT gardé jusqu'à l'échéance, une chute de l'AMM n'est qu'une perte de valeur de marché, effacée au remboursement |
| collateral_liquidation | 0,02 / 0,1 / 0,3 | stETH 2022 : ~6 % de décote sous les ventes forcées. **Borne haute si le collatéral à liquider dépasse la liquidité de l'AMM** (cas mesuré : 7,9 M$ de PT-sUSDe en garantie sur Morpho face à 3,8 M$ de liquidité AMM, ratio 2,1) |
| settlement | 0 / 0,05 / 0,2 | Solana 2024 : 5 h d'arrêt, valeur intacte |
| <a id="hedging"></a>hedging | part × (0,05 / 0,3 / 1) | **part mesurée** du collatéral d'Ethena sur chaque bourse (API Ethena : Binance 13,6 %, OKX 4,2 %, Bybit 2,3 %…) × fraction perdue en cas de défaut ; avec la garde hors-bourse (Copper, Ceffu), on perd surtout le PnL non réglé (Bybit 2025) |
| <a id="custody"></a>custody | 0,05 / 0,25 / 0,5 | **HYPOTHÈSE** : Ethena ne publie pas la part par dépositaire |
| bad_debt | part × (0,1 / 0,3 / 0,7) | **part mesurée** du collatéral dans le pool (API Aave) × fraction non couverte par les liquidations ; Stream / Elixir 2025 : créances douteuses en cascade |

<a id="oracle-roles"></a>**Flux d'un oracle composé :** primaire d'un méta-oracle 0,3 / 0,5 / 1 (protégé par le secours après le délai de bascule lu on-chain, 16 h pour le marché PT-sUSDe) ; secours 0,05 / 0,2 / 0,5 ; flux multiplié dans un oracle Morpho 0,9 / 1 / 1.

---

## Propagation

Facteur d'impact I(n) = produit des w sur le **chemin le plus fort** de n à la position. Exemple : flux USDe/USD → méta-oracle (0,2) → marché Morpho (0,3) → AMM (0,3) → position (0,1) ⇒ I ≈ 0,002. Variante « somme des chemins plafonnée à 1 » disponible sur la page ; la page affiche les deux valeurs pour le composant choisi.

Monte Carlo : 20 000 scénarios ; chaque composant défaille avec sa probabilité p ; s'il défaille, sa perte suit une loi Bêta(4, 4(1−sev)/sev) de moyenne sev ; perte de la position = Σ sev × I, plafonnée à 100 %. Tirages propres à chaque composant (graine = son identifiant) : deux poches qui partagent Ethena voient Ethena défaillir dans les mêmes scénarios.

## Coût de sortie

- **Crise (défaut) :** C(x) = c·x², C(1) = 11 % (énoncé de Belem).
- **Hors crise (mesuré) :** cotations réelles de l'API Pendle « convert » pour notre taille : ~0,03 % pour 1 M PT, ~0,13 % pour 3 M PT, et refus au-delà (« MarketProportionTooHigh » pour 5 M PT) le 2026-10-10. Conclusion : les 11 % décrivent une crise, quand la liquidité part ; hors crise la sortie est presque gratuite mais **plafonnée** par la taille du pool.
