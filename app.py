"""Page locale : une seule histoire, de la position à la décision (bibliothèque standard uniquement).

  python app.py            puis ouvrir http://127.0.0.1:8000
  python app.py --port 8001

Le site relève la position affichée en ligne au démarrage, puis toutes les cinq minutes.
Une collecte échouée rend la page indisponible au lieu d'afficher un ancien rapport.
"""

import argparse
import json
import threading
import time
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from string import Template
from urllib.parse import parse_qs, urlencode, urlparse

from risk_engine import decide as D
from risk_engine import discover, sources as S
from risk_engine import model as M
from risk_engine import rules, svg
from risk_engine import simulate as SIM
from risk_engine.risk import assess
import pipeline

ROOT = Path(__file__).resolve().parent
TEMPLATE = Template((ROOT / "templates" / "index.html").read_text(encoding="utf-8"))
STYLE = (ROOT / "static" / "style.css").read_bytes()
POSITIONS = json.loads((ROOT / "data" / "positions.json").read_text(encoding="utf-8"))
EVIDENCE = json.loads((ROOT / "data" / "evidence.json").read_text(encoding="utf-8"))["incidents"]
KIND_FR = {k: v[2] for k, v in svg.KIND_STYLE.items()}

_models, _graphs, _reports, _sims = {}, {}, {}, {}
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
        for key in list(_models):
            if key[0] == pid:
                del _models[key]
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


def sim_ctx_for(pid):
    """État de simulation relevé en ligne pour cette position."""
    return _sims[pid]


def pm_for(pid, scenario="base", propagation="max", **ov):
    key = (pid, scenario, propagation, tuple(sorted(ov.items())))
    if key not in _models:
        _models[key] = M.PositionModel(graph_for(pid), scenario, propagation, overrides=ov or None)
    return _models[key]


def available_positions():
    return POSITIONS["positions"]


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
    return {**sim, "pos": pid, "incident": incident, "p": fnum("p", 100 * rep["default_incident_p"], 0, 100) / 100,
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
    return "/?" + urlencode({**q, "p": round(q["p"] * 100), "c1": round(q["c1"] * 100, 2),
                             "sev": round(q["sev"] * 100, 1), **changes})


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
                     (("pos", q["pos"]), ("incident", q["incident"]), ("p", round(q["p"] * 100)), ("c1", round(q["c1"] * 100, 2)),
                      ("regime", q["regime"]), ("view", q["view"]), ("prop", q["prop"]), ("snode", q["snode"])))
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
    return f"""
      <form method="get" action="/#sim" class="sim-form">{hidden}
        <div class="sim-node"><span>② Nœud en défaillance</span><strong>{escape(n["name"])}</strong><small>{escape(KIND_FR.get(n["kind"], n["kind"]))}</small></div>
        <label>③ Scénario<select name="stype">{opts}</select><small>{escape(spec["help"])}</small></label>
        <label>④ Sévérité ({escape(spec["variable"])}) <output id="sev-out">−{num(sev, 0) if sev == int(sev) else num(sev, 1)} %</output>
          <input type="range" name="sev" min="1" max="{round(spec['max'] * 100)}" step="1" value="{sev}"
            oninput="document.getElementById('sev-out').value='−'+this.value+' %'"></label>
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
    tiles = [("Défaillance", f"{escape(res['scenario_label'])} −{pct(res['severity'], 0)}", escape(svg._clip(res["node_name"], 48)), ""),
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
                   "sortie complète possible" if ex1["full_exit"] else
                   f"sortie complète IMPOSSIBLE : {svg.fmt_pct(ex1['sold_units'] / ex1['units'], 0)} vendable", "")]
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
                  f"<em>(non inclus dans le total)</em></li>" for e in res["secondary_effects"])
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
      <h3>Sévérité financière</h3>
      <table class="data sim-metrics"><thead><tr><th>Métrique</th><th>Avant</th><th>Après</th><th>Variation</th><th>% valeur initiale</th><th>Méthode</th></tr></thead><tbody>{rows}</tbody></table>
      <p class="fine-print">{escape(T["note"]) + ". " if T.get("note") else ""}Pourcentages rapportés à la valeur initiale de la position.</p>
      <h3>Chemin de propagation</h3><p class="sim-path">{path or "La défaillance n'atteint pas la position par un chemin modifiant sa valeur."}</p>
      <h3>Transmissions non chiffrées (INCONNU)</h3>{unk}
      {"<h3>Effets secondaires estimés</h3><ul class='plain'>" + sec + "</ul>" if sec else ""}
      <details open><summary>Détail de chaque étape ({len(res["steps"])})</summary>
        <table class="data small"><thead><tr><th>Nœud</th><th>Variable</th><th>Avant</th><th>Après</th><th>Mécanisme et modèle</th><th>Hypothèses</th></tr></thead><tbody>{steps}</tbody></table></details>
      <details><summary>Modèle de chaque dépendance traversée ({len(res["edges"])})</summary>
        <table class="data small"><thead><tr><th>Arête</th><th>Type</th><th>Transmet</th><th>Modèle</th><th>Paramètres</th><th>Source</th><th>Hypothèses</th><th>Chiffré</th></tr></thead><tbody>{edges}</tbody></table></details>
      <details><summary>Données utilisées : vérifiées et hypothèses</summary><ul class="evidence">{ev}{assumed}{missing}</ul></details>"""


def graph_for_id(res):
    return graph_for(res["position"]["id"].split(":", 1)[1])


def simulation_section(q, g):
    head = """<section class="panel" id="sim" aria-labelledby="s-title">
      <p class="eyebrow">SIMULER — SI CE MAILLON CÈDE, COMBIEN PERD LA POSITION ?</p>
      <h2 id="s-title">Simulateur de défaillance : propagation et sévérité financière</h2>
      <p class="section-intro">Aucune probabilité ici : on suppose que la défaillance se produit et on calcule ses conséquences, nœud par nœud,
        avec les formules des protocoles (oracle Morpho, règle de liquidation Morpho Blue, courbe de l'AMM Pendle V2).
        Une transmission impossible à modéliser est marquée <strong>INCONNU</strong> et n'entre pas dans les chiffres.</p>"""
    ctx = sim_ctx_for(q["pos"])
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
                         node_href=lambda nid: link(q, snode=nid, run="0") + "#sim")
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
    main = next((p["id"] for p in POSITIONS["positions"] if p.get("main")), POSITIONS["positions"][0]["id"])
    pid = args.get("pos", [main])[0]
    if pid not in {p["id"] for p in POSITIONS["positions"]}:
        pid = main
    ensure_live_data(pid, force=args.get("refresh") == ["1"])
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
    badge = f"Données relevées en ligne le {escape((ts.get('newest') or '?')[:16].replace('T', ' '))} UTC"
    subs = {
        "position_name": escape(g["position"]["label"]),
        "position_size": usd(size),
        "horizon": num(pm.horizon, 0),
        "badge": badge,
        "refresh_url": "/?" + escape(urlencode({"pos": q["pos"], "refresh": 1}), quote=True),
        "controls": controls(q, g, pm),
        "answer": answer_box(q, g, pm, st, dec, cost, stab, size),
        "graph": graph_section(q, g, pm),
        "simulation": simulation_section(q, g),
        "decision": decision_section(q, rep, st, dec, cost, size),
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
    main = next((p["id"] for p in POSITIONS["positions"] if p.get("main")), POSITIONS["positions"][0]["id"])
    print("Collecte en ligne de la position principale…", flush=True)
    ensure_live_data(main)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Ouvrez http://127.0.0.1:{args.port} (Ctrl+C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()
