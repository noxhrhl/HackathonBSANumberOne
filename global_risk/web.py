"""Interface HTML de l'analyse globale ; aucun fichier du site initial n'est modifié."""

import hashlib
from html import escape
from urllib.parse import urlencode

from risk_engine import svg


CSS = """
<style>
.global-tabs{display:flex;gap:8px;flex-wrap:wrap;padding:10px 12px;background:#0b1d35;border:1px solid var(--line);border-radius:6px}
.global-tabs a{padding:8px 13px;border-radius:4px;border:1px solid #27496d;color:var(--ink);text-decoration:none;font-weight:600}
.global-tabs a[aria-current=page]{background:var(--cyan);color:#04121f;border-color:var(--cyan)}
.global-tabs a:hover{border-color:var(--cyan-soft)}
.global-current-graph{min-width:0}.global-current-mode{margin:0 0 10px;color:var(--cyan-soft);font-size:13px;font-weight:600}
.global-hero{padding:20px}.global-hero h1{font-size:25px;margin:0 0 8px}.global-hero p{color:var(--muted);margin:0 0 14px}
.global-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.global-kpi{background:#0c1d38;border:1px solid var(--soft-line);padding:12px;border-radius:4px}
.global-kpi span{display:block;color:var(--muted);font-size:12px}.global-kpi strong{display:block;font-size:18px;margin-top:4px}
.global-note{padding:12px;background:#142139;border-left:3px solid var(--partial);color:var(--ink);line-height:1.55}
.global-note strong{color:var(--partial)}
.global-panel{padding:16px 18px}.global-panel h2{font-size:17px;margin:0 0 10px}.global-panel h3{font-size:14px;margin:16px 0 8px}
.global-panel p,.global-panel li{line-height:1.55}.global-muted{color:var(--muted)}
.global-chips{display:flex;flex-wrap:wrap;gap:6px;margin:12px 0}.global-chips a{border:1px solid #285074;border-radius:20px;padding:5px 10px;text-decoration:none;font-size:12px}
.global-risk{padding:14px 0;border-top:1px solid var(--soft-line);scroll-margin-top:70px}
.global-risk:first-of-type{border-top:0}.global-risk h3{margin:0 0 6px;font-size:15px}
.global-risk .tag{display:inline-block;padding:2px 7px;border-radius:3px;background:#153c58;color:var(--cyan-soft);font-size:11px;margin-right:6px}
.global-risk dl{display:grid;grid-template-columns:145px minmax(0,1fr);gap:6px 12px;margin:10px 0 0;font-size:12.5px}
.global-risk dt{color:var(--muted)}.global-risk dd{margin:0;overflow-wrap:anywhere}
.global-risk a,.global-panel a{overflow-wrap:anywhere}.global-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.global-card{border:1px solid var(--soft-line);background:#0c1d38;border-radius:4px;padding:12px}
.global-card b{display:block;margin-bottom:6px}.global-card p{margin:0;font-size:12.5px}
.global-list{padding-left:20px;margin-bottom:0}.global-graph{max-height:540px}
.global-graph .dep-graph a:hover .node rect{stroke:var(--cyan-soft);stroke-width:2}
@media(max-width:1000px){.global-kpis{grid-template-columns:repeat(2,1fr)}.global-grid{grid-template-columns:1fr}}
@media(max-width:700px){.app{display:block;background:none}.side{position:static;height:auto}.main{padding:12px}.topbar{height:auto;min-height:48px;flex-wrap:wrap;padding:10px}.global-kpis{grid-template-columns:1fr 1fr}.global-risk dl{grid-template-columns:1fr}.global-risk dt{font-weight:700}}
</style>
"""


def _s(value):
    return escape(str(value), quote=True)


def _pct(value, decimals=1):
    return "Données indisponibles" if value is None else f"{100 * value:.{decimals}f} %".replace(".", ",")


def _money(value):
    return "Données indisponibles" if value is None else f"{value:,.0f} $".replace(",", " ")


def _anchor(nid):
    return "risk-" + hashlib.sha1(nid.encode("utf-8")).hexdigest()[:12]


