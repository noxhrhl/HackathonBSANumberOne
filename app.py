"""Page locale : une seule histoire, de la position à la décision (bibliothèque standard uniquement).

  python app.py            puis ouvrir http://127.0.0.1:8000
  python app.py --port 8001

La page lit data/graphs/*.json et data/reports/*.json (produits par pipeline.py) et
recalcule la décision quand on change l'incident, sa probabilité ou le coût de sortie.
"""

import argparse
import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlencode, urlparse

from risk_engine import decide as D
from risk_engine import model as M
from risk_engine import rules, svg
from risk_engine.risk import assess

ROOT = Path(__file__).resolve().parent
TEMPLATE = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
STYLE = (ROOT / "static" / "style.css").read_bytes()
POSITIONS = json.loads((ROOT / "data" / "positions.json").read_text(encoding="utf-8"))
EVIDENCE = json.loads((ROOT / "data" / "evidence.json").read_text(encoding="utf-8"))["incidents"]
KIND_FR = {k: v[2] for k, v in svg.KIND_STYLE.items()}

_models, _graphs, _reports, _book = {}, {}, {}, {}


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


def num(x, d=2):
    return f"{x:.{d}f}".replace(".", ",")


def graph_for(pid):
    if pid not in _graphs:
        _graphs[pid] = json.loads((ROOT / "data" / "graphs" / f"{pid}.json").read_text(encoding="utf-8"))
    return _graphs[pid]


def report_for(pid):
    if pid not in _reports:
        _reports[pid] = json.loads((ROOT / "data" / "reports" / f"{pid}.json").read_text(encoding="utf-8"))
    return _reports[pid]


def book_report():
    p = ROOT / "data" / "reports" / "book.json"
    if "book" not in _book and p.exists():
        _book["book"] = json.loads(p.read_text(encoding="utf-8"))
    return _book.get("book")


def pm_for(pid, scenario="base", propagation="max", **ov):
    key = (pid, scenario, propagation, tuple(sorted(ov.items())))
    if key not in _models:
        _models[key] = M.PositionModel(graph_for(pid), scenario, propagation, overrides=ov or None)
    return _models[key]


def available_positions():
    out = []
    for p in POSITIONS["positions"]:
        if (ROOT / "data" / "reports" / f"{p['id']}.json").exists():
            out.append(p)
    return out


def parse(query):
    f = {k: v[0] for k, v in parse_qs(query, keep_blank_values=True).items()}
    positions = available_positions()
    if not positions:
        raise RuntimeError("Aucun rapport : lancez d'abord `python pipeline.py --all --offline --book`.")
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

    return {"pos": pid, "incident": incident, "p": fnum("p", 100 * rep["default_incident_p"], 0, 100) / 100,
            "c1": fnum("c1", 100 * rep["exit"]["stress"]["c1"], 0, 100) / 100,
            "regime": f.get("regime", "crise") if f.get("regime") in ("crise", "normal") else "crise",
            "view": f.get("view", "neutre") if f.get("view") in ("neutre", "prudent") else "neutre",
            "prop": f.get("prop", "max") if f.get("prop") in ("max", "sum") else "max"}


def exit_model(rep, q):
    if q["regime"] == "normal":
        n = rep["exit"]["normal"]
        return D.ExitCost(n["c1"], n["capacity"], n["label"], n["shape"], [tuple(x) for x in n["points"]])
    return D.ExitCost(q["c1"], 1.0, f"Crise : {pct(q['c1'], 0)} pour tout vendre (défaut = chiffre de Belem)", "quadratic")


def link(q, **changes):
    return "/?" + urlencode({**q, "p": round(q["p"] * 100), "c1": round(q["c1"] * 100, 2), **changes})


