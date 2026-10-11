"""Règle « rester ou sortir » sans probabilité (docs/SIMULATION_MODEL.md, section 14).

La défaillance est supposée ; on décide à l'alerte, avant que le choc n'atteigne la position.

  rester   : perte de marché + perte de liquidation (simulées)
  vendre x : coût de vente de x sur l'AMM Pendle (pool actuel) + perte simulée sur le reste
  x*       : le moins cher sur x = 0 %, 5 %, …, 100 %, dans la limite de ce que l'AMM accepte

Le reste est re-simulé pour chaque x : un emprunteur qui vend une partie de sa garantie
rembourse sa dette. Un prêteur retire sans glissement, dans la limite de la liquidité.
"""

from . import simulate as SIM

GRID = [i / 20 for i in range(21)]


def _loss_stay(res):
    R = res["result"]
    if R.get("error"):
        return None
    return sum(m.get("loss_usd") or 0.0 for m in R["metrics"] if m["key"] in ("market", "liquidation"))


def _market_loss(res):
    return sum(m.get("loss_usd") or 0.0 for m in res["result"]["metrics"] if m["key"] == "market")


def _grid(cap):
    """x = 0 %, 5 %, …, jusqu'à la capacité ; la capacité elle-même est un point (sortie maximale possible)."""
    xs = [x for x in GRID if x <= cap + 1e-9]
    if cap < 1.0 and cap - xs[-1] > 1e-3:
        xs.append(cap)
    return xs


def _with_position(ctx, k=1.0, **override):
    pc = dict(ctx["position"])
    for key in ("size_usd", "units", "collateral_units", "debt_units"):
        if isinstance(pc.get(key), (int, float)):
            pc[key] = pc[key] * k
    pc.update(override)
    return {**ctx, "position": pc}


def decide(graph, ctx, scenario, node, severity, hours=SIM.DEFAULT_HOURS):
    """Compare rester, vendre une partie et tout vendre, en dollars, pour une défaillance supposée."""
    def sim(c):
        return SIM.simulate(graph, c, scenario, node, severity, hours, with_threshold=False)

    pc = ctx["position"]
    typ, size = pc["type"], pc["size_usd"]
    full = sim(ctx)
    stay = _loss_stay(full)
    if stay is None:
        return {"ok": False, "why": full["result"].get("error") or "simulation impossible"}
    unknown = list(full["result"].get("unknown") or [])
    rows, cap, kind, note = [], 1.0, None, None

    if typ in ("pendle_pt", "morpho_borrow"):
        kind = "vendre"
        eng = SIM.Engine(graph, ctx, scenario, node, severity, hours)
        eng.run()
        venue = pc.get("venue")
        sy_p, sy_node = eng._sy_price(venue)
        if typ == "pendle_pt":
            units, unit_ref = pc.get("units"), ctx["prices_usd"].get(graph["position"]["id"])
        else:
            units, unit_ref = pc.get("collateral_units"), ctx["prices_usd"].get(pc.get("collateral_node"))
        mk = eng.market_for(venue, baseline=True)
        if not (units and unit_ref and sy_p and mk):
            return {"ok": False, "why": "pas de modèle de vente pour cette position (pool Pendle ou prix manquant)"}
        cap = min(1.0, mk.max_pt_sellable() * 0.999 / units)
        m_after = eng.m_of(sy_node) if sy_node else 1.0
        p_debt = ctx["prices_usd"].get(pc.get("debt_node"), 1.0)
        for x in _grid(cap):
            sold = x * units
            proceeds = after = 0.0
            if x > 0:
                ex = eng.exit_value(venue, sold, sy_p, baseline=True)
                proceeds = ex["proceeds_usd"] if ex else 0.0
                ex_a = eng.exit_value(venue, sold, sy_p * m_after, baseline=False)
                after = (sold * unit_ref * m_after - ex_a["proceeds_usd"]) if ex_a and ex_a["full_exit"] else None
            exec_cost = sold * unit_ref - proceeds
            if typ == "pendle_pt":
                rest = _loss_stay(sim(_with_position(ctx, 1 - x))) if x < 1 else 0.0
            else:
                debt_left = max(0.0, pc["debt_units"] - proceeds / p_debt)
                if x >= 1:
                    rest = 0.0
                elif debt_left <= 1e-6 * max(pc["debt_units"], 1.0):
                    # dette remboursée : plus de liquidation possible, il reste la baisse de la garantie
                    rest = _market_loss(full) * (1 - x)
                else:
                    rest = _loss_stay(sim(_with_position(ctx, 1.0, collateral_units=units * (1 - x),
                                                         debt_units=debt_left)))
            rows.append({"x": x, "exec": exec_cost, "exec_after": after, "rest": rest or 0.0,
                         "total": exec_cost + (rest or 0.0)})
        if cap < 1.0:
            note = f"le marché Pendle n'accepte que {cap:.0%} de la position d'un coup ; le reste est bloqué jusqu'à l'échéance"
    elif typ in ("aave_supply", "morpho_supply"):
        kind = "retirer"
        avail = pc.get("available_liquidity_usd")
        cap = min(1.0, avail / size) if avail else 1.0
        market = _market_loss(full)
        for x in _grid(cap):
            rest = _loss_stay(sim(_with_position(ctx, 1 - x))) if x < 1 else 0.0
            # l'argent retiré reste dans l'actif prêté : il subit encore sa baisse de prix
            rows.append({"x": x, "exec": x * market, "exec_after": None, "rest": rest or 0.0,
                         "total": x * market + (rest or 0.0)})
        if market > 0:
            note = "retirer évite la créance douteuse, pas la baisse de l'actif prêté (il faut l'échanger pour s'en protéger)"
        if cap < 1.0:
            note = (note + " ; " if note else "") + f"seuls {cap:.0%} sont retirables maintenant (liquidité disponible)"
    else:
        return {"ok": False, "why": f"pas de règle de sortie pour ce type de position ({typ})"}

    if not rows:
        return {"ok": False, "why": "aucune sortie possible maintenant"}
    best = min(rows, key=lambda r: (round(r["total"], 2), r["x"]))
    if best["total"] >= rows[0]["total"] - 1.0:   # gain < 1 $ : rester
        best = rows[0]
    last = rows[-1]
    category = "rester" if best["x"] == 0 else ("tout" if best["x"] >= cap - 1e-9 else "partiel")
    if category == "rester" and abs(rows[0]["total"]) < 1.0 and unknown:
        category = "inconnu"   # aucune perte chiffrée, mais des effets non chiffrés : on ne conclut pas
    saved = rows[0]["total"] - best["rest"]
    breakeven = (best["exec"] / saved) if best["x"] > 0 and saved > 0 else None
    return {"ok": True, "kind": kind, "type": typ, "size": size, "stay": rows[0]["total"], "best": best, "all": last,
            "rows": rows, "cap": cap, "sim_total": (full["result"].get("total") or {}).get("loss_usd"), "category": category, "note": note, "unknown": unknown,
            "breakeven_p": breakeven, "scenario_label": full["scenario_label"], "node_name": full["node_name"],
            "severity": severity}
