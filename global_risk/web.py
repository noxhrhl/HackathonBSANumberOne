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
.global-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,200px));gap:10px}
.global-all{display:none}
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
.global-graph .dep-graph.has-selected a:not(.selected){opacity:.28;transition:opacity .2s}
.global-graph .dep-graph a.selected .node rect{stroke:#ffffff;stroke-width:5;fill:#2a3a52;filter:drop-shadow(0 0 12px #ffffff);animation:risk-pulse 1.4s ease-in-out infinite}
.global-graph .dep-graph a.selected .node text{fill:#ffffff}
@keyframes risk-pulse{0%,100%{stroke-width:4;filter:drop-shadow(0 0 6px #ffffff)}50%{stroke-width:6;filter:drop-shadow(0 0 16px #ffffff)}}
.global-priority[aria-pressed=true]{border-color:#ffffff;box-shadow:0 0 0 1px #ffffff inset}
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
            f'<a href="{_s(current)}" {"aria-current=page" if active == "current" else ""}><b>Si un bloc casse</b> : rester ou sortir ?</a>'
            f'<a href="{_s(global_url)}" {"aria-current=page" if active == "global" else ""}><b>Analyse globale des risques</b> : lesquels sont les plus probables ?</a>'
            '</nav>')


def add_tabs_to_current(page, pid, amount=None):
    """Ajoute une navigation au HTML rendu par app.py, sans toucher à son contenu."""
    marker = '<div class="tabs-wrap">'
    if marker not in page:
        raise ValueError("Repère d'onglet absent dans le site existant")
    page = page.replace("</head>", CSS + "</head>", 1)
    top = '<main class="main">'
    if top in page:   # en haut de la page, au-dessus de la réponse « rester ou sortir »
        return page.replace(top, top + tabs(pid, "current", amount), 1)
    return page.replace(marker, tabs(pid, "current", amount) + marker, 1)


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
    transmission = " ".join(_s(x) for x in dict.fromkeys(row["transmission"])) or "Mécanisme non documenté."
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
            f'<dt>Perte si ça casse</dt><dd>{_pct(row["conditional_loss_fraction"])} de la position '
            f'({_money(row["conditional_loss_usd"])})</dd>'
            f'<dt>Probabilité</dt><dd>{_pct(row["probability"], 2) + " sur l’horizon (moyenne de la catégorie)" if row["probability"] is not None else "non estimable"}</dd>'
            f'<dt>Probabilité × perte</dt><dd>{_money(row["expected_loss_usd"]) if row["expected_loss_fraction"] is not None else "non calculable"}</dd>'
            f'<dt>Source</dt><dd>{_sources_html(row["sources"])}</dd>'
            '</dl>')


def _expected(row):
    if row["expected_loss_fraction"] is None:
        return "Non calculable : probabilité non estimable avec fiabilité"
    return (f'{_pct(row["expected_loss_fraction"], 3)} de la position ({_money(row["expected_loss_usd"])}) '
            f'= probabilité {_pct(row["probability"], 2)} × perte si défaillance {_pct(row["conditional_loss_fraction"])}')


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
        f'<b>{_s(row["name"])}</b>'
        + (f'<strong>{_money(row["expected_loss_usd"])}</strong>'
           f'<small>probabilité {_pct(row["probability"], 2)} × perte si ça casse {_money(row["conditional_loss_usd"])}</small></button>'
           if row["expected_loss_fraction"] is not None else
           f'<strong>{_pct(row["conditional_loss_fraction"])}</strong>'
           f'<small>de la position perdue si ça casse ; probabilité non estimable</small></button>')
        for index, row in enumerate(top_three, 1))
    initial_risk = top_three[0] if top_three else None
    initial_anchor = _anchor(initial_risk["id"]) if initial_risk else ""
    initial_body = _risk_body(initial_risk) if initial_risk else '<p>Données indisponibles : aucun risque couvert.</p>'
    drawing = svg.render(graph, node_href=lambda nid: "#summary" if nid == graph["position"]["id"] else "#" + _anchor(nid))
    fetched = analysis["fetched_at"] or "Données indisponibles"
    largest = analysis["summary"]["largest_single_event_fraction"]
    summary_text = (f'{_pct(largest)} si « {_s(analysis["summary"]["largest_single_event_node"])} » défaille seul'
                    if largest is not None else "Données indisponibles")
    likeliest = analysis["summary"].get("largest_expected_fraction")
    expected_text = (f'{_pct(likeliest, 3)} ({_money(likeliest * amount)}) pour « {_s(analysis["summary"]["largest_expected_node"])} »'
                     if likeliest is not None else "non calculable")
    return f'''<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Belem — Analyse globale des risques</title><link rel="stylesheet" href="/static/style.css">{CSS}</head><body>
<header class="topbar"><span class="product"><b>Belem Capital</b> Risque de dépendances DeFi</span>
<span class="pos"><b>{_s(analysis["position_name"])}</b> {_money(analysis["position_size_usd"])}</span>
<span class="stamp">Relevé en ligne : {_s(fetched[:16].replace("T", " "))} UTC <a href="/global?{_s(urlencode({"pos": pid, "refresh": 1}))}">Actualiser</a></span></header>
<div class="app"><aside class="side"><p class="side-title">Position analysée</p>
<form class="controls" action="/global" method="get"><label>Choisir une position<select name="pos">{options}</select></label>
<label>Montant à analyser <span>$</span><input type="number" name="amount" min="10000" max="10000000000" step="any" value="{round(amount)}"></label>
<button type="submit">Analyser</button></form>
</aside>
<main class="main">{tabs(pid, amount=amount)}
<section class="panel global-hero" id="summary"><h1>Analyse globale des risques</h1>
<p>Les blocs dont dépend la position, classés selon probabilité × perte si ça casse.</p>
<div class="global-kpis"><div class="global-kpi"><span>Blocs analysés</span><strong>{len(analysis["rows"])}</strong></div>
<div class="global-kpi"><span>Horizon</span><strong>{analysis["horizon_days"]:.0f} jours</strong></div></div></section>
<section class="panel global-panel"><h2>Les 3 risques les plus importants</h2>
<p class="global-muted">Probabilité × perte si ça casse. Les probabilités sont des moyennes par catégorie : les montants sont des ordres de grandeur. Plusieurs blocs ont la même cause : leurs pertes ne s'additionnent pas.</p>
<div class="global-priorities">{priorities or '<p>Données indisponibles : aucun risque couvert.</p>'}</div></section>
<section class="panel global-panel"><h2>Le graphe</h2><p class="global-muted">Cliquez sur un bloc pour voir sa fiche. Une flèche A → B veut dire « si A casse, B est touché ».</p>
<div class="global-explore"><div class="graph-wrap global-graph">{drawing}</div>
<aside class="global-detail" id="global-risk-detail" tabindex="-1" aria-live="polite"><p class="global-detail-label">Risque sélectionné</p>
<div class="global-risk" id="global-risk-detail-content">{initial_body}</div></aside></div>
<details class="global-all"><summary>Voir tous les risques du graphe ({len(analysis["rows"])})</summary><div>
<p class="global-muted">Risques regroupés par famille et classés selon leur espérance de perte (probabilité × perte si défaillance). Chaque ligne traite une cause séparément.</p>{risks}</div></details></section>
</main></div><script>
(function () {{
  const detail = document.getElementById("global-risk-detail");
  const content = document.getElementById("global-risk-detail-content");
  function selectRisk(id, scrollToDetail) {{
    const article = document.getElementById(id);
    const body = article && article.querySelector(".global-risk-content");
    if (!body) return;
    content.innerHTML = body.innerHTML;
    let picked = null;
    document.querySelectorAll(".global-graph .dep-graph a").forEach(link => {{
      const on = link.getAttribute("href") === "#" + id;
      link.classList.toggle("selected", on);
      if (on) picked = link;
    }});
    document.querySelectorAll(".global-graph .dep-graph").forEach(g => g.classList.toggle("has-selected", !!picked));
    if (picked) {{
      picked.parentNode.appendChild(picked);   // au premier plan
      const wrap = picked.closest(".global-graph");
      const r = picked.getBoundingClientRect(), w = wrap.getBoundingClientRect();
      wrap.scrollLeft += (r.left + r.width / 2) - (w.left + w.width / 2);
      wrap.scrollTop += (r.top + r.height / 2) - (w.top + w.height / 2);
    }}
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
      if (button.classList.contains("global-priority")) {{   // un des 3 risques : on descend jusqu'au graphe
        const g = document.querySelector(".global-explore");
        if (g) g.scrollIntoView({{behavior: "smooth", block: "start"}});
      }}
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