def relevant_evidence(kind, mechanisms):
    return [e for e in EVIDENCE if kind in e["kinds"] or any(m in e["mechanisms"] for m in mechanisms)]


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def controls(q, g, pm):
    pos_opts = "".join(f'<option value="{p["id"]}"{" selected" if p["id"] == q["pos"] else ""}>{escape(p["label"])}</option>'
                       for p in available_positions())
    groups = {}
    for n in g["nodes"]:
        if n["kind"] == "position":
            continue
        groups.setdefault(n["kind"], []).append(n)
    inc_opts = ""
    for kind in svg.KIND_STYLE:
        if kind not in groups:
            continue
        opts = "".join(
            f'<option value="{escape(n["id"], quote=True)}"{" selected" if n["id"] == q["incident"] else ""}>'
            f'{escape(svg._clip(n["name"], 60))} — I = {num(pm.impact.get(n["id"], 0), 3)}</option>'
            for n in sorted(groups[kind], key=lambda n: -pm.impact.get(n["id"], 0)))
        inc_opts += f'<optgroup label="{KIND_FR.get(kind, kind)}">{opts}</optgroup>'
    sel = lambda name, val: " selected" if q[name] == val else ""  # noqa: E731
    hint = rules.INCIDENT_P_HINTS.get(pm.node_by_id[q["incident"]]["kind"], "")
    return f"""
    <form method="get" action="/" class="controls">
      <label class="wide">Position<select name="pos" onchange="this.form.submit()">{pos_opts}</select></label>
      <label class="wide">Composant en alerte<select name="incident">{inc_opts}</select></label>
      <label>Probabilité que l'alerte devienne un dommage <span>%</span>
        <input type="number" name="p" min="0" max="100" step="1" value="{round(q['p'] * 100)}"><small>{escape(hint)}</small></label>
      <label>Coût pour tout vendre en crise, C(1) <span>%</span>
        <input type="number" name="c1" min="0" max="100" step="0.5" value="{round(q['c1'] * 100, 2)}"><small>Défaut : 11 % (énoncé de Belem).</small></label>
      <label>Régime de sortie<select name="regime"><option value="crise"{sel('regime', 'crise')}>Crise (C(x) = c·x²)</option>
        <option value="normal"{sel('regime', 'normal')}>Hors crise (cotation réelle)</option></select></label>
      <label>Vue du risque<select name="view"><option value="neutre"{sel('view', 'neutre')}>Neutre (perte attendue)</option>
        <option value="prudent"{sel('view', 'prudent')}>Prudente (pire 5 %)</option></select></label>
      <label>Propagation<select name="prop"><option value="max"{sel('prop', 'max')}>Chemin le plus fort</option>
        <option value="sum"{sel('prop', 'sum')}>Somme des chemins (plafonnée)</option></select></label>
      <button type="submit">Recalculer <span aria-hidden="true">→</span></button>
    </form>"""


