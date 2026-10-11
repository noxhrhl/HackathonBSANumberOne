"""Profil de tous les risques du graphe, sans addition de causes corrélées.

Pour chaque composant : la perte *conditionnelle et isolée* (s'il défaille), sa
probabilité de défaillance sur l'horizon (repère de catégorie), et leur produit,
l'*espérance de perte* p × perte, qui sert au classement. Un même événement peut
traverser plusieurs nœuds : additionner ces espérances donnerait une perte globale
trompeuse, donc aucun total n'est calculé.
"""

from risk_engine import model, schema

GROUPS = {
    "asset": "Actifs sous-jacents", "issuer": "Émetteurs et garanties",
    "oracle": "Oracles", "lending": "Prêts et liquidations",
    "venue": "Liquidité de sortie", "chain": "Chaîne et règlement",
    "protocol": "Contrats et protocoles", "perp_exchange": "Couverture de l'émetteur",
    "custodian": "Dépositaires", "bridge": "Ponts",
    "admin": "Gouvernance et autres dépendances",
}

MECHANISMS = {
    "backing": "La valeur du sous-jacent se transmet à l'actif détenu.",
    "pricing": "Un prix utilisé par un protocole peut modifier ses décisions et liquidations.",
    "exit_liquidity": "La vente devient plus chère ou impossible si la liquidité disparaît.",
    "collateral_liquidation": "Des liquidations imposent des ventes et peuvent accentuer la décote.",
    "settlement": "Le règlement et les transactions peuvent être retardés ou bloqués.",
    "hedging": "La couverture de l'émetteur peut subir une perte.",
    "custody": "Les actifs gardés par ce dépositaire peuvent devenir indisponibles.",
    "bad_debt": "Une garantie insuffisante peut laisser une créance irrécouvrable.",
}


def _path_edges(graph, path):
    """Retrouve les arêtes du meilleur chemin, dans le sens du dommage."""
    lookup = {(e["from"], e["to"]): e for e in graph["edges"]}
    return [lookup[(a, b)] for a, b in zip(path, path[1:]) if (a, b) in lookup]


def _sources(node, edges):
    """Déduplique les preuves du composant et du chemin sans inventer de mesure."""
    found, seen = [], set()
    for item in [node, *edges]:
        for ev in item.get("evidence", []):
            key = (ev.get("source"), ev.get("url"), ev.get("fetched_at"))
            if key not in seen:
                seen.add(key)
                found.append({"source": ev.get("source") or "Source inconnue",
                              "url": ev.get("url") or "", "fetched_at": ev.get("fetched_at") or "?"})
    return found


def _limit(node, edges, sources, prm):
    """Nomme la principale limite propre à ce risque."""
    if not sources:
        return "Aucune preuve de découverte reliée à ce composant."
    if "undecoded" in node.get("flags", []):
        return "Contrat ou oracle non décodé : dépendances et comportement incomplets."
    if any(e.get("status") == "assumed" for e in edges):
        return "Au moins un lien du chemin est supposé, non vérifié."
    if prm["assumption"]:
        return "Fréquence et sévérité hypothétiques, sans calibration historique robuste."
    if node["kind"] == "oracle":
        return "Le modèle ne prouve pas que le prix de l'oracle suit le prix de vente."
    if node["kind"] == "venue":
        return "Cotation normale observée ; profondeur et vitesse de vente en crise inconnues."
    if node["kind"] == "lending":
        return "Le facteur de propagation simplifie liquidateurs, garanties et créances douteuses."
    return "Probabilité de catégorie, pas fréquence mesurée pour ce composant précis."


