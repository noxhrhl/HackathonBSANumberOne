"""Dessin du graphe de dépendances en SVG, généré côté serveur (aucune bibliothèque JS).

Colonnes = profondeur (la position est à droite, ses dépendances lointaines à gauche).
Couleur = type de nœud. Arête pleine = vérifiée / API / documentée ; pointillés rouges = supposée.
Le nœud en incident est rouge ; le chemin de propagation jusqu'à la position est surligné.
Survol d'un nœud = preuves (source + date) ; clic = ouvre la première preuve.
"""

from html import escape

from .schema import depths

KIND_STYLE = {
    "position": ("#1d2633", "#ffffff", "Position"),
    "chain": ("#e8eaf6", "#283593", "Chaîne"),
    "asset": ("#e3f2fd", "#0d47a1", "Actif"),
    "issuer": ("#fff3e0", "#b45309", "Émetteur"),
    "oracle": ("#f3e5f5", "#6a1b9a", "Oracle"),
    "venue": ("#e0f2f1", "#00695c", "AMM / bourse DEX"),
    "lending": ("#e8f5e9", "#2e7d32", "Marché de prêt"),
    "perp_exchange": ("#fce4ec", "#ad1457", "Bourse de couverture"),
    "custodian": ("#efebe9", "#4e342e", "Dépositaire"),
    "protocol": ("#e1f5fe", "#01579b", "Contrat de protocole"),
    "admin": ("#eceff1", "#37474f", "Gouvernance"),
    "bridge": ("#fffde7", "#827717", "Pont"),
}
MECH_SHORT = {"backing": "valeur", "pricing": "prix", "exit_liquidity": "sortie", "collateral_liquidation": "liquidations",
              "settlement": "règlement", "hedging": "couverture", "custody": "garde", "bad_debt": "créance douteuse"}
STATUS_LABEL = {"verified_onchain": "lu on-chain", "api": "API", "documented": "documenté", "assumed": "À VÉRIFIER"}

NODE_W, NODE_H, COL_GAP, ROW_GAP, MARGIN_X, MARGIN_Y = 200, 46, 70, 14, 16, 26


def _clip(text, n):
    return text if len(text) <= n else text[: n - 1] + "…"


def short_name(name, n=24):
    """Nom court pour la case : sans la précision entre parenthèses, coupé à n caractères."""
    base = name.split(" (")[0].split(" « ")[0].strip() or name
    return _clip(base, n)


def fmt_pct(x, d=1):
    if x is None:
        return "—"
    return f"{100 * x:.{d}f} %".replace(".", ",")


def layout(graph):
    d = depths(graph)
    maxd = max(d.values()) if d else 0
    cols = {}
    for n in graph["nodes"]:
        cols.setdefault(d.get(n["id"], maxd), []).append(n["id"])
    out_edges = {}
    for e in graph["edges"]:
        out_edges.setdefault(e["from"], []).append(e["to"])
    kinds = {n["id"]: n["kind"] for n in graph["nodes"]}
    order = list(KIND_STYLE)
    pos_y = {}
    for depth in range(0, maxd + 1):
        ids = cols.get(depth, [])
        if depth <= 1:
            ids.sort(key=lambda i: (order.index(kinds[i]) if kinds[i] in order else 99, i))
        else:
            def bary(i):
                ys = [pos_y[t] for t in out_edges.get(i, []) if t in pos_y]
                return sum(ys) / len(ys) if ys else 1e9
            ids.sort(key=lambda i: (bary(i), i))
        for k, i in enumerate(ids):
            pos_y[i] = k
        cols[depth] = ids
    height_rows = max(len(v) for v in cols.values()) if cols else 1
    total_h = MARGIN_Y * 2 + height_rows * (NODE_H + ROW_GAP)
    coords = {}
    for depth, ids in cols.items():
        col_h = len(ids) * (NODE_H + ROW_GAP) - ROW_GAP
        top = (total_h - col_h) / 2
        x = MARGIN_X + (maxd - depth) * (NODE_W + COL_GAP)
        for k, i in enumerate(ids):
            coords[i] = (x, top + k * (NODE_H + ROW_GAP))
    width = MARGIN_X * 2 + (maxd + 1) * NODE_W + maxd * COL_GAP
    return coords, width, total_h


SIM_STROKE = {"origin": ("#9e1b32", 2.5, ""), "changed": ("#d1495b", 2.2, ""), "unknown": ("#e08a2c", 2.2, "5 3"),
              "estimate": ("#e08a2c", 2.2, "5 3"), "no_effect": ("#8a96a3", 1.4, "4 3")}


