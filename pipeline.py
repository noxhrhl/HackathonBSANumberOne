"""Chaîne complète : découvrir → enrichir → simuler → décider → rapport JSON.

  python pipeline.py pt-susde-26nov2026          # une position (en ligne, met le cache à jour)
  python pipeline.py --all --offline             # toutes les positions, depuis le cache
  python pipeline.py --all --offline --book      # + vue « livre » (10 poches)

Écrit data/graphs/<id>.json (le graphe découvert) et data/reports/<id>.json (le rapport).
La page web lit ces fichiers et recalcule à la volée quand on change l'incident ou p.
"""

import json
import sys
from pathlib import Path

import decide as D
import discover
import model as M
import rules
import sources as S

ROOT = Path(__file__).resolve().parent
GRAPHS = ROOT / "data" / "graphs"
REPORTS = ROOT / "data" / "reports"
BELEM_EXIT = 0.11          # énoncé : « unwinding it costs 11 % in slippage »
DEFAULT_INCIDENT_P = 0.30  # probabilité par défaut qu'une alerte devienne un dommage (entrée du gérant)
QUOTE_FRACTIONS = (0.25, 0.5, 1.0, 1.5, 2.5)
HOLD_NORMAL_EXIT = 0.001   # HYPOTHÈSE : 0,1 % hors crise pour un actif liquide sans cotation


def position_cfg(pid):
    return next(p for p in discover.POSITIONS["positions"] if p["id"] == pid)


# ---------------------------------------------------------------------------
# Coût de sortie
# ---------------------------------------------------------------------------

def exit_quotes_for(pid, graph=None):
    """Cotations réelles (hors crise) : coût total de vente pour plusieurs tailles."""
    pos = position_cfg(pid)
    out = {"type": pos["type"], "quotes": [], "capacity": 1.0, "source": None}
    if pos["type"] == "pendle_pt":
        m = discover.pendle_market_for_pt(pos["chain_id"], pos["pt"])
        under = S.strip_chain(m["underlyingAsset"])
        prices = S.pendle_prices(pos["chain_id"], [pos["pt"], under])
        pt_price = prices[pos["pt"].lower()]
        units = pos["size_usd"] / pt_price
        cap = None
        for f in QUOTE_FRACTIONS:
            q = S.pendle_quote(pos["chain_id"], pos["pt"], under, round(units * f))
            notional = round(units * f) * pt_price
            if q.get("ok"):
                cost = abs(float(q["priceImpact"])) + float(q["fee_usd"]) / notional
                out["quotes"].append({"fraction": f, "size_usd": notional, "cost": cost,
                                      "price_impact": float(q["priceImpact"]), "fee_usd": float(q["fee_usd"])})
            else:
                out["quotes"].append({"fraction": f, "size_usd": notional, "cost": None, "error": q.get("error", "")[:160]})
                cap = f if cap is None else cap
        ok = [q for q in out["quotes"] if q["cost"] is not None]
        out["capacity_fraction_normal"] = max((q["fraction"] for q in ok), default=0.0)
        out["refused_from_fraction"] = cap
        out["source"] = "API Pendle « convert » (cotation réelle, hors crise)"
    elif pos["type"] in ("aave_supply", "morpho_supply"):
        g = graph or json.loads((GRAPHS / f"{pid}.json").read_text(encoding="utf-8"))
        avail = g["position"]["info"].get("available_liquidity_usd") or 0.0
        out["available_liquidity_usd"] = avail
        out["capacity_fraction_normal"] = min(1.0, avail / pos["size_usd"]) if pos["size_usd"] else 1.0
        out["quotes"].append({"fraction": 1.0, "size_usd": pos["size_usd"], "cost": 0.0,
                              "note": "retrait sans glissement tant que la liquidité disponible suffit"})
        out["source"] = "liquidité disponible (API Aave / Morpho)"
    else:
        out["quotes"].append({"fraction": 1.0, "size_usd": pos["size_usd"], "cost": HOLD_NORMAL_EXIT,
                              "note": "HYPOTHÈSE : pas de cotation automatique pour cet actif"})
        out["capacity_fraction_normal"] = 1.0
        out["source"] = "HYPOTHÈSE"
    return out