def answer_box(q, g, pm, st, dec, cost, sweep_info, size):
    s = dec["strategies"]
    x = dec["x_star"]
    inc = pm.node_by_id[q["incident"]]
    if dec["category"] == "rester":
        head = "Rester : ne rien vendre."
    elif dec["category"] == "tout vendre":
        head = "Tout vendre maintenant."
    else:
        head = f"Vendre {pct(x, 0)} maintenant, garder {pct(1 - x, 0)}."
    key = "expected" if q["view"] == "neutre" else "worst5"
    lab = "Coût attendu" if q["view"] == "neutre" else "Coût dans les 5 % pires cas"
    body = (f"{lab} {pct(s['partiel'][key])} ({usd(size * s['partiel'][key])}) contre "
            f"{pct(s['tout_vendre'][key])} (tout vendre) et {pct(s['rester'][key])} (tout garder).")
    tail = f"Pire 5 % si on applique la recommandation : {pct(s['partiel']['worst5'])}."
    a, b = sweep_info["from_p"], sweep_info["to_p"]
    stab = f"Recommandation « {sweep_info['category']} » stable pour p entre {pct(a, 0)} et {pct(b, 0)}"
    if sweep_info["first_all_p"] is not None and sweep_info["category"] != "tout vendre":
        stab += f" ; tout vendre au-delà de {pct(sweep_info['first_all_p'], 0)}"
    stab += "."
    path = M.best_path(g, pm.route, q["incident"])
    names = " → ".join(svg._clip(pm.node_by_id[i]["name"], 26) for i in path)
    sev = pm.params[q["incident"]]["sev"]
    imp = pm.impact.get(q["incident"], 0)
    ev = relevant_evidence(inc["kind"], [e["mechanism"] for e in g["edges"] if e["from"] == q["incident"]])[:3]
    ev_txt = ", ".join(f'<a href="{e["url"]}" target="_blank" rel="noopener">{escape(e["title"])} ({e["date"][:7]})</a>' for e in ev)
    simple = assess(size, min(100.0, 100 * sev * imp), min(100.0, 100 * cost(1.0)))
    be = simple["break_even_probability_percent"]
    be_txt = ("aucune perte possible par ce chemin" if be is None else
              (f"au-dessus de {num(be, 1)} %" if be <= 100 else "jamais (même certain, l'incident coûte moins que la sortie)"))
    tone = {"rester": "stay", "vente partielle": "partial", "tout vendre": "exit"}[dec["category"]]
    return f"""
    <section class="answer {tone}" aria-live="polite">
      <p class="eyebrow">LA RÉPONSE À 3 H DU MATIN — {escape(svg._clip(inc['name'], 60))}, p = {pct(q['p'], 0)}</p>
      <h2>{escape(head)}</h2>
      <p class="big">{body}</p>
      <p>{tail} {stab}</p>
      <p class="why"><strong>Pourquoi :</strong> le dommage voyage par {escape(names)}.
        Facteur d'impact I = {num(imp, 3)} ; si l'incident se produit, perte sur la position ≈ sévérité {pct(sev, 0)} × I = {pct(sev * imp, 2)}.
        Perte attendue (vue neutre) {pct(st['expected'], 2)}, pire 5 % {pct(st['cvar95'], 2)} (20 000 scénarios).</p>
      <p class="why"><strong>Seuil simple</strong> (calcul de l'équipe, risk.assess) : tout vendre devient rentable si la probabilité est {be_txt}.</p>
      <p class="why"><strong>Preuves :</strong> {ev_txt or "—"}</p>
    </section>"""


def graph_section(q, g, pm):
    cov = g["checks"]["coverage"]
    path = M.best_path(g, pm.route, q["incident"])
    info = {nid: {"p": prm["p"], "impact": pm.impact.get(nid, 0)} for nid, prm in pm.params.items()}
    if q["incident"] in info:
        info[q["incident"]]["p"] = q["p"]
    drawing = svg.render(g, info, pm.weights, q["incident"], path)
    sh = cov["share_by_status"]
    cov_line = (f"{cov['dependencies']} dépendances · {cov['edges']} arêtes · profondeur {cov['max_depth']} · "
                f"{pct(cov['verified_share'], 0)} non supposées (on-chain {pct(sh['verified_onchain'], 0)}, "
                f"API {pct(sh['api'], 0)}, documentées {pct(sh['documented'], 0)}, supposées {pct(sh['assumed'], 0)})")
    errs = g["checks"]["errors"]
    checks = ("Contrôles automatiques : chaque arête a une preuve, pas de cycle, tout nœud atteint la position, "
              "aucune arête de prêt sans identifiant de marché réel — <strong>OK</strong>." if not errs else
              "Contrôles : " + "; ".join(escape(e) for e in errs))
    loops = "".join(f"<li>{escape(pm.node_by_id.get(lp['from'], {}).get('name', lp['from']))} → "
                    f"{escape(pm.node_by_id.get(lp['to'], {}).get('name', lp['to']))} ({escape(lp['mechanism'])}) : "
                    f"{escape(lp['note'])}</li>" for lp in g.get("loops", []))
    assumed = [e for e in g["edges"] if e["status"] == "assumed"]
    assumed_html = ("<p class='fine-print'>Aucune arête supposée : toutes viennent d'un contrat, d'une API ou d'une documentation.</p>"
                    if not assumed else "<ul>" + "".join(f"<li class='assumed'>À VÉRIFIER : {escape(e['from'])} → {escape(e['to'])}</li>" for e in assumed) + "</ul>")
    return f"""
    <section class="panel" aria-labelledby="g-title">
      <div class="section-head"><div><p class="eyebrow">01 / DÉCOUVRIR</p><h2 id="g-title">Ce dont dépend la position — découvert automatiquement</h2></div>
        <span class="section-tag">{escape(cov_line)}</span></div>
      <p class="section-intro">Une flèche A → B veut dire « si A cède, B est touché ». Survolez un nœud ou une flèche pour voir la preuve et son heure de relevé ; cliquez pour l'ouvrir.
        Cliquez sur un composant dans la liste ci-dessus pour simuler son incident.</p>
      <div class="graph-wrap">{drawing}</div>
      {svg.legend(g)}
      <p class="fine-print">{checks}</p>
      {assumed_html}
      {"<details open><summary>Boucles réflexives détectées (" + str(len(g.get('loops', []))) + ")</summary><ul>" + loops + "</ul></details>" if loops else ""}
    </section>"""


