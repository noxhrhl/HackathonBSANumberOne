# Récap : quelle défaillance → quoi faire

Calculé avec la règle de la page (perte si on reste comparée au coût réel de vente sur Pendle), pour une position de **2 M$**, gravité par défaut de chaque scénario.
Données enregistrées du 10/10/2026 : avec les données en ligne, certains chiffres peuvent changer.

Légende : 🔴 tout vendre · 🟠 vendre une partie · 🟢 rester · « sous réserve » = aucune perte chiffrable, mais des effets non chiffrés (INCONNU).

## PT-sUSDe-26NOV2026

| Si ce bloc casse | Scénario | Que faire | Rester coûte | Meilleure option coûte | Tout vendre coûte |
|---|---|---|---|---|---|
| Contrat SY Pendle (sUSDe) | Exploit du protocole −10 % | **🔴 TOUT VENDRE** | 200 000 $ | 10 430 $ | 10 430 $ |
| sUSDe | Chute de prix / dépeg −5 % | **🔴 TOUT VENDRE** | 100 000 $ | 10 430 $ | 10 430 $ |
| AMM Pendle sUSDe (2026-11-26) | Crise de liquidité −80 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| USDe | Chute de prix / dépeg −5 % | **🔴 TOUT VENDRE** | 100 000 $ | 10 430 $ | 10 430 $ |
| Morpho PT-sUSDE-26NOV2026/USDC (LLTV 91.5 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Oracle primaire 0x8fca…706d (combinaison de flux) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Morpho sUSDe/PYUSD (LLTV 91.5 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| PYUSD / USD (Chainlink) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Morpho sUSDe/DAI (LLTV 86.0 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Oracle du marché 0x5d91…ae25 (taux du coffre sUSDe seulement, sans prix de marché) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |

🟢 **RESTER (sous réserve)** pour : Méta-oracle 0xf8b2…1b08 (primaire + secours, bascule après 16 h), Flux Pendle « décote linéaire » (6.0 %/an, ne regarde pas le marché), Oracle secours 0xe2f4…5465 (combinaison de flux), USDe / USD (Chainlink), Ethena (émetteur d'USDe), Oracle du marché 0xe621…9e7e (combinaison de flux), Binance (bourse de couverture), OKX (bourse de couverture), Bybit (bourse de couverture), Deribit (bourse de couverture), CDE (bourse de couverture), Copper (ClearLoop, garde hors-bourse), Ceffu (MirrorX, garde hors-bourse).

## PT-wstETH-30DEC2027

| Si ce bloc casse | Scénario | Que faire | Rester coûte | Meilleure option coûte | Tout vendre coûte |
|---|---|---|---|---|---|
| Contrat SY Pendle (wstETH) | Exploit du protocole −10 % | **🔴 TOUT VENDRE** | 200 000 $ | 74 775 $ | 74 775 $ |
| wstETH | Chute de prix / dépeg −5 % | **🟠 VENDRE 80 % (1 600 000 $)** | 100 000 $ | 73 012 $ | 74 775 $ |
| AMM Pendle wstETH (2027-12-30) | Crise de liquidité −80 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |
| stETH | Chute de prix / dépeg −5 % | **🟠 VENDRE 80 % (1 600 000 $)** | 100 000 $ | 73 012 $ | 74 775 $ |
| Morpho wstETH/USDT (LLTV 86.0 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |
| Oracle du marché 0x95db…6992 (combinaison de flux) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |
| Custom price feed for wstETH / ETH (fournisseur non identifié) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |
| USDT / ETH (Chainlink) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |
| Morpho wstETH/WETH (LLTV 96.5 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 74 775 $ |

🟢 **RESTER (sous réserve)** pour : Lido (émetteur de stETH), Oracle du marché 0xbd60…71d8 (combinaison de flux), wstETH/stETH exchange rate (fournisseur non identifié), Opérateurs de nœuds Lido (choisis par la DAO), Comité d'oracles Lido (9 membres, rapporte les soldes).

## USDC prêté sur Aave V3 (Ethereum Core)

| Si ce bloc casse | Scénario | Que faire | Rester coûte | Meilleure option coûte | Tout vendre coûte |
|---|---|---|---|---|---|
| USDC | Chute de prix / dépeg −5 % | **🟢 RESTER** | 100 000 $ | 100 000 $ | 100 000 $ |

🟢 **RESTER (sous réserve)** pour : Circle (émetteur d'USDC), WETH, ETH / USD (Chainlink), weETH, Capped weETH / eETH(ETH) / USD (Chainlink (adaptateur CAPO d'Aave)), WBTC, wBTC/BTC/USD (fournisseur non identifié), USDT, Capped USDT/USD (Chainlink (adaptateur CAPO d'Aave)), wstETH, Capped wstETH / stETH(ETH) / USD (Chainlink (adaptateur CAPO d'Aave)), cbBTC, BTC / USD (Chainlink), USDe, Capped USDT/USD (Chainlink (adaptateur CAPO d'Aave)), osETH, Capped osETH / ETH / USD (Chainlink (adaptateur CAPO d'Aave)), Circle Reserve Fund (USDXX, fonds monétaire géré par BlackRock), Liquidités chez « quelques très grandes banques » (cf. SVB 2023), Tether (émetteur d'USDT), stETH, Ethena (émetteur d'USDe), Cantor Fitzgerald (garde des bons du Trésor), Lido (émetteur de stETH), Binance (bourse de couverture), OKX (bourse de couverture), Bybit (bourse de couverture), Deribit (bourse de couverture), CDE (bourse de couverture), Copper (ClearLoop, garde hors-bourse), Ceffu (MirrorX, garde hors-bourse), Opérateurs de nœuds Lido (choisis par la DAO), Comité d'oracles Lido (9 membres, rapporte les soldes).

## USDC prêté sur Morpho contre PT-sUSDe

| Si ce bloc casse | Scénario | Que faire | Rester coûte | Meilleure option coûte | Tout vendre coûte |
|---|---|---|---|---|---|
| Morpho PT-sUSDE-26NOV2026/USDC (LLTV 91.5 %) | Exploit du protocole −10 % | **🔴 RETIRER 38 % (le maximum, 764 324 $)** | 200 000 $ | 123 568 $ | 123 568 $ |
| USDC | Chute de prix / dépeg −5 % | **🟢 RESTER** | 100 000 $ | 100 000 $ | 100 000 $ |
| PT-sUSDE-26NOV2026 | Chute de prix / dépeg −5 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| Contrat SY Pendle (sUSDe) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| sUSDe | Chute de prix / dépeg −5 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| AMM Pendle sUSDe (2026-11-26) | Crise de liquidité −80 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| Méta-oracle 0xf8b2…1b08 (primaire + secours, bascule après 16 h) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| Oracle primaire 0x8fca…706d (combinaison de flux) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| Flux Pendle « décote linéaire » (6.0 %/an, ne regarde pas le marché) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| Oracle secours 0xe2f4…5465 (combinaison de flux) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| USDe / USD (Chainlink) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |
| USDe | Chute de prix / dépeg −5 % | **🟢 RESTER** | 0 $ | 0 $ | 0 $ |

🟢 **RESTER (sous réserve)** pour : Circle (émetteur d'USDC), Circle Reserve Fund (USDXX, fonds monétaire géré par BlackRock), Liquidités chez « quelques très grandes banques » (cf. SVB 2023), Ethena (émetteur d'USDe), Binance (bourse de couverture), OKX (bourse de couverture), Bybit (bourse de couverture), Deribit (bourse de couverture), CDE (bourse de couverture), Copper (ClearLoop, garde hors-bourse), Ceffu (MirrorX, garde hors-bourse).

## PT-sUSDe en garantie sur Morpho, USDC emprunté

| Si ce bloc casse | Scénario | Que faire | Rester coûte | Meilleure option coûte | Tout vendre coûte |
|---|---|---|---|---|---|
| Morpho PT-sUSDE-26NOV2026/USDC (LLTV 91.5 %) | Exploit du protocole −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| PT-sUSDE-26NOV2026 | Chute de prix / dépeg −5 % | **🔴 TOUT VENDRE** | 100 000 $ | 10 430 $ | 10 430 $ |
| USDC | Chute de prix / dépeg −5 % | **🟢 RESTER** | gain de 85 000 $ (la dette en USDC baisse) | gain de 85 000 $ | 10 430 $ |
| Contrat SY Pendle (sUSDe) | Exploit du protocole −10 % | **🔴 TOUT VENDRE** | 200 000 $ | 10 430 $ | 10 430 $ |
| sUSDe | Chute de prix / dépeg −5 % | **🔴 TOUT VENDRE** | 100 000 $ | 10 430 $ | 10 430 $ |
| AMM Pendle sUSDe (2026-11-26) | Crise de liquidité −80 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Méta-oracle 0xf8b2…1b08 (primaire + secours, bascule après 16 h) | Défaillance d'oracle −10 % | **🟠 VENDRE 20 % (400 000 $)** | 239 383 $ | 485 $ | 10 430 $ |
| Oracle primaire 0x8fca…706d (combinaison de flux) | Défaillance d'oracle −10 % | **🟢 RESTER** | 0 $ | 0 $ | 10 430 $ |
| Flux Pendle « décote linéaire » (6.0 %/an, ne regarde pas le marché) | Défaillance d'oracle −10 % | **🟠 VENDRE 20 % (400 000 $)** | 239 383 $ | 485 $ | 10 430 $ |
| Oracle secours 0xe2f4…5465 (combinaison de flux) | Défaillance d'oracle −10 % | **🟠 VENDRE 20 % (400 000 $)** | 240 165 $ | 485 $ | 10 430 $ |
| USDe / USD (Chainlink) | Défaillance d'oracle −10 % | **🟠 VENDRE 20 % (400 000 $)** | 240 165 $ | 485 $ | 10 430 $ |
| USDe | Chute de prix / dépeg −5 % | **🔴 TOUT VENDRE** | 100 000 $ | 10 430 $ | 10 430 $ |

🟢 **RESTER (sous réserve)** pour : Circle (émetteur d'USDC), Circle Reserve Fund (USDXX, fonds monétaire géré par BlackRock), Liquidités chez « quelques très grandes banques » (cf. SVB 2023), Ethena (émetteur d'USDe), Binance (bourse de couverture), OKX (bourse de couverture), Bybit (bourse de couverture), Deribit (bourse de couverture), CDE (bourse de couverture), Copper (ClearLoop, garde hors-bourse), Ceffu (MirrorX, garde hors-bourse).

## À retenir

- **Le bloc qui casse fait bouger le prix de ce que tu détiens** (sUSDe, USDe, wstETH, contrat SY) → vendre avant le choc coûte bien moins que rester : 🔴 ou 🟠.
- **Emprunteur Morpho + oracle qui se trompe** → 🟠 vendre ~20 % suffit : on rembourse une partie de la dette et on n'est plus liquidable (485 $ au lieu de 239 383 $).
- **PT-wstETH** → 🟠 vendre 80 % : le pool Pendle wstETH est plus petit, donc tout vendre coûte plus cher que de garder un peu.
- **Prêteur Morpho, exploit du marché** → 🔴 retirer le maximum (38 %) : le reste est prêté, impossible à retirer tout de suite.
- **Baisse de l'actif prêté (USDC)** → 🟢 rester : retirer ne protège pas, l'argent retiré reste en USDC.
- **Crise de liquidité du pool Pendle** → 🟢 rester : le PT est remboursé à l'échéance, vendre maintenant ne sert à rien.
- **Émetteurs, bourses, gardiens (Ethena, Binance, Circle…)** → 🟢 sous réserve : leur perte n'a pas de formule chiffrée, marquée INCONNU.
