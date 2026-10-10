"""Décision : rester, vendre une partie, ou tout vendre — et pourquoi.

Idée clé : sur un AMM, vendre plus coûte plus cher PAR DOLLAR (l'impact de prix grandit avec la
taille). Le coût total de sortie C(x) pour vendre une fraction x de la position est convexe.

Modèle quadratique (1re approximation, explicable) : C(x) = c · x², calibré par C(1).
  - Défaut : C(1) = 11 % (chiffre de Belem pour une sortie en crise).
  - On affiche aussi la cotation RÉELLE de Pendle hors crise (impact de prix pour notre taille).
Pourquoi x² : l'impact de prix par dollar est à peu près proportionnel à la taille vendue
(courbe à produit constant, petits ordres) ; coût total = taille × impact ∝ x².

Coût de garder la partie restante : (1 − x) · L, où L = perte attendue (vue neutre)
ou CVaR 95 (vue prudente) issue du Monte Carlo.
Fraction optimale : x* = argmin C(x) + (1 − x) · L. Forme fermée pour C = c·x² :
dérivée 2cx − L = 0  ⇒  x* = L / (2c), bornée à [0, 1] (et à la capacité de sortie).
Exemple : L = 6 %, c = 11 % ⇒ x* = 0,06 / 0,22 = 27 % ; coût = 0,11·0,27² + 0,73·6 % = 5,2 %
contre 6 % (tout garder) et 11 % (tout vendre).
"""

import math

import model as M

GRID = [i / 100 for i in range(101)]
STAY_MAX = 0.05     # en dessous : « garder »
EXIT_MIN = 0.95     # au-dessus : « tout vendre »


class ExitCost:
    """C(x) : coût total de sortie (fraction de la position) pour vendre la fraction x."""

    def __init__(self, c1=0.11, capacity=1.0, label="Belem (crise) : 11 % pour tout vendre", shape="quadratic",
                 points=None):
        self.c1 = c1
        self.capacity = capacity
        self.label = label
        self.shape = shape
        self.points = points or []

    def __call__(self, x):
        if x > self.capacity + 1e-9:
            return math.inf
        if self.shape == "quadratic":
            return self.c1 * x * x
        # interpolation linéaire sur des points mesurés (x, coût total)
        pts = [(0.0, 0.0)] + sorted(self.points)
        for (x0, c0), (x1, c1) in zip(pts, pts[1:]):
            if x <= x1:
                return c0 + (c1 - c0) * (x - x0) / (x1 - x0) if x1 > x0 else c1
        return pts[-1][1] * (x / pts[-1][0]) ** 2

    def to_dict(self):
        return {"c1": self.c1, "capacity": self.capacity, "label": self.label, "shape": self.shape,
                "points": self.points}


def optimal_fraction(L, cost: ExitCost):
    """x* par recherche sur grille (et forme fermée quand C est quadratique)."""
    grid = [x for x in GRID if x <= cost.capacity + 1e-9]
    best = min(grid, key=lambda x: cost(x) + (1 - x) * L)
    closed = None
    if cost.shape == "quadratic" and cost.c1 > 0:
        closed = min(max(L / (2 * cost.c1), 0.0), cost.capacity)
        # la forme fermée est exacte : on la garde si elle est meilleure que le point de grille
        if cost(closed) + (1 - closed) * L <= cost(best) + (1 - best) * L:
            best = closed
    return {"x": best, "closed_form": closed}


def strategy_costs(x, stat, cost: ExitCost):
    return {"x": x, "expected": cost(x) + (1 - x) * stat["expected"],
            "worst5": cost(x) + (1 - x) * stat["cvar95"], "exit_cost": cost(x)}


def category(x):
    return "rester" if x < STAY_MAX else ("tout vendre" if x > EXIT_MIN else "vente partielle")


def decide(stat, cost: ExitCost, view="neutre"):
    """Compare garder tout / vendre x* / tout vendre, pour une vue neutre ou prudente."""
    L = stat["expected"] if view == "neutre" else stat["cvar95"]
    opt = optimal_fraction(L, cost)
    x_star = opt["x"]
    x_all = min(1.0, cost.capacity)
    strategies = {
        "rester": strategy_costs(0.0, stat, cost),
        "partiel": strategy_costs(x_star, stat, cost),
        "tout_vendre": strategy_costs(x_all, stat, cost),
    }
    key = "expected" if view == "neutre" else "worst5"
    best_extreme = min(strategies["rester"][key], strategies["tout_vendre"][key])
    return {"view": view, "L": L, "x_star": x_star, "closed_form": opt["closed_form"],
            "category": category(x_star), "strategies": strategies,
            "saving_vs_best_extreme": best_extreme - strategies["partiel"][key]}