def decision_section(q, rep, st, dec, cost, size):
    s = dec["strategies"]
    rows = ""
    for key, label in (("rester", "Tout garder"), ("partiel", f"Vendre {pct(dec['x_star'], 0)}"), ("tout_vendre", "Tout vendre")):
        v = s[key]
        rows += (f"<tr{' class=best' if key == 'partiel' else ''}><td>{label}</td><td>{pct(v['exit_cost'], 2)}</td>"
                 f"<td>{pct(v['expected'], 2)}</td><td>{pct(v['worst5'], 2)}</td><td>{usd(size * v['expected'])}</td></tr>")
    L_n, L_p = st["expected"], st["cvar95"]
    xs = [i / 50 for i in range(51)]
    series = [("Coût attendu (vue neutre)", "#087e82", [(x, cost(x) + (1 - x) * L_n) for x in xs]),
              ("Coût pire 5 % (vue prudente)", "#d1495b", [(x, cost(x) + (1 - x) * L_p) for x in xs]),
              ("Coût de sortie C(x)", "#9aa5b1", [(x, cost(x)) for x in xs])]
    dn = D.decide(st, cost, "neutre")
    dp = D.decide(st, cost, "prudent")
    markers = [(dn["x_star"], dn["strategies"]["partiel"]["expected"], "#087e82", f"x* neutre {pct(dn['x_star'], 0)}"),
               (dp["x_star"], dp["strategies"]["partiel"]["worst5"], "#d1495b", f"x* prudent {pct(dp['x_star'], 0)}")]
    finite = [y for _, _, pts in series for _, y in pts if y != float("inf")]
    chart = svg.line_chart(series, x_label="fraction vendue x", markers=markers, y_max=max(finite) * 1.1 if finite else None)
    quotes = rep["exit"]["quotes"]
    qrows = "".join(f"<tr><td>{pct(qq['fraction'], 0)} ({usd(qq['size_usd'])})</td><td>{pct(qq['cost'], 3) if qq.get('cost') is not None else 'refusé : ' + escape((qq.get('error') or '')[:80])}</td>"
                    f"<td>{escape(qq.get('note', ''))}</td></tr>" for qq in quotes["quotes"])
    closed = f" Forme fermée x* = L / (2c) = {pct(dec['L'], 2)} / {num(2 * cost.c1, 2)} = {pct(dec['closed_form'], 1)}." if dec.get("closed_form") is not None else ""
    return f"""
    <section class="panel" aria-labelledby="d-title">
      <p class="eyebrow">02 / DÉCIDER — COMBIEN VENDRE</p><h2 id="d-title">Vendre une partie peut coûter moins que les deux extrêmes</h2>
      <p class="section-intro">Vendre coûte de plus en plus cher par dollar : C(x) = c·x² avec C(1) = {pct(cost.c1, 1)} ({escape(cost.label)}).
        Garder la part restante coûte (1 − x) × L. On choisit x* qui minimise la somme.{closed}
        Économie par rapport au meilleur des deux extrêmes : <strong>{pct(dec['saving_vs_best_extreme'], 2)}</strong> ({usd(size * dec['saving_vs_best_extreme'])}).</p>
      <div class="two-col">
        <table class="data"><thead><tr><th>Stratégie</th><th>Coût de sortie</th><th>Coût attendu</th><th>Pire 5 %</th><th>Coût attendu ($)</th></tr></thead><tbody>{rows}</tbody></table>
        <div>{chart}<p class="fine-print">Courbes : coût total selon la fraction vendue. Les points marquent l'optimum de chaque vue.</p></div>
      </div>
      <details><summary>Coût de sortie réel hors crise ({escape(quotes.get('source') or '')})</summary>
        <table class="data"><thead><tr><th>Taille vendue</th><th>Coût total (impact + frais)</th><th>Note</th></tr></thead><tbody>{qrows}</tbody></table>
        <p class="fine-print">Hors crise, sortir est presque gratuit ; les 11 % de Belem décrivent une crise, quand la liquidité s'en va. Le régime « hors crise » du formulaire utilise ces cotations.</p>
      </details>
    </section>"""


