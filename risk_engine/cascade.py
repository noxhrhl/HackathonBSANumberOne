"""Stress test conditionnel de liquidations Morpho/PT-sUSDe, sans accès réseau.

Le déclenchement utilise exclusivement l'oracle Morpho. La courbe Pendle ne sert
qu'à estimer le produit des ventes et la capacité d'exécution. Le mode par défaut
ne fait JAMAIS remonter le prix de vente vers l'oracle de ce marché.
"""

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """Hypothèses explicites du choc et du comportement des liquidateurs."""

    oracle_shock: float = 0.05
    sale_shock: float = 0.05
    liquidity_fraction: float = 0.5
    oracle_feedback: float = 0.0  # >0 : contrefactuel, PAS l'oracle observé
    gas_usdc: float = 100.0
    max_sales_per_round: int = 2
    max_rounds: int = 12

    def __post_init__(self):
        for name in ("oracle_shock", "sale_shock", "oracle_feedback"):
            if not 0 <= getattr(self, name) < 1:
                raise ValueError(f"{name} doit être dans [0, 1[")
        if not 0 < self.liquidity_fraction <= 1:
            raise ValueError("liquidity_fraction doit être dans ]0, 1]")
        if self.gas_usdc < 0 or self.max_sales_per_round < 1 or self.max_rounds < 1:
            raise ValueError("Frais, vitesse ou nombre de tours invalides")


def liquidation_incentive(lltv):
    """Bonus Morpho Blue : LIF = min(1,15 ; 1/(0,3×LLTV + 0,7))."""
    return min(1.15, 1.0 / (0.3 * lltv + 0.7))


