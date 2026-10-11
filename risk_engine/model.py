"""Facteurs d'impact et paramètres de risque par composant (bibliothèque standard uniquement).

Utilisé par la page « Analyse globale des risques » (global_risk/analysis.py), qui affiche
pour chaque composant sa sévérité, son facteur d'impact et le chemin jusqu'à la position,
sans jamais les additionner.

Facteur d'impact I(n) : part d'une défaillance du nœud n qui arrive jusqu'à la position.
  - « max » (par défaut) : produit des poids w sur le chemin le plus fort.
    Exemple : oracle → marché de prêt (0,3) → AMM (0,3) → position (0,1) : I = 0,009.
    Limite : ignore qu'un dommage peut arriver par plusieurs chemins à la fois.
  - « sum » : somme des produits sur tous les chemins, plafonnée à 1 (peut compter deux fois
    un même dommage).
"""

from datetime import datetime, timezone

from . import rules


def horizon_days(graph: dict, today=None) -> float:
    """Horizon de décision : jusqu'à l'échéance pour un PT, sinon 30 jours."""
    info = graph["position"].get("info") or {}
    if info.get("expiry"):
        today = today or datetime.now(timezone.utc)
        exp = datetime.fromisoformat(info["expiry"].replace("Z", "+00:00"))
        return max(1.0, (exp - today).total_seconds() / 86400)
    return 30.0


def out_edges(graph):
    out = {}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append(e)
    return out


def impact_factors(graph: dict, weights: dict, mode: str = "max") -> dict:
    """I(n) pour chaque nœud, avec la meilleure route (mode max) pour l'affichage."""
    target = graph["position"]["id"]
    out = out_edges(graph)
    memo, route = {}, {}

    def visit(n):
        if n == target:
            return 1.0
        if n in memo:
            return memo[n]
        best, best_e, total = 0.0, None, 0.0
        for e in out.get(n, []):
            val = weights[(e["from"], e["to"])] * visit(e["to"])
            total += val
            if val > best:
                best, best_e = val, e
        memo[n] = best if mode == "max" else min(total, 1.0)
        route[n] = best_e
        return memo[n]

    for nd in graph["nodes"]:
        visit(nd["id"])
    return memo, route


def best_path(graph, route, start):
    path, cur, target = [start], start, graph["position"]["id"]
    while cur != target and route.get(cur):
        cur = route[cur]["to"]
        path.append(cur)
    return path


class PositionModel:
    """Poids des arêtes, facteurs d'impact et paramètres (p, sev) d'une position, en BAS / BASE / HAUT."""

    def __init__(self, graph, scenario="base", propagation="max", horizon=None):
        self.graph = graph
        self.scenario = scenario
        self.propagation = propagation
        self.horizon = horizon if horizon is not None else horizon_days(graph)
        self.weights = {(e["from"], e["to"]): rules.edge_weight(e, scenario)["w"] for e in graph["edges"]}
        self.impact, self.route = impact_factors(graph, self.weights, propagation)
        pos = graph["position"]["id"]
        self.params = {nd["id"]: rules.node_params(nd, self.horizon, scenario)
                       for nd in graph["nodes"] if nd["id"] != pos}