# ---------------------------------------------------------------------------
# Robustesse
# ---------------------------------------------------------------------------

def p_sweep(pm: M.PositionModel, incident, cost: ExitCost, view="neutre", step=1):
    """x* en fonction de incident_p (0 → 100 %), avec les mêmes tirages aléatoires."""
    rows = []
    for k in range(0, 101, step):
        st = M.stats(pm.losses(incident, k / 100))
        d = decide(st, cost, view)
        rows.append({"p": k / 100, "x_star": d["x_star"], "category": d["category"],
                     "expected": st["expected"], "cvar95": st["cvar95"]})
    return rows


def stability_sentence(rows, current_p):
    """Phrase « La recommandation tient pour p entre A % et B % »."""
    cur = min(rows, key=lambda r: abs(r["p"] - current_p))
    cat = cur["category"]
    idx = rows.index(cur)
    lo = idx
    while lo > 0 and rows[lo - 1]["category"] == cat:
        lo -= 1
    hi = idx
    while hi < len(rows) - 1 and rows[hi + 1]["category"] == cat:
        hi += 1
    a, b = rows[lo]["p"], rows[hi]["p"]
    first_partial = next((r["p"] for r in rows if r["category"] != "rester"), None)
    first_all = next((r["p"] for r in rows if r["category"] == "tout vendre"), None)
    return {"category": cat, "from_p": a, "to_p": b, "first_partial_p": first_partial, "first_all_p": first_all}


def _maker(graph, make):
    return make or (lambda sc, prop, **ov: M.PositionModel(graph, sc, prop, overrides=ov or None))


def scenario_table(graph, incident, incident_p, cost: ExitCost, propagation="max", make=None):
    """Décision sous les trois jeux de paramètres BAS / BASE / HAUT (tous en même temps)."""
    make = _maker(graph, make)
    out = {}
    for sc in ("low", "base", "high"):
        pm = make(sc, propagation)
        st = M.stats(pm.losses(incident, incident_p))
        out[sc] = {"stats": st, "neutre": decide(st, cost, "neutre"), "prudent": decide(st, cost, "prudent"),
                   "impact": pm.impact.get(incident, 0.0)}
    return out


def sensitivity(graph, incident, incident_p, cost: ExitCost, propagation="max", make=None):
    """Un paramètre à la fois en BAS puis HAUT : lequel fait le plus bouger x* ?"""
    make = _maker(graph, make)
    base_pm = make("base", propagation)
    base_st = M.stats(base_pm.losses(incident, incident_p))
    base_x = decide(base_st, cost)["x_star"]
    tests = []

    def run(label, pm=None, p=None, c=None, sev=None):
        pm = pm or base_pm
        st = M.stats(pm.losses(incident, incident_p if p is None else p, sev))
        d = decide(st, c or cost)
        return {"label": label, "x_star": d["x_star"], "expected": st["expected"]}

    sev_rng = base_pm.params[incident]["sev_range"]
    pairs = [
        ("Sévérité de l'incident", run("bas", sev=sev_rng[0]), run("haut", sev=sev_rng[2])),
        ("Poids des arêtes (w)", run("bas", pm=make("base", propagation, w_scenario="low")),
         run("haut", pm=make("base", propagation, w_scenario="high"))),
        ("Probabilités de fond (p_base)", run("bas", pm=make("base", propagation, p_scenario="low")),
         run("haut", pm=make("base", propagation, p_scenario="high"))),
        ("Coût de sortie C(1) ×0,5 / ×1,5", run("bas", c=ExitCost(cost.c1 * 0.5, cost.capacity, cost.label, cost.shape, cost.points)),
         run("haut", c=ExitCost(cost.c1 * 1.5, cost.capacity, cost.label, cost.shape, cost.points))),
        ("Probabilité de l'incident ±20 pts", run("bas", p=max(0.0, incident_p - 0.2)), run("haut", p=min(1.0, incident_p + 0.2))),
    ]
    for name, lo, hi in pairs:
        tests.append({"input": name, "low": lo, "high": hi,
                      "swing_x": abs(hi["x_star"] - lo["x_star"]), "swing_cost": abs(hi["expected"] - lo["expected"])})
    tests.sort(key=lambda t: (-t["swing_x"], -t["swing_cost"]))
    return {"base_x": base_x, "tests": tests, "most_sensitive": tests[0]["input"]}
