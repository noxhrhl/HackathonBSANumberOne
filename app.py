"""Site local : analyse actuelle et analyse globale (bibliothèque standard uniquement).

  python app.py            puis ouvrir http://127.0.0.1:8000
  python app.py --port 8001

Le site relève la position affichée en ligne au démarrage, puis toutes les cinq minutes.
Une collecte échouée rend la page indisponible au lieu d'afficher un ancien rapport.
"""

import argparse
import json
import sys
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlencode, urlparse

from risk_engine import discover, sources as S
from risk_engine import model as M
from risk_engine import svg
from risk_engine import simulate as SIM
from risk_engine import exit_rule
from global_risk import analyze, current, web
import pipeline

ROOT = Path(__file__).resolve().parent
TEMPLATE = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
STYLE = (ROOT / "static" / "style.css").read_bytes()
POSITIONS = json.loads((ROOT / "data" / "positions.json").read_text(encoding="utf-8"))
KIND_FR = {k: v[2] for k, v in svg.KIND_STYLE.items()}

_graphs, _reports, _sims = {}, {}, {}
_refresh_lock = threading.RLock()
_last_refresh = {}
REFRESH_SECONDS = 300


def ensure_live_data(pid, force=False):
    """Reconstruit une position avec les API ; aucun rapport ancien ne sert de repli."""
    with _refresh_lock:
        if pid not in {p["id"] for p in POSITIONS["positions"]}:
            raise ValueError(f"Position inconnue : {pid}")
        last = _last_refresh.get(pid, 0.0)
        if not force and last and time.monotonic() - last < REFRESH_SECONDS:
            return
        _last_refresh.pop(pid, None)
        _graphs.pop(pid, None)
        _reports.pop(pid, None)
        _sims.pop(pid, None)
        S.reset_sources()
        graph = discover.discover(pid)
        report = pipeline.build_report(graph)
        context = SIM.build_context(graph)
        _graphs[pid] = graph
        _reports[pid] = report
        _sims[pid] = context
        _last_refresh[pid] = time.monotonic()


def pct(x, d=1):
    return svg.fmt_pct(x, d)


def usd(x):
    if x is None:
        return "—"
    if abs(x) >= 1e9:
        return f"{x / 1e9:,.2f} Md$".replace(",", " ").replace(".", ",")
    if abs(x) >= 1e6:
        return f"{x / 1e6:,.2f} M$".replace(",", " ").replace(".", ",")
    return f"{x:,.0f} $".replace(",", " ")


def usd_exact(x):
    """Montant au dollar près (le simulateur montre des écarts de quelques milliers de dollars sur des millions)."""
    if x is None:
        return "—"
    return f"{'−' if x < -0.5 else ''}{abs(x):,.0f} $".replace(",", " ")


