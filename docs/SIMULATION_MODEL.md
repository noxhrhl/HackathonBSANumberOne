# How the failure simulator works and how the stay/exit decision is made

This document explains, in detail:

1. the **generic propagation engine** that every failure scenario runs through;
2. the three failure scenarios: **price crash / depeg**, **oracle failure** and **liquidity crisis**;
3. the **financial severity metrics** (market, liquidation, execution and total loss);
4. **where every number comes from** (on-chain reads, APIs, documentation, assumptions);
5. **how the stay / sell part / exit decision is taken**, and how it relates to the simulator.

Code: `risk_engine/simulate.py` (engine and scenarios), `risk_engine/amm.py` (Pendle AMM curve), `risk_engine/decide.py` (decision), `risk_engine/model.py` (Monte Carlo), `risk_engine/rules.py` (risk parameters). Tests: `tests/test_simulate.py`.

> **Important distinction.** The simulator answers *"IF this component fails, how much do I lose?"* and uses **no probability**. The stay/exit decision (section 7) is a separate, older module that weighs a *probability-weighted* loss against an exit cost. The two are not wired together yet (see section 7.6).

---

## Contents

- [1. The data the model starts from](#1-the-data-the-model-starts-from)
- [2. Case 4: the generic propagation engine](#2-case-4-the-generic-propagation-engine)
- [3. Case 1: asset price crash / stablecoin depeg](#3-case-1-asset-price-crash--stablecoin-depeg)
- [4. Case 2: oracle failure](#4-case-2-oracle-failure)
- [5. Case 3: liquidity crisis](#5-case-3-liquidity-crisis)
- [6. Financial severity metrics](#6-financial-severity-metrics)
- [7. How the stay / exit decision is taken](#7-how-the-stay--exit-decision-is-taken)
- [8. Data sources and verification status](#8-data-sources-and-verification-status)
- [9. Assumptions and known limits](#9-assumptions-and-known-limits)

---

## 1. The data the model starts from

### 1.1 The dependency graph

Each position has a graph in `data/graphs/<position>.json`, produced by `risk_engine/discover.py`. An edge **A → B** means "if A fails, B is affected". The position is the only sink. Each edge has a **mechanism** (`backing`, `pricing`, `exit_liquidity`, `collateral_liquidation`, `bad_debt`, `settlement`, `hedging`, `custody`), a **verification status** (`verified_onchain`, `api`, `documented`, `assumed`) and **evidence** (source, URL, timestamp).

Example, the borrower position `morpho-ptsusde-borrow`:

```text
USDe ──backing──► sUSDe ──backing──► SY-sUSDe ──backing──► PT-sUSDe ──backing(collateral)──► POSITION
                                                               ▲
AMM Pendle sUSDe ─────────────exit_liquidity──────────────────┘

Linear-discount feed ──► Primary oracle ──┐
        └───────────────► Backup oracle ──┴──► Meta-oracle ──► Morpho PT-sUSDe/USDC ──collateral_liquidation──► POSITION
USDe/USD Chainlink ──────► Backup oracle                          (LLTV 91.5 %)
USDC ──backing(debt)──► POSITION
```

### 1.2 The simulation baseline (`data/sim/<position>.json`)

`simulate.build_context(graph)` builds this file, and `pipeline.py` calls it. It holds the values the formulas need, all read **read-only**:

| Data | How it is obtained | Example (sUSDe market, 2026-10-10) |
|---|---|---|
| Pendle AMM state | `readState(router)` on the market contract (eth_call) | totalPt 971,271; totalSy 2,282,035; scalarRoot 57.597; lnFeeRateRoot 0.000979; lastLnImpliedRate 0.048895; expiry 2026-11-26 |
| SY → accounting asset rate | `SY.exchangeRate()` (eth_call) | 1.25239 USDe per SY-sUSDe |
| PT and underlying USD prices | Pendle API `/assets/prices` | PT-sUSDe 0.99295 $, sUSDe 1.25129 $ |
| Morpho oracle prices | `IOracle.price()` on meta, primary and backup oracles (eth_call) | 0.99240 USDC per PT |
| Token decimals | `decimals()` (eth_call) | PT 18, USDC 6 |
| Morpho market state | Morpho GraphQL API | collateral 7.93 M$, borrow 6.99 M$, supply 7.76 M$, LLTV 91.5 % |
| Meta-oracle parameters | `deviationThreshold()`, `challengeTimelockDuration()`, `healingTimelockDuration()` (eth_call, read by discovery) | 0.75 %, 16 h, 24 h |

The file uses a **cache-first** rule (`sources.cache_first`): an entry already in `data/cache/` is reused, so evidence used by the graphs is never silently refreshed. A missing entry is fetched online, or reported as missing in offline mode. Missing data never crashes the model. The model that needs it returns **UNKNOWN** (section 9).

---

## 2. Case 4: the generic propagation engine

All three failure scenarios run through the **same** engine (`simulate.Engine`). Nothing is a hard-coded calculator per scenario.

### 2.1 Separate state variables

Every node carries independent state variables. Prices are stored as **multipliers** relative to the baseline (1.0 = unchanged):

| Variable | Meaning | Changed by |
|---|---|---|
| `m` | market price multiplier (assets, SY, PT) | price shock, value transmission |
| `om` | oracle-reported price multiplier (oracles, lending markets) | oracle failure, oracle reading a market price |
| `lm` | pool liquidity multiplier (AMM venues) | liquidity crisis |
| `status` | `origin`, `changed`, `unknown`, `no_effect`, `unchanged` | engine |
| `unknown` | list of transmissions that could not be quantified | engine |

Executable exit price, collateral value, debt value, liquidation status and equity are computed at **position level** from these variables (section 3.5 onwards).

Keeping `m` and `om` separate is what makes it possible to say "the market price fell 5 % but the protocol still sees the old price".

### 2.2 The algorithm

1. **Apply the failure** to the selected node:
   - price shock: `m = 1 − s`
   - oracle failure: `om = 1 − s` (market `m` untouched)
   - liquidity crisis: `lm = 1 − s`
2. **Discover the downstream set**: every node reachable from the failed node through graph edges, plus *derived* edges (2.4).
3. **Order** the downstream nodes topologically.
4. For each node, in order, **recompute its state from the state of ALL its affected parents**, using the transmission model of each incoming edge (2.3).
5. Run the **feedback pass** for liquidation spirals (2.5).
6. **Evaluate the position** with its protocol's rules (sections 3 to 5).
7. Compute the **severity metrics** (section 6).
8. Return the **explanation**: one step per affected node (variable, before, after, mechanism, formula, parameters, source, assumptions), the edge models, the main propagation path, the UNKNOWN list and the secondary effects.

### 2.3 No double counting

A node's new state is a **function of its parents' states**. It is never a sum of losses arriving along different paths. Example: in an oracle failure on the Pendle linear-discount feed, the failure reaches the meta-oracle through two paths (primary and backup). The meta-oracle is evaluated **once** and reports `× 0.90`, not `× 0.81`. A test (`test_each_node_evaluated_once_even_with_two_paths`) checks this.

When a node receives two *different* value changes it has no formula to combine (for example two backing sources moving differently), it is marked **UNKNOWN** rather than guessing.

### 2.4 Transmission models (one per edge type)

`simulate.classify(edge)` assigns every edge a model from `simulate.MODELS`. Each model records its **dependency type, transmission mechanism, mathematical model, required parameters, data source, assumptions** and whether it is **quantified**.

| Model | Edge | Formula | Quantified |
|---|---|---|---|
| `wrapper_rate` | asset → ERC-4626 wrapper (USDe → sUSDe, stETH → wstETH) | `P_wrapper = P_base × conversionRate`; the rate does not depend on market price, so `m_wrapper = m_base` | yes |
| `sy_wrap` | underlying → Pendle SY | `1 SY = 1 underlying token`, so `m_SY = m_underlying` | yes |
| `pt_backing` | SY → PT | `P_PT = P_accountingAsset × exp(−lnImpliedRate × t / year)`; implied rate held constant, so `m_PT = m_SY` | yes |
| `observes` *(derived)* | asset → price feed | `reported = P_base / P_quote`, parsed from the feed's on-chain `description()` | yes |
| `observes_unknown` *(derived)* | asset → undecoded feed | feed formula not identified | **UNKNOWN** |
| `feed_combination` | feed → MorphoChainlinkOracleV2 | `price = SCALE × BASE_1 × BASE_2 × vaultRate / (QUOTE_1 × QUOTE_2)` | yes |
| `meta_source` | primary/backup → meta-oracle | switching rule (section 4.3) | yes |
| `oracle_to_lending` | oracle → lending market | `collateral(oracle) = Q × price() / 1e36` | yes |
| `collateral_value` | collateral asset → isolated market | `recoverable = collateral × P_market` | yes |
| `liquidation_rule` | Morpho market → borrower | Morpho Blue liquidation rules (section 3.6) | yes |
| `lender_claim` | lending market → lender | bad-debt estimate (section 3.8) | estimate |
| `amm_exit` | AMM → PT | Pendle V2 curve (section 5) | yes |
| `forced_sales` | lending market → AMM/asset | secondary effect, never in the total | **UNKNOWN / estimate** |
| `issuer_backing`, `issuer_deployment`, `pool_bad_debt`, `not_modeled` | issuer → asset, Aave multi-collateral pool, hedging, custody… | no mechanical price formula | **UNKNOWN** |

**Derived edges.** The graph has no edge "USDe → USDe/USD feed", because the feed does not *depend* on USDe for discovery purposes. A USDe price shock must still reach the feed. The engine therefore adds simulation-only edges from an asset to every feed whose on-chain `description()` names it:

- `"USDe / USD"` (Chainlink) observes USDe.
- `"USDT / ETH"` observes USDT divided by WETH.
- Aave CAPO adapters with a ratio provider (`"Capped wstETH / stETH(ETH) / USD"`) observe only the base ETH feed, because the ratio is an on-chain exchange rate.
- Feeds flagged `non_market_price` (Pendle linear discount, vault-rate-only oracles, exchange-rate feeds) get **no** edge: they do not react to market prices at all.

### 2.5 Reflexive loops (liquidation spirals)

Some graphs contain a real loop:

```text
wstETH price → wstETH oracle → Morpho liquidations → forced wstETH sales → wstETH price
```

The engine computes the **first-order** transmission with `forced_sales` edges left out of the ordering. Then `feedback_pass()` checks every forced-sales edge. If the lending market became liquidatable, or its oracle mechanism is unknown, the target node and **everything downstream** get the flag *"ventes forcées… (rétroaction non chiffrée)"*. The first-order number stays exact, and the spiral is visible but not invented.

### 2.6 Adding a failure type or protocol

- New failure type: add an entry to `SCENARIOS` (compatible node kinds, default and max severity, state variable) and its line in `Engine.run`.
- New protocol: add a model to `MODELS`, a rule in `classify()`, and a node or position evaluator.

---

## 3. Case 1: asset price crash / stablecoin depeg

**Applies to:** any `asset` node. **Severity slider:** 1 % to 90 % fall in market price.

### 3.1 Value chain (market prices)

For a USDe depeg of `s`:

```text
m_USDe  = 1 − s
m_sUSDe = m_USDe            (ERC-4626: P = P_USDe × convertToAssets; the rate is unaffected)
m_SY    = m_sUSDe           (1 SY = 1 sUSDe)
m_PT    = m_SY              (P_PT = P_USDe × exp(−lnImpliedRate·t/yr); implied rate held constant)
```

Along pure backing links the shock passes 1:1, because each link is a fixed exchange rate. This is the only place where "−5 % upstream = −5 % downstream" holds, and it rests on a stated assumption (the AMM implied rate does not move). Everything below **is not 1:1**.

### 3.2 Oracle chain (prices seen by protocols)

The same shock also travels through the oracle side, through derived edges:

```text
USDe/USD Chainlink feed:  om = m_USDe = 0.95
Backup Morpho oracle:     om = BASE_FEED_1(linear) × BASE_FEED_2(USDe/USD) = 1 × 0.95
Primary Morpho oracle:    om = BASE_FEED_1(linear) = 1        ← the linear-discount feed ignores USDe
Meta-oracle:              depends on the incident duration (section 4.3)
```

### 3.3 Meta-oracle: the time dimension

The Morpho PT-sUSDe/USDC market uses a Steakhouse **MetaOracleDeviationTimelock**:

- **less than 16 h** after the incident: the meta-oracle stays on the **primary** oracle, so the protocol sees **no change**;
- **16 h or more**: the deviation `|primary − backup| / average = 5.17 %` exceeds the 0.75 % threshold, so it switches to the **backup**, and the protocol sees about −5 %.

That is why the UI has an "incident duration" selector (1 h / 24 h).

### 3.4 Results on the PT holder (`pt-susde-26nov2026`, 2 M$)

USDe −5 %: market loss **100,000 $** (2 M$ × 5 %). Exit cost on the AMM goes from 10,430 $ to 9,909 $: the same relative impact applied to a smaller value. **Total: 99,478 $.**

### 3.5 Lending position: collateral, debt, LTV, health factor

For the borrower (`morpho-ptsusde-borrow`: Q = 2,014,202 PT collateral ≈ 2 M$, D = 1,700,000 USDC debt):

```text
Collateral (market, USD)   = Q × P_PT_market
Collateral (oracle, USDC)  = Q × P_oracle                 P_oracle = price() / 10^(36 + dec_loan − dec_coll)
Debt (USD)                 = D × P_USDC
LTV                        = D / (Q × P_oracle)
Health factor              = Q × P_oracle × LLTV / D
Liquidatable               ⇔  HF < 1
```

Morpho counts debt **in USDC units** and the oracle does not read USDC/USD. A USDC depeg therefore **does not change the health factor**, while it lowers the USD value of the debt and so raises the borrower's USD equity. A test covers this.

| USDe −5 % | LTV | Health factor | Liquidatable? |
|---|---|---|---|
| Before | 85.05 % | 1.0759 | no |
| After, 1 h | 85.05 % | 1.0759 | no |
| After, 24 h (backup oracle) | 89.56 % | 1.0217 | no |

**Minimum shock to liquidation.** For positions with a health factor, the engine bisects the severity (40 iterations), re-running the full propagation each time, to find the smallest `s` that makes `HF < 1`:

- USDe depeg, 24 h: **7.02 %**
- USDe depeg, 1 h: **none** up to 90 %, because the primary oracle never sees it

### 3.6 Liquidation (Morpho Blue rules)

If eligible, the engine **simulates** a liquidation using the formulas in `Morpho.sol` and `ConstantsLib.sol`:

```text
LIF            = min(1.15, 1 / (1 − 0.3 × (1 − LLTV)))        LLTV 91.5 % → LIF = 1.02617
seized_units   = min(Q, D_repaid × LIF / P_oracle)
repaid         = seized_units × P_oracle / LIF
bad debt       = D − repaid   (only if all collateral is seized; socialised to lenders)
Liquidation loss (borrower) = seized_units × P_market − repaid × P_USDC
```

Morpho has no close factor, so the engine simulates a **full** liquidation, the worst case. Eligibility and execution are reported separately (section 6.2).

### 3.7 Executable exit

The borrower's unwind is: sell all PT on the Pendle AMM (section 5), then repay the debt. Executable equity = proceeds − D × P_USDC.

### 3.8 Lenders (`morpho-ptsusde-usdc`, `aave-v3-*`)

- **Market loss:** `size × (1 − m_loanAsset)` (a USDC depeg lowers the deposit's USD value).
- **Bad-debt estimate (isolated Morpho market only):**

  ```text
  loss ≈ (size / market supply) × max(0, borrow × m_loan − collateral × m_collateral)
  ```

  This treats all borrowers as one *representative borrower* (the distribution of positions is not published), and is labelled **ESTIMATION**. Example: USDe −20 % gives about 167,689 $ for a 25.8 % share of the market.
- **Aave (multi-collateral pool):** bad debt depends on each borrower and is **UNKNOWN**. The engine reports which collateral prices moved, but gives no number.
- **Exit:** a withdrawal is capped by the market's available liquidity (764 k$ on the Morpho market, 38 % of the position). The remainder is **delayed, not lost**.

### 3.9 Hold positions (`susde-hold`, `wsteth-hold`, `usdc-hold`)

Market loss = `size × m_asset`. There is no exit pool in the graph, so execution loss is **"information insuffisante"**. Example: stETH −20 % gives wstETH −20 %, a loss of 400,000 $.

---

## 4. Case 2: oracle failure

**Applies to:** any `oracle` node. **Severity slider:** 1 % to 90 % *under-reporting*.

### 4.1 Principle

The failed oracle reports `(1 − s) ×` its true value. **Market prices are not touched**: `m` stays 1.0 everywhere. Only the `om` variables change, and they propagate through:

```text
feed → feed_combination → meta_source → oracle_to_lending → liquidation_rule → position
```

### 4.2 Which protocols depend on the oracle

The engine follows the graph's `pricing` edges from the failed oracle to every oracle combination and lending market that uses it. Each step records the multiplier.

### 4.3 Meta-oracle switching rule

Source: [Steakhouse MetaOracle docs](https://steakhouse.financial/docs/products/infrastructure/metaoracle) and the [Morpho forum introduction](https://forum.morpho.org/t/steakhouse-introducing-metaoracledeviationtimelock/1850). The contract source is not on Sourcify, so the rule comes from the official documentation.

```text
p1 = price_primary0 × om_primary        b1 = price_backup0 × om_backup      (absolute price() values read on-chain)
deviation = |p1 − b1| / ((p1 + b1) / 2)
if using primary  and deviation > threshold and duration ≥ challengeTimelock → switch to backup
if using backup   and deviation ≤ threshold and duration ≥ healingTimelock   → switch back to primary
om_meta = selected_new_price / current_price0
```

Assumption: someone calls the public `challenge()` function as soon as the deviation exceeds the threshold.

**Key finding.** The meta-oracle switches to the backup whichever source is the faulty one:

| Failure (−10 %) | Primary | Backup | Deviation | Meta-oracle | Borrower HF (24 h) |
|---|---|---|---|---|---|
| Linear-discount feed (used by both) | −10 % | −10 % | 0.04 % | stays on primary, −10 % | **0.968, liquidatable** |
| USDe/USD Chainlink feed (backup only), 1 h | 0 | −10 % | 10.6 % | stays on primary (timelock not elapsed) | 1.076, safe |
| USDe/USD Chainlink feed (backup only), 24 h | 0 | −10 % | 10.6 % | **switches to the faulty backup** | **0.968, liquidatable** |

### 4.4 Liquidation loss under oracle failure

Because the oracle undervalues the collateral, the liquidator seizes more units than the debt justifies:

```text
seized_units = D × LIF / P_oracle_faulty
loss         = seized_units × P_market − D        ≈ D × (LIF × P_market / P_oracle_faulty − 1)
```

Linear feed −10 %: 1,953,155 PT seized (worth 1,939,383 $ at market) against 1,700,000 USDC repaid. **Liquidation loss 239,383 $**, and 61,048 PT remain to the borrower. **Total 228,971 $ if the liquidation is executed.** Without liquidation the loss is 0 $, because the market price is unchanged.

### 4.5 Remaining equity after a hypothetical liquidation

```text
equity_after_liquidation = (Q − seized) × P_market − (D − repaid) × P_USDC
```

### 4.6 Positions that do not use the oracle

A PT holder has no exposure to the oracle *price itself*: market loss is 0. The engine still detects that the Morpho market's aggregate health factor falls from 1.037 to 0.934, so the whole market becomes liquidatable. It then estimates the **secondary effect**: about 7.98 M PT would need to be resold, but the AMM can absorb only 2.70 M (34 %). This is shown under "Effets secondaires", flagged UNKNOWN on the position, and **never included in the total**, because the distribution of borrowers is unknown.

---

## 5. Case 3: liquidity crisis

**Applies to:** any `venue` (AMM) node. **Severity slider:** 1 % to 99 % of liquidity withdrawn.

### 5.1 What an LP withdrawal does

LPs burn LP tokens and receive PT and SY **in the pool's current proportion**. So:

```text
totalPt'  = totalPt × (1 − s)
totalSy'  = totalSy × (1 − s)
proportion, implied rate, spot price  → UNCHANGED
depth (and capacity)                  → × (1 − s)
```

**An 80 % liquidity drop is not an 80 % price drop.** The reference (market) price is unchanged, and only the cost and possibility of exiting change.

### 5.2 Pendle V2 AMM pricing model

`risk_engine/amm.py` is a transcription of `MarketMathCore.sol` ([pendle-core-v2-public](https://github.com/pendle-finance/pendle-core-v2-public)):

```text
t            = expiry − now                          (seconds)
rateScalar   = scalarRoot × 365 days / t
totalAsset   = totalSy × SY.exchangeRate()
proportion   = totalPt / (totalPt + totalAsset)
logit(p)     = ln(p / (1 − p))
rateAnchor   = exp(lastLnImpliedRate × t / year) − logit(proportion) / rateScalar
feeRate      = exp(lnFeeRateRoot × t / year)

Selling d PT (swapExactPtForSy):
  proportion' = (totalPt + d) / (totalPt + totalAsset)
  if proportion' > 0.96  → revert "MarketProportionTooHigh"   (full exit impossible)
  rate        = logit(proportion') / rateScalar + rateAnchor  (asset per PT, must be ≥ 1)
  preFee      = d / rate
  fee         = preFee × (feeRate − 1) / feeRate
  assetOut    = preFee − fee
  syOut       = assetOut / SY.exchangeRate()

Spot price (PT in asset)   = exp(−lastLnImpliedRate × t / year)
Price impact               = 1 − (assetOut / d) / spot
Max PT sellable            = 0.96 × (totalPt + totalAsset) − totalPt
Exit proceeds (USD)        = syOut × P_SY_USD
```

**Verification.** The implementation matches Pendle's on-chain `RouterStatic.swapExactPtForSyStatic` to within 10⁻⁵ (399,007.32 vs 399,007.32 SY for 503,551 PT; 1,590,020.61 vs 1,590,020.61 SY for 2,014,202 PT). Its spot implied APY (5.011 %) matches the Pendle API. Both checks are automated tests.

The Pendle API `convert` quotes are 0.13–0.5 % better, because the router also fills **limit orders**. Limit orders are not guaranteed in a crisis, so the model uses the **AMM alone**, a conservative bound.

### 5.3 Results (PT-sUSDe pool, position 2,014,202 PT)

| Liquidity withdrawn | Max sellable PT | Full exit? | Effect |
|---|---|---|---|
| 0 % (today) | 2,704,829 | yes, impact 0.52 % | exit cost about 10,430 $ |
| 10 % | 2,434,346 | yes | higher price impact |
| 30 % | 1,893,380 | **no** (122,715 PT blocked) | |
| 80 % | 540,966 | **no** (1,473,778 PT ≈ 1.46 M$ blocked) | cost on the sellable part 829 $ → 4,990 $ |

Near expiry the Pendle curve is very flat (rateScalar ≈ 447), so price impact stays below about 1 %. The binding constraint is the **96 % proportion cap**, a hard capacity limit.

### 5.4 Does the pool feed an oracle?

The engine looks for `pricing` edges leaving the venue. In every current graph there are **none**: the Morpho PT oracles use the Pendle *linear-discount* feed, read on-chain, which never reads the AMM. **No propagation to lending markets**, and the borrower's health factor stays at 1.076 (tested). Even for a pool-TWAP oracle, a proportional withdrawal leaves spot and TWAP unchanged. Only a manipulation scenario, which is not modelled, would move them.

### 5.5 Liquidity crisis and liquidations

For a borrower, liquidity does not change the health factor. It does change the **unwind**: at an 80 % cut, the borrower can sell only 27 % of the collateral. It also hurts lenders indirectly: liquidators could not resell seized collateral, which is the secondary-effect estimate of section 4.6.

---

## 6. Financial severity metrics

Each metric shows **initial value, value after, absolute change (USD), % of initial position value, method and status**.

### 6.1 Definitions

```text
E_ref0  = equity at market prices before            (collateral − debt for a borrower; value for a holder)
E_ref1  = equity at market prices after, no liquidation
E_liq1  = equity after, with simulated liquidation   (= E_ref1 if not eligible)
c0      = execution cost before = reference value of units sold − exit proceeds   (baseline pool)
c1      = execution cost after  = same, on what remains after liquidation, in the shocked pool
```

| Metric | Formula |
|---|---|
| **Market loss** | `E_ref0 − E_ref1` |
| **Liquidation loss** | `E_ref1 − E_liq1` (includes the liquidation incentive LIF) |
| **Execution loss** | `c1 − c0` (incremental cost of exiting after vs before) |
| **Total financial impact** | market + liquidation + execution |

### 6.2 Why there is no double counting

The total is a **telescoping sum**:

```text
(E_ref0 − E_ref1) + (E_ref1 − E_liq1) + (c1 − c0)  =  (E_ref0 − c0) − (E_liq1 − c1)
                                                   =  executable equity before − executable equity after
```

Each dollar is counted in exactly one component. Example: after a liquidation, the seized collateral is no longer charged an exit cost, because it is part of the liquidation loss instead. Tests check `total = sum of components` on several scenarios.

### 6.3 Special cases

- **Liquidation eligibility is not a realised loss.** The liquidation metric's status reads *"calculé — SI la liquidation est exécutée"*. The result also gives `loss_without_liquidation`.
- **Full exit impossible.** Execution costs before and after are compared on the **same quantity** (the part still sellable). The blocked part appears on its own line, *"Valeur non exécutable maintenant"*, which is **excluded from the main total**: it is redeemable at the PT's maturity, so it is illiquid, not lost. Because of this exclusion, the main total can look *smaller* in a severe crisis than in a mild one (−80 %: 4,161 $, against 4,592 $ at −20 %). The result therefore also gives a second figure, highlighted in red on the dashboard:

  ```text
  blocked_usd             = blocked PT × reference price
  loss_if_forced_exit_usd = total loss + blocked_usd      (upper bound: blocked part counted as lost)
  ```

  PT-sUSDe at −80 %: 1,463,386 $ blocked, so 1,467,547 $ lost if you must exit now. The dashboard shows the "Sortie complète IMPOSSIBLE" and "Perte si sortie forcée maintenant" tiles, plus an extra row in the metrics table.
- **Amounts are shown to the dollar** in the simulator. A loss of a few thousand dollars on a 2 M$ position is visible, whereas the rest of the page rounds to "2,00 M$".
- **No exit model** (hold positions, lending withdrawals): execution loss is "information insuffisante", and the total covers market + liquidation only.

---

## 7. How the stay / exit decision is taken

This is the **existing** decision module (`risk_engine/decide.py`, `risk_engine/model.py`, `risk_engine/rules.py`), shown on the page as "La réponse à 3 h du matin" and "Combien vendre". It answers: *an alert is live on a component; should I stay, sell part, or exit?*

### 7.1 Inputs

| Input | Meaning | Source |
|---|---|---|
| **Component in alert** | the node the alert is about | chosen on the page |
| **incident_p** | probability that the alert becomes real damage | **manager's input**, default 30 %, with suggested ranges per component type (`rules.INCIDENT_P_HINTS`, e.g. oracle 10–40 %) |
| **p_base** for every other node | background failure probability over the horizon: `p = 1 − exp(−λ × H / 365)` | λ from 5 years of DeFiLlama exploits (`data/calibration/hacks_rates.json`) or documented incidents; H = days to PT expiry, otherwise 30 |
| **sev** | loss of a component *if* it fails | real incidents (USDC 2023 −12 %, stETH 2022 −6 %, xUSD 2025 −77 %, Mango 2022…) |
| **w** | share of damage crossing an edge, by mechanism | `rules.MECHANISM_W`, measured shares where available (e.g. Ethena on Binance 13.6 %) |
| **C(1)** | cost of selling 100 % of the position | 11 % in a crisis (Belem brief), or real Pendle quotes outside crisis |

Every parameter has a **LOW / BASE / HIGH** range, justified in `NOTES.md`.

### 7.2 Impact factor and Monte Carlo

```text
Impact factor  I(n) = product of w along the strongest path from n to the position   ("max" mode)
                       (or sum over all paths capped at 1, "sum" mode)
Loss if n fails ≈ sev_n × I(n)
```

Monte Carlo (`model.PositionModel`, 20,000 scenarios):

- each node fails with probability `p` (the alert node uses `incident_p`);
- if it fails, its severity is drawn from a Beta distribution with mean `sev` (`a = 4, b = 4(1 − sev)/sev`);
- position loss in a scenario = `min(1, Σ sev_draw × I)`;
- draws depend only on the node id (common random numbers), so two positions sharing Ethena see Ethena fail in the same scenarios.

Output: **expected loss** `E[L]` and **CVaR 95** (mean of the 5 % worst scenarios).

### 7.3 Exit cost

Selling on an AMM costs more per dollar as size grows, so the total exit cost is convex:

```text
C(x) = c × x²          x = fraction sold, calibrated so that C(1) = c
```

- **Crisis regime (default):** `c = 11 %` (Belem).
- **Normal regime:** linear interpolation of real Pendle `convert` quotes for 25 %, 50 %, 100 %… of the position. Example: about 0.07 % (impact + fees) for the full PT-sUSDe position, but a 5 M PT sale is **refused** ("MarketProportionTooHigh"), so exits are capped by capacity.

### 7.4 Optimal fraction to sell

The cost of selling `x` and keeping `1 − x`:

```text
Total(x) = C(x) + (1 − x) × L        L = E[L] (neutral view)  or  CVaR95 (prudent view)

dTotal/dx = 2cx − L = 0    ⇒    x* = L / (2c),  clipped to [0, capacity]
```

The code also searches a 1 % grid, which covers non-quadratic cost curves, and keeps the better of the grid point and the closed form.

**Decision category:**

| x* | Decision |
|---|---|
| x* < 5 % | **Stay** (rester) |
| 5 % ≤ x* ≤ 95 % | **Sell x\*** (vente partielle) |
| x* > 95 % | **Exit everything** (tout vendre) |

Worked example: `L = 6 %, c = 11 %` gives `x* = 0.06 / 0.22 = 27 %`, total cost `0.11 × 0.27² + 0.73 × 6 % = 5.2 %`, against 6 % (stay) and 11 % (exit all).

The page shows the three strategies side by side (exit cost, expected cost, worst 5 %, USD) and the saving against the better of the two extremes.

### 7.5 Robustness checks shown with the decision

- **Probability sweep:** x* recomputed for `incident_p` from 0 to 100 % with the same random draws. This gives *"the recommendation holds for p between A % and B %; exit everything above C %"*.
- **LOW / BASE / HIGH:** the decision under the three parameter sets.
- **Sensitivity:** one input at a time (severity, w, p_base, C(1) × 0.5 / × 1.5, incident_p ± 20 pts), ranked by how much x* moves.
- **Simple break-even** (`risk.assess`): exiting everything pays when `incident_p > C(1) / (sev × I)`.

### 7.6 Relation to the new simulator (read this)

- The simulator **does not** produce stay/exit recommendations or probabilities, as the task required.
- The decision module still uses the **linear** loss approximation `sev × I`. The simulator computes the **exact, non-linear** loss for a given failure: oracle timelocks, health-factor thresholds, liquidation penalties, AMM capacity. These can differ a lot. A 5 % USDe depeg costs a PT-sUSDe holder 5 % in the simulator, while `sev × I` gives a different, averaged figure.
- The natural next step (**not implemented**) would be to feed the simulator into the decision: for a chosen severity, `L = incident_p × simulated_total_loss / position_value`; for a distribution of severities, average the simulated losses. Then use the same `x* = L / 2c` rule, possibly with the simulated exit cost (section 5) in place of `C(x) = c·x²`.

---

## 8. Data sources and verification status

| Data | Source | Status |
|---|---|---|
| Pendle AMM state (totalPt, totalSy, scalarRoot, fees, implied rate, expiry) | `readState(0x8888…F946)` on the market contract, Ethereum RPC `ethereum-rpc.publicnode.com` | **verified on-chain** |
| SY exchange rate | `SY.exchangeRate()` | **verified on-chain** |
| AMM-only reference quotes | `RouterStatic.swapExactPtForSyStatic` at `0x2638…B14C` | **verified on-chain** (validation) |
| Morpho oracle prices | `IOracle.price()` on meta, primary and backup oracles | **verified on-chain** |
| Oracle structure (primary/backup, feeds, linear discount 6 %/yr, threshold 0.75 %, timelocks 16 h / 24 h) | eth_call during discovery | **verified on-chain** |
| Feed descriptions (USDe / USD, etc.) | `description()` | **verified on-chain** |
| Token decimals | `decimals()` | **verified on-chain** |
| PT, sUSDe, wstETH, sUSDS prices | Pendle API `/v1/1/assets/prices` | API |
| Pendle pool liquidity, implied APY | Pendle API `/v2/1/markets/{market}/data` | API |
| Real exit quotes (incl. limit orders) | Pendle API `/v2/sdk/1/convert` | API |
| Morpho markets (LLTV, collateral, borrow, supply, oracle) | Morpho GraphQL `api.morpho.org/graphql` | API |
| Aave reserves, oracles, available liquidity | Aave GraphQL `api.v3.aave.com/graphql` | API |
| Ethena hedging venues and collateral breakdown | `app.ethena.fi/api` | API |
| Protocol TVL, exploit history (for λ) | DeFiLlama `api.llama.fi` | API |
| Morpho liquidation constants (LIF cap 1.15, cursor 0.3) | Morpho Blue `ConstantsLib.sol` / `Morpho.sol` | documented (code) |
| Pendle AMM math | Pendle `MarketMathCore.sol` | documented (code), verified against RouterStatic |
| Meta-oracle switching rule | Steakhouse documentation and Morpho forum | documented |
| Position sizes (2 M$ pockets; borrower collateral 2 M$, debt 1.7 M$) | `data/positions.json` | **illustrative** |
| USDC / USDT = 1 $ when no price is read | simulator default | **assumption** (shown on the page) |

Every response is stored in `data/cache/<source>/` with its URL and `fetched_at` timestamp. The page lists the evidence used by each simulation under "Données utilisées : vérifiées et hypothèses".

All blockchain interactions are read-only (`eth_call`, `eth_getCode`). Nothing is signed or sent.

---

## 9. Assumptions and known limits

**Assumptions, stated in each step's output:**

- AMM implied rate constant during the incident. In real crises it usually rises, so the PT market loss is a lower bound.
- Wrapper market price follows its redemption value (no extra discount on sUSDe or wstETH).
- Price feeds update to the new price (shock beyond the deviation threshold, or heartbeat elapsed).
- Someone calls the meta-oracle's `challenge()` as soon as possible.
- Simulated liquidation is full (worst case). Eligibility does not mean execution.
- AMM-only exit (limit orders ignored), so exit proceeds are conservative.
- Lender bad debt and forced sales use one representative borrower: **estimates**.
- Stablecoins without a read price = 1 $.

**Reported as UNKNOWN, never as a number:**

- issuer insolvency (Ethena, Circle, Lido…): no mechanical price formula;
- Aave multi-collateral bad debt;
- undecoded or unidentified price feeds;
- the price impact of forced liquidation sales (secondary effect, outside the total);
- hedging and custody failures (exchanges, custodians).

**Not modelled yet:**

- Uniswap and Curve AMMs (no such venue in the current graphs; a new AMM model plugs into `exit_value`);
- implied-rate shocks and oracle manipulation of pool TWAPs;
- partial liquidations and liquidator behaviour;
- connecting the simulator's exact losses to the stay/exit decision (section 7.6).