def analyze(graph, report, context=None, horizon=None, amount=None):
    """Produit un profil déterministe de chaque nœud, sans incident choisi.

    `graph`, `report` et `context` viennent exclusivement du relevé en mémoire
    fourni par app.ensure_live_data. `amount` est la taille choisie, validée par
    app.parse ; elle change les dollars, jamais les pourcentages. `horizon` fixe
    une date de comparaison en test.
    Aucun appel à PositionModel.losses(None) ni au simulateur de cascades.
    """
    horizon = report.get("horizon_days") if horizon is None else horizon
    if horizon is None:
        horizon = model.horizon_days(graph)
    reference_size = graph["position"].get("size_usd")
    size = reference_size if amount is None else amount
    scenarios = {s: model.PositionModel(graph, s, "max", horizon=horizon, n=1)
                 for s in ("low", "base", "high")}
    base = scenarios["base"]
    names = {n["id"]: n["name"] for n in graph["nodes"]}
    target = graph["position"]["id"]
    rows = []
    missing = list((context or {}).get("missing") or [])
    for name in (context or {}).get("assumed_prices") or []:
        missing.append(f"{name} : prix normalisé supposé, pas un prix observé")
    for node in graph["nodes"]:
        nid = node["id"]
        if nid == target:
            continue
        path = model.best_path(graph, base.route, nid)
        edges = _path_edges(graph, path)
        sources = _sources(node, edges)
        prm = base.params[nid]
        impact = base.impact.get(nid, 0.0)
        conditional = prm["sev"] * impact
        # p est utilisable comme repère de catégorie, jamais comme probabilité
        # d'incident spécifique. Une règle supposée n'a pas de p publiable.
        p = prm["p"] if sources and not prm["assumption"] else None
        expected = p * conditional if p is not None else None   # espérance de perte : p × perte si défaillance
        if not sources:
            missing.append(f"{node['name']} : preuve de découverte indisponible")
        if path[-1] != target:
            missing.append(f"{node['name']} : aucun chemin complet vers la position")
        if "undecoded" in node.get("flags", []):
            missing.append(f"{node['name']} : contrat ou oracle non décodé")
        rows.append({
            "id": nid, "name": node["name"], "kind": node["kind"],
            "group": GROUPS.get(node["kind"], "Autres dépendances"),
            "path": path, "path_names": [names.get(x, x) for x in path],
            "mechanisms": [e["mechanism"] for e in edges],
            "transmission": [MECHANISMS.get(e["mechanism"], e["mechanism"]) for e in edges],
            "impact_factor": impact, "severity_if_failure": prm["sev"],
            "conditional_loss_fraction": conditional,
            "conditional_loss_usd": conditional * size if size is not None else None,
            "expected_loss_fraction": expected,
            "expected_loss_usd": expected * size if expected is not None and size is not None else None,
            "probability": p,
            "probability_basis": "Paramètre historique ou heuristique de catégorie, non propre au composant" if p is not None else "Non estimable avec fiabilité",
            "sources": sources, "main_limit": _limit(node, edges, sources, prm),
            "edge_statuses": [e.get("status") for e in edges],
            "has_evidence": bool(sources), "assumption": prm["assumption"],
            "channel": "oracle" if node["kind"] == "oracle" else "sale" if node["kind"] == "venue" else "other",
        })
    # Classement par espérance de perte ; sans probabilité publiable, le composant passe après,
    # trié par perte conditionnelle (sa probabilité est « non estimable », pas nulle).
    rows.sort(key=lambda r: (r["expected_loss_fraction"] is None, -(r["expected_loss_fraction"] or 0),
                             -r["conditional_loss_fraction"], r["name"], r["id"]))
    sensitivity = []
    for label, pm in scenarios.items():
        values = [(n["id"], pm.params[n["id"]]["sev"] * pm.impact.get(n["id"], 0.0))
                  for n in graph["nodes"] if n["id"] != target]
        leader, loss = max(values, key=lambda x: x[1], default=(None, 0.0))
        sensitivity.append({"scenario": label, "leading_node": leader,
                            "leading_name": names.get(leader), "largest_single_loss_fraction": loss})
    checks = graph.get("checks") or {}
    for error in checks.get("errors") or []:
        missing.append(f"Validation du graphe : {error}")
    assumed = sum(e.get("status") == "assumed" for e in graph["edges"])
    if assumed:
        missing.append(f"{assumed} lien(s) supposé(s), à vérifier")
    fetched = (report.get("data_fetched_at") or {}).get("newest")
    if not fetched:
        missing.append("Date du relevé en ligne indisponible")
    quote = ((report.get("exit") or {}).get("quotes") or {})
    if quote and not any(q.get("fraction") == 1.0 and q.get("cost") is not None
                         for q in quote.get("quotes", [])):
        missing.append("Cotation de sortie à la taille entière indisponible")
    confidence = "faible" if missing else "partielle"
    if not rows:
        confidence = "données indisponibles"
        missing.append("Aucune dépendance découverte")
    strongest = max(rows, key=lambda r: r["conditional_loss_fraction"]) if rows else None
    likeliest = rows[0] if rows and rows[0]["expected_loss_fraction"] is not None else None
    core_groups = ("Actifs sous-jacents", "Émetteurs et garanties", "Oracles",
                   "Prêts et liquidations", "Liquidité de sortie", "Chaîne et règlement")
    absent_groups = [group for group in core_groups if not any(r["group"] == group for r in rows)]
    return {
        "position_id": graph.get("position_id") or graph["position"].get("config_id"),
        "position_name": graph["position"]["label"], "position_size_usd": size,
        "reference_size_usd": reference_size,
        "size_illustrative": True, "horizon_days": horizon, "fetched_at": fetched,
        "confidence": confidence, "rows": rows, "covered_node_ids": [r["id"] for r in rows],
        "coverage": checks.get("coverage") or schema.coverage(graph),
        "summary": {"quantified_global_risk": None,
                    "reason": "Causes corrélées et probabilités de catégorie : somme non défendable.",
                    "largest_single_event_fraction": strongest["conditional_loss_fraction"] if strongest else None,
                    "largest_single_event_node": strongest["name"] if strongest else None,
                    "largest_expected_fraction": likeliest["expected_loss_fraction"] if likeliest else None,
                    "largest_expected_node": likeliest["name"] if likeliest else None},
        "adverse_scenarios": rows[:3], "sensitivity": sensitivity,
        "missing": list(dict.fromkeys(missing)),
        "absent_groups": absent_groups,
        "sources": graph.get("meta", {}).get("sources", []),
        "limits": [
            "Périmètre : uniquement les risques représentés par les nœuds et liens du graphe découvert ; pas tous les risques réels.",
            "Aucune probabilité totale ni perte totale : Ethena → USDe → sUSDe peut représenter une même cause plusieurs fois.",
            "Les poids de transmission et sévérités sont des hypothèses de modèle, même si les dépendances sont observées.",
            "Le prix de vente Pendle et le prix de l'oracle Morpho sont deux voies distinctes ; aucune baisse de l'un n'est injectée dans l'autre.",
            "Le stress test de liquidations est conditionnel et séparé ; ses pertes ne sont pas ajoutées ici.",
            "Le graphe découvert peut manquer des contrats, expositions cachées ou réactions de marché en crise.",
        ],
    }