def sweep_rows(q, pm, cost):
    rows = []
    for k in range(0, 101, 2):
        st = M.stats(pm.losses(q["incident"], k / 100))
        d = D.decide(st, cost, q["view"])
        rows.append({"p": k / 100, "x_star": d["x_star"], "category": d["category"]})
    return rows


def robustness_section(q, g, pm, cost, rows):
    stab = D.stability_sentence(rows, q["p"])
    make = lambda sc, prop, **ov: pm_for(q["pos"], sc, prop, **ov)  # noqa: E731
    chart = svg.line_chart([("x* selon p", "#087e82", [(r["p"], r["x_star"]) for r in rows])], x_label="probabilité de l'incident p",
                           markers=[(q["p"], next(r["x_star"] for r in rows if abs(r["p"] - round(q["p"] * 50) / 50) < 1e-9), "#d1495b", "aujourd'hui")],
                           y_max=1.0)
    fp, fa = stab["first_partial_p"], stab["first_all_p"]
    sentence = f"La recommandation « {stab['category']} » tient pour p entre {pct(stab['from_p'], 0)} et {pct(stab['to_p'], 0)}."
    if fp is not None:
        sentence += f" Vendre une partie devient utile dès p ≈ {pct(fp, 0)}"
        sentence += f", tout vendre au-delà de {pct(fa, 0)}." if fa is not None else " ; tout vendre n'est jamais optimal ici."
    else:
        sentence += " Même avec un incident certain, garder reste moins cher que vendre."
    scen = D.scenario_table(g, q["incident"], q["p"], cost, q["prop"], make)
    srows = "".join(f"<tr><td>{lab}</td><td>{num(v['impact'], 3)}</td><td>{pct(v['stats']['expected'], 2)}</td><td>{pct(v['stats']['cvar95'], 2)}</td>"
                    f"<td>{pct(v['neutre']['x_star'], 0)}</td><td>{pct(v['prudent']['x_star'], 0)}</td></tr>"
                    for lab, v in (("BAS", scen["low"]), ("BASE", scen["base"]), ("HAUT", scen["high"])))
    sens = D.sensitivity(g, q["incident"], q["p"], cost, q["prop"], make)
    trs = ""
    for t in sens["tests"]:
        lo, hi = t["low"]["x_star"], t["high"]["x_star"]
        left, right = min(lo, hi), max(lo, hi)
        trs += (f"<tr><td>{escape(t['input'])}</td><td>{pct(lo, 0)}</td><td>{pct(hi, 0)}</td>"
                f"<td><div class='tornado'><span style='left:{left * 100:.0f}%;width:{max(1, (right - left) * 100):.0f}%'></span>"
                f"<i style='left:{sens['base_x'] * 100:.0f}%'></i></div></td></tr>")
    pm_other = pm_for(q["pos"], "base", "sum" if q["prop"] == "max" else "max")
    other = pm_other.impact.get(q["incident"], 0)
    prop_txt = (f"Chemin le plus fort : I = {num(pm.impact.get(q['incident'], 0) if q['prop'] == 'max' else other, 3)} ; "
                f"somme des chemins plafonnée : I = {num(other if q['prop'] == 'max' else pm.impact.get(q['incident'], 0), 3)}.")
    return f"""
    <section class="panel" aria-labelledby="r-title">
      <p class="eyebrow">03 / ROBUSTESSE</p><h2 id="r-title">La recommandation résiste-t-elle à nos hypothèses ?</h2>
      <p class="decision">{escape(sentence)} Entrée la plus sensible : <strong>{escape(sens['most_sensitive'])}</strong>.</p>
      <div class="two-col">
        <div>{chart}<p class="fine-print">Fraction optimale à vendre x* selon la probabilité de l'incident (mêmes 20 000 tirages pour chaque p).</p></div>
        <div>
          <table class="data"><thead><tr><th>Scénario</th><th>I</th><th>Perte attendue</th><th>Pire 5 %</th><th>x* neutre</th><th>x* prudent</th></tr></thead><tbody>{srows}</tbody></table>
          <p class="fine-print">BAS / BASE / HAUT : toutes les fréquences, sévérités et poids pris à leur borne basse, centrale ou haute (NOTES.md).</p>
        </div>
      </div>
      <table class="data"><thead><tr><th>Une entrée à la fois (bas → haut)</th><th>x* bas</th><th>x* haut</th><th>Écart (le trait = base)</th></tr></thead><tbody>{trs}</tbody></table>
      <p class="fine-print">Choix de la propagation : {escape(prop_txt)} Le chemin le plus fort ignore qu'un dommage peut arriver par plusieurs routes ; la somme peut le compter deux fois. Voir le formulaire pour basculer.</p>
    </section>"""


