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


def render(graph, node_info=None, weights=None, incident=None, path=None, coverage_line=""):
    """SVG complet. node_info[id] = {'p','impact'} ; weights[(a,b)] = w ; path = liste d'ids."""
    node_info = node_info or {}
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
        assumed = e["status"] == "assumed"
        w = weights.get((e["from"], e["to"]))
        cls = "edge"
        style = 'stroke="#b8c2cc" stroke-width="1.2"'
        if e["mechanism"] == "settlement" and not hot:
            style = 'stroke="#d5dbe1" stroke-width="0.8" opacity="0.6"'
        if assumed:
            style = 'stroke="#d1495b" stroke-width="1.4" stroke-dasharray="5 4"'
        if hot:
            style = 'stroke="#d1495b" stroke-width="3"'
            cls += " hot"
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
        info = node_info.get(nid, {})
        sub = kind_label
        if n["kind"] != "position" and info:
            sub += f" · p {fmt_pct(info.get('p'), 2)} · I {info.get('impact', 0):.2f}".replace(".", ",", 1)
        flags = n.get("flags", [])
        if "single_source" in flags and n["kind"] == "oracle":
            sub += " · source unique"
        ev = n.get("evidence") or []
        href = next((x.get("url") for x in ev if (x.get("url") or "").startswith("http")), None)
        tip = (f"{n['name']}\n{kind_label}" + (f" · {n.get('address')}" if n.get("address") else "")
               + "".join(f"\n{k} : {v:,.0f}" if isinstance(v, (int, float)) and abs(v) >= 1000 else f"\n{k} : {v}"
                         for k, v in (n.get("metrics") or {}).items() if v is not None)
               + "".join(f"\npreuve : {x.get('source')} ({x.get('fetched_at')}) {x.get('url')}" for x in ev[:3])
               + (f"\nsignaux : {', '.join(flags)}" if flags else ""))
        g = (f'<g class="node kind-{n["kind"]}"><title>{escape(tip)}</title>'
             f'<rect x="{x:.1f}" y="{y:.1f}" width="{NODE_W}" height="{NODE_H}" rx="8" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>'
             f'<text x="{x + 10:.1f}" y="{y + 19:.1f}" font-size="12.5" font-weight="700" fill="{ink}">{escape(_clip(n["name"], 30))}</text>'
             f'<text x="{x + 10:.1f}" y="{y + 35:.1f}" font-size="10.5" fill="{ink}" opacity="0.8">{escape(_clip(sub, 40))}</text></g>')
        if href:
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
    items = "".join(f'<span class="lg"><i style="background:{KIND_STYLE[k][0]};border-color:{KIND_STYLE[k][1]}"></i>{KIND_STYLE[k][2]}</span>'
                    for k in KIND_STYLE if k in kinds)
    items += ('<span class="lg"><i class="line"></i>arête vérifiée / API / documentée</span>'
              '<span class="lg"><i class="line dashed"></i>supposée — À VÉRIFIER</span>'
              '<span class="lg"><i class="line hot"></i>chemin de propagation</span>'
              '<span class="lg"><i style="background:#d1495b;border-color:#9e1b32"></i>composant en incident</span>')
    return f'<div class="legend">{items}</div>'


def line_chart(series, width=520, height=180, x_label="", y_label="", markers=None, x_fmt=None, y_max=None):
    """Petit graphique en lignes (SVG). series = [(nom, couleur, [(x, y), …])], x et y dans [0, 1]."""
    markers = markers or []
    pad_l, pad_b, pad_t, pad_r = 44, 30, 12, 12
    ys = [y for _, _, pts in series for _, y in pts if y is not None] + [m[1] for m in markers]
    top = y_max or (max(ys) * 1.1 if ys and max(ys) > 0 else 1)
    W, H = width - pad_l - pad_r, height - pad_t - pad_b

    def X(x):
        return pad_l + x * W

    def Y(y):
        return pad_t + H - min(y, top) / top * H

    out = [f'<svg viewBox="0 0 {width} {height}" class="mini-chart" role="img">']
    for k in range(5):
        yv = top * k / 4
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{Y(yv):.1f}" y2="{Y(yv):.1f}" stroke="#eef1f4"/>'
                   f'<text x="{pad_l - 6}" y="{Y(yv) + 3:.1f}" text-anchor="end" font-size="10" fill="#657181">{fmt_pct(yv, 0 if top > 0.1 else 1)}</text>')
    for k in range(6):
        xv = k / 5
        lab = x_fmt(xv) if x_fmt else fmt_pct(xv, 0)
        out.append(f'<text x="{X(xv):.1f}" y="{height - 10}" text-anchor="middle" font-size="10" fill="#657181">{lab}</text>')
    for name, color, pts in series:
        d = " ".join(f"{'M' if i == 0 else 'L'}{X(x):.1f},{Y(y):.1f}" for i, (x, y) in enumerate(pts) if y is not None)
        out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.2"><title>{escape(name)}</title></path>')
    for x, y, color, label in markers:
        out.append(f'<circle cx="{X(x):.1f}" cy="{Y(y):.1f}" r="4.5" fill="{color}"/>'
                   f'<text x="{X(x) + 7:.1f}" y="{Y(y) - 7:.1f}" font-size="10.5" font-weight="700" fill="{color}">{escape(label)}</text>')
    if x_label:
        out.append(f'<text x="{pad_l + W / 2:.1f}" y="{height}" text-anchor="middle" font-size="10" fill="#657181">{escape(x_label)}</text>')
    out.append("</svg>")
    return "".join(out)