def tabs(pid, active="global", amount=None):
    """Deux pages de premier niveau qui gardent la position et le montant choisis."""
    params = {"pos": pid}
    if amount is not None:
        params["amount"] = round(amount)
    current = "/?" + urlencode(params)
    global_url = "/global?" + urlencode(params)
    return (f'<nav class="global-tabs" aria-label="Vues de la position">'
            f'<a href="{_s(current)}" {"aria-current=page" if active == "current" else ""}>Analyse actuelle</a>'
            f'<a href="{_s(global_url)}" {"aria-current=page" if active == "global" else ""}>Analyse globale des risques</a>'
            '</nav>')


def add_tabs_to_current(page, pid, amount=None):
    """Ajoute une navigation au HTML rendu par app.py, sans toucher à son contenu."""
    marker = '<div class="tabs-wrap">'
    if marker not in page:
        raise ValueError("Repère d'onglet absent dans le site existant")
    return page.replace("</head>", CSS + "</head>", 1).replace(marker, tabs(pid, "current", amount) + marker, 1)


def _sources_html(items):
    if not items:
        return "Données indisponibles"
    rendered = []
    for ev in items[:4]:
        name = _s(ev.get("source") or "Source inconnue")
        url = ev.get("url") or ""
        stamp = _s(ev.get("fetched_at") or "?")
        if url.startswith(("https://", "http://")):
            rendered.append(f'<a href="{_s(url)}" target="_blank" rel="noopener noreferrer">{name}</a> ({stamp})')
        else:
            rendered.append(f"{name} ({stamp})")
    return " ; ".join(rendered)


def _risk_html(row):
    path = " → ".join(_s(x) for x in row["path_names"])
    transmission = " ".join(_s(x) for x in row["transmission"]) or "Mécanisme non documenté."
    probability = (_pct(row["probability"], 2) + " — " + _s(row["probability_basis"])
                   if row["probability"] is not None else "Non estimable avec fiabilité")
    statuses = {"verified_onchain": "lu sur chaîne", "api": "API", "documented": "documentation",
                "assumed": "hypothèse"}
    status_line = ", ".join(dict.fromkeys(statuses.get(s, s or "inconnu") for s in row["edge_statuses"]))
    if row["channel"] == "oracle":
        channel = '<span class="tag">Prix de l’oracle</span>'
    elif row["channel"] == "sale":
        channel = '<span class="tag">Prix de vente / liquidité</span>'
    else:
        channel = ""
    return (f'<article class="global-risk" id="{_anchor(row["id"])}">'
            f'<h3>{_s(row["name"])} {channel}</h3>'
            f'<span class="tag">{_s(row["group"])}</span>'
            '<dl>'
            f'<dt>Chemin vers la position</dt><dd>{path}</dd>'
            f'<dt>Transmission</dt><dd>{transmission}</dd>'
            f'<dt>Exposition isolée</dt><dd>{_pct(row["conditional_loss_fraction"])} '
            f'de la position ({_money(row["conditional_loss_usd"])}) si ce composant défaille ; '
            f'impact du chemin {_pct(row["impact_factor"])}, sévérité supposée {_pct(row["severity_if_failure"])}</dd>'
            f'<dt>Probabilité sur l’horizon</dt><dd>{probability}</dd>'
            f'<dt>Source du lien</dt><dd>{_sources_html(row["sources"])}</dd>'
            f'<dt>Statut du chemin</dt><dd>{_s(status_line or "indisponible")}</dd>'
            f'<dt>Limite principale</dt><dd>{_s(row["main_limit"])}</dd>'
            '</dl></article>')


