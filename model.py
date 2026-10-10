"""Propagation d'un incident et simulation Monte Carlo (bibliothèque standard uniquement).

1. Facteur d'impact I(n) : part d'une défaillance du nœud n qui arrive jusqu'à la position.
   - « max » (par défaut) : produit des poids w sur le chemin le plus fort.
     Exemple : oracle → marché de prêt (0,3) → AMM (0,3) → position (0,1) : I = 0,009.
     Limite : ignore qu'un dommage peut arriver par plusieurs chemins à la fois.
   - « sum » : somme des produits sur tous les chemins, plafonnée à 1 (prudent ; peut
     compter deux fois un même dommage).
2. Monte Carlo : dans chaque scénario simulé, chaque nœud défaille avec sa probabilité p
   (Bernoulli) ; s'il défaille, sa perte suit une loi Bêta de moyenne sev. La perte de la
   position est la somme de sev × I sur les nœuds défaillants, plafonnée à 100 %.
3. Nombres aléatoires communs : les tirages d'un nœud dépendent seulement de son identifiant.
   Changer p, ou combiner plusieurs positions dans un livre, réutilise les mêmes tirages :
   les comparaisons sont stables, et deux positions qui partagent un nœud défaillent ensemble.
"""

import random
import zlib
from datetime import datetime, timezone

import rules

N_SIM = 20_000
SEED = 2026
_Q_POINTS = 200          # quantiles tabulés de la loi Bêta
_BETA_CONCENTRATION = 4  # a = 4, b = 4(1 − sev)/sev : moyenne = sev

_draw_cache = {}
_quantile_cache = {}


def node_draws(node_id: str, n: int = N_SIM):
    """(U, V) : deux suites uniformes propres au nœud (U décide la défaillance, V la sévérité)."""
    key = (node_id, n)
    if key not in _draw_cache:
        rng = random.Random(SEED ^ zlib.crc32(node_id.encode()))
        u = [rng.random() for _ in range(n)]
        v = [rng.random() for _ in range(n)]
        _draw_cache[key] = (u, v)
    return _draw_cache[key]


def beta_quantiles(sev: float):
    """Table de quantiles d'une Bêta de moyenne sev (tirée une fois, puis réutilisée)."""
    sev = min(max(sev, 0.001), 0.999)
    key = round(sev, 6)
    if key not in _quantile_cache:
        rng = random.Random(SEED + int(sev * 1e6))
        a = _BETA_CONCENTRATION
        b = a * (1 - sev) / sev
        draws = sorted(rng.betavariate(a, b) for _ in range(4000))
        _quantile_cache[key] = [draws[int((i + 0.5) / _Q_POINTS * len(draws))] for i in range(_Q_POINTS)]
    return _quantile_cache[key]


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
    """Tout ce qu'il faut pour simuler une position dans un scénario BAS / BASE / HAUT."""

    def __init__(self, graph, scenario="base", propagation="max", horizon=None,
                 overrides=None, n=N_SIM):
        self.graph = graph
        self.scenario = scenario
        self.propagation = propagation
        self.n = n
        self.horizon = horizon if horizon is not None else horizon_days(graph)
        ov = overrides or {}
        self.weights, self.weight_info = {}, {}
        w_scen = ov.get("w_scenario", scenario)
        for e in graph["edges"]:
            info = rules.edge_weight(e, w_scen)
            self.weights[(e["from"], e["to"])] = info["w"]
            self.weight_info[(e["from"], e["to"])] = info
        self.impact, self.route = impact_factors(graph, self.weights, propagation)
        self.params = {}
        pos = graph["position"]["id"]
        for nd in graph["nodes"]:
            if nd["id"] == pos:
                continue
            prm = rules.node_params(nd, self.horizon, ov.get("p_scenario", scenario))
            sev_prm = rules.node_params(nd, self.horizon, ov.get("sev_scenario", scenario))
            prm["sev"] = sev_prm["sev"]
            self.params[nd["id"]] = prm
        self.node_by_id = {nd["id"]: nd for nd in graph["nodes"]}
        self._background = {}

    def components(self):
        return [k for k in self.params]

    def contribution(self, node_id, p=None, sev=None):
        """Liste des pertes (par scénario simulé) causées par ce seul nœud."""
        u, v = node_draws(node_id, self.n)
        prm = self.params[node_id]
        p = prm["p"] if p is None else p
        sev = prm["sev"] if sev is None else sev
        imp = self.impact.get(node_id, 0.0)
        if imp <= 0 or sev <= 0 or p <= 0:
            return None
        q = beta_quantiles(sev)
        m = len(q)
        return [q[int(v[s] * m)] * imp if u[s] < p else 0.0 for s in range(self.n)]

    def background(self, exclude):
        """Somme des pertes de tous les nœuds sauf `exclude`, à leur probabilité normale."""
        if exclude not in self._background:
            total = [0.0] * self.n
            for nid in self.params:
                if nid == exclude:
                    continue
                c = self.contribution(nid)
                if c is None:
                    continue
                for s in range(self.n):
                    if c[s]:
                        total[s] += c[s]
            self._background[exclude] = total
        return self._background[exclude]

    def losses(self, incident=None, incident_p=None, incident_sev=None):
        """Pertes simulées de la position (fractions entre 0 et 1)."""
        bg = self.background(incident)
        if incident is None:
            return [min(x, 1.0) for x in bg]
        c = self.contribution(incident, p=incident_p, sev=incident_sev)
        if c is None:
            return [min(x, 1.0) for x in bg]
        return [min(bg[s] + c[s], 1.0) for s in range(self.n)]


def stats(losses) -> dict:
    """Perte attendue, VaR 95 et CVaR 95 (moyenne des 5 % pires scénarios)."""
    srt = sorted(losses)
    n = len(srt)
    tail = srt[int(0.95 * n):]
    return {"expected": sum(srt) / n, "var95": srt[int(0.95 * n)], "cvar95": sum(tail) / len(tail),
            "p_any_loss": sum(1 for x in srt if x > 0) / n}
