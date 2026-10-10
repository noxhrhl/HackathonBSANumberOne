"""Calcul minimal d'un scénario de risque, indépendant de l'interface."""

from math import isfinite


def assess(position_usd: float, loss_percent: float, exit_percent: float) -> dict:
    """Compare une perte conditionnelle et le coût d'une sortie immédiate.

    loss_percent est la perte *si* l'incident arrive, pas sa probabilité.
    Les pourcentages s'appliquent tous deux à la valeur actuelle de la position.
    """
    values = (position_usd, loss_percent, exit_percent)
    if not all(isfinite(value) for value in values):
        raise ValueError("Les valeurs doivent être des nombres finis.")
    if position_usd <= 0:
        raise ValueError("La valeur de la position doit être supérieure à zéro.")
    if not 0 <= loss_percent <= 100 or not 0 <= exit_percent <= 100:
        raise ValueError("Les pourcentages doivent être compris entre 0 et 100.")

    scenario_loss = position_usd * loss_percent / 100
    exit_cost = position_usd * exit_percent / 100
    break_even_probability = (
        100 * exit_cost / scenario_loss if scenario_loss > 0 else None
    )
    return {
        "scenario_loss_usd": scenario_loss,
        "exit_cost_usd": exit_cost,
        "break_even_probability_percent": break_even_probability,
    }


# ---------------------------------------------------------------------------
# Modèle par graphe : un incident se propage le long des dépendances
# ---------------------------------------------------------------------------
import json
import random

# Règles par type de nœud : p = probabilité de défaillance en temps normal,
# sev = perte moyenne de valeur du nœud s'il défaille. À justifier par des
# incidents passés (voir NOTES.md). Un nœud peut surcharger p/sev dans le JSON.
NODE_RULES = {
    "chain": {"p": 0.001, "sev": 0.30},
    "issuer": {"p": 0.010, "sev": 0.50},
    "asset": {"p": 0.010, "sev": 0.50},
    "oracle": {"p": 0.010, "sev": 0.50},
    "venue": {"p": 0.010, "sev": 0.20},
    "lending": {"p": 0.010, "sev": 0.30},
}
DEFAULT_RULE = {"p": 0.010, "sev": 0.30}


def load_graph(path) -> dict:
    """Lit un graphe {position, nodes, edges} au format JSON."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def node_params(graph: dict, node: str) -> dict:
    """p et sev d'un nœud : règle de son type, surchargée par le nœud lui-même."""
    data = graph["nodes"][node]
    params = dict(NODE_RULES.get(data.get("kind"), DEFAULT_RULE))
    params.update({k: data[k] for k in ("p", "sev") if k in data})
    return params


def impact_factor(graph: dict, node: str) -> float:
    """Part d'une défaillance de `node` qui atteint la position (chemin le plus fort)."""
    target = graph["position"]
    out = {}
    for e in graph["edges"]:
        out.setdefault(e["from"], []).append((e["to"], float(e["w"])))

    def best(current, seen):
        if current == target:
            return 1.0
        return max(
            (w * best(nxt, seen | {nxt}) for nxt, w in out.get(current, []) if nxt not in seen),
            default=0.0,
        )

    return best(node, {node})


def simulate_graph(graph, incident_node, incident_p, n=20_000, seed=42):
    """Monte Carlo : renvoie n pertes simulées sur la position (entre 0 et 1)."""
    rng = random.Random(seed)
    nodes = [x for x, d in graph["nodes"].items() if d.get("kind") != "position"]
    setup = []
    for x in nodes:
        prm = node_params(graph, x)
        p = incident_p if x == incident_node else prm["p"]
        setup.append((p, min(max(prm["sev"], 0.001), 0.999), impact_factor(graph, x)))
    losses = []
    for _ in range(n):
        loss = 0.0
        for p, sev, impact in setup:
            if impact > 0 and rng.random() < p:
                loss += rng.betavariate(4.0, 4.0 * (1 - sev) / sev) * impact
        losses.append(min(loss, 1.0))
    return losses


def assess_graph(graph: dict, incident_node: str, incident_p_percent: float,
                 exit_percent: float) -> dict:
    """Rester ou sortir ? Compare la perte attendue simulée au coût de sortie."""
    if incident_node not in graph["nodes"] or graph["nodes"][incident_node].get("kind") == "position":
        raise ValueError("Choisissez un composant du graphe.")
    for value in (incident_p_percent, exit_percent):
        if not isfinite(value) or not 0 <= value <= 100:
            raise ValueError("Les pourcentages doivent être compris entre 0 et 100.")

    losses = sorted(simulate_graph(graph, incident_node, incident_p_percent / 100))
    expected = sum(losses) / len(losses)
    tail = losses[int(0.95 * len(losses)):]
    cvar95 = sum(tail) / len(tail)
    exit_cost = exit_percent / 100

    impact = impact_factor(graph, incident_node)
    sev = node_params(graph, incident_node)["sev"]
    break_even = 100 * exit_cost / (sev * impact) if sev * impact > 0 else None

    return {
        "impact_factor": impact,
        "expected_loss_percent": 100 * expected,
        "cvar95_percent": 100 * cvar95,
        "exit_percent": exit_percent,
        "break_even_probability_percent": break_even,
        "verdict": "SORTIR" if expected > exit_cost else "RESTER",
        "tail_warning": expected <= exit_cost < cvar95,
    }