def book_section(q, g):
    book = book_report()
    if not book:
        return ""
    pids = [p["id"] for p in book["pockets"]]
    graphs = [graph_for(p) for p in pids if (ROOT / "data" / "graphs" / f"{p}.json").exists()]
    key = (q["incident"], round(q["p"], 2))
    if key not in _book:
        n_p = len(graphs)
        per, total = [], [0.0] * M.N_SIM
        for gg in graphs:
            pm = pm_for(gg["position_id"])
            touched = q["incident"] in pm.params and pm.impact.get(q["incident"], 0) > 0
            ls = pm.losses(q["incident"] if touched else None, q["p"] if touched else None)
            for s in range(M.N_SIM):
                total[s] += ls[s] / n_p
            st = M.stats(ls)
            per.append((gg, touched, pm.impact.get(q["incident"], 0) if touched else 0, st))
        _book[key] = (per, M.stats(total))
    per, bst = _book[key]
    rows = "".join(f"<tr class='{'hit' if t else ''}'><td><a href='{link(q, pos=gg['position_id'])}'>{escape(gg['position']['label'])}</a></td>"
                   f"<td>{'oui' if t else 'non'}</td><td>{num(i, 3)}</td><td>{pct(st['expected'], 2)}</td><td>{pct(st['cvar95'], 2)}</td></tr>"
                   for gg, t, i, st in per)
    touched_share = sum(1 for _, t, _, _ in per if t) / max(len(per), 1)
    shared = "".join(f"<li><a href='{link(q, incident=s['id'])}'>{escape(s['name'])}</a> — {len(s['pockets'])} poches</li>" for s in book["shared_nodes"][:12])
    conc = "".join(f"<li class='{'flag' if c['share_of_chain'] >= book['concentration']['flag_threshold'] else ''}'>{escape(c['name'])} : "
                   f"{usd(c['ethereum_tvl_usd'])} sur Ethereum, soit {pct(c['share_of_chain'], 0)} de la TVL de la chaîne</li>"
                   for c in book["concentration"]["protocols"])
    name = graph_for(q["pos"])
    inc_name = next((n["name"] for n in name["nodes"] if n["id"] == q["incident"]), q["incident"])
    return f"""
    <section class="panel" aria-labelledby="b-title">
      <p class="eyebrow">04 / LE LIVRE — 10 POCHES DE 10 %</p><h2 id="b-title">Si « {escape(svg._clip(inc_name, 50))} » cède, qui d'autre est touché ?</h2>
      <p class="section-intro">Dix positions réelles découvertes par le même moteur ; tailles égales (10 % chacune, illustratif comme dans l'énoncé).
        Un même nœud défaille dans les mêmes scénarios pour toutes les poches : les pertes sont corrélées.</p>
      <div class="metrics three">
        <div class="metric"><span>Part du livre exposée</span><strong>{pct(touched_share, 0)}</strong></div>
        <div class="metric"><span>Perte attendue du livre</span><strong>{pct(bst['expected'], 3)}</strong></div>
        <div class="metric accent"><span>Pire 5 % du livre</span><strong>{pct(bst['cvar95'], 3)}</strong></div>
      </div>
      <div class="two-col">
        <table class="data"><thead><tr><th>Poche</th><th>Touchée</th><th>I</th><th>Perte attendue</th><th>Pire 5 %</th></tr></thead><tbody>{rows}</tbody></table>
        <div><h3>Nœuds partagés par plusieurs poches</h3><ul class="plain">{shared}</ul>
          <h3>Concentration de la TVL d'Ethereum (DeFiLlama)</h3><ul class="plain">{conc}</ul>
          <p class="fine-print">Signalé au-delà de 10 % de la TVL de la chaîne.</p></div>
      </div>
    </section>"""