def validate_snapshot(snapshot):
    """Rejette les relevés incomplets ou incohérents avant toute simulation."""
    if snapshot.get("schema_version") != 1:
        raise ValueError("Format de l'instantané non reconnu")
    market = snapshot["market"]
    if market["marketId"].lower() != snapshot["market_id"].lower():
        raise ValueError("Identifiant de marché incohérent")
    if market["oracle"]["address"].lower() != snapshot["oracle"]["address"].lower():
        raise ValueError("Adresse d'oracle incohérente")
    if snapshot["oracle"].get("current_is_primary") is False:
        raise ValueError("Oracle de secours actif : hypothèse de non-retour du prix Pendle à revoir")
    loan_dec = int(market["loanAsset"]["decimals"])
    coll_dec = int(market["collateralAsset"]["decimals"])
    if loan_dec != 6 or coll_dec != 18 or market["loanAsset"]["symbol"] != "USDC":
        raise ValueError("Ce moteur attend le marché USDC / PT à 18 décimales")
    debt = int(market["state"]["borrowAssets"])
    collateral = int(market["state"]["collateralAssets"])
    debt_sum = sum(int(x["debt_raw"]) for x in snapshot["borrowers"])
    collateral_sum = sum(int(x["collateral_raw"]) for x in snapshot["borrowers"])
    if abs(debt_sum - debt) > max(100, debt // 1000):
        raise ValueError("Couverture des dettes insuffisante")
    if abs(collateral_sum - collateral) > max(100, collateral // 1000):
        raise ValueError("Couverture des garanties insuffisante")
    if len({x["address"] for x in snapshot["borrowers"]}) != len(snapshot["borrowers"]):
        raise ValueError("Emprunteurs dupliqués")
    if any(int(x["debt_raw"]) > 0 and int(x["collateral_raw"]) == 0 for x in snapshot["borrowers"]):
        raise ValueError("Dette préexistante sans garantie : traitement séparé requis")
    points = [(float(q["size_pt"]), float(q["proceeds_usdc_assumed"]))
              for q in snapshot["pendle"]["quotes"] if q["ok"]]
    if len(points) < 3 or points != sorted(points):
        raise ValueError("Courbe de vente insuffisante ou non triée")
    if any(a <= 0 or b <= 0 for a, b in points) or any(points[i][1] <= points[i - 1][1]
                                                           for i in range(1, len(points))):
        raise ValueError("Cotations de vente non monotones")
    if float(snapshot["oracle"]["price_usdc_per_pt"]) <= 0:
        raise ValueError("Prix d'oracle invalide")
    return points


def _linear_cumulative(points, quantity):
    """Interpôle seulement ENTRE les tailles cotées ; aucune extrapolation au-delà."""
    if quantity <= 0:
        return 0.0
    if quantity > points[-1][0] + 1e-9:
        raise ValueError("Vente au-delà de la plus grande cotation vérifiée")
    left_x, left_y = 0.0, 0.0
    for right_x, right_y in points:
        if quantity <= right_x:
            return left_y + (quantity - left_x) * (right_y - left_y) / (right_x - left_x)
        left_x, left_y = right_x, right_y
    return points[-1][1]


def sale_proceeds(points, already_sold_pt, new_sale_pt, scenario):
    """Produit marginal estimé sous choc de prix et liquidité, sans extrapolation."""
    factor = scenario.liquidity_fraction
    before = _linear_cumulative(points, already_sold_pt / factor)
    after = _linear_cumulative(points, (already_sold_pt + new_sale_pt) / factor)
    return factor * (after - before) * (1 - scenario.sale_shock)


def _choose_liquidation(position, oracle_price, lltv, points, sold_pt, scenario):
    """Cherche une liquidation partielle rentable sous la capacité encore cotée."""
    lif = liquidation_incentive(lltv)
    capacity_pt = max(0.0, points[-1][0] * scenario.liquidity_fraction - sold_pt)
    max_repay = min(position["debt_usdc"], position["collateral_pt"] * oracle_price / lif,
                    capacity_pt * oracle_price / lif)
    if max_repay <= 1e-8:
        return None, "liquidité insuffisante"
    # Grille décroissante, puis petites fractions : choix stable et reproductible.
    fractions = [i / 64 for i in range(64, 0, -1)] + [1 / (2**i) for i in range(7, 21)]
    for fraction in fractions:
        repaid = max_repay * fraction
        seized = min(position["collateral_pt"], repaid * lif / oracle_price)
        proceeds = sale_proceeds(points, sold_pt, seized, scenario)
        if proceeds + 1e-8 >= repaid + scenario.gas_usdc:
            return {"repaid_usdc": repaid, "seized_pt": seized, "sale_proceeds_usdc": proceeds,
                    "liquidator_profit_usdc": proceeds - repaid - scenario.gas_usdc}, None
    return None, "liquidation non rentable"


def simulate(snapshot, scenario=None):
    """Simule, tour par tour, seuil Morpho, saisies, ventes et dette non couverte.

    Aucun tirage aléatoire ni appel réseau. Les positions d'entrée sont copiées.
    Les ventes épuisent une capacité de cotation commune sans reconstitution.
    """
    scenario = scenario or Scenario()
    points = validate_snapshot(snapshot)
    lltv = int(snapshot["market"]["lltv"]) / 1e18
    initial_oracle = float(snapshot["oracle"]["price_usdc_per_pt"])
    oracle = initial_oracle * (1 - scenario.oracle_shock)
    positions = [{"address": x["address"], "debt_usdc": int(x["debt_raw"]) / 1e6,
                  "collateral_pt": int(x["collateral_raw"]) / 1e18} for x in snapshot["borrowers"]
                 if int(x["debt_raw"]) > 0]
    positions = copy.deepcopy(positions)
    initial_debt = sum(p["debt_usdc"] for p in positions)
    initial_collateral = sum(p["collateral_pt"] for p in positions)
    sold = repaid_total = bad_debt = 0.0
    steps = []
    stop_reason = "maximum de tours atteint"
    reference_sale_price = points[0][1] / points[0][0] * (1 - scenario.sale_shock)
    for round_no in range(1, scenario.max_rounds + 1):
        eligible = sorted((p for p in positions if p["collateral_pt"] > 1e-10 and
                           p["debt_usdc"] > p["collateral_pt"] * oracle * lltv + 1e-8),
                          key=lambda p: (-p["debt_usdc"] / (p["collateral_pt"] * oracle), p["address"]))
        if not eligible:
            stop_reason = "aucune position au-dessus du seuil de liquidation"
            break
        actions, skipped = [], []
        for p in eligible:
            if len(actions) >= scenario.max_sales_per_round:
                skipped.append({"address": p["address"], "reason": "reportée au tour suivant"})
                continue
            before_debt, before_coll = p["debt_usdc"], p["collateral_pt"]
            action, reason = _choose_liquidation(p, oracle, lltv, points, sold, scenario)
            if not action:
                skipped.append({"address": p["address"], "reason": reason})
                continue
            p["debt_usdc"] = max(0.0, before_debt - action["repaid_usdc"])
            p["collateral_pt"] = max(0.0, before_coll - action["seized_pt"])
            sold += action["seized_pt"]
            repaid_total += action["repaid_usdc"]
            if p["collateral_pt"] < 1e-8 and p["debt_usdc"] > 0:
                bad_debt += p["debt_usdc"]
                p["debt_usdc"] = 0.0  # dette retirée du marché lors de la liquidation totale
            action.update({"address": p["address"], "ltv_before": before_debt / (before_coll * oracle),
                           "bad_debt_usdc": max(0.0, before_debt - action["repaid_usdc"])
                           if p["collateral_pt"] < 1e-8 else 0.0,
                           "debt_after_usdc": p["debt_usdc"], "collateral_after_pt": p["collateral_pt"]})
            actions.append(action)
        # Ce lien prix de vente→oracle est un CONTREFACTUEL. Pour le marché réel,
        # oracle_feedback=0 et cette ligne ne peut jamais changer l'oracle.
        if actions and scenario.oracle_feedback:
            last_price = actions[-1]["sale_proceeds_usdc"] / actions[-1]["seized_pt"]
            drawdown = max(0.0, 1 - last_price / reference_sale_price)
            oracle = min(oracle, initial_oracle * (1 - scenario.oracle_shock) *
                         (1 - scenario.oracle_feedback * drawdown))
        steps.append({"round": round_no, "eligible_count": len(eligible), "oracle_price": oracle,
                      "actions": actions, "skipped": skipped, "sold_pt_cumulative": sold,
                      "quote_capacity_remaining_pt": max(0.0, points[-1][0] * scenario.liquidity_fraction - sold)})
        if not actions:
            stop_reason = "aucune liquidation exécutable et rentable"
            break
    remaining_debt = sum(p["debt_usdc"] for p in positions)
    remaining_collateral = sum(p["collateral_pt"] for p in positions)
    if abs(initial_debt - repaid_total - bad_debt - remaining_debt) > 0.01:
        raise ArithmeticError("Conservation de la dette violée")
    if abs(initial_collateral - sold - remaining_collateral) > 1e-5:
        raise ArithmeticError("Conservation des garanties violée")
    exposed = [p for p in positions if p["collateral_pt"] > 1e-10 and
               p["debt_usdc"] > p["collateral_pt"] * oracle * lltv + 1e-8]
    return {"scenario": vars(scenario), "snapshot_at": snapshot["collected_at"],
            "market_id": snapshot["market_id"], "lltv": lltv, "lif": liquidation_incentive(lltv),
            "initial_oracle_price": initial_oracle, "final_oracle_price": oracle,
            "quote_capacity_pt": points[-1][0] * scenario.liquidity_fraction,
            "quote_capacity_remaining_pt": max(0.0, points[-1][0] * scenario.liquidity_fraction - sold),
            "total_borrowers": len(positions), "liquidated_borrowers": len({a["address"] for s in steps for a in s["actions"]}),
            "exposed_borrowers_remaining": len(exposed),
            "exposed_positions": [{"address": p["address"], "debt_usdc": p["debt_usdc"],
                                   "collateral_pt": p["collateral_pt"],
                                   "ltv": p["debt_usdc"] / (p["collateral_pt"] * oracle)} for p in exposed],
            "sold_pt": sold,
            "repaid_usdc": repaid_total, "realized_bad_debt_usdc": bad_debt,
            "remaining_debt_usdc": remaining_debt, "remaining_collateral_pt": remaining_collateral,
            "initial_debt_usdc": initial_debt, "initial_collateral_pt": initial_collateral,
            "steps": steps, "stop_reason": stop_reason,
            "counterfactual_oracle_feedback": scenario.oracle_feedback > 0}