def exit_models(quotes):
    """Deux régimes : crise (Belem, 11 %) et hors crise (cotation réelle)."""
    stress = D.ExitCost(BELEM_EXIT, 1.0, "Crise — chiffre de Belem : 11 % pour tout vendre", "quadratic")
    full = next((q for q in quotes["quotes"] if q["fraction"] == 1.0 and q["cost"] is not None), None)
    c1 = full["cost"] if full else BELEM_EXIT
    pts = [(q["fraction"], q["cost"] * q["fraction"]) for q in quotes["quotes"]
           if q["cost"] is not None and q["fraction"] <= 1.0]
    normal = D.ExitCost(c1, 1.0, f"Hors crise — cotation réelle : {c1:.2%} pour tout vendre",
                        "points" if len(pts) >= 2 else "quadratic", pts)
    return {"stress": stress, "normal": normal}


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def default_incident(graph):
    """Incident par défaut. PT : l'exemple de Belem, l'oracle qui prix l'actif (couche 3).
    Autres positions : l'émetteur de l'actif détenu (alerte « émetteur »)."""
    pos = graph["position"]["id"]
    if graph["position"].get("type") != "pendle_pt":
        issuers = [n for n in graph["nodes"] if n["kind"] == "issuer"]
        direct = {e["from"] for e in graph["edges"]}
        if issuers:
            pm = M.PositionModel(graph, "base", "max")
            return max(issuers, key=lambda n: pm.impact.get(n["id"], 0))["id"]
    oracles = [n for n in graph["nodes"] if n["kind"] == "oracle"]
    edges_to_lending = {e["from"] for e in graph["edges"] if e["mechanism"] == "pricing"}
    top = [n for n in oracles if n["id"] in edges_to_lending and any(
        e["from"] == n["id"] and e["to"].startswith("lending:") for e in graph["edges"])]
    pick = (top or oracles or [n for n in graph["nodes"] if n["kind"] == "issuer"] or
            [n for n in graph["nodes"] if n["id"] != pos])[0]
    return pick["id"]


def build_report(graph, incident=None, incident_p=DEFAULT_INCIDENT_P):
    pid = graph["position_id"]
    incident = incident or default_incident(graph)
    pm = M.PositionModel(graph, "base", "max")
    pm_sum = M.PositionModel(graph, "base", "sum")
    quotes = exit_quotes_for(pid, graph)
    models = exit_models(quotes)
    st = M.stats(pm.losses(incident, incident_p))
    decision = {v: D.decide(st, models["stress"], v) for v in ("neutre", "prudent")}
    sweep = D.p_sweep(pm, incident, models["stress"], "neutre", step=5)
    nodes = []
    for n in graph["nodes"]:
        if n["id"] == graph["position"]["id"]:
            continue
        prm = pm.params[n["id"]]
        nodes.append({"id": n["id"], "name": n["name"], "kind": n["kind"], "p": prm["p"], "lam": prm["lam"],
                      "sev": prm["sev"], "lam_range": prm["lam_range"], "sev_range": prm["sev_range"],
                      "impact_max": pm.impact.get(n["id"], 0.0), "impact_sum": pm_sum.impact.get(n["id"], 0.0),
                      "assumption": prm["assumption"], "why": prm["why"], "flags": n.get("flags", [])})
    nodes.sort(key=lambda x: -x["impact_max"])
    differs = [x for x in nodes if abs(x["impact_sum"] - x["impact_max"]) > 0.005]
    edges = [{"from": e["from"], "to": e["to"], "mechanism": e["mechanism"], "status": e["status"],
              **pm.weight_info[(e["from"], e["to"])]} for e in graph["edges"]]
    return {
        "position_id": pid, "label": graph["position"]["label"], "size_usd": graph["position"]["size_usd"],
        "generated_at": S.now_iso(), "offline": S.is_offline(), "data_fetched_at": data_timestamp(graph),
        "horizon_days": pm.horizon, "coverage": graph["checks"]["coverage"], "errors": graph["checks"]["errors"],
        "loops": graph.get("loops", []), "default_incident": incident, "default_incident_p": incident_p,
        "stats": st, "decision": decision, "exit": {"quotes": quotes, "stress": models["stress"].to_dict(),
                                                     "normal": models["normal"].to_dict()},
        "p_sweep": sweep, "stability": D.stability_sentence(sweep, incident_p),
        "scenarios": D.scenario_table(graph, incident, incident_p, models["stress"]),
        "sensitivity": D.sensitivity(graph, incident, incident_p, models["stress"]),
        "propagation": {"nodes_where_sum_differs": [{"id": x["id"], "name": x["name"], "max": x["impact_max"],
                                                     "sum": x["impact_sum"]} for x in differs],
                        "incident_max": pm.impact.get(incident, 0.0), "incident_sum": pm_sum.impact.get(incident, 0.0)},
        "nodes": nodes, "edges": edges,
    }


def data_timestamp(graph):
    ts = sorted(s["fetched_at"] for s in graph["meta"]["sources"] if s.get("fetched_at") not in (None, "?"))
    return {"oldest": ts[0] if ts else None, "newest": ts[-1] if ts else None}