def render(graph, node_info=None, weights=None, incident=None, path=None, coverage_line="",
           sim=None, node_href=None, sim_edges=None):
    """SVG complet. node_info[id] = {'p','impact'} ; weights[(a,b)] = w ; path = liste d'ids.

    Mode simulation : sim[id] = {'status', 'label', 'tip'} colore les nœuds touchés et estompe
    les autres ; node_href(id) rend chaque nœud cliquable ; sim_edges = arêtes traversées."""
    node_info = node_info or {}
    sim_edges = sim_edges or set()
    weights = weights or {}
    path = path or []
    path_edges = set(zip(path, path[1:]))
    coords, width, height = layout(graph)
    nodes = {n["id"]: n for n in graph["nodes"]}
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width:.0f} {height:.0f}" '
             f'width="{width:.0f}" height="{height:.0f}" class="dep-graph" role="img" '
             f'aria-label="Graphe des dépendances de {escape(graph["position"]["label"])}">',
             '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#9aa5b1"/></marker>'
             '<marker id="arr-hot" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
             '<path d="M0,0 L10,5 L0,10 z" fill="#d1495b"/></marker></defs>']
    # arêtes (dessinées d'abord, sous les nœuds)
    labels = []
    for e in graph["edges"]:
        if e["from"] not in coords or e["to"] not in coords:
            continue
        x1, y1 = coords[e["from"]]
        x2, y2 = coords[e["to"]]
        sx, sy = x1 + NODE_W, y1 + NODE_H / 2
        tx, ty = x2, y2 + NODE_H / 2
        if tx <= sx:  # même colonne ou retour : on contourne par le bas
            tx, ty = x2 + NODE_W / 2, y2 + NODE_H
        mid = (sx + tx) / 2
        d = f"M{sx:.1f},{sy:.1f} C{mid:.1f},{sy:.1f} {mid:.1f},{ty:.1f} {tx:.1f},{ty:.1f}"
        hot = (e["from"], e["to"]) in path_edges
        crossed = (e["from"], e["to"]) in sim_edges
        assumed = e["status"] == "assumed"
        w = weights.get((e["from"], e["to"]))
        cls = "edge"
        style = 'stroke="#b8c2cc" stroke-width="1.2"'
        if e["mechanism"] == "settlement" and not hot:
            style = 'stroke="#d5dbe1" stroke-width="0.8" opacity="0.6"'
        if assumed:
            style = 'stroke="#d1495b" stroke-width="1.4" stroke-dasharray="5 4"'
        if crossed and not hot:
            style = 'stroke="#e08a2c" stroke-width="1.8"'
        if hot:
            style = 'stroke="#d1495b" stroke-width="3"'
            cls += " hot"
        if sim is not None and not (hot or crossed) and "opacity" not in style:
            style += ' opacity="0.35"'
        tip = (f"{nodes[e['from']]['name']} → {nodes[e['to']]['name']}\n"
               f"mécanisme : {e['mechanism']} · statut : {STATUS_LABEL.get(e['status'], e['status'])}"
               + (f" · w = {w:.3f}" if w is not None else "")
               + "".join(f"\npreuve : {ev.get('source')} ({ev.get('fetched_at')}) {ev.get('url')}" for ev in e.get("evidence", [])[:2])
               + (f"\n{(e.get('details') or {}).get('why')}" if (e.get("details") or {}).get("why") else ""))
        marker = "arr-hot" if hot else "arr"
        parts.append(f'<path class="{cls}" d="{d}" fill="none" {style} marker-end="url(#{marker})"><title>{escape(tip)}</title></path>')
        if hot or assumed:
            lx, ly = mid, (sy + ty) / 2 - 4
            text = f"{MECH_SHORT.get(e['mechanism'], e['mechanism'])}" + (f" · w {w:.2f}".replace(".", ",") if w is not None else "")
            if assumed:
                text = "À VÉRIFIER · " + text
            labels.append(f'<g class="edge-label"><rect x="{lx - 4 - 3.2 * len(text):.1f}" y="{ly - 10:.1f}" width="{6.4 * len(text) + 8:.1f}" '
                          f'height="14" rx="3" fill="#fff" opacity="0.92"/><text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                          f'font-size="10.5" fill="{"#d1495b" if hot or assumed else "#566574"}">{escape(text)}</text></g>')
    # nœuds
    for nid, (x, y) in coords.items():
        n = nodes[nid]
        fill, ink, kind_label = KIND_STYLE.get(n["kind"], ("#f5f5f5", "#333", n["kind"]))
        stroke, sw = "#cfd8dc", 1
        if nid in path:
            stroke, sw = "#d1495b", 2
        if nid == incident:
            fill, ink, stroke, sw = "#d1495b", "#ffffff", "#9e1b32", 2.5
        dash, opacity = "", ""
        sm = (sim or {}).get(nid)
        if sim is not None:
            if sm:
                stroke, sw, dash = SIM_STROKE.get(sm["status"], (stroke, sw, ""))
                if sm["status"] == "origin":
                    fill, ink = "#d1495b", "#ffffff"
            else:
                opacity = ' opacity="0.45"'
        info = node_info.get(nid, {})
        stats = ""
        if sm and sm.get("label"):
            stats = sm["label"]
        elif n["kind"] != "position" and info:
            stats = f"p {fmt_pct(info.get('p'), 2)} · I {info.get('impact', 0):.2f}".replace(".", ",", 1)
        flags = n.get("flags", [])
        full_sub = " · ".join(x for x in (kind_label, stats) if x)
        if "single_source" in flags and n["kind"] == "oracle":
            full_sub += " · source unique"
        short_sub = stats or kind_label
        ev = n.get("evidence") or []
        href = next((x.get("url") for x in ev if (x.get("url") or "").startswith("http")), None)
        tip = (f"{n['name']}\n{kind_label}" + (f" · {n.get('address')}" if n.get("address") else "")
               + "".join(f"\n{k} : {v:,.0f}" if isinstance(v, (int, float)) and abs(v) >= 1000 else f"\n{k} : {v}"
                         for k, v in (n.get("metrics") or {}).items() if v is not None)
               + "".join(f"\npreuve : {x.get('source')} ({x.get('fetched_at')}) {x.get('url')}" for x in ev[:3])
               + (f"\nsignaux : {', '.join(flags)}" if flags else ""))
        if sm and sm.get("tip"):
            tip = f"{n['name']}\n{sm['tip']}"
        dash_attr = f' stroke-dasharray="{dash}"' if dash else ""
        g = (f'<g class="node kind-{n["kind"]}"{opacity}><title>{escape(tip)}</title>'
             f'<rect x="{x:.1f}" y="{y:.1f}" width="{NODE_W}" height="{NODE_H}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash_attr}/>'
             f'<text x="{x + 10:.1f}" y="{y + 19:.1f}" font-size="12.5" font-weight="700" fill="{ink}">{escape(short_name(n["name"]))}</text>'
             f'<text x="{x + 10:.1f}" y="{y + 35:.1f}" font-size="10.5" fill="{ink}" opacity="0.8">{escape(_clip(short_sub, 30))}</text>'
             # au survol : la case s'élargit vers la droite et montre le nom complet
             f'<g class="full"><rect x="{x:.1f}" y="{y:.1f}" width="{max(NODE_W, 24 + 7.6 * len(n["name"]), 24 + 6.2 * len(full_sub)):.0f}" '
             f'height="{NODE_H}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash_attr}/>'
             f'<text x="{x + 10:.1f}" y="{y + 19:.1f}" font-size="12.5" font-weight="700" fill="{ink}">{escape(n["name"])}</text>'
             f'<text x="{x + 10:.1f}" y="{y + 35:.1f}" font-size="10.5" fill="{ink}" opacity="0.8">{escape(full_sub)}</text></g></g>')
        if node_href:
            g = f'<a href="{escape(node_href(nid), quote=True)}">{g}</a>'
        elif href:
            g = f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener">{g}</a>'
        parts.append(g)
    parts.extend(labels)
    parts.append("</svg>")
    return "".join(parts)


def legend(graph):
    kinds = []
    for n in graph["nodes"]:
        if n["kind"] not in kinds:
            kinds.append(n["kind"])
    items = "".join(f'<span class="lg"><i class="kind-{k}" style="background:{KIND_STYLE[k][0]};border-color:{KIND_STYLE[k][1]}"></i>{KIND_STYLE[k][2]}</span>'
                    for k in KIND_STYLE if k in kinds)
    items += ('<span class="lg"><i class="line"></i>arête vérifiée / API / documentée</span>'
              '<span class="lg"><i class="line dashed"></i>supposée — À VÉRIFIER</span>'
              '<span class="lg"><i class="line hot"></i>chemin de propagation</span>'
              '<span class="lg"><i class="incident" style="background:#d1495b;border-color:#9e1b32"></i>composant en incident</span>')
    return f'<div class="legend">{items}</div>'


