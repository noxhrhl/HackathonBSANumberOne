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
.global-risk{padding:14px 0;border-top:1px solid var(--soft-line);scroll-margin-top:70px}
.global-risk:first-of-type{border-top:0}.global-risk h3{margin:0 0 6px;font-size:15px}
.global-risk .tag{display:inline-block;padding:2px 7px;border-radius:3px;background:#153c58;color:var(--cyan-soft);font-size:11px;margin-right:6px}
.global-risk dl{display:grid;grid-template-columns:145px minmax(0,1fr);gap:6px 12px;margin:10px 0 0;font-size:12.5px}
.global-risk dt{color:var(--muted)}.global-risk dd{margin:0;overflow-wrap:anywhere}
.global-risk a,.global-panel a{overflow-wrap:anywhere}.global-graph{max-height:540px}
.global-graph .dep-graph a:hover .node rect{stroke:var(--cyan-soft);stroke-width:2}
.global-graph .dep-graph a.selected .node rect{stroke:var(--cyan);stroke-width:3}
.global-priorities{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
.global-priority{width:100%;text-align:left;border:1px solid var(--soft-line);background:#0c1d38;color:var(--ink);padding:14px;border-radius:4px;cursor:pointer;box-shadow:none}
.global-priority:hover,.global-priority[aria-pressed=true]{border-color:var(--cyan);background:#12304a}
.global-priority span,.global-priority small{display:block;color:var(--muted);font-size:12px}
.global-priority b{display:block;margin:8px 0;font-size:15px}.global-priority strong{display:block;font-size:20px;color:var(--cyan-soft)}
.global-explore{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(320px,1fr);gap:14px;align-items:start}
.global-explore .global-graph{min-width:0;margin:0}.global-detail{background:#0c1d38;border:1px solid var(--cyan);padding:14px;border-radius:4px;min-width:0;scroll-margin-top:16px}
.global-detail-label{margin:0 0 8px;color:var(--cyan-soft);font-weight:700;font-size:12px;text-transform:uppercase;letter-spacing:.06em}
.global-detail .global-risk{padding:0;border:0}.global-detail .global-risk h3{margin-top:0}
.global-all,.global-family{border:1px solid var(--soft-line);border-radius:4px;margin-top:10px;background:#0c1d38}
.global-all>summary,.global-family>summary{padding:12px 14px;cursor:pointer;font-weight:700}
.global-all>div,.global-family>div{padding:0 14px 12px}.global-family{background:#10243e}
.global-open-detail{margin-top:12px;padding:6px 10px;border:1px solid #285074;border-radius:4px;background:transparent;color:var(--cyan-soft);cursor:pointer;box-shadow:none}
.global-open-detail:hover{border-color:var(--cyan)}
@media(max-width:1000px){.global-kpis{grid-template-columns:repeat(2,1fr)}.global-explore{grid-template-columns:1fr}}
@media(max-width:750px){.global-priorities{grid-template-columns:1fr}}
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


def _risk_body(row):
    """Décrit un risque isolé, partagé par la liste et la fiche du graphe."""
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
    return (f'<h3>{_s(row["name"])} {channel}</h3>'
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
            '</dl>')


def _risk_html(row):
    """Garde le détail complet accessible dans la liste des risques."""
    anchor = _anchor(row["id"])
    return (f'<article class="global-risk" id="{anchor}">'
            f'<div class="global-risk-content">{_risk_body(row)}</div>'
            f'<button type="button" class="global-open-detail" data-risk-id="{anchor}" '
            f'aria-pressed="false">Afficher dans la fiche du graphe</button></article>')


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
    risks = "".join(
        f'<details class="global-family"><summary>{_s(group)} '
        f'({sum(r["group"] == group for r in analysis["rows"])})</summary><div>'
        + "".join(_risk_html(row) for row in analysis["rows"] if row["group"] == group)
        + '</div></details>' for group in groups)
    top_three = analysis["adverse_scenarios"][:3]
    priorities = "".join(
        f'<button type="button" class="global-priority" data-risk-id="{_anchor(row["id"])}" '
        f'aria-pressed="false"><span>Risque {index:02d} · {_s(row["group"])}</span>'
        f'<b>{_s(row["name"])}</b><strong>{_pct(row["conditional_loss_fraction"])}</strong>'
        f'<small>de perte conditionnelle isolée ({_money(row["conditional_loss_usd"])})</small></button>'
        for index, row in enumerate(top_three, 1))
    initial_risk = top_three[0] if top_three else None
    initial_anchor = _anchor(initial_risk["id"]) if initial_risk else ""
    initial_body = _risk_body(initial_risk) if initial_risk else '<p>Données indisponibles : aucun risque couvert.</p>'
    drawing = svg.render(graph, node_href=lambda nid: "#summary" if nid == graph["position"]["id"] else "#" + _anchor(nid))
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
<section class="panel global-panel"><h2>Les 3 risques les plus importants</h2>
<p class="global-muted">Classement selon la perte conditionnelle isolée dans le scénario central : ce n'est pas un classement par probabilité. Plusieurs lignes peuvent partager une même cause ; leurs pertes ne s'additionnent pas.</p>
<div class="global-priorities">{priorities or '<p>Données indisponibles : aucun risque couvert.</p>'}</div></section>
<section class="panel global-panel"><h2>Graphe et détail du risque</h2><p class="global-muted">Cliquez sur un nœud pour lire son risque dans la fiche. Une flèche A → B signifie qu'une défaillance de A peut toucher B.</p>
<div class="global-explore"><div class="graph-wrap global-graph">{drawing}</div>
<aside class="global-detail" id="global-risk-detail" tabindex="-1" aria-live="polite"><p class="global-detail-label">Risque sélectionné</p>
<div class="global-risk" id="global-risk-detail-content">{initial_body}</div></aside></div>
<details class="global-all"><summary>Voir tous les risques du graphe ({len(analysis["rows"])})</summary><div>
<p class="global-muted">Risques regroupés par famille et classés selon leur perte conditionnelle isolée. Chaque ligne traite une cause séparément.</p>{risks}</div></details></section>
</main></div><script>
(function () {{
  const detail = document.getElementById("global-risk-detail");
  const content = document.getElementById("global-risk-detail-content");
  function selectRisk(id, scrollToDetail) {{
    const article = document.getElementById(id);
    const body = article && article.querySelector(".global-risk-content");
    if (!body) return;
    content.innerHTML = body.innerHTML;
    document.querySelectorAll(".global-graph .dep-graph a").forEach(link => {{
      link.classList.toggle("selected", link.getAttribute("href") === "#" + id);
    }});
    document.querySelectorAll("[data-risk-id]").forEach(button => {{
      button.setAttribute("aria-pressed", button.dataset.riskId === id ? "true" : "false");
    }});
    if (scrollToDetail) {{
      if (window.matchMedia("(max-width: 1000px)").matches) {{
        detail.scrollIntoView({{behavior: "smooth", block: "start"}});
      }}
      detail.focus({{preventScroll: true}});
    }}
  }}
  document.addEventListener("click", event => {{
    if (!(event.target instanceof Element)) return;
    const link = event.target.closest('.global-graph a[href^="#risk-"]');
    if (link) {{
      event.preventDefault();
      selectRisk(link.getAttribute("href").slice(1), true);
      return;
    }}
    const button = event.target.closest("[data-risk-id]");
    if (button) {{
      event.preventDefault();
      selectRisk(button.dataset.riskId, true);
    }}
  }});
  selectRisk("{initial_anchor}", false);
}})();
</script></body></html>'''


def render_unavailable(pid, error):
    """Affiche explicitement l'échec du relevé en ligne, sans données mises en cache."""
    return (f'<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Données indisponibles</title>'
            f'<link rel="stylesheet" href="/static/style.css">{CSS}</head><body>'
            f'<main class="main"><section class="panel global-panel"><h1>Données indisponibles</h1>'
            f'<p>Le relevé en ligne de cette position a échoué. Aucune ancienne donnée n’est affichée.</p>'
            f'<p class="global-muted">{_s(type(error).__name__)} : {_s(error)}</p>'
            f'<p><a href="/global?{_s(urlencode({"pos": pid, "refresh": 1}))}">Réessayer</a></p>'
            '</section></main></body></html>')