# ---------------------------------------------------------------------------
# Livre (10 poches)
# ---------------------------------------------------------------------------

def load_graph(pid):
    return json.loads((GRAPHS / f"{pid}.json").read_text(encoding="utf-8"))


def book_exposure(graphs, node_id, incident_p, view="neutre"):
    """Une défaillance de `node_id` : quelles poches touchées, quelle perte pour le livre ?"""
    n_pockets = len(graphs)
    weight = 1.0 / n_pockets
    per, book = [], [0.0] * M.N_SIM
    for g in graphs:
        pm = M.PositionModel(g, "base", "max")
        touched = node_id in pm.params and pm.impact.get(node_id, 0) > 0
        losses = pm.losses(node_id if touched else None, incident_p if touched else None)
        st = M.stats(losses)
        for s in range(M.N_SIM):
            book[s] += weight * losses[s]
        per.append({"position_id": g["position_id"], "label": g["position"]["label"], "touched": touched,
                    "impact": pm.impact.get(node_id, 0.0) if touched else 0.0, "expected": st["expected"],
                    "cvar95": st["cvar95"]})
    bst = M.stats(book)
    return {"node": node_id, "incident_p": incident_p, "pockets": per,
            "touched_share": sum(weight for x in per if x["touched"]), "book": bst}


def shared_nodes(graphs):
    seen = {}
    for g in graphs:
        for n in g["nodes"]:
            if n["kind"] == "position":
                continue
            seen.setdefault(n["id"], {"id": n["id"], "name": n["name"], "kind": n["kind"], "pockets": []})
            seen[n["id"]]["pockets"].append(g["position_id"])
    return sorted([v for v in seen.values() if len(v["pockets"]) >= 2], key=lambda v: (-len(v["pockets"]), v["name"]))


def concentration(graphs):
    """Protocoles qui pèsent une grosse part de la TVL d'Ethereum (DeFiLlama)."""
    chain_tvl = S.llama_chain_tvl("Ethereum")
    out, seen = [], set()
    for g in graphs:
        for n in g["nodes"]:
            eth = (n.get("metrics") or {}).get("ethereum_tvl_usd")
            if eth and n["id"] not in seen and chain_tvl:
                seen.add(n["id"])
                out.append({"id": n["id"], "name": n["name"], "ethereum_tvl_usd": eth, "share_of_chain": eth / chain_tvl})
    out.sort(key=lambda x: -x["share_of_chain"])
    return {"chain_tvl_usd": chain_tvl, "protocols": out, "flag_threshold": 0.10}


def build_book(pids):
    graphs = [load_graph(p) for p in pids]
    shared = shared_nodes(graphs)
    top = [s["id"] for s in shared[:8]]
    return {"generated_at": S.now_iso(), "pockets": [{"id": g["position_id"], "label": g["position"]["label"]} for g in graphs],
            "shared_nodes": shared, "concentration": concentration(graphs),
            "exposures": {nid: book_exposure(graphs, nid, DEFAULT_INCIDENT_P) for nid in top}}


def run(pid, offline):
    S.set_offline(offline)
    g = discover.discover(pid)
    GRAPHS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    (GRAPHS / f"{pid}.json").write_text(json.dumps(g, indent=1, ensure_ascii=False), encoding="utf-8")
    rep = build_report(g)
    (REPORTS / f"{pid}.json").write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    return g, rep


def main(argv):
    offline = "--offline" in argv
    ids = [a for a in argv if not a.startswith("--")]
    if "--all" in argv or not ids:
        ids = [p["id"] for p in discover.POSITIONS["positions"]]
    for pid in ids:
        g, rep = run(pid, offline)
        cov = rep["coverage"]
        d = rep["decision"]["neutre"]
        print(f"{pid:24s} {cov['dependencies']:3d} dépendances, profondeur {cov['max_depth']}, "
              f"{cov['verified_share']:.0%} non supposées | incident {rep['default_incident'][:40]} "
              f"p={rep['default_incident_p']:.0%} → E[L]={rep['stats']['expected']:.2%} "
              f"CVaR={rep['stats']['cvar95']:.2%} x*={d['x_star']:.0%} ({d['category']})"
              + (f" ERREURS {rep['errors']}" if rep["errors"] else ""))
    if "--book" in argv:
        S.set_offline(offline)
        book = build_book([p["id"] for p in discover.POSITIONS["positions"]])
        (REPORTS / "book.json").write_text(json.dumps(book, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"livre : {len(book['shared_nodes'])} nœuds partagés ; concentration :",
              ", ".join(f"{c['name']} {c['share_of_chain']:.0%}" for c in book["concentration"]["protocols"][:4]))


if __name__ == "__main__":
    main(sys.argv[1:])