def evidence_section(q, g, pm, rep):
    inc_kind = pm.node_by_id[q["incident"]]["kind"]
    items = "".join(f"<li class='{'rel' if inc_kind in e['kinds'] else ''}'><a href='{e['url']}' target='_blank' rel='noopener'>{escape(e['title'])}</a> "
                    f"<small>({e['date']})</small> — {escape(e['facts'])} <em>Utilisé pour : {escape(e['used_for'])}</em></li>" for e in EVIDENCE)
    prows = ""
    for nid, prm in sorted(pm.params.items(), key=lambda kv: -pm.impact.get(kv[0], 0)):
        n = pm.node_by_id[nid]
        prows += (f"<tr{' class=hit' if nid == q['incident'] else ''}><td>{escape(svg._clip(n['name'], 48))}</td><td>{KIND_FR.get(n['kind'], n['kind'])}</td>"
                  f"<td>{pct(prm['lam'], 2)} <small>[{pct(prm['lam_range'][0], 2)} ; {pct(prm['lam_range'][2], 2)}]</small></td>"
                  f"<td>{pct(prm['p'], 3)}</td><td>{pct(prm['sev'], 0)} <small>[{pct(prm['sev_range'][0], 0)} ; {pct(prm['sev_range'][2], 0)}]</small></td>"
                  f"<td>{num(pm.impact.get(nid, 0), 3)}</td><td>{'HYPOTHÈSE' if prm['assumption'] else ''} {escape(prm['why'] or '')}</td></tr>")
    srcs = "".join(f"<li>{escape(s['source'])} — <a href='{escape(s['url'], quote=True)}' target='_blank' rel='noopener'>{escape(s['url'][:90])}</a> <small>relevé {escape(s['fetched_at'])}</small></li>"
                   for s in g["meta"]["sources"])
    return f"""
    <section class="panel" aria-labelledby="e-title">
      <p class="eyebrow">05 / PREUVES</p><h2 id="e-title">D'où viennent les nombres</h2>
      <details open><summary>Paramètres de chaque composant (horizon {num(pm.horizon, 0)} jours)</summary>
        <table class="data small"><thead><tr><th>Composant</th><th>Type</th><th>λ annuel [bas ; haut]</th><th>p sur l'horizon</th><th>Sévérité [bas ; haut]</th><th>I</th><th>Note</th></tr></thead><tbody>{prows}</tbody></table>
        <p class="fine-print">p = 1 − exp(−λ·H/365). λ vient de la base d'exploits DeFiLlama (2021-2025) ou d'incidents documentés ; détails dans NOTES.md.</p></details>
      <details><summary>Incidents réels utilisés ({len(EVIDENCE)})</summary><ul class="evidence">{items}</ul></details>
      <details><summary>Données relevées ({len(g['meta']['sources'])} réponses en cache)</summary><ul class="evidence">{srcs}</ul></details>
    </section>"""


