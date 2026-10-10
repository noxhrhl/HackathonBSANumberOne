"""Site existant augmenté d'un onglet d'analyse globale.

    python app_global.py
    python app_global.py --port 8001

Le site d'origine reste disponible avec `python app.py`. Aucun instantané local
ne sert de repli quand une collecte en ligne échoue.
"""

import argparse
import json
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import app
from global_risk import analyze
from global_risk import current, web


def selected_position(query):
    """Applique la même position par défaut que le site initial."""
    requested = parse_qs(query).get("pos", [None])[0]
    positions = app.available_positions()
    main = next((p["id"] for p in positions if p.get("main")), positions[0]["id"])
    return requested if requested in {p["id"] for p in positions} else main


def live_analysis(pid, force=False):
    """Construit un profil uniquement depuis les relevés en mémoire de app.py."""
    with app._refresh_lock:
        app.ensure_live_data(pid, force=force)
        graph = app.graph_for(pid)
        report = app.report_for(pid)
        context = app.sim_ctx_for(pid)
        return analyze(graph, report, context), graph, report, context


class Handler(app.Handler):
    """Conserve toutes les routes de app.Handler et ajoute /global et /api/global."""

    def _send(self, body, content_type="text/html; charset=utf-8", status=200):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        request = urlparse(self.path)
        if request.path not in ("/", "/global", "/api/global"):
            return super().do_GET()
        pid = selected_position(request.query)
        try:
            if request.path == "/":
                # app.py produit ses calculs et sections ; la présentation garde
                # un seul graphe selon la défaillance sélectionnée.
                body = current.render_current(request.query, pid)
                return self._send(body)
            result, graph, report, context = live_analysis(
                pid, force=parse_qs(request.query).get("refresh") == ["1"])
            if request.path == "/api/global":
                return self._send(json.dumps(result, ensure_ascii=False),
                                  "application/json; charset=utf-8")
            return self._send(web.render_global(result, graph, report, context,
                                                app.available_positions()))
        except Exception as exc:
            return self._send(web.render_unavailable(pid, exc), status=503)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Belem : site et analyse globale des risques")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    main = selected_position("")
    print("Collecte en ligne de la position principale…", flush=True)
    try:
        app.ensure_live_data(main)
    except Exception as exc:
        print(f"Données indisponibles au démarrage : {type(exc).__name__}: {exc}", flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Ouvrez http://127.0.0.1:{args.port} (Ctrl+C pour arrêter)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServeur arrêté.")
    finally:
        server.server_close()
