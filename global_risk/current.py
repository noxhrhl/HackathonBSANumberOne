"""Une seule zone de graphe sur la page d'analyse actuelle.

Le site original continue de rendre ses sections et de calculer la recommandation.
Ce module choisit laquelle des deux représentations déjà produites afficher selon
la défaillance sélectionnée. Il ne modifie aucun fichier de l'application d'origine.
"""

from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode

import app
from risk_engine import simulate

from . import web


class _DivLocator(HTMLParser):
    """Repère les limites des div nommées, sans altérer le HTML qu'elles contiennent."""

    def __init__(self, page):
        super().__init__(convert_charrefs=False)
        self.line_offsets = [0]
        for line in page.splitlines(keepends=True):
            self.line_offsets.append(self.line_offsets[-1] + len(line))
        self.stack = []
        self.spans = {}

    def _offset(self):
        line, col = self.getpos()
        return self.line_offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        values = dict(attrs)
        start = self._offset()
        self.stack.append((values, start, start + len(self.get_starttag_text())))

    def handle_endtag(self, tag):
        if tag != "div" or not self.stack:
            return
        values, start, content_start = self.stack.pop()
        end_start = self._offset()
        key = values.get("id") or ("tabs-wrap" if "tabs-wrap" in (values.get("class") or "").split() else None)
        if key:
            self.spans[key] = (start, content_start, end_start, end_start + len("</div>"))


def _spans(page):
    locator = _DivLocator(page)
    locator.feed(page)
    required = {"tabs-wrap", "t-graph", "t-sim"}
    if not required.issubset(locator.spans):
        raise ValueError("Sections du graphe introuvables dans la page actuelle")
    return locator.spans


def _normalized_query(query, pid, graph, report):
    """Synchronise le composant du formulaire et un clic dans le graphe simulé."""
    args = {key: values[0] for key, values in parse_qs(query).items() if values}
    ids = {n["id"] for n in graph["nodes"] if n["kind"] != "position"}
    incident = args.get("incident") if args.get("incident") in ids else report["default_incident"]
    # Dans le graphe avancé existant, cliquer un nœud change `snode`.
    # Ce clic devient aussi le choix de défaillance de la page unique.
    if args.get("snode") in ids and args["snode"] != incident:
        incident = args["snode"]
        args.pop("run", None)
    compatible = (pid in app.ADVANCED_POSITIONS and
                  bool(simulate.compatible_scenarios(graph, incident)))
    args.update(pos=pid, incident=incident)
    if compatible:
        args["snode"] = incident
    else:
        for name in ("snode", "stype", "sev", "hours", "run"):
            args.pop(name, None)
    return urlencode(args), compatible


def _one_graph(page, advanced):
    """Conserve exactement un panneau de graphe et retire le script des sous-onglets."""
    spans = _spans(page)
    pane = spans["t-sim" if advanced else "t-graph"]
    content = page[pane[1]:pane[2]]
    outer = spans["tabs-wrap"]
    mode = ("Simulation approfondie de la défaillance choisie" if advanced
            else "Dépendances de la défaillance choisie")
    marker = '<div class="global-current-graph" aria-label="Graphe unique">'
    replacement = (f'{marker}<p class="global-current-mode">{mode}</p>'
                   f'{content}</div>')
    page = page[:outer[0]] + replacement + page[outer[3]:]
    script_start = page.find('<script>\n    // Onglets du graphe')
    if script_start < 0:
        raise ValueError("Script des anciens sous-onglets introuvable")
    script_end = page.find("</script>", script_start)
    if script_end < 0:
        raise ValueError("Fin du script des anciens sous-onglets introuvable")
    page = page[:script_start] + page[script_end + len("</script>"):]
    # Le choix de la défaillance déclenche immédiatement le bon mode de graphe.
    page = page.replace('<select name="incident">',
                        '<select name="incident" onchange="this.form.submit()">', 1)
    return page


def render_current(query, pid):
    """Rend la page d'origine avec un seul graphe, sans recalculer ses résultats."""
    with app._refresh_lock:
        app.ensure_live_data(pid, force=parse_qs(query).get("refresh") == ["1"])
        normalized, advanced = _normalized_query(query, pid, app.graph_for(pid), app.report_for(pid))
        page = app.render_page(normalized)
        page = web.add_tabs_to_current(page, pid)
        return _one_graph(page, advanced)