def render_global(analysis, graph, report, context, positions):
    """Construit la page globale avec les mêmes couleurs et le graphe du site actuel."""
    pid = analysis["position_id"]
    amount = analysis["position_size_usd"]
    options = "".join(f'<option value="{_s(p["id"])}" {"selected" if p["id"] == pid else ""}>{_s(p["label"])}</option>'
                      for p in positions)
    groups = []
    for row in analysis["rows"]:
        if row["group"] not in groups:
            groups.append(row["group"])
    chips = "".join(f'<a href="#{_anchor(row["id"])}">{_s(row["name"])}</a>' for row in analysis["rows"])
    risks = "".join(
        f'<h3>{_s(group)} ({sum(r["group"] == group for r in analysis["rows"])})</h3>' +
        "".join(_risk_html(row) for row in analysis["rows"] if row["group"] == group)
        for group in groups)
    drawing = svg.render(graph, node_href=lambda nid: "#summary" if nid == graph["position"]["id"] else "#" + _anchor(nid))
    sensitivity = "".join(
        f'<div class="global-card"><b>{_s({"low":"Basse", "base":"Centrale", "high":"Haute"}[s["scenario"]])}</b>'
        f'<p>Plus forte perte conditionnelle isolée : {_pct(s["largest_single_loss_fraction"])} '
        f'({_s(s["leading_name"] or "aucun composant")}).</p></div>' for s in analysis["sensitivity"])
    scenarios = "".join(f'<li><a href="#{_anchor(r["id"])}">{_s(r["name"])}</a> : '
                        f'{_pct(r["conditional_loss_fraction"])} de perte conditionnelle isolée.</li>'
                        for r in analysis["adverse_scenarios"])
    missing = "".join(f'<li>{_s(x)}</li>' for x in analysis["missing"])
    if not missing:
        missing = '<li>Aucun manque signalé par les collecteurs actuels ; couverture réelle non garantie.</li>'
    absent = (", ".join(_s(x) for x in analysis["absent_groups"])
              if analysis["absent_groups"] else "Aucune des grandes familles listées n'est absente")
    limits = "".join(f'<li>{_s(x)}</li>' for x in analysis["limits"])
    sources = _sources_html(analysis["sources"])
    quote = (report.get("exit") or {}).get("quotes") or {}
    quote_line = _s(quote.get("source") or "Données indisponibles")
    exact_quote = next((q for q in quote.get("quotes", []) if q.get("cost") is not None
                        and abs(q.get("size_usd", 0) - amount) <= max(1.0, amount * 0.001)), None)
    if exact_quote:
        quote_kind = "hypothétique" if quote.get("source") == "HYPOTHÈSE" else "relevé"
        quote_line += f" ; coût {quote_kind} pour le montant choisi : " + _pct(exact_quote["cost"])
    elif any(q.get("cost") is not None for q in quote.get("quotes", [])):
        quote_line += " ; les cotations ont été relevées pour d'autres tailles. Le coût du montant choisi est calculé par l'analyse actuelle."
    else:
        quote_line += " ; cotation indisponible"
    oracle_count = len((context or {}).get("oracles") or {})
    oracle_line = (f"{oracle_count} relevé(s) price() on-chain disponible(s) dans le contexte de simulation"
                   if oracle_count else "Données indisponibles ou aucun oracle de prêt dans cette position")
    fetched = analysis["fetched_at"] or "Données indisponibles"
    largest = analysis["summary"]["largest_single_event_fraction"]
    summary_text = (f'{_pct(largest)} si « {_s(analysis["summary"]["largest_single_event_node"])} » défaille seul'
                    if largest is not None else "Données indisponibles")
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Belem — Analyse globale des risques</title><link rel="stylesheet" href="/static/style.css">{CSS}</head><body>
<header class="topbar"><span class="product"><b>Belem Capital</b> Risque de dépendances DeFi</span>
<span class="pos"><b>{_s(analysis["position_name"])}</b> {_money(analysis["position_size_usd"])}</span>
<span class="stamp">Relevé en ligne : {_s(fetched[:16].replace("T", " "))} UTC <a href="/global?{_s(urlencode({"pos": pid, "refresh": 1}))}">Actualiser</a></span></header>
<div class="app"><aside class="side"><p class="side-title">Position analysée</p>
<form class="controls" action="/global" method="get"><label>Choisir une position<select name="pos">{options}</select></label>
<label>Montant à analyser <span>$</span><input type="number" name="amount" min="10000" max="10000000000" step="100000" value="{round(amount)}"></label>
<button type="submit">Analyser</button></form>
<p class="side-note">Aucune défaillance à sélectionner. Cette page parcourt toutes les dépendances découvertes pour cette position.</p></aside>
<main class="main">{tabs(pid, amount=amount)}
<section class="panel global-hero" id="summary"><h1>Analyse globale des risques</h1>
<p>Profil des risques modélisés dans le graphe de cette position. Cette vue ne change pas la recommandation « rester ou vendre ».</p>
<div class="global-kpis"><div class="global-kpi"><span>Dépendances couvertes</span><strong>{len(analysis["rows"])} nœuds</strong></div>
<div class="global-kpi"><span>Horizon</span><strong>{analysis["horizon_days"]:.0f} jours</strong></div>
<div class="global-kpi"><span>Montant choisi, illustratif</span><strong>{_money(amount)}</strong></div>
<div class="global-kpi"><span>Confiance</span><strong>{_s(analysis["confidence"])}</strong></div></div>
<p class="global-muted" style="margin-top:12px">Relevé : {_s(fetched)} · Taille de référence des cotations : {_money(analysis["reference_size_usd"])} · Groupes présents : {_s(", ".join(groups) or "aucun")}</p>
<div class="global-note"><strong>Pas de score total fiable.</strong> {_s(analysis["summary"]["reason"])}<br>
Repère de stress : plus forte perte conditionnelle d’un seul composant = {summary_text}. Ce n’est ni une prévision, ni une somme, ni la perte maximale de toutes les causes réunies.</div></section>
<section class="panel global-panel"><h2>Chemins de dépendance</h2><p class="global-muted">Cliquez sur un nœud du graphe ou sur un nom pour voir son risque. Une flèche A → B signifie qu'une défaillance de A peut toucher B.</p>
<div class="graph-wrap global-graph">{drawing}</div><div class="global-chips">{chips}</div></section>
<section class="panel global-panel"><h2>Risques identifiés par famille</h2>
<p class="global-muted">Familles classées selon leur plus forte perte conditionnelle isolée ; composants classés de même dans chaque famille. Chaque ligne traite une cause séparément. Les pourcentages ne doivent pas être additionnés.</p>{risks}</section>
<section class="panel global-panel"><h2>Scénarios défavorables et sensibilité</h2><p class="global-muted">Trois chocs isolés les plus exposants dans le scénario central :</p><ol class="global-list">{scenarios}</ol>
<h3>Hypothèses basse, centrale et haute</h3><div class="global-grid">{sensitivity}</div>
<p class="global-muted" style="margin-top:10px">Ces variantes changent sévérité et poids de transmission. Elles ne constituent pas une distribution jointe des défaillances.</p></section>
<section class="panel global-panel"><h2>Prix de vente et prix d'oracle</h2><div class="global-grid">
<div class="global-card"><b>Vente / sortie</b><p>{quote_line}</p></div>
<div class="global-card"><b>Oracle de prêt</b><p>{_s(oracle_line)}. Sa valeur n'est pas remplacée par une cotation Pendle.</p></div>
<div class="global-card"><b>Liquidations</b><p>Le stress test de liquidations reste dans l'analyse actuelle. Aucune de ses pertes n'est ajoutée aux chiffres ci-dessus.</p></div></div></section>
<section class="panel global-panel"><h2>Sources, données manquantes et limites</h2><p>Sources du relevé : {sources}</p>
<h3>Dépendances absentes du graphe découvert</h3><p>{absent}. Leur absence dans le graphe ne prouve pas qu'elles n'existent pas dans la réalité.</p>
<h3>Données manquantes ou à vérifier</h3><ul class="global-list">{missing}</ul>
<h3>Limites du modèle</h3><ul class="global-list">{limits}</ul>
<p class="global-muted">Les dépendances proviennent de lectures sur chaîne, d'API, de documentation ou de liens supposés, selon leur statut. Les cotations en ligne sont des observations de sortie normale. Les fréquences, sévérités et poids sont des paramètres historiques ou des hypothèses ; les pertes sont des résultats du modèle.</p></section>
</main></div></body></html>'''


def render_unavailable(pid, error):
    """Affiche explicitement l'échec du relevé en ligne, sans données mises en cache."""
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Données indisponibles</title>'
            f'<link rel="stylesheet" href="/static/style.css">{CSS}</head><body>'
            f'<main class="main"><section class="panel global-panel"><h1>Données indisponibles</h1>'
            f'<p>Le relevé en ligne de cette position a échoué. Aucune ancienne donnée n’est affichée.</p>'
            f'<p class="global-muted">{_s(type(error).__name__)} : {_s(error)}</p>'
            f'<p><a href="/global?{_s(urlencode({"pos": pid, "refresh": 1}))}">Réessayer</a></p>'
            '</section></main></body></html>')