def render_page(query: str) -> str:
    q = parse(query)
    g = graph_for(q["pos"])
    rep = report_for(q["pos"])
    pm = pm_for(q["pos"], "base", q["prop"])
    cost = exit_model(rep, q)
    st = M.stats(pm.losses(q["incident"], q["p"]))
    dec = D.decide(st, cost, q["view"])
    sweep = sweep_rows(q, pm, cost)
    stab = D.stability_sentence(sweep, q["p"])
    size = g["position"]["size_usd"]
    ts = rep["data_fetched_at"]
    badge = (f"Données en cache du {escape((ts.get('newest') or '?')[:16].replace('T', ' '))} UTC" if rep.get("offline")
             else f"Données relevées le {escape((ts.get('newest') or '?')[:16].replace('T', ' '))} UTC")
    subs = {
        "position_name": escape(g["position"]["label"]),
        "position_size": usd(size),
        "horizon": num(pm.horizon, 0),
        "badge": badge,
        "controls": controls(q, g, pm),
        "answer": answer_box(q, g, pm, st, dec, cost, stab, size),
        "graph": graph_section(q, g, pm),
        "decision": decision_section(q, rep, st, dec, cost, size),
        "robustness": robustness_section(q, g, pm, cost, sweep),
        "book": book_section(q, g),
        "evidence": evidence_section(q, g, pm, rep),
    }
    return TEMPLATE.substitute(subs)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        request = urlparse(self.path)
        try:
            if request.path == "/static/style.css":
                body, content_type, status = STYLE, "text/css; charset=utf-8", 200
            elif request.path == "/health":
                body, content_type, status = b"ok", "text/plain; charset=utf-8", 200
            elif request.path == "/":
                body = render_page(request.query).encode("utf-8")
                content_type, status = "text/html; charset=utf-8", 200
            elif request.path.startswith("/api/report/"):
                pid = request.path.rsplit("/", 1)[-1]
                body = json.dumps(report_for(pid), ensure_ascii=False).encode("utf-8")
                content_type, status = "application/json; charset=utf-8", 200
            else:
                body, content_type, status = b"Page introuvable", "text/plain; charset=utf-8", 404
        except Exception as exc:  # la démo ne doit jamais afficher une page blanche
            body = f"<h1>Erreur</h1><pre>{escape(type(exc).__name__)}: {escape(str(exc))}</pre>".encode("utf-8")
            content_type, status = "text/html; charset=utf-8", 500
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prototype local de risque DeFi")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Ouvrez http://127.0.0.1:{args.port} (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()