def num(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def graph_for(pid):
    return _graphs[pid]


def report_for(pid):
    return _reports[pid]


# Les 5 positions du site ; toutes ont une simulation : un bloc compatible (actif, oracle,
# pool AMM) affiche le graphe approfondi.
SITE_POSITIONS = POSITIONS["positions"]
ADVANCED_POSITIONS = {p["id"] for p in SITE_POSITIONS}


def sim_ctx_for(pid):
    """État de simulation relevé en ligne pour cette position."""
    return _sims[pid]


def available_positions():
    return SITE_POSITIONS


def parse(query):
    f = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
    positions = available_positions()
    main = next((p["id"] for p in positions if p.get("main")), positions[0]["id"])
    pid = f.get("pos", main)
    if pid not in [p["id"] for p in positions]:
        pid = main
    rep = report_for(pid)
    g = graph_for(pid)
    ids = {n["id"] for n in g["nodes"] if n["kind"] != "position"}
    incident = f.get("incident", rep["default_incident"])
    if incident not in ids:
        incident = rep["default_incident"]

    def fnum(name, default, lo, hi):
        try:
            v = float(f.get(name, default))
        except ValueError:
            v = default
        return min(max(v, lo), hi)

    snode = f.get("snode", "")
    if snode not in {n["id"] for n in g["nodes"]}:
        snode = ""
    comp = SIM.compatible_scenarios(g, snode) if snode else []
    stype = f.get("stype") if f.get("stype") in comp else (comp[0] if comp else "")
    spec = SIM.SCENARIOS.get(stype, {"default": 0.05, "max": 0.9})
    sim = {"snode": snode, "stype": stype, "sev": fnum("sev", 100 * spec["default"], 1, 100 * spec["max"]) / 100,
           "hours": 1 if f.get("hours") == "1" else 24, "run": "1" if f.get("run") == "1" and stype else "0"}
    ref = g["position"]["size_usd"]
    return {**sim, "pos": pid, "incident": incident, "amount": fnum("amount", ref, 10_000, 10_000_000_000)}


def link(q, **changes):
    return "/?" + urlencode({**q, "sev": round(q["sev"] * 100, 1), **changes})


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def controls(q, g):
    pos_opts = "".join(f'<option value="{p["id"]}"{" selected" if p["id"] == q["pos"] else ""}>{escape(p["label"])}</option>'
                       for p in available_positions())
    return f"""
    <form method="get" action="/" class="controls">
      <label class="wide">Position<select name="pos" onchange="this.form.submit()">{pos_opts}</select></label>
      <input type="hidden" name="incident" value="{escape(q['incident'], quote=True)}">
      <label>Montant de la position <span>$</span>
        <input type="number" name="amount" min="10000" step="any" value="{round(q['amount'])}"><small>Exemple : {usd(g["position"]["size_usd"])}. Change les montants en dollars.</small></label>
      <p class="wide controls-hint">Bloc en défaillance : <strong>cliquez sur un bloc du graphe</strong>. On suppose qu'il défaille ;
        la page calcule l'impact sur la position, sans probabilité.</p>
      <button type="submit">Afficher <span aria-hidden="true">→</span></button>
    </form>"""


NO_SCENARIO_WHY = {
    "chain": "Un arrêt de la chaîne bloque les transactions pendant sa durée, mais ne change aucun prix : "
             "il n'y a pas de perte de valeur à calculer. Son risque est de ne pas pouvoir agir pendant un autre incident.",
    "admin": "Une défaillance de gouvernance n'a pas de mécanisme de prix écrit : pas de simulation chiffrée.",
    "bridge": "Pas de mécanisme de prix modélisé pour un pont : pas de simulation chiffrée.",
}
NO_SCENARIO_WHY.update({
    "lending": "Pool Aave multi-collatéral : une créance douteuse passe d'abord par les mécanismes de couverture d'Aave, "
               "et les positions de chaque emprunteur ne sont pas publiées par l'API. Pas de chiffre (INCONNU).",
    "protocol": "Pas de modèle d'exploit pour ce contrat (seul le contrat SY de Pendle est modélisé).",
})


def failure_box(q, g):
    """Défaillance supposée (sans probabilité) : ce que la page calcule pour ce composant."""
    node = next((n for n in g["nodes"] if n["id"] == q["incident"]), None)
    if node is None:
        return ""
    comp = SIM.compatible_scenarios(g, node["id"])
    kind = KIND_FR.get(node["kind"], node["kind"])
    if comp:
        body = ("Le simulateur ci-dessous calcule son impact sur la position, nœud par nœud, avec les formules des "
                "protocoles : choisissez le type de défaillance, sa gravité et sa durée, puis lancez la simulation.")
    else:
        body = NO_SCENARIO_WHY.get(node["kind"], "Pas de simulation chiffrée pour ce type de composant.")
    return f"""
    <section class="answer partial" aria-live="polite">
      <p class="eyebrow">DÉFAILLANCE SUPPOSÉE — {escape(kind)}</p>
      <h2>{escape(node["name"])}</h2>
      <p>{escape(body)} Cliquez sur un autre bloc du graphe pour changer.</p>
    </section>"""


# ---------------------------------------------------------------------------
# Rester ou sortir (règle sans probabilité, docs/SIMULATION_MODEL.md § 14 ; risk_engine/exit_rule.py)
# ---------------------------------------------------------------------------

def sev_text(stype, sev):
    spec = SIM.SCENARIOS.get(stype, {})
    return f"+{num(sev * 100, 0)} points" if spec.get("unit") == "pts" else f"−{pct(sev, 0)}"


def decision_for(q, g):
    """Compare rester / vendre une partie / tout vendre pour la défaillance choisie (None si aucun scénario)."""
    if not q["snode"] or not q["stype"]:
        return None
    ctx = scaled_ctx(sim_ctx_for(q["pos"]), q["amount"] / g["position"]["size_usd"])
    if ctx is None:
        return {"ok": False, "why": "données de simulation indisponibles"}
    try:
        return exit_rule.decide(g, ctx, q["stype"], q["snode"], q["sev"], q["hours"])
    except (ValueError, KeyError, ZeroDivisionError) as exc:
        return {"ok": False, "why": f"calcul impossible ({exc})"}


def _verb(d, x):
    if d["kind"] == "retirer":
        return "RETIRER" if x >= 1 - 1e-9 else f"RETIRER {pct(x, 0)}"
    return "TOUT VENDRE" if x >= 1 - 1e-9 else f"VENDRE {pct(x, 0)}"


def recommendation_box(q, g, d):
    """La réponse, en haut de la page : rester ou sortir, et pourquoi, en dollars."""
    if d is None:
        return """
    <section class="verdict none"><p class="eyebrow">RESTER OU SORTIR ?</p>
      <h2 class="verdict-word">Pas de recommandation chiffrée pour ce bloc</h2>
      <p>Ce type de bloc n'a pas de scénario de défaillance chiffré. Cliquez sur un actif, un oracle, un pool AMM ou un marché de prêt.</p></section>"""
    if not d["ok"]:
        return f"""
    <section class="verdict none"><p class="eyebrow">RESTER OU SORTIR ?</p>
      <h2 class="verdict-word">Pas de recommandation chiffrée</h2><p>{escape(d["why"])}.</p></section>"""
    b, cat = d["best"], d["category"]
    amount_txt = ""
    scen = f'{escape(d["scenario_label"])} {sev_text(q["stype"], d["severity"])} sur <strong>{escape(d["node_name"])}</strong>'
    act = "sortir" if d["kind"] == "vendre" else "retirer"
    if cat == "inconnu":
        word, cls = "RESTER, SOUS RÉSERVE", "stay"
        why = ("Le simulateur ne trouve aucune perte chiffrable pour la position, mais certains effets ne sont pas chiffrés "
               "(voir « non chiffré » ci-dessous). Sortir coûterait " + usd(d["all"]["total"]) + " : rien ne le justifie avec les chiffres connus.")
    elif cat == "rester":
        word, cls = "RESTER", "stay"
        why = (f"Si ça arrive, rester fait perdre {usd(d['stay'])}. {act.capitalize()} ne protège pas mieux : "
               f"{_verb(d, d['all']['x']).lower()} coûterait {usd(d['all']['total'])} au total.") if d["stay"] > 1 else \
              (f"Rester ne fait rien perdre dans ce scénario ({usd(d['stay'])}). {act.capitalize()} coûterait {usd(d['all']['total'])}.")
    else:
        word = _verb(d, b["x"]) + (" (le maximum possible)" if cat == "tout" and d["cap"] < 1 - 1e-9 else "")
        cls = "exit" if cat == "tout" else "partial"
        amount_txt = f'{"vendre" if d["kind"] == "vendre" else "retirer"} {usd(b["x"] * d["size"])} sur {usd(d["size"])}' + (
            f', garder {usd((1 - b["x"]) * d["size"])}' if b["x"] < 1 - 1e-9 else "")
        why = (f"Si ça arrive, rester fait perdre <strong>{usd(d['stay'])}</strong>. {word.capitalize()} maintenant, avant que le choc "
               f"n'atteigne la position, coûte <strong>{usd(b['total'])}</strong> au total "
               f"({usd(b['exec'])} de coût de {'vente' if d['kind'] == 'vendre' else 'sortie'}"
               + (f" + {usd(b['rest'])} de perte sur ce qui reste" if b["rest"] > 1 else "") + ").")
        if cat == "partiel":
            why += f" Vendre plus coûterait plus cher : tout vendre, {usd(d['all']['total'])}."
    saved = d["stay"] - b["total"]
    tiles = [("Rester", usd(d["stay"]), "perte si la défaillance arrive"),
             ("Meilleure option", usd(b["total"]), _verb(d, b["x"]).capitalize() if b["x"] > 0 else "rester"),
             (("Tout vendre" if d["kind"] == "vendre" else "Tout retirer") if d["cap"] >= 1 - 1e-9 else f"Sortie maximale ({pct(d['cap'], 0)})",
              usd(d["all"]["total"]), "coût total"),
             ("Économie", usd(max(saved, 0.0)), "par rapport à rester")]
    tiles_html = "".join(f'<div class="vt"><span>{escape(a)}</span><strong>{v}</strong><small>{escape(c)}</small></div>'
                         for a, v, c in tiles)
    note = f'<p class="fine-print">{escape(d["note"])}.</p>' if d.get("note") else ""
    st = d.get("sim_total")
    if st is not None and abs(st - d["stay"]) > 1.0:
        note += (f'<p class="fine-print">Le simulateur affiche une perte de {usd(st)} : il compte aussi ce que coûterait une vente '
                 f'après le choc. Ici « rester » veut dire garder la position, sans vendre ({usd(d["stay"])} = perte de marché + liquidation).</p>')
    return f"""
    <section class="verdict {cls}" aria-live="polite">
      <p class="eyebrow">RESTER OU SORTIR ? — si {scen}</p>
      <h2 class="verdict-word">{escape(word)}</h2>
      {f'<p class="verdict-amount">Concrètement : {amount_txt}</p>' if cat in ("partiel", "tout") else ""}
      <p class="verdict-why">{why}</p>
      <div class="verdict-tiles">{tiles_html}</div>
      {note}
      <p class="fine-print">Aucune probabilité : on suppose que la défaillance arrive. Gravité et durée se changent dans le simulateur plus bas ; détail et courbe en bas de page.</p>
    </section>"""


def _curve(d):
    """Courbe : coût total (vente + perte restante) selon la part vendue maintenant."""
    rows = d["rows"]
    W, H, L, R, T, B = 680, 260, 70, 16, 16, 40
    ymax = max(max(r["total"] for r in rows), max(r["rest"] for r in rows), max(r["exec"] for r in rows), 1.0) * 1.1
    ymin = min(0.0, min(r["total"] for r in rows), min(r["rest"] for r in rows))
    fx = lambda x: L + x * (W - L - R)
    fy = lambda y: T + (ymax - y) / (ymax - ymin) * (H - T - B)
    def line(key, style):
        pts = " ".join(f"{fx(r['x']):.1f},{fy(r[key]):.1f}" for r in rows)
        return f'<polyline points="{pts}" fill="none" style="{style}"/>'
    grid = ""
    for i in range(5):
        y = ymin + (ymax - ymin) * i / 4
        grid += (f'<line x1="{L}" x2="{W - R}" y1="{fy(y):.1f}" y2="{fy(y):.1f}" style="stroke:var(--line,#1c3557);stroke-width:1"/>'
                 f'<text x="{L - 8}" y="{fy(y) + 4:.1f}" text-anchor="end">{escape(usd(y))}</text>')
    for i in range(0, 11, 2):
        x = i / 10
        grid += f'<text x="{fx(x):.1f}" y="{H - B + 18}" text-anchor="middle">{i * 10} %</text>'
    if d["cap"] < 1 - 1e-9:
        grid += (f'<line x1="{fx(d["cap"]):.1f}" x2="{fx(d["cap"]):.1f}" y1="{T}" y2="{H - B}" style="stroke:var(--exit);stroke-dasharray:4 4"/>'
                 f'<text x="{fx(d["cap"]) + 4:.1f}" y="{T + 12}">limite : {pct(d["cap"], 0)}</text>')
    b = d["best"]
    return f"""
      <svg class="curve" viewBox="0 0 {W} {H}" role="img" aria-label="Coût total selon la part vendue">
        {grid}
        {line("exec", "stroke:var(--partial);stroke-width:2;stroke-dasharray:6 4")}
        {line("rest", "stroke:var(--exit);stroke-width:2;stroke-dasharray:2 4")}
        {line("total", "stroke:var(--cyan);stroke-width:3")}
        <circle cx="{fx(b['x']):.1f}" cy="{fy(b['total']):.1f}" r="7" style="fill:var(--stay);stroke:#07101d;stroke-width:2"/>
        <text x="{W / 2:.0f}" y="{H - 4}" text-anchor="middle">part de la position {"vendue" if d["kind"] == "vendre" else "retirée"} maintenant</text>
      </svg>
      <div class="legend"><span class="lg"><i class="line" style="border-top-color:var(--cyan)"></i>coût total</span>
        <span class="lg"><i class="line" style="border-top-color:var(--partial);border-top-style:dashed"></i>coût de {"vente sur l'AMM" if d["kind"] == "vendre" else "sortie"}</span>
        <span class="lg"><i class="line" style="border-top-color:var(--exit);border-top-style:dotted"></i>perte sur ce qui reste</span>
        <span class="lg"><i style="background:var(--stay);border-radius:50%"></i>meilleur choix</span></div>"""


def decision_section(q, g, d):
    """En bas de page : le détail du calcul (tableau des stratégies et courbe)."""
    if not d or not d.get("ok"):
        return ""
    b, rows = d["best"], d["rows"]
    open_ = d["category"] == "partiel"   # la courbe justifie une vente partielle ; sinon elle reste repliée
    picks = {0.0: "Rester", b["x"]: "Meilleur choix", rows[-1]["x"]: "Sortie maximale"}
    show = sorted({0.0, b["x"], rows[-1]["x"]} | {r["x"] for r in rows if abs(r["x"] * 4 - round(r["x"] * 4)) < 1e-9})
    trs = ""
    for r in rows:
        if r["x"] not in show:
            continue
        after = "—" if r["x"] == 0 else (usd(r["exec_after"]) if r.get("exec_after") is not None else "impossible")
        tag = picks.get(r["x"], "")
        trs += (f'<tr class="{"best" if r is b else ""}"><td>{pct(r["x"], 0)}{(" · " + tag) if tag else ""}</td>'
                f'<td>{usd(r["exec"])}</td><td>{usd(r["rest"])}</td><td><strong>{usd(r["total"])}</strong></td>'
                + (f'<td>{after}</td>' if d["kind"] == "vendre" else "") + "</tr>")
    head_after = "<th>Coût de vente APRÈS le choc</th>" if d["kind"] == "vendre" else ""
    if d["kind"] == "vendre":
        how = ("Coût de vente = valeur de marché des PT vendus − ce que rapporte la vente sur le pool Pendle "
               "(formule on-chain de l'AMM Pendle V2, réserves actuelles). Perte sur ce qui reste = la position restante, "
               "re-simulée avec la même défaillance" + (" (la vente rembourse d'abord la dette : facteur de santé recalculé)."
                                                        if d["type"] == "morpho_borrow" else "."))
    else:
        how = ("Retirer ne coûte pas de glissement, mais l'argent retiré reste dans l'actif prêté : il subit encore sa baisse de prix. "
               "Le retrait est limité par la liquidité disponible du marché.")
    be = ""
    if d.get("breakeven_p") is not None:
        p = d["breakeven_p"]
        be = (f'<p class="breakeven">Et si on n\'est pas sûr que ça arrive ? Sortir coûte {usd(b["exec"])} tout de suite et évite '
              f'{usd(d["stay"] - b["rest"])} si ça arrive : sortir vaut le coup dès qu\'on pense que la défaillance a plus de '
              f'<strong>{pct(p, 1) if p >= 0.001 else "0 %"}</strong> de chances de se produire.</p>') if p > 0 else \
             '<p class="breakeven">Sortir ne coûte rien ici : c\'est intéressant dès qu\'il y a un doute.</p>'
    unk = ""
    if d.get("unknown"):
        unk = ("<details><summary>Non chiffré (" + str(len(d["unknown"])) + ")</summary><ul>" +
               "".join(f"<li>{escape(u)}</li>" for u in d["unknown"][:8]) + "</ul></details>")
    return f"""
    <section class="panel" id="decision" aria-labelledby="d-title">
      <div class="section-head"><div><p class="eyebrow">DÉCISION — COMBIEN VENDRE MAINTENANT ?</p>
        <h2 id="d-title">Coût total selon la part vendue, pour {usd(d["size"])}</h2></div></div>
      <p class="section-intro">Pour chaque part vendue maintenant (de 0 à 100 %, par pas de 5 %) :
        <strong>coût total = coût de la vente + perte sur ce qui reste</strong> si la défaillance arrive. Le point vert est le moins cher.</p>
      {"" if open_ else '<details class="sim-more"><summary>Voir le calcul : coût total selon la part vendue</summary>'}
      {_curve(d)}
      <table class="data small"><thead><tr><th>Part {"vendue" if d["kind"] == "vendre" else "retirée"}</th><th>Coût de {"vente" if d["kind"] == "vendre" else "sortie"} (avant le choc)</th>
        <th>Perte sur ce qui reste</th><th>Coût total</th>{head_after}</tr></thead><tbody>{trs}</tbody></table>
      <p class="fine-print">{escape(how)}{" La colonne « après le choc » montre le coût de la même vente une fois le choc arrivé, en plus de la baisse déjà subie : attendre ne fait pas éviter la perte." if d["kind"] == "vendre" else ""}</p>
      {be}
      {unk}
      {"" if open_ else "</details>"}
    </section>"""


def graph_section(q, g):
    cov = g["checks"]["coverage"]
    nodes = {n["id"]: n for n in g["nodes"]}
    drawing = svg.render(g, None, None, q["incident"], [],
                         node_href=lambda nid: link(q, incident=nid, snode=nid, run="0") + "#g-title")
    sh = cov["share_by_status"]
    cov_line = (f"{cov['dependencies']} dépendances · {cov['edges']} arêtes · profondeur {cov['max_depth']} · "
                f"{pct(cov['verified_share'], 0)} non supposées (on-chain {pct(sh['verified_onchain'], 0)}, "
                f"API {pct(sh['api'], 0)}, documentées {pct(sh['documented'], 0)}, supposées {pct(sh['assumed'], 0)})")
    errs = g["checks"]["errors"]
    checks = ("Contrôles automatiques : chaque arête a une preuve, pas de cycle, tout nœud atteint la position, "
              "aucune arête de prêt sans identifiant de marché réel — <strong>OK</strong>." if not errs else
              "Contrôles : " + "; ".join(escape(e) for e in errs))
    loops = "".join(f"<li>{escape(nodes.get(lp['from'], {}).get('name', lp['from']))} → "
                    f"{escape(nodes.get(lp['to'], {}).get('name', lp['to']))} ({escape(lp['mechanism'])}) : "
                    f"{escape(lp['note'])}</li>" for lp in g.get("loops", []))
    assumed = [e for e in g["edges"] if e["status"] == "assumed"]
    assumed_html = ("<p class='fine-print'>Aucune arête supposée : toutes viennent d'un contrat, d'une API ou d'une documentation.</p>"
                    if not assumed else "<ul>" + "".join(f"<li class='assumed'>À VÉRIFIER : {escape(e['from'])} → {escape(e['to'])}</li>" for e in assumed) + "</ul>")
    return f"""
    <section class="panel" aria-labelledby="g-title">
      <div class="section-head"><div><p class="eyebrow">01 / DÉCOUVRIR</p><h2 id="g-title">Ce dont dépend la position — découvert automatiquement</h2></div>
        <span class="section-tag">{escape(cov_line)}</span></div>
      <p class="section-intro">Une flèche A → B veut dire « si A cède, B est touché ». <strong>Cliquez sur un bloc</strong> pour supposer sa défaillance ;
        survolez un nœud ou une flèche pour voir la preuve et son heure de relevé.</p>
      <div class="graph-wrap">{drawing}</div>
      {svg.legend(g)}
      <p class="fine-print">{checks}</p>
      {assumed_html}
      {"<details open><summary>Boucles réflexives détectées (" + str(len(g.get('loops', []))) + ")</summary><ul>" + loops + "</ul></details>" if loops else ""}
    </section>"""


# ---------------------------------------------------------------------------
# Simulateur de défaillance (impact SI l'incident se produit, sans probabilité)
# ---------------------------------------------------------------------------

STATUS_FR = {"origin": "défaillance", "changed": "modifié", "unknown": "INCONNU", "estimate": "estimation",
             "no_effect": "atteint, inchangé"}


def fmt_state(variable, v):
    if v is None:
        return "—"
    if "USD" in variable and "produit" not in variable:
        return f"{v:,.4f} $".replace(",", " ").replace(".", ",")
    if "produit" in variable:
        return usd(v)
    if "santé" in variable:
        return num(v, 3)
    return "× " + num(v, 4)


def step_label(st):
    short = {"prix de marché (USD)": "prix", "prix rapporté (relatif)": "oracle", "prix rapporté": "oracle",
             "prix de marché": "prix", "collatéral vu par le protocole (relatif)": "collat. (oracle)",
             "liquidité du pool": "liquidité", "liquidité du pool (relative)": "liquidité",
             "facteur de santé": "HF", "prix de marché du PT (USD)": "prix PT"}.get(st["variable"], st["variable"])
    if st["status"] == "unknown":
        return "INCONNU" + (f" · {short} {pct(st['change_pct'], 1)}" if st.get("change_pct") else "")
    if st["status"] == "no_effect":
        return f"{short} inchangé"
    if st["variable"] == "facteur de santé":
        return f"HF {num(st['before'], 3)} → {num(st['after'], 3)}"
    if st.get("change_pct") is not None:
        return f"{short} {'+' if st['change_pct'] > 0 else ''}{pct(st['change_pct'], 1)}"
    return STATUS_FR.get(st["status"], st["status"])


def sim_overlay(res):
    last = {}
    for st in res["steps"]:
        last[st["node"]] = st
    out = {}
    for nid, a in res["affected"].items():
        st = last.get(nid)
        tip = ""
        if st:
            tip = (f"État initial : {fmt_state(st['variable'], st['before'])}\nÉtat simulé : {fmt_state(st['variable'], st['after'])}"
                   f"\nMécanisme : {st['mechanism']}\nModèle : {st.get('formula') or '—'}")
        if a["unknown"]:
            tip += "\nNon chiffré : " + " ; ".join(a["unknown"][:3])
        out[nid] = {"status": a["status"], "label": step_label(st) if st else STATUS_FR.get(a["status"], ""), "tip": tip}
    pos = res["position"]["id"]
    tot = res["result"].get("total")
    if tot:
        lab = f"valeur {'+' if tot['change_pct'] > 0 else ''}{pct(tot['change_pct'], 1)}"
        lend = res["result"].get("lending")
        if lend:
            lab = f"HF {num(lend['hf0'], 2)} → {num(lend['hf1'], 2)}" + (" LIQUIDABLE" if lend["eligible"] else "")
        out[pos] = {"status": "changed" if abs(tot["loss_usd"]) > 0.5 else ("unknown" if res["result"]["unknown"] else "no_effect"),
                    "label": lab, "tip": f"Perte totale : {usd(tot['loss_usd'])} ({pct(-tot['change_pct'], 2)})"}
    return out


def sim_form(q, g):
    n = next((x for x in g["nodes"] if x["id"] == q["snode"]), None)
    hidden = "".join(f'<input type="hidden" name="{k}" value="{escape(str(v), quote=True)}">' for k, v in
                     (("pos", q["pos"]), ("amount", round(q["amount"])), ("incident", q["incident"]), ("snode", q["snode"])))
    if not n:
        return ('<p class="sim-hint">① Cliquez un nœud du graphe ci-dessous : un <strong>actif</strong> (chute de prix / dépeg), '
                'un <strong>oracle</strong> (prix rapporté faux) ou un <strong>pool AMM</strong> (crise de liquidité).</p>')
    comp = SIM.compatible_scenarios(g, n["id"])
    if not comp:
        return (f'<p class="sim-hint">Nœud choisi : <strong>{escape(n["name"])}</strong> ({escape(KIND_FR.get(n["kind"], n["kind"]))}). '
                'Aucun scénario de défaillance n\'est défini pour ce type de nœud : choisissez un actif, un oracle ou un pool AMM.</p>')
    opts = "".join(f'<option value="{k}"{" selected" if k == q["stype"] else ""}>{escape(SIM.SCENARIOS[k]["label"])}</option>'
                   for k in comp)
    spec = SIM.SCENARIOS[q["stype"]]
    sev = round(q["sev"] * 100, 1)
    sign, unit = spec.get("sign", "−"), spec.get("unit", "%")
    return f"""
      <form method="get" action="/#sim" class="sim-form">{hidden}
        <div class="sim-node"><span>② Nœud en défaillance</span><strong>{escape(n["name"])}</strong><small>{escape(KIND_FR.get(n["kind"], n["kind"]))}</small></div>
        <label>③ Scénario<select name="stype">{opts}</select><small>{escape(spec["help"])}</small></label>
        <label>④ Sévérité ({escape(spec["variable"])}) <output id="sev-out">{sign}{num(sev, 0) if sev == int(sev) else num(sev, 1)} {unit}</output>
          <input type="range" name="sev" min="1" max="{round(spec['max'] * 100)}" step="1" value="{sev}"
            oninput="document.getElementById('sev-out').value='{sign}'+this.value+' {unit}'"></label>
        <label>Durée de l'incident<select name="hours"><option value="24"{" selected" if q["hours"] == 24 else ""}>24 h (persistant)</option>
          <option value="1"{" selected" if q["hours"] == 1 else ""}>1 h (bref)</option></select>
          <small>Compte pour les méta-oracles (délai de contestation avant bascule).</small></label>
        <button type="submit" name="run" value="1">⑤ Lancer la simulation <span aria-hidden="true">→</span></button>
      </form>"""


def sim_dashboard(res):
    R = res["result"]
    if R.get("error"):
        return f'<p class="error">Simulation impossible : {escape(R["error"])} — {escape("; ".join(R.get("unknown", [])))}</p>'
    T, L = R["total"], R.get("lending")
    ex0, ex1 = R.get("exit_before"), R.get("exit_after")
    blocked = T.get("blocked_usd") or 0.0
    share = (lambda v: f"{pct(v / T['initial'], 2)} de la valeur initiale" if T["initial"] else "")
    spec = SIM.SCENARIOS[res["scenario"]]
    sev_txt = (f"+{num(res['severity'] * 100, 0)} points" if spec.get("unit") == "pts" else f"−{pct(res['severity'], 0)}")
    tiles = [("Défaillance", f"{escape(res['scenario_label'])} {sev_txt}", escape(svg._clip(res["node_name"], 48)), ""),
             ("Valeur initiale de la position", usd_exact(T["initial"]), "fonds propres au prix de marché" if L else "au prix de marché", ""),
             ("Valeur après l'incident", usd_exact(T["after"]),
              "liquidation simulée incluse" if L and L["eligible"] else ("hors part bloquée" if blocked else ""), ""),
             ("Perte financière", usd_exact(T["loss_usd"]), share(T["loss_usd"]) + (" (hors part bloquée)" if blocked else ""), "")]
    if blocked:
        tiles += [("Sortie complète IMPOSSIBLE", f"{usd_exact(blocked)} bloqués",
                   "invendables sur l'AMM maintenant ; remboursables à l'échéance du PT", "alert"),
                  ("Perte si sortie forcée maintenant", usd_exact(T["loss_if_forced_exit_usd"]),
                   share(T["loss_if_forced_exit_usd"]) + " — part bloquée comptée comme perdue", "alert")]
    if L:
        thr = res.get("liquidation_threshold") or {}
        tiles += [("LTV (dette / collatéral oracle)", f"{pct(L['ltv0'], 1)} → {pct(L['ltv1'], 1)}", f"LLTV {pct(L['lltv'], 1)}", ""),
                  ("Facteur de santé", f"{num(L['hf0'], 3)} → {num(L['hf1'], 3)}", "liquidable sous 1", ""),
                  ("Liquidation", "ÉLIGIBLE" if L["eligible"] else "non éligible",
                   ("éligible ≠ liquidée : il faut qu'un liquidateur agisse" if L["eligible"] else "position saine"),
                   "alert" if L["eligible"] else ""),
                  ("Choc minimal pour liquider", pct(thr["severity"], 2) if thr.get("severity") is not None else "aucun",
                   escape(thr.get("text", "")), "")]
    if ex0 and ex1:
        tiles += [("Impact de prix à la sortie", f"{pct(ex0['price_impact'], 2)} → {pct(ex1['price_impact'], 2)}", "AMM seul (courbe Pendle V2)", ""),
                  ("Produit exécutable", f"{usd_exact(ex0['proceeds_usd'])} → {usd_exact(ex1['proceeds_usd'])}",
                   ("sortie complète possible" if ex1["full_exit"] else
                    f"sortie complète IMPOSSIBLE : {svg.fmt_pct(ex1['sold_units'] / ex1['units'], 0)} vendable")
                   + (" — après les liquidations des autres emprunteurs" if any(f["quantified"] for f in res.get("forced_liquidations") or []) else ""),
                   "alert" if not ex1["full_exit"] else "")]
    kpis = "".join(f'<div class="kpi{" " + k if k else ""}"><span>{a}</span><strong>{b}</strong><small>{c}</small></div>'
                   for a, b, c, k in tiles)
    rows = ""
    extra = []
    if blocked:
        extra = [{"key": "forced", "label": "Impact si sortie forcée maintenant (part bloquée comptée comme perdue)",
                  "initial": T["initial"], "after": T["after_if_forced_exit"], "change_usd": -T["loss_if_forced_exit_usd"],
                  "status": "borne haute : la part bloquée n'est perdue que si l'on doit sortir avant le retour de la liquidité",
                  "method": "Impact financier total + valeur non exécutable maintenant"}]
    for m in R["metrics"] + [dict(T, key="total", status="calculé")] + extra:
        if m.get("initial") is None:
            rows += (f'<tr><td>{escape(m["label"])}</td><td colspan="4" class="muted">{escape(m["status"])}</td>'
                     f'<td>{escape(m.get("method") or "")}</td></tr>')
            continue
        delta = m.get("change_usd", m["after"] - m["initial"])
        if m["key"] == "blocked":
            delta = -m["after"]  # valeur qu'on ne peut plus encaisser maintenant
        cls = {"total": ' class="total"', "forced": ' class="forced"'}.get(m["key"], ' class="excluded"' if m.get("excluded_from_total") else "")
        rows += (f'<tr{cls}><td>{escape(m["label"])}<br><small>{escape(m["status"])}</small></td><td>{usd_exact(m["initial"])}</td>'
                 f'<td>{usd_exact(m["after"])}</td><td>{"+" if delta > 0.5 else ""}{usd_exact(delta)}</td>'
                 f'<td>{pct(delta / T["initial"], 2) if T["initial"] else "—"}</td>'
                 f'<td>{escape(m.get("method") or "")}{"<br><small>" + escape(m["note"]) + "</small>" if m.get("note") else ""}</td></tr>')
    names = {n["id"]: n["name"] for n in graph_for_id(res)["nodes"]}
    path = ""
    if res["path"]:
        path = escape(svg._clip(res["node_name"], 40)) + "".join(
            f' <span class="arrow">—{escape(svg.MECH_SHORT.get(p["mechanism"], p["mechanism"]))}→</span> {escape(svg._clip(names.get(p["to"], p["to"]), 40))}'
            for p in res["path"])
    steps = ""
    for st in res["steps"]:
        hyp = "; ".join(st.get("assumptions", []) + st.get("assumptions_extra", []))
        steps += (f'<tr class="st-{st["status"]}"><td><strong>{escape(svg._clip(st["name"], 44))}</strong><br><small>{escape(STATUS_FR.get(st["status"], st["status"]))}</small></td>'
                  f'<td>{escape(st["variable"])}</td><td>{fmt_state(st["variable"], st["before"])}</td><td>{fmt_state(st["variable"], st["after"])}</td>'
                  f'<td>{escape(st["mechanism"])}<br><small>{escape(st.get("dependency") or "")} · {escape(st.get("formula") or "")}</small></td>'
                  f'<td><small>{escape(hyp) or "—"}</small></td></tr>')
    unknown = R.get("unknown") or []
    unk = ("<ul class='plain'>" + "".join(f"<li class='flag'>{escape(u)}</li>" for u in unknown) + "</ul>") if unknown else \
        "<p class='fine-print'>Aucune transmission non chiffrée n'atteint la position.</p>"
    sec = "".join(f"<li>{escape(names.get(e['from'], e['from']))} → {escape(names.get(e['to'], e['to']))} : {escape(e.get('text', ''))} "
                  f"<em>(estimation avec un emprunteur moyen, non incluse dans le total)</em></li>" for e in res["secondary_effects"])
    forced = forced_html(res.get("forced_liquidations") or [])
    edges = "".join(f"<tr><td>{escape(svg._clip(names.get(e['from'], e['from']), 32))} → {escape(svg._clip(names.get(e['to'], e['to']), 32))}</td>"
                    f"<td>{escape(e['dependency'])}</td><td>{escape(e['transmission'])}</td><td>{escape(e['formula'])}</td>"
                    f"<td>{escape(', '.join(e['params']) or '—')}</td><td>{escape(e['source'])}</td>"
                    f"<td>{escape('; '.join(e['assumptions']) or '—')}</td><td>{'oui' if e['quantified'] else 'INCONNU'}</td></tr>"
                    for e in res["edges"])
    data = res["data"]
    ev = "".join(f"<li>{escape(x['label'])} — <em>{escape({'verified_onchain': 'lu on-chain', 'api': 'API'}.get(x['status'], x['status']))}, "
                 f"relevé {escape(x['fetched_at'])}</em></li>" for x in data["evidence"])
    assumed = (f"<li class='flag'>HYPOTHÈSE : prix de {escape(', '.join(data['assumed_prices']))} = 1 $ (aucun prix relevé)</li>"
               if data.get("assumed_prices") else "")
    missing = "".join(f"<li class='flag'>Donnée manquante : {escape(m)}</li>" for m in data.get("missing", []))
    return f"""
      <div class="kpis">{kpis}</div>
      <p class="sim-path"><strong>Chemin de propagation :</strong> {path or "la défaillance n'atteint pas la position par un chemin modifiant sa valeur."}</p>
      {forced}
      <details class="sim-more"><summary>Voir le détail du calcul (sévérité par cause, effets non chiffrés, étapes, sources)</summary>
      <h3>Sévérité financière : perte par cause</h3>
      <table class="data sim-metrics"><thead><tr><th>Métrique</th><th>Avant</th><th>Après</th><th>Variation</th><th>% valeur initiale</th><th>Méthode</th></tr></thead><tbody>{rows}</tbody></table>
      <p class="fine-print">{escape(T["note"]) + ". " if T.get("note") else ""}Pourcentages rapportés à la valeur initiale de la position.</p>
      <h3>Transmissions non chiffrées (INCONNU)</h3>{unk}
      {"<h3>Effets secondaires estimés</h3><ul class='plain'>" + sec + "</ul>" if sec else ""}
      <details><summary>Détail de chaque étape ({len(res["steps"])})</summary>
        <table class="data small"><thead><tr><th>Nœud</th><th>Variable</th><th>Avant</th><th>Après</th><th>Mécanisme et modèle</th><th>Hypothèses</th></tr></thead><tbody>{steps}</tbody></table></details>
      <details><summary>Modèle de chaque dépendance traversée ({len(res["edges"])})</summary>
        <table class="data small"><thead><tr><th>Arête</th><th>Type</th><th>Transmet</th><th>Modèle</th><th>Paramètres</th><th>Source</th><th>Hypothèses</th><th>Chiffré</th></tr></thead><tbody>{edges}</tbody></table></details>
      <details><summary>Données utilisées : vérifiées et hypothèses</summary><ul class="evidence">{ev}{assumed}{missing}</ul></details>
      </details>"""


def forced_html(rows):
    """Liquidations des autres emprunteurs (positions réelles), incluses dans les chiffres ci-dessus."""
    if not rows:
        return ""
    trs = ""
    for f in rows:
        cap = (f"{num(f['capacity_before'], 0)} → {num(f['capacity_after'], 0)} PT" if f["capacity_before"] is not None
               else "pas de pool modélisé")
        trs += (f"<tr><td>{escape(svg._clip(f['market_name'], 44))}<br><small>{f['borrowers']} emprunteurs réels</small></td>"
                f"<td>{f['liquidations']}</td><td>{num(f['collateral_sold'], 0)}</td><td>{usd_exact(f['debt_repaid_usd'])}</td>"
                f"<td>{cap}<br><small>prix spot {'+' if f['spot_change'] > 0 else ''}{pct(f['spot_change'], 2)}</small></td>"
                f"<td>{f['stuck_count']}<br><small>{usd_exact(f['stuck_debt_usd'])} de dette</small></td>"
                f"<td>{usd_exact(f['bad_debt_usd'])}</td>"
                f"<td>{'incluses' if f['quantified'] else 'effet sur le prix non chiffré'}</td></tr>")
    return f"""
      <h3>Liquidations des autres emprunteurs</h3>
      <p class="fine-print">Positions réelles (API Morpho). Un liquidateur n'agit que si la revente couvre la dette remboursée et ses frais ;
        le collatéral saisi est revendu sur le pool Pendle avant ta sortie. Les ventes de panique ne sont pas chiffrées.</p>
      <table class="data small"><thead><tr><th>Marché</th><th>Liquidations</th><th>Collatéral revendu</th><th>Dette remboursée</th>
        <th>Capacité du pool</th><th>Liquidables non liquidés</th><th>Créance douteuse potentielle</th><th>Dans les chiffres</th></tr></thead>
        <tbody>{trs}</tbody></table>"""


def graph_for_id(res):
    return graph_for(res["position"]["id"].split(":", 1)[1])


def scaled_ctx(ctx, k):
    """Même simulation pour un autre montant : la position (taille, unités, garantie, dette) est mise à l'échelle k."""
    if ctx is None or abs(k - 1) < 1e-12:
        return ctx
    pc = dict(ctx["position"])
    for key in ("size_usd", "units", "collateral_units", "debt_units"):
        if isinstance(pc.get(key), (int, float)):
            pc[key] = pc[key] * k
    return {**ctx, "position": pc}


def simulation_section(q, g):
    head = """<section class="panel" id="sim" aria-labelledby="s-title">
      <p class="eyebrow">SIMULER — SI CE MAILLON CÈDE, COMBIEN PERD LA POSITION ?</p>
      <h2 id="s-title">Simulateur de défaillance : propagation et sévérité financière</h2>
      <p class="section-intro">Aucune probabilité ici : on suppose que la défaillance se produit et on calcule ses conséquences, nœud par nœud,
        avec les formules des protocoles (oracle Morpho, règle de liquidation Morpho Blue, courbe de l'AMM Pendle V2).
        Une transmission impossible à modéliser est marquée <strong>INCONNU</strong> et n'entre pas dans les chiffres.</p>"""
    ctx = scaled_ctx(sim_ctx_for(q["pos"]), q["amount"] / g["position"]["size_usd"])
    if ctx is None:
        return head + "<p class='error'>Données de simulation indisponibles depuis les sources en ligne.</p></section>"
    res, err = None, ""
    if q["run"] == "1":
        try:
            res = SIM.simulate(g, ctx, q["stype"], q["snode"], q["sev"], q["hours"])
        except ValueError as exc:
            err = f"<p class='error'>{escape(str(exc))}</p>"
    if res:
        overlay = sim_overlay(res)
        crossed = {(e["from"], e["to"]) for e in res["edges"]}
        path = [res["node"]] + [p["to"] for p in res["path"]]
    else:
        overlay = {q["snode"]: {"status": "origin", "label": "nœud choisi"}} if q["snode"] else None
        crossed, path = set(), []
    drawing = svg.render(g, path=path, sim=overlay, sim_edges=crossed,
                         node_href=lambda nid: link(q, incident=nid, snode=nid, run="0") + "#sim")
    legend = ('<div class="legend"><span class="lg"><i style="background:#d1495b;border-color:#9e1b32"></i>défaillance</span>'
              '<span class="lg"><i style="border-color:#d1495b"></i>état modifié</span>'
              '<span class="lg"><i style="border-color:#e08a2c;border-style:dashed"></i>INCONNU (non chiffré)</span>'
              '<span class="lg"><i style="border-color:#8a96a3;border-style:dashed"></i>atteint, inchangé</span>'
              '<span class="lg"><i class="line hot"></i>chemin vers la position</span>'
              '<span class="lg"><i class="line" style="border-top-color:#e08a2c"></i>dépendance traversée</span></div>') if res else ""
    return (head + sim_form(q, g) + err +
            f'<div class="graph-wrap" id="sim-graph">{drawing}</div>{legend}' +
            # la position est à droite du graphe : on l'affiche d'abord
            '<script>(function(){var w=document.getElementById("sim-graph");if(w)w.scrollLeft=w.scrollWidth;})();</script>' +
            (f'<p class="fine-print">Survolez un nœud pour son état initial, son état simulé et le mécanisme. Cliquez un autre nœud pour changer de cible.</p>'
             if res else "") +
            (sim_dashboard(res) if res else "") + "</section>")


def render_page(query: str) -> str:
    with _refresh_lock:
        return _render_page(query)


def _render_page(query: str) -> str:
    args = parse_qs(query)
    main = next((p["id"] for p in SITE_POSITIONS if p.get("main")), SITE_POSITIONS[0]["id"])
    pid = args.get("pos", [main])[0]
    if pid not in {p["id"] for p in SITE_POSITIONS}:
        pid = main
    ensure_live_data(pid, force=args.get("refresh") == ["1"])
    q = parse(query)
    g = graph_for(q["pos"])
    rep = report_for(q["pos"])
    size = q["amount"]
    dec = decision_for(q, g)
    ts = rep["data_fetched_at"]
    badge = f"Données relevées en ligne le {escape((ts.get('newest') or '?')[:16].replace('T', ' '))} UTC"
    subs = {
        "position_name": escape(g["position"]["label"]),
        "position_size": usd(size),
        "horizon": num(M.horizon_days(g), 0),
        "badge": badge,
        "refresh_url": "/?" + escape(urlencode({"pos": q["pos"], "refresh": 1}), quote=True),
        "controls": controls(q, g),
        "answer": recommendation_box(q, g, dec) + failure_box(q, g),
        "graph": graph_section(q, g),
        "simulation": simulation_section(q, g) if q["pos"] in ADVANCED_POSITIONS else "",
        "decision": decision_section(q, g, dec),  # règle § 14 : coût réel de sortie sur l'AMM, sans probabilité
    }
    return TEMPLATE.substitute(subs)


def api_simulate(query):
    with _refresh_lock:
        return _api_simulate(query)


def _api_simulate(query):
    """/api/simulate?pos=…&node=…&type=price_shock|oracle_failure|liquidity_crisis&severity=0.05&hours=24"""
    f = {k: v[0] for k, v in parse_qs(query).items()}
    pid = f.get("pos", "")
    if pid not in [p["id"] for p in available_positions()]:
        return {"error": f"position inconnue : {pid}"}
    ensure_live_data(pid)
    if sim_ctx_for(pid) is None:
        return {"error": f"position inconnue ou sans données de simulation : {pid}"}
    g = graph_for(pid)
    node = f.get("node", "")
    stype = f.get("type") or (SIM.compatible_scenarios(g, node) or [""])[0]
    try:
        sev = float(f.get("severity", SIM.SCENARIOS.get(stype, {}).get("default", 0.05)))
        hours = float(f.get("hours", SIM.DEFAULT_HOURS))
        return SIM.simulate(g, sim_ctx_for(pid), stype, node, sev, hours)
    except ValueError as exc:
        return {"error": str(exc), "compatible": SIM.compatible_scenarios(g, node)}


def selected_position(query):
    """Choisit la position du lien, ou la position principale si l'identifiant est absent."""
    requested = parse_qs(query).get("pos", [None])[0]
    positions = available_positions()
    main = next((p["id"] for p in positions if p.get("main")), positions[0]["id"])
    return requested if requested in {p["id"] for p in positions} else main


def live_analysis(pid, force=False, query=""):
    """Analyse globale à partir du relevé en mémoire, sans lire d'ancien rapport."""
    with _refresh_lock:
        ensure_live_data(pid, force=force)
        graph = graph_for(pid)
        report = report_for(pid)
        context = sim_ctx_for(pid)
        amount = parse(query)["amount"]
        return analyze(graph, report, context, amount=amount), graph, report, context


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        pid = selected_position(request.query)
        try:
            if request.path == "/static/style.css":
                body, content_type, status = STYLE, "text/css; charset=utf-8", 200
            elif request.path == "/health":
                body, content_type, status = b"ok", "text/plain; charset=utf-8", 200
            elif request.path == "/":
                body = current.render_current(request.query, pid, sys.modules[__name__]).encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 200
            elif request.path in ("/global", "/api/global"):
                result, graph, report, context = live_analysis(
                    pid, force=parse_qs(request.query).get("refresh") == ["1"], query=request.query)
                if request.path == "/api/global":
                    body = json.dumps(result, ensure_ascii=False).encode("utf-8")
                    content_type, status = "application/json; charset=utf-8", 200
                else:
                    body = web.render_global(result, graph, report, context,
                                             available_positions()).encode("utf-8")
                    content_type, status = "text/html; charset=utf-8", 200
            elif request.path == "/api/simulate":
                body = json.dumps(api_simulate(request.query), ensure_ascii=False).encode("utf-8")
                content_type, status = "application/json; charset=utf-8", 200
            elif request.path.startswith("/api/report/"):
                pid = request.path.rsplit("/", 1)[-1]
                with _refresh_lock:
                    ensure_live_data(pid)
                    body = json.dumps(report_for(pid), ensure_ascii=False).encode("utf-8")
                content_type, status = "application/json; charset=utf-8", 200
            else:
                body, content_type, status = b"Page introuvable", "text/plain; charset=utf-8", 404
        except Exception as exc:
            if request.path in ("/", "/global", "/api/global"):
                body = web.render_unavailable(pid, exc).encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 503
            else:
                body = f"<h1>Erreur</h1><pre>{escape(type(exc).__name__)}: {escape(str(exc))}</pre>".encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 500
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if request.path in ("/", "/global", "/api/global"):
            self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Belem : analyses actuelle et globale des risques")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    main = selected_position("")
    print("Collecte en ligne de la position principale…", flush=True)
    try:
        ensure_live_data(main)
    except Exception as exc:
        print(f"Données indisponibles au démarrage : {type(exc).__name__}: {exc}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Ouvrez http://127.0.0.1:{args.port} (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()
